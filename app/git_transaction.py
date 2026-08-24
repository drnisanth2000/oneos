"""Immutable approval transaction contracts and vault-safety primitives."""

from __future__ import annotations

from contextlib import contextmanager
import ctypes
import ctypes.util
from dataclasses import dataclass
import errno
import platform
import sys
import fcntl
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import stat
import subprocess
import tempfile
from typing import Callable, Iterator


class GitTransactionError(Exception):
    pass


class VaultBusyError(GitTransactionError):
    pass


class ReviewedStateConflict(GitTransactionError):
    pass


class ReviewedPathIntegrityError(ReviewedStateConflict):
    pass


class ReviewedStateChanged(ReviewedStateConflict):
    pass


class ReviewedPathUnavailable(ReviewedStateConflict):
    pass


class InvalidTransactionPath(ReviewedStateConflict):
    pass


class GitTransactionFailure(GitTransactionError):
    pass


class GitTransactionCommittedError(GitTransactionError):
    def __init__(self, result: TransactionResult, cleanup_error: OSError) -> None:
        self.result = result
        self.commit_oid = result.commit_oid
        self.cleanup_error = cleanup_error
        super().__init__("approval transaction committed but cleanup failed")


class _ApprovalLockCleanupFailure(GitTransactionFailure):
    def __init__(self, cleanup_error: OSError) -> None:
        self.cleanup_error = cleanup_error
        super().__init__("approval lock cleanup failed")


class AtomicMoveUnavailable(GitTransactionError):
    """This kernel or filesystem has no atomic no-overwrite move.

    S7 fails closed here rather than degrading to an ordinary rename, which
    silently overwrites its destination (design §3, Amendment 1).
    """


class QuarantineRestorationBlocked(GitTransactionError):
    """A consumed record could not be returned to its own name.

    Both files survive — the record in quarantine and whatever took its
    name — and neither is deleted to tidy up. The state is indeterminate and
    must be reported as such, never as "nothing was changed".
    """

    def __init__(self, path: str) -> None:
        self.path = path
        super().__init__("reviewed record could not be restored to its name")


class QuarantineCleanupError(GitTransactionFailure):
    """The record was quarantined; only the cleanup afterwards failed.

    Distinct from `_ApprovalLockCleanupFailure`, which reaches the operator
    as "nothing was changed and it was rolled back". After a completed
    consumption that sentence is simply false.
    """

    def __init__(self, cleanup_error: OSError) -> None:
        self.cleanup_error = cleanup_error
        super().__init__("reviewed record was quarantined but cleanup failed")


class _ReviewedIndexOwnershipConflict(GitTransactionFailure):
    def __init__(self, paths: tuple[str, ...]) -> None:
        self.paths = tuple(sorted(paths))
        super().__init__("reviewed index changed before synchronization")


class GitTransactionRecoveryError(GitTransactionError):
    def __init__(
        self, paths: tuple[str, ...], *, temporary_index_cleanup_failed: bool = False
    ) -> None:
        self.paths = tuple(sorted(paths))
        self.temporary_index_cleanup_failed = temporary_index_cleanup_failed
        message = "transaction recovery blocked: " + ", ".join(self.paths)
        if temporary_index_cleanup_failed:
            message += "; temporary index cleanup failed"
        super().__init__(message)


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
    #: Checks that must hold **under the approval lock, immediately before
    #: any mutation**. A precondition whose truth depends on state this
    #: transaction does not own — a live reference count, say — cannot be
    #: evaluated before the lock: another approval can commit between the
    #: check and the lock, and the transaction's own expected states would
    #: still match. Each callable raises to refuse; none may mutate.
    preconditions: tuple[Callable[[], None], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.message, str) or not self.message or "\n" in self.message:
            raise ValueError("commit message must be one non-empty line")
        if not isinstance(self.changes, tuple) or not self.changes:
            raise ValueError("transaction requires reviewed changes")
        if not isinstance(self.commit_paths, tuple) or not self.commit_paths:
            raise ValueError("transaction requires commit paths")
        if not isinstance(self.preconditions, tuple):
            raise ValueError("preconditions must be a tuple")
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
        raise InvalidTransactionPath("reviewed path is unsafe") from exc

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
            raise ReviewedPathUnavailable("reviewed path is unavailable") from exc
        if stat.S_ISLNK(leaf_stat.st_mode) or not stat.S_ISREG(leaf_stat.st_mode):
            raise ReviewedPathIntegrityError("reviewed path is not a regular file")

        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(parts[-1], flags, dir_fd=directory_descriptor)
        except OSError as exc:
            raise _unsafe_open_failure(
                exc, "reviewed path could not be opened safely"
            ) from exc

        try:
            opened_stat = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened_stat.st_mode)
                or (opened_stat.st_dev, opened_stat.st_ino)
                != (leaf_stat.st_dev, leaf_stat.st_ino)
            ):
                raise ReviewedPathIntegrityError("reviewed path changed while being captured")
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


def _unsafe_open_failure(exc: OSError, message: str) -> ReviewedStateConflict:
    """Discriminate one open-time `OSError` into its truthful subtype.

    ELOOP (and EMLINK, the O_NOFOLLOW rejection on some BSDs) is a
    redirection finding; every other `OSError` is ordinary unavailability.
    """
    if exc.errno in {errno.ELOOP, errno.EMLINK}:
        return ReviewedPathIntegrityError(message)
    return ReviewedPathUnavailable(message)


def _open_checked_directory(
    path: str | Path, description: str, *, dir_fd: int | None = None
) -> int:
    try:
        checked_stat = os.lstat(path, dir_fd=dir_fd)
    except OSError as exc:
        raise ReviewedPathUnavailable(f"{description} is unavailable") from exc
    if stat.S_ISLNK(checked_stat.st_mode) or not stat.S_ISDIR(checked_stat.st_mode):
        raise ReviewedPathIntegrityError(f"{description} is not a directory")

    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags, dir_fd=dir_fd)
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.EMLINK, errno.ENOTDIR, errno.ENOENT}:
            # `lstat` just said this was a real directory, so it has changed
            # between then and now — swapped for a symlink, for a
            # non-directory, or removed. That is a redirection finding, not
            # an "unavailable" one, and the tier matters: the operator must
            # be told to inspect the vault, not to try again.
            raise ReviewedPathIntegrityError(
                f"{description} changed while being opened"
            ) from exc
        raise _unsafe_open_failure(
            exc, f"{description} could not be opened safely"
        ) from exc
    try:
        opened_stat = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(opened_stat.st_mode)
            or (opened_stat.st_dev, opened_stat.st_ino)
            != (checked_stat.st_dev, checked_stat.st_ino)
        ):
            raise ReviewedPathIntegrityError(f"{description} changed while being captured")
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
    primary_error: BaseException | None = None
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN}:
                raise VaultBusyError("another approval is already running") from exc
            raise GitTransactionFailure("could not acquire approval lock") from exc
        yield
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        cleanup_error: OSError | None = None
        try:
            if locked:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        except OSError as exc:
            cleanup_error = exc
        try:
            os.close(descriptor)
        except OSError as exc:
            if cleanup_error is None:
                cleanup_error = exc
            else:
                cleanup_error.add_note(f"approval lock close also failed: {exc}")
        if cleanup_error is not None:
            if primary_error is not None:
                primary_error.add_note(
                    f"approval lock cleanup also failed: {cleanup_error}"
                )
            else:
                raise _ApprovalLockCleanupFailure(cleanup_error) from cleanup_error


def execute_transaction(vault: Path, plan: TransactionPlan) -> TransactionResult:
    """Commit exactly the reviewed paths while preserving unrelated Git state."""
    vault = Path(vault).resolve()
    result: TransactionResult | None = None
    try:
        with _approval_lock(vault):
            start_head = _git_text(vault, "rev-parse", "HEAD").strip()
            reviewed_index = _capture_reviewed_index(vault, plan.commit_paths)
            _require_owned_paths_untracked(vault, start_head, plan.owned_changes)
            unrelated = _capture_unrelated_state(vault, plan)
            _require_expected_states(vault, plan)
            # Under the lock, before anything is applied: the last moment at
            # which a refusal still costs nothing.
            for precondition in plan.preconditions:
                precondition()
            _require_reviewed_index_matches_head(
                vault, start_head, plan.commit_paths
            )
            result = _execute_locked(
                vault, start_head, reviewed_index, unrelated, plan
            )
    except _ApprovalLockCleanupFailure as exc:
        if result is None:
            raise
        raise GitTransactionCommittedError(result, exc.cleanup_error) from exc.cleanup_error
    if result is None:
        raise GitTransactionFailure("approval transaction produced no result")
    return result


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
    applied_changes: list[tuple[PathChange, PathState]] = []
    quarantined: list[tuple[PathChange, str]] = []
    temporary_index: str | None = None
    commit_oid: str | None = None
    commit_created = False
    commit_output: bytes | None = None
    transaction_index: tuple[_IndexEntry, ...] | None = None
    result: TransactionResult | None = None
    transaction_error: GitTransactionError | None = None
    transaction_cause: Exception | None = None
    try:
        for change in plan.changes:
            _apply_state(
                vault,
                change,
                on_applied=lambda state, change=change: applied_changes.append(
                    (change, state)
                ),
            )
        # Amendment 1: owned changes are proposal records, and a proposal
        # record is consumed by moving it into quarantine — never unlinked.
        # A rollback moves it back under its own name.
        for change in plan.owned_changes:
            quarantined.append(
                (
                    change,
                    quarantine_path_if_unchanged(vault, change.path, change.before),
                )
            )
        _checkpoint("filesystem-applied")

        descriptor, temporary_index = tempfile.mkstemp(prefix="oneos-index-")
        os.close(descriptor)
        os.unlink(temporary_index)
        alternate_env = os.environ.copy()
        alternate_env["GIT_INDEX_FILE"] = temporary_index
        _git(vault, "read-tree", start_head, env=alternate_env)
        _checkpoint("alternate-index-ready")
        _stage_in_alternate_index(vault, plan, alternate_env)
        _checkpoint("reviewed-paths-staged")
        commit_env = alternate_env.copy()
        commit_env["LC_ALL"] = "C"
        committed = _git(
            vault,
            "-c",
            "core.abbrev=64",
            "commit",
            "--no-quiet",
            "--no-status",
            "-m",
            plan.message,
            env=commit_env,
        )
        commit_created = True
        commit_output = committed.stdout
        _checkpoint("commit-returned")
        commit_oid = _transaction_commit_from_output(commit_output)
        _checkpoint("commit-created")
        _verify_commit(vault, commit_oid, start_head, plan)
        _checkpoint("commit-verified")
        desired_index = _head_index_entries(
            vault, commit_oid, plan.commit_paths
        )

        def mark_index_potentially_owned() -> None:
            nonlocal transaction_index
            transaction_index = desired_index

        _sync_reviewed_index(
            vault,
            reviewed_index,
            desired_index,
            plan.commit_paths,
            on_replace_ready=mark_index_potentially_owned,
        )
        _verify_reviewed_index_matches_head(vault, commit_oid, plan.commit_paths)
        _checkpoint("real-index-synchronized")
        _require_unrelated_state_unchanged(vault, unrelated, plan)
        _require_final_states(vault, plan)
        _require_head_matches(vault, commit_oid)
        result = TransactionResult(commit_oid, tuple(sorted(plan.commit_paths)))
    except Exception as exc:
        transaction_cause = exc
        rolled_back_paths, stranded_records = _rollback_transaction(
                vault,
                start_head,
                commit_oid,
                commit_created,
                commit_output,
                reviewed_index,
                transaction_index,
                unrelated,
                plan,
                applied_changes,
                quarantined,
        )
        blocked_paths = set(rolled_back_paths)
        if isinstance(exc, _ReviewedIndexOwnershipConflict):
            blocked_paths.update(exc.paths)
        if stranded_records:
            # A consumed record that could not be returned to its own name is
            # a more specific fact than "rollback was blocked", and the only
            # one that tells the operator both files survive and where the
            # record is. It outranks the generic recovery outcome.
            transaction_error = stranded_records[0]
        elif blocked_paths:
            transaction_error = GitTransactionRecoveryError(tuple(blocked_paths))
        else:
            transaction_error = GitTransactionFailure(
                "approval transaction failed and was rolled back"
            )

    cleanup_error = None
    if temporary_index is not None:
        cleanup_error = _remove_temporary_index(temporary_index)

    if transaction_error is not None:
        if cleanup_error is not None:
            if isinstance(transaction_error, QuarantineRestorationBlocked):
                # A consumed record stranded in quarantine outranks a
                # leftover temporary index. Replacing it here would report
                # "the commit failed and was rolled back; nothing was
                # changed" while a record sits in `.consumed/` and something
                # else holds its name — false, and it would lose the one
                # outcome that says where the record is. The cleanup failure
                # is composed onto it rather than over it; the operator is
                # already told to inspect the vault, which is where the
                # leftover index becomes visible.
                transaction_error.add_note(
                    "the transaction's temporary index could not be removed "
                    f"either: {cleanup_error}"
                )
            elif isinstance(transaction_error, GitTransactionRecoveryError):
                transaction_error = GitTransactionRecoveryError(
                    transaction_error.paths,
                    temporary_index_cleanup_failed=True,
                )
            else:
                transaction_error = GitTransactionFailure(
                    "approval transaction failed and was rolled back; "
                    "temporary index cleanup failed"
                )
        raise transaction_error from transaction_cause

    if cleanup_error is not None:
        if result is None:
            raise GitTransactionFailure(
                "approval transaction cleanup failed without a result"
            ) from cleanup_error
        raise GitTransactionCommittedError(result, cleanup_error) from cleanup_error
    if result is None:
        raise GitTransactionFailure("approval transaction produced no result")
    return result


def _rollback_transaction(
    vault: Path,
    start_head: str,
    commit_oid: str | None,
    commit_created: bool,
    commit_output: bytes | None,
    reviewed_index: tuple[_IndexEntry, ...],
    transaction_index: tuple[_IndexEntry, ...] | None,
    unrelated: _UnrelatedState,
    plan: TransactionPlan,
    applied_changes: list[tuple[PathChange, PathState]],
    quarantined: list[tuple[PathChange, str]] = (),
) -> tuple[tuple[str, ...], tuple[QuarantineRestorationBlocked, ...]]:
    blocked_paths: set[str] = set()
    stranded: list[QuarantineRestorationBlocked] = []

    # Amendment 1: a rolled-back approval must leave the proposal pending and
    # actionable again, with a fingerprint that still matches its unchanged
    # bytes — so the record is moved back, not rewritten from a copy.
    for change, quarantined_name in reversed(list(quarantined)):
        try:
            restore_quarantined_leaf(vault, change.path, quarantined_name)
        except QuarantineRestorationBlocked as exc:
            # Keep it: this is the one outcome that names what actually
            # happened — the record is in quarantine, something else holds
            # its name, and both survive. Collapsing it into the generic
            # blocked-path set would report E-RECOVER ("rollback was blocked
            # by a change made at the same time"), which describes a
            # different situation and does not tell the operator where the
            # record is.
            stranded.append(exc)
            blocked_paths.add(change.path)
        except (OSError, GitTransactionError):
            blocked_paths.add(change.path)

    for change, state_written in reversed(applied_changes):
        try:
            current = capture_path_state(vault, change.path)
        except (OSError, GitTransactionError):
            blocked_paths.add(change.path)
            continue
        if current != state_written:
            blocked_paths.add(change.path)
            continue
        try:
            _apply_state(
                vault, PathChange(change.path, state_written, change.before)
            )
        except (OSError, GitTransactionError):
            blocked_paths.add(change.path)

    if transaction_index is not None:
        blocked_paths.update(
            _restore_reviewed_index_if_owned(
                vault,
                reviewed_index,
                transaction_index,
                plan.commit_paths,
            )
        )

    if commit_created and commit_oid is None:
        try:
            if commit_output is None:
                raise GitTransactionFailure("transaction commit ownership is missing")
            commit_oid = _transaction_commit_from_output(commit_output)
        except (OSError, GitTransactionError):
            blocked_paths.add("HEAD")

    if commit_oid is not None:
        try:
            _verify_commit_identity(
                vault, commit_oid, start_head, plan.message, plan.commit_paths
            )
        except (OSError, GitTransactionError):
            blocked_paths.add("HEAD")
            commit_oid = None

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

    return tuple(sorted(blocked_paths)), tuple(stranded)


def _entries_by_path(
    entries: tuple[_IndexEntry, ...],
) -> dict[str, tuple[_IndexEntry, ...]]:
    grouped: dict[str, list[_IndexEntry]] = {}
    for entry in entries:
        grouped.setdefault(entry.path, []).append(entry)
    return {
        path: tuple(sorted(path_entries, key=lambda entry: entry.stage))
        for path, path_entries in grouped.items()
    }


def _real_index_path(vault: Path) -> Path:
    value = _git_text(
        vault,
        "rev-parse",
        "--path-format=absolute",
        "--git-path",
        "index",
    ).strip()
    if not value:
        raise GitTransactionFailure("could not determine the real Git index")
    return Path(value)


def _restore_reviewed_index_if_owned(
    vault: Path,
    original_entries: tuple[_IndexEntry, ...],
    transaction_entries: tuple[_IndexEntry, ...],
    paths: tuple[str, ...],
) -> tuple[str, ...]:
    """Compare and restore reviewed entries while holding Git's real index lock."""
    blocked: set[str] = set()
    try:
        index_path = _real_index_path(vault)
    except (OSError, GitTransactionError):
        return tuple(sorted(paths))
    lock_path = Path(f"{index_path}.lock")
    lock_descriptor = -1
    owns_lock = False
    replace_completed = False
    eligible: list[str] = []
    try:
        try:
            lock_descriptor = os.open(
                lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
            )
            owns_lock = True
        except OSError:
            return tuple(sorted(paths))

        current_entries = _capture_reviewed_index(vault, paths)
        original_by_path = _entries_by_path(original_entries)
        transaction_by_path = _entries_by_path(transaction_entries)
        current_by_path = _entries_by_path(current_entries)
        for path in paths:
            original = original_by_path.get(path, ())
            current = current_by_path.get(path, ())
            if current == original:
                continue
            if current == transaction_by_path.get(path, ()):
                eligible.append(path)
            else:
                blocked.add(path)

        if not eligible:
            return tuple(sorted(blocked))

        index_mode = stat.S_IMODE(os.stat(index_path).st_mode)
        with index_path.open("rb") as source, os.fdopen(
            lock_descriptor, "wb", closefd=True
        ) as destination:
            lock_descriptor = -1
            while chunk := source.read(1024 * 1024):
                destination.write(chunk)
            destination.flush()
            os.fsync(destination.fileno())
            os.fchmod(destination.fileno(), index_mode)

        locked_env = os.environ.copy()
        locked_env["GIT_INDEX_FILE"] = os.fspath(lock_path)
        for path in eligible:
            _git(
                vault,
                "update-index",
                "--force-remove",
                "--",
                path,
                env=locked_env,
            )
            original = original_by_path.get(path, ())
            if len(original) > 1 or original and original[0].stage != 0:
                raise GitTransactionFailure(
                    "reviewed index rollback state is not stage zero"
                )
            if original:
                entry = original[0]
                _git(
                    vault,
                    "update-index",
                    "--add",
                    "--cacheinfo",
                    entry.mode,
                    entry.oid,
                    path,
                    env=locked_env,
                )

        restored_entries = _capture_reviewed_index(
            vault, paths, env=locked_env
        )
        restored_by_path = _entries_by_path(restored_entries)
        for path in paths:
            expected = (
                original_by_path.get(path, ())
                if path in eligible
                else current_by_path.get(path, ())
            )
            if restored_by_path.get(path, ()) != expected:
                raise GitTransactionFailure(
                    "reviewed index rollback verification failed"
                )

        os.replace(lock_path, index_path)
        replace_completed = True
    except (OSError, GitTransactionError):
        blocked.update(eligible or paths)
    finally:
        if lock_descriptor >= 0:
            try:
                os.close(lock_descriptor)
            except OSError:
                blocked.update(eligible or paths)
        if owns_lock and not replace_completed:
            try:
                os.unlink(lock_path)
            except FileNotFoundError:
                pass
            except OSError:
                blocked.update(eligible or paths)
    return tuple(sorted(blocked))


def _transaction_commit_from_output(output: bytes) -> str:
    candidates = {
        match.group("oid").decode("ascii")
        for match in re.finditer(
            rb"^\[[^\r\n]* (?P<oid>[0-9a-f]{40}|[0-9a-f]{64})\](?: .*)?$",
            output,
            flags=re.MULTILINE,
        )
    }
    if len(candidates) != 1:
        raise GitTransactionFailure("could not identify transaction commit")
    return candidates.pop()


def _changed_unrelated_paths(
    expected: _UnrelatedState, current: _UnrelatedState
) -> tuple[str, ...]:
    expected_index = _entries_by_path(expected.index_entries)
    current_index = _entries_by_path(current.index_entries)
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


def _remove_temporary_index(temporary_index: str) -> OSError | None:
    cleanup_error = None
    for path in (temporary_index, f"{temporary_index}.lock"):
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        except OSError as exc:
            if cleanup_error is None:
                cleanup_error = exc
    return cleanup_error


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
    vault: Path,
    paths: tuple[str, ...],
    env: dict[str, str] | None = None,
) -> tuple[_IndexEntry, ...]:
    output = _git(
        vault, "ls-files", "--stage", "-z", "--", *paths, env=env
    ).stdout
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
            raise ReviewedPathIntegrityError("reviewed index path is not a regular file")
        entries.append(_IndexEntry(os.fsdecode(path_bytes), mode, oid, 0))
    return tuple(sorted(entries, key=lambda entry: (entry.path, entry.stage)))


def _require_reviewed_index_matches_head(
    vault: Path, revision: str, paths: tuple[str, ...]
) -> None:
    if _capture_reviewed_index(vault, paths) != _head_index_entries(
        vault, revision, paths
    ):
        raise ReviewedStateChanged("reviewed path has an unexpected staged change")


def _require_owned_paths_untracked(
    vault: Path,
    revision: str,
    owned_changes: tuple[PathChange, ...],
) -> None:
    paths = tuple(change.path for change in owned_changes)
    if not paths:
        return
    if _capture_reviewed_index(vault, paths) or _head_index_entries(
        vault, revision, paths
    ):
        raise ReviewedStateChanged(
            "transaction-owned path must be absent from Git index and HEAD"
        )


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
        entry for entry in all_entries if entry.path not in reviewed
    )
    # Amendment 1: an owned proposal is consumed by moving it into
    # quarantine, so its destination is part of the transaction's own effect,
    # not unrelated state that changed underneath it.
    owned |= {quarantine_destination(path) for path in owned}
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
        kind, mode, fingerprint = _fingerprint_path(vault, path)
        dirty.append(_DirtyPathState(path, status_code, kind, mode, fingerprint))
    return tuple(sorted(dirty, key=lambda state: state.path))


def _fingerprint_path(
    vault: Path, relative_path: str
) -> tuple[str, int | None, bytes | None]:
    parts = tuple(relative_path.split("/"))
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if (
        no_follow is None
        or relative_path.startswith("/")
        or "\0" in relative_path
        or not parts
        or any(part in {"", ".", ".."} for part in parts)
    ):
        return "redirected", None, None
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | no_follow
    try:
        directory_descriptor = os.open(vault, directory_flags)
    except FileNotFoundError:
        return "absent", None, None
    except OSError:
        return "redirected", None, None
    try:
        for component in parts[:-1]:
            try:
                next_descriptor = os.open(
                    component, directory_flags, dir_fd=directory_descriptor
                )
            except FileNotFoundError:
                return "absent", None, None
            except OSError:
                return "redirected", None, None
            os.close(directory_descriptor)
            directory_descriptor = next_descriptor

        leaf = parts[-1]
        try:
            path_stat = os.stat(
                leaf, dir_fd=directory_descriptor, follow_symlinks=False
            )
        except FileNotFoundError:
            return "absent", None, None
        except OSError:
            return "redirected", None, None

        mode = stat.S_IMODE(path_stat.st_mode)
        if stat.S_ISREG(path_stat.st_mode):
            descriptor = -1
            try:
                descriptor = os.open(
                    leaf, os.O_RDONLY | no_follow, dir_fd=directory_descriptor
                )
                opened_stat = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(opened_stat.st_mode)
                    or (opened_stat.st_dev, opened_stat.st_ino)
                    != (path_stat.st_dev, path_stat.st_ino)
                ):
                    return "redirected", None, None
                with os.fdopen(descriptor, "rb", closefd=True) as opened_file:
                    descriptor = -1
                    digest = hashlib.sha256(opened_file.read()).digest()
            except OSError:
                return "redirected", None, None
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
            return "regular", stat.S_IMODE(opened_stat.st_mode), digest
        if stat.S_ISLNK(path_stat.st_mode):
            try:
                return (
                    "symlink",
                    mode,
                    os.fsencode(os.readlink(leaf, dir_fd=directory_descriptor)),
                )
            except OSError:
                return "redirected", None, None
        if stat.S_ISDIR(path_stat.st_mode):
            return "directory", mode, None
        return "other", mode, None
    finally:
        os.close(directory_descriptor)


def _require_expected_states(vault: Path, plan: TransactionPlan) -> None:
    for change in plan.changes + plan.owned_changes:
        if capture_path_state(vault, change.path) != change.before:
            raise ReviewedStateChanged(
                f"reviewed path does not match expected state: {change.path}"
            )


def _capture_leaf_state(directory_descriptor: int, leaf: str) -> PathState:
    try:
        leaf_stat = os.lstat(leaf, dir_fd=directory_descriptor)
    except FileNotFoundError:
        return PathState.absent()
    except OSError as exc:
        raise ReviewedPathUnavailable("reviewed path is unavailable") from exc
    if stat.S_ISLNK(leaf_stat.st_mode) or not stat.S_ISREG(leaf_stat.st_mode):
        raise ReviewedPathIntegrityError("reviewed path is not a regular file")

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(leaf, flags, dir_fd=directory_descriptor)
    except OSError as exc:
        raise _unsafe_open_failure(
            exc, "reviewed path could not be opened safely"
        ) from exc
    try:
        opened_stat = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened_stat.st_mode)
            or (opened_stat.st_dev, opened_stat.st_ino)
            != (leaf_stat.st_dev, leaf_stat.st_ino)
        ):
            raise ReviewedPathIntegrityError("reviewed path changed before mutation")
        with os.fdopen(descriptor, "rb", closefd=True) as opened_file:
            descriptor = -1
            return PathState.regular(
                opened_file.read(), stat.S_IMODE(opened_stat.st_mode)
            )
    finally:
        if descriptor >= 0:
            os.close(descriptor)


#: The single kernel operation Amendment 1 rests on: move a leaf and fail if
#: the destination exists. Reserving a name and renaming onto it later is two
#: operations and does not compose into one guarantee — another writer can
#: take the reservation in between, and an ordinary rename destroys what took
#: it. `ctypes` is stdlib, so this adds no dependency.
_RENAME_EXCL = 0x4          # macOS renameatx_np
_RENAME_NOREPLACE = 0x1     # Linux renameat2
_SYS_RENAMEAT2 = {"x86_64": 316, "aarch64": 276, "arm64": 276, "i686": 353, "armv7l": 382}


def _atomic_mover():
    """Resolve the platform's atomic no-overwrite move, or `None`."""
    library = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
    if sys.platform == "darwin":
        entry = getattr(library, "renameatx_np", None)
        if entry is None:
            return None
        entry.argtypes = [
            ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint
        ]
        entry.restype = ctypes.c_int
        return lambda ffd, f, tfd, t: entry(ffd, f, tfd, t, _RENAME_EXCL)
    if sys.platform.startswith("linux"):
        entry = getattr(library, "renameat2", None)
        if entry is not None:
            entry.argtypes = [
                ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p,
                ctypes.c_uint,
            ]
            entry.restype = ctypes.c_int
            return lambda ffd, f, tfd, t: entry(ffd, f, tfd, t, _RENAME_NOREPLACE)
        number = _SYS_RENAMEAT2.get(platform.machine())
        if number is None:
            return None
        library.syscall.restype = ctypes.c_long
        return lambda ffd, f, tfd, t: library.syscall(
            ctypes.c_long(number),
            ctypes.c_int(ffd), ctypes.c_char_p(f),
            ctypes.c_int(tfd), ctypes.c_char_p(t),
            ctypes.c_uint(_RENAME_NOREPLACE),
        )
    return None


_MOVE_NO_REPLACE = _atomic_mover()

#: Errnos that mean "this kernel or filesystem cannot do it", as opposed to
#: "it refused for a reason about these files".
#:
#: `EPERM` is deliberately absent, and used to be here. It is ambiguous: a
#: seccomp filter that blocks `renameat2` reports `EPERM` (Docker's default
#: profile did exactly this), which is a capability problem — but `EPERM` is
#: also the kernel's answer for a refusal about *these files*, such as an
#: immutable or append-only attribute, or a sticky-bit directory whose file
#: this process does not own. Treating both as unsupported told the operator
#: "this vault's filesystem cannot move files safely" — a whole-vault verdict
#: at `attention` severity — when the truth was one unwritable file.
_UNSUPPORTED_ERRNOS = {
    errno.ENOSYS, errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP,
}

#: Whether the move syscall itself is reachable, resolved at most once.
_MOVE_REACHABLE: bool | None = None


def _move_syscall_is_blocked() -> bool:
    """Is `EPERM` from the mover about the syscall, or about the files?

    Answered without touching the filesystem at all. A seccomp filter acts
    at syscall entry, before the kernel looks at any argument, so a call
    with deliberately invalid descriptors and empty names separates the two
    cases: a *blocked* syscall still answers `EPERM`, while a reachable one
    gets as far as validating its arguments and answers some non-`EPERM`
    argument error — `EBADF` on some platforms, `ENOENT` on macOS. Which one
    is deliberately not relied on; only "not `EPERM`" is. Either way the call
    cannot succeed and cannot name a real file, so there is nothing to
    create, move, or clean up.

    An earlier version of this probed by creating a file in the target
    directory and moving it. That put writes inside the vault on a refusal
    path, outside the Git transaction, with the cleanup swallowing `OSError`
    so an artifact could survive — which is exactly the invariant S5 and S7
    exist to hold. A classification is not worth a write.

    Anything other than `EPERM` means the syscall ran, so the answer is no.
    """
    global _MOVE_REACHABLE
    if _MOVE_REACHABLE is None:
        ctypes.set_errno(0)
        outcome = _MOVE_NO_REPLACE(-1, b"", -1, b"")
        # A success here would mean the call did something with no valid
        # operands, which cannot happen; treat it as reachable regardless.
        _MOVE_REACHABLE = outcome == 0 or ctypes.get_errno() != errno.EPERM
    return not _MOVE_REACHABLE


def _move_no_replace(
    from_descriptor: int, from_name: str, to_descriptor: int, to_name: str
) -> None:
    """Atomically move a leaf, raising `FileExistsError` if the destination exists.

    Never falls back to an ordinary rename: overwriting is the exact harm
    this exists to prevent.
    """
    if _MOVE_NO_REPLACE is None:
        raise AtomicMoveUnavailable(
            "this platform has no atomic no-overwrite move"
        )
    ctypes.set_errno(0)
    outcome = _MOVE_NO_REPLACE(
        from_descriptor, os.fsencode(from_name), to_descriptor, os.fsencode(to_name)
    )
    if outcome == 0:
        return
    code = ctypes.get_errno()
    if code == errno.EEXIST:
        raise FileExistsError(code, os.strerror(code), to_name)
    if code in _UNSUPPORTED_ERRNOS or (
        code == errno.EPERM and _move_syscall_is_blocked()
    ):
        raise AtomicMoveUnavailable(
            "this filesystem has no atomic no-overwrite move"
        )
    raise OSError(code, os.strerror(code), from_name)


#: Amendment 1: a proposal record is consumed by moving it here, never by
#: unlinking it. Outside the outbox's `*.yaml` glob, so quarantined records
#: never appear in a listing and no reviewed action can reach them.
QUARANTINE_DIRECTORY = ".consumed"


def quarantine_destination(relative_path: str) -> str:
    """Where a consumed record lands: `<parent>/.consumed/<name>`."""
    leaf = PurePosixPath(relative_path)
    return (leaf.parent / QUARANTINE_DIRECTORY / leaf.name).as_posix()


def _open_quarantine(parent_descriptor: int) -> int:
    """Open the quarantine directory, creating it only when it is absent.

    Opened through `_open_checked_directory` and relative to the
    parent's descriptor, so
    neither the parent nor the quarantine can be swapped between validation
    and use.

    `mkdir` is the only thing that establishes ownership, and it does so
    atomically: a `FileExistsError` means something appeared between the
    check and the creation. That is refused rather than adopted — whatever
    appeared was not established here, and silently accepting it would be
    trusting a directory this call never validated as its own.
    """
    # One lookup, not two: probing for existence separately from opening
    # would add a window of this function's own making between the two.
    try:
        return _open_checked_directory(
            QUARANTINE_DIRECTORY, "quarantine", dir_fd=parent_descriptor
        )
    except ReviewedPathUnavailable as exc:
        if not isinstance(exc.__cause__, FileNotFoundError):
            raise

    try:
        os.mkdir(QUARANTINE_DIRECTORY, 0o700, dir_fd=parent_descriptor)
    except FileExistsError as exc:
        raise ReviewedPathIntegrityError(
            "quarantine appeared while it was being created"
        ) from exc
    except OSError as exc:
        raise ReviewedPathUnavailable("quarantine is unavailable") from exc
    return _open_checked_directory(
        QUARANTINE_DIRECTORY, "quarantine", dir_fd=parent_descriptor
    )


def _quarantine_reviewed_leaf(
    parent_descriptor: int,
    leaf: str,
    expected: PathState,
    quarantine_descriptor: int,
    path: str,
) -> str:
    """Move one reviewed leaf into quarantine, or leave everything as found.

    The move is atomic and cannot overwrite. Verification runs through a
    descriptor opened on the **source**, before the move, and held across
    it: a descriptor is bound to one inode for its lifetime, so the object
    verified is provably the object consumed (design §3, Amendment 1 step
    3 — "identity and contents, never a fresh name lookup").

    This previously opened the descriptor *after* the move, by name, in the
    quarantine directory. That is a fresh name lookup, and it verified
    contents and mode but never identity, so a writer that replaced
    `.consumed/<leaf>` between the move and the open had the substitute
    verified — and, on mismatch, moved back under the reviewed record's
    name while the real record was gone. The docstring claimed the
    guarantee the code did not implement.

    The single name lookup that remains has the opposite job: confirming
    that the quarantined name resolves to the very inode held open. If it
    does not, something other than the reviewed object is sitting there and
    the move is undone.

    No step unlinks anything, so losing a race costs a refusal rather than
    a file.
    """
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(leaf, flags, dir_fd=parent_descriptor)
    except FileNotFoundError as exc:
        raise ReviewedStateChanged(
            f"reviewed path changed before mutation: {path}"
        ) from exc
    except OSError as exc:
        raise _unsafe_open_failure(
            exc, "reviewed path could not be opened safely"
        ) from exc

    try:
        # The reviewed object, established before anything moves.
        held = _held_state(descriptor)
        if held != expected:
            raise ReviewedStateChanged(
                f"reviewed path changed before mutation: {path}"
            )
        identity = os.fstat(descriptor)

        try:
            _move_no_replace(
                parent_descriptor, leaf, quarantine_descriptor, leaf
            )
        except FileNotFoundError as exc:
            raise ReviewedStateChanged(
                f"reviewed path changed before mutation: {path}"
            ) from exc
        except FileExistsError as exc:
            # A record under this id is already quarantined: this one was
            # consumed already, so the state is not what was reviewed.
            raise ReviewedStateChanged(
                f"reviewed path changed before mutation: {path}"
            ) from exc
        except OSError as exc:
            # An operand-level refusal: `EPERM` on an immutable record or a
            # sticky-bit outbox, `EACCES`, `EROFS`, an I/O error. Nothing
            # moved, so this is a plain refusal and must be described as
            # one. It is normalised *here*, at the boundary that knows what
            # the operation meant, rather than by widening a route's catch
            # list — a raw `OSError` reaching a route would resolve to
            # E-UNKNOWN ("an unexpected error was not handled",
            # committed=unknown, retry=stop) for a designed refusal in
            # which the vault is provably untouched.
            #
            # Reject reaches this path without an `except Exception` above
            # it, unlike approve and delete, so before this clause existed
            # a `chattr +i` record produced E-UNKNOWN there and a truthful
            # refusal everywhere else.
            raise ReviewedPathUnavailable(
                "reviewed record could not be consumed"
            ) from exc

        def _restore() -> None:
            try:
                _move_no_replace(
                    quarantine_descriptor, leaf, parent_descriptor, leaf
                )
            except (OSError, GitTransactionError) as exc:
                # Whatever stopped it — an occupied name, an I/O failure, or
                # a move primitive that turned out to be unavailable — the
                # fact is the same: the record is still in quarantine.
                # Reporting anything that says nothing changed would be a
                # false statement about state the operator can see.
                raise QuarantineRestorationBlocked(path) from exc

        try:
            # Identity: the quarantined name must be this inode, not a
            # look-alike that arrived in the meantime.
            landed = os.lstat(leaf, dir_fd=quarantine_descriptor)
            if (landed.st_dev, landed.st_ino) != (
                identity.st_dev, identity.st_ino
            ):
                raise ReviewedStateChanged(
                    f"reviewed path changed before mutation: {path}"
                )
            # Contents: re-read through the descriptor still held, so an
            # in-place rewrite during the move is caught too.
            if _held_state(descriptor) != expected:
                raise ReviewedStateChanged(
                    f"reviewed path changed before mutation: {path}"
                )
        except BaseException:
            _restore()
            raise
    finally:
        os.close(descriptor)
    return leaf


def _walk_to_parent(vault: Path, relative_path: str) -> tuple[int, str]:
    """Open the leaf's parent through checked, no-follow descriptors."""
    root = Path(os.path.abspath(os.fspath(vault)))
    parts = PurePosixPath(relative_path).parts
    descriptor = _open_checked_directory(root, "vault root")
    try:
        for part in parts[:-1]:
            nxt = _open_checked_directory(part, "reviewed parent", dir_fd=descriptor)
            os.close(descriptor)
            descriptor = nxt
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor, parts[-1]


def quarantine_path_if_unchanged(
    vault: Path, relative_path: str, expected: PathState
) -> str:
    """Consume one reviewed regular leaf by moving it into quarantine."""
    try:
        _validate_transaction_path(relative_path)
    except ValueError as exc:
        raise InvalidTransactionPath("reviewed path is unsafe") from exc
    if expected.contents is None:
        raise ValueError("quarantine requires a regular expected state")

    parent_descriptor, leaf = _walk_to_parent(vault, relative_path)
    try:
        # The quarantine directory is durable infrastructure: created on
        # first use and never removed. Removing it again after a refusal
        # would add a cleanup step that can fail — and a failure there is
        # invisible to the operator, since the refusal they see is about the
        # proposal, not about a directory. An empty `.consumed/` is not vault
        # content and costs nothing; a silently failing cleanup does.
        quarantine_descriptor = _open_quarantine(parent_descriptor)
        try:
            return _quarantine_reviewed_leaf(
                parent_descriptor, leaf, expected,
                quarantine_descriptor, relative_path,
            )
        finally:
            os.close(quarantine_descriptor)
    finally:
        os.close(parent_descriptor)


def consume_reviewed_proposal(
    vault: Path, relative_path: str, expected: PathState
) -> str:
    """Quarantine one reviewed record under the per-vault approval lock.

    The lock is taken here rather than inside `quarantine_path_if_unchanged`
    because the transaction already holds it when it consumes owned changes.
    A standalone consumer — reject — must not race an approval that owns the
    same record, so it takes the lock itself.
    """
    root = Path(os.path.abspath(os.fspath(vault)))
    quarantined: str | None = None
    try:
        with _approval_lock(root):
            quarantined = quarantine_path_if_unchanged(root, relative_path, expected)
    except _ApprovalLockCleanupFailure as exc:
        # Mirrors `execute_transaction`: once the work has happened, a cleanup
        # failure may not be reported as "nothing was changed".
        if quarantined is None:
            raise
        raise QuarantineCleanupError(exc.cleanup_error) from exc.cleanup_error
    return quarantined


def restore_quarantined_leaf(
    vault: Path, relative_path: str, quarantined_name: str
) -> None:
    """Move a quarantined record back under its own name (rollback)."""
    try:
        _validate_transaction_path(relative_path)
    except ValueError as exc:
        raise InvalidTransactionPath("reviewed path is unsafe") from exc
    parent_descriptor, leaf = _walk_to_parent(vault, relative_path)
    try:
        # Opening the quarantine is inside the clause too. If `.consumed/`
        # has been removed or swapped by the time rollback runs, the record
        # is just as stranded as if the move itself failed — and reporting
        # the generic recovery outcome instead would drop the one fact the
        # operator needs, which is *where the record is*.
        try:
            quarantine_descriptor = _open_checked_directory(
                QUARANTINE_DIRECTORY, "quarantine", dir_fd=parent_descriptor
            )
        except (OSError, GitTransactionError) as exc:
            raise QuarantineRestorationBlocked(relative_path) from exc
        try:
            _move_no_replace(
                quarantine_descriptor, quarantined_name, parent_descriptor, leaf
            )
        except (OSError, GitTransactionError) as exc:
            raise QuarantineRestorationBlocked(relative_path) from exc
        finally:
            os.close(quarantine_descriptor)
    finally:
        os.close(parent_descriptor)


def _read_open_file(descriptor: int) -> bytes:
    """Read a descriptor's full contents from the start.

    Reads go through the descriptor, never through a fresh lookup: a
    descriptor is bound to one inode for its lifetime, so nothing can
    substitute a different file underneath it.
    """
    os.lseek(descriptor, 0, os.SEEK_SET)
    chunks = []
    while True:
        chunk = os.read(descriptor, 1 << 20)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def _held_state(descriptor: int) -> PathState:
    opened = os.fstat(descriptor)
    if not stat.S_ISREG(opened.st_mode):
        raise ReviewedPathIntegrityError("reviewed path is not a regular file")
    return PathState.regular(_read_open_file(descriptor), stat.S_IMODE(opened.st_mode))


def _apply_state(
    vault: Path,
    change: PathChange,
    *,
    on_applied: Callable[[PathState], None] | None = None,
) -> None:
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
            raise ReviewedStateChanged(
                f"reviewed path changed before mutation: {change.path}"
            )
        if change.after.contents is None:
            if change.before.contents is not None:
                # Not a proposal record: this branch serves approve's
                # source-to-destination move, whose removal is the intended,
                # committed, Git-revertible effect (Amendment 1 scopes the
                # no-deletion rule to proposal records). The state was
                # compared immediately above, under this same descriptor.
                try:
                    os.unlink(leaf, dir_fd=directory_descriptor)
                except OSError as exc:
                    raise GitTransactionFailure(
                        "could not remove reviewed file"
                    ) from exc
                if on_applied is not None:
                    on_applied(change.after)
                    _checkpoint("filesystem-path-applied")
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
            if on_applied is not None:
                on_applied(change.after)
                _checkpoint("filesystem-path-applied")
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
    vault: Path, plan: TransactionPlan, alternate_env: dict[str, str]
) -> None:
    _git(vault, "add", "--all", "--", *plan.commit_paths, env=alternate_env)
    staged = _name_only(
        vault,
        "diff",
        "--cached",
        "--no-renames",
        "--name-only",
        "-z",
        env=alternate_env,
    )
    if tuple(sorted(staged)) != tuple(sorted(plan.commit_paths)):
        raise GitTransactionFailure("alternate index staged an unexpected path")
    _require_entries_match_after_states(
        vault,
        _capture_reviewed_index(vault, plan.commit_paths, env=alternate_env),
        plan.changes,
        "alternate index does not contain the reviewed state",
    )


def _verify_commit_identity(
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


def _verify_commit(
    vault: Path,
    commit_oid: str,
    start_head: str,
    plan: TransactionPlan,
) -> None:
    _verify_commit_identity(
        vault, commit_oid, start_head, plan.message, plan.commit_paths
    )
    _require_entries_match_after_states(
        vault,
        _head_index_entries(vault, commit_oid, plan.commit_paths),
        plan.changes,
        "transaction commit does not contain the reviewed state",
    )


def _require_entries_match_after_states(
    vault: Path,
    entries: tuple[_IndexEntry, ...],
    changes: tuple[PathChange, ...],
    message: str,
) -> None:
    by_path: dict[str, _IndexEntry] = {}
    for entry in entries:
        if entry.stage != 0 or entry.path in by_path:
            raise GitTransactionFailure(message)
        by_path[entry.path] = entry

    expected_paths = {
        change.path for change in changes if change.after.contents is not None
    }
    if set(by_path) != expected_paths:
        raise GitTransactionFailure(message)

    for change in changes:
        if change.after.contents is None:
            continue
        entry = by_path[change.path]
        expected_mode = (
            "100755" if change.after.mode & stat.S_IXUSR else "100644"
        )
        if entry.mode != expected_mode:
            raise GitTransactionFailure(message)
        if _git(vault, "cat-file", "blob", entry.oid).stdout != change.after.contents:
            raise GitTransactionFailure(message)


def _require_final_states(vault: Path, plan: TransactionPlan) -> None:
    for change in plan.changes + plan.owned_changes:
        if capture_path_state(vault, change.path) != change.after:
            raise GitTransactionFailure(
                f"transaction-owned path changed before success: {change.path}"
            )


def _require_head_matches(vault: Path, commit_oid: str) -> None:
    if _git_text(vault, "rev-parse", "HEAD").strip() != commit_oid:
        raise GitTransactionFailure("HEAD changed before transaction success")


def _sync_reviewed_index(
    vault: Path,
    expected_entries: tuple[_IndexEntry, ...],
    transaction_entries: tuple[_IndexEntry, ...],
    paths: tuple[str, ...],
    *,
    on_replace_ready: Callable[[], None],
) -> None:
    """Replace reviewed entries only if their complete preflight state remains."""
    index_path = _real_index_path(vault)
    lock_path = Path(f"{index_path}.lock")
    lock_descriptor = -1
    owns_lock = False
    replace_completed = False
    primary_error: BaseException | None = None
    try:
        try:
            lock_descriptor = os.open(
                lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
            )
            owns_lock = True
        except OSError as exc:
            raise GitTransactionFailure(
                "could not acquire the real Git index lock"
            ) from exc

        current_entries = _capture_reviewed_index(vault, paths)
        expected_by_path = _entries_by_path(expected_entries)
        current_by_path = _entries_by_path(current_entries)
        changed_paths = tuple(
            path
            for path in paths
            if current_by_path.get(path, ()) != expected_by_path.get(path, ())
        )
        if changed_paths:
            raise _ReviewedIndexOwnershipConflict(changed_paths)

        index_mode = stat.S_IMODE(os.stat(index_path).st_mode)
        with index_path.open("rb") as source, os.fdopen(
            lock_descriptor, "wb", closefd=True
        ) as destination:
            lock_descriptor = -1
            while chunk := source.read(1024 * 1024):
                destination.write(chunk)
            destination.flush()
            os.fsync(destination.fileno())
            os.fchmod(destination.fileno(), index_mode)

        transaction_by_path = _entries_by_path(transaction_entries)
        locked_env = os.environ.copy()
        locked_env["GIT_INDEX_FILE"] = os.fspath(lock_path)
        for path in paths:
            _git(
                vault,
                "update-index",
                "--force-remove",
                "--",
                path,
                env=locked_env,
            )
            entries = transaction_by_path.get(path, ())
            if len(entries) > 1 or entries and entries[0].stage != 0:
                raise GitTransactionFailure(
                    "transaction index state is not stage zero"
                )
            if entries:
                entry = entries[0]
                _git(
                    vault,
                    "update-index",
                    "--add",
                    "--cacheinfo",
                    entry.mode,
                    entry.oid,
                    path,
                    env=locked_env,
                )

        if _capture_reviewed_index(
            vault, paths, env=locked_env
        ) != transaction_entries:
            raise GitTransactionFailure(
                "reviewed index synchronization verification failed"
            )

        on_replace_ready()
        os.replace(lock_path, index_path)
        replace_completed = True
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        cleanup_error: OSError | None = None
        if lock_descriptor >= 0:
            try:
                os.close(lock_descriptor)
            except OSError as exc:
                cleanup_error = exc
        if owns_lock and not replace_completed:
            try:
                os.unlink(lock_path)
            except FileNotFoundError:
                pass
            except OSError as exc:
                if cleanup_error is None:
                    cleanup_error = exc
                else:
                    cleanup_error.add_note(
                        f"real index lock unlink also failed: {exc}"
                    )
        if cleanup_error is not None:
            if primary_error is not None:
                primary_error.add_note(
                    f"real index lock cleanup also failed: {cleanup_error}"
                )
            else:
                raise GitTransactionFailure(
                    "real Git index lock cleanup failed"
                ) from cleanup_error


def _require_unrelated_state_unchanged(
    vault: Path, expected: _UnrelatedState, plan: TransactionPlan
) -> None:
    current = _capture_unrelated_state(vault, plan)
    if current != expected:
        raise GitTransactionFailure("unrelated vault state changed during transaction")
