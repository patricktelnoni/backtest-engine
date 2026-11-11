import os
import tempfile

# Must be set before `main`/`auth` are imported by the test modules: a throwaway database per test run.
_tmp = tempfile.mkdtemp(prefix="bt-auth-")
os.environ["BT_AUTH_DB"] = os.path.join(_tmp, "auth.db")
os.environ["BT_SCREENER_DIR"] = os.path.join(_tmp, "screener")
os.environ.pop("BT_COOKIE_SECURE", None)  # test the production (Secure, __Host-) cookie

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import auth  # noqa: E402
from main import app  # noqa: E402

USER, PASSWORD = "tester", "Correct-Horse-Battery-9"


def make_client() -> TestClient:
    # https: the session cookie is Secure, so the client only sends it back over https.
    return TestClient(app, base_url="https://testserver")


def login(c: TestClient, username=USER, password=PASSWORD):
    r = c.post("/api/auth/login", json={"username": username, "password": password})
    if r.status_code == 200:
        c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    return r


@pytest.fixture(scope="session", autouse=True)
def _user():
    auth.create_user(USER, PASSWORD)


@pytest.fixture(autouse=True)
def _reset_throttle():
    auth.clear_login_failures()
    yield
