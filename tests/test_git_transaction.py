import fcntl
import os
import stat
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
from tests.conftest import git_entity_vault


def _vault(tmp_path: Path) -> Path:
    return git_entity_vault(
        tmp_path,
        ("synthetic",),
        {
            "synthetic/00-inbox/active/item.md": "reviewed\n",
            "synthetic/11-library/active/.gitkeep": "",
        },
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
