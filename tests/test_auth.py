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
                db.execute("INSERT INTO sessions(token,csrf,created,seen) VALUES(?,?,?,?)", original)
        store.available()
        assert store.session(token) == original[1]
        assert client.get("/docs").status_code == 200


def _old_schema_store(tmp_path):
    """A database written before kept sessions/passkeys: the three original tables only."""
    import sqlite3
    import pyotp
    from app.auth import AuthStore, HASHER
    directory = tmp_path / "old-auth"
    directory.mkdir(mode=0o700)
    secret = pyotp.random_base32()
    path = directory / "owner.sqlite3"
    with closing(sqlite3.connect(path)) as db:
        with db:
            db.execute("CREATE TABLE owner(id INTEGER PRIMARY KEY CHECK(id=1), password TEXT NOT NULL, secret TEXT NOT NULL, counter INTEGER NOT NULL)")
            db.execute("CREATE TABLE sessions(token TEXT PRIMARY KEY, csrf TEXT NOT NULL, created REAL NOT NULL, seen REAL NOT NULL)")
            db.execute("CREATE TABLE throttle(id INTEGER PRIMARY KEY CHECK(id=1), failures INTEGER NOT NULL, blocked REAL NOT NULL)")
            db.execute("INSERT INTO throttle VALUES(1,0,0)")
            db.execute("INSERT INTO owner VALUES(1,?,?,?)", (HASHER.hash("synthetic owner password"), secret, 100))
    path.chmod(0o600)
    return AuthStore(directory), secret


def _columns(path, table):
    import sqlite3
    with closing(sqlite3.connect(path)) as db:
        return [row[1] for row in db.execute(f"PRAGMA table_info({table})")]


def _tables(path):
    import sqlite3
    with closing(sqlite3.connect(path)) as db:
        return {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _markers(path):
    import sqlite3
    with closing(sqlite3.connect(path)) as db:
        return sorted(row[0] for row in db.execute("SELECT token FROM kept_sessions"))


SESSION_COLUMNS = ["token", "csrf", "created", "seen"]


def _binding(secret):
    """What a passkey row stores to bind it to the owner's authenticator secret."""
    import hashlib
    return hashlib.sha256(secret.encode()).hexdigest()


def test_old_schema_database_still_signs_in_and_writes_nothing_on_read(tmp_path):
    import sqlite3
    import time
    import pyotp
    store, secret = _old_schema_store(tmp_path)
    # A session written by the previous release (four columns, no kept).
    old_token = "A" * 43
    from app.auth import digest
    with closing(sqlite3.connect(store.path)) as db:
        with db:
            db.execute("INSERT INTO sessions VALUES(?,?,?,?)", (digest(old_token), "B" * 43, time.time(), time.time()))
    before = store.path.read_bytes()
    store.available()
    assert store.path.read_bytes() == before
    assert "kept_sessions" not in _tables(store.path)
    info = store.session_info(old_token, now=time.time())
    assert info.csrf == "B" * 43 and info.kept is False
    assert "kept_sessions" not in _tables(store.path)
    before = store.path.read_bytes()
    store.available()
    assert store.path.read_bytes() == before
    token = store.login("synthetic owner password", pyotp.TOTP(secret).at(3030), keep=True, now=3030)
    assert token
    # The sessions table keeps exactly the previous release's four columns.
    assert _columns(store.path, "sessions") == SESSION_COLUMNS
    assert _columns(store.path, "kept_sessions") == ["token"]
    store.available()
    assert store.session_info(token, now=3031).kept is True


def test_recover_deletes_passkeys_challenges_and_sessions(owner):
    import sqlite3
    import time
    import pyotp
    store, secret = owner
    token = store.login("synthetic owner password", pyotp.TOTP(secret).at(3030), keep=True, now=3030)
    assert store.session_info(token, now=3031).kept is True
    with closing(sqlite3.connect(store.path)) as db:
        with db:
            db.execute("INSERT INTO passkeys VALUES(?,?,?,?,?)", ("a" * 22, b"key", 0, time.time(), _binding(secret)))
            db.execute("INSERT INTO passkey_challenges VALUES(?,?,?)", ("d" * 64, "login", time.time() + 300))
    store.available()
    store.enroll("another synthetic owner password", secret, pyotp.TOTP(secret).at(4000), now=4000, recover=True)
    with closing(sqlite3.connect(store.path)) as db:
        for table in ("sessions", "kept_sessions", "passkeys", "passkey_challenges"):
            assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone() == (0,), table
    store.available()
    assert store.session(token, now=4001) is None


def test_enroll_creates_current_schema(owner):
    store, _ = owner
    assert _columns(store.path, "sessions") == SESSION_COLUMNS
    assert _columns(store.path, "kept_sessions") == ["token"]
    assert _columns(store.path, "passkeys") == ["id", "public_key", "sign_count", "created", "owner_binding"]
    assert _columns(store.path, "passkey_challenges") == ["digest", "purpose", "expires"]


@pytest.mark.parametrize("column,bad_value", [
    ("public_key", b""), ("public_key", "text"),
    ("sign_count", -1), ("sign_count", 1.5),
    ("created", -1), ("created", float("inf")),
    ("id", "short"), ("id", "not base64url!" * 2),
    ("owner_binding", "short"), ("owner_binding", "A" * 64), ("owner_binding", "g" * 64),
    ("owner_binding", "a" * 65), ("owner_binding", 5), ("owner_binding", b"\x00" * 32),
])
def test_invalid_passkey_row_makes_store_unavailable(owner, column, bad_value):
    import sqlite3
    from app.auth import AuthUnavailable
    store, secret = owner
    good = {"id": "a" * 22, "public_key": b"key", "sign_count": 0, "created": 5.0, "owner_binding": _binding(secret)}
    good[column] = bad_value
    with closing(sqlite3.connect(store.path)) as db:
        with db:
            db.execute("INSERT INTO passkeys VALUES(?,?,?,?,?)", tuple(good[name] for name in ("id", "public_key", "sign_count", "created", "owner_binding")))
    with pytest.raises(AuthUnavailable):
        store.available()


def test_valid_passkey_row_is_available(owner):
    import sqlite3
    store, secret = owner
    with closing(sqlite3.connect(store.path)) as db:
        with db:
            db.execute("INSERT INTO passkeys VALUES(?,?,?,?,?)", ("a" * 22, b"key", 3, 5.0, _binding(secret)))
    store.available()
    # A well-formed binding for another secret is not unsafe state, only a revoked passkey.
    with closing(sqlite3.connect(store.path)) as db:
        with db:
            db.execute("INSERT INTO passkeys VALUES(?,?,?,?,?)", ("b" * 22, b"key", 3, 5.0, "0" * 64))
    store.available()
    assert [key_id for key_id, _ in store.passkeys()] == ["a" * 22]


@pytest.mark.parametrize("marker", [None, 5, 1.5, b"\x00" * 32, "short", "A" * 64, "g" * 64, "a" * 63, "a" * 65])
def test_malformed_kept_marker_makes_store_unavailable(owner, marker):
    import sqlite3
    import pyotp
    from app.auth import AuthUnavailable
    store, secret = owner
    assert store.login("synthetic owner password", pyotp.TOTP(secret).at(3030), keep=True, now=3030)
    with closing(sqlite3.connect(store.path)) as db:
        with db:
            db.execute("INSERT INTO kept_sessions(token) VALUES(?)", (marker,))
    with pytest.raises(AuthUnavailable):
        store.available()


def test_orphan_kept_marker_is_ignored_and_never_keeps_another_session(owner):
    """A marker whose session is gone (the previous release deleted it) is tolerated, not trusted."""
    import sqlite3
    from app.auth import digest
    store, secret = owner
    orphan = digest("O" * 43)
    with closing(sqlite3.connect(store.path)) as db:
        with db:
            db.execute("INSERT INTO kept_sessions(token) VALUES(?)", (orphan,))
    before = store.path.read_bytes()
    store.available()
    assert store.path.read_bytes() == before  # tolerating it is a read: nothing is written
    assert store.session("O" * 43, now=3031) is None
    token = _store_login(store, secret, 3030)
    assert store.session_info(token, now=3031).kept is False
    assert store.session(token, now=3031 + 1800) is None  # the unkept limits apply
    # The next sign-in (a write) clears orphans away.
    assert orphan not in _markers(store.path)
    store.available()


def test_ending_a_session_removes_its_kept_marker(owner):
    from app.auth import digest
    store, secret = owner
    first = _store_login(store, secret, 3030, keep=True)
    second = _store_login(store, secret, 3060, keep=True)
    third = _store_login(store, secret, 3090, keep=True)
    assert _markers(store.path) == sorted(digest(t) for t in (first, second, third))
    store.logout(first)
    assert _markers(store.path) == sorted(digest(t) for t in (second, third))
    store.logout_everywhere()
    assert _markers(store.path) == []
    store.available()


def test_previous_release_still_works_on_an_upgraded_database(owner):
    """Rolling back must not lock the owner out: the previous release's own SQL still runs.

    The statements below are the previous release's, verbatim: its positional
    session insert, its expiry purge (which knows nothing of kept sessions),
    its session read and touch, its validation read, and its recovery delete.
    """
    import sqlite3
    import time
    import pyotp
    from app.auth import digest
    from tests.passkey_fake import ORIGIN, FakeAuthenticator
    store, secret = owner
    kept = _store_login(store, secret, 3030, keep=True)
    other = _store_login(store, secret, 3060, keep=True)
    options = store.begin_registration("synthetic owner password", pyotp.TOTP(secret).at(3090), origin=ORIGIN, now=3090)
    assert store.finish_registration(FakeAuthenticator().register(options), origin=ORIGIN, now=3091) is True
    assert len(_markers(store.path)) == 2
    now = time.time()
    old_token = "Q" * 43
    with closing(sqlite3.connect(store.path)) as db:
        with db:
            # Previous release: sign-in (purge, then positional insert).
            db.execute("DELETE FROM sessions WHERE seen<=? OR created<=?", (now - 1800, now - 43200))
            db.execute("INSERT INTO sessions VALUES(?,?,?,?)", (digest(old_token), "C" * 43, now, now))
        # Previous release: available() reads exactly four columns.
        assert db.execute("SELECT token,csrf,created,seen FROM sessions").fetchall() == [(digest(old_token), "C" * 43, now, now)]
        with db:
            # Previous release: session() read and touch.
            assert db.execute("SELECT token,csrf,created,seen FROM sessions WHERE token=?", (digest(old_token),)).fetchone()
            db.execute("UPDATE sessions SET seen=? WHERE token=?", (now, digest(old_token)))
    # Both kept sessions were purged by the old release; their markers are orphans now.
    assert _markers(store.path) == sorted([digest(kept), digest(other)])
    # This build, rolled forward again, still works on the same file.
    store.available()
    assert store.session(kept, now=now) is None and store.session(other, now=now) is None
    info = store.session_info(old_token, now=now)
    assert info.csrf == "C" * 43 and info.kept is False
    assert store.session(old_token, now=now + 1800) is None  # an old-release session is unkept
    with closing(sqlite3.connect(store.path)) as db:
        with db:
            # Previous release: recovery deletes every session (and nothing else).
            db.execute("INSERT INTO sessions VALUES(?,?,?,?)", (digest(old_token), "C" * 43, now, now))
            db.execute("DELETE FROM sessions")
    store.available()
    assert store.session(old_token, now=now) is None
    token = _store_login(store, secret, 4000, keep=True)
    assert store.session_info(token, now=4001).kept is True
    assert _markers(store.path) == [digest(token)]  # orphans cleared by the write
    assert _columns(store.path, "sessions") == SESSION_COLUMNS
    store.available()


def _previous_release_recover(store, password, *, now=None):
    """The previous release's recovery, verbatim: new owner row, sessions deleted, nothing else.

    It knows nothing of passkeys, so their rows survive it. Returns the new secret.
    """
    import sqlite3
    import time
    import pyotp
    from app.auth import HASHER
    secret = pyotp.random_base32()
    now = time.time() if now is None else now
    with closing(sqlite3.connect(store.path)) as db:
        with db:
            db.execute("INSERT OR REPLACE INTO owner VALUES(1,?,?,?)", (HASHER.hash(password), secret, int(now // 30)))
            db.execute("DELETE FROM sessions")
    return secret


def _passkey_rows(store):
    import sqlite3
    with closing(sqlite3.connect(store.path)) as db:
        return db.execute("SELECT id FROM passkeys").fetchall()


def test_previous_release_recover_revokes_passkeys_after_roll_forward(owner):
    """Register, roll back and recover (a suspect credential), roll forward: Touch ID is revoked."""
    import hashlib
    import sqlite3
    import time
    import pyotp
    from app.main import app
    from tests.passkey_fake import ORIGIN, FakeAuthenticator, b64url
    store, secret = owner
    fake = FakeAuthenticator()
    options = store.begin_registration("synthetic owner password", pyotp.TOTP(secret).at(3030), origin=ORIGIN, now=3030)
    assert store.finish_registration(fake.register(options), origin=ORIGIN, now=3031) is True
    assert [key_id for key_id, _ in store.passkeys()] == [fake.id]
    new_secret = _previous_release_recover(store, "another synthetic owner password")
    # This build again. Reads hide the revoked passkey and write nothing.
    store.available()
    before = store.path.read_bytes()
    assert store.passkeys() == []
    assert store.path.read_bytes() == before
    assert _passkey_rows(store) == [(fake.id,)]
    with TestClient(app, base_url="https://localhost:8443", follow_redirects=False) as client:
        page = client.get("/login")
        assert page.status_code == 200 and "Sign in with Touch ID" not in page.text
    # A sign-in answer from the revoked passkey, to a live challenge, is refused and counted.
    now = time.time()
    challenge = bytes(range(32))
    with closing(sqlite3.connect(store.path)) as db:
        with db:
            db.execute("INSERT INTO passkey_challenges VALUES(?,?,?)", (hashlib.sha256(challenge).hexdigest(), "login", now + 200))
    answer = fake.assertion('{"rpId": "localhost", "challenge": "' + b64url(challenge) + '"}')
    assert store.finish_login(answer, origin=ORIGIN, keep=True, now=now) is None
    with closing(sqlite3.connect(store.path)) as db:
        assert db.execute("SELECT failures FROM throttle").fetchone() == (1,)
        assert db.execute("SELECT COUNT(*) FROM sessions").fetchone() == (0,)
    # The same write transaction removed the stale row.
    assert _passkey_rows(store) == []
    store.available()
    # The recovered credentials still sign in.
    assert store.login("another synthetic owner password", pyotp.TOTP(new_secret).at(now + 30), now=now + 30)


def test_passkey_registered_under_the_current_secret_survives_and_signs_in(owner):
    import pyotp
    from tests.passkey_fake import ORIGIN, FakeAuthenticator
    store, secret = owner
    stale = FakeAuthenticator()
    options = store.begin_registration("synthetic owner password", pyotp.TOTP(secret).at(3030), origin=ORIGIN, now=3030)
    assert store.finish_registration(stale.register(options), origin=ORIGIN, now=3031) is True
    new_secret = _previous_release_recover(store, "synthetic owner password", now=3060)
    # The recovery spent its code window; set up again in the next one.
    now = 3090
    fresh = FakeAuthenticator()
    options = store.begin_registration("synthetic owner password", pyotp.TOTP(new_secret).at(now), origin=ORIGIN, now=now)
    assert options and stale.id not in options  # the revoked passkey is not excluded as "already set up"
    assert store.finish_registration(fresh.register(options), origin=ORIGIN, now=now + 1) is True
    # Setup (a write) removed the stale one; only the new passkey is stored and listed.
    assert _passkey_rows(store) == [(fresh.id,)]
    assert [key_id for key_id, _ in store.passkeys()] == [fresh.id]
    login = store.begin_login(origin=ORIGIN, now=now + 2)
    assert store.finish_login(fresh.assertion(login), origin=ORIGIN, keep=False, now=now + 3)
    store.available()


def test_webauthn_is_importable():
    import webauthn
    assert webauthn.generate_registration_options


KEPT_AGE = 34560000
OWN_ORIGIN = {"Origin": "https://localhost:8443"}


def _store_login(store, secret, at, *, keep=False):
    import pyotp
    return store.login("synthetic owner password", pyotp.TOTP(secret).at(at), keep=keep, now=at)


def test_kept_session_outlives_idle_and_absolute_limits(owner):
    store, secret = owner
    token = _store_login(store, secret, 3030, keep=True)
    assert store.session_info(token, now=3030 + 3600).kept is True
    # 13 h after sign-in, and 13 h of silence: neither limit applies.
    info = store.session_info(token, now=3030 + 13 * 3600)
    assert info.kept is True and store.session(token, now=3030 + 26 * 3600) == info.csrf
    assert store.session_info(token, now=3030 + 400 * 86400)


def test_kept_session_survives_a_clock_that_steps_backwards(owner):
    store, secret = owner
    token = _store_login(store, secret, 3030, keep=True)
    assert store.session(token, now=9000)
    # The clock steps back to before the session was created. `seen` must not
    # be written below `created`, or available() would fail every request.
    assert store.session(token, now=1000)
    store.available()
    assert store.session(token, now=1001)


def test_unkept_session_limits_unchanged(owner):
    store, secret = owner
    token = _store_login(store, secret, 3030)
    assert store.session_info(token, now=3031).kept is False
    assert store.session(token, now=3031 + 1799)
    assert store.session(token, now=3031 + 1799 + 1800) is None  # idle
    token = _store_login(store, secret, 4000)
    for now in range(4001, 46000, 1000):
        assert store.session(token, now=now)
    assert store.session(token, now=4000 + 43200) is None  # absolute


def test_login_purge_keeps_kept_sessions(owner):
    import sqlite3
    store, secret = owner
    kept = _store_login(store, secret, 3030, keep=True)
    unkept = _store_login(store, secret, 3060)
    later = 3060 + 50000
    assert _store_login(store, secret, later, keep=False)
    from app.auth import digest
    with closing(sqlite3.connect(store.path)) as db:
        rows = db.execute("SELECT token FROM sessions ORDER BY created").fetchall()
    # The expired unkept one was purged, the kept one was not; the newest is the third sign-in.
    assert len(rows) == 2 and rows[0] == (digest(kept),)
    assert _markers(store.path) == [digest(kept)]
    assert store.session(unkept, now=later) is None
    assert store.session(kept, now=later)


def _web_login(client, secret, *, keep):
    import pyotp
    page = client.get("/login")
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    data = {"password": "synthetic owner password", "code": pyotp.TOTP(secret).now(), "csrf_token": csrf}
    if keep:
        data["keep"] = "1"
    return client.post("/login", data=data, headers=OWN_ORIGIN)


def _cookie_headers(response, name):
    return [value for value in response.headers.get_list("set-cookie") if value.startswith(name + "=")]


def test_login_form_offers_keep_checked_by_default(owner):
    from app.main import app
    with TestClient(app, base_url="https://localhost:8443", follow_redirects=False) as client:
        page = client.get("/login").text
    assert re.search(r'<input[^>]*type="checkbox"[^>]*name="keep"[^>]*value="1"[^>]*checked', page)
    assert "Keep me signed in on this Mac" in page


def test_kept_login_sets_long_cookie_and_renews_it(owner, monkeypatch):
    import time
    from app import auth
    from app.main import app
    store, secret = owner
    with TestClient(app, base_url="https://localhost:8443", follow_redirects=False) as client:
        response = _web_login(client, secret, keep=True)
        assert response.status_code == 303
        (cookie,) = _cookie_headers(response, "__Host-oneos")
        assert f"Max-Age={KEPT_AGE}" in cookie
        for flag in ("Secure", "HttpOnly", "SameSite=strict", "Path=/"):
            assert flag in cookie
        (issued,) = _cookie_headers(response, "__Host-oneos-issued")
        assert f"Max-Age={KEPT_AGE}" in issued and "Secure" in issued and "HttpOnly" in issued
        token = client.cookies.get("__Host-oneos")
        assert store.session_info(token).kept is True
        # Same day: no re-issue.
        assert _cookie_headers(client.get("/docs"), "__Host-oneos") == []
        real = time.time()
        monkeypatch.setattr(auth.time, "time", lambda: real + 86401)
        response = client.get("/docs")
        assert response.status_code == 200
        (renewed,) = _cookie_headers(response, "__Host-oneos")
        assert renewed.startswith(f"__Host-oneos={token};") and f"Max-Age={KEPT_AGE}" in renewed
        assert len(_cookie_headers(response, "__Host-oneos-issued")) == 1
        # Renewed once; the next request that day does not renew again.
        assert _cookie_headers(client.get("/docs"), "__Host-oneos") == []
        # Past the idle and absolute limits the kept session still works.
        monkeypatch.setattr(auth.time, "time", lambda: real + 3 * 86400)
        assert client.get("/docs").status_code == 200


def test_kept_session_without_a_usable_issue_stamp_is_renewed(owner):
    from app.main import app
    store, secret = owner
    with TestClient(app, base_url="https://localhost:8443", follow_redirects=False) as client:
        assert _web_login(client, secret, keep=True).status_code == 303
        for stamp in ("", "garbage", "-5", "9" * 20):
            client.cookies.set("__Host-oneos-issued", stamp, domain="localhost.local")
            assert len(_cookie_headers(client.get("/docs"), "__Host-oneos")) == 1, stamp


def test_unkept_login_gets_short_cookie_and_is_never_renewed_or_promoted(owner, monkeypatch):
    import time
    from app import auth
    from app.main import app
    store, secret = owner
    with TestClient(app, base_url="https://localhost:8443", follow_redirects=False) as client:
        response = _web_login(client, secret, keep=False)
        assert response.status_code == 303
        (cookie,) = _cookie_headers(response, "__Host-oneos")
        assert "Max-Age=43200" in cookie
        token = client.cookies.get("__Host-oneos")
        # A client forging every hint it controls changes nothing for an unkept session.
        client.cookies.set("__Host-oneos-issued", "0", domain="localhost.local")
        client.cookies.set("__Host-oneos-kept", "1", domain="localhost.local")
        real = time.time()
        monkeypatch.setattr(auth.time, "time", lambda: real + 1000)
        response = client.get("/docs", headers={"X-OneOS-Keep": "1"})
        assert response.status_code == 200
        assert _cookie_headers(response, "__Host-oneos") == []
        assert store.session_info(token, now=real + 1000).kept is False
        monkeypatch.setattr(auth.time, "time", lambda: real + 50000)
        assert client.get("/docs").status_code == 303  # absolute limit still applies
        assert store.session_info(token, now=real + 50000) is None


def _cleared(response, name):
    return any("Max-Age=0" in value for value in _cookie_headers(response, name))


def test_logout_and_logout_everywhere_end_kept_sessions(owner):
    from app.main import app
    store, secret = owner
    # Store-side sign-ins first: their small `now` values must precede the web sign-in's TOTP step.
    first = _store_login(store, secret, 3030, keep=True)
    second = _store_login(store, secret, 3060, keep=True)
    store.logout(first)
    assert store.session(first) is None and store.session(second)
    with TestClient(app, base_url="https://localhost:8443", follow_redirects=False) as client:
        assert _web_login(client, secret, keep=True).status_code == 303
        token = client.cookies.get("__Host-oneos")
        csrf = store.session(token)
        # Sign out ends only this session and clears both cookies.
        response = client.post("/logout", headers=dict(OWN_ORIGIN, **{"X-CSRF-Token": csrf}))
        assert response.status_code == 303 and response.headers["location"] == "/login"
        assert _cleared(response, "__Host-oneos") and _cleared(response, "__Host-oneos-issued")
        assert store.session(token) is None
        assert store.session(second)
        # Sign out everywhere, from a session that is itself kept, ends every session.
        client.cookies.set("__Host-oneos", second, domain="localhost.local")
        response = client.post("/logout/everywhere", headers=dict(OWN_ORIGIN, **{"X-CSRF-Token": store.session(second), "HX-Request": "true"}))
        assert response.status_code == 303
        assert response.headers["location"] == "/login" and response.headers["HX-Redirect"] == "/login"
        assert _cleared(response, "__Host-oneos") and _cleared(response, "__Host-oneos-issued")
        assert store.session(second) is None
        assert client.get("/docs").status_code == 303
    store.logout_everywhere()  # idempotent on an empty table
    store.available()


def test_logout_everywhere_requires_csrf(owner):
    from app.main import app
    store, secret = owner
    token = _store_login(store, secret, 3030, keep=True)
    other = _store_login(store, secret, 3060, keep=True)
    csrf = store.session(token)
    with TestClient(app, base_url="https://localhost:8443", follow_redirects=False) as client:
        client.cookies.set("__Host-oneos", token, domain="localhost.local")
        # No token, wrong token, wrong origin, no origin: refused, nothing deleted.
        assert client.post("/logout/everywhere", headers=OWN_ORIGIN).status_code == 403
        assert client.post("/logout/everywhere", headers=dict(OWN_ORIGIN, **{"X-CSRF-Token": "x" * 43})).status_code == 403
        assert client.post("/logout/everywhere", headers={"Origin": "https://evil.invalid", "X-CSRF-Token": csrf}).status_code == 403
        assert client.post("/logout/everywhere", headers={"X-CSRF-Token": csrf}).status_code == 403
        # A GET is not the sign-out action.
        assert client.get("/logout/everywhere").status_code != 303
        assert store.session(token) and store.session(other)
    with TestClient(app, base_url="https://localhost:8443", follow_redirects=False) as anonymous:
        response = anonymous.post("/logout/everywhere", headers=dict(OWN_ORIGIN, **{"X-CSRF-Token": csrf}))
        assert response.status_code == 401
    assert store.session(other)
