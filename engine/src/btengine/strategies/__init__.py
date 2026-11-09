from .base import Strategy
from .builtin import BuyHold, Donchian, RsiReversion, SmaCross

REGISTRY: dict[str, type[Strategy]] = {s.name: s for s in (BuyHold, SmaCross, RsiReversion, Donchian)}


def get_strategy(name: str, **params) -> Strategy:
    if name not in REGISTRY:
        raise KeyError(f"unknown strategy {name!r}; available: {sorted(REGISTRY)}")
    return REGISTRY[name](**params)


__all__ = ["Strategy", "REGISTRY", "get_strategy"]
