from station.closing.repository import RoundReport
from station.closing.tally import tally_conclusions
from station.intake.gain import GainStore, IntervalConclusion, classify
from station.intake.grading import Grade
from station.intake.receipt import SampleReceipt
from station.intake.repository import IntakeRepository, PieceConclusion, SourceTrace
from station.rounds.repository import RoundRecord

Conclusion = PieceConclusion | IntervalConclusion


class ReferenceIntegrityError(RuntimeError):
    """A known public number points to incomplete or inconsistent persisted evidence."""


def verify_interval_conclusion(store: GainStore, conclusion: IntervalConclusion,
                               limit: int) -> tuple[int, int | None] | None:
    high = store.find_original(conclusion.order_id, 'high')
    low = store.find_original(conclusion.order_id, 'low')
    if high is None:
        raise ReferenceIntegrityError(
            f'interval conclusion {conclusion.conclusion_id} lacks its high original')
    status, grade, effective = classify(high, low, limit)
    if status != conclusion.grade.value:
        raise ReferenceIntegrityError(
            f'interval conclusion {conclusion.conclusion_id} conflicts with its originals')
    if conclusion.high_exposure_id != high.exposure.exposure_id:
        raise ReferenceIntegrityError(
            f'interval conclusion {conclusion.conclusion_id} misbinds its high exposure')
    if (low is None) != (conclusion.low_exposure_id is None):
        raise ReferenceIntegrityError(
            f'interval conclusion {conclusion.conclusion_id} misbinds its low exposure')
    if low is not None and conclusion.low_exposure_id != low.exposure.exposure_id:
        raise ReferenceIntegrityError(
            f'interval conclusion {conclusion.conclusion_id} misbinds its low exposure')
    receipt_count = store.connection.execute(
        'SELECT COUNT(*) FROM gain_receipts WHERE conclusion_id = ?',
        (conclusion.conclusion_id,),
    ).fetchone()[0]
    if conclusion.grade == Grade.MISSING:
        if receipt_count:
            raise ReferenceIntegrityError('a missing conclusion must not have a receipt')
        return None
    if receipt_count != 1:
        raise ReferenceIntegrityError('a signed interval conclusion needs exactly one receipt')
    return effective


def verify_receipt_link(receipt: SampleReceipt,
                        conclusion: PieceConclusion | None) -> PieceConclusion:
    if conclusion is None:
        raise ReferenceIntegrityError(f'receipt {receipt.number!r} has no conclusion')
    if receipt.conclusion_id != conclusion.conclusion_id or conclusion.grade == Grade.MISSING:
        raise ReferenceIntegrityError(f'receipt {receipt.number!r} has an inconsistent conclusion')
    return conclusion


def verify_source_link(conclusion: PieceConclusion, source: SourceTrace | None) -> SourceTrace:
    if source is None:
        raise ReferenceIntegrityError(f'missing original {conclusion.source_id!r}')
    recorded = (source.source_id, source.round_id, source.piece, source.order_id, source.trigger_id)
    fixed = (conclusion.source_id, conclusion.round_id, conclusion.piece,
             conclusion.order_id, conclusion.trigger_id)
    if recorded != fixed or source.tick is None or source.raw_code is None:
        raise ReferenceIntegrityError(f'original {conclusion.source_id!r} conflicts with the conclusion')
    return source


def verify_report_snapshot(report: RoundReport, round_record: RoundRecord | None,
                           intake: IntakeRepository,
                           gain: GainStore | None = None) -> tuple[Conclusion, ...]:
    if round_record is None or round_record.round_id != report.round_id or round_record.phase != 'sealed':
        raise ReferenceIntegrityError(f'report {report.number!r} lacks its sealed round')
    if len(report.lines) != len(round_record.roster.pieces):
        raise ReferenceIntegrityError(f'report {report.number!r} has incomplete roster coverage')
    if gain is None:
        gain = GainStore(intake.connection)
    conclusions = []
    for position, (line, piece) in enumerate(zip(report.lines, round_record.roster.pieces)):
        if (line.position, line.piece) != (position, piece):
            raise ReferenceIntegrityError(f'report {report.number!r} conflicts with roster position {position}')
        if line.kind == 'interval':
            conclusion = gain.get_conclusion(line.conclusion_id)
        else:
            conclusion = intake.get_conclusion(line.conclusion_id)
        if conclusion is None:
            raise ReferenceIntegrityError(f'report {report.number!r} lacks conclusion {line.conclusion_id}')
        if (conclusion.round_id, conclusion.piece) != (report.round_id, piece):
            raise ReferenceIntegrityError(f'report {report.number!r} has a misbound conclusion')
        if line.kind == 'interval':
            limit = round_record.policy.limit
            verify_interval_conclusion(gain, conclusion, limit)
        conclusions.append(conclusion)
    if tally_conclusions(conclusions) != report.counts:
        raise ReferenceIntegrityError(f'report {report.number!r} has inconsistent category counts')
    return tuple(conclusions)
