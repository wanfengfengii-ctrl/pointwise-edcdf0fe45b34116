import sqlite3
from dataclasses import dataclass

from station.intake.repository import PieceConclusion


@dataclass(frozen=True)
class SampleReceipt:
    number: str
    conclusion_id: int


class ReceiptStore:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def initialize(self) -> None:
        self.connection.execute('''
            CREATE TABLE IF NOT EXISTS sample_receipts (
                number TEXT PRIMARY KEY NOT NULL,
                conclusion_id INTEGER NOT NULL UNIQUE REFERENCES piece_conclusions(conclusion_id)
            )
        ''')

    def issue(self, conclusion: PieceConclusion) -> SampleReceipt:
        receipt = SampleReceipt(f'sample:{conclusion.conclusion_id}', conclusion.conclusion_id)
        self.connection.execute(
            'INSERT INTO sample_receipts (number, conclusion_id) VALUES (?, ?)',
            (receipt.number, receipt.conclusion_id),
        )
        return receipt

    def find_for_conclusion(self, conclusion_id: int) -> SampleReceipt | None:
        row = self.connection.execute(
            'SELECT number, conclusion_id FROM sample_receipts WHERE conclusion_id = ?',
            (conclusion_id,),
        ).fetchone()
        return None if row is None else SampleReceipt(*row)

    def find_by_number(self, number: str) -> SampleReceipt | None:
        row = self.connection.execute(
            'SELECT number, conclusion_id FROM sample_receipts WHERE number = ?', (number,)
        ).fetchone()
        return None if row is None else SampleReceipt(*row)
