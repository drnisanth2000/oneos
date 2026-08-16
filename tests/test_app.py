"""End-to-end: the app boots, renders a vault-driven sidebar, and its client
wiring is vendored + morph-enabled (spec steps 1–2, §6).

The app reads its vault from ONEOS_VAULT, so the test builds a synthetic one and
points the env at it before importing the app — no real slug or path in the repo.
"""
import importlib
import hashlib
import json
import re
import sqlite3
import subprocess
import threading
import textwrap
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from pathlib import Path

import pytest
import yaml
from starlette.testclient import TestClient

from tests.conftest import git_head, write_vault, scaffold_modules

ENTITIES = """
version: "1.0"
entities:
  alpha: { label: Alpha, flags: [special] }
  beta:  { label: Beta,  flags: [other] }
"""

HTTP_ARCHETYPES = textwrap.dedent(
    """
    version: "2.0"
    flags:
      special: "Activates the extra module"
      other:   "Some other capability"
    modules:
      00-intake:  { block: system, core: true }
      01-core:    { block: govern, core: true }
      02-work:    { block: build }
      zz-extra:   { block: self, core: true, requires_flag: special }
    submodules:
      00-intake:
        triage: { name: "Triage" }
      02-work:
        general: { name: "General" }
    archetypes:
      plain:   { }
      special: { special: true }
    """
).strip()


def snapshot_entity_bytes(vault: Path, entities: tuple[str, ...]) -> dict[str, bytes]:
    return {
        path.relative_to(vault).as_posix(): path.read_bytes()
        for entity in entities
        for path in sorted((vault / entity).rglob("*"))
        if path.is_file()
    }


def snapshot_entity_tree(vault: Path, entity: str):
    entries = []
    root = vault / entity
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            entries.append((relative, "symlink", path.readlink().as_posix()))
        elif path.is_dir():
            entries.append((relative, "directory", b""))
        else:
            entries.append((relative, "file", path.read_bytes()))
    return tuple(sorted(entries))


class HxValsParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.values: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "button":
            values = dict(attrs).get("hx-vals")
            if values is not None:
                self.values.append(values)


@pytest.fixture
def client(tmp_path, monkeypatch):
    write_vault(tmp_path, ENTITIES, HTTP_ARCHETYPES)
    (tmp_path / "_system/classifier").mkdir()
    (tmp_path / "_system/classifier/rules.yaml").write_text(
        """version: "1.0"
rules:
  - id: invalid-destination
    match: {any: [invalid-destination-marker]}
    route: {module: 02-work, sub: inactive}
  - id: valid-destination
    match: {any: [valid-destination-marker]}
    route: {module: 02-work, sub: general}
default: {module: 00-inbox, sub: triage}
""",
        encoding="utf-8",
    )
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
        (tmp_path / entity / "02-work" / "active").mkdir(parents=True)
        path = tmp_path / entity / "00-inbox" / "active" / "marker.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"---\ntype: inbox-item\ntitle: {marker} valid-destination-marker\n"
            f"sub: triage\n---\n{entity}-diff-marker\n",
            encoding="utf-8",
        )
        outbox = tmp_path / entity / "outbox"
        outbox.mkdir(parents=True, exist_ok=True)
        proposal_id = "20260815T090703-" + (
            "11" * 16 if entity == "alpha" else "22" * 16
        )
        (outbox / f"{proposal_id}.yaml").write_text(
            "\n".join(
                (
                    f"id: {proposal_id}",
                    "action: classify",
                    f"entity: {entity}",
                    f"src: {entity}/00-inbox/active/marker.md",
                    f"source_sha256: {hashlib.sha256(path.read_bytes()).hexdigest()}",
                    f"dst: {entity}/02-work/active/marker.md",
                    "module: 02-work",
                    "sub: general",
                    "block: build",
                    "status: pending",
                    "",
                )
            ),
            encoding="utf-8",
        )
    invalid = tmp_path / "alpha/00-inbox/active/invalid.md"
    invalid.write_text(
        "---\ntype: inbox-item\ntitle: invalid-destination-marker\n"
        "sub: triage\n---\ninvalid recommendation remains visible\n",
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
    (tmp_path / ".gitignore").write_text("*/outbox/*.yaml\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "test"], cwd=tmp_path, check=True
    )
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "fixture"], cwd=tmp_path, check=True
    )
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


def test_triage_renders_accept_only_for_canonical_destination(client):
    html = client.get("/triage/alpha").text
    assert "valid-destination-marker" in html
    assert "invalid-destination-marker" in html
    assert html.count('class="accept"') == 1
    assert '"block":' not in html


@pytest.mark.parametrize("redirect", ("directory", "leaf"))
def test_triage_rejects_same_entity_sensitive_redirect_without_read_or_render(
    client, monkeypatch, redirect
):
    active = client.vault / "alpha/00-inbox/active"
    sensitive = client.vault / "alpha/.sensitive"
    sensitive.mkdir()
    if redirect == "directory":
        target = sensitive / "redirected-active"
        active.rename(target)
        active.symlink_to(target, target_is_directory=True)
        watched = target / "invalid.md"
    else:
        watched = sensitive / "secret.md"
        watched.write_text(
            "---\ntitle: sensitive-render-marker\nsub: triage\n---\nsecret\n",
            encoding="utf-8",
        )
        linked = active / "aaa-sensitive.md"
        linked.symlink_to(watched)
    real_read = Path.read_text
    body_reads = []

    def guarded(candidate, *args, **kwargs):
        if candidate == watched:
            body_reads.append(candidate)
            raise AssertionError("redirected inbox body was opened")
        return real_read(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded)
    before_head = git_head(client.vault)
    before_tree = snapshot_entity_tree(client.vault, "alpha")
    route_client = TestClient(client.app, raise_server_exceptions=False)

    response = route_client.get("/triage/alpha")

    assert response.status_code >= 400
    assert "sensitive-render-marker" not in response.text
    assert body_reads == []
    assert git_head(client.vault) == before_head
    assert snapshot_entity_tree(client.vault, "alpha") == before_tree


def test_triage_serializes_canonical_destination_as_one_hx_vals_mapping(client):
    hostile_leaf = 'shown\'.md", "filename": "marker.md'
    source = client.vault / "alpha/00-inbox/active/marker.md"
    source.rename(source.with_name(hostile_leaf))

    parser = HxValsParser()
    parser.feed(client.get("/triage/alpha").text)

    assert len(parser.values) == 1
    assert json.loads(parser.values[0]) == {
        "filename": hostile_leaf,
        "module": "02-work",
        "sub": "general",
    }


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


def test_concurrent_proposal_requests_keep_canonical_destinations_isolated(
    client, monkeypatch
):
    import app.outbox as outbox

    for entity in ("alpha", "beta"):
        for proposal in (client.vault / entity / "outbox").glob("*.yaml"):
            proposal.unlink()

    barrier = threading.Barrier(2)
    real_resolve = outbox.resolve_classification_destination
    destinations = []

    def overlapped(scope, item_path, **claims):
        barrier.wait(timeout=5)
        destination = real_resolve(scope, item_path, **claims)
        destinations.append(destination)
        return destination

    monkeypatch.setattr(outbox, "resolve_classification_destination", overlapped)
    data = {"filename": "marker.md", "module": "02-work", "sub": "general"}
    with ThreadPoolExecutor(max_workers=2) as pool:
        alpha = pool.submit(client.post, "/triage/alpha/propose", data=data)
        beta = pool.submit(client.post, "/triage/beta/propose", data=data)

    assert alpha.result().status_code == 200
    assert beta.result().status_code == 200
    records = {}
    for entity in ("alpha", "beta"):
        paths = list((client.vault / entity / "outbox").glob("*.yaml"))
        assert len(paths) == 1
        records[entity] = yaml.safe_load(paths[0].read_text(encoding="utf-8"))
    assert records["alpha"]["entity"] == "alpha"
    assert records["alpha"]["src"] == "alpha/00-inbox/active/marker.md"
    assert records["alpha"]["dst"] == "alpha/02-work/active/marker.md"
    assert records["beta"]["entity"] == "beta"
    assert records["beta"]["src"] == "beta/00-inbox/active/marker.md"
    assert records["beta"]["dst"] == "beta/02-work/active/marker.md"
    assert len(destinations) == 4
    assert len({id(destination) for destination in destinations}) == 4
    assert [destination.entity for destination in destinations].count("alpha") == 2
    assert [destination.entity for destination in destinations].count("beta") == 2


@pytest.mark.parametrize("data", [
    {"filename": "../marker.md", "module": "02-work", "sub": "general"},
    {"filename": r"..\\marker.md", "module": "02-work", "sub": "general"},
    {"filename": "marker.md", "module": "missing", "sub": "general"},
    {"filename": "marker.md", "module": "02-work", "sub": "wrong-module"},
    {"filename": "marker.md", "module": "02-work", "sub": "general", "block": "growth"},
    {"filename": "marker.md", "module": "02-work", "sub": "general", "entity": "beta"},
])
def test_tampered_proposal_form_writes_nothing(client, data):
    for proposal in (client.vault / "alpha/outbox").glob("*.yaml"):
        proposal.unlink()
    route_client = TestClient(client.app, raise_server_exceptions=False)
    route_client.vault = client.vault
    before_head = git_head(route_client.vault)
    before = snapshot_entity_bytes(route_client.vault, ("alpha", "beta"))

    response = route_client.post("/triage/alpha/propose", data=data)

    assert response.status_code >= 400
    assert git_head(route_client.vault) == before_head
    assert snapshot_entity_bytes(route_client.vault, ("alpha", "beta")) == before
    assert not list((route_client.vault / "alpha/outbox").glob("*.yaml"))


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


@pytest.mark.parametrize(
    "precondition,expected",
    [
        (
            "changed",
            "Approval refused: source changed since this proposal was created. "
            "Create a fresh proposal.",
        ),
        (
            "missing",
            "Approval refused: source is missing. Restore it or reject the proposal.",
        ),
    ],
)
def test_approval_route_visibly_refuses_unfresh_source(
    client, precondition, expected
):
    outbox_dir = client.vault / "alpha/outbox"
    for path in outbox_dir.glob("*.yaml"):
        path.unlink()
    response = client.post(
        "/triage/alpha/propose",
        data={"filename": "marker.md", "module": "02-work", "sub": "general"},
    )
    assert response.status_code == 200
    (proposal_path,) = tuple(outbox_dir.glob("*.yaml"))
    record = yaml.safe_load(proposal_path.read_text(encoding="utf-8"))
    source = client.vault / "alpha/00-inbox/active/marker.md"
    if precondition == "changed":
        source.write_bytes(source.read_bytes() + b"changed-after-proposal\n")
    else:
        source.unlink()
    head_before = git_head(client.vault)
    proposal_before = proposal_path.read_bytes()

    refusal = client.post(
        "/outbox/alpha/approve", data={"id": record["id"]}
    )

    assert refusal.status_code == 200
    assert 'role="alert"' in refusal.text
    assert expected in refusal.text
    assert record["id"] in refusal.text
    assert proposal_path.read_bytes() == proposal_before
    assert git_head(client.vault) == head_before
    assert not (client.vault / "alpha/02-work/active/marker.md").exists()


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
    beta_id = "20260815T090703-" + "22" * 16
    beta_proposal = client.vault / "beta/outbox" / f"{beta_id}.yaml"
    beta_source = client.vault / "beta/00-inbox/active/marker.md"
    proposal_before = beta_proposal.read_bytes()
    source_before = beta_source.read_bytes()

    response = client.post(
        f"/outbox/alpha/{action}", data={"id": beta_id}
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
