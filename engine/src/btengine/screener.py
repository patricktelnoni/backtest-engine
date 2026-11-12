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
CHART_BARS = 120
STOP_BUFFER = 0.005      # stop loss sits 0.5% under the setup's invalidation level
RR_TARGETS = (2.0, 3.0)  # take profits at 2R and 3R


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


def idx_tick(price: float) -> float:
    """IDX price fraction (tick size) for a given price level."""
    if price < 200:
        return 1.0
    if price < 500:
        return 2.0
    if price < 2000:
        return 5.0
    if price < 5000:
        return 10.0
    return 25.0


def _tick(price: float, market) -> float | None:
    return idx_tick(price) if Market(market) == Market.IDX_STOCK else None


def _snap(price: float, market, mode: str = "nearest") -> float:
    """Round to a valid tick (IDX) or to 4 decimals (other markets)."""
    tick = _tick(price, market)
    if tick is None:
        return round(price, 4)
    q = price / tick
    n = math.floor(q) if mode == "down" else math.ceil(q) if mode == "up" else round(q)
    return float(n * tick)


def trade_plan(last_close: float, signal_price: float, stop: float, market) -> dict | None:
    """Entry area, stop loss and 2R/3R targets. The entry area spans the signal close and the current close
    (whichever order), never below the stop; R is measured from the middle of that area. None when the
    setup is already invalid (price at or under the stop)."""
    stop_loss = _snap(stop * (1 - STOP_BUFFER), market, "down")
    if last_close <= stop_loss:
        return None
    tick = _tick(stop_loss, market) or 0.0
    low = max(min(last_close, signal_price), stop_loss + tick)
    high = max(last_close, signal_price, low)
    entry = _snap((low + high) / 2, market)
    risk = entry - stop_loss
    if risk <= 0:
        return None
    targets = [_snap(entry + rr * risk, market, "up") for rr in RR_TARGETS]
    return {
        "entry_low": _snap(low, market, "down"),
        "entry_high": _snap(high, market, "up"),
        "entry": entry,
        "stop_loss": stop_loss,
        "risk_pct": risk / entry,
        "tp1": targets[0],
        "tp2": targets[1],
        "tp1_pct": targets[0] / entry - 1,
        "tp2_pct": targets[1] / entry - 1,
        "rr_targets": list(RR_TARGETS),
        # reward:risk if one bought at the current close instead of the planned entry
        "rr_at_last": (targets[0] - last_close) / (last_close - stop_loss),
    }


def _chart(df: pd.DataFrame, strat, bars: int) -> dict:
    """Last `bars` bars of price plus the setup's indicator series, as plain lists for the UI."""
    ind = strat.indicators(df)
    tail = df.iloc[-bars:]
    out = {
        "t": [d.strftime("%Y-%m-%d") for d in tail.index],
        "open": [round(float(v), 4) for v in tail["open"]],
        "close": [round(float(v), 4) for v in tail["close"]],
        "high": [round(float(v), 4) for v in tail["high"]],
        "low": [round(float(v), 4) for v in tail["low"]],
        "series": {name: [_num(round(float(v), 4)) for v in ser.iloc[-bars:]] for name, ser in ind.items()},
    }
    return out


def _row(ticker: str, df: pd.DataFrame, strat, market, recent_bars: int, chart_bars: int = CHART_BARS) -> dict | None:
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
        "plan": trade_plan(last_close, float(close[e]), last["stop"], market) if last["exit_bar"] is None else None,
        "chart": _chart(df, strat, chart_bars),
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
           market: Market | str = Market.IDX_STOCK, recent_bars: int = 10, min_bars: int = MIN_BARS,
           chart_bars: int = CHART_BARS) -> dict:
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
            rows = {name: _row(ticker, df, s, market, recent_bars, chart_bars) for name, s in strats.items()}
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
