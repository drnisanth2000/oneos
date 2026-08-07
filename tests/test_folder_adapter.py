"""folder-drop adapter (spec §8.1, step 5).

Done-when: a file dropped in appears in 00-inbox/active/ within a minute, with
PII stripped before anything is written. Tests use temp dirs only; the real
vault is never touched. All PII values are synthetic.
"""
from pathlib import Path

from app.ingest.adapters.folder import process_drop, extract_text
from app.ingest.pii import verhoeff_check_digit
from app.schema import validate_file

FIXTURES = Path(__file__).parent / "fixtures"


def _valid_aadhaar() -> str:
    base = "40001010001"
    return base + str(verhoeff_check_digit(base))


def _read_fm(note: Path) -> str:
    return note.read_text()


def test_drop_txt_lands_in_inbox_with_pii_stripped(tmp_path):
    vault = tmp_path / "vault"
    dropbox = tmp_path / "dropbox"
    raw = tmp_path / "raw"
    dropbox.mkdir(parents=True)
    aadhaar = _valid_aadhaar()
    dropped = dropbox / "letter.txt"
    dropped.write_text(f"PAN ABCDE1234F and aadhaar {aadhaar}. Meeting Tuesday.\n")

    note = process_drop(vault, "acme", dropped, raw_archive=raw)

    # landed in the right place
    assert note.parent == vault / "acme" / "00-inbox" / "active"
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
    vault = tmp_path / "vault"
    dropbox = tmp_path / "dropbox"
    raw = tmp_path / "raw"
    dropbox.mkdir(parents=True)
    dropped = dropbox / "secret.txt"
    dropped.write_text("PAN ABCDE1234F\n")

    process_drop(vault, "acme", dropped, raw_archive=raw)

    # original moved out of the dropbox, into the raw archive (outside vault)
    assert not dropped.exists()
    archived = list(raw.iterdir())
    assert len(archived) == 1
    assert "ABCDE1234F" in archived[0].read_text()          # raw kept, outside git
    assert not str(archived[0]).startswith(str(vault))       # NOT inside the vault

    # the raw PII value appears nowhere under the vault tree
    for p in vault.rglob("*.md"):
        assert "ABCDE1234F" not in p.read_text()


def test_clean_file_is_not_quarantined(tmp_path):
    vault = tmp_path / "vault"
    dropbox = tmp_path / "dropbox"
    dropbox.mkdir(parents=True)
    dropped = dropbox / "notes.txt"
    dropped.write_text("Quarterly planning notes. No identifiers here.\n")

    note = process_drop(vault, "acme", dropped, raw_archive=tmp_path / "raw")
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

    vault = tmp_path / "vault"
    dropbox = tmp_path / "dropbox"
    dropbox.mkdir(parents=True)
    dropped = dropbox / "scan.pdf"
    dropped.write_bytes(sample.read_bytes())

    note = process_drop(vault, "acme", dropped, raw_archive=tmp_path / "raw")
    body = note.read_text()
    assert "[PAN]" in body
    assert "ABCDE1234F" not in body
    ok, problems = validate_file(note)
    assert ok, problems
