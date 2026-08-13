from pathlib import Path

import pytest

from app.ingest.base import IngestRepositoryError, IngestResult, prepare_inbox_item
from app.scope import Scope
from tests.conftest import git_vault


def _vault(tmp_path: Path) -> Path:
    return git_vault(tmp_path, {"synthetic/00-inbox/active/.gitkeep": ""})


def _kwargs() -> dict:
    return {
        "text": "Planning note with PAN ABCDE1234F.",
        "title": "Planning note",
        "source": "folder",
        "source_id": "0123456789abcdef",
        "received_at": "2026-08-12T10:00:00",
        "source_ref": "raw:0123456789abcdef-note.txt",
        "body_ref": "raw:0123456789abcdef-note.txt",
        "sha256": "0123456789abcdef" * 4,
        "mime": "text/plain",
        "size": 37,
        "slug_seed": "0123456789abcdef",
    }


def test_prepare_returns_redacted_schema_ready_receipt_without_writing(tmp_path):
    vault = _vault(tmp_path)
    path, env, rendered = prepare_inbox_item(Scope(vault), "synthetic", **_kwargs())

    assert path == vault / "synthetic/00-inbox/active/planning-note-01234567.md"
    assert env.sha256 == "0123456789abcdef" * 4
    assert "[PAN]" in rendered
    assert "ABCDE1234F" not in rendered
    assert not path.exists()


def test_prepare_rejects_adapter_receipt_without_source_hash(tmp_path):
    vault = _vault(tmp_path)
    kwargs = {**_kwargs(), "sha256": None}
    with pytest.raises(IngestRepositoryError, match="requires sha256"):
        prepare_inbox_item(Scope(vault), "synthetic", **kwargs)
