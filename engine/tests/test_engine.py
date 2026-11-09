import numpy as np
import pandas as pd
import pytest

from btengine import CostModel, Market, run_backtest
from btengine.strategies import REGISTRY, get_strategy


def make_df(closes, start="2022-01-03"):
    idx = pd.date_range(start, periods=len(closes), freq="D", tz="UTC")
    c = pd.Series(closes, index=idx, dtype=float)
    return pd.DataFrame({"open": c, "high": c * 1.01, "low": c * 0.99, "close": c, "volume": 1000.0})


ZERO = CostModel()


def test_buy_hold_matches_price_return_after_first_bar():
    df = make_df([100, 110, 121, 133.1])
    r = run_backtest(df, "buy_hold", Market.CRYPTO, costs=ZERO)
    # Signal at bar 0 -> position held over bar 1's close-to-close return; bar 0 itself earns nothing.
    assert r.position.iloc[0] == 0 and r.position.iloc[1] == 1
    assert r.metrics["total_return"] == pytest.approx(133.1 / 100 - 1)


def test_no_lookahead_signal_executes_next_bar():
    # Price jumps 50% on bar 2. A strategy that goes long on the jump bar must NOT capture it.
    df = make_df([100, 100, 150, 150, 150])

    class JumpChaser(get_strategy("buy_hold").__class__):
        def signals(self, d):
            return (d["close"] > 120).astype(float)

    r = run_backtest(df, JumpChaser(), Market.CRYPTO, costs=ZERO)
    assert r.metrics["total_return"] == pytest.approx(0.0)


def test_costs_reduce_returns():
    rng = np.random.default_rng(0)
    df = make_df(100 * np.exp(np.cumsum(rng.normal(0.0005, 0.01, 400))))
    free = run_backtest(df, "sma_cross", Market.CRYPTO, costs=ZERO)
    paid = run_backtest(df, "sma_cross", Market.CRYPTO)
    assert paid.metrics["total_return"] < free.metrics["total_return"]


def test_idx_is_long_only_even_if_strategy_shorts():
    rng = np.random.default_rng(1)
    df = make_df(100 * np.exp(np.cumsum(rng.normal(-0.001, 0.01, 300))))
    r = run_backtest(df, "sma_cross", Market.IDX_STOCK, strategy_params={"allow_short": True})
    assert (r.position >= 0).all()
    r2 = run_backtest(df, "sma_cross", Market.US_STOCK, strategy_params={"allow_short": True})
    assert (r2.position < 0).any()


def test_annualization_differs_by_market():
    rng = np.random.default_rng(2)
    df = make_df(100 * np.exp(np.cumsum(rng.normal(0.0003, 0.01, 500))))
    a = run_backtest(df, "buy_hold", Market.CRYPTO, costs=ZERO).metrics["annual_volatility"]
    b = run_backtest(df, "buy_hold", Market.US_STOCK, costs=ZERO).metrics["annual_volatility"]
    assert a / b == pytest.approx(np.sqrt(365 / 252))


def test_max_drawdown_known_value():
    df = make_df([100, 100, 120, 60, 90])
    r = run_backtest(df, "buy_hold", Market.CRYPTO, costs=ZERO)
    assert r.metrics["max_drawdown"] == pytest.approx(-0.5)


def test_trade_count_and_win_rate():
    # up, down, up; long only when close > previous close (via custom strategy)
    df = make_df([100, 101, 102, 90, 80, 85, 95, 105])

    class Mom(get_strategy("buy_hold").__class__):
        def signals(self, d):
            return (d["close"].diff() > 0).astype(float)

    r = run_backtest(df, Mom(), Market.CRYPTO, costs=ZERO)
    assert r.metrics["n_trades"] >= 1
    assert 0 <= r.metrics["win_rate"] <= 1


def test_all_strategies_run_on_all_markets():
    rng = np.random.default_rng(3)
    df = make_df(100 * np.exp(np.cumsum(rng.normal(0.0003, 0.015, 600))))
    for name in REGISTRY:
        for m in Market:
            res = run_backtest(df, name, m)
            assert len(res.equity) == len(df)
            assert np.isfinite(res.metrics["max_drawdown"])


def test_param_validation_and_schema():
    with pytest.raises(Exception):
        get_strategy("sma_cross", fast=50, slow=20)
    assert "fast" in get_strategy("sma_cross").schema()["properties"]


def test_invalid_data_rejected():
    df = make_df([100, 101, 102])
    with pytest.raises(ValueError):
        run_backtest(df.drop(columns=["volume"]), "buy_hold", Market.CRYPTO)
    bad = df.copy(); bad.iloc[1, bad.columns.get_loc("close")] = -1
    with pytest.raises(ValueError):
        run_backtest(bad, "buy_hold", Market.CRYPTO)
