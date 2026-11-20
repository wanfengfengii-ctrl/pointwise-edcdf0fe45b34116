import sqlite3
from dataclasses import dataclass
from typing import ClassVar

from station.device.gain_fixture import GainExposure, GainOriginal
from station.intake.grading import Grade
from station.rounds.repository import ReadOrder

MISSING = 'missing'
NEED_LOW = 'need_low'
CONFLICT = 'conflict'
INDETERMINATE = 'indeterminate'


@dataclass(frozen=True)
class IntervalConclusion:
    """A signed interval conclusion that mirrors PieceConclusion's public fields."""

    conclusion_id: int
    round_id: str
    piece: str
    order_id: str
    grade: Grade
    high_exposure_id: str
    low_exposure_id: str | None
    kind: ClassVar[str] = 'interval'

    @property
    def trigger_id(self) -> str:
        return self.high_exposure_id

    @property
    def source_id(self) -> str:
        return f'gain:{self.high_exposure_id}'


def canonical_interval(value) -> tuple[int, int | None] | None:
    if value is None:
        return None
    if type(value) is not tuple or len(value) != 2:
        raise ValueError('interval must be a two-ended tuple or None')
    lower, upper = value
    if type(lower) is not int:
        raise ValueError('interval endpoints must be integers')
    if upper is not None and (type(upper) is not int or upper < lower):
        raise ValueError('interval upper endpoint must be an integer not below the lower one')
    return (lower, upper)


def validate_original(original: GainOriginal) -> GainOriginal:
    if not isinstance(original, GainOriginal):
        raise ValueError('an interval delivery must be a GainOriginal')
    exposure = original.exposure
    if not isinstance(exposure, GainExposure):
        raise ValueError('an interval delivery must carry a device exposure')
    for field, value in (('device_id', exposure.device_id), ('exposure_id', exposure.exposure_id),
                         ('piece', exposure.piece), ('gain', exposure.gain), ('tag', exposure.tag)):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f'exposure {field} must be a nonblank string')
    if exposure.gain not in ('high', 'low'):
        raise ValueError('exposure gain must be high or low')
    if type(exposure.tick) is not int:
        raise ValueError('exposure tick must be an integer')
    interval = canonical_interval(original.interval)
    return GainOriginal(exposure, interval)


def _encode(value: int) -> str:
    return hex(value)


def _decode(value: str) -> int:
    return int(value, 16)


def _decode_original(row) -> GainOriginal:
    exposure_id, device_id, piece, gain, tick, tag, kind, lower, upper = row
    exposure = GainExposure(device_id, exposure_id, piece, gain, _decode(tick), tag)
    if kind == 'missing':
        return GainOriginal(exposure, None)
    return GainOriginal(exposure, (_decode(lower), None if upper is None else _decode(upper)))


class GainStore:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def initialize(self) -> None:
        self.connection.execute('''
            CREATE TABLE IF NOT EXISTS gain_exposures (
                exposure_id TEXT PRIMARY KEY NOT NULL,
                device_id TEXT NOT NULL,
                round_id TEXT NOT NULL,
                piece TEXT NOT NULL,
                order_id TEXT NOT NULL REFERENCES read_orders(order_id),
                gain TEXT NOT NULL CHECK (gain IN ('high', 'low')),
                tag TEXT NOT NULL,
                tick TEXT NOT NULL,
                UNIQUE (order_id, gain)
            )
        ''')
        self.connection.execute('''
            CREATE TABLE IF NOT EXISTS gain_originals (
                order_id TEXT NOT NULL,
                gain TEXT NOT NULL CHECK (gain IN ('high', 'low')),
                exposure_id TEXT NOT NULL UNIQUE REFERENCES gain_exposures(exposure_id),
                kind TEXT NOT NULL CHECK (kind IN ('missing', 'closed')),
                lower_code TEXT,
                upper_code TEXT,
                CHECK ((kind = 'closed' AND lower_code IS NOT NULL)
                    OR (kind = 'missing' AND lower_code IS NULL AND upper_code IS NULL)),
                PRIMARY KEY (order_id, gain)
            )
        ''')
        self.connection.execute('''
            CREATE TABLE IF NOT EXISTS interval_conclusions (
                conclusion_id INTEGER PRIMARY KEY,
                round_id TEXT NOT NULL,
                piece TEXT NOT NULL,
                order_id TEXT NOT NULL UNIQUE REFERENCES read_orders(order_id),
                high_exposure_id TEXT NOT NULL REFERENCES gain_exposures(exposure_id),
                low_exposure_id TEXT REFERENCES gain_exposures(exposure_id),
                grade TEXT NOT NULL CHECK (grade IN ('pass', 'over_limit', 'missing')),
                UNIQUE (round_id, piece),
                FOREIGN KEY (round_id, piece) REFERENCES roster(round_id, piece)
            )
        ''')
        self.connection.execute('''
            CREATE TABLE IF NOT EXISTS gain_receipts (
                number TEXT PRIMARY KEY NOT NULL,
                conclusion_id INTEGER NOT NULL UNIQUE
                    REFERENCES interval_conclusions(conclusion_id)
            )
        ''')

    # -- device exposures -------------------------------------------------

    _EXPOSURE_SELECT = '''
        SELECT exposure_id, device_id, piece, gain, tick, tag
        FROM gain_exposures
    '''

    @staticmethod
    def _exposure(row) -> GainExposure:
        exposure_id, device_id, piece, gain, tick, tag = row
        return GainExposure(device_id, exposure_id, piece, gain, _decode(tick), tag)

    def find_exposure(self, order_id: str, gain: str) -> GainExposure | None:
        row = self.connection.execute(
            self._EXPOSURE_SELECT + ' WHERE order_id = ? AND gain = ?', (order_id, gain)
        ).fetchone()
        return None if row is None else self._exposure(row)

    def find_exposure_by_id(self, exposure_id: str) -> GainExposure | None:
        row = self.connection.execute(
            self._EXPOSURE_SELECT + ' WHERE exposure_id = ?', (exposure_id,)
        ).fetchone()
        return None if row is None else self._exposure(row)

    def find_exposure_order(self, exposure_id: str) -> str | None:
        row = self.connection.execute(
            'SELECT order_id FROM gain_exposures WHERE exposure_id = ?', (exposure_id,)
        ).fetchone()
        return None if row is None else row[0]

    def device_for_order(self, order_id: str) -> str | None:
        row = self.connection.execute(
            'SELECT device_id FROM gain_exposures WHERE order_id = ? ORDER BY rowid LIMIT 1',
            (order_id,),
        ).fetchone()
        return None if row is None else row[0]

    def insert_exposure(self, order: ReadOrder, exposure: GainExposure) -> GainExposure:
        stored = self.find_exposure(order.order_id, exposure.gain)
        if stored is not None:
            if stored != exposure:
                raise ValueError('gain exposure conflicts with the recorded physical exposure')
            return stored
        device = self.device_for_order(order.order_id)
        if device is not None and device != exposure.device_id:
            raise ValueError('gain exposure comes from a different device')
        self.connection.execute('''
            INSERT INTO gain_exposures
                (exposure_id, device_id, round_id, piece, order_id, gain, tag, tick)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (exposure.exposure_id, exposure.device_id, order.round_id, order.piece,
              order.order_id, exposure.gain, exposure.tag, _encode(exposure.tick)))
        return exposure

    # -- accepted originals ----------------------------------------------

    _ORIGINAL_SELECT = '''
        SELECT exposure.exposure_id, exposure.device_id, exposure.piece, exposure.gain,
               exposure.tick, exposure.tag, original.kind, original.lower_code, original.upper_code
        FROM gain_originals AS original
        JOIN gain_exposures AS exposure ON exposure.exposure_id = original.exposure_id
    '''

    def find_original(self, order_id: str, gain: str) -> GainOriginal | None:
        row = self.connection.execute(
            self._ORIGINAL_SELECT + ' WHERE original.order_id = ? AND original.gain = ?',
            (order_id, gain),
        ).fetchone()
        return None if row is None else _decode_original(row)

    def list_originals(self, order_id: str) -> tuple[GainOriginal, ...]:
        rows = self.connection.execute(
            self._ORIGINAL_SELECT + " WHERE original.order_id = ? ORDER BY original.gain = 'low'",
            (order_id,),
        ).fetchall()
        return tuple(_decode_original(row) for row in rows)

    def record_original(self, order_id: str, original: GainOriginal) -> GainOriginal:
        original = validate_original(original)
        if self.find_exposure_order(original.exposure.exposure_id) != order_id:
            raise ValueError('original does not belong to the requested read order')
        stored = self.find_original(order_id, original.exposure.gain)
        if stored is not None:
            if stored != original:
                raise ValueError('delivered original conflicts with the accepted original')
            return stored
        if original.interval is None:
            kind, lower, upper = 'missing', None, None
        else:
            lower, upper = original.interval
            kind = 'closed'
            lower, upper = _encode(lower), None if upper is None else _encode(upper)
        self.connection.execute('''
            INSERT INTO gain_originals
                (order_id, gain, exposure_id, kind, lower_code, upper_code)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (order_id, original.exposure.gain, original.exposure.exposure_id, kind, lower, upper))
        return original

    # -- interval conclusions and receipts --------------------------------

    _CONCLUSION_SELECT = '''
        SELECT conclusion_id, round_id, piece, order_id, high_exposure_id, low_exposure_id, grade
        FROM interval_conclusions
    '''

    @staticmethod
    def _conclusion(row) -> IntervalConclusion:
        conclusion_id, round_id, piece, order_id, high_id, low_id, grade = row
        return IntervalConclusion(conclusion_id, round_id, piece, order_id, Grade(grade),
                                  high_id, low_id)

    def find_conclusion_for_piece(self, round_id: str, piece: str) -> IntervalConclusion | None:
        row = self.connection.execute(
            self._CONCLUSION_SELECT + ' WHERE round_id = ? AND piece = ?', (round_id, piece)
        ).fetchone()
        return None if row is None else self._conclusion(row)

    def get_conclusion(self, conclusion_id: int) -> IntervalConclusion | None:
        row = self.connection.execute(
            self._CONCLUSION_SELECT + ' WHERE conclusion_id = ?', (conclusion_id,)
        ).fetchone()
        return None if row is None else self._conclusion(row)

    def list_conclusions_for_round(self, round_id: str) -> tuple[IntervalConclusion, ...]:
        rows = self.connection.execute(
            self._CONCLUSION_SELECT + ' WHERE round_id = ? ORDER BY conclusion_id', (round_id,)
        ).fetchall()
        return tuple(self._conclusion(row) for row in rows)

    def insert_conclusion(self, round_id: str, piece: str, order_id: str, grade: Grade,
                          high_exposure_id: str,
                          low_exposure_id: str | None) -> IntervalConclusion:
        insert = self.connection.execute('''
            INSERT INTO interval_conclusions
                (round_id, piece, order_id, high_exposure_id, low_exposure_id, grade)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (round_id, piece, order_id, high_exposure_id, low_exposure_id, grade.value))
        return IntervalConclusion(insert.lastrowid, round_id, piece, order_id, grade,
                                  high_exposure_id, low_exposure_id)

    def issue_receipt(self, conclusion: IntervalConclusion) -> str:
        number = f'interval:{conclusion.conclusion_id}'
        self.connection.execute(
            'INSERT INTO gain_receipts (number, conclusion_id) VALUES (?, ?)',
            (number, conclusion.conclusion_id),
        )
        return number

    def find_receipt(self, number: str) -> tuple[str, int] | None:
        row = self.connection.execute(
            'SELECT number, conclusion_id FROM gain_receipts WHERE number = ?', (number,)
        ).fetchone()
        return None if row is None else row

    def find_receipt_for_conclusion(self, conclusion_id: int) -> str | None:
        row = self.connection.execute(
            'SELECT number FROM gain_receipts WHERE conclusion_id = ?', (conclusion_id,)
        ).fetchone()
        return None if row is None else row[0]


def grade_high(interval: tuple[int, int | None], limit: int):
    lower, upper = interval
    if upper is not None and upper <= limit:
        return Grade.PASS, interval
    if lower > limit:
        return Grade.OVER_LIMIT, interval
    return NEED_LOW, None


def intersect(left: tuple[int, int | None], right: tuple[int, int | None]):
    lower = max(left[0], right[0])
    uppers = [value for value in (left[1], right[1]) if value is not None]
    upper = min(uppers) if uppers else None
    if upper is not None and lower > upper:
        return None
    return (lower, upper)


def classify(high: GainOriginal, low: GainOriginal | None, limit: int):
    """Return (status, grade or None, effective interval or None)."""
    if high.interval is None:
        return MISSING, Grade.MISSING, None
    graded, effective = grade_high(high.interval, limit)
    if graded != NEED_LOW:
        return graded, graded, effective
    if low is None:
        return NEED_LOW, None, None
    if low.interval is None:
        return INDETERMINATE, None, None
    combined = intersect(high.interval, low.interval)
    if combined is None:
        return CONFLICT, None, None
    lower, upper = combined
    if upper is not None and upper <= limit:
        return Grade.PASS, Grade.PASS, combined
    if lower > limit:
        return Grade.OVER_LIMIT, Grade.OVER_LIMIT, combined
    return INDETERMINATE, None, None
