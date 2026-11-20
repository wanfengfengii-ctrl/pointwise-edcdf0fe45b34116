import sqlite3
from dataclasses import dataclass

from station.closing.readiness import check_readiness
from station.closing.repository import ClosingRepository
from station.closing.tally import tally_conclusions
from station.intake.repository import IntakeRepository
from station.lookup.round import RoundResult, resolve_report
from station.lookup.verification import ReferenceIntegrityError
from station.rounds.repository import RoundRepository, transaction
from station.rounds.roster import require_name


@dataclass(frozen=True)
class CloseResult:
    pending: tuple[str, ...]
    report: RoundResult | None


def close(connection: sqlite3.Connection, round_id: str) -> CloseResult:
    require_name(round_id, 'round_id')
    rounds = RoundRepository(connection)
    reports = ClosingRepository(connection)
    intake = IntakeRepository(connection)
    with transaction(connection, write=True):
        active_round = rounds.require_round(round_id)
        if active_round.phase == 'sealed':
            stored = reports.find_by_round(round_id)
            if stored is None:
                raise ReferenceIntegrityError(f'sealed round {round_id!r} has no report')
            return CloseResult((), resolve_report(connection, stored))
        readiness = check_readiness(active_round, intake)
        if readiness.pending:
            return CloseResult(readiness.pending, None)
        counts = tally_conclusions(readiness.conclusions)
        fixed = reports.insert_snapshot(round_id, counts, readiness.conclusions)
        rounds.mark_sealed(round_id)
        return CloseResult((), RoundResult(fixed.number, round_id, counts, readiness.conclusions))
