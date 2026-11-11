import { useEffect, useRef, useState } from "react";
import ThemeToggle from "./ThemeToggle";
import {
  getMarkets, getMe, getStrategies, logout, runBacktest, runCompare, setUnauthorizedHandler, type Me, type RunInput,
} from "./api";
import Compare from "./Compare";
import Login from "./Login";
import ConfigPanel, { MAX_RUNS, type Config, type RunCfg } from "./ConfigPanel";
import Results from "./Results";
import Screener from "./Screener";
import type { BacktestResponse, CompareResponse, MarketInfo, Params, StrategyInfo } from "./types";

type View = "backtest" | "screener";

type Outcome =
  | { kind: "single"; data: BacktestResponse; strategy: string; market: string; initialCash: number }
  | { kind: "compare"; data: CompareResponse; market: string };

function defaultsFor(s: StrategyInfo): Params {
  const out: Params = {};
  for (const [k, p] of Object.entries(s.params_schema.properties ?? {}))
    if (p.default !== undefined) out[k] = p.default;
  return out;
}

/** Cleared number inputs become NaN, which JSON-encodes as null and would fail confusingly server-side. */
function firstInvalidNumber(cfg: Config): string | null {
  if (Number.isNaN(cfg.initialCash)) return "Modal awal harus berupa angka.";
  for (const [k, v] of Object.entries(cfg.costs)) if (Number.isNaN(v)) return `Biaya ${k} harus berupa angka.`;
  for (const [i, r] of cfg.runs.entries())
    for (const [k, v] of Object.entries(r.params))
      if (typeof v === "number" && Number.isNaN(v)) return `Strategi ${i + 1}: parameter ${k} harus berupa angka.`;
  return null;
}

export default function App() {
  // undefined = still checking the session; null = logged out
  const [me, setMe] = useState<Me | null | undefined>(undefined);
  const [authNotice, setAuthNotice] = useState<string | null>(null);
  const [strategies, setStrategies] = useState<StrategyInfo[]>([]);
  const [markets, setMarkets] = useState<MarketInfo[]>([]);
  const [cfg, setCfg] = useState<Config | null>(null);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [view, setView] = useState<View>("backtest");
  const abort = useRef<AbortController | null>(null);
  const nextId = useRef(1);

  useEffect(() => {
    setUnauthorizedHandler(() => {
      abort.current?.abort();
      setMe(null);
      setAuthNotice("Sesi Anda berakhir. Silakan masuk lagi.");
    });
    getMe().then(setMe).catch(() => {
      setMe(null);
      setAuthNotice("Tidak bisa menghubungi API. Pastikan backend berjalan.");
    });
  }, []);

  // Wipe everything loaded under the previous session when the user changes or logs out.
  useEffect(() => {
    setOutcome(null);
    setError(null);
    setCfg(null);
  }, [me?.username]);

  useEffect(() => {
    if (!me) return;
    Promise.all([getStrategies(), getMarkets()])
      .then(([s, m]) => {
        setStrategies(s);
        setMarkets(m);
        const strat = s.find((x) => x.name === "sma_cross") ?? s[0];
        const mkt = m.find((x) => x.name === "crypto") ?? m[0];
        setCfg({
          runs: [{ id: 0, strategy: strat.name, params: defaultsFor(strat) }],
          market: mkt.name, costs: { ...mkt.default_costs }, initialCash: 10000, start: "", end: "", file: null,
        });
      })
      .catch((e: Error) => setError(`Tidak bisa memuat katalog strategi: ${e.message}`));
  }, [me?.username]);

  const run = async () => {
    if (!cfg) return;
    const bad = firstInvalidNumber(cfg);
    if (bad) return setError(bad);
    abort.current?.abort();
    const ctl = (abort.current = new AbortController());
    setLoading(true);
    setError(null);
    const input: RunInput = {
      market: cfg.market, initialCash: cfg.initialCash, costs: cfg.costs, file: cfg.file,
      start: cfg.start || undefined, end: cfg.end || undefined,
    };
    const specs = cfg.runs.map(({ strategy, params }) => ({ strategy, params }));
    try {
      if (specs.length === 1) {
        const data = await runBacktest(input, specs[0], ctl.signal);
        setOutcome({ kind: "single", data, strategy: specs[0].strategy, market: cfg.market, initialCash: cfg.initialCash });
      } else {
        const data = await runCompare(input, specs, ctl.signal);
        setOutcome({ kind: "compare", data, market: cfg.market });
      }
    } catch (e) {
      if ((e as Error).name !== "AbortError") setError((e as Error).message);
    } finally {
      if (abort.current === ctl) setLoading(false);
    }
  };

  const defaultsByStrategy = Object.fromEntries(strategies.map((s) => [s.name, defaultsFor(s)]));

  const patchRun = (id: number, patch: Partial<RunCfg>) =>
    setCfg((c) => c && { ...c, runs: c.runs.map((r) => (r.id === id ? { ...r, ...patch } : r)) });
  const changeStrategy = (id: number, name: string) =>
    patchRun(id, { strategy: name, params: defaultsFor(strategies.find((x) => x.name === name)!) });
  const addRun = () =>
    setCfg((c) => {
      if (!c || c.runs.length >= MAX_RUNS) return c;
      // Default the new run to a strategy not yet used. buy_hold is skipped: it is already drawn as the benchmark.
      const used = new Set(c.runs.map((r) => r.strategy));
      const s = strategies.find((x) => !used.has(x.name) && x.name !== "buy_hold")
        ?? strategies.find((x) => x.name !== "buy_hold") ?? strategies[0];
      return { ...c, runs: [...c.runs, { id: nextId.current++, strategy: s.name, params: defaultsFor(s) }] };
    });
  const removeRun = (id: number) =>
    setCfg((c) => c && c.runs.length > 1 ? { ...c, runs: c.runs.filter((r) => r.id !== id) } : c);
  const changeMarket = (name: string) => {
    const m = markets.find((x) => x.name === name)!;
    setCfg((c) => c && { ...c, market: name, costs: { ...m.default_costs } });
  };

  const signOut = async () => {
    abort.current?.abort();
    await logout().catch(() => {});
    setAuthNotice("Anda sudah keluar.");
    setMe(null);
  };
  const signedIn = (m: Me) => {
    setAuthNotice(null);
    setMe(m);
  };

  if (me === undefined) return <div className="login-page" aria-busy="true" />;
  if (me === null) return <Login onLogin={signedIn} notice={authNotice} />;

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <svg width="28" height="28" viewBox="0 0 28 28" aria-hidden="true">
            <rect width="28" height="28" rx="8" fill="var(--accent)" />
            <path d="M5 19l5-6 4 3 8-9" fill="none" stroke="#fff" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
            <circle cx="22" cy="7" r="2.2" fill="#fff" />
          </svg>
          <span>Backtest Platform</span>
        </div>
        <nav className="nav" aria-label="Halaman">
          <button type="button" className={view === "backtest" ? "active" : ""} onClick={() => setView("backtest")}>Backtest</button>
          <button type="button" className={view === "screener" ? "active" : ""} onClick={() => setView("screener")}>Screener</button>
        </nav>
        <div className="topbar-actions">
          <ThemeToggle />
          <span className="user-chip" title="Pengguna yang sedang masuk">{me.username}</span>
          <button type="button" className="ghost" onClick={signOut}>Keluar</button>
        </div>
      </header>
      {view === "screener" ? <Screener /> : (
      <div className="layout">
        <aside>
          {cfg && (
            <ConfigPanel
              cfg={cfg} strategies={strategies} markets={markets} loading={loading} onChange={setCfg}
              onRunChange={patchRun} onStrategy={changeStrategy} onAddRun={addRun} onRemoveRun={removeRun}
              onMarket={changeMarket} onSubmit={run}
            />
          )}
        </aside>
        <main>
          {error && <div className="error" role="alert">{error}</div>}
          {!outcome && !error && cfg && (
            <div className="empty">
              <svg width="120" height="64" viewBox="0 0 120 64" aria-hidden="true">
                <path d="M4 52l22-20 18 10 26-28 22 14 24-22" fill="none" stroke="var(--series-1)" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
                <path d="M4 58l22-8 18 4 26-14 22 6 24-8" fill="none" stroke="var(--muted)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" strokeDasharray="5 4" />
              </svg>
              <h2>Siap menguji strategi</h2>
              <p>
                Pilih strategi dan market di panel kiri, lalu jalankan. Tanpa file CSV, data demo sintetis dipakai.
                Tambahkan strategi pembanding untuk melihat beberapa strategi berdampingan.
              </p>
              <button type="button" onClick={run} disabled={loading}>{loading ? "Menjalankan…" : "Jalankan dengan data demo"}</button>
            </div>
          )}
          {outcome?.kind === "single" && (
            <Results r={outcome.data} strategy={outcome.strategy} market={outcome.market}
              initialCash={outcome.initialCash} stale={loading} />
          )}
          {outcome?.kind === "compare" && (
            <Compare r={outcome.data} market={outcome.market} defaults={defaultsByStrategy} stale={loading} />
          )}
        </main>
      </div>
      )}
    </div>
  );
}
