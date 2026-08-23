"""registry.py — registry CRUD (spec §2.2b, step 9).

Add and edit go direct — the GUI writes YAML, every write is a commit, and the
file stays the hand-editable source of truth. Delete goes through the outbox
with a reference count, because deleting a value orphans front-matter, ledger
rows and saved scopes (ADR-006 consequence test). Delete executes only if no
references remain.
"""
from __future__ import annotations

import sqlite3
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import yaml

from .git_transaction import (
    GitTransactionError,
    PathChange,
    PathState,
    ReviewedPathIntegrityError,
    ReviewedPathUnavailable,
    TransactionPlan,
    capture_path_state,
    execute_transaction,
)
from .console_routing import structured_reader
from .review_tokens import (
    ReviewSnapshot,
    make_review_snapshot,
    require_review_match,
)
from .inbox import split_front_matter
from .outbox import UnreadableProposalRecord
from .proposal_identity import (
    ProposalIdentityError,
    proposal_id_candidates,
    require_proposal_id,
    require_proposal_identity,
)
from .scope import OutOfScopeError, RedirectedPathError, Scope
from .vault import DestinationRegistryError

# Columns in books.db that carry a product/member value.
_DB_COLUMNS = {
    "product": ("product", "tag"),
    "member": ("member", "member_id"),
}
_SKIP_DIRS = {".git", ".obsidian", ".sensitive", "outbox", "staging"}


class RegistryError(Exception):
    pass


class RegistryTransactionError(RegistryError):
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

@structured_reader(category="front-matter")
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


@structured_reader(category="registry")
def _count_workspaces(scope: Scope, kind: str, slug: str) -> int:
    ws = scope.system_path("workspaces.yaml")
    if not ws.is_file():
        # Absent workspaces registry counts zero — deliberate tolerance.
        return 0
    try:
        cfg = yaml.safe_load(ws.read_text(encoding="utf-8")) or {}
    except UnicodeDecodeError as exc:
        raise DestinationRegistryError(
            "workspaces registry is not valid UTF-8"
        ) from exc
    except OSError as exc:
        raise DestinationRegistryError(
            "workspaces registry could not be read"
        ) from exc
    except yaml.YAMLError as exc:
        raise DestinationRegistryError(
            "workspaces registry is invalid YAML"
        ) from exc
    if not isinstance(cfg, dict):
        raise DestinationRegistryError("workspaces registry must be a mapping")
    entries = cfg.get("workspaces") or []
    if not isinstance(entries, list):
        raise DestinationRegistryError(
            "workspaces registry must list workspaces"
        )
    entity = scope.current_entity()
    total = 0
    for entry in entries:
        if not entry:
            # Pre-S6 this loop used `(entry or {}).get(...)`, tolerating every
            # falsy entry. Narrowing to `is None` would make a scalar-falsy
            # item fatal — a refusal S6 has no authority to invent.
            continue
        if not isinstance(entry, dict):
            raise DestinationRegistryError("workspace entry is malformed")
        if (
            entry.get("entity", entry.get("primary_entity")) == entity
            and str(entry.get(kind)) == slug
        ):
            total += 1
    return total


def _quote_identifier(name: str) -> str:
    """Quote a SQLite identifier (table or column name) discovered from a
    file's own schema, never trusted as SQL syntax. Doubling an embedded `"`
    is SQLite's own escaping rule for a quoted identifier — this is not
    string-escaping for a value (values stay parameter-bound); it makes an
    arbitrary identifier safe to interpolate positionally, which parameter
    binding cannot do for identifiers at all. This also fixes legitimate
    names containing spaces, hyphens, or reserved words, which previously
    broke the generated SQL outright."""
    return '"' + name.replace('"', '""') + '"'


@structured_reader(category="admin-db")
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
    try:
        conn = sqlite3.connect(f"file:{resolved}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        # A permission/lock/open failure at connect() time escaped raw today
        # — never guarded at all, since connect() ran before this function's
        # own try/except. Normalize the type; nothing was tolerated before.
        raise RegistryError("books.db could not be opened") from exc
    try:
        try:
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")]
            for table in tables:
                qtable = _quote_identifier(table)
                present = {r[1] for r in conn.execute(f"PRAGMA table_info({qtable})")}
                for col in cols:
                    if col in present:
                        qcol = _quote_identifier(col)
                        total += conn.execute(
                            f"SELECT COUNT(*) FROM {qtable} WHERE {qcol} = ?",
                            (slug,),
                        ).fetchone()[0]
        except sqlite3.DatabaseError as exc:
            # connect() with mode=ro never validates the header, so a corrupt
            # file only fails at the first query. Escaping today as
            # sqlite3.DatabaseError, it would reach the delete-preview route as
            # E-UNKNOWN; normalize the type, not the fatality.
            raise RegistryError("books.db could not be read") from exc
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


@structured_reader(category="registry")
def products_for(scope: Scope) -> list[str]:
    path = scope.system_path("products.yaml")
    if not path.is_file():
        # Absent products registry returns [] — deliberate tolerance.
        return []
    try:
        cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except UnicodeDecodeError as exc:
        raise DestinationRegistryError(
            "products registry is not valid UTF-8"
        ) from exc
    except OSError as exc:
        raise DestinationRegistryError(
            "products registry could not be read"
        ) from exc
    except yaml.YAMLError as exc:
        raise DestinationRegistryError(
            "products registry is invalid YAML"
        ) from exc
    if not isinstance(cfg, dict):
        raise DestinationRegistryError("products registry must be a mapping")
    products = cfg.get("products") or {}
    if not isinstance(products, dict):
        raise DestinationRegistryError("products registry must map entities")
    values = products.get(scope.current_entity()) or {}
    if not isinstance(values, dict):
        return []
    return list(values.keys())


# --- add (direct) ----------------------------------------------------------

@structured_reader(category="registry")
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
    try:
        proposal_id = require_proposal_id(proposal_id)
    except ProposalIdentityError as exc:
        raise RegistryError("invalid delete proposal id") from exc
    entity_root = scope.resolve()
    bound_outbox = entity_root / "outbox"
    # C2 (S6 review): classify a lexical symlink BEFORE calling
    # scope.resolve("outbox") — this site previously called scope.resolve()
    # with no lexical check at all, so a symlinked outbox raised
    # OutOfScopeError (-> E-SCOPE) instead of RedirectedPathError
    # (-> E-TAMPER), the wrong tier for a redirection finding (design §2).
    # Mirrors app/outbox.py's _require_outbox_path.
    lexical_outbox = scope.root / scope.current_entity() / "outbox"
    if lexical_outbox.is_symlink():
        raise RedirectedPathError("outbox redirects outside the bound outbox")
    resolved_outbox = scope.resolve("outbox")
    if resolved_outbox != bound_outbox:
        raise RedirectedPathError("outbox redirects outside the bound outbox")
    candidate = resolved_outbox / f"{proposal_id}.yaml"
    if candidate.is_symlink():
        raise RedirectedPathError("delete proposal redirects from the requested leaf")
    if candidate.parent != resolved_outbox:
        raise OutOfScopeError("delete proposal leaves the bound outbox")
    return candidate


def propose_delete(scope: Scope, kind: str, slug: str) -> DeleteProposal:
    """Write a delete proposal carrying the reference count. Removes nothing."""
    entity = scope.current_entity()
    report = reference_count(scope, kind, slug)
    created_at = datetime.now()
    for pid in proposal_id_candidates(created_at):
        path = _delete_proposal_path(scope, pid)
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "id": pid,
            "action": "delete",
            "entity": entity,
            "kind": kind,
            "slug": slug,
            "created": created_at.isoformat(timespec="seconds"),
            "status": "pending",
            "total_references": report.total,
            "impact": report.sources,
        }
        try:
            with path.open("x", encoding="utf-8") as stream:
                stream.write(yaml.safe_dump(record, sort_keys=False))
        except FileExistsError:
            continue
        return DeleteProposal(
            pid, path, entity, kind, slug, report.total, report.sources
        )
    raise RegistryError("unable to allocate a unique delete proposal id")


@structured_reader(category="proposal")
def _parse_delete_record(contents: bytes) -> object:
    """Parse a delete proposal from bytes already in hand.

    S7's single-read rule lives on this seam: a caller holding captured
    bytes must parse *those* bytes, never re-open the path and parse
    whatever is there now.
    """
    try:
        text = contents.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise UnreadableProposalRecord(
            "delete proposal record is not valid UTF-8"
        ) from exc
    try:
        return yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise UnreadableProposalRecord(
            "delete proposal record is invalid YAML"
        ) from exc


def _validate_delete_record(
    scope: Scope, path: Path, rec: object
) -> DeleteProposal:
    if not isinstance(rec, dict):
        raise UnreadableProposalRecord(
            "delete proposal record must be a mapping"
        )
    try:
        require_proposal_identity(path, rec.get("id"))
    except ProposalIdentityError as exc:
        raise RegistryError("invalid delete proposal id") from exc
    if rec.get("action") != "delete":
        # `require_proposal_identity` above has already proved the record's
        # id matches this filename, so the stem is the validated id.
        raise RegistryError(f"{path.stem!r} is not a delete proposal")
    # PR #15 must-fix 5: `DeleteProposal` is a plain dataclass and enforces
    # nothing at runtime. Without this, e.g. `kind: []` passed silently and
    # later made `_REGISTRY_FILE.get(prop.kind)` raise a raw `TypeError` in
    # `execute_delete` (unhashable dict key) — already fatal, escaping as
    # E-UNKNOWN. Checked after the action check (so a record of the wrong
    # action type keeps its existing, more specific message) and before the
    # entity-ownership check (so a malformed entity gets this message rather
    # than the ownership one). `id` is not re-checked here: `require_proposal_
    # identity` above already guarantees it is a canonical string. `path`
    # stays server-derived and is never persisted data, so it is not checked.
    for field_name in ("entity", "kind", "slug"):
        if not isinstance(rec.get(field_name), str):
            raise UnreadableProposalRecord(
                f"delete proposal record field {field_name!r} must be a string"
            )
    if rec.get("entity") != scope.current_entity():
        raise RegistryError("delete proposal belongs to another entity")
    # The recorded impact is shown to the operator *and* gates the deletion
    # (see `execute_delete`), so it is validated rather than trusted as
    # whatever YAML happened to hold. `bool` is excluded deliberately: it is
    # an `int` subclass, and `total_references: true` is not a count.
    total = rec.get("total_references", 0)
    if isinstance(total, bool) or not isinstance(total, int) or total < 0:
        raise UnreadableProposalRecord(
            "delete proposal record field 'total_references' must be a "
            "non-negative integer"
        )
    sources = rec.get("impact", {})
    if not isinstance(sources, dict) or not all(
        isinstance(name, str)
        and not isinstance(count, bool)
        and isinstance(count, int)
        and count >= 0
        for name, count in sources.items()
    ):
        raise UnreadableProposalRecord(
            "delete proposal record field 'impact' must map names to counts"
        )
    # The total gates the deletion, so it may not disagree with the breakdown
    # it claims to summarise. A zero beside a non-empty breakdown is exactly
    # how an unsafe deletion would otherwise be waved through.
    if total != sum(sources.values()):
        raise UnreadableProposalRecord(
            "delete proposal record impact does not sum to its total"
        )
    try:
        return DeleteProposal(
            rec["id"], path, rec["entity"], rec["kind"], rec["slug"],
            total, sources,
        )
    except KeyError as exc:
        raise UnreadableProposalRecord(
            "delete proposal record is missing a required field"
        ) from exc


def _capture_delete_proposal_state(
    scope: Scope, path: Path, proposal_id: str
) -> PathState:
    """One no-follow capture of a delete proposal, in the registry's own
    vocabulary — so an unreadable record keeps the outcome it always had
    rather than surfacing a transaction-layer type."""
    relative = path.relative_to(scope.root).as_posix()
    try:
        state = capture_path_state(scope.root, relative)
    except ReviewedPathIntegrityError as exc:
        # The leaf became a symlink or a non-regular file between its lexical
        # check and this capture. Re-narrowed to the redirection type the
        # registry routes already declare, so it reaches the operator as a
        # tamper finding instead of escaping to the global fallback.
        raise RedirectedPathError("delete proposal leaf is redirected") from exc
    except ReviewedPathUnavailable as exc:
        raise UnreadableProposalRecord(
            "delete proposal record could not be read"
        ) from exc
    if state.contents is None:
        raise RegistryError(f"no delete proposal {proposal_id!r}")
    return state


def get_delete_review(
    scope: Scope, proposal_id: str
) -> ReviewSnapshot[DeleteProposal]:
    """The reviewable state of one delete proposal.

    The sequence is fixed (design §Architecture-1): capture one byte
    snapshot, parse *those* bytes, validate the value they produced, and
    fingerprint the same bytes. Displayed kind, value and recorded impact
    all come from this snapshot — never from a second live report that is
    not part of what was fingerprinted.
    """
    path = _delete_proposal_path(scope, proposal_id)
    state = _capture_delete_proposal_state(scope, path, proposal_id)
    proposal = _validate_delete_record(
        scope, path, _parse_delete_record(state.contents)
    )
    return make_review_snapshot(proposal, state.contents)


def get_delete_proposal(scope: Scope, proposal_id: str) -> DeleteProposal:
    """The validated value alone, for callers that will not act on it.

    No action may use this: an action needs the fingerprint that came from
    the same bytes, which only `get_delete_review` can supply.
    """
    return get_delete_review(scope, proposal_id).value


_REGISTRY_FILE = {"product": "products.yaml", "member": "members.yaml"}


@structured_reader(category="registry")
def _remove_scoped_registry_value(
    scope: Scope, kind: str, slug: str, registry_bytes: bytes
) -> bytes:
    try:
        cfg = yaml.safe_load(registry_bytes.decode("utf-8")) or {}
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise DestinationRegistryError("registry file is unparseable") from exc
    if not isinstance(cfg, dict):
        raise DestinationRegistryError("registry file must be a mapping")
    registry = cfg.get(f"{kind}s") or {}
    if not isinstance(registry, dict):
        raise DestinationRegistryError("registry section is malformed")
    values = registry.get(scope.current_entity())
    if isinstance(values, dict):
        if slug not in values:
            raise RegistryError(f"unknown {kind} {slug!r} in selected entity")
        del values[slug]
    elif isinstance(values, list):
        try:
            kept = [item for item in values if str((item or {}).get("id")) != slug]
        except AttributeError as exc:
            # A list of scalars where a list of mappings is expected: valid YAML,
            # wrong shape. Escapes raw today and reaches the delete-execute
            # route as E-UNKNOWN.
            raise DestinationRegistryError(
                f"{kind} registry entries are malformed"
            ) from exc
        if len(kept) == len(values):
            raise RegistryError(f"unknown {kind} {slug!r} in selected entity")
        registry[scope.current_entity()] = kept
    else:
        raise RegistryError(f"selected entity has no {kind} registry")
    return yaml.safe_dump(
        cfg, sort_keys=False, allow_unicode=True
    ).encode("utf-8")


@structured_reader(category="proposal")
def execute_delete(
    scope: Scope, proposal_id: str, review_sha256: object
) -> DeleteProposal:
    """On approval: refuse if references remain (recomputed fresh), else remove
    the value from its registry and commit.

    Bound to the reviewed proposal bytes. `review_sha256` is required, has no
    default and no id-only fallback, and is compared against the state the
    transaction will own — so the proposal consumed is the one reviewed.

    The fingerprint does not authorise the deletion by itself: scope,
    kind/value existence, current registry state, a freshly repeated
    reference count and transaction-owned state all remain independent gates.
    """
    vault = scope.root
    path = _delete_proposal_path(scope, proposal_id)
    proposal_rel = path.relative_to(vault).as_posix()
    try:
        # Transaction authority, captured once. The comparison, the parse and
        # the consumption are all about THIS state; a later reread may never
        # replace it.
        proposal_state = _capture_delete_proposal_state(scope, path, proposal_id)
        require_review_match(proposal_state.contents, review_sha256)
        prop = _validate_delete_record(
            scope, path, _parse_delete_record(proposal_state.contents)
        )

        # Two independent gates, and both must permit the deletion.
        #
        # The reviewed impact: an operator who was shown "this would orphan
        # N references" reviewed a deletion that was refused at the time.
        # Clearing the references afterwards does not retroactively make that
        # review a review of a valid deletion — the approved design requires
        # clearing them and reviewing a currently valid deletion again.
        if prop.total:
            raise RegistryError(
                "refusing to delete — this proposal was reviewed while "
                "references remained. Review the deletion again."
            )

        # The live count runs as a transaction **precondition**, so it is
        # evaluated under the approval lock immediately before any mutation.
        #
        # Counting here, before the lock, would leave a window the reviewed
        # bytes cannot close: another approval can acquire the lock and
        # commit a new reference in between, and this transaction's own
        # expected states — the registry file and the proposal record —
        # would still match, so the deletion would proceed and orphan it.
        # Acceptance criterion 5 is about that instant, not about an earlier
        # one.
        def _require_no_live_references() -> None:
            report = reference_count(scope, prop.kind, prop.slug)
            if report.total:
                raise RegistryError(
                    f"refusing to delete — references remain.\n{report.as_text()}"
                )

        filename = _REGISTRY_FILE.get(prop.kind)
        if filename is None:
            raise RegistryError(f"delete not supported for kind {prop.kind!r}")
        registry_rel = scope.system_path(filename).relative_to(vault).as_posix()
        registry_state = capture_path_state(vault, registry_rel)
        if registry_state.contents is None or registry_state.mode is None:
            raise RegistryError(f"{filename!r} registry is missing")

        rendered_registry_bytes = _remove_scoped_registry_value(
            scope, prop.kind, prop.slug, registry_state.contents
        )
        plan = TransactionPlan(
            message=f"registry: delete {prop.kind} {prop.slug}",
            changes=(
                PathChange(
                    registry_rel,
                    registry_state,
                    PathState.regular(
                        rendered_registry_bytes, registry_state.mode
                    ),
                ),
            ),
            commit_paths=(registry_rel,),
            owned_changes=(
                PathChange(proposal_rel, proposal_state, PathState.absent()),
            ),
            preconditions=(_require_no_live_references,),
        )
        execute_transaction(vault, plan)
    except GitTransactionError as exc:
        raise RegistryTransactionError(
            "registry deletion transaction failed"
        ) from exc
    return prop
