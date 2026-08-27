import os
from pathlib import Path
import subprocess

import pytest

from app.cutover_build import CutoverError, isolated_worktree
from tests.conftest import git_head, git_is_clean, git_vault


def commit_in(root: Path, message: str) -> str:
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=t@e", "-c", "user.name=t",
         "commit", "-q", "-m", message],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return git_head(root)


def test_isolated_worktree_starts_at_the_requested_head(tmp_path: Path):
    vault = git_vault(tmp_path / "vault", {"a.md": "x\n"})
    head = git_head(vault)

    with isolated_worktree(vault, head) as scratch:
        assert git_head(scratch) == head
        assert scratch != vault


def test_isolated_worktree_is_detached_and_creates_no_branch(tmp_path: Path):
    vault = git_vault(tmp_path / "vault", {"a.md": "x\n"})
    before = subprocess.run(
        ["git", "branch", "--format=%(refname)"],
        cwd=vault, check=True, capture_output=True, text=True,
    ).stdout

    with isolated_worktree(vault, git_head(vault)) as scratch:
        symbolic = subprocess.run(
            ["git", "symbolic-ref", "-q", "HEAD"],
            cwd=scratch, check=False, capture_output=True, text=True,
        )
        assert symbolic.returncode != 0  # detached HEAD

    after = subprocess.run(
        ["git", "branch", "--format=%(refname)"],
        cwd=vault, check=True, capture_output=True, text=True,
    ).stdout
    assert before == after


def test_a_pre_existing_branch_is_never_deleted(tmp_path: Path):
    vault = git_vault(tmp_path / "vault", {"a.md": "x\n"})
    head = git_head(vault)
    subprocess.run(
        ["git", "branch", f"cutover/build-{head[:12]}"],
        cwd=vault, check=True, capture_output=True,
    )

    with isolated_worktree(vault, head):
        pass

    remaining = subprocess.run(
        ["git", "branch", "--format=%(refname:short)"],
        cwd=vault, check=True, capture_output=True, text=True,
    ).stdout
    assert f"cutover/build-{head[:12]}" in remaining


def test_isolated_worktree_writes_do_not_reach_the_live_vault(tmp_path: Path):
    vault = git_vault(tmp_path / "vault", {"a.md": "x\n"})
    head = git_head(vault)

    with isolated_worktree(vault, head) as scratch:
        (scratch / "a.md").write_text("changed\n", encoding="utf-8")

    assert (vault / "a.md").read_text(encoding="utf-8") == "x\n"
    assert git_is_clean(vault)
    assert git_head(vault) == head


def test_isolated_worktree_is_removed_on_success_and_failure(tmp_path: Path):
    vault = git_vault(tmp_path / "vault", {"a.md": "x\n"})
    recorded: list[Path] = []

    with isolated_worktree(vault, git_head(vault)) as scratch:
        recorded.append(scratch)
    assert not recorded[0].exists()

    with pytest.raises(RuntimeError):
        with isolated_worktree(vault, git_head(vault)) as scratch:
            recorded.append(scratch)
            raise RuntimeError("injected")
    assert not recorded[1].exists()
    assert git_is_clean(vault)


def test_a_commit_built_in_isolation_is_visible_from_the_vault(tmp_path: Path):
    vault = git_vault(tmp_path / "vault", {"a.md": "x\n"})
    head = git_head(vault)

    with isolated_worktree(vault, head) as scratch:
        (scratch / "a.md").write_text("changed\n", encoding="utf-8")
        built = commit_in(scratch, "built")

    kind = subprocess.run(
        ["git", "cat-file", "-t", built],
        cwd=vault, check=True, capture_output=True, text=True,
    ).stdout.strip()
    assert kind == "commit"
    assert git_head(vault) == head


def test_a_head_that_is_not_the_requested_source_is_refused(tmp_path: Path):
    vault = git_vault(tmp_path / "vault", {"a.md": "x\n"})

    with pytest.raises(CutoverError):
        with isolated_worktree(vault, "b" * 40):
            pass
