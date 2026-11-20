import sqlite3
from dataclasses import dataclass

from station.device.recording import DeviceRecording
from station.device.terminal import RawReading, Terminal
from station.intake.grading import Grade


@dataclass(frozen=True)
class PieceConclusion:
    conclusion_id: int
    round_id: str
    piece: str
    order_id: str
    trigger_id: str
    source_id: str
    grade: Grade


@dataclass(frozen=True)
class SourceTrace:
    source_id: str
    round_id: str
    piece: str
    order_id: str
    trigger_id: str
    tick: int | None
    raw_code: int | None


def _conclusion(row) -> PieceConclusion:
    conclusion_id, round_id, piece, order_id, trigger_id, source_id, grade = row
    return PieceConclusion(conclusion_id, round_id, piece, order_id, trigger_id,
                           source_id, Grade(grade))


_CONCLUSION_SELECT = '''
    SELECT conclusion_id, round_id, piece, order_id, trigger_id, source_id, grade
    FROM piece_conclusions
'''


class IntakeRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def initialize(self) -> None:
        self.connection.execute('''
            CREATE TABLE IF NOT EXISTS piece_conclusions (
                conclusion_id INTEGER PRIMARY KEY,
                round_id TEXT NOT NULL,
                piece TEXT NOT NULL,
                order_id TEXT NOT NULL UNIQUE REFERENCES read_orders(order_id),
                trigger_id TEXT NOT NULL UNIQUE REFERENCES exposures(trigger_id),
                source_id TEXT NOT NULL UNIQUE REFERENCES device_terminals(source_id),
                grade TEXT NOT NULL CHECK (grade IN ('pass', 'over_limit', 'missing')),
                UNIQUE (round_id, piece),
                FOREIGN KEY (round_id, piece) REFERENCES roster(round_id, piece)
            )
        ''')

    def find_for_piece(self, round_id: str, piece: str) -> PieceConclusion | None:
        row = self.connection.execute(
            _CONCLUSION_SELECT + ' WHERE round_id = ? AND piece = ?', (round_id, piece)
        ).fetchone()
        return None if row is None else _conclusion(row)

    def insert_conclusion(self, terminal: Terminal, grade: Grade) -> PieceConclusion:
        binding = (terminal.round_id, terminal.piece, terminal.order_id,
                   terminal.trigger_id, terminal.source_id)
        insert = self.connection.execute('''
            INSERT INTO piece_conclusions (round_id, piece, order_id, trigger_id, source_id, grade)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (*binding, grade.value))
        return PieceConclusion(insert.lastrowid, *binding, grade)

    def get_conclusion(self, conclusion_id: int) -> PieceConclusion | None:
        row = self.connection.execute(
            _CONCLUSION_SELECT + ' WHERE conclusion_id = ?', (conclusion_id,)
        ).fetchone()
        return None if row is None else _conclusion(row)

    def list_for_round(self, round_id: str) -> tuple[PieceConclusion, ...]:
        rows = self.connection.execute(
            _CONCLUSION_SELECT + ' WHERE round_id = ? ORDER BY conclusion_id', (round_id,)
        )
        return tuple(_conclusion(row) for row in rows)

    def read_source(self, source_id: str) -> SourceTrace | None:
        original = DeviceRecording(self.connection).find_by_source(source_id)
        if original is None:
            return None
        binding = (original.source_id, original.round_id, original.piece,
                   original.order_id, original.trigger_id)
        if isinstance(original, RawReading):
            return SourceTrace(*binding, original.tick, original.raw_code)
        return SourceTrace(*binding, None, None)
