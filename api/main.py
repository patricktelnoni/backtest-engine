"""Thin HTTP layer over btengine. Run: uvicorn main:app --reload --port 8001 (from ./api)

Resource model: one uvicorn process is enough. CPU-bound work (CSV parsing, the backtests, JSON encoding)
runs on a small in-process thread pool so the event loop keeps answering auth checks and static requests;
when the pool and its queue are full, extra requests get 503 + Retry-After instead of piling up in memory.
"""
from __future__ import annotations

import asyncio
import functools
import json
import math
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError

import auth
import screener
from btengine import REGISTRY, CostModel, Market, get_spec, run_backtest
from btengine.cli import _demo_data
from btengine.data import read_ohlcv_csv, validate_ohlcv

try:  # orjson is several times faster and allocates far less than the stdlib encoder; fall back if absent.
    import orjson

    def _dumps(obj) -> bytes:
        return orjson.dumps(obj)
except ImportError:  # pragma: no cover
    def _dumps(obj) -> bytes:
        return json.dumps(obj, separators=(",", ":")).encode()


# ---------- compute pool ----------
# The GIL means more threads would not add throughput, only memory held by in-flight DataFrames; 2 threads let
# one request's numpy work overlap another's Python loops. BT_BACKTEST_QUEUE bounds how many more may wait.
BACKTEST_THREADS = max(1, int(os.environ.get("BT_BACKTEST_THREADS", "2")))
BACKTEST_QUEUE = max(0, int(os.environ.get("BT_BACKTEST_QUEUE", "8")))
_pool = ThreadPoolExecutor(max_workers=BACKTEST_THREADS, thread_name_prefix="backtest")
_in_flight = 0


async def _compute(fn, *args):
    """Run blocking `fn` on the compute pool, or answer 503 when too much work is already pending."""
    global _in_flight
    if _in_flight >= BACKTEST_THREADS + BACKTEST_QUEUE:
        raise HTTPException(503, "server busy, try again in a moment", headers={"Retry-After": "2"})
    _in_flight += 1
    try:
        return await asyncio.get_running_loop().run_in_executor(_pool, fn, *args)
    finally:
        _in_flight -= 1


@asynccontextmanager
async def _lifespan(_app):
    auth._dummy_hash()  # compute once up front so the first unknown-user login is not measurably slower
    yield
    _pool.shutdown(wait=False, cancel_futures=True)


# Interactive docs/OpenAPI schema are off unless explicitly enabled (BT_ENABLE_DOCS=1, e.g. for local dev).
_docs = os.environ.get("BT_ENABLE_DOCS") == "1"
app = FastAPI(title="Backtest API", docs_url="/docs" if _docs else None, redoc_url=None,
              openapi_url="/openapi.json" if _docs else None, lifespan=_lifespan)


PUBLIC_PATHS = {"/api/auth/login"}


@app.middleware("http")
async def _api_guard(request: Request, call_next):
    """Reject unauthenticated /api/ calls *before* FastAPI reads the body (uploads can be 20 MB), and mark
    every API response no-store: results and CSRF tokens must not end up in a browser or proxy cache.
    The per-route dependency below is the authoritative check; it reuses the session verified here."""
    path = request.url.path
    if path.startswith("/api/") and path not in PUBLIC_PATHS:
        try:
            await run_in_threadpool(auth.current_session, request)
        except HTTPException as e:
            resp = JSONResponse({"detail": e.detail}, status_code=e.status_code)
            resp.headers["Cache-Control"] = "no-store"
            return resp
    resp = await call_next(request)
    if path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


# Every data endpoint requires a logged-in session (and a CSRF token for POST).
api = APIRouter(dependencies=[Depends(auth.current_session)])

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_COMPARE_RUNS = 6

# Series values are for charting; a few decimals keep the payload (and the gzip work nginx does on it) small.
EQUITY_DECIMALS, RATIO_DECIMALS = 4, 6


def _json(body: bytes) -> Response:
    """Pre-encoded JSON. Returning a Response skips FastAPI's jsonable_encoder, which walks every value."""
    return Response(body, media_type="application/json")


def _clean(v):
    """JSON has no NaN/Inf."""
    if isinstance(v, float) and not math.isfinite(v):
        return None
    return v


def _metrics(m: dict) -> dict:
    return {k: _clean(v) for k, v in m.items()}


def _422(e: Exception, prefix: str = "") -> HTTPException:
    if isinstance(e, ValidationError):
        msg = "; ".join(
            (f"{'.'.join(map(str, x['loc']))}: " if x["loc"] else "") + x["msg"].removeprefix("Value error, ")
            for x in e.errors()
        )
    elif isinstance(e, KeyError) and e.args:
        msg = str(e.args[0])
    else:
        msg = str(e)
    return HTTPException(422, prefix + msg)


class Common:
    """Data + market + cost inputs shared by every endpoint that runs a backtest."""

    def __init__(
        self,
        market: str = Form(...),
        initial_cash: float = Form(10_000.0),
        start: str | None = Form(None),
        end: str | None = Form(None),
        commission_bps: float | None = Form(None),
        slippage_bps: float | None = Form(None),
        borrow_bps_annual: float | None = Form(None),
        swap_bps_daily: float | None = Form(None),
        file: UploadFile | None = File(None),
    ):
        self.market, self.initial_cash, self.start, self.end, self.file = market, initial_cash, start, end, file
        self.cost_overrides = {
            k: v for k, v in dict(commission_bps=commission_bps, slippage_bps=slippage_bps,
                                  borrow_bps_annual=borrow_bps_annual, swap_bps_daily=swap_bps_daily).items()
            if v is not None
        }


def _check_common(c: Common) -> None:
    """Cheap input checks, done before a request takes a compute slot."""
    if c.market not in {m.value for m in Market}:
        raise HTTPException(422, f"unknown market {c.market!r}")
    if not c.initial_cash > 0:
        raise HTTPException(422, "initial_cash must be > 0")
    if c.file is not None:
        size = c.file.size
        if size is None:  # older multipart parsers: measure the spooled file instead
            f = c.file.file
            f.seek(0, os.SEEK_END)
            size, _ = f.tell(), f.seek(0)
        if size > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "file too large (max 20 MB)")


@functools.cache
def _demo() -> pd.DataFrame:
    """The synthetic series is deterministic: build and validate it once per process."""
    return validate_ohlcv(_demo_data())


def _load(c: Common) -> tuple[pd.DataFrame, CostModel | None]:
    """Blocking: parse, filter and validate the data and resolve costs. Runs on the compute pool."""
    try:
        # Parsing straight from the spooled upload (on disk above 1 MB) avoids holding a second copy in RAM.
        df = _demo() if c.file is None else read_ohlcv_csv(c.file.file)
        if c.start or c.end:
            lo = pd.Timestamp(c.start, tz="UTC") if c.start else df.index.min()
            hi = pd.Timestamp(c.end, tz="UTC") if c.end else df.index.max()
            df = df[(df.index >= lo) & (df.index <= hi)]
            if df.empty:
                raise ValueError("no data left after date filter")
        # Validate once here; every run_backtest below then recognises the canonical frame and skips the copy.
        df = validate_ohlcv(df)
        costs = get_spec(c.market).default_costs.model_copy(update=c.cost_overrides) if c.cost_overrides else None
    except (ValueError, KeyError, ValidationError) as e:
        raise _422(e)
    return df, costs


def _parse_params(raw, what: str = "params") -> dict:
    try:
        p = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        p = None
    if not isinstance(p, dict):
        raise HTTPException(422, f"{what} must be a JSON object")
    return p


def _check_strategy(name) -> str:
    if name not in REGISTRY:
        raise HTTPException(422, f"unknown strategy {name!r}")
    return name


def _drawdown(equity: pd.Series, initial_cash: float) -> pd.Series:
    return equity / equity.cummax().clip(lower=initial_cash) - 1


def _date(ts) -> str:
    return ts.strftime("%Y-%m-%d")


def _dates(index: pd.DatetimeIndex) -> list[str]:
    return index.strftime("%Y-%m-%d").tolist()


def _values(s: pd.Series, decimals: int) -> list[float]:
    """Rounded plain floats in one vectorized step (per-element Series access is ~100x slower)."""
    return np.round(s.to_numpy(dtype=float), decimals).tolist()


# The catalog never changes while the process runs: encode it once.
_STRATEGIES = _dumps([{"name": n, "params_schema": c.schema()} for n, c in sorted(REGISTRY.items())])
_MARKETS = _dumps([{"name": m.value, **get_spec(m).model_dump(exclude={"market"}, mode="json")} for m in Market])


@api.get("/api/strategies")
async def strategies():
    return _json(_STRATEGIES)


@api.get("/api/markets")
async def markets():
    return _json(_MARKETS)


def _run_single(common: Common, strategy: str, strategy_params: dict) -> bytes:
    df, costs = _load(common)
    try:
        r = run_backtest(df, strategy, common.market, initial_cash=common.initial_cash, costs=costs,
                         strategy_params=strategy_params)
        bh = run_backtest(df, "buy_hold", common.market, initial_cash=common.initial_cash, costs=costs)
    except (ValueError, KeyError, ValidationError) as e:
        raise _422(e)

    columns = (
        _dates(r.equity.index),
        _values(r.equity, EQUITY_DECIMALS),
        _values(bh.equity, EQUITY_DECIMALS),
        _values(_drawdown(r.equity, common.initial_cash), RATIO_DECIMALS),
        _values(r.position, RATIO_DECIMALS),
    )
    series = [{"t": t, "equity": e, "benchmark": b, "drawdown": d, "position": p} for t, e, b, d, p in zip(*columns)]
    trades = [{**t, "entry_date": _date(t["entry_date"]), "exit_date": _date(t["exit_date"])} for t in r.trades]
    return _dumps({
        "metrics": _metrics(r.metrics),
        "benchmark_metrics": _metrics(bh.metrics),
        "series": series,
        "trade_returns": r.trade_returns,
        "trades": trades,
        "bars": len(df),
    })


@api.post("/api/backtest")
async def backtest(common: Common = Depends(), strategy: str = Form(...), params: str = Form("{}")):
    _check_strategy(strategy)
    strategy_params = _parse_params(params)
    _check_common(common)
    return _json(await _compute(_run_single, common, strategy, strategy_params))


def _run_compare(common: Common, specs: list[dict]) -> bytes:
    df, costs = _load(common)
    results = []
    for i, s in enumerate(specs, 1):
        try:
            results.append(run_backtest(df, s["strategy"], common.market, initial_cash=common.initial_cash,
                                        costs=costs, strategy_params=s["params"]))
        except (ValueError, KeyError, ValidationError) as e:
            raise _422(e, prefix=f"run {i} ({s['strategy']}): ")
    try:
        bh = run_backtest(df, "buy_hold", common.market, initial_cash=common.initial_cash, costs=costs)
    except (ValueError, KeyError, ValidationError) as e:
        raise _422(e)

    columns = {"t": _dates(bh.equity.index), "benchmark": _values(bh.equity, EQUITY_DECIMALS)}
    for i, r in enumerate(results):
        columns[f"e{i}"] = _values(r.equity, EQUITY_DECIMALS)
        columns[f"d{i}"] = _values(_drawdown(r.equity, common.initial_cash), RATIO_DECIMALS)
    keys = list(columns)
    series = [dict(zip(keys, row)) for row in zip(*columns.values())]
    return _dumps({
        "runs": [
            {"strategy": s["strategy"], "params": s["params"], "metrics": _metrics(r.metrics)}
            for s, r in zip(specs, results)
        ],
        "benchmark_metrics": _metrics(bh.metrics),
        "series": series,
        "bars": len(df),
    })


@api.post("/api/compare")
async def compare(common: Common = Depends(), runs: str = Form(...)):
    """`runs` = JSON list of {"strategy": str, "params": {...}}; all share data/market/costs."""
    try:
        specs = json.loads(runs)
    except ValueError:
        specs = None
    if not isinstance(specs, list) or not 1 <= len(specs) <= MAX_COMPARE_RUNS:
        raise HTTPException(422, f"runs must be a JSON list of 1..{MAX_COMPARE_RUNS} items")
    for i, s in enumerate(specs, 1):
        if not isinstance(s, dict):
            raise HTTPException(422, f"run {i}: must be an object")
        _check_strategy(s.get("strategy"))
        s["params"] = _parse_params(s.get("params", {}), f"run {i}: params")
    _check_common(common)
    return _json(await _compute(_run_compare, common, specs))


api.include_router(screener.router)  # inherits the session requirement
app.include_router(auth.router)
app.include_router(api)
