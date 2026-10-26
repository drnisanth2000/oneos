"""inbox.py — read unsorted triage items from an entity's 00-inbox/active/.

All path resolution goes through Scope (invariant 4). Reads only; the triage
screen proposes classifications and the outbox performs any move (steps 7–8).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .console_routing import failure_contract, structured_reader
from .scope import RedirectedPathError, Scope


@dataclass
class InboxItem:
    path: Path
    title: str
    summary: str
    source: str | None
    fm: dict = field(default_factory=dict)


@structured_reader(category="front-matter")
def split_front_matter(text: str) -> tuple[dict, str]:
    """Split a leading front-matter fence from the body.

    The closing fence is a line whose only content is ``---``. A quoted title
    may contain that character sequence without ending the block.
    """
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return {}, text
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() != "---":
            continue
        try:
            fm = yaml.safe_load("".join(lines[1:index])) or {}
        except yaml.YAMLError:
            fm = {}
        if not isinstance(fm, dict):
            fm = {}
        return fm, "".join(lines[index + 1:]).lstrip("\n")
    return {}, text


def _require_real_directory(
    scope: Scope, *parts: str, label: str = "inbox lifecycle directory"
) -> Path | None:
    lexical = scope.root / scope.current_entity() / Path(*parts)
    if not lexical.exists() and not lexical.is_symlink():
        return None
    # PR #15 must-fix 6: classify a lexical symlink BEFORE calling
    # `scope.resolve()` — see the identical reasoning in
    # `app/destinations.py::_require_real_directory`. A symlink pointing
    # outside the entity root would otherwise let `scope.resolve()` raise
    # `OutOfScopeError` (-> E-SCOPE) for what is a redirection (-> E-TAMPER).
    if lexical.is_symlink():
        raise RedirectedPathError(f"{label} is redirected")
    resolved = scope.resolve(*parts)
    if resolved != lexical or not lexical.is_dir():
        raise RedirectedPathError(f"{label} is redirected")
    return lexical


def _require_real_receipt(
    directory: Path, discovered: Path, label: str = "inbox receipt"
) -> Path:
    if (
        discovered.parent != directory
        or discovered.is_symlink()
        or not discovered.is_file()
        or discovered.resolve() != discovered
    ):
        raise RedirectedPathError(f"{label} is redirected")
    return discovered


@structured_reader(category="front-matter")
@failure_contract(raises=(RedirectedPathError,))
def read_inbox(scope: Scope) -> list[InboxItem]:
    if _require_real_directory(scope, "00-inbox") is None:
        return []
    directory = _require_real_directory(scope, "00-inbox", "active")
    if directory is None:
        return []
    items: list[InboxItem] = []
    for discovered in sorted(directory.glob("*.md")):
        p = _require_real_receipt(directory, discovered)
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
