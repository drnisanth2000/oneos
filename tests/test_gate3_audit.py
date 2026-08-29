"""Gate 3 audits sanctioned Git transactions and exact dirty-session state.

Every repository in this file is synthetic.  The tests exercise Git's real
porcelain/name-status formats and the application's runtime registries rather
than duplicating either implementation in mocks.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import textwrap

import pytest
import yaml

from app.action_receipts import (
    make_action_receipt,
    render_action_receipt,
    validate_head_receipt_store,
)
from app.outbox import (
    approve,
    get_proposal_review,
    propose_classification,
    reject,
)
from app.registry import execute_delete, get_delete_review, propose_delete
from app.rename import AXES, apply_rename, plan_rename
from app.scope import Scope
from tests.conftest import git_vault, write_tree
import tools.gate3_audit as gate3


AUDIT_ARCHETYPES = textwrap.dedent(
    """\
    version: "2.0"
    flags:
      gated: "Activates a gated module"
    modules:
      00-inbox:    {block: system}
      02-pipeline: {block: build}
      11-library:  {block: knowledge}
      12-archive:  {block: knowledge, lifecycle_pattern: false}
      15-inactive: {block: self, requires_flag: gated}
    submodules:
      11-library:
        notes: {name: "Notes"}
    """
)

AUDIT_ENTITIES = textwrap.dedent(
    """\
    version: "1.0"
    entities:
      synthetic:
        label: Synthetic
        flags: []
      secondary:
        label: Secondary
        flags: []
    """
)

AUDIT_PROPOSAL_ID = "20260824T120000-" + "ab" * 16

RECEIPT = textwrap.dedent(
    """\
    ---
    type: note
    title: Synthetic receipt
    entity: synthetic
    product: null
    status: active
    created: 2026-01-01
    updated: 2026-01-01
    sub: triage
    ---
    Synthetic body.
    """
)


def _git(vault: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=vault,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _write_blob(vault: Path, contents: bytes) -> str:
    return subprocess.run(
        ["git", "hash-object", "-w", "--stdin"],
        cwd=vault,
        input=contents,
        check=True,
        capture_output=True,
    ).stdout.decode("ascii").strip()


def _set_unmerged_index(
    vault: Path, path: str, stage_oids: tuple[str, str, str]
) -> None:
    _git(vault, "update-index", "--force-remove", "--", path)
    records = "".join(
        f"100644 {oid} {stage}\t{path}\n"
        for stage, oid in enumerate(stage_oids, start=1)
    ).encode("ascii")
    subprocess.run(
        ["git", "update-index", "--index-info"],
        cwd=vault,
        input=records,
        check=True,
        capture_output=True,
    )


def _audit_files() -> dict[str, str]:
    return {
        "_system/archetypes.yaml": AUDIT_ARCHETYPES,
        "_system/entities.yaml": AUDIT_ENTITIES,
        "_system/products.yaml": (
            'version: "1.0"\nproducts:\n  synthetic:\n'
            "    unused: {label: Unused}\n"
        ),
        "_system/members.yaml": 'version: "1.0"\nmembers: {}\n',
        "_system/workspaces.yaml": 'version: "1.0"\nworkspaces: []\n',
        "synthetic/00-inbox/active/receipt.md": RECEIPT,
        "synthetic/11-library/active/.gitkeep": "",
        "synthetic/02-pipeline/active/.gitkeep": "",
        "secondary/00-inbox/active/.gitkeep": "",
        "secondary/11-library/active/.gitkeep": "",
        "secondary/02-pipeline/active/.gitkeep": "",
    }


def _audit_vault(root: Path, *, initialize_git: bool = False) -> Path:
    files = _audit_files()
    if initialize_git:
        return git_vault(root, files)
    write_tree(root, files)
    return root


def _record(
    message: str,
    changes: tuple[tuple[str, str], ...],
    *,
    parents: tuple[str, ...] = ("e" * 40,),
):
    return gate3.CommitRecord(
        oid="f" * 40,
        message=message,
        parents=parents,
        changes=tuple(
            gate3.PathChangeRecord(status=status, path=path)
            for status, path in changes
        ),
    )


def test_gate3_pattern_audits_every_accumulated_head_receipt(tmp_path: Path):
    vault = _audit_vault(tmp_path, initialize_git=True)
    first = "20260824T120000-" + "ab" * 16
    second = "20260824T120001-" + "cd" * 16
    records = {
        first: render_action_receipt(
            make_action_receipt(first, "a" * 64, "approval")
        ),
        second: render_action_receipt(
            make_action_receipt(second, "b" * 64, "registry deletion")
        ),
    }
    for proposal_id, contents in records.items():
        path = vault / "synthetic" / "outbox" / ".receipts" / f"{proposal_id}.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)
    _git(vault, "add", "--", "synthetic/outbox/.receipts")
    _git(vault, "commit", "-q", "-m", "fixture: accumulated receipts")

    receipts = validate_head_receipt_store(vault, "synthetic")

    assert tuple(receipt.proposal_id for receipt in receipts) == (first, second)
    assert not (vault / "synthetic" / "outbox" / f"{first}.yaml").exists(), (
        "offline audit fixture unexpectedly created a pending proposal"
    )


def test_gate3_check_rejects_a_malformed_accumulated_head_receipt(
    tmp_path: Path, monkeypatch
):
    vault = _audit_vault(tmp_path / "vault", initialize_git=True)
    proposal_id = AUDIT_PROPOSAL_ID
    relative = f"synthetic/outbox/.receipts/{proposal_id}.yaml"
    path = vault / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not: a closed receipt\n")
    _git(vault, "add", "--", relative)
    _git(vault, "commit", "-q", "-m", "fixture: malformed receipt")
    snapshot = tmp_path / "gate3-snapshot.json"
    monkeypatch.setenv("ONEOS_VAULT", str(vault))
    monkeypatch.setenv("GATE3_SNAP", str(snapshot))
    assert gate3.main(["snapshot"]) == 0
    (vault / "_system/entities.yaml").write_text(
        'version: "1.0"\nentities: {}\n', encoding="utf-8"
    )

    assert gate3.main(["check"]) == 2, (
        "the real Gate 3 command ignored an invalid accumulated receipt"
    )


@pytest.mark.parametrize(
    ("action_kind", "expected_ok"),
    [("approval", True), ("registry deletion", False)],
)
def test_gate3_approval_envelope_binds_the_receipt_action_kind(
    tmp_path: Path, action_kind: str, expected_ok: bool
):
    vault = _audit_vault(tmp_path, initialize_git=True)
    before = _git(vault, "rev-parse", "HEAD").strip()
    source = vault / "synthetic/00-inbox/active/receipt.md"
    destination = vault / "synthetic/11-library/active/receipt.md"
    source.replace(destination)
    receipt = (
        vault
        / "synthetic/outbox/.receipts"
        / f"{AUDIT_PROPOSAL_ID}.yaml"
    )
    receipt.parent.mkdir(parents=True)
    receipt.write_bytes(
        render_action_receipt(
            make_action_receipt(AUDIT_PROPOSAL_ID, "a" * 64, action_kind)
        )
    )
    _git(vault, "add", "-A")
    _git(
        vault,
        "commit",
        "-q",
        "-m",
        f"outbox: approve {AUDIT_PROPOSAL_ID} (synthetic receipt)",
    )
    after = _git(vault, "rev-parse", "HEAD").strip()
    records = gate3.collect_commit_records(vault, before, after)

    assert gate3.audit_commits(
        records, gate3.AuditRules.load(vault), vault
    ).ok is expected_ok


@pytest.mark.parametrize(
    ("action_kind", "expected_ok"),
    [("registry deletion", True), ("approval", False)],
)
def test_gate3_registry_delete_envelope_binds_the_receipt_action_kind(
    tmp_path: Path, action_kind: str, expected_ok: bool
):
    vault = _audit_vault(tmp_path, initialize_git=True)
    before = _git(vault, "rev-parse", "HEAD").strip()
    registry = vault / "_system/products.yaml"
    registry.write_text(
        registry.read_text(encoding="utf-8") + "# deletion\n",
        encoding="utf-8",
    )
    receipt = (
        vault
        / "synthetic/outbox/.receipts"
        / f"{AUDIT_PROPOSAL_ID}.yaml"
    )
    receipt.parent.mkdir(parents=True)
    receipt.write_bytes(
        render_action_receipt(
            make_action_receipt(AUDIT_PROPOSAL_ID, "a" * 64, action_kind)
        )
    )
    _git(vault, "add", "-A")
    _git(vault, "commit", "-q", "-m", "registry: delete product unused")
    after = _git(vault, "rev-parse", "HEAD").strip()
    records = gate3.collect_commit_records(vault, before, after)

    assert gate3.audit_commits(
        records, gate3.AuditRules.load(vault), vault
    ).ok is expected_ok


@pytest.mark.parametrize(
    ("message", "changes", "valid"),
    [
        (
            "ingest: add redacted receipt",
            (("A", "synthetic/00-inbox/active/r.md"),),
            True,
        ),
        (
            "ingest: misleading",
            (("A", "synthetic/11-library/active/r.md"),),
            False,
        ),
        (
            "ingest: two",
            (
                ("A", "synthetic/00-inbox/active/a.md"),
                ("A", "synthetic/00-inbox/active/b.md"),
            ),
            False,
        ),
        (
            f"outbox: approve {AUDIT_PROPOSAL_ID} (synthetic receipt)",
            (
                ("D", "synthetic/00-inbox/active/r.md"),
                ("A", "synthetic/11-library/active/r.md"),
                (
                    "A",
                    f"synthetic/outbox/.receipts/{AUDIT_PROPOSAL_ID}.yaml",
                ),
            ),
            True,
        ),
        (
            f"outbox: approve {AUDIT_PROPOSAL_ID} (missing receipt)",
            (
                ("D", "synthetic/00-inbox/active/r.md"),
                ("A", "synthetic/11-library/active/r.md"),
            ),
            False,
        ),
        (
            f"outbox: approve {AUDIT_PROPOSAL_ID} (wrong receipt id)",
            (
                ("D", "synthetic/00-inbox/active/r.md"),
                ("A", "synthetic/11-library/active/r.md"),
                (
                    "A",
                    "synthetic/outbox/.receipts/"
                    + "20260824T120001-"
                    + "cd" * 16
                    + ".yaml",
                ),
            ),
            False,
        ),
        (
            "outbox: misleading",
            (("M", "_system/entities.yaml"),),
            False,
        ),
        (
            "registry: delete product x",
            (
                ("M", "_system/products.yaml"),
                (
                    "A",
                    f"synthetic/outbox/.receipts/{AUDIT_PROPOSAL_ID}.yaml",
                ),
            ),
            True,
        ),
        (
            "registry: delete product x",
            (("M", "_system/products.yaml"),),
            False,
        ),
        (
            "registry: delete product x",
            (
                ("M", "_system/products.yaml"),
                (
                    "M",
                    f"synthetic/outbox/.receipts/{AUDIT_PROPOSAL_ID}.yaml",
                ),
            ),
            False,
        ),
        (
            "registry: delete product x",
            (("M", "_system/members.yaml"),),
            False,
        ),
        (
            "registry: add workspace x",
            (("M", "_system/workspaces.yaml"),),
            True,
        ),
        (
            "unknown: edit",
            (("M", "_system/products.yaml"),),
            False,
        ),
    ],
)
def test_message_and_changed_paths_must_both_be_sanctioned(
    tmp_path: Path,
    monkeypatch,
    message: str,
    changes: tuple[tuple[str, str], ...],
    valid: bool,
):
    vault = _audit_vault(tmp_path)
    rules = gate3.AuditRules.load(vault)
    monkeypatch.setattr(
        gate3,
        "_receipt_from_commit",
        lambda _vault, record, path: make_action_receipt(
            Path(path).stem,
            "a" * 64,
            "approval" if record.message.startswith("outbox:") else "registry deletion",
        ),
    )

    result = gate3.audit_commits((_record(message, changes),), rules, vault)

    assert result.ok is valid
    assert bool(result.violating_commits) is (not valid)


@pytest.mark.parametrize("action", ["add", "edit", "delete"])
@pytest.mark.parametrize(
    ("kind", "path"),
    [
        ("workspace", "_system/workspaces.yaml"),
        ("product", "_system/products.yaml"),
        ("member", "_system/members.yaml"),
    ],
)
def test_each_registry_action_kind_pair_accepts_only_its_conventional_file(
    tmp_path: Path, monkeypatch, action: str, kind: str, path: str
):
    vault = _audit_vault(tmp_path)
    rules = gate3.AuditRules.load(vault)
    monkeypatch.setattr(
        gate3,
        "_receipt_from_commit",
        lambda _vault, _record, receipt_path: make_action_receipt(
            Path(receipt_path).stem, "a" * 64, "registry deletion"
        ),
    )
    receipt_change = (
        (
            "A",
            f"synthetic/outbox/.receipts/{AUDIT_PROPOSAL_ID}.yaml",
        ),
    ) if action == "delete" else ()
    valid = _record(
        f"registry: {action} {kind} value",
        (("M", path), *receipt_change),
    )
    wrong = {
        "workspace": "_system/products.yaml",
        "product": "_system/members.yaml",
        "member": "_system/workspaces.yaml",
    }[kind]
    invalid = _record(
        f"registry: {action} {kind} value",
        (("M", wrong), *receipt_change),
    )

    assert gate3.audit_commits((valid,), rules, vault).ok is True
    assert gate3.audit_commits((invalid,), rules, vault).ok is False


@pytest.mark.parametrize(
    ("message", "changes"),
    [
        (
            "outbox: cross entity",
            (
                ("D", "synthetic/00-inbox/active/r.md"),
                ("A", "secondary/11-library/active/r.md"),
            ),
        ),
        (
            "outbox: mismatched leaves",
            (
                ("D", "synthetic/00-inbox/active/a.md"),
                ("A", "synthetic/11-library/active/b.md"),
            ),
        ),
        (
            "outbox: inactive destination",
            (
                ("D", "synthetic/00-inbox/active/r.md"),
                ("A", "synthetic/15-inactive/active/r.md"),
            ),
        ),
        (
            "outbox: unknown destination",
            (
                ("D", "synthetic/00-inbox/active/r.md"),
                ("A", "synthetic/99-unknown/active/r.md"),
            ),
        ),
        (
            "outbox: system destination",
            (
                ("D", "synthetic/00-inbox/active/r.md"),
                ("A", "_system/active/r.md"),
            ),
        ),
        (
            "outbox: non-lifecycle destination",
            (
                ("D", "synthetic/00-inbox/active/r.md"),
                ("A", "synthetic/12-archive/active/r.md"),
            ),
        ),
        (
            "outbox: outbox destination",
            (
                ("D", "synthetic/00-inbox/active/r.md"),
                ("A", "synthetic/outbox/active/r.md"),
            ),
        ),
        (
            "outbox: staging destination",
            (
                ("D", "synthetic/00-inbox/active/r.md"),
                ("A", "synthetic/staging/active/r.md"),
            ),
        ),
    ],
)
def test_outbox_commit_rejects_noncanonical_move_envelopes(
    tmp_path: Path, message: str, changes: tuple[tuple[str, str], ...]
):
    vault = _audit_vault(tmp_path)
    rules = gate3.AuditRules.load(vault)

    assert gate3.audit_commits((_record(message, changes),), rules, vault).ok is False


@pytest.mark.parametrize(
    ("message", "changes"),
    [
        ("ingest: non-markdown", (("A", "synthetic/00-inbox/active/r.txt"),)),
        (
            "ingest: nested leaf",
            (("A", "synthetic/00-inbox/active/nested/r.md"),),
        ),
        ("ingest: unknown entity", (("A", "unknown/00-inbox/active/r.md"),)),
        ("ingest: modified", (("M", "synthetic/00-inbox/active/r.md"),)),
        (
            "outbox: wrong source status",
            (
                ("M", "synthetic/00-inbox/active/r.md"),
                ("A", "synthetic/11-library/active/r.md"),
            ),
        ),
        (
            "outbox: wrong destination status",
            (
                ("D", "synthetic/00-inbox/active/r.md"),
                ("M", "synthetic/11-library/active/r.md"),
            ),
        ),
        ("registry: delete product x", (("D", "_system/products.yaml"),)),
        (
            "registry: edit product x",
            (
                ("M", "_system/products.yaml"),
                ("M", "_system/workspaces.yaml"),
            ),
        ),
    ],
)
def test_sanctioned_actions_reject_wrong_status_or_leaf_shape(
    tmp_path: Path, message: str, changes: tuple[tuple[str, str], ...]
):
    vault = _audit_vault(tmp_path)
    rules = gate3.AuditRules.load(vault)

    assert gate3.audit_commits((_record(message, changes),), rules, vault).ok is False


@pytest.mark.parametrize(
    ("message", "changes"),
    [
        ("ingest: merge", (("A", "synthetic/00-inbox/active/r.md"),)),
        (
            "outbox: merge",
            (
                ("D", "synthetic/00-inbox/active/r.md"),
                ("A", "synthetic/11-library/active/r.md"),
            ),
        ),
        ("registry: add product x", (("M", "_system/products.yaml"),)),
        ("rename: old → new", (("M", "_system/entities.yaml"),)),
    ],
)
def test_sanctioned_action_messages_cannot_hide_merge_commits(
    tmp_path: Path, message: str, changes: tuple[tuple[str, str], ...]
):
    vault = _audit_vault(tmp_path)
    rules = gate3.AuditRules.load(vault)
    record = _record(message, changes, parents=("d" * 40, "e" * 40))

    assert gate3.audit_commits((record,), rules, vault).ok is False


@pytest.mark.parametrize(
    "message",
    [
        "ingested: add receipt",
        "outboxed: approve",
        "registryish: add product",
        "rename later: old → new",
    ],
)
def test_sanctioned_looking_words_are_not_action_prefixes(
    tmp_path: Path, message: str
):
    vault = _audit_vault(tmp_path)
    rules = gate3.AuditRules.load(vault)

    assert gate3.audit_commits(
        (_record(message, (("M", "_system/products.yaml"),)),), rules, vault
    ).ok is False


def _rename_files(axis: str) -> tuple[dict[str, str], str, str]:
    entity = "oldentity" if axis == "entity" else "synthetic"
    files = {
        "_system/archetypes.yaml": AUDIT_ARCHETYPES,
        "_system/entities.yaml": textwrap.dedent(
            f"""\
            version: "1.0"
            entities:
              {entity}:
                label: Synthetic
                flags: []
            """
        ),
        f"{entity}/00-inbox/active/.gitkeep": "",
        f"{entity}/11-library/active/.gitkeep": "",
        f"{entity}/02-pipeline/active/.gitkeep": "",
    }
    if axis == "entity":
        files[f"{entity}/11-library/active/note.md"] = (
            "---\ntype: note\nentity: oldentity\n---\n"
        )
        return files, "oldentity", "newentity"
    if axis == "product":
        files["_system/products.yaml"] = (
            'version: "1.0"\nproducts:\n  synthetic:\n'
            "    oldproduct:\n      label: Old\n"
        )
        files["synthetic/11-library/active/note.md"] = (
            "---\ntype: note\nproduct: oldproduct\n---\n"
        )
        return files, "oldproduct", "newproduct"
    if axis == "member":
        files["_system/members.yaml"] = (
            'version: "1.0"\nmembers:\n  synthetic:\n'
            "    - {id: oldmember, label: Old}\n"
        )
        files["synthetic/11-library/active/note.md"] = (
            "---\ntype: note\nmember: oldmember\n---\n"
        )
        return files, "oldmember", "newmember"
    if axis == "workspace":
        files["_system/workspaces.yaml"] = (
            'version: "1.0"\nworkspaces:\n'
            "  - {id: oldworkspace, entity: synthetic}\n"
        )
        return files, "oldworkspace", "newworkspace"
    if axis == "project":
        files["synthetic/02-pipeline/active/oldproject/index.md"] = (
            "---\ntype: project\n---\nrepo: oldproject\n[[oldproject]]\n"
        )
        files["synthetic/11-library/active/reference.md"] = (
            "See [[oldproject]] in synthetic/02-pipeline/active/oldproject/index.md\n"
        )
        return files, "oldproject", "newproject"
    raise AssertionError(f"unhandled rename axis {axis}")


def test_offline_rename_envelope_uses_explicit_parent_oid_without_git_repo(
    tmp_path: Path,
):
    files, old, new = _rename_files("entity")
    vault = git_vault(tmp_path, files)
    parent_oid = _git(vault, "rev-parse", "HEAD").strip()
    apply_rename(
        vault,
        plan_rename(vault, "entity", old, new),
        validators=[],
    )
    record = gate3.collect_commit_records(vault, parent_oid)[0]
    temporary, tree, tracked = gate3._parent_tree(vault, parent_oid)
    try:
        assert not (tree / ".git").exists()
        expected = gate3._rename_envelope(
            tree,
            tracked,
            "entity",
            old,
            new,
            parent_oid=parent_oid,
        )
    finally:
        temporary.cleanup()

    actual = frozenset((change.status, change.path) for change in record.changes)
    assert expected == actual


@pytest.mark.parametrize("axis", sorted(AXES))
def test_existing_rename_planner_commit_has_one_exact_accepted_envelope(
    tmp_path: Path, axis: str
):
    files, old, new = _rename_files(axis)
    vault = git_vault(tmp_path, files)
    parent = _git(vault, "rev-parse", "HEAD").strip()

    apply_rename(vault, plan_rename(vault, axis, old, new), validators=[])

    records = gate3.collect_commit_records(vault, parent)
    assert len(records) == 1
    rules = gate3.AuditRules.load(vault)
    assert gate3.audit_commits(records, rules, vault).ok is True


@pytest.mark.parametrize("axis", sorted(AXES))
def test_rename_prefix_cannot_hide_one_path_outside_planner_envelope(
    tmp_path: Path, axis: str
):
    files, old, new = _rename_files(axis)
    vault = git_vault(tmp_path, files)
    parent = _git(vault, "rev-parse", "HEAD").strip()
    apply_rename(vault, plan_rename(vault, axis, old, new), validators=[])
    valid_oid = _git(vault, "rev-parse", "HEAD").strip()

    (vault / "_system/unrelated.yaml").write_text("unrelated: true\n")
    _git(vault, "add", "_system/unrelated.yaml")
    _git(vault, "commit", "-q", "--amend", "--no-edit")

    records = gate3.collect_commit_records(vault, parent)
    assert len(records) == 1
    assert records[0].oid != valid_oid
    assert records[0].parents == (parent,)
    rules = gate3.AuditRules.load(vault)
    assert gate3.audit_commits(records, rules, vault).ok is False


def _classification_proposal(vault: Path) -> Path:
    scope = Scope(vault, "synthetic")
    proposal = propose_classification(
        scope,
        scope.resolve("00-inbox", "active", "receipt.md"),
        module="11-library",
        sub="notes",
        claimed_block="knowledge",
        rule_id="synthetic-rule",
    )
    return proposal.path


def test_new_canonical_pending_classification_proposal_is_sanctioned(tmp_path: Path):
    vault = _audit_vault(tmp_path, initialize_git=True)
    rules = gate3.AuditRules.load(vault)

    proposal = _classification_proposal(vault)
    after = gate3.collect_dirty_fingerprints(vault)
    result = gate3.audit_dirty({}, after, rules, vault)

    assert result.ok is True
    assert result.sanctioned_writes == [proposal.relative_to(vault).as_posix()]


def test_new_canonical_pending_registry_delete_proposal_is_sanctioned(tmp_path: Path):
    vault = _audit_vault(tmp_path, initialize_git=True)
    scope = Scope(vault, "synthetic")
    proposal = propose_delete(scope, "product", "unused")
    rules = gate3.AuditRules.load(vault)

    result = gate3.audit_dirty(
        {}, gate3.collect_dirty_fingerprints(vault), rules, vault
    )

    assert result.ok is True
    assert result.sanctioned_writes == [proposal.path.relative_to(vault).as_posix()]


def test_sanctioned_approval_consumed_record_is_not_a_direct_write(
    tmp_path: Path,
):
    vault = _audit_vault(tmp_path, initialize_git=True)
    scope = Scope(vault, "synthetic")
    before_head = _git(vault, "rev-parse", "HEAD").strip()
    before = gate3.collect_dirty_fingerprints(vault)
    proposal = _classification_proposal(vault)
    proposal_id = proposal.stem
    review = get_proposal_review(scope, proposal_id)

    approve(scope, proposal_id, review.sha256)

    records = gate3.collect_commit_records(vault, before_head)
    after = gate3.collect_dirty_fingerprints(vault)
    consumed_relative = (
        f"synthetic/outbox/.consumed/{proposal_id}.yaml"
    )
    result = gate3.audit_dirty(
        before,
        after,
        gate3.AuditRules.load(vault),
        vault,
        records=records,
    )

    assert result.ok is True
    assert result.sanctioned_writes == [consumed_relative]
    assert result.violating_writes == []


def test_sanctioned_reject_consumed_record_is_not_a_direct_write(
    tmp_path: Path,
):
    vault = _audit_vault(tmp_path, initialize_git=True)
    scope = Scope(vault, "synthetic")
    proposal = _classification_proposal(vault)
    proposal_id = proposal.stem
    proposal_relative = proposal.relative_to(vault).as_posix()
    before = gate3.collect_dirty_fingerprints(vault)
    review = get_proposal_review(scope, proposal_id)

    reject(scope, proposal_id, review.sha256)

    after = gate3.collect_dirty_fingerprints(vault)
    consumed_relative = (
        f"synthetic/outbox/.consumed/{proposal_id}.yaml"
    )
    result = gate3.audit_dirty(
        before,
        after,
        gate3.AuditRules.load(vault),
        vault,
        records=(),
    )

    assert result.ok is True
    assert set(result.sanctioned_writes) == {
        proposal_relative,
        consumed_relative,
    }
    assert result.violating_writes == []


def test_sanctioned_registry_delete_consumed_record_is_not_a_direct_write(
    tmp_path: Path,
):
    vault = _audit_vault(tmp_path, initialize_git=True)
    scope = Scope(vault, "synthetic")
    before_head = _git(vault, "rev-parse", "HEAD").strip()
    before = gate3.collect_dirty_fingerprints(vault)
    proposal = propose_delete(scope, "product", "unused")
    review = get_delete_review(scope, proposal.id)

    execute_delete(scope, proposal.id, review.sha256)

    records = gate3.collect_commit_records(vault, before_head)
    after = gate3.collect_dirty_fingerprints(vault)
    consumed_relative = (
        f"synthetic/outbox/.consumed/{proposal.id}.yaml"
    )
    result = gate3.audit_dirty(
        before,
        after,
        gate3.AuditRules.load(vault),
        vault,
        records=records,
    )

    assert result.ok is True
    assert result.sanctioned_writes == [consumed_relative]
    assert result.violating_writes == []


def test_reject_created_entirely_after_snapshot_remains_a_violation(
    tmp_path: Path,
):
    vault = _audit_vault(tmp_path, initialize_git=True)
    scope = Scope(vault, "synthetic")
    proposal = _classification_proposal(vault)
    review = get_proposal_review(scope, proposal.stem)

    reject(scope, proposal.stem, review.sha256)

    consumed_relative = (
        f"synthetic/outbox/.consumed/{proposal.stem}.yaml"
    )
    result = gate3.audit_dirty(
        {},
        gate3.collect_dirty_fingerprints(vault),
        gate3.AuditRules.load(vault),
        vault,
        records=(),
    )

    assert result.sanctioned_writes == []
    assert result.violating_writes == [consumed_relative]


@pytest.mark.parametrize(
    "mutation",
    [
        "malformed-content",
        "mismatched-id",
        "symlink",
        "non-regular",
        "wrong-location",
        "wrong-entity",
        "baseline-digest-mismatch",
    ],
)
def test_reject_consumed_record_requires_exact_shape_and_snapshot_correlation(
    tmp_path: Path, mutation: str
):
    vault = _audit_vault(tmp_path / "vault", initialize_git=True)
    scope = Scope(vault, "synthetic")
    proposal = _classification_proposal(vault)
    proposal_relative = proposal.relative_to(vault).as_posix()
    before = gate3.collect_dirty_fingerprints(vault)
    if mutation == "baseline-digest-mismatch":
        proposal.write_bytes(proposal.read_bytes() + b"\n")
    review = get_proposal_review(scope, proposal.stem)
    reject(scope, proposal.stem, review.sha256)
    consumed = (
        vault
        / "synthetic"
        / "outbox"
        / ".consumed"
        / f"{proposal.stem}.yaml"
    )
    expected_candidate = consumed.relative_to(vault).as_posix()
    if mutation == "malformed-content":
        consumed.write_bytes(b"action: classify\nmodule: [unterminated\n")
    elif mutation == "mismatched-id":
        record = yaml.safe_load(consumed.read_bytes())
        record["id"] = "20260824T120001-" + "cd" * 16
        consumed.write_text(yaml.safe_dump(record, sort_keys=False))
    elif mutation == "symlink":
        consumed.unlink()
        outside = tmp_path / "outside.yaml"
        outside.write_bytes(b"outside\n")
        os.symlink(outside, consumed)
    elif mutation == "non-regular":
        consumed.unlink()
        os.mkfifo(consumed)
    elif mutation == "wrong-location":
        wrong = vault / "synthetic/outbox/quarantine" / consumed.name
        wrong.parent.mkdir()
        consumed.rename(wrong)
        expected_candidate = wrong.relative_to(vault).as_posix()
    elif mutation == "wrong-entity":
        wrong = vault / "secondary/outbox/.consumed" / consumed.name
        wrong.parent.mkdir(parents=True)
        consumed.rename(wrong)
        expected_candidate = wrong.relative_to(vault).as_posix()
    elif mutation != "baseline-digest-mismatch":
        raise AssertionError(mutation)

    result = gate3.audit_dirty(
        before,
        gate3.collect_dirty_fingerprints(vault),
        gate3.AuditRules.load(vault),
        vault,
        records=(),
    )

    assert result.sanctioned_writes == []
    assert proposal_relative in result.violating_writes
    assert expected_candidate in result.violating_writes


@pytest.mark.parametrize(
    ("action_kind", "review_sha256"),
    [
        ("registry deletion", None),
        ("approval", "0" * 64),
    ],
)
def test_approval_consumed_record_requires_matching_session_receipt(
    tmp_path: Path,
    action_kind: str,
    review_sha256: str | None,
):
    vault = _audit_vault(tmp_path, initialize_git=True)
    scope = Scope(vault, "synthetic")
    proposal = _classification_proposal(vault)
    proposal_relative = proposal.relative_to(vault).as_posix()
    before_head = _git(vault, "rev-parse", "HEAD").strip()
    before = gate3.collect_dirty_fingerprints(vault)
    review = get_proposal_review(scope, proposal.stem)
    approve(scope, proposal.stem, review.sha256)
    receipt_relative = (
        f"synthetic/outbox/.receipts/{proposal.stem}.yaml"
    )
    replacement = make_action_receipt(
        proposal.stem,
        review.sha256 if review_sha256 is None else review_sha256,
        action_kind,
    )
    (vault / receipt_relative).write_bytes(render_action_receipt(replacement))
    _git(vault, "add", receipt_relative)
    _git(vault, "commit", "-q", "--amend", "--no-edit")
    records = gate3.collect_commit_records(vault, before_head)
    consumed_relative = (
        f"synthetic/outbox/.consumed/{proposal.stem}.yaml"
    )

    result = gate3.audit_dirty(
        before,
        gate3.collect_dirty_fingerprints(vault),
        gate3.AuditRules.load(vault),
        vault,
        records=records,
    )

    assert result.sanctioned_writes == []
    assert set(result.violating_writes) == {
        proposal_relative,
        consumed_relative,
    }


def test_approval_consumed_record_requires_session_receipt_to_exist(
    tmp_path: Path,
):
    vault = _audit_vault(tmp_path, initialize_git=True)
    scope = Scope(vault, "synthetic")
    proposal = _classification_proposal(vault)
    proposal_relative = proposal.relative_to(vault).as_posix()
    before_head = _git(vault, "rev-parse", "HEAD").strip()
    before = gate3.collect_dirty_fingerprints(vault)
    review = get_proposal_review(scope, proposal.stem)
    approve(scope, proposal.stem, review.sha256)
    receipt_relative = f"synthetic/outbox/.receipts/{proposal.stem}.yaml"
    (vault / receipt_relative).unlink()
    _git(vault, "add", "-u", receipt_relative)
    _git(vault, "commit", "-q", "--amend", "--no-edit")
    records = gate3.collect_commit_records(vault, before_head)
    consumed_relative = (
        f"synthetic/outbox/.consumed/{proposal.stem}.yaml"
    )

    result = gate3.audit_dirty(
        before,
        gate3.collect_dirty_fingerprints(vault),
        gate3.AuditRules.load(vault),
        vault,
        records=records,
    )

    assert result.sanctioned_writes == []
    assert set(result.violating_writes) == {
        proposal_relative,
        consumed_relative,
    }


def test_sanctioned_consumption_does_not_hide_an_unrelated_sibling_write(
    tmp_path: Path,
):
    vault = _audit_vault(tmp_path, initialize_git=True)
    scope = Scope(vault, "synthetic")
    proposal = _classification_proposal(vault)
    proposal_relative = proposal.relative_to(vault).as_posix()
    before_head = _git(vault, "rev-parse", "HEAD").strip()
    before = gate3.collect_dirty_fingerprints(vault)
    review = get_proposal_review(scope, proposal.stem)
    approve(scope, proposal.stem, review.sha256)
    records = gate3.collect_commit_records(vault, before_head)
    unrelated = "synthetic/outbox/.consumed/unrelated.txt"
    (vault / unrelated).write_text("unrelated\n")
    consumed_relative = (
        f"synthetic/outbox/.consumed/{proposal.stem}.yaml"
    )

    result = gate3.audit_dirty(
        before,
        gate3.collect_dirty_fingerprints(vault),
        gate3.AuditRules.load(vault),
        vault,
        records=records,
    )

    assert set(result.sanctioned_writes) == {
        proposal_relative,
        consumed_relative,
    }
    assert result.violating_writes == [unrelated]


def test_sanctioned_consumption_requires_the_pending_leaf_to_remain_absent(
    tmp_path: Path,
):
    vault = _audit_vault(tmp_path, initialize_git=True)
    scope = Scope(vault, "synthetic")
    before_head = _git(vault, "rev-parse", "HEAD").strip()
    before = gate3.collect_dirty_fingerprints(vault)
    proposal = _classification_proposal(vault)
    review = get_proposal_review(scope, proposal.stem)
    approve(scope, proposal.stem, review.sha256)
    records = gate3.collect_commit_records(vault, before_head)
    consumed_relative = (
        f"synthetic/outbox/.consumed/{proposal.stem}.yaml"
    )
    proposal.write_bytes((vault / consumed_relative).read_bytes())
    proposal_relative = proposal.relative_to(vault).as_posix()

    result = gate3.audit_dirty(
        before,
        gate3.collect_dirty_fingerprints(vault),
        gate3.AuditRules.load(vault),
        vault,
        records=records,
    )

    assert result.sanctioned_writes == []
    assert set(result.violating_writes) == {
        proposal_relative,
        consumed_relative,
    }


def test_approval_receipt_requires_the_final_consumed_record(tmp_path: Path):
    vault = _audit_vault(tmp_path, initialize_git=True)
    scope = Scope(vault, "synthetic")
    before_head = _git(vault, "rev-parse", "HEAD").strip()
    before = gate3.collect_dirty_fingerprints(vault)
    proposal = _classification_proposal(vault)
    review = get_proposal_review(scope, proposal.stem)
    approve(scope, proposal.stem, review.sha256)
    records = gate3.collect_commit_records(vault, before_head)
    consumed_relative = (
        f"synthetic/outbox/.consumed/{proposal.stem}.yaml"
    )
    (vault / consumed_relative).unlink()

    result = gate3.audit_dirty(
        before,
        gate3.collect_dirty_fingerprints(vault),
        gate3.AuditRules.load(vault),
        vault,
        records=records,
    )

    assert result.sanctioned_writes == []
    assert result.violating_writes == [consumed_relative]


def test_receipt_authorization_survives_a_later_entity_rename(tmp_path: Path):
    """Authorization must be read with the rules of its own commit.

    `_audit_commit_history` already loads `AuditRules` from each commit's
    tree, so an approval made before a rename stays sanctioned. Receipt
    authorization read the *final* vault rules instead, where the original
    slug no longer exists — so every path helper refused, no authorization
    was emitted, and the missing quarantine record was never added to
    `unclaimed`. A session that approved and then renamed reported PASS with
    its consumed evidence gone.
    """
    vault = _audit_vault(tmp_path, initialize_git=True)
    scope = Scope(vault, "synthetic")
    before_head = _git(vault, "rev-parse", "HEAD").strip()
    before = gate3.collect_dirty_fingerprints(vault)
    proposal = _classification_proposal(vault)
    review = get_proposal_review(scope, proposal.stem)
    approve(scope, proposal.stem, review.sha256)
    consumed_relative = f"synthetic/outbox/.consumed/{proposal.stem}.yaml"
    (vault / consumed_relative).unlink()
    apply_rename(
        vault, plan_rename(vault, "entity", "synthetic", "renamed"), validators=[]
    )
    rules = gate3.AuditRules.load(vault)
    assert "synthetic" not in rules.entities, "fixture must retire the old slug"
    records = gate3.collect_commit_records(vault, before_head)

    result = gate3.audit_dirty(
        before,
        gate3.collect_dirty_fingerprints(vault),
        rules,
        vault,
        records=records,
    )

    assert consumed_relative in result.violating_writes
    assert result.ok is False


def test_registry_delete_receipt_requires_the_final_consumed_record(
    tmp_path: Path,
):
    vault = _audit_vault(tmp_path, initialize_git=True)
    scope = Scope(vault, "synthetic")
    before_head = _git(vault, "rev-parse", "HEAD").strip()
    before = gate3.collect_dirty_fingerprints(vault)
    proposal = propose_delete(scope, "product", "unused")
    review = get_delete_review(scope, proposal.id)
    execute_delete(scope, proposal.id, review.sha256)
    records = gate3.collect_commit_records(vault, before_head)
    consumed_relative = (
        f"synthetic/outbox/.consumed/{proposal.id}.yaml"
    )
    (vault / consumed_relative).unlink()

    result = gate3.audit_dirty(
        before,
        gate3.collect_dirty_fingerprints(vault),
        gate3.AuditRules.load(vault),
        vault,
        records=records,
    )

    assert result.sanctioned_writes == []
    assert result.violating_writes == [consumed_relative]


def test_git_invisible_non_directory_consumed_store_is_a_violation(
    tmp_path: Path,
):
    vault = _audit_vault(tmp_path, initialize_git=True)
    consumed_store = vault / "synthetic/outbox/.consumed"
    consumed_store.parent.mkdir(exist_ok=True)
    os.mkfifo(consumed_store)
    relative = consumed_store.relative_to(vault).as_posix()

    after = gate3.collect_dirty_fingerprints(vault)
    result = gate3.audit_dirty(
        {}, after, gate3.AuditRules.load(vault), vault
    )

    assert after[relative].kind == "other"
    assert result.sanctioned_writes == []
    assert result.violating_writes == [relative]


def test_classification_proposal_created_must_match_id_timestamp(tmp_path: Path):
    vault = _audit_vault(tmp_path, initialize_git=True)
    proposal = _classification_proposal(vault)
    record = yaml.safe_load(proposal.read_text(encoding="utf-8"))
    record["created"] = "2000-01-01T00:00:00"
    proposal.write_text(yaml.safe_dump(record, sort_keys=False))
    rules = gate3.AuditRules.load(vault)

    result = gate3.audit_dirty(
        {}, gate3.collect_dirty_fingerprints(vault), rules, vault
    )

    assert result.sanctioned_writes == []
    assert result.violating_writes == [proposal.relative_to(vault).as_posix()]


def test_delete_proposal_created_must_match_id_timestamp(tmp_path: Path):
    vault = _audit_vault(tmp_path, initialize_git=True)
    scope = Scope(vault, "synthetic")
    proposal = propose_delete(scope, "product", "unused")
    record = yaml.safe_load(proposal.path.read_text(encoding="utf-8"))
    record["created"] = "2000-01-01T00:00:00"
    proposal.path.write_text(yaml.safe_dump(record, sort_keys=False))
    rules = gate3.AuditRules.load(vault)

    result = gate3.audit_dirty(
        {}, gate3.collect_dirty_fingerprints(vault), rules, vault
    )

    assert result.sanctioned_writes == []
    assert result.violating_writes == [proposal.path.relative_to(vault).as_posix()]


def test_new_staged_pending_proposal_is_not_a_sanctioned_dirty_write(tmp_path: Path):
    vault = _audit_vault(tmp_path, initialize_git=True)
    proposal = _classification_proposal(vault)
    relative = proposal.relative_to(vault).as_posix()
    _git(vault, "add", relative)
    after = gate3.collect_dirty_fingerprints(vault)
    rules = gate3.AuditRules.load(vault)

    result = gate3.audit_dirty({}, after, rules, vault)

    assert after[relative].status == "A "
    assert result.sanctioned_writes == []
    assert result.violating_writes == [relative]


@pytest.mark.parametrize(
    "mutation",
    [
        "malformed-yaml",
        "mismatched-id",
        "wrong-entity",
        "non-pending",
        "missing-status",
        "unknown-action",
        "invalid-created-type",
        "invalid-rule-id-type",
        "mismatched-source-hash",
        "extra-classify-field",
    ],
)
def test_new_outbox_yaml_must_be_a_canonical_pending_proposal(
    tmp_path: Path, mutation: str
):
    vault = _audit_vault(tmp_path, initialize_git=True)
    proposal = _classification_proposal(vault)
    record = yaml.safe_load(proposal.read_text(encoding="utf-8"))
    if mutation == "malformed-yaml":
        proposal.write_text("action: classify\nmodule: [unterminated\n")
    elif mutation == "mismatched-id":
        record["id"] = "20260816T010203-" + "a" * 32
        proposal.write_text(yaml.safe_dump(record, sort_keys=False))
    elif mutation == "wrong-entity":
        record["entity"] = "secondary"
        proposal.write_text(yaml.safe_dump(record, sort_keys=False))
    elif mutation == "non-pending":
        record["status"] = "approved"
        proposal.write_text(yaml.safe_dump(record, sort_keys=False))
    elif mutation == "missing-status":
        del record["status"]
        proposal.write_text(yaml.safe_dump(record, sort_keys=False))
    elif mutation == "unknown-action":
        record["action"] = "publish"
        proposal.write_text(yaml.safe_dump(record, sort_keys=False))
    elif mutation == "invalid-created-type":
        record["created"] = 20260816
        proposal.write_text(yaml.safe_dump(record, sort_keys=False))
    elif mutation == "invalid-rule-id-type":
        record["rule_id"] = {"unexpected": "mapping"}
        proposal.write_text(yaml.safe_dump(record, sort_keys=False))
    elif mutation == "mismatched-source-hash":
        record["source_sha256"] = "0" * 64
        proposal.write_text(yaml.safe_dump(record, sort_keys=False))
    elif mutation == "extra-classify-field":
        record["unexpected"] = True
        proposal.write_text(yaml.safe_dump(record, sort_keys=False))
    else:
        raise AssertionError(mutation)
    rules = gate3.AuditRules.load(vault)

    result = gate3.audit_dirty(
        {}, gate3.collect_dirty_fingerprints(vault), rules, vault
    )

    assert result.ok is False
    assert result.violating_writes == [proposal.relative_to(vault).as_posix()]


@pytest.mark.parametrize(
    "mutation",
    ["missing-impact-key", "extra-impact-key", "extra-delete-field"],
)
def test_new_delete_proposal_must_match_the_writer_record_shape(
    tmp_path: Path, mutation: str
):
    vault = _audit_vault(tmp_path, initialize_git=True)
    scope = Scope(vault, "synthetic")
    proposal = propose_delete(scope, "product", "unused")
    record = yaml.safe_load(proposal.path.read_text(encoding="utf-8"))
    if mutation == "missing-impact-key":
        del record["impact"]["books.db"]
    elif mutation == "extra-impact-key":
        record["impact"]["unexpected"] = 0
    elif mutation == "extra-delete-field":
        record["unexpected"] = True
    else:
        raise AssertionError(mutation)
    proposal.path.write_text(yaml.safe_dump(record, sort_keys=False))
    rules = gate3.AuditRules.load(vault)

    result = gate3.audit_dirty(
        {}, gate3.collect_dirty_fingerprints(vault), rules, vault
    )

    assert result.sanctioned_writes == []
    assert result.violating_writes == [proposal.path.relative_to(vault).as_posix()]


def test_new_outbox_leaf_for_unknown_entity_is_refused(tmp_path: Path):
    vault = _audit_vault(tmp_path, initialize_git=True)
    proposal_id = "20260816T010203-" + "b" * 32
    path = vault / "unknown" / "outbox" / f"{proposal_id}.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(
        yaml.safe_dump(
            {
                "id": proposal_id,
                "action": "delete",
                "entity": "unknown",
                "kind": "product",
                "slug": "unused",
                "created": "2026-08-16T01:02:03",
                "status": "pending",
                "total_references": 0,
                "impact": {},
            },
            sort_keys=False,
        )
    )
    rules = gate3.AuditRules.load(vault)

    result = gate3.audit_dirty(
        {}, gate3.collect_dirty_fingerprints(vault), rules, vault
    )

    assert result.ok is False
    assert result.violating_writes == [path.relative_to(vault).as_posix()]


@pytest.mark.parametrize(
    "relative",
    [
        "synthetic/00-inbox/active/uncommitted.md",
        "synthetic/11-library/active/direct.md",
    ],
)
def test_new_uncommitted_receipt_or_curated_write_is_refused(
    tmp_path: Path, relative: str
):
    vault = _audit_vault(tmp_path, initialize_git=True)
    path = vault / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("direct\n")
    rules = gate3.AuditRules.load(vault)

    result = gate3.audit_dirty(
        {}, gate3.collect_dirty_fingerprints(vault), rules, vault
    )

    assert result.ok is False
    assert result.violating_writes == [relative]


def test_changed_bytes_on_an_initially_dirty_path_are_refused(tmp_path: Path):
    vault = _audit_vault(tmp_path, initialize_git=True)
    relative = "synthetic/11-library/active/baseline.md"
    path = vault / relative
    path.write_text("before\n")
    before = gate3.collect_dirty_fingerprints(vault)

    path.write_text("after\n")
    after = gate3.collect_dirty_fingerprints(vault)
    rules = gate3.AuditRules.load(vault)
    result = gate3.audit_dirty(before, after, rules, vault)

    assert before[relative].status == after[relative].status == "??"
    assert before[relative].digest != after[relative].digest
    assert result.violating_writes == [relative]


def test_changed_worktree_bytes_on_an_initially_modified_path_are_refused(
    tmp_path: Path,
):
    vault = _audit_vault(tmp_path, initialize_git=True)
    relative = "synthetic/11-library/active/tracked-baseline.md"
    path = vault / relative
    path.write_text("committed\n")
    _git(vault, "add", relative)
    _git(vault, "commit", "-q", "-m", "fixture: tracked baseline")
    path.write_text("dirty before\n")
    before = gate3.collect_dirty_fingerprints(vault)

    path.write_text("dirty after\n")
    after = gate3.collect_dirty_fingerprints(vault)
    rules = gate3.AuditRules.load(vault)
    result = gate3.audit_dirty(before, after, rules, vault)

    assert before[relative].status == after[relative].status == " M"
    assert before[relative].index_entries == after[relative].index_entries
    assert before[relative].digest != after[relative].digest
    assert result.violating_writes == [relative]


def test_changed_index_oid_on_an_initially_staged_path_is_refused(tmp_path: Path):
    vault = _audit_vault(tmp_path, initialize_git=True)
    relative = "synthetic/11-library/active/staged.md"
    path = vault / relative
    path.write_text("staged one\n")
    _git(vault, "add", relative)
    before = gate3.collect_dirty_fingerprints(vault)

    path.write_text("staged two\n")
    _git(vault, "add", relative)
    after = gate3.collect_dirty_fingerprints(vault)
    rules = gate3.AuditRules.load(vault)
    result = gate3.audit_dirty(before, after, rules, vault)

    assert before[relative].status == after[relative].status == "A "
    assert before[relative].index_entries != after[relative].index_entries
    assert result.violating_writes == [relative]


def test_changed_unmerged_index_stage_is_refused(tmp_path: Path):
    vault = _audit_vault(tmp_path, initialize_git=True)
    relative = "synthetic/11-library/active/conflict.md"
    path = vault / relative
    path.write_bytes(b"worktree stays constant\n")
    _git(vault, "add", relative)
    _git(vault, "commit", "-q", "-m", "fixture: conflict base")
    base_oid = _write_blob(vault, b"base\n")
    ours_oid = _write_blob(vault, b"ours\n")
    theirs_oid = _write_blob(vault, b"theirs\n")
    replacement_oid = _write_blob(vault, b"replacement ours\n")
    _set_unmerged_index(vault, relative, (base_oid, ours_oid, theirs_oid))
    before = gate3.collect_dirty_fingerprints(vault)

    _set_unmerged_index(vault, relative, (base_oid, replacement_oid, theirs_oid))
    after = gate3.collect_dirty_fingerprints(vault)
    rules = gate3.AuditRules.load(vault)
    result = gate3.audit_dirty(before, after, rules, vault)

    assert before[relative].status == after[relative].status == "UU"
    assert before[relative].digest == after[relative].digest
    assert before[relative] != after[relative]
    assert result.violating_writes == [relative]


def test_disappearance_of_an_initially_dirty_path_is_refused(tmp_path: Path):
    vault = _audit_vault(tmp_path, initialize_git=True)
    relative = "synthetic/11-library/active/disappears.md"
    path = vault / relative
    path.write_text("baseline\n")
    before = gate3.collect_dirty_fingerprints(vault)

    path.unlink()
    after = gate3.collect_dirty_fingerprints(vault)
    rules = gate3.AuditRules.load(vault)

    assert relative not in after
    assert gate3.audit_dirty(before, after, rules, vault).violating_writes == [
        relative
    ]


def test_unchanged_initial_dirty_path_remains_unclassified_baseline(tmp_path: Path):
    vault = _audit_vault(tmp_path, initialize_git=True)
    relative = "synthetic/11-library/active/unchanged.md"
    (vault / relative).write_text("baseline\n")
    before = gate3.collect_dirty_fingerprints(vault)
    after = gate3.collect_dirty_fingerprints(vault)
    rules = gate3.AuditRules.load(vault)

    result = gate3.audit_dirty(before, after, rules, vault)

    assert result.ok is True
    assert result.sanctioned_writes == []
    assert result.violating_writes == []


def test_deleted_tracked_path_has_explicit_absence_fingerprint(tmp_path: Path):
    vault = _audit_vault(tmp_path, initialize_git=True)
    relative = "synthetic/11-library/active/tracked.md"
    (vault / relative).write_text("tracked\n")
    _git(vault, "add", relative)
    _git(vault, "commit", "-q", "-m", "fixture: tracked path")
    (vault / relative).unlink()

    fingerprint = gate3.collect_dirty_fingerprints(vault)[relative]

    assert fingerprint.status == " D"
    assert fingerprint.kind == "absence"
    assert fingerprint.mode is None
    assert fingerprint.digest is None
    assert fingerprint.index_entries


def test_untracked_symlink_hashes_its_target_bytes_without_following_it(tmp_path: Path):
    vault = _audit_vault(tmp_path / "vault", initialize_git=True)
    external = tmp_path / "outside.txt"
    external.write_text("external contents must not be hashed\n")
    relative = "synthetic/11-library/active/link.md"
    os.symlink(os.fspath(external), vault / relative)

    fingerprint = gate3.collect_dirty_fingerprints(vault)[relative]

    assert fingerprint.status == "??"
    assert fingerprint.kind == "symlink"
    assert fingerprint.digest == hashlib.sha256(os.fsencode(external)).hexdigest()
    assert fingerprint.digest != hashlib.sha256(external.read_bytes()).hexdigest()


def test_dirty_fingerprint_never_follows_a_redirected_parent_directory(
    tmp_path: Path,
):
    vault = _audit_vault(tmp_path / "vault", initialize_git=True)
    relative = "synthetic/11-library/active/tracked.md"
    (vault / relative).write_text("committed bytes\n")
    _git(vault, "add", relative)
    _git(vault, "commit", "-q", "-m", "fixture: redirected parent")
    module = vault / "synthetic/11-library"
    module.rename(tmp_path / "original-module")
    external_module = tmp_path / "external-module"
    write_tree(external_module, {"active/tracked.md": "outside bytes\n"})
    os.symlink(external_module, module, target_is_directory=True)

    fingerprint = gate3.collect_dirty_fingerprints(vault)[relative]

    assert fingerprint.kind == "redirected"
    assert fingerprint.digest is None
    assert fingerprint.digest != hashlib.sha256(b"outside bytes\n").hexdigest()


def test_dirty_status_uses_no_renames_and_preserves_spaces_in_paths(tmp_path: Path):
    vault = _audit_vault(tmp_path, initialize_git=True)
    old = "synthetic/11-library/active/old name.md"
    new = "synthetic/11-library/active/new name.md"
    (vault / old).write_text("same bytes\n")
    _git(vault, "add", old)
    _git(vault, "commit", "-q", "-m", "fixture: spaced path")

    _git(vault, "mv", old, new)
    fingerprints = gate3.collect_dirty_fingerprints(vault)

    assert set(fingerprints) == {old, new}
    assert fingerprints[old].status == "D "
    assert fingerprints[new].status == "A "


def test_commit_collection_uses_no_renames_and_preserves_spaces(tmp_path: Path):
    vault = _audit_vault(tmp_path, initialize_git=True)
    old = "synthetic/11-library/active/old commit name.md"
    new = "synthetic/11-library/active/new commit name.md"
    (vault / old).write_text("same bytes\n")
    _git(vault, "add", old)
    _git(vault, "commit", "-q", "-m", "fixture: spaced commit path")
    snapshot_head = _git(vault, "rev-parse", "HEAD").strip()

    _git(vault, "mv", old, new)
    _git(vault, "commit", "-q", "-m", "unknown: rename")
    records = gate3.collect_commit_records(vault, snapshot_head)

    assert len(records) == 1
    assert {(change.status, change.path) for change in records[0].changes} == {
        ("D", old),
        ("A", new),
    }


def _open_dir(path: Path) -> int:
    return os.open(
        path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    )


def _make_socket(path: Path) -> bool:
    """Bind a UNIX socket where the host safely supports it."""
    try:
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    except (AttributeError, OSError):
        return False
    try:
        server.bind(os.fspath(path))
    except OSError:
        server.close()
        return False
    server.close()
    return True


def test_filesystem_kind_is_closed_without_type_confusion():
    """One mode maps to exactly one kind, and regular is not evidence.

    A regular file reaching the supplemental map would let a lookalike enter
    a path Git already governs, so the classifier refuses it outright rather
    than inventing a kind for it.
    """
    assert gate3._filesystem_kind(stat.S_IFDIR | 0o755) == "directory"
    assert gate3._filesystem_kind(stat.S_IFLNK | 0o777) == "symlink"
    assert gate3._filesystem_kind(stat.S_IFIFO | 0o600) == "fifo"
    assert gate3._filesystem_kind(stat.S_IFSOCK | 0o600) == "socket"
    assert gate3._filesystem_kind(stat.S_IFCHR | 0o600) == "char-device"
    assert gate3._filesystem_kind(stat.S_IFBLK | 0o600) == "block-device"
    assert gate3._filesystem_kind(0o600) == "other"

    with pytest.raises(gate3.FilesystemEvidenceError):
        gate3._filesystem_kind(stat.S_IFREG | 0o644)


def test_filesystem_identity_digest_changes_on_same_kind_replacement(
    tmp_path: Path,
):
    """A replaced directory of the same kind must not look unchanged."""
    target = tmp_path / "d"
    target.mkdir()
    first = os.stat(target, follow_symlinks=False)
    first_digest = gate3._filesystem_identity_digest("directory", first)
    target.rmdir()
    target.mkdir()
    second = os.stat(target, follow_symlinks=False)
    second_digest = gate3._filesystem_identity_digest("directory", second)

    assert gate3._filesystem_kind(first.st_mode) == gate3._filesystem_kind(
        second.st_mode
    )
    assert first_digest != second_digest


def test_filesystem_symlink_hashes_raw_target_without_following(tmp_path: Path):
    """The link's own text is the evidence; its target is never opened."""
    external = tmp_path / "outside.txt"
    external.write_bytes(b"external content\n")
    boundary = tmp_path / "vault"
    boundary.mkdir()
    link = boundary / "link"
    link.symlink_to(external)
    descriptor = _open_dir(boundary)
    try:
        metadata = os.stat("link", dir_fd=descriptor, follow_symlinks=False)
        fingerprint = gate3._filesystem_fingerprint(descriptor, "link", metadata)
    finally:
        os.close(descriptor)

    assert fingerprint.kind == "symlink"
    assert fingerprint.target_digest == hashlib.sha256(
        b"oneos-gate3-target-v1\0" + os.fsencode(os.readlink(link))
    ).hexdigest()
    assert fingerprint.target_digest != hashlib.sha256(
        external.read_bytes()
    ).hexdigest()


def test_filesystem_fingerprint_records_a_socket_where_supported(tmp_path: Path):
    boundary = tmp_path / "vault"
    boundary.mkdir()
    if not _make_socket(boundary / "sock"):
        pytest.skip("host does not safely support UNIX sockets here")
    descriptor = _open_dir(boundary)
    try:
        metadata = os.stat("sock", dir_fd=descriptor, follow_symlinks=False)
        fingerprint = gate3._filesystem_fingerprint(descriptor, "sock", metadata)
    finally:
        os.close(descriptor)

    assert fingerprint.kind == "socket"
    assert fingerprint.target_digest is None
    assert gate3._SHA256_HEX.fullmatch(fingerprint.identity_digest)


def test_filesystem_fingerprint_records_a_fifo_mode(tmp_path: Path):
    boundary = tmp_path / "vault"
    boundary.mkdir()
    os.mkfifo(boundary / "pipe", 0o600)
    descriptor = _open_dir(boundary)
    try:
        metadata = os.stat("pipe", dir_fd=descriptor, follow_symlinks=False)
        fingerprint = gate3._filesystem_fingerprint(descriptor, "pipe", metadata)
    finally:
        os.close(descriptor)

    assert fingerprint.kind == "fifo"
    assert fingerprint.mode == 0o600
    assert fingerprint.target_digest is None


def test_cli_snapshot_writes_version_four_evidence_outside_vault(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    vault = _audit_vault(tmp_path / "vault", initialize_git=True)
    relative = "synthetic/11-library/active/path with spaces.md"
    (vault / relative).write_text("baseline bytes\n")
    snapshot = tmp_path / "gate3.json"
    monkeypatch.setenv("ONEOS_VAULT", os.fspath(vault))
    monkeypatch.setenv("GATE3_SNAP", os.fspath(snapshot))

    assert gate3.main(["snapshot"]) == 0

    data = json.loads(snapshot.read_text(encoding="utf-8"))
    fingerprint = data["dirty"][relative]
    assert set(data) == {"version", "head", "dirty", "filesystem"}
    assert data["version"] == 4
    assert data["filesystem"] == {}
    assert data["head"] == _git(vault, "rev-parse", "HEAD").strip()
    assert fingerprint["status"] == "??"
    assert fingerprint["index_entries"] == []
    assert fingerprint["kind"] == "file"
    assert fingerprint["mode"] == 0o644
    assert fingerprint["digest"] == hashlib.sha256(b"baseline bytes\n").hexdigest()


def _version_three_snapshot(head: str) -> dict:
    return {
        "version": 3,
        "head": head,
        "dirty": {
            "synthetic/11-library/active/note.md": {
                "status": "??",
                "index_entries": [],
                "kind": "file",
                "mode": 0o644,
                "digest": "0" * 64,
            }
        },
    }


def test_load_snapshot_rejects_version_three_without_upgrade(tmp_path: Path):
    """A version 3 snapshot has no initial filesystem evidence.

    Silently upgrading it would read a missing supplemental map as a clean
    baseline, so every pre-existing special entry would look like a session
    addition and every genuine one would be invisible. The operator must take
    a fresh snapshot instead.
    """
    snapshot = tmp_path / "gate3.json"
    snapshot.write_text(
        json.dumps(_version_three_snapshot("a" * 40)), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="Gate 3 snapshot version is unsupported"):
        gate3._load_snapshot(snapshot)


def _version_four_snapshot(**filesystem: dict) -> dict:
    return {
        "version": 4,
        "head": "a" * 40,
        "dirty": {},
        "filesystem": dict(filesystem),
    }


_VALID_FS_ENTRY = {
    "kind": "fifo",
    "mode": 0o600,
    "identity_digest": "1" * 64,
    "target_digest": None,
}


def _fs_entry(**overrides) -> dict:
    entry = dict(_VALID_FS_ENTRY)
    entry.update(overrides)
    return entry


_MALFORMED_FILESYSTEM_CASES = {
    "missing-kind": lambda: _version_four_snapshot(
        **{"a/b": {k: v for k, v in _VALID_FS_ENTRY.items() if k != "kind"}}
    ),
    "extra-field": lambda: _version_four_snapshot(
        **{"a/b": _fs_entry(unexpected=1)}
    ),
    "unknown-kind": lambda: _version_four_snapshot(
        **{"a/b": _fs_entry(kind="regular")}
    ),
    "negative-mode": lambda: _version_four_snapshot(
        **{"a/b": _fs_entry(mode=-1)}
    ),
    "boolean-mode": lambda: _version_four_snapshot(
        **{"a/b": _fs_entry(mode=True)}
    ),
    "uppercase-digest": lambda: _version_four_snapshot(
        **{"a/b": _fs_entry(identity_digest="A" * 64)}
    ),
    "short-digest": lambda: _version_four_snapshot(
        **{"a/b": _fs_entry(identity_digest="1" * 63)}
    ),
    "target-on-non-symlink": lambda: _version_four_snapshot(
        **{"a/b": _fs_entry(target_digest="2" * 64)}
    ),
    "no-target-on-symlink": lambda: _version_four_snapshot(
        **{"a/b": _fs_entry(kind="symlink", target_digest=None)}
    ),
    "absolute-path": lambda: _version_four_snapshot(**{"/a/b": _fs_entry()}),
    "empty-component": lambda: _version_four_snapshot(**{"a//b": _fs_entry()}),
    "dot-component": lambda: _version_four_snapshot(**{"a/./b": _fs_entry()}),
    "parent-component": lambda: _version_four_snapshot(**{"a/../b": _fs_entry()}),
    "nul-path": lambda: _version_four_snapshot(**{"a\x00b": _fs_entry()}),
    "surrogate-path": lambda: _version_four_snapshot(**{"a\udcffb": _fs_entry()}),
}


@pytest.mark.parametrize("case", sorted(_MALFORMED_FILESYSTEM_CASES))
def test_load_snapshot_rejects_malformed_filesystem_fingerprint(
    tmp_path: Path, case: str
):
    """Every closed-shape violation is a controlled, value-free refusal."""
    snapshot = tmp_path / "gate3.json"
    snapshot.write_text(
        json.dumps(_MALFORMED_FILESYSTEM_CASES[case]()), encoding="utf-8"
    )

    with pytest.raises(ValueError) as raised:
        gate3._load_snapshot(snapshot)
    message = str(raised.value)
    assert message.startswith("Gate 3 snapshot")
    # The refusal must come from the closed shape, never from the version
    # check: a loader that rejected every version 4 payload would pass this
    # test while validating nothing.
    assert "version is unsupported" not in message
    assert "a/b" not in message


@pytest.mark.parametrize(
    "raw",
    (
        '{"version": 4, "version": 4, "head": "%s", "dirty": {}, '
        '"filesystem": {}}' % ("a" * 40),
        '{"version": 4, "head": "%s", "dirty": {}, "filesystem": '
        '{"a/b": {"kind": "fifo", "mode": 384, "identity_digest": "%s", '
        '"target_digest": null}, "a/b": {"kind": "fifo", "mode": 384, '
        '"identity_digest": "%s", "target_digest": null}}}'
        % ("a" * 40, "1" * 64, "1" * 64),
    ),
    ids=("duplicate-top-level-key", "duplicate-filesystem-path"),
)
def test_load_snapshot_rejects_duplicate_object_keys(tmp_path: Path, raw: str):
    """A repeated key must be refused before Python collapses it.

    `json.loads` keeps the last value silently, so a second `filesystem`
    entry for one path could overwrite the evidence the snapshot recorded.
    """
    snapshot = tmp_path / "gate3.json"
    snapshot.write_text(raw, encoding="utf-8")

    with pytest.raises(ValueError) as raised:
        gate3._load_snapshot(snapshot)
    assert "version is unsupported" not in str(raised.value)


def test_cli_refuses_to_store_the_snapshot_inside_the_vault(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    vault = _audit_vault(tmp_path / "vault", initialize_git=True)
    snapshot = vault / ".gate3.json"
    monkeypatch.setenv("ONEOS_VAULT", os.fspath(vault))
    monkeypatch.setenv("GATE3_SNAP", os.fspath(snapshot))

    assert gate3.main(["snapshot"]) == 2
    assert snapshot.exists() is False


def test_cli_snapshot_reports_a_missing_entity_manifest_as_a_controlled_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    """`snapshot` must fail through the command boundary, not a traceback.

    The store sweep loads the entity catalog, so `snapshot` now reaches
    `EntityCatalog.load` where it never did before. `EntityManifestError` is a
    `RuntimeError`, which the boundary's exception tuple does not name, so an
    unreadable manifest escaped as an unhandled traceback and exit 1 instead
    of the controlled outcome every other Gate 3 failure reports.
    """
    vault = _audit_vault(tmp_path / "vault", initialize_git=True)
    (vault / "_system" / "entities.yaml").unlink()
    snapshot = tmp_path / "gate3.json"
    monkeypatch.setenv("ONEOS_VAULT", os.fspath(vault))
    monkeypatch.setenv("GATE3_SNAP", os.fspath(snapshot))

    assert gate3.main(["snapshot"]) == 2
    assert "GATE 3 ERROR:" in capsys.readouterr().err
    assert snapshot.exists() is False


def test_cli_check_accepts_a_sanctioned_commit_after_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    vault = _audit_vault(tmp_path / "vault", initialize_git=True)
    snapshot = tmp_path / "gate3.json"
    monkeypatch.setenv("ONEOS_VAULT", os.fspath(vault))
    monkeypatch.setenv("GATE3_SNAP", os.fspath(snapshot))
    assert gate3.main(["snapshot"]) == 0
    receipt = "synthetic/00-inbox/active/new receipt.md"
    (vault / receipt).write_text("redacted\n")
    _git(vault, "add", receipt)
    _git(vault, "commit", "-q", "-m", "ingest: add redacted receipt")

    assert gate3.main(["check"]) == 0


def test_cli_check_fails_closed_after_clean_rewind_behind_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    vault = _audit_vault(tmp_path / "vault", initialize_git=True)
    marker = vault / "snapshot-marker.txt"
    marker.write_text("snapshot state\n")
    _git(vault, "add", marker.relative_to(vault).as_posix())
    _git(vault, "commit", "-q", "-m", "fixture: snapshot state")
    snapshot_head = _git(vault, "rev-parse", "HEAD").strip()
    snapshot_parent = _git(vault, "rev-parse", "HEAD^").strip()
    snapshot = tmp_path / "gate3.json"
    monkeypatch.setenv("ONEOS_VAULT", os.fspath(vault))
    monkeypatch.setenv("GATE3_SNAP", os.fspath(snapshot))
    assert gate3.main(["snapshot"]) == 0

    _git(vault, "reset", "--hard", snapshot_parent)
    assert _git(vault, "status", "--porcelain") == ""
    assert _git(vault, "rev-parse", "HEAD").strip() != snapshot_head

    assert gate3.main(["check"]) == 2


def test_cli_check_fails_closed_for_divergent_sanctioned_replacement_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    vault = _audit_vault(tmp_path / "vault", initialize_git=True)
    base = _git(vault, "rev-parse", "HEAD").strip()
    original = "synthetic/00-inbox/active/original.md"
    (vault / original).write_text("redacted original\n")
    _git(vault, "add", original)
    _git(vault, "commit", "-q", "-m", "ingest: add redacted receipt")
    snapshot_head = _git(vault, "rev-parse", "HEAD").strip()
    snapshot = tmp_path / "gate3.json"
    monkeypatch.setenv("ONEOS_VAULT", os.fspath(vault))
    monkeypatch.setenv("GATE3_SNAP", os.fspath(snapshot))
    assert gate3.main(["snapshot"]) == 0

    _git(vault, "reset", "--hard", base)
    replacement = "synthetic/00-inbox/active/replacement.md"
    (vault / replacement).write_text("redacted replacement\n")
    _git(vault, "add", replacement)
    _git(vault, "commit", "-q", "-m", "ingest: add redacted receipt")
    assert _git(vault, "status", "--porcelain") == ""
    assert _git(vault, "merge-base", "HEAD", snapshot_head).strip() == base

    assert gate3.main(["check"]) == 2


def test_cli_check_fails_closed_when_head_rewinds_after_ancestry_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    vault = _audit_vault(tmp_path / "vault", initialize_git=True)
    marker = vault / "snapshot-marker.txt"
    marker.write_text("snapshot state\n")
    _git(vault, "add", marker.relative_to(vault).as_posix())
    _git(vault, "commit", "-q", "-m", "fixture: snapshot state")
    snapshot_head = _git(vault, "rev-parse", "HEAD").strip()
    snapshot_parent = _git(vault, "rev-parse", "HEAD^").strip()
    snapshot = tmp_path / "gate3.json"
    monkeypatch.setenv("ONEOS_VAULT", os.fspath(vault))
    monkeypatch.setenv("GATE3_SNAP", os.fspath(snapshot))
    assert gate3.main(["snapshot"]) == 0
    original_git_bytes = gate3._git_bytes
    rewound = False

    def rewind_after_ancestry(vault_arg, *args, env=None):
        nonlocal rewound
        output = original_git_bytes(vault_arg, *args, env=env)
        if args[:2] == ("merge-base", "--is-ancestor") and not rewound:
            rewound = True
            _git(vault, "reset", "--hard", snapshot_parent)
        return output

    monkeypatch.setattr(gate3, "_git_bytes", rewind_after_ancestry)

    assert gate3.main(["check"]) == 2
    assert rewound is True
    assert _git(vault, "rev-parse", "HEAD").strip() == snapshot_parent
    assert snapshot_head != snapshot_parent
    assert _git(vault, "status", "--porcelain") == ""


def test_cli_check_fails_closed_when_head_advances_after_range_collection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    vault = _audit_vault(tmp_path / "vault", initialize_git=True)
    snapshot = tmp_path / "gate3.json"
    monkeypatch.setenv("ONEOS_VAULT", os.fspath(vault))
    monkeypatch.setenv("GATE3_SNAP", os.fspath(snapshot))
    assert gate3.main(["snapshot"]) == 0
    first = "synthetic/00-inbox/active/first.md"
    (vault / first).write_text("redacted first\n")
    _git(vault, "add", first)
    _git(vault, "commit", "-q", "-m", "ingest: add redacted receipt")
    audit_head = _git(vault, "rev-parse", "HEAD").strip()
    original_git_bytes = gate3._git_bytes
    advanced = False

    def advance_after_range(vault_arg, *args, env=None):
        nonlocal advanced
        output = original_git_bytes(vault_arg, *args, env=env)
        if args and args[0] == "rev-list" and "--reverse" in args and not advanced:
            advanced = True
            second = "synthetic/00-inbox/active/second.md"
            (vault / second).write_text("redacted second\n")
            _git(vault, "add", second)
            _git(vault, "commit", "-q", "-m", "ingest: add redacted receipt")
        return output

    monkeypatch.setattr(gate3, "_git_bytes", advance_after_range)

    assert gate3.main(["check"]) == 2
    assert advanced is True
    assert _git(vault, "rev-parse", "HEAD").strip() != audit_head
    assert _git(vault, "status", "--porcelain") == ""


def test_cli_check_rejects_a_changed_baseline_dirty_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    vault = _audit_vault(tmp_path / "vault", initialize_git=True)
    path = vault / "synthetic/11-library/active/baseline.md"
    path.write_text("before\n")
    snapshot = tmp_path / "gate3.json"
    monkeypatch.setenv("ONEOS_VAULT", os.fspath(vault))
    monkeypatch.setenv("GATE3_SNAP", os.fspath(snapshot))
    assert gate3.main(["snapshot"]) == 0

    path.write_text("after\n")

    assert gate3.main(["check"]) == 1


def test_cli_check_uses_commit_relative_rules_before_an_entity_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    files, old, new = _rename_files("entity")
    vault = git_vault(tmp_path / "vault", files)
    snapshot = tmp_path / "gate3.json"
    monkeypatch.setenv("ONEOS_VAULT", os.fspath(vault))
    monkeypatch.setenv("GATE3_SNAP", os.fspath(snapshot))
    assert gate3.main(["snapshot"]) == 0
    receipt = f"{old}/00-inbox/active/new-receipt.md"
    (vault / receipt).write_text("redacted receipt\n")
    _git(vault, "add", receipt)
    _git(vault, "commit", "-q", "-m", "ingest: add redacted receipt")
    apply_rename(vault, plan_rename(vault, "entity", old, new), validators=[])

    assert gate3.main(["check"]) == 0
