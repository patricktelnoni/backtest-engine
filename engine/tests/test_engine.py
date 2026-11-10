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


def test_trade_details_dates_prices_and_consistency():
    df = make_df([100, 100, 110, 120, 90, 90, 95])

    class Manual(get_strategy("buy_hold").__class__):
        def signals(self, d):
            return pd.Series([0, 1, 1, 0, 0, 1, 1], index=d.index, dtype=float)

    r = run_backtest(df, Manual(), Market.CRYPTO, costs=ZERO)
    # position (shifted one bar) = [0,0,1,1,0,0,1] -> two trades, the second still open
    assert len(r.trades) == 2
    assert [t["return"] for t in r.trades] == pytest.approx(r.trade_returns)
    t0, t1 = r.trades
    # signal bar 1 (close 100) -> earns bars 2-3 -> exit at close of bar 3 (120)
    assert (t0["side"], t0["entry_price"], t0["exit_price"], t0["bars"], t0["open"]) == ("long", 100.0, 120.0, 2, False)
    assert t0["entry_date"] == df.index[1] and t0["exit_date"] == df.index[3]
    assert t0["return"] == pytest.approx(0.20)
    assert (t1["entry_price"], t1["exit_price"], t1["bars"], t1["open"]) == (90.0, 95.0, 1, True)
    assert t1["return"] == pytest.approx(95 / 90 - 1)


def test_open_trade_is_marked_and_valued_at_last_close():
    df = make_df([100, 100, 105, 110])
    r = run_backtest(df, "buy_hold", Market.CRYPTO, costs=ZERO)
    (t,) = r.trades
    assert t["open"] and t["exit_date"] == df.index[-1] and t["exit_price"] == 110.0
    assert t["entry_price"] == 100.0 and t["return"] == pytest.approx(0.10)
    assert [t["return"]] == r.trade_returns


# ---------- bullish_divergence ----------

def _div_closes():
    """Warm-up, sharp drop to low A (bar 35, RSI ~3), rebound, then a slow grind to a lower low B
    (bar 67, RSI ~17), then a rally: lower low in price, higher low in RSI = bullish divergence."""
    return np.concatenate([np.linspace(100, 102, 30), np.linspace(101, 80, 6), np.linspace(81, 90, 10),
                           np.linspace(89.5, 79, 22), np.linspace(80, 96, 15)])


def _no_div_closes():
    """Choppy decline to low A (bar 56, RSI ~34), bounce, then a straight decline to a lower low B
    (bar 71, RSI ~29): RSI confirms the lower low, so there is no divergence."""
    chop = 102 + np.cumsum(np.tile([-3.0, 2.2], 13))
    return np.concatenate([np.linspace(100, 102, 30), chop, np.linspace(83, 88, 4), np.linspace(87, 78, 12),
                           np.linspace(79, 90, 12)])


def _rsi_of(closes, period=14):
    from btengine.strategies.builtin import _rsi
    return _rsi(pd.Series(closes, dtype=float), period).to_numpy()


def test_bullish_divergence_scenario_is_a_real_divergence():
    c = _div_closes()
    r = _rsi_of(c)
    assert c[67] < c[35] and r[67] > r[35] and r[35] <= 40  # lower low in price, higher low in RSI


@pytest.mark.parametrize("right", [1, 3, 5])
def test_bullish_divergence_fires_only_after_pivot_is_confirmed(right):
    df = make_df(_div_closes())
    sig = get_strategy("bullish_divergence", pivot_right=right).signals(df).to_numpy()
    first = int(np.argmax(sig > 0))
    assert sig.any() and first == 67 + right  # the low is at bar 67; it can't be known before 67 + right


def test_bullish_divergence_ignores_lower_low_confirmed_by_rsi():
    c = _no_div_closes()
    r = _rsi_of(c)
    # Both lows are valid pivots, far enough apart, and the first is oversold -- only RSI rules it out.
    assert c[71] < c[56] and r[71] < r[56] and r[56] <= 40
    assert not get_strategy("bullish_divergence").signals(make_df(c)).any()
    # Sanity: lifting the oversold filter changes nothing, and the scenario does produce pivots at 56 and 71.
    assert not get_strategy("bullish_divergence", oversold=100).signals(make_df(c)).any()


def test_bullish_divergence_has_no_lookahead():
    # Signal at bar t must not change when bars after t are removed.
    rng = np.random.default_rng(7)
    df = make_df(100 * np.exp(np.cumsum(rng.normal(0, 0.02, 300))))
    strat = get_strategy("bullish_divergence", pivot_left=2, pivot_right=2, oversold=100, max_hold=5)
    full = strat.signals(df).to_numpy()
    assert full.sum() > 0, "test needs at least one signal to be meaningful"
    for t in range(len(df)):
        assert strat.signals(df.iloc[: t + 1]).iloc[-1] == full[t], f"look-ahead at bar {t}"


def test_bullish_divergence_stop_exits_when_divergence_low_breaks():
    # Entry at bar 70 (pivot at 67 confirmed); stop = low of bar 67 = 79 * 0.99 = 78.21.
    # Bar 71 closes at 78 < 78.21 -> the setup is invalidated and the position is closed on that bar.
    c = np.concatenate([_div_closes()[:68], [80, 80.5, 81, 78, 77, 77, 77]])
    df = make_df(c)
    with_stop = get_strategy("bullish_divergence", exit_rsi=100).signals(df).to_numpy()
    no_stop = get_strategy("bullish_divergence", exit_rsi=100, use_stop=False).signals(df).to_numpy()
    assert with_stop[70] == 1 and with_stop[71:].sum() == 0
    assert (no_stop[70:] == 1).all()


def test_bullish_divergence_max_hold():
    df = make_df(_div_closes())
    sig = get_strategy("bullish_divergence", exit_rsi=100, use_stop=False, max_hold=5).signals(df).to_numpy()
    assert (sig[70:75] == 1).all() and sig[75] == 0


def test_bullish_divergence_param_validation():
    with pytest.raises(ValueError, match="min_gap must be < max_gap"):
        get_strategy("bullish_divergence", min_gap=30, max_gap=30)


def test_bullish_divergence_runs_through_engine():
    r = run_backtest(make_df(_div_closes()), "bullish_divergence", Market.IDX_STOCK)
    assert r.metrics["n_trades"] == 1 and r.trades[0]["entry_date"] == make_df(_div_closes()).index[70]
