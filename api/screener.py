"""IDX setup screener (bullish divergence, consolidated MA): the scheduled job (data refresh + scan) and the endpoint serving its result.

Job (run from ./api, as the service user; systemd timer deploy/systemd/backtest-screener.timer does this on
weekdays after the IDX close):
    python screener.py                    # refresh cached OHLCV from Yahoo Finance, scan, write the result file
    python screener.py --no-fetch         # scan the cached data only (offline / re-run with other params)
    python screener.py -p bullish_divergence.oversold=35 -p consolidated_ma.max_hold=30

Files under BT_SCREENER_DIR (default: ./screener_data; production: /var/lib/backtest/screener):
    ohlcv/<TICKER>.csv                    per-symbol daily bars, extended incrementally (only missing days are
                                          downloaded, so a daily run is ~1 small request per symbol); prices are
                                          dividend-adjusted, `factor` = adjusted / traded price of each bar (the
                                          screener reports traded prices, see btengine.screener)
    idx_screener.json                     the latest result (every setup in btengine.screener.SETUPS), served
                                          as-is by GET /api/screener

The API process never fetches or scans: it only reads the result file, so a request costs one stat() call.
Data source: Yahoo Finance chart endpoint (unofficial, no key; same rules as scripts/fetch_sample_data.py).
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import pandas as pd
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from btengine.cli import _parse_param
from btengine.data import read_ohlcv_csv
from btengine.screener import SETUPS, screen

log = logging.getLogger("backtest.screener")
HERE = os.path.dirname(os.path.abspath(__file__))
RESULT_NAME = "idx_screener.json"
UA = "Mozilla/5.0 (compatible; backtest-platform-screener)"
FULL_RANGE = "5y"


def state_dir() -> str:
    return os.environ.get("BT_SCREENER_DIR") or os.path.join(HERE, "screener_data")


def result_path() -> str:
    return os.path.join(state_dir(), RESULT_NAME)


def cache_dir() -> str:
    return os.path.join(state_dir(), "ohlcv")


def universe_path() -> str:
    return os.environ.get("BT_SCREENER_UNIVERSE") or os.path.join(HERE, "idx_universe.txt")


def load_universe(path: str | None = None) -> list[str]:
    seen, out = set(), []
    with open(path or universe_path()) as f:
        for line in f:
            sym = line.split("#", 1)[0].strip().upper()
            if sym and sym not in seen:
                seen.add(sym)
                out.append(sym)
    return out


# ---------- data download (Yahoo Finance) ----------

def _get_json(url: str, retries: int = 3):
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            last = e
            if e.code not in (429, 500, 502, 503, 504):
                break
        except (urllib.error.URLError, TimeoutError) as e:
            last = e
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"request failed ({last})")


def _repair_ohlc(rows: list[tuple]) -> list[tuple]:
    """Fix bars that violate low <= open,close <= high (seen in Yahoo data). Close is never modified."""
    out, prev_close = [], None
    for d, o, h, l, c, v, f in rows:
        if not (l <= o <= h):
            o = prev_close if prev_close is not None else c
        out.append((d, o, max(h, o, c), min(l, o, c), c, v, f))
        prev_close = c
    return out


SESSION_GRACE_S = 600  # wait this long after the regular session ends before trusting today's bar as final


def _session_closed(meta: dict, today: str, now: float) -> bool:
    """True when the exchange's regular session for `today` (exchange-local date) has ended. The job runs after
    the close, so without this the bar it is meant to scan would always be dropped as "still forming"."""
    end = ((meta.get("currentTradingPeriod") or {}).get("regular") or {}).get("end")
    if not end:
        return False
    offset = meta.get("gmtoffset", 0)
    end_day = dt.datetime.fromtimestamp(end + offset, tz=dt.timezone.utc).date().isoformat()
    return end_day == today and now >= end + SESSION_GRACE_S


def fetch_yahoo(symbol: str, range_: str = FULL_RANGE, now: float | None = None) -> list[tuple]:
    """Completed daily bars (date, open, high, low, close, volume, factor), split/dividend adjusted, oldest first;
    factor = adjclose / close (adjusted price / traded price), so traded price = adjusted price / factor.
    Today's bar is included once the regular session has closed (plus SESSION_GRACE_S), dropped before that."""
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}"
           f"?range={range_}&interval=1d&events=div%2Csplit")
    body = _get_json(url)["chart"]
    if body.get("error") or not body.get("result"):
        raise RuntimeError(f"no data for {symbol}: {body.get('error')}")
    res = body["result"][0]
    offset = res["meta"].get("gmtoffset", 0)  # exchange-local calendar date
    q = res["indicators"]["quote"][0]
    adj = (res["indicators"].get("adjclose") or [{}])[0].get("adjclose")
    now = time.time() if now is None else now
    today = dt.datetime.fromtimestamp(now + offset, tz=dt.timezone.utc).date().isoformat()
    keep_today = _session_closed(res["meta"], today, now)
    rows = []
    for i, ts in enumerate(res.get("timestamp", [])):
        o, h, l, c, v = (q[k][i] for k in ("open", "high", "low", "close", "volume"))
        if None in (o, h, l, c) or c <= 0:
            continue  # holiday / missing bar
        f = 1.0
        if adj and adj[i]:
            f = adj[i] / c
            o, h, l, c = o * f, h * f, l * f, adj[i]
        d = dt.datetime.fromtimestamp(ts + offset, tz=dt.timezone.utc).date().isoformat()
        if d > today or (d == today and not keep_today):
            continue  # today's bar is still forming
        rows.append((d, o, h, l, c, v or 0, f))
    return _repair_ohlc(rows)


def _range_for(days_missing: int) -> str:
    for limit, r in ((4, "5d"), (25, "1mo"), (80, "3mo"), (170, "6mo"), (350, "1y"), (700, "2y")):
        if days_missing <= limit:
            return r
    return FULL_RANGE


COLUMNS = ["date", "open", "high", "low", "close", "volume", "factor"]


def _read_rows(path: str) -> dict[str, tuple]:
    """Cached rows by date; {} when missing or written before the `factor` column existed (forces a full refresh,
    since the traded prices of old rows cannot be recovered without it)."""
    if not os.path.exists(path):
        return {}
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        if "factor" not in (reader.fieldnames or []):
            return {}
        return {r["date"]: (r["date"], *(float(r[k]) for k in COLUMNS[1:])) for r in reader}


def _write_rows(path: str, rows: dict[str, tuple]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(COLUMNS)
        w.writerows(rows[k] for k in sorted(rows))
    os.replace(tmp, path)


def refresh_cache(tickers: list[str], fetch=fetch_yahoo, pause: float = 0.3) -> dict[str, str]:
    """Extend each symbol's cached CSV with the bars it is missing. Returns {ticker: error} for failures."""
    os.makedirs(cache_dir(), exist_ok=True)
    errors = {}
    for i, t in enumerate(tickers):
        path = os.path.join(cache_dir(), f"{t}.csv")
        have = _read_rows(path)
        if have:
            last = dt.date.fromisoformat(max(have))
            # Adjusted prices shift after a split/dividend, so refetch everything now and then (weekly on Monday).
            days = (dt.date.today() - last).days + 1
            range_ = FULL_RANGE if dt.date.today().weekday() == 0 else _range_for(days)
        else:
            range_ = FULL_RANGE
        try:
            new = fetch(f"{t}.JK", range_)
            if not new:
                raise RuntimeError("no rows")
        except Exception as e:  # keep going so one bad symbol does not block the rest
            errors[t] = str(e)
            log.warning("fetch %s failed: %s", t, e)
            continue
        if range_ == FULL_RANGE:
            have = {}  # full refresh replaces the history (new adjustment factors)
        # Rows of an incremental fetch are adjusted to a newer reference than the cached ones after a fresh
        # dividend; each row still carries its own factor, so its traded price (adjusted / factor) stays right.
        have.update({r[0]: (r if len(r) == 7 else (*r, 1.0)) for r in new})
        _write_rows(path, have)
        log.info("%-6s %s +%d bars (%s), %d total", t, range_, len(new), new[-1][0], len(have))
        if pause and i + 1 < len(tickers):
            time.sleep(pause)
    return errors


def load_frames(tickers: list[str]) -> tuple[dict, dict[str, str]]:
    frames, errors = {}, {}
    for t in tickers:
        path = os.path.join(cache_dir(), f"{t}.csv")
        if not os.path.exists(path):
            errors[t] = "no cached data"
            continue
        try:
            df = read_ohlcv_csv(path)
            if "factor" in pd.read_csv(path, nrows=0).columns:  # absent in caches older than the column (--no-fetch)
                df["factor"] = pd.read_csv(path, usecols=["factor"])["factor"].to_numpy()
            frames[t] = df
        except ValueError as e:
            errors[t] = f"bad cache file: {e}"
    return frames, errors


def run(params: dict[str, dict] | None = None, fetch: bool = True, recent_bars: int = 10, fetcher=fetch_yahoo) -> dict:
    """Refresh data (unless fetch=False), scan the universe with every setup and write the result file atomically.
    `params` = {setup name: {param: value}} overrides; setups not mentioned run with their defaults."""
    t0 = time.perf_counter()
    tickers = load_universe()
    errors = refresh_cache(tickers, fetch=fetcher) if fetch else {}
    frames, load_errors = load_frames(tickers)
    setups = {name: (params or {}).get(name) for name in SETUPS}
    result = screen(frames, setups, recent_bars=recent_bars)
    for t, e in {**load_errors, **errors}.items():
        result["errors"].append({"ticker": t, "error": e})
    result["errors"].sort(key=lambda x: x["ticker"])
    result.update(universe=len(tickers), generated_at=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                  duration_s=round(time.perf_counter() - t0, 1))
    os.makedirs(state_dir(), exist_ok=True)
    tmp = result_path() + ".tmp"
    with open(tmp, "w") as f:
        json.dump(result, f, separators=(",", ":"))
    os.replace(tmp, result_path())
    found = ", ".join(f"{k}={len(v['results'])}" for k, v in result["setups"].items())
    log.info("scanned %d/%d symbols (%s), %d errors, %.1fs", result["scanned"], len(tickers), found,
             len(result["errors"]), result["duration_s"])
    return result


# ---------- endpoint ----------

router = APIRouter()
_cached: tuple[tuple, bytes] | None = None


@router.get("/api/screener")
async def screener():
    """The latest result file, verbatim; re-read only when it changed on disk."""
    global _cached
    path = result_path()
    try:
        st = os.stat(path)
    except FileNotFoundError:
        raise HTTPException(404, "screener has not run yet")
    key = (path, st.st_mtime_ns, st.st_size)
    if _cached is None or _cached[0] != key:
        with open(path, "rb") as f:
            _cached = (key, f.read())
    return Response(_cached[1], media_type="application/json")


# ---------- CLI ----------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Refresh IDX data and screen for every setup.")
    ap.add_argument("--no-fetch", action="store_true", help="scan cached data only")
    ap.add_argument("-p", "--param", action="append", default=[], type=_parse_param, metavar="SETUP.KEY=VALUE",
                    help=f"setup parameter, repeatable; setups: {', '.join(SETUPS)}")
    ap.add_argument("--recent-bars", type=int, default=10, help="also list setups closed within N bars (default 10)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    params: dict[str, dict] = {}
    for key, value in args.param:
        setup, _, name = key.partition(".")
        if setup not in SETUPS or not name:
            ap.error(f"-p expects SETUP.KEY=VALUE with SETUP one of {', '.join(SETUPS)}; got {key!r}")
        params.setdefault(setup, {})[name] = value
    try:
        run(params, fetch=not args.no_fetch, recent_bars=args.recent_bars)
    except Exception as e:
        log.error("screener failed: %s", e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
