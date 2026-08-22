"""Renderers, route metadata, and the shared head (S6 Task 7).

Status selection follows severity, not code (design §5); the htmx-config
override must reach every full-page document through one shared head
(design §4). Synthetic vaults only.
"""
import importlib

import pytest

from tests.conftest import scaffold_modules, write_vault

ENTITIES = """
version: "1.0"
entities:
  alpha: { label: Alpha, flags: [] }
"""

NO_ENTITIES = """
version: "1.0"
entities: {}
"""

HTMX_CONFIG_MARKER = 'name="htmx-config"'
OVERRIDE_MARKER = '{"code":"[45]..","swap":true,"error":true}'


def _client(tmp_path, monkeypatch, entities_yaml):
    from starlette.testclient import TestClient

    write_vault(tmp_path, entities_yaml)
    scaffold_modules(tmp_path, "alpha", ["00-intake", "01-core", "02-work"])
    monkeypatch.setenv("ONEOS_VAULT", str(tmp_path))
    import app.main as main

    importlib.reload(main)
    return TestClient(main.app)


class _StubRequest:
    def __init__(self, headers=None):
        self.headers = dict(headers or {})


def test_fragment_refusal_status_is_200():
    from app.console_errors import _CODES
    from app.console_render import status_for

    assert _CODES["E-STALE"].severity == "refusal"
    assert status_for(_CODES["E-STALE"], fragment=True) == 200


def test_fragment_attention_status_is_the_page_status():
    from app.console_errors import _CODES
    from app.console_render import status_for

    assert _CODES["E-TAMPER"].severity == "attention"
    assert status_for(_CODES["E-TAMPER"], fragment=True) == 409
    assert status_for(_CODES["E-COMMITTED"], fragment=True) == 500


def test_page_status_comes_from_the_error():
    from app.console_errors import _CODES
    from app.console_render import status_for

    for error in _CODES.values():
        assert status_for(error, fragment=False) == error.page_status


def test_fragment_only_route_ignores_missing_hx_request():
    from app.console_render import is_fragment
    from app.console_routing import console_route

    @console_route(catches=(ValueError,), surface="fragment-only")
    def fragment_endpoint():
        pass

    @console_route(catches=(ValueError,), surface="page")
    def page_endpoint():
        pass

    bare = _StubRequest()
    htmx = _StubRequest({"HX-Request": "true"})

    assert is_fragment(bare, fragment_endpoint) is True
    assert is_fragment(htmx, fragment_endpoint) is True
    assert is_fragment(bare, page_endpoint) is False
    assert is_fragment(htmx, page_endpoint) is True


def test_console_route_rejects_exception_in_catches():
    from app.console_routing import console_route

    with pytest.raises(ValueError):
        console_route(catches=(Exception,), surface="page")
    with pytest.raises(ValueError):
        console_route(catches=(ValueError, BaseException), surface="page")


def _full_page_route_paths(main) -> list[str]:
    """Every full-page route's concrete request path, derived from
    `app.routes` plus each endpoint's OWN `__console_route__.surface ==
    "page"` — the same source design §7 invariant 6 reads (I8, review: this
    test hard-coded a four-URL list, the exact enumeration shape design §7
    exists to forbid, and it went undischarged through every subsequent
    route task because no route task owned this file).

    `triage_default`'s bare `/triage` is excluded here: with bundles present
    (as `ENTITIES` below provides) it 307-redirects rather than rendering a
    page body, so there is nothing to assert the meta tag INTO on this path
    — its own templated no-bundles render, where it DOES produce a page, is
    covered separately by `test_no_bundles_response_carries_htmx_config_
    meta`.
    """
    paths = []
    for route in main.app.routes:
        endpoint = getattr(route, "endpoint", None)
        meta = getattr(endpoint, "__console_route__", None)
        if meta is None or meta.surface != "page":
            continue
        if endpoint is main.triage_default:
            continue
        if "GET" not in (getattr(route, "methods", None) or set()):
            continue
        paths.append(route.path.replace("{entity}", "alpha"))
    return paths


def test_every_page_template_carries_htmx_config_meta(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, ENTITIES)
    import app.main as main

    paths = _full_page_route_paths(main)
    # Floor, so a sweep that silently matched nothing cannot pass by
    # asserting [] == [] — the same reasoning
    # `test_every_registered_route_declares_its_catch_family`
    # (tests/test_console_invariants.py) already applies to its own
    # enumeration.
    assert len(paths) >= 4, f"the sweep saw only {paths}"

    for url in paths:
        response = client.get(url)
        assert response.status_code == 200, url
        assert HTMX_CONFIG_MARKER in response.text, url
        assert OVERRIDE_MARKER in response.text, url


def test_no_bundles_response_carries_htmx_config_meta(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, NO_ENTITIES)
    response = client.get("/triage", follow_redirects=False)
    assert response.status_code == 200
    assert HTMX_CONFIG_MARKER in response.text
    assert OVERRIDE_MARKER in response.text
