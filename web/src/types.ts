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
