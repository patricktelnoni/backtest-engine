import datetime as dt
import json
import os

import numpy as np
import pytest

import screener
from btengine.screener import CHART_BARS
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
    assert client.get("/api/screener/chart", params={"setup": "x", "ticker": "y"}).status_code == 404


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
    assert sorted(calls) == [("AAAA.JK", "5y"), ("BBBB.JK", "5y"), ("FAIL.JK", "5y")]   # parallel: any order
    assert out["scanned"] == 2 and out["universe"] == 3
    assert [e["ticker"] for e in out["errors"]] == ["FAIL"] and "boom" in out["errors"][0]["error"]
    assert out["generated_at"].endswith("+00:00")
    assert set(out["setups"]) == {"bullish_divergence", "consolidated_ma", "stopping_volume"}
    assert out["setups"]["bullish_divergence"]["params"]["rsi_period"] == 14
    assert os.path.exists(os.path.join(screener.cache_dir(), "AAAA.csv"))
    assert open(os.path.join(screener.cache_dir(), "AAAA.csv")).readline().strip().endswith(",factor")
    # served verbatim, with the usual auth + no-store rules
    r = client.get("/api/screener")
    assert r.status_code == 200 and r.json() == out and r.headers["cache-control"] == "no-store"
    assert make_client().get("/api/screener").status_code == 401

    # second run: cache exists, so only the missing range is requested (unless it is Monday: full refresh)
    calls.clear()
    screener.run(fetch=True, fetcher=fake_fetch)
    expected = "5y" if dt.date.today().weekday() == 0 else screener._range_for((dt.date.today() - dt.date(2022, 2, 7)).days + 1)
    assert ("AAAA.JK", expected) in calls
    # offline re-run with other params re-reads the cache and rewrites the file
    out2 = screener.run({"consolidated_ma": {"max_hold": 7}}, fetch=False, fetcher=None)
    assert out2["setups"]["consolidated_ma"]["params"]["max_hold"] == 7
    assert out2["setups"]["bullish_divergence"]["params"]["max_hold"] == 20   # untouched default
    assert client.get("/api/screener").json() == out2


def test_charts_are_split_out_and_served_per_row(monkeypatch, tmp_path):
    uni = tmp_path / "universe.txt"
    uni.write_text("\n".join(f"T{i:02d}" for i in range(12)))
    monkeypatch.setenv("BT_SCREENER_UNIVERSE", str(uni))
    # fixed seeds (str hash is randomised per process) and every setup ever taken listed, so rows exist
    out = screener.run(fetch=True, fetcher=lambda sym, rng: _rows(600, seed=int(sym[1:3])), recent_bars=10_000)
    rows = [(name, r) for name, s in out["setups"].items() for r in s["results"]]
    assert rows and all("chart" not in r for _, r in rows)
    on_disk = json.load(open(screener.charts_path()))
    assert on_disk["generated_at"] == out["generated_at"]
    assert sum(len(v) for v in on_disk["setups"].values()) == len(rows)
    setup, row = rows[0]
    q = {"setup": setup, "ticker": row["ticker"], "generated_at": out["generated_at"]}
    r = client.get("/api/screener/chart", params=q)
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    c = r.json()
    assert len(c["t"]) == len(c["close"]) == CHART_BARS
    assert c["t"][-1] == row["last_date"] and "series" in c
    assert client.get("/api/screener/chart", params={**q, "ticker": "NOPE"}).status_code == 404
    # a job run since the page loaded its result: refuse rather than draw a chart that does not match the row
    assert client.get("/api/screener/chart", params={**q, "generated_at": "2000-01-01T00:00:00+00:00"}).status_code == 409
    assert make_client().get("/api/screener/chart", params=q).status_code == 401


def test_fetch_yahoo_keeps_todays_bar_only_after_the_close(monkeypatch):
    off = 7 * 3600                                          # WIB
    day = lambda d, hh, mm=0: int(dt.datetime(2026, 11, d, hh, mm, tzinfo=dt.timezone.utc).timestamp()) - off
    stamps = [day(12, 9), day(13, 9)]                       # Yahoo stamps daily bars at the session open
    body = {"chart": {"error": None, "result": [{
        "meta": {"gmtoffset": off, "currentTradingPeriod": {"regular": {"start": day(13, 9), "end": day(13, 16, 15)}}},
        "timestamp": stamps,
        "indicators": {"quote": [{"open": [100, 101], "high": [102, 103], "low": [99, 100], "close": [101, 102],
                                  "volume": [10, 20]}], "adjclose": [{"adjclose": [101, 102]}]},
    }]}}
    monkeypatch.setattr(screener, "_get_json", lambda url: body)
    dates = lambda now: [r[0] for r in screener.fetch_yahoo("X.JK", "5d", now=now)]
    assert screener.fetch_yahoo("X.JK", "5d", now=day(14, 10))[0][-1] == 1.0  # factor = adjclose / close
    assert dates(day(13, 14)) == ["2026-11-12"]                  # session open: today's bar still forming
    assert dates(day(13, 16, 20)) == ["2026-11-12"]              # closed, but within the grace period
    assert dates(day(13, 16, 30)) == ["2026-11-12", "2026-11-13"]  # the scheduled 16:30 run sees today's bar
    assert dates(day(14, 10)) == ["2026-11-12", "2026-11-13"]    # next day (weekend): yesterday is complete
    del body["chart"]["result"][0]["meta"]["currentTradingPeriod"]
    assert dates(day(13, 16, 30)) == ["2026-11-12"]              # no session info: stay conservative


def test_cache_without_factor_column_is_refetched_in_full(monkeypatch, tmp_path):
    monkeypatch.setenv("BT_SCREENER_DIR", str(tmp_path))
    os.makedirs(screener.cache_dir())
    path = os.path.join(screener.cache_dir(), "OLD.csv")
    with open(path, "w") as f:  # written by a version before the factor column
        f.write("date,open,high,low,close,volume\n2026-11-12,98,99,97,98,10\n")
    calls = []
    rows = [("2026-11-11", 98.0, 99.0, 97.0, 98.0, 10.0, 0.98), ("2026-11-12", 100.0, 101.0, 99.0, 100.0, 10.0, 1.0)]
    screener.refresh_cache(["OLD"], fetch=lambda sym, r: calls.append(r) or rows, spacing=0)
    assert calls == ["5y"]
    frames, errors = screener.load_frames(["OLD"])
    assert not errors and frames["OLD"]["factor"].tolist() == [0.98, 1.0]


def test_refresh_cache_downloads_in_parallel_and_keeps_errors_per_symbol(monkeypatch, tmp_path):
    import threading
    import time
    monkeypatch.setenv("BT_SCREENER_DIR", str(tmp_path))
    tickers = [f"S{i}" for i in range(8)]
    lock, active, peak = threading.Lock(), [0], [0]

    def slow_fetch(symbol, range_):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(0.2)
        with lock:
            active[0] -= 1
        if symbol == "S3.JK":
            raise RuntimeError("boom")
        return _rows(5, seed=1)

    t0 = time.perf_counter()
    errors = screener.refresh_cache(tickers, fetch=slow_fetch, workers=4, spacing=0)
    elapsed = time.perf_counter() - t0
    assert errors == {"S3": "boom"}
    assert peak[0] == 4 and elapsed < 0.2 * 8 / 2                  # 2 waves of 4, not 8 sequential calls
    assert sorted(os.listdir(screener.cache_dir())) == sorted(f"{t}.csv" for t in tickers if t != "S3")


def test_spacer_spaces_request_starts():
    import time
    sp = screener._Spacer(0.05)
    t0 = time.monotonic()
    for _ in range(4):
        sp.wait()
    assert time.monotonic() - t0 >= 0.15                           # first slot is immediate, then 3 gaps


def test_range_for_days_missing():
    assert screener._range_for(1) == "5d" and screener._range_for(20) == "1mo" and screener._range_for(900) == "5y"


def test_cli_parses_params(monkeypatch):
    seen = {}
    monkeypatch.setattr(screener, "run", lambda params, fetch, recent_bars, workers:
                        seen.update(params=params, fetch=fetch, rb=recent_bars, workers=workers))
    assert screener.main(["--no-fetch", "-p", "bullish_divergence.oversold=35", "-p", "consolidated_ma.use_stop=false",
                          "--recent-bars", "3", "--workers", "2"]) == 0
    assert seen == {"params": {"bullish_divergence": {"oversold": 35}, "consolidated_ma": {"use_stop": False}},
                    "fetch": False, "rb": 3, "workers": 2}
    assert screener.main(["--no-fetch"]) == 0 and seen["workers"] == screener.FETCH_WORKERS
    with pytest.raises(SystemExit):
        screener.main(["-p", "oversold=35"])


# ---------- manual runs (button in the UI) ----------

@pytest.fixture
def run_dir(monkeypatch, tmp_path):
    """Own state dir, fake runner (no real job is ever spawned by the tests), 10-minute cooldown."""
    monkeypatch.setenv("BT_SCREENER_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("BT_SCREENER_COOLDOWN_S", "600")
    monkeypatch.setenv("BT_SCREENER_RUNNER", "process")
    spawned = []
    monkeypatch.setattr(screener, "_spawn_job", lambda: spawned.append(1))
    return spawned


def _finish_job(monkeypatch, fail=False):
    """Run the CLI job as the runner would (scan replaced by a stub)."""
    def fake_run(params, fetch, recent_bars, workers):
        if fail:
            raise RuntimeError("yahoo down")
        return {"generated_at": "2026-11-16T09:30:00+00:00"}
    monkeypatch.setattr(screener, "run", fake_run)
    return screener.main([])


def test_manual_run_queue_status_and_cooldown(monkeypatch, run_dir):
    st = client.get("/api/screener/status").json()
    assert st["state"] == "idle" and st["cooldown_s"] == 0 and st["ok"] is None
    assert make_client().get("/api/screener/status").status_code == 401
    anon = make_client()
    login(anon)
    anon.headers.pop("X-CSRF-Token")
    assert anon.post("/api/screener/run").status_code == 403          # CSRF required like every POST

    r = client.post("/api/screener/run")
    assert r.status_code == 202 and r.json()["state"] == "queued" and run_dir == [1]
    assert json.load(open(screener.request_path()))["by"] == "tester"
    assert client.post("/api/screener/run").status_code == 409        # one at a time
    assert run_dir == [1]

    assert _finish_job(monkeypatch) == 0
    assert not os.path.exists(screener.request_path())               # consumed by the job
    st = client.get("/api/screener/status").json()
    assert st["state"] == "idle" and st["ok"] is True and st["generated_at"] == "2026-11-16T09:30:00+00:00"
    assert 590 < st["cooldown_s"] <= 600
    r = client.post("/api/screener/run")
    assert r.status_code == 429 and 590 < int(r.headers["retry-after"]) <= 600

    monkeypatch.setenv("BT_SCREENER_COOLDOWN_S", "0")
    assert client.post("/api/screener/run").status_code == 202 and run_dir == [1, 1]


def test_manual_run_systemd_runner_only_writes_the_request(monkeypatch, run_dir):
    monkeypatch.setenv("BT_SCREENER_RUNNER", "systemd")
    assert client.post("/api/screener/run").status_code == 202
    assert run_dir == [] and os.path.exists(screener.request_path())


def test_job_failure_and_dead_or_unclaimed_runs_are_reported(monkeypatch, run_dir):
    assert _finish_job(monkeypatch, fail=True) == 1
    st = screener.job_status()
    assert st["state"] == "idle" and st["ok"] is False and "yahoo down" in st["error"]

    now = screener._utcnow()
    old = screener._iso(now - dt.timedelta(seconds=screener.RUN_STALE_S + 60))
    screener._write_json(screener.status_path(), {"state": "running", "started_at": old})
    st = screener.job_status(now)
    assert st["state"] == "idle" and st["ok"] is False and "berhenti" in st["error"]

    # a request the trigger never picked up (path unit not enabled) stops counting as queued
    os.remove(screener.status_path())
    req = screener._iso(now - dt.timedelta(seconds=screener.START_TIMEOUT_S + 5))
    screener._write_json(screener.request_path(), {"requested_at": req})
    st = screener.job_status(now)
    assert st["state"] == "idle" and "tidak dijalankan" in st["error"]
    assert screener.job_status(now - dt.timedelta(seconds=screener.START_TIMEOUT_S))["state"] == "queued"


def test_job_lock_keeps_runs_from_overlapping(monkeypatch, run_dir):
    calls = []
    monkeypatch.setattr(screener, "run", lambda *a, **k: calls.append(1))
    with screener.job_lock() as mine:
        assert mine
        assert screener.main([]) == 0 and calls == []                 # second run backs off
        assert not os.path.exists(screener.status_path())
    assert screener.main([]) == 0 and calls == [1]
