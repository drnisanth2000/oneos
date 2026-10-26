"""Phase 3 slice 1: archive one unlocked PDF outside the vault.

The vault receives the existing redacted inbox receipt and no extracted
transcript. This slice does not unlock encrypted PDFs, store passwords,
connect a mailbox, move provider mail, or call a private parser.
"""
from __future__ import annotations

import hashlib
import os
import re
import stat
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from pypdf import PdfReader

from ..scope import Scope
from .adapters.folder import (
    RawArchiveContainmentError,
    RawArchiveWriteError,
    _directory_flags,
    _directory_is_within,
    _identity,
    _open_archive_directory,
    _open_external_archive_anchor,
    _open_regular_source,
    _source_is_current,
)
from .base import IngestError, IngestResult, commit_inbox_item

_SOURCE_ID = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,63})")
_RECEIVED_AT = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}")
_STEM = re.compile(r"[^a-z0-9]+")
_RECEIPT_TEXT = "Verified unlocked document is stored in the external archive."


class DocumentArchiveError(IngestError):
    pass


class DocumentChecksumError(DocumentArchiveError):
    pass


class DocumentEncryptedError(DocumentArchiveError):
    pass


@dataclass(frozen=True)
class DocumentIngest:
    result: IngestResult
    fingerprint: str
    reference: str
    archive_name: str
    reused: bool


def ingest_unlocked_pdf(
    scope: Scope,
    source: Path | str,
    *,
    archive_root: Path | str,
    source_id: str,
    received_at: str,
) -> DocumentIngest:
    """Copy one unlocked PDF into the external archive and commit its receipt."""
    if not _SOURCE_ID.fullmatch(source_id):
        raise DocumentArchiveError("document source identity is invalid")
    if not _RECEIVED_AT.fullmatch(received_at):
        raise DocumentArchiveError("document receipt time is invalid")

    source_path = Path(source)
    archive_name = _archive_name(source_path.name)
    payload = _read_current_pdf(source_path)
    fingerprint = hashlib.sha256(payload).hexdigest()
    _require_unlocked(payload)

    anchor = _open_external_archive_anchor(scope.root, Path(archive_root))
    archive_fd: int | None = None
    try:
        archive_fd = _open_archive_directory(anchor, scope.root)
        vault_identity = _identity(os.stat(scope.root, follow_symlinks=True))
        objects_fd: int | None = None
        names_fd: int | None = None
        quarantine_fd: int | None = None
        try:
            objects_fd = _open_child(archive_fd, "objects", vault_identity)
            names_fd = _open_child(archive_fd, "names", vault_identity)
            quarantine_fd = _open_child(archive_fd, "quarantine", vault_identity)
            reused = _store_object(
                objects_fd, quarantine_fd, fingerprint, payload, vault_identity
            )
            chosen_name = _assign_name(names_fd, archive_name, fingerprint)
            reference = f"archive:{fingerprint}"
            result = commit_inbox_item(
                scope,
                text=_RECEIPT_TEXT,
                title=chosen_name,
                source="document",
                source_id=source_id,
                received_at=received_at,
                source_ref=reference,
                body_ref=reference,
                sha256=fingerprint,
                mime="application/pdf",
                size=len(payload),
                slug_seed=hashlib.sha256(
                    f"{source_id}:{fingerprint}".encode("ascii")
                ).hexdigest(),
            )
        finally:
            for directory_fd in (objects_fd, names_fd, quarantine_fd):
                if directory_fd is not None:
                    os.close(directory_fd)
    finally:
        if archive_fd is not None:
            os.close(archive_fd)
        os.close(anchor.fd)

    return DocumentIngest(
        result=result,
        fingerprint=fingerprint,
        reference=reference,
        archive_name=chosen_name,
        reused=reused,
    )


def _archive_name(filename: str) -> str:
    leaf = Path(filename).name
    if leaf != filename or leaf in {"", ".", ".."}:
        raise DocumentArchiveError("document name is invalid")
    suffix = Path(leaf).suffix.lower()
    if suffix != ".pdf":
        raise DocumentArchiveError("document must be a pdf")
    stem = _STEM.sub("-", Path(leaf).stem.lower()).strip("-")[:80]
    if not stem:
        stem = "document"
    return f"{stem}.pdf"


def _read_current_pdf(path: Path) -> bytes:
    source, source_stat = _open_regular_source(path)
    try:
        payload = source.read()
        if not _source_is_current(path, source_stat):
            raise DocumentArchiveError("document changed during read")
    finally:
        source.close()
    if not payload:
        raise DocumentArchiveError("document is empty")
    return payload


def _require_unlocked(payload: bytes) -> None:
    try:
        reader = PdfReader(BytesIO(payload))
        encrypted = bool(reader.is_encrypted)
    except Exception as exc:
        raise DocumentArchiveError("document could not be read") from exc
    if encrypted:
        raise DocumentEncryptedError("encrypted documents are not archived")


def _open_child(parent_fd: int, name: str, vault_identity: tuple[int, int]) -> int:
    try:
        os.mkdir(name, 0o700, dir_fd=parent_fd)
    except FileExistsError:
        pass
    child = os.open(name, _directory_flags(), dir_fd=parent_fd)
    try:
        if _directory_is_within(child, vault_identity):
            raise RawArchiveContainmentError("raw archive must be outside the vault")
        return child
    except BaseException:
        os.close(child)
        raise


def _store_object(
    objects_fd: int,
    quarantine_fd: int,
    fingerprint: str,
    payload: bytes,
    vault_identity: tuple[int, int],
) -> bool:
    name = f"{fingerprint}.pdf"
    existing = _read_regular(objects_fd, name)
    if existing is not None:
        if hashlib.sha256(existing).hexdigest() != fingerprint:
            _quarantine(objects_fd, quarantine_fd, name)
            raise DocumentChecksumError("archive checksum mismatch")
        if _directory_is_within(objects_fd, vault_identity):
            raise RawArchiveContainmentError("raw archive must be outside the vault")
        return True
    _write_new(objects_fd, name, payload)
    stored = _read_regular(objects_fd, name)
    if stored != payload or hashlib.sha256(stored or b"").hexdigest() != fingerprint:
        if stored is not None:
            _quarantine(objects_fd, quarantine_fd, name)
        raise DocumentChecksumError("archive checksum mismatch")
    if _directory_is_within(objects_fd, vault_identity):
        _discard_new_object(objects_fd, name)
        raise RawArchiveContainmentError("raw archive must be outside the vault")
    return False


def _discard_new_object(directory_fd: int, name: str) -> None:
    try:
        os.unlink(name, dir_fd=directory_fd)
    except OSError as exc:
        raise RawArchiveWriteError(
            "raw document archival failed; cleanup failed"
        ) from exc


def _assign_name(names_fd: int, archive_name: str, fingerprint: str) -> str:
    chosen = archive_name
    recorded = _read_regular(names_fd, chosen)
    if recorded is not None and recorded != _name_record(fingerprint):
        stem = archive_name[:-4]
        chosen = f"{stem}-{fingerprint[:12]}.pdf"
        recorded = _read_regular(names_fd, chosen)
    if recorded is None:
        _write_new(names_fd, chosen, _name_record(fingerprint))
        return chosen
    if recorded != _name_record(fingerprint):
        raise DocumentArchiveError("archive name points at different content")
    return chosen


def _name_record(fingerprint: str) -> bytes:
    return f"{fingerprint}\n".encode("ascii")


def _read_regular(dir_fd: int, name: str) -> bytes | None:
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dir_fd)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise DocumentArchiveError("archive object could not be read") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise DocumentArchiveError("archive object must be a regular file")
        chunks: list[bytes] = []
        while True:
            block = os.read(fd, 65536)
            if not block:
                break
            chunks.append(block)
        return b"".join(chunks)
    finally:
        os.close(fd)


def _write_new(dir_fd: int, name: str, payload: bytes) -> None:
    fd = os.open(
        name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
        dir_fd=dir_fd,
    )
    try:
        view = memoryview(payload)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise DocumentArchiveError("archive object could not be written")
            view = view[written:]
        os.fsync(fd)
    except BaseException:
        os.close(fd)
        try:
            os.unlink(name, dir_fd=dir_fd)
        except OSError:
            pass
        raise
    os.close(fd)


def _quarantine(objects_fd: int, quarantine_fd: int, name: str) -> None:
    payload = _read_regular(objects_fd, name)
    if payload is None:
        raise DocumentChecksumError("archive checksum mismatch")
    for attempt in range(100):
        destination = name if attempt == 0 else f"{attempt}-{name}"
        try:
            _write_new(quarantine_fd, destination, payload)
        except FileExistsError:
            continue
        os.unlink(name, dir_fd=objects_fd)
        return
    raise DocumentChecksumError("archive checksum mismatch; quarantine failed")
