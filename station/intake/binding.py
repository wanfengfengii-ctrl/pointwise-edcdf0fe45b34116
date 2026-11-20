import sqlite3
from dataclasses import dataclass

from station.device.recording import DeviceRecording
from station.device.terminal import ConfirmedMissing, RawReading, Terminal
from station.rounds.policy import LimitPolicy
from station.rounds.repository import RoundRepository
from station.rounds.roster import require_name


@dataclass(frozen=True)
class VerifiedTerminal:
    terminal: Terminal
    policy: LimitPolicy


def verify_terminal(connection: sqlite3.Connection, order_id: str,
                    delivered: Terminal) -> VerifiedTerminal:
    require_name(order_id, 'order_id')
    if type(delivered) not in (RawReading, ConfirmedMissing) or delivered.order_id != order_id:
        raise ValueError('delivered terminal does not match the requested read order')
    rounds = RoundRepository(connection)
    order = rounds.find_order(order_id)
    if order is None:
        raise KeyError(f'unknown read order {order_id!r}')
    recordings = DeviceRecording(connection)
    exposure = recordings.find_exposure(order_id)
    if exposure is None:
        raise ValueError(f'read order {order_id!r} has not triggered')
    if (delivered.round_id, delivered.piece, delivered.trigger_id) != (
        order.round_id, order.piece, exposure.trigger_id
    ):
        raise ValueError('delivered terminal has a conflicting original binding')
    original = recordings.get_terminal(exposure.trigger_id)
    if original is None or delivered != original:
        raise ValueError('delivered terminal differs from the device original')
    return VerifiedTerminal(original, rounds.require_round(order.round_id).policy)
