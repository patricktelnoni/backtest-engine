import { useEffect, useState } from "react";
import { getScreener } from "./api";
import { pct, price } from "./format";
import type { ScreenerResponse, ScreenerRow } from "./types";

const REASON: Record<NonNullable<ScreenerRow["exit_reason"]>, string> = { stop: "stop", rsi: "RSI", max_hold: "max hold" };

function when(iso: string): string {
  return new Date(iso).toLocaleString("id-ID", { dateStyle: "medium", timeStyle: "short" });
}

function Status({ r }: { r: ScreenerRow }) {
  if (r.status === "active") return <span className="badge active">aktif · {r.bars_since_signal} bar</span>;
  return <span className="badge exited">keluar {r.exit_date} ({REASON[r.exit_reason!] ?? r.exit_reason})</span>;
}

function Signed({ v }: { v: number }) {
  return <span className={v > 0 ? "pos" : v < 0 ? "neg" : ""}>{pct(v)}</span>;
}

function Row({ r }: { r: ScreenerRow }) {
  const h = r.history;
  const hist = h.n_trades
    ? `${h.n_trades} trade · win ${h.win_rate === null ? "n/a" : `${(h.win_rate * 100).toFixed(0)}%`} · avg ${h.avg_trade_return === null ? "n/a" : pct(h.avg_trade_return, 1)}`
    : "belum ada trade";
  const d = r.divergence;
  return (
    <tr>
      <td><strong>{r.ticker}</strong><span className="sub">{r.last_date}</span></td>
      <td><Status r={r} /></td>
      <td>{r.signal_date}<span className="sub">{price(r.signal_price)}</span></td>
      <td>{price(r.last_close)}<span className="sub"><Signed v={r.return_since_signal} /> sejak sinyal</span></td>
      <td>{price(r.stop)}<span className="sub"><Signed v={r.stop_distance} /> ke stop</span></td>
      <td>{r.rsi === null ? "n/a" : r.rsi.toFixed(1)}</td>
      <td className="divergence">
        <span>{d.first_date} @ {price(d.first_low)}</span>
        <span>{d.second_date} @ {price(d.second_low)}</span>
      </td>
      <td className="muted">{hist}</td>
    </tr>
  );
}

export default function Screener() {
  // undefined = loading; null = the job has not produced a result yet
  const [data, setData] = useState<ScreenerResponse | null | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  const [activeOnly, setActiveOnly] = useState(true);

  useEffect(() => {
    getScreener().then(setData).catch((e: Error) => {
      setError(e.message);
      setData(null);
    });
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

  const rows = activeOnly ? data.results.filter((r) => r.status === "active") : data.results;
  const active = data.results.filter((r) => r.status === "active").length;
  const params = Object.entries(data.params).map(([k, v]) => `${k}=${String(v)}`).join(" · ");

  return (
    <div className="page">
      <div className="result-head">
        <h2>Screener bullish divergence</h2>
        <span className="pill">{data.market}</span>
        <span className="pill">{data.scanned}/{data.universe} saham</span>
        <span className="pill">diperbarui {when(data.generated_at)}</span>
        {data.errors.length > 0 && <span className="pill warn">{data.errors.length} gagal</span>}
      </div>
      <p className="hint">
        Sinyal pada bar penutupan terakhir: harga membuat swing low lebih rendah sementara RSI membuat low lebih tinggi.
        Harga sinyal = close bar sinyal; stop = low divergence. Parameter: {params}.
      </p>

      <section className="card">
        <header className="card-head">
          <div>
            <h3>{active} setup aktif · {data.results.length - active} baru keluar (≤ {data.recent_bars} bar)</h3>
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
                  <th>Saham</th><th>Status</th><th>Sinyal</th><th>Harga terakhir</th><th>Stop</th><th>RSI</th>
                  <th>Divergence (low 1 → low 2)</th><th>Histori setup ini</th>
                </tr>
              </thead>
              <tbody>{rows.map((r) => <Row key={r.ticker} r={r} />)}</tbody>
            </table>
          </div>
        )}
        <p className="hint">
          Histori = hasil setup yang sama pada saham itu selama {Math.round((data.results[0]?.history.bars ?? 0) / 245)} tahun data
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
