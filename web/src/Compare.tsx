import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import {
  activeDot, axisFormatter, ChartCard, ChartLegend, ChartTooltip, CURSOR, DataTable, endDot, GRID, seriesColor,
  X_AXIS, Y_AXIS,
} from "./charts";
import { fmtMetric, METRIC_LABELS, money, pct, uniqueLabels } from "./format";
import type { CompareResponse, Params } from "./types";

type Better = "high" | "low" | null;
/** Which direction is "better" for each metric; null = no ranking (e.g. trade count). */
const BETTER: Record<string, Better> = {
  total_return: "high", cagr: "high", sharpe: "high", sortino: "high", calmar: "high", max_drawdown: "high",
  win_rate: "high", profit_factor: "high", annual_volatility: "low", turnover_per_year: "low",
  n_trades: null, exposure: null,
};

function bestIndex(values: (number | null)[], better: Better): number {
  if (!better) return -1;
  let best = -1;
  values.forEach((v, i) => {
    if (v === null || !Number.isFinite(v)) return;
    if (best === -1 || (better === "high" ? v > values[best]! : v < values[best]!)) best = i;
  });
  // A tie for "best" isn't informative.
  return best !== -1 && values.filter((v) => v === values[best]).length > 1 ? -1 : best;
}

const BENCH = "var(--muted)";

export default function Compare({ r, market, defaults, stale }: {
  r: CompareResponse; market: string; defaults: Record<string, Params>; stale: boolean;
}) {
  const labels = uniqueLabels(r.runs, defaults);
  const n = r.series.length;
  const first = r.series[0].t, last = r.series[n - 1].t;
  const metricKeys = Object.keys(r.runs[0].metrics);
  const legend = [
    ...labels.map((l, i) => ({ label: l, color: seriesColor(i) })),
    { label: "Buy & hold", color: BENCH, dashed: true },
  ];
  const equityMax = Math.max(...r.series.flatMap((p) => [p.benchmark, ...labels.map((_, i) => p[`e${i}`] as number)]));
  const fmtAxis = axisFormatter(equityMax);

  return (
    <div className={stale ? "stale" : ""}>
      <div className="result-head">
        <h2>Perbandingan {r.runs.length} strategi</h2>
        <span className="pill">{market}</span>
        <span className="pill">{r.bars} bar</span>
        <span className="pill">{first} → {last}</span>
      </div>

      <ChartCard
        title="Equity" hint="Semua strategi pada data, market, dan biaya yang sama"
        legend={<ChartLegend items={legend} />}
        table={() => (
          <DataTable rows={r.series} columns={[
            { key: "t", label: "Tanggal" },
            ...labels.map((l, i) => ({ key: `e${i}`, label: l, format: money })),
            { key: "benchmark", label: "Buy & hold", format: money },
          ]} />
        )}
      >
        <ResponsiveContainer width="100%" height={340}>
          <LineChart data={r.series} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
            <CartesianGrid {...GRID} />
            <XAxis dataKey="t" {...X_AXIS} />
            <YAxis {...Y_AXIS} tickFormatter={fmtAxis} domain={["auto", "auto"]} />
            <Tooltip content={<ChartTooltip format={money} pinLast="Buy & hold" />} cursor={CURSOR} />
            <Line dataKey="benchmark" name="Buy & hold" stroke={BENCH} strokeWidth={1.5} strokeDasharray="5 3"
              strokeLinecap="round" dot={false} activeDot={activeDot(BENCH)} isAnimationActive={false} />
            {labels.map((l, i) => (
              <Line key={i} dataKey={`e${i}`} name={l} stroke={seriesColor(i)} strokeWidth={2} strokeLinecap="round"
                strokeLinejoin="round" dot={endDot(n - 1, seriesColor(i))} activeDot={activeDot(seriesColor(i))}
                isAnimationActive={false} />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </ChartCard>

      <ChartCard
        title="Drawdown" hint="Penurunan dari puncak equity sebelumnya"
        legend={<ChartLegend items={legend.slice(0, labels.length)} />}
        table={() => (
          <DataTable rows={r.series} columns={[
            { key: "t", label: "Tanggal" },
            ...labels.map((l, i) => ({ key: `d${i}`, label: l, format: (v: number) => pct(v) })),
          ]} />
        )}
      >
        <ResponsiveContainer width="100%" height={220}>
          <LineChart data={r.series} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
            <CartesianGrid {...GRID} />
            <XAxis dataKey="t" {...X_AXIS} />
            <YAxis {...Y_AXIS} tickFormatter={(v: number) => `${(v * 100).toFixed(0)}%`} />
            <Tooltip content={<ChartTooltip format={(v) => pct(v)} />} cursor={CURSOR} />
            {labels.map((l, i) => (
              <Line key={i} dataKey={`d${i}`} name={l} stroke={seriesColor(i)} strokeWidth={2} strokeLinecap="round"
                strokeLinejoin="round" dot={false} activeDot={activeDot(seriesColor(i))} isAnimationActive={false} />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </ChartCard>

      <section className="card">
        <header className="card-head"><div><h3>Metrik</h3>
          <p className="hint">Cetak tebal = terbaik per baris di antara strategi (tidak termasuk buy & hold). Drawdown terbaik = yang paling dangkal.</p>
        </div></header>
        <div className="scroll">
          <table>
            <thead>
              <tr>
                <th>Metrik</th>
                {labels.map((l, i) => (
                  <th key={i} className="run-col"><span className="swatch" style={{ background: seriesColor(i) }} aria-hidden="true" />{l}</th>
                ))}
                <th>Buy & hold</th>
              </tr>
            </thead>
            <tbody>
              {metricKeys.map((k) => {
                const vals = r.runs.map((x) => x.metrics[k]);
                const best = bestIndex(vals, BETTER[k] ?? null);
                return (
                  <tr key={k}>
                    <td>{METRIC_LABELS[k] ?? k}</td>
                    {vals.map((v, i) => (
                      <td key={i} className={i === best ? "best" : ""}>{fmtMetric(k, v)}</td>
                    ))}
                    <td className="muted">{fmtMetric(k, r.benchmark_metrics[k])}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
