"""IDX bullish-divergence screener: the scheduled job (data refresh + scan) and the endpoint serving its result.

Job (run from ./api, as the service user; systemd timer deploy/systemd/backtest-screener.timer does this on
weekdays after the IDX close):
    python screener.py                    # refresh cached OHLCV from Yahoo Finance, scan, write the result file
    python screener.py --no-fetch         # scan the cached data only (offline / re-run with other params)
    python screener.py -p oversold=35 -p max_hold=15

Files under BT_SCREENER_DIR (default: ./screener_data; production: /var/lib/backtest/screener):
    ohlcv/<TICKER>.csv                    per-symbol daily bars, extended incrementally (only missing days are
                                          downloaded, so a daily run is ~1 small request per symbol)
    idx_bullish_divergence.json           the latest result, served as-is by GET /api/screener

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

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from btengine.cli import _parse_param
from btengine.data import read_ohlcv_csv
from btengine.screener import screen_bullish_divergence

log = logging.getLogger("backtest.screener")
HERE = os.path.dirname(os.path.abspath(__file__))
RESULT_NAME = "idx_bullish_divergence.json"
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
    for d, o, h, l, c, v in rows:
        if not (l <= o <= h):
            o = prev_close if prev_close is not None else c
        out.append((d, o, max(h, o, c), min(l, o, c), c, v))
        prev_close = c
    return out


def fetch_yahoo(symbol: str, range_: str = FULL_RANGE) -> list[tuple]:
    """Completed daily bars (date, open, high, low, close, volume), split/dividend adjusted, oldest first."""
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}"
           f"?range={range_}&interval=1d&events=div%2Csplit")
    body = _get_json(url)["chart"]
    if body.get("error") or not body.get("result"):
        raise RuntimeError(f"no data for {symbol}: {body.get('error')}")
    res = body["result"][0]
    offset = res["meta"].get("gmtoffset", 0)  # exchange-local calendar date
    q = res["indicators"]["quote"][0]
    adj = (res["indicators"].get("adjclose") or [{}])[0].get("adjclose")
    today = dt.datetime.fromtimestamp(time.time() + offset, tz=dt.timezone.utc).date().isoformat()
    rows = []
    for i, ts in enumerate(res.get("timestamp", [])):
        o, h, l, c, v = (q[k][i] for k in ("open", "high", "low", "close", "volume"))
        if None in (o, h, l, c) or c <= 0:
            continue  # holiday / missing bar
        if adj and adj[i]:
            f = adj[i] / c
            o, h, l, c = o * f, h * f, l * f, adj[i]
        d = dt.datetime.fromtimestamp(ts + offset, tz=dt.timezone.utc).date().isoformat()
        if d >= today:
            continue  # today's bar is still forming
        rows.append((d, o, h, l, c, v or 0))
    return _repair_ohlc(rows)


def _range_for(days_missing: int) -> str:
    for limit, r in ((4, "5d"), (25, "1mo"), (80, "3mo"), (170, "6mo"), (350, "1y"), (700, "2y")):
        if days_missing <= limit:
            return r
    return FULL_RANGE


def _read_rows(path: str) -> dict[str, tuple]:
    if not os.path.exists(path):
        return {}
    with open(path, newline="") as f:
        return {r["date"]: (r["date"], float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]),
                            float(r["volume"])) for r in csv.DictReader(f)}


def _write_rows(path: str, rows: dict[str, tuple]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "open", "high", "low", "close", "volume"])
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
        have.update({r[0]: r for r in new})
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
            frames[t] = read_ohlcv_csv(path)
        except ValueError as e:
            errors[t] = f"bad cache file: {e}"
    return frames, errors


def run(params: dict | None = None, fetch: bool = True, recent_bars: int = 10, fetcher=fetch_yahoo) -> dict:
    """Refresh data (unless fetch=False), scan the universe and write the result file atomically."""
    t0 = time.perf_counter()
    tickers = load_universe()
    errors = refresh_cache(tickers, fetch=fetcher) if fetch else {}
    frames, load_errors = load_frames(tickers)
    result = screen_bullish_divergence(frames, params, recent_bars=recent_bars)
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
    log.info("scanned %d/%d symbols, %d setups, %d errors, %.1fs", result["scanned"], len(tickers),
             len(result["results"]), len(result["errors"]), result["duration_s"])
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
    ap = argparse.ArgumentParser(description="Refresh IDX data and screen for bullish divergence.")
    ap.add_argument("--no-fetch", action="store_true", help="scan cached data only")
    ap.add_argument("-p", "--param", action="append", default=[], type=_parse_param, metavar="KEY=VALUE",
                    help="bullish_divergence parameter, repeatable")
    ap.add_argument("--recent-bars", type=int, default=10, help="also list setups closed within N bars (default 10)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        run(dict(args.param), fetch=not args.no_fetch, recent_bars=args.recent_bars)
    except Exception as e:
        log.error("screener failed: %s", e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
