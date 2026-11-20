import argparse
import json
import sqlite3
from contextlib import closing
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory

from station.closing.repository import ClosingRepository
from station.closing.seal import close
from station.device.fixture import (DeliverySchedule, FailOnceBeforeCommit, InjectedCommitFailure,
                                    ScenarioFixture, load_fixture)
from station.device.optical import apply_observation
from station.device.recording import DeviceRecording
from station.device.trigger import trigger
from station.intake.accept import accept
from station.intake.receipt import ReceiptStore
from station.intake.repository import IntakeRepository
from station.lookup.round import get_round
from station.lookup.sample import get_sample
from station.rounds.authorization import authorize_read
from station.rounds.opening import open
from station.rounds.repository import RoundRepository, transaction


def initialize_station(connection: sqlite3.Connection) -> None:
    if connection.in_transaction:
        raise ValueError('station initialization requires an idle connection')
    connection.execute('PRAGMA foreign_keys = ON')
    with transaction(connection, write=True):
        RoundRepository(connection).initialize()
        DeviceRecording(connection).initialize()
        IntakeRepository(connection).initialize()
        ReceiptStore(connection).initialize()
        ClosingRepository(connection).initialize()


def _prepare_deliveries(connection: sqlite3.Connection, scenario: ScenarioFixture):
    deliveries = DeliverySchedule()
    slots = set()
    for entry in scenario.exposures:
        authorize_read(connection, scenario.round_id, entry.piece, entry.order_id)
        exposure = trigger(connection, scenario.round_id, entry.piece, entry.order_id)
        for observation in entry.fixture.observations_for('left'):
            terminal = apply_observation(connection, exposure, observation)
            if terminal is not None:
                deliveries.schedule(entry.deliver_at, (entry.order_id, terminal))
                slots.add(entry.deliver_at)
                break
        else:
            raise ValueError(f'fixture for {entry.piece!r} never reaches a device terminal')
    return deliveries, sorted(slots)


def _run_intake(connection: sqlite3.Connection, scenario: ScenarioFixture):
    deliveries, slots = _prepare_deliveries(connection, scenario)
    fail_once = FailOnceBeforeCommit()
    arrivals = []
    failures = []
    receipts = {}
    pending = None
    for slot in slots:
        for order_id, terminal in deliveries.take_due(slot):
            piece = terminal.piece
            arrivals.append(piece)
            hook = fail_once if piece == scenario.fail_once_for else None
            try:
                result = accept(connection, order_id, terminal, before_commit=hook)
            except InjectedCommitFailure:
                failures.append(piece)
                if IntakeRepository(connection).find_for_piece(scenario.round_id, piece) is not None:
                    raise RuntimeError('interrupted intake left a committed conclusion')
                original = DeviceRecording(connection).get_terminal(terminal.trigger_id)
                if original != terminal:
                    raise RuntimeError('original terminal unavailable for intake retry')
                result = accept(connection, order_id, original, before_commit=hook)
                if accept(connection, order_id, original) != result:
                    raise RuntimeError('intake replay changed the original result')
            if result.receipt is not None:
                receipts[piece] = result.receipt.number
            if len(arrivals) == scenario.close_after:
                early = close(connection, scenario.round_id)
                if early.report is not None:
                    raise RuntimeError('round sealed during the incomplete intake check')
                pending = list(early.pending)
    return receipts, {'arrival': arrivals, 'pending': pending, 'failed_once': failures}


def _reopen_archive(database: str | Path, report, receipts: dict[str, str]) -> dict:
    with closing(sqlite3.connect(database)) as offline:
        reloaded = get_round(offline, report.number)
        if reloaded != report:
            raise RuntimeError('reopened report differs from the sealed result')
        samples = {}
        for entry in reloaded.conclusions:
            if entry.piece not in receipts:
                continue
            sample = get_sample(offline, receipts[entry.piece])
            if (sample.round_id, sample.piece, sample.order_id, sample.trigger_id,
                sample.source_id, sample.grade) != (entry.round_id, entry.piece, entry.order_id,
                                                    entry.trigger_id, entry.source_id, entry.grade):
                raise RuntimeError('reopened sample differs from its sealed conclusion')
            samples[entry.piece] = {'tick': sample.tick, 'raw_code': sample.raw_code,
                                    'grade': sample.grade.value}
        return {
            'pieces': [[entry.piece, entry.grade.value] for entry in reloaded.conclusions],
            'counts': asdict(reloaded.counts),
            'samples': samples,
        }


def run_scenario(database: str | Path, fixture_path: str | Path) -> dict:
    scenario = load_fixture(fixture_path)
    with closing(sqlite3.connect(database)) as connection:
        initialize_station(connection)
        if RoundRepository(connection).find_round(scenario.round_id) is not None:
            raise ValueError('scenario run requires an unused round ID')
        piece_names = [entry.piece for entry in scenario.exposures]
        if len(piece_names) != len(scenario.roster) or set(piece_names) != set(scenario.roster):
            raise ValueError('scenario requires exactly one exposure entry per roster piece')
        if type(scenario.close_after) is not int or not 0 < scenario.close_after < len(piece_names):
            raise ValueError('scenario early close must follow a proper nonempty subset of deliveries')
        if scenario.fail_once_for is not None and scenario.fail_once_for not in piece_names:
            raise ValueError('scenario failure must target a roster piece')
        open(connection, scenario.round_id, scenario.roster, scenario.limit)
        receipts, intake_actual = _run_intake(connection, scenario)
        report = close(connection, scenario.round_id).report
        if report is None:
            raise RuntimeError('round still pending after all scripted deliveries')
    archive = _reopen_archive(database, report, receipts)
    actual = {'intake': intake_actual, 'archive': archive}
    if actual != scenario.expected:
        raise RuntimeError(f'{scenario.case_id}: expected {scenario.expected!r}; got {actual!r}')
    evidence = {
        'case_id': scenario.case_id,
        'input': {'round_id': scenario.round_id, 'roster': scenario.roster, 'limit': scenario.limit,
                  'exposures': [asdict(entry) for entry in scenario.exposures],
                  'close_after': scenario.close_after, 'fail_once_for': scenario.fail_once_for},
        'expected': scenario.expected['intake'],
        'actual': intake_actual,
        'downstream_expected': scenario.expected['archive'],
        'downstream_actual': archive,
        'code_paths': ['station/device/optical.py', 'station/intake/accept.py',
                       'station/intake/grading.py', 'station/closing/seal.py',
                       'station/lookup/sample.py', 'station/lookup/round.py'],
        'oracle': 'Hand-calculated fixture delivery order, strict integer comparison, roster tally and original ticks.',
    }
    print('POINTWISE_SCENARIO ' + json.dumps(evidence, sort_keys=True))
    result = {'report': report.number, 'receipts': receipts}
    print(json.dumps(result, sort_keys=True))
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Run or audit the optical inspection station')
    actions = parser.add_subparsers(dest='action', required=True)
    actions.add_parser('init').add_argument('database')
    demo = actions.add_parser('demo')
    demo.add_argument('fixtures', nargs='+')
    run = actions.add_parser('run')
    run.add_argument('database')
    run.add_argument('fixture')
    for name in ('sample', 'round'):
        lookup = actions.add_parser(name)
        lookup.add_argument('database')
        lookup.add_argument('number')
    args = parser.parse_args(argv)
    if args.action == 'demo':
        for fixture in args.fixtures:
            with TemporaryDirectory(prefix='optical-station-') as directory:
                run_scenario(Path(directory) / 'station.sqlite3', fixture)
    elif args.action == 'run':
        run_scenario(args.database, args.fixture)
    elif args.action == 'init':
        with closing(sqlite3.connect(args.database)) as connection:
            initialize_station(connection)
    else:
        database_uri = Path(args.database).resolve().as_uri() + '?mode=ro'
        with closing(sqlite3.connect(database_uri, uri=True)) as offline:
            retrieve = get_sample if args.action == 'sample' else get_round
            print(json.dumps(asdict(retrieve(offline, args.number)), sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
