"""Gate 3 audits sanctioned Git transactions and exact dirty-session state.

Every repository in this file is synthetic.  The tests exercise Git's real
porcelain/name-status formats and the application's runtime registries rather
than duplicating either implementation in mocks.
"""
from __future__ import annotations

import dataclasses
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
from app.rename import AXES, RenameError, apply_rename, plan_rename
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
        expected, _mappings = gate3._axis_envelope_and_moves(
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
    baseline = gate3.collect_gate3_evidence(vault)
    before = baseline.dirty
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

    rules = gate3.AuditRules.load(vault)
    current = gate3.collect_gate3_evidence(vault)
    dirty_result = gate3.audit_dirty(
        before, current.dirty, rules, vault, records=()
    )
    # A non-regular record is filesystem evidence, not dirty evidence, so the
    # outcome is asserted over both channels exactly as the CLI composes them.
    filesystem_result = gate3.audit_filesystem(
        baseline.filesystem,
        current.filesystem,
        rules,
        classified_paths=gate3._classify_dirty_path_changes(
            before, current.dirty, dirty_result
        ),
    )
    sanctioned = (
        dirty_result.sanctioned_writes + filesystem_result.sanctioned_writes
    )
    violating = (
        dirty_result.violating_writes + filesystem_result.violating_writes
    )

    # No *record* may be sanctioned. The canonical quarantine directory may
    # appear when a mutation moves its record elsewhere and leaves it empty:
    # that empty-directory addition is the design's one directory-only
    # exception, and it never authorizes the record.
    assert set(sanctioned) <= {"synthetic/outbox/.consumed"}
    assert expected_candidate not in sanctioned
    assert proposal_relative in violating
    assert expected_candidate in violating


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
    """A non-directory at the canonical store name is still a violation.

    This used to depend on the canonical-store-only supplement, which the
    boundary-wide walk now subsumes. The evidence moved from the dirty map
    to the filesystem map; the outcome must not.
    """
    vault = _audit_vault(tmp_path, initialize_git=True)
    consumed_store = vault / "synthetic/outbox/.consumed"
    consumed_store.parent.mkdir(exist_ok=True)
    os.mkfifo(consumed_store)
    relative = consumed_store.relative_to(vault).as_posix()

    evidence = gate3.collect_filesystem_fingerprints(vault)
    result = gate3.audit_filesystem(
        {}, evidence, gate3.AuditRules.load(vault), classified_paths=()
    )

    assert evidence[relative].kind == "fifo"
    assert result.sanctioned_writes == []
    assert relative in result.violating_writes


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
    """Bind a UNIX socket where the host safely supports it.

    Binds relative to the parent directory: `sun_path` is 104 bytes on
    macOS, and a pytest `tmp_path` alone exceeds that. Passing the absolute
    path made every socket case skip on a host that supports sockets fine.
    """
    try:
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    except (AttributeError, OSError):
        return False
    previous = os.getcwd()
    try:
        os.chdir(path.parent)
        server.bind(path.name)
    except OSError:
        return False
    finally:
        os.chdir(previous)
        server.close()
    return True


def _boundary(root: Path) -> Path:
    """A minimal real Git working tree to walk."""
    root.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    return root


def test_filesystem_walk_records_real_directories_and_special_entries_in_byte_order(
    tmp_path: Path,
):
    """Every real directory and non-regular entry, sorted by raw bytes."""
    vault = _boundary(tmp_path / "vault")
    (vault / "b").mkdir()
    (vault / "b" / "deep").mkdir()
    (vault / "a-empty").mkdir()
    (vault / "regular.md").write_text("content\n")
    (vault / "b" / "regular.md").write_text("content\n")
    os.mkfifo(vault / "b" / "pipe", 0o600)
    (vault / "link").symlink_to("b")
    has_socket = _make_socket(vault / "sock")

    evidence = gate3.collect_filesystem_fingerprints(vault)

    assert list(evidence) == sorted(evidence, key=os.fsencode)
    assert evidence["a-empty"].kind == "directory"
    assert evidence["b"].kind == "directory"
    assert evidence["b/deep"].kind == "directory"
    assert evidence["b/pipe"].kind == "fifo"
    assert evidence["link"].kind == "symlink"
    assert "regular.md" not in evidence
    assert "b/regular.md" not in evidence
    if has_socket:
        assert evidence["sock"].kind == "socket"


@pytest.mark.parametrize(
    "excluded",
    (".sensitive", "_scratch", ".obsidian"),
)
def test_filesystem_walk_excludes_only_authoritative_real_directories(
    tmp_path: Path, excluded: str
):
    """The exclusion entry is evidence; its contents are not walked."""
    vault = _boundary(tmp_path / "vault")
    directory = vault / excluded
    directory.mkdir()
    os.mkfifo(directory / "pipe", 0o600)

    evidence = gate3.collect_filesystem_fingerprints(vault)

    assert evidence[excluded].kind == "directory"
    assert f"{excluded}/pipe" not in evidence


def test_exclusion_name_symlink_is_evidence_and_is_not_followed(tmp_path: Path):
    """An exclusion name only excludes when it is a real directory.

    A symlink wearing the name would otherwise skip a whole subtree from the
    audit while pointing anywhere it liked.
    """
    external = tmp_path / "outside"
    external.mkdir()
    os.mkfifo(external / "pipe", 0o600)
    vault = _boundary(tmp_path / "vault")
    (vault / ".sensitive").symlink_to(external)

    evidence = gate3.collect_filesystem_fingerprints(vault)

    assert evidence[".sensitive"].kind == "symlink"
    assert ".sensitive/pipe" not in evidence


def test_directory_symlink_is_not_followed(tmp_path: Path):
    external = tmp_path / "outside"
    external.mkdir()
    os.mkfifo(external / "pipe", 0o600)
    vault = _boundary(tmp_path / "vault")
    (vault / "link").symlink_to(external)

    evidence = gate3.collect_filesystem_fingerprints(vault)

    assert evidence["link"].kind == "symlink"
    assert not any(path.startswith("link/") for path in evidence)


def test_git_administrative_directory_is_derived_and_not_traversed(
    tmp_path: Path,
):
    """The administrative directory comes from Git, not a guessed name."""
    vault = _boundary(tmp_path / "vault")
    os.mkfifo(vault / ".git" / "pipe", 0o600)

    evidence = gate3.collect_filesystem_fingerprints(vault)

    assert not any(path.startswith(".git/") for path in evidence)


def test_separate_git_directory_is_excluded_by_derivation_not_by_name(
    tmp_path: Path,
):
    """A hardcoded `.git` would walk a separate administrative directory.

    Asserting only that `.git/` is skipped cannot distinguish derivation
    from a guessed literal, so the store is placed under a different name.
    """
    root = tmp_path / "vault"
    root.mkdir()
    store = root / "gitstore"
    subprocess.run(
        ["git", "init", "-q", f"--separate-git-dir={store}"],
        cwd=root,
        check=True,
    )
    os.mkfifo(store / "pipe", 0o600)

    evidence = gate3.collect_filesystem_fingerprints(root)

    assert evidence["gitstore"].kind == "directory"
    assert not any(path.startswith("gitstore/") for path in evidence)


def test_undecodable_entry_name_fails_closed(tmp_path: Path):
    """An unrepresentable name cannot be silently dropped from evidence."""
    vault = _boundary(tmp_path / "vault")
    raw = os.path.join(os.fsencode(vault), b"bad\xff\xfename")
    try:
        os.mkdir(raw)
    except OSError:
        pytest.skip("filesystem rejects undecodable names")

    with pytest.raises(gate3.FilesystemEvidenceError):
        gate3.collect_filesystem_fingerprints(vault)


def test_filesystem_walk_closes_siblings_during_depth_first_unwind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Descriptor lifetime is bounded by depth, not by directories visited.

    Holding one descriptor per visited directory would exhaust the process
    limit on a real vault, so the first sibling subtree must be fully closed
    before the second one opens.
    """
    vault = _boundary(tmp_path / "vault")
    for sibling in ("one", "two"):
        (vault / sibling / "mid" / "leaf").mkdir(parents=True)

    events: list[tuple[str, int]] = []
    active: set[int] = set()
    peak = 0
    real_open = gate3._open_directory
    real_close = gate3._close_directory

    def spy_open(path, *, parent_descriptor=None):
        nonlocal peak
        descriptor = real_open(path, parent_descriptor=parent_descriptor)
        events.append(("open", descriptor))
        active.add(descriptor)
        peak = max(peak, len(active))
        return descriptor

    def spy_close(descriptor):
        events.append(("close", descriptor))
        active.discard(descriptor)
        real_close(descriptor)

    monkeypatch.setattr(gate3, "_open_directory", spy_open)
    monkeypatch.setattr(gate3, "_close_directory", spy_close)

    evidence = gate3.collect_filesystem_fingerprints(vault)

    assert evidence["one/mid/leaf"].kind == "directory"
    assert active == set(), "every descriptor must be closed"
    opens = [index for index, (event, _) in enumerate(events) if event == "open"]
    assert len(opens) == 7, "root plus six directories"
    # root + maximum depth (sibling, mid, leaf)
    assert peak <= 4


def test_gate3_evidence_brackets_the_filesystem_walk_with_equal_git_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Git is read before and after the walk, and the walk sits between.

    One observation must describe one instant. Reading Git only once would
    let the working tree change under the walk with nothing to detect it.
    """
    vault = _audit_vault(tmp_path / "vault", initialize_git=True)
    (vault / "d").mkdir()
    calls: list[str] = []
    inputs = gate3.GitDirtyInputs(statuses={}, index_entries={})
    real_filesystem = gate3.collect_filesystem_fingerprints
    real_fingerprint = gate3._fingerprint_git_dirty_inputs

    def git_inputs(_vault):
        calls.append("git-before" if not calls else "git-after")
        return inputs

    def filesystem(target):
        calls.append("filesystem")
        return real_filesystem(target)

    def fingerprint(target, given):
        calls.append("fingerprint")
        return real_fingerprint(target, given)

    monkeypatch.setattr(gate3, "_collect_git_dirty_inputs", git_inputs)
    monkeypatch.setattr(gate3, "collect_filesystem_fingerprints", filesystem)
    monkeypatch.setattr(gate3, "_fingerprint_git_dirty_inputs", fingerprint)

    evidence = gate3.collect_gate3_evidence(vault)

    assert calls == ["git-before", "filesystem", "fingerprint", "git-after"]
    assert evidence.filesystem["d"].kind == "directory"


@pytest.mark.parametrize("field", ("statuses", "index_entries"))
def test_gate3_evidence_rejects_changed_status_or_index_across_walk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str
):
    """A Git change across the bracket voids the observation."""
    vault = _boundary(tmp_path / "vault")
    first = gate3.GitDirtyInputs(statuses={}, index_entries={})
    if field == "statuses":
        second = gate3.GitDirtyInputs(
            statuses={"a.md": "??"}, index_entries={}
        )
    else:
        second = gate3.GitDirtyInputs(
            statuses={}, index_entries={"a.md": ("x",)}
        )
    responses = iter((first, second))
    monkeypatch.setattr(
        gate3, "_collect_git_dirty_inputs", lambda _vault: next(responses)
    )
    monkeypatch.setattr(
        gate3, "_fingerprint_git_dirty_inputs", lambda _vault, _given: {}
    )

    with pytest.raises(gate3.FilesystemEvidenceError) as raised:
        gate3.collect_gate3_evidence(vault)
    assert str(raised.value) == (
        "Gate 3 Git evidence changed during filesystem traversal"
    )


def _cli_vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    vault = _audit_vault(tmp_path / "vault", initialize_git=True)
    snapshot = tmp_path / "gate3.json"
    monkeypatch.setenv("ONEOS_VAULT", os.fspath(vault))
    monkeypatch.setenv("GATE3_SNAP", os.fspath(snapshot))
    return vault, snapshot


def test_cli_wrong_location_fifo_after_snapshot_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Finding A itself: a Git-invisible FIFO outside the canonical store.

    Git omits it from porcelain entirely, so before this boundary-wide walk
    an unsanctioned filesystem write passed a full-session Gate 3 audit.
    """
    vault, _snapshot = _cli_vault(tmp_path, monkeypatch)
    assert gate3.main(["snapshot"]) == 0
    (vault / "synthetic" / ".consumed").mkdir()
    os.mkfifo(vault / "synthetic" / ".consumed" / "stray.yaml", 0o600)

    assert gate3.main(["check"]) == 1


def test_cli_unrelated_entity_local_specials_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    vault, _snapshot = _cli_vault(tmp_path, monkeypatch)
    assert gate3.main(["snapshot"]) == 0
    os.mkfifo(vault / "synthetic" / "11-library" / "pipe", 0o600)
    _make_socket(vault / "synthetic" / "sock")

    assert gate3.main(["check"]) == 1


def test_cli_unchanged_preexisting_wrong_location_fifo_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Baseline preservation: unchanged pre-existing evidence is not a write."""
    vault, _snapshot = _cli_vault(tmp_path, monkeypatch)
    os.mkfifo(vault / "synthetic" / "stray", 0o600)

    assert gate3.main(["snapshot"]) == 0
    assert gate3.main(["check"]) == 0


@pytest.mark.parametrize("direction", ("added", "removed"))
def test_cli_empty_directory_change_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, direction: str
):
    vault, _snapshot = _cli_vault(tmp_path, monkeypatch)
    target = vault / "synthetic" / "empty"
    if direction == "removed":
        target.mkdir()
    assert gate3.main(["snapshot"]) == 0
    if direction == "added":
        target.mkdir()
    else:
        target.rmdir()

    assert gate3.main(["check"]) == 1


def test_cli_canonical_empty_quarantine_directory_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    vault, _snapshot = _cli_vault(tmp_path, monkeypatch)
    # A real vault already has its outbox; only `.consumed` is new. Creating
    # the parent here too would test an unrelated directory addition.
    (vault / "synthetic" / "outbox").mkdir()
    assert gate3.main(["snapshot"]) == 0
    (vault / "synthetic" / "outbox" / ".consumed").mkdir()

    assert gate3.main(["check"]) == 0


def test_cli_canonical_directory_with_unrelated_sibling_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    vault, _snapshot = _cli_vault(tmp_path, monkeypatch)
    (vault / "synthetic" / "outbox").mkdir()
    assert gate3.main(["snapshot"]) == 0
    (vault / "synthetic" / "outbox" / ".consumed").mkdir()
    os.mkfifo(vault / "synthetic" / "outbox" / "stray", 0o600)

    # The sibling must fail on its own merits, not because the parent
    # directory happened to be new as well.
    assert gate3.main(["check"]) == 1


def test_cli_directory_symlink_target_children_never_appear(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    vault, _snapshot = _cli_vault(tmp_path, monkeypatch)
    external = tmp_path / "outside"
    external.mkdir()
    os.mkfifo(external / "hidden", 0o600)
    assert gate3.main(["snapshot"]) == 0
    (vault / "synthetic" / "link").symlink_to(external)

    assert gate3.main(["check"]) == 1
    captured = capsys.readouterr().out
    assert "synthetic/link" in captured
    assert "hidden" not in captured


def test_cli_sanctioned_commit_still_passes_with_filesystem_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Valid tracked evidence must not regress into a directory violation."""
    vault, _snapshot = _cli_vault(tmp_path, monkeypatch)
    assert gate3.main(["snapshot"]) == 0
    receipt = "synthetic/00-inbox/active/new receipt.md"
    (vault / receipt).write_text("redacted\n")
    _git(vault, "add", receipt)
    _git(vault, "commit", "-q", "-m", "ingest: add redacted receipt")

    assert gate3.main(["check"]) == 0


_CLI_ERROR_INJECTIONS = (
    "list",
    "open",
    "stat",
    "identity",
    "root-stat",
    "fingerprint",
    # Minor review finding: the design names "failure to close or otherwise
    # complete a descriptor-owned operation" as a controlled failure, and
    # nothing exercised it.
    "close",
)


@pytest.mark.parametrize("command", ("snapshot", "check"))
@pytest.mark.parametrize("injection", _CLI_ERROR_INJECTIONS)
def test_cli_controlled_error_never_leaks_a_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
    command: str,
    injection: str,
):
    """A failed observation exits 2 with no path or cause in the message."""
    vault, snapshot = _cli_vault(tmp_path, monkeypatch)
    if command == "check":
        assert gate3.main(["snapshot"]) == 0
        capsys.readouterr()

    outside_marker = os.fspath(tmp_path / "outside-marker")

    def explode(*_args, **_kwargs):
        raise OSError(f"boom {outside_marker}")

    targets = {
        "list": "_list_directory",
        "open": "_open_directory",
        "stat": "_stat_entry",
        "identity": "_fstat_descriptor",
        "root-stat": "_stat_entry_absolute",
        "fingerprint": "_filesystem_fingerprint",
        "close": "_close_directory",
    }
    monkeypatch.setattr(gate3, targets[injection], explode)

    assert gate3.main([command]) == 2
    captured = capsys.readouterr()
    assert "GATE 3 ERROR:" in captured.err
    assert os.fspath(vault) not in captured.err
    assert outside_marker not in captured.err
    if command == "snapshot":
        assert not snapshot.exists()
    else:
        assert "GATE 3: PASS" not in captured.out


def test_cli_unequal_git_bracket_is_a_controlled_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    vault, snapshot = _cli_vault(tmp_path, monkeypatch)
    real_inputs = gate3._collect_git_dirty_inputs
    state = {"calls": 0}

    def drifting(target):
        state["calls"] += 1
        inputs = real_inputs(target)
        if state["calls"] == 2:
            return gate3.GitDirtyInputs(
                statuses={**inputs.statuses, "drift.md": "??"},
                index_entries=inputs.index_entries,
            )
        return inputs

    monkeypatch.setattr(gate3, "_collect_git_dirty_inputs", drifting)

    assert gate3.main(["snapshot"]) == 2
    captured = capsys.readouterr()
    assert "GATE 3 ERROR:" in captured.err
    assert os.fspath(vault) not in captured.err
    assert not snapshot.exists()


def _classified(
    path: str, kind: str, disposition: str
) -> gate3.ClassifiedPathChange:
    return gate3.ClassifiedPathChange(path, kind, disposition)


def _audit_fs(before, after, rules, classified=()):
    return gate3.audit_filesystem(
        before, after, rules, classified_paths=tuple(classified)
    )


def test_filesystem_audit_preserves_unchanged_preexisting_special_entry(
    tmp_path: Path,
):
    """A wrong-location FIFO that never changed is baseline, not a write."""
    vault = _audit_vault(tmp_path / "vault", initialize_git=True)
    os.mkfifo(vault / "synthetic" / "stray", 0o600)
    rules = gate3.AuditRules.load(vault)
    before = gate3.collect_filesystem_fingerprints(vault)
    after = gate3.collect_filesystem_fingerprints(vault)

    audit = _audit_fs(before, after, rules)

    assert audit.sanctioned_writes == []
    assert audit.violating_writes == []
    assert audit.ok


@pytest.mark.parametrize(
    "mutation",
    (
        "new-fifo",
        "removed-fifo",
        "replaced-fifo",
        "fifo-to-symlink",
        "symlink-target",
        "socket",
    ),
)
def test_filesystem_audit_rejects_new_removed_replaced_or_changed_special_entry(
    tmp_path: Path, mutation: str
):
    """Every non-directory delta is a direct write with no sanction."""
    vault = _audit_vault(tmp_path / "vault", initialize_git=True)
    rules = gate3.AuditRules.load(vault)
    target = vault / "synthetic" / "stray"
    relative = "synthetic/stray"

    if mutation in {"removed-fifo", "replaced-fifo", "fifo-to-symlink"}:
        os.mkfifo(target, 0o600)
    elif mutation == "symlink-target":
        target.symlink_to("first")
    elif mutation == "socket":
        if not _make_socket(vault / "synthetic" / "sock"):
            pytest.skip("host does not safely support UNIX sockets here")
        relative = "synthetic/sock"

    before = gate3.collect_filesystem_fingerprints(vault)

    if mutation == "new-fifo":
        os.mkfifo(target, 0o600)
    elif mutation == "removed-fifo":
        target.unlink()
    elif mutation == "replaced-fifo":
        target.unlink()
        os.mkfifo(target, 0o600)
    elif mutation == "fifo-to-symlink":
        target.unlink()
        target.symlink_to("elsewhere")
    elif mutation == "symlink-target":
        target.unlink()
        target.symlink_to("second")
    else:
        (vault / "synthetic" / "sock").unlink()

    after = gate3.collect_filesystem_fingerprints(vault)
    audit = _audit_fs(before, after, rules)

    assert audit.violating_writes == [relative]
    assert audit.sanctioned_writes == []


_DIR_BEFORE = {"synthetic/d": gate3.FilesystemFingerprint(
    "directory", 0o755, "1" * 64, None
)}
_DIR_AFTER = {"synthetic/d": gate3.FilesystemFingerprint(
    "directory", 0o755, "1" * 64, None
)}


def _dir_fp(mode: int = 0o755, identity: str = "1" * 64):
    return gate3.FilesystemFingerprint("directory", mode, identity, None)


def test_added_directory_inherits_a_sanctioned_added_descendant(tmp_path: Path):
    """Ancestry a sanctioned write required is not a second finding."""
    vault = _audit_vault(tmp_path / "vault", initialize_git=True)
    rules = gate3.AuditRules.load(vault)

    audit = _audit_fs(
        {},
        {"synthetic/d": _dir_fp()},
        rules,
        [_classified("synthetic/d/note.md", "added", "sanctioned")],
    )

    assert audit.sanctioned_writes == ["synthetic/d"]
    assert audit.violating_writes == []


def test_removed_directory_inherits_a_sanctioned_removed_descendant(
    tmp_path: Path,
):
    vault = _audit_vault(tmp_path / "vault", initialize_git=True)
    rules = gate3.AuditRules.load(vault)

    audit = _audit_fs(
        {"synthetic/d": _dir_fp()},
        {},
        rules,
        [_classified("synthetic/d/note.md", "removed", "sanctioned")],
    )

    assert audit.sanctioned_writes == ["synthetic/d"]
    assert audit.violating_writes == []


def test_directory_with_a_violating_descendant_is_not_a_duplicate_finding(
    tmp_path: Path,
):
    """The descendant already fails the gate; the ancestor adds nothing."""
    vault = _audit_vault(tmp_path / "vault", initialize_git=True)
    rules = gate3.AuditRules.load(vault)

    audit = _audit_fs(
        {},
        {"synthetic/d": _dir_fp()},
        rules,
        [_classified("synthetic/d/note.md", "added", "violating")],
    )

    assert audit.violating_writes == []
    assert audit.sanctioned_writes == []


def test_mixed_descendants_never_let_a_sanctioned_one_erase_a_violation(
    tmp_path: Path,
):
    vault = _audit_vault(tmp_path / "vault", initialize_git=True)
    rules = gate3.AuditRules.load(vault)

    audit = _audit_fs(
        {},
        {"synthetic/d": _dir_fp()},
        rules,
        [
            _classified("synthetic/d/ok.md", "added", "sanctioned"),
            _classified("synthetic/d/bad.md", "added", "violating"),
        ],
    )

    assert audit.sanctioned_writes == []
    assert audit.violating_writes == []


def test_added_directory_with_only_a_changed_descendant_violates(
    tmp_path: Path,
):
    """`changed` is not the matching topology for an addition."""
    vault = _audit_vault(tmp_path / "vault", initialize_git=True)
    rules = gate3.AuditRules.load(vault)

    audit = _audit_fs(
        {},
        {"synthetic/d": _dir_fp()},
        rules,
        [_classified("synthetic/d/note.md", "changed", "sanctioned")],
    )

    assert audit.violating_writes == ["synthetic/d"]


@pytest.mark.parametrize("direction", ("added", "removed"))
def test_empty_directory_without_a_descendant_violates(
    tmp_path: Path, direction: str
):
    vault = _audit_vault(tmp_path / "vault", initialize_git=True)
    rules = gate3.AuditRules.load(vault)
    evidence = {"synthetic/d": _dir_fp()}
    before, after = ({}, evidence) if direction == "added" else (evidence, {})

    audit = _audit_fs(before, after, rules)

    assert audit.violating_writes == ["synthetic/d"]


def test_unrelated_empty_sibling_violates_beside_a_sanctioned_descendant(
    tmp_path: Path,
):
    vault = _audit_vault(tmp_path / "vault", initialize_git=True)
    rules = gate3.AuditRules.load(vault)

    audit = _audit_fs(
        {},
        {"synthetic/d": _dir_fp(), "synthetic/sibling": _dir_fp()},
        rules,
        [_classified("synthetic/d/note.md", "added", "sanctioned")],
    )

    assert audit.sanctioned_writes == ["synthetic/d"]
    assert audit.violating_writes == ["synthetic/sibling"]


def test_existing_directory_mode_or_identity_change_always_violates(
    tmp_path: Path,
):
    """A directory that exists at both endpoints cannot inherit anything."""
    vault = _audit_vault(tmp_path / "vault", initialize_git=True)
    rules = gate3.AuditRules.load(vault)

    audit = _audit_fs(
        {"synthetic/d": _dir_fp(mode=0o755)},
        {"synthetic/d": _dir_fp(mode=0o700)},
        rules,
        [_classified("synthetic/d/note.md", "added", "sanctioned")],
    )

    assert audit.violating_writes == ["synthetic/d"]


def test_directory_to_symlink_replacement_violates_as_a_changed_path(
    tmp_path: Path,
):
    vault = _audit_vault(tmp_path / "vault", initialize_git=True)
    rules = gate3.AuditRules.load(vault)

    audit = _audit_fs(
        {"synthetic/d": _dir_fp()},
        {
            "synthetic/d": gate3.FilesystemFingerprint(
                "symlink", 0o777, "2" * 64, "3" * 64
            )
        },
        rules,
        [_classified("synthetic/d/note.md", "added", "sanctioned")],
    )

    assert audit.violating_writes == ["synthetic/d"]


def test_directory_is_not_reported_beside_its_own_nonregular_descendant(
    tmp_path: Path,
):
    """One unsanctioned event must produce one finding, not two.

    Creating `x/p` necessarily creates `x`. Reporting both inflates the
    violation count and obscures which write actually happened, so the
    ancestor is suppressed as a duplicate exactly as it is for a violating
    descendant from commit or dirty evidence.
    """
    vault = _audit_vault(tmp_path / "vault", initialize_git=True)
    rules = gate3.AuditRules.load(vault)

    audit = _audit_fs(
        {},
        {
            "synthetic/x": _dir_fp(),
            "synthetic/x/p": gate3.FilesystemFingerprint(
                "fifo", 0o600, "9" * 64, None
            ),
        },
        rules,
    )

    assert audit.violating_writes == ["synthetic/x/p"]
    assert audit.sanctioned_writes == []


def test_exact_canonical_quarantine_directory_addition_is_sanctioned(
    tmp_path: Path,
):
    """The durable store may remain after a refusal leaves it empty."""
    vault = _audit_vault(tmp_path / "vault", initialize_git=True)
    rules = gate3.AuditRules.load(vault)

    audit = _audit_fs({}, {"synthetic/outbox/.consumed": _dir_fp()}, rules)

    assert audit.sanctioned_writes == ["synthetic/outbox/.consumed"]
    assert audit.violating_writes == []


@pytest.mark.parametrize("mutation", ("removal", "replacement", "mode"))
def test_canonical_quarantine_directory_removal_or_replacement_is_a_violation(
    tmp_path: Path, mutation: str
):
    vault = _audit_vault(tmp_path / "vault", initialize_git=True)
    rules = gate3.AuditRules.load(vault)
    path = "synthetic/outbox/.consumed"
    before = {path: _dir_fp()}
    if mutation == "removal":
        after = {}
    elif mutation == "replacement":
        after = {
            path: gate3.FilesystemFingerprint("symlink", 0o777, "2" * 64, "3" * 64)
        }
    else:
        after = {path: _dir_fp(mode=0o700)}

    audit = _audit_fs(before, after, rules)

    assert audit.violating_writes == [path]
    assert audit.sanctioned_writes == []


def test_wrong_location_quarantine_lookalike_directory_is_a_violation(
    tmp_path: Path,
):
    vault = _audit_vault(tmp_path / "vault", initialize_git=True)
    rules = gate3.AuditRules.load(vault)

    audit = _audit_fs(
        {},
        {
            "synthetic/.consumed": _dir_fp(),
            "synthetic/11-library/outbox/.consumed": _dir_fp(),
        },
        rules,
    )

    assert audit.violating_writes == [
        "synthetic/.consumed",
        "synthetic/11-library/outbox/.consumed",
    ]


def test_unknown_entity_quarantine_lookalike_is_a_violation(tmp_path: Path):
    vault = _audit_vault(tmp_path / "vault", initialize_git=True)
    rules = gate3.AuditRules.load(vault)

    audit = _audit_fs({}, {"stranger/outbox/.consumed": _dir_fp()}, rules)

    assert audit.violating_writes == ["stranger/outbox/.consumed"]


def test_canonical_quarantine_directory_does_not_sanction_unrelated_sibling(
    tmp_path: Path,
):
    """The directory exception authorizes the directory and nothing else."""
    vault = _audit_vault(tmp_path / "vault", initialize_git=True)
    rules = gate3.AuditRules.load(vault)

    audit = _audit_fs(
        {},
        {
            "synthetic/outbox/.consumed": _dir_fp(),
            "synthetic/outbox/stray": gate3.FilesystemFingerprint(
                "fifo", 0o600, "9" * 64, None
            ),
        },
        rules,
    )

    assert audit.sanctioned_writes == ["synthetic/outbox/.consumed"]
    assert audit.violating_writes == ["synthetic/outbox/stray"]


def test_nonregular_quarantine_record_cannot_enter_record_sanctioning(
    tmp_path: Path,
):
    """A FIFO at a record path is a direct write, never a consumed record."""
    vault = _audit_vault(tmp_path / "vault", initialize_git=True)
    rules = gate3.AuditRules.load(vault)
    path = "synthetic/outbox/.consumed/20260829T000000-" + "a" * 32 + ".yaml"

    audit = _audit_fs(
        {},
        {path: gate3.FilesystemFingerprint("fifo", 0o600, "9" * 64, None)},
        rules,
    )

    assert audit.violating_writes == [path]
    assert audit.sanctioned_writes == []


def _fs_fp(
    kind: gate3.FilesystemKind = "fifo",
    *,
    mode: int = 0o600,
    identity: str = "1" * 64,
    target: str | None = None,
) -> gate3.FilesystemFingerprint:
    return gate3.FilesystemFingerprint(kind, mode, identity, target)


def test_filesystem_comparison_preserves_identical_preexisting_evidence():
    """Unchanged pre-existing evidence is baseline, not a session change.

    Reporting it would make every vault with a pre-existing special entry
    fail its first audit, which is exactly the endpoint semantics Gate 3
    already promises for Git-derived evidence.
    """
    evidence = {"a": _fs_fp(), "b": _fs_fp("directory", mode=0o755)}

    assert gate3.compare_filesystem_evidence(evidence, dict(evidence)) == ()


def test_filesystem_comparison_reports_added_removed_and_changed_in_sorted_order():
    before = {"b": _fs_fp(), "c": _fs_fp(identity="2" * 64)}
    after = {"a": _fs_fp(), "c": _fs_fp(identity="3" * 64)}

    assert gate3.compare_filesystem_evidence(before, after) == (
        gate3.FilesystemChange("a", "added", None, _fs_fp()),
        gate3.FilesystemChange("b", "removed", _fs_fp(), None),
        gate3.FilesystemChange(
            "c", "changed", _fs_fp(identity="2" * 64), _fs_fp(identity="3" * 64)
        ),
    )


def test_filesystem_comparison_detects_same_kind_identity_replacement():
    before = {"a": _fs_fp(identity="1" * 64)}
    after = {"a": _fs_fp(identity="2" * 64)}

    (change,) = gate3.compare_filesystem_evidence(before, after)
    assert change.kind == "changed"


def test_filesystem_comparison_detects_directory_mode_change():
    before = {"a": _fs_fp("directory", mode=0o755)}
    after = {"a": _fs_fp("directory", mode=0o700)}

    (change,) = gate3.compare_filesystem_evidence(before, after)
    assert change.kind == "changed"


def test_filesystem_comparison_detects_symlink_target_change():
    before = {"a": _fs_fp("symlink", mode=0o777, target="1" * 64)}
    after = {"a": _fs_fp("symlink", mode=0o777, target="2" * 64)}

    (change,) = gate3.compare_filesystem_evidence(before, after)
    assert change.kind == "changed"


@pytest.mark.parametrize(
    ("first", "second"),
    (
        ("directory", "symlink"),
        ("symlink", "fifo"),
        ("fifo", "socket"),
        ("socket", "directory"),
    ),
)
def test_filesystem_comparison_never_confuses_directory_symlink_fifo_or_socket(
    first: str, second: str
):
    """A type swap is a replacement, never an unchanged path."""
    before = {"a": _fs_fp(first, target="1" * 64 if first == "symlink" else None)}
    after = {"a": _fs_fp(second, target="1" * 64 if second == "symlink" else None)}

    (change,) = gate3.compare_filesystem_evidence(before, after)
    assert change.kind == "changed"
    assert change.before.kind == first
    assert change.after.kind == second


def _race_vault(root: Path) -> Path:
    vault = _boundary(root)
    (vault / "d").mkdir()
    os.mkfifo(vault / "d" / "p", 0o600)
    (vault / "d" / "l").symlink_to("p")
    return vault


def _replace_fifo(path: Path) -> None:
    path.unlink()
    os.mkfifo(path, 0o600)


@pytest.mark.parametrize(
    "injection",
    (
        "after-initial-list",
        "pre-stat",
        "child-open",
        "child-fstat",
        "readlink",
        "post-stat",
        "relist",
    ),
)
def test_filesystem_walk_fails_closed_on_observed_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, injection: str
):
    """Any observed change during the walk voids the whole observation.

    A partially-consistent walk is worse than none: it would serialise
    evidence that never existed at one instant, and the endpoint comparison
    would then blame or excuse the wrong path.
    """
    vault = _race_vault(tmp_path / "vault")
    directory = vault / "d"

    if injection == "after-initial-list":
        real_list = gate3._list_directory
        state = {"done": False}

        def listing(descriptor):
            names = real_list(descriptor)
            if not state["done"] and "d" in names:
                state["done"] = True
                (directory / "p").unlink()
                (directory / "l").unlink()
                directory.rmdir()
            return names

        monkeypatch.setattr(gate3, "_list_directory", listing)
    elif injection == "pre-stat":
        real_stat = gate3._stat_entry

        def stat_entry(descriptor, name):
            if name == "p" and (directory / "p").exists():
                (directory / "p").unlink()
            return real_stat(descriptor, name)

        monkeypatch.setattr(gate3, "_stat_entry", stat_entry)
    elif injection == "child-open":
        real_open = gate3._open_directory

        def opener(path, *, parent_descriptor=None):
            if path == "d":
                (directory / "p").unlink()
                (directory / "l").unlink()
                directory.rmdir()
                os.mkfifo(vault / "d", 0o600)
            return real_open(path, parent_descriptor=parent_descriptor)

        monkeypatch.setattr(gate3, "_open_directory", opener)
    elif injection == "child-fstat":
        real_fstat = gate3._fstat_descriptor
        state = {"calls": 0}

        def fstat(descriptor):
            state["calls"] += 1
            result = real_fstat(descriptor)
            if state["calls"] == 2:
                return os.stat(tmp_path, follow_symlinks=False)
            return result

        monkeypatch.setattr(gate3, "_fstat_descriptor", fstat)
    elif injection == "readlink":
        def readlink(name, *, dir_fd=None):
            raise OSError("link vanished")

        monkeypatch.setattr(gate3.os, "readlink", readlink)
    elif injection == "post-stat":
        real_fingerprint = gate3._filesystem_fingerprint

        def fingerprint(parent_descriptor, name, metadata):
            result = real_fingerprint(parent_descriptor, name, metadata)
            if name == "p":
                _replace_fifo(directory / "p")
            return result

        monkeypatch.setattr(gate3, "_filesystem_fingerprint", fingerprint)
    else:
        real_list = gate3._list_directory
        state = {"seen": 0}

        def listing(descriptor):
            names = real_list(descriptor)
            if "p" in names:
                state["seen"] += 1
                if state["seen"] == 1:
                    os.mkfifo(directory / "late", 0o600)
            return names

        monkeypatch.setattr(gate3, "_list_directory", listing)

    with pytest.raises(gate3.FilesystemEvidenceError) as raised:
        gate3.collect_filesystem_fingerprints(vault)
    assert os.fspath(vault) not in str(raised.value)
    assert str(raised.value) in {
        "Gate 3 filesystem traversal failed",
        "Gate 3 filesystem entry is unclassifiable",
    }


def test_filesystem_walk_closes_all_descriptors_after_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A failure mid-walk must not leak the ancestor descriptors it holds."""
    vault = _boundary(tmp_path / "vault")
    (vault / "a" / "b" / "c").mkdir(parents=True)

    opened: list[int] = []
    closed: list[int] = []
    real_open = gate3._open_directory
    real_close = gate3._close_directory
    real_list = gate3._list_directory

    def spy_open(path, *, parent_descriptor=None):
        descriptor = real_open(path, parent_descriptor=parent_descriptor)
        opened.append(descriptor)
        return descriptor

    def spy_close(descriptor):
        closed.append(descriptor)
        real_close(descriptor)

    def failing_list(descriptor):
        names = real_list(descriptor)
        if "c" in names:
            raise gate3.FilesystemEvidenceError(
                "Gate 3 filesystem traversal failed"
            )
        return names

    monkeypatch.setattr(gate3, "_open_directory", spy_open)
    monkeypatch.setattr(gate3, "_close_directory", spy_close)
    monkeypatch.setattr(gate3, "_list_directory", failing_list)

    with pytest.raises(gate3.FilesystemEvidenceError):
        gate3.collect_filesystem_fingerprints(vault)

    assert sorted(closed) == sorted(opened)
    assert len(closed) == len(set(closed)), "each descriptor closes exactly once"


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
    (vault / "synthetic" / "empty-dir").mkdir()
    os.mkfifo(vault / "synthetic" / "pipe", 0o600)
    snapshot = tmp_path / "gate3.json"
    monkeypatch.setenv("ONEOS_VAULT", os.fspath(vault))
    monkeypatch.setenv("GATE3_SNAP", os.fspath(snapshot))

    assert gate3.main(["snapshot"]) == 0

    data = json.loads(snapshot.read_text(encoding="utf-8"))
    fingerprint = data["dirty"][relative]
    assert set(data) == {"version", "head", "dirty", "filesystem"}
    assert data["version"] == 4
    # A regular file is Git's business; the directory and the FIFO are the
    # supplement's, and neither map may claim the other's evidence.
    assert data["filesystem"]["synthetic/empty-dir"]["kind"] == "directory"
    assert data["filesystem"]["synthetic/pipe"]["kind"] == "fifo"
    assert relative not in data["filesystem"]
    assert "synthetic/pipe" not in data["dirty"]
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
    "out-of-range-mode": lambda: _version_four_snapshot(
        **{"a/b": _fs_entry(mode=0o10000)}
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


def test_cli_check_reports_a_missing_entity_manifest_as_a_controlled_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    """`check` must fail through the command boundary, not a traceback.

    `EntityManifestError` is a `RuntimeError`, which the boundary's exception
    tuple did not originally name, so an unreadable manifest escaped as an
    unhandled traceback instead of the controlled outcome every other Gate 3
    failure reports. `AuditRules.load` still reaches `EntityCatalog.load`
    here; the retired store sweep no longer makes `snapshot` do so.
    """
    vault = _audit_vault(tmp_path / "vault", initialize_git=True)
    snapshot = tmp_path / "gate3.json"
    monkeypatch.setenv("ONEOS_VAULT", os.fspath(vault))
    monkeypatch.setenv("GATE3_SNAP", os.fspath(snapshot))
    assert gate3.main(["snapshot"]) == 0
    capsys.readouterr()
    (vault / "_system" / "entities.yaml").unlink()

    assert gate3.main(["check"]) == 2
    captured = capsys.readouterr()
    assert "GATE 3 ERROR:" in captured.err
    assert "GATE 3: PASS" not in captured.out


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


# --- Task 10: sanctioned rename topology ------------------------------------


def _rec(oid: str, message: str, changes) -> gate3.CommitRecord:
    return gate3.CommitRecord(
        oid=oid,
        message=message,
        parents=("e" * 40,),
        changes=tuple(
            gate3.PathChangeRecord(status, path) for status, path in changes
        ),
    )


def _m(old_root: str, new_root: str) -> gate3.RenameMapping:
    return gate3.RenameMapping(old_root, new_root)


def _pair_fp(mode: int = 0o755, identity: str = "1" * 64):
    return gate3.FilesystemFingerprint("directory", mode, identity, None)


def _pair_audit(before, after, rules, mappings, classified=()):
    return gate3.audit_filesystem(
        before,
        after,
        rules,
        classified_paths=tuple(classified),
        rename_mappings=tuple(mappings),
    )


def test_cli_sanctioned_rename_of_an_untracked_only_directory_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A sanctioned rename must not fail the gate on a directory Git cannot see.

    `archive/` holds no tracked file, so nothing is ever classified beneath
    it. Both endpoints were reported as unsanctioned direct writes even though
    the rename commit itself was sanctioned, and the operator had no remedy
    but to delete the directory.
    """
    files, old, new = _rename_files("entity")
    vault = git_vault(tmp_path / "vault", files)
    (vault / old / "11-library" / "archive").mkdir(parents=True, exist_ok=True)
    snapshot = tmp_path / "gate3.json"
    monkeypatch.setenv("ONEOS_VAULT", os.fspath(vault))
    monkeypatch.setenv("GATE3_SNAP", os.fspath(snapshot))
    assert gate3.main(["snapshot"]) == 0

    apply_rename(vault, plan_rename(vault, "entity", old, new), validators=[])

    assert gate3.main(["check"]) == 0


def test_unsanctioned_rename_commit_contributes_no_mapping(tmp_path: Path):
    """Only a commit the existing verification accepted may map anything.

    Built from a *real* rename commit, then broken in a way only
    `_sanctioned_rename` rejects. A fabricated parent OID would make the
    envelope rebuild fail instead, and the provenance guard would never be
    reached — the test would pass with the guard deleted.
    """
    files, old, new = _rename_files("entity")
    vault = git_vault(tmp_path / "vault", files)
    head = _git(vault, "rev-parse", "HEAD").strip()
    apply_rename(vault, plan_rename(vault, "entity", old, new), validators=[])
    (record,) = gate3.collect_commit_records(vault, head)
    assert gate3._analyze_rename(record, vault).mappings != ()

    duplicated = dataclasses.replace(
        record, changes=record.changes + (record.changes[0],)
    )

    analysis = gate3._analyze_rename(duplicated, vault)
    assert analysis.sanctioned is False
    assert analysis.mappings == ()


def test_ambiguous_axis_match_contributes_no_mapping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Two axes reproducing one envelope is ambiguous, so nothing is mapped."""
    files, old, new = _rename_files("entity")
    vault = git_vault(tmp_path / "vault", files)
    head = _git(vault, "rev-parse", "HEAD").strip()
    apply_rename(vault, plan_rename(vault, "entity", old, new), validators=[])
    (record,) = gate3.collect_commit_records(vault, head)

    # Patch one level below the axis loop so the loop itself still runs. A
    # loop that returned on its first match would report one axis here and
    # the ambiguity would go undetected.
    real = gate3._axis_envelope_and_moves

    def every_axis_matches(tree, tracked, axis, o, n, *, parent_oid):
        envelope, _ = real(
            tree, tracked, "entity", o, n, parent_oid=parent_oid
        )
        return envelope, (gate3.RenameMapping(o, f"{n}-{axis}"),)

    monkeypatch.setattr(gate3, "_axis_envelope_and_moves", every_axis_matches)

    analysis = gate3._analyze_rename(record, vault)
    assert analysis.sanctioned is True
    assert len(analysis.matched_axes) == len(gate3.AXES)
    assert analysis.mappings == ()


def test_rename_mappings_compose_oldest_first_over_exact_roots():
    """An exact chain is consumed by the forward rewrite, not duplicated."""
    composed = gate3._compose_rename_mappings(((_m("a", "b"),), (_m("b", "c"),)))

    assert composed == (_m("a", "c"),)


@pytest.mark.parametrize(
    ("ordered", "expected"),
    (
        (
            ((_m("a/M/op", "a/M/np"),), (_m("a", "b"),)),
            (_m("a/M/op", "b/M/np"), _m("a", "b")),
        ),
        (
            ((_m("a", "b"),), (_m("b/M/op", "b/M/np"),)),
            (_m("a", "b"), _m("a/M/op", "b/M/np")),
        ),
    ),
    ids=("nested-then-ancestor", "ancestor-then-nested"),
)
def test_rename_mappings_compose_across_nesting_orders(ordered, expected):
    """Both nesting orders must end at the original source and final target."""
    assert gate3._compose_rename_mappings(ordered) == expected


def test_rename_mappings_compose_through_a_deeper_nested_chain():
    """A third rename beneath the second must find the most-specific source."""
    composed = gate3._compose_rename_mappings(
        (
            (_m("a", "b"),),
            (_m("b/M/op", "b/M/np"),),
            (_m("b/M/np/dp", "b/M/np/dpr"),),
        )
    )

    assert composed == (
        _m("a", "b"),
        _m("a/M/op", "b/M/np"),
        _m("a/M/op/dp", "b/M/np/dpr"),
    )
    assert (
        gate3._predict_rename_destination("a/M/op/dp/x", composed)
        == "b/M/np/dpr/x"
    )


def test_composed_mappings_retain_the_general_tail():
    """A nested rename must not shadow tails it does not touch."""
    composed = gate3._compose_rename_mappings(
        ((_m("a", "b"),), (_m("b/M/op", "b/M/np"),))
    )

    assert gate3._predict_rename_destination("a/other/x", composed) == "b/other/x"
    assert gate3._predict_rename_destination("a/M/op/x", composed) == "b/M/np/x"


@pytest.mark.parametrize("order", ("general-first", "specific-first"))
def test_source_preimage_selects_the_unique_most_specific_mapping(order: str):
    """Accumulated tuple order must not decide the derived source."""
    composed = (_m("a", "b"), _m("a/M/op", "b/M/np"))
    if order == "specific-first":
        composed = tuple(reversed(composed))

    assert gate3._source_preimage("b/M/np/dp", composed) == "a/M/op/dp"


def test_source_preimage_fails_closed_on_equally_specific_disagreement():
    """Two same-length destinations predicting different sources is ambiguous."""
    assert gate3._source_preimage("p/q/z", (_m("x", "p/q"), _m("y", "p/q"))) is None


@pytest.mark.parametrize(
    "ordered",
    (
        ((_m("a", "b"), _m("a", "c")),),
        ((_m("a", "c"), _m("b", "c")),),
        ((_m("a", "b"),), (_m("c", "b"),)),
    ),
    ids=(
        "one-source-two-destinations",
        "two-sources-one-destination",
        "cross-commit-two-sources-one-destination",
    ),
)
def test_conflicting_rename_mappings_contribute_nothing(ordered):
    assert gate3._compose_rename_mappings(ordered) == ()


def test_paired_rename_directories_are_sanctioned_at_both_endpoints(
    tmp_path: Path,
):
    vault = _audit_vault(tmp_path / "vault", initialize_git=True)
    rules = gate3.AuditRules.load(vault)

    audit = _pair_audit(
        {"synthetic/d/empty": _pair_fp()},
        {"renamed/d/empty": _pair_fp()},
        rules,
        (_m("synthetic", "renamed"),),
    )

    assert audit.sanctioned_writes == ["renamed/d/empty", "synthetic/d/empty"]
    assert audit.violating_writes == []


@pytest.mark.parametrize(
    ("before_path", "after_path", "before_fp", "after_fp", "mappings"),
    (
        ("synthetic/d/empty", "renamed/d/empty", _pair_fp(), _pair_fp(), ()),
        ("stranger/d/empty", "renamed/d/empty", _pair_fp(), _pair_fp(),
         (_m("synthetic", "renamed"),)),
        ("synthetic/d/empty", "stranger/d/empty", _pair_fp(), _pair_fp(),
         (_m("synthetic", "renamed"),)),
        ("synthetic/d/empty", "renamed/d/other", _pair_fp(), _pair_fp(),
         (_m("synthetic", "renamed"),)),
        ("synthetic/d/empty", "renamed/d/empty", _pair_fp(),
         _pair_fp(identity="2" * 64), (_m("synthetic", "renamed"),)),
        ("synthetic/d/empty", "renamed/d/empty", _pair_fp(),
         _pair_fp(mode=0o700), (_m("synthetic", "renamed"),)),
        ("synthetic/d/empty", "renamed/d/empty", _pair_fp(),
         gate3.FilesystemFingerprint("symlink", 0o755, "1" * 64, "3" * 64),
         (_m("synthetic", "renamed"),)),
        ("synthetic/d/empty", "renamed/d/empty",
         gate3.FilesystemFingerprint("symlink", 0o755, "1" * 64, "3" * 64),
         _pair_fp(), (_m("synthetic", "renamed"),)),
        # A sibling whose name merely *starts with* the mapped root. Under a
        # prefix match that forgot the separator this would predict
        # `renamed/x/empty` and wrongly pair.
        ("synthetic-x/empty", "renamed/x/empty", _pair_fp(),
         _pair_fp(), (_m("synthetic", "renamed"),)),
    ),
    ids=(
        "no-sanctioned-rename",
        "wrong-old-root",
        "wrong-new-root",
        "different-tail",
        "identity-mismatch",
        "mode-mismatch",
        "non-directory-added-endpoint",
        "non-directory-removed-endpoint",
        "prefix-only-root-match",
    ),
)
def test_unpaired_rename_shapes_remain_violations(
    tmp_path: Path, before_path, after_path, before_fp, after_fp, mappings
):
    """Every shape outside the verified mapping stays a direct-write violation."""
    vault = _audit_vault(tmp_path / "vault", initialize_git=True)
    rules = gate3.AuditRules.load(vault)

    audit = _pair_audit(
        {before_path: before_fp}, {after_path: after_fp}, rules, mappings
    )

    assert audit.sanctioned_writes == []
    assert sorted(audit.violating_writes) == sorted({before_path, after_path})


def test_pairing_does_not_sanction_an_unrelated_sibling(tmp_path: Path):
    vault = _audit_vault(tmp_path / "vault", initialize_git=True)
    rules = gate3.AuditRules.load(vault)

    audit = _pair_audit(
        {"synthetic/d/empty": _pair_fp()},
        {"renamed/d/empty": _pair_fp(), "renamed/d/extra": _pair_fp()},
        rules,
        (_m("synthetic", "renamed"),),
    )

    assert audit.violating_writes == ["renamed/d/extra"]
    assert audit.sanctioned_writes == ["renamed/d/empty", "synthetic/d/empty"]


def test_violating_descendant_beneath_a_paired_directory_still_fails(
    tmp_path: Path,
):
    """Pairing is evaluated after the descendant rule and cannot hide it."""
    vault = _audit_vault(tmp_path / "vault", initialize_git=True)
    rules = gate3.AuditRules.load(vault)

    audit = _pair_audit(
        {"synthetic/d/empty": _pair_fp()},
        {"renamed/d/empty": _pair_fp()},
        rules,
        (_m("synthetic", "renamed"),),
        classified=[_classified("renamed/d/empty/bad.md", "added", "violating")],
    )

    assert "renamed/d/empty" not in audit.sanctioned_writes
    # Suppressed as a duplicate, not re-reported: the descendant already
    # fails the gate.
    assert "renamed/d/empty" not in audit.violating_writes


@pytest.mark.parametrize("order", ("general-first", "specific-first"))
def test_pairing_selects_the_most_specific_mapping_regardless_of_order(
    tmp_path: Path, order: str
):
    """Tuple order must not decide which destination is predicted."""
    vault = _audit_vault(tmp_path / "vault", initialize_git=True)
    rules = gate3.AuditRules.load(vault)
    mappings = (_m("synthetic", "renamed"), _m("synthetic/d", "renamed/e"))
    if order == "specific-first":
        mappings = tuple(reversed(mappings))

    audit = _pair_audit(
        {"synthetic/d/empty": _pair_fp()},
        {"renamed/e/empty": _pair_fp()},
        rules,
        mappings,
    )

    assert audit.sanctioned_writes == ["renamed/e/empty", "synthetic/d/empty"]
    assert audit.violating_writes == []


def test_equally_specific_mappings_that_disagree_fail_closed(tmp_path: Path):
    """Two candidates of the same specificity are ambiguous, not a choice."""
    vault = _audit_vault(tmp_path / "vault", initialize_git=True)
    rules = gate3.AuditRules.load(vault)

    audit = _pair_audit(
        {"synthetic/d/empty": _pair_fp()},
        {"renamed/d/empty": _pair_fp()},
        rules,
        (_m("synthetic", "renamed"), _m("synthetic", "other")),
    )

    assert audit.sanctioned_writes == []
    assert "synthetic/d/empty" in audit.violating_writes


def test_one_removed_path_never_sanctions_two_destinations(tmp_path: Path):
    vault = _audit_vault(tmp_path / "vault", initialize_git=True)
    rules = gate3.AuditRules.load(vault)

    audit = _pair_audit(
        {"synthetic/d/empty": _pair_fp()},
        {"renamed/d/empty": _pair_fp(), "other/d/empty": _pair_fp()},
        rules,
        (_m("synthetic", "renamed"),),
    )

    assert "other/d/empty" in audit.violating_writes
    assert audit.sanctioned_writes == ["renamed/d/empty", "synthetic/d/empty"]


def test_two_removed_paths_never_share_one_added_path(tmp_path: Path):
    vault = _audit_vault(tmp_path / "vault", initialize_git=True)
    rules = gate3.AuditRules.load(vault)

    audit = _pair_audit(
        {"synthetic/d/empty": _pair_fp(), "second/d/empty": _pair_fp()},
        {"renamed/d/empty": _pair_fp()},
        rules,
        (_m("synthetic", "renamed"), _m("second", "renamed")),
    )

    assert audit.sanctioned_writes == []
    assert sorted(audit.violating_writes) == [
        "renamed/d/empty",
        "second/d/empty",
        "synthetic/d/empty",
    ]


def _nested_rename_files():
    """An entity fixture that also supports a nested project rename.

    The project axis is the one that moves *directories* (under
    `02-pipeline/`), so it is what actually nests beneath an entity rename.
    """
    files, old, new = _rename_files("entity")
    files[f"{old}/02-pipeline/active/oldproject/index.md"] = (
        "---\ntype: project\n---\nrepo: oldproject\n[[oldproject]]\n"
    )
    return files, old, new


@pytest.mark.parametrize("order", ("nested-then-ancestor", "ancestor-then-nested"))
def test_cli_nested_renames_pair_an_untracked_only_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, order: str
):
    """Both nesting orders must reach the same final destination.

    The untracked-only directory sits *inside* the project directory, so its
    snapshot path is rewritten by both renames. Placing it outside would let
    the general entity mapping alone explain it, and the nested source
    derivation would never be exercised.
    """
    files, old, new = _nested_rename_files()
    vault = git_vault(tmp_path / "vault", files)
    (vault / old / "02-pipeline" / "active" / "oldproject" / "empty").mkdir(
        parents=True, exist_ok=True
    )
    snapshot = tmp_path / "gate3.json"
    monkeypatch.setenv("ONEOS_VAULT", os.fspath(vault))
    monkeypatch.setenv("GATE3_SNAP", os.fspath(snapshot))
    assert gate3.main(["snapshot"]) == 0

    def rename_entity():
        apply_rename(
            vault, plan_rename(vault, "entity", old, new), validators=[]
        )

    def rename_project():
        apply_rename(
            vault,
            plan_rename(vault, "project", "oldproject", "newproject"),
            validators=[],
        )

    if order == "nested-then-ancestor":
        rename_project()
        rename_entity()
    else:
        rename_entity()
        rename_project()

    assert gate3.main(["check"]) == 0


# --- Task 11: non-directory rename inheritance ------------------------------


def _kind_fp(
    kind: str,
    *,
    mode: int = 0o755,
    identity: str = "1" * 64,
    target: str | None = None,
):
    return gate3.FilesystemFingerprint(kind, mode, identity, target)


_PAIRABLE_KINDS = (
    ("symlink", "9" * 64),
    ("fifo", None),
    ("socket", None),
    ("char-device", None),
    ("block-device", None),
    ("other", None),
)


@pytest.mark.parametrize(
    ("kind", "target"), _PAIRABLE_KINDS, ids=[kind for kind, _ in _PAIRABLE_KINDS]
)
def test_verified_rename_pairs_every_included_kind(
    tmp_path: Path, kind: str, target: str | None
):
    vault = _audit_vault(tmp_path / "vault", initialize_git=True)
    rules = gate3.AuditRules.load(vault)

    audit = _pair_audit(
        {"synthetic/d/x": _kind_fp(kind, target=target)},
        {"renamed/d/x": _kind_fp(kind, target=target)},
        rules,
        (_m("synthetic", "renamed"),),
    )

    assert audit.sanctioned_writes == ["renamed/d/x", "synthetic/d/x"]
    assert audit.violating_writes == []


def test_regular_files_are_never_pairable_supplemental_evidence(tmp_path: Path):
    vault = _audit_vault(tmp_path / "vault", initialize_git=True)
    rules = gate3.AuditRules.load(vault)

    audit = _pair_audit(
        {"synthetic/d/x": _kind_fp("regular")},
        {"renamed/d/x": _kind_fp("regular")},
        rules,
        (_m("synthetic", "renamed"),),
    )

    assert audit.sanctioned_writes == []


_UNPAIRED_KIND_CASES = {
    "no-sanctioned-rename": (
        "synthetic/d/x", "renamed/d/x", _kind_fp("fifo"), _kind_fp("fifo"), ()
    ),
    "wrong-old-root": (
        "stranger/d/x", "renamed/d/x", _kind_fp("fifo"), _kind_fp("fifo"),
        (("synthetic", "renamed"),),
    ),
    "wrong-new-root": (
        "synthetic/d/x", "stranger/d/x", _kind_fp("fifo"), _kind_fp("fifo"),
        (("synthetic", "renamed"),),
    ),
    "different-tail": (
        "synthetic/d/x", "renamed/d/y", _kind_fp("fifo"), _kind_fp("fifo"),
        (("synthetic", "renamed"),),
    ),
    "prefix-only-root": (
        "synthetic-x/d/x", "renamed/d/x", _kind_fp("fifo"), _kind_fp("fifo"),
        (("synthetic", "renamed"),),
    ),
    "kind-change": (
        "synthetic/d/x", "renamed/d/x", _kind_fp("fifo"), _kind_fp("socket"),
        (("synthetic", "renamed"),),
    ),
    "mode-change": (
        "synthetic/d/x", "renamed/d/x", _kind_fp("fifo"),
        _kind_fp("fifo", mode=0o700), (("synthetic", "renamed"),),
    ),
    "identity-change": (
        "synthetic/d/x", "renamed/d/x", _kind_fp("fifo"),
        _kind_fp("fifo", identity="2" * 64), (("synthetic", "renamed"),),
    ),
    "symlink-target-change": (
        "synthetic/d/x", "renamed/d/x", _kind_fp("symlink", target="9" * 64),
        _kind_fp("symlink", target="8" * 64), (("synthetic", "renamed"),),
    ),
    "non-symlink-carries-target": (
        "synthetic/d/x", "renamed/d/x", _kind_fp("fifo"),
        _kind_fp("fifo", target="9" * 64), (("synthetic", "renamed"),),
    ),
}


@pytest.mark.parametrize("case", sorted(_UNPAIRED_KIND_CASES))
def test_unpaired_kind_shapes_remain_violations(tmp_path: Path, case: str):
    before_path, after_path, before_fp, after_fp, raw = _UNPAIRED_KIND_CASES[case]
    vault = _audit_vault(tmp_path / "vault", initialize_git=True)
    rules = gate3.AuditRules.load(vault)

    audit = _pair_audit(
        {before_path: before_fp},
        {after_path: after_fp},
        rules,
        tuple(_m(old, new) for old, new in raw),
    )

    assert audit.sanctioned_writes == []
    assert sorted(audit.violating_writes) == sorted({before_path, after_path})


def test_standalone_new_special_entry_is_never_sanctioned(tmp_path: Path):
    vault = _audit_vault(tmp_path / "vault", initialize_git=True)
    rules = gate3.AuditRules.load(vault)
    audit = _pair_audit(
        {}, {"renamed/d/x": _kind_fp("fifo")}, rules,
        (_m("synthetic", "renamed"),),
    )
    assert audit.violating_writes == ["renamed/d/x"]
    assert audit.sanctioned_writes == []


@pytest.mark.parametrize("side", ("added", "removed"))
def test_unrelated_special_sibling_is_not_sanctioned(tmp_path: Path, side: str):
    vault = _audit_vault(tmp_path / "vault", initialize_git=True)
    rules = gate3.AuditRules.load(vault)
    before = {"synthetic/d/x": _kind_fp("fifo")}
    after = {"renamed/d/x": _kind_fp("fifo")}
    if side == "added":
        after["renamed/d/extra"] = _kind_fp("fifo")
        expected = "renamed/d/extra"
    else:
        before["synthetic/d/extra"] = _kind_fp("fifo")
        expected = "synthetic/d/extra"
    audit = _pair_audit(before, after, rules, (_m("synthetic", "renamed"),))
    assert audit.violating_writes == [expected]
    assert audit.sanctioned_writes == ["renamed/d/x", "synthetic/d/x"]


@pytest.mark.parametrize("shape", ("one-to-two", "two-to-one"))
def test_ambiguous_special_pairing_fails_closed(tmp_path: Path, shape: str):
    vault = _audit_vault(tmp_path / "vault", initialize_git=True)
    rules = gate3.AuditRules.load(vault)
    if shape == "one-to-two":
        before = {"synthetic/d/x": _kind_fp("fifo")}
        after = {
            "renamed/d/x": _kind_fp("fifo"),
            "other/d/x": _kind_fp("fifo"),
        }
        mappings = (_m("synthetic", "renamed"),)
        expected_sanctioned = ["renamed/d/x", "synthetic/d/x"]
    else:
        before = {
            "synthetic/d/x": _kind_fp("fifo"),
            "second/d/x": _kind_fp("fifo"),
        }
        after = {"renamed/d/x": _kind_fp("fifo")}
        mappings = (_m("synthetic", "renamed"), _m("second", "renamed"))
        expected_sanctioned = []
    audit = _pair_audit(before, after, rules, mappings)
    assert audit.sanctioned_writes == expected_sanctioned


def test_conflicting_mapping_never_sanctions_a_special_entry(tmp_path: Path):
    vault = _audit_vault(tmp_path / "vault", initialize_git=True)
    rules = gate3.AuditRules.load(vault)
    audit = _pair_audit(
        {"synthetic/d/x": _kind_fp("fifo")},
        {"renamed/d/x": _kind_fp("fifo")},
        rules,
        (_m("synthetic", "renamed"), _m("synthetic", "other")),
    )
    assert audit.sanctioned_writes == []


@pytest.mark.parametrize("refusal", ("mode", "identity"))
def test_paired_special_entry_never_sanctions_its_enclosing_directory(
    tmp_path: Path, refusal: str
):
    vault = _audit_vault(tmp_path / "vault", initialize_git=True)
    rules = gate3.AuditRules.load(vault)
    refused = (
        _kind_fp("directory", mode=0o777)
        if refusal == "mode"
        else _kind_fp("directory", identity="2" * 64)
    )
    audit = _pair_audit(
        {
            "synthetic/d": _kind_fp("directory"),
            "synthetic/d/link": _kind_fp("symlink", target="9" * 64),
        },
        {
            "renamed/d": refused,
            "renamed/d/link": _kind_fp("symlink", target="9" * 64),
        },
        rules,
        (_m("synthetic", "renamed"),),
    )
    assert sorted(audit.violating_writes) == ["renamed/d", "synthetic/d"]
    assert audit.sanctioned_writes == [
        "renamed/d/link", "synthetic/d/link"
    ]


def test_tracked_paired_special_is_neutral_despite_commit_classification(
    tmp_path: Path,
):
    """Sanctioned Git D/A evidence cannot bypass refused directory pairing."""
    vault = _audit_vault(tmp_path / "vault", initialize_git=True)
    rules = gate3.AuditRules.load(vault)
    audit = _pair_audit(
        {
            "synthetic/d": _kind_fp("directory"),
            "synthetic/d/link": _kind_fp("symlink", target="9" * 64),
        },
        {
            "renamed/d": _kind_fp("directory", identity="2" * 64),
            "renamed/d/link": _kind_fp("symlink", target="9" * 64),
        },
        rules,
        (_m("synthetic", "renamed"),),
        classified=(
            gate3.ClassifiedPathChange(
                "synthetic/d/link", "removed", "sanctioned"
            ),
            gate3.ClassifiedPathChange(
                "renamed/d/link", "added", "sanctioned"
            ),
        ),
    )

    assert sorted(audit.violating_writes) == ["renamed/d", "synthetic/d"]
    assert audit.sanctioned_writes == [
        "renamed/d/link", "synthetic/d/link"
    ]


def test_refused_special_pair_still_suppresses_its_ancestor(tmp_path: Path):
    vault = _audit_vault(tmp_path / "vault", initialize_git=True)
    rules = gate3.AuditRules.load(vault)
    audit = _pair_audit(
        {},
        {"renamed/d": _kind_fp("directory"), "renamed/d/x": _kind_fp("fifo")},
        rules,
        (),
    )
    assert audit.violating_writes == ["renamed/d/x"]
    assert audit.sanctioned_writes == []


def _ignored_symlink_files():
    files, old, new = _rename_files("entity")
    files[".gitignore"] = "ignored-link\n"
    return files, old, new


@pytest.mark.parametrize(
    "entry", ("tracked-symlink", "ignored-symlink", "fifo", "socket")
)
def test_cli_sanctioned_rename_carries_special_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entry: str
):
    files, old, new = _ignored_symlink_files()
    vault = git_vault(tmp_path / "vault", files)
    holder = vault / old / "11-library" / "archive"
    holder.mkdir(parents=True, exist_ok=True)
    if entry == "tracked-symlink":
        os.symlink("target", holder / "link")
        _git(vault, "add", "-A")
        _git(vault, "commit", "-q", "-m", "ingest: add redacted receipt")
    elif entry == "ignored-symlink":
        os.symlink("target", holder / "ignored-link")
    elif entry == "fifo":
        os.mkfifo(holder / "pipe", 0o600)
    else:
        if not _make_socket(holder / "sock"):
            pytest.skip("host does not safely support UNIX sockets here")
    snapshot = tmp_path / "gate3.json"
    monkeypatch.setenv("ONEOS_VAULT", os.fspath(vault))
    monkeypatch.setenv("GATE3_SNAP", os.fspath(snapshot))
    assert gate3.main(["snapshot"]) == 0
    apply_rename(vault, plan_rename(vault, "entity", old, new), validators=[])
    assert gate3.main(["check"]) == 0


def test_cli_git_visible_untracked_symlink_refuses_the_transaction(tmp_path: Path):
    files, old, new = _rename_files("entity")
    vault = git_vault(tmp_path / "vault", files)
    os.symlink("target", vault / old / "11-library" / "stray-link")
    with pytest.raises(RenameError):
        apply_rename(
            vault, plan_rename(vault, "entity", old, new), validators=[]
        )


# --- Task 12: one immutable rename analysis ---------------------------------


def _instrumented_check(vault, snapshot, monkeypatch):
    counts = {"parent_tree": 0, "plan": 0, "analysis": 0, "sanctioned": 0}
    for name, key in (
        ("_parent_tree", "parent_tree"),
        ("build_rename_plan", "plan"),
        ("_analyze_rename", "analysis"),
        ("_sanctioned_rename", "sanctioned"),
    ):
        real = getattr(gate3, name, None)
        if real is None:
            continue

        def wrapper(*args, _real=real, _key=key, **kwargs):
            counts[_key] += 1
            return _real(*args, **kwargs)

        monkeypatch.setattr(gate3, name, wrapper)
    monkeypatch.setenv("ONEOS_VAULT", os.fspath(vault))
    monkeypatch.setenv("GATE3_SNAP", os.fspath(snapshot))
    assert gate3.main(["check"]) in (0, 1)
    return counts


def test_rename_record_performs_one_analysis_and_bounded_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    files, old, new = _rename_files("entity")
    vault = git_vault(tmp_path / "vault", files)
    snapshot = tmp_path / "gate3.json"
    monkeypatch.setenv("ONEOS_VAULT", os.fspath(vault))
    monkeypatch.setenv("GATE3_SNAP", os.fspath(snapshot))
    assert gate3.main(["snapshot"]) == 0
    apply_rename(vault, plan_rename(vault, "entity", old, new), validators=[])
    counts = _instrumented_check(vault, snapshot, monkeypatch)
    assert counts["parent_tree"] <= 2
    assert counts["plan"] <= len(gate3.AXES)
    assert counts["analysis"] == 1
    assert counts["sanctioned"] <= 1


def test_non_rename_record_builds_no_rename_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    files, old, _ = _rename_files("entity")
    vault = git_vault(tmp_path / "vault", files)
    snapshot = tmp_path / "gate3.json"
    monkeypatch.setenv("ONEOS_VAULT", os.fspath(vault))
    monkeypatch.setenv("GATE3_SNAP", os.fspath(snapshot))
    assert gate3.main(["snapshot"]) == 0
    receipt = f"{old}/00-inbox/active/new receipt.md"
    (vault / receipt).write_text("redacted\n")
    _git(vault, "add", receipt)
    _git(vault, "commit", "-q", "-m", "ingest: add redacted receipt")
    counts = _instrumented_check(vault, snapshot, monkeypatch)
    assert counts["plan"] == 0
    assert counts["parent_tree"] <= 1


def test_two_rename_records_each_get_their_own_analysis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    files, old, new = _rename_files("entity")
    vault = git_vault(tmp_path / "vault", files)
    snapshot = tmp_path / "gate3.json"
    monkeypatch.setenv("ONEOS_VAULT", os.fspath(vault))
    monkeypatch.setenv("GATE3_SNAP", os.fspath(snapshot))
    assert gate3.main(["snapshot"]) == 0
    apply_rename(vault, plan_rename(vault, "entity", old, new), validators=[])
    apply_rename(
        vault,
        plan_rename(vault, "entity", new, "thirdentity"),
        validators=[],
    )
    counts = _instrumented_check(vault, snapshot, monkeypatch)
    assert counts["analysis"] == 2


def _rename_record(tmp_path: Path, axis: str = "entity"):
    files, old, new = _rename_files(axis)
    vault = git_vault(tmp_path / f"vault-{axis}", files)
    head = _git(vault, "rev-parse", "HEAD").strip()
    apply_rename(vault, plan_rename(vault, axis, old, new), validators=[])
    (record,) = gate3.collect_commit_records(vault, head)
    return vault, record


@pytest.mark.parametrize("axis", sorted(AXES))
def test_rename_analysis_preserves_literal_sanctioning_results(
    tmp_path: Path, axis: str
):
    vault, record = _rename_record(tmp_path, axis)
    variants = {
        "sanctioned": (record, True),
        "duplicate-change": (
            dataclasses.replace(
                record, changes=record.changes + (record.changes[0],)
            ),
            False,
        ),
        "wrong-parent": (
            dataclasses.replace(record, parents=("e" * 40,)),
            False,
        ),
        "malformed-envelope": (
            dataclasses.replace(record, changes=()),
            False,
        ),
        "non-rename-message": (
            dataclasses.replace(
                record, message="ingest: add redacted receipt"
            ),
            False,
        ),
    }
    for name, (candidate, expected) in variants.items():
        analysis = gate3._analyze_rename(candidate, vault)
        assert analysis.sanctioned is expected, name
        if not expected:
            assert analysis.matched_axes == (), name
            assert analysis.mappings == (), name


def test_expected_later_axis_failures_preserve_an_earlier_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    vault, record = _rename_record(tmp_path)
    real = gate3._axis_envelope_and_moves

    def expected_failure_after_match(
        tree, tracked, axis, old, new, *, parent_oid
    ):
        if axis == "entity":
            return real(tree, tracked, axis, old, new, parent_oid=parent_oid)
        raise OSError("synthetic expected axis-local failure")

    monkeypatch.setattr(
        gate3, "_axis_envelope_and_moves", expected_failure_after_match
    )
    analysis = gate3._analyze_rename(record, vault)
    assert analysis.sanctioned is True
    assert analysis.matched_axes == ("entity",)
    assert analysis.mappings


@pytest.mark.parametrize(
    "error",
    (
        TypeError("synthetic unexpected axis type error"),
        ValueError("synthetic unexpected axis value error"),
        subprocess.CalledProcessError(1, ("synthetic", "axis")),
    ),
    ids=("type-error", "value-error", "called-process-error"),
)
def test_unexpected_later_axis_error_fails_check_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
    error: Exception,
):
    files, old, new = _rename_files("entity")
    vault = git_vault(tmp_path / "vault", files)
    snapshot = tmp_path / "gate3.json"
    monkeypatch.setenv("ONEOS_VAULT", os.fspath(vault))
    monkeypatch.setenv("GATE3_SNAP", os.fspath(snapshot))
    assert gate3.main(["snapshot"]) == 0
    apply_rename(vault, plan_rename(vault, "entity", old, new), validators=[])
    real = gate3._axis_envelope_and_moves

    def unexpected_failure_after_match(
        tree, tracked, axis, old, new, *, parent_oid
    ):
        if axis == "entity":
            return real(tree, tracked, axis, old, new, parent_oid=parent_oid)
        raise error

    monkeypatch.setattr(
        gate3, "_axis_envelope_and_moves", unexpected_failure_after_match
    )
    assert gate3.main(["check"]) == 2
    captured = capsys.readouterr()
    assert "GATE 3 ERROR:" in captured.err
    assert "GATE 3: PASS" not in captured.out
