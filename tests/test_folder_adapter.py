"""folder-drop adapter (spec §8.1, step 5).

Done-when: a file dropped in appears in 00-inbox/active/ within a minute, with
PII stripped before anything is written. Tests use temp dirs only; the real
vault is never touched. All PII values are synthetic.
"""
import inspect
from pathlib import Path

import pytest

import app.ingest.adapters.folder as folder
from app.entities import EntitySelectionError
from app.ingest.adapters.folder import (
    FolderSourceRestoreError,
    extract_text,
    process_drop,
    sha256_of,
    watch,
)
from app.ingest.base import IngestCommitError, IngestPathCollision, IngestResult
from app.ingest.pii import verhoeff_check_digit
from app.schema import validate_file
from app.scope import Scope
from tests.conftest import git_changed_paths, git_entity_vault, git_head, git_head_message, git_history_contains

FIXTURES = Path(__file__).parent / "fixtures"


def _valid_aadhaar() -> str:
    base = "40001010001"
    return base + str(verhoeff_check_digit(base))


def _read_fm(note: Path) -> str:
    return note.read_text()


def _vault(tmp_path, name):
    return git_entity_vault(
        tmp_path / name, ("synthetic",), {"synthetic/00-inbox/active/.gitkeep": ""}
    )


def test_drop_txt_lands_in_inbox_with_pii_stripped(tmp_path):
    vault = _vault(tmp_path, "vault-pii")
    dropbox = tmp_path / "dropbox"
    raw = tmp_path / "raw"
    dropbox.mkdir(parents=True)
    aadhaar = _valid_aadhaar()
    dropped = dropbox / "letter.txt"
    dropped.write_text(f"PAN ABCDE1234F and aadhaar {aadhaar}. Meeting Tuesday.\n")

    result = process_drop(Scope(vault, "synthetic"), dropped, raw_archive=raw)
    note = result.path
    assert result.created is True
    assert result.commit_oid
    assert git_head_message(vault) == "ingest: add redacted receipt"
    assert git_changed_paths(vault) == [note.relative_to(vault).as_posix()]

    # landed in the right place
    assert note.parent == vault / "synthetic" / "00-inbox" / "active"
    body = note.read_text()
    assert "sub: triage" in body
    assert "pii_quarantined: true" in body
    assert "pan" in body and "aadhaar" in body  # pii_classes listed

    # PII redacted, raw values gone
    assert "[PAN]" in body and "[AADHAAR]" in body
    assert "ABCDE1234F" not in body
    assert aadhaar not in body
    assert "Meeting Tuesday" in body  # non-PII preserved

    # front-matter validates against the shared schema
    ok, problems = validate_file(note)
    assert ok, problems


def test_raw_original_leaves_the_vault_never_committed(tmp_path):
    vault = _vault(tmp_path, "vault-raw")
    dropbox = tmp_path / "dropbox"
    raw = tmp_path / "raw"
    dropbox.mkdir(parents=True)
    dropped = dropbox / "secret.txt"
    dropped.write_text("PAN ABCDE1234F\n")

    process_drop(Scope(vault, "synthetic"), dropped, raw_archive=raw)

    # original moved out of the dropbox, into the raw archive (outside vault)
    assert not dropped.exists()
    archived = list(raw.iterdir())
    assert len(archived) == 1
    assert "ABCDE1234F" in archived[0].read_text()          # raw kept, outside git
    assert not str(archived[0]).startswith(str(vault))       # NOT inside the vault

    # the raw PII value appears nowhere under the vault tree
    for p in vault.rglob("*.md"):
        assert "ABCDE1234F" not in p.read_text()
    assert not git_history_contains(vault, "ABCDE1234F")


def test_clean_file_is_not_quarantined(tmp_path):
    vault = _vault(tmp_path, "vault-clean")
    dropbox = tmp_path / "dropbox"
    dropbox.mkdir(parents=True)
    dropped = dropbox / "notes.txt"
    dropped.write_text("Quarterly planning notes. No identifiers here.\n")

    note = process_drop(
        Scope(vault, "synthetic"), dropped, raw_archive=tmp_path / "raw"
    ).path
    body = note.read_text()
    assert "pii_quarantined: false" in body
    assert "pii_classes: []" in body


def test_drop_pdf_extracts_and_redacts(tmp_path):
    """The done-when's headline case: a PDF dropped in appears in the inbox with
    PII stripped."""
    sample = FIXTURES / "sample.pdf"
    assert sample.exists(), "run scripts to generate tests/fixtures/sample.pdf"
    # sanity: the PDF really carries the PII we expect to strip
    raw_text = extract_text(sample)
    assert "ABCDE1234F" in raw_text

    vault = _vault(tmp_path, "vault-pdf")
    dropbox = tmp_path / "dropbox"
    dropbox.mkdir(parents=True)
    dropped = dropbox / "scan.pdf"
    dropped.write_bytes(sample.read_bytes())

    note = process_drop(
        Scope(vault, "synthetic"), dropped, raw_archive=tmp_path / "raw"
    ).path
    body = note.read_text()
    assert "[PAN]" in body
    assert "ABCDE1234F" not in body
    ok, problems = validate_file(note)
    assert ok, problems


def test_duplicate_drop_is_no_op_and_leaves_new_source_in_place(tmp_path):
    vault = _vault(tmp_path, "vault-duplicate")
    raw = tmp_path / "raw"
    first_src = tmp_path / "first/note.txt"
    first_src.parent.mkdir()
    first_src.write_text("same synthetic content\n", encoding="utf-8")
    scope = Scope(vault, "synthetic")
    first = process_drop(scope, first_src, raw_archive=raw)
    head = git_head(vault)
    duplicate_src = tmp_path / "second/note.txt"
    duplicate_src.parent.mkdir()
    duplicate_src.write_text("same synthetic content\n", encoding="utf-8")
    duplicate = process_drop(scope, duplicate_src, raw_archive=raw)
    assert duplicate == IngestResult(first.path, duplicate.envelope, False, None)
    assert duplicate_src.exists()
    assert git_head(vault) == head


def test_existing_archive_destination_is_never_overwritten(tmp_path):
    vault = _vault(tmp_path, "vault-collision")
    src = tmp_path / "drop/note.txt"
    src.parent.mkdir()
    src.write_text("new source\n", encoding="utf-8")
    digest = sha256_of(src)
    raw = tmp_path / "raw"
    raw.mkdir()
    archived = raw / f"{digest[:16]}-{src.name}"
    archived.write_text("pre-existing archive\n", encoding="utf-8")
    with pytest.raises(IngestPathCollision):
        process_drop(Scope(vault, "synthetic"), src, raw_archive=raw)
    assert src.exists()
    assert archived.read_text(encoding="utf-8") == "pre-existing archive\n"


def test_commit_failure_restores_raw_source_to_drop_location(tmp_path, monkeypatch):
    vault = _vault(tmp_path, "vault-restore")
    src = tmp_path / "drop/note.txt"
    src.parent.mkdir()
    src.write_text("source survives failure\n", encoding="utf-8")
    raw = tmp_path / "raw"

    def fail_commit(*args, **kwargs):
        raise IngestCommitError("synthetic commit failure")

    monkeypatch.setattr(folder, "commit_inbox_item", fail_commit)
    with pytest.raises(IngestCommitError, match="synthetic commit failure"):
        process_drop(Scope(vault, "synthetic"), src, raw_archive=raw)
    assert src.read_text(encoding="utf-8") == "source survives failure\n"
    assert list(raw.iterdir()) == []


def test_commit_failure_never_overwrites_reoccupied_drop_path(tmp_path, monkeypatch):
    vault = _vault(tmp_path, "vault-reoccupied")
    src = tmp_path / "drop/note.txt"
    src.parent.mkdir()
    src.write_text("archived original\n", encoding="utf-8")
    raw = tmp_path / "raw"

    def fail_after_reoccupying(*args, **kwargs):
        src.write_text("new occupant\n", encoding="utf-8")
        raise IngestCommitError("synthetic commit failure")

    monkeypatch.setattr(folder, "commit_inbox_item", fail_after_reoccupying)
    with pytest.raises(FolderSourceRestoreError, match="original drop path is occupied"):
        process_drop(Scope(vault, "synthetic"), src, raw_archive=raw)
    assert src.read_text(encoding="utf-8") == "new occupant\n"
    archived = list(raw.iterdir())
    assert len(archived) == 1
    assert archived[0].read_text(encoding="utf-8") == "archived original\n"


def test_process_drop_interface_accepts_only_bound_scope_identity():
    parameters = inspect.signature(process_drop).parameters

    assert tuple(parameters) == ("scope", "source", "raw_archive", "now")
    assert parameters["scope"].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert parameters["source"].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert parameters["raw_archive"].kind is inspect.Parameter.KEYWORD_ONLY
    assert parameters["now"].kind is inspect.Parameter.KEYWORD_ONLY


def test_bound_scope_alone_selects_receipt_entity(tmp_path):
    vault = git_entity_vault(
        tmp_path / "vault",
        ("alpha", "beta"),
        {
            "alpha/00-inbox/active/.gitkeep": "",
            "beta/00-inbox/active/.gitkeep": "",
        },
    )
    source = tmp_path / "drop/item.txt"
    source.parent.mkdir()
    source.write_bytes(b"scope selects alpha\n")

    result = process_drop(Scope(vault, "alpha"), source, raw_archive=tmp_path / "raw")

    assert result.path.is_relative_to(vault / "alpha/00-inbox/active")
    assert not list((vault / "beta/00-inbox/active").glob("*.md"))


def test_unknown_folder_entity_is_rejected_before_source_move(tmp_path):
    vault = git_entity_vault(
        tmp_path / "vault", ("alpha",), {"alpha/00-inbox/active/.gitkeep": ""}
    )
    (vault / "directory-only").mkdir()
    source = tmp_path / "drop/item.txt"
    source.parent.mkdir()
    source.write_bytes(b"source bytes stay here\n")
    raw = tmp_path / "raw"
    before = git_head(vault)

    with pytest.raises(EntitySelectionError):
        process_drop(Scope(vault, "directory-only"), source, raw_archive=raw)

    assert source.read_bytes() == b"source bytes stay here\n"
    assert not raw.exists()
    assert git_head(vault) == before


def test_unknown_watcher_entity_is_rejected_before_side_effects(tmp_path, monkeypatch):
    vault = git_entity_vault(
        tmp_path / "vault", ("alpha",), {"alpha/00-inbox/active/.gitkeep": ""}
    )
    (vault / "directory-only").mkdir()
    source = tmp_path / "source/item.txt"
    source.parent.mkdir()
    source.write_bytes(b"source bytes stay here\n")
    dropbox = tmp_path / "missing-dropbox"
    raw = tmp_path / "raw"
    before = git_head(vault)

    class ForbiddenObserver:
        def __init__(self):
            raise AssertionError("observer constructed before watcher validation")

    monkeypatch.setattr("watchdog.observers.Observer", ForbiddenObserver)

    with pytest.raises(EntitySelectionError):
        watch(vault, "directory-only", dropbox, raw)

    assert not dropbox.exists()
    assert source.read_bytes() == b"source bytes stay here\n"
    assert not raw.exists()
    assert git_head(vault) == before
