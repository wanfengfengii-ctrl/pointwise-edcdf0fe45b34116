import sqlite3

from station.device.gain_fixture import GainExposure, GainHead
from station.device.recording import DeviceRecording, ExposureRecord
from station.intake.gain import NEED_LOW, GainStore, classify
from station.rounds.authorization import require_trigger_binding
from station.rounds.repository import ReadOrder, RoundRepository, transaction


def trigger(connection: sqlite3.Connection, round_id: str, piece: str,
            order_id: str) -> ExposureRecord:
    with transaction(connection, write=True):
        order = require_trigger_binding(RoundRepository(connection), round_id, piece, order_id)
        return DeviceRecording(connection).start(order)


def _interval_order(connection: sqlite3.Connection, round_id: str, piece: str,
                    order_id: str) -> ReadOrder:
    rounds = RoundRepository(connection)
    order = rounds.find_order(order_id)
    if order is None:
        raise ValueError('interval trigger does not match an authorized read order')
    if order.mode != 'interval':
        raise ValueError('old point triggers cannot fire interval orders')
    if (order.round_id, order.piece) != (round_id, piece):
        raise ValueError('interval trigger does not match an authorized read order')
    if rounds.require_round(round_id).phase != 'open':
        raise ValueError(f'round {round_id!r} is sealed')
    return order


def _require_eligible_low(connection: sqlite3.Connection, store: GainStore, head: GainHead,
                          order: ReadOrder) -> None:
    high_exposure = store.find_exposure(order.order_id, 'high')
    if high_exposure is None:
        raise ValueError('cannot request a low gain before the high exposure was fired')
    if high_exposure.device_id != head.device_id:
        raise ValueError('read order is bound to a different gain head')
    high = store.find_original(order.order_id, 'high')
    if high is None:
        try:
            high = head.read(high_exposure.exposure_id)
        except KeyError:
            high = None
    if high is None:
        raise ValueError('cannot request a low gain before the high original was completed')
    if high.exposure != high_exposure:
        raise ValueError('the high original is bound to a different physical exposure')
    limit = RoundRepository(connection).require_round(order.round_id).policy.limit
    status, _, _ = classify(high, None, limit)
    if status != NEED_LOW:
        raise ValueError('the high original already decides the grade or has no usable read')


def trigger_interval(connection: sqlite3.Connection, head: GainHead, round_id: str,
                     piece: str, order_id: str, gain: str) -> GainExposure:
    if gain not in ('high', 'low'):
        raise ValueError('interval trigger gain must be high or low')
    tag = f'{round_id}/{piece}/{gain}'
    with transaction(connection, write=True):
        store = GainStore(connection)
        order = _interval_order(connection, round_id, piece, order_id)
        bound_device = store.device_for_order(order_id)
        if bound_device is not None and bound_device != head.device_id:
            raise ValueError('read order is bound to a different gain head')
        stored = store.find_exposure(order_id, gain)
        if stored is not None:
            return stored
        for exposure in head.history():
            if exposure.tag == tag:
                if (exposure.piece, exposure.gain) != (piece, gain):
                    raise ValueError('the gain head records a conflicting exposure for this tag')
                return store.insert_exposure(order, exposure)
        if gain == 'low':
            _require_eligible_low(connection, store, head, order)
        exposure = head.fire(piece, gain, tag)
        return store.insert_exposure(order, exposure)
