"""rename — first-class, tested, atomic (spec §2.2c, conventions-v2.1 §7).

Every test runs on a throwaway git vault under tmp_path; the real vault is
never touched. The safety-critical assertion (BUILD §4): an entity rename must
rewrite BOTH halves of the fail-open action-policy rule atomically — the allow
`paths:` and its `except:` for `.sensitive/` — with no residual old slug left.
"""
import textwrap

import pytest

from app.rename import (
    RenameError,
    apply_rename,
    grep_gate,
    plan_rename,
    render_diff,
)
from tests.conftest import (
    git_count_commits,
    git_head_message,
    git_is_clean,
    git_vault,
)

# A throwaway vault whose fail-open rule mirrors the real action-policy.yaml:95.
ENTITY_FILES = {
    "_system/entities.yaml": textwrap.dedent(
        """\
        # OneOS entities
        version: "1.0"
        entities:
          oldentity:
            label: Old Entity
            flags: [personal]
        """
    ),
    "_system/action-policy.yaml": textwrap.dedent(
        """\
        version: 1.0
        default: deny
        actors:
          hermes:oneos-fs-mcp:
            allow:
              - {action: read,  paths: ["oldentity/**"], except: ["oldentity/.sensitive/**"]}
              - {action: write, paths: ["oldentity/00-inbox/**"]}
            deny:
              - {paths: [".sensitive/**"]}
        rules:
          - id: sensitive-lockout
            deny: {actors: ["hermes:*"], paths: [".sensitive/**"]}
        """
    ),
    "_system/members.yaml": textwrap.dedent(
        """\
        version: "1.0"
        members:
          oldentity:
            - {id: nn, label: NN}
        """
    ),
    "_system/workspaces.yaml": textwrap.dedent(
        """\
        version: "1.0"
        workspaces:
          - {id: oldentity, label: Old, kind: entity, entity: oldentity, default_view: folders}
        """
    ),
    "_system/scripts/oneos_fs_mcp.py": 'VAULT_ROOT = ROOT / "oldentity"  # hardcoded\n',
    "oldentity/index.md": textwrap.dedent(
        """\
        ---
        type: index
        title: Old
        entity: oldentity
        product: null
        status: active
        created: 2026-01-01
        updated: 2026-01-01
        ---
        """
    ),
    "oldentity/00-inbox/active/note.md": textwrap.dedent(
        """\
        ---
        type: note
        title: N
        entity: oldentity
        product: null
        status: active
        created: 2026-01-01
        updated: 2026-01-01
        ---
        body
        """
    ),
    "oldentity/.sensitive/secret.md": "secret\n",
}


def test_entity_rename_rewrites_everything_atomically(tmp_path):
    vault = git_vault(tmp_path, ENTITY_FILES)
    apply_rename(vault, plan_rename(vault, "entity", "oldentity", "newentity"))

    assert (vault / "newentity").is_dir()
    assert not (vault / "oldentity").exists()

    ent = (vault / "_system/entities.yaml").read_text()
    assert "newentity:" in ent
    assert "former_slugs: [oldentity]" in ent

    note = (vault / "newentity/00-inbox/active/note.md").read_text()
    assert "entity: newentity" in note

    assert "newentity" in (vault / "_system/scripts/oneos_fs_mcp.py").read_text()

    # Exactly one new commit, working tree clean.
    assert git_head_message(vault) == "rename: oldentity → newentity"
    assert git_count_commits(vault) == 2
    assert git_is_clean(vault)


def test_entity_rename_updates_both_halves_of_the_fail_open_rule(tmp_path):
    """The BUILD §4 danger: rename the allow path, miss the except, and
    .sensitive/ becomes agent-readable."""
    vault = git_vault(tmp_path, ENTITY_FILES)
    apply_rename(vault, plan_rename(vault, "entity", "oldentity", "newentity"))

    pol = (vault / "_system/action-policy.yaml").read_text()
    assert 'paths: ["newentity/**"]' in pol
    assert 'except: ["newentity/.sensitive/**"]' in pol      # the second half
    assert 'paths: ["newentity/00-inbox/**"]' in pol
    assert "oldentity" not in pol                            # no residual anywhere


def test_dry_run_touches_nothing(tmp_path):
    vault = git_vault(tmp_path, ENTITY_FILES)
    plan = plan_rename(vault, "entity", "oldentity", "newentity")
    diff = render_diff(plan)
    assert "oldentity" in diff and "newentity" in diff
    assert (vault / "oldentity").is_dir()          # untouched
    assert git_count_commits(vault) == 1
    assert git_is_clean(vault)


def test_apply_rolls_back_on_validator_failure(tmp_path):
    vault = git_vault(tmp_path, ENTITY_FILES)
    plan = plan_rename(vault, "entity", "oldentity", "newentity")

    def boom(vault, plan):
        raise RenameError("validator says no")

    with pytest.raises(RenameError):
        apply_rename(vault, plan, validators=[boom])

    # Fully rolled back: old tree restored, no new commit, clean.
    assert (vault / "oldentity").is_dir()
    assert not (vault / "newentity").exists()
    assert git_count_commits(vault) == 1
    assert git_is_clean(vault)


def test_grep_gate_flags_residual_and_excludes_former_slugs(tmp_path):
    dirty = git_vault(tmp_path / "a", {"note.md": "still mentions oldentity here\n"})
    assert grep_gate(dirty, "oldentity")

    clean = git_vault(
        tmp_path / "b",
        {"_system/entities.yaml": "entities:\n  new:\n    former_slugs: [oldentity]\n"},
    )
    assert grep_gate(clean, "oldentity") == []


def test_rejects_invalid_or_reserved_new_slug(tmp_path):
    vault = git_vault(tmp_path, ENTITY_FILES)
    for bad in ["Bad_Slug", "UPPER", "has space", "..", "outbox", "active"]:
        with pytest.raises(RenameError):
            plan_rename(vault, "entity", "oldentity", bad)


def test_rejects_new_slug_already_taken(tmp_path):
    files = dict(ENTITY_FILES)
    files["taken/index.md"] = "---\ntype: x\n---\n"
    vault = git_vault(tmp_path, files)
    with pytest.raises(RenameError):
        plan_rename(vault, "entity", "oldentity", "taken")


def test_rejects_unknown_old(tmp_path):
    vault = git_vault(tmp_path, ENTITY_FILES)
    with pytest.raises(RenameError):
        plan_rename(vault, "entity", "ghost", "newentity")


# --- other axes ------------------------------------------------------------

import sqlite3


def _make_books_db(path, product_tag):
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE products (tag TEXT PRIMARY KEY, name TEXT);"
        "CREATE TABLE invoices (id INTEGER PRIMARY KEY, product TEXT);"
    )
    conn.execute("INSERT INTO products (tag, name) VALUES (?, 'x')", (product_tag,))
    conn.execute("INSERT INTO invoices (product) VALUES (?)", (product_tag,))
    conn.commit()
    conn.close()


PRODUCT_FILES = {
    "_system/products.yaml": textwrap.dedent(
        """\
        version: "1.0"
        products:
          acme:
            oldprod:
              label: Old
            other:
              label: Other
        """
    ),
    "_system/workspaces.yaml": textwrap.dedent(
        """\
        version: "1.0"
        workspaces:
          - {id: oldprod, label: Old, kind: product, entity: acme, product: oldprod, default_view: blocks}
        """
    ),
    "acme/07-finance/active/inv.md": textwrap.dedent(
        """\
        ---
        type: note
        title: Inv
        entity: acme
        product: oldprod
        status: active
        created: 2026-01-01
        updated: 2026-01-01
        ---
        """
    ),
}


def test_product_rename_scoped_and_reports_books_db(tmp_path):
    from tests.conftest import write_tree, _git

    write_tree(tmp_path, PRODUCT_FILES)
    _make_books_db(tmp_path / "acme" / "books.db", "oldprod")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@e.com")
    _git(tmp_path, "config", "user.name", "t")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "init")
    vault = tmp_path

    plan = plan_rename(vault, "product", "oldprod", "newprod")
    assert any("books.db" in r for r in plan.reports)  # reported...
    apply_rename(vault, plan)

    prods = (vault / "_system/products.yaml").read_text()
    assert "newprod:" in prods and "former_slugs: [oldprod]" in prods
    assert "other:" in prods  # unrelated key untouched
    assert "product: newprod" in (vault / "acme/07-finance/active/inv.md").read_text()
    assert "product: newprod" in (vault / "_system/workspaces.yaml").read_text()

    # ...but the DB was NOT modified (deferred per spec §2.2c).
    conn = sqlite3.connect(vault / "acme" / "books.db")
    assert conn.execute("SELECT tag FROM products").fetchone()[0] == "oldprod"
    conn.close()
    assert git_is_clean(vault)


def test_member_rename(tmp_path):
    files = {
        "_system/members.yaml": textwrap.dedent(
            """\
            version: "1.0"
            members:
              personal:
                - {id: oldm, label: OldM}
                - {id: nn, label: NN}
            """
        ),
        "personal/07-finance/active/itr.md": textwrap.dedent(
            """\
            ---
            type: note
            title: ITR
            entity: personal
            product: null
            member: oldm
            status: active
            created: 2026-01-01
            updated: 2026-01-01
            ---
            """
        ),
    }
    vault = git_vault(tmp_path, files)
    apply_rename(vault, plan_rename(vault, "member", "oldm", "newm"))
    mem = (vault / "_system/members.yaml").read_text()
    assert "id: newm" in mem and "id: nn" in mem
    assert "member: newm" in (vault / "personal/07-finance/active/itr.md").read_text()
    assert git_is_clean(vault)


def test_workspace_rename(tmp_path):
    files = {
        "_system/workspaces.yaml": textwrap.dedent(
            """\
            version: "1.0"
            workspaces:
              - {id: oldws, label: W, kind: matter, entity: acme, default_view: folders}
            """
        ),
    }
    vault = git_vault(tmp_path, files)
    apply_rename(vault, plan_rename(vault, "workspace", "oldws", "newws"))
    assert "id: newws" in (vault / "_system/workspaces.yaml").read_text()
    assert git_is_clean(vault)


def test_project_rename_moves_dir_and_updates_references(tmp_path):
    files = {
        "acme/02-pipeline/active/oldproj/index.md": textwrap.dedent(
            """\
            ---
            type: project
            title: Old Project
            entity: acme
            product: null
            status: active
            created: 2026-01-01
            updated: 2026-01-01
            repo: ~/code/oldproj
            ---
            See [[oldproj]] for details.
            """
        ),
        "acme/11-knowledge/active/ref.md": textwrap.dedent(
            """\
            ---
            type: note
            title: Ref
            entity: acme
            product: null
            status: active
            created: 2026-01-01
            updated: 2026-01-01
            ---
            Linked [[oldproj]] at acme/02-pipeline/active/oldproj/index.md
            """
        ),
    }
    vault = git_vault(tmp_path, files)
    apply_rename(vault, plan_rename(vault, "project", "oldproj", "newproj"))
    assert (vault / "acme/02-pipeline/active/newproj").is_dir()
    assert not (vault / "acme/02-pipeline/active/oldproj").exists()
    idx = (vault / "acme/02-pipeline/active/newproj/index.md").read_text()
    assert "[[newproj]]" in idx and "repo: ~/code/newproj" in idx
    ref = (vault / "acme/11-knowledge/active/ref.md").read_text()
    assert "[[newproj]]" in ref and "newproj/index.md" in ref
    assert git_is_clean(vault)
