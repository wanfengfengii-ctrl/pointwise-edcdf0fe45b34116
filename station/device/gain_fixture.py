import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class GainExposure:
    device_id: str
    exposure_id: str
    piece: str
    gain: str
    tick: int
    tag: str


@dataclass(frozen=True)
class GainOriginal:
    exposure: GainExposure
    interval: tuple[int, int | None] | None


class LostExposureReply(ConnectionError):
    pass


class GainHead:
    """A simulated head with physical history independent of the station connection."""

    def __init__(self, device_id: str, pieces: dict):
        if not isinstance(device_id, str) or not device_id.strip():
            raise ValueError("device_id must be nonblank")
        self.device_id = device_id
        self._intervals = {}
        for piece, gains in pieces.items():
            if not isinstance(piece, str) or not piece.strip() or set(gains) != {"high", "low"}:
                raise ValueError("each piece needs a high and a low observation")
            for gain, value in gains.items():
                if value is None:
                    self._intervals[piece, gain] = None
                    continue
                lower, upper = value
                if type(lower) is not int or (upper is not None and
                                              (type(upper) is not int or upper < lower)):
                    raise ValueError("invalid closed integer interval")
                self._intervals[piece, gain] = (lower, upper)
        self._actions: dict[str, GainExposure] = {}
        self._originals: dict[str, GainOriginal] = {}
        self._loss: str | None = None

    def lose_next_reply(self, when: str) -> None:
        if when not in ("before", "after") or self._loss is not None:
            raise ValueError("one before or after fault may be armed")
        self._loss = when

    def history(self) -> tuple[GainExposure, ...]:
        return tuple(self._actions.values())

    def fire(self, piece: str, gain: str, tag: str) -> GainExposure:
        if (piece, gain) not in self._intervals or not isinstance(tag, str) or not tag.strip():
            raise ValueError("unknown piece or gain, or invalid tag")
        fault = self._loss
        self._loss = None
        if fault == "before":
            raise LostExposureReply("head did not return a fire reply")
        position = len(self._actions) + 1
        exposure = GainExposure(self.device_id, f"exposure:{position}", piece, gain,
                                position + 9, tag)
        self._actions[exposure.exposure_id] = exposure
        if fault == "after":
            raise LostExposureReply("head did not return a fire reply")
        return exposure

    def complete(self, exposure_id: str) -> GainOriginal:
        exposure = self._actions[exposure_id]
        original = GainOriginal(exposure, self._intervals[exposure.piece, exposure.gain])
        return self._originals.setdefault(exposure_id, original)

    def read(self, exposure_id: str) -> GainOriginal | None:
        if exposure_id not in self._actions:
            raise KeyError(exposure_id)
        return self._originals.get(exposure_id)


def load_head(path: str | Path) -> GainHead:
    with Path(path).open(encoding="utf-8") as source:
        fixture = json.load(source)
    return GainHead(fixture["device_id"], fixture["pieces"])


def check_fixture(head: GainHead) -> dict:
    high = head.fire("X", "high", "raw-check")
    if head.read(high.exposure_id) is not None:
        raise RuntimeError("unfinished physical exposure returned an original")
    high_original = head.complete(high.exposure_id)
    counts = []
    for when in ("before", "after"):
        head.lose_next_reply(when)
        try:
            head.fire("X", "low", "repeatable-tag")
        except LostExposureReply:
            counts.append(len(head.history()))
        else:
            raise RuntimeError("missing fire-reply loss")
    lost = head.history()[-1]
    late = head.complete(lost.exposure_id)
    duplicate = head.fire("X", "low", "repeatable-tag")
    if (counts != [1, 2] or high_original.interval != (48, 52) or late.interval != (44, 48)
            or lost.gain != "low" or duplicate.exposure_id == lost.exposure_id
            or head.read(lost.exposure_id) != late or len(head.history()) != 3):
        raise RuntimeError("unexpected physical history or original")
    return {"counts_after_lost_replies": counts, "late_original": asdict(late),
            "separate_physical_exposure": asdict(duplicate)}


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        raise SystemExit("usage: python3 -m station.device.gain_fixture FIXTURE.json")
    print(json.dumps(check_fixture(load_head(sys.argv[1])), sort_keys=True))
