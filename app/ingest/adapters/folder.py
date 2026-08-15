"""folder.py — folder-drop ingest adapter (spec §8.1, first adapter).

A file dropped into `_dropbox/` is hashed, its text extracted, run through the
ADR-008 PII filter, and committed as a redacted item into `<entity>/00-inbox/
active/` with `sub: triage`. The order is load → filter → write: only redacted
text is ever written, so PII never reaches git (the one irreversible mistake in
this design). The raw original is moved out of the vault to a raw archive and
referenced by `source_ref` + `sha256`; it is never copied into git.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import stat
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import BinaryIO

from ..base import (
    IngestError, IngestPathCollision, IngestResult, commit_inbox_item,
    find_tracked_receipt, prepare_inbox_item,
)
from ...scope import Scope

_MIME = {
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".csv": "text/csv",
    ".log": "text/plain",
    ".json": "application/json",
    ".yaml": "application/yaml",
    ".yml": "application/yaml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
_TEXT_EXT = {".txt", ".md", ".csv", ".log", ".json", ".yaml", ".yml"}


class FolderSourceRestoreError(IngestError):
    pass


class RawArchiveContainmentError(IngestError):
    pass


class RawArchiveWriteError(IngestError):
    pass


@dataclass
class _ArchiveAnchor:
    fd: int
    path: Path
    missing: tuple[str, ...]


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _identity(stat_result: os.stat_result) -> tuple[int, int]:
    return stat_result.st_dev, stat_result.st_ino


def _directory_flags() -> int:
    if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        raise RawArchiveContainmentError(
            "raw archive containment could not be verified"
        )
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def _directory_is_within(fd: int, root_identity: tuple[int, int]) -> bool:
    current = os.dup(fd)
    try:
        while True:
            current_identity = _identity(os.fstat(current))
            if current_identity == root_identity:
                return True
            parent = os.open("..", _directory_flags(), dir_fd=current)
            parent_identity = _identity(os.fstat(parent))
            if parent_identity == current_identity:
                os.close(parent)
                return False
            os.close(current)
            current = parent
    finally:
        os.close(current)


def _open_external_archive_anchor(vault: Path, raw_archive: Path) -> _ArchiveAnchor:
    try:
        vault_lexical = Path(os.path.abspath(vault))
        archive_lexical = Path(os.path.abspath(raw_archive))
        archive_physical = raw_archive.resolve(strict=False)
        vault_identity = _identity(os.stat(vault_lexical, follow_symlinks=True))
    except (OSError, RuntimeError) as exc:
        raise RawArchiveContainmentError(
            "raw archive containment could not be verified"
        ) from exc

    if _is_within(archive_lexical, vault_lexical):
        raise RawArchiveContainmentError("raw archive must be outside the vault")

    current = os.open(os.path.sep, _directory_flags())
    current_path = Path(os.path.sep)
    parts = archive_physical.parts[1:]
    try:
        for index, part in enumerate(parts):
            try:
                child = os.open(part, _directory_flags(), dir_fd=current)
            except FileNotFoundError:
                if _directory_is_within(current, vault_identity):
                    raise RawArchiveContainmentError(
                        "raw archive must be outside the vault"
                    )
                return _ArchiveAnchor(current, current_path, tuple(parts[index:]))
            except OSError as exc:
                raise RawArchiveContainmentError(
                    "raw archive containment could not be verified"
                ) from exc
            os.close(current)
            current = child
            current_path /= part
            if _directory_is_within(current, vault_identity):
                raise RawArchiveContainmentError(
                    "raw archive must be outside the vault"
                )
        return _ArchiveAnchor(current, current_path, ())
    except BaseException:
        os.close(current)
        raise


def _anchor_is_current(anchor: _ArchiveAnchor) -> bool:
    try:
        current = os.stat(anchor.path, follow_symlinks=False)
        return _identity(current) == _identity(os.fstat(anchor.fd))
    except OSError:
        return False


def _open_regular_source(path: Path) -> tuple[BinaryIO, os.stat_result]:
    if not hasattr(os, "O_NOFOLLOW"):
        raise RawArchiveWriteError("folder source could not be verified")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        try:
            is_symlink = stat.S_ISLNK(os.stat(path, follow_symlinks=False).st_mode)
        except OSError:
            is_symlink = False
        message = (
            "folder source must be a regular file"
            if is_symlink
            else "folder source could not be verified"
        )
        raise RawArchiveWriteError(message) from exc
    try:
        source_stat = os.fstat(fd)
        if not stat.S_ISREG(source_stat.st_mode):
            raise RawArchiveWriteError("folder source must be a regular file")
        return os.fdopen(fd, "rb"), source_stat
    except BaseException:
        os.close(fd)
        raise


def _source_is_current(path: Path, source_stat: os.stat_result) -> bool:
    try:
        current = os.stat(path, follow_symlinks=False)
    except OSError:
        return False
    return stat.S_ISREG(current.st_mode) and _identity(current) == _identity(source_stat)


def _open_archive_directory(anchor: _ArchiveAnchor, vault: Path) -> int:
    if not _anchor_is_current(anchor):
        raise RawArchiveContainmentError(
            "raw archive containment could not be verified"
        )
    vault_identity = _identity(os.stat(vault, follow_symlinks=True))
    current = os.dup(anchor.fd)
    try:
        for part in anchor.missing:
            try:
                os.mkdir(part, mode=0o700, dir_fd=current)
            except FileExistsError:
                pass
            child = os.open(part, _directory_flags(), dir_fd=current)
            os.close(current)
            current = child
            if _directory_is_within(current, vault_identity):
                raise RawArchiveContainmentError(
                    "raw archive must be outside the vault"
                )
        return current
    except RawArchiveContainmentError:
        os.close(current)
        raise
    except OSError as exc:
        os.close(current)
        raise RawArchiveContainmentError(
            "raw archive containment could not be verified"
        ) from exc


def _archive_source(
    source: BinaryIO,
    source_stat: os.stat_result,
    source_path: Path,
    archive_fd: int,
    name: str,
    vault_identity: tuple[int, int],
) -> None:
    destination_fd: int | None = None
    destination_created = False
    try:
        destination_fd = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=archive_fd,
        )
        destination_created = True
        with os.fdopen(destination_fd, "wb") as destination:
            destination_fd = None
            source.seek(0)
            shutil.copyfileobj(source, destination)
            destination.flush()
            os.fsync(destination.fileno())
            os.fchmod(destination.fileno(), stat.S_IMODE(source_stat.st_mode))
            os.utime(
                destination.fileno(),
                ns=(source_stat.st_atime_ns, source_stat.st_mtime_ns),
            )
        if _directory_is_within(archive_fd, vault_identity):
            raise RawArchiveContainmentError("raw archive must be outside the vault")
        if not _source_is_current(source_path, source_stat):
            raise RawArchiveWriteError("folder source changed during ingest")
        os.unlink(source_path)
    except FileExistsError as exc:
        raise IngestPathCollision("raw archive destination already exists") from exc
    except (OSError, RawArchiveContainmentError, RawArchiveWriteError) as exc:
        if destination_fd is not None:
            os.close(destination_fd)
        cleanup_error: OSError | None = None
        if destination_created:
            try:
                os.unlink(name, dir_fd=archive_fd)
            except OSError as cleanup_exc:
                cleanup_error = cleanup_exc
        if cleanup_error is not None:
            raise RawArchiveWriteError(
                "raw source archival failed; cleanup failed"
            ) from exc
        if isinstance(exc, (RawArchiveContainmentError, RawArchiveWriteError)):
            raise
        raise RawArchiveWriteError("raw source archival failed") from exc


def _restore_raw(archive_fd: int, name: str, src: Path, reason: str) -> None:
    if os.path.lexists(src):
        raise FolderSourceRestoreError(f"{reason} and original drop path is occupied")
    source_fd: int | None = None
    destination_fd: int | None = None
    destination_created = False
    try:
        source_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=archive_fd)
        source_stat = os.fstat(source_fd)
        destination_fd = os.open(
            src,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
        destination_created = True
        with os.fdopen(source_fd, "rb") as source, os.fdopen(
            destination_fd, "wb"
        ) as destination:
            source_fd = None
            destination_fd = None
            shutil.copyfileobj(source, destination)
            destination.flush()
            os.fsync(destination.fileno())
            os.fchmod(destination.fileno(), stat.S_IMODE(source_stat.st_mode))
            os.utime(
                destination.fileno(),
                ns=(source_stat.st_atime_ns, source_stat.st_mtime_ns),
            )
        os.unlink(name, dir_fd=archive_fd)
    except OSError as exc:
        if source_fd is not None:
            os.close(source_fd)
        if destination_fd is not None:
            os.close(destination_fd)
        cleanup_error: OSError | None = None
        if destination_created:
            try:
                os.unlink(src)
            except OSError as cleanup_exc:
                cleanup_error = cleanup_exc
        if cleanup_error is not None:
            raise FolderSourceRestoreError(
                f"{reason} and raw source restoration failed; cleanup failed"
            ) from exc
        raise FolderSourceRestoreError(f"{reason} and raw source restoration failed") from exc


def _sha256_stream(source: BinaryIO) -> str:
    h = hashlib.sha256()
    source.seek(0)
    for chunk in iter(lambda: source.read(65536), b""):
        h.update(chunk)
    source.seek(0)
    return h.hexdigest()


def sha256_of(source: Path | str | BinaryIO) -> str:
    if isinstance(source, (str, os.PathLike)):
        with open(source, "rb") as source_file:
            return _sha256_stream(source_file)
    return _sha256_stream(source)


def mime_of(path: Path) -> str:
    return _MIME.get(path.suffix.lower(), "application/octet-stream")


def _extract_text_stream(path: Path, source: BinaryIO) -> str:
    ext = path.suffix.lower()
    source.seek(0)
    if ext == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(source)
        text = "\n".join((page.extract_text() or "") for page in reader.pages)
        source.seek(0)
        return text
    if ext in _TEXT_EXT:
        text = source.read().decode("utf-8", errors="replace")
        source.seek(0)
        return text
    return ""  # unknown binary — no text; metadata only


def extract_text(path: Path) -> str:
    with open(path, "rb") as source:
        return _extract_text_stream(path, source)


def process_drop(
    scope: Scope,
    source: Path | str,
    *,
    raw_archive: Path | str,
    now: datetime | None = None,
) -> IngestResult:
    """Ingest one dropped file. Returns the shared ingest result.
    The raw original is moved into `raw_archive` (outside the vault); only the
    redacted note enters the vault via the shared write path."""
    source_path = Path(source)
    archive_root = Path(raw_archive)
    archive_anchor = _open_external_archive_anchor(scope.root, archive_root)
    now = now or datetime.now()
    source_file: BinaryIO | None = None
    try:
        source_file, source_stat = _open_regular_source(source_path)
        digest = sha256_of(source_file)
        size = source_stat.st_size
        mime = mime_of(source_path)
        text = _extract_text_stream(source_path, source_file)
        if not _source_is_current(source_path, source_stat):
            raise RawArchiveWriteError("folder source changed during ingest")

        archived_name = f"{digest[:16]}-{source_path.name}"
        source_ref = f"raw:{archived_name}"
        kwargs = {
            "text": text,
            "title": source_path.name,
            "source": "folder",
            "source_id": digest[:16],
            "received_at": now.isoformat(timespec="seconds"),
            "source_ref": source_ref,
            "body_ref": source_ref,
            "sha256": digest,
            "mime": mime,
            "size": size,
            "slug_seed": digest,
        }

        _path, env, _rendered = prepare_inbox_item(scope, **kwargs)
        existing = find_tracked_receipt(scope, env)
        if existing is not None:
            return IngestResult(existing, env, False, None)

        archive_fd = _open_archive_directory(archive_anchor, scope.root)
        try:
            vault_identity = _identity(os.stat(scope.root, follow_symlinks=True))
            _archive_source(
                source_file,
                source_stat,
                source_path,
                archive_fd,
                archived_name,
                vault_identity,
            )
            try:
                result = commit_inbox_item(scope, **kwargs)
            except IngestError as exc:
                try:
                    _restore_raw(
                        archive_fd, archived_name, source_path, "receipt commit failed"
                    )
                except FolderSourceRestoreError as restore_exc:
                    raise restore_exc from exc
                raise
            if not result.created:
                _restore_raw(
                    archive_fd,
                    archived_name,
                    source_path,
                    "duplicate detected after archive",
                )
            return result
        finally:
            os.close(archive_fd)
    finally:
        if source_file is not None:
            source_file.close()
        os.close(archive_anchor.fd)


def watch(
    vault: Path | str,
    entity: str,
    dropbox: Path | str,
    raw_archive: Path | str,
) -> None:  # pragma: no cover - I/O glue over process_drop
    """Block, processing every file that lands in `dropbox`."""
    scope = Scope(vault, entity)

    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer

    dropbox_path = Path(dropbox)
    dropbox_path.mkdir(parents=True, exist_ok=True)

    class _Handler(FileSystemEventHandler):
        def on_created(self, event):
            if not event.is_directory:
                process_drop(scope, event.src_path, raw_archive=raw_archive)

    observer = Observer()
    observer.schedule(_Handler(), str(dropbox_path), recursive=False)
    observer.start()
    try:
        while True:
            observer.join(1)
    finally:
        observer.stop()
        observer.join()
