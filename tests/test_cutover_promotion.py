from pathlib import Path
import subprocess

import pytest

import app.cutover as cutover
from app.cutover import promote
from app.cutover_build import CutoverCommittedError, CutoverError, isolated_worktree
from app.cutover_inventory import UnmigratableContentError
from app.git_transaction import GitTransactionFailure
from tests.conftest import git_head, git_status_bytes, git_vault


def commit_in(root: Path, message: str) -> str:
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=t@e", "-c", "user.name=t",
         "commit", "-q", "-m", message],
        cwd=root, check=True, capture_output=True,
    )
    return git_head(root)


def build_a_commit(vault: Path, head: str, filename: str = "a.md") -> str:
    with isolated_worktree(vault, head) as scratch:
        target = scratch / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("changed\n", encoding="utf-8")
        return commit_in(scratch, "cutover")


def test_promotion_fast_forwards_the_live_vault(tmp_path: Path):
    vault = git_vault(tmp_path / "vault", {"a.md": "x\n"})
    head = git_head(vault)
    built = build_a_commit(vault, head)

    assert promote(vault, built, head, git_status_bytes(vault), [], []) == built
    assert (vault / "a.md").read_text(encoding="utf-8") == "changed\n"


def test_promotion_takes_the_shared_action_lock(tmp_path: Path, monkeypatch):
    vault = git_vault(tmp_path / "vault", {"a.md": "x\n"})
    head = git_head(vault)
    built = build_a_commit(vault, head)
    taken: list[Path] = []

    real = cutover.action_lock

    import contextlib

    @contextlib.contextmanager
    def recording(target):
        taken.append(target)
        with real(target):
            yield

    monkeypatch.setattr(cutover, "action_lock", recording)
    promote(vault, built, head, git_status_bytes(vault), [], [])

    assert taken == [vault]


def test_promotion_refuses_a_moved_head(tmp_path: Path):
    vault = git_vault(tmp_path / "vault", {"a.md": "x\n"})
    head = git_head(vault)
    built = build_a_commit(vault, head)
    (vault / "b.md").write_text("y\n", encoding="utf-8")
    moved = commit_in(vault, "concurrent")

    # Match the precheck's own message. Without it, disabling the precheck
    # still raises CutoverError from `git merge --ff-only`, and the test would
    # prove the fallback primitive rather than the precheck.
    with pytest.raises(
        CutoverError,
        match="live HEAD moved since the build",
    ):
        promote(vault, built, head, git_status_bytes(vault), [], [])
    assert git_head(vault) == moved


def test_promotion_refuses_a_changed_status(tmp_path: Path):
    vault = git_vault(tmp_path / "vault", {"a.md": "x\n"})
    head = git_head(vault)
    captured = git_status_bytes(vault)
    built = build_a_commit(vault, head)
    (vault / "stray.md").write_text("s\n", encoding="utf-8")

    with pytest.raises(CutoverError):
        promote(vault, built, head, captured, [], [])
    assert git_head(vault) == head


def test_promotion_repeats_the_ignored_content_check(tmp_path: Path):
    vault = git_vault(
        tmp_path / "vault", {".gitignore": ".sensitive/\n", "ab/n.md": "x\n"}
    )
    head = git_head(vault)
    built = build_a_commit(vault, head, filename="ab/other.md")
    captured = git_status_bytes(vault)
    (vault / "ab" / ".sensitive").mkdir()
    (vault / "ab" / ".sensitive" / "s.md").write_text("s\n", encoding="utf-8")

    with pytest.raises(
        UnmigratableContentError,
        match="ignored or untracked content",
    ):
        promote(vault, built, head, captured, ["ab"], [])
    assert git_head(vault) == head


def test_promotion_leaves_an_obstructing_untracked_file_intact(tmp_path: Path):
    vault = git_vault(tmp_path / "vault", {"a.md": "x\n"})
    head = git_head(vault)
    built = build_a_commit(vault, head, filename="new.md")
    (vault / "new.md").write_text("mine\n", encoding="utf-8")

    with pytest.raises(CutoverError):
        promote(vault, built, head, git_status_bytes(vault), [], [])
    assert (vault / "new.md").read_text(encoding="utf-8") == "mine\n"
    assert git_head(vault) == head


def test_a_failed_promotion_is_not_reported_as_committed(
    tmp_path: Path, monkeypatch
):
    """A failure before the body runs is an ordinary refusal, never "committed".

    The lock layer fails before promotion can reach `git merge`, so nothing was
    written. Reporting that as `CutoverCommittedError` would tell an operator
    not to retry a cutover that never started.
    """
    import contextlib

    vault = git_vault(tmp_path / "vault", {"a.md": "x\n"})
    head = git_head(vault)
    built = build_a_commit(vault, head)
    captured = git_status_bytes(vault)

    @contextlib.contextmanager
    def refuses_to_lock(_vault):
        raise GitTransactionFailure("lock layer unavailable")
        yield  # pragma: no cover

    monkeypatch.setattr(cutover, "action_lock", refuses_to_lock)

    with pytest.raises(CutoverError) as caught:
        promote(vault, built, head, captured, [], [])
    assert not isinstance(caught.value, CutoverCommittedError), (
        "uncommitted failure reported committed"
    )
    assert git_head(vault) == head


def test_a_commit_that_cannot_be_confirmed_is_reported_as_committed(
    tmp_path: Path, monkeypatch
):
    vault = git_vault(tmp_path / "vault", {"a.md": "x\n"})
    head = git_head(vault)
    built = build_a_commit(vault, head)
    captured = git_status_bytes(vault)
    state = {"merged": False}

    real_run = subprocess.run

    def flaky(args, **kwargs):
        if args[:2] == ["git", "merge"]:
            state["merged"] = True
            return real_run(args, **kwargs)
        if state["merged"] and args[:3] == ["git", "rev-parse", "HEAD"]:
            raise OSError("injected confirmation failure")
        return real_run(args, **kwargs)

    monkeypatch.setattr(cutover.subprocess, "run", flaky)

    with pytest.raises(CutoverCommittedError):
        promote(vault, built, head, captured, [], [])


def test_a_commit_confirmed_as_the_wrong_head_is_reported_as_committed_but_unresolved(
    tmp_path: Path, monkeypatch
):
    vault = git_vault(tmp_path / "vault", {"a.md": "x\n"})
    head = git_head(vault)
    built = build_a_commit(vault, head)
    captured = git_status_bytes(vault)
    real_run = subprocess.run
    merged = {"done": False}

    def wrong_head(args, **kwargs):
        if args[:2] == ["git", "merge"]:
            result = real_run(args, **kwargs)
            merged["done"] = True
            return result
        if merged["done"] and args[:3] == ["git", "rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(args, 0, stdout=head + "\n", stderr="")
        return real_run(args, **kwargs)

    monkeypatch.setattr(cutover.subprocess, "run", wrong_head)

    with pytest.raises(CutoverCommittedError, match="does not equal the built commit"):
        promote(vault, built, head, captured, [], [])


def test_promotion_checks_the_destination_entity_path_too(tmp_path: Path):
    """Ignored content at the *destination* is what promotion would overwrite.

    Checking only the source entity misses the path the migration moves into:
    a `git merge` will happily write over an ignored file there, destroying
    content no gate ever saw.
    """
    vault = git_vault(
        tmp_path / "vault", {".gitignore": "*.local\n", "ab/n.md": "x\n"}
    )
    head = git_head(vault)
    built = build_a_commit(vault, head, filename="ab-entity/other.md")
    captured = git_status_bytes(vault)
    (vault / "ab-entity").mkdir()
    (vault / "ab-entity" / "notes.local").write_text("mine\n", encoding="utf-8")

    with pytest.raises(UnmigratableContentError, match="ignored or untracked"):
        promote(vault, built, head, captured, ["ab"], ["ab-entity"])

    assert (vault / "ab-entity" / "notes.local").read_text(encoding="utf-8") == "mine\n"
    assert git_head(vault) == head


def test_promotion_refuses_to_overwrite_an_ignored_file(tmp_path: Path):
    """`--no-overwrite-ignore` makes git refuse rather than clobber."""
    vault = git_vault(
        tmp_path / "vault", {".gitignore": "*.local\n", "a.md": "x\n"}
    )
    head = git_head(vault)
    # The commit must genuinely add the ignored path, so it is force-added:
    # `git add -A` would skip it and there would be nothing to promote.
    with isolated_worktree(vault, head) as scratch:
        (scratch / "notes.local").write_text("from the cutover\n", encoding="utf-8")
        subprocess.run(["git", "add", "-f", "notes.local"], cwd=scratch, check=True)
        built = commit_in(scratch, "cutover")
    captured = git_status_bytes(vault)
    (vault / "notes.local").write_text("mine\n", encoding="utf-8")

    with pytest.raises(CutoverError):
        promote(vault, built, head, captured, [], [])

    assert (vault / "notes.local").read_text(encoding="utf-8") == "mine\n"
    assert git_head(vault) == head
