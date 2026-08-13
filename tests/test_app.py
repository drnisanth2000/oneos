"""End-to-end: the app boots, renders a vault-driven sidebar, and its client
wiring is vendored + morph-enabled (spec steps 1–2, §6).

The app reads its vault from ONEOS_VAULT, so the test builds a synthetic one and
points the env at it before importing the app — no real slug or path in the repo.
"""
import importlib
import re
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from tests.conftest import write_vault, scaffold_modules, ARCHETYPES

ENTITIES = """
version: "1.0"
entities:
  alpha: { label: Alpha, flags: [special] }
  beta:  { label: Beta,  flags: [other] }
"""


@pytest.fixture
def client(tmp_path, monkeypatch):
    write_vault(tmp_path, ENTITIES, ARCHETYPES)
    (tmp_path / "_system/products.yaml").write_text(
        """version: "1.0"
products:
  alpha:
    shared: {label: Alpha Shared}
    alpha-only: {label: Alpha Only}
  beta:
    shared: {label: Beta Registry Marker}
    beta-only: {label: Beta Only}
""",
        encoding="utf-8",
    )
    (tmp_path / "_system/workspaces.yaml").write_text(
        """version: "1.0"
workspaces:
  - id: alpha-cross
    label: Alpha Cross
    kind: cross
    primary_entity: alpha
    entities: [alpha, beta]
    product: shared
    default_view: blocks
""",
        encoding="utf-8",
    )
    scaffold_modules(tmp_path, "alpha", ["00-intake", "01-core", "02-work", "zz-extra"])
    scaffold_modules(tmp_path, "beta", ["00-intake", "01-core", "02-work"])
    for entity, marker in (("alpha", "alpha-marker"), ("beta", "beta-marker")):
        path = tmp_path / entity / "00-inbox" / "active" / "marker.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"---\ntype: inbox-item\ntitle: {marker}\nsub: triage\n---\n{entity}-diff-marker\n",
            encoding="utf-8",
        )
        outbox = tmp_path / entity / "outbox"
        outbox.mkdir(parents=True, exist_ok=True)
        (outbox / f"{entity}-proposal.yaml").write_text(
            "\n".join(
                (
                    f"id: {entity}-proposal",
                    "action: classify",
                    f"entity: {entity}",
                    f"src: {entity}/00-inbox/active/marker.md",
                    f"dst: {entity}/11-knowledge/active/marker.md",
                    "module: 11-knowledge",
                    "sub: kb",
                    "block: govern",
                    "status: pending",
                    "",
                )
            ),
            encoding="utf-8",
        )
    for entity, relative, marker in (
        ("alpha", "07-finance/active/alpha.md", "alpha-registry-marker"),
        ("beta", "07-finance/active/beta-one.md", "beta-registry-marker-one"),
        ("beta", "09-marketing/active/beta-two.md", "beta-registry-marker-two"),
    ):
        path = tmp_path / entity / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"---\ntype: note\ntitle: {marker}\nentity: {entity}\n"
            "product: shared\nstatus: active\ncreated: 2026-01-01\n"
            f"updated: 2026-01-01\n---\n{marker}\n",
            encoding="utf-8",
        )
    for entity, columns in (("alpha", ("product", "tag")), ("beta", ("product",))):
        connection = sqlite3.connect(tmp_path / entity / "books.db")
        connection.execute(
            "CREATE TABLE entries (id INTEGER PRIMARY KEY, product TEXT, tag TEXT)"
        )
        for column in columns:
            connection.execute(
                f"INSERT INTO entries ({column}) VALUES (?)", ("shared",)
            )
        connection.commit()
        connection.close()
    monkeypatch.setenv("ONEOS_VAULT", str(tmp_path))
    import app.main as main
    importlib.reload(main)
    test_client = TestClient(main.app)
    test_client.vault = tmp_path
    return test_client


def test_shell_boots_and_lists_bundles_from_vault(client):
    html = client.get("/").text
    assert "Alpha" in html and "Beta" in html
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
    html = client.get("/triage/alpha").text
    assert "Start timing" in html                 # the stopwatch control
    assert "$store.timer" in html                 # Alpine store drives it
    assert "htmx:afterRequest" in html            # accepts increment the count


def test_concurrent_triage_requests_keep_entity_rows_isolated(client, monkeypatch):
    import app.main

    barrier = threading.Barrier(2)
    real_read = app.main.read_inbox

    def overlapped(scope):
        barrier.wait(timeout=5)
        return real_read(scope)

    monkeypatch.setattr(app.main, "read_inbox", overlapped)
    with ThreadPoolExecutor(max_workers=2) as pool:
        alpha = pool.submit(client.get, "/triage/alpha")
        beta = pool.submit(client.get, "/triage/beta")
    assert "alpha-marker" in alpha.result().text
    assert "beta-marker" not in alpha.result().text
    assert "beta-marker" in beta.result().text
    assert "alpha-marker" not in beta.result().text


def test_concurrent_outbox_requests_keep_entity_diffs_isolated(client, monkeypatch):
    import app.main as main

    barrier = threading.Barrier(2)
    real_load = main.load_proposals

    def overlapped(scope):
        barrier.wait(timeout=5)
        return real_load(scope)

    monkeypatch.setattr(main, "load_proposals", overlapped)
    with ThreadPoolExecutor(max_workers=2) as pool:
        alpha = pool.submit(client.get, "/outbox/alpha")
        beta = pool.submit(client.get, "/outbox/beta")
    alpha_html = alpha.result().text
    beta_html = beta.result().text
    assert "alpha-diff-marker" in alpha_html and "beta-diff-marker" not in alpha_html
    assert "beta-diff-marker" in beta_html and "alpha-diff-marker" not in beta_html


def test_unknown_route_entity_is_404_without_entity_directory_read(client, monkeypatch):
    watched = client.vault / "directory-only"
    watched.mkdir()
    real_is_dir = Path.is_dir

    def guarded(path):
        if path == watched:
            raise AssertionError("unknown entity directory was consulted")
        return real_is_dir(path)

    monkeypatch.setattr(Path, "is_dir", guarded)
    response = client.get("/triage/directory-only")
    assert response.status_code == 404


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_alpha_outbox_action_cannot_touch_beta_proposal(client, action):
    beta_proposal = client.vault / "beta/outbox/beta-proposal.yaml"
    beta_source = client.vault / "beta/00-inbox/active/marker.md"
    proposal_before = beta_proposal.read_bytes()
    source_before = beta_source.read_bytes()

    response = client.post(
        f"/outbox/alpha/{action}", data={"id": "beta-proposal"}
    )

    assert response.status_code == 200
    assert beta_proposal.read_bytes() == proposal_before
    assert beta_source.read_bytes() == source_before
    assert "alpha-diff-marker" in response.text
    assert "beta-diff-marker" not in response.text


def test_registry_products_route_reads_only_bound_namespace(client):
    alpha_html = client.get("/registry/alpha/products").text
    beta_html = client.get("/registry/beta/products").text
    assert "alpha-only" in alpha_html and "beta-only" not in alpha_html
    assert "beta-only" in beta_html and "alpha-only" not in beta_html


def test_alpha_delete_impact_excludes_beta_totals_and_marker_text(client):
    response = client.post(
        "/registry/alpha/product/delete-preview", data={"slug": "shared"}
    )
    assert response.status_code == 200
    assert "orphan 4 reference(s)" in response.text
    assert "front-matter: 1" in response.text
    assert "books.db: 2" in response.text
    assert "front-matter: 2" not in response.text
    assert "beta-registry-marker" not in response.text


def test_concurrent_delete_previews_keep_reference_totals_isolated(
    client, monkeypatch
):
    import app.main as main

    barrier = threading.Barrier(2)
    real_count = main.reference_count

    def overlapped(scope, kind, slug):
        barrier.wait(timeout=5)
        return real_count(scope, kind, slug)

    monkeypatch.setattr(main, "reference_count", overlapped)
    with ThreadPoolExecutor(max_workers=2) as pool:
        alpha = pool.submit(
            client.post,
            "/registry/alpha/product/delete-preview",
            data={"slug": "shared"},
        )
        beta = pool.submit(
            client.post,
            "/registry/beta/product/delete-preview",
            data={"slug": "shared"},
        )
    alpha_html = alpha.result().text
    beta_html = beta.result().text
    assert "orphan 4 reference(s)" in alpha_html
    assert "orphan 3 reference(s)" not in alpha_html
    assert "orphan 3 reference(s)" in beta_html
    assert "orphan 4 reference(s)" not in beta_html
