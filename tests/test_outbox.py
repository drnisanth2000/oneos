"""Outbox write path (spec §10 steps 7–8, invariant 1).

Step 7: confirming a classification writes a PROPOSAL into <entity>/outbox/ and
renders a diff — no file is moved. Step 8: approval performs the move and
commits (exactly one commit, git-revertible); reject discards the proposal.

Temp git vaults only; the real vault is never touched.
"""
import textwrap

from app.scope import Scope
from app.outbox import (
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
    git_vault,
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
    return git_vault(tmp_path, files)


def _propose(vault):
    scope = Scope(vault)
    src = scope.resolve("demo", "00-inbox", "active", "note.md")
    return scope, propose_classification(
        scope, "demo", src, module="11-knowledge", sub="kb", block="govern",
        rule_id="research",
    )


def test_propose_writes_proposal_and_moves_nothing(tmp_path):
    vault = _vault(tmp_path)
    scope, prop = _propose(vault)

    # proposal lives in the outbox
    assert prop.path.exists()
    assert prop.path.parent == scope.resolve("demo", "outbox")
    # the inbox item has NOT moved
    assert scope.resolve("demo", "00-inbox", "active", "note.md").exists()
    assert not scope.resolve("demo", "11-knowledge", "active", "note.md").exists()

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
    props = load_proposals(scope, "demo")
    assert [p.id for p in props] == [prop.id]


def test_approve_moves_file_and_makes_one_commit(tmp_path):
    vault = _vault(tmp_path)
    scope, prop = _propose(vault)
    before = git_count_commits(vault)

    approve(scope, "demo", prop.id)

    assert not scope.resolve("demo", "00-inbox", "active", "note.md").exists()
    dst = scope.resolve("demo", "11-knowledge", "active", "note.md")
    assert dst.exists()
    assert "sub: kb" in dst.read_text()
    assert not prop.path.exists()

    assert git_count_commits(vault) == before + 1
    assert git_head_message(vault).startswith("outbox: approve")
    assert git_is_clean(vault)


def test_real_adapter_receipt_approval_is_one_later_revertible_commit(tmp_path):
    import subprocess

    from app.ingest.adapters.folder import process_drop

    vault = git_vault(tmp_path / "vault", {
        "synthetic/00-inbox/active/.gitkeep": "",
        "synthetic/11-library/active/.gitkeep": "",
    })
    source = tmp_path / "dropbox/research.txt"
    source.parent.mkdir()
    source.write_text("Synthetic research summary.\n", encoding="utf-8")
    result = process_drop(vault, "synthetic", source, raw_archive=tmp_path / "raw")
    ingest_oid = git_head(vault)
    triage_rel = result.path.relative_to(vault).as_posix()

    assert git_head_message(vault) == "ingest: add redacted receipt"
    assert git_changed_paths(vault) == [triage_rel]
    assert triage_rel in git_tracked_paths(vault)

    scope = Scope(vault)
    prop = propose_classification(
        scope, "synthetic", result.path,
        module="11-library", sub="reference", block="govern",
        rule_id="synthetic-rule",
    )
    approve(scope, "synthetic", prop.id)
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
    reject(scope, "demo", prop.id)
    assert not prop.path.exists()
    assert scope.resolve("demo", "00-inbox", "active", "note.md").exists()
    assert load_proposals(scope, "demo") == []
