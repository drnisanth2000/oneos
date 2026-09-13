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


@pytest.mark.parametrize("blocked", [float("inf"), float("-inf"), -1, 1e300])
def test_invalid_throttle_deadline_is_unavailable(owner, blocked):
    from app.auth import AuthUnavailable
    store, _ = owner
    with store.connect() as db:
        db.execute("UPDATE throttle SET blocked=? WHERE id=1", (blocked,))
    with pytest.raises(AuthUnavailable):
        store.available()


def test_future_totp_counter_is_unavailable(owner, monkeypatch):
    from app import auth
    from app.auth import AuthUnavailable

    store, _ = owner
    monkeypatch.setattr(auth.time, "time", lambda: 3000)
    with store.connect() as db:
        db.execute("UPDATE owner SET counter=? WHERE id=1", (101,))
    with pytest.raises(AuthUnavailable):
        store.available()


def test_recovery_recreates_missing_throttle_and_allows_new_login(owner):
    import pyotp
    store, secret = owner
    old_token = store.login("synthetic owner password", pyotp.TOTP(secret).at(3030), now=3030)
    with store.connect() as db:
        db.execute("DELETE FROM throttle")
    new_secret = pyotp.random_base32()
    store.enroll("replacement owner password", new_secret,
                 pyotp.TOTP(new_secret).at(3060), now=3060, recover=True)
    store.available()
    assert store.session(old_token, now=3061) is None
    assert store.login("replacement owner password", pyotp.TOTP(new_secret).at(3090), now=3090)


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


def test_authenticated_maximum_lifecycle_review_passes_body_guard(owner):
    import pyotp
    from pathlib import Path
    from fastapi import FastAPI, Request
    from app.auth_web import AuthenticatedFormRoute, OwnerAuthMiddleware
    from app.lifecycle_receipts import canonical, EMPTY_SHA, REGISTRIES
    from app.outbox import LifecycleProposal

    record = dict(version=1, id="20260915T120000-" + "a" * 32, action="lifecycle_repair",
                  entity="sample", created="2026-09-15T12:00:00", baseline_head="a" * 40,
                  vault_identity="b" * 64, shape_sha256="c" * 64,
                  registry_sha256={key: "d" * 64 for key in REGISTRIES}, repair=None,
                  manifest=[dict(path=f"sample/{index:04d}-module/active",
                                 declaration="\u2603" * 255, parent_identity=[1, 2, 493],
                                 before_identity=None, after_mode=493, placeholder_sha256=EMPTY_SHA)
                            for index in range(1024)])
    proposal = LifecycleProposal(record["id"], Path(record["id"] + ".yaml"),
                                 record["action"], "sample", record["created"], canonical(record))
    reviewed = proposal.reviewed_fields  # Real schema validation at its manifest/declaration maximum.
    app = FastAPI()
    app.router.route_class = AuthenticatedFormRoute
    app.add_middleware(OwnerAuthMiddleware)

    @app.post("/review")
    async def review(request: Request):
        fields = await request.form()
        return {"reviewed": json.loads(fields["reviewed_values"])}

    store, secret = owner
    token = store.login("synthetic owner password", pyotp.TOTP(secret).now())
    with TestClient(app, base_url="https://localhost:8443") as client:
        client.cookies.set("__Host-oneos", token)
        response = client.post("/review", headers={"Origin": "https://localhost:8443"},
                               data={"csrf_token": store.session(token), "reviewed_values": json.dumps(reviewed)})
        assert response.status_code == 200, response.text
        assert response.json()["reviewed"] == reviewed
        assert client.post("/login", headers={"Origin": "https://localhost:8443"},
                           content=b"x" * 16385).status_code == 413
        assert client.post("/review", headers={"Origin": "https://localhost:8443"},
                           content=(b"x" * (1024 * 1024) for _ in range(40))).status_code == 413
    from app.main import app as production_app
    with TestClient(production_app, base_url="https://localhost:8443") as client:
        client.cookies.set("__Host-oneos", token)
        # The actual Form(...) route must reach proposal-id validation,
        # rather than fail in either body parser. Invalid id prevents mutation.
        response = client.post("/outbox/sample/approve", headers={"Origin": "https://localhost:8443"},
                               data={"csrf_token": store.session(token), "reviewed_values": json.dumps(reviewed),
                                     "id": "invalid", "review_sha256": "e" * 64})
        assert "E-INVALID" in response.text, response.text
        assert "E-REQUEST" not in response.text


@pytest.mark.parametrize("failure", ["io", "interrupt"])
def test_failed_first_enrollment_is_atomic_and_retryable(tmp_path, monkeypatch, failure):
    import sqlite3
    import pyotp
    from app.auth import AuthStore, AuthUnavailable

    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    store = AuthStore(directory)
    secret = pyotp.random_base32()
    original_connect = sqlite3.connect

    class InterruptedConnection(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            result = super().execute(sql, *args, **kwargs)
            if sql.startswith("INSERT OR REPLACE INTO owner"):
                if failure == "io":
                    raise sqlite3.OperationalError("synthetic write failure")
                raise KeyboardInterrupt("synthetic enrollment interruption")
            return result

    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, "connect", lambda *a, **kw: original_connect(*a, **kw, factory=InterruptedConnection))
        with pytest.raises((AuthUnavailable, KeyboardInterrupt)):
            store.enroll("synthetic owner password", secret, pyotp.TOTP(secret).at(3000), now=3000)
    assert not store.path.exists(), "failed enrollment exposed an incomplete live database"
    store.enroll("synthetic owner password", secret, pyotp.TOTP(secret).at(3000), now=3000)
    store.available()
    assert store.login("synthetic owner password", pyotp.TOTP(secret).at(3030), now=3030)


def test_published_enrollment_survives_process_exit_before_staging_cleanup(tmp_path):
    import os
    import subprocess
    import sys
    from app.auth import AuthStore

    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    script = '''
import os, sys, tempfile, pyotp
from pathlib import Path
from app.auth import AuthStore
tempfile.TemporaryDirectory.__exit__ = lambda *args: os._exit(77)
secret = pyotp.random_base32()
AuthStore(Path(sys.argv[1])).enroll("synthetic owner password", secret, pyotp.TOTP(secret).at(3000), now=3000)
'''
    result = subprocess.run([sys.executable, "-c", script, str(directory)],
                            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}, capture_output=True)
    assert result.returncode == 77, result.stderr
    AuthStore(directory).available()
    assert (directory / "owner.sqlite3").stat().st_nlink == 1


@pytest.mark.parametrize("suffix", ["-journal", "-wal", "-shm"])
def test_authentication_tolerates_sidecar_removed_during_safety_check(owner, monkeypatch, suffix):
    from pathlib import Path

    store, _ = owner
    sidecar = Path(str(store.path) + suffix)
    sidecar.write_bytes(b"")
    sidecar.chmod(0o600)
    original_lstat = Path.lstat

    def removed_before_lstat(path, *args, **kwargs):
        if path == sidecar:
            path.unlink(missing_ok=True)
        return original_lstat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", removed_before_lstat)
    store.available()
    assert not sidecar.exists()


def test_authentication_refuses_main_database_removed_during_safety_check(owner, monkeypatch):
    from pathlib import Path
    from app.auth import AuthUnavailable

    store, _ = owner
    original_lstat = Path.lstat

    def removed_before_lstat(path, *args, **kwargs):
        if path == store.path:
            path.unlink(missing_ok=True)
        return original_lstat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", removed_before_lstat)
    with pytest.raises(AuthUnavailable):
        store.available()


def test_authentication_refuses_database_replaced_while_opening(owner, monkeypatch, tmp_path):
    import sqlite3
    from app.auth import AuthUnavailable

    store, _ = owner
    replacement = tmp_path / "replacement.sqlite3"
    replacement.write_bytes(store.path.read_bytes())
    replacement.chmod(0o600)
    original_connect = sqlite3.connect

    def replace_then_connect(*args, **kwargs):
        replacement.replace(store.path)
        return original_connect(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", replace_then_connect)
    with pytest.raises(AuthUnavailable):
        store.available()


@pytest.mark.parametrize("suffix", ["-journal", "-wal", "-shm"])
def test_authentication_refuses_sidecar_permission_error(owner, monkeypatch, suffix):
    from pathlib import Path
    from app.auth import AuthUnavailable

    store, _ = owner
    sidecar = Path(str(store.path) + suffix)
    sidecar.write_bytes(b"")
    sidecar.chmod(0o600)
    original_lstat = Path.lstat

    def denied_lstat(path, *args, **kwargs):
        if path == sidecar:
            raise PermissionError("synthetic access denied")
        return original_lstat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", denied_lstat)
    with pytest.raises(AuthUnavailable):
        store.available()


@pytest.mark.parametrize("column,bad_value", [
    ("token", b"a" * 64), ("token", "invalid"),
    ("csrf", b"a" * 43), ("csrf", "short"), ("csrf", "é" * 43),
    ("created", "not-a-time"), ("seen", b"not-a-time"),
    ("created", float("inf")), ("seen", float("inf")),
    ("created", -1), ("seen", 1),
])
def test_corrupt_session_is_unavailable_without_mutation_and_restore_recovers(owner, column, bad_value):
    import sqlite3
    import pyotp
    from app.auth import AuthUnavailable
    from app.main import app

    store, secret = owner
    token = store.login("synthetic owner password", pyotp.TOTP(secret).now())
    with closing(sqlite3.connect(store.path)) as db:
        original = db.execute("SELECT token,csrf,created,seen FROM sessions").fetchone()
        with db:
            db.execute(f"UPDATE sessions SET {column}=?", (bad_value,))
        corrupted = db.execute("SELECT token,csrf,created,seen FROM sessions").fetchone()
    with pytest.raises(AuthUnavailable):
        store.available()
    if column != "token":
        with pytest.raises(AuthUnavailable):
            store.session(token)
    with TestClient(app, base_url="https://localhost:8443") as client:
        client.cookies.set("__Host-oneos", token)
        for method, path, kwargs in [
            ("GET", "/docs", {}),
            ("POST", "/logout", {"headers": {"Origin": "https://localhost:8443", "X-CSRF-Token": original[1]}}),
        ]:
            response = client.request(method, path, **kwargs)
            assert response.status_code == 503
            assert response.text == "Owner authentication unavailable. Run local setup or recovery."
        with closing(sqlite3.connect(store.path)) as db:
            assert db.execute("SELECT token,csrf,created,seen FROM sessions").fetchone() == corrupted
            with db:
                db.execute("DELETE FROM sessions")
                db.execute("INSERT INTO sessions VALUES(?,?,?,?)", original)
        store.available()
        assert store.session(token) == original[1]
        assert client.get("/docs").status_code == 200
