import fcntl
import hashlib
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


def _inject_lock_teardown_failure(monkeypatch, failure: str) -> list[int]:
    original_open = transaction.os.open
    original_close = transaction.os.close
    original_flock = transaction.fcntl.flock
    lock_descriptors: list[int] = []

    def record_lock_open(path, *args, **kwargs):
        descriptor = original_open(path, *args, **kwargs)
        if Path(path).name == "oneos-approval.lock":
            lock_descriptors.append(descriptor)
        return descriptor

    def fail_unlock(descriptor, operation):
        if (
            failure == "unlock"
            and descriptor in lock_descriptors
            and operation == fcntl.LOCK_UN
        ):
            raise OSError("injected approval lock unlock failure")
        return original_flock(descriptor, operation)

    def fail_close_after_closing(descriptor):
        if failure == "close" and descriptor in lock_descriptors:
            original_close(descriptor)
            raise OSError("injected approval lock close failure")
        return original_close(descriptor)

    monkeypatch.setattr(transaction.os, "open", record_lock_open)
    monkeypatch.setattr(transaction.fcntl, "flock", fail_unlock)
    monkeypatch.setattr(transaction.os, "close", fail_close_after_closing)
    return lock_descriptors


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
    git_bytes(vault, "update-index", "--force-remove", "--", path)
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


def test_unrelated_fingerprint_never_hashes_through_redirected_parent(tmp_path):
    vault = _vault(tmp_path / "vault")
    relative = "synthetic/11-library/active/tracked.md"
    (vault / relative).write_bytes(b"committed bytes\n")
    git_bytes(vault, "add", relative)
    git_bytes(vault, "commit", "-q", "-m", "fixture: tracked fingerprint path")
    module = vault / "synthetic/11-library"
    original_module = tmp_path / "original-module"
    module.rename(original_module)
    external_module = tmp_path / "external-module"
    (external_module / "active").mkdir(parents=True)
    external_bytes = b"external bytes must not be hashed\n"
    (external_module / "active/tracked.md").write_bytes(external_bytes)
    module.symlink_to(external_module, target_is_directory=True)

    dirty = {
        state.path: state
        for state in transaction._capture_dirty_paths(vault, set())
    }
    fingerprint = dirty[relative]

    assert fingerprint.kind == "redirected"
    assert fingerprint.fingerprint is None
    assert fingerprint.fingerprint != hashlib.sha256(external_bytes).digest()


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

    with pytest.raises(transaction.GitTransactionFailure) as raised:
        with transaction._approval_lock(vault):
            pass

    assert isinstance(raised.value.__cause__, OSError)
    assert str(raised.value.__cause__) == "injected unlock failure"
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


def test_same_path_replacement_before_staging_is_not_committed(
    tmp_path, monkeypatch
):
    vault = _vault(tmp_path)
    plan = _approval_plan()
    proposal = _write_proposal(vault)
    source = vault / plan.commit_paths[0]
    destination = vault / plan.commit_paths[1]
    start_head = git_head(vault)

    def replace_reviewed_destination(name: str) -> None:
        if name == "filesystem-applied":
            destination.write_bytes(b"same-path replacement\n")

    monkeypatch.setattr(transaction, "_checkpoint", replace_reviewed_destination)

    with pytest.raises(transaction.GitTransactionRecoveryError) as raised:
        transaction.execute_transaction(vault, plan)

    assert raised.value.paths == (plan.commit_paths[1],)
    assert git_head(vault) == start_head
    assert source.read_bytes() == b"reviewed\n"
    assert destination.read_bytes() == b"same-path replacement\n"
    assert proposal.read_bytes() == b"proposal\n"


@pytest.mark.parametrize(
    "hook_body",
    (
        (
            "printf 'unreviewed bytes\\n' > synthetic/11-library/active/item.md\n"
            "git add -- synthetic/11-library/active/item.md\n"
            "printf 'approved\\n' > synthetic/11-library/active/item.md\n"
        ),
        (
            "chmod 755 synthetic/11-library/active/item.md\n"
            "git add -- synthetic/11-library/active/item.md\n"
            "chmod 644 synthetic/11-library/active/item.md\n"
        ),
    ),
    ids=("bytes", "git-mode"),
)
def test_hook_cannot_replace_reviewed_commit_tree_entry(
    tmp_path, hook_body
):
    vault = _vault(tmp_path)
    plan = _approval_plan()
    proposal = _write_proposal(vault)
    source = vault / plan.commit_paths[0]
    destination = vault / plan.commit_paths[1]
    start_head = git_head(vault)
    hook = vault / ".git/hooks/pre-commit"
    hook.write_text("#!/bin/sh\nset -eu\n" + hook_body, encoding="utf-8")
    hook.chmod(0o755)

    with pytest.raises(transaction.GitTransactionFailure) as raised:
        transaction.execute_transaction(vault, plan)

    assert str(raised.value) == "approval transaction failed and was rolled back"
    assert git_head(vault) == start_head
    assert source.read_bytes() == b"reviewed\n"
    assert destination.exists() is False
    assert proposal.read_bytes() == b"proposal\n"


def test_commit_tree_mode_uses_git_owner_execute_normalization(tmp_path):
    vault = _vault(tmp_path)
    base_plan = _approval_plan()
    _write_proposal(vault)
    destination_change = base_plan.changes[1]
    plan = TransactionPlan(
        base_plan.message,
        (
            base_plan.changes[0],
            PathChange(
                destination_change.path,
                destination_change.before,
                PathState.regular(b"approved\n", 0o654),
            ),
        ),
        base_plan.commit_paths,
        base_plan.owned_changes,
    )

    result = transaction.execute_transaction(vault, plan)

    tree_entry = git_bytes(
        vault, "ls-tree", result.commit_oid, "--", destination_change.path
    ).split(b" ", 1)[0]
    assert tree_entry == b"100644"
    assert stat.S_IMODE((vault / destination_change.path).stat().st_mode) == 0o654


def test_hook_same_path_replacement_after_staging_blocks_success(tmp_path):
    vault = _vault(tmp_path)
    plan = _approval_plan()
    proposal = _write_proposal(vault)
    source = vault / plan.commit_paths[0]
    destination = vault / plan.commit_paths[1]
    start_head = git_head(vault)
    hook = vault / ".git/hooks/pre-commit"
    hook.write_text(
        "#!/bin/sh\n"
        "printf 'concurrent replacement\\n' > "
        "synthetic/11-library/active/item.md\n",
        encoding="utf-8",
    )
    hook.chmod(0o755)

    with pytest.raises(transaction.GitTransactionRecoveryError) as raised:
        transaction.execute_transaction(vault, plan)

    assert raised.value.paths == (plan.commit_paths[1],)
    assert git_head(vault) == start_head
    assert source.read_bytes() == b"reviewed\n"
    assert destination.read_bytes() == b"concurrent replacement\n"
    assert proposal.read_bytes() == b"proposal\n"


def test_head_advance_after_transaction_commit_blocks_success(tmp_path, monkeypatch):
    vault = _vault(tmp_path)
    plan = _approval_plan()
    proposal = _write_proposal(vault)
    source = vault / plan.commit_paths[0]
    destination = vault / plan.commit_paths[1]
    start_head = git_head(vault)
    newer_head = ""

    def advance_head_without_failing(name: str) -> None:
        nonlocal newer_head
        if name != "commit-created":
            return
        transaction_head = git_head(vault)
        tree = git_bytes(vault, "rev-parse", f"{transaction_head}^{{tree}}").decode(
            "ascii"
        ).strip()
        newer_head = git_bytes(
            vault,
            "commit-tree",
            tree,
            "-p",
            transaction_head,
            "-m",
            "concurrent head",
        ).decode("ascii").strip()
        git_bytes(vault, "update-ref", "HEAD", newer_head, transaction_head)

    monkeypatch.setattr(transaction, "_checkpoint", advance_head_without_failing)

    with pytest.raises(transaction.GitTransactionRecoveryError) as raised:
        transaction.execute_transaction(vault, plan)

    assert raised.value.paths == ("HEAD",)
    assert git_head(vault) == newer_head
    assert source.read_bytes() == b"reviewed\n"
    assert destination.exists() is False
    assert proposal.read_bytes() == b"proposal\n"
    assert git_head(vault) != start_head


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


@pytest.mark.parametrize("proposal_git_state", ("staged", "tracked"))
def test_owned_proposal_must_be_absent_from_real_index_and_head(
    tmp_path, proposal_git_state
):
    vault = _vault(tmp_path)
    plan = _approval_plan()
    proposal = _write_proposal(vault)
    proposal_relative = proposal.relative_to(vault).as_posix()
    git_bytes(vault, "add", proposal_relative)
    if proposal_git_state == "tracked":
        git_bytes(vault, "commit", "-q", "-m", "fixture: tracked proposal")
    start_head = git_head(vault)
    status_before = git_status_bytes(vault)
    index_before = git_index_entries(vault)

    with pytest.raises(ReviewedStateConflict):
        transaction.execute_transaction(vault, plan)

    assert git_head(vault) == start_head
    assert git_status_bytes(vault) == status_before
    assert git_index_entries(vault) == index_before
    assert proposal.read_bytes() == b"proposal\n"
    assert (vault / plan.commit_paths[0]).read_bytes() == b"reviewed\n"
    assert (vault / plan.commit_paths[1]).exists() is False


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


def test_forward_index_sync_preserves_concurrent_reviewed_index_update(
    tmp_path, monkeypatch
):
    vault, plan, index_directory, before = _prepared_transaction(
        tmp_path, monkeypatch
    )
    destination = plan.commit_paths[1]
    concurrent_oid = _write_blob(vault, b"concurrent pre-sync index update\n")

    def replace_reviewed_index_before_sync(name: str) -> None:
        if name != "commit-verified":
            return
        git_bytes(
            vault,
            "update-index",
            "--add",
            "--cacheinfo",
            "100644",
            concurrent_oid,
            destination,
        )

    monkeypatch.setattr(
        transaction, "_checkpoint", replace_reviewed_index_before_sync
    )

    with pytest.raises(transaction.GitTransactionRecoveryError) as raised:
        transaction.execute_transaction(vault, plan)

    assert raised.value.paths == (destination,)
    assert git_head(vault) == before["head"]
    assert concurrent_oid.encode("ascii") in b"\0".join(
        _reviewed_index_entries(vault, plan.commit_paths)
    )
    current = _complete_state(vault, plan, index_directory)
    for key in ("unrelated_index", "unrelated_status", "temporary_indexes"):
        assert current[key] == before[key]
    assert (vault / plan.commit_paths[0]).read_bytes() == b"reviewed\n"
    assert (vault / destination).exists() is False
    assert (vault / plan.owned_changes[0].path).read_bytes() == b"proposal\n"
    assert list(index_directory.iterdir()) == []


def test_forward_index_sync_holds_real_index_lock_across_compare_and_replace(
    tmp_path, monkeypatch
):
    vault, plan, index_directory, before = _prepared_transaction(
        tmp_path, monkeypatch
    )
    destination = plan.commit_paths[1]
    concurrent_oid = _write_blob(vault, b"racing forward index writer\n")
    index_lock = Path(f"{transaction._real_index_path(vault)}.lock")
    original_git = transaction._git
    concurrent_attempt: subprocess.CompletedProcess[bytes] | None = None

    def race_forward_sync(vault_arg, *args, env=None, check=True):
        nonlocal concurrent_attempt
        at_locked_compare = (
            args[0] == "ls-files" and env is None and index_lock.exists()
        )
        at_unlocked_legacy_sync = args[0] == "reset" and "--" in args
        if (
            concurrent_attempt is None
            and (at_locked_compare or at_unlocked_legacy_sync)
        ):
            concurrent_attempt = subprocess.run(
                [
                    "git",
                    "update-index",
                    "--add",
                    "--cacheinfo",
                    "100644",
                    concurrent_oid,
                    destination,
                ],
                cwd=vault,
                check=False,
                capture_output=True,
            )
        return original_git(vault_arg, *args, env=env, check=check)

    monkeypatch.setattr(transaction, "_git", race_forward_sync)

    result = transaction.execute_transaction(vault, plan)

    assert result.commit_oid == git_head(vault)
    assert concurrent_attempt is not None
    assert concurrent_attempt.returncode != 0
    assert concurrent_oid.encode("ascii") not in b"\0".join(
        _reviewed_index_entries(vault, plan.commit_paths)
    )
    assert _unrelated_index_entries(vault, plan.commit_paths) == before[
        "unrelated_index"
    ]
    assert list(index_directory.iterdir()) == []


def test_forward_index_replace_then_exception_restores_exact_starting_state(
    tmp_path, monkeypatch
):
    vault = _vault(tmp_path)
    plan = _approval_plan()
    _write_proposal(vault)
    conflict = "unrelated-conflict.md"
    (vault / conflict).write_bytes(b"worktree remains exact\n")
    git_bytes(vault, "add", conflict)
    git_bytes(vault, "commit", "-q", "-m", "fixture: unmerged index base")
    base_oid = _write_blob(vault, b"base stage\n")
    ours_oid = _write_blob(vault, b"ours stage\n")
    theirs_oid = _write_blob(vault, b"theirs stage\n")
    _set_unmerged_index(vault, conflict, (base_oid, ours_oid, theirs_oid))
    index_directory = tmp_path.parent / f"{tmp_path.name}-replace-indexes"
    index_directory.mkdir()
    monkeypatch.setattr(transaction.tempfile, "tempdir", os.fspath(index_directory))
    before = _complete_state(vault, plan, index_directory)
    starting_index = git_index_entries(vault)
    assert all(
        f" {stage}\t{conflict}".encode("ascii") in starting_index
        for stage in (1, 2, 3)
    )
    index_path = transaction._real_index_path(vault)
    index_lock = Path(f"{index_path}.lock")
    original_replace = transaction.os.replace
    replace_completed = False

    def fail_after_real_index_replace(source, destination, *args, **kwargs):
        nonlocal replace_completed
        result = original_replace(source, destination, *args, **kwargs)
        if (
            not replace_completed
            and Path(source) == index_lock
            and Path(destination) == index_path
        ):
            replace_completed = True
            raise OSError("injected exception after real index replace")
        return result

    monkeypatch.setattr(transaction.os, "replace", fail_after_real_index_replace)

    with pytest.raises(transaction.GitTransactionFailure) as raised:
        transaction.execute_transaction(vault, plan)

    assert type(raised.value) is transaction.GitTransactionFailure
    assert str(raised.value) == "approval transaction failed and was rolled back"
    assert _exception_chain_contains(raised.value, OSError)
    assert replace_completed is True
    assert git_index_entries(vault) == starting_index
    assert _complete_state(vault, plan, index_directory) == before
    assert index_lock.exists() is False
    with transaction._approval_lock(vault):
        pass


def test_recovery_preserves_concurrent_reviewed_index_replacement(
    tmp_path, monkeypatch
):
    vault, plan, index_directory, before = _prepared_transaction(
        tmp_path, monkeypatch
    )
    destination = plan.commit_paths[1]
    concurrent_oid = _write_blob(vault, b"concurrent index replacement\n")

    def replace_reviewed_index_after_sync(name: str) -> None:
        if name != "real-index-synchronized":
            return
        git_bytes(
            vault,
            "update-index",
            "--add",
            "--cacheinfo",
            "100644",
            concurrent_oid,
            destination,
        )
        raise OSError("injected concurrent reviewed-index replacement")

    monkeypatch.setattr(
        transaction, "_checkpoint", replace_reviewed_index_after_sync
    )

    with pytest.raises(transaction.GitTransactionRecoveryError) as raised:
        transaction.execute_transaction(vault, plan)

    assert raised.value.paths == (destination,)
    assert git_head(vault) == before["head"]
    assert concurrent_oid.encode("ascii") in b"\0".join(
        _reviewed_index_entries(vault, plan.commit_paths)
    )
    current = _complete_state(vault, plan, index_directory)
    for key in ("unrelated_index", "unrelated_status", "temporary_indexes"):
        assert current[key] == before[key]
    assert (vault / "staged.bin").read_bytes() == b"staged exact\x00\xff\n"
    assert (vault / "unstaged.bin").read_bytes() == b"unstaged exact\x00\xfe\n"
    assert (vault / "untracked.bin").read_bytes() == b"untracked exact\x00\xfd\n"
    assert list(index_directory.iterdir()) == []


def test_recovery_holds_real_index_lock_across_compare_and_restore(
    tmp_path, monkeypatch
):
    vault, plan, index_directory, before = _prepared_transaction(
        tmp_path, monkeypatch
    )
    destination = plan.commit_paths[1]
    concurrent_oid = _write_blob(vault, b"racing index writer\n")
    original_git = transaction._git
    rollback_started = False
    concurrent_attempt: subprocess.CompletedProcess[bytes] | None = None

    def fail_after_index_sync(name: str) -> None:
        nonlocal rollback_started
        if name == "real-index-synchronized":
            rollback_started = True
            raise OSError("injected post-index-sync failure")

    def race_restore(vault_arg, *args, env=None, check=True):
        nonlocal concurrent_attempt
        if rollback_started and concurrent_attempt is None and args[0] == "update-index":
            concurrent_attempt = subprocess.run(
                [
                    "git",
                    "update-index",
                    "--add",
                    "--cacheinfo",
                    "100644",
                    concurrent_oid,
                    destination,
                ],
                cwd=vault,
                check=False,
                capture_output=True,
            )
        return original_git(vault_arg, *args, env=env, check=check)

    monkeypatch.setattr(transaction, "_checkpoint", fail_after_index_sync)
    monkeypatch.setattr(transaction, "_git", race_restore)

    with pytest.raises(transaction.GitTransactionFailure) as raised:
        transaction.execute_transaction(vault, plan)

    assert type(raised.value) is transaction.GitTransactionFailure
    assert concurrent_attempt is not None
    assert concurrent_attempt.returncode != 0
    assert _complete_state(vault, plan, index_directory) == before


def test_unmerged_unrelated_stage_mutation_is_detected_during_recovery(
    tmp_path, monkeypatch
):
    vault = _vault(tmp_path)
    plan = _approval_plan()
    proposal = _write_proposal(vault)
    conflict = "unrelated-conflict.md"
    (vault / conflict).write_bytes(b"worktree stays constant\n")
    git_bytes(vault, "add", conflict)
    git_bytes(vault, "commit", "-q", "-m", "fixture: conflict base")
    base_oid = _write_blob(vault, b"base\n")
    ours_oid = _write_blob(vault, b"ours\n")
    theirs_oid = _write_blob(vault, b"theirs\n")
    replacement_oid = _write_blob(vault, b"replacement ours\n")
    _set_unmerged_index(vault, conflict, (base_oid, ours_oid, theirs_oid))
    start_head = git_head(vault)

    def mutate_only_stage_two(name: str) -> None:
        if name != "filesystem-applied":
            return
        _set_unmerged_index(
            vault, conflict, (base_oid, replacement_oid, theirs_oid)
        )
        raise OSError("injected unmerged stage mutation")

    monkeypatch.setattr(transaction, "_checkpoint", mutate_only_stage_two)

    with pytest.raises(transaction.GitTransactionRecoveryError) as raised:
        transaction.execute_transaction(vault, plan)

    assert raised.value.paths == (conflict,)
    assert git_head(vault) == start_head
    assert replacement_oid.encode("ascii") in git_index_entries(vault)
    assert (vault / conflict).read_bytes() == b"worktree stays constant\n"
    assert (vault / plan.commit_paths[0]).read_bytes() == b"reviewed\n"
    assert (vault / plan.commit_paths[1]).exists() is False
    assert proposal.read_bytes() == b"proposal\n"


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
    assert str(raised.value) == "approval transaction committed but cleanup failed"
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


@pytest.mark.parametrize("failure", ("unlock", "close"))
def test_lock_teardown_failure_after_commit_carries_structured_result(
    tmp_path, monkeypatch, failure
):
    vault = _vault(tmp_path)
    plan = _approval_plan()
    _write_proposal(vault)
    start_head = git_head(vault)
    lock_descriptors = _inject_lock_teardown_failure(monkeypatch, failure)

    with pytest.raises(transaction.GitTransactionCommittedError) as raised:
        transaction.execute_transaction(vault, plan)

    assert raised.value.result == transaction.TransactionResult(
        git_head(vault), tuple(sorted(plan.commit_paths))
    )
    assert raised.value.commit_oid == git_head(vault)
    assert git_head(vault) != start_head
    assert isinstance(raised.value.cleanup_error, OSError)
    assert failure in str(raised.value.cleanup_error)
    assert raised.value.__cause__ is raised.value.cleanup_error
    assert lock_descriptors
    for descriptor in lock_descriptors:
        with pytest.raises(OSError):
            os.fstat(descriptor)


def test_lock_teardown_failure_preserves_original_transaction_failure(
    tmp_path, monkeypatch
):
    vault = _vault(tmp_path)
    plan = _approval_plan()
    _write_proposal(vault)
    start_head = git_head(vault)
    lock_descriptors = _inject_lock_teardown_failure(monkeypatch, "unlock")

    def fail_before_commit(name: str) -> None:
        if name == "filesystem-applied":
            raise OSError("injected transaction body failure")

    monkeypatch.setattr(transaction, "_checkpoint", fail_before_commit)

    with pytest.raises(transaction.GitTransactionFailure) as raised:
        transaction.execute_transaction(vault, plan)

    assert type(raised.value) is transaction.GitTransactionFailure
    assert str(raised.value) == "approval transaction failed and was rolled back"
    assert isinstance(raised.value.__cause__, OSError)
    assert str(raised.value.__cause__) == "injected transaction body failure"
    assert git_head(vault) == start_head
    assert lock_descriptors
    for descriptor in lock_descriptors:
        with pytest.raises(OSError):
            os.fstat(descriptor)


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


# --- S7 Task 3: conditional removal for reject -------------------------------


def _conditional_vault(tmp_path):
    from tests.conftest import git_vault

    vault = git_vault(tmp_path, {"tracked.md": "tracked\n"})
    leaf = vault / "outbox-record.yaml"
    leaf.write_bytes(b"id: probe\nvalue: first\n")
    return vault, leaf


def test_conditional_removal_removes_only_the_expected_state(tmp_path):
    from app.git_transaction import capture_path_state, remove_path_if_unchanged
    from tests.conftest import git_count_commits, git_head

    vault, leaf = _conditional_vault(tmp_path)
    head_before, commits_before = git_head(vault), git_count_commits(vault)

    expected = capture_path_state(vault, "outbox-record.yaml")
    remove_path_if_unchanged(vault, "outbox-record.yaml", expected)

    assert not leaf.exists()
    # A removal, not a commit: reject never enters Git history.
    assert git_head(vault) == head_before
    assert git_count_commits(vault) == commits_before


def test_conditional_removal_refuses_a_rewritten_leaf(tmp_path):
    from app.git_transaction import (
        ReviewedStateChanged,
        capture_path_state,
        remove_path_if_unchanged,
    )

    vault, leaf = _conditional_vault(tmp_path)
    expected = capture_path_state(vault, "outbox-record.yaml")
    leaf.write_bytes(b"id: probe\nvalue: second\n")

    with pytest.raises(ReviewedStateChanged):
        remove_path_if_unchanged(vault, "outbox-record.yaml", expected)

    assert leaf.read_bytes() == b"id: probe\nvalue: second\n"


def test_conditional_removal_refuses_a_leaf_swapped_for_a_symlink(tmp_path):
    from app.git_transaction import (
        ReviewedStateConflict,
        capture_path_state,
        remove_path_if_unchanged,
    )

    vault, leaf = _conditional_vault(tmp_path)
    expected = capture_path_state(vault, "outbox-record.yaml")
    outside = tmp_path / "outside.yaml"
    outside.write_bytes(expected.contents)
    leaf.unlink()
    leaf.symlink_to(outside)

    with pytest.raises(ReviewedStateConflict):
        remove_path_if_unchanged(vault, "outbox-record.yaml", expected)

    # The redirection target is never followed and never unlinked.
    assert outside.exists()
    assert leaf.is_symlink()


def test_conditional_removal_refuses_a_vanished_leaf(tmp_path):
    from app.git_transaction import (
        ReviewedStateChanged,
        capture_path_state,
        remove_path_if_unchanged,
    )

    vault, leaf = _conditional_vault(tmp_path)
    expected = capture_path_state(vault, "outbox-record.yaml")
    leaf.unlink()

    with pytest.raises(ReviewedStateChanged):
        remove_path_if_unchanged(vault, "outbox-record.yaml", expected)


def test_conditional_removal_refuses_an_absent_expected_state(tmp_path):
    from app.git_transaction import PathState, remove_path_if_unchanged

    vault, _leaf = _conditional_vault(tmp_path)

    # Not an arbitrary-delete utility: without reviewed bytes there is
    # nothing to have reviewed.
    with pytest.raises(ValueError):
        remove_path_if_unchanged(vault, "outbox-record.yaml", PathState.absent())


@pytest.mark.parametrize(
    "unsafe", ["/absolute.yaml", "../escape.yaml", ".git/config", "", "a//b.yaml"]
)
def test_conditional_removal_refuses_an_unsafe_path(tmp_path, unsafe):
    from app.git_transaction import (
        InvalidTransactionPath,
        PathState,
        remove_path_if_unchanged,
    )

    vault, _leaf = _conditional_vault(tmp_path)

    with pytest.raises(InvalidTransactionPath):
        remove_path_if_unchanged(
            vault, unsafe, PathState.regular(b"probe", 0o644)
        )


def test_conditional_removal_leaves_unrelated_git_state_untouched(tmp_path):
    from app.git_transaction import capture_path_state, remove_path_if_unchanged
    from tests.conftest import git_cached_diff, git_status_bytes, git_worktree_diff

    vault, leaf = _conditional_vault(tmp_path)
    (vault / "tracked.md").write_text("locally edited\n", encoding="utf-8")
    worktree_before = git_worktree_diff(vault)
    cached_before = git_cached_diff(vault)

    expected = capture_path_state(vault, "outbox-record.yaml")
    remove_path_if_unchanged(vault, "outbox-record.yaml", expected)

    assert not leaf.exists()
    assert git_worktree_diff(vault) == worktree_before
    assert git_cached_diff(vault) == cached_before
    assert b"outbox-record.yaml" not in git_status_bytes(vault)


# --- S7 Task 3 review: the compare-to-unlink gap -----------------------------


def test_conditional_removal_never_destroys_a_replacement_swapped_after_comparison(
    tmp_path,
):
    """P1 (review): comparing a name and then unlinking that name are two
    operations, and `os.unlink` destroys whatever holds the name when it
    runs — not what was compared.

    Checking the parcel's label and then looking away before destroying it
    lets someone swap the parcel in the gap. The removal must act on the
    reviewed file itself, never on whatever currently answers to its name.
    """
    import app.git_transaction as gt
    from app.git_transaction import (
        ReviewedStateConflict,
        capture_path_state,
        remove_path_if_unchanged,
    )

    vault, leaf = _conditional_vault(tmp_path)
    reviewed = b"id: probe\nvalue: first\n"
    assert leaf.read_bytes() == reviewed
    expected = capture_path_state(vault, "outbox-record.yaml")

    replacement = b"id: probe\nvalue: REPLACEMENT-NOBODY-REVIEWED\n"
    real_capture = gt._capture_leaf_state
    swapped = []

    def capture_then_swap(dir_fd, name):
        state = real_capture(dir_fd, name)
        if not swapped and name == "outbox-record.yaml":
            swapped.append(True)
            leaf.write_bytes(replacement)
        return state

    gt._capture_leaf_state = capture_then_swap
    try:
        with pytest.raises(ReviewedStateConflict):
            remove_path_if_unchanged(vault, "outbox-record.yaml", expected)
    finally:
        gt._capture_leaf_state = real_capture

    assert swapped, "the probe never swapped the file"
    assert leaf.exists(), "the unreviewed replacement was destroyed"
    assert leaf.read_bytes() == replacement


def test_conditional_removal_leaves_no_orphan_after_refusing_a_replacement(tmp_path):
    """Whatever mechanism refuses the swap must not strand the file under
    some other name: the outbox is globbed, and an orphan there is a record
    nobody can see, review or clear."""
    import app.git_transaction as gt
    from app.git_transaction import (
        ReviewedStateConflict,
        capture_path_state,
        remove_path_if_unchanged,
    )

    vault, leaf = _conditional_vault(tmp_path)
    expected = capture_path_state(vault, "outbox-record.yaml")
    before = {p.name for p in vault.iterdir()}

    real_capture = gt._capture_leaf_state
    swapped = []

    def capture_then_swap(dir_fd, name):
        state = real_capture(dir_fd, name)
        if not swapped and name == "outbox-record.yaml":
            swapped.append(True)
            leaf.write_bytes(b"id: probe\nvalue: replacement\n")
        return state

    gt._capture_leaf_state = capture_then_swap
    try:
        with pytest.raises(ReviewedStateConflict):
            remove_path_if_unchanged(vault, "outbox-record.yaml", expected)
    finally:
        gt._capture_leaf_state = real_capture

    assert {p.name for p in vault.iterdir()} == before


def test_conditional_removal_reports_a_completed_removal_when_cleanup_fails(tmp_path):
    """P1 (review): the removal succeeded, so the operator must not be told
    that nothing was changed. `execute_transaction` already makes this
    distinction for a commit; a completed reject deserves the same honesty."""
    import app.git_transaction as gt
    from app.console_errors import describe
    from app.git_transaction import capture_path_state, remove_path_if_unchanged

    vault, leaf = _conditional_vault(tmp_path)
    expected = capture_path_state(vault, "outbox-record.yaml")

    real_flock = gt.fcntl.flock

    def failing_unlock(descriptor, operation):
        if operation == gt.fcntl.LOCK_UN:
            raise OSError(5, "simulated unlock failure")
        return real_flock(descriptor, operation)

    gt.fcntl.flock = failing_unlock
    try:
        with pytest.raises(gt.GitTransactionError) as raised:
            remove_path_if_unchanged(vault, "outbox-record.yaml", expected)
    finally:
        gt.fcntl.flock = real_flock

    assert not leaf.exists(), "the removal did happen"
    outcome = describe(raised.value)
    # It must not claim nothing changed, must not invite a retry of an
    # action that already took effect, and must not shrug: this is a known,
    # handled condition, so "an unexpected error was not handled" would be
    # its own kind of untruth.
    assert "Nothing was changed" not in outcome.message
    assert "rolled back" not in outcome.message
    assert outcome.retry == "stop"
    assert outcome.committed != "no"
    assert outcome.code != "E-UNKNOWN"
    assert "unexpected" not in outcome.message.lower()
    # The operator is told plainly that the discard did happen.
    assert "discarded" in outcome.message.lower()


def test_conditional_removal_cleanup_failure_before_removal_still_reports_no_change(
    tmp_path,
):
    """The mirror case: if the removal never happened, the operator must
    still be told nothing changed."""
    import app.git_transaction as gt
    from app.console_errors import describe
    from app.git_transaction import PathState, capture_path_state, remove_path_if_unchanged

    vault, leaf = _conditional_vault(tmp_path)
    expected = capture_path_state(vault, "outbox-record.yaml")
    leaf.write_bytes(b"id: probe\nvalue: changed before the call\n")
    survived = leaf.read_bytes()

    real_flock = gt.fcntl.flock

    def failing_unlock(descriptor, operation):
        if operation == gt.fcntl.LOCK_UN:
            raise OSError(5, "simulated unlock failure")
        return real_flock(descriptor, operation)

    gt.fcntl.flock = failing_unlock
    try:
        with pytest.raises(gt.GitTransactionError) as raised:
            remove_path_if_unchanged(vault, "outbox-record.yaml", expected)
    finally:
        gt.fcntl.flock = real_flock

    assert leaf.read_bytes() == survived
    assert describe(raised.value).committed == "no"


# --- S7 Task 3 review, round 2: the claim name is not a hiding place --------


def test_conditional_removal_never_displaces_a_file_already_at_the_claim_name(
    tmp_path,
):
    """P1 (review): reserving the private name must fail rather than take
    over a name in use. `os.rename` overwrites its destination silently, so
    a claim name that collides destroys whatever was there — unrelated
    state the removal was never reviewed against."""
    import app.git_transaction as gt
    from app.git_transaction import capture_path_state, remove_path_if_unchanged

    vault, leaf = _conditional_vault(tmp_path)
    expected = capture_path_state(vault, "outbox-record.yaml")

    # Force every reservation attempt onto one predictable name, and put an
    # unrelated file there first.
    real_token_hex = gt.secrets.token_hex
    gt.secrets.token_hex = lambda size: "cafe" * 6
    victim = vault / (".oneos-remove-" + "cafe" * 6)
    victim.write_bytes(b"UNRELATED PRE-EXISTING STATE\n")

    try:
        with pytest.raises(gt.GitTransactionError):
            remove_path_if_unchanged(vault, "outbox-record.yaml", expected)
    finally:
        gt.secrets.token_hex = real_token_hex

    assert victim.exists(), "an unrelated file at the claim name was destroyed"
    assert victim.read_bytes() == b"UNRELATED PRE-EXISTING STATE\n"
    # The reviewed record is left where it was, not stranded.
    assert leaf.exists()
    assert leaf.read_bytes() == expected.contents


def test_conditional_removal_refuses_a_rewrite_landing_on_the_claimed_file(tmp_path):
    """P1 (review): moving the file to a private name must not merely move
    the compare-then-destroy race along with it.

    The verification and the final gate both read through the descriptor
    holding the claimed file, so a rewrite that lands after the first check
    is still seen — the removal refuses instead of destroying bytes nobody
    reviewed.
    """
    import app.git_transaction as gt
    from app.git_transaction import (
        ReviewedStateConflict,
        capture_path_state,
        remove_path_if_unchanged,
    )

    vault, leaf = _conditional_vault(tmp_path)
    reviewed = leaf.read_bytes()
    expected = capture_path_state(vault, "outbox-record.yaml")

    replacement = b"REPLACEMENT-NOBODY-REVIEWED\n"
    real_held = gt._held_state
    swapped = []

    def verify_then_rewrite_the_claim(descriptor):
        state = real_held(descriptor)
        if not swapped:
            swapped.append(True)
            claim = next(
                entry for entry in vault.iterdir()
                if entry.name.startswith(".oneos-remove-")
            )
            claim.write_bytes(replacement)
        return state

    gt._held_state = verify_then_rewrite_the_claim
    try:
        with pytest.raises(ReviewedStateConflict):
            remove_path_if_unchanged(vault, "outbox-record.yaml", expected)
    finally:
        gt._held_state = real_held

    assert swapped, "the probe never rewrote the claimed file"
    # Nothing was destroyed, and nothing is stranded under a private name.
    assert leaf.exists()
    assert leaf.read_bytes() == replacement
    assert not [e for e in vault.iterdir() if e.name.startswith(".oneos-remove-")]
    assert reviewed != replacement


def test_conditional_removal_refuses_when_the_claim_name_is_rebound(tmp_path):
    """The other half: the name is repointed at a different inode.

    The descriptor still holds the reviewed file, so the mismatch is
    visible. The planted file was never verified by this call, so it is
    left exactly as found — an oddly named leftover is a far smaller harm
    than deleting bytes nobody reviewed.
    """
    import app.git_transaction as gt
    from app.git_transaction import (
        ReviewedStateConflict,
        capture_path_state,
        remove_path_if_unchanged,
    )

    vault, leaf = _conditional_vault(tmp_path)
    expected = capture_path_state(vault, "outbox-record.yaml")

    real_held = gt._held_state
    swapped = []

    def verify_then_rebind_the_claim(descriptor):
        state = real_held(descriptor)
        if not swapped:
            swapped.append(True)
            claim = next(
                entry for entry in vault.iterdir()
                if entry.name.startswith(".oneos-remove-")
            )
            planted = vault / "planted.yaml"
            planted.write_bytes(b"PLANTED\n")
            claim.unlink()
            planted.rename(claim)          # a different inode, same name
        return state

    gt._held_state = verify_then_rebind_the_claim
    try:
        with pytest.raises(ReviewedStateConflict):
            remove_path_if_unchanged(vault, "outbox-record.yaml", expected)
    finally:
        gt._held_state = real_held

    assert swapped, "the probe never rebound the claim"
    leftovers = [e for e in vault.iterdir() if e.name.startswith(".oneos-remove-")]
    assert len(leftovers) == 1
    assert leftovers[0].read_bytes() == b"PLANTED\n", (
        "an unverified planted file was destroyed"
    )


def test_discarding_a_reservation_never_deletes_content(tmp_path):
    """The reservation-cleanup path is only reached before any rename, so
    it should only ever see this call's own empty sentinel. It refuses to
    delete anything else regardless — a helper that can delete content is
    one refactor away from being used where it must not be."""
    import app.git_transaction as gt

    vault, _leaf = _conditional_vault(tmp_path)
    holding_bytes = vault / ".oneos-remove-holding-bytes"
    holding_bytes.write_bytes(b"content\n")
    a_directory = vault / ".oneos-remove-a-directory"
    a_directory.mkdir()
    empty_sentinel = vault / ".oneos-remove-empty"
    empty_sentinel.write_bytes(b"")

    descriptor = os.open(vault, os.O_RDONLY)
    try:
        gt._discard_private_name(descriptor, holding_bytes.name)
        gt._discard_private_name(descriptor, a_directory.name)
        gt._discard_private_name(descriptor, "never-existed")
        gt._discard_private_name(descriptor, empty_sentinel.name)
    finally:
        os.close(descriptor)

    assert holding_bytes.exists() and holding_bytes.read_bytes() == b"content\n"
    assert a_directory.is_dir()
    assert not empty_sentinel.exists()   # its own sentinel is cleaned up
