"""Scripted interval capture scenes used by the runtime demo and the hand checks."""
import json
import sqlite3
from contextlib import closing
from dataclasses import asdict
from pathlib import Path

from station.closing.seal import close
from station.device.fixture import FailOnceBeforeCommit, InjectedCommitFailure
from station.device.gain_fixture import GainExposure, GainOriginal, LostExposureReply, load_head
from station.device.trigger import trigger_interval
from station.intake.accept import accept_interval
from station.intake.gain import GainStore
from station.lookup.round import get_round
from station.lookup.sample import get_interval_sample
from station.rounds.authorization import authorize_read
from station.rounds.opening import open
from station.rounds.workstation import initialize_station


def _load(path: str | Path) -> dict:
    with Path(path).open(encoding='utf-8') as source:
        return json.load(source)


def _order(spec: dict, piece: str) -> str:
    return f"{spec['round_id']}/{piece}"


def _authorize(connection, spec: dict, piece: str) -> str:
    order_id = _order(spec, piece)
    authorize_read(connection, spec['round_id'], piece, order_id, mode='interval')
    return order_id


def _action(piece: str, gain: str, exposure: GainExposure, interval, result: dict) -> dict:
    return {'piece': piece, 'gain': gain, 'exposure_id': exposure.exposure_id,
            'tick': exposure.tick, 'interval': interval, 'status': result['status'],
            'receipt': result['receipt'],
            'effective_interval': result['effective_interval']}


def _deliver(connection, head, spec: dict, piece: str, gain: str) -> dict:
    order_id = _authorize(connection, spec, piece)
    exposure = trigger_interval(connection, head, spec['round_id'], piece, order_id, gain)
    original = head.complete(exposure.exposure_id)
    return _action(piece, gain, exposure, original.interval,
                   accept_interval(connection, head, order_id, original))


def _summary(connection, spec: dict, actions: list[dict], fired: int) -> dict:
    closed = close(connection, spec['round_id'])
    report = closed.report
    receipts = {}
    samples = {}
    for item in actions:
        if item['receipt'] is None:
            continue
        receipts[item['piece']] = item['receipt']
        sample = get_interval_sample(connection, item['receipt'])
        samples[item['piece']] = {
            'effective_interval': sample['effective_interval'],
            'originals': [{'gain': original.exposure.gain,
                           'exposure_id': original.exposure.exposure_id,
                           'tick': original.exposure.tick,
                           'interval': original.interval}
                          for original in sample['originals']],
        }
    return {
        'fired': fired,
        'statuses': {item['piece']: item['status'] for item in actions},
        'receipts': receipts,
        'pending': list(closed.pending),
        'report': None if report is None else report.number,
        'pieces': None if report is None else
        [[entry.piece, entry.grade.value] for entry in report.conclusions],
        'counts': None if report is None else asdict(report.counts),
        'samples': samples,
    }


def _run_mixed(connection, head, spec: dict, start: int):
    open(connection, spec['round_id'], spec['roster'], spec['limit'])
    actions = [_deliver(connection, head, spec, firing['piece'], firing['gain'])
               for firing in spec['firings']]
    return connection, _summary(connection, spec, actions, len(head.history()) - start)


def _reopen(spec: dict) -> sqlite3.Connection:
    connection = sqlite3.connect(spec['database'])
    initialize_station(connection)
    return connection


def _run_fault_before(connection, head, spec: dict, start: int):
    """The low gain never fires; after the reopened connection it may fire again."""
    open(connection, spec['round_id'], spec['roster'], spec['limit'])
    piece = spec['roster'][0]
    order_id = _authorize(connection, spec, piece)
    high = _deliver(connection, head, spec, piece, 'high')
    high_exposure = GainStore(connection).find_exposure(order_id, 'high')
    fired_before = len(head.history())
    head.lose_next_reply('before')
    try:
        trigger_interval(connection, head, spec['round_id'], piece, order_id, 'low')
    except LostExposureReply:
        pass
    else:
        raise RuntimeError('the before-reply fault did not interrupt the fire')
    interrupted = {'history_unchanged': len(head.history()) == fired_before,
                   'recorded_low': GainStore(connection).find_exposure(order_id, 'low') is not None}
    if not interrupted['history_unchanged'] or interrupted['recorded_low']:
        raise RuntimeError('a fire that never happened was recorded or fired')
    connection.close()
    connection = _reopen(spec)
    retried = trigger_interval(connection, head, spec['round_id'], piece, order_id, 'low')
    if retried.exposure_id == high_exposure.exposure_id:
        raise RuntimeError('the retry did not allocate a separate low exposure')
    original = head.complete(retried.exposure_id)
    low = _action(piece, 'low', retried, original.interval,
                  accept_interval(connection, head, order_id, original))
    summary = _summary(connection, spec, [high, low], len(head.history()) - start)
    summary['interrupted'] = interrupted
    summary['retried_exposure_id'] = retried.exposure_id
    return connection, summary


def _run_fault_after(connection, head, spec: dict, start: int):
    """The low gain physically fires; the reopened connection reuses that exposure."""
    open(connection, spec['round_id'], spec['roster'], spec['limit'])
    piece = spec['roster'][0]
    order_id = _authorize(connection, spec, piece)
    actions = [_deliver(connection, head, spec, piece, 'high')]
    head.lose_next_reply('after')
    try:
        trigger_interval(connection, head, spec['round_id'], piece, order_id, 'low')
    except LostExposureReply:
        pass
    else:
        raise RuntimeError('the after-reply fault did not interrupt the fire')
    fired = head.history()[-1]
    if GainStore(connection).find_exposure(order_id, 'low') is not None:
        raise RuntimeError('a rolled-back fire left a station record')
    connection.close()
    connection = _reopen(spec)
    recovered = trigger_interval(connection, head, spec['round_id'], piece, order_id, 'low')
    if recovered.exposure_id != fired.exposure_id:
        raise RuntimeError('the retried low gain did not reuse the physical exposure')
    original = head.complete(recovered.exposure_id)

    rejections = []
    forged = GainOriginal(GainExposure(fired.device_id, fired.exposure_id, fired.piece,
                                       fired.gain, fired.tick, fired.tag), (0, 1))
    foreign_row = connection.execute(
        'SELECT exposure_id FROM gain_exposures WHERE order_id != ? '
        'ORDER BY rowid LIMIT 1', (order_id,)).fetchone()
    foreign = None
    if foreign_row is not None:
        foreign = GainStore(connection).find_exposure_by_id(foreign_row[0])
        foreign = GainOriginal(foreign, (0, 1))
    for label, delivered in (('forged interval', forged), ('foreign exposure', foreign)):
        if delivered is None:
            continue
        try:
            accept_interval(connection, head, order_id, delivered)
        except ValueError:
            rejections.append(label)
        else:
            raise RuntimeError(f'the {label} original was accepted')

    fault = FailOnceBeforeCommit()
    try:
        accept_interval(connection, head, order_id, original, before_commit=fault)
    except InjectedCommitFailure:
        pass
    else:
        raise RuntimeError('the injected signing failure did not interrupt the acceptance')
    if GainStore(connection).find_conclusion_for_piece(spec['round_id'], piece) is not None:
        raise RuntimeError('a failed signing left a new conclusion behind')
    resumed = accept_interval(connection, head, order_id, head.complete(recovered.exposure_id))
    replay = accept_interval(connection, head, order_id, original)
    if replay != resumed:
        raise RuntimeError('replaying the adopted original changed the signed result')
    actions.append(_action(piece, 'low', recovered, original.interval, resumed))
    summary = _summary(connection, spec, actions, len(head.history()) - start)
    summary['recovered_exposure_id'] = recovered.exposure_id
    summary['rejections'] = rejections
    summary['signing_failure_left_conclusion'] = False
    return connection, summary


def _offline(database: str | Path, report_number: str, receipts: dict[str, str]) -> dict:
    uri = Path(database).resolve().as_uri() + '?mode=ro'
    with closing(sqlite3.connect(uri, uri=True)) as offline:
        reopened = get_round(offline, report_number)
        samples = {}
        for piece, receipt in receipts.items():
            sample = get_interval_sample(offline, receipt)
            samples[piece] = {
                'grade': sample['grade'],
                'effective_interval': sample['effective_interval'],
                'originals': [{'gain': original.exposure.gain,
                               'exposure_id': original.exposure.exposure_id,
                               'tick': original.exposure.tick,
                               'interval': original.interval}
                              for original in sample['originals']],
            }
        return {'report': reopened.number,
                'pieces': [[entry.piece, entry.grade.value] for entry in reopened.conclusions],
                'samples': samples}


_KINDS = {'mixed': _run_mixed, 'fault_before': _run_fault_before, 'fault_after': _run_fault_after}


def _plain(value):
    """Normalize tuples and dataclasses into the JSON shapes used by the fixtures."""
    return json.loads(json.dumps(value, default=lambda item: item.interval
                                if isinstance(item, GainOriginal) else asdict(item)))


def run_gain_scenario(database: str | Path, path: str | Path, *, echo: bool = True) -> dict:
    scenario = _load(path)
    head = load_head(scenario['device_fixture'])
    evidence = {'case_id': scenario['case_id'], 'rounds': [], 'device_history': []}
    with closing(sqlite3.connect(database)) as connection:
        initialize_station(connection)
        signed = []
        for spec in scenario['rounds']:
            spec = dict(spec, database=str(database))
            start = len(head.history())
            connection, actual = _KINDS[spec['kind']](connection, head, spec, start)
            expected = spec['expected']
            for key, value in expected.items():
                if _plain(actual.get(key)) != value:
                    raise RuntimeError(
                        f"{scenario['case_id']}/{spec['round_id']}: {key} expected "
                        f"{value!r}; got {_plain(actual.get(key))!r}")
            evidence['rounds'].append({'round_id': spec['round_id'],
                                       'expected': expected, 'actual': _plain(actual)})
            if actual['report']:
                signed.append(actual)
        evidence['offline'] = _plain([
            _offline(database, item['report'], item['receipts']) for item in signed])
    evidence['device_history'] = [asdict(exposure) for exposure in head.history()]
    if echo:
        print('GAIN_SCENARIO ' + json.dumps(evidence, sort_keys=True))
    return evidence

