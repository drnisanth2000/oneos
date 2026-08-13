"""End-to-end: the app boots, renders a vault-driven sidebar, and its client
wiring is vendored + morph-enabled (spec steps 1–2, §6).

The app reads its vault from ONEOS_VAULT, so the test builds a synthetic one and
points the env at it before importing the app — no real slug or path in the repo.
"""
import importlib
import re
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from starlette.testclient import TestClient

from tests.conftest import write_vault, scaffold_modules, ARCHETYPES

ENTITIES = """
version: "1.0"
entities:
  acme:   { label: Acme,   flags: [special] }
  globex: { label: Globex, flags: [other] }
"""


@pytest.fixture
def client(tmp_path, monkeypatch):
    write_vault(tmp_path, ENTITIES, ARCHETYPES)
    scaffold_modules(tmp_path, "acme", ["00-intake", "01-core", "02-work", "zz-extra"])
    scaffold_modules(tmp_path, "globex", ["00-intake", "01-core", "02-work"])
    for entity, marker in (("acme", "alpha-marker"), ("globex", "beta-marker")):
        path = tmp_path / entity / "00-inbox" / "active" / "marker.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"---\ntype: inbox-item\ntitle: {marker}\nsub: triage\n---\n{marker}\n",
            encoding="utf-8",
        )
    monkeypatch.setenv("ONEOS_VAULT", str(tmp_path))
    # Import fresh so module-level scope binds to this vault.
    import app.main as main
    importlib.reload(main)
    return TestClient(main.app)


def test_shell_boots_and_lists_bundles_from_vault(client):
    html = client.get("/").text
    assert "Acme" in html and "Globex" in html
    # Module names come from the registry, rendered in the sidebar.
    assert "zz-extra" in html


def test_client_assets_are_vendored_not_cdn(client):
    html = client.get("/").text
    srcs = re.findall(r'<script[^>]+src="([^"]+)"', html)
    assert srcs, "no script tags found"
    assert all(s.startswith("/static/vendor/") for s in srcs), srcs
    assert "unpkg" not in html and "cdn." not in html
    assert 'src="http' not in html


def test_morph_is_wired(client):
    html = client.get("/").text
    assert 'hx-ext="alpine-morph"' in html
    assert 'hx-swap="morph"' in html


def test_pulse_partial_renders_alpine_drawer(client):
    html = client.get("/blocks/pulse").text
    assert 'x-data="{ drawer: false }"' in html
    assert 'x-show="drawer"' in html


def test_triage_screen_has_gate1_timing_instrument(client):
    html = client.get("/triage/acme").text
    assert "Start timing" in html                 # the stopwatch control
    assert "$store.timer" in html                 # Alpine store drives it
    assert "htmx:afterRequest" in html            # accepts increment the count


def test_concurrent_triage_requests_keep_entity_rows_isolated(client, monkeypatch):
    import app.main

    barrier = threading.Barrier(2)
    real_read = app.main.read_inbox

    def overlapped(scope, entity):
        barrier.wait(timeout=5)
        return real_read(scope, entity)

    monkeypatch.setattr(app.main, "read_inbox", overlapped)
    with ThreadPoolExecutor(max_workers=2) as pool:
        alpha = pool.submit(client.get, "/triage/acme")
        beta = pool.submit(client.get, "/triage/globex")
    assert "alpha-marker" in alpha.result().text
    assert "beta-marker" not in alpha.result().text
    assert "beta-marker" in beta.result().text
    assert "alpha-marker" not in beta.result().text
