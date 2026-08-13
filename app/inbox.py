"""inbox.py — read unsorted triage items from an entity's 00-inbox/active/.

All path resolution goes through Scope (invariant 4). Reads only; the triage
screen proposes classifications and the outbox performs any move (steps 7–8).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .scope import Scope


@dataclass
class InboxItem:
    path: Path
    title: str
    summary: str
    source: str | None
    fm: dict = field(default_factory=dict)


def split_front_matter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    end = text.find("---", 3)
    if end == -1:
        return {}, text
    try:
        fm = yaml.safe_load(text[3:end]) or {}
    except yaml.YAMLError:
        fm = {}
    if not isinstance(fm, dict):
        fm = {}
    return fm, text[end + 3:].lstrip("\n")


def read_inbox(scope: Scope, entity: str) -> list[InboxItem]:
    entity = scope.require_entity(entity)
    d = scope.resolve("00-inbox", "active")
    if not d.is_dir():
        return []
    items: list[InboxItem] = []
    for p in sorted(d.glob("*.md")):
        fm, body = split_front_matter(p.read_text(encoding="utf-8"))
        if fm.get("sub") != "triage":
            continue
        items.append(
            InboxItem(
                path=p,
                title=str(fm.get("title", p.stem)),
                summary=body.strip(),
                source=fm.get("source"),
                fm=fm,
            )
        )
    return items
