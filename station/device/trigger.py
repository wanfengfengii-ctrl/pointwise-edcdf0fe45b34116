import sqlite3

from station.device.recording import DeviceRecording, ExposureRecord
from station.rounds.authorization import require_trigger_binding
from station.rounds.repository import RoundRepository, transaction


def trigger(connection: sqlite3.Connection, round_id: str, piece: str,
            order_id: str) -> ExposureRecord:
    with transaction(connection, write=True):
        order = require_trigger_binding(RoundRepository(connection), round_id, piece, order_id)
        return DeviceRecording(connection).start(order)
