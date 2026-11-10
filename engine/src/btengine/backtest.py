from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .data import validate_ohlcv
from .markets import CostModel, Market, get_spec
from .metrics import compute_metrics
from .strategies import Strategy, get_strategy


@dataclass
class BacktestResult:
    metrics: dict
    equity: pd.Series
    position: pd.Series
    net_returns: pd.Series
    trade_returns: list[float]
    trades: list[dict] = field(default_factory=list)


def _trade_spans(position: pd.Series) -> list[tuple[int, int]]:
    """[start, end) bar ranges of one trade = a contiguous run of the same non-zero position."""
    spans, start = [], None
    pos = position.to_numpy()
    for i in range(len(pos)):
        if start is not None and pos[i] != pos[start]:
            spans.append((start, i))
            start = None
        if start is None and pos[i] != 0:
            start = i
    if start is not None:
        spans.append((start, len(pos)))
    return spans


def _trade_returns(position: pd.Series, net: pd.Series) -> list[float]:
    vals = net.to_numpy()
    return [float(np.prod(1 + vals[s:e]) - 1) for s, e in _trade_spans(position)]


def build_trades(position: pd.Series, net: pd.Series, close: pd.Series) -> list[dict]:
    """Per-trade detail. `position` is already shifted, so a trade spanning bars [s, e) was
    entered at the close of bar s-1 (the signal bar) and exited at the close of bar e-1.
    A trade still held on the last bar is marked `open` and valued at the last close."""
    idx, pos, vals, px = position.index, position.to_numpy(), net.to_numpy(), close.to_numpy()
    trades = []
    for s, e in _trade_spans(position):
        trades.append({
            "side": "long" if pos[s] > 0 else "short",
            "size": float(abs(pos[s])),
            "entry_date": idx[s - 1], "entry_price": float(px[s - 1]),
            "exit_date": idx[e - 1], "exit_price": float(px[e - 1]),
            "bars": e - s,
            "return": float(np.prod(1 + vals[s:e]) - 1),
            "open": e == len(pos),
        })
    return trades


def run_backtest(
    df: pd.DataFrame,
    strategy: Strategy | str,
    market: Market | str,
    initial_cash: float = 10_000.0,
    costs: CostModel | None = None,
    strategy_params: dict | None = None,
) -> BacktestResult:
    """Vectorized daily-bar backtest.

    A signal computed from bar t's close is held over bar t+1's close-to-close return,
    i.e. fills are assumed at the signal bar's close (no look-ahead, but no overnight gap
    risk modelled; slippage_bps is the proxy for that).
    """
    df = validate_ohlcv(df)
    spec = get_spec(market)
    if isinstance(strategy, str):
        strategy = get_strategy(strategy, **(strategy_params or {}))
    costs = costs or spec.default_costs

    target = strategy.signals(df).clip(-1.0, 1.0)
    if not spec.allow_short:
        target = target.clip(lower=0.0)
    position = target.shift(1).fillna(0.0)  # execute next bar: no look-ahead

    ret = df["close"].pct_change().fillna(0.0)
    turnover = position.diff().abs()
    turnover.iloc[0] = abs(position.iloc[0])
    ppy = spec.periods_per_year

    gross = position * ret
    trading_cost = turnover * costs.per_side_rate
    borrow = (-position).clip(lower=0) * costs.borrow_bps_annual / 10_000 / ppy
    swap = position.abs() * costs.swap_bps_daily / 10_000
    net = gross - trading_cost - borrow - swap

    equity = initial_cash * (1 + net).cumprod()
    trades = _trade_returns(position, net)
    metrics = compute_metrics(net, equity, position, trades, ppy)
    return BacktestResult(metrics, equity, position, net, trades, build_trades(position, net, df["close"]))
