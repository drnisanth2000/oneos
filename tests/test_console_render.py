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


def test_every_page_template_carries_htmx_config_meta(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, ENTITIES)
    for url in (
        "/",
        "/triage/alpha",
        "/outbox/alpha",
        "/registry/alpha/products",
    ):
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
