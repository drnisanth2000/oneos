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

import app.git_transaction as git_transaction
import app.outbox as outbox
import app.proposal_identity as proposal_identity
from app.git_transaction import (
    GitTransactionFailure,
    ReviewedStateConflict,
    VaultBusyError,
)
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
    git_bytes,
    git_changed_paths,
    git_count_commits,
    git_head,
    git_head_message,
    git_index_entries,
    git_is_clean,
    git_status_bytes,
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


def _add_unrelated_git_dirt(vault: Path) -> tuple[dict[str, bytes], bytes]:
    paths = ("staged.bin", "unstaged.bin")
    (vault / paths[0]).write_bytes(b"staged base\n")
    (vault / paths[1]).write_bytes(b"unstaged base\n")
    git_bytes(vault, "add", *paths)
    git_bytes(vault, "commit", "-q", "-m", "add unrelated fixtures")

    expected = {
        paths[0]: b"staged exact\x00\xff\n",
        paths[1]: b"unstaged exact\x00\xfe\n",
        "untracked.bin": b"untracked exact\x00\xfd\n",
    }
    (vault / paths[0]).write_bytes(expected[paths[0]])
    git_bytes(vault, "add", paths[0])
    (vault / paths[1]).write_bytes(expected[paths[1]])
    (vault / "untracked.bin").write_bytes(expected["untracked.bin"])
    index_entries = git_bytes(vault, "ls-files", "--stage", "-z", "--", *paths)
    return expected, index_entries


def _assert_unrelated_git_dirt(
    vault: Path, expected: dict[str, bytes], index_entries: bytes
) -> None:
    for relative_path, contents in expected.items():
        assert (vault / relative_path).read_bytes() == contents
    assert git_bytes(
        vault, "ls-files", "--stage", "-z", "--", "staged.bin", "unstaged.bin"
    ) == index_entries


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


def _fp(scope: Scope, proposal_id: str) -> str:
    """The fingerprint of the proposal exactly as it now stands.

    S7 test convenience only: a real operator's fingerprint comes from the
    review they were shown. Tests that need a *stale* fingerprint capture it
    before mutating the record.
    """
    return outbox.get_proposal_review(scope, proposal_id).sha256


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
    approval = approve(scope, prop.id, _fp(scope, prop.id))
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
        attempt = lambda: approve(scope, record["id"], _fp(scope, record["id"]))

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
        attempt = lambda: approve(scope, record["id"], _fp(scope, record["id"]))

    _assert_destination_error(attempt)


def test_approve_moves_file_and_makes_one_commit(tmp_path):
    vault = _vault(tmp_path)
    scope, prop = _propose(vault)
    before = git_count_commits(vault)

    approve(scope, prop.id, _fp(scope, prop.id))

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
        approve(scope, prop.id, _fp(scope, prop.id))

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
        approve(scope, prop.id, _fp(scope, prop.id))

    assert _approval_state(vault) == before
    assert prop.path.read_bytes() == proposal_bytes
    assert not scope.resolve("11-knowledge", "active", "note.md").exists()


def test_approval_with_unrelated_staged_unstaged_and_untracked_work_commits_only_source_and_destination(
    tmp_path,
):
    vault = _vault(tmp_path)
    unrelated, unrelated_index = _add_unrelated_git_dirt(vault)
    scope, prop = _propose(vault)
    head_before = git_head(vault)

    approve(scope, prop.id, _fp(scope, prop.id))

    assert git_changed_paths(vault) == sorted([prop.src, prop.dst])
    assert subprocess.run(
        ["git", "rev-list", "--count", f"{head_before}..HEAD"],
        cwd=vault,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip() == "1"
    _assert_unrelated_git_dirt(vault, unrelated, unrelated_index)


@pytest.mark.parametrize("dirty_path", ("source", "destination"))
def test_reviewed_source_or_destination_index_dirt_is_refused_before_mutation(
    tmp_path, dirty_path
):
    vault = _vault(tmp_path)
    scope, prop = _propose(vault)
    source = vault / prop.src
    destination = vault / prop.dst
    source_bytes = source.read_bytes()
    proposal_bytes = prop.path.read_bytes()

    if dirty_path == "source":
        source.write_bytes(b"unexpected indexed source\n")
        git_bytes(vault, "add", prop.src)
        source.write_bytes(source_bytes)
    else:
        destination.write_bytes(b"unexpected indexed destination\n")
        git_bytes(vault, "add", prop.dst)
        destination.unlink()
    head_before = git_head(vault)
    index_before = git_index_entries(vault)
    status_before = git_status_bytes(vault)

    with pytest.raises(outbox.OutboxTransactionError) as raised:
        approve(scope, prop.id, _fp(scope, prop.id))

    assert isinstance(raised.value.__cause__, ReviewedStateConflict)
    assert git_head(vault) == head_before
    assert source.read_bytes() == source_bytes
    assert destination.exists() is False
    assert prop.path.read_bytes() == proposal_bytes
    assert git_index_entries(vault) == index_before
    assert git_status_bytes(vault) == status_before


def test_approval_busy_error_preserves_source_destination_proposal_and_git_state(
    tmp_path,
):
    vault = _vault(tmp_path)
    scope, prop = _propose(vault)
    source = vault / prop.src
    destination = vault / prop.dst
    source_bytes = source.read_bytes()
    proposal_bytes = prop.path.read_bytes()
    head_before = git_head(vault)
    index_before = git_index_entries(vault)
    status_before = git_status_bytes(vault)

    with git_transaction._approval_lock(vault):
        with pytest.raises(outbox.OutboxTransactionError) as raised:
            approve(scope, prop.id, _fp(scope, prop.id))

    assert isinstance(raised.value.__cause__, VaultBusyError)
    assert git_head(vault) == head_before
    assert source.read_bytes() == source_bytes
    assert destination.exists() is False
    assert prop.path.read_bytes() == proposal_bytes
    assert git_index_entries(vault) == index_before
    assert git_status_bytes(vault) == status_before


def test_injected_transaction_failure_restores_source_destination_and_exact_proposal_bytes(
    tmp_path, monkeypatch
):
    vault = _vault(tmp_path)
    unrelated, unrelated_index = _add_unrelated_git_dirt(vault)
    scope, prop = _propose(vault)
    source = vault / prop.src
    destination = vault / prop.dst
    source_bytes = source.read_bytes()
    proposal_bytes = prop.path.read_bytes()
    head_before = git_head(vault)

    def fail_after_filesystem_apply(checkpoint: str) -> None:
        if checkpoint == "filesystem-applied":
            raise OSError("injected classification transaction failure")

    monkeypatch.setattr(git_transaction, "_checkpoint", fail_after_filesystem_apply)

    with pytest.raises(outbox.OutboxTransactionError) as raised:
        approve(scope, prop.id, _fp(scope, prop.id))

    assert isinstance(raised.value.__cause__, GitTransactionFailure)
    assert git_head(vault) == head_before
    assert source.read_bytes() == source_bytes
    assert destination.exists() is False
    assert prop.path.read_bytes() == proposal_bytes
    _assert_unrelated_git_dirt(vault, unrelated, unrelated_index)


def test_approval_transaction_error_is_an_outbox_error(tmp_path, monkeypatch):
    vault = _vault(tmp_path)
    scope, prop = _propose(vault)

    def fail_transaction(*_args, **_kwargs):
        raise GitTransactionFailure("injected transaction failure")

    monkeypatch.setattr(outbox, "execute_transaction", fail_transaction, raising=False)

    with pytest.raises(outbox.OutboxTransactionError) as raised:
        approve(scope, prop.id, _fp(scope, prop.id))

    assert isinstance(raised.value, outbox.OutboxError)
    assert isinstance(raised.value.__cause__, GitTransactionFailure)


def test_approval_refuses_race_after_verified_snapshot_without_overwriting_source(
    tmp_path, monkeypatch
):
    vault = _vault(tmp_path)
    scope, prop = _propose(vault)
    source = scope.resolve("00-inbox", "active", "note.md")
    reviewed_marker = b"Randomised trial protocol body."
    raced_marker = b"replacement-after-verification"
    proposal_bytes = prop.path.read_bytes()
    head_before = git_head(vault)
    real_capture = outbox.capture_path_state
    raced = False

    def race_after_source_capture(root, relative_path):
        nonlocal raced
        state = real_capture(root, relative_path)
        if relative_path == prop.src and not raced:
            raced = True
            source.write_bytes(
                source.read_bytes().replace(reviewed_marker, raced_marker)
            )
        return state

    monkeypatch.setattr(outbox, "capture_path_state", race_after_source_capture)

    with pytest.raises(outbox.OutboxTransactionError) as raised:
        approve(scope, prop.id, _fp(scope, prop.id))

    destination = scope.resolve("11-knowledge", "active", "note.md")
    assert isinstance(raised.value.__cause__, ReviewedStateConflict)
    assert git_head(vault) == head_before
    assert raced_marker in source.read_bytes()
    assert reviewed_marker not in source.read_bytes()
    assert destination.exists() is False
    assert prop.path.read_bytes() == proposal_bytes


def test_classification_approval_still_creates_exactly_one_commit_and_one_revert_restores_both_paths(
    tmp_path,
):
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

    unrelated = vault / "unrelated.bin"
    unrelated_bytes = b"unrelated exact\x00\xff\n"
    unrelated.write_bytes(unrelated_bytes)
    scope = Scope(vault, "synthetic")
    prop = propose_classification(
        scope, result.path,
        module="11-library", sub="reference", claimed_block="govern",
        rule_id="synthetic-rule",
    )
    before_approval = git_head(vault)
    approve(scope, prop.id, _fp(scope, prop.id))
    approval_oid = git_head(vault)

    assert approval_oid != ingest_oid
    assert git_head_message(vault).startswith("outbox: approve")
    assert subprocess.run(
        ["git", "rev-list", "--count", f"{before_approval}..{approval_oid}"],
        cwd=vault,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip() == "1"
    assert git_changed_paths(vault, approval_oid) == sorted([prop.src, prop.dst])
    assert unrelated.read_bytes() == unrelated_bytes

    subprocess.run(
        ["git", "revert", "--no-edit", approval_oid], cwd=vault,
        check=True, capture_output=True,
    )
    assert result.path.exists()
    assert triage_rel in git_tracked_paths(vault)
    assert not (vault / prop.dst).exists()
    assert unrelated.read_bytes() == unrelated_bytes


def test_reject_discards_proposal_without_moving(tmp_path):
    vault = _vault(tmp_path)
    scope, prop = _propose(vault)
    reject(scope, prop.id, _fp(scope, prop.id))
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
        attempt = lambda: approve(scope, proposal.id, _fp(scope, proposal.id))
    else:
        attempt = lambda: reject(scope, proposal.id, _fp(scope, proposal.id))

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
    fingerprint = _fp(scope, proposal.id)
    # S7: actions no longer read through `get_proposal`; they locate the
    # leaf through `get_proposal_review`. Patching the reader they actually
    # use keeps this test's point — the mutation must revalidate the leaf
    # *after* the lookup — instead of leaving it inert.
    real_get = outbox.get_proposal_review
    state = {}

    def redirect_after_lookup(bound_scope, proposal_id):
        review = real_get(bound_scope, proposal_id)
        loaded = review.value
        shadow = bound_scope.resolve("proposal-shadow", loaded.path.name)
        shadow.parent.mkdir()
        loaded.path.rename(shadow)
        loaded.path.symlink_to(shadow)
        state["head"] = git_head(vault)
        state["paths"] = git_tracked_paths(vault)
        state["tree"] = _vault_tree(vault)
        return review

    monkeypatch.setattr(outbox, "get_proposal_review", redirect_after_lookup)

    with pytest.raises(CrossScopeError):
        operation(scope, proposal.id, fingerprint)

    assert git_head(vault) == state["head"]
    assert git_tracked_paths(vault) == state["paths"]
    assert _vault_tree(vault) == state["tree"]


@pytest.mark.parametrize("shape", ("missing", "redirected"))
def test_approval_never_scaffolds_destination_parent_after_validation(
    tmp_path, monkeypatch, shape
):
    vault = _vault(tmp_path)
    scope, proposal = _propose(vault)
    real_get = outbox.get_proposal_review
    state = {}

    def change_parent_after_lookup(bound_scope, proposal_id):
        review = real_get(bound_scope, proposal_id)
        active = vault / "demo/11-knowledge/active"
        saved = vault / "demo/11-knowledge/saved-active"
        active.rename(saved)
        if shape == "redirected":
            active.symlink_to(saved, target_is_directory=True)
        state["head"] = git_head(vault)
        state["paths"] = git_tracked_paths(vault)
        state["tree"] = _vault_tree(vault)
        return review

    fingerprint = _fp(scope, proposal.id)
    monkeypatch.setattr(outbox, "get_proposal_review", change_parent_after_lookup)

    _assert_destination_error(lambda: approve(scope, proposal.id, fingerprint))

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


def test_public_routes_remain_single_proposal_actions():
    """Still one proposal per call — now bound to that proposal's review."""
    bound = ("scope", "proposal_id", "review_sha256")
    assert tuple(inspect.signature(approve).parameters) == bound
    assert tuple(inspect.signature(reject).parameters) == bound


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

    # A well-formed fingerprint that binds nothing: the scope refusal is
    # independent of it, and must come first.
    with pytest.raises(outbox.OutboxScopeError):
        operation(alpha, record["id"], "0" * 64)

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


# --- S7 Task 2: one byte snapshot behind every classification review --------


def _review_imports():
    from app.outbox import get_proposal_review

    return get_proposal_review


def _proposal_bytes(prop: Proposal) -> bytes:
    return prop.path.read_bytes()


def _rewrite_record(path: Path, **changes) -> bytes:
    """Rewrite a stored proposal in place, preserving id and filename."""
    record = yaml.safe_load(path.read_text(encoding="utf-8"))
    record.update(changes)
    raw = yaml.safe_dump(record, sort_keys=False).encode("utf-8")
    path.write_bytes(raw)
    return raw


def test_get_proposal_review_returns_the_value_its_bytes_and_their_hash(tmp_path):
    get_proposal_review = _review_imports()
    scope, prop = _propose(_vault(tmp_path))

    review = get_proposal_review(scope, prop.id)

    stored = _proposal_bytes(prop)
    assert review.value.id == prop.id
    assert review.value == prop
    assert review.contents == stored
    assert review.sha256 == hashlib.sha256(stored).hexdigest()


def test_review_value_and_hash_come_from_one_capture_not_a_second_read(tmp_path):
    """The core Task 2 proof.

    The stored file is replaced *between* the byte capture and everything
    that follows. If the reader parses the path again — instead of the bytes
    it captured — the returned value describes the replacement while the
    digest describes the capture, and the two no longer agree about
    anything. The operator would then review one proposal and act on
    another, which is precisely the S7 defect.
    """
    get_proposal_review = _review_imports()
    scope, prop = _propose(_vault(tmp_path))

    original = _proposal_bytes(prop)
    replacement_holder = {}

    real_capture = outbox.capture_path_state

    def capture_then_replace(vault, relative_path, *args, **kwargs):
        state = real_capture(vault, relative_path, *args, **kwargs)
        if relative_path.endswith(f"{prop.id}.yaml") and not replacement_holder:
            # The instant after the bytes are in hand, the file becomes a
            # different proposal under the same id and filename.
            # A fully canonical replacement: module, sub AND dst all agree,
            # so it would validate cleanly. Nothing but a value/digest
            # disagreement can make this test fail.
            replacement_holder["bytes"] = _rewrite_record(
                prop.path,
                module="11-library",
                sub="reference",
                dst="demo/11-library/active/note.md",
            )
        return state

    import app.outbox as outbox_module

    original_attr = outbox_module.capture_path_state
    outbox_module.capture_path_state = capture_then_replace
    try:
        review = get_proposal_review(scope, prop.id)
    finally:
        outbox_module.capture_path_state = original_attr

    assert replacement_holder["bytes"] != original, "the probe did not replace anything"

    # Every part of the review describes the captured bytes, not the file.
    assert review.contents == original
    assert review.sha256 == hashlib.sha256(original).hexdigest()
    assert review.value.module == prop.module
    assert review.value.sub == prop.sub
    assert review.value.dst == prop.dst
    # And the value is genuinely the one those bytes describe.
    assert review.value == _to_proposal_from_bytes(prop.path, original)


def _to_proposal_from_bytes(path: Path, raw: bytes) -> Proposal:
    return outbox._to_proposal(path, yaml.safe_load(raw.decode("utf-8")))


def test_a_replacement_after_the_review_does_not_change_the_review(tmp_path):
    from app.review_tokens import ReviewedProposalChanged, require_review_match

    get_proposal_review = _review_imports()
    scope, prop = _propose(_vault(tmp_path))

    review = get_proposal_review(scope, prop.id)
    replacement = _rewrite_record(prop.path, module="11-library", sub="reference")

    assert review.contents != replacement
    assert review.sha256 == hashlib.sha256(review.contents).hexdigest()
    # The fingerprint the operator holds no longer matches the file: this is
    # exactly what the action boundary in Task 3 will compare.
    with pytest.raises(ReviewedProposalChanged):
        require_review_match(replacement, review.sha256)


def test_the_same_id_with_only_byte_differences_reviews_differently(tmp_path):
    get_proposal_review = _review_imports()
    scope, prop = _propose(_vault(tmp_path))

    first = get_proposal_review(scope, prop.id)

    # Same meaningful fields, different stored bytes: re-serialised sorted.
    record = yaml.safe_load(first.contents.decode("utf-8"))
    prop.path.write_bytes(yaml.safe_dump(record, sort_keys=True).encode("utf-8"))
    second = get_proposal_review(scope, prop.id)

    assert second.contents != first.contents
    assert second.sha256 != first.sha256
    assert second.value == first.value        # the values are indistinguishable


@pytest.mark.parametrize(
    "bad_id",
    [
        "../../../etc/passwd",
        "..",
        "nested/20260102T030405-" + "ab" * 16,
        "20260102T030405-" + "ab" * 16 + "/../other",
        "not-a-proposal-id",
        "",
        "20260102T030405-" + "AB" * 16,        # uppercase is non-canonical
    ],
)
def test_review_refuses_a_non_canonical_proposal_id(tmp_path, bad_id):
    get_proposal_review = _review_imports()
    scope, _prop = _propose(_vault(tmp_path))
    before = _vault_tree(scope.root)

    from app.console_errors import describe

    # P2 (review): assert the promised family and outcome, not merely that
    # *something* was raised — a TypeError or an unrelated scope error would
    # otherwise pass as if the contract held.
    with pytest.raises(outbox.OutboxError) as raised:
        get_proposal_review(scope, bad_id)
    assert describe(raised.value).code == "E-INVALID"

    assert _vault_tree(scope.root) == before


def test_review_refuses_a_missing_proposal(tmp_path):
    get_proposal_review = _review_imports()
    scope, prop = _propose(_vault(tmp_path))
    prop.path.unlink()

    with pytest.raises(outbox.OutboxError):
        get_proposal_review(scope, prop.id)


def test_review_refuses_an_unreadable_record(tmp_path):
    get_proposal_review = _review_imports()
    scope, prop = _propose(_vault(tmp_path))
    prop.path.write_text("{ not: [valid, yaml", encoding="utf-8")

    with pytest.raises(outbox.OutboxError):
        get_proposal_review(scope, prop.id)


def test_review_refuses_a_redirected_proposal_leaf(tmp_path):
    get_proposal_review = _review_imports()
    scope, prop, _target = _redirect_proposal_leaf(_vault(tmp_path))

    with pytest.raises(CrossScopeError):
        get_proposal_review(scope, prop.id)


def test_review_refuses_a_cross_scope_proposal(two_entity_vault):
    """An alpha-entity record sitting in beta's outbox: the filename and id
    are canonical, so only the scope check refuses it."""
    get_proposal_review = _review_imports()
    vault = two_entity_vault
    alpha_scope = Scope(vault, "alpha")
    beta_scope = Scope(vault, "beta")
    record = _canonical_alpha_record(alpha_scope)
    path = _write_record(
        beta_scope, f"{record['id']}.yaml", yaml.safe_dump(record, sort_keys=False)
    )
    assert path.exists()

    with pytest.raises(outbox.OutboxScopeError):
        get_proposal_review(beta_scope, record["id"])

    # And the misfiled record is left exactly where it was for diagnosis.
    assert path.read_bytes() == yaml.safe_dump(record, sort_keys=False).encode("utf-8")


def test_get_proposal_delegates_to_the_review_reader(tmp_path):
    """`get_proposal` survives only for non-action callers, and must be the
    review reader's value — never a second, independently parsed read."""
    get_proposal_review = _review_imports()
    scope, prop = _propose(_vault(tmp_path))

    assert outbox.get_proposal(scope, prop.id) == get_proposal_review(scope, prop.id).value

    source = inspect.getsource(outbox.get_proposal)
    assert "get_proposal_review" in source
    assert "load_proposals" not in source.split("get_proposal_review")[-1]


# --- S7 Task 2 review findings: the strict scan ------------------------------


def _two_note_review_vault(tmp_path):
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
        "demo/00-inbox/active/other.md": textwrap.dedent(
            """\
            ---
            type: inbox-item
            title: Second protocol
            entity: demo
            product: null
            status: active
            created: 2026-01-01
            updated: 2026-01-01
            sub: triage
            source: folder
            ---
            Second protocol body.
            """
        ),
        "demo/11-knowledge/active/.gitkeep": "",
        "demo/11-library/active/.gitkeep": "",
    }
    vault = _outbox_vault(tmp_path, ("demo",), files)
    scope = Scope(vault, "demo")
    target = propose_classification(
        scope, scope.resolve("00-inbox", "active", "note.md"),
        module="11-knowledge", sub="kb", claimed_block="govern",
    )
    sibling = propose_classification(
        scope, scope.resolve("00-inbox", "active", "other.md"),
        module="11-knowledge", sub="kb", claimed_block="govern",
    )
    return vault, scope, target, sibling


def test_review_refuses_when_a_sibling_destination_is_non_canonical(tmp_path):
    """P1 (review): the strict loader's refusal is listing-wide, and it is
    not only about unreadable records.

    A well-formed sibling whose destination no longer canonicalises makes
    `load_proposals` refuse everything in the entity. A review reader that
    checked only phase-1 readability would hand out a fingerprint — and
    therefore an action — that the strict loader would refuse.
    """
    _vault_root, scope, target, sibling = _two_note_review_vault(tmp_path)

    record = yaml.safe_load(sibling.path.read_text(encoding="utf-8"))
    record["dst"] = "demo/11-library/active/other.md"   # module says knowledge
    sibling.path.write_text(yaml.safe_dump(record, sort_keys=False), encoding="utf-8")

    # The strict loader refuses ...
    with pytest.raises(outbox.OutboxDestinationError):
        load_proposals(scope)
    # ... so the review of a perfectly valid target must refuse identically.
    with pytest.raises(outbox.OutboxDestinationError):
        outbox.get_proposal_review(scope, target.id)


def test_the_strict_scan_never_follows_a_leaf_swapped_after_its_lexical_check(
    tmp_path,
):
    """P1 (review): the lexical `is_symlink()` check and the record read are
    two separate operations. Between them the leaf can become a symlink to a
    file outside the outbox; a following read would parse that outside file
    and, because the symlink keeps the filename, its identity check would
    pass. The read itself must be no-follow."""
    _vault_root, scope, target, _sibling = _two_note_review_vault(tmp_path)
    vault = scope.root

    # An outside file that is a perfectly valid proposal under the SAME id,
    # naming a different destination. If it is ever read, it is returned.
    outside = tmp_path / "outside-proposal.yaml"
    planted = yaml.safe_load(target.path.read_text(encoding="utf-8"))
    planted["module"] = "11-library"
    planted["sub"] = "reference"
    planted["dst"] = "demo/11-library/active/note.md"
    outside.write_text(yaml.safe_dump(planted, sort_keys=False), encoding="utf-8")

    original_bytes = target.path.read_bytes()

    # Record what each read actually resolves to. Watching the *argument*
    # path is not enough: a following read is called on the leaf and only
    # its realpath reveals that the outside file was consumed.
    import os as _os

    reads: list[str] = []
    real_read_bytes = Path.read_bytes
    real_read_text = Path.read_text

    def spy_read_bytes(self):
        reads.append(_os.path.realpath(self))
        return real_read_bytes(self)

    def spy_read_text(self, *args, **kwargs):
        reads.append(_os.path.realpath(self))
        return real_read_text(self, *args, **kwargs)

    real_require = outbox._require_outbox_path
    swapped = []

    def swap_after_check(scope_arg, proposal_path=None, **kwargs):
        result = real_require(scope_arg, proposal_path, **kwargs)
        if (
            proposal_path is not None
            and Path(proposal_path).name == f"{target.id}.yaml"
            and not swapped
        ):
            swapped.append(True)
            Path(proposal_path).unlink()
            Path(proposal_path).symlink_to(outside)
        return result

    outbox._require_outbox_path = swap_after_check
    Path.read_bytes = spy_read_bytes
    Path.read_text = spy_read_text
    try:
        with pytest.raises(CrossScopeError):
            outbox.get_proposal_review(scope, target.id)
    finally:
        Path.read_bytes = real_read_bytes
        Path.read_text = real_read_text
        outbox._require_outbox_path = real_require

    assert swapped, "the probe never swapped the leaf"
    # The decisive assertion: no read anywhere in the scan resolved to the
    # planted outside file.
    assert _os.path.realpath(outside) not in reads, (
        f"the redirected outside file was read: {reads}"
    )
    # It is also untouched, and its distinctive destination never became a
    # review.
    assert outside.read_text(encoding="utf-8") == yaml.safe_dump(
        planted, sort_keys=False
    )

    # Restoring the real leaf reviews the real record, proving the refusal
    # above was about the redirection and not about the id.
    Path(target.path).unlink()
    Path(target.path).write_bytes(original_bytes)
    assert outbox.get_proposal_review(scope, target.id).value.module == "11-knowledge"


# --- S7 Task 3: approve and reject bound to the reviewed bytes ---------------


def _review_of(scope: Scope, prop: Proposal):
    return outbox.get_proposal_review(scope, prop.id)


def _rewrite_same_id_meaningfully(prop: Proposal) -> bytes:
    """A canonical replacement under the same id and filename."""
    return _rewrite_record(
        prop.path,
        module="11-library",
        sub="reference",
        dst="demo/11-library/active/note.md",
    )


def _rewrite_same_id_byte_only(prop: Proposal) -> bytes:
    """Identical action-relevant values, different stored bytes."""
    record = yaml.safe_load(prop.path.read_text(encoding="utf-8"))
    raw = yaml.safe_dump(record, sort_keys=True).encode("utf-8")
    prop.path.write_bytes(raw)
    return raw


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_actions_require_a_review_fingerprint_with_no_default(action):
    signature = inspect.signature(getattr(outbox, action))
    assert list(signature.parameters) == ["scope", "proposal_id", "review_sha256"]
    fingerprint = signature.parameters["review_sha256"]
    assert fingerprint.default is inspect.Parameter.empty
    assert fingerprint.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_actions_have_no_id_only_compatibility_path(tmp_path, action):
    scope, prop = _propose(_vault(tmp_path))
    before = _approval_state(scope.root)

    with pytest.raises(TypeError):
        getattr(outbox, action)(scope, prop.id)

    assert _approval_state(scope.root) == before


@pytest.mark.parametrize("action", ["approve", "reject"])
@pytest.mark.parametrize(
    "fingerprint", [None, "", "0" * 63, "0" * 65, "G" * 64, "A" * 64, 123, b"0" * 64]
)
def test_actions_refuse_a_malformed_fingerprint_without_mutation(
    tmp_path, action, fingerprint
):
    from app.review_tokens import InvalidReviewToken

    scope, prop = _propose(_vault(tmp_path))
    before = _approval_state(scope.root)

    with pytest.raises(InvalidReviewToken):
        getattr(outbox, action)(scope, prop.id, fingerprint)

    assert _approval_state(scope.root) == before
    assert prop.path.exists()


def test_approve_still_moves_and_commits_when_the_review_matches(tmp_path):
    vault = _vault(tmp_path)
    scope, prop = _propose(vault)
    commits_before = git_count_commits(vault)

    approved = approve(scope, prop.id, _review_of(scope, prop).sha256)

    assert approved.id == prop.id
    assert git_count_commits(vault) == commits_before + 1
    assert not (vault / prop.src).exists()
    assert (vault / prop.dst).exists()
    assert not prop.path.exists()


def test_reject_still_discards_when_the_review_matches(tmp_path):
    vault = _vault(tmp_path)
    scope, prop = _propose(vault)
    commits_before = git_count_commits(vault)

    rejected = reject(scope, prop.id, _review_of(scope, prop).sha256)

    assert rejected.id == prop.id
    assert not prop.path.exists()
    assert git_count_commits(vault) == commits_before
    assert (vault / prop.src).exists()


@pytest.mark.parametrize("action", ["approve", "reject"])
@pytest.mark.parametrize(
    ("label", "rewrite"),
    [
        ("meaningful", _rewrite_same_id_meaningfully),
        ("byte-only", _rewrite_same_id_byte_only),
    ],
)
def test_a_same_id_replacement_refuses_and_changes_nothing(
    tmp_path, action, label, rewrite
):
    from app.review_tokens import ReviewedProposalChanged

    vault = _vault(tmp_path)
    scope, prop = _propose(vault)
    review = _review_of(scope, prop)

    replacement = rewrite(prop)
    assert replacement != review.contents, label
    before = _approval_state(vault)

    with pytest.raises(ReviewedProposalChanged):
        getattr(outbox, action)(scope, prop.id, review.sha256)

    # Complete non-mutation, and the replacement is preserved for diagnosis.
    assert _approval_state(vault) == before
    assert prop.path.read_bytes() == replacement


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_a_replacement_between_the_comparison_and_the_mutation_refuses(
    tmp_path, action
):
    """The final pre-mutation boundary.

    The fingerprint already matched. A replacement landing *after* that
    comparison must still be refused, because the state the action owns is
    the state it compared — not whatever is on disk when it reaches the
    mutation.
    """
    from app.console_errors import describe

    vault = _vault(tmp_path)
    scope, prop = _propose(vault)
    review = _review_of(scope, prop)
    before = _approval_state(vault)

    mutating_call = "execute_transaction" if action == "approve" else "remove_path_if_unchanged"
    real = getattr(outbox, mutating_call)
    replaced = []

    def replace_then_run(*args, **kwargs):
        if not replaced:
            replaced.append(_rewrite_same_id_byte_only(prop))
        return real(*args, **kwargs)

    setattr(outbox, mutating_call, replace_then_run)
    try:
        # approve wraps the transaction's refusal in its own domain type;
        # reject raises the conflict directly. The operator outcome is the
        # contract, so assert that rather than either wrapper.
        with pytest.raises(Exception) as raised:
            getattr(outbox, action)(scope, prop.id, review.sha256)
    finally:
        setattr(outbox, mutating_call, real)

    assert describe(raised.value).code == "E-CONFLICT"
    assert replaced, "the probe never replaced the record"

    # Everything the action could have changed is unchanged. The vault tree
    # is compared minus the proposal leaf, because the probe itself rewrote
    # that one file — asserting it equal would assert the probe never ran.
    after = _approval_state(vault)
    for key in ("head", "status", "index", "worktree"):
        assert after[key] == before[key], key
    proposal_rel = prop.path.relative_to(vault).as_posix()
    # `.git/oneos-approval.lock` is the transaction's own lock file, created
    # by taking the lock and not vault content.
    ignored = {proposal_rel, ".git/oneos-approval.lock"}

    def _comparable(tree):
        return tuple(entry for entry in tree if entry[0] not in ignored)

    assert _comparable(after["tree"]) == _comparable(before["tree"])
    assert (vault / prop.src).exists()
    assert not (vault / prop.dst).exists()
    # Reject must not unlink the replacement it never reviewed.
    assert prop.path.exists()
    assert prop.path.read_bytes() == replaced[0]


@pytest.mark.parametrize("action", ["approve", "reject"])
@pytest.mark.parametrize("substitution", ["absent", "symlink", "directory"])
def test_a_non_regular_substitution_before_mutation_refuses_without_blocking(
    tmp_path, action, substitution
):
    from app.console_errors import describe

    vault = _vault(tmp_path)
    scope, prop = _propose(vault)
    review = _review_of(scope, prop)
    outside = tmp_path / "elsewhere.yaml"
    outside.write_bytes(review.contents)

    mutating_call = "execute_transaction" if action == "approve" else "remove_path_if_unchanged"
    real = getattr(outbox, mutating_call)
    swapped = []

    def substitute_then_run(*args, **kwargs):
        if not swapped:
            swapped.append(substitution)
            prop.path.unlink()
            if substitution == "symlink":
                prop.path.symlink_to(outside)
            elif substitution == "directory":
                prop.path.mkdir()
        return real(*args, **kwargs)

    setattr(outbox, mutating_call, substitute_then_run)
    try:
        with pytest.raises(Exception) as raised:
            getattr(outbox, action)(scope, prop.id, review.sha256)
    finally:
        setattr(outbox, mutating_call, real)

    # A changed regular file is a conflict; a type swap is a tamper finding.
    # Both refuse, neither blocks, and neither is E-UNKNOWN.
    assert describe(raised.value).code in {"E-CONFLICT", "E-TAMPER"}
    assert swapped, "the probe never substituted anything"
    # Whatever was substituted is still there — never followed, never removed.
    assert outside.exists() and outside.read_bytes() == review.contents
    if substitution == "symlink":
        assert prop.path.is_symlink()
    elif substitution == "directory":
        assert prop.path.is_dir()
    else:
        assert not prop.path.exists()


def test_no_action_reads_the_proposal_through_the_value_only_reader():
    """`get_proposal` survives for non-action callers only. An action that
    used it would be acting on a value with no fingerprint behind it."""
    for action in (outbox.approve, outbox.reject):
        source = inspect.getsource(action)
        assert "get_proposal(" not in source, action.__name__


def test_approve_gives_the_transaction_the_state_it_compared(tmp_path):
    """The compared bytes and the transaction-owned bytes are one object.

    A reread between the comparison and the plan would reopen exactly the
    window S7 exists to close, so the owned change must carry the same
    `PathState` the fingerprint was checked against.
    """
    vault = _vault(tmp_path)
    scope, prop = _propose(vault)
    review = _review_of(scope, prop)
    proposal_rel = prop.path.relative_to(vault).as_posix()

    seen = {}
    real = outbox.execute_transaction

    def record_plan(vault_arg, plan):
        seen["owned"] = plan.owned_changes
        return real(vault_arg, plan)

    outbox.execute_transaction = record_plan
    try:
        approve(scope, prop.id, review.sha256)
    finally:
        outbox.execute_transaction = real

    owned = {change.path: change for change in seen["owned"]}
    assert proposal_rel in owned
    assert owned[proposal_rel].before.contents == review.contents
    assert owned[proposal_rel].after.contents is None


def test_approve_owns_the_reviewed_state_not_whatever_arrives_later(tmp_path):
    """The reread that a contents comparison alone cannot catch.

    `test_approve_gives_the_transaction_the_state_it_compared` compares the
    owned bytes to the reviewed bytes — but when nothing changes in between,
    a fresh capture yields byte-identical contents and the substitution is
    invisible. So change the record *after* the fingerprint matched and
    *before* the plan is built: the reviewed state no longer describes the
    file, and only an implementation that kept the compared state refuses.

    An implementation that recaptured here would take the replacement as
    authority, `_apply_state` would find it unchanged, and approve would
    commit a move for a proposal nobody ever reviewed — while unlinking the
    replacement as though it had been.
    """
    from app.console_errors import describe

    vault = _vault(tmp_path)
    scope, prop = _propose(vault)
    review = _review_of(scope, prop)
    commits_before = git_count_commits(vault)
    before = _approval_state(vault)

    real_read = outbox._read_no_follow_bytes
    replaced = []

    def rewrite_proposal_then_read(path):
        # Fires on approve's source-receipt read, which sits between the
        # fingerprint comparison and the transaction plan.
        if not replaced:
            replaced.append(_rewrite_same_id_byte_only(prop))
        return real_read(path)

    outbox._read_no_follow_bytes = rewrite_proposal_then_read
    try:
        with pytest.raises(Exception) as raised:
            approve(scope, prop.id, review.sha256)
    finally:
        outbox._read_no_follow_bytes = real_read

    assert replaced, "the probe never replaced the record"
    assert replaced[0] != review.contents
    assert describe(raised.value).code == "E-CONFLICT"

    # Nothing committed, nothing moved, and the replacement is still there.
    assert git_count_commits(vault) == commits_before
    assert _approval_state(vault)["head"] == before["head"]
    assert (vault / prop.src).exists()
    assert not (vault / prop.dst).exists()
    assert prop.path.read_bytes() == replaced[0]
