import { useEffect, useState } from "react";
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
      first_low_date: ["Low 1", String], first_low: ["harga", (v) => price(Number(v))], rsi_first: ["RSI", (v) => Number(v).toFixed(1)],
      second_low_date: ["Low 2", String], second_low: ["harga", (v) => price(Number(v))], rsi_second: ["RSI", (v) => Number(v).toFixed(1)],
    },
  },
  consolidated_ma: {
    title: "Consolidated MA",
    rule: "EMA 10/20/50/100/200 merapat (rentang ≤ tightness_pct dari close) selama ≥ min_squeeze_bars bar, lalu close menembus di atas EMA tertinggi dengan volume ≥ volume_mult × rata-rata. Stop = EMA terendah saat sinyal.",
    stopLabel: "EMA terendah",
    detail: {
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
  // Group pairs like (first_low_date, first_low, rsi_first) onto one line by rendering "label value" chips in order.
  return (
    <div className="detail">
      {Object.entries(r.detail).map(([k, v]) => {
        const [label, fmt] = spec[k] ?? [k, String];
        return <span key={k}><em>{label}</em> {v === null ? "n/a" : fmt(v)}</span>;
      })}
    </div>
  );
}

function Row({ r, setup }: { r: ScreenerRow; setup: string }) {
  const h = r.history;
  const hist = h.n_trades
    ? `${h.n_trades} trade · win ${h.win_rate === null ? "n/a" : `${(h.win_rate * 100).toFixed(0)}%`} · avg ${h.avg_trade_return === null ? "n/a" : pct(h.avg_trade_return, 1)}`
    : "belum ada trade";
  return (
    <tr>
      <td><strong>{r.ticker}</strong><span className="sub">{r.last_date}</span></td>
      <td><Status r={r} /></td>
      <td>{r.signal_date}<span className="sub">{price(r.signal_price)}</span></td>
      <td>{price(r.last_close)}<span className="sub"><Signed v={r.return_since_signal} /> sejak sinyal</span></td>
      <td>{price(r.stop)}<span className="sub"><Signed v={r.stop_distance} /> ke stop</span></td>
      <td className="left"><Detail r={r} setup={setup} /></td>
      <td className="muted">{hist}</td>
    </tr>
  );
}

export default function Screener() {
  // undefined = loading; null = the job has not produced a result yet
  const [data, setData] = useState<ScreenerResponse | null | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  const [setup, setSetup] = useState<string>("bullish_divergence");
  const [activeOnly, setActiveOnly] = useState(true);

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
                  <th>Saham</th><th>Status</th><th>Sinyal</th><th>Harga terakhir</th><th>Stop ({meta.stopLabel})</th>
                  <th className="left">Detail setup</th><th>Histori setup ini</th>
                </tr>
              </thead>
              <tbody>{rows.map((r) => <Row key={r.ticker} r={r} setup={setup} />)}</tbody>
            </table>
          </div>
        )}
        <p className="hint">
          Harga sinyal = close bar sinyal. Histori = hasil setup yang sama pada saham itu selama {years} tahun data
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
