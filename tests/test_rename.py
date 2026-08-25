"""rename — first-class, tested, atomic (spec §2.2c, conventions-v2.1 §7).

Every test runs on a throwaway git vault under tmp_path; the real vault is
never touched. The safety-critical assertion (BUILD §4): an entity rename must
rewrite BOTH halves of the fail-open action-policy rule atomically — the allow
`paths:` and its `except:` for `.sensitive/` — with no residual old slug left.
"""
import os
from pathlib import Path
import stat
import subprocess
import textwrap

import pytest

import app.git_transaction as git_transaction
import app.rename as rename
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
    git_cached_diff,
    git_head,
    git_index_entries,
    git_status_bytes,
    git_vault,
    git_worktree_diff,
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


def _rename_boundary(vault):
    """Git state and every non-Git vault object, including ignored proposals."""
    entries = []
    for path in sorted(vault.rglob("*")):
        relative = path.relative_to(vault)
        if relative.parts and relative.parts[0] == ".git":
            continue
        found = os.lstat(path)
        if stat.S_ISREG(found.st_mode):
            value = ("file", stat.S_IMODE(found.st_mode), path.read_bytes())
        elif stat.S_ISDIR(found.st_mode):
            value = ("directory", stat.S_IMODE(found.st_mode), None)
        elif stat.S_ISLNK(found.st_mode):
            value = ("symlink", stat.S_IMODE(found.st_mode), os.readlink(path))
        else:
            value = ("other", stat.S_IMODE(found.st_mode), None)
        entries.append((relative.as_posix(), value))
    return (
        git_head(vault),
        git_index_entries(vault),
        git_cached_diff(vault),
        git_worktree_diff(vault),
        git_status_bytes(vault),
        tuple(entries),
    )


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


def test_entity_rename_busy_shared_lock_refuses_before_any_mutation(
    tmp_path, monkeypatch
):
    """The sanctioned root move must cooperate with reviewed actions."""
    files = dict(ENTITY_FILES)
    files[".gitignore"] = "*/outbox/*.yaml\n"
    vault = git_vault(tmp_path, files)
    proposal = vault / "oldentity/outbox/20260825T120000-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.yaml"
    proposal.parent.mkdir(parents=True)
    proposal.write_bytes(b"entity: oldentity\nstatus: pending\n")
    plan = plan_rename(vault, "entity", "oldentity", "newentity")
    boundary = _rename_boundary(vault)
    rename_git_calls = []
    real_git = rename._git

    def record_rename_git_call(vault_arg, *args):
        rename_git_calls.append(args)
        return real_git(vault_arg, *args)

    monkeypatch.setattr(rename, "_git", record_rename_git_call)
    shared_lock = getattr(
        git_transaction, "action_lock", git_transaction._approval_lock
    )

    with shared_lock(vault):
        with pytest.raises(RenameError, match="vault is busy"):
            apply_rename(vault, plan)

        assert _rename_boundary(vault) == boundary
        assert (vault / "oldentity").is_dir()
        assert not (vault / "newentity").exists()
        assert proposal.read_bytes() == b"entity: oldentity\nstatus: pending\n"
        assert rename_git_calls == [], (
            "busy refusal reached the rename Git boundary, where mv, reset, "
            "clean, or another mutation could run"
        )

    assert shared_lock is git_transaction.action_lock


def test_stale_rename_plan_refuses_before_mutation_and_preserves_newer_commit(
    tmp_path, monkeypatch
):
    """A reviewed plan may apply only to the exact HEAD it was built from."""
    vault = git_vault(tmp_path, ENTITY_FILES)
    plan = plan_rename(vault, "entity", "oldentity", "newentity")

    entities = vault / "_system/entities.yaml"
    newer_bytes = entities.read_bytes().replace(
        b"label: Old Entity", b"label: Newer Committed Label"
    )
    entities.write_bytes(newer_bytes)
    subprocess.run(
        ["git", "add", "--", "_system/entities.yaml"], cwd=vault, check=True
    )
    subprocess.run(
        ["git", "commit", "-q", "-m", "newer relevant change"],
        cwd=vault,
        check=True,
    )
    assert git_is_clean(vault)
    boundary = _rename_boundary(vault)
    rename_git_calls = []
    real_git = rename._git

    def record_rename_git_call(vault_arg, *args):
        rename_git_calls.append(args)
        return real_git(vault_arg, *args)

    monkeypatch.setattr(rename, "_git", record_rename_git_call)

    with pytest.raises(RenameError, match="HEAD changed since this rename was planned"):
        apply_rename(vault, plan)

    assert _rename_boundary(vault) == boundary
    assert entities.read_bytes() == newer_bytes
    assert git_head_message(vault) == "newer relevant change"
    assert (vault / "oldentity").is_dir()
    assert not (vault / "newentity").exists()
    assert rename_git_calls == [
        ("status", "--short"),
        ("rev-parse", "HEAD"),
    ], "stale refusal crossed into Git mutation or cleanup"


def test_rename_plan_from_another_vault_refuses_before_lock_or_mutation(
    tmp_path, monkeypatch
):
    planned_vault = git_vault(tmp_path / "planned", ENTITY_FILES)
    execution_vault = tmp_path / "execution"
    subprocess.run(
        ["git", "clone", "-q", str(planned_vault), str(execution_vault)],
        check=True,
    )
    assert git_head(planned_vault) == git_head(execution_vault)
    plan = plan_rename(
        planned_vault, "entity", "oldentity", "newentity"
    )
    planned_boundary = _rename_boundary(planned_vault)
    execution_boundary = _rename_boundary(execution_vault)
    lock_calls = []
    git_calls = []
    real_git = rename._git

    def forbidden_action_lock(vault_arg):
        lock_calls.append(vault_arg)
        raise AssertionError("cross-vault refusal reached action_lock")

    def record_rename_git_call(vault_arg, *args):
        git_calls.append((vault_arg, args))
        return real_git(vault_arg, *args)

    monkeypatch.setattr(rename, "action_lock", forbidden_action_lock)
    monkeypatch.setattr(rename, "_git", record_rename_git_call)

    with pytest.raises(RenameError, match="different vault"):
        apply_rename(execution_vault, plan)

    assert lock_calls == []
    assert git_calls == []
    assert _rename_boundary(planned_vault) == planned_boundary
    assert _rename_boundary(execution_vault) == execution_boundary


def test_rename_plan_accepts_relative_and_absolute_names_for_same_vault(
    tmp_path, monkeypatch
):
    vault = git_vault(tmp_path / "same-vault", ENTITY_FILES)
    monkeypatch.chdir(tmp_path)
    plan = plan_rename(
        Path("same-vault"), "entity", "oldentity", "newentity"
    )

    apply_rename(vault.resolve(), plan)

    assert (vault / "newentity").is_dir()
    assert not (vault / "oldentity").exists()
    assert git_head_message(vault) == "rename: oldentity → newentity"
    assert git_is_clean(vault)


def test_rename_keeps_planned_vault_when_symlink_alias_is_retargeted_before_lock(
    tmp_path, monkeypatch
):
    planned_vault = git_vault(tmp_path / "planned", ENTITY_FILES)
    alternate_vault = tmp_path / "alternate"
    subprocess.run(
        ["git", "clone", "-q", str(planned_vault), str(alternate_vault)],
        check=True,
    )
    alias = tmp_path / "vault-alias"
    alias.symlink_to(planned_vault, target_is_directory=True)
    plan = plan_rename(alias, "entity", "oldentity", "newentity")
    alternate_boundary = _rename_boundary(alternate_vault)
    real_action_lock = rename.action_lock
    retargeted = False

    def retarget_before_lock(vault_arg):
        nonlocal retargeted
        alias.unlink()
        alias.symlink_to(alternate_vault, target_is_directory=True)
        retargeted = True
        return real_action_lock(vault_arg)

    monkeypatch.setattr(rename, "action_lock", retarget_before_lock)

    apply_rename(alias, plan)

    assert retargeted, "the test never retargeted the vault alias before locking"
    assert (planned_vault / "newentity").is_dir()
    assert not (planned_vault / "oldentity").exists()
    assert _rename_boundary(alternate_vault) == alternate_boundary, (
        "retargeting the alias redirected the reviewed rename into another vault"
    )


@pytest.mark.parametrize("failure_point", ("unlock", "close"))
def test_entity_rename_lock_cleanup_failure_reports_committed_without_rollback(
    tmp_path, monkeypatch, failure_point
):
    files = dict(ENTITY_FILES)
    files[".gitignore"] = "*/outbox/*.yaml\n"
    vault = git_vault(tmp_path, files)
    pending = vault / "oldentity/outbox/20260825T120000-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.yaml"
    pending.parent.mkdir(parents=True)
    pending.write_bytes(b"entity: oldentity\nstatus: pending\n")
    plan = plan_rename(vault, "entity", "oldentity", "newentity")
    rename_git_calls = []
    real_git = rename._git

    def record_rename_git_call(vault_arg, *args):
        rename_git_calls.append(args)
        return real_git(vault_arg, *args)

    monkeypatch.setattr(rename, "_git", record_rename_git_call)
    if failure_point == "unlock":
        real_flock = git_transaction.fcntl.flock

        def fail_unlock(descriptor, operation):
            if operation == git_transaction.fcntl.LOCK_UN:
                raise OSError("injected rename unlock failure")
            return real_flock(descriptor, operation)

        monkeypatch.setattr(git_transaction.fcntl, "flock", fail_unlock)
    else:
        real_open = git_transaction.os.open
        real_close = git_transaction.os.close
        lock_descriptors = set()

        def record_lock_open(path, *args, **kwargs):
            descriptor = real_open(path, *args, **kwargs)
            if Path(path).name == "oneos-approval.lock":
                lock_descriptors.add(descriptor)
            return descriptor

        def fail_lock_close(descriptor):
            result = real_close(descriptor)
            if descriptor in lock_descriptors:
                raise OSError("injected rename close failure")
            return result

        monkeypatch.setattr(git_transaction.os, "open", record_lock_open)
        monkeypatch.setattr(git_transaction.os, "close", fail_lock_close)

    with pytest.raises(Exception) as raised:
        apply_rename(vault, plan)

    assert type(raised.value).__name__ == "RenameCommittedError"
    assert type(raised.value) is rename.RenameCommittedError
    assert raised.value.commit_oid == git_head(vault)
    assert "committed" in str(raised.value).lower()
    assert "do not retry" in str(raised.value).lower()
    assert (vault / "newentity").is_dir()
    assert not (vault / "oldentity").exists()
    assert (
        vault
        / "newentity/outbox/20260825T120000-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.yaml"
    ).read_bytes() == b"entity: newentity\nstatus: pending\n"
    assert not any(args[0] in {"reset", "clean"} for args in rename_git_calls)
    assert git_head_message(vault) == "rename: oldentity → newentity"
    assert git_count_commits(vault) == 2
    assert git_is_clean(vault)


def test_entity_rename_precommit_lock_open_failure_is_a_safe_refusal(
    tmp_path, monkeypatch
):
    vault = git_vault(tmp_path, ENTITY_FILES)
    plan = plan_rename(vault, "entity", "oldentity", "newentity")
    boundary = _rename_boundary(vault)
    real_open = git_transaction.os.open
    rename_git_calls = []

    def fail_lock_open(path, *args, **kwargs):
        if Path(path).name == "oneos-approval.lock":
            raise OSError("injected lock open failure")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(git_transaction.os, "open", fail_lock_open)
    real_git = rename._git

    def record_real_rename_git_call(vault_arg, *args):
        rename_git_calls.append(args)
        return real_git(vault_arg, *args)

    monkeypatch.setattr(rename, "_git", record_real_rename_git_call)

    with pytest.raises(RenameError, match="shared action lock is unavailable"):
        apply_rename(vault, plan)

    assert _rename_boundary(vault) == boundary
    assert (vault / "oldentity").is_dir()
    assert not (vault / "newentity").exists()
    assert rename_git_calls == []


def test_rename_cli_reports_committed_lock_cleanup_failure_without_traceback(
    tmp_path, monkeypatch, capsys
):
    files = dict(ENTITY_FILES)
    files[".gitignore"] = "*/outbox/*.yaml\n"
    vault = git_vault(tmp_path, files)
    pending = vault / "oldentity/outbox/20260825T120000-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.yaml"
    pending.parent.mkdir(parents=True)
    pending.write_bytes(b"entity: oldentity\nstatus: pending\n")
    real_flock = git_transaction.fcntl.flock
    real_git = rename._git
    rename_git_calls = []

    def fail_unlock(descriptor, operation):
        if operation == git_transaction.fcntl.LOCK_UN:
            raise OSError("injected CLI unlock failure")
        return real_flock(descriptor, operation)

    def record_rename_git_call(vault_arg, *args):
        rename_git_calls.append(args)
        return real_git(vault_arg, *args)

    monkeypatch.setattr(git_transaction.fcntl, "flock", fail_unlock)
    monkeypatch.setattr(rename, "_git", record_rename_git_call)

    result = rename.main(
        ["entity", "oldentity", "newentity", "--vault-root", str(vault), "--apply"]
    )
    output = capsys.readouterr()

    assert result == 2
    assert "[COMMITTED]" in output.out
    assert "committed" in output.out.lower()
    assert "do not retry" in output.out.lower()
    assert git_head(vault) in output.out
    assert output.err == ""
    assert (vault / "newentity").is_dir()
    assert not (vault / "oldentity").exists()
    assert (
        vault
        / "newentity/outbox/20260825T120000-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.yaml"
    ).read_bytes() == b"entity: newentity\nstatus: pending\n"
    assert not any(args[0] in {"reset", "clean"} for args in rename_git_calls)


def test_rename_cli_reports_committed_when_postcommit_oid_lookup_fails(
    tmp_path, monkeypatch, capsys
):
    files = dict(ENTITY_FILES)
    files[".gitignore"] = "*/outbox/*.yaml\n"
    vault = git_vault(tmp_path, files)
    pending = vault / "oldentity/outbox/20260825T120000-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.yaml"
    pending.parent.mkdir(parents=True)
    pending.write_bytes(b"entity: oldentity\nstatus: pending\n")
    real_git = rename._git
    rename_git_calls = []

    def fail_only_postcommit_oid_lookup(vault_arg, *args):
        rename_git_calls.append(args)
        if (
            args == ("rev-parse", "HEAD")
            and git_head_message(vault_arg) == "rename: oldentity → newentity"
        ):
            raise subprocess.CalledProcessError(73, ["git", *args])
        return real_git(vault_arg, *args)

    monkeypatch.setattr(rename, "_git", fail_only_postcommit_oid_lookup)

    result = rename.main(
        ["entity", "oldentity", "newentity", "--vault-root", str(vault), "--apply"]
    )
    output = capsys.readouterr()

    assert result == 2
    assert "[COMMITTED]" in output.out
    assert "committed" in output.out.lower()
    assert "unknown/unavailable" in output.out.lower()
    assert "do not retry" in output.out.lower()
    assert "[ABORTED]" not in output.out
    assert "rolled back" not in output.out.lower()
    assert output.err == ""
    assert (vault / "newentity").is_dir()
    assert not (vault / "oldentity").exists()
    assert (
        vault
        / "newentity/outbox/20260825T120000-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.yaml"
    ).read_bytes() == b"entity: newentity\nstatus: pending\n"
    assert not any(args[0] in {"reset", "clean"} for args in rename_git_calls)
    assert git_head_message(vault) == "rename: oldentity → newentity"
    assert git_count_commits(vault) == 2
    assert git_is_clean(vault)


def test_postcommit_oid_lookup_and_unlock_failures_preserve_committed_outcome(
    tmp_path, monkeypatch
):
    files = dict(ENTITY_FILES)
    files[".gitignore"] = "*/outbox/*.yaml\n"
    vault = git_vault(tmp_path, files)
    pending = vault / "oldentity/outbox/20260825T120000-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.yaml"
    pending.parent.mkdir(parents=True)
    pending.write_bytes(b"entity: oldentity\nstatus: pending\n")
    plan = plan_rename(vault, "entity", "oldentity", "newentity")
    real_git = rename._git
    real_flock = git_transaction.fcntl.flock
    rename_git_calls = []

    def fail_only_postcommit_oid_lookup(vault_arg, *args):
        rename_git_calls.append(args)
        if (
            args == ("rev-parse", "HEAD")
            and git_head_message(vault_arg) == "rename: oldentity → newentity"
        ):
            raise subprocess.CalledProcessError(74, ["git", *args])
        return real_git(vault_arg, *args)

    def fail_unlock(descriptor, operation):
        if operation == git_transaction.fcntl.LOCK_UN:
            raise OSError("injected cleanup failure after OID lookup failure")
        return real_flock(descriptor, operation)

    monkeypatch.setattr(rename, "_git", fail_only_postcommit_oid_lookup)
    monkeypatch.setattr(git_transaction.fcntl, "flock", fail_unlock)

    with pytest.raises(Exception) as raised:
        apply_rename(vault, plan)

    assert type(raised.value) is rename.RenameCommittedError
    assert raised.value.commit_oid is None
    assert "unknown/unavailable" in str(raised.value).lower()
    assert "committed" in str(raised.value).lower()
    assert "do not retry" in str(raised.value).lower()
    assert isinstance(raised.value.__cause__, subprocess.CalledProcessError)
    notes = getattr(raised.value, "__notes__", ())
    assert any("approval lock cleanup also failed" in note for note in notes)
    assert any("injected cleanup failure" in note for note in notes)
    assert not any(args[0] in {"reset", "clean"} for args in rename_git_calls)
    assert (vault / "newentity").is_dir()
    assert not (vault / "oldentity").exists()
    assert (
        vault
        / "newentity/outbox/20260825T120000-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.yaml"
    ).read_bytes() == b"entity: newentity\nstatus: pending\n"
    assert git_head_message(vault) == "rename: oldentity → newentity"
    assert git_count_commits(vault) == 2
    assert git_is_clean(vault)


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
