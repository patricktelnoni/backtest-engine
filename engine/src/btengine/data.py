"""Uniform OHLCV schema: DatetimeIndex (UTC, sorted, unique) + open/high/low/close/volume."""
from __future__ import annotations

import pandas as pd

REQUIRED_COLUMNS = ["open", "high", "low", "close", "volume"]


def validate_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"missing columns: {missing}")
    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError("index must be a DatetimeIndex")
    if df.index.has_duplicates:
        raise ValueError("duplicate timestamps in index")
    out = df[REQUIRED_COLUMNS].sort_index()
    out.index = out.index.tz_localize("UTC") if out.index.tz is None else out.index.tz_convert("UTC")
    if out["close"].isna().any() or (out["close"] <= 0).any():
        raise ValueError("close contains NaN or non-positive values")
    return out
