import sqlite3
import time

import pytest
from fastapi.routing import APIRoute

import auth
from conftest import PASSWORD, USER, login, make_client
from main import PUBLIC_PATHS, app

COOKIE = "__Host-bt_session"


def raw_db():
    return sqlite3.connect(auth.db_path())


# ---------- every endpoint is protected ----------

def _api_routes(routes):
    for r in routes:
        if isinstance(r, APIRoute):
            yield from ((m, r.path) for m in r.methods)
        elif hasattr(r, "original_router"):  # FastAPI >= 0.16x wraps included routers
            yield from _api_routes(r.original_router.routes)


def test_every_api_route_requires_a_session():
    c = make_client()
    routes = list(_api_routes(app.routes))
    assert len(routes) >= 7
    for method, path in routes:
        if path in PUBLIC_PATHS:
            continue
        r = c.request(method, path)
        assert r.status_code == 401, f"{method} {path} is reachable without login"


def test_unauthenticated_upload_is_rejected():
    r = make_client().post("/api/backtest", data={"strategy": "buy_hold", "market": "crypto"},
                           files={"file": ("a.csv", b"x" * 100_000, "text/csv")})
    assert r.status_code == 401


def test_forged_cookie_is_rejected():
    c = make_client()
    c.cookies.set(COOKIE, "a" * 43)
    assert c.get("/api/auth/me").status_code == 401


def test_docs_and_schema_are_not_exposed():
    c = make_client()
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert c.get(path).status_code == 404


# ---------- login ----------

def test_login_sets_hardened_cookie_and_returns_csrf():
    c = make_client()
    r = login(c)
    assert r.status_code == 200
    assert r.json()["username"] == USER and len(r.json()["csrf_token"]) >= 40
    sc = r.headers["set-cookie"].lower()
    assert sc.startswith(COOKIE.lower() + "=")
    for attr in ("httponly", "secure", "samesite=strict", "path=/"):
        assert attr in sc
    assert "domain=" not in sc
    assert c.get("/api/auth/me").json()["username"] == USER


def test_wrong_password_and_unknown_user_look_identical():
    c = make_client()
    a = c.post("/api/auth/login", json={"username": USER, "password": "Wrong-Password-123"})
    b = c.post("/api/auth/login", json={"username": "nobody", "password": "Wrong-Password-123"})
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json() == {"detail": "invalid username or password"}
    assert "set-cookie" not in a.headers and "set-cookie" not in b.headers


def test_username_is_case_insensitive_but_password_is_not():
    assert login(make_client(), username=USER.upper()).status_code == 200
    assert login(make_client(), password=PASSWORD.swapcase()).status_code == 401


@pytest.mark.parametrize("kwargs", [
    dict(data={"username": USER, "password": PASSWORD}),     # form-encoded: what a cross-site <form> can send
    dict(json={"username": USER}),
    dict(json={"username": USER, "password": "x" * 1000}),
    dict(json={"username": "", "password": PASSWORD}),
])
def test_login_rejects_malformed_requests(kwargs):
    assert make_client().post("/api/auth/login", **kwargs).status_code == 422


def test_session_token_is_stored_hashed():
    c = make_client()
    login(c)
    token = c.cookies.get(COOKIE)
    with raw_db() as conn:
        stored = [r[0] for r in conn.execute("SELECT token_hash FROM sessions")]
    assert token not in stored and auth._token_hash(token) in stored


def test_new_login_issues_a_new_token_and_drops_the_old_one():
    c = make_client()
    login(c)
    first = c.cookies.get(COOKIE)
    login(c)
    second = c.cookies.get(COOKIE)
    assert first != second
    stale = make_client()
    stale.cookies.set(COOKIE, first)
    assert stale.get("/api/auth/me").status_code == 401


# ---------- CSRF ----------

def test_post_without_or_with_wrong_csrf_token_is_forbidden():
    c = make_client()
    login(c)
    good = c.headers.pop("X-CSRF-Token")
    assert c.get("/api/strategies").status_code == 200          # safe method: no token needed
    assert c.post("/api/backtest", data={"strategy": "buy_hold", "market": "crypto"}).status_code == 403
    r = c.post("/api/backtest", data={"strategy": "buy_hold", "market": "crypto"}, headers={"X-CSRF-Token": "nope"})
    assert r.status_code == 403
    r = c.post("/api/backtest", data={"strategy": "buy_hold", "market": "crypto"}, headers={"X-CSRF-Token": good})
    assert r.status_code == 200


def test_csrf_token_of_another_session_is_rejected():
    a, b = make_client(), make_client()
    login(a)
    login(b)
    a.headers["X-CSRF-Token"] = b.headers["X-CSRF-Token"]
    assert a.post("/api/auth/logout").status_code == 403


# ---------- logout & expiry ----------

def test_logout_invalidates_the_session_server_side():
    c = make_client()
    login(c)
    token = c.cookies.get(COOKIE)
    r = c.post("/api/auth/logout")
    assert r.status_code == 204 and COOKIE in r.headers["set-cookie"]
    replay = make_client()
    replay.cookies.set(COOKIE, token)  # an attacker who copied the cookie earlier
    assert replay.get("/api/auth/me").status_code == 401


@pytest.mark.parametrize("column, age_s", [("last_seen", 2 * 3600), ("created_at", 13 * 3600)])
def test_idle_and_absolute_expiry(column, age_s):
    c = make_client()
    login(c)
    th = auth._token_hash(c.cookies.get(COOKIE))
    with raw_db() as conn:
        conn.execute(f"UPDATE sessions SET {column} = ? WHERE token_hash = ?", (time.time() - age_s, th))
    r = c.get("/api/auth/me")
    assert r.status_code == 401 and r.json()["detail"] == "session expired"
    with raw_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM sessions WHERE token_hash = ?", (th,)).fetchone()[0] == 0


def test_password_change_signs_out_everywhere():
    auth.create_user("rotating", "Initial-Passphrase-42")
    c = make_client()
    assert login(c, "rotating", "Initial-Passphrase-42").status_code == 200
    auth.set_password("rotating", "Rotated-Passphrase-43")
    assert c.get("/api/auth/me").status_code == 401
    assert login(make_client(), "rotating", "Initial-Passphrase-42").status_code == 401
    assert login(make_client(), "rotating", "Rotated-Passphrase-43").status_code == 200


# ---------- brute force ----------

def test_username_is_locked_after_repeated_failures_even_for_the_right_password():
    c = make_client()
    for _ in range(auth.MAX_FAILS_PER_USER):
        assert c.post("/api/auth/login", json={"username": USER, "password": "Guess-Guess-123"}).status_code == 401
    r = login(c)
    assert r.status_code == 429 and int(r.headers["retry-after"]) > 0
    auth.clear_login_failures()
    assert login(c).status_code == 200


def test_ip_is_throttled_across_usernames():
    c = make_client()
    for i in range(auth.MAX_FAILS_PER_IP):
        c.post("/api/auth/login", json={"username": f"spray{i}", "password": "Guess-Guess-123"})
    assert login(c).status_code == 429


def test_unknown_usernames_are_throttled_too():
    # Otherwise "429 vs 401" would reveal which usernames exist.
    c = make_client()
    for _ in range(auth.MAX_FAILS_PER_USER):
        c.post("/api/auth/login", json={"username": "ghost", "password": "Guess-Guess-123"})
    assert c.post("/api/auth/login", json={"username": "ghost", "password": "x"}).status_code == 429


# ---------- passwords ----------

def test_password_hash_roundtrip_and_salting():
    h1, h2 = auth.hash_password("Some-Passphrase-77"), auth.hash_password("Some-Passphrase-77")
    assert h1 != h2 and h1.startswith("scrypt$32768$8$1$")
    assert auth.verify_password("Some-Passphrase-77", h1)
    assert not auth.verify_password("some-passphrase-77", h1)
    assert not auth.verify_password("Some-Passphrase-77", "garbage")


def test_weak_hash_is_upgraded_on_login():
    import base64, hashlib, secrets
    auth.create_user("legacy", "Older-Passphrase-11")
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(b"Older-Passphrase-11", salt=salt, n=1024, r=8, p=1, dklen=32)
    old = f"scrypt$1024$8$1${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"
    with raw_db() as conn:
        conn.execute("UPDATE users SET pw_hash = ? WHERE username = 'legacy'", (old,))
    assert login(make_client(), "legacy", "Older-Passphrase-11").status_code == 200
    with raw_db() as conn:
        new = conn.execute("SELECT pw_hash FROM users WHERE username = 'legacy'").fetchone()[0]
    assert new.startswith("scrypt$32768$") and not auth.needs_rehash(new)


@pytest.mark.parametrize("pw", [
    "Short-1a",                    # too short
    "alllowercaseletters",         # one character class
    "Password123456!",             # common word + digits/symbols
    "Rahasia2024!!!!",             # common Indonesian word, and 4 repeated '!'
    "My-tester-Account-1",         # contains the username
    "Aaaaa-Bbbbbb-Cc-1",           # repeated character
])
def test_weak_passwords_are_rejected(pw):
    assert auth.password_problems(pw, "tester")
    with pytest.raises(ValueError, match="password"):
        auth._check_new_credentials("tester", pw)


def test_generated_passwords_pass_the_policy():
    for _ in range(20):
        pw = auth.generate_password()
        assert len(pw) == 24 and not auth.password_problems(pw)


@pytest.mark.parametrize("name", ["ab", "with space", "x" * 33, "semi;colon", "admin'--"])
def test_invalid_usernames_are_rejected(name):
    with pytest.raises(ValueError, match="username"):
        auth.create_user(name, "Valid-Passphrase-99")


def test_duplicate_user_is_rejected_case_insensitively():
    with pytest.raises(ValueError, match="already exists"):
        auth.create_user(USER.upper(), "Another-Passphrase-1")


# ---------- headers ----------

def test_api_responses_are_not_cacheable():
    c = make_client()
    assert c.get("/api/strategies").headers["cache-control"] == "no-store"   # 401 path
    r = login(c)
    assert r.headers["cache-control"] == "no-store"
    assert c.get("/api/auth/me").headers["cache-control"] == "no-store"
