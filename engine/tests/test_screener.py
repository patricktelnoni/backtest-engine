import numpy as np
import pytest

from btengine.screener import SETUPS, screen
from btengine.strategies import get_strategy
from test_engine import _div_closes, make_df


def test_scan_matches_signals():
    df = make_df(_div_closes())
    strat = get_strategy("bullish_divergence")
    sig = strat.signals(df).to_numpy()
    (setup,) = strat.scan(df)
    assert setup["entry_bar"] == 70 == int(np.argmax(sig > 0))
    assert setup["detail"]["second_low_bar"] == 67 and setup["detail"]["first_low_bar"] == 35
    assert setup["stop"] == pytest.approx(df["low"].iloc[67])
    exit_bar = setup["exit_bar"]
    assert sig[70:exit_bar].all() and not sig[exit_bar:].any()
    assert setup["exit_reason"] in {"stop", "rsi", "max_hold"}


def test_screener_reports_open_and_recent_setups_only():
    div = make_df(_div_closes())                                   # setup fires at bar 70, exits later
    open_setup = make_df(_div_closes()[:72])                       # cut right after entry: still open
    flat = make_df(np.linspace(100, 150, 200))                     # no swing lows at all -> no setup
    short = make_df([100, 101, 102])
    out = screen({"OPEN": open_setup, "DIV": div, "FLAT": flat, "SHORT": short},
                 {"bullish_divergence": {"max_hold": 5}}, recent_bars=100, min_bars=50)
    assert out["scanned"] == 3 and out["errors"] == [{"ticker": "SHORT", "error": "only 3 bars, need at least 50"}]
    assert list(out["setups"]) == ["bullish_divergence"]
    bd = out["setups"]["bullish_divergence"]
    assert [r["ticker"] for r in bd["results"]] == ["OPEN", "DIV"]   # open first
    o, d = bd["results"]
    assert o["status"] == "active" and o["exit_date"] is None and o["bars_since_signal"] == 1
    assert d["status"] == "exited" and d["exit_reason"] == "max_hold" and d["signal_date"] == "2022-03-14"
    assert d["detail"]["second_low_date"] == "2022-03-11" and "second_low_bar" not in d["detail"]
    assert o["stop_distance"] == pytest.approx(o["last_close"] / o["stop"] - 1)
    assert d["history"]["n_trades"] == 1 and 0 <= d["history"]["win_rate"] <= 1
    assert bd["params"]["max_hold"] == 5 and out["market"] == "idx_stock"
    # recent_bars window excludes an old, closed setup
    assert screen({"DIV": div}, {"bullish_divergence": {"max_hold": 5}}, recent_bars=3, min_bars=50)["setups"]["bullish_divergence"]["results"] == []


def test_screener_defaults_to_every_setup():
    out = screen({"X": make_df(np.linspace(100, 150, 300))})
    assert set(out["setups"]) == set(SETUPS) == {"bullish_divergence", "consolidated_ma"}


# ---------- consolidated_ma ----------

def _squeeze_df(breakout_vol=3000.0, n_flat=260):
    """Flat at 100 (EMAs converge, volume 1000), then a 6% breakout bar on `breakout_vol`, then drifting up."""
    closes = np.concatenate([np.full(n_flat, 100.0), [106.0], np.linspace(106.5, 112, 30)])
    df = make_df(closes)
    df["volume"] = 1000.0
    df.iloc[n_flat, df.columns.get_loc("volume")] = breakout_vol
    return df


def test_consolidated_ma_fires_on_breakout_from_tight_bundle_with_volume():
    df = _squeeze_df()
    strat = get_strategy("consolidated_ma")
    sig = strat.signals(df).to_numpy()
    (setup,) = strat.scan(df)
    assert setup["entry_bar"] == 260 == int(np.argmax(sig > 0))
    d = setup["detail"]
    assert d["spread_pct"] < 3 and d["squeeze_bars"] >= 5 and d["volume_ratio"] == pytest.approx(3.0)
    assert d["ema_bottom"] <= setup["stop"] <= d["ema_top"] <= 106
    assert setup["exit_bar"] is None and sig[260:].all()     # still held at the end (max_hold 60 not reached)


def test_consolidated_ma_volume_filter_and_warmup():
    quiet = _squeeze_df(breakout_vol=1000.0)                    # breakout without volume: no signal
    assert not get_strategy("consolidated_ma").signals(quiet).any()
    assert get_strategy("consolidated_ma", volume_mult=0).signals(quiet).to_numpy()[260] == 1
    early = _squeeze_df(n_flat=150)                               # EMAs not warmed up (need 200 bars): no signal
    assert not get_strategy("consolidated_ma").signals(early).any()


def test_consolidated_ma_exits_on_stop_and_max_hold():
    df = _squeeze_df()
    closes = df["close"].to_numpy().copy()
    closes[265:] = 95.0                                           # falls back through the bundle
    crash = make_df(closes); crash["volume"] = df["volume"].to_numpy()
    (s,) = get_strategy("consolidated_ma").scan(crash)
    assert (s["exit_bar"], s["exit_reason"]) == (265, "stop")
    (s,) = get_strategy("consolidated_ma", use_stop=False, max_hold=10).scan(crash)
    assert (s["exit_bar"], s["exit_reason"]) == (270, "max_hold")


# ---------- trade plan & chart ----------

def test_idx_tick_and_snap():
    from btengine.screener import idx_tick, _snap
    assert [idx_tick(p) for p in (150, 250, 1000, 3000, 9000)] == [1, 2, 5, 10, 25]
    assert _snap(1003, "idx_stock") == 1005 and _snap(1003, "idx_stock", "down") == 1000 and _snap(1001, "idx_stock", "up") == 1005
    assert _snap(1.23456, "crypto") == 1.2346


def test_trade_plan_rr_2_and_3_on_ticks():
    from btengine.screener import trade_plan
    p = trade_plan(last_close=2460, signal_price=2400, stop=2280, market="idx_stock")
    assert p["stop_loss"] == 2260                                  # 2280 * 0.995 = 2268.6 -> down to tick 10
    assert p["entry_low"] == 2400 and p["entry_high"] == 2460 and p["entry"] == 2430
    risk = p["entry"] - p["stop_loss"]
    assert p["tp1"] >= p["entry"] + 2 * risk and p["tp1"] < p["entry"] + 2 * risk + 10
    assert p["tp2"] >= p["entry"] + 3 * risk and p["tp2"] % 10 == 0
    assert p["risk_pct"] == pytest.approx(risk / 2430) and p["rr_targets"] == [2.0, 3.0]
    assert p["rr_at_last"] == pytest.approx((p["tp1"] - 2460) / (2460 - 2260))
    # price fell to the stop: no plan
    assert trade_plan(2250, 2400, 2280, "idx_stock") is None
    # price below the signal but above the stop: area is [close, signal]
    q = trade_plan(2350, 2400, 2280, "idx_stock")
    assert (q["entry_low"], q["entry_high"]) == (2350, 2400)


def test_rows_carry_plan_and_chart():
    open_setup = make_df(_div_closes()[:72])
    out = screen({"OPEN": open_setup}, {"bullish_divergence": {}, "consolidated_ma": {}}, min_bars=50, chart_bars=40)
    (r,) = out["setups"]["bullish_divergence"]["results"]
    assert r["plan"] is not None and r["plan"]["stop_loss"] < r["stop"] < r["plan"]["entry"] < r["plan"]["tp1"] < r["plan"]["tp2"]
    c = r["chart"]
    assert len(c["t"]) == len(c["close"]) == len(c["series"]["rsi"]) == 40 and c["t"][-1] == r["last_date"]
    # an exited setup has no plan
    out = screen({"DIV": make_df(_div_closes())}, {"bullish_divergence": {"max_hold": 5}}, recent_bars=100, min_bars=50)
    assert out["setups"]["bullish_divergence"]["results"][0]["plan"] is None
    # consolidated_ma charts carry the five EMAs
    df = make_df(np.linspace(100, 150, 300))
    out = screen({"X": df}, {"consolidated_ma": {}})
    assert out["setups"]["consolidated_ma"]["results"] == []  # no setup, but indicators are exercised via strategy
    from btengine.strategies import get_strategy
    assert set(get_strategy("consolidated_ma").indicators(df)) == {"ema10", "ema20", "ema50", "ema100", "ema200"}
