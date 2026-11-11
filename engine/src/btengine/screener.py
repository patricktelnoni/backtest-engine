"""Screen many symbols for the bullish_divergence setup and summarise what is happening on the last bar.

Pure computation: callers hand in already-loaded OHLCV frames (one per symbol) and get a JSON-ready dict.
"""
from __future__ import annotations

import math

import pandas as pd

from .backtest import run_backtest
from .data import validate_ohlcv
from .markets import Market
from .strategies import get_strategy
from .strategies.builtin import _rsi

MIN_BARS = 120


def _num(v):
    f = float(v)
    return None if not math.isfinite(f) else f


def _date(index, i: int) -> str:
    return index[i].strftime("%Y-%m-%d")


def _row(ticker: str, df: pd.DataFrame, strat, market, recent_bars: int) -> dict | None:
    """One result row, or None when the symbol has no live or recently closed setup."""
    setups = strat.scan(df)
    if not setups:
        return None
    last, n = setups[-1], len(df)
    if last["exit_bar"] is not None and n - 1 - last["entry_bar"] > recent_bars:
        return None

    close, low, idx = df["close"].to_numpy(), df["low"].to_numpy(), df.index
    e = last["entry_bar"]
    last_close = float(close[-1])
    rsi_now = _rsi(df["close"], strat.params.rsi_period).iloc[-1]
    bt = run_backtest(df, strat, market)
    m = bt.metrics
    return {
        "ticker": ticker,
        "status": "active" if last["exit_bar"] is None else "exited",
        "signal_date": _date(idx, e),
        "signal_price": float(close[e]),
        "bars_since_signal": n - 1 - e,
        "last_date": _date(idx, n - 1),
        "last_close": last_close,
        "return_since_signal": last_close / close[e] - 1,
        "stop": last["stop"],
        "stop_distance": last_close / last["stop"] - 1,
        "rsi": _num(rsi_now),
        "exit_date": _date(idx, last["exit_bar"]) if last["exit_bar"] is not None else None,
        "exit_reason": last["exit_reason"],
        "divergence": {  # the two swing lows: price lower, RSI higher
            "first_date": _date(idx, last["ref_pivot_bar"]), "first_low": float(low[last["ref_pivot_bar"]]),
            "second_date": _date(idx, last["pivot_bar"]), "second_low": float(low[last["pivot_bar"]]),
        },
        "history": {  # how this setup did on this symbol over the whole frame (same params, market costs)
            "bars": n,
            "n_trades": m["n_trades"],
            "win_rate": _num(m["win_rate"]),
            "profit_factor": _num(m["profit_factor"]),
            "avg_trade_return": _num(sum(bt.trade_returns) / len(bt.trade_returns)) if bt.trade_returns else None,
            "total_return": _num(m["total_return"]),
        },
    }


def screen_bullish_divergence(frames: dict[str, pd.DataFrame], params: dict | None = None,
                              market: Market | str = Market.IDX_STOCK, recent_bars: int = 10,
                              min_bars: int = MIN_BARS) -> dict:
    """Scan every frame; return rows for symbols with an open setup or one that closed within `recent_bars`.

    Rows are sorted: open setups first, newest signal first. Symbols that cannot be scanned are listed under
    `errors` instead of aborting the whole scan."""
    strat = get_strategy("bullish_divergence", **(params or {}))
    rows, errors = [], []
    for ticker, df in frames.items():
        try:
            df = validate_ohlcv(df)
            if len(df) < min_bars:
                raise ValueError(f"only {len(df)} bars, need at least {min_bars}")
            row = _row(ticker, df, strat, market, recent_bars)
        except (ValueError, KeyError) as e:
            errors.append({"ticker": ticker, "error": str(e)})
            continue
        if row:
            rows.append(row)
    rows.sort(key=lambda r: (r["status"] != "active", r["bars_since_signal"], r["ticker"]))
    return {
        "strategy": "bullish_divergence",
        "market": Market(market).value,
        "params": strat.params.model_dump(),
        "recent_bars": recent_bars,
        "scanned": len(frames) - len(errors),
        "results": rows,
        "errors": errors,
    }
