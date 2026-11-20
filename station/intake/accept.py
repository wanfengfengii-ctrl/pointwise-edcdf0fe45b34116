import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from station.device.gain_fixture import GainOriginal
from station.device.terminal import RawReading, Terminal
from station.intake.binding import verify_terminal
from station.intake.gain import (CONFLICT, INDETERMINATE, NEED_LOW, GainStore,
                                 IntervalConclusion, classify, validate_original)
from station.intake.grading import Grade, grade_terminal
from station.intake.receipt import ReceiptStore, SampleReceipt
from station.intake.repository import IntakeRepository, PieceConclusion
from station.lookup.verification import ReferenceIntegrityError, verify_interval_conclusion
from station.rounds.repository import RoundRepository, transaction
from station.rounds.roster import require_name


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


def _result(status: str, receipt: str | None, effective) -> dict:
    return {'status': status, 'receipt': receipt, 'effective_interval': effective}


def _current(store: GainStore, order_id: str, limit: int) -> dict:
    """Derive the current read-order state from the accepted originals alone."""
    high = store.find_original(order_id, 'high')
    low = store.find_original(order_id, 'low')
    if high is None:
        return _result(NEED_LOW, None, None)
    status, _, effective = classify(high, low, limit)
    return _result(status, None, effective)


def _signed(store: GainStore, conclusion: IntervalConclusion, limit: int) -> dict:
    effective = verify_interval_conclusion(store, conclusion, limit)
    receipt = store.find_receipt_for_conclusion(conclusion.conclusion_id)
    if conclusion.grade != Grade.MISSING and receipt is None:
        raise ReferenceIntegrityError('a signed interval conclusion has no receipt')
    return _result(conclusion.grade.value, receipt, effective)


def _require_completed_high(head, store: GainStore, order_id: str) -> None:
    high_exposure = store.find_exposure(order_id, 'high')
    if high_exposure is None:
        raise ValueError('a low original requires the same order high exposure')
    try:
        high = head.read(high_exposure.exposure_id)
    except KeyError:
        high = None
    if high is None or high.exposure != high_exposure:
        raise ValueError('the same order high original is not available on the head')


def _verify_whole_original(head, order_id: str, stored_exposure, delivered: GainOriginal) -> None:
    exposure = delivered.exposure
    if stored_exposure != exposure:
        raise ValueError('delivered original conflicts with the recorded gain exposure')
    if (exposure.piece, exposure.gain) != (stored_exposure.piece, stored_exposure.gain):
        raise ValueError('delivered original conflicts with the recorded gain exposure')
    try:
        source = head.read(exposure.exposure_id)
    except KeyError:
        source = None
    if source is None:
        raise ValueError('the gain head has not completed this exposure')
    if source != delivered:
        raise ValueError('delivered original differs from the gain head original')


def accept_interval(connection: sqlite3.Connection, head, order_id: str,
                    original: GainOriginal, *,
                    before_commit: Callable[[], None] | None = None) -> dict:
    require_name(order_id, 'order_id')
    original = validate_original(original)
    store = GainStore(connection)
    with transaction(connection, write=True):
        order = RoundRepository(connection).find_order(order_id)
        if order is None:
            raise KeyError(f'unknown read order {order_id!r}')
        if order.mode != 'interval':
            raise ValueError('point read orders cannot receive an interval original')
        limit = RoundRepository(connection).require_round(order.round_id).policy.limit
        exposure = original.exposure
        gain = exposure.gain

        stored_exposure = store.find_exposure_by_id(exposure.exposure_id)
        if stored_exposure is None:
            raise ValueError('the delivered original was never fired for this station')
        if store.find_exposure_order(exposure.exposure_id) != order_id:
            raise ValueError('the delivered original is bound to a different read order')
        _verify_whole_original(head, order_id, stored_exposure, original)

        recorded = store.find_original(order_id, gain)
        if recorded is not None:
            if recorded != original:
                raise ValueError('delivered original conflicts with the accepted original')
            existing = store.find_conclusion_for_piece(order.round_id, order.piece)
            if existing is None:
                return _current(store, order_id, limit)
            return _signed(store, existing, limit)

        existing = store.find_conclusion_for_piece(order.round_id, order.piece)
        if existing is not None:
            raise ValueError('the interval read order has already been signed')
        if gain == 'low' and store.find_original(order_id, 'high') is None:
            _require_completed_high(head, store, order_id)

        store.record_original(order_id, original)
        high = store.find_original(order_id, 'high')
        low = store.find_original(order_id, 'low')
        if high is None:
            return _result(NEED_LOW, None, None)
        status, grade, effective = classify(high, low, limit)
        if status in (NEED_LOW, CONFLICT, INDETERMINATE):
            return _result(status, None, None)
        conclusion = store.insert_conclusion(order.round_id, order.piece, order_id, grade,
                                             high.exposure.exposure_id,
                                             None if low is None else low.exposure.exposure_id)
        receipt = None if grade == Grade.MISSING else store.issue_receipt(conclusion)
        if before_commit is not None:
            before_commit()
        return _result(grade.value, receipt,
                       None if grade == Grade.MISSING else effective)
