"""Minimal liveness and authenticated deployment readiness.

Authentication is supplied by the application-wide owner middleware. Only
``/healthz`` is exempt there. Diagnostics never return paths or exception text.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import stat
import time

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from .config import build_catalog
from .console_routing import console_route, structured_reader
from .entities import EntityManifestError, SystemRegistryPathError
from .vault import DestinationRegistryError, Vault


@structured_reader(category="admin-record")
def backup_status() -> dict:
    raw = os.environ.get("ONEOS_BACKUP_STATUS_FILE")
    if not raw:
        return {"state": "never", "last_success": None}
    try:
        path = Path(raw)
        if not path.is_absolute() or path.resolve(strict=True) != path:
            raise ValueError("unsafe status path")
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 4096:
                raise ValueError("invalid status file")
            payload = json.loads(stream.read(4097))
        state = payload["state"]
        last = payload.get("last_success")
        if state not in {"ok", "never", "missed", "failed"}:
            raise ValueError("invalid backup status")
        if last is not None and (
            isinstance(last, bool) or not isinstance(last, (int, float))
            or last < 0 or last > time.time() + 300 or not math.isfinite(last)
        ):
            raise ValueError("invalid timestamp")
        if state == "ok" and (last is None or time.time() - last > 86400):
            state = "missed"
        return {"state": state, "last_success": last}
    except (OSError, ValueError, KeyError, TypeError):
        return {"state": "unavailable", "last_success": None}


def install_health(app: FastAPI) -> None:
    @app.get("/healthz", include_in_schema=False)
    @console_route(catches=(), surface="page")
    def healthz():
        return JSONResponse({"status": "ok"}, headers={"Cache-Control": "no-store"})

    @app.get("/readyz", include_in_schema=False)
    @console_route(catches=(EntityManifestError, SystemRegistryPathError, DestinationRegistryError,
                            OSError, RuntimeError), surface="page", services=(Vault.bundles,))
    def readyz(request: Request):
        try:
            # Eager evaluation is intentional: readiness includes registry
            # access rather than merely checking the mountpoint's existence.
            bundles = Vault(build_catalog()).bundles()
            vault = "unavailable" if any(not bundle.on_disk or bundle.errors for bundle in bundles) else "ok"
        except (EntityManifestError, SystemRegistryPathError, DestinationRegistryError,
                OSError, RuntimeError):
            # Known configuration failures reveal readiness, never private text.
            vault = "unavailable"
        backup = backup_status()
        code = 200 if vault == "ok" else 503
        payload = {"status": "ready" if code == 200 else "unavailable", "vault": vault, "backup": backup}
        headers = {"Cache-Control": "no-store"}
        if "text/html" in request.headers.get("accept", ""):
            labels = {
                "ok": "Up to date", "never": "First backup pending",
                "missed": "Backup overdue — connect the backup drive",
                "failed": "Last backup failed — check local logs",
                "unavailable": "Backup status unavailable",
            }
            return HTMLResponse(
                '<!doctype html><html lang="en"><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width,initial-scale=1">'
                '<title>OneOS service status</title><link rel="stylesheet" href="/static/app.css">'
                '<main class="main"><h1>Service status</h1>'
                f'<p>Vault: {vault}</p><p>Backup: {labels[backup["state"]]}</p>'
                '<p><a href="/">Back to OneOS</a></p></main></html>',
                status_code=code, headers=headers,
            )
        return JSONResponse(payload, status_code=code, headers=headers)
