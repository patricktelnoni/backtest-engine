import datetime as dt
import json
import os

import numpy as np
import pytest

import screener
from conftest import login, make_client

client = make_client()


@pytest.fixture(scope="module", autouse=True)
def _logged_in(_user):
    assert login(client).status_code == 200


def _rows(n, seed, start=dt.date(2021, 1, 4)):
    rng = np.random.default_rng(seed)
    p, out = 1000.0, []
    for i in range(n):
        p *= 1 + rng.normal(0.0003, 0.02)
        out.append(((start + dt.timedelta(i)).isoformat(), p, p * 1.02, p * 0.98, p, 1e6))
    return out


def test_endpoint_404_before_first_run():
    assert not os.path.exists(screener.result_path())
    r = client.get("/api/screener")
    assert r.status_code == 404 and "not run" in r.json()["detail"]


def test_job_builds_cache_and_result_file(monkeypatch, tmp_path):
    uni = tmp_path / "universe.txt"
    uni.write_text("# test\nAAAA\nbbbb  # dup below\nBBBB\nFAIL\n")
    monkeypatch.setenv("BT_SCREENER_UNIVERSE", str(uni))
    assert screener.load_universe() == ["AAAA", "BBBB", "FAIL"]

    calls = []

    def fake_fetch(symbol, range_):
        calls.append((symbol, range_))
        if symbol == "FAIL.JK":
            raise RuntimeError("boom")
        return _rows(400, seed=hash(symbol) % 100)

    out = screener.run(fetch=True, fetcher=fake_fetch)
    assert [c[0] for c in calls] == ["AAAA.JK", "BBBB.JK", "FAIL.JK"] and calls[0][1] == "5y"
    assert out["scanned"] == 2 and out["universe"] == 3
    assert [e["ticker"] for e in out["errors"]] == ["FAIL"] and "boom" in out["errors"][0]["error"]
    assert out["generated_at"].endswith("+00:00")
    assert set(out["setups"]) == {"bullish_divergence", "consolidated_ma", "stopping_volume"}
    assert out["setups"]["bullish_divergence"]["params"]["rsi_period"] == 14
    assert os.path.exists(os.path.join(screener.cache_dir(), "AAAA.csv"))
    # served verbatim, with the usual auth + no-store rules
    r = client.get("/api/screener")
    assert r.status_code == 200 and r.json() == out and r.headers["cache-control"] == "no-store"
    assert make_client().get("/api/screener").status_code == 401

    # second run: cache exists, so only the missing range is requested (unless it is Monday: full refresh)
    calls.clear()
    screener.run(fetch=True, fetcher=fake_fetch)
    expected = "5y" if dt.date.today().weekday() == 0 else screener._range_for((dt.date.today() - dt.date(2022, 2, 7)).days + 1)
    assert calls[0] == ("AAAA.JK", expected)
    # offline re-run with other params re-reads the cache and rewrites the file
    out2 = screener.run({"consolidated_ma": {"max_hold": 7}}, fetch=False, fetcher=None)
    assert out2["setups"]["consolidated_ma"]["params"]["max_hold"] == 7
    assert out2["setups"]["bullish_divergence"]["params"]["max_hold"] == 20   # untouched default
    assert client.get("/api/screener").json() == out2


def test_range_for_days_missing():
    assert screener._range_for(1) == "5d" and screener._range_for(20) == "1mo" and screener._range_for(900) == "5y"


def test_cli_parses_params(monkeypatch):
    seen = {}
    monkeypatch.setattr(screener, "run", lambda params, fetch, recent_bars: seen.update(params=params, fetch=fetch, rb=recent_bars))
    assert screener.main(["--no-fetch", "-p", "bullish_divergence.oversold=35", "-p", "consolidated_ma.use_stop=false",
                          "--recent-bars", "3"]) == 0
    assert seen == {"params": {"bullish_divergence": {"oversold": 35}, "consolidated_ma": {"use_stop": False}},
                    "fetch": False, "rb": 3}
    with pytest.raises(SystemExit):
        screener.main(["-p", "oversold=35"])
