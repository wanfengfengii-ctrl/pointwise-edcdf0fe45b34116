from dataclasses import dataclass


@dataclass(frozen=True)
class ConfirmedMissing:
    source_id: str
    round_id: str
    piece: str
    order_id: str
    trigger_id: str


@dataclass(frozen=True)
class RawReading:
    source_id: str
    round_id: str
    piece: str
    order_id: str
    trigger_id: str
    tick: int
    raw_code: int

    def __post_init__(self) -> None:
        if type(self.tick) is not int or type(self.raw_code) is not int:
            raise ValueError('a reading requires an integer physical tick and raw code')


Terminal = RawReading | ConfirmedMissing
