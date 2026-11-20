from dataclasses import dataclass

from station.intake.repository import IntakeRepository, PieceConclusion
from station.rounds.repository import RoundRecord


@dataclass(frozen=True)
class ClosureReadiness:
    pending: tuple[str, ...]
    conclusions: tuple[PieceConclusion, ...]


def check_readiness(round_record: RoundRecord, intake: IntakeRepository) -> ClosureReadiness:
    by_piece = {entry.piece: entry for entry in intake.list_for_round(round_record.round_id)}
    pending = []
    concluded = []
    for piece in round_record.roster.pieces:
        if piece in by_piece:
            concluded.append(by_piece[piece])
        else:
            pending.append(piece)
    return ClosureReadiness(tuple(pending), tuple(concluded))
