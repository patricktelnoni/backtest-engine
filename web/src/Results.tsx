import { Area, AreaChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import {
  activeDot, axisFormatter, ChartCard, ChartLegend, ChartTooltip, CURSOR, DataTable, endDot, GRID, seriesColor,
  X_AXIS, Y_AXIS,
} from "./charts";
import { fmtMetric as fmt, METRIC_LABELS as LABELS, money, pct, price } from "./format";
import type { BacktestResponse, Metrics, Trade } from "./types";

const STRATEGY = seriesColor(0); // color follows the entity: the strategy is always series 1
const BENCH = "var(--muted)";

/** Signed delta vs a named baseline. Color = direction x whether up is good; the glyph + text carry it too. */
function Delta({ now, base, scale = 1, unit, label }: {
  now: number | null; base: number | null; scale?: number; unit: string; label: string;
}) {
  if (now === null || base === null) return null;
  const d = (now - base) * scale;
  if (Math.abs(d) < 0.005) return <span className="delta flat">● sama dengan {label}</span>;
  const up = d > 0;
  return (
    <span className={`delta ${up ? "good" : "bad"}`}>
      {up ? "▲" : "▼"} {up ? "+" : "−"}{Math.abs(d).toFixed(2)} {unit} vs {label}
    </span>
  );
}

function Tile({ label, value, children }: { label: string; value: string; children?: React.ReactNode }) {
  return (
    <div className="tile">
      <span className="tile-label">{label}</span>
      <strong className="tile-value">{value}</strong>
      {children}
    </div>
  );
}

function TradesTable({ trades }: { trades: Trade[] }) {
  if (trades.length === 0) return <p className="muted">Tidak ada trade pada periode ini.</p>;
  return (
    <div className="scroll">
      <table className="trades">
        <thead>
          <tr><th>#</th><th>Sisi</th><th>Masuk</th><th>Keluar</th><th>Bar</th><th>Return</th></tr>
        </thead>
        <tbody>
          {trades.map((t, i) => (
            <tr key={i}>
              <td>{i + 1}</td>
              <td>{t.side}{t.size !== 1 ? ` ×${t.size}` : ""}</td>
              <td>{t.entry_date}<span className="sub">{price(t.entry_price)}</span></td>
              <td>
                {t.exit_date}{t.open && <span className="pill">terbuka</span>}
                <span className="sub">{price(t.exit_price)}</span>
              </td>
              <td>{t.bars}</td>
              <td className={t.return > 0 ? "pos" : t.return < 0 ? "neg" : ""}>{pct(t.return)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function MetricsTable({ m, b }: { m: Metrics; b: Metrics }) {
  return (
    <table>
      <thead><tr><th>Metrik</th><th>Strategi</th><th>Buy & hold</th></tr></thead>
      <tbody>
        {Object.keys(m).map((k) => (
          <tr key={k}><td>{LABELS[k] ?? k}</td><td>{fmt(k, m[k])}</td><td className="muted">{fmt(k, b[k])}</td></tr>
        ))}
      </tbody>
    </table>
  );
}

export default function Results({ r, strategy, market, initialCash, stale }: {
  r: BacktestResponse; strategy: string; market: string; initialCash: number; stale: boolean;
}) {
  const n = r.series.length;
  const first = r.series[0].t, last = r.series[n - 1];
  const m = r.metrics, b = r.benchmark_metrics;
  const total = m.total_return ?? 0;
  const wins = r.trade_returns.filter((t) => t > 0).length;
  const equityMax = Math.max(...r.series.map((p) => Math.max(p.equity, p.benchmark)));
  const fmtAxis = axisFormatter(equityMax);

  return (
    <div className={stale ? "stale" : ""}>
      <div className="result-head">
        <h2>{strategy}</h2>
        <span className="pill">{market}</span>
        <span className="pill">{r.bars} bar</span>
        <span className="pill">{first} → {last.t}</span>
      </div>

      <div className="summary">
        <div className="hero">
          <span className="tile-label">Equity akhir</span>
          <strong className="hero-value">{money(last.equity)}</strong>
          <span className={`delta ${total >= 0 ? "good" : "bad"}`}>
            {total >= 0 ? "▲" : "▼"} {pct(total)} dari modal awal {money(initialCash)}
          </span>
        </div>
        <Tile label="Total return" value={fmt("total_return", m.total_return)}>
          <Delta now={m.total_return} base={b.total_return} scale={100} unit="pp" label="buy & hold" />
        </Tile>
        <Tile label="Sharpe ratio" value={fmt("sharpe", m.sharpe)}>
          <Delta now={m.sharpe} base={b.sharpe} unit="" label="buy & hold" />
        </Tile>
        <Tile label="Max drawdown" value={fmt("max_drawdown", m.max_drawdown)}>
          <Delta now={m.max_drawdown} base={b.max_drawdown} scale={100} unit="pp" label="buy & hold" />
        </Tile>
      </div>

      <ChartCard
        title="Equity" hint="Nilai portofolio dari waktu ke waktu"
        legend={<ChartLegend items={[{ label: "Strategi", color: STRATEGY }, { label: "Buy & hold", color: BENCH, dashed: true }]} />}
        table={() => (
          <DataTable rows={r.series} columns={[
            { key: "t", label: "Tanggal" },
            { key: "equity", label: "Strategi", format: money },
            { key: "benchmark", label: "Buy & hold", format: money },
          ]} />
        )}
      >
        <ResponsiveContainer width="100%" height={300}>
          <LineChart data={r.series} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
            <CartesianGrid {...GRID} />
            <XAxis dataKey="t" {...X_AXIS} />
            <YAxis {...Y_AXIS} tickFormatter={fmtAxis} domain={["auto", "auto"]} />
            <Tooltip content={<ChartTooltip format={money} pinLast="Buy & hold" />} cursor={CURSOR} />
            <Line dataKey="benchmark" name="Buy & hold" stroke={BENCH} strokeWidth={1.5} strokeDasharray="5 3"
              strokeLinecap="round" dot={false} activeDot={activeDot(BENCH)} isAnimationActive={false} />
            <Line dataKey="equity" name="Strategi" stroke={STRATEGY} strokeWidth={2} strokeLinecap="round" strokeLinejoin="round"
              dot={endDot(n - 1, STRATEGY)} activeDot={activeDot(STRATEGY)} isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      </ChartCard>

      <ChartCard
        title="Drawdown" hint="Penurunan dari puncak equity sebelumnya"
        table={() => (
          <DataTable rows={r.series} columns={[
            { key: "t", label: "Tanggal" },
            { key: "drawdown", label: "Drawdown", format: (v) => pct(v) },
          ]} />
        )}
      >
        <ResponsiveContainer width="100%" height={180}>
          <AreaChart data={r.series} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
            <CartesianGrid {...GRID} />
            <XAxis dataKey="t" {...X_AXIS} />
            <YAxis {...Y_AXIS} tickFormatter={(v: number) => `${(v * 100).toFixed(0)}%`} />
            <Tooltip content={<ChartTooltip format={(v) => pct(v)} />} cursor={CURSOR} />
            <Area dataKey="drawdown" name="Drawdown" stroke={STRATEGY} strokeWidth={2} strokeLinejoin="round"
              fill={STRATEGY} fillOpacity={0.1} dot={false} activeDot={activeDot(STRATEGY)} isAnimationActive={false} />
          </AreaChart>
        </ResponsiveContainer>
      </ChartCard>

      <div className="two-col">
        <section className="card">
          <header className="card-head"><div><h3>Metrik</h3></div></header>
          <MetricsTable m={m} b={b} />
          <p className="hint">{r.trade_returns.length} trade · {wins} menang · {r.trade_returns.length - wins} kalah/impas</p>
        </section>

        <section className="card">
          <header className="card-head"><div><h3>Daftar trade</h3></div></header>
          <TradesTable trades={r.trades} />
          <p className="hint">
            Return per trade mencakup biaya masuk, borrow, dan swap selama posisi dipegang. Biaya keluar tidak termasuk.
          </p>
        </section>
      </div>
    </div>
  );
}
