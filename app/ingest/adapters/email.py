"""email.py — IMAP email ingest adapter (spec §8.1 2nd source, step 10).

Normalises an email into text + metadata and hands it to the ONE shared commit
path (base.commit_inbox_item): same Envelope, same PII filter, no second code
path. The full body stays in the mail server (§8.3) — the vault gets the
redacted summary plus a `body_ref` pointing back at the message.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from email.message import Message
from email.policy import SMTP
from email.utils import getaddresses, parsedate_to_datetime
from pathlib import Path

from ..base import IngestError, IngestResult, commit_inbox_item
from ...entities import (
    EntityCatalog,
    RecipientConfigurationError,
    normalize_email_address,
)
from ...scope import Scope

_RECIPIENT_HEADERS = ("Delivered-To", "X-Original-To", "Envelope-To", "To", "Cc")


class EmailRoutingError(IngestError):
    pass


class UnmappedRecipientError(EmailRoutingError):
    pass


class AmbiguousRecipientError(EmailRoutingError):
    pass


def _decode(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        p = part.get_payload()
        return p if isinstance(p, str) else ""
    charset = part.get_content_charset() or "utf-8"
    return payload.decode(charset, errors="replace")


def body_text(msg: Message) -> str:
    if not msg.is_multipart():
        return _decode(msg)
    for want in ("text/plain", "text/"):
        for part in msg.walk():
            if part.get_content_maintype() == "multipart" or part.get_filename():
                continue
            if part.get_content_type().startswith(want):
                return _decode(part)
    return ""


def attachments(msg: Message) -> list[str]:
    if not msg.is_multipart():
        return []
    return [p.get_filename() for p in msg.walk() if p.get_filename()]


def recipient_addresses(message: Message) -> frozenset[str]:
    values = [value for name in _RECIPIENT_HEADERS for value in message.get_all(name, [])]
    addresses: set[str] = set()
    for _display, raw in getaddresses(values):
        try:
            addresses.add(normalize_email_address(raw))
        except RecipientConfigurationError:
            continue
    return frozenset(addresses)


def route_email_scope(root: Path | str, message: Message) -> Scope:
    catalog = EntityCatalog.load(root)
    matches = {
        entity
        for address in recipient_addresses(message)
        if (entity := catalog.entity_for_recipient(address)) is not None
    }
    if not matches:
        raise UnmappedRecipientError("email has no configured entity recipient")
    if len(matches) != 1:
        raise AmbiguousRecipientError("email recipients map to multiple entities")
    return Scope(catalog.root, matches.pop())


def process_email(
    scope: Scope,
    message: Message,
    *,
    now: datetime | None = None,
) -> IngestResult:
    """Ingest one email into the inbox via the shared write path."""
    now = now or datetime.now()

    subject = message.get("Subject", "(no subject)")
    sender = message.get("From")
    msgid = (message.get("Message-ID") or "").strip("<>")
    thread_id = (
        message.get("In-Reply-To") or message.get("References") or msgid or ""
    ).strip("<>") or None

    date_hdr = message.get("Date")
    try:
        received_at = parsedate_to_datetime(date_hdr).isoformat(timespec="seconds") \
            if date_hdr else now.isoformat(timespec="seconds")
    except (TypeError, ValueError):
        received_at = now.isoformat(timespec="seconds")

    text = body_text(message)
    digest = hashlib.sha256(message.as_bytes(policy=SMTP)).hexdigest()
    source_id = msgid or digest[:16]
    source_ref = f"imap:{msgid}" if msgid else f"imap:{source_id}"

    return commit_inbox_item(
        scope,
        text=text, title=subject, source="email", source_id=source_id,
        received_at=received_at, sender=sender, thread_id=thread_id,
        source_ref=source_ref, body_ref=source_ref, sha256=digest,
        attachments=attachments(message), slug_seed=digest,
    )


def process_shared_email(
    root: Path | str,
    message: Message,
    *,
    now: datetime | None = None,
) -> IngestResult:
    return process_email(route_email_scope(root, message), message, now=now)


def poll(  # pragma: no cover - IMAP I/O glue over process_email
    vault: Path | str,
    host: str,
    user: str,
    password: str,
    mailbox: str = "INBOX",
) -> int:
    """Fetch unseen messages once and ingest each. Returns the count processed.

    Credentials are passed by the caller (from config/secrets), never stored in
    the vault. One-shot; Hermes cron drives the cadence (spec §5 — no scheduler
    in this app)."""
    import email as _email
    import imaplib

    catalog = EntityCatalog.load(vault)
    conn = imaplib.IMAP4_SSL(host)
    count = 0
    try:
        conn.login(user, password)
        conn.select(mailbox)
        _typ, data = conn.search(None, "UNSEEN")
        for uid in data[0].split():
            _typ, raw = conn.fetch(uid, "(BODY.PEEK[])")
            msg = _email.message_from_bytes(raw[0][1])
            process_shared_email(catalog.root, msg)
            conn.store(uid, "+FLAGS", "\\Seen")
            count += 1
    finally:
        try:
            conn.close()
        finally:
            conn.logout()
    return count
