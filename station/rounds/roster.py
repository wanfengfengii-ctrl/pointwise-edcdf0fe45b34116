from collections.abc import Sequence
from dataclasses import dataclass


def require_name(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{field} must be a nonblank string')
    return value


@dataclass(frozen=True)
class Roster:
    pieces: tuple[str, ...]

    def contains(self, piece: str) -> bool:
        return piece in self.pieces


def validate_roster(pieces: Sequence[str]) -> Roster:
    if not isinstance(pieces, Sequence) or isinstance(pieces, (str, bytes, bytearray)):
        raise ValueError('roster must be an ordered sequence of piece names')
    names = tuple(require_name(piece, 'piece') for piece in pieces)
    if len(set(names)) != len(names):
        raise ValueError('roster contains duplicate pieces')
    return Roster(names)
