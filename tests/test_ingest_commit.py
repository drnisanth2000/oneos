from pathlib import Path
import subprocess

import pytest

import app.ingest.base as ingest_base
from app.ingest.base import (
    IngestCommitError,
    IngestIdentityConflict,
    IngestPathCollision,
    IngestRepositoryError,
    IngestResult,
    commit_inbox_item,
    prepare_inbox_item,
)
from app.scope import Scope
from tests.conftest import (
    git_changed_paths,
    git_count_commits,
    git_head,
    git_head_message,
    git_history_contains,
    git_index_paths,
    git_is_clean,
    git_tracked_paths,
    git_vault,
)


def _vault(tmp_path: Path) -> Path:
    return git_vault(tmp_path, {"synthetic/00-inbox/active/.gitkeep": ""})


def _kwargs() -> dict:
    return {
        "text": "Planning note with PAN ABCDE1234F.",
        "title": "Planning note",
        "source": "folder",
        "source_id": "0123456789abcdef",
        "received_at": "2026-08-12T10:00:00",
        "source_ref": "raw:0123456789abcdef-note.txt",
        "body_ref": "raw:0123456789abcdef-note.txt",
        "sha256": "0123456789abcdef" * 4,
        "mime": "text/plain",
        "size": 37,
        "slug_seed": "0123456789abcdef",
    }


def test_prepare_returns_redacted_schema_ready_receipt_without_writing(tmp_path):
    vault = _vault(tmp_path)
    path, env, rendered = prepare_inbox_item(Scope(vault), "synthetic", **_kwargs())

    assert path == vault / "synthetic/00-inbox/active/planning-note-01234567.md"
    assert env.sha256 == "0123456789abcdef" * 4
    assert "[PAN]" in rendered
    assert "ABCDE1234F" not in rendered
    assert not path.exists()


def test_prepare_rejects_adapter_receipt_without_source_hash(tmp_path):
    vault = _vault(tmp_path)
    kwargs = {**_kwargs(), "sha256": None}
    with pytest.raises(IngestRepositoryError, match="requires sha256"):
        prepare_inbox_item(Scope(vault), "synthetic", **kwargs)


def test_new_intake_creates_one_receipt_only_ingest_commit(tmp_path):
    vault = _vault(tmp_path)
    before = git_count_commits(vault)
    result = commit_inbox_item(Scope(vault), "synthetic", **_kwargs())
    rel = "synthetic/00-inbox/active/planning-note-01234567.md"
    assert result.created is True
    assert result.path == vault / rel
    assert result.commit_oid
    assert git_count_commits(vault) == before + 1
    assert git_head_message(vault) == "ingest: add redacted receipt"
    assert git_changed_paths(vault) == [rel]
    assert rel in git_tracked_paths(vault)
    assert not git_history_contains(vault, "ABCDE1234F")
    assert git_is_clean(vault)


def test_non_git_vault_fails_without_creating_receipt(tmp_path):
    vault = tmp_path / "not-git"
    vault.mkdir()
    with pytest.raises(IngestRepositoryError):
        commit_inbox_item(Scope(vault), "synthetic", **_kwargs())
    assert not list(vault.rglob("*.md"))


def test_git_repository_without_head_fails_without_creating_receipt(tmp_path):
    vault = tmp_path / "no-head"
    vault.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=vault, check=True)
    with pytest.raises(IngestRepositoryError):
        commit_inbox_item(Scope(vault), "synthetic", **_kwargs())
    assert not list(vault.rglob("*.md"))


def test_rejecting_hook_removes_only_attempted_receipt_and_new_directories(tmp_path):
    vault = git_vault(tmp_path, {"unrelated.txt": "base\n"})
    staged = vault / "staged.txt"
    staged.write_text("keep staged\n", encoding="utf-8")
    subprocess.run(["git", "add", "staged.txt"], cwd=vault, check=True)
    unstaged = vault / "unrelated.txt"
    unstaged.write_text("keep unstaged\n", encoding="utf-8")
    head_before = git_head(vault)
    hook = vault / ".git/hooks/pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    with pytest.raises(IngestCommitError):
        commit_inbox_item(Scope(vault), "synthetic", **_kwargs())
    assert git_head(vault) == head_before
    assert git_index_paths(vault) == ["staged.txt"]
    assert unstaged.read_text(encoding="utf-8") == "keep unstaged\n"
    assert not (vault / "synthetic").exists()


def test_duplicate_tracked_identity_is_a_no_op_after_move(tmp_path):
    vault = _vault(tmp_path)
    scope = Scope(vault)
    first = commit_inbox_item(scope, "synthetic", **_kwargs())
    moved = vault / "synthetic/11-library/active/planning-note-01234567.md"
    moved.parent.mkdir(parents=True)
    first.path.rename(moved)
    subprocess.run(["git", "add", "-A"], cwd=vault, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "fixture: move receipt"], cwd=vault, check=True)
    before = git_head(vault)
    duplicate = commit_inbox_item(scope, "synthetic", **_kwargs())
    assert duplicate.path == moved
    assert duplicate.created is False
    assert duplicate.commit_oid is None
    assert git_head(vault) == before
    assert git_is_clean(vault)


def test_same_source_identity_with_different_hash_is_rejected(tmp_path):
    vault = _vault(tmp_path)
    scope = Scope(vault)
    commit_inbox_item(scope, "synthetic", **_kwargs())
    changed = {**_kwargs(), "sha256": "fedcba9876543210" * 4}
    with pytest.raises(IngestIdentityConflict):
        commit_inbox_item(scope, "synthetic", **changed)


def test_occupied_destination_is_not_overwritten(tmp_path):
    vault = _vault(tmp_path)
    scope = Scope(vault)
    path, _env, _rendered = prepare_inbox_item(scope, "synthetic", **_kwargs())
    path.write_text("unrelated existing bytes\n", encoding="utf-8")
    with pytest.raises(IngestPathCollision):
        commit_inbox_item(scope, "synthetic", **_kwargs())
    assert path.read_text(encoding="utf-8") == "unrelated existing bytes\n"


def test_cleanup_never_deletes_receipt_bytes_changed_by_another_actor(tmp_path, monkeypatch):
    vault = _vault(tmp_path)
    scope = Scope(vault)
    path, _env, _rendered = prepare_inbox_item(scope, "synthetic", **_kwargs())
    real_git = ingest_base._git

    def failing_git(local_scope, *args, check=True):
        if args and args[0] == "commit":
            path.write_text("concurrent bytes\n", encoding="utf-8")
            raise subprocess.CalledProcessError(1, ["git", *args])
        return real_git(local_scope, *args, check=check)

    monkeypatch.setattr(ingest_base, "_git", failing_git)
    with pytest.raises(IngestCommitError, match="cleanup failed"):
        commit_inbox_item(scope, "synthetic", **_kwargs())
    assert path.read_text(encoding="utf-8") == "concurrent bytes\n"
