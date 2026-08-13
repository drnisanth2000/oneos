"""Registry CRUD (spec §10 step 9, §2.2b).

Add and edit go direct; delete goes through the outbox with a reference count
that shows what breaks before it runs, and refuses if references remain
(ADR-006 consequence test). Temp git vaults only.
"""
import sqlite3
import textwrap

from app.scope import Scope
from app.registry import (
    RegistryError,
    add_workspace,
    execute_delete,
    propose_delete,
    reference_count,
)
from tests.conftest import git_entity_vault, git_count_commits, git_head_message, git_is_clean


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
    prop = propose_delete(scope, "demo", "product", "widgetx")
    assert prop.path.exists()
    text = prop.path.read_text()
    assert "front-matter" in text and "books.db" in text     # impact recorded
    # still present in the registry — nothing deleted yet
    assert "widgetx:" in (vault / "_system/products.yaml").read_text()


def test_execute_delete_refuses_while_referenced(tmp_path):
    vault = _products_vault(tmp_path, referenced=True)
    scope = Scope(vault, "demo")
    prop = propose_delete(scope, "demo", "product", "widgetx")
    try:
        execute_delete(scope, "demo", prop.id)
        assert False, "should have refused"
    except RegistryError:
        pass
    assert "widgetx:" in (vault / "_system/products.yaml").read_text()


def test_execute_delete_removes_when_unreferenced(tmp_path):
    vault = _products_vault(tmp_path, referenced=False)
    scope = Scope(vault, "demo")
    prop = propose_delete(scope, "demo", "product", "widgetx")
    execute_delete(scope, "demo", prop.id)
    prods = (vault / "_system/products.yaml").read_text()
    assert "widgetx:" not in prods
    assert "other:" in prods                    # sibling untouched
    assert git_head_message(vault).startswith("registry: delete product")
    assert git_is_clean(vault)
