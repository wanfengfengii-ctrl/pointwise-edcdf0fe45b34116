import sqlite3
from dataclasses import dataclass

from station.intake.grading import Grade
from station.intake.receipt import ReceiptStore
from station.intake.repository import IntakeRepository
from station.lookup.provenance import load_provenance
from station.lookup.verification import verify_receipt_link
from station.rounds.repository import transaction
from station.rounds.roster import require_name


@dataclass(frozen=True)
class SampleResult:
    receipt_number: str
    round_id: str
    piece: str
    order_id: str
    source_id: str
    trigger_id: str
    tick: int
    raw_code: int
    grade: Grade


def get_sample(connection: sqlite3.Connection, receipt_number: str) -> SampleResult:
    require_name(receipt_number, 'receipt_number')
    with transaction(connection):
        receipt = ReceiptStore(connection).find_by_number(receipt_number)
        if receipt is None:
            raise KeyError(f'unknown sample receipt {receipt_number!r}')
        intake = IntakeRepository(connection)
        conclusion = verify_receipt_link(receipt, intake.get_conclusion(receipt.conclusion_id))
        source = load_provenance(intake, conclusion)
        return SampleResult(receipt.number, conclusion.round_id, conclusion.piece, conclusion.order_id,
                            source.source_id, source.trigger_id, source.tick, source.raw_code,
                            conclusion.grade)
