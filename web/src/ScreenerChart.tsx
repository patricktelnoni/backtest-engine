import {
  Bar, CartesianGrid, ComposedChart, Line, ReferenceArea, ReferenceDot, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { AXIS_TICK, axisFormatter, ChartLegend, ChartTooltip, CURSOR, GRID, seriesColor, X_AXIS, Y_AXIS } from "./charts";
import { price } from "./format";
import type { ScreenerRow } from "./types";

const EMA_LABEL: Record<string, string> = { ema10: "EMA 10", ema20: "EMA 20", ema50: "EMA 50", ema100: "EMA 100", ema200: "EMA 200" };
const PLAN = { entry: "var(--accent)", stop: "var(--bad)", tp: "var(--good)" };
const UP = "var(--good)", DOWN = "var(--bad)";

type Candle = { t: string; open: number; high: number; low: number; close: number; range: [number, number] } & Record<string, unknown>;

/** Recharts has no candlestick: draw one inside a Bar whose value is [low, high]. The bar's pixel box maps
 *  high -> y and low -> y + height, so open/close are placed by linear interpolation; no access to scales needed. */
function CandleShape(props: { x?: number; y?: number; width?: number; height?: number; payload?: Candle }) {
  const { x = 0, y = 0, width = 0, height = 0, payload } = props;
  if (!payload || !Number.isFinite(x) || !Number.isFinite(y)) return <g />;
  const { open, close, high, low } = payload;
  const up = close >= open;
  const span = high - low || 1;
  const px = (v: number) => y + ((high - v) / span) * height;
  const top = px(Math.max(open, close)), bottom = px(Math.min(open, close));
  const bodyH = Math.max(1, bottom - top);
  const bodyW = Math.max(2, width * 0.7);
  const cx = x + width / 2;
  const color = up ? UP : DOWN;
  return (
    <g>
      <line x1={cx} x2={cx} y1={y} y2={y + height} stroke={color} strokeWidth={1} />
      <rect x={cx - bodyW / 2} y={top} width={bodyW} height={bodyH} fill={up ? "var(--surface)" : color} stroke={color} strokeWidth={1.2} />
    </g>
  );
}

function CandleTooltip({ active, payload, label, emaKeys }: {
  active?: boolean; payload?: { payload?: Candle }[]; label?: string; emaKeys: string[];
}) {
  const c = payload?.[0]?.payload;
  if (!active || !c) return null;
  const chg = c.close / c.open - 1;
  return (
    <div className="tip">
      <div className="tip-date">{label}</div>
      {(["open", "high", "low", "close"] as const).map((k) => (
        <div key={k} className="tip-row"><span className="tip-name">{k[0].toUpperCase()}</span><span className="tip-val">{price(c[k])}</span></div>
      ))}
      <div className="tip-row"><span className="tip-name">chg</span><span className={`tip-val ${chg >= 0 ? "pos" : "neg"}`}>{(chg * 100).toFixed(2)}%</span></div>
      {emaKeys.map((k, i) => c[k] != null && (
        <div key={k} className="tip-row">
          <span className="swatch" style={{ background: seriesColor(i + 1) }} aria-hidden="true" />
          <span className="tip-name">{EMA_LABEL[k] ?? k}</span><span className="tip-val">{price(Number(c[k]))}</span>
        </div>
      ))}
    </div>
  );
}

/** Candlesticks with the setup's overlays: indicator lines, signal bar, divergence lows, entry area, SL and TPs. */
export default function ScreenerChart({ r }: { r: ScreenerRow }) {
  const c = r.chart;
  if (!c) return null;
  const emaKeys = Object.keys(c.series ?? {}).filter((k) => k.startsWith("ema"));
  const rsi = c.series.rsi;
  const rows: Candle[] = c.t.map((t, i) => {
    const row: Candle = { t, open: c.open[i], high: c.high[i], low: c.low[i], close: c.close[i], range: [c.low[i], c.high[i]] };
    for (const k of emaKeys) row[k] = c.series[k][i];
    if (rsi) row.rsi = rsi[i];
    return row;
  });
  const p = r.plan;
  const values = rows.flatMap((x) => [x.low, x.high, ...emaKeys.map((k) => Number(x[k] ?? NaN))]);
  if (p) values.push(p.stop_loss, p.tp2);
  const finite = values.filter((v) => Number.isFinite(v));
  const lo = Math.min(...finite), hi = Math.max(...finite), pad = (hi - lo) * 0.04;
  const fmtAxis = axisFormatter(hi);
  const inWindow = (d: string) => c.t.includes(d);
  const d = r.detail;
  const legend = [
    { label: "Candle (naik kosong, turun isi)", color: "var(--text-2)" },
    ...emaKeys.map((k, i) => ({ label: EMA_LABEL[k] ?? k, color: seriesColor(i + 1) })),
    ...(p ? [{ label: "Area entry", color: PLAN.entry }, { label: "Stop loss", color: PLAN.stop, dashed: true }, { label: "TP 2R / 3R", color: PLAN.tp, dashed: true }] : []),
  ];

  return (
    <div className="screener-chart">
      <ChartLegend items={legend} />
      <ResponsiveContainer width="100%" height={320}>
        <ComposedChart data={rows} margin={{ top: 8, right: 12, left: 0, bottom: 0 }} barCategoryGap="20%">
          <CartesianGrid {...GRID} />
          <XAxis dataKey="t" {...X_AXIS} />
          <YAxis {...Y_AXIS} width={64} domain={[lo - pad, hi + pad]} tickFormatter={fmtAxis} />
          <Tooltip content={<CandleTooltip emaKeys={emaKeys} />} cursor={CURSOR} />
          {p && <ReferenceArea y1={p.entry_low} y2={p.entry_high} fill={PLAN.entry} fillOpacity={0.14} stroke={PLAN.entry} strokeOpacity={0.5} />}
          {p && <ReferenceLine y={p.stop_loss} stroke={PLAN.stop} strokeDasharray="5 3" label={{ value: `SL ${price(p.stop_loss)}`, position: "insideBottomLeft", fill: PLAN.stop, fontSize: 11 }} />}
          {p && <ReferenceLine y={p.tp1} stroke={PLAN.tp} strokeDasharray="5 3" label={{ value: `TP1 ${price(p.tp1)}`, position: "insideTopLeft", fill: PLAN.tp, fontSize: 11 }} />}
          {p && <ReferenceLine y={p.tp2} stroke={PLAN.tp} strokeDasharray="5 3" label={{ value: `TP2 ${price(p.tp2)}`, position: "insideTopLeft", fill: PLAN.tp, fontSize: 11 }} />}
          {inWindow(r.signal_date) && <ReferenceLine x={r.signal_date} stroke="var(--text-2)" strokeDasharray="3 3" label={{ value: "sinyal", position: "top", fill: "var(--text-2)", fontSize: 11 }} />}
          {r.exit_date && inWindow(r.exit_date) && <ReferenceLine x={r.exit_date} stroke="var(--muted)" strokeDasharray="3 3" label={{ value: "keluar", position: "top", fill: "var(--muted)", fontSize: 11 }} />}
          <Bar dataKey="range" name="OHLC" shape={<CandleShape />} isAnimationActive={false} />
          {emaKeys.map((k, i) => (
            <Line key={k} dataKey={k} name={EMA_LABEL[k] ?? k} stroke={seriesColor(i + 1)} strokeWidth={1.25} dot={false} isAnimationActive={false} connectNulls />
          ))}
          {typeof d.first_low_date === "string" && inWindow(d.first_low_date) && (
            <ReferenceDot x={d.first_low_date} y={Number(d.first_low)} r={5} fill={PLAN.stop} stroke="var(--surface)" strokeWidth={2} />
          )}
          {typeof d.second_low_date === "string" && inWindow(d.second_low_date) && (
            <ReferenceDot x={d.second_low_date} y={Number(d.second_low)} r={5} fill={PLAN.stop} stroke="var(--surface)" strokeWidth={2} />
          )}
        </ComposedChart>
      </ResponsiveContainer>
      {rsi && (
        <ResponsiveContainer width="100%" height={110}>
          <ComposedChart data={rows} margin={{ top: 4, right: 12, left: 0, bottom: 0 }}>
            <CartesianGrid {...GRID} />
            <XAxis dataKey="t" {...X_AXIS} hide />
            <YAxis {...Y_AXIS} width={64} domain={[0, 100]} ticks={[30, 50, 70]} tick={AXIS_TICK} />
            <Tooltip content={<ChartTooltip format={(v) => v.toFixed(1)} />} cursor={CURSOR} />
            <ReferenceLine y={30} stroke="var(--baseline)" strokeDasharray="3 3" />
            <ReferenceLine y={70} stroke="var(--baseline)" strokeDasharray="3 3" />
            <Line dataKey="rsi" name="RSI" stroke={seriesColor(1)} strokeWidth={1.5} dot={false} isAnimationActive={false} />
            {typeof d.first_low_date === "string" && inWindow(d.first_low_date) && (
              <ReferenceDot x={d.first_low_date} y={Number(d.rsi_first)} r={4} fill={seriesColor(1)} stroke="var(--surface)" strokeWidth={2} />
            )}
            {typeof d.second_low_date === "string" && inWindow(d.second_low_date) && (
              <ReferenceDot x={d.second_low_date} y={Number(d.rsi_second)} r={4} fill={seriesColor(1)} stroke="var(--surface)" strokeWidth={2} />
            )}
          </ComposedChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}
