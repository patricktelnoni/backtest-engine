from __future__ import annotations

from dataclasses import dataclass

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


def _trade_returns(position: pd.Series, net: pd.Series) -> list[float]:
    """One trade = a contiguous run of the same non-zero position."""
    trades, start = [], None
    pos, vals = position.to_numpy(), net.to_numpy()
    for i in range(len(pos)):
        if start is not None and pos[i] != pos[start]:
            trades.append(float(np.prod(1 + vals[start:i]) - 1))
            start = None
        if start is None and pos[i] != 0:
            start = i
    if start is not None:
        trades.append(float(np.prod(1 + vals[start:]) - 1))
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
    return BacktestResult(metrics, equity, position, net, trades)
