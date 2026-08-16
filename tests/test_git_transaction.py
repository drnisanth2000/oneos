import fcntl
import os
import shlex
import stat
import subprocess
from pathlib import Path

import pytest

import app.git_transaction as transaction
from app.git_transaction import (
    PathChange,
    PathState,
    ReviewedStateConflict,
    TransactionPlan,
    VaultBusyError,
    capture_path_state,
)
from tests.conftest import (
    git_bytes,
    git_cached_diff,
    git_changed_paths,
    git_entity_vault,
    git_head,
    git_head_message,
    git_index_entries,
    git_status_bytes,
    git_worktree_diff,
)


def _vault(tmp_path: Path) -> Path:
    return git_entity_vault(
        tmp_path,
        ("synthetic",),
        {
            "synthetic/00-inbox/active/item.md": "reviewed\n",
            "synthetic/11-library/active/.gitkeep": "",
        },
    )


def _approval_plan() -> TransactionPlan:
    source = "synthetic/00-inbox/active/item.md"
    destination = "synthetic/11-library/active/item.md"
    proposal = "synthetic/outbox/proposal.yaml"
    return TransactionPlan(
        "outbox: approve synthetic item",
        (
            PathChange(
                source,
                PathState.regular(b"reviewed\n", 0o644),
                PathState.absent(),
            ),
            PathChange(
                destination,
                PathState.absent(),
                PathState.regular(b"approved\n", 0o644),
            ),
        ),
        (source, destination),
        (
            PathChange(
                proposal,
                PathState.regular(b"proposal\n", 0o644),
                PathState.absent(),
            ),
        ),
    )


def _write_proposal(vault: Path, contents: bytes = b"proposal\n") -> Path:
    proposal = vault / "synthetic/outbox/proposal.yaml"
    proposal.parent.mkdir(parents=True, exist_ok=True)
    proposal.write_bytes(contents)
    return proposal


def _unrelated_index_entries(vault: Path, reviewed: tuple[str, ...]) -> tuple[bytes, ...]:
    reviewed_bytes = {os.fsencode(path) for path in reviewed}
    return tuple(
        sorted(
            record
            for record in git_index_entries(vault).split(b"\0")
            if record and record.split(b"\t", 1)[1] not in reviewed_bytes
        )
    )


def _reviewed_index_entries(vault: Path, reviewed: tuple[str, ...]) -> tuple[bytes, ...]:
    reviewed_bytes = {os.fsencode(path) for path in reviewed}
    return tuple(
        sorted(
            record
            for record in git_index_entries(vault).split(b"\0")
            if record and record.split(b"\t", 1)[1] in reviewed_bytes
        )
    )


def _unrelated_status(vault: Path, excluded: set[str]) -> tuple[bytes, ...]:
    return tuple(
        sorted(
            record
            for record in git_status_bytes(vault).split(b"\0")
            if record and os.fsdecode(record[3:]) not in excluded
        )
    )


def _direct_path_state(vault: Path, path: str) -> tuple[bytes | None, int | None]:
    target = vault / path
    try:
        target_stat = os.lstat(target)
    except FileNotFoundError:
        return None, None
    assert stat.S_ISREG(target_stat.st_mode)
    return target.read_bytes(), stat.S_IMODE(target_stat.st_mode)


def _temporary_directory_state(directory: Path) -> tuple[tuple[str, bytes], ...]:
    return tuple(
        sorted(
            (path.relative_to(directory).as_posix(), path.read_bytes())
            for path in directory.rglob("*")
            if path.is_file()
        )
    )


def _complete_state(
    vault: Path, plan: TransactionPlan, index_directory: Path
) -> dict[str, object]:
    owned_paths = tuple(change.path for change in plan.changes + plan.owned_changes)
    excluded = set(owned_paths)
    return {
        "head": git_head(vault),
        "owned": tuple(
            (path, _direct_path_state(vault, path)) for path in owned_paths
        ),
        "reviewed_index": _reviewed_index_entries(vault, plan.commit_paths),
        "unrelated_index": _unrelated_index_entries(vault, plan.commit_paths),
        "status": git_status_bytes(vault),
        "unrelated_status": _unrelated_status(vault, excluded),
        "worktree_diff": git_worktree_diff(vault),
        "cached_diff": git_cached_diff(vault),
        "temporary_indexes": _temporary_directory_state(index_directory),
    }


def _prepared_transaction(
    tmp_path: Path, monkeypatch
) -> tuple[Path, TransactionPlan, Path, dict[str, object]]:
    vault = _vault(tmp_path)
    plan = _approval_plan()
    _write_proposal(vault)

    staged = vault / "staged.bin"
    unstaged = vault / "unstaged.bin"
    untracked = vault / "untracked.bin"
    staged.write_bytes(b"staged base\n")
    unstaged.write_bytes(b"unstaged base\n")
    git_bytes(vault, "add", "staged.bin", "unstaged.bin")
    git_bytes(vault, "commit", "-q", "-m", "add unrelated rollback fixtures")
    staged.write_bytes(b"staged exact\x00\xff\n")
    git_bytes(vault, "add", "staged.bin")
    unstaged.write_bytes(b"unstaged exact\x00\xfe\n")
    untracked.write_bytes(b"untracked exact\x00\xfd\n")

    index_directory = tmp_path.parent / f"{tmp_path.name}-rollback-indexes"
    index_directory.mkdir()
    monkeypatch.setattr(transaction.tempfile, "tempdir", os.fspath(index_directory))
    before = _complete_state(vault, plan, index_directory)
    return vault, plan, index_directory, before


def _assert_unrelated_state_matches(
    current: dict[str, object], before: dict[str, object]
) -> None:
    for key in (
        "unrelated_index",
        "unrelated_status",
        "worktree_diff",
        "cached_diff",
        "temporary_indexes",
    ):
        assert current[key] == before[key]


def _exception_chain_contains(error: BaseException, expected: type[BaseException]) -> bool:
    current: BaseException | None = error
    while current is not None:
        if isinstance(current, expected):
            return True
        current = current.__cause__
    return False


def _inject_temporary_index_cleanup_failure(
    monkeypatch, index_directory: Path
) -> None:
    original_unlink = transaction.os.unlink
    attempts: dict[str, int] = {}

    def fail_persistent_cleanup(path, *args, **kwargs):
        target = Path(path)
        if (
            target.parent == index_directory
            and target.name.startswith("oneos-index-")
        ):
            attempts[target.name] = attempts.get(target.name, 0) + 1
            if attempts[target.name] > 1 and target.exists():
                raise OSError("injected temporary index cleanup failure")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(transaction.os, "unlink", fail_persistent_cleanup)


def test_path_state_requires_absence_or_exact_regular_bytes_and_mode():
    assert PathState.absent() == PathState(None, None)
    assert PathState.regular(b"body\n", 0o644) == PathState(b"body\n", 0o644)
    with pytest.raises(ValueError):
        PathState(b"body\n", None)
    with pytest.raises(ValueError):
        PathState(None, 0o644)


@pytest.mark.parametrize(
    "path",
    [
        "",
        ".",
        "../escape",
        "/absolute",
        "synthetic/../escape",
        ".git/config",
        "synthetic/.git/config",
        "synthetic\\item.md",
    ],
)
def test_plan_rejects_nonlexical_or_git_internal_paths(path):
    change = PathChange(path, PathState.absent(), PathState.regular(b"x", 0o644))
    with pytest.raises(ValueError):
        TransactionPlan("outbox: synthetic", (change,), (path,))


def test_plan_rejects_duplicate_or_mismatched_commit_paths():
    change = PathChange(
        "synthetic/00-inbox/active/item.md",
        PathState.regular(b"reviewed\n", 0o644),
        PathState.absent(),
    )
    with pytest.raises(ValueError):
        TransactionPlan("outbox: synthetic", (change, change), (change.path,))
    with pytest.raises(ValueError):
        TransactionPlan("outbox: synthetic", (change,), ("synthetic/other.md",))


def test_capture_rejects_symlink_directory_and_redirected_parent(tmp_path):
    vault = _vault(tmp_path)
    target = vault / "target.md"
    target.write_bytes(b"target\n")
    leaf = vault / "synthetic/leaf.md"
    leaf.symlink_to(target)
    redirected = vault / "synthetic/redirected"
    redirected.symlink_to(vault / "synthetic/00-inbox", target_is_directory=True)

    with pytest.raises(ReviewedStateConflict):
        capture_path_state(vault, "synthetic/leaf.md")
    with pytest.raises(ReviewedStateConflict):
        capture_path_state(vault, "synthetic/redirected/active/item.md")


def test_capture_refuses_parent_replaced_after_validation(tmp_path, monkeypatch):
    vault = _vault(tmp_path)
    active = vault / "synthetic/00-inbox/active"
    replacement = vault / "replacement"
    replacement.mkdir()
    (replacement / "item.md").write_bytes(b"redirected\n")
    original_lstat = transaction.os.lstat
    swapped = False

    def replace_parent_after_validation(path, *args, **kwargs):
        nonlocal swapped
        state = original_lstat(path, *args, **kwargs)
        if not swapped and Path(path).name == "active":
            swapped = True
            active.rename(vault / "original-active")
            active.symlink_to(replacement, target_is_directory=True)
        return state

    monkeypatch.setattr(transaction.os, "lstat", replace_parent_after_validation)

    with pytest.raises(ReviewedStateConflict):
        capture_path_state(vault, "synthetic/00-inbox/active/item.md")


def test_capture_preserves_exact_regular_bytes_and_mode(tmp_path):
    vault = _vault(tmp_path)
    item = vault / "synthetic/00-inbox/active/item.md"
    item.write_bytes(b"\x00reviewed\xff\n")
    item.chmod(0o640)

    assert capture_path_state(vault, "synthetic/00-inbox/active/item.md") == PathState.regular(
        b"\x00reviewed\xff\n", 0o640
    )


def test_capture_allows_only_an_absent_leaf_not_a_missing_parent(tmp_path):
    vault = _vault(tmp_path)

    assert capture_path_state(
        vault, "synthetic/00-inbox/active/new-item.md"
    ) == PathState.absent()
    with pytest.raises(ReviewedStateConflict):
        capture_path_state(vault, "synthetic/missing/active/new-item.md")


def test_second_approval_lock_fails_without_waiting_or_mutating(tmp_path):
    vault = _vault(tmp_path)
    with transaction._approval_lock(vault):
        with pytest.raises(VaultBusyError):
            with transaction._approval_lock(vault):
                pytest.fail("contended lock body must not run")


def test_approval_lock_releases_for_immediate_reacquisition(tmp_path):
    vault = _vault(tmp_path)

    with transaction._approval_lock(vault):
        pass
    with transaction._approval_lock(vault):
        pass


def test_approval_lock_releases_after_exceptional_body_exit(tmp_path):
    vault = _vault(tmp_path)

    with pytest.raises(RuntimeError, match="body failure"):
        with transaction._approval_lock(vault):
            raise RuntimeError("body failure")
    with transaction._approval_lock(vault):
        pass


def test_approval_lock_closes_and_releases_when_unlock_fails(tmp_path, monkeypatch):
    vault = _vault(tmp_path)
    original_flock = transaction.fcntl.flock
    original_close = transaction.os.close
    original_open = transaction.os.open
    opened_descriptors = []
    closed_descriptors = []

    def unlock_failure(descriptor, operation):
        if operation == fcntl.LOCK_UN:
            raise OSError("injected unlock failure")
        return original_flock(descriptor, operation)

    def record_close(descriptor):
        closed_descriptors.append(descriptor)
        return original_close(descriptor)

    def record_lock_open(path, *args, **kwargs):
        descriptor = original_open(path, *args, **kwargs)
        if Path(path).name == "oneos-approval.lock":
            opened_descriptors.append(descriptor)
        return descriptor

    monkeypatch.setattr(transaction.fcntl, "flock", unlock_failure)
    monkeypatch.setattr(transaction.os, "close", record_close)
    monkeypatch.setattr(transaction.os, "open", record_lock_open)

    with pytest.raises(OSError, match="injected unlock failure"):
        with transaction._approval_lock(vault):
            pass

    assert set(opened_descriptors).issubset(closed_descriptors)
    monkeypatch.setattr(transaction.fcntl, "flock", original_flock)
    monkeypatch.setattr(transaction.os, "close", original_close)
    monkeypatch.setattr(transaction.os, "open", original_open)
    with transaction._approval_lock(vault):
        pass


def test_list_capable_plan_commits_two_reviewed_changes_once(tmp_path):
    vault = _vault(tmp_path)
    proposal = _write_proposal(vault)
    plan = _approval_plan()
    source = vault / plan.commit_paths[0]
    destination = vault / plan.commit_paths[1]
    start_head = git_head(vault)

    result = transaction.execute_transaction(vault, plan)

    assert result.changed_paths == (
        "synthetic/00-inbox/active/item.md",
        "synthetic/11-library/active/item.md",
    )
    assert git_changed_paths(vault, result.commit_oid) == list(result.changed_paths)
    assert git_head_message(vault) == plan.message
    assert subprocess.run(
        ["git", "rev-list", "--count", f"{start_head}..{result.commit_oid}"],
        cwd=vault,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip() == "1"
    assert source.exists() is False
    assert destination.read_bytes() == b"approved\n"
    assert proposal.exists() is False


def test_unrelated_staged_unstaged_and_untracked_state_is_preserved_exactly(tmp_path):
    vault = _vault(tmp_path)
    plan = _approval_plan()
    _write_proposal(vault)
    unrelated_staged = vault / "staged.md"
    unrelated_unstaged = vault / "unstaged.md"
    unrelated_untracked = vault / "untracked.bin"
    unrelated_staged.write_bytes(b"staged base\n")
    unrelated_unstaged.write_bytes(b"unstaged base\n")
    git_bytes(vault, "add", "staged.md", "unstaged.md")
    git_bytes(vault, "commit", "-q", "-m", "add unrelated fixtures")
    staged_bytes = b"staged exact\x00\xff\n"
    unstaged_bytes = b"unstaged exact\x00\xfe\n"
    untracked_bytes = b"untracked exact\x00\xfd\n"
    unrelated_staged.write_bytes(staged_bytes)
    git_bytes(vault, "add", "staged.md")
    unrelated_unstaged.write_bytes(unstaged_bytes)
    unrelated_untracked.write_bytes(untracked_bytes)
    status_exclusions = set(plan.commit_paths) | {
        change.path for change in plan.owned_changes
    }
    status_before = _unrelated_status(vault, status_exclusions)
    worktree_diff_before = git_worktree_diff(vault)
    cached_diff_before = git_cached_diff(vault)
    unrelated_index_entries_before = _unrelated_index_entries(vault, plan.commit_paths)

    transaction.execute_transaction(vault, plan)

    assert unrelated_staged.read_bytes() == staged_bytes
    assert unrelated_unstaged.read_bytes() == unstaged_bytes
    assert unrelated_untracked.read_bytes() == untracked_bytes
    assert _unrelated_index_entries(vault, plan.commit_paths) == unrelated_index_entries_before
    assert _unrelated_status(vault, status_exclusions) == status_before
    assert git_worktree_diff(vault) == worktree_diff_before
    assert git_cached_diff(vault) == cached_diff_before


def test_existing_pre_commit_hook_runs_against_only_alternate_index_paths(tmp_path):
    vault = _vault(tmp_path)
    plan = _approval_plan()
    _write_proposal(vault)
    unrelated_staged = vault / "staged.md"
    unrelated_staged.write_bytes(b"base\n")
    git_bytes(vault, "add", "staged.md")
    git_bytes(vault, "commit", "-q", "-m", "add hook fixture")
    unrelated_staged.write_bytes(b"unrelated staged\n")
    git_bytes(vault, "add", "staged.md")
    hook_record = tmp_path.parent / f"{tmp_path.name}-hook-paths.txt"
    hook = vault / ".git/hooks/pre-commit"
    hook.write_text(
        "#!/bin/sh\n"
        f"git diff --cached --name-only > {shlex.quote(os.fspath(hook_record))}\n",
        encoding="utf-8",
    )
    hook.chmod(0o755)

    transaction.execute_transaction(vault, plan)

    assert hook_record.read_text(encoding="utf-8").splitlines() == sorted(plan.commit_paths)
    assert "staged.md" not in hook_record.read_text(encoding="utf-8").splitlines()


def test_reviewed_staged_change_is_refused_before_filesystem_mutation(tmp_path):
    vault = _vault(tmp_path)
    plan = _approval_plan()
    proposal = _write_proposal(vault)
    source = vault / plan.commit_paths[0]
    destination = vault / plan.commit_paths[1]
    start_head = git_head(vault)
    source.write_bytes(b"unexpected staged\n")
    git_bytes(vault, "add", plan.commit_paths[0])
    source.write_bytes(b"reviewed\n")

    with pytest.raises(ReviewedStateConflict):
        transaction.execute_transaction(vault, plan)

    assert git_head(vault) == start_head
    assert source.read_bytes() == b"reviewed\n"
    assert destination.exists() is False
    assert proposal.read_bytes() == b"proposal\n"


def test_reviewed_unstaged_change_is_refused_before_filesystem_mutation(tmp_path):
    vault = _vault(tmp_path)
    plan = _approval_plan()
    proposal = _write_proposal(vault)
    source = vault / plan.commit_paths[0]
    destination = vault / plan.commit_paths[1]
    start_head = git_head(vault)
    source.write_bytes(b"unexpected unstaged\n")

    with pytest.raises(ReviewedStateConflict):
        transaction.execute_transaction(vault, plan)

    assert git_head(vault) == start_head
    assert source.read_bytes() == b"unexpected unstaged\n"
    assert destination.exists() is False
    assert proposal.read_bytes() == b"proposal\n"


def test_owned_proposal_mismatch_is_refused_before_filesystem_mutation(tmp_path):
    vault = _vault(tmp_path)
    plan = _approval_plan()
    proposal = _write_proposal(vault, b"different proposal\n")
    source = vault / plan.commit_paths[0]
    destination = vault / plan.commit_paths[1]
    start_head = git_head(vault)

    with pytest.raises(ReviewedStateConflict):
        transaction.execute_transaction(vault, plan)

    assert git_head(vault) == start_head
    assert source.read_bytes() == b"reviewed\n"
    assert destination.exists() is False
    assert proposal.read_bytes() == b"different proposal\n"


def test_temporary_alternate_index_is_removed_after_success(tmp_path, monkeypatch):
    vault = _vault(tmp_path)
    plan = _approval_plan()
    _write_proposal(vault)
    index_directory = tmp_path.parent / f"{tmp_path.name}-indexes"
    index_directory.mkdir()
    monkeypatch.setattr(transaction.tempfile, "tempdir", os.fspath(index_directory))

    transaction.execute_transaction(vault, plan)

    assert list(index_directory.iterdir()) == []


@pytest.mark.parametrize(
    "checkpoint",
    (
        "filesystem-applied",
        "alternate-index-ready",
        "reviewed-paths-staged",
        "commit-created",
        "commit-verified",
        "real-index-synchronized",
    ),
)
def test_failure_at_every_phase_restores_owned_and_unrelated_state(
    tmp_path, monkeypatch, checkpoint
):
    vault, plan, index_directory, before = _prepared_transaction(
        tmp_path, monkeypatch
    )

    def fail_here(name: str) -> None:
        if name == checkpoint:
            raise OSError(f"injected {name}")

    monkeypatch.setattr(transaction, "_checkpoint", fail_here, raising=False)

    with pytest.raises(transaction.GitTransactionFailure) as raised:
        transaction.execute_transaction(vault, plan)

    assert str(raised.value) == "approval transaction failed and was rolled back"
    assert _complete_state(vault, plan, index_directory) == before
    assert list(index_directory.iterdir()) == []
    with transaction._approval_lock(vault):
        pass


def test_rejecting_hook_restores_exact_starting_state(tmp_path, monkeypatch):
    vault, plan, index_directory, before = _prepared_transaction(
        tmp_path, monkeypatch
    )
    hook = vault / ".git/hooks/pre-commit"
    hook.write_text("#!/bin/sh\nexit 23\n", encoding="utf-8")
    hook.chmod(0o755)

    with pytest.raises(transaction.GitTransactionFailure) as raised:
        transaction.execute_transaction(vault, plan)

    assert str(raised.value) == "approval transaction failed and was rolled back"
    assert _complete_state(vault, plan, index_directory) == before
    assert _exception_chain_contains(raised.value, subprocess.CalledProcessError)
    assert list(index_directory.iterdir()) == []
    with transaction._approval_lock(vault):
        pass


def test_concurrent_same_path_replacement_is_preserved_during_recovery(
    tmp_path, monkeypatch
):
    vault, plan, index_directory, before = _prepared_transaction(
        tmp_path, monkeypatch
    )
    destination = plan.commit_paths[1]

    def replace_destination_after_commit(name: str) -> None:
        if name == "commit-created":
            (vault / destination).write_bytes(b"concurrent replacement\n")
            raise OSError("injected concurrent same-path replacement")

    monkeypatch.setattr(
        transaction, "_checkpoint", replace_destination_after_commit, raising=False
    )

    with pytest.raises(transaction.GitTransactionRecoveryError) as raised:
        transaction.execute_transaction(vault, plan)

    assert raised.value.paths == (destination,)
    assert (vault / destination).read_bytes() == b"concurrent replacement\n"
    assert git_head(vault) == before["head"]
    current = _complete_state(vault, plan, index_directory)
    _assert_unrelated_state_matches(current, before)
    assert current["reviewed_index"] == before["reviewed_index"]
    assert list(index_directory.iterdir()) == []
    with transaction._approval_lock(vault):
        pass


def test_head_ownership_preserves_newer_ref_during_recovery(tmp_path, monkeypatch):
    vault, plan, index_directory, before = _prepared_transaction(
        tmp_path, monkeypatch
    )
    newer_head = ""

    def advance_head_after_commit(name: str) -> None:
        nonlocal newer_head
        if name != "commit-created":
            return
        transaction_head = git_head(vault)
        start_tree = git_bytes(
            vault, "rev-parse", f"{before['head']}^{{tree}}"
        ).decode("ascii").strip()
        newer_head = git_bytes(
            vault,
            "commit-tree",
            start_tree,
            "-p",
            transaction_head,
            "-m",
            "concurrent head",
        ).decode("ascii").strip()
        git_bytes(vault, "update-ref", "HEAD", newer_head, transaction_head)
        raise OSError("injected concurrent HEAD update")

    monkeypatch.setattr(
        transaction, "_checkpoint", advance_head_after_commit, raising=False
    )

    with pytest.raises(transaction.GitTransactionRecoveryError) as raised:
        transaction.execute_transaction(vault, plan)

    assert raised.value.paths == ("HEAD",)
    assert git_head(vault) == newer_head
    current = _complete_state(vault, plan, index_directory)
    current["head"] = before["head"]
    assert current == before
    assert list(index_directory.iterdir()) == []
    with transaction._approval_lock(vault):
        pass


def test_failure_after_commit_returns_before_ownership_capture_restores_head(
    tmp_path, monkeypatch
):
    vault, plan, index_directory, before = _prepared_transaction(
        tmp_path, monkeypatch
    )

    def fail_before_ownership_capture(name: str) -> None:
        if name == "commit-returned":
            raise OSError("injected ownership capture failure")

    monkeypatch.setattr(
        transaction, "_checkpoint", fail_before_ownership_capture
    )

    with pytest.raises(transaction.GitTransactionFailure) as raised:
        transaction.execute_transaction(vault, plan)

    assert str(raised.value) == "approval transaction failed and was rolled back"
    assert _complete_state(vault, plan, index_directory) == before


def test_concurrent_head_before_ownership_capture_is_never_claimed(
    tmp_path, monkeypatch
):
    vault, plan, index_directory, before = _prepared_transaction(
        tmp_path, monkeypatch
    )
    newer_head = ""

    def advance_before_ownership_capture(name: str) -> None:
        nonlocal newer_head
        if name == "commit-created":
            raise OSError("injected failure after concurrent ownership capture")
        if name != "commit-returned":
            return
        transaction_head = git_head(vault)
        start_tree = git_bytes(
            vault, "rev-parse", f"{before['head']}^{{tree}}"
        ).decode("ascii").strip()
        newer_head = git_bytes(
            vault,
            "commit-tree",
            start_tree,
            "-p",
            transaction_head,
            "-m",
            "concurrent before ownership capture",
        ).decode("ascii").strip()
        git_bytes(vault, "update-ref", "HEAD", newer_head, transaction_head)

    monkeypatch.setattr(
        transaction, "_checkpoint", advance_before_ownership_capture
    )

    with pytest.raises(transaction.GitTransactionRecoveryError) as raised:
        transaction.execute_transaction(vault, plan)

    assert raised.value.paths == ("HEAD",)
    assert git_head(vault) == newer_head
    current = _complete_state(vault, plan, index_directory)
    current["head"] = before["head"]
    assert current == before


def test_failure_after_real_mutation_before_apply_returns_restores_state(
    tmp_path, monkeypatch
):
    vault, plan, index_directory, before = _prepared_transaction(
        tmp_path, monkeypatch
    )

    def fail_before_apply_returns(name: str) -> None:
        if name == "filesystem-path-applied":
            raise OSError("injected failure before apply returns")

    monkeypatch.setattr(transaction, "_checkpoint", fail_before_apply_returns)

    with pytest.raises(transaction.GitTransactionFailure) as raised:
        transaction.execute_transaction(vault, plan)

    assert str(raised.value) == "approval transaction failed and was rolled back"
    assert _complete_state(vault, plan, index_directory) == before


def test_concurrent_replacement_before_apply_returns_is_preserved(
    tmp_path, monkeypatch
):
    vault, plan, index_directory, before = _prepared_transaction(
        tmp_path, monkeypatch
    )
    source = plan.commit_paths[0]

    def replace_source_before_apply_returns(name: str) -> None:
        if name == "filesystem-path-applied":
            (vault / source).write_bytes(b"concurrent source replacement\n")
            raise OSError("injected concurrent replacement before apply returns")

    monkeypatch.setattr(
        transaction, "_checkpoint", replace_source_before_apply_returns
    )

    with pytest.raises(transaction.GitTransactionRecoveryError) as raised:
        transaction.execute_transaction(vault, plan)

    assert raised.value.paths == (source,)
    assert (vault / source).read_bytes() == b"concurrent source replacement\n"
    assert git_head(vault) == before["head"]
    current = _complete_state(vault, plan, index_directory)
    for key in (
        "reviewed_index",
        "unrelated_index",
        "unrelated_status",
        "cached_diff",
        "temporary_indexes",
    ):
        assert current[key] == before[key]
    assert (vault / "staged.bin").read_bytes() == b"staged exact\x00\xff\n"
    assert (vault / "unstaged.bin").read_bytes() == b"unstaged exact\x00\xfe\n"
    assert (vault / "untracked.bin").read_bytes() == b"untracked exact\x00\xfd\n"


def test_pre_commit_failure_with_cleanup_failure_is_typed_and_restores_state(
    tmp_path, monkeypatch
):
    vault, plan, index_directory, before = _prepared_transaction(
        tmp_path, monkeypatch
    )
    hook = vault / ".git/hooks/pre-commit"
    hook.write_text("#!/bin/sh\nexit 29\n", encoding="utf-8")
    hook.chmod(0o755)
    _inject_temporary_index_cleanup_failure(monkeypatch, index_directory)

    with pytest.raises(transaction.GitTransactionFailure) as raised:
        transaction.execute_transaction(vault, plan)

    assert type(raised.value) is transaction.GitTransactionFailure
    assert str(raised.value) == (
        "approval transaction failed and was rolled back; "
        "temporary index cleanup failed"
    )
    current = _complete_state(vault, plan, index_directory)
    leaked_indexes = current["temporary_indexes"]
    current["temporary_indexes"] = before["temporary_indexes"]
    assert current == before
    assert leaked_indexes
    with transaction._approval_lock(vault):
        pass


def test_success_cleanup_failure_carries_committed_result_without_rewinding(
    tmp_path, monkeypatch
):
    vault, plan, index_directory, before = _prepared_transaction(
        tmp_path, monkeypatch
    )
    _inject_temporary_index_cleanup_failure(monkeypatch, index_directory)

    with pytest.raises(transaction.GitTransactionError) as raised:
        transaction.execute_transaction(vault, plan)

    assert type(raised.value) is transaction.GitTransactionCommittedError
    assert isinstance(raised.value, transaction.GitTransactionFailure) is False
    assert raised.value.result == transaction.TransactionResult(
        git_head(vault), tuple(sorted(plan.commit_paths))
    )
    assert raised.value.commit_oid == git_head(vault)
    assert isinstance(raised.value.cleanup_error, OSError)
    assert str(raised.value) == (
        "approval transaction committed but temporary index cleanup failed"
    )
    assert git_head(vault) != before["head"]
    assert git_head_message(vault) == plan.message
    assert (vault / plan.commit_paths[0]).exists() is False
    assert (vault / plan.commit_paths[1]).read_bytes() == b"approved\n"
    assert (vault / plan.owned_changes[0].path).exists() is False
    current = _complete_state(vault, plan, index_directory)
    for key in (
        "unrelated_index",
        "unrelated_status",
        "worktree_diff",
        "cached_diff",
    ):
        assert current[key] == before[key]
    assert current["temporary_indexes"]
    with transaction._approval_lock(vault):
        pass


def test_sha256_repository_commits_full_multi_path_transaction(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GIT_DEFAULT_HASH", "sha256")
    vault, plan, index_directory, before = _prepared_transaction(
        tmp_path, monkeypatch
    )

    result = transaction.execute_transaction(vault, plan)

    assert len(before["head"]) == 64
    assert len(result.commit_oid) == 64
    assert result.commit_oid == git_head(vault)
    assert result.changed_paths == tuple(sorted(plan.commit_paths))
    assert git_changed_paths(vault, result.commit_oid) == list(result.changed_paths)
    assert (vault / plan.commit_paths[0]).exists() is False
    assert (vault / plan.commit_paths[1]).read_bytes() == b"approved\n"
    assert (vault / plan.owned_changes[0].path).exists() is False
    assert list(index_directory.iterdir()) == []


def test_sha256_post_commit_failure_restores_exact_starting_state(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GIT_DEFAULT_HASH", "sha256")
    vault, plan, index_directory, before = _prepared_transaction(
        tmp_path, monkeypatch
    )

    def fail_after_commit_capture(name: str) -> None:
        if name == "commit-created":
            raise OSError("injected SHA-256 post-commit failure")

    monkeypatch.setattr(transaction, "_checkpoint", fail_after_commit_capture)

    with pytest.raises(transaction.GitTransactionFailure) as raised:
        transaction.execute_transaction(vault, plan)

    assert type(raised.value) is transaction.GitTransactionFailure
    assert str(raised.value) == "approval transaction failed and was rolled back"
    assert len(before["head"]) == 64
    assert _complete_state(vault, plan, index_directory) == before
