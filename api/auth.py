"""Authentication for the Backtest API.

Design (no third-party crypto deps; everything below is Python stdlib):
- Passwords: scrypt (memory-hard), 16-byte random salt per user, constant-time comparison. Hashes carry their
  parameters, so they can be strengthened later (old hashes are upgraded on the next successful login).
- Sessions: 256-bit random token in a cookie (HttpOnly, Secure, SameSite=Strict, __Host- prefix). Only the
  SHA-256 of the token is stored server-side, so a leaked database cannot be replayed as cookies. Sessions
  expire after an idle timeout and an absolute lifetime, and logout deletes them on the server.
- CSRF: every state-changing request must echo the session's CSRF token in X-CSRF-Token (on top of SameSite).
- Brute force: failed logins are throttled per client IP and per username (sliding window); unknown users
  cost the same scrypt work as known ones and get the same error, so usernames cannot be enumerated.
- No self-registration: users are created by an operator with `python manage.py` (see that file).

Configuration (environment variables):
  BT_AUTH_DB               SQLite file (default: api/auth.db). Keep it outside the web root, mode 600.
  BT_COOKIE_SECURE         "1" (default) = Secure + __Host- cookie (HTTPS). "0" only for local http dev.
  BT_SESSION_IDLE_MINUTES  default 60
  BT_SESSION_MAX_HOURS     default 12
"""
from __future__ import annotations

import base64
import functools
import hashlib
import hmac
import logging
import os
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

log = logging.getLogger("backtest.auth")

# ---------- settings ----------

LOGIN_WINDOW_S = 15 * 60
MAX_FAILS_PER_USER = 5      # per username (across all IPs) within the window
MAX_FAILS_PER_IP = 20       # per client IP (across all usernames) within the window
CSRF_HEADER = "X-CSRF-Token"
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def db_path() -> str:
    return os.environ.get("BT_AUTH_DB") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "auth.db")


def cookie_secure() -> bool:
    return os.environ.get("BT_COOKIE_SECURE", "1") != "0"


def cookie_name() -> str:
    # __Host- makes the browser refuse the cookie unless it is Secure, Path=/ and has no Domain.
    return "__Host-bt_session" if cookie_secure() else "bt_session"


def session_idle_s() -> float:
    return float(os.environ.get("BT_SESSION_IDLE_MINUTES", 60)) * 60


def session_max_s() -> float:
    return float(os.environ.get("BT_SESSION_MAX_HOURS", 12)) * 3600


# ---------- password hashing (scrypt) ----------

SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**15, 8, 1   # ~32 MiB and roughly 50-150 ms per hash
_MAXMEM = 128 * 1024 * 1024


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, maxmem=_MAXMEM, dklen=32)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64(salt)}${_b64(dk)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, expected = stored.split("$")
        if algo != "scrypt":
            return False
        salt_b, expected_b = base64.b64decode(salt), base64.b64decode(expected)
        got = hashlib.scrypt(password.encode(), salt=salt_b, n=int(n), r=int(r), p=int(p),
                             maxmem=_MAXMEM, dklen=len(expected_b))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got, expected_b)


def needs_rehash(stored: str) -> bool:
    return not stored.startswith(f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}$")


@functools.cache
def _dummy_hash() -> str:
    """Verified against when the username does not exist, so both cases take the same time."""
    return hash_password(secrets.token_urlsafe(16))


# ---------- password policy ----------

PASSWORD_MIN_LEN = 14
PASSWORD_MAX_LEN = 128
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
_COMMON = {
    "password", "passw0rd", "qwerty", "qwertyuiop", "asdfgh", "asdfghjkl", "zxcvbnm", "abcdef", "abcdefgh",
    "admin", "administrator", "root", "user", "login", "welcome", "letmein", "changeme", "secret", "default",
    "iloveyou", "monkey", "dragon", "master", "sunshine", "princess", "football", "superman", "batman",
    "backtest", "backtestengine", "trading", "trader", "saham", "crypto", "bitcoin",
    "rahasia", "sayang", "indonesia", "jakarta", "bismillah", "katasandi", "masuk",
}


def password_problems(password: str, username: str = "") -> list[str]:
    """Empty list = acceptable. Policy: >= 14 chars, 3 of 4 character classes, not based on a common word
    or the username, and no character repeated 4+ times in a row."""
    probs = []
    if len(password) < PASSWORD_MIN_LEN:
        probs.append(f"must be at least {PASSWORD_MIN_LEN} characters")
    if len(password) > PASSWORD_MAX_LEN:
        probs.append(f"must be at most {PASSWORD_MAX_LEN} characters")
    classes = sum(bool(re.search(p, password)) for p in (r"[a-z]", r"[A-Z]", r"[0-9]", r"[^A-Za-z0-9]"))
    if classes < 3:
        probs.append("must mix at least 3 of: lowercase, uppercase, digits, symbols")
    if re.search(r"(.)\1{3,}", password):
        probs.append("must not repeat the same character 4+ times in a row")
    lowered = password.lower()
    letters = re.sub(r"[^a-z]", "", lowered)
    if username and username.lower() in lowered:
        probs.append("must not contain the username")
    if lowered in _COMMON or letters in _COMMON:
        probs.append("is too common (a dictionary word with digits/symbols added)")
    return probs


def generate_password(length: int = 24) -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789-_.!@#%+="
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(length))
        if not password_problems(pw):
            return pw


# ---------- storage ----------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    pw_hash TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    csrf TEXT NOT NULL,
    created_at REAL NOT NULL,
    last_seen REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS login_failures (
    ts REAL NOT NULL,
    ip TEXT NOT NULL,
    username TEXT NOT NULL COLLATE NOCASE
);
CREATE INDEX IF NOT EXISTS login_failures_ts ON login_failures(ts);
"""
_initialized: set[str] = set()


@contextmanager
def db():
    """One short-lived connection per use; commits on success, rolls back on error."""
    path = db_path()
    old = os.umask(0o077)  # a newly created database file is readable by the service user only
    try:
        conn = sqlite3.connect(path, timeout=10)
    finally:
        os.umask(old)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        if path not in _initialized:
            conn.executescript(_SCHEMA)
            _initialized.add(path)
        with conn:
            yield conn
    finally:
        conn.close()


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ---------- user management (used by manage.py) ----------

def _check_new_credentials(username: str, password: str) -> None:
    if not USERNAME_RE.match(username):
        raise ValueError("username must be 3-32 characters: letters, digits, '_', '.', '-'")
    if probs := password_problems(password, username):
        raise ValueError("password " + "; ".join(probs))


def create_user(username: str, password: str) -> None:
    _check_new_credentials(username, password)
    try:
        with db() as conn:
            conn.execute("INSERT INTO users (username, pw_hash, created_at) VALUES (?, ?, ?)",
                         (username, hash_password(password), time.time()))
    except sqlite3.IntegrityError:
        raise ValueError(f"user {username!r} already exists") from None


def set_password(username: str, password: str) -> None:
    """Also signs the user out everywhere."""
    _check_new_credentials(username, password)
    with db() as conn:
        row = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
        if row is None:
            raise ValueError(f"no such user {username!r}")
        conn.execute("UPDATE users SET pw_hash = ? WHERE id = ?", (hash_password(password), row["id"]))
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (row["id"],))
        conn.execute("DELETE FROM login_failures WHERE username = ?", (username,))


def delete_user(username: str) -> None:
    with db() as conn:
        if conn.execute("DELETE FROM users WHERE username = ?", (username,)).rowcount == 0:
            raise ValueError(f"no such user {username!r}")


def revoke_sessions(username: str) -> int:
    with db() as conn:
        return conn.execute("DELETE FROM sessions WHERE user_id = (SELECT id FROM users WHERE username = ?)",
                            (username,)).rowcount


def list_users() -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT u.username, u.created_at, COUNT(s.token_hash) AS sessions FROM users u "
            "LEFT JOIN sessions s ON s.user_id = u.id GROUP BY u.id ORDER BY u.username"
        ).fetchall()
    return [dict(r) for r in rows]


def clear_login_failures() -> None:
    with db() as conn:
        conn.execute("DELETE FROM login_failures")


# ---------- request handling ----------

@dataclass(frozen=True)
class Session:
    user_id: int
    username: str
    csrf: str
    token_hash: str


def _client_ip(request: Request) -> str:
    # Behind nginx, uvicorn --proxy-headers --forwarded-allow-ips 127.0.0.1 puts the real client IP here.
    return request.client.host if request.client else "unknown"


def _unauthorized(detail: str = "not authenticated") -> HTTPException:
    return HTTPException(401, detail)


def current_session(request: Request) -> Session:
    """FastAPI dependency: a valid, unexpired session; for unsafe methods also a matching CSRF token."""
    token = request.cookies.get(cookie_name())
    if not token or len(token) > 128:
        raise _unauthorized()
    now, th = time.time(), _token_hash(token)
    with db() as conn:
        row = conn.execute(
            "SELECT s.csrf, s.created_at, s.last_seen, u.id, u.username FROM sessions s "
            "JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?", (th,)
        ).fetchone()
        expired = row is not None and (now - row["created_at"] > session_max_s()
                                       or now - row["last_seen"] > session_idle_s())
        if expired:
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (th,))
        elif row is not None and now - row["last_seen"] > 30:
            conn.execute("UPDATE sessions SET last_seen = ? WHERE token_hash = ?", (now, th))
    if row is None or expired:
        raise _unauthorized("session expired" if expired else "not authenticated")
    if request.method in UNSAFE_METHODS:
        sent = request.headers.get(CSRF_HEADER, "")
        if not hmac.compare_digest(sent.encode(), row["csrf"].encode()):
            raise HTTPException(403, "missing or invalid CSRF token")
    return Session(user_id=row["id"], username=row["username"], csrf=row["csrf"], token_hash=th)


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(cookie_name(), token, max_age=int(session_max_s()), path="/",
                        secure=cookie_secure(), httponly=True, samesite="strict")


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(cookie_name(), path="/", secure=cookie_secure(), httponly=True, samesite="strict")


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response):
    # Login is a JSON request (not a form), so a cross-site page cannot submit it without a CORS preflight.
    ip, username, now = _client_ip(request), body.username.strip(), time.time()
    with db() as conn:
        conn.execute("DELETE FROM login_failures WHERE ts < ?", (now - LOGIN_WINDOW_S,))
        by_ip = conn.execute("SELECT COUNT(*) FROM login_failures WHERE ip = ?", (ip,)).fetchone()[0]
        by_user = conn.execute("SELECT COUNT(*) FROM login_failures WHERE username = ?", (username,)).fetchone()[0]
        oldest = conn.execute("SELECT MIN(ts) FROM login_failures WHERE ip = ? OR username = ?",
                              (ip, username)).fetchone()[0]
    if by_ip >= MAX_FAILS_PER_IP or by_user >= MAX_FAILS_PER_USER:
        retry = max(1, int((oldest or now) + LOGIN_WINDOW_S - now))
        log.warning("login throttled ip=%s user=%r", ip, username[:64])
        raise HTTPException(429, "too many failed login attempts, try again later",
                            headers={"Retry-After": str(retry)})

    with db() as conn:
        user = conn.execute("SELECT id, username, pw_hash FROM users WHERE username = ?", (username,)).fetchone()
    ok = verify_password(body.password, user["pw_hash"] if user else _dummy_hash())
    if not (user and ok):
        with db() as conn:
            conn.execute("INSERT INTO login_failures (ts, ip, username) VALUES (?, ?, ?)", (now, ip, username))
        log.warning("login failed ip=%s user=%r", ip, username[:64])
        raise HTTPException(401, "invalid username or password")

    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    old = request.cookies.get(cookie_name())
    with db() as conn:
        conn.execute("DELETE FROM login_failures WHERE username = ?", (username,))
        conn.execute("DELETE FROM sessions WHERE created_at < ? OR last_seen < ?",
                     (now - session_max_s(), now - session_idle_s()))
        if old:  # never keep a pre-login session alive (session fixation)
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(old),))
        if needs_rehash(user["pw_hash"]):
            conn.execute("UPDATE users SET pw_hash = ? WHERE id = ?", (hash_password(body.password), user["id"]))
        conn.execute("INSERT INTO sessions (token_hash, user_id, csrf, created_at, last_seen) VALUES (?, ?, ?, ?, ?)",
                     (_token_hash(token), user["id"], csrf, now, now))
    _set_session_cookie(response, token)
    log.info("login ok ip=%s user=%r", ip, user["username"])
    return {"username": user["username"], "csrf_token": csrf}


@router.post("/logout", status_code=204)
def logout(session: Session = Depends(current_session)):
    with db() as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (session.token_hash,))
    resp = Response(status_code=204)
    _clear_session_cookie(resp)
    return resp


@router.get("/me")
def me(session: Session = Depends(current_session)):
    return {"username": session.username, "csrf_token": session.csrf}
