import sqlite3
from dataclasses import dataclass

from station.device.terminal import ConfirmedMissing, RawReading, Terminal
from station.rounds.repository import ReadOrder


@dataclass(frozen=True)
class ExposureRecord:
    round_id: str
    piece: str
    order_id: str
    trigger_id: str


def _decode_terminal(row) -> Terminal | None:
    if row is None:
        return None
    source_id, round_id, piece, order_id, trigger_id, kind, tick, code = row
    binding = (source_id, round_id, piece, order_id, trigger_id)
    if kind == 'missing':
        return ConfirmedMissing(*binding)
    return RawReading(*binding, int(tick, 16), int(code, 16))


_TERMINAL_SELECT = '''
    SELECT terminal.source_id, orders.round_id, orders.piece, orders.order_id,
           exposure.trigger_id, terminal.kind, terminal.physical_tick, terminal.raw_code
    FROM device_terminals AS terminal
    JOIN exposures AS exposure ON exposure.trigger_id = terminal.trigger_id
    JOIN read_orders AS orders ON orders.order_id = exposure.order_id
'''


class DeviceRecording:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def initialize(self) -> None:
        self.connection.execute('''
            CREATE TABLE IF NOT EXISTS exposures (
                trigger_id TEXT PRIMARY KEY NOT NULL,
                order_id TEXT NOT NULL UNIQUE REFERENCES read_orders(order_id)
            )
        ''')
        self.connection.execute('''
            CREATE TABLE IF NOT EXISTS device_terminals (
                source_id TEXT PRIMARY KEY NOT NULL,
                trigger_id TEXT NOT NULL UNIQUE REFERENCES exposures(trigger_id),
                kind TEXT NOT NULL CHECK (kind IN ('reading', 'missing')),
                physical_tick TEXT,
                raw_code TEXT,
                CHECK ((kind = 'reading' AND physical_tick IS NOT NULL AND raw_code IS NOT NULL)
                    OR (kind = 'missing' AND physical_tick IS NULL AND raw_code IS NULL))
            )
        ''')

    def start(self, order: ReadOrder) -> ExposureRecord:
        existing = self.find_exposure(order.order_id)
        exposure = ExposureRecord(order.round_id, order.piece, order.order_id,
                                  f'trigger:{order.order_id}')
        if existing is not None:
            if existing != exposure:
                raise ValueError('read order conflicts with its original exposure')
            return existing
        self.connection.execute(
            'INSERT INTO exposures (trigger_id, order_id) VALUES (?, ?)',
            (exposure.trigger_id, order.order_id),
        )
        return exposure

    def find_exposure(self, order_id: str) -> ExposureRecord | None:
        row = self.connection.execute('''
            SELECT orders.round_id, orders.piece, orders.order_id, exposure.trigger_id
            FROM exposures AS exposure
            JOIN read_orders AS orders ON orders.order_id = exposure.order_id
            WHERE orders.order_id = ?
        ''', (order_id,)).fetchone()
        return None if row is None else ExposureRecord(*row)

    def finalize(self, terminal: Terminal) -> Terminal:
        exposure = self.find_exposure(terminal.order_id)
        if exposure != ExposureRecord(terminal.round_id, terminal.piece,
                                      terminal.order_id, terminal.trigger_id):
            raise ValueError('terminal does not belong to the original exposure')
        existing = self.get_terminal(terminal.trigger_id)
        if existing is not None:
            if existing != terminal:
                raise ValueError('observation conflicts with the fixed device terminal')
            return existing
        if isinstance(terminal, RawReading):
            data = ('reading', hex(terminal.tick), hex(terminal.raw_code))
        else:
            data = ('missing', None, None)
        self.connection.execute('''
            INSERT INTO device_terminals (source_id, trigger_id, kind, physical_tick, raw_code)
            VALUES (?, ?, ?, ?, ?)
        ''', (terminal.source_id, terminal.trigger_id, *data))
        return terminal

    def get_terminal(self, trigger_id: str) -> Terminal | None:
        row = self.connection.execute(
            _TERMINAL_SELECT + ' WHERE terminal.trigger_id = ?', (trigger_id,)
        ).fetchone()
        return _decode_terminal(row)

    def find_by_source(self, source_id: str) -> Terminal | None:
        row = self.connection.execute(
            _TERMINAL_SELECT + ' WHERE terminal.source_id = ?', (source_id,)
        ).fetchone()
        return _decode_terminal(row)
