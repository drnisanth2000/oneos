"""Immutable approval transaction contracts and vault-safety primitives."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import errno
import fcntl
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
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
