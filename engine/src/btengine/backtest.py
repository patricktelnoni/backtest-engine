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
    pos = position.to_numpy()
    n = len(pos)
    if n == 0:
        return []
    change = np.flatnonzero(pos[1:] != pos[:-1]) + 1  # first bar of every new run
    starts = np.concatenate(([0], change)).tolist()
    ends = np.concatenate((change, [n])).tolist()
    return [(s, e) for s, e in zip(starts, ends) if pos[s] != 0]


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
    trades = build_trades(position, net, df["close"])
    trade_returns = [t["return"] for t in trades]
    metrics = compute_metrics(net, equity, position, trade_returns, ppy)
    return BacktestResult(metrics, equity, position, net, trade_returns, trades)
