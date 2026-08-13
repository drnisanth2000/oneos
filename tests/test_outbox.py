"""Outbox write path (spec §10 steps 7–8, invariant 1).

Step 7: confirming a classification writes a PROPOSAL into <entity>/outbox/ and
renders a diff — no file is moved. Step 8: approval performs the move and
commits (exactly one commit, git-revertible); reject discards the proposal.

Temp git vaults only; the real vault is never touched.
"""
import inspect
import textwrap
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import app.outbox as outbox
from app.scope import CrossScopeError, Scope
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
    }
    return git_entity_vault(tmp_path, ("demo",), files)


def _propose(vault):
    scope = Scope(vault, "demo")
    src = scope.resolve("00-inbox", "active", "note.md")
    return scope, propose_classification(
        scope, src, module="11-knowledge", sub="kb", block="govern",
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
    }
    return git_entity_vault(tmp_path, ("alpha", "beta"), files)


FORGED_BETA_PROPOSAL = textwrap.dedent(
    """\
    id: shared-id
    action: classify
    entity: beta
    src: beta/00-inbox/active/beta.md
    dst: beta/11-knowledge/active/beta.md
    module: 11-knowledge
    sub: kb
    block: govern
    status: pending
    """
)


def _write_record(scope: Scope, name: str, record: str) -> Path:
    path = scope.resolve("outbox", name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(record, encoding="utf-8")
    return path


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


def test_preview_diff_shows_move_and_sub_change(tmp_path):
    vault = _vault(tmp_path)
    scope, prop = _propose(vault)
    diff = preview_diff(scope, prop)
    assert "00-inbox/active/note.md" in diff
    assert "11-knowledge/active/note.md" in diff
    assert "-sub: triage" in diff and "+sub: kb" in diff


def test_load_proposals(tmp_path):
    vault = _vault(tmp_path)
    scope, prop = _propose(vault)
    props = load_proposals(scope)
    assert [p.id for p in props] == [prop.id]


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


def test_real_adapter_receipt_approval_is_one_later_revertible_commit(tmp_path):
    import subprocess

    from app.ingest.adapters.folder import process_drop

    vault = git_entity_vault(tmp_path / "vault", ("synthetic",), {
        "synthetic/00-inbox/active/.gitkeep": "",
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
        module="11-library", sub="reference", block="govern",
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


def test_proposal_discovery_rejects_cross_scope_leaf_symlink(tmp_path):
    record = textwrap.dedent(
        """\
        id: hidden
        action: classify
        entity: beta
        src: beta/00-inbox/active/note.md
        dst: beta/11-knowledge/active/note.md
        module: 11-knowledge
        sub: kb
        """
    )
    vault = git_entity_vault(
        tmp_path,
        ("alpha", "beta"),
        {
            "alpha/outbox/.gitkeep": "",
            "beta/outbox/hidden.yaml": record,
        },
    )
    (vault / "alpha/outbox/linked.yaml").symlink_to(vault / "beta/outbox/hidden.yaml")

    with pytest.raises(CrossScopeError):
        load_proposals(Scope(vault, "alpha"))


def test_loading_mismatched_proposal_fails_before_other_entity_source_read(
    two_entity_vault, monkeypatch
):
    scope = Scope(two_entity_vault, "alpha")
    _write_record(scope, "forged.yaml", FORGED_BETA_PROPOSAL)
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
    with pytest.raises(CrossScopeError):
        propose_classification(
            alpha,
            beta_item,
            module="11-knowledge",
            sub="kb",
            block="govern",
        )
    assert not alpha.resolve("outbox").exists()


def test_preview_diff_rejects_proposal_bound_to_another_entity(two_entity_vault):
    alpha = Scope(two_entity_vault, "alpha")
    record_path = _write_record(alpha, "forged.yaml", FORGED_BETA_PROPOSAL)
    proposal = Proposal(
        id="shared-id",
        path=record_path,
        action="classify",
        entity="beta",
        src="beta/00-inbox/active/beta.md",
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
    alpha_record = _write_record(alpha, "shared-id.yaml", FORGED_BETA_PROPOSAL)
    beta_record = _write_record(beta, "shared-id.yaml", FORGED_BETA_PROPOSAL)
    watched = (
        alpha_record,
        beta_record,
        two_entity_vault / "alpha/00-inbox/active/alpha.md",
        two_entity_vault / "beta/00-inbox/active/beta.md",
    )
    before = {path: path.read_bytes() for path in watched}

    with pytest.raises(outbox.OutboxScopeError):
        operation(alpha, "shared-id")

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
def test_loading_rejects_foreign_stored_path(two_entity_vault, field, foreign_value):
    alpha = Scope(two_entity_vault, "alpha")
    record = {
        "id": "forged-path",
        "action": "classify",
        "entity": "alpha",
        "src": "alpha/00-inbox/active/alpha.md",
        "dst": "alpha/11-knowledge/active/alpha.md",
        "module": "11-knowledge",
        "sub": "kb",
        "block": "govern",
    }
    record[field] = foreign_value
    _write_record(alpha, "forged-path.yaml", __import__("yaml").safe_dump(record))

    with pytest.raises(CrossScopeError):
        load_proposals(alpha)


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
            block="govern",
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
