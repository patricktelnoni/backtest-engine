import { useState, type ReactNode } from "react";

/** Series colors are CSS roles (--series-1..6) so light/dark swap in one place (see styles.css). */
export const seriesColor = (i: number) => `var(--series-${(i % 6) + 1})`;

/** Y-axis labels: full numbers below 10,000, compact (10K / 1.2M) once the axis reaches it; one style per axis. */
export function axisFormatter(max: number): (v: number) => string {
  if (max < 10_000) return (v) => v.toLocaleString("en-US", { maximumFractionDigits: 0 });
  return (v) => {
    const a = Math.abs(v);
    if (a >= 1e6) return `${+(v / 1e6).toFixed(2)}M`;
    return a >= 1e3 ? `${+(v / 1e3).toFixed(1)}K` : String(Math.round(v));
  };
}

export const AXIS_TICK = { fontSize: 12, fill: "var(--muted)" } as const;
export const GRID = { stroke: "var(--grid)", vertical: false, strokeWidth: 1 } as const;
export const X_AXIS = { tick: AXIS_TICK, tickLine: false, axisLine: { stroke: "var(--baseline)" }, minTickGap: 56 } as const;
export const Y_AXIS = { tick: AXIS_TICK, tickLine: false, axisLine: false, width: 56 } as const;
export const CURSOR = { stroke: "var(--baseline)", strokeWidth: 1 } as const;

/** Surface-ringed dot on the last point only (marker >= 8px, 2px surface ring). */
export function endDot(lastIndex: number, color: string) {
  return function EndDot(p: { cx?: number; cy?: number; index?: number }) {
    if (p.index !== lastIndex || p.cx === undefined || p.cy === undefined) return <g key={`d${p.index}`} />;
    return <circle key={`d${p.index}`} cx={p.cx} cy={p.cy} r={5} fill={color} stroke="var(--surface)" strokeWidth={2} />;
  };
}

export const activeDot = (color: string) => ({ r: 5, fill: color, stroke: "var(--surface)", strokeWidth: 2 });

/** Legend: a mark-colored key beside text-token text. Always shown for >= 2 series. */
export function ChartLegend({ items }: { items: { label: string; color: string; dashed?: boolean }[] }) {
  return (
    <ul className="legend">
      {items.map((it) => (
        <li key={it.label}>
          <svg width="18" height="10" aria-hidden="true">
            <line x1="1" y1="5" x2="17" y2="5" stroke={it.color} strokeWidth="2" strokeLinecap="round"
              strokeDasharray={it.dashed ? "4 3" : undefined} />
          </svg>
          {it.label}
        </li>
      ))}
    </ul>
  );
}

interface TipProps {
  active?: boolean;
  payload?: { name?: string; value?: number | string; color?: string; stroke?: string }[];
  label?: string;
  format: (v: number) => string;
  pinLast?: string; // series name listed last (e.g. the benchmark)
}

/** Crosshair tooltip body: date, then one row per series with its mark color and a text-token value. */
export function ChartTooltip({ active, payload, label, format, pinLast }: TipProps) {
  if (!active || !payload?.length) return null;
  const rows = [...payload].sort((a, b) => Number(a.name === pinLast) - Number(b.name === pinLast));
  return (
    <div className="tip">
      <div className="tip-date">{label}</div>
      {rows.map((p) => (
        <div key={p.name} className="tip-row">
          <span className="swatch" style={{ background: p.stroke ?? p.color }} aria-hidden="true" />
          <span className="tip-name">{p.name}</span>
          <span className="tip-val">{format(Number(p.value))}</span>
        </div>
      ))}
    </div>
  );
}

/** Card with title, optional legend, chart body and a collapsible table view of the same data. */
export function ChartCard({ title, hint, legend, table, children }: {
  title: string;
  hint?: string;
  legend?: ReactNode;
  table?: () => ReactNode; // rendered lazily, only while open
  children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  return (
    <section className="card">
      <header className="card-head">
        <div>
          <h3>{title}</h3>
          {hint && <p className="hint">{hint}</p>}
        </div>
        {legend}
      </header>
      {children}
      {table && (
        <details className="table-view" onToggle={(e) => setOpen((e.currentTarget as HTMLDetailsElement).open)}>
          <summary>Lihat sebagai tabel</summary>
          {open && table()}
        </details>
      )}
    </section>
  );
}

/** Plain table for a chart's underlying rows. */
export function DataTable({ columns, rows }: {
  columns: { key: string; label: string; format?: (v: number) => string }[];
  rows: object[];
}) {
  return (
    <div className="scroll">
      <table>
        <thead>
          <tr>{columns.map((c) => <th key={c.key}>{c.label}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((row, i) => {
            const r = row as Record<string, unknown>;
            return (
              <tr key={i}>
                {columns.map((c) => {
                  const v = r[c.key];
                  return <td key={c.key}>{typeof v === "number" && c.format ? c.format(v) : String(v ?? "")}</td>;
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
