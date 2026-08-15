"""Outbox write path (spec §10 steps 7–8, invariant 1).

Step 7: confirming a classification writes a PROPOSAL into <entity>/outbox/ and
renders a diff — no file is moved. Step 8: approval performs the move and
commits (exactly one commit, git-revertible); reject discards the proposal.

Temp git vaults only; the real vault is never touched.
"""
import hashlib
import inspect
import re
import subprocess
import textwrap
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import pytest
import yaml

import app.outbox as outbox
import app.proposal_identity as proposal_identity
from app.scope import CrossScopeError, Scope
from app.destinations import DestinationError
from app.outbox import (
    Proposal,
    approve,
    load_proposals,
    preview_diff,
    propose_classification,
    reject,
)
from tests.conftest import (
    git_changed_paths,
    git_count_commits,
    git_head,
    git_head_message,
    git_is_clean,
    git_tracked_paths,
    git_entity_vault,
)


OUTBOX_ARCHETYPES = textwrap.dedent(
    """\
    version: "2.0"
    flags: {}
    modules:
      00-inbox: {block: system}
      11-knowledge: {block: govern}
      11-library: {block: govern}
    submodules:
      00-inbox:
        triage: {name: Triage}
      11-knowledge:
        kb: {name: Knowledge base}
      11-library:
        reference: {name: Reference}
    archetypes:
      plain: {}
    """
)


def _outbox_vault(root, entities, files):
    return git_entity_vault(
        root,
        entities,
        {"_system/archetypes.yaml": OUTBOX_ARCHETYPES, **files},
    )


def _vault_tree(root: Path) -> tuple[tuple[str, str, bytes | str], ...]:
    entries = []
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            entries.append((relative, "symlink", path.readlink().as_posix()))
        elif path.is_dir():
            entries.append((relative, "directory", ""))
        else:
            entries.append((relative, "file", path.read_bytes()))
    return tuple(sorted(entries))


def _approval_state(vault: Path):
    def git_bytes(*args: str) -> bytes:
        return subprocess.run(
            ["git", *args], cwd=vault, check=True, capture_output=True
        ).stdout

    return {
        "head": git_bytes("rev-parse", "HEAD"),
        "status": git_bytes("status", "--porcelain=v1", "-z"),
        "index": git_bytes("diff", "--cached", "--binary"),
        "worktree": git_bytes("diff", "--binary"),
        "tree": _vault_tree(vault),
    }


def _vault(tmp_path):
    files = {
        "demo/00-inbox/active/note.md": textwrap.dedent(
            """\
            ---
            type: inbox-item
            title: Clinical study protocol
            entity: demo
            product: null
            status: active
            created: 2026-01-01
            updated: 2026-01-01
            sub: triage
            source: folder
            ---
            Randomised trial protocol body.
            """
        ),
        "demo/11-knowledge/active/.gitkeep": "",
        "demo/11-library/active/.gitkeep": "",
    }
    return _outbox_vault(tmp_path, ("demo",), files)


def _propose(vault):
    scope = Scope(vault, "demo")
    src = scope.resolve("00-inbox", "active", "note.md")
    return scope, propose_classification(
        scope, src, module="11-knowledge", sub="kb", claimed_block="govern",
        rule_id="research",
    )


@pytest.fixture
def two_entity_vault(tmp_path):
    files = {
        "alpha/00-inbox/active/alpha.md": textwrap.dedent(
            """\
            ---
            type: inbox-item
            title: Alpha note
            entity: alpha
            status: active
            created: 2026-01-01
            updated: 2026-01-01
            sub: triage
            source: folder
            ---
            alpha-source-marker
            """
        ),
        "beta/00-inbox/active/beta.md": textwrap.dedent(
            """\
            ---
            type: inbox-item
            title: Beta note
            entity: beta
            status: active
            created: 2026-01-01
            updated: 2026-01-01
            sub: triage
            source: folder
            ---
            beta-source-marker
            """
        ),
        "alpha/11-knowledge/active/.gitkeep": "",
        "beta/11-knowledge/active/.gitkeep": "",
        "alpha/11-library/active/.gitkeep": "",
        "beta/11-library/active/.gitkeep": "",
    }
    return _outbox_vault(tmp_path, ("alpha", "beta"), files)


_MISSING = object()


def _canonical_alpha_record(scope: Scope) -> dict:
    source = scope.resolve("00-inbox", "active", "alpha.md")
    return {
        "id": "20260815T090703-" + "11" * 16,
        "action": "classify",
        "entity": "alpha",
        "created": "2026-01-02T03:04:05",
        "status": "pending",
        "src": "alpha/00-inbox/active/alpha.md",
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "dst": "alpha/11-knowledge/active/alpha.md",
        "module": "11-knowledge",
        "sub": "kb",
        "block": "govern",
        "rule_id": "synthetic-rule",
    }


def _forged_beta_record(scope: Scope) -> dict:
    source = scope.root / "beta/00-inbox/active/beta.md"
    return {
        "id": "20260815T090703-" + "22" * 16,
        "action": "classify",
        "entity": "beta",
        "created": "2026-01-02T03:04:05",
        "status": "pending",
        "src": "beta/00-inbox/active/beta.md",
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "dst": "beta/11-knowledge/active/beta.md",
        "module": "11-knowledge",
        "sub": "kb",
        "block": "govern",
    }


def _proposal_from_record(path: Path, record: dict) -> Proposal:
    return Proposal(
        id=record["id"],
        path=path,
        action=record["action"],
        entity=record["entity"],
        src=record["src"],
        source_sha256=record["source_sha256"],
        dst=record["dst"],
        module=record["module"],
        sub=record["sub"],
        block=record["block"],
        rule_id=record.get("rule_id"),
        created=record.get("created", ""),
        status=record.get("status", "pending"),
    )


def _assert_destination_error(operation) -> None:
    with pytest.raises(outbox.OutboxError) as raised:
        operation()
    assert type(raised.value) is outbox.OutboxDestinationError


def _write_record(scope: Scope, name: str, record: str) -> Path:
    path = scope.resolve("outbox", name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(record, encoding="utf-8")
    return path


class _FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 1, 2, 3, 4, 5, tzinfo=tz)


def test_same_second_classification_proposals_are_distinct_and_preserved(
    tmp_path, monkeypatch
):
    vault = _vault(tmp_path)
    scope = Scope(vault, "demo")
    source = scope.resolve("00-inbox", "active", "note.md")
    monkeypatch.setattr(outbox, "datetime", _FixedDatetime)
    suffixes = iter(("11" * 16, "22" * 16))
    monkeypatch.setattr(
        proposal_identity.secrets, "token_hex", lambda size: next(suffixes)
    )

    first = propose_classification(
        scope, source, module="11-knowledge", sub="kb"
    )
    second = propose_classification(
        scope, source, module="11-knowledge", sub="kb"
    )

    assert first.id == "20260102T030405-" + "11" * 16
    assert second.id == "20260102T030405-" + "22" * 16
    assert first.path != second.path
    assert first.path.exists() and second.path.exists()
    assert len(list(scope.resolve("outbox").glob("*.yaml"))) == 2


def test_proposal_hash_is_sha256_of_exact_receipt_bytes(tmp_path):
    vault = _vault(tmp_path)
    scope = Scope(vault, "demo")
    source = scope.resolve("00-inbox", "active", "note.md")
    exact = source.read_bytes()

    prop = propose_classification(
        scope, source, module="11-knowledge", sub="kb"
    )
    record = yaml.safe_load(prop.path.read_text(encoding="utf-8"))

    assert record["source_sha256"] == hashlib.sha256(exact).hexdigest()
    assert prop.source_sha256 == hashlib.sha256(exact).hexdigest()


def test_snapshot_rejects_receipt_leaf_swapped_to_in_scope_symlink(
    tmp_path, monkeypatch
):
    vault = _vault(tmp_path)
    scope = Scope(vault, "demo")
    source = scope.resolve("00-inbox", "active", "note.md")
    redirected = scope.resolve("redirected-receipt.md")
    redirected.write_bytes(b"redirected receipt bytes\n")
    redirected_bytes = redirected.read_bytes()
    real_resolve = outbox.resolve_classification_destination

    def swap_after_destination(*args, **kwargs):
        destination = real_resolve(*args, **kwargs)
        source.unlink()
        source.symlink_to(redirected)
        return destination

    monkeypatch.setattr(
        outbox, "resolve_classification_destination", swap_after_destination
    )

    with pytest.raises(CrossScopeError):
        propose_classification(scope, source, module="11-knowledge", sub="kb")

    assert redirected.read_bytes() == redirected_bytes
    assert not scope.resolve("outbox").exists()


def _redirect_proposal_leaf(vault: Path) -> tuple[Scope, Proposal, Path]:
    scope, proposal = _propose(vault)
    shadow = scope.resolve("proposal-shadow", f"{proposal.id}.yaml")
    shadow.parent.mkdir()
    proposal.path.rename(shadow)
    proposal.path.symlink_to(shadow)
    return scope, proposal, shadow


def test_propose_writes_proposal_and_moves_nothing(tmp_path):
    vault = _vault(tmp_path)
    scope, prop = _propose(vault)

    # proposal lives in the outbox
    assert prop.path.exists()
    assert prop.path.parent == scope.resolve("outbox")
    # the inbox item has NOT moved
    assert scope.resolve("00-inbox", "active", "note.md").exists()
    assert not scope.resolve("11-knowledge", "active", "note.md").exists()

    assert prop.src == "demo/00-inbox/active/note.md"
    assert prop.dst == "demo/11-knowledge/active/note.md"
    assert prop.sub == "kb" and prop.module == "11-knowledge"
    assert prop.status == "pending"


def test_proposal_derives_block_and_canonical_destination(tmp_path):
    vault = _vault(tmp_path)
    scope = Scope(vault, "demo")
    source = scope.resolve("00-inbox", "active", "note.md")

    prop = propose_classification(
        scope, source, module="11-library", sub="reference", claimed_block="govern"
    )

    record = yaml.safe_load(prop.path.read_text(encoding="utf-8"))
    assert record["entity"] == "demo"
    assert record["module"] == "11-library"
    assert record["sub"] == "reference"
    assert record["block"] == "govern"
    assert record["dst"] == "demo/11-library/active/note.md"


def test_proposal_interface_has_no_entity_or_trusted_block_authority():
    params = inspect.signature(propose_classification).parameters
    assert "entity" not in params
    assert "block" not in params
    assert "claimed_block" in params


def test_proposal_creation_is_exclusive_and_preserves_existing_record(
    tmp_path, monkeypatch
):
    vault = _vault(tmp_path)
    scope = Scope(vault, "demo")
    source = scope.resolve("00-inbox", "active", "note.md")
    monkeypatch.setattr(outbox, "datetime", _FixedDatetime)
    monkeypatch.setattr(
        proposal_identity.secrets, "token_hex", lambda size: "11" * 16
    )
    first = propose_classification(
        scope,
        source,
        module="11-knowledge",
        sub="kb",
        rule_id="first-rule",
    )
    before_head = git_head(vault)
    before_paths = git_tracked_paths(vault)
    before_tree = _vault_tree(vault)
    first_bytes = first.path.read_bytes()

    with pytest.raises(outbox.OutboxError):
        propose_classification(
            scope,
            source,
            module="11-knowledge",
            sub="kb",
            rule_id="second-rule",
        )

    assert first.path.read_bytes() == first_bytes
    assert git_head(vault) == before_head
    assert git_tracked_paths(vault) == before_paths
    assert _vault_tree(vault) == before_tree


def test_proposal_creation_rejects_same_entity_outbox_directory_redirect(
    tmp_path,
):
    vault = _vault(tmp_path)
    scope = Scope(vault, "demo")
    redirected = scope.resolve("redirected-outbox")
    redirected.mkdir()
    lexical_outbox = vault / "demo/outbox"
    lexical_outbox.symlink_to(redirected, target_is_directory=True)
    before_head = git_head(vault)
    before_paths = git_tracked_paths(vault)
    before_tree = _vault_tree(vault)

    with pytest.raises(CrossScopeError):
        propose_classification(
            scope,
            scope.resolve("00-inbox", "active", "note.md"),
            module="11-knowledge",
            sub="kb",
        )

    assert git_head(vault) == before_head
    assert git_tracked_paths(vault) == before_paths
    assert _vault_tree(vault) == before_tree


def test_proposal_creation_rejects_same_entity_leaf_symlink_without_write(
    tmp_path, monkeypatch
):
    vault = _vault(tmp_path)
    scope = Scope(vault, "demo")
    monkeypatch.setattr(outbox, "datetime", _FixedDatetime)
    monkeypatch.setattr(
        proposal_identity.secrets, "token_hex", lambda size: "11" * 16
    )
    outbox_dir = vault / "demo/outbox"
    outbox_dir.mkdir()
    protected = vault / "demo/.sensitive/protected.yaml"
    protected.parent.mkdir()
    protected.write_text("protected-marker\n", encoding="utf-8")
    proposal = outbox_dir / ("20260102T030405-" + "11" * 16 + ".yaml")
    proposal.symlink_to(protected)
    before_head = git_head(vault)
    before_paths = git_tracked_paths(vault)
    before_tree = _vault_tree(vault)

    with pytest.raises(CrossScopeError):
        propose_classification(
            scope,
            scope.resolve("00-inbox", "active", "note.md"),
            module="11-knowledge",
            sub="kb",
        )

    assert protected.read_text(encoding="utf-8") == "protected-marker\n"
    assert git_head(vault) == before_head
    assert git_tracked_paths(vault) == before_paths
    assert _vault_tree(vault) == before_tree


@pytest.mark.parametrize(
    ("module", "sub", "claimed_block", "item"),
    [
        ("missing", "kb", None, "canonical"),
        ("11-knowledge", "missing", None, "canonical"),
        ("11-knowledge", "kb", "forged", "canonical"),
        ("11-knowledge", "kb", None, "noncanonical"),
    ],
)
def test_invalid_classification_leaves_vault_and_outbox_unchanged(
    tmp_path, module, sub, claimed_block, item
):
    vault = _vault(tmp_path)
    scope = Scope(vault, "demo")
    source = scope.resolve("00-inbox", "active", "note.md")
    item_path = source if item == "canonical" else scope.resolve(
        "11-knowledge", "active", "note.md"
    )
    attempted_destination = vault / "demo" / module / "active" / source.name
    before_head = git_head(vault)
    before_paths = git_tracked_paths(vault)
    before_tree = _vault_tree(vault)
    assert not scope.resolve("outbox").exists()
    assert not attempted_destination.exists()

    with pytest.raises(DestinationError):
        propose_classification(
            scope,
            item_path,
            module=module,
            sub=sub,
            claimed_block=claimed_block,
        )

    assert not scope.resolve("outbox").exists()
    assert not attempted_destination.exists()
    assert git_head(vault) == before_head
    assert git_tracked_paths(vault) == before_paths
    assert _vault_tree(vault) == before_tree


def test_module_general_approval_removes_triage_sub_in_one_revertible_commit(
    tmp_path,
):
    import subprocess

    vault = _vault(tmp_path)
    scope = Scope(vault, "demo")
    prop = propose_classification(
        scope,
        scope.resolve("00-inbox", "active", "note.md"),
        module="11-library",
        sub="",
    )
    record = yaml.safe_load(prop.path.read_text(encoding="utf-8"))
    assert record["sub"] is None
    source = scope.resolve("00-inbox", "active", "note.md")
    assert "sub: triage" in source.read_text(encoding="utf-8")
    loaded = load_proposals(scope)
    assert len(loaded) == 1
    assert loaded[0].sub is None
    diff = preview_diff(scope, loaded[0])
    assert "-sub: triage" in diff
    assert "+sub:" not in diff

    before = git_count_commits(vault)
    approval = approve(scope, prop.id)
    approval_oid = git_head(vault)
    destination = vault / approval.dst

    assert git_count_commits(vault) == before + 1
    assert not re.search(r"(?m)^sub:", destination.read_text(encoding="utf-8"))
    assert not scope.resolve("00-inbox", "active", "note.md").exists()

    subprocess.run(
        ["git", "revert", "--no-edit", approval_oid],
        cwd=vault,
        check=True,
        capture_output=True,
    )
    restored = scope.resolve("00-inbox", "active", "note.md")
    assert restored.exists()
    assert "sub: triage" in restored.read_text(encoding="utf-8")
    assert not destination.exists()
    assert git_is_clean(vault)


def test_preview_diff_shows_move_and_sub_change(tmp_path):
    vault = _vault(tmp_path)
    scope, prop = _propose(vault)
    diff = preview_diff(scope, prop)
    assert "00-inbox/active/note.md" in diff
    assert "11-knowledge/active/note.md" in diff
    assert "-sub: triage" in diff and "+sub: kb" in diff


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("id", "20260815T090703-" + "22" * 16),
        ("source_sha256", "A" * 64),
        ("source_sha256", "not-a-sha256"),
        ("source_sha256", _MISSING),
    ],
    ids=("mismatched-id", "uppercase-hash", "malformed-hash", "missing-hash"),
)
def test_preview_revalidates_loaded_persisted_record_before_rendering(
    tmp_path, field, value
):
    vault = _vault(tmp_path)
    scope, proposed = _propose(vault)
    loaded = load_proposals(scope)[0]
    record = yaml.safe_load(proposed.path.read_text(encoding="utf-8"))
    if value is _MISSING:
        record.pop(field)
    else:
        record[field] = value
    proposed.path.write_text(yaml.safe_dump(record), encoding="utf-8")
    proposal_before = proposed.path.read_bytes()
    source = scope.resolve("00-inbox", "active", "note.md")
    source_before = source.read_bytes()
    destination = scope.root / loaded.dst
    before_head = git_head(vault)
    before_paths = git_tracked_paths(vault)
    before_tree = _vault_tree(vault)

    _assert_destination_error(lambda: preview_diff(scope, loaded))

    assert proposed.path.exists()
    assert proposed.path.read_bytes() == proposal_before
    assert source.read_bytes() == source_before
    assert not destination.exists()
    assert git_head(vault) == before_head
    assert git_tracked_paths(vault) == before_paths
    assert _vault_tree(vault) == before_tree


def test_load_proposals(tmp_path):
    vault = _vault(tmp_path)
    scope, prop = _propose(vault)
    props = load_proposals(scope)
    assert [p.id for p in props] == [prop.id]


@pytest.mark.parametrize(
    "source_hash",
    [None, 7, "a" * 63, "a" * 65, "A" * 64, "g" * 64],
)
def test_classification_hash_must_be_lowercase_sha256(two_entity_vault, source_hash):
    scope = Scope(two_entity_vault, "alpha")
    record = _canonical_alpha_record(scope)
    if source_hash is None:
        record.pop("source_sha256")
    else:
        record["source_sha256"] = source_hash
    _write_record(scope, f"{record['id']}.yaml", yaml.safe_dump(record))

    _assert_destination_error(lambda: load_proposals(scope))


@pytest.mark.parametrize(
    "filename,stored_id",
    [
        ("legacy.yaml", "legacy"),
        (
            "20260815T090703-" + "11" * 16 + ".yaml",
            "20260815T090703-" + "22" * 16,
        ),
        (
            "20261315T090703-" + "11" * 16 + ".yaml",
            "20261315T090703-" + "11" * 16,
        ),
    ],
)
def test_classification_id_and_filename_must_be_canonical(
    two_entity_vault, filename, stored_id
):
    scope = Scope(two_entity_vault, "alpha")
    record = _canonical_alpha_record(scope)
    record["id"] = stored_id
    _write_record(scope, filename, yaml.safe_dump(record))

    _assert_destination_error(lambda: load_proposals(scope))


def test_loading_rejects_same_entity_outbox_directory_redirect_without_read(
    tmp_path, monkeypatch
):
    vault = _vault(tmp_path)
    scope, proposal = _propose(vault)
    lexical_outbox = vault / "demo/outbox"
    redirected = vault / "demo/redirected-outbox"
    lexical_outbox.rename(redirected)
    lexical_outbox.symlink_to(redirected, target_is_directory=True)
    proposal_target = redirected / proposal.path.name
    real_read = Path.read_text

    def guarded(candidate, *args, **kwargs):
        if candidate == proposal_target:
            raise AssertionError("redirected proposal body was opened")
        return real_read(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded)
    before_head = git_head(vault)
    before_paths = git_tracked_paths(vault)
    before_tree = _vault_tree(vault)

    with pytest.raises(CrossScopeError):
        load_proposals(scope)

    assert git_head(vault) == before_head
    assert git_tracked_paths(vault) == before_paths
    assert _vault_tree(vault) == before_tree


def test_loading_rejects_same_entity_proposal_leaf_symlink_before_target_read(
    tmp_path, monkeypatch
):
    vault = _vault(tmp_path)
    scope, _, shadow = _redirect_proposal_leaf(vault)
    real_read = Path.read_text

    def guarded(candidate, *args, **kwargs):
        if candidate == shadow:
            raise AssertionError("redirected proposal body was opened")
        return real_read(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded)
    before_head = git_head(vault)
    before_paths = git_tracked_paths(vault)
    before_tree = _vault_tree(vault)

    with pytest.raises(CrossScopeError):
        load_proposals(scope)

    assert git_head(vault) == before_head
    assert git_tracked_paths(vault) == before_paths
    assert _vault_tree(vault) == before_tree


def test_loading_classifications_skips_valid_registry_delete_record(tmp_path):
    vault = _vault(tmp_path)
    scope, classification = _propose(vault)
    delete_id = "20260815T090703-" + "33" * 16
    _write_record(
        scope,
        f"{delete_id}.yaml",
        yaml.safe_dump(
            {
                "id": delete_id,
                "action": "delete",
                "entity": "demo",
                "kind": "product",
                "slug": "invented-product",
                "status": "pending",
                "total_references": 0,
                "impact": {},
            }
        ),
    )

    assert [proposal.id for proposal in load_proposals(scope)] == [classification.id]


def test_loading_rejects_unknown_proposal_action(tmp_path):
    vault = _vault(tmp_path)
    scope = Scope(vault, "demo")
    proposal_id = "20260815T090703-" + "33" * 16
    record = {
        "id": proposal_id,
        "action": "publish",
        "entity": "demo",
        "src": "demo/00-inbox/active/note.md",
        "dst": "demo/11-knowledge/active/note.md",
        "module": "11-knowledge",
        "sub": "kb",
        "block": "govern",
    }
    _write_record(scope, f"{proposal_id}.yaml", yaml.safe_dump(record))
    before_head = git_head(vault)
    before_paths = git_tracked_paths(vault)
    before_tree = _vault_tree(vault)

    _assert_destination_error(lambda: load_proposals(scope))

    assert git_head(vault) == before_head
    assert git_tracked_paths(vault) == before_paths
    assert _vault_tree(vault) == before_tree


@pytest.mark.parametrize(
    ("field", "value"),
    [
        *(
            pytest.param(field, value, id=f"{field}-{kind}")
            for field in (
                "id",
                "action",
                "entity",
                "src",
                "dst",
                "module",
                "block",
            )
            for value, kind in (
                (["not-a-scalar"], "list"),
                (7, "int"),
                (_MISSING, "missing"),
            )
        ),
        pytest.param("sub", ["kb"], id="sub-list"),
        pytest.param("sub", 7, id="sub-int"),
        pytest.param("sub", {"id": "kb"}, id="sub-mapping"),
        pytest.param("sub", "", id="sub-empty"),
        pytest.param("sub", _MISSING, id="sub-missing"),
    ],
)
def test_loading_rejects_malformed_destination_scalars(
    two_entity_vault, field, value
):
    scope = Scope(two_entity_vault, "alpha")
    record = _canonical_alpha_record(scope)
    filename = f"{record['id']}.yaml"
    if value is _MISSING:
        record.pop(field)
    else:
        record[field] = value
    _write_record(scope, filename, yaml.safe_dump(record))

    _assert_destination_error(lambda: load_proposals(scope))


def test_loading_rejects_non_mapping_proposal_record(two_entity_vault):
    scope = Scope(two_entity_vault, "alpha")
    _write_record(scope, "malformed.yaml", yaml.safe_dump(["not", "a", "mapping"]))

    _assert_destination_error(lambda: load_proposals(scope))


def test_loading_wraps_invalid_yaml_without_mutation(two_entity_vault):
    scope = Scope(two_entity_vault, "alpha")
    _write_record(scope, "invalid.yaml", "action: classify\nmodule: [unterminated\n")
    before_head = git_head(two_entity_vault)
    before_paths = git_tracked_paths(two_entity_vault)
    before_tree = _vault_tree(two_entity_vault)

    _assert_destination_error(lambda: load_proposals(scope))

    assert git_head(two_entity_vault) == before_head
    assert git_tracked_paths(two_entity_vault) == before_paths
    assert _vault_tree(two_entity_vault) == before_tree


@pytest.mark.parametrize(
    ("case", "field", "value"),
    [
        ("active different module", "module", "11-library"),
        ("sub registered to another module", "sub", "reference"),
        ("incorrect block", "block", "system"),
        (
            "source filename mismatch",
            "src",
            "alpha/00-inbox/active/different.md",
        ),
        (
            "destination filename mismatch",
            "dst",
            "alpha/11-knowledge/active/different.md",
        ),
        (
            "destination module mismatch",
            "dst",
            "alpha/11-library/active/alpha.md",
        ),
        (
            "destination extra path segment",
            "dst",
            "alpha/11-knowledge/active/nested/alpha.md",
        ),
    ],
)
@pytest.mark.parametrize("operation", ["load", "preview", "approve"])
def test_operations_reject_noncanonical_destination_without_mutation(
    two_entity_vault, case, field, value, operation
):
    scope = Scope(two_entity_vault, "alpha")
    record = _canonical_alpha_record(scope)
    record[field] = value
    path = _write_record(scope, f"{record['id']}.yaml", yaml.safe_dump(record))
    before_head = git_head(two_entity_vault)
    before_paths = git_tracked_paths(two_entity_vault)
    before_tree = _vault_tree(two_entity_vault)

    if operation == "load":
        attempt = lambda: load_proposals(scope)
    elif operation == "preview":
        proposal = _proposal_from_record(path, record)
        attempt = lambda: preview_diff(scope, proposal)
    else:
        attempt = lambda: approve(scope, record["id"])

    _assert_destination_error(attempt)

    assert git_head(two_entity_vault) == before_head
    assert git_tracked_paths(two_entity_vault) == before_paths
    assert _vault_tree(two_entity_vault) == before_tree


@pytest.mark.parametrize("operation", ["load", "preview", "approve"])
def test_noncanonical_destination_fails_before_source_body_read(
    two_entity_vault, monkeypatch, operation
):
    scope = Scope(two_entity_vault, "alpha")
    record = _canonical_alpha_record(scope)
    record["block"] = "system"
    path = _write_record(scope, f"{record['id']}.yaml", yaml.safe_dump(record))
    source = scope.resolve("00-inbox", "active", "alpha.md")
    real_read = Path.read_text

    def guarded(candidate, *args, **kwargs):
        if candidate.resolve() == source.resolve():
            raise AssertionError("source receipt body was opened before validation")
        return real_read(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded)
    if operation == "load":
        attempt = lambda: load_proposals(scope)
    elif operation == "preview":
        proposal = _proposal_from_record(path, record)
        attempt = lambda: preview_diff(scope, proposal)
    else:
        attempt = lambda: approve(scope, record["id"])

    _assert_destination_error(attempt)


def test_approve_moves_file_and_makes_one_commit(tmp_path):
    vault = _vault(tmp_path)
    scope, prop = _propose(vault)
    before = git_count_commits(vault)

    approve(scope, prop.id)

    assert not scope.resolve("00-inbox", "active", "note.md").exists()
    dst = scope.resolve("11-knowledge", "active", "note.md")
    assert dst.exists()
    assert "sub: kb" in dst.read_text()
    assert not prop.path.exists()

    assert git_count_commits(vault) == before + 1
    assert git_head_message(vault).startswith("outbox: approve")
    assert git_is_clean(vault)


def test_approval_refuses_changed_source_without_any_added_mutation(tmp_path):
    vault = _vault(tmp_path)
    scope, prop = _propose(vault)
    source = scope.resolve("00-inbox", "active", "note.md")
    source.write_bytes(source.read_bytes() + b"changed-after-proposal\n")
    proposal_bytes = prop.path.read_bytes()
    before = _approval_state(vault)

    with pytest.raises(outbox.StaleProposalSource):
        approve(scope, prop.id)

    assert _approval_state(vault) == before
    assert prop.path.read_bytes() == proposal_bytes
    assert source.exists()
    assert not scope.resolve("11-knowledge", "active", "note.md").exists()


def test_approval_refuses_missing_source_and_preserves_proposal(tmp_path):
    vault = _vault(tmp_path)
    scope, prop = _propose(vault)
    source = scope.resolve("00-inbox", "active", "note.md")
    source.unlink()
    proposal_bytes = prop.path.read_bytes()
    before = _approval_state(vault)

    with pytest.raises(outbox.MissingProposalSource):
        approve(scope, prop.id)

    assert _approval_state(vault) == before
    assert prop.path.read_bytes() == proposal_bytes
    assert not scope.resolve("11-knowledge", "active", "note.md").exists()


def test_approval_commits_bytes_from_verified_snapshot(tmp_path, monkeypatch):
    vault = _vault(tmp_path)
    scope, prop = _propose(vault)
    source = scope.resolve("00-inbox", "active", "note.md")
    reviewed_marker = b"Randomised trial protocol body."
    raced_marker = b"replacement-after-verification"
    real_git = outbox._git

    def race_before_move(root, *args):
        if args[:1] == ("mv",):
            source.write_bytes(
                source.read_bytes().replace(reviewed_marker, raced_marker)
            )
        return real_git(root, *args)

    monkeypatch.setattr(outbox, "_git", race_before_move)

    approve(scope, prop.id)

    destination = scope.resolve("11-knowledge", "active", "note.md")
    assert reviewed_marker in destination.read_bytes()
    assert raced_marker not in destination.read_bytes()
    assert git_is_clean(vault)


def test_real_adapter_receipt_approval_is_one_later_revertible_commit(tmp_path):
    import subprocess

    from app.ingest.adapters.folder import process_drop

    vault = _outbox_vault(tmp_path / "vault", ("synthetic",), {
        "synthetic/00-inbox/active/.gitkeep": "",
        "synthetic/11-knowledge/active/.gitkeep": "",
        "synthetic/11-library/active/.gitkeep": "",
    })
    source = tmp_path / "dropbox/research.txt"
    source.parent.mkdir()
    source.write_text("Synthetic research summary.\n", encoding="utf-8")
    result = process_drop(
        Scope(vault, "synthetic"), source, raw_archive=tmp_path / "raw"
    )
    ingest_oid = git_head(vault)
    triage_rel = result.path.relative_to(vault).as_posix()

    assert git_head_message(vault) == "ingest: add redacted receipt"
    assert git_changed_paths(vault) == [triage_rel]
    assert triage_rel in git_tracked_paths(vault)

    scope = Scope(vault, "synthetic")
    prop = propose_classification(
        scope, result.path,
        module="11-library", sub="reference", claimed_block="govern",
        rule_id="synthetic-rule",
    )
    approve(scope, prop.id)
    approval_oid = git_head(vault)

    assert approval_oid != ingest_oid
    assert git_head_message(vault).startswith("outbox: approve")
    assert git_count_commits(vault) == 3

    subprocess.run(
        ["git", "revert", "--no-edit", approval_oid], cwd=vault,
        check=True, capture_output=True,
    )
    assert result.path.exists()
    assert triage_rel in git_tracked_paths(vault)
    assert not (vault / prop.dst).exists()
    assert git_is_clean(vault)


def test_reject_discards_proposal_without_moving(tmp_path):
    vault = _vault(tmp_path)
    scope, prop = _propose(vault)
    reject(scope, prop.id)
    assert not prop.path.exists()
    assert scope.resolve("00-inbox", "active", "note.md").exists()
    assert load_proposals(scope) == []


@pytest.mark.parametrize("operation", ("preview", "approve", "reject"))
def test_operations_reject_same_entity_proposal_leaf_symlink_without_mutation(
    tmp_path, operation
):
    vault = _vault(tmp_path)
    scope, proposal, _ = _redirect_proposal_leaf(vault)
    before_head = git_head(vault)
    before_paths = git_tracked_paths(vault)
    before_tree = _vault_tree(vault)

    if operation == "preview":
        attempt = lambda: preview_diff(scope, proposal)
    elif operation == "approve":
        attempt = lambda: approve(scope, proposal.id)
    else:
        attempt = lambda: reject(scope, proposal.id)

    with pytest.raises(CrossScopeError):
        attempt()

    assert git_head(vault) == before_head
    assert git_tracked_paths(vault) == before_paths
    assert _vault_tree(vault) == before_tree


@pytest.mark.parametrize("operation", (approve, reject), ids=("approve", "reject"))
def test_mutation_revalidates_lexical_proposal_leaf_after_lookup(
    tmp_path, monkeypatch, operation
):
    vault = _vault(tmp_path)
    scope, proposal = _propose(vault)
    real_get = outbox.get_proposal
    state = {}

    def redirect_after_lookup(bound_scope, proposal_id):
        loaded = real_get(bound_scope, proposal_id)
        shadow = bound_scope.resolve("proposal-shadow", loaded.path.name)
        shadow.parent.mkdir()
        loaded.path.rename(shadow)
        loaded.path.symlink_to(shadow)
        state["head"] = git_head(vault)
        state["paths"] = git_tracked_paths(vault)
        state["tree"] = _vault_tree(vault)
        return loaded

    monkeypatch.setattr(outbox, "get_proposal", redirect_after_lookup)

    with pytest.raises(CrossScopeError):
        operation(scope, proposal.id)

    assert git_head(vault) == state["head"]
    assert git_tracked_paths(vault) == state["paths"]
    assert _vault_tree(vault) == state["tree"]


@pytest.mark.parametrize("shape", ("missing", "redirected"))
def test_approval_never_scaffolds_destination_parent_after_validation(
    tmp_path, monkeypatch, shape
):
    vault = _vault(tmp_path)
    scope, proposal = _propose(vault)
    real_get = outbox.get_proposal
    state = {}

    def change_parent_after_lookup(bound_scope, proposal_id):
        loaded = real_get(bound_scope, proposal_id)
        active = vault / "demo/11-knowledge/active"
        saved = vault / "demo/11-knowledge/saved-active"
        active.rename(saved)
        if shape == "redirected":
            active.symlink_to(saved, target_is_directory=True)
        state["head"] = git_head(vault)
        state["paths"] = git_tracked_paths(vault)
        state["tree"] = _vault_tree(vault)
        return loaded

    monkeypatch.setattr(outbox, "get_proposal", change_parent_after_lookup)

    _assert_destination_error(lambda: approve(scope, proposal.id))

    assert git_head(vault) == state["head"]
    assert git_tracked_paths(vault) == state["paths"]
    assert _vault_tree(vault) == state["tree"]
    assert scope.resolve("00-inbox", "active", "note.md").exists()


def test_proposal_discovery_rejects_cross_scope_leaf_symlink(tmp_path):
    proposal_id = "20260815T090703-" + "44" * 16
    source = b"---\ntype: inbox-item\nsub: triage\n---\nbeta receipt\n"
    record = yaml.safe_dump(
        {
            "id": proposal_id,
            "action": "classify",
            "entity": "beta",
            "src": "beta/00-inbox/active/note.md",
            "source_sha256": hashlib.sha256(source).hexdigest(),
            "dst": "beta/11-knowledge/active/note.md",
            "module": "11-knowledge",
            "sub": "kb",
            "block": "govern",
        }
    )
    vault = _outbox_vault(
        tmp_path,
        ("alpha", "beta"),
        {
            "alpha/00-inbox/active/.gitkeep": "",
            "beta/00-inbox/active/.gitkeep": "",
            "beta/00-inbox/active/note.md": source.decode("utf-8"),
            "alpha/11-knowledge/active/.gitkeep": "",
            "beta/11-knowledge/active/.gitkeep": "",
            "alpha/11-library/active/.gitkeep": "",
            "beta/11-library/active/.gitkeep": "",
            "alpha/outbox/.gitkeep": "",
            f"beta/outbox/{proposal_id}.yaml": record,
        },
    )
    (vault / "alpha/outbox/linked.yaml").symlink_to(
        vault / "beta/outbox" / f"{proposal_id}.yaml"
    )

    with pytest.raises(CrossScopeError):
        load_proposals(Scope(vault, "alpha"))


def test_loading_mismatched_proposal_fails_before_other_entity_source_read(
    two_entity_vault, monkeypatch
):
    scope = Scope(two_entity_vault, "alpha")
    record = _forged_beta_record(scope)
    _write_record(scope, f"{record['id']}.yaml", yaml.safe_dump(record))
    beta_source = two_entity_vault / "beta/00-inbox/active/beta.md"
    real_read = Path.read_text

    def guarded(path, *args, **kwargs):
        if path.resolve() == beta_source.resolve():
            raise AssertionError("cross-entity source was opened")
        return real_read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded)
    with pytest.raises(outbox.OutboxScopeError):
        load_proposals(scope)


def test_outbox_interfaces_have_one_identity_authority():
    for function in (
        propose_classification,
        load_proposals,
        approve,
        reject,
    ):
        assert "entity" not in inspect.signature(function).parameters


def test_propose_rejects_item_path_from_another_entity(two_entity_vault):
    alpha = Scope(two_entity_vault, "alpha")
    beta_item = two_entity_vault / "beta/00-inbox/active/beta.md"
    attempted_destination = alpha.resolve("11-knowledge", "active", "beta.md")
    before_head = git_head(two_entity_vault)
    before_paths = git_tracked_paths(two_entity_vault)
    before_tree = _vault_tree(two_entity_vault)
    assert not alpha.resolve("outbox").exists()
    assert not attempted_destination.exists()

    with pytest.raises(DestinationError):
        propose_classification(
            alpha,
            beta_item,
            module="11-knowledge",
            sub="kb",
            claimed_block="govern",
        )

    assert not alpha.resolve("outbox").exists()
    assert not attempted_destination.exists()
    assert git_head(two_entity_vault) == before_head
    assert git_tracked_paths(two_entity_vault) == before_paths
    assert _vault_tree(two_entity_vault) == before_tree


def test_preview_diff_rejects_proposal_bound_to_another_entity(two_entity_vault):
    alpha = Scope(two_entity_vault, "alpha")
    record = _forged_beta_record(alpha)
    record_path = _write_record(
        alpha, f"{record['id']}.yaml", yaml.safe_dump(record)
    )
    proposal = Proposal(
        id=record["id"],
        path=record_path,
        action="classify",
        entity="beta",
        src="beta/00-inbox/active/beta.md",
        source_sha256=record["source_sha256"],
        dst="beta/11-knowledge/active/beta.md",
        module="11-knowledge",
        sub="kb",
        block="govern",
        rule_id=None,
        created="",
    )
    record_before = record_path.read_bytes()
    source = two_entity_vault / "beta/00-inbox/active/beta.md"
    source_before = source.read_bytes()

    with pytest.raises(outbox.OutboxScopeError):
        preview_diff(alpha, proposal)

    assert record_path.read_bytes() == record_before
    assert source.read_bytes() == source_before


@pytest.mark.parametrize("operation", [approve, reject])
def test_mutation_rejects_foreign_record_and_leaves_both_entities_unchanged(
    two_entity_vault, operation
):
    alpha = Scope(two_entity_vault, "alpha")
    beta = Scope(two_entity_vault, "beta")
    record = _forged_beta_record(alpha)
    alpha_record = _write_record(
        alpha, f"{record['id']}.yaml", yaml.safe_dump(record)
    )
    beta_record = _write_record(
        beta, f"{record['id']}.yaml", yaml.safe_dump(record)
    )
    watched = (
        alpha_record,
        beta_record,
        two_entity_vault / "alpha/00-inbox/active/alpha.md",
        two_entity_vault / "beta/00-inbox/active/beta.md",
    )
    before = {path: path.read_bytes() for path in watched}

    with pytest.raises(outbox.OutboxScopeError):
        operation(alpha, record["id"])

    assert {path: path.read_bytes() for path in watched} == before
    assert not (two_entity_vault / "alpha/11-knowledge/active/beta.md").exists()
    assert not (two_entity_vault / "beta/11-knowledge/active/beta.md").exists()


@pytest.mark.parametrize(
    ("field", "foreign_value"),
    [
        ("src", "beta/00-inbox/active/beta.md"),
        ("dst", "beta/11-knowledge/active/beta.md"),
    ],
)
def test_loading_wraps_foreign_stored_path_as_destination_error(
    two_entity_vault, field, foreign_value
):
    alpha = Scope(two_entity_vault, "alpha")
    record = _canonical_alpha_record(alpha)
    record[field] = foreign_value
    _write_record(alpha, f"{record['id']}.yaml", yaml.safe_dump(record))

    _assert_destination_error(lambda: load_proposals(alpha))


def test_loading_wraps_destination_registry_error(two_entity_vault):
    scope = Scope(two_entity_vault, "alpha")
    record = _canonical_alpha_record(scope)
    _write_record(
        scope,
        f"{record['id']}.yaml",
        yaml.safe_dump(record),
    )
    (two_entity_vault / "_system/archetypes.yaml").write_text(
        OUTBOX_ARCHETYPES.replace("submodules:\n", "submodules: []\ninvalid:\n"),
        encoding="utf-8",
    )

    _assert_destination_error(lambda: load_proposals(scope))


def test_concurrent_proposals_are_written_only_to_the_bound_entity(two_entity_vault):
    alpha = Scope(two_entity_vault, "alpha")
    beta = Scope(two_entity_vault, "beta")
    barrier = threading.Barrier(2)
    real_propose = propose_classification

    def overlapped(scope, item_path):
        barrier.wait(timeout=5)
        return real_propose(
            scope,
            item_path,
            module="11-knowledge",
            sub="kb",
            claimed_block="govern",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        alpha_future = pool.submit(
            overlapped, alpha, two_entity_vault / "alpha/00-inbox/active/alpha.md"
        )
        beta_future = pool.submit(
            overlapped, beta, two_entity_vault / "beta/00-inbox/active/beta.md"
        )
    alpha_future.result()
    beta_future.result()

    assert list((two_entity_vault / "alpha/outbox").glob("*.yaml"))
    assert list((two_entity_vault / "beta/outbox").glob("*.yaml"))
    assert not list((two_entity_vault / "alpha/outbox").glob("*beta*.yaml"))
    assert not list((two_entity_vault / "beta/outbox").glob("*alpha*.yaml"))
