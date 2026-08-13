"""Email adapter (spec §10 step 10): IMAP poll into the step-5 envelope.

Done-when: same envelope, same PII filter, no second code path. Built with
email.message.EmailMessage — no network. Synthetic PII only.
"""
import hashlib
import imaplib
from email.message import EmailMessage
from email.policy import SMTP

import pytest

from app.entities import RecipientConfigurationError
from app.scope import Scope
from app.schema import validate_file
from app.inbox import split_front_matter
from app.ingest.adapters.email import (
    AmbiguousRecipientError,
    UnmappedRecipientError,
    poll,
    process_email,
    process_shared_email,
    recipient_addresses,
)
from app.ingest.adapters.folder import process_drop
from app.ingest.pii import verhoeff_check_digit
from tests.conftest import git_entity_vault, git_head, git_tracked_paths


def _valid_aadhaar() -> str:
    base = "40001010001"
    return base + str(verhoeff_check_digit(base))


def _msg(
    body: str,
    *,
    to: str | None = None,
    sender: str = "Accounts <accounts@vendor.example.invalid>",
    subject: str = "Vendor invoice Q3",
) -> EmailMessage:
    m = EmailMessage()
    m["Subject"] = subject
    m["From"] = sender
    if to is not None:
        m["To"] = to
    m["Message-ID"] = "<abc123@vendor.example.invalid>"
    m["Date"] = "Wed, 06 Aug 2026 10:00:00 +0000"
    m.set_content(body)
    return m


@pytest.fixture
def email_vault(tmp_path):
    return git_entity_vault(
        tmp_path / "email-vault",
        ("alpha", "beta"),
        {
            "alpha/00-inbox/active/.gitkeep": "",
            "beta/00-inbox/active/.gitkeep": "",
        },
        ingest={
            "alpha": ["intake-alpha@example.invalid", "alias@example.invalid"],
            "beta": ["intake-beta@example.invalid"],
        },
    )


def test_recipient_parser_uses_only_approved_headers():
    message = _msg("cc-hidden@example.invalid", to="Alpha <INTAKE-ALPHA@example.invalid>")
    message["Delivered-To"] = "intake-alpha@example.invalid"
    message["Delivered-To"] = "Repeated <INTAKE-ALPHA@example.invalid>"
    message["X-Original-To"] = "alias@example.invalid"
    message["Envelope-To"] = "Alias <alias@example.invalid>"
    message["Cc"] = "cc@example.invalid"
    message["Reply-To"] = "intake-beta@example.invalid"
    assert recipient_addresses(message) == frozenset({
        "intake-alpha@example.invalid", "alias@example.invalid", "cc@example.invalid"
    })


def test_shared_email_routes_to_exactly_one_entity(email_vault):
    result = process_shared_email(
        email_vault,
        _msg("alpha body", to="intake-alpha@example.invalid"),
    )
    assert result.path.is_relative_to(email_vault / "alpha/00-inbox/active")
    assert not list((email_vault / "beta/00-inbox/active").glob("*.md"))


def test_sender_subject_and_body_never_select_an_entity(email_vault):
    result = process_shared_email(
        email_vault,
        _msg(
            "intake-beta@example.invalid must not route this body",
            to="intake-alpha@example.invalid",
            sender="intake-beta@example.invalid",
            subject="intake-beta@example.invalid",
        ),
    )
    assert result.path.is_relative_to(email_vault / "alpha/00-inbox/active")
    assert not list((email_vault / "beta/00-inbox/active").glob("*.md"))


def test_multiple_recipients_for_one_entity_are_not_ambiguous(email_vault):
    result = process_shared_email(
        email_vault,
        _msg(
            "one entity",
            to="intake-alpha@example.invalid, ALIAS@example.invalid",
        ),
    )
    assert result.path.is_relative_to(email_vault / "alpha/00-inbox/active")


@pytest.mark.parametrize("recipients,error", [
    (["unknown@example.invalid"], UnmappedRecipientError),
    (["intake-alpha@example.invalid", "intake-beta@example.invalid"], AmbiguousRecipientError),
])
def test_routing_error_creates_no_receipt_or_commit(email_vault, recipients, error):
    before_head = git_head(email_vault)
    before_paths = git_tracked_paths(email_vault)
    before_inboxes = {
        entity: list((email_vault / entity / "00-inbox/active").iterdir())
        for entity in ("alpha", "beta")
    }
    message = _msg("must not be written", to=", ".join(recipients))
    with pytest.raises(error):
        process_shared_email(email_vault, message)
    assert git_head(email_vault) == before_head
    assert git_tracked_paths(email_vault) == before_paths
    assert {
        entity: list((email_vault / entity / "00-inbox/active").iterdir())
        for entity in ("alpha", "beta")
    } == before_inboxes
    assert not list(email_vault.glob("*/00-inbox/active/*.md"))


def test_poll_rejects_duplicate_ownership_before_opening_imap(tmp_path, monkeypatch):
    vault = git_entity_vault(
        tmp_path / "duplicate-vault",
        ("alpha", "beta"),
        {},
        ingest={
            "alpha": ["shared@example.invalid"],
            "beta": ["SHARED@example.invalid"],
        },
    )

    def fail_if_opened(_host):
        raise AssertionError("IMAP opened before validating recipient ownership")

    monkeypatch.setattr(imaplib, "IMAP4_SSL", fail_if_opened)
    with pytest.raises(RecipientConfigurationError):
        poll(vault, "mail.example.invalid", "user", "password")


@pytest.mark.parametrize("recipients,error", [
    (["unknown@example.invalid"], UnmappedRecipientError),
    (["intake-alpha@example.invalid", "intake-beta@example.invalid"], AmbiguousRecipientError),
])
def test_poll_routing_failure_peeks_without_mailbox_or_vault_mutation(
    email_vault, monkeypatch, recipients, error
):
    class PollingIMAP:
        def __init__(self, raw_message):
            self.raw_message = raw_message
            self.fetches = []
            self.stores = []
            self.closed = False
            self.logged_out = False

        def login(self, _user, _password):
            return "OK", []

        def select(self, _mailbox):
            return "OK", []

        def search(self, _charset, _criterion):
            return "OK", [b"17"]

        def fetch(self, uid, query):
            self.fetches.append((uid, query))
            return "OK", [(b"17 (BODY[] {1})", self.raw_message)]

        def store(self, uid, command, flags):
            self.stores.append((uid, command, flags))
            return "OK", []

        def close(self):
            self.closed = True
            return "OK", []

        def logout(self):
            self.logged_out = True
            return "BYE", []

    message = _msg("must remain unseen", to=", ".join(recipients))
    connection = PollingIMAP(message.as_bytes())
    monkeypatch.setattr(imaplib, "IMAP4_SSL", lambda _host: connection)
    head_before = git_head(email_vault)
    paths_before = git_tracked_paths(email_vault)

    with pytest.raises(error):
        poll(email_vault, "mail.example.invalid", "user", "password")

    assert connection.fetches == [(b"17", "(BODY.PEEK[])")]
    assert connection.stores == []
    assert connection.closed is True
    assert connection.logged_out is True
    assert git_head(email_vault) == head_before
    assert git_tracked_paths(email_vault) == paths_before
    assert not list(email_vault.glob("*/00-inbox/active/*.md"))


def test_email_lands_in_inbox_with_envelope_and_pii_stripped(tmp_path):
    vault = git_entity_vault(tmp_path / "vault", ("synthetic",), {"synthetic/00-inbox/active/.gitkeep": ""})
    aadhaar = _valid_aadhaar()
    result = process_email(
        Scope(vault, "synthetic"),
        message=_msg(f"Please pay. PAN ABCDE1234F, aadhaar {aadhaar}. Thanks."),
    )
    note = result.path
    assert note.parent == vault / "synthetic" / "00-inbox" / "active"
    fm, body = split_front_matter(note.read_text())

    assert fm["source"] == "email"
    assert fm["sub"] == "triage"
    assert "accounts@vendor.example.invalid" in fm["sender"]
    assert str(fm["received_at"]).startswith("2026-08-06")  # YAML parses it to datetime
    assert fm["body_ref"] == "imap:abc123@vendor.example.invalid"
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
    evault = git_entity_vault(tmp_path / "ve", ("synthetic",), {"synthetic/00-inbox/active/.gitkeep": ""})
    enote = process_email(Scope(evault, "synthetic"), _msg(body)).path
    efm, ebody = split_front_matter(enote.read_text())

    # via folder (same text)
    drop = tmp_path / "dropbox" / "note.txt"
    drop.parent.mkdir(parents=True)
    drop.write_text(body)
    fvault = git_entity_vault(tmp_path / "vf", ("synthetic",), {"synthetic/00-inbox/active/.gitkeep": ""})
    fnote = process_drop(
        Scope(fvault, "synthetic"), drop, raw_archive=tmp_path / "raw"
    ).path
    ffm, fbody = split_front_matter(fnote.read_text())

    assert ebody.strip() == fbody.strip()                  # identical redaction
    assert efm["pii_classes"] == ffm["pii_classes"]        # identical classes
    assert "[PAN]" in ebody and "[PHONE]" in ebody
    # but the source differs — the only thing the adapter sets
    assert efm["source"] == "email" and ffm["source"] == "folder"


def test_multipart_prefers_text_plain(tmp_path):
    m = EmailMessage()
    m["Subject"] = "Mixed"
    m["From"] = "a@b.example.invalid"
    m["Message-ID"] = "<m1@b.example.invalid>"
    m.set_content("Plain body with PAN ABCDE1234F.")
    m.add_alternative("<p>HTML body with PAN ABCDE1234F.</p>", subtype="html")

    vault = git_entity_vault(tmp_path / "v", ("synthetic",), {"synthetic/00-inbox/active/.gitkeep": ""})
    note = process_email(Scope(vault, "synthetic"), m).path
    _fm, body = split_front_matter(note.read_text())
    assert "[PAN]" in body
    assert "Plain body" in body


def test_email_hash_represents_deterministic_message_bytes(tmp_path):
    vault = git_entity_vault(tmp_path / "vault", ("synthetic",), {"synthetic/00-inbox/active/.gitkeep": ""})
    msg = _msg("stable body\n")
    expected = hashlib.sha256(msg.as_bytes(policy=SMTP)).hexdigest()
    result = process_email(Scope(vault, "synthetic"), msg)
    fm, _body = split_front_matter(result.path.read_text(encoding="utf-8"))
    assert fm["sha256"] == expected


def test_duplicate_email_creates_no_second_commit(tmp_path):
    vault = git_entity_vault(tmp_path / "vault", ("synthetic",), {"synthetic/00-inbox/active/.gitkeep": ""})
    scope = Scope(vault, "synthetic")
    first = process_email(scope, _msg("same body\n"))
    before = git_head(vault)
    duplicate = process_email(scope, _msg("same body\n"))
    assert duplicate.path == first.path
    assert duplicate.created is False
    assert duplicate.commit_oid is None
    assert git_head(vault) == before
