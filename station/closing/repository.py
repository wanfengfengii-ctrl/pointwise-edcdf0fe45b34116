import sqlite3
from dataclasses import dataclass

from station.closing.tally import CategoryCounts
from station.intake.gain import IntervalConclusion
from station.intake.repository import PieceConclusion

Conclusion = PieceConclusion | IntervalConclusion


def conclusion_kind(conclusion: Conclusion) -> str:
    return 'interval' if isinstance(conclusion, IntervalConclusion) else 'point'


@dataclass(frozen=True)
class ReportLine:
    position: int
    piece: str
    conclusion_id: int
    kind: str = 'point'


@dataclass(frozen=True)
class RoundReport:
    number: str
    round_id: str
    counts: CategoryCounts
    lines: tuple[ReportLine, ...]


class ClosingRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def initialize(self) -> None:
        self.connection.execute('''
            CREATE TABLE IF NOT EXISTS round_reports (
                number TEXT PRIMARY KEY NOT NULL,
                round_id TEXT NOT NULL UNIQUE REFERENCES rounds(round_id),
                passed INTEGER NOT NULL CHECK (passed >= 0),
                over_limit INTEGER NOT NULL CHECK (over_limit >= 0),
                missing INTEGER NOT NULL CHECK (missing >= 0)
            )
        ''')
        self.connection.execute('''
            CREATE TABLE IF NOT EXISTS report_lines (
                report_number TEXT NOT NULL REFERENCES round_reports(number),
                position INTEGER NOT NULL CHECK (position >= 0),
                piece TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT 'point' CHECK (kind IN ('point', 'interval')),
                conclusion_id INTEGER NOT NULL,
                PRIMARY KEY (report_number, position),
                UNIQUE (report_number, piece),
                UNIQUE (report_number, kind, conclusion_id)
            )
        ''')
        self._upgrade_report_lines()

    def _upgrade_report_lines(self) -> None:
        references = self.connection.execute('PRAGMA foreign_key_list(report_lines)').fetchall()
        columns = {row[1] for row in self.connection.execute('PRAGMA table_info(report_lines)')}
        needs_kind = 'kind' not in columns
        needs_rebuild = needs_kind or any(row[2] == 'piece_conclusions' for row in references)
        if not needs_rebuild:
            return
        self.connection.execute('ALTER TABLE report_lines RENAME TO report_lines_legacy')
        self.connection.execute('''
            CREATE TABLE report_lines (
                report_number TEXT NOT NULL REFERENCES round_reports(number),
                position INTEGER NOT NULL CHECK (position >= 0),
                piece TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT 'point' CHECK (kind IN ('point', 'interval')),
                conclusion_id INTEGER NOT NULL,
                PRIMARY KEY (report_number, position),
                UNIQUE (report_number, piece),
                UNIQUE (report_number, kind, conclusion_id)
            )
        ''')
        self.connection.execute('''
            INSERT INTO report_lines (report_number, position, piece, kind, conclusion_id)
            SELECT report_number, position, piece, 'point', conclusion_id FROM report_lines_legacy
        ''')
        self.connection.execute('DROP TABLE report_lines_legacy')

    def insert_snapshot(self, round_id: str, counts: CategoryCounts,
                        conclusions: tuple[Conclusion, ...]) -> RoundReport:
        number = f'round:{round_id}'
        lines = tuple(ReportLine(position, entry.piece, entry.conclusion_id,
                                 conclusion_kind(entry))
                      for position, entry in enumerate(conclusions))
        self.connection.execute('''
            INSERT INTO round_reports (number, round_id, passed, over_limit, missing)
            VALUES (?, ?, ?, ?, ?)
        ''', (number, round_id, counts.passed, counts.over_limit, counts.missing))
        self.connection.executemany('''
            INSERT INTO report_lines (report_number, position, piece, kind, conclusion_id)
            VALUES (?, ?, ?, ?, ?)
        ''', ((number, line.position, line.piece, line.kind, line.conclusion_id) for line in lines))
        return RoundReport(number, round_id, counts, lines)

    def _snapshot(self, row) -> RoundReport | None:
        if row is None:
            return None
        number, round_id, passed, over_limit, missing = row
        rows = self.connection.execute('''
            SELECT position, piece, conclusion_id, kind FROM report_lines
            WHERE report_number = ? ORDER BY position
        ''', (number,))
        return RoundReport(number, round_id, CategoryCounts(passed, over_limit, missing),
                           tuple(ReportLine(position, piece, conclusion_id, kind)
                                 for position, piece, conclusion_id, kind in rows))

    def find_by_round(self, round_id: str) -> RoundReport | None:
        row = self.connection.execute('''
            SELECT number, round_id, passed, over_limit, missing FROM round_reports WHERE round_id = ?
        ''', (round_id,)).fetchone()
        return self._snapshot(row)

    def find_by_number(self, number: str) -> RoundReport | None:
        row = self.connection.execute('''
            SELECT number, round_id, passed, over_limit, missing FROM round_reports WHERE number = ?
        ''', (number,)).fetchone()
        return self._snapshot(row)
