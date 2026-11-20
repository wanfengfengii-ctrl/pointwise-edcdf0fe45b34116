import sqlite3
from dataclasses import dataclass

from station.closing.repository import ClosingRepository, RoundReport
from station.closing.tally import CategoryCounts
from station.intake.repository import IntakeRepository, PieceConclusion
from station.lookup.verification import verify_report_snapshot
from station.rounds.repository import RoundRepository, transaction
from station.rounds.roster import require_name


@dataclass(frozen=True)
class RoundResult:
    number: str
    round_id: str
    counts: CategoryCounts
    conclusions: tuple[PieceConclusion, ...]


def resolve_report(connection: sqlite3.Connection, report: RoundReport) -> RoundResult:
    frozen_round = RoundRepository(connection).find_round(report.round_id)
    conclusions = verify_report_snapshot(report, frozen_round, IntakeRepository(connection))
    return RoundResult(report.number, report.round_id, report.counts, conclusions)


def get_round(connection: sqlite3.Connection, report_number: str) -> RoundResult:
    require_name(report_number, 'report_number')
    with transaction(connection):
        report = ClosingRepository(connection).find_by_number(report_number)
        if report is None:
            raise KeyError(f'unknown round report {report_number!r}')
        return resolve_report(connection, report)
