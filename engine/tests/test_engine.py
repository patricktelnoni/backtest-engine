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

WARM_BARS = 90  # > 3 * max(rsi_period, macd_slow) = 78: past the RSI/MACD warm-up (see BullishDivergence)


def _div_pattern():
    """Sharp drop to low A (pattern bar 35, RSI ~3), rebound, then a slow grind to a lower low B
    (pattern bar 67, RSI ~17), then a rally: lower low in price, higher low in RSI = bullish divergence."""
    return np.concatenate([np.linspace(100, 102, 30), np.linspace(101, 80, 6), np.linspace(81, 90, 10),
                           np.linspace(89.5, 79, 22), np.linspace(80, 96, 15)])


def _div_closes():
    """The classic divergence pattern behind WARM_BARS bars of flat history (no pivots there), so both
    lows sit past the indicator warm-up and their RSI/MACD readings are converged, not seed artifacts."""
    return np.concatenate([np.full(WARM_BARS, 100.0), _div_pattern()])


def _no_div_pattern():
    """Choppy decline to low A (pattern bar 56, RSI ~34), bounce, then a straight decline to a lower low B
    (pattern bar 71, RSI ~29): RSI confirms the lower low, so there is no divergence."""
    chop = 102 + np.cumsum(np.tile([-3.0, 2.2], 13))
    return np.concatenate([np.linspace(100, 102, 30), chop, np.linspace(83, 88, 4), np.linspace(87, 78, 12),
                           np.linspace(79, 90, 12)])


def _no_div_closes():
    return np.concatenate([np.full(WARM_BARS, 100.0), _no_div_pattern()])


def _rsi_of(closes, period=14):
    from btengine.strategies.builtin import _rsi
    return _rsi(pd.Series(closes, dtype=float), period).to_numpy()


def test_bullish_divergence_scenario_is_a_real_divergence():
    c = _div_closes()
    r = _rsi_of(c)
    a, b = 35 + WARM_BARS, 67 + WARM_BARS
    assert c[b] < c[a] and r[b] > r[a] and r[a] <= 30  # lower low in price, higher low in RSI, first low oversold


@pytest.mark.parametrize("right", [1, 3, 5])
def test_bullish_divergence_fires_only_after_pivot_is_confirmed(right):
    df = make_df(_div_closes())
    sig = get_strategy("bullish_divergence", pivot_right=right).signals(df).to_numpy()
    first = int(np.argmax(sig > 0))
    # the low is at bar 67 + WARM_BARS; it can't be known before 67 + WARM_BARS + right
    assert sig.any() and first == 67 + WARM_BARS + right


def test_bullish_divergence_ignores_lower_low_confirmed_by_rsi():
    c = _no_div_closes()
    r = _rsi_of(c)
    a, b = 56 + WARM_BARS, 71 + WARM_BARS
    # Both lows are valid pivots, far enough apart, and the first is oversold at 40 -- only RSI rules it out.
    # (At the default oversold=30 the first low, RSI ~34, is not oversold, which rules it out on its own.)
    assert c[b] < c[a] and r[b] < r[a] and 30 < r[a] <= 40
    assert get_strategy("bullish_divergence").params.oversold == 30
    assert not get_strategy("bullish_divergence").signals(make_df(c)).any()
    assert not get_strategy("bullish_divergence", oversold=40).signals(make_df(c)).any()
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


def test_bullish_divergence_warmup_guard_blocks_seed_artifact_divergences():
    # A fresh decline at the very start of the data: the gain EWM is still zero, so RSI at the first low
    # reads a fake 0.00 and any later higher reading looks like a divergence. The warm-up guard must block it.
    fresh = np.concatenate([np.linspace(100, 94, 8), np.linspace(94, 96.5, 4),
                            np.linspace(96.5, 89, 26), np.linspace(89, 95, 10)])
    assert not get_strategy("bullish_divergence", indicator="any").signals(make_df(fresh)).any()
    # The same pattern behind 150 bars of history: the second low confirms the lower low (lower RSI), so
    # there is no signal either -- but now for the right reason, on converged indicator values.
    warm = np.concatenate([np.linspace(100, 99, 150), fresh])
    assert not get_strategy("bullish_divergence", indicator="any").signals(make_df(warm)).any()
    # The guard must not merely silence everything: a real divergence past the warm-up still fires.
    assert get_strategy("bullish_divergence").signals(make_df(_div_closes())).any()


def _walks(n_seeds=12, bars=600):
    for seed in range(n_seeds):
        rng = np.random.default_rng(seed)
        yield make_df(100 * np.exp(np.cumsum(rng.normal(0, 0.02, bars))))


def _legs_hold(df, osc, j, i):
    """Oscillator's low in the second leg (from the highest high between the lows to i) above its first-leg low."""
    h = j + int(np.argmax(df["high"].to_numpy()[j:i + 1]))
    return osc[h:i + 1].min() > osc[j:h + 1].min()


def test_bullish_divergence_indicator_modes_and_labels():
    # Every setup's label must match what its two lows actually show, and each mode only takes what it asks for.
    allowed = {"rsi": {"rsi", "both"}, "macd": {"macd", "both"}, "both": {"both"}, "any": {"rsi", "macd", "both"}}
    seen = {m: set() for m in allowed}
    for df in _walks():
        ind = get_strategy("bullish_divergence").indicators(df)
        for mode in allowed:
            strat = get_strategy("bullish_divergence", indicator=mode)
            for s in strat.scan(df):
                d = s["detail"]
                j, i = d["first_low_bar"], d["second_low_bar"]
                rsi_div = (d["rsi_second"] > d["rsi_first"] and d["rsi_first"] <= strat.params.oversold
                           and _legs_hold(df, ind["rsi"].to_numpy(), j, i))
                macd_div = (d["macd_second"] > d["macd_first"] and d["macd_first"] < 0
                            and _legs_hold(df, ind["macd"].to_numpy(), j, i))
                assert d["second_low"] < d["first_low"]
                assert d["divergence"] == ("both" if rsi_div and macd_div else "rsi" if rsi_div else "macd")
                assert d["divergence"] in allowed[mode]
                seen[mode].add(d["divergence"])
    assert seen == allowed  # random walks exercise every label in every mode


def test_bullish_divergence_rsi_default_is_unchanged_and_macd_classic_case():
    df = make_df(_div_closes())
    assert get_strategy("bullish_divergence").params.indicator == "rsi"     # backtests keep their old results
    (s,) = get_strategy("bullish_divergence").scan(df)
    assert s["entry_bar"] == 70 + WARM_BARS and s["detail"]["divergence"] == "both"  # diverges on MACD too
    assert get_strategy("bullish_divergence", indicator="macd").scan(df)[0]["entry_bar"] == 70 + WARM_BARS
    assert not get_strategy("bullish_divergence", indicator="any").signals(make_df(_no_div_closes())).any()
    # MACD's below-zero filter: an impossible threshold for RSI leaves only MACD, which then needs macd_first < 0
    for s in get_strategy("bullish_divergence", indicator="any", oversold=1).scan(make_df(_div_closes())):
        assert s["detail"]["divergence"] == "macd" and s["detail"]["macd_first"] < 0
    with pytest.raises(ValueError):
        get_strategy("bullish_divergence", macd_fast=26, macd_slow=12)
    with pytest.raises(ValueError):
        get_strategy("bullish_divergence", indicator="stoch")


@pytest.mark.parametrize("mode", ["macd", "both", "any"])
def test_bullish_divergence_macd_modes_have_no_lookahead(mode):
    rng = np.random.default_rng(7)
    df = make_df(100 * np.exp(np.cumsum(rng.normal(0, 0.02, 300))))
    strat = get_strategy("bullish_divergence", indicator=mode, pivot_left=2, pivot_right=2, oversold=100, max_hold=5)
    full = strat.signals(df).to_numpy()
    assert full.sum() > 0
    for t in range(len(df)):
        assert strat.signals(df.iloc[: t + 1]).iloc[-1] == full[t], f"look-ahead at bar {t}"


def _bounce_closes():
    """Low A (pattern bar 35, ~80), a crash to an even lower low K (pattern bar ~54, ~72), a rebound, then a
    pullback to C (~77): C is below A but a higher low than K -- a bounce, not a bullish divergence of C
    against A. Behind WARM_BARS bars of flat history like _div_closes."""
    return np.concatenate([np.full(WARM_BARS, 100.0), np.linspace(100, 102, 30), np.linspace(101, 80, 6),
                           np.linspace(81, 88, 6), np.linspace(87, 72, 13), np.linspace(73, 86, 10),
                           np.linspace(85, 77, 10), np.linspace(78, 90, 12)])


def test_bullish_divergence_never_pairs_across_a_lower_low():
    df = make_df(_bounce_closes())
    low = df["low"].to_numpy()
    for mode in ("rsi", "macd", "any"):
        for s in get_strategy("bullish_divergence", indicator=mode, oversold=100).scan(df):
            j, i = s["detail"]["first_low_bar"], s["detail"]["second_low_bar"]
            assert low[j:i].min() >= low[i], f"{mode}: pivot {i} paired with {j} across a lower low"
    # The same check on random walks, where intermediate lower lows are common.
    for df in _walks():
        low = df["low"].to_numpy()
        for s in get_strategy("bullish_divergence", indicator="any", oversold=100).scan(df):
            j, i = s["detail"]["first_low_bar"], s["detail"]["second_low_bar"]
            assert low[j:i].min() >= low[i]


def test_bullish_divergence_rejects_oscillator_lower_low_in_second_leg():
    # Every reported divergence must have the oscillator's second-leg low above its first-leg low.
    for df in _walks():
        ind = get_strategy("bullish_divergence").indicators(df)
        for s in get_strategy("bullish_divergence", indicator="any", oversold=100).scan(df):
            d = s["detail"]
            j, i = d["first_low_bar"], d["second_low_bar"]
            if d["divergence"] in ("rsi", "both"):
                assert _legs_hold(df, ind["rsi"].to_numpy(), j, i)
            if d["divergence"] in ("macd", "both"):
                assert _legs_hold(df, ind["macd"].to_numpy(), j, i)


def test_bullish_divergence_pairs_with_most_recent_qualifying_pivot():
    # Re-run the pairing over every candidate: no more recent pivot than the chosen one may also qualify
    # (with the same "both" preference: a newer "both" beats the chosen one, a newer single label cannot when
    # the chosen one is "both").
    from btengine.strategies.builtin import BullishDivergence
    checked = 0
    for df in _walks():
        strat = get_strategy("bullish_divergence", indicator="any", oversold=100)
        p = strat.params
        low, high = df["low"].to_numpy(), df["high"].to_numpy()
        ind = strat.indicators(df)
        rsi, macd = ind["rsi"].to_numpy(), ind["macd"].to_numpy()
        # All confirmed pivots, as the strategy sees them.
        piv = [k for k in range(p.pivot_left, len(df) - p.pivot_right)
               if low[k] < low[k - p.pivot_left:k].min() and low[k] <= low[k + 1:k + 1 + p.pivot_right].min()]
        for s in strat.scan(df):
            d = s["detail"]
            j, i = d["first_low_bar"], d["second_low_bar"]
            newer = [k for k in piv if j < k < i and i - k <= p.max_gap]
            for k in newer:
                hit = BullishDivergence._pick(strat, i, [k], low, high, rsi, macd)
                assert hit is None or (d["divergence"] == "both" and hit[1] != "both"), (i, j, k)
            checked += 1
    assert checked > 0


def test_rsi_is_100_without_losses_and_50_when_flat():
    from btengine.strategies.builtin import _rsi
    up = _rsi(pd.Series(np.linspace(100, 120, 30)), 14).to_numpy()
    flat = _rsi(pd.Series(np.full(30, 100.0)), 14).to_numpy()
    assert up[0] == 50 and (up[1:] == 100).all()
    assert (flat == 50).all()


def test_bullish_divergence_stop_exits_when_divergence_low_breaks():
    # Entry at bar 70 + WARM_BARS (pivot at 67 + WARM_BARS confirmed); stop = low of that bar = 79 * 0.99 = 78.21.
    # The next bar closes at 78 < 78.21 -> the setup is invalidated and the position is closed on that bar.
    c = np.concatenate([_div_closes()[:68 + WARM_BARS], [80, 80.5, 81, 78, 77, 77, 77]])
    df = make_df(c)
    with_stop = get_strategy("bullish_divergence", exit_rsi=100).signals(df).to_numpy()
    no_stop = get_strategy("bullish_divergence", exit_rsi=100, use_stop=False).signals(df).to_numpy()
    assert with_stop[70 + WARM_BARS] == 1 and with_stop[71 + WARM_BARS:].sum() == 0
    assert (no_stop[70 + WARM_BARS:] == 1).all()


def test_bullish_divergence_max_hold():
    df = make_df(_div_closes())
    sig = get_strategy("bullish_divergence", exit_rsi=100, use_stop=False, max_hold=5).signals(df).to_numpy()
    assert (sig[70 + WARM_BARS:75 + WARM_BARS] == 1).all() and sig[75 + WARM_BARS] == 0


def test_bullish_divergence_param_validation():
    with pytest.raises(ValueError, match="min_gap must be < max_gap"):
        get_strategy("bullish_divergence", min_gap=30, max_gap=30)


def test_bullish_divergence_runs_through_engine():
    r = run_backtest(make_df(_div_closes()), "bullish_divergence", Market.IDX_STOCK)
    assert r.metrics["n_trades"] == 1 and r.trades[0]["entry_date"] == make_df(_div_closes()).index[70 + WARM_BARS]
