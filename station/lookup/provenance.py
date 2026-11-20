from dataclasses import dataclass

from station.intake.repository import IntakeRepository, PieceConclusion
from station.lookup.verification import verify_source_link


@dataclass(frozen=True)
class SampleProvenance:
    source_id: str
    trigger_id: str
    tick: int
    raw_code: int


def load_provenance(intake: IntakeRepository, conclusion: PieceConclusion) -> SampleProvenance:
    source = verify_source_link(conclusion, intake.read_source(conclusion.source_id))
    return SampleProvenance(source.source_id, source.trigger_id, source.tick, source.raw_code)
