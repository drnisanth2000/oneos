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
from .scope import CrossScopeError, Scope

# Columns in books.db that carry a product/member value.
_DB_COLUMNS = {
    "product": ("product", "tag"),
    "member": ("member", "member_id"),
}
_SKIP_DIRS = {".git", ".obsidian", ".sensitive", "outbox", "staging"}
_PROPOSAL_ID = re.compile(r"^[A-Za-z0-9]+(?:[A-Za-z0-9_-]*[A-Za-z0-9])?$")


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

def _count_front_matter(entity_root: Path, field_name: str, slug: str) -> int:
    n = 0
    for p in entity_root.rglob("*.md"):
        if any(part in _SKIP_DIRS for part in p.relative_to(entity_root).parts):
            continue
        resolved = p.resolve()
        if not resolved.is_relative_to(entity_root):
            continue
        if any(
            part in _SKIP_DIRS
            for part in resolved.relative_to(entity_root).parts
        ):
            continue
        try:
            fm, _ = split_front_matter(resolved.read_text(encoding="utf-8"))
        except OSError:
            continue
        if str(fm.get(field_name)) == slug:
            n += 1
    return n


def _count_workspaces(scope: Scope, kind: str, slug: str) -> int:
    ws = scope.system_path("workspaces.yaml")
    if not ws.is_file():
        return 0
    cfg = yaml.safe_load(ws.read_text(encoding="utf-8")) or {}
    entries = cfg.get("workspaces") or []
    entity = scope.current_entity()
    return sum(
        1
        for entry in entries
        if (entry or {}).get("entity", (entry or {}).get("primary_entity")) == entity
        and str((entry or {}).get(kind)) == slug
    )


def _count_books_db(entity_root: Path, kind: str, slug: str) -> int:
    db = entity_root / "books.db"
    if not db.is_file():
        return 0
    resolved = db.resolve()
    if not resolved.is_relative_to(entity_root):
        return 0
    cols = _DB_COLUMNS.get(kind, ())
    if not cols:
        return 0
    total = 0
    conn = sqlite3.connect(f"file:{resolved}?mode=ro", uri=True)
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
    entity_root = scope.resolve()
    return ReferenceReport(
        kind=kind,
        slug=slug,
        sources={
            "front-matter": _count_front_matter(entity_root, kind, slug),
            "workspaces": _count_workspaces(scope, kind, slug),
            "books.db": _count_books_db(entity_root, kind, slug),
        },
    )


def products_for(scope: Scope) -> list[str]:
    path = scope.system_path("products.yaml")
    if not path.is_file():
        return []
    cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    values = (cfg.get("products") or {}).get(scope.current_entity()) or {}
    if not isinstance(values, dict):
        return []
    return list(values.keys())


# --- add (direct) ----------------------------------------------------------

def add_workspace(scope: Scope, entry: dict) -> None:
    """Append a workspace. Direct write + commit (spec §2.2b)."""
    entity = entry.get("entity", entry.get("primary_entity"))
    if entity != scope.current_entity():
        raise RegistryError("workspace owner disagrees with selected scope")
    vault = scope.root
    ws = scope.system_path("workspaces.yaml")
    text = ws.read_text(encoding="utf-8") if ws.is_file() else 'version: "1.0"\nworkspaces:\n'
    flow = "{" + ", ".join(f"{k}: {v}" for k, v in entry.items()) + "}"
    if not text.endswith("\n"):
        text += "\n"
    text += f"  - {flow}\n"
    ws.write_text(text, encoding="utf-8")
    _git(vault, "add", "_system/workspaces.yaml")
    _git(vault, "commit", "-q", "-m", f"registry: add workspace {entry.get('id')}")


# --- delete (via outbox) ---------------------------------------------------

def _delete_proposal_path(scope: Scope, proposal_id: str) -> Path:
    if not isinstance(proposal_id, str) or not _PROPOSAL_ID.fullmatch(proposal_id):
        raise RegistryError("invalid delete proposal id")
    entity_root = scope.resolve()
    bound_outbox = entity_root / "outbox"
    resolved_outbox = scope.resolve("outbox")
    if resolved_outbox != bound_outbox:
        raise CrossScopeError("outbox redirects outside the bound outbox")
    candidate = (resolved_outbox / f"{proposal_id}.yaml").resolve()
    if candidate.parent != resolved_outbox:
        raise CrossScopeError("delete proposal leaves the bound outbox")
    return candidate

def propose_delete(scope: Scope, kind: str, slug: str) -> DeleteProposal:
    """Write a delete proposal carrying the reference count. Removes nothing."""
    entity = scope.current_entity()
    report = reference_count(scope, kind, slug)
    pid = f"{datetime.now():%Y%m%dT%H%M%S}-delete"
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
    outbox = _delete_proposal_path(scope, pid).parent
    outbox.mkdir(parents=True, exist_ok=True)
    path = _delete_proposal_path(scope, pid)
    path.write_text(yaml.safe_dump(record, sort_keys=False), encoding="utf-8")
    return DeleteProposal(pid, path, entity, kind, slug, report.total, report.sources)


def get_delete_proposal(scope: Scope, proposal_id: str) -> DeleteProposal:
    path = _delete_proposal_path(scope, proposal_id)
    if not path.is_file():
        raise RegistryError(f"no delete proposal {proposal_id!r}")
    rec = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if rec.get("action") != "delete":
        raise RegistryError(f"{proposal_id!r} is not a delete proposal")
    if rec.get("entity") != scope.current_entity():
        raise RegistryError("delete proposal belongs to another entity")
    return DeleteProposal(
        rec["id"], path, rec["entity"], rec["kind"], rec["slug"],
        rec.get("total_references", 0), rec.get("impact", {}),
    )


_REGISTRY_FILE = {"product": "products.yaml", "member": "members.yaml"}


def _remove_scoped_registry_value(
    scope: Scope, kind: str, slug: str
) -> tuple[Path, str]:
    filename = _REGISTRY_FILE.get(kind)
    if filename is None:
        raise RegistryError(f"delete not supported for kind {kind!r}")
    path = scope.system_path(filename)
    cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    registry = cfg.get(f"{kind}s") or {}
    values = registry.get(scope.current_entity())
    if isinstance(values, dict):
        if slug not in values:
            raise RegistryError(f"unknown {kind} {slug!r} in selected entity")
        del values[slug]
    elif isinstance(values, list):
        kept = [item for item in values if str((item or {}).get("id")) != slug]
        if len(kept) == len(values):
            raise RegistryError(f"unknown {kind} {slug!r} in selected entity")
        registry[scope.current_entity()] = kept
    else:
        raise RegistryError(f"selected entity has no {kind} registry")
    return path, yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True)


def execute_delete(scope: Scope, proposal_id: str) -> None:
    """On approval: refuse if references remain (recomputed fresh), else remove
    the value from its registry and commit."""
    prop = get_delete_proposal(scope, proposal_id)
    report = reference_count(scope, prop.kind, prop.slug)
    if report.total:
        raise RegistryError(
            f"refusing to delete — references remain.\n{report.as_text()}"
        )

    reg, text = _remove_scoped_registry_value(scope, prop.kind, prop.slug)
    reg.write_text(text, encoding="utf-8")
    vault = scope.root
    prop.path.unlink(missing_ok=True)  # untracked proposal
    filename = _REGISTRY_FILE[prop.kind]
    _git(vault, "add", f"_system/{filename}")
    _git(vault, "commit", "-q", "-m", f"registry: delete {prop.kind} {prop.slug}")
