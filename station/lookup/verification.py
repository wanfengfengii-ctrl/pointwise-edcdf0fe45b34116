from station.closing.repository import RoundReport
from station.closing.tally import tally_conclusions
from station.intake.grading import Grade
from station.intake.receipt import SampleReceipt
from station.intake.repository import IntakeRepository, PieceConclusion, SourceTrace
from station.rounds.repository import RoundRecord


class ReferenceIntegrityError(RuntimeError):
    """A known public number points to incomplete or inconsistent persisted evidence."""


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
        raise ReferenceIntegrityError(f'original {source.source_id!r} conflicts with the conclusion')
    return source


def verify_report_snapshot(report: RoundReport, round_record: RoundRecord | None,
                           intake: IntakeRepository) -> tuple[PieceConclusion, ...]:
    if round_record is None or round_record.round_id != report.round_id or round_record.phase != 'sealed':
        raise ReferenceIntegrityError(f'report {report.number!r} lacks its sealed round')
    if len(report.lines) != len(round_record.roster.pieces):
        raise ReferenceIntegrityError(f'report {report.number!r} has incomplete roster coverage')
    conclusions = []
    for position, (line, piece) in enumerate(zip(report.lines, round_record.roster.pieces)):
        if (line.position, line.piece) != (position, piece):
            raise ReferenceIntegrityError(f'report {report.number!r} conflicts with roster position {position}')
        conclusion = intake.get_conclusion(line.conclusion_id)
        if conclusion is None:
            raise ReferenceIntegrityError(f'report {report.number!r} lacks conclusion {line.conclusion_id}')
        if (conclusion.round_id, conclusion.piece) != (report.round_id, piece):
            raise ReferenceIntegrityError(f'report {report.number!r} has a misbound conclusion')
        conclusions.append(conclusion)
    if tally_conclusions(conclusions) != report.counts:
        raise ReferenceIntegrityError(f'report {report.number!r} has inconsistent category counts')
    return tuple(conclusions)
