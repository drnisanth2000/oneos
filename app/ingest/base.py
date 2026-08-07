"""base.py — the single ingest write path shared by every adapter.

An adapter's only job is to normalise its source into text + metadata; from
there every source goes through the same code: redact (ADR-008), build the
Envelope (§8.2), write the redacted inbox item. "Same envelope, same PII filter,
no second code path" (spec §10 step 10) is enforced by there being exactly one
of these.
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from ..scope import Scope
from .envelope import Envelope
from .pii import redact

SUMMARY_CHARS = 800


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "item"


def _scalar(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def render_note(env: Envelope, entity: str) -> str:
    today = date.today().isoformat()
    lines = [
        "---",
        "type: inbox-item",
        f"title: {env.title}",
        f"entity: {entity}",
        "product: null",
        "status: active",
        f"created: {today}",
        f"updated: {today}",
        "sub: triage",
        f"source: {env.source}",
        f"source_id: {env.source_id}",
        f"received_at: {env.received_at}",
        f"sender: {_scalar(env.sender)}",
        f"thread_id: {_scalar(env.thread_id)}",
        f"source_ref: {_scalar(env.source_ref)}",
        f"sha256: {_scalar(env.sha256)}",
        f"mime: {_scalar(env.mime)}",
        f"size: {_scalar(env.size)}",
        f"body_ref: {_scalar(env.body_ref)}",
        f"pii_quarantined: {_scalar(env.pii_quarantined)}",
        f"pii_classes: [{', '.join(env.pii_classes)}]",
        "---",
    ]
    return "\n".join(lines) + "\n" + env.summary + "\n"


def write_inbox_item(
    scope: Scope,
    entity: str,
    *,
    text: str,
    title: str,
    source: str,
    source_id: str,
    received_at: str,
    sender: str | None = None,
    thread_id: str | None = None,
    source_ref: str | None = None,
    body_ref: str | None = None,
    sha256: str | None = None,
    mime: str | None = None,
    size: int | None = None,
    attachments: list[str] | None = None,
    slug_seed: str | None = None,
) -> tuple[Path, Envelope]:
    """Redact, build the envelope, and write the inbox item. Every adapter ends
    here. Returns (note_path, envelope)."""
    redacted, matches = redact(text)
    env = Envelope(
        source=source,
        source_id=source_id,
        thread_id=thread_id,
        sender=sender,
        received_at=received_at,
        title=title,
        summary=redacted[:SUMMARY_CHARS],
        attachments=attachments or [],
        source_ref=source_ref,
        body_ref=body_ref or source_ref,
        sha256=sha256,
        mime=mime,
        size=size,
        pii_quarantined=bool(matches),
        pii_classes=sorted({m.kind for m in matches}),
    )
    # Inbox items live in 00-inbox/active/ with sub: triage — path via Scope.
    note_dir = scope.resolve(entity, "00-inbox", "active")
    note_dir.mkdir(parents=True, exist_ok=True)
    seed = (slug_seed or source_id or "item")[:8]
    note_path = note_dir / f"{_slug(title)}-{seed}.md"
    note_path.write_text(render_note(env, entity), encoding="utf-8")
    return note_path, env
