"""Email adapter (spec §10 step 10): IMAP poll into the step-5 envelope.

Done-when: same envelope, same PII filter, no second code path. Built with
email.message.EmailMessage — no network. Synthetic PII only.
"""
import hashlib
from email.message import EmailMessage
from email.policy import SMTP

from app.scope import Scope
from app.schema import validate_file
from app.inbox import split_front_matter
from app.ingest.adapters.email import process_email
from app.ingest.adapters.folder import process_drop
from app.ingest.pii import verhoeff_check_digit
from tests.conftest import git_head, git_vault


def _valid_aadhaar() -> str:
    base = "40001010001"
    return base + str(verhoeff_check_digit(base))


def _msg(body: str) -> EmailMessage:
    m = EmailMessage()
    m["Subject"] = "Vendor invoice Q3"
    m["From"] = "Accounts <accounts@vendor.example>"
    m["Message-ID"] = "<abc123@vendor.example>"
    m["Date"] = "Wed, 06 Aug 2026 10:00:00 +0000"
    m.set_content(body)
    return m


def test_email_lands_in_inbox_with_envelope_and_pii_stripped(tmp_path):
    vault = git_vault(tmp_path / "vault", {"synthetic/00-inbox/active/.gitkeep": ""})
    aadhaar = _valid_aadhaar()
    result = process_email(
        vault, "synthetic",
        _msg(f"Please pay. PAN ABCDE1234F, aadhaar {aadhaar}. Thanks."),
    )
    note = result.path
    assert note.parent == vault / "synthetic" / "00-inbox" / "active"
    fm, body = split_front_matter(note.read_text())

    assert fm["source"] == "email"
    assert fm["sub"] == "triage"
    assert "accounts@vendor.example" in fm["sender"]
    assert str(fm["received_at"]).startswith("2026-08-06")  # YAML parses it to datetime
    assert fm["body_ref"] == "imap:abc123@vendor.example"
    assert fm["pii_quarantined"] is True

    assert "[PAN]" in body and "[AADHAAR]" in body
    assert "ABCDE1234F" not in body and aadhaar not in body

    ok, problems = validate_file(note)
    assert ok, problems


def test_same_filter_and_envelope_as_folder_no_second_path(tmp_path):
    """The same body, ingested by email and by folder, yields the same redacted
    summary and the same PII classes — proof both go through one write path."""
    body = "Ref PAN ABCDE1234F and phone +91 9812345678."

    # via email
    evault = git_vault(tmp_path / "ve", {"synthetic/00-inbox/active/.gitkeep": ""})
    enote = process_email(evault, "synthetic", _msg(body)).path
    efm, ebody = split_front_matter(enote.read_text())

    # via folder (same text)
    drop = tmp_path / "dropbox" / "note.txt"
    drop.parent.mkdir(parents=True)
    drop.write_text(body)
    fvault = git_vault(tmp_path / "vf", {"synthetic/00-inbox/active/.gitkeep": ""})
    fnote = process_drop(fvault, "synthetic", drop, raw_archive=tmp_path / "raw").path
    ffm, fbody = split_front_matter(fnote.read_text())

    assert ebody.strip() == fbody.strip()                  # identical redaction
    assert efm["pii_classes"] == ffm["pii_classes"]        # identical classes
    assert "[PAN]" in ebody and "[PHONE]" in ebody
    # but the source differs — the only thing the adapter sets
    assert efm["source"] == "email" and ffm["source"] == "folder"


def test_multipart_prefers_text_plain(tmp_path):
    m = EmailMessage()
    m["Subject"] = "Mixed"
    m["From"] = "a@b.example"
    m["Message-ID"] = "<m1@b.example>"
    m.set_content("Plain body with PAN ABCDE1234F.")
    m.add_alternative("<p>HTML body with PAN ABCDE1234F.</p>", subtype="html")

    vault = git_vault(tmp_path / "v", {"synthetic/00-inbox/active/.gitkeep": ""})
    note = process_email(vault, "synthetic", m).path
    _fm, body = split_front_matter(note.read_text())
    assert "[PAN]" in body
    assert "Plain body" in body


def test_email_hash_represents_deterministic_message_bytes(tmp_path):
    vault = git_vault(tmp_path / "vault", {"synthetic/00-inbox/active/.gitkeep": ""})
    msg = _msg("stable body\n")
    expected = hashlib.sha256(msg.as_bytes(policy=SMTP)).hexdigest()
    result = process_email(vault, "synthetic", msg)
    fm, _body = split_front_matter(result.path.read_text(encoding="utf-8"))
    assert fm["sha256"] == expected


def test_duplicate_email_creates_no_second_commit(tmp_path):
    vault = git_vault(tmp_path / "vault", {"synthetic/00-inbox/active/.gitkeep": ""})
    first = process_email(vault, "synthetic", _msg("same body\n"))
    before = git_head(vault)
    duplicate = process_email(vault, "synthetic", _msg("same body\n"))
    assert duplicate.path == first.path
    assert duplicate.created is False
    assert duplicate.commit_oid is None
    assert git_head(vault) == before
