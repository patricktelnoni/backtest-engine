from __future__ import annotations

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field, model_validator

from .base import Strategy


class BuyHold(Strategy):
    name = "buy_hold"

    class Params(BaseModel):
        pass

    def signals(self, df):
        return pd.Series(1.0, index=df.index)


class SmaCross(Strategy):
    name = "sma_cross"

    class Params(BaseModel):
        fast: int = Field(20, ge=2, le=500)
        slow: int = Field(50, ge=3, le=1000)
        allow_short: bool = False

        @model_validator(mode="after")
        def _check(self):
            if self.fast >= self.slow:
                raise ValueError("fast must be < slow")
            return self

    def signals(self, df):
        c = df["close"]
        fast, slow = c.rolling(self.params.fast).mean(), c.rolling(self.params.slow).mean()
        sig = pd.Series(np.where(fast > slow, 1.0, -1.0 if self.params.allow_short else 0.0), index=df.index)
        return sig.where(slow.notna(), 0.0)


class RsiReversion(Strategy):
    name = "rsi_reversion"

    class Params(BaseModel):
        period: int = Field(14, ge=2, le=200)
        oversold: float = Field(30, ge=1, le=50)
        exit_level: float = Field(50, ge=1, le=99)

    def signals(self, df):
        delta = df["close"].diff()
        gain = delta.clip(lower=0).ewm(alpha=1 / self.params.period, adjust=False).mean()
        loss = (-delta.clip(upper=0)).ewm(alpha=1 / self.params.period, adjust=False).mean()
        rsi = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
        rsi = rsi.fillna(50.0)
        # Stateful: enter long when RSI < oversold, hold until RSI >= exit_level.
        pos, out = 0.0, []
        for v in rsi.to_numpy():
            if pos == 0.0 and v < self.params.oversold:
                pos = 1.0
            elif pos == 1.0 and v >= self.params.exit_level:
                pos = 0.0
            out.append(pos)
        return pd.Series(out, index=df.index)


class Donchian(Strategy):
    name = "donchian_breakout"

    class Params(BaseModel):
        entry: int = Field(20, ge=2, le=500)
        exit: int = Field(10, ge=2, le=500)

    def signals(self, df):
        # Channels use prior bars only (shift(1)) so the breakout bar isn't in its own channel.
        hi = df["high"].rolling(self.params.entry).max().shift(1)
        lo = df["low"].rolling(self.params.exit).min().shift(1)
        pos, out = 0.0, []
        for c, h, l in zip(df["close"].to_numpy(), hi.to_numpy(), lo.to_numpy()):
            if pos == 0.0 and not np.isnan(h) and c > h:
                pos = 1.0
            elif pos == 1.0 and not np.isnan(l) and c < l:
                pos = 0.0
            out.append(pos)
        return pd.Series(out, index=df.index)
