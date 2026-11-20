from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass

from station.intake.grading import Grade
from station.intake.repository import PieceConclusion


@dataclass(frozen=True)
class CategoryCounts:
    passed: int
    over_limit: int
    missing: int


def tally_conclusions(conclusions: Iterable[PieceConclusion]) -> CategoryCounts:
    counts = Counter(entry.grade for entry in conclusions)
    return CategoryCounts(counts[Grade.PASS], counts[Grade.OVER_LIMIT], counts[Grade.MISSING])
