from dataclasses import dataclass

from station.intake.gain import GainStore, IntervalConclusion
from station.intake.repository import IntakeRepository, PieceConclusion
from station.rounds.repository import RoundRecord

Conclusion = PieceConclusion | IntervalConclusion


@dataclass(frozen=True)
class ClosureReadiness:
    pending: tuple[str, ...]
    conclusions: tuple[Conclusion, ...]


def check_readiness(round_record: RoundRecord, intake: IntakeRepository,
                    gain: GainStore | None = None) -> ClosureReadiness:
    if gain is None:
        gain = GainStore(intake.connection)
    entries = tuple(intake.list_for_round(round_record.round_id)) + \
        tuple(gain.list_conclusions_for_round(round_record.round_id))
    by_piece = {entry.piece: entry for entry in entries}
    pending = []
    concluded = []
    for piece in round_record.roster.pieces:
        if piece in by_piece:
            concluded.append(by_piece[piece])
        else:
            pending.append(piece)
    return ClosureReadiness(tuple(pending), tuple(concluded))
