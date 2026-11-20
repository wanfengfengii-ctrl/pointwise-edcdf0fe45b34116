from dataclasses import dataclass


@dataclass(frozen=True)
class LimitPolicy:
    limit: int


def freeze_limit(limit: int) -> LimitPolicy:
    if type(limit) is not int:
        raise ValueError('limit must be an integer raw code')
    return LimitPolicy(limit)
