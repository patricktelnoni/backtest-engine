"""Uniform OHLCV schema: DatetimeIndex (UTC, sorted, unique) + open/high/low/close/volume."""
from __future__ import annotations

import pandas as pd

REQUIRED_COLUMNS = ["open", "high", "low", "close", "volume"]


def _is_canonical(df: pd.DataFrame) -> bool:
    """True when `df` already has the exact shape validate_ohlcv produces (its checks are cached on the index)."""
    idx = df.index
    return (list(df.columns) == REQUIRED_COLUMNS and isinstance(idx, pd.DatetimeIndex)
            and str(idx.tz) == "UTC" and idx.is_monotonic_increasing)


def validate_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """Check the schema and return a canonical frame. A frame that is already canonical (e.g. one validated
    by an earlier run on the same data) is returned as is, without copying; callers must not mutate it."""
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"missing columns: {missing}")
    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError("index must be a DatetimeIndex")
    if df.index.has_duplicates:
        raise ValueError("duplicate timestamps in index")
    if _is_canonical(df):
        out = df
    else:
        out = df[REQUIRED_COLUMNS].sort_index()
        out.index = out.index.tz_localize("UTC") if out.index.tz is None else out.index.tz_convert("UTC")
    if out["close"].isna().any() or (out["close"] <= 0).any():
        raise ValueError("close contains NaN or non-positive values")
    return out


def _norm(name) -> str:
    return str(name).strip().lower()


def read_ohlcv_csv(src, date_col: str | None = None) -> pd.DataFrame:
    """Read a CSV (path or seekable file object) into a frame indexed by its UTC date column.

    Only the date column and the five OHLCV columns are parsed: uploads often carry extra columns
    (adjusted close, indicators, ...) that would otherwise be read into memory and then dropped.
    Column names are matched case-insensitively and without surrounding whitespace."""
    if hasattr(src, "seek"):
        src.seek(0)
    cols = [_norm(c) for c in pd.read_csv(src, nrows=0).columns]
    if hasattr(src, "seek"):
        src.seek(0)
    col = _norm(date_col) if date_col else cols[0]
    if col not in cols:
        raise ValueError(f"date column {col!r} not found; columns: {cols}")
    wanted = {col, *REQUIRED_COLUMNS}
    df = pd.read_csv(src, usecols=lambda c: _norm(c) in wanted)
    df.columns = [_norm(c) for c in df.columns]
    df[col] = pd.to_datetime(df[col], utc=True)
    return df.set_index(col)
