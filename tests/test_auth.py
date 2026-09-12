"""Owner boundary tests use only temporary synthetic authentication state."""
import re
from contextlib import closing, contextmanager
from html.parser import HTMLParser
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient


@pytest.mark.parametrize("reviewed", [
    {"module": "02-work", "sub": ""},
    {"module": 'quoted" autofocus onfocus="bad()', "sub": "'>&<"},
])
def test_action_form_preserves_json_as_one_escaped_attribute(reviewed):
    """Trusted JSON Markup must not escape the hidden input's value boundary."""
    from pathlib import Path
    from types import SimpleNamespace
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    class Inputs(HTMLParser):
        def __init__(self):
            super().__init__()
            self.inputs = []

        def handle_starttag(self, tag, attrs):
            if tag == "input":
                self.inputs.append(dict(attrs))

    environment = Environment(
        loader=FileSystemLoader(Path(__file__).resolve().parents[1] / "templates"),
        autoescape=select_autoescape(),
    )
    template = environment.from_string(
        '{% from "_auth_forms.html" import action_form with context %}'
        '{{ action_form("review", {"reviewed_values": reviewed | tojson}, "/approve") }}'
    )
    parser = Inputs()
    parser.feed(template.render(
        reviewed=reviewed, request=SimpleNamespace(state=SimpleNamespace(csrf_token="synthetic"))
    ))
    hidden = next(item for item in parser.inputs if item.get("name") == "reviewed_values")
    assert set(hidden) == {"type", "name", "value"}
    assert hidden["type"] == "hidden"
    assert json.loads(hidden["value"]) == reviewed


@pytest.fixture(autouse=True)
def synthetic_app(make_vault, monkeypatch):
    from tests.conftest import entities_yaml
    monkeypatch.setenv("ONEOS_VAULT", str(make_vault(entities_yaml("sample"))))


def test_default_blocks_even_unknown_routes(monkeypatch):
    monkeypatch.delenv("ONEOS_AUTH_MODE", raising=False)
    monkeypatch.delenv("ONEOS_AUTH_STATE_DIR", raising=False)
    from app.main import app
    with TestClient(app, raise_server_exceptions=False) as client:
        for path in ("/", "/missing", "/outbox/unknown", "/docs", "/static/app.css"):
            assert client.get(path).status_code == 503


@pytest.fixture
def owner(tmp_path, monkeypatch):
    import pyotp
    from app.auth import AuthStore
    directory = tmp_path / "auth"
    directory.mkdir(mode=0o700)
    secret = pyotp.random_base32()
    store = AuthStore(directory)
    store.enroll("synthetic owner password", secret, pyotp.TOTP(secret).at(3000), now=3000)
    monkeypatch.setenv("ONEOS_AUTH_STATE_DIR", str(directory))
    monkeypatch.setenv("ONEOS_AUTH_MODE", "required")
    monkeypatch.setenv("ONEOS_ORIGIN", "https://localhost:8443")
    return store, secret


def test_hash_replay_concurrency_and_recovery(owner):
    import pyotp
    store, secret = owner
    code = pyotp.TOTP(secret).at(3030)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: store.login("synthetic owner password", code, now=3030), range(2)))
    tokens = [token for token in results if token]
    assert len(tokens) == 1
    assert store.session(tokens[0], now=3031)
    assert tokens[0].encode() not in store.path.read_bytes()
    assert b"$argon2id$" in store.path.read_bytes()
    new_secret = pyotp.random_base32()
    store.enroll("replacement owner password", new_secret, pyotp.TOTP(new_secret).at(3060), now=3060, recover=True)
    assert store.session(tokens[0], now=3061) is None
    assert store.login("synthetic owner password", pyotp.TOTP(secret).at(3090), now=3090) is None


def test_session_expiry_logout_and_throttle(owner):
    import pyotp
    store, secret = owner
    token = store.login("synthetic owner password", pyotp.TOTP(secret).at(3030), now=3030)
    assert store.session(token, now=3031)
    store.logout(token)
    assert store.session(token, now=3032) is None
    token = store.login("synthetic owner password", pyotp.TOTP(secret).at(3060), now=3060)
    assert store.session(token, now=5000) is None
    for _ in range(5):
        assert store.login("wrong", "000000", now=5100) is None
    assert store.login("synthetic owner password", pyotp.TOTP(secret).at(5100), now=5100) is None


def test_session_samples_the_clock_inside_its_write_transaction(owner, monkeypatch):
    """An in-flight request must not timestamp itself before transaction order is known."""
    import pyotp
    from app import auth
    from types import SimpleNamespace

    store, secret = owner
    token = store.login("synthetic owner password", pyotp.TOTP(secret).at(3030), now=3030)
    original_connect = store.connect
    transaction_active = False

    @contextmanager
    def tracked_connect():
        nonlocal transaction_active
        with original_connect() as database:
            transaction_active = True
            try:
                yield database
            finally:
                transaction_active = False

    def transaction_clock():
        assert transaction_active, "clock sampled before BEGIN IMMEDIATE"
        return 3031

    monkeypatch.setattr(store, "connect", tracked_connect)
    monkeypatch.setattr(auth, "time", SimpleNamespace(time=transaction_clock))
    assert store.session(token)


def test_browser_csrf_cookie_host_and_origin(owner):
    import pyotp
    from app.main import app
    store, secret = owner
    with TestClient(app, base_url="https://localhost:8443", follow_redirects=False) as client:
        assert client.get("/docs").status_code == 303
        response = client.get("/login")
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', response.text).group(1)
        data = {"password": "synthetic owner password", "code": pyotp.TOTP(secret).now(), "csrf_token": csrf}
        assert client.post("/login", data=data).status_code == 403
        assert client.post("/login", data=data, headers={"Origin": "https://evil.invalid"}).status_code == 403
        assert client.post("/login", data=dict(data, csrf_token="é"*43), headers={"Origin":"https://localhost:8443"}).status_code == 403
        response = client.post("/login", data=data, headers={"Origin": "https://localhost:8443"})
        assert response.status_code == 303
        assert "HttpOnly" in response.headers["set-cookie"]
        assert "Secure" in response.headers["set-cookie"]
        assert "SameSite=strict" in response.headers["set-cookie"]
        assert client.post("/logout", headers={"Origin": "https://localhost:8443"}).status_code == 403
        assert client.get("/docs", headers={"Host": "evil.invalid"}).status_code == 400
        session = store.session(client.cookies.get("__Host-oneos"))
        response = client.post("/logout", headers={"Origin": "https://localhost:8443", "X-CSRF-Token": session, "HX-Request": "true"})
        assert response.status_code == 303
        assert client.get("/docs", headers={"HX-Request": "true"}).headers["HX-Redirect"] == "/login"


def test_production_cannot_disable(monkeypatch):
    monkeypatch.setenv("ONEOS_DEPLOYMENT", "local")
    monkeypatch.setenv("ONEOS_AUTH_MODE", "disabled")
    from app.main import app
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/docs").status_code == 503


@pytest.mark.parametrize("damage", ["permissions", "symlink", "corrupt", "missing_throttle", "invalid_hash"])
def test_unsafe_state_is_generic_and_closed(owner, damage):
    import sqlite3
    from app.main import app
    store, _ = owner
    if damage == "permissions":
        store.path.chmod(0o644)
    elif damage == "symlink":
        real = store.path.with_suffix(".saved")
        store.path.rename(real)
        store.path.symlink_to(real)
    elif damage == "corrupt":
        store.path.write_bytes(b"broken sqlite")
    else:
        with closing(sqlite3.connect(store.path)) as db:
            with db:
                if damage == "missing_throttle":
                    db.execute("DELETE FROM throttle")
                else:
                    db.execute("UPDATE owner SET password='$argon2id$broken'")
    with TestClient(app, base_url="https://localhost:8443", raise_server_exceptions=False) as client:
        response = client.get("/login")
        assert response.status_code == 503
        assert "authentication unavailable" in response.text
        assert str(store.path) not in response.text


def test_all_registered_routes_require_owner(owner):
    from app.main import app
    with TestClient(app, base_url="https://localhost:8443", follow_redirects=False) as client:
        for route in app.routes:
            if route.path == "/healthz":
                continue
            path = re.sub(r"\{[^}]+\}", "synthetic", route.path)
            for method in getattr(route, "methods", {"GET"}):
                response = client.request(method, path)
                assert response.status_code in (303, 401), (method, path, response.status_code)
                assert response.headers["HX-Redirect"] == "/login"


def test_login_size_limit_and_oversized_password(owner):
    from app.main import app
    with TestClient(app, base_url="https://localhost:8443") as client:
        response = client.get("/login")
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', response.text).group(1)
        response = client.post("/login", content=b"x"*20000, headers={"Origin": "https://localhost:8443"})
        assert response.status_code == 413
        assert client.post("/login", data={"csrf_token":csrf,"password":"a"*1100,"code":"123456"}, headers={"Origin":"https://localhost:8443"}).status_code == 401


def test_absolute_expiry_even_when_active(owner):
    import pyotp
    store, secret = owner
    token = store.login("synthetic owner password", pyotp.TOTP(secret).at(3030), now=3030)
    for now in range(3031, 46000, 1000):
        assert store.session(token, now=now)
    assert store.session(token, now=46230) is None


def test_enrollment_requires_verified_code_and_existing_owner_is_not_overwritten(tmp_path):
    import pyotp
    from app.auth import AuthStore
    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    store = AuthStore(directory)
    secret = pyotp.random_base32()
    with pytest.raises(ValueError):
        store.enroll("long enough password", secret, "invalid")
    assert not store.path.exists()
    store.enroll("long enough password", secret, pyotp.TOTP(secret).now())
    before = store.path.read_bytes()
    with pytest.raises(FileExistsError):
        store.enroll("new enough password", secret, pyotp.TOTP(secret).now())
    assert store.path.read_bytes() == before


def test_health_alone_public_and_real_form_and_htmx_bodies(owner):
    import pyotp
    from fastapi import FastAPI, Request
    from app.auth_web import OwnerAuthMiddleware
    store, secret = owner
    app = FastAPI()
    app.add_middleware(OwnerAuthMiddleware)

    @app.get("/healthz")
    def health():
        return {"status": "ok"}

    @app.post("/mutate")
    async def mutate(request: Request):
        fields = await request.form()
        return {"value": fields["value"]}

    with TestClient(app, base_url="https://localhost:8443", follow_redirects=False) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        token = store.login("synthetic owner password", pyotp.TOTP(secret).now())
        client.cookies.set("__Host-oneos", token)
        csrf = store.session(token)
        for headers, data in [
            ({"Origin":"https://localhost:8443"}, {"csrf_token":csrf,"value":"ordinary"}),
            ({"Origin":"https://localhost:8443","HX-Request":"true","X-CSRF-Token":csrf}, {"value":"htmx"}),
        ]:
            response = client.post("/mutate", headers=headers, data=data)
            assert response.status_code == 200
            assert response.json() == {"value":data["value"]}
        store.path.chmod(0o644)
        assert client.get("/healthz").status_code == 200
        assert client.post("/mutate", data={"value":"forbidden"}).status_code == 503


def test_private_directory_uri_punctuation_is_literal(tmp_path):
    import pyotp
    from app.auth import AuthStore
    directory = tmp_path / "private?#state"
    directory.mkdir(mode=0o700)
    store = AuthStore(directory)
    secret = pyotp.random_base32()
    store.enroll("synthetic owner password", secret, pyotp.TOTP(secret).at(3000), now=3000)
    store.available()
    assert store.login("synthetic owner password", pyotp.TOTP(secret).at(3030), now=3030)
    assert not (tmp_path / "private").exists()
    assert store.path.stat().st_size > 0
