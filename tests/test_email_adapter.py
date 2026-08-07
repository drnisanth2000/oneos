"""Email adapter (spec §10 step 10): IMAP poll into the step-5 envelope.

Done-when: same envelope, same PII filter, no second code path. Built with
email.message.EmailMessage — no network. Synthetic PII only.
"""
from email.message import EmailMessage

from app.scope import Scope
from app.schema import validate_file
from app.inbox import split_front_matter
from app.ingest.adapters.email import process_email
from app.ingest.adapters.folder import process_drop
from app.ingest.pii import verhoeff_check_digit


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
    vault = tmp_path / "vault"
    aadhaar = _valid_aadhaar()
    note = process_email(
        vault, "acme",
        _msg(f"Please pay. PAN ABCDE1234F, aadhaar {aadhaar}. Thanks."),
    )
    assert note.parent == vault / "acme" / "00-inbox" / "active"
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
    enote = process_email(tmp_path / "ve", "acme", _msg(body))
    efm, ebody = split_front_matter(enote.read_text())

    # via folder (same text)
    drop = tmp_path / "dropbox" / "note.txt"
    drop.parent.mkdir(parents=True)
    drop.write_text(body)
    fnote = process_drop(tmp_path / "vf", "acme", drop, raw_archive=tmp_path / "raw")
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

    note = process_email(tmp_path / "v", "acme", m)
    _fm, body = split_front_matter(note.read_text())
    assert "[PAN]" in body
    assert "Plain body" in body
