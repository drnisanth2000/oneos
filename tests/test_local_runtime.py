from pathlib import Path

import yaml


ROOT = Path(__file__).parents[1]


def _compose() -> dict:
    return yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))


def _mounts(service: dict) -> dict[str, dict]:
    return {mount["target"]: mount for mount in service["volumes"]}


def test_app_is_private_non_root_and_has_only_explicit_writable_locations():
    app = _compose()["services"]["app"]
    mounts = _mounts(app)

    assert "ports" not in app
    assert app["build"]["target"] == "runtime"
    assert app["user"] == "${ONEOS_UID:?ONEOS_UID is required}:${ONEOS_GID:?ONEOS_GID is required}"
    assert app["read_only"] is True
    assert app["cap_drop"] == ["ALL"]
    assert app["security_opt"] == ["no-new-privileges:true"]
    assert mounts["/vault"]["read_only"] is False
    assert mounts["/state/auth"]["read_only"] is False
    assert mounts["/state/status"]["read_only"] is True
    assert set(app["tmpfs"]) == {"/tmp", "/run"}


def test_runtime_contract_is_explicit_and_backup_status_uses_directory_mount():
    app = _compose()["services"]["app"]
    environment = app["environment"]
    mounts = _mounts(app)

    assert environment == {
        "GIT_AUTHOR_EMAIL": "${ONEOS_GIT_EMAIL:?ONEOS_GIT_EMAIL is required}",
        "GIT_AUTHOR_NAME": "${ONEOS_GIT_NAME:?ONEOS_GIT_NAME is required}",
        "GIT_COMMITTER_EMAIL": "${ONEOS_GIT_EMAIL:?ONEOS_GIT_EMAIL is required}",
        "GIT_COMMITTER_NAME": "${ONEOS_GIT_NAME:?ONEOS_GIT_NAME is required}",
        "HOME": "/tmp",
        "ONEOS_AUTH_STATE_DIR": "/state/auth",
        "ONEOS_BACKUP_STATUS_FILE": "/state/status/backup-status.json",
        "ONEOS_DEPLOYMENT": "local",
        "ONEOS_GIT_EMAIL": "${ONEOS_GIT_EMAIL:?ONEOS_GIT_EMAIL is required}",
        "ONEOS_GIT_NAME": "${ONEOS_GIT_NAME:?ONEOS_GIT_NAME is required}",
        "ONEOS_ORIGIN": "https://localhost:8443",
        "ONEOS_VAULT": "/vault",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
    }
    assert mounts["/state/status"]["source"] == "${ONEOS_STATE_DIR:?ONEOS_STATE_DIR is required}/status"
    assert all(mount["target"] != "/state/status/backup-status.json" for mount in app["volumes"])


def test_only_caddy_publishes_loopback_tls_and_persists_state_for_backup():
    compose = _compose()
    app = compose["services"]["app"]
    caddy = compose["services"]["caddy"]
    mounts = _mounts(caddy)

    assert caddy["ports"] == [{"target": 8443, "published": "8443", "host_ip": "127.0.0.1", "protocol": "tcp"}]
    assert "ports" not in app
    assert mounts["/data"] == {
        "type": "bind",
        "source": "${ONEOS_STATE_DIR:?ONEOS_STATE_DIR is required}/caddy/data",
        "target": "/data",
        "read_only": False,
        "bind": {"create_host_path": False},
    }
    assert mounts["/config"] == {
        "type": "bind",
        "source": "${ONEOS_STATE_DIR:?ONEOS_STATE_DIR is required}/caddy/config",
        "target": "/config",
        "read_only": False,
        "bind": {"create_host_path": False},
    }
    assert caddy["depends_on"] == {"app": {"condition": "service_healthy"}}
    assert "volumes" not in compose


def test_services_have_bounded_logs_healthchecks_and_explicit_stop_semantics():
    services = _compose()["services"]
    for service in services.values():
        assert service["restart"] == "unless-stopped"
        assert service["logging"] == {
            "driver": "local",
            "options": {"max-size": "10m", "max-file": "3"},
        }
        assert service["healthcheck"]["interval"] == "30s"
        assert service["healthcheck"]["timeout"] == "5s"
        assert service["healthcheck"]["retries"] == 3
        assert service["healthcheck"]["start_period"] == "10s"

    assert "http://127.0.0.1:8000/healthz" in services["app"]["healthcheck"]["test"][-1]


def test_image_build_is_locked_non_root_and_keeps_git_hooks_available():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "FROM python:3.12" in dockerfile
    assert "COPY --from=ghcr.io/astral-sh/uv:" in dockerfile
    assert dockerfile.count("@sha256:") == 3
    assert "uv sync --locked --no-dev --no-editable" in dockerfile
    assert "apt-get install -y --no-install-recommends" in dockerfile
    assert "git" in dockerfile
    assert "USER ${ONEOS_UID}:${ONEOS_GID}" in dockerfile
    assert "getent passwd \"${ONEOS_UID}\"" in dockerfile
    assert "uvicorn" in dockerfile
    assert "--reload" not in dockerfile


def test_caddy_image_is_digest_pinned_and_built_for_selected_non_root_ids():
    compose = _compose()
    caddy = compose["services"]["caddy"]
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert caddy["build"]["target"] == "caddy"
    assert caddy["user"] == "${ONEOS_UID:?ONEOS_UID is required}:${ONEOS_GID:?ONEOS_GID is required}"
    assert "FROM caddy:2.11.4-alpine@sha256:" in dockerfile
    assert "chown -R ${ONEOS_UID}:${ONEOS_GID} /data /config" in dockerfile


def test_build_context_excludes_runtime_data_and_local_secrets():
    ignored = set((ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines())

    assert {".git", ".env", ".env.*", "config.json", "*.pem", "*.key", "vault", "state"} <= ignored


def test_caddy_terminates_internal_tls_on_the_declared_origin():
    caddyfile = (ROOT / "deploy" / "Caddyfile").read_text(encoding="utf-8")

    assert "https://localhost:8443" in caddyfile
    assert "tls internal" in caddyfile
    assert "reverse_proxy app:8000" in caddyfile
