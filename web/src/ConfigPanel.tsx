import type { CostModel, MarketInfo, Params, StrategyInfo } from "./types";

export const MAX_RUNS = 6;

export interface RunCfg {
  id: number; // stable React key
  strategy: string;
  params: Params;
}

export interface Config {
  runs: RunCfg[];
  market: string;
  costs: CostModel;
  initialCash: number;
  start: string;
  end: string;
  file: File | null;
}

interface Props {
  cfg: Config;
  strategies: StrategyInfo[];
  markets: MarketInfo[];
  loading: boolean;
  onChange: (c: Config) => void;
  onRunChange: (id: number, patch: Partial<RunCfg>) => void;
  onStrategy: (id: number, name: string) => void;
  onAddRun: () => void;
  onRemoveRun: (id: number) => void;
  onMarket: (name: string) => void;
  onSubmit: () => void;
}

const COST_LABELS: Record<keyof CostModel, string> = {
  commission_bps: "Commission (bps/side)",
  slippage_bps: "Slippage (bps/side)",
  borrow_bps_annual: "Borrow (bps/yr, short)",
  swap_bps_daily: "Swap (bps/day)",
};

function RunFields({ run, index, strategies, canRemove, onStrategy, onChange, onRemove }: {
  run: RunCfg; index: number; strategies: StrategyInfo[]; canRemove: boolean;
  onStrategy: (name: string) => void; onChange: (p: Params) => void; onRemove: () => void;
}) {
  const strat = strategies.find((s) => s.name === run.strategy)!;
  const props = Object.entries(strat.params_schema.properties ?? {});
  return (
    <div className="run">
      <div className="run-head">
        <label>
          Strategi {index + 1}
          <select value={run.strategy} onChange={(e) => onStrategy(e.target.value)}>
            {strategies.map((s) => <option key={s.name}>{s.name}</option>)}
          </select>
        </label>
        {canRemove && (
          <button type="button" className="ghost" onClick={onRemove} aria-label={`Hapus strategi ${index + 1}`}>Hapus</button>
        )}
      </div>
      {props.length === 0 && <small className="muted">Tidak ada parameter.</small>}
      {props.map(([key, p]) => {
        const val = run.params[key];
        if (p.type === "boolean")
          return (
            <label key={key} className="check">
              <input type="checkbox" checked={Boolean(val)} onChange={(e) => onChange({ ...run.params, [key]: e.target.checked })} />
              {key}
            </label>
          );
        return (
          <label key={key}>
            {key}
            <input
              type="number" value={val as number} min={p.minimum} max={p.maximum}
              step={p.type === "integer" ? 1 : "any"}
              onChange={(e) => onChange({ ...run.params, [key]: e.target.valueAsNumber })}
            />
          </label>
        );
      })}
    </div>
  );
}

export default function ConfigPanel(p: Props) {
  const { cfg, strategies, markets, loading, onChange } = p;
  const mkt = markets.find((m) => m.name === cfg.market)!;
  const set = (patch: Partial<Config>) => onChange({ ...cfg, ...patch });
  const comparing = cfg.runs.length > 1;

  return (
    <form onSubmit={(e) => { e.preventDefault(); p.onSubmit(); }}>
      <fieldset>
        <legend>Data</legend>
        <label>
          File CSV (opsional)
          <input type="file" accept=".csv,text/csv" onChange={(e) => set({ file: e.target.files?.[0] ?? null })} />
        </label>
        <small className="muted">Kolom: date, open, high, low, close, volume. Kosong = data demo.</small>
        <div className="row">
          <label>Mulai<input type="date" value={cfg.start} onChange={(e) => set({ start: e.target.value })} /></label>
          <label>Sampai<input type="date" value={cfg.end} onChange={(e) => set({ end: e.target.value })} /></label>
        </div>
      </fieldset>

      <fieldset>
        <legend>{comparing ? "Strategi (dibandingkan)" : "Strategi"}</legend>
        {cfg.runs.map((run, i) => (
          <RunFields
            key={run.id} run={run} index={i} strategies={strategies} canRemove={comparing}
            onStrategy={(name) => p.onStrategy(run.id, name)}
            onChange={(params) => p.onRunChange(run.id, { params })}
            onRemove={() => p.onRemoveRun(run.id)}
          />
        ))}
        <button type="button" className="secondary" onClick={p.onAddRun} disabled={cfg.runs.length >= MAX_RUNS}>
          + Tambah strategi pembanding
        </button>
        {cfg.runs.length >= MAX_RUNS && <small className="muted">Maksimum {MAX_RUNS} strategi.</small>}
      </fieldset>

      <fieldset>
        <legend>Market & biaya</legend>
        <label>
          Market
          <select value={cfg.market} onChange={(e) => p.onMarket(e.target.value)}>
            {markets.map((m) => <option key={m.name}>{m.name}</option>)}
          </select>
        </label>
        <small className="muted">{mkt.periods_per_year} bar/tahun · short {mkt.allow_short ? "diizinkan" : "tidak diizinkan"}</small>
        <label>
          Modal awal
          <input type="number" min={1} value={cfg.initialCash} onChange={(e) => set({ initialCash: e.target.valueAsNumber })} />
        </label>
        {(Object.keys(COST_LABELS) as (keyof CostModel)[]).map((k) => (
          <label key={k}>
            {COST_LABELS[k]}
            <input type="number" min={0} step="any" value={cfg.costs[k]}
              onChange={(e) => set({ costs: { ...cfg.costs, [k]: e.target.valueAsNumber } })} />
          </label>
        ))}
      </fieldset>

      <div className="submit">
      <button type="submit" disabled={loading}>
        {loading && <span className="spinner" aria-hidden="true" />}
        {loading ? "Menjalankan…" : comparing ? `Bandingkan ${cfg.runs.length} strategi` : "Run backtest"}
      </button>
      </div>
    </form>
  );
}
