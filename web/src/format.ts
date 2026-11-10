import type { Params } from "./types";

const PCT = new Set(["total_return", "cagr", "annual_volatility", "max_drawdown", "win_rate", "exposure"]);

export const METRIC_LABELS: Record<string, string> = {
  total_return: "Total return", cagr: "CAGR", annual_volatility: "Volatilitas (thn)", sharpe: "Sharpe",
  sortino: "Sortino", max_drawdown: "Max drawdown", calmar: "Calmar", n_trades: "Jumlah trade",
  win_rate: "Win rate", profit_factor: "Profit factor", exposure: "Exposure", turnover_per_year: "Turnover/thn",
};

export function fmtMetric(key: string, v: number | null | undefined): string {
  if (v === null || v === undefined) return "n/a";
  if (PCT.has(key)) return `${(v * 100).toFixed(2)}%`;
  return Number.isInteger(v) ? String(v) : v.toFixed(3);
}

export const pct = (v: number, digits = 2) => `${v > 0 ? "+" : ""}${(v * 100).toFixed(digits)}%`;
export const money = (v: number) => v.toLocaleString("en-US", { maximumFractionDigits: 0 });
export const price = (v: number) => v.toLocaleString("en-US", { maximumFractionDigits: 4 });

/** `sma_cross(fast=10)`: only params that differ from the strategy's defaults are shown. */
export function runLabel(strategy: string, params: Params, defaults: Params = {}): string {
  const kv = Object.entries(params).filter(([k, v]) => defaults[k] !== v).map(([k, v]) => `${k}=${v}`);
  return kv.length ? `${strategy}(${kv.join(", ")})` : strategy;
}

/** Labels for a set of runs, with " #n" appended only when two runs would otherwise be identical. */
export function uniqueLabels(
  runs: { strategy: string; params: Params }[], defaults: Record<string, Params> = {},
): string[] {
  const base = runs.map((r) => runLabel(r.strategy, r.params, defaults[r.strategy]));
  const seen = new Map<string, number>();
  return base.map((l) => {
    if (base.filter((x) => x === l).length === 1) return l;
    const n = (seen.get(l) ?? 0) + 1;
    seen.set(l, n);
    return `${l} #${n}`;
  });
}
