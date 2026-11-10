import datetime as dt
import json
import math

import numpy as np
import pytest
from fastapi.testclient import TestClient

from main import MAX_COMPARE_RUNS, app

client = TestClient(app)


def csv_bytes(n=200, seed=0, header="Date,Open,High,Low,Close,Volume") -> bytes:
    rng = np.random.default_rng(seed)
    p, d0 = 100.0, dt.date(2023, 1, 1)
    lines = [header]
    for i in range(n):
        p *= 1 + rng.normal(0.001, 0.012)
        lines.append(f"{d0 + dt.timedelta(i)},{p},{p * 1.01},{p * 0.99},{p},1000")
    return "\n".join(lines).encode()


def post(path, files=None, **data):
    return client.post(path, data={k: str(v) for k, v in data.items()}, files=files)


def bt(**over):
    d = dict(strategy="sma_cross", market="crypto", params=json.dumps({"fast": 10, "slow": 30}))
    d.update(over)
    return post("/api/backtest", **d)


# ---------- catalog ----------

def test_strategies_expose_param_schema():
    body = client.get("/api/strategies").json()
    names = {s["name"] for s in body}
    assert {"buy_hold", "sma_cross", "rsi_reversion", "donchian_breakout", "bullish_divergence"} <= names
    sma = next(s for s in body if s["name"] == "sma_cross")
    assert sma["params_schema"]["properties"]["fast"]["default"] == 20


def test_markets_include_default_costs():
    body = {m["name"]: m for m in client.get("/api/markets").json()}
    assert set(body) == {"us_stock", "idx_stock", "crypto", "forex"}
    assert body["idx_stock"]["allow_short"] is False
    assert body["crypto"]["default_costs"]["commission_bps"] == 10


# ---------- /api/backtest ----------

def test_backtest_demo_data_shape():
    r = bt()
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["bars"] == len(j["series"]) == 750
    assert {"t", "equity", "benchmark", "drawdown", "position"} <= set(j["series"][0])
    assert all(p["drawdown"] <= 0 for p in j["series"])
    assert set(j["metrics"]) == set(j["benchmark_metrics"])


def test_backtest_trades_are_consistent_with_trade_returns():
    j = bt().json()
    assert j["trades"], "expected at least one trade"
    assert [t["return"] for t in j["trades"]] == pytest.approx(j["trade_returns"])
    assert j["metrics"]["n_trades"] == len(j["trades"])
    for t in j["trades"]:
        assert t["side"] in {"long", "short"} and t["bars"] >= 1
        assert t["entry_date"] <= t["exit_date"]
    assert sum(t["open"] for t in j["trades"]) <= 1  # only the last trade can still be open


def test_backtest_metrics_have_no_nan_in_json():
    # buy_hold on a short series -> some ratios are undefined; they must be null, not NaN
    r = client.post("/api/backtest", data={"strategy": "buy_hold", "market": "forex"},
                    files={"file": ("a.csv", csv_bytes(n=5), "text/csv")})
    assert r.status_code == 200
    assert "NaN" not in r.text and "Infinity" not in r.text
    for v in r.json()["metrics"].values():
        assert v is None or math.isfinite(v)


def test_backtest_uploaded_csv_and_date_filter():
    r = client.post("/api/backtest",
                    data={"strategy": "buy_hold", "market": "us_stock", "start": "2023-02-01", "end": "2023-03-31"},
                    files={"file": ("a.csv", csv_bytes(), "text/csv")})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["series"][0]["t"] == "2023-02-01" and j["series"][-1]["t"] == "2023-03-31"
    assert j["bars"] == 59


def test_backtest_cost_overrides_change_result():
    free = bt(commission_bps=0, slippage_bps=0, borrow_bps_annual=0).json()
    paid = bt(commission_bps=100, slippage_bps=50).json()
    assert paid["metrics"]["total_return"] < free["metrics"]["total_return"]


def test_backtest_idx_never_shorts():
    j = bt(market="idx_stock", params=json.dumps({"fast": 10, "slow": 30, "allow_short": True})).json()
    assert all(p["position"] >= 0 for p in j["series"])


@pytest.mark.parametrize("over, fragment", [
    (dict(strategy="nope"), "unknown strategy"),
    (dict(market="mars"), "unknown market"),
    (dict(initial_cash=0), "initial_cash"),
    (dict(initial_cash=-5), "initial_cash"),
    (dict(params="[1,2]"), "JSON object"),
    (dict(params="not json"), "JSON object"),
    (dict(params=json.dumps({"fast": 50, "slow": 30})), "fast must be < slow"),
    (dict(params=json.dumps({"fast": 1})), "fast"),
    (dict(start="2090-01-01"), "no data left"),
])
def test_backtest_validation_errors_are_422_with_message(over, fragment):
    r = bt(**over)
    assert r.status_code == 422, r.text
    assert fragment in r.json()["detail"]


def test_backtest_missing_required_field_is_422():
    assert client.post("/api/backtest", data={"market": "crypto"}).status_code == 422


@pytest.mark.parametrize("content, fragment", [
    (b"date,open\n2020-01-01,1\n", "missing columns"),
    (b"", None),
    (b"date,open,high,low,close,volume\nnot-a-date,1,1,1,1,1\n", None),
    (b"date,open,high,low,close,volume\n2020-01-01,1,1,1,0,1\n", "close"),
])
def test_backtest_bad_csv_is_422(content, fragment):
    r = client.post("/api/backtest", data={"strategy": "buy_hold", "market": "crypto"},
                    files={"file": ("bad.csv", content, "text/csv")})
    assert r.status_code == 422, r.text
    if fragment:
        assert fragment in r.json()["detail"]


def test_backtest_rejects_oversized_upload(monkeypatch):
    import main
    monkeypatch.setattr(main, "MAX_UPLOAD_BYTES", 100)
    r = client.post("/api/backtest", data={"strategy": "buy_hold", "market": "crypto"},
                    files={"file": ("big.csv", csv_bytes(), "text/csv")})
    assert r.status_code == 413


# ---------- /api/compare ----------

def cmp(runs, **over):
    d = dict(market="crypto", runs=json.dumps(runs))
    d.update(over)
    return post("/api/compare", **d)


def test_compare_returns_aligned_series_for_each_run():
    runs = [
        {"strategy": "sma_cross", "params": {"fast": 10, "slow": 30}},
        {"strategy": "donchian_breakout", "params": {"entry": 20, "exit": 10}},
        {"strategy": "rsi_reversion", "params": {}},
    ]
    r = cmp(runs)
    assert r.status_code == 200, r.text
    j = r.json()
    assert [x["strategy"] for x in j["runs"]] == [x["strategy"] for x in runs]
    row = j["series"][0]
    assert {"t", "benchmark", "e0", "d0", "e1", "d1", "e2", "d2"} <= set(row)
    assert "e3" not in row
    assert len(j["series"]) == j["bars"] == 750


def test_compare_matches_single_run_results():
    spec = {"strategy": "sma_cross", "params": {"fast": 10, "slow": 30}}
    single = bt().json()
    multi = cmp([spec, {"strategy": "buy_hold", "params": {}}]).json()
    assert multi["runs"][0]["metrics"] == single["metrics"]
    assert [p["e0"] for p in multi["series"]] == pytest.approx([p["equity"] for p in single["series"]])
    # buy_hold run must equal the benchmark
    assert [p["e1"] for p in multi["series"]] == pytest.approx([p["benchmark"] for p in multi["series"]])


def test_compare_same_strategy_with_different_params():
    j = cmp([{"strategy": "sma_cross", "params": {"fast": 5, "slow": 20}},
             {"strategy": "sma_cross", "params": {"fast": 20, "slow": 100}}]).json()
    assert j["runs"][0]["metrics"] != j["runs"][1]["metrics"]


def test_compare_error_names_the_failing_run():
    r = cmp([{"strategy": "buy_hold", "params": {}},
             {"strategy": "sma_cross", "params": {"fast": 50, "slow": 30}}])
    assert r.status_code == 422
    assert r.json()["detail"].startswith("run 2 (sma_cross):")
    assert "fast must be < slow" in r.json()["detail"]


@pytest.mark.parametrize("runs", [
    "not json",
    "{}",
    "[]",
    json.dumps([{"strategy": "buy_hold"}] * (MAX_COMPARE_RUNS + 1)),
    json.dumps(["buy_hold"]),
    json.dumps([{"strategy": "nope"}]),
    json.dumps([{"strategy": "buy_hold", "params": [1]}]),
])
def test_compare_rejects_bad_runs(runs):
    assert post("/api/compare", market="crypto", runs=runs).status_code == 422


def test_compare_accepts_max_runs_and_missing_params_key():
    r = cmp([{"strategy": "buy_hold"}] * MAX_COMPARE_RUNS)
    assert r.status_code == 200
    assert len(r.json()["runs"]) == MAX_COMPARE_RUNS


def test_compare_uses_uploaded_csv():
    r = client.post("/api/compare",
                    data={"market": "us_stock", "runs": json.dumps([{"strategy": "buy_hold"}])},
                    files={"file": ("a.csv", csv_bytes(n=50), "text/csv")})
    assert r.status_code == 200
    assert r.json()["bars"] == 50
