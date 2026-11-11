import { useState, type FormEvent } from "react";
import ThemeToggle from "./ThemeToggle";
import { login, LoginError, type Me } from "./api";

function message(e: unknown): string {
  if (e instanceof LoginError) {
    if (e.status === 401) return "Username atau password salah.";
    if (e.status === 429) {
      const min = e.retryAfterS ? Math.ceil(e.retryAfterS / 60) : 15;
      return `Terlalu banyak percobaan gagal. Coba lagi dalam ${min} menit.`;
    }
    if (e.status === 422) return "Isi username dan password dengan benar.";
    return `Login gagal (kode ${e.status}). Coba lagi nanti.`;
  }
  return "Tidak bisa menghubungi server. Periksa koneksi Anda.";
}

export default function Login({ onLogin, notice }: { onLogin: (me: Me) => void; notice?: string | null }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      onLogin(await login(username.trim(), password));
    } catch (err) {
      setError(message(err));
    } finally {
      setPassword(""); // never keep the password in memory longer than the request
      setBusy(false);
    }
  };

  return (
    <div className="login-page">
      <div className="login-theme"><ThemeToggle /></div>
      <form className="login-card" onSubmit={submit} noValidate>
        <div className="brand">
          <svg width="32" height="32" viewBox="0 0 28 28" aria-hidden="true">
            <rect width="28" height="28" rx="8" fill="var(--accent)" />
            <path d="M5 19l5-6 4 3 8-9" fill="none" stroke="#fff" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
            <circle cx="22" cy="7" r="2.2" fill="#fff" />
          </svg>
          <span>Backtest Platform</span>
        </div>
        <div>
          <h2>Masuk</h2>
          <p className="muted login-sub">Akun dibuat oleh administrator. Tidak ada pendaftaran publik.</p>
        </div>

        {error ? <div className="error" role="alert">{error}</div>
          : notice && <div className="notice" role="status">{notice}</div>}

        <label>
          Username
          <input name="username" autoComplete="username" autoCapitalize="none" spellCheck={false} required
            maxLength={64} value={username} onChange={(e) => setUsername(e.target.value)} autoFocus />
        </label>
        <label>
          Password
          <span className="pw-field">
            <input name="password" type={show ? "text" : "password"} autoComplete="current-password" required
              maxLength={256} value={password} onChange={(e) => setPassword(e.target.value)} />
            <button type="button" className="pw-toggle" onClick={() => setShow((s) => !s)}
              aria-label={show ? "Sembunyikan password" : "Tampilkan password"} aria-pressed={show}>
              {show ? "Sembunyikan" : "Tampilkan"}
            </button>
          </span>
        </label>
        <button type="submit" disabled={busy || !username.trim() || !password}>
          {busy && <span className="spinner" aria-hidden="true" />}
          {busy ? "Memeriksa…" : "Masuk"}
        </button>
      </form>
    </div>
  );
}
