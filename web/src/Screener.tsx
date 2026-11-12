import { Fragment, useEffect, useState } from "react";

const PAGE_SIZES = [10, 25, 50, 100] as const;
import ScreenerChart from "./ScreenerChart";
import { getScreener } from "./api";
import { pct, price } from "./format";
import type { ScreenerResponse, ScreenerRow } from "./types";

const REASON: Record<string, string> = { stop: "stop", rsi: "RSI", max_hold: "max hold" };

/** Per setup: title, one-line rule, and how to label/format its `detail` keys (unknown keys fall back to key: value). */
const SETUPS: Record<string, { title: string; rule: string; stopLabel: string; detail: Record<string, [string, (v: number | string) => string]> }> = {
  bullish_divergence: {
    title: "Bullish divergence",
    rule: "Harga membuat swing low lebih rendah sementara RSI membuat low lebih tinggi. Sinyal saat pivot kedua terkonfirmasi; stop = low divergence.",
    stopLabel: "low divergence",
    detail: {
      stop: ["Stop", (v) => price(Number(v))],
      first_low_date: ["Low 1", String], first_low: ["harga", (v) => price(Number(v))], rsi_first: ["RSI", (v) => Number(v).toFixed(1)],
      second_low_date: ["Low 2", String], second_low: ["harga", (v) => price(Number(v))], rsi_second: ["RSI", (v) => Number(v).toFixed(1)],
    },
  },
  consolidated_ma: {
    title: "Consolidated MA",
    rule: "EMA 10/20/50/100/200 merapat (rentang ≤ tightness_pct dari close) selama ≥ min_squeeze_bars bar, lalu close menembus di atas EMA tertinggi dengan volume ≥ volume_mult × rata-rata. Stop = EMA terendah saat sinyal.",
    stopLabel: "EMA terendah",
    detail: {
      stop: ["Stop", (v) => price(Number(v))],
      squeeze_start_date: ["Rapat sejak", String], squeeze_bars: ["bar", String],
      spread_pct: ["Rentang EMA", (v) => `${Number(v).toFixed(2)}%`], volume_ratio: ["Volume", (v) => `${Number(v).toFixed(1)}×`],
      ema_top: ["EMA atas", (v) => price(Number(v))], ema_bottom: ["EMA bawah", (v) => price(Number(v))],
    },
  },
};

function when(iso: string): string {
  return new Date(iso).toLocaleString("id-ID", { dateStyle: "medium", timeStyle: "short" });
}

function Status({ r }: { r: ScreenerRow }) {
  if (r.status === "active") return <span className="badge active">aktif · {r.bars_since_signal} bar</span>;
  return <span className="badge exited">keluar {r.exit_date} ({REASON[r.exit_reason ?? ""] ?? r.exit_reason})</span>;
}

function Signed({ v }: { v: number }) {
  return <span className={v > 0 ? "pos" : v < 0 ? "neg" : ""}>{pct(v)}</span>;
}

function Detail({ r, setup }: { r: ScreenerRow; setup: string }) {
  const spec = SETUPS[setup]?.detail ?? {};
  const entries: [string, number | string | null][] = [["stop", r.stop], ...Object.entries(r.detail)];
  return (
    <div className="detail">
      {entries.map(([k, v]) => {
        const [label, fmt] = spec[k] ?? [k, String];
        return <span key={k}><em>{label}</em> {v === null ? "n/a" : fmt(v)}</span>;
      })}
    </div>
  );
}

function Plan({ r }: { r: ScreenerRow }) {
  const p = r.plan;
  if (p === undefined) return <td colSpan={3} className="muted left">belum dihitung (jalankan ulang screener)</td>;
  if (!p) return <td colSpan={3} className="muted left">{r.status === "active" ? "harga sudah di bawah stop" : "–"}</td>;
  return (
    <>
      <td>
        {price(p.entry_low)} – {price(p.entry_high)}
        <span className="sub">risiko {(p.risk_pct * 100).toFixed(1)}% · RR di harga kini {p.rr_at_last.toFixed(1)}</span>
      </td>
      <td className="neg">{price(p.stop_loss)}</td>
      <td className="pos">
        {price(p.tp1)} <span className="muted">({p.rr_targets[0]}R, +{(p.tp1_pct * 100).toFixed(1)}%)</span>
        <span className="sub">TP2 {price(p.tp2)} ({p.rr_targets[1]}R, +{(p.tp2_pct * 100).toFixed(1)}%)</span>
      </td>
    </>
  );
}

function Row({ r, setup, open, onToggle }: { r: ScreenerRow; setup: string; open: boolean; onToggle: () => void }) {
  const h = r.history;
  const hist = h.n_trades
    ? `${h.n_trades} trade · win ${h.win_rate === null ? "n/a" : `${(h.win_rate * 100).toFixed(0)}%`} · avg ${h.avg_trade_return === null ? "n/a" : pct(h.avg_trade_return, 1)}`
    : "belum ada trade";
  return (
    <Fragment>
      <tr className={`clickable${open ? " open" : ""}`} onClick={onToggle} aria-expanded={open}>
        <td><span className="chev" aria-hidden="true">{open ? "▾" : "▸"}</span><strong>{r.ticker}</strong><span className="sub">{r.last_date}</span></td>
        <td><Status r={r} /></td>
        <td>{r.signal_date}<span className="sub">{price(r.signal_price)}</span></td>
        <td>{price(r.last_close)}<span className="sub"><Signed v={r.return_since_signal} /> sejak sinyal</span></td>
        <Plan r={r} />
        <td className="left"><Detail r={r} setup={setup} /></td>
        <td className="muted">{hist}</td>
      </tr>
      {open && (
        <tr className="expanded">
          <td colSpan={9}>
            {r.chart ? <ScreenerChart r={r} /> : (
              <p className="muted">Data chart belum ada: hasil ini dibuat oleh versi screener lama. Chart dan trade plan muncul setelah job
                screener berikutnya (atau <code>sudo systemctl start backtest-screener.service</code>).</p>
            )}
            <p className="hint">
              Area entry = rentang antara close bar sinyal dan close terakhir (tidak di bawah stop); R dihitung dari tengah area.
              Stop loss = {setup === "consolidated_ma" ? "EMA terendah saat sinyal" : "low divergence"} dikurangi 0,5%, dibulatkan ke fraksi harga.
              TP1 = entry + 2R, TP2 = entry + 3R. Bukan rekomendasi.
            </p>
          </td>
        </tr>
      )}
    </Fragment>
  );
}

export default function Screener() {
  // undefined = loading; null = the job has not produced a result yet
  const [data, setData] = useState<ScreenerResponse | null | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  const [setup, setSetup] = useState<string>("bullish_divergence");
  const [activeOnly, setActiveOnly] = useState(true);
  const [open, setOpen] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState<number>(25);

  // Back to the first page whenever the visible set changes.
  useEffect(() => { setPage(1); setOpen(null); }, [setup, activeOnly, pageSize]);

  useEffect(() => {
    getScreener().then((d) => {
      setData(d);
      if (d && !(setup in d.setups)) setSetup(Object.keys(d.setups)[0]);
    }).catch((e: Error) => {
      setError(e.message);
      setData(null);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (data === undefined) return <div className="page" aria-busy="true" />;
  if (error) return <div className="page"><div className="error" role="alert">{error}</div></div>;
  if (data === null) {
    return (
      <div className="page">
        <div className="empty">
          <h2>Screener belum pernah dijalankan</h2>
          <p>
            Hasil muncul setelah job terjadwal <code>backtest-screener</code> selesai (hari bursa, 16:30 WIB).
            Jalankan manual di server: <code>sudo systemctl start backtest-screener.service</code>.
          </p>
        </div>
      </div>
    );
  }

  const names = Object.keys(data.setups);
  const cur = data.setups[setup] ?? data.setups[names[0]];
  const meta = SETUPS[setup] ?? { title: setup, rule: "", stopLabel: "stop", detail: {} };
  const rows = activeOnly ? cur.results.filter((r) => r.status === "active") : cur.results;
  const active = cur.results.filter((r) => r.status === "active").length;
  const params = Object.entries(cur.params).map(([k, v]) => `${k}=${String(v)}`).join(" · ");
  const years = Math.round((cur.results[0]?.history.bars ?? 0) / 245);
  const pages = Math.max(1, Math.ceil(rows.length / pageSize));
  const current = Math.min(page, pages);
  const start = (current - 1) * pageSize;
  const shown = rows.slice(start, start + pageSize);
  const goTo = (n: number) => { setPage(Math.min(pages, Math.max(1, n))); setOpen(null); };

  return (
    <div className="page">
      <div className="result-head">
        <h2>Screener IDX</h2>
        <span className="pill">{data.scanned}/{data.universe} saham</span>
        <span className="pill">diperbarui {when(data.generated_at)}</span>
        {data.errors.length > 0 && <span className="pill warn">{data.errors.length} gagal</span>}
      </div>

      <div className="setup-tabs" role="tablist" aria-label="Setup">
        {names.map((n) => {
          const a = data.setups[n].results.filter((r) => r.status === "active").length;
          return (
            <button key={n} type="button" role="tab" aria-selected={n === setup} className={n === setup ? "active" : ""}
              onClick={() => setSetup(n)}>
              {SETUPS[n]?.title ?? n} <span className="count">{a}</span>
            </button>
          );
        })}
      </div>

      <p className="hint">{meta.rule} Parameter: {params}.</p>

      <section className="card">
        <header className="card-head">
          <div>
            <h3>{active} setup aktif · {cur.results.length - active} baru keluar (≤ {data.recent_bars} bar)</h3>
          </div>
          <label className="check">
            <input type="checkbox" checked={activeOnly} onChange={(e) => setActiveOnly(e.target.checked)} />
            Hanya yang aktif
          </label>
        </header>
        {rows.length === 0 ? (
          <p className="muted">Tidak ada setup {activeOnly ? "aktif" : ""} saat ini.</p>
        ) : (
          <div className="scroll">
            <table className="screener">
              <thead>
                <tr>
                  <th>Saham</th><th>Status</th><th>Sinyal</th><th>Harga terakhir</th>
                  <th>Area entry</th><th>Stop loss</th><th>Take profit</th>
                  <th className="left">Detail setup</th><th>Histori setup ini</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((r) => (
                  <Row key={r.ticker} r={r} setup={setup} open={open === r.ticker}
                    onToggle={() => setOpen(open === r.ticker ? null : r.ticker)} />
                ))}
              </tbody>
            </table>
          </div>
        )}
        {rows.length > PAGE_SIZES[0] && (
          <nav className="pager" aria-label="Halaman tabel">
            <span className="muted">{start + 1}–{start + shown.length} dari {rows.length}</span>
            <label className="check">
              per halaman
              <select value={pageSize} onChange={(e) => setPageSize(Number(e.target.value))}>
                {PAGE_SIZES.map((n) => <option key={n} value={n}>{n}</option>)}
              </select>
            </label>
            <div className="pager-buttons">
              <button type="button" className="ghost" onClick={() => goTo(1)} disabled={current === 1} aria-label="Halaman pertama">«</button>
              <button type="button" className="ghost" onClick={() => goTo(current - 1)} disabled={current === 1} aria-label="Sebelumnya">‹</button>
              <span>hal. {current} / {pages}</span>
              <button type="button" className="ghost" onClick={() => goTo(current + 1)} disabled={current === pages} aria-label="Berikutnya">›</button>
              <button type="button" className="ghost" onClick={() => goTo(pages)} disabled={current === pages} aria-label="Halaman terakhir">»</button>
            </div>
          </nav>
        )}
        <p className="hint">
          Klik baris untuk melihat chart 120 bar terakhir dengan area entry, stop loss, dan target. Stop ({meta.stopLabel}) ada di kolom
          detail setup. Harga sinyal = close bar sinyal. Histori = hasil setup yang sama pada saham itu selama {years} tahun data
          (biaya idx_stock). Bukan rekomendasi; uji dulu di tab Backtest.
        </p>
      </section>

      {data.errors.length > 0 && (
        <details className="table-view">
          <summary>{data.errors.length} saham tidak bisa dipindai</summary>
          <ul className="errors">{data.errors.map((e) => <li key={e.ticker}><strong>{e.ticker}</strong> {e.error}</li>)}</ul>
        </details>
      )}
    </div>
  );
}
