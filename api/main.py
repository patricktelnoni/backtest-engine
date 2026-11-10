"""Thin HTTP layer over btengine. Run: uvicorn main:app --reload --port 8001 (from ./api)"""
from __future__ import annotations

import io
import json
import math

import pandas as pd
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from pydantic import ValidationError

from btengine import REGISTRY, Market, get_spec, run_backtest
from btengine.cli import _demo_data

app = FastAPI(title="Backtest API")

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_COMPARE_RUNS = 6


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


async def _prepare(c: Common):
    """Validate shared inputs and return (df, costs-or-None)."""
    if c.market not in {m.value for m in Market}:
        raise HTTPException(422, f"unknown market {c.market!r}")
    if not c.initial_cash > 0:
        raise HTTPException(422, "initial_cash must be > 0")
    try:
        if c.file is None:
            df = _demo_data()
        else:
            raw = await c.file.read(MAX_UPLOAD_BYTES + 1)
            if len(raw) > MAX_UPLOAD_BYTES:
                raise HTTPException(413, "file too large (max 20 MB)")
            df = pd.read_csv(io.BytesIO(raw))
            df.columns = [col.strip().lower() for col in df.columns]
            df[df.columns[0]] = pd.to_datetime(df[df.columns[0]], utc=True)
            df = df.set_index(df.columns[0])
        if c.start or c.end:
            lo = pd.Timestamp(c.start, tz="UTC") if c.start else df.index.min()
            hi = pd.Timestamp(c.end, tz="UTC") if c.end else df.index.max()
            df = df[(df.index >= lo) & (df.index <= hi)]
            if df.empty:
                raise ValueError("no data left after date filter")
        costs = get_spec(c.market).default_costs.model_copy(update=c.cost_overrides) if c.cost_overrides else None
    except HTTPException:
        raise
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


@app.get("/api/strategies")
def strategies():
    return [{"name": n, "params_schema": c.schema()} for n, c in sorted(REGISTRY.items())]


@app.get("/api/markets")
def markets():
    return [{"name": m.value, **get_spec(m).model_dump(exclude={"market"}, mode="json")} for m in Market]


@app.post("/api/backtest")
async def backtest(common: Common = Depends(), strategy: str = Form(...), params: str = Form("{}")):
    _check_strategy(strategy)
    strategy_params = _parse_params(params)
    df, costs = await _prepare(common)
    try:
        r = run_backtest(df, strategy, common.market, initial_cash=common.initial_cash, costs=costs,
                         strategy_params=strategy_params)
        bh = run_backtest(df, "buy_hold", common.market, initial_cash=common.initial_cash, costs=costs)
    except (ValueError, KeyError, ValidationError) as e:
        raise _422(e)

    dd = _drawdown(r.equity, common.initial_cash)
    series = [
        {"t": _date(t), "equity": float(e), "benchmark": float(b), "drawdown": float(d), "position": float(p)}
        for t, e, b, d, p in zip(r.equity.index, r.equity, bh.equity, dd, r.position)
    ]
    trades = [{**t, "entry_date": _date(t["entry_date"]), "exit_date": _date(t["exit_date"])} for t in r.trades]
    return {
        "metrics": _metrics(r.metrics),
        "benchmark_metrics": _metrics(bh.metrics),
        "series": series,
        "trade_returns": r.trade_returns,
        "trades": trades,
        "bars": len(df),
    }


@app.post("/api/compare")
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

    df, costs = await _prepare(common)
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

    dds = [_drawdown(r.equity, common.initial_cash) for r in results]
    series = []
    for j, t in enumerate(bh.equity.index):
        row = {"t": _date(t), "benchmark": float(bh.equity.iloc[j])}
        for i, (r, d) in enumerate(zip(results, dds)):
            row[f"e{i}"] = float(r.equity.iloc[j])
            row[f"d{i}"] = float(d.iloc[j])
        series.append(row)
    return {
        "runs": [
            {"strategy": s["strategy"], "params": s["params"], "metrics": _metrics(r.metrics)}
            for s, r in zip(specs, results)
        ],
        "benchmark_metrics": _metrics(bh.metrics),
        "series": series,
        "bars": len(df),
    }
