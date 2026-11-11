import numpy as np
import pandas as pd
import pytest

from btengine.screener import screen_bullish_divergence
from btengine.strategies import get_strategy
from test_engine import _div_closes, make_df


def test_scan_matches_signals():
    df = make_df(_div_closes())
    strat = get_strategy("bullish_divergence")
    sig = strat.signals(df).to_numpy()
    (setup,) = strat.scan(df)
    assert setup["entry_bar"] == 70 == int(np.argmax(sig > 0))
    assert setup["pivot_bar"] == 67 and setup["ref_pivot_bar"] == 35
    assert setup["stop"] == pytest.approx(df["low"].iloc[67])
    exit_bar = setup["exit_bar"]
    assert sig[70:exit_bar].all() and not sig[exit_bar:].any()
    assert setup["exit_reason"] in {"stop", "rsi", "max_hold"}


def test_screener_reports_open_and_recent_setups_only():
    div = make_df(_div_closes())                                   # setup fires at bar 70, exits later
    open_setup = make_df(_div_closes()[:72])                       # cut right after entry: still open
    flat = make_df(np.linspace(100, 150, 200))                     # no swing lows at all -> no setup
    short = make_df([100, 101, 102])
    out = screen_bullish_divergence({"OPEN": open_setup, "DIV": div, "FLAT": flat, "SHORT": short},
                                    params={"max_hold": 5}, recent_bars=100, min_bars=50)
    assert out["scanned"] == 3 and out["errors"] == [{"ticker": "SHORT", "error": "only 3 bars, need at least 50"}]
    assert [r["ticker"] for r in out["results"]] == ["OPEN", "DIV"]   # open first
    o, d = out["results"]
    assert o["status"] == "active" and o["exit_date"] is None and o["bars_since_signal"] == 1
    assert d["status"] == "exited" and d["exit_reason"] == "max_hold" and d["signal_date"] == "2022-03-14"
    assert o["stop_distance"] == pytest.approx(o["last_close"] / o["stop"] - 1)
    assert d["history"]["n_trades"] == 1 and 0 <= d["history"]["win_rate"] <= 1
    assert out["params"]["max_hold"] == 5 and out["market"] == "idx_stock"
    # recent_bars window excludes an old, closed setup
    assert screen_bullish_divergence({"DIV": div}, params={"max_hold": 5}, recent_bars=3, min_bars=50)["results"] == []
