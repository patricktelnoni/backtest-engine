from __future__ import annotations

import numpy as np
import pandas as pd


def compute_metrics(net_returns: pd.Series, equity: pd.Series, position: pd.Series,
                    trade_returns: list[float], periods_per_year: int) -> dict:
    n = len(net_returns)
    years = n / periods_per_year if n else float("nan")
    # equity.iloc[0] is the post-first-bar value; reconstruct the starting equity
    start = equity.iloc[0] / (1 + net_returns.iloc[0]) if n else 1.0
    total = equity.iloc[-1] / start - 1 if n else 0.0
    cagr = (1 + total) ** (1 / years) - 1 if n and years > 0 and total > -1 else float("nan")

    std = net_returns.std(ddof=1) if n > 1 else float("nan")
    downside = net_returns[net_returns < 0].std(ddof=1) if (net_returns < 0).sum() > 1 else float("nan")
    ann = np.sqrt(periods_per_year)
    sharpe = net_returns.mean() / std * ann if std and std > 0 else float("nan")
    sortino = net_returns.mean() / downside * ann if downside and downside > 0 else float("nan")

    peak = pd.concat([pd.Series([start]), equity.reset_index(drop=True)]).cummax().iloc[1:].to_numpy()
    dd = equity.to_numpy() / peak - 1
    max_dd = float(dd.min()) if n else 0.0
    calmar = cagr / abs(max_dd) if max_dd < 0 and not np.isnan(cagr) else float("nan")

    wins = [t for t in trade_returns if t > 0]
    losses = [t for t in trade_returns if t < 0]
    return {
        "total_return": float(total),
        "cagr": float(cagr),
        "annual_volatility": float(std * ann) if n > 1 else float("nan"),
        "sharpe": float(sharpe),
        "sortino": float(sortino),
        "max_drawdown": max_dd,
        "calmar": float(calmar),
        "n_trades": len(trade_returns),
        "win_rate": len(wins) / len(trade_returns) if trade_returns else float("nan"),
        "profit_factor": sum(wins) / abs(sum(losses)) if losses else float("nan"),
        "exposure": float((position != 0).mean()) if n else 0.0,
        "turnover_per_year": float(position.diff().abs().fillna(position.abs()).sum() / years) if n and years > 0 else float("nan"),
    }
