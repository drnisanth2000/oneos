"""Readiness must not leak paths or confuse stale backups with healthy backups."""
import importlib.util
import json
import time
import os
import subprocess
import sys

from fastapi import FastAPI
from fastapi.testclient import TestClient


def health_client():
    assert importlib.util.find_spec("app.deployment_health"), "deployment health routes are missing"
    from app.deployment_health import install_health
    app = FastAPI()
    install_health(app)
    return TestClient(app)


def test_process_health_does_not_require_or_reveal_vault(monkeypatch):
    monkeypatch.delenv("ONEOS_VAULT", raising=False)
    r = health_client().get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
    assert r.headers["cache-control"] == "no-store"


def test_readiness_reports_missing_vault_without_environment_leak(monkeypatch, tmp_path):
    private_path = tmp_path / "private-missing-vault"
    monkeypatch.setenv("ONEOS_VAULT", str(private_path))
    monkeypatch.delenv("ONEOS_BACKUP_STATUS_FILE", raising=False)
    r = health_client().get("/readyz")
    assert r.status_code == 503
    assert r.json()["vault"] == "unavailable"
    assert r.json()["backup"]["state"] == "never"
    assert str(private_path) not in r.text


def test_readiness_reports_valid_runtime_and_filters_backup_details(monkeypatch, make_vault, tmp_path):
    vault = make_vault("entities: {}\n")
    monkeypatch.setenv("ONEOS_VAULT", str(vault))
    status = tmp_path / "backup-status.json"
    status.write_text(json.dumps({"state": "ok", "last_success": time.time(), "message": "PRIVATE SOURCE", "checked_at": time.time()}))
    monkeypatch.setenv("ONEOS_BACKUP_STATUS_FILE", str(status))
    r = health_client().get("/readyz")
    assert r.status_code == 200
    assert r.json()["vault"] == "ok"
    assert r.json()["backup"]["state"] == "ok"
    assert "PRIVATE SOURCE" not in r.text


def test_stale_backup_is_visible(monkeypatch, tmp_path):
    status = tmp_path / "backup.json"
    status.write_text(json.dumps({"state": "ok", "last_success": time.time() - 172800}))
    monkeypatch.setenv("ONEOS_BACKUP_STATUS_FILE", str(status))
    assert health_client().get("/readyz").json()["backup"]["state"] == "missed"


def test_registered_but_absent_bundle_is_not_ready(monkeypatch, make_vault):
    vault = make_vault("entities:\n  example:\n    label: Example\n    flags: []\n")
    monkeypatch.setenv("ONEOS_VAULT", str(vault))
    r = health_client().get("/readyz")
    assert r.status_code == 503
    assert r.json()["vault"] == "unavailable"


def test_symlink_or_malformed_backup_status_is_not_trusted(monkeypatch, tmp_path):
    target = tmp_path / "target"
    target.write_text('{"state":"ok","last_success":999999999999999}')
    status = tmp_path / "link"
    status.symlink_to(target)
    monkeypatch.setenv("ONEOS_BACKUP_STATUS_FILE", str(status))
    assert health_client().get("/readyz").json()["backup"]["state"] == "unavailable"
    monkeypatch.setenv("ONEOS_BACKUP_STATUS_FILE", str(target))
    assert health_client().get("/readyz").json()["backup"]["state"] == "unavailable"


def test_enormous_json_integer_fails_closed(monkeypatch, tmp_path):
    status = tmp_path / "backup.json"
    status.write_text(json.dumps({"state": "ok", "last_success": 10 ** 400}))
    monkeypatch.setenv("ONEOS_BACKUP_STATUS_FILE", str(status))
    assert health_client().get("/readyz").json()["backup"]["state"] == "unavailable"


def test_real_asgi_startup_keeps_health_available_with_bad_vault(tmp_path):
    script = '''
import os, time
from pathlib import Path
import pyotp
from fastapi.testclient import TestClient
from app.main import app
from app.auth import AuthStore
directory = Path(os.environ["ONEOS_AUTH_STATE_DIR"])
directory.mkdir(mode=0o700)
store = AuthStore(directory)
secret = pyotp.random_base32()
previous = time.time() - 60
store.enroll("synthetic owner password", secret, pyotp.TOTP(secret).at(previous), now=previous)
with TestClient(app, base_url="https://localhost:8443") as client:
    assert client.get("/healthz").json() == {"status": "ok"}
    client.cookies.set("__Host-oneos", store.login("synthetic owner password", pyotp.TOTP(secret).now()))
    readiness = client.get("/readyz")
    assert readiness.status_code == 503
    assert readiness.json()["vault"] == "unavailable"
    page = client.get("/")
    assert page.status_code >= 400, page.status_code
    assert 'role="alert"' in page.text
    assert os.environ.get("ONEOS_VAULT", "PRIVATE_UNSET_MARKER") not in page.text
'''
    env = {key: value for key, value in os.environ.items() if not key.startswith("ONEOS_")}
    env["ONEOS_DEPLOYMENT"] = "local"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["ONEOS_AUTH_STATE_DIR"] = str(tmp_path / "first-auth")
    absent = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True, text=True)
    assert absent.returncode == 0, absent.stderr
    (tmp_path / "_system").mkdir()
    (tmp_path / "_system/entities.yaml").write_text("entities: [broken")
    env["ONEOS_VAULT"] = str(tmp_path)
    env["ONEOS_AUTH_STATE_DIR"] = str(tmp_path / "second-auth")
    invalid = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True, text=True)
    assert invalid.returncode == 0, invalid.stderr
