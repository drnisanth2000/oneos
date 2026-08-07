"""envelope.py — the normalised ingest envelope (spec §8.2).

Every adapter produces this before anything touches 00-inbox/active/. The triage
screen never learns the source; a fifth adapter later is zero UI change. The
vault stores the summary and metadata, never the raw body — the body lives
outside git and is referenced by `body_ref` / `source_ref` + `sha256` (§8.3–4).
"""
from __future__ import annotations

from pydantic import BaseModel


class Envelope(BaseModel):
    source: str                       # email | telegram | folder | whatsapp
    source_id: str
    thread_id: str | None = None
    sender: str | None = None
    received_at: str                  # ISO 8601
    title: str
    summary: str                      # deterministic, already PII-redacted
    attachments: list[str] = []
    body_ref: str | None = None       # pointer to raw archive — NOT the body

    # file linkage (§8.4) — named root, never an absolute path
    source_ref: str | None = None
    sha256: str | None = None
    mime: str | None = None
    size: int | None = None

    # PII (ADR-008) — set when the deny-list filter matched
    pii_quarantined: bool = False
    pii_classes: list[str] = []
