from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

import pandas as pd
from pydantic import BaseModel


class Strategy(ABC):
    """Built-in strategy. `signals` returns target position per bar in [-1, 1].

    The signal at bar t is computed from data up to and including t; the engine
    executes it at bar t+1 (no look-ahead).
    """

    name: ClassVar[str]
    Params: ClassVar[type[BaseModel]]
    # Screener display: scan() detail keys (and "stop") that hold prices, mapped to the detail key of the bar the
    # price was read from (None = the entry bar), so they can be reported in that day's traded (unadjusted) prices.
    price_keys: ClassVar[dict[str, str | None]] = {}
    # True when indicators() returns price-scaled series (EMAs), False for oscillators (RSI).
    price_indicators: ClassVar[bool] = True

    def __init__(self, **params):
        self.params = self.Params(**params)

    @classmethod
    def schema(cls) -> dict:
        """JSON schema of parameters, usable by the frontend to render forms."""
        return cls.Params.model_json_schema()

    @abstractmethod
    def signals(self, df: pd.DataFrame) -> pd.Series: ...

    def indicators(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        """Indicator series worth drawing next to price (for screener charts). Default: none."""
        return {}
