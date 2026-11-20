import sqlite3

from station.rounds.repository import ReadOrder, RoundRepository, transaction
from station.rounds.roster import require_name


def require_mode(mode: str) -> str:
    if mode not in ('point', 'interval'):
        raise ValueError(f'unknown read order mode {mode!r}')
    return mode


def _requested_order(repository: RoundRepository, round_id: str, piece: str,
                     order_id: str, mode: str) -> ReadOrder:
    require_name(round_id, 'round_id')
    require_name(piece, 'piece')
    require_name(order_id, 'order_id')
    require_mode(mode)
    active_round = repository.require_round(round_id)
    if active_round.phase != 'open':
        raise ValueError(f'round {round_id!r} is sealed')
    if not active_round.roster.contains(piece):
        raise ValueError(f'piece {piece!r} is outside round {round_id!r}')
    return ReadOrder(round_id, piece, order_id, mode)


def authorize_read(connection: sqlite3.Connection, round_id: str, piece: str,
                   order_id: str, *, mode: str = 'point') -> ReadOrder:
    repository = RoundRepository(connection)
    with transaction(connection, write=True):
        requested = _requested_order(repository, round_id, piece, order_id, mode)
        bound = repository.find_piece_order(round_id, piece)
        if bound is not None:
            if bound != requested:
                raise ValueError(f'piece {piece!r} already has a different read order')
            return bound
        if repository.find_order(order_id) is not None:
            raise ValueError(f'read order {order_id!r} is already bound')
        return repository.insert_order(requested)


def require_trigger_binding(repository: RoundRepository, round_id: str, piece: str,
                            order_id: str, *, mode: str = 'point') -> ReadOrder:
    requested = _requested_order(repository, round_id, piece, order_id, mode)
    if repository.find_order(order_id) != requested:
        raise ValueError('trigger request does not match an authorized read order')
    return requested
