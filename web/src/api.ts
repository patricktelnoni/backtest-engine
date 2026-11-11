import type { BacktestResponse, CompareResponse, MarketInfo, Params, StrategyInfo } from "./types";

// ---------- session ----------
// The session itself is an HttpOnly cookie (JS cannot read it). The CSRF token lives only in memory and is
// sent on every POST; a reload fetches it again from /api/auth/me.
let csrfToken = "";
let onUnauthorized: () => void = () => {};

/** Called when any API call answers 401 (session expired / logged out elsewhere). */
export function setUnauthorizedHandler(fn: () => void) {
  onUnauthorized = fn;
}

export interface Me {
  username: string;
  csrf_token: string;
}

export class LoginError extends Error {
  constructor(public status: number, public retryAfterS: number | null) {
    super(`login failed (${status})`);
  }
}

function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  if (init.method && init.method !== "GET") headers.set("X-CSRF-Token", csrfToken);
  return fetch(path, { ...init, headers, credentials: "same-origin", cache: "no-store" });
}

/** The logged-in user, or null when there is no valid session. */
export async function getMe(): Promise<Me | null> {
  const res = await apiFetch("/api/auth/me");
  if (res.status === 401) return null;
  const me = await check<Me>(res);
  csrfToken = me.csrf_token;
  return me;
}

export async function login(username: string, password: string): Promise<Me> {
  const res = await apiFetch("/api/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) {
    const ra = Number(res.headers.get("Retry-After"));
    throw new LoginError(res.status, Number.isFinite(ra) && ra > 0 ? ra : null);
  }
  const me: Me = await res.json();
  csrfToken = me.csrf_token;
  return me;
}

export async function logout(): Promise<void> {
  try {
    await apiFetch("/api/auth/logout", { method: "POST" });
  } finally {
    csrfToken = "";
  }
}

async function check<T>(res: Response): Promise<T> {
  if (res.status === 401) {
    csrfToken = "";
    onUnauthorized();
    throw new Error("Sesi Anda berakhir. Silakan masuk lagi.");
  }
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (typeof body.detail === "string") msg = body.detail;
    } catch {
      /* non-JSON error body */
    }
    throw new Error(msg);
  }
  return res.json();
}

export const getStrategies = () => apiFetch("/api/strategies").then(check<StrategyInfo[]>);
export const getMarkets = () => apiFetch("/api/markets").then(check<MarketInfo[]>);

export interface RunSpec {
  strategy: string;
  params: Params;
}

export interface RunInput {
  market: string;
  initialCash: number;
  start?: string;
  end?: string;
  costs: Partial<Record<"commission_bps" | "slippage_bps" | "borrow_bps_annual" | "swap_bps_daily", number>>;
  file: File | null;
}

function baseForm(i: RunInput): FormData {
  const f = new FormData();
  f.set("market", i.market);
  f.set("initial_cash", String(i.initialCash));
  if (i.start) f.set("start", i.start);
  if (i.end) f.set("end", i.end);
  for (const [k, v] of Object.entries(i.costs)) if (v !== undefined) f.set(k, String(v));
  if (i.file) f.set("file", i.file);
  return f;
}

export function runBacktest(i: RunInput, run: RunSpec, signal?: AbortSignal): Promise<BacktestResponse> {
  const f = baseForm(i);
  f.set("strategy", run.strategy);
  f.set("params", JSON.stringify(run.params));
  return apiFetch("/api/backtest", { method: "POST", body: f, signal }).then(check<BacktestResponse>);
}

export function runCompare(i: RunInput, runs: RunSpec[], signal?: AbortSignal): Promise<CompareResponse> {
  const f = baseForm(i);
  f.set("runs", JSON.stringify(runs));
  return apiFetch("/api/compare", { method: "POST", body: f, signal }).then(check<CompareResponse>);
}
