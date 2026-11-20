import heapq
import json
import sqlite3
from dataclasses import dataclass
from itertools import count
from pathlib import Path
from typing import Generic, TypeVar


@dataclass(frozen=True)
class OpticalObservation:
    head: str
    tick: int
    kind: str
    raw_code: int | None = None

    def __post_init__(self) -> None:
        if self.head not in ('left', 'right') or type(self.tick) is not int:
            raise ValueError('observation requires a known head and an integer physical tick')
        if self.kind == 'reading' and type(self.raw_code) is int:
            return
        if self.kind in ('empty', 'end') and self.raw_code is None:
            return
        raise ValueError('observation kind and raw code are inconsistent')


@dataclass(frozen=True)
class OpticalFixture:
    observations: tuple[OpticalObservation, ...]

    def __post_init__(self) -> None:
        previous = {}
        for current in self.observations:
            prior = previous.get(current.head)
            if prior is not None:
                if prior.kind == 'end' or current.tick < prior.tick:
                    raise ValueError('fixture observations must follow the physical order for each head')
                if current.tick == prior.tick and current.kind != 'end':
                    raise ValueError('fixture allows one reading or empty observation per head and tick')
            previous[current.head] = current

    def observations_for(self, head: str) -> tuple[OpticalObservation, ...]:
        if head not in ('left', 'right'):
            raise ValueError(f'unknown fixture head {head!r}')
        return tuple(observation for observation in self.observations if observation.head == head)


@dataclass(frozen=True)
class FixtureExposure:
    piece: str
    order_id: str
    deliver_at: int
    fixture: OpticalFixture


@dataclass(frozen=True)
class ScenarioFixture:
    case_id: str
    round_id: str
    roster: tuple[str, ...]
    limit: int
    exposures: tuple[FixtureExposure, ...]
    close_after: int
    fail_once_for: str | None
    expected: dict


def load_fixture(path: str | Path) -> ScenarioFixture:
    with Path(path).open(encoding='utf-8') as source:
        script = json.load(source)
    if not isinstance(script['roster'], list):
        raise ValueError('fixture roster must be a JSON array')
    exposures = []
    for entry in script['exposures']:
        observations = tuple(OpticalObservation(**item) for item in entry['observations'])
        exposures.append(FixtureExposure(entry['piece'], entry['order_id'], entry['deliver_at'],
                                         OpticalFixture(observations)))
    return ScenarioFixture(script['case_id'], script['round_id'], tuple(script['roster']),
                           script['limit'], tuple(exposures), script['close_after'],
                           script.get('fail_once_for'), script['expected'])


Message = TypeVar('Message')


class DeliverySchedule(Generic[Message]):
    def __init__(self) -> None:
        self._sequence = count()
        self._queue: list[tuple[int, int, Message]] = []

    def schedule(self, deliver_at: int, message: Message) -> None:
        if type(deliver_at) is not int:
            raise ValueError('delivery slot must be an integer')
        heapq.heappush(self._queue, (deliver_at, next(self._sequence), message))

    def take_due(self, slot: int) -> tuple[Message, ...]:
        if type(slot) is not int:
            raise ValueError('delivery slot must be an integer')
        due = []
        while self._queue and self._queue[0][0] <= slot:
            _, _, message = heapq.heappop(self._queue)
            due.append(message)
        return tuple(due)


class InjectedCommitFailure(sqlite3.OperationalError):
    pass


class FailOnceBeforeCommit:
    def __init__(self) -> None:
        self.failed = False

    def __call__(self) -> None:
        if not self.failed:
            self.failed = True
            raise InjectedCommitFailure('fixture interrupted intake before commit')
