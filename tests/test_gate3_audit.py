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
import subprocess
import textwrap

import pytest
import yaml

from app.outbox import propose_classification
from app.registry import propose_delete
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
            "outbox: approve p",
            (
                ("D", "synthetic/00-inbox/active/r.md"),
                ("A", "synthetic/11-library/active/r.md"),
            ),
            True,
        ),
        (
            "outbox: misleading",
            (("M", "_system/entities.yaml"),),
            False,
        ),
        (
            "registry: delete product x",
            (("M", "_system/products.yaml"),),
            True,
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
    message: str,
    changes: tuple[tuple[str, str], ...],
    valid: bool,
):
    vault = _audit_vault(tmp_path)
    rules = gate3.AuditRules.load(vault)

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
    tmp_path: Path, action: str, kind: str, path: str
):
    vault = _audit_vault(tmp_path)
    rules = gate3.AuditRules.load(vault)
    valid = _record(f"registry: {action} {kind} value", (("M", path),))
    wrong = {
        "workspace": "_system/products.yaml",
        "product": "_system/members.yaml",
        "member": "_system/workspaces.yaml",
    }[kind]
    invalid = _record(f"registry: {action} {kind} value", (("M", wrong),))

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
    assert before[relative].index_entry == after[relative].index_entry
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
    assert before[relative].index_entry != after[relative].index_entry
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
    assert fingerprint.index_entry is not None


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


def test_cli_snapshot_writes_version_two_fingerprints_outside_vault(
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
    assert data["version"] == 2
    assert data["head"] == _git(vault, "rev-parse", "HEAD").strip()
    assert fingerprint["status"] == "??"
    assert fingerprint["index_entry"] is None
    assert fingerprint["kind"] == "file"
    assert fingerprint["mode"] == 0o644
    assert fingerprint["digest"] == hashlib.sha256(b"baseline bytes\n").hexdigest()


def test_cli_refuses_to_store_the_snapshot_inside_the_vault(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    vault = _audit_vault(tmp_path / "vault", initialize_git=True)
    snapshot = vault / ".gate3.json"
    monkeypatch.setenv("ONEOS_VAULT", os.fspath(vault))
    monkeypatch.setenv("GATE3_SNAP", os.fspath(snapshot))

    assert gate3.main(["snapshot"]) == 2
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
