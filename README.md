# Backtest Platform

- `engine/` — library `btengine` (pure Python) + CLI (`btengine --help`)
- `api/`    — FastAPI wrapper (port 8001)
- `web/`    — React + Vite UI (port 5173, proxies `/api` to the API)

## Run

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e "engine[dev]" -r api/requirements-dev.txt

# once: create a login (there is no public sign-up)
cd api && BT_COOKIE_SECURE=0 python manage.py add admin --generate && cd ..

# terminal 1  (BT_COOKIE_SECURE=0 only because local dev is plain http)
cd api && BT_COOKIE_SECURE=0 uvicorn main:app --reload --port 8001
# production: deploy/systemd/backtest-api.service (one uvicorn process; see "Resource usage" below)

# terminal 2
cd web && npm install && npm run dev      # http://localhost:5173
```

API on another port: `API_URL=http://127.0.0.1:9000 npm run dev`.

## Test

```bash
(cd engine && pytest)     # engine: 12 tests
(cd api && pytest)        # HTTP layer + auth: 74 tests
(cd web && npm run build) # type-check + bundle
```

## CI/CD (GitHub Actions)

[.github/workflows/ci-cd.yml](.github/workflows/ci-cd.yml) runs on every push and pull request: engine + API tests on
Python 3.11 and 3.12, and the web type-check + build. On a push to `main` that passed, the `deploy` job copies
the built frontend to the VPS over SSH and runs [deploy/deploy.sh](deploy/deploy.sh) there (checkout of the pushed
commit, `pip install`, config files into systemd/nginx, restart, health check). The VPS needs no Node.

One-time VPS setup for the deploy user (replace `deploy` with the name you use):

```bash
sudo adduser --disabled-password --gecos "" deploy
sudo chown -R deploy:deploy /opt/backtest-platform          # git checkout, .venv and web/dist live here
sudo -u deploy mkdir -p /home/deploy/.ssh && sudo -u deploy tee -a /home/deploy/.ssh/authorized_keys < deploy_key.pub
sudo visudo -cf deploy/sudoers-backtest-deploy && sudo install -m 440 deploy/sudoers-backtest-deploy /etc/sudoers.d/backtest-deploy
```

Repository secrets: `VPS_HOST`, `VPS_USER`, `VPS_SSH_KEY` (private key matching `deploy_key.pub`), optionally
`VPS_PORT` and `VPS_KNOWN_HOSTS` (`ssh-keyscan -p 22 your-host`, pins the server's host key). Create a
`production` environment in the repository settings if you want manual approval before each deploy.

## Screener (IDX bullish divergence)

A scheduled job, not a request handler: [api/screener.py](api/screener.py) downloads the missing daily bars for
every symbol in [api/idx_universe.txt](api/idx_universe.txt) (Yahoo Finance, incremental, cached as CSV), runs
the `bullish_divergence` setup on each, and writes one JSON file. `GET /api/screener` serves that file (one
`stat()` per request); the **Screener** tab in the UI shows open setups, recently closed ones, the two
divergence lows, the stop, and how the same setup did historically on that symbol.

```bash
cd api && python screener.py                   # refresh data + scan (~1 min for 100 symbols, mostly network)
cd api && python screener.py --no-fetch -p oversold=35 -p max_hold=15
```

Production: `deploy/systemd/backtest-screener.timer` runs it Mon-Fri 16:30 WIB as the service user, with the
lowest CPU/I-O priority; `deploy.sh` installs and enables it, and starts the first run if there is no result yet.
Compute cost is ~1 ms per symbol; state lives in `BT_SCREENER_DIR` (default `api/screener_data`,
production `/var/lib/backtest/screener`). `BT_SCREENER_UNIVERSE` points at another symbol list.

## Authentication

Every `/api/*` endpoint except `POST /api/auth/login` needs a logged-in session. Implementation: [api/auth.py](api/auth.py).

- **Accounts** are created by an operator only: `python manage.py add|passwd|delete|revoke|list` (run in `api/`).
  Passwords: at least 14 characters, 3 of lowercase/uppercase/digits/symbols, not a common word, not containing
  the username. `--generate` creates a random 24-character password.
- **Password storage**: scrypt (N=2^15, r=8, p=1), random salt per user, constant-time comparison.
- **Sessions**: 256-bit random token in an `HttpOnly; Secure; SameSite=Strict` cookie named `__Host-bt_session`.
  The database holds only its SHA-256. Sessions end after 60 minutes idle or 12 hours, on logout, or when the password changes.
- **CSRF**: every POST must send the session's token in `X-CSRF-Token`. Login only accepts JSON.
- **Brute force**: 5 failed logins per username or 20 per IP within 15 minutes give `429` with `Retry-After`. Unknown
  usernames get the same error and the same hashing cost, so usernames cannot be enumerated.
- **Other**: API responses are `Cache-Control: no-store`. `/docs` and `/openapi.json` are off unless `BT_ENABLE_DOCS=1`.
  Failed logins are logged (`backtest.auth`, with IP).

| Env var | Default | |
| --- | --- | --- |
| `BT_AUTH_DB` | `api/auth.db` | SQLite file with users and sessions (created with mode 600) |
| `BT_COOKIE_SECURE` | `1` | `0` only for local plain-http dev |
| `BT_SESSION_IDLE_MINUTES` / `BT_SESSION_MAX_HOURS` | `60` / `12` | session lifetimes |

## Resource usage

The API is sized for a small shared VPS:

- **One uvicorn process** (`--workers 1`, ~90 MB RSS). CSV parsing, the backtests and JSON encoding run on an
  in-process thread pool, so the event loop keeps answering logins and catalog requests while a backtest runs.
  Each extra worker costs another ~90 MB of pandas/numpy; add one only if CPU is idle under load.
- **Bounded work in flight**: `BT_BACKTEST_THREADS` (default 2) threads compute, up to `BT_BACKTEST_QUEUE`
  (default 8) more requests wait, and anything beyond that is answered `503` with `Retry-After: 2` instead of
  queueing DataFrames in memory. nginx additionally rate-limits `/api/` per client IP.
- **Cheap responses**: results are encoded once with `orjson` (stdlib `json` if it is missing) straight from
  numpy arrays, chart series are rounded (equity 4 decimals, ratios 6), the strategy/market catalog is encoded
  at startup, and uploads are parsed from the spooled file reading only the date + OHLCV columns.
- **One session lookup per request** (the middleware's check is reused by the route) on a SQLite database in
  WAL mode with `synchronous=NORMAL`.
- **Static assets are pre-compressed** by `npm run build` (`scripts/precompress.mjs`) and served by nginx with
  `gzip_static on`, so nothing is re-gzipped per visitor. Fingerprinted assets are cached for a year.

| Env var | Default | |
| --- | --- | --- |
| `BT_BACKTEST_THREADS` | `2` | threads running CSV parsing + backtests + encoding |
| `BT_BACKTEST_QUEUE` | `8` | requests allowed to wait for a thread before `503` |
| `MALLOC_ARENA_MAX` (systemd unit) | `2` | caps glibc per-thread malloc arenas so RSS stays flat |

## UI

- **Single run**: one strategy → metrics, equity vs buy & hold, drawdown, and a per-trade table.
- **Compare**: click “+ Tambah strategi pembanding” (up to 6, same data/market/costs) → overlaid
  equity and drawdown curves and a side-by-side metrics table with the best value per row in bold.

## API

| Endpoint | Purpose |
| --- | --- |
| `POST /api/auth/login`, `POST /api/auth/logout`, `GET /api/auth/me` | session (see Authentication) |
| `GET /api/strategies`, `GET /api/markets` | catalog (strategy param JSON schemas, market cost defaults) |
| `POST /api/backtest` | one strategy; returns metrics, series, trades |
| `POST /api/compare` | `runs` = JSON list of `{strategy, params}`; returns per-run metrics + aligned series |

Both POSTs are multipart forms sharing `market`, `initial_cash`, `start`, `end`, the four cost overrides, and an optional CSV `file`.

## Notes

- Trade `return` includes entry costs and borrow/swap while held, but **not** the exit cost
  (the engine books it on the bar after the trade ends).

## Sample data

```bash
python scripts/fetch_sample_data.py          # -> data/*.csv (git-ignored), ~5 years of daily bars
python scripts/fetch_sample_data.py SPY BTCUSDT
```

Yahoo Finance (unofficial endpoint): SPY, AAPL (`us_stock`), BBCA.JK, TLKM.JK (`idx_stock`), EURUSD=X, USDIDR=X (`forex`).
Binance public klines: BTCUSDT, ETHUSDT (`crypto`). Stock prices are split/dividend-adjusted; the still-forming day is
dropped; Yahoo FX bars with a corrupt open get open := previous close. FX volume is always 0 (Yahoo has none).

## Strategies

| Name | Idea |
| --- | --- |
| `buy_hold` | always long |
| `sma_cross` | long while fast SMA > slow SMA (optional short) |
| `rsi_reversion` | long when RSI < oversold, exit at RSI >= exit_level |
| `donchian_breakout` | long on a close above the N-bar high, exit below the M-bar low |
| `bullish_divergence` | long when price makes a lower swing low but RSI makes a higher low; exit on RSI >= `exit_rsi`, after `max_hold` bars, or (with `use_stop`) a close below the divergence low |

`bullish_divergence` only acts on a swing low once it is confirmed, `pivot_right` bars later, so there is no look-ahead;
`engine/tests/test_engine.py` checks that signals are unchanged when future bars are removed.
