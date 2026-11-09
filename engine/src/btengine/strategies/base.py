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

    def __init__(self, **params):
        self.params = self.Params(**params)

    @classmethod
    def schema(cls) -> dict:
        """JSON schema of parameters, usable by the frontend to render forms."""
        return cls.Params.model_json_schema()

    @abstractmethod
    def signals(self, df: pd.DataFrame) -> pd.Series: ...
