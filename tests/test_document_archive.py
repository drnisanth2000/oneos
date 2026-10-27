"""Phase 3 slice 1: unlocked PDF archive and redacted receipt."""
from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path

import pytest
from pypdf import PdfWriter

from app.ingest import document_archive
from app.ingest.adapters.folder import RawArchiveContainmentError
from app.ingest.base import IngestIdentityConflict
from app.ingest.document_archive import (
    DocumentArchiveError,
    RawArchiveCleanupError,
    DocumentChecksumError,
    DocumentEncryptedError,
    ingest_unlocked_pdf,
)
from app.schema import validate_file
from app.scope import Scope
from tests.conftest import git_changed_paths, git_entity_vault, git_head_message, git_history_contains

_MARKER = "SYNTHETIC-DOCUMENT-MARKER"
_RECEIVED_AT = "2026-10-26T09:30:00"


def _vault(tmp_path: Path) -> Path:
    return git_entity_vault(
        tmp_path / "vault",
        ("synthetic",),
        {"synthetic/00-inbox/active/.gitkeep": ""},
    )


def _pdf(marker: str) -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.add_metadata({"/Title": marker})
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _write_pdf(directory: Path, name: str, payload: bytes) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(payload)
    return path


def test_unlocked_pdf_is_archived_outside_the_vault_and_receipt_omits_text(tmp_path):
    vault = _vault(tmp_path)
    payload = _pdf(_MARKER)
    source = _write_pdf(tmp_path / "incoming", "statement.pdf", payload)
    archive = tmp_path / "archive"

    archived = ingest_unlocked_pdf(
        Scope(vault, "synthetic"),
        source,
        archive_root=archive,
        source_id="case-one",
        received_at=_RECEIVED_AT,
    )

    note = archived.result.path
    body = note.read_text(encoding="utf-8")
    fingerprint = hashlib.sha256(payload).hexdigest()
    assert archived.result.created is True
    assert archived.reused is False
    assert archived.fingerprint == fingerprint
    assert archived.reference == f"archive:{fingerprint}"
    assert str(archive) not in body
    assert str(source) not in body
    assert _MARKER not in body
    assert "Verified unlocked document is stored in the external archive." in body
    assert "sub: triage" in body
    assert "source: document" in body
    assert note.parent == vault / "synthetic" / "00-inbox" / "active"
    ok, problems = validate_file(note)
    assert ok, problems
    assert git_head_message(vault) == "ingest: add redacted receipt"
    assert git_changed_paths(vault) == [note.relative_to(vault).as_posix()]
    assert git_history_contains(vault, _MARKER) is False
    stored = archive / "objects" / f"{fingerprint}.pdf"
    assert stored.read_bytes() == payload
    assert source.read_bytes() == payload
    assert (archive / "names" / "statement.pdf").read_text(encoding="ascii") == (
        fingerprint + "\n"
    )


def test_same_fingerprint_reuses_one_archive_object_and_keeps_a_second_receipt(tmp_path):
    vault = _vault(tmp_path)
    payload = _pdf(_MARKER)
    first = _write_pdf(tmp_path / "incoming", "statement.pdf", payload)
    second = _write_pdf(tmp_path / "later", "statement.pdf", payload)
    archive = tmp_path / "archive"
    scope = Scope(vault, "synthetic")

    initial = ingest_unlocked_pdf(
        scope, first, archive_root=archive, source_id="case-one", received_at=_RECEIVED_AT,
    )
    follow = ingest_unlocked_pdf(
        scope, second, archive_root=archive, source_id="case-two", received_at=_RECEIVED_AT,
    )

    objects = list((archive / "objects").iterdir())
    assert len(objects) == 1
    assert follow.reused is True
    assert follow.fingerprint == initial.fingerprint
    assert follow.result.path != initial.result.path
    assert follow.result.created is True


def test_same_case_is_a_receipt_no_op(tmp_path):
    vault = _vault(tmp_path)
    source = _write_pdf(tmp_path / "incoming", "statement.pdf", _pdf(_MARKER))
    archive = tmp_path / "archive"
    scope = Scope(vault, "synthetic")
    kwargs = {
        "archive_root": archive,
        "source_id": "case-one",
        "received_at": _RECEIVED_AT,
    }

    first = ingest_unlocked_pdf(scope, source, **kwargs)
    second = ingest_unlocked_pdf(scope, source, **kwargs)

    assert second.result.created is False
    assert second.result.path == first.result.path
    assert second.reused is True
    assert len(list((archive / "objects").iterdir())) == 1


def test_different_documents_with_the_same_name_do_not_overwrite(tmp_path):
    vault = _vault(tmp_path)
    archive = tmp_path / "archive"
    scope = Scope(vault, "synthetic")
    first_payload = _pdf("first-marker")
    second_payload = _pdf("second-marker")
    first = _write_pdf(tmp_path / "one", "statement.pdf", first_payload)
    second = _write_pdf(tmp_path / "two", "statement.pdf", second_payload)

    initial = ingest_unlocked_pdf(
        scope, first, archive_root=archive, source_id="case-one", received_at=_RECEIVED_AT,
    )
    follow = ingest_unlocked_pdf(
        scope, second, archive_root=archive, source_id="case-two", received_at=_RECEIVED_AT,
    )

    assert follow.archive_name == f"statement-{follow.fingerprint[:12]}.pdf"
    assert (archive / "objects" / f"{initial.fingerprint}.pdf").read_bytes() == first_payload
    assert (archive / "objects" / f"{follow.fingerprint}.pdf").read_bytes() == second_payload
    assert (archive / "names" / "statement.pdf").read_text(encoding="ascii").startswith(
        initial.fingerprint
    )


def test_checksum_mismatch_is_quarantined_and_not_treated_as_archived(tmp_path):
    vault = _vault(tmp_path)
    payload = _pdf(_MARKER)
    source = _write_pdf(tmp_path / "incoming", "statement.pdf", payload)
    archive = tmp_path / "archive"
    fingerprint = hashlib.sha256(payload).hexdigest()
    planted = archive / "objects"
    planted.mkdir(parents=True)
    (planted / f"{fingerprint}.pdf").write_bytes(b"corrupt-copy")
    scope = Scope(vault, "synthetic")

    with pytest.raises(DocumentChecksumError):
        ingest_unlocked_pdf(
            scope, source, archive_root=archive, source_id="case-one", received_at=_RECEIVED_AT,
        )

    assert not (planted / f"{fingerprint}.pdf").exists()
    assert (archive / "quarantine" / f"{fingerprint}.pdf").read_bytes() == b"corrupt-copy"
    assert git_head_message(vault) == "init"
    restored = ingest_unlocked_pdf(
        scope, source, archive_root=archive, source_id="case-one", received_at=_RECEIVED_AT,
    )
    assert restored.reused is False
    assert (planted / f"{fingerprint}.pdf").read_bytes() == payload


def test_archive_inside_the_vault_is_refused(tmp_path):
    vault = _vault(tmp_path)
    source = _write_pdf(tmp_path / "incoming", "statement.pdf", _pdf(_MARKER))

    with pytest.raises(RawArchiveContainmentError):
        ingest_unlocked_pdf(
            Scope(vault, "synthetic"),
            source,
            archive_root=vault / "synthetic" / "archive",
            source_id="case-one",
            received_at=_RECEIVED_AT,
        )

    assert git_head_message(vault) == "init"


def test_archive_reparented_during_write_does_not_keep_the_pdf(tmp_path, monkeypatch):
    vault = _vault(tmp_path)
    payload = _pdf(_MARKER)
    source = _write_pdf(tmp_path / "incoming", "statement.pdf", payload)
    archive = tmp_path / "archive"
    moved = vault / "synthetic" / "moved-archive"
    real_fsync = document_archive.os.fsync
    reparented = False

    def reparent_after_write(fd):
        nonlocal reparented
        real_fsync(fd)
        if not reparented:
            archive.rename(moved)
            reparented = True

    monkeypatch.setattr(document_archive.os, "fsync", reparent_after_write)

    with pytest.raises(RawArchiveContainmentError, match="outside the vault"):
        ingest_unlocked_pdf(
            Scope(vault, "synthetic"),
            source,
            archive_root=archive,
            source_id="case-one",
            received_at=_RECEIVED_AT,
        )

    assert reparented is True
    assert source.read_bytes() == payload
    assert list(moved.rglob("*.pdf")) == []
    assert git_head_message(vault) == "init"


def test_reparented_archive_cleanup_failure_is_a_hard_failure(tmp_path, monkeypatch):
    vault = _vault(tmp_path)
    payload = _pdf(_MARKER)
    source = _write_pdf(tmp_path / "incoming", "statement.pdf", payload)
    archive = tmp_path / "archive"
    moved = vault / "synthetic" / "moved-archive"
    real_fsync = document_archive.os.fsync
    real_unlink = document_archive.os.unlink

    def reparent_after_write(fd):
        real_fsync(fd)
        if archive.exists():
            archive.rename(moved)

    def fail_archive_cleanup(path, *args, dir_fd=None, **kwargs):
        if dir_fd is not None:
            raise OSError("synthetic cleanup failure")
        return real_unlink(path, *args, dir_fd=dir_fd, **kwargs)

    monkeypatch.setattr(document_archive.os, "fsync", reparent_after_write)
    monkeypatch.setattr(document_archive.os, "unlink", fail_archive_cleanup)

    with pytest.raises(RawArchiveCleanupError, match="cleanup failed"):
        ingest_unlocked_pdf(
            Scope(vault, "synthetic"),
            source,
            archive_root=archive,
            source_id="case-one",
            received_at=_RECEIVED_AT,
        )

    assert source.read_bytes() == payload
    stored = list(moved.rglob("*.pdf"))
    assert len(stored) == 1
    assert stored[0].read_bytes() == payload
    assert git_head_message(vault) == "init"


def test_encrypted_pdf_is_refused_without_a_password_attempt(tmp_path):
    vault = _vault(tmp_path)
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.encrypt("synthetic-password")
    buffer = BytesIO()
    writer.write(buffer)
    source = _write_pdf(tmp_path / "incoming", "locked.pdf", buffer.getvalue())
    archive = tmp_path / "archive"

    with pytest.raises(DocumentEncryptedError):
        ingest_unlocked_pdf(
            Scope(vault, "synthetic"),
            source,
            archive_root=archive,
            source_id="case-one",
            received_at=_RECEIVED_AT,
        )

    assert not archive.exists()
    assert git_head_message(vault) == "init"


@pytest.mark.parametrize(
    "received_at",
    ["2026-10-26T09:30:00Z", "2026-10-26T09:30:00+00:00", "2026-10-26T09:30:00-05:30"],
)
def test_received_time_with_a_timezone_offset_is_accepted(tmp_path, received_at):
    vault = _vault(tmp_path)
    source = _write_pdf(tmp_path / "incoming", "statement.pdf", _pdf(_MARKER))

    archived = ingest_unlocked_pdf(
        Scope(vault, "synthetic"),
        source,
        archive_root=tmp_path / "archive",
        source_id="case-one",
        received_at=received_at,
    )

    assert archived.result.created is True
    ok, problems = validate_file(archived.result.path)
    assert ok, problems


@pytest.mark.parametrize(
    "received_at",
    ["2026-13-40T09:30:00", "2026-10-26T25:30:00", "2026-10-26T09:30:00+0500", "2026-10-26"],
)
def test_invalid_received_time_is_refused_before_the_archive(tmp_path, received_at):
    vault = _vault(tmp_path)
    source = _write_pdf(tmp_path / "incoming", "statement.pdf", _pdf(_MARKER))
    archive = tmp_path / "archive"

    with pytest.raises(DocumentArchiveError, match="receipt time"):
        ingest_unlocked_pdf(
            Scope(vault, "synthetic"),
            source,
            archive_root=archive,
            source_id="case-one",
            received_at=received_at,
        )

    assert not archive.exists()
    assert git_head_message(vault) == "init"


def test_conflicting_receipt_identity_leaves_no_new_archive_object(tmp_path):
    vault = _vault(tmp_path)
    first = _write_pdf(tmp_path / "one", "statement.pdf", _pdf("first"))
    second = _write_pdf(tmp_path / "two", "other.pdf", _pdf("second"))
    archive = tmp_path / "archive"
    scope = Scope(vault, "synthetic")
    ingest_unlocked_pdf(
        scope, first, archive_root=archive, source_id="case-one", received_at=_RECEIVED_AT,
    )
    objects_before = sorted(path.name for path in (archive / "objects").iterdir())
    names_before = sorted(path.name for path in (archive / "names").iterdir())
    head_before = git_head_message(vault)

    with pytest.raises(IngestIdentityConflict):
        ingest_unlocked_pdf(
            scope, second, archive_root=archive, source_id="case-one", received_at=_RECEIVED_AT,
        )

    assert sorted(path.name for path in (archive / "objects").iterdir()) == objects_before
    assert sorted(path.name for path in (archive / "names").iterdir()) == names_before
    assert git_head_message(vault) == head_before
