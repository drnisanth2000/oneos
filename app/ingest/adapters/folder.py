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
import shutil
from datetime import datetime
from pathlib import Path

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


def _restore_raw(archived: Path, src: Path, reason: str) -> None:
    if src.exists():
        raise FolderSourceRestoreError(f"{reason} and original drop path is occupied")
    try:
        shutil.move(str(archived), str(src))
    except OSError as exc:
        raise FolderSourceRestoreError(f"{reason} and raw source restoration failed") from exc


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def mime_of(path: Path) -> str:
    return _MIME.get(path.suffix.lower(), "application/octet-stream")


def extract_text(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    if ext in _TEXT_EXT:
        return path.read_text(encoding="utf-8", errors="replace")
    return ""  # unknown binary — no text; metadata only


def process_drop(
    vault: Path | str,
    entity: str,
    src: Path | str,
    *,
    raw_archive: Path | str,
    scope: Scope | None = None,
    now: datetime | None = None,
) -> IngestResult:
    """Ingest one dropped file. Returns the shared ingest result.
    The raw original is moved into `raw_archive` (outside the vault); only the
    redacted note enters the vault via the shared write path."""
    vault = Path(vault)
    src = Path(src)
    raw_archive = Path(raw_archive)
    scope = scope or Scope(vault, entity)
    entity = scope.require_entity(entity)
    now = now or datetime.now()

    digest = sha256_of(src)
    size = src.stat().st_size
    mime = mime_of(src)
    text = extract_text(src)

    archived = raw_archive / f"{digest[:16]}-{src.name}"
    source_ref = f"raw:{archived.name}"
    kwargs = {
        "text": text,
        "title": src.name,
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

    _path, env, _rendered = prepare_inbox_item(scope, entity, **kwargs)
    existing = find_tracked_receipt(scope, entity, env)
    if existing is not None:
        return IngestResult(existing, env, False, None)
    if archived.exists():
        raise IngestPathCollision("raw archive destination already exists")

    raw_archive.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(archived))
    try:
        result = commit_inbox_item(scope, entity, **kwargs)
    except IngestError as exc:
        try:
            _restore_raw(archived, src, "receipt commit failed")
        except FolderSourceRestoreError as restore_exc:
            raise restore_exc from exc
        raise
    if not result.created:
        _restore_raw(archived, src, "duplicate detected after archive")
    return result


def watch(
    vault: Path | str,
    entity: str,
    dropbox: Path | str,
    raw_archive: Path | str,
) -> None:  # pragma: no cover - I/O glue over process_drop
    """Block, processing every file that lands in `dropbox`."""
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer

    dropbox = Path(dropbox)
    dropbox.mkdir(parents=True, exist_ok=True)

    class _Handler(FileSystemEventHandler):
        def on_created(self, event):
            if not event.is_directory:
                process_drop(vault, entity, event.src_path, raw_archive=raw_archive)

    observer = Observer()
    observer.schedule(_Handler(), str(dropbox), recursive=False)
    observer.start()
    try:
        while True:
            observer.join(1)
    finally:
        observer.stop()
        observer.join()
