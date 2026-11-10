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


def _rsi(close: pd.Series, period: int) -> pd.Series:
    """Wilder RSI (causal EWM). Undefined values (warm-up, zero loss) are filled with a neutral 50."""
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rsi = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
    return rsi.fillna(50.0)


class RsiReversion(Strategy):
    name = "rsi_reversion"

    class Params(BaseModel):
        period: int = Field(14, ge=2, le=200)
        oversold: float = Field(30, ge=1, le=50)
        exit_level: float = Field(50, ge=1, le=99)

    def signals(self, df):
        rsi = _rsi(df["close"], self.params.period)
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


class BullishDivergence(Strategy):
    """Long when price makes a lower swing low while RSI makes a higher low.

    Swing lows are only known in hindsight: bar i is a pivot low if its low is strictly below the
    `pivot_left` bars before it and no higher than the `pivot_right` bars after it. That is first
    knowable at bar i + pivot_right, so the signal fires there -- never at bar i itself (no look-ahead).

    A divergence pairs the new pivot with any earlier pivot j, `min_gap`..`max_gap` bars before it, where
    low[i] < low[j], RSI[i] > RSI[j], and RSI[j] <= `oversold` (the first low was genuinely oversold).

    Exit on the first of: RSI >= `exit_rsi`; `max_hold` bars held; or, with `use_stop`, a close below
    the divergence low (the setup is invalidated). Long-only.
    """

    name = "bullish_divergence"

    class Params(BaseModel):
        rsi_period: int = Field(14, ge=2, le=200)
        pivot_left: int = Field(5, ge=1, le=50)
        pivot_right: int = Field(3, ge=1, le=50)
        min_gap: int = Field(5, ge=1, le=500)
        max_gap: int = Field(60, ge=2, le=1000)
        oversold: float = Field(40, ge=1, le=100)
        exit_rsi: float = Field(70, ge=1, le=100)
        max_hold: int = Field(20, ge=1, le=1000)
        use_stop: bool = True

        @model_validator(mode="after")
        def _check(self):
            if self.min_gap >= self.max_gap:
                raise ValueError("min_gap must be < max_gap")
            return self

    def signals(self, df):
        p = self.params
        low, close = df["low"].to_numpy(), df["close"].to_numpy()
        rsi = _rsi(df["close"], p.rsi_period).to_numpy()
        L, R = p.pivot_left, p.pivot_right

        out = np.zeros(len(df))
        pivots: list[int] = []
        pos, entry_bar, stop = 0.0, -1, np.nan
        for t in range(len(df)):
            exited = False
            if pos == 1.0 and t > entry_bar:
                if (p.use_stop and close[t] < stop) or rsi[t] >= p.exit_rsi or t - entry_bar >= p.max_hold:
                    pos, exited = 0.0, True

            i = t - R  # the only pivot candidate that becomes confirmed at bar t
            if i - L >= 0 and low[i] < low[i - L:i].min() and low[i] <= low[i + 1:t + 1].min():
                diverges = any(
                    p.min_gap <= i - j <= p.max_gap and low[i] < low[j] and rsi[i] > rsi[j] and rsi[j] <= p.oversold
                    for j in pivots
                )
                if diverges and pos == 0.0 and not exited:
                    pos, entry_bar, stop = 1.0, t, low[i]
                pivots.append(i)
                # Pivots older than max_gap can never pair with a future pivot.
                pivots = [j for j in pivots if i - j <= p.max_gap]
            out[t] = pos
        return pd.Series(out, index=df.index)
