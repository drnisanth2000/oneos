"""Routes — framework surface (S6 Task 10, design §4 "Rule 4" and §5 "Rule 6").

Three framework-owned surfaces this task closes:

- `entity_scope` no longer converts `EntitySelectionError` into
  `HTTPException`; a dedicated handler describes it as `E-ENTITY` at 404.
- `RequestValidationError` is described as `E-REQUEST` without ever echoing
  the submitted field name or value (the resolver never reads `exc.errors()`).
- `StarletteHTTPException` — an unmatched URL, a wrong method, a `StaticFiles`
  miss — keeps the framework's own status and plain body, except that an
  HTMX request gets its body replaced with safe text. This closes the live
  regression window Task 7 opened: 4xx/5xx responses now swap under HTMX, so
  without this handler, raw framework text would swap into an operator
  target. `HTTPException` stays outside the taxonomy (Rule 6): the status is
  never remapped through `describe()`.
- A global fallback describes any exception no route has caught and returns
  the code's page status — never 200.

Synthetic vaults only; the real vault is never touched.
"""
from __future__ import annotations

import asyncio
import importlib
from pathlib import Path

import pytest
from starlette.requests import Request
from starlette.testclient import TestClient

from app.console_routing import console_route
from tests.conftest import scaffold_modules, write_vault

ENTITIES = """
version: "1.0"
entities:
  alpha: { label: Alpha, flags: [] }
"""


def _load_main(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entities_yaml: str):
    """Fresh `app.main` module bound to a throwaway vault, as
    tests/test_console_render.py already does for the same reason: the app
    reads `ONEOS_VAULT` at import time."""
    write_vault(tmp_path, entities_yaml)
    scaffold_modules(tmp_path, "alpha", ["00-intake", "01-core", "02-work"])
    monkeypatch.setenv("ONEOS_VAULT", str(tmp_path))
    import app.main as main

    importlib.reload(main)
    return main


def _client(tmp_path, monkeypatch, entities_yaml=ENTITIES, **kwargs) -> TestClient:
    main = _load_main(tmp_path, monkeypatch, entities_yaml)
    return TestClient(main.app, **kwargs)


def _synthetic_request(app, *, endpoint=None, headers=None) -> Request:
    """A minimal ASGI-http scope, built by hand rather than routed, so the
    RequestValidationError test can pin the handler's page/fragment behavior
    directly instead of depending on which real route it happens to reach."""
    encoded_headers = [
        (key.lower().encode(), value.encode()) for key, value in (headers or {}).items()
    ]
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/synthetic",
        "raw_path": b"/synthetic",
        "query_string": b"",
        "headers": encoded_headers,
        "app": app,
        "router": app.router,
        "endpoint": endpoint,
    }
    return Request(scope)


def test_unknown_entity_renders_e_entity_404(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    response = client.get("/triage/hostile-unknown-slug-marker")

    assert response.status_code == 404
    assert "E-ENTITY" in response.text
    # Rule 9 / §6 disclosure: the unresolved slug is never echoed back.
    assert "hostile-unknown-slug-marker" not in response.text


def test_request_validation_renders_e_request_without_echo(tmp_path, monkeypatch):
    """Must drive the app, not the handler function.

    An earlier version called `main._request_validation_error_handler` directly
    with a synthetic scope. Unregistering the `@app.exception_handler` — the
    production wiring — left all eight tests green, because FastAPI's default
    also returns 422 and nothing proved a real malformed form reaches
    `E-REQUEST`.

    The status is **200, not 422**: every route that takes form data is
    `surface="fragment-only"`, and Rule 5 selects by route shape first, so the
    fragment renderer applies and a refusal-severity code returns 200. The
    plan's test name predated the decoration and its `422` no longer describes
    any reachable case — no page-surface route accepts a form.
    """
    client = _client(tmp_path, monkeypatch)

    response = client.post(
        "/triage/alpha/propose",
        data={"filename": "note.md", "hostile-field-name": "hostile-submitted-value"},
    )

    assert response.status_code == 200
    body = response.text
    assert "E-REQUEST" in body
    assert "hostile-field-name" not in body
    assert "hostile-submitted-value" not in body


def test_request_validation_under_htmx_is_a_swappable_fragment(tmp_path, monkeypatch):
    """Same outcome with the header present — the swap target must receive it."""
    client = _client(tmp_path, monkeypatch)

    response = client.post(
        "/triage/alpha/propose",
        data={"filename": "note.md"},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    assert "E-REQUEST" in response.text


def test_refusal_severity_escapee_keeps_page_status_under_htmx(tmp_path, monkeypatch):
    """The global fallback must never return 200, even rendering a fragment.

    A refusal-severity exception escaping a route's declared family is a defect;
    `status_for` would give it 200 under `HX-Request` and monitoring would see a
    success. The existing fallback test is blind to this twice over — it injects
    a `RuntimeError` (E-UNKNOWN, attention, 500 either way) and sends no
    `HX-Request`.
    """
    main = _load_main(tmp_path, monkeypatch, ENTITIES)
    from app.scope import OutOfScopeError

    def _escape(*args, **kwargs):
        raise OutOfScopeError("undeclared by this route's catch family")

    monkeypatch.setattr(main, "read_inbox", _escape)
    client = TestClient(main.app, raise_server_exceptions=False)

    response = client.get("/triage/alpha", headers={"HX-Request": "true"})

    # E-SCOPE is refusal severity with page status 404; the fragment rule would
    # have returned 200.
    assert response.status_code == 404
    assert "E-SCOPE" in response.text


def test_unmatched_url_keeps_plain_404(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    response = client.get("/this-route-does-not-exist")

    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}
    assert "E-" not in response.text


def test_wrong_method_keeps_405(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    response = client.put("/")

    assert response.status_code == 405
    assert "E-" not in response.text


def test_static_miss_keeps_404(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    response = client.get("/static/does-not-exist.js")

    assert response.status_code == 404
    assert "E-" not in response.text


def test_htmx_unmatched_url_gets_safe_body_at_404(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    plain = client.get("/this-route-does-not-exist")
    swapped = client.get(
        "/this-route-does-not-exist", headers={"HX-Request": "true"}
    )

    # Status is the framework's own, preserved exactly (design §4: "The
    # framework's status is preserved, not the code's").
    assert swapped.status_code == 404 == plain.status_code
    # The body is replaced only for the HTMX request; the plain response is
    # the framework's own untouched JSON detail.
    assert plain.headers["content-type"].startswith("application/json")
    assert swapped.headers["content-type"].startswith("text/html")
    assert "Not Found" not in swapped.text
    assert 'role="alert"' in swapped.text
    # Never mapped through the taxonomy (Rule 6): no described code appears.
    assert "E-" not in swapped.text


def test_unhandled_error_reaches_global_fallback_at_500(tmp_path, monkeypatch):
    main = _load_main(tmp_path, monkeypatch, ENTITIES)

    def _boom(scope):
        raise RuntimeError("synthetic unmapped failure - must not leak")

    monkeypatch.setattr(main, "read_inbox", _boom)
    # ServerErrorMiddleware always re-raises after handing the response to
    # the handler, specifically so a test client can opt in to seeing it —
    # three existing tests in tests/test_app.py already rely on this.
    route_client = TestClient(main.app, raise_server_exceptions=False)

    response = route_client.get("/triage/alpha")

    assert response.status_code == 500
    assert "E-UNKNOWN" in response.text
    assert "synthetic unmapped failure" not in response.text


def test_described_errors_never_reach_the_global_fallback(tmp_path, monkeypatch):
    main = _load_main(tmp_path, monkeypatch, ENTITIES)
    calls = []
    original = main.app.exception_handlers[Exception]

    async def _spy(request, exc):
        calls.append(exc)
        return await original(request, exc)

    # Mutate before the first dispatch: Starlette's middleware stack is built
    # lazily on first call and cached, so this is picked up cleanly.
    main.app.exception_handlers[Exception] = _spy
    client = TestClient(main.app)

    entity_response = client.get("/triage/hostile-unknown-slug-marker")
    assert entity_response.status_code == 404
    assert calls == []

    validation_response = client.post(
        "/triage/alpha/propose", data={"filename": "marker.md"}
    )
    assert validation_response.status_code < 500
    assert calls == []

    framework_response = client.get("/this-route-does-not-exist")
    assert framework_response.status_code == 404
    assert calls == []

    # Sanity: the fallback genuinely is reachable, so an empty `calls` above
    # is proof of routing, not of a spy that never fires.
    def _boom(scope):
        raise RuntimeError("synthetic unmapped failure")

    monkeypatch.setattr(main, "read_inbox", _boom)
    TestClient(main.app, raise_server_exceptions=False).get("/triage/alpha")
    assert len(calls) == 1
