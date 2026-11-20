from enum import Enum

from station.device.terminal import ConfirmedMissing, Terminal
from station.rounds.policy import LimitPolicy


class Grade(str, Enum):
    PASS = 'pass'
    OVER_LIMIT = 'over_limit'
    MISSING = 'missing'


def grade_terminal(terminal: Terminal, policy: LimitPolicy) -> Grade:
    if isinstance(terminal, ConfirmedMissing):
        return Grade.MISSING
    if terminal.raw_code > policy.limit:
        return Grade.OVER_LIMIT
    return Grade.PASS
