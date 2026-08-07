"""registry.py — registry CRUD (spec §2.2b, step 9).

Add and edit go direct — the GUI writes YAML, every write is a commit, and the
file stays the hand-editable source of truth. Delete goes through the outbox
with a reference count, because deleting a value orphans front-matter, ledger
rows and saved scopes (ADR-006 consequence test). Delete executes only if no
references remain.
"""
from __future__ import annotations

import re
import sqlite3
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import yaml

from .inbox import split_front_matter
from .scope import Scope

# Columns in books.db that carry a product/member value.
_DB_COLUMNS = {
    "product": ("product", "tag"),
    "member": ("member", "member_id"),
}
_SKIP_DIRS = {".git", ".obsidian", "_system"}


class RegistryError(Exception):
    pass


@dataclass
class ReferenceReport:
    kind: str
    slug: str
    sources: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(self.sources.values())

    def as_text(self) -> str:
        if not self.total:
            return f"{self.kind} '{self.slug}': no references — safe to delete."
        lines = [f"{self.kind} '{self.slug}' is referenced by:"]
        for src, n in self.sources.items():
            if n:
                lines.append(f"  {src}: {n}")
        return "\n".join(lines)


@dataclass
class DeleteProposal:
    id: str
    path: Path
    entity: str
    kind: str
    slug: str
    total: int
    sources: dict[str, int]


def _git(vault: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=vault, check=True, capture_output=True, text=True
    ).stdout


# --- reference counting ----------------------------------------------------

def _count_front_matter(vault: Path, field_name: str, slug: str) -> int:
    n = 0
    for p in vault.rglob("*.md"):
        if any(part in _SKIP_DIRS for part in p.relative_to(vault).parts):
            continue
        try:
            fm, _ = split_front_matter(p.read_text(encoding="utf-8"))
        except OSError:
            continue
        if str(fm.get(field_name)) == slug:
            n += 1
    return n


def _count_workspaces(vault: Path, slug: str) -> int:
    ws = vault / "_system" / "workspaces.yaml"
    if not ws.is_file():
        return 0
    cfg = yaml.safe_load(ws.read_text(encoding="utf-8")) or {}
    entries = cfg.get("workspaces") or []
    return sum(1 for e in entries if slug in (str(v) for v in (e or {}).values()))


def _count_books_db(vault: Path, kind: str, slug: str) -> int:
    cols = _DB_COLUMNS.get(kind, ())
    if not cols:
        return 0
    total = 0
    for db in vault.rglob("books.db"):
        try:
            conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        except sqlite3.Error:
            continue
        try:
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")]
            for table in tables:
                present = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
                for col in cols:
                    if col in present:
                        total += conn.execute(
                            f"SELECT COUNT(*) FROM {table} WHERE {col} = ?", (slug,)
                        ).fetchone()[0]
        finally:
            conn.close()
    return total


def reference_count(scope: Scope, kind: str, slug: str) -> ReferenceReport:
    vault = scope.root
    return ReferenceReport(
        kind=kind,
        slug=slug,
        sources={
            "front-matter": _count_front_matter(vault, kind, slug),
            "workspaces": _count_workspaces(vault, slug),
            "books.db": _count_books_db(vault, kind, slug),
        },
    )


# --- add (direct) ----------------------------------------------------------

def add_workspace(scope: Scope, entry: dict) -> None:
    """Append a workspace. Direct write + commit (spec §2.2b)."""
    vault = scope.root
    ws = vault / "_system" / "workspaces.yaml"
    text = ws.read_text(encoding="utf-8") if ws.is_file() else 'version: "1.0"\nworkspaces:\n'
    flow = "{" + ", ".join(f"{k}: {v}" for k, v in entry.items()) + "}"
    if not text.endswith("\n"):
        text += "\n"
    text += f"  - {flow}\n"
    ws.write_text(text, encoding="utf-8")
    _git(vault, "add", "_system/workspaces.yaml")
    _git(vault, "commit", "-q", "-m", f"registry: add workspace {entry.get('id')}")


# --- delete (via outbox) ---------------------------------------------------

def propose_delete(scope: Scope, entity: str, kind: str, slug: str) -> DeleteProposal:
    """Write a delete proposal carrying the reference count. Removes nothing."""
    report = reference_count(scope, kind, slug)
    pid = f"{datetime.now():%Y%m%dT%H%M%S}-delete-{kind}-{slug}"
    record = {
        "id": pid,
        "action": "delete",
        "entity": entity,
        "kind": kind,
        "slug": slug,
        "created": datetime.now().isoformat(timespec="seconds"),
        "status": "pending",
        "total_references": report.total,
        "impact": report.sources,
    }
    outbox = scope.resolve(entity, "outbox")
    outbox.mkdir(parents=True, exist_ok=True)
    path = outbox / f"{pid}.yaml"
    path.write_text(yaml.safe_dump(record, sort_keys=False), encoding="utf-8")
    return DeleteProposal(pid, path, entity, kind, slug, report.total, report.sources)


def get_delete_proposal(scope: Scope, entity: str, proposal_id: str) -> DeleteProposal:
    outbox = scope.resolve(entity, "outbox")
    path = outbox / f"{proposal_id}.yaml"
    if not path.is_file():
        raise RegistryError(f"no delete proposal {proposal_id!r}")
    rec = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if rec.get("action") != "delete":
        raise RegistryError(f"{proposal_id!r} is not a delete proposal")
    return DeleteProposal(
        rec["id"], path, rec["entity"], rec["kind"], rec["slug"],
        rec.get("total_references", 0), rec.get("impact", {}),
    )


_REGISTRY_FILE = {"product": "products.yaml", "member": "members.yaml"}


def _remove_key_block(text: str, slug: str) -> str:
    """Drop the `<indent><slug>:` mapping key and its more-indented children."""
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    i = 0
    while i < len(lines):
        m = re.match(rf"^(\s+){re.escape(slug)}:", lines[i])
        if m:
            base = len(m.group(1))
            i += 1
            while i < len(lines):
                l2 = lines[i]
                if l2.strip() == "":
                    i += 1
                    continue
                indent = len(l2) - len(l2.lstrip(" "))
                if indent > base:
                    i += 1
                    continue
                break
            continue
        out.append(lines[i])
        i += 1
    return "".join(out)


def execute_delete(scope: Scope, entity: str, proposal_id: str) -> None:
    """On approval: refuse if references remain (recomputed fresh), else remove
    the value from its registry and commit."""
    prop = get_delete_proposal(scope, entity, proposal_id)
    report = reference_count(scope, prop.kind, prop.slug)
    if report.total:
        raise RegistryError(
            f"refusing to delete — references remain.\n{report.as_text()}"
        )

    filename = _REGISTRY_FILE.get(prop.kind)
    if not filename:
        raise RegistryError(f"delete not supported for kind {prop.kind!r}")
    reg = scope.system_path(filename)
    reg.write_text(_remove_key_block(reg.read_text(encoding="utf-8"), prop.slug),
                   encoding="utf-8")
    vault = scope.root
    prop.path.unlink(missing_ok=True)  # untracked proposal
    _git(vault, "add", f"_system/{filename}")
    _git(vault, "commit", "-q", "-m", f"registry: delete {prop.kind} {prop.slug}")
