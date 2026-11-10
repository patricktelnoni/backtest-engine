import type { BacktestResponse, CompareResponse, MarketInfo, Params, StrategyInfo } from "./types";

async function check<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (typeof body.detail === "string") msg = body.detail;
    } catch {
      /* non-JSON error body */
    }
    throw new Error(msg);
  }
  return res.json();
}

export const getStrategies = () => fetch("/api/strategies").then(check<StrategyInfo[]>);
export const getMarkets = () => fetch("/api/markets").then(check<MarketInfo[]>);

export interface RunSpec {
  strategy: string;
  params: Params;
}

export interface RunInput {
  market: string;
  initialCash: number;
  start?: string;
  end?: string;
  costs: Partial<Record<"commission_bps" | "slippage_bps" | "borrow_bps_annual" | "swap_bps_daily", number>>;
  file: File | null;
}

function baseForm(i: RunInput): FormData {
  const f = new FormData();
  f.set("market", i.market);
  f.set("initial_cash", String(i.initialCash));
  if (i.start) f.set("start", i.start);
  if (i.end) f.set("end", i.end);
  for (const [k, v] of Object.entries(i.costs)) if (v !== undefined) f.set(k, String(v));
  if (i.file) f.set("file", i.file);
  return f;
}

export function runBacktest(i: RunInput, run: RunSpec, signal?: AbortSignal): Promise<BacktestResponse> {
  const f = baseForm(i);
  f.set("strategy", run.strategy);
  f.set("params", JSON.stringify(run.params));
  return fetch("/api/backtest", { method: "POST", body: f, signal }).then(check<BacktestResponse>);
}

export function runCompare(i: RunInput, runs: RunSpec[], signal?: AbortSignal): Promise<CompareResponse> {
  const f = baseForm(i);
  f.set("runs", JSON.stringify(runs));
  return fetch("/api/compare", { method: "POST", body: f, signal }).then(check<CompareResponse>);
}
