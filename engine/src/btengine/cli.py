"""Command-line interface: python -m btengine --csv data.csv --strategy sma_cross --market crypto"""
from __future__ import annotations

import argparse
import json
import math
import sys

import numpy as np
import pandas as pd
from pydantic import ValidationError

from .backtest import run_backtest
from .data import read_ohlcv_csv
from .markets import Market, get_spec
from .strategies import REGISTRY


def _parse_param(item: str) -> tuple[str, object]:
    if "=" not in item:
        raise argparse.ArgumentTypeError(f"expected key=value, got {item!r}")
    key, raw = item.split("=", 1)
    try:
        return key, json.loads(raw)  # 20 -> int, 0.5 -> float, true -> bool
    except json.JSONDecodeError:
        return key, raw


def _load_csv(path: str, date_col: str | None) -> pd.DataFrame:
    return read_ohlcv_csv(path, date_col)


def _demo_data(n: int = 750, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2021-01-04", periods=n, freq="D", tz="UTC")
    close = pd.Series(100 * np.exp(np.cumsum(rng.normal(0.0005, 0.015, n))), index=idx)
    return pd.DataFrame({"open": close, "high": close * 1.01, "low": close * 0.99,
                         "close": close, "volume": 1000.0})


def _fmt(key: str, v) -> str:
    if isinstance(v, float):
        if math.isnan(v):
            return "n/a"
        pct = key in {"total_return", "cagr", "annual_volatility", "max_drawdown", "win_rate", "exposure"}
        return f"{v * 100:.2f}%" if pct else f"{v:.3f}"
    return str(v)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="btengine", description="Run a daily-bar backtest.")
    p.add_argument("--list", action="store_true", help="list strategies, markets and parameters, then exit")
    src = p.add_mutually_exclusive_group()
    src.add_argument("--csv", help="OHLCV CSV (first column = date unless --date-col)")
    src.add_argument("--demo", action="store_true", help="use synthetic random-walk data")
    p.add_argument("--date-col", help="name of the date column (default: first column)")
    p.add_argument("-s", "--strategy", choices=sorted(REGISTRY), help="strategy name")
    p.add_argument("-m", "--market", choices=[m.value for m in Market], help="market (sets costs/annualization)")
    p.add_argument("-p", "--param", action="append", default=[], type=_parse_param, metavar="KEY=VALUE",
                   help="strategy parameter, repeatable (e.g. -p fast=10 -p slow=30)")
    p.add_argument("--cash", type=float, default=10_000.0, help="initial cash (default 10000)")
    p.add_argument("--start", help="only use bars on/after this date (YYYY-MM-DD)")
    p.add_argument("--end", help="only use bars on/before this date (YYYY-MM-DD)")
    for name in ("commission-bps", "slippage-bps", "borrow-bps-annual", "swap-bps-daily"):
        p.add_argument(f"--{name}", type=float, help=f"override market default {name.replace('-', '_')}")
    p.add_argument("--json", action="store_true", help="print metrics as JSON")
    p.add_argument("--equity-out", metavar="FILE", help="write equity curve + position to CSV")
    return p


def _print_catalog() -> None:
    print("Strategies:")
    for name, cls in sorted(REGISTRY.items()):
        props = cls.schema().get("properties", {})
        desc = ", ".join(f"{k}={v.get('default')}" for k, v in props.items()) or "(no params)"
        print(f"  {name:20s} {desc}")
    print("\nMarkets:")
    for m in Market:
        s = get_spec(m)
        c = s.default_costs
        print(f"  {m.value:10s} {s.periods_per_year} bars/yr, short={'yes' if s.allow_short else 'no'}, "
              f"commission={c.commission_bps}bps slippage={c.slippage_bps}bps")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.list:
        _print_catalog()
        return 0
    if not args.strategy or not args.market:
        parser.error("--strategy and --market are required (or use --list)")
    if not args.csv and not args.demo:
        parser.error("provide --csv FILE or --demo")

    try:
        df = _demo_data() if args.demo else _load_csv(args.csv, args.date_col)
        if args.start or args.end:
            idx = df.index
            lo = pd.Timestamp(args.start, tz="UTC") if args.start else idx.min()
            hi = pd.Timestamp(args.end, tz="UTC") if args.end else idx.max()
            df = df[(idx >= lo) & (idx <= hi)]
            if df.empty:
                raise ValueError("no data left after --start/--end filter")

        overrides = {k: getattr(args, k) for k in
                     ("commission_bps", "slippage_bps", "borrow_bps_annual", "swap_bps_daily")
                     if getattr(args, k) is not None}
        costs = get_spec(args.market).default_costs.model_copy(update=overrides) if overrides else None

        result = run_backtest(df, args.strategy, args.market, initial_cash=args.cash,
                              costs=costs, strategy_params=dict(args.param))
    except (ValidationError, KeyError, ValueError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    if args.equity_out:
        pd.DataFrame({"equity": result.equity, "position": result.position}).to_csv(args.equity_out)

    if args.json:
        print(json.dumps(result.metrics, indent=2, default=lambda o: None if o != o else o))
    else:
        print(f"{args.strategy} on {args.market} | {df.index[0].date()} -> {df.index[-1].date()} "
              f"({len(df)} bars) | final equity {result.equity.iloc[-1]:,.2f}")
        for k, v in result.metrics.items():
            print(f"  {k:18s} {_fmt(k, v)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
