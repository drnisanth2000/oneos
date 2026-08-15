"""Registry CRUD (spec §10 step 9, §2.2b).

Add and edit go direct; delete goes through the outbox with a reference count
that shows what breaks before it runs, and refuses if references remain
(ADR-006 consequence test). Temp git vaults only.
"""
import inspect
import sqlite3
import textwrap
from datetime import datetime

import pytest
import yaml

import app.registry as registry
from app.scope import CrossScopeError, Scope
from app.registry import (
    RegistryError,
    add_workspace,
    execute_delete,
    get_delete_proposal,
    propose_delete,
    reference_count,
)
from tests.conftest import (
    git_count_commits,
    git_entity_vault,
    git_head,
    git_head_message,
    git_is_clean,
)


class _FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 8, 15, 9, 7, 3, tzinfo=tz)


def _products_vault(tmp_path, referenced=True):
    files = {
        "_system/products.yaml": textwrap.dedent(
            """\
            version: "1.0"
            products:
              demo:
                widgetx:
                  label: Widgetx
                other:
                  label: Other
            """
        ),
        "_system/workspaces.yaml": textwrap.dedent(
            """\
            version: "1.0"
            workspaces:
              - {id: main, label: Main, kind: entity, entity: demo, default_view: blocks}
            """
        ),
    }
    if referenced:
        files["demo/07-finance/active/inv.md"] = (
            "---\ntype: note\ntitle: Inv\nentity: demo\nproduct: widgetx\n"
            "status: active\ncreated: 2026-01-01\nupdated: 2026-01-01\n---\n"
        )
        files["_system/workspaces.yaml"] += (
            "  - {id: widgetx, label: Widgetx, kind: product, entity: demo, product: widgetx, default_view: blocks}\n"
        )
    vault = git_entity_vault(tmp_path, ("demo",), files)
    if referenced:
        db = tmp_path / "demo" / "books.db"
        conn = sqlite3.connect(db)
        conn.executescript("CREATE TABLE products (tag TEXT, name TEXT);"
                           "CREATE TABLE invoices (id INTEGER PRIMARY KEY, product TEXT);")
        conn.execute("INSERT INTO products (tag, name) VALUES ('widgetx','x')")
        conn.execute("INSERT INTO invoices (product) VALUES ('widgetx')")
        conn.commit(); conn.close()
        # commit the db so the tree is clean
        import subprocess
        subprocess.run(["git", "add", "-A"], cwd=vault, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-q", "-m", "add db"], cwd=vault, check=True, capture_output=True)
    return vault


@pytest.fixture
def two_entity_registry_vault(tmp_path):
    files = {
        "_system/products.yaml": textwrap.dedent(
            """\
            version: "1.0"
            products:
              alpha:
                shared:
                  label: Alpha Shared
                unused:
                  label: Alpha Unused
                alpha-only:
                  label: Alpha Only
              beta:
                shared:
                  label: Beta Registry Marker
                unused:
                  label: Beta Unused
                beta-only:
                  label: Beta Only
            """
        ),
        "_system/workspaces.yaml": textwrap.dedent(
            """\
            version: "1.0"
            workspaces:
              - id: alpha-cross
                label: Alpha Cross
                kind: cross
                primary_entity: alpha
                entities: [alpha, beta]
                product: shared
                default_view: blocks
            """
        ),
        "alpha/07-finance/active/alpha.md": (
            "---\ntype: note\ntitle: Alpha Registry Marker\nentity: alpha\n"
            "product: shared\nstatus: active\ncreated: 2026-01-01\n"
            "updated: 2026-01-01\n---\nalpha-registry-marker\n"
        ),
        "beta/07-finance/active/beta-one.md": (
            "---\ntype: note\ntitle: Beta Registry Marker One\nentity: beta\n"
            "product: shared\nstatus: active\ncreated: 2026-01-01\n"
            "updated: 2026-01-01\n---\nbeta-registry-marker-one\n"
        ),
        "beta/09-marketing/active/beta-two.md": (
            "---\ntype: note\ntitle: Beta Registry Marker Two\nentity: beta\n"
            "product: shared\nstatus: active\ncreated: 2026-01-01\n"
            "updated: 2026-01-01\n---\nbeta-registry-marker-two\n"
        ),
        "alpha/.sensitive/hidden.md": (
            "---\ntype: note\ntitle: Hidden\nentity: alpha\nproduct: shared\n---\n"
        ),
        "alpha/outbox/ignored.md": (
            "---\ntype: note\ntitle: Outbox\nentity: alpha\nproduct: shared\n---\n"
        ),
        "alpha/staging/ignored.md": (
            "---\ntype: note\ntitle: Staging\nentity: alpha\nproduct: shared\n---\n"
        ),
    }
    vault = git_entity_vault(tmp_path, ("alpha", "beta"), files)
    (vault / "alpha/07-finance/active/beta-link.md").symlink_to(
        vault / "beta/07-finance/active/beta-one.md"
    )
    for entity, rows in (("alpha", ("product", "tag")), ("beta", ("product",))):
        connection = sqlite3.connect(vault / entity / "books.db")
        connection.executescript(
            "CREATE TABLE entries (id INTEGER PRIMARY KEY, product TEXT, tag TEXT);"
        )
        for column in rows:
            connection.execute(f"INSERT INTO entries ({column}) VALUES (?)", ("shared",))
        connection.commit()
        connection.close()
    import subprocess

    subprocess.run(["git", "add", "-A"], cwd=vault, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "add registry fixtures"],
        cwd=vault,
        check=True,
        capture_output=True,
    )
    return vault


def test_reference_count_finds_every_reference(tmp_path):
    vault = _products_vault(tmp_path, referenced=True)
    r = reference_count(Scope(vault, "demo"), "product", "widgetx")
    assert r.sources.get("front-matter") == 1
    assert r.sources.get("workspaces") == 1
    assert r.sources.get("books.db") == 2      # products.tag + invoices.product
    assert r.total == 4


def test_reference_count_zero_when_unused(tmp_path):
    vault = _products_vault(tmp_path, referenced=False)
    r = reference_count(Scope(vault, "demo"), "product", "widgetx")
    assert r.total == 0


def test_reference_count_reads_only_bound_entity(two_entity_registry_vault):
    alpha = reference_count(
        Scope(two_entity_registry_vault, "alpha"), "product", "shared"
    )
    beta = reference_count(
        Scope(two_entity_registry_vault, "beta"), "product", "shared"
    )
    assert alpha.sources == {"front-matter": 1, "workspaces": 1, "books.db": 2}
    assert beta.sources == {"front-matter": 2, "workspaces": 0, "books.db": 1}


def test_reference_count_never_opens_another_entity_or_sensitive_paths(
    two_entity_registry_vault, monkeypatch
):
    scope = Scope(two_entity_registry_vault, "alpha")
    beta_root = (two_entity_registry_vault / "beta").resolve()
    hidden = (two_entity_registry_vault / "alpha/.sensitive/hidden.md").resolve()
    beta_db = (beta_root / "books.db").resolve()
    real_read_text = registry.Path.read_text
    real_connect = registry.sqlite3.connect

    def guarded_read_text(path, *args, **kwargs):
        resolved = path.resolve()
        if resolved == hidden or (
            resolved.is_relative_to(beta_root) and resolved.suffix == ".md"
        ):
            raise AssertionError(f"forbidden registry read: {resolved.name}")
        return real_read_text(path, *args, **kwargs)

    def guarded_connect(database, *args, **kwargs):
        if str(beta_db) in str(database):
            raise AssertionError("beta books.db was opened from alpha scope")
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(registry.Path, "read_text", guarded_read_text)
    monkeypatch.setattr(registry.sqlite3, "connect", guarded_connect)

    report = reference_count(scope, "product", "shared")
    assert report.sources == {"front-matter": 1, "workspaces": 1, "books.db": 2}


def test_reference_count_does_not_follow_cross_entity_books_db_symlink(
    two_entity_registry_vault, monkeypatch
):
    scope = Scope(two_entity_registry_vault, "alpha")
    alpha_db = two_entity_registry_vault / "alpha/books.db"
    beta_db = (two_entity_registry_vault / "beta/books.db").resolve()
    alpha_db.rename(two_entity_registry_vault / "alpha/original-books.db")
    alpha_db.symlink_to(beta_db)
    real_connect = registry.sqlite3.connect

    def guarded_connect(database, *args, **kwargs):
        raw_path = str(database).removeprefix("file:").split("?", 1)[0]
        if registry.Path(raw_path).resolve() == beta_db:
            raise AssertionError("beta books.db was opened through an alpha symlink")
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(registry.sqlite3, "connect", guarded_connect)

    report = reference_count(scope, "product", "shared")
    assert report.sources == {"front-matter": 1, "workspaces": 1, "books.db": 0}


def test_products_for_reads_only_bound_registry_namespace(two_entity_registry_vault):
    assert registry.products_for(Scope(two_entity_registry_vault, "alpha")) == [
        "shared",
        "unused",
        "alpha-only",
    ]
    assert registry.products_for(Scope(two_entity_registry_vault, "beta")) == [
        "shared",
        "unused",
        "beta-only",
    ]


def test_add_workspace_is_direct_and_commits(tmp_path):
    vault = _products_vault(tmp_path, referenced=False)
    scope = Scope(vault, "demo")
    before = git_count_commits(vault)
    add_workspace(scope, {"id": "rti", "label": "RTI", "kind": "matter", "entity": "demo"})
    ws = (vault / "_system/workspaces.yaml").read_text()
    assert "id: rti" in ws
    assert git_count_commits(vault) == before + 1
    assert git_head_message(vault).startswith("registry: add workspace")
    assert git_is_clean(vault)


def test_propose_delete_writes_impact_and_removes_nothing(tmp_path):
    vault = _products_vault(tmp_path, referenced=True)
    scope = Scope(vault, "demo")
    prop = propose_delete(scope, "product", "widgetx")
    assert prop.path.exists()
    text = prop.path.read_text()
    assert "front-matter" in text and "books.db" in text     # impact recorded
    # still present in the registry — nothing deleted yet
    assert "widgetx:" in (vault / "_system/products.yaml").read_text()


def test_propose_delete_keeps_untrusted_slug_out_of_proposal_filename(tmp_path):
    vault = _products_vault(tmp_path, referenced=False)
    scope = Scope(vault, "demo")
    target = scope.resolve("13-analytics", "kpis.yaml")
    target.parent.mkdir(parents=True)
    original = b"version: '1.0'\nkpis: unchanged\n"
    target.write_bytes(original)
    slug = "../../../13-analytics/kpis"

    proposal = propose_delete(scope, "product", slug)

    assert proposal.path.parent == scope.resolve("outbox")
    assert yaml.safe_load(proposal.path.read_text())["slug"] == slug
    assert target.read_bytes() == original


def test_propose_delete_rejects_redirected_outbox_without_creating_target(
    tmp_path,
):
    vault = _products_vault(tmp_path, referenced=False)
    scope = Scope(vault, "demo")
    redirected = vault / "demo/redirected-outbox"
    outbox_link = vault / "demo/outbox"
    outbox_link.parent.mkdir(parents=True)
    outbox_link.symlink_to(redirected)

    with pytest.raises(CrossScopeError):
        propose_delete(scope, "product", "widgetx")

    assert not redirected.exists()


def test_propose_delete_preserves_collision_before_writing_later_candidate(
    tmp_path, monkeypatch
):
    vault = _products_vault(tmp_path, referenced=False)
    scope = Scope(vault, "demo")
    first_id = "20260815T090703-" + "ab" * 16
    second_id = "20260815T090703-" + "cd" * 16
    first_path = scope.resolve("outbox", f"{first_id}.yaml")
    first_path.parent.mkdir(parents=True)
    original = b"pre-existing collision\n"
    first_path.write_bytes(original)
    monkeypatch.setattr(
        registry, "proposal_id_candidates", lambda created: iter((first_id, second_id))
    )

    proposal = propose_delete(scope, "product", "widgetx")

    assert first_path.read_bytes() == original
    assert proposal.id == second_id
    assert proposal.path == scope.resolve("outbox", f"{second_id}.yaml")
    assert proposal.path.exists()


def test_propose_delete_raises_after_four_collisions_without_modifying_files(
    tmp_path, monkeypatch
):
    vault = _products_vault(tmp_path, referenced=False)
    scope = Scope(vault, "demo")
    proposal_ids = tuple(
        f"20260815T090703-{'ab' * 15}{suffix}"
        for suffix in ("aa", "bb", "cc", "dd")
    )
    outbox = scope.resolve("outbox")
    outbox.mkdir(parents=True)
    originals = {
        proposal_id: f"pre-existing {proposal_id}\n".encode()
        for proposal_id in proposal_ids
    }
    for proposal_id, original in originals.items():
        (outbox / f"{proposal_id}.yaml").write_bytes(original)
    monkeypatch.setattr(
        registry,
        "proposal_id_candidates",
        lambda created: iter(proposal_ids),
    )

    with pytest.raises(
        RegistryError, match="^unable to allocate a unique delete proposal id$"
    ):
        propose_delete(scope, "product", "widgetx")

    assert {
        proposal_id: (outbox / f"{proposal_id}.yaml").read_bytes()
        for proposal_id in proposal_ids
    } == originals


def test_same_second_delete_proposals_are_distinct_and_preserved(
    tmp_path, monkeypatch
):
    vault = _products_vault(tmp_path, referenced=False)
    scope = Scope(vault, "demo")
    monkeypatch.setattr(registry, "datetime", _FixedDatetime)

    first = propose_delete(scope, "product", "widgetx")
    second = propose_delete(scope, "product", "widgetx")

    assert first.id != second.id
    assert first.path != second.path
    assert first.path.exists() and second.path.exists()
    assert first.path.stem == first.id
    assert second.path.stem == second.id


def test_delete_record_id_must_equal_filename(tmp_path):
    vault = _products_vault(tmp_path, referenced=False)
    scope = Scope(vault, "demo")
    prop = propose_delete(scope, "product", "widgetx")
    record = yaml.safe_load(prop.path.read_text(encoding="utf-8"))
    record["id"] = "20260815T090703-" + "ab" * 16
    prop.path.write_text(yaml.safe_dump(record), encoding="utf-8")

    with pytest.raises(RegistryError):
        get_delete_proposal(scope, prop.path.stem)


def test_delete_proposal_id_cannot_traverse_outbox_or_unlink_entity_file(tmp_path):
    vault = _products_vault(tmp_path, referenced=False)
    scope = Scope(vault, "demo")
    target = scope.resolve("13-analytics", "kpis.yaml")
    target.parent.mkdir(parents=True)
    target.write_text(
        textwrap.dedent(
            """\
            id: ../13-analytics/kpis
            action: delete
            entity: demo
            kind: product
            slug: widgetx
            status: pending
            total_references: 0
            impact: {}
            """
        ),
        encoding="utf-8",
    )
    target_before = target.read_bytes()
    registry = scope.system_path("products.yaml")
    registry_before = registry.read_bytes()
    head_before = git_head(vault)

    with pytest.raises(RegistryError):
        get_delete_proposal(scope, "../13-analytics/kpis")
    with pytest.raises(RegistryError):
        execute_delete(scope, "../13-analytics/kpis")

    assert target.read_bytes() == target_before
    assert registry.read_bytes() == registry_before
    assert git_head(vault) == head_before


def test_execute_delete_refuses_while_referenced(tmp_path):
    vault = _products_vault(tmp_path, referenced=True)
    scope = Scope(vault, "demo")
    prop = propose_delete(scope, "product", "widgetx")
    try:
        execute_delete(scope, prop.id)
        assert False, "should have refused"
    except RegistryError:
        pass
    assert "widgetx:" in (vault / "_system/products.yaml").read_text()


def test_execute_delete_removes_when_unreferenced(tmp_path):
    vault = _products_vault(tmp_path, referenced=False)
    scope = Scope(vault, "demo")
    prop = propose_delete(scope, "product", "widgetx")
    execute_delete(scope, prop.id)
    prods = (vault / "_system/products.yaml").read_text()
    assert "widgetx:" not in prods
    assert "other:" in prods                    # sibling untouched
    assert git_head_message(vault).startswith("registry: delete product")
    assert git_is_clean(vault)


def test_delete_removes_only_bound_registry_key(two_entity_registry_vault):
    scope = Scope(two_entity_registry_vault, "alpha")
    proposal = propose_delete(scope, "product", "unused")
    execute_delete(scope, proposal.id)
    cfg = yaml.safe_load(scope.system_path("products.yaml").read_text())
    assert "unused" not in cfg["products"]["alpha"]
    assert "unused" in cfg["products"]["beta"]


def test_forged_delete_proposal_cannot_be_read_or_executed(
    two_entity_registry_vault,
):
    scope = Scope(two_entity_registry_vault, "alpha")
    proposal = scope.resolve("outbox", "forged-delete.yaml")
    proposal.parent.mkdir(parents=True, exist_ok=True)
    proposal.write_text(
        textwrap.dedent(
            """\
            id: forged-delete
            action: delete
            entity: beta
            kind: product
            slug: unused
            status: pending
            total_references: 0
            impact: {}
            """
        ),
        encoding="utf-8",
    )
    registry_before = scope.system_path("products.yaml").read_bytes()
    head_before = git_head(two_entity_registry_vault)

    with pytest.raises(RegistryError):
        get_delete_proposal(scope, "forged-delete")
    with pytest.raises(RegistryError):
        execute_delete(scope, "forged-delete")

    assert scope.system_path("products.yaml").read_bytes() == registry_before
    assert git_head(two_entity_registry_vault) == head_before


def test_delete_proposal_read_rejects_cross_entity_leaf_symlink(
    two_entity_registry_vault,
):
    scope = Scope(two_entity_registry_vault, "alpha")
    proposal_id = "20260815T090703-" + "ab" * 16
    foreign = two_entity_registry_vault / "beta/outbox" / f"{proposal_id}.yaml"
    foreign.parent.mkdir(parents=True, exist_ok=True)
    foreign.write_text(
        f"id: {proposal_id}\naction: delete\nentity: beta\nkind: product\nslug: unused\n",
        encoding="utf-8",
    )
    linked = scope.resolve("outbox") / f"{proposal_id}.yaml"
    linked.parent.mkdir(parents=True, exist_ok=True)
    linked.symlink_to(foreign)

    with pytest.raises(CrossScopeError):
        get_delete_proposal(scope, proposal_id)


def test_registry_interfaces_have_one_identity_authority():
    for function in (propose_delete, get_delete_proposal, execute_delete):
        assert "entity" not in inspect.signature(function).parameters


def test_add_workspace_rejects_another_entity_entry(two_entity_registry_vault):
    scope = Scope(two_entity_registry_vault, "alpha")
    path = scope.system_path("workspaces.yaml")
    before = path.read_bytes()
    head_before = git_head(two_entity_registry_vault)

    with pytest.raises(RegistryError):
        add_workspace(
            scope,
            {
                "id": "beta-cross",
                "label": "Beta Cross",
                "kind": "cross",
                "primary_entity": "beta",
                "entities": ["alpha", "beta"],
            },
        )

    assert path.read_bytes() == before
    assert git_head(two_entity_registry_vault) == head_before
