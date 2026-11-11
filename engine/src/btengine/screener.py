"""Screen many symbols for setups and summarise what is happening on the last bar.

Pure computation: callers hand in already-loaded OHLCV frames (one per symbol) and get a JSON-ready dict.
A screenable strategy implements `scan(df) -> list[dict]`, one record per setup taken, with keys
entry_bar, exit_bar (None while open), exit_reason, stop and a `detail` dict; detail keys ending in `_bar`
are reported as dates.
"""
from __future__ import annotations

import math

import pandas as pd

from .backtest import run_backtest
from .data import validate_ohlcv
from .markets import Market
from .strategies import get_strategy

SETUPS = ("bullish_divergence", "consolidated_ma")
MIN_BARS = 120


def _num(v):
    if v is None:
        return None
    f = float(v)
    return None if not math.isfinite(f) else f


def _date(index, i: int) -> str:
    return index[i].strftime("%Y-%m-%d")


def _detail(detail: dict, index) -> dict:
    out = {}
    for k, v in detail.items():
        if k.endswith("_bar"):
            out[k[:-4] + "_date"] = _date(index, v)
        elif isinstance(v, float):
            out[k] = _num(v)
        else:
            out[k] = v
    return out


def _row(ticker: str, df: pd.DataFrame, strat, market, recent_bars: int) -> dict | None:
    """One result row, or None when the symbol has no live or recently closed setup."""
    setups = strat.scan(df)
    if not setups:
        return None
    last, n = setups[-1], len(df)
    if last["exit_bar"] is not None and n - 1 - last["entry_bar"] > recent_bars:
        return None

    close, idx = df["close"].to_numpy(), df.index
    e = last["entry_bar"]
    last_close = float(close[-1])
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
        "exit_date": _date(idx, last["exit_bar"]) if last["exit_bar"] is not None else None,
        "exit_reason": last["exit_reason"],
        "detail": _detail(last.get("detail", {}), idx),
        "history": {  # how this setup did on this symbol over the whole frame (same params, market costs)
            "bars": n,
            "n_trades": m["n_trades"],
            "win_rate": _num(m["win_rate"]),
            "profit_factor": _num(m["profit_factor"]),
            "avg_trade_return": _num(sum(bt.trade_returns) / len(bt.trade_returns)) if bt.trade_returns else None,
            "total_return": _num(m["total_return"]),
        },
    }


def screen(frames: dict[str, pd.DataFrame], setups: dict[str, dict | None] | None = None,
           market: Market | str = Market.IDX_STOCK, recent_bars: int = 10, min_bars: int = MIN_BARS) -> dict:
    """Scan every frame with every setup; a symbol is listed under a setup when it has an open one or one
    that closed within `recent_bars`. Rows are sorted: open first, newest signal first. Symbols that cannot
    be scanned are listed under `errors` instead of aborting the scan.

    `setups` maps strategy name -> params (None/{} for defaults); default: every name in SETUPS."""
    if setups is None:
        setups = {name: None for name in SETUPS}
    strats = {name: get_strategy(name, **(params or {})) for name, params in setups.items()}
    results = {name: [] for name in strats}
    errors = []
    for ticker, df in frames.items():
        try:
            df = validate_ohlcv(df)
            if len(df) < min_bars:
                raise ValueError(f"only {len(df)} bars, need at least {min_bars}")
            rows = {name: _row(ticker, df, s, market, recent_bars) for name, s in strats.items()}
        except (ValueError, KeyError) as e:
            errors.append({"ticker": ticker, "error": str(e)})
            continue
        for name, row in rows.items():
            if row:
                results[name].append(row)
    for rows in results.values():
        rows.sort(key=lambda r: (r["status"] != "active", r["bars_since_signal"], r["ticker"]))
    return {
        "market": Market(market).value,
        "recent_bars": recent_bars,
        "scanned": len(frames) - len(errors),
        "errors": errors,
        "setups": {name: {"params": s.params.model_dump(), "results": results[name]} for name, s in strats.items()},
    }
