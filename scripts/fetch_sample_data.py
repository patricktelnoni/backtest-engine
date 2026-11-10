#!/usr/bin/env python3
"""Download daily OHLCV sample data into ./data as CSV (date,open,high,low,close,volume).

  python scripts/fetch_sample_data.py              # everything below
  python scripts/fetch_sample_data.py SPY BTCUSDT  # only these symbols

Sources (public, unauthenticated, no API key):
  * Yahoo Finance chart endpoint -- unofficial; format/availability may change, check its terms of use.
    Stock OHLC are adjusted for splits/dividends (open/high/low scaled by adjclose/close).
    The current (still-forming) day is dropped. Bars whose open falls outside [low, high] (seen in Yahoo FX data)
    get open := previous close; high/low are then widened to contain open and close. Repairs are counted on output.
  * Binance public market-data API (data-api.binance.vision), spot klines. The still-open candle is dropped.

Standard library only.
"""
from __future__ import annotations

import csv
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent.parent / "data"
UA = "Mozilla/5.0 (compatible; backtest-platform-sample-data)"
YEARS = 5

# symbol -> (source, market, filename)
DATASETS = {
    "SPY": ("yahoo", "us_stock", "us_stock_SPY.csv"),
    "AAPL": ("yahoo", "us_stock", "us_stock_AAPL.csv"),
    "BBCA.JK": ("yahoo", "idx_stock", "idx_stock_BBCA.csv"),
    "TLKM.JK": ("yahoo", "idx_stock", "idx_stock_TLKM.csv"),
    "EURUSD=X": ("yahoo", "forex", "forex_EURUSD.csv"),
    "USDIDR=X": ("yahoo", "forex", "forex_USDIDR.csv"),
    "BTCUSDT": ("binance", "crypto", "crypto_BTCUSDT.csv"),
    "ETHUSDT": ("binance", "crypto", "crypto_ETHUSDT.csv"),
}


def get_json(url: str, retries: int = 3):
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
    raise RuntimeError(f"request failed: {url} ({last})")


def repair_ohlc(rows: list[tuple]) -> tuple[list[tuple], int]:
    """Fix bars that violate low <= open,close <= high. Close is never modified."""
    out, fixed, prev_close = [], 0, None
    for d, o, h, l, c, v in rows:
        o2, h2, l2 = o, h, l
        if not (l <= o <= h):
            o2 = prev_close if prev_close is not None else c
        h2, l2 = max(h, o2, c), min(l, o2, c)
        if (o2, h2, l2) != (o, h, l):
            fixed += 1
        out.append((d, o2, h2, l2, c, v))
        prev_close = c
    return out, fixed


def fetch_yahoo(symbol: str) -> tuple[list[tuple], int]:
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}"
           f"?range={YEARS}y&interval=1d&events=div%2Csplit")
    body = get_json(url)["chart"]
    if body.get("error") or not body.get("result"):
        raise RuntimeError(f"yahoo returned no data for {symbol}: {body.get('error')}")
    res = body["result"][0]
    offset = res["meta"].get("gmtoffset", 0)  # exchange-local calendar date
    q = res["indicators"]["quote"][0]
    adj = (res["indicators"].get("adjclose") or [{}])[0].get("adjclose")
    today = datetime.fromtimestamp(time.time() + offset, tz=timezone.utc).date().isoformat()
    rows = []
    for i, ts in enumerate(res.get("timestamp", [])):
        o, h, l, c, v = (q[k][i] for k in ("open", "high", "low", "close", "volume"))
        if None in (o, h, l, c) or c <= 0:
            continue  # holiday / missing bar
        if adj and adj[i]:
            f = adj[i] / c
            o, h, l, c = o * f, h * f, l * f, adj[i]
        d = datetime.fromtimestamp(ts + offset, tz=timezone.utc).date().isoformat()
        if d >= today:
            continue  # today's bar is still forming (partial volume / range)
        rows.append((d, o, h, l, c, v or 0))
    return repair_ohlc(rows)


def fetch_binance(symbol: str) -> tuple[list[tuple], int]:
    now_ms = int(time.time() * 1000)
    start = now_ms - YEARS * 365 * 86_400_000
    rows, seen = [], set()
    while start < now_ms:
        url = ("https://data-api.binance.vision/api/v3/klines?"
               + urllib.parse.urlencode({"symbol": symbol, "interval": "1d", "startTime": start, "limit": 1000}))
        batch = get_json(url)
        if not batch:
            break
        for k in batch:
            open_ms, close_ms = k[0], k[6]
            if close_ms > now_ms or open_ms in seen:  # drop still-open candle / duplicates
                continue
            seen.add(open_ms)
            d = datetime.fromtimestamp(open_ms / 1000, tz=timezone.utc).date().isoformat()
            rows.append((d, float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])))
        start = batch[-1][0] + 1
        if len(batch) < 1000:
            break
    return rows, 0


def write_csv(path: Path, rows: list[tuple]) -> None:
    rows = sorted({r[0]: r for r in rows}.values())  # unique, sorted by date
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "open", "high", "low", "close", "volume"])
        w.writerows(rows)


def main(argv: list[str]) -> int:
    wanted = argv or list(DATASETS)
    unknown = [s for s in wanted if s not in DATASETS]
    if unknown:
        print(f"unknown symbol(s): {unknown}; available: {list(DATASETS)}", file=sys.stderr)
        return 2
    failed = 0
    for sym in wanted:
        source, market, fname = DATASETS[sym]
        try:
            rows, repaired = fetch_yahoo(sym) if source == "yahoo" else fetch_binance(sym)
            if not rows:
                raise RuntimeError("no rows")
            write_csv(OUT_DIR / fname, rows)
            print(f"ok   {sym:10s} {market:10s} {len(rows):5d} rows  {rows[0][0]} -> {rows[-1][0]}  data/{fname}"
                  + (f"  (repaired {repaired} bars)" if repaired else ""))
        except Exception as e:  # keep going so one bad symbol doesn't block the rest
            failed += 1
            print(f"FAIL {sym:10s} {market:10s} {e}", file=sys.stderr)
        time.sleep(0.5)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
