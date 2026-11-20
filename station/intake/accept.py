import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from station.device.terminal import RawReading, Terminal
from station.intake.binding import verify_terminal
from station.intake.grading import Grade, grade_terminal
from station.intake.receipt import ReceiptStore, SampleReceipt
from station.intake.repository import IntakeRepository, PieceConclusion
from station.rounds.repository import transaction


@dataclass(frozen=True)
class AcceptanceResult:
    conclusion: PieceConclusion
    receipt: SampleReceipt | None


def _replay(existing: PieceConclusion, terminal: Terminal,
            receipts: ReceiptStore) -> AcceptanceResult:
    if (existing.order_id, existing.trigger_id, existing.source_id) != (
        terminal.order_id, terminal.trigger_id, terminal.source_id
    ):
        raise ValueError('delivered terminal conflicts with the accepted conclusion')
    receipt = receipts.find_for_conclusion(existing.conclusion_id)
    if isinstance(terminal, RawReading):
        if existing.grade == Grade.MISSING or receipt is None:
            raise RuntimeError('accepted reading has an incomplete conclusion or receipt')
    elif existing.grade != Grade.MISSING or receipt is not None:
        raise RuntimeError('accepted missing terminal has an inconsistent conclusion')
    return AcceptanceResult(existing, receipt)


def accept(connection: sqlite3.Connection, order_id: str, terminal: Terminal, *,
           before_commit: Callable[[], None] | None = None) -> AcceptanceResult:
    intake = IntakeRepository(connection)
    receipts = ReceiptStore(connection)
    with transaction(connection, write=True):
        verified = verify_terminal(connection, order_id, terminal)
        original = verified.terminal
        existing = intake.find_for_piece(original.round_id, original.piece)
        if existing is not None:
            return _replay(existing, original, receipts)
        grade = grade_terminal(original, verified.policy)
        conclusion = intake.insert_conclusion(original, grade)
        receipt = receipts.issue(conclusion) if grade != Grade.MISSING else None
        if before_commit is not None:
            before_commit()
    return AcceptanceResult(conclusion, receipt)
