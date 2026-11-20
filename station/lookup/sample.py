import sqlite3
from dataclasses import dataclass

from station.intake.gain import GainStore
from station.intake.grading import Grade
from station.intake.receipt import ReceiptStore
from station.intake.repository import IntakeRepository
from station.lookup.provenance import load_provenance
from station.lookup.verification import (ReferenceIntegrityError, verify_interval_conclusion,
                                         verify_receipt_link)
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
        store = GainStore(connection)
        if store.find_receipt(receipt_number) is not None:
            raise ValueError(f'receipt {receipt_number!r} is an interval receipt')
        receipt = ReceiptStore(connection).find_by_number(receipt_number)
        if receipt is None:
            raise KeyError(f'unknown sample receipt {receipt_number!r}')
        intake = IntakeRepository(connection)
        conclusion = verify_receipt_link(receipt, intake.get_conclusion(receipt.conclusion_id))
        source = load_provenance(intake, conclusion)
        return SampleResult(receipt.number, conclusion.round_id, conclusion.piece, conclusion.order_id,
                            source.source_id, source.trigger_id, source.tick, source.raw_code,
                            conclusion.grade)


def _limit(connection: sqlite3.Connection, round_id: str) -> int:
    row = connection.execute(
        'SELECT limit_code FROM rounds WHERE round_id = ?', (round_id,)
    ).fetchone()
    if row is None:
        raise ReferenceIntegrityError(f'round {round_id!r} has no frozen limit')
    return int(row[0], 16)


def _adopted_originals(store: GainStore, conclusion) -> tuple:
    originals = store.list_originals(conclusion.order_id)
    adopted = [conclusion.high_exposure_id]
    if conclusion.low_exposure_id is not None:
        adopted.append(conclusion.low_exposure_id)
    recorded = [original.exposure.exposure_id for original in originals]
    if recorded != adopted:
        raise ReferenceIntegrityError(
            f'interval conclusion {conclusion.conclusion_id} misbinds its adopted originals')
    return originals


def get_interval_sample(connection: sqlite3.Connection, receipt_number: str) -> dict:
    require_name(receipt_number, 'receipt_number')
    with transaction(connection):
        store = GainStore(connection)
        receipt = store.find_receipt(receipt_number)
        if receipt is None:
            if ReceiptStore(connection).find_by_number(receipt_number) is None:
                raise KeyError(f'unknown interval receipt {receipt_number!r}')
            raise ValueError(f'receipt {receipt_number!r} is a point receipt')
        conclusion = store.get_conclusion(receipt[1])
        if conclusion is None:
            raise ReferenceIntegrityError(f'receipt {receipt_number!r} has no conclusion')
        if conclusion.grade == Grade.MISSING:
            raise ReferenceIntegrityError(f'receipt {receipt_number!r} signs a missing result')
        effective = verify_interval_conclusion(store, conclusion,
                                               _limit(connection, conclusion.round_id))
        originals = _adopted_originals(store, conclusion)
        if not originals or originals[0].exposure.gain != 'high':
            raise ReferenceIntegrityError(f'receipt {receipt_number!r} lacks its adopted originals')
        return {
            'receipt_number': receipt_number,
            'round_id': conclusion.round_id,
            'piece': conclusion.piece,
            'order_id': conclusion.order_id,
            'grade': conclusion.grade.value,
            'effective_interval': effective,
            'originals': originals,
        }
