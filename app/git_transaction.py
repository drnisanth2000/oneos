"""Immutable approval transaction contracts and vault-safety primitives."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import errno
import fcntl
import hashlib
import os
from pathlib import Path, PurePosixPath
import secrets
import stat
import subprocess
import tempfile
from typing import Iterator


class GitTransactionError(Exception):
    pass


class VaultBusyError(GitTransactionError):
    pass


class ReviewedStateConflict(GitTransactionError):
    pass


class GitTransactionFailure(GitTransactionError):
    pass


class GitTransactionRecoveryError(GitTransactionError):
    def __init__(self, paths: tuple[str, ...]) -> None:
        self.paths = tuple(sorted(paths))
        super().__init__("transaction recovery blocked: " + ", ".join(self.paths))


@dataclass(frozen=True)
class PathState:
    contents: bytes | None
    mode: int | None

    def __post_init__(self) -> None:
        if (self.contents is None) != (self.mode is None):
            raise ValueError("path state must be absent or a regular file")
        if self.mode is not None and (self.mode < 0 or self.mode > 0o7777):
            raise ValueError("file mode is invalid")

    @classmethod
    def absent(cls) -> PathState:
        return cls(None, None)

    @classmethod
    def regular(cls, contents: bytes, mode: int) -> PathState:
        return cls(bytes(contents), mode)


@dataclass(frozen=True)
class PathChange:
    path: str
    before: PathState
    after: PathState


@dataclass(frozen=True)
class TransactionPlan:
    message: str
    changes: tuple[PathChange, ...]
    commit_paths: tuple[str, ...]
    owned_changes: tuple[PathChange, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.message, str) or not self.message or "\n" in self.message:
            raise ValueError("commit message must be one non-empty line")
        if not isinstance(self.changes, tuple) or not self.changes:
            raise ValueError("transaction requires reviewed changes")
        if not isinstance(self.commit_paths, tuple) or not self.commit_paths:
            raise ValueError("transaction requires commit paths")
        if not isinstance(self.owned_changes, tuple):
            raise ValueError("owned changes must be a tuple")

        all_paths = tuple(change.path for change in self.changes + self.owned_changes)
        for value in all_paths + self.commit_paths:
            _validate_transaction_path(value)

        if len(set(all_paths)) != len(all_paths):
            raise ValueError("transaction paths must be duplicate-free")
        if len(set(self.commit_paths)) != len(self.commit_paths):
            raise ValueError("commit paths must be duplicate-free")
        if set(self.commit_paths) != {change.path for change in self.changes}:
            raise ValueError("commit paths must equal reviewed change paths")


@dataclass(frozen=True)
class TransactionResult:
    commit_oid: str
    changed_paths: tuple[str, ...]


@dataclass(frozen=True)
class _IndexEntry:
    path: str
    mode: str
    oid: str
    stage: int


@dataclass(frozen=True)
class _DirtyPathState:
    path: str
    status: str
    kind: str
    mode: int | None
    fingerprint: bytes | None


@dataclass(frozen=True)
class _UnrelatedState:
    index_entries: tuple[_IndexEntry, ...]
    dirty_paths: tuple[_DirtyPathState, ...]


def _validate_transaction_path(value: str) -> None:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError("transaction path is not lexical POSIX")
    path = PurePosixPath(value)
    if (
        not path.parts
        or path.is_absolute()
        or any(part in {"", ".", "..", ".git"} for part in path.parts)
    ):
        raise ValueError("transaction path is unsafe")
    if path.as_posix() != value:
        raise ValueError("transaction path is not canonical")


def capture_path_state(vault: Path, relative_path: str) -> PathState:
    """Return a regular leaf's exact state without following path redirects."""
    try:
        _validate_transaction_path(relative_path)
    except ValueError as exc:
        raise ReviewedStateConflict("reviewed path is unsafe") from exc

    root = Path(os.path.abspath(os.fspath(vault)))
    parts = PurePosixPath(relative_path).parts
    directory_descriptor = _open_checked_directory(root, "vault root")
    try:
        for part in parts[:-1]:
            next_descriptor = _open_checked_directory(
                part, "reviewed parent", dir_fd=directory_descriptor
            )
            os.close(directory_descriptor)
            directory_descriptor = next_descriptor

        try:
            leaf_stat = os.lstat(parts[-1], dir_fd=directory_descriptor)
        except FileNotFoundError:
            return PathState.absent()
        except OSError as exc:
            raise ReviewedStateConflict("reviewed path is unavailable") from exc
        if stat.S_ISLNK(leaf_stat.st_mode) or not stat.S_ISREG(leaf_stat.st_mode):
            raise ReviewedStateConflict("reviewed path is not a regular file")

        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(parts[-1], flags, dir_fd=directory_descriptor)
        except OSError as exc:
            raise ReviewedStateConflict("reviewed path could not be opened safely") from exc

        try:
            opened_stat = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened_stat.st_mode)
                or (opened_stat.st_dev, opened_stat.st_ino)
                != (leaf_stat.st_dev, leaf_stat.st_ino)
            ):
                raise ReviewedStateConflict("reviewed path changed while being captured")
            with os.fdopen(descriptor, "rb", closefd=True) as opened_file:
                descriptor = -1
                return PathState.regular(
                    opened_file.read(), stat.S_IMODE(opened_stat.st_mode)
                )
        finally:
            if descriptor >= 0:
                os.close(descriptor)
    finally:
        os.close(directory_descriptor)


def _open_checked_directory(
    path: str | Path, description: str, *, dir_fd: int | None = None
) -> int:
    try:
        checked_stat = os.lstat(path, dir_fd=dir_fd)
    except OSError as exc:
        raise ReviewedStateConflict(f"{description} is unavailable") from exc
    if stat.S_ISLNK(checked_stat.st_mode) or not stat.S_ISDIR(checked_stat.st_mode):
        raise ReviewedStateConflict(f"{description} is not a directory")

    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags, dir_fd=dir_fd)
    except OSError as exc:
        raise ReviewedStateConflict(f"{description} could not be opened safely") from exc
    try:
        opened_stat = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(opened_stat.st_mode)
            or (opened_stat.st_dev, opened_stat.st_ino)
            != (checked_stat.st_dev, checked_stat.st_ino)
        ):
            raise ReviewedStateConflict(f"{description} changed while being captured")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


@contextmanager
def _approval_lock(vault: Path) -> Iterator[None]:
    """Acquire the per-vault approval lock without waiting."""
    try:
        git_dir_output = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-dir"],
            cwd=vault,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise GitTransactionFailure("could not determine vault Git directory") from exc

    lock_path = Path(git_dir_output.strip()) / "oneos-approval.lock"
    try:
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    except OSError as exc:
        raise GitTransactionFailure("could not open approval lock") from exc

    locked = False
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN}:
                raise VaultBusyError("another approval is already running") from exc
            raise GitTransactionFailure("could not acquire approval lock") from exc
        yield
    finally:
        try:
            if locked:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def execute_transaction(vault: Path, plan: TransactionPlan) -> TransactionResult:
    """Commit exactly the reviewed paths while preserving unrelated Git state."""
    vault = Path(vault).resolve()
    with _approval_lock(vault):
        start_head = _git_text(vault, "rev-parse", "HEAD").strip()
        reviewed_index = _capture_reviewed_index(vault, plan.commit_paths)
        unrelated = _capture_unrelated_state(vault, plan)
        _require_expected_states(vault, plan)
        _require_reviewed_index_matches_head(vault, start_head, plan.commit_paths)
        return _execute_locked(vault, start_head, reviewed_index, unrelated, plan)


def _checkpoint(name: str) -> None:
    """Provide a deterministic failure-injection seam for transaction tests."""
    del name


def _execute_locked(
    vault: Path,
    start_head: str,
    reviewed_index: tuple[_IndexEntry, ...],
    unrelated: _UnrelatedState,
    plan: TransactionPlan,
) -> TransactionResult:
    applied_changes: list[PathChange] = []
    temporary_index: str | None = None
    commit_oid: str | None = None
    try:
        for change in plan.changes + plan.owned_changes:
            _apply_state(vault, change)
            applied_changes.append(change)
        _checkpoint("filesystem-applied")

        descriptor, temporary_index = tempfile.mkstemp(prefix="oneos-index-")
        os.close(descriptor)
        os.unlink(temporary_index)
        alternate_env = os.environ.copy()
        alternate_env["GIT_INDEX_FILE"] = temporary_index
        _git(vault, "read-tree", start_head, env=alternate_env)
        _checkpoint("alternate-index-ready")
        _stage_in_alternate_index(vault, plan.commit_paths, alternate_env)
        _checkpoint("reviewed-paths-staged")
        _git(vault, "commit", "-q", "-m", plan.message, env=alternate_env)
        commit_oid = _git_text(vault, "rev-parse", "HEAD").strip()
        _checkpoint("commit-created")
        _verify_commit(
            vault, commit_oid, start_head, plan.message, plan.commit_paths
        )
        _checkpoint("commit-verified")
        _sync_reviewed_index(vault, commit_oid, plan.commit_paths)
        _verify_reviewed_index_matches_head(vault, commit_oid, plan.commit_paths)
        _checkpoint("real-index-synchronized")
        _require_unrelated_state_unchanged(vault, unrelated, plan)
        return TransactionResult(commit_oid, tuple(sorted(plan.commit_paths)))
    except Exception as exc:
        blocked_paths = _rollback_transaction(
            vault,
            start_head,
            commit_oid,
            reviewed_index,
            unrelated,
            plan,
            applied_changes,
        )
        if blocked_paths:
            raise GitTransactionRecoveryError(blocked_paths) from exc
        raise GitTransactionFailure(
            "approval transaction failed and was rolled back"
        ) from exc
    finally:
        if temporary_index is not None:
            _remove_temporary_index(temporary_index)


def _rollback_transaction(
    vault: Path,
    start_head: str,
    commit_oid: str | None,
    reviewed_index: tuple[_IndexEntry, ...],
    unrelated: _UnrelatedState,
    plan: TransactionPlan,
    applied_changes: list[PathChange],
) -> tuple[str, ...]:
    blocked_paths: set[str] = set()

    for change in reversed(applied_changes):
        try:
            current = capture_path_state(vault, change.path)
        except (OSError, GitTransactionError):
            blocked_paths.add(change.path)
            continue
        if current != change.after:
            blocked_paths.add(change.path)
            continue
        try:
            _apply_state(vault, PathChange(change.path, change.after, change.before))
        except (OSError, GitTransactionError):
            blocked_paths.add(change.path)

    original_entries = {entry.path: entry for entry in reviewed_index}
    for path in plan.commit_paths:
        try:
            entry = original_entries.get(path)
            if entry is None:
                _git(vault, "update-index", "--force-remove", "--", path)
            else:
                _git(
                    vault,
                    "update-index",
                    "--add",
                    "--cacheinfo",
                    entry.mode,
                    entry.oid,
                    path,
                )
        except (OSError, GitTransactionError):
            blocked_paths.add(path)

    if commit_oid is not None:
        try:
            updated = _git(
                vault,
                "update-ref",
                "HEAD",
                start_head,
                commit_oid,
                check=False,
            )
        except (OSError, GitTransactionError):
            blocked_paths.add("HEAD")
        else:
            if updated.returncode:
                blocked_paths.add("HEAD")

    try:
        current_unrelated = _capture_unrelated_state(vault, plan)
    except (OSError, GitTransactionError):
        blocked_paths.update(entry.path for entry in unrelated.index_entries)
        blocked_paths.update(state.path for state in unrelated.dirty_paths)
    else:
        blocked_paths.update(_changed_unrelated_paths(unrelated, current_unrelated))

    return tuple(sorted(blocked_paths))


def _changed_unrelated_paths(
    expected: _UnrelatedState, current: _UnrelatedState
) -> tuple[str, ...]:
    expected_index = {entry.path: entry for entry in expected.index_entries}
    current_index = {entry.path: entry for entry in current.index_entries}
    expected_dirty = {state.path: state for state in expected.dirty_paths}
    current_dirty = {state.path: state for state in current.dirty_paths}
    changed = {
        path
        for path in expected_index.keys() | current_index.keys()
        if expected_index.get(path) != current_index.get(path)
    }
    changed.update(
        path
        for path in expected_dirty.keys() | current_dirty.keys()
        if expected_dirty.get(path) != current_dirty.get(path)
    )
    return tuple(sorted(changed))


def _remove_temporary_index(temporary_index: str) -> None:
    for path in (temporary_index, f"{temporary_index}.lock"):
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass


def _git(
    vault: Path,
    *args: str,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=vault,
            env=env,
            check=check,
            capture_output=True,
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or b"").decode("utf-8", "replace").strip()
        message = "Git transaction command failed"
        if detail:
            message = f"{message}: {detail}"
        raise GitTransactionFailure(message) from exc
    except OSError as exc:
        raise GitTransactionFailure("could not run Git transaction command") from exc
    return completed


def _git_text(
    vault: Path, *args: str, env: dict[str, str] | None = None
) -> str:
    return _git(vault, *args, env=env).stdout.decode("utf-8", "strict")


def _parse_index_entries(output: bytes) -> tuple[_IndexEntry, ...]:
    entries = []
    for record in output.split(b"\0"):
        if not record:
            continue
        try:
            metadata, path_bytes = record.split(b"\t", 1)
            mode, oid, stage = metadata.decode("ascii").split(" ")
        except (UnicodeDecodeError, ValueError) as exc:
            raise GitTransactionFailure("Git returned malformed index state") from exc
        entries.append(_IndexEntry(os.fsdecode(path_bytes), mode, oid, int(stage)))
    return tuple(sorted(entries, key=lambda entry: (entry.path, entry.stage)))


def _capture_reviewed_index(
    vault: Path, paths: tuple[str, ...]
) -> tuple[_IndexEntry, ...]:
    output = _git(vault, "ls-files", "--stage", "-z", "--", *paths).stdout
    return _parse_index_entries(output)


def _head_index_entries(
    vault: Path, revision: str, paths: tuple[str, ...]
) -> tuple[_IndexEntry, ...]:
    output = _git(vault, "ls-tree", "-r", "-z", revision, "--", *paths).stdout
    entries = []
    for record in output.split(b"\0"):
        if not record:
            continue
        try:
            metadata, path_bytes = record.split(b"\t", 1)
            mode, object_type, oid = metadata.decode("ascii").split(" ")
        except (UnicodeDecodeError, ValueError) as exc:
            raise GitTransactionFailure("Git returned malformed tree state") from exc
        if object_type != "blob":
            raise ReviewedStateConflict("reviewed index path is not a regular file")
        entries.append(_IndexEntry(os.fsdecode(path_bytes), mode, oid, 0))
    return tuple(sorted(entries, key=lambda entry: (entry.path, entry.stage)))


def _require_reviewed_index_matches_head(
    vault: Path, revision: str, paths: tuple[str, ...]
) -> None:
    if _capture_reviewed_index(vault, paths) != _head_index_entries(
        vault, revision, paths
    ):
        raise ReviewedStateConflict("reviewed path has an unexpected staged change")


def _verify_reviewed_index_matches_head(
    vault: Path, revision: str, paths: tuple[str, ...]
) -> None:
    if _capture_reviewed_index(vault, paths) != _head_index_entries(
        vault, revision, paths
    ):
        raise GitTransactionFailure("reviewed index does not match the transaction commit")


def _capture_unrelated_state(vault: Path, plan: TransactionPlan) -> _UnrelatedState:
    reviewed = set(plan.commit_paths)
    owned = {change.path for change in plan.owned_changes}
    all_entries = _parse_index_entries(
        _git(vault, "ls-files", "--stage", "-z").stdout
    )
    unrelated_entries = tuple(
        entry for entry in all_entries if entry.stage == 0 and entry.path not in reviewed
    )
    dirty_paths = _capture_dirty_paths(vault, reviewed | owned)
    return _UnrelatedState(unrelated_entries, dirty_paths)


def _capture_dirty_paths(
    vault: Path, excluded_paths: set[str]
) -> tuple[_DirtyPathState, ...]:
    output = _git(
        vault,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
        "--no-renames",
    ).stdout
    dirty = []
    for record in output.split(b"\0"):
        if not record:
            continue
        if len(record) < 4 or record[2:3] != b" ":
            raise GitTransactionFailure("Git returned malformed worktree status")
        try:
            status_code = record[:2].decode("ascii")
        except UnicodeDecodeError as exc:
            raise GitTransactionFailure("Git returned malformed worktree status") from exc
        path = os.fsdecode(record[3:])
        if path in excluded_paths:
            continue
        kind, mode, fingerprint = _fingerprint_path(vault / path)
        dirty.append(_DirtyPathState(path, status_code, kind, mode, fingerprint))
    return tuple(sorted(dirty, key=lambda state: state.path))


def _fingerprint_path(path: Path) -> tuple[str, int | None, bytes | None]:
    try:
        path_stat = os.lstat(path)
    except FileNotFoundError:
        return "absent", None, None
    except OSError as exc:
        raise GitTransactionFailure("could not fingerprint unrelated path") from exc

    mode = stat.S_IMODE(path_stat.st_mode)
    if stat.S_ISREG(path_stat.st_mode):
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags)
            with os.fdopen(descriptor, "rb", closefd=True) as opened_file:
                descriptor = -1
                digest = hashlib.sha256(opened_file.read()).digest()
        except OSError as exc:
            raise GitTransactionFailure("could not fingerprint unrelated file") from exc
        finally:
            if "descriptor" in locals() and descriptor >= 0:
                os.close(descriptor)
        return "regular", mode, digest
    if stat.S_ISLNK(path_stat.st_mode):
        try:
            return "symlink", mode, os.fsencode(os.readlink(path))
        except OSError as exc:
            raise GitTransactionFailure("could not fingerprint unrelated symlink") from exc
    if stat.S_ISDIR(path_stat.st_mode):
        return "directory", mode, None
    return "other", mode, None


def _require_expected_states(vault: Path, plan: TransactionPlan) -> None:
    for change in plan.changes + plan.owned_changes:
        if capture_path_state(vault, change.path) != change.before:
            raise ReviewedStateConflict(
                f"reviewed path does not match expected state: {change.path}"
            )


def _capture_leaf_state(directory_descriptor: int, leaf: str) -> PathState:
    try:
        leaf_stat = os.lstat(leaf, dir_fd=directory_descriptor)
    except FileNotFoundError:
        return PathState.absent()
    except OSError as exc:
        raise ReviewedStateConflict("reviewed path is unavailable") from exc
    if stat.S_ISLNK(leaf_stat.st_mode) or not stat.S_ISREG(leaf_stat.st_mode):
        raise ReviewedStateConflict("reviewed path is not a regular file")

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(leaf, flags, dir_fd=directory_descriptor)
    except OSError as exc:
        raise ReviewedStateConflict("reviewed path could not be opened safely") from exc
    try:
        opened_stat = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened_stat.st_mode)
            or (opened_stat.st_dev, opened_stat.st_ino)
            != (leaf_stat.st_dev, leaf_stat.st_ino)
        ):
            raise ReviewedStateConflict("reviewed path changed before mutation")
        with os.fdopen(descriptor, "rb", closefd=True) as opened_file:
            descriptor = -1
            return PathState.regular(
                opened_file.read(), stat.S_IMODE(opened_stat.st_mode)
            )
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _apply_state(vault: Path, change: PathChange) -> None:
    parts = PurePosixPath(change.path).parts
    directory_descriptor = _open_checked_directory(vault, "vault root")
    try:
        for part in parts[:-1]:
            next_descriptor = _open_checked_directory(
                part, "reviewed parent", dir_fd=directory_descriptor
            )
            os.close(directory_descriptor)
            directory_descriptor = next_descriptor

        leaf = parts[-1]
        if _capture_leaf_state(directory_descriptor, leaf) != change.before:
            raise ReviewedStateConflict(
                f"reviewed path changed before mutation: {change.path}"
            )
        if change.after.contents is None:
            if change.before.contents is not None:
                try:
                    os.unlink(leaf, dir_fd=directory_descriptor)
                except OSError as exc:
                    raise GitTransactionFailure("could not remove reviewed file") from exc
            return

        temporary_leaf = ""
        temporary_descriptor = -1
        try:
            for _ in range(100):
                temporary_leaf = f".oneos-write-{secrets.token_hex(12)}"
                try:
                    temporary_descriptor = os.open(
                        temporary_leaf,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                        0o600,
                        dir_fd=directory_descriptor,
                    )
                    break
                except FileExistsError:
                    continue
            else:
                raise GitTransactionFailure("could not allocate reviewed temporary file")

            with os.fdopen(temporary_descriptor, "wb", closefd=True) as temporary_file:
                temporary_descriptor = -1
                temporary_file.write(change.after.contents)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
                os.fchmod(temporary_file.fileno(), change.after.mode)
            os.replace(
                temporary_leaf,
                leaf,
                src_dir_fd=directory_descriptor,
                dst_dir_fd=directory_descriptor,
            )
            temporary_leaf = ""
        except GitTransactionError:
            raise
        except OSError as exc:
            raise GitTransactionFailure("could not write reviewed file") from exc
        finally:
            if temporary_descriptor >= 0:
                os.close(temporary_descriptor)
            if temporary_leaf:
                try:
                    os.unlink(temporary_leaf, dir_fd=directory_descriptor)
                except FileNotFoundError:
                    pass
    finally:
        os.close(directory_descriptor)


def _name_only(
    vault: Path, *args: str, env: dict[str, str] | None = None
) -> tuple[str, ...]:
    output = _git(vault, *args, env=env).stdout
    return tuple(os.fsdecode(path) for path in output.split(b"\0") if path)


def _stage_in_alternate_index(
    vault: Path, paths: tuple[str, ...], alternate_env: dict[str, str]
) -> None:
    _git(vault, "add", "--all", "--", *paths, env=alternate_env)
    staged = _name_only(
        vault,
        "diff",
        "--cached",
        "--name-only",
        "-z",
        env=alternate_env,
    )
    if tuple(sorted(staged)) != tuple(sorted(paths)):
        raise GitTransactionFailure("alternate index staged an unexpected path")


def _verify_commit(
    vault: Path,
    commit_oid: str,
    start_head: str,
    message: str,
    paths: tuple[str, ...],
) -> None:
    ancestry = _git_text(vault, "rev-list", "--parents", "-n", "1", commit_oid).split()
    if ancestry != [commit_oid, start_head]:
        raise GitTransactionFailure("transaction commit has unexpected ancestry")
    subject = _git_text(vault, "show", "-s", "--format=%s", commit_oid).rstrip("\n")
    if subject != message:
        raise GitTransactionFailure("transaction commit has unexpected subject")
    changed = _name_only(
        vault,
        "diff-tree",
        "--no-commit-id",
        "--no-renames",
        "--name-only",
        "-r",
        "-z",
        commit_oid,
    )
    if tuple(sorted(changed)) != tuple(sorted(paths)):
        raise GitTransactionFailure("transaction commit changed an unexpected path")


def _sync_reviewed_index(vault: Path, commit_oid: str, paths: tuple[str, ...]) -> None:
    _git(vault, "reset", "-q", commit_oid, "--", *paths)


def _require_unrelated_state_unchanged(
    vault: Path, expected: _UnrelatedState, plan: TransactionPlan
) -> None:
    current = _capture_unrelated_state(vault, plan)
    if current != expected:
        raise GitTransactionFailure("unrelated vault state changed during transaction")
