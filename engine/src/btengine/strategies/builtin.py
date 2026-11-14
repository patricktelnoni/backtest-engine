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
    price_keys = {"stop": "second_low_bar", "first_low": "first_low_bar", "second_low": "second_low_bar"}
    price_indicators = False

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

    def _run(self, df) -> tuple[np.ndarray, list[dict]]:
        """Position per bar plus one record per setup taken (used by the screener to explain a signal)."""
        p = self.params
        low, close = df["low"].to_numpy(), df["close"].to_numpy()
        rsi = _rsi(df["close"], p.rsi_period).to_numpy()
        L, R = p.pivot_left, p.pivot_right

        out = np.zeros(len(df))
        pivots: list[int] = []
        setups: list[dict] = []
        pos, entry_bar, stop = 0.0, -1, np.nan
        for t in range(len(df)):
            exited = False
            if pos == 1.0 and t > entry_bar:
                reason = ("stop" if p.use_stop and close[t] < stop else
                          "rsi" if rsi[t] >= p.exit_rsi else
                          "max_hold" if t - entry_bar >= p.max_hold else None)
                if reason:
                    pos, exited = 0.0, True
                    setups[-1].update(exit_bar=t, exit_reason=reason)

            i = t - R  # the only pivot candidate that becomes confirmed at bar t
            if i - L >= 0 and low[i] < low[i - L:i].min() and low[i] <= low[i + 1:t + 1].min():
                ref = next((j for j in pivots if p.min_gap <= i - j <= p.max_gap and low[i] < low[j]
                            and rsi[i] > rsi[j] and rsi[j] <= p.oversold), None)
                if ref is not None and pos == 0.0 and not exited:
                    pos, entry_bar, stop = 1.0, t, low[i]
                    setups.append({"entry_bar": t, "exit_bar": None, "exit_reason": None, "stop": float(low[i]),
                                   "detail": {"first_low_bar": ref, "first_low": float(low[ref]), "rsi_first": float(rsi[ref]),
                                              "second_low_bar": i, "second_low": float(low[i]), "rsi_second": float(rsi[i])}})
                pivots.append(i)
                # Pivots older than max_gap can never pair with a future pivot.
                pivots = [j for j in pivots if i - j <= p.max_gap]
            out[t] = pos
        return out, setups

    def signals(self, df):
        out, _ = self._run(df)
        return pd.Series(out, index=df.index)

    def scan(self, df) -> list[dict]:
        """Every divergence setup in `df`, in order; the last one has exit_bar None if still open."""
        return self._run(df)[1]

    def indicators(self, df):
        return {"rsi": _rsi(df["close"], self.params.rsi_period)}


EMA_PERIODS = (10, 20, 50, 100, 200)


class ConsolidatedMa(Strategy):
    """Breakout from a tight bundle of EMAs (the "consolidated MA" / "superketat" setup).

    The EMA 10, 20, 50, 100 and 200 are "consolidated" on a bar when the gap between the highest and the lowest
    of them is at most `tightness_pct` of the close. Once that has held for `min_squeeze_bars` consecutive bars,
    a close above the highest EMA (coming from at or below it on the previous bar) is the signal, provided the
    bar's volume is at least `volume_mult` x the average of the previous `volume_window` bars (0 disables the
    volume condition). Nothing fires before all EMAs have had `max(EMA_PERIODS)` bars of warm-up.

    Exit on the first of: `max_hold` bars held, or, with `use_stop`, a close below the lowest EMA at signal time
    (the bundle has failed as support). Long-only.
    """

    name = "consolidated_ma"
    price_keys = {"stop": None, "ema_top": None, "ema_bottom": None}

    class Params(BaseModel):
        tightness_pct: float = Field(3.0, gt=0, le=50)
        min_squeeze_bars: int = Field(5, ge=1, le=500)
        volume_mult: float = Field(1.5, ge=0, le=20)
        volume_window: int = Field(20, ge=1, le=500)
        max_hold: int = Field(60, ge=1, le=1000)
        use_stop: bool = True

    def _run(self, df) -> tuple[np.ndarray, list[dict]]:
        p = self.params
        close, vol = df["close"].to_numpy(), df["volume"].to_numpy(dtype=float)
        emas = np.column_stack([df["close"].ewm(span=k, adjust=False).mean().to_numpy() for k in EMA_PERIODS])
        top, bot = emas.max(axis=1), emas.min(axis=1)
        spread = (top - bot) / close
        tight = spread <= p.tightness_pct / 100
        vol_avg = pd.Series(vol).rolling(p.volume_window).mean().shift(1).to_numpy()  # previous bars only
        warm = max(EMA_PERIODS)

        n = len(df)
        out = np.zeros(n)
        setups: list[dict] = []
        pos, entry_bar, stop, run = 0.0, -1, np.nan, 0  # run = consecutive tight bars ending at t-1
        for t in range(n):
            exited = False
            if pos == 1.0 and t > entry_bar:
                reason = ("stop" if p.use_stop and close[t] < stop else
                          "max_hold" if t - entry_bar >= p.max_hold else None)
                if reason:
                    pos, exited = 0.0, True
                    setups[-1].update(exit_bar=t, exit_reason=reason)

            if (pos == 0.0 and not exited and t >= warm and run >= p.min_squeeze_bars
                    and close[t] > top[t] and close[t - 1] <= top[t - 1]):
                ratio = vol[t] / vol_avg[t] if vol_avg[t] > 0 else np.nan
                if p.volume_mult == 0 or ratio >= p.volume_mult:
                    pos, entry_bar, stop = 1.0, t, float(bot[t])
                    setups.append({"entry_bar": t, "exit_bar": None, "exit_reason": None, "stop": stop,
                                   "detail": {"squeeze_start_bar": t - run, "squeeze_bars": run,
                                              "spread_pct": float(spread[t - 1] * 100),
                                              "volume_ratio": None if np.isnan(ratio) else float(ratio),
                                              "ema_top": float(top[t]), "ema_bottom": float(bot[t])}})
            run = run + 1 if tight[t] else 0
            out[t] = pos
        return out, setups

    def signals(self, df):
        out, _ = self._run(df)
        return pd.Series(out, index=df.index)

    def scan(self, df) -> list[dict]:
        return self._run(df)[1]

    def indicators(self, df):
        return {f"ema{k}": df["close"].ewm(span=k, adjust=False).mean() for k in EMA_PERIODS}


class StoppingVolume(Strategy):
    """Stopping volume (Wyckoff / VSA): heavy selling absorbed at the end of a decline.

    Bar i is a stopping-volume bar when, in a downtrend (close below the `trend_ema` EMA), it prints the lowest
    low of the last `lookback` bars on volume at least `volume_mult` x the average of the previous `volume_window`
    bars (zero-volume bars -- suspensions, holiday filler rows -- are left out of that average), yet closes in the
    upper part of its range: (close - low) / (high - low) >= `close_position`. Big volume
    that fails to push price lower means buyers are absorbing the supply.

    The signal waits `confirm_bars` bars (0 = signal on the bar itself) and fires at bar i + confirm_bars only if
    none of those bars traded below the stopping-volume low, so a confirmation never uses future data.

    Exit on the first of: `max_hold` bars held, or, with `use_stop`, a close below the stopping-volume low
    (supply was not stopped after all). Long-only.
    """

    name = "stopping_volume"
    price_keys = {"stop": "sv_bar", "sv_low": "sv_bar"}

    class Params(BaseModel):
        trend_ema: int = Field(50, ge=2, le=500)
        lookback: int = Field(20, ge=2, le=500)
        volume_mult: float = Field(2.0, ge=0, le=20)
        volume_window: int = Field(20, ge=1, le=500)
        close_position: float = Field(0.5, ge=0, le=1)
        confirm_bars: int = Field(2, ge=0, le=20)
        max_hold: int = Field(30, ge=1, le=1000)
        use_stop: bool = True

    def _run(self, df) -> tuple[np.ndarray, list[dict]]:
        p = self.params
        high, low, close = (df[k].to_numpy(dtype=float) for k in ("high", "low", "close"))
        vol = df["volume"].to_numpy(dtype=float)
        ema = df["close"].ewm(span=p.trend_ema, adjust=False).mean().to_numpy()
        # Average of the previous bars only, skipping zero-volume bars (suspension, holiday filler rows): they are
        # not "quiet" sessions, and counting them as 0 inflates the ratio of the first bar traded after them.
        # Needs more than half of the window actually traded, otherwise there is no baseline (NaN -> no signal).
        traded = pd.Series(np.where(vol > 0, vol, np.nan))
        vol_avg = traded.rolling(p.volume_window, min_periods=p.volume_window // 2 + 1).mean().shift(1).to_numpy()
        warm = max(p.trend_ema, p.lookback, p.volume_window)

        def is_sv(i: int) -> tuple[float, float] | None:
            """(volume ratio, close position) when bar i is a stopping-volume bar, else None."""
            rng = high[i] - low[i]
            if i < warm or rng <= 0 or not vol_avg[i] > 0 or close[i] >= ema[i]:
                return None
            if low[i] > low[i - p.lookback + 1:i + 1].min():
                return None
            ratio, pos_in_range = vol[i] / vol_avg[i], (close[i] - low[i]) / rng
            if ratio < p.volume_mult or pos_in_range < p.close_position:
                return None
            return float(ratio), float(pos_in_range)

        n = len(df)
        out = np.zeros(n)
        setups: list[dict] = []
        pos, entry_bar, stop = 0.0, -1, np.nan
        for t in range(n):
            exited = False
            if pos == 1.0 and t > entry_bar:
                reason = ("stop" if p.use_stop and close[t] < stop else
                          "max_hold" if t - entry_bar >= p.max_hold else None)
                if reason:
                    pos, exited = 0.0, True
                    setups[-1].update(exit_bar=t, exit_reason=reason)

            i = t - p.confirm_bars  # the only candidate whose confirmation completes at bar t
            if pos == 0.0 and not exited and i >= 0 and (hit := is_sv(i)) and low[i + 1:t + 1].min(initial=np.inf) >= low[i]:
                ratio, pos_in_range = hit
                start = max(0, i - p.lookback + 1)
                pos, entry_bar, stop = 1.0, t, float(low[i])
                setups.append({"entry_bar": t, "exit_bar": None, "exit_reason": None, "stop": stop,
                               "detail": {"sv_bar": i, "sv_low": stop, "volume_ratio": ratio,
                                          "close_position": pos_in_range,
                                          "drop_pct": float((1 - low[i] / high[start:i + 1].max()) * 100)}})
            out[t] = pos
        return out, setups

    def signals(self, df):
        out, _ = self._run(df)
        return pd.Series(out, index=df.index)

    def scan(self, df) -> list[dict]:
        return self._run(df)[1]

    def indicators(self, df):
        return {f"ema{self.params.trend_ema}": df["close"].ewm(span=self.params.trend_ema, adjust=False).mean()}
