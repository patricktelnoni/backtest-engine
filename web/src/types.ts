export interface ParamProp {
  type?: "integer" | "number" | "boolean" | "string";
  default?: number | boolean | string;
  minimum?: number;
  maximum?: number;
  title?: string;
}
export interface StrategyInfo {
  name: string;
  params_schema: { properties?: Record<string, ParamProp> };
}
export interface CostModel {
  commission_bps: number;
  slippage_bps: number;
  borrow_bps_annual: number;
  swap_bps_daily: number;
}
export interface MarketInfo {
  name: string;
  periods_per_year: number;
  allow_short: boolean;
  default_costs: CostModel;
}
export interface Point {
  t: string;
  equity: number;
  benchmark: number;
  drawdown: number;
  position: number;
}
export type Metrics = Record<string, number | null>;
export interface Trade {
  side: "long" | "short";
  size: number;
  entry_date: string;
  entry_price: number;
  exit_date: string;
  exit_price: number;
  bars: number;
  return: number;
  open: boolean;
}
export interface BacktestResponse {
  metrics: Metrics;
  benchmark_metrics: Metrics;
  series: Point[];
  trade_returns: number[];
  trades: Trade[];
  bars: number;
}
export type Params = Record<string, number | boolean | string>;
export interface CompareRun {
  strategy: string;
  params: Params;
  metrics: Metrics;
}
/** Series rows carry `t`, `benchmark`, and per run i: `e{i}` (equity) and `d{i}` (drawdown). */
export type CompareRow = { t: string; benchmark: number } & Record<string, number | string>;
export interface CompareResponse {
  runs: CompareRun[];
  benchmark_metrics: Metrics;
  series: CompareRow[];
  bars: number;
}

export interface TradePlan {
  entry_low: number;
  entry_high: number;
  entry: number;
  stop_loss: number;
  risk_pct: number;
  tp1: number;
  tp2: number;
  tp1_pct: number;
  tp2_pct: number;
  rr_targets: number[];
  rr_at_last: number;
}
export interface ChartData {
  t: string[];
  open: number[];
  close: number[];
  high: number[];
  low: number[];
  /** Indicator series aligned with `t`: ema10..ema200 for consolidated_ma, rsi for bullish_divergence. */
  series: Record<string, (number | null)[]>;
}
export interface ScreenerRow {
  ticker: string;
  status: "active" | "exited";
  signal_date: string;
  signal_price: number;
  bars_since_signal: number;
  last_date: string;
  last_close: number;
  return_since_signal: number;
  stop: number;
  stop_distance: number;
  exit_date: string | null;
  exit_reason: string | null;
  /** Setup-specific facts (dates, prices, ratios), keys differ per setup. */
  detail: Record<string, number | string | null>;
  /** Entry area, stop loss and 2R/3R targets; null for closed setups or when price is already under the stop. */
  plan: TradePlan | null;
  chart: ChartData;
  history: {
    bars: number; n_trades: number; win_rate: number | null; profit_factor: number | null;
    avg_trade_return: number | null; total_return: number | null;
  };
}
export interface ScreenerSetup {
  params: Params;
  results: ScreenerRow[];
}
export interface ScreenerResponse {
  market: string;
  recent_bars: number;
  scanned: number;
  universe: number;
  generated_at: string;
  duration_s: number;
  errors: { ticker: string; error: string }[];
  setups: Record<string, ScreenerSetup>;
}
