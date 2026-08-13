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
from email.utils import parsedate_to_datetime
from pathlib import Path

from ..base import IngestResult, commit_inbox_item
from ...scope import Scope


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


def process_email(
    vault: Path | str,
    entity: str,
    msg: Message,
    *,
    scope: Scope | None = None,
    now: datetime | None = None,
) -> IngestResult:
    """Ingest one email into the inbox via the shared write path."""
    scope = scope or Scope(Path(vault), entity)
    entity = scope.require_entity(entity)
    now = now or datetime.now()

    subject = msg.get("Subject", "(no subject)")
    sender = msg.get("From")
    msgid = (msg.get("Message-ID") or "").strip("<>")
    thread_id = (msg.get("In-Reply-To") or msg.get("References") or msgid or "").strip("<>") or None

    date_hdr = msg.get("Date")
    try:
        received_at = parsedate_to_datetime(date_hdr).isoformat(timespec="seconds") \
            if date_hdr else now.isoformat(timespec="seconds")
    except (TypeError, ValueError):
        received_at = now.isoformat(timespec="seconds")

    text = body_text(msg)
    digest = hashlib.sha256(msg.as_bytes(policy=SMTP)).hexdigest()
    source_id = msgid or digest[:16]
    source_ref = f"imap:{msgid}" if msgid else f"imap:{source_id}"

    return commit_inbox_item(
        scope, entity,
        text=text, title=subject, source="email", source_id=source_id,
        received_at=received_at, sender=sender, thread_id=thread_id,
        source_ref=source_ref, body_ref=source_ref, sha256=digest,
        attachments=attachments(msg), slug_seed=digest,
    )


def poll(  # pragma: no cover - IMAP I/O glue over process_email
    vault: Path | str,
    entity: str,
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

    conn = imaplib.IMAP4_SSL(host)
    conn.login(user, password)
    conn.select(mailbox)
    _typ, data = conn.search(None, "UNSEEN")
    count = 0
    for uid in data[0].split():
        _typ, raw = conn.fetch(uid, "(RFC822)")
        msg = _email.message_from_bytes(raw[0][1])
        process_email(vault, entity, msg)
        count += 1
    conn.close()
    conn.logout()
    return count
