import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass

from station.rounds.policy import LimitPolicy
from station.rounds.roster import Roster


@contextmanager
def transaction(connection: sqlite3.Connection, *, write: bool = False):
    """Give one public operation a consistent snapshot or an atomic write."""
    connection.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
    try:
        yield
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


@dataclass(frozen=True)
class RoundRecord:
    round_id: str
    roster: Roster
    policy: LimitPolicy
    phase: str


@dataclass(frozen=True)
class ReadOrder:
    round_id: str
    piece: str
    order_id: str


class RoundRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def initialize(self) -> None:
        self.connection.execute('''
            CREATE TABLE IF NOT EXISTS rounds (
                round_id TEXT PRIMARY KEY NOT NULL,
                limit_code TEXT NOT NULL,
                phase TEXT NOT NULL CHECK (phase IN ('open', 'sealed'))
            )
        ''')
        self.connection.execute('''
            CREATE TABLE IF NOT EXISTS roster (
                round_id TEXT NOT NULL REFERENCES rounds(round_id),
                position INTEGER NOT NULL CHECK (position >= 0),
                piece TEXT NOT NULL,
                PRIMARY KEY (round_id, position),
                UNIQUE (round_id, piece)
            )
        ''')
        self.connection.execute('''
            CREATE TABLE IF NOT EXISTS read_orders (
                order_id TEXT PRIMARY KEY NOT NULL,
                round_id TEXT NOT NULL,
                piece TEXT NOT NULL,
                UNIQUE (round_id, piece),
                FOREIGN KEY (round_id, piece) REFERENCES roster(round_id, piece)
            )
        ''')

    def find_round(self, round_id: str) -> RoundRecord | None:
        row = self.connection.execute(
            'SELECT limit_code, phase FROM rounds WHERE round_id = ?', (round_id,)
        ).fetchone()
        if row is None:
            return None
        limit_code, phase = row
        pieces = self.connection.execute(
            'SELECT piece FROM roster WHERE round_id = ? ORDER BY position', (round_id,)
        )
        return RoundRecord(round_id, Roster(tuple(piece for piece, in pieces)),
                           LimitPolicy(int(limit_code, 16)), phase)

    def require_round(self, round_id: str) -> RoundRecord:
        round_record = self.find_round(round_id)
        if round_record is None:
            raise KeyError(f'unknown round {round_id!r}')
        return round_record

    def insert_round(self, round_id: str, roster: Roster, policy: LimitPolicy) -> RoundRecord:
        self.connection.execute(
            'INSERT INTO rounds (round_id, limit_code, phase) VALUES (?, ?, ?)',
            (round_id, hex(policy.limit), 'open'),
        )
        self.connection.executemany(
            'INSERT INTO roster (round_id, position, piece) VALUES (?, ?, ?)',
            ((round_id, position, piece) for position, piece in enumerate(roster.pieces)),
        )
        return RoundRecord(round_id, roster, policy, 'open')

    def find_order(self, order_id: str) -> ReadOrder | None:
        row = self.connection.execute(
            'SELECT round_id, piece, order_id FROM read_orders WHERE order_id = ?', (order_id,)
        ).fetchone()
        return None if row is None else ReadOrder(*row)

    def find_piece_order(self, round_id: str, piece: str) -> ReadOrder | None:
        row = self.connection.execute(
            'SELECT round_id, piece, order_id FROM read_orders WHERE round_id = ? AND piece = ?',
            (round_id, piece),
        ).fetchone()
        return None if row is None else ReadOrder(*row)

    def insert_order(self, order: ReadOrder) -> ReadOrder:
        self.connection.execute(
            'INSERT INTO read_orders (order_id, round_id, piece) VALUES (?, ?, ?)',
            (order.order_id, order.round_id, order.piece),
        )
        return order

    def mark_sealed(self, round_id: str) -> None:
        update = self.connection.execute(
            "UPDATE rounds SET phase = 'sealed' WHERE round_id = ? AND phase = 'open'", (round_id,)
        )
        if update.rowcount != 1:
            raise RuntimeError(f'cannot seal round {round_id!r} from its current phase')
