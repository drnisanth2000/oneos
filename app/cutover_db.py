"""cutover_db.py — path-confined, axis-filtered database updates.

`UPDATE` only: no `CREATE`, `ALTER`, or `DROP`, and therefore no schema
change. The writer allowlist is exact `(path, table, column, axis)` targets and
is never derived from a column name: `registry.py` counts over a `member_id`
column that `rename.py` documents as an opaque key rather than a registry id,
and a `tag` column may hold free text that merely coincides with a product id.

Each target receives **only** its declared axis's mappings. Applying both would
mean that where one literal is short on both axes, whichever mapping ran first
would win and a product column could silently receive a member identifier.

The text residual gate skips binaries and can never see inside a database, so
`database_residuals` is the only fail-closed check this half has — and it too
is axis-typed, or it would report a false residual on a correctly migrated
column.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import re
import sqlite3
from urllib.request import pathname2url

from .console_routing import structured_reader
from .cutover_manifest import DatabaseTarget, Mapping


_ENTITY_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class DatabaseCutoverError(Exception):
    pass


@dataclass(frozen=True, order=True)
class DatabaseReference:
    path: str
    table: str
    column: str
    axis: str
    old: str
    count: int


@dataclass(frozen=True, order=True)
class DatabaseChange:
    path: str
    table: str
    column: str
    axis: str
    old: str
    new: str
    count: int


def _quote_identifier(name: str) -> str:
    """SQLite's own escaping rule for a quoted identifier. Identifiers cannot
    be parameter-bound; values always are."""
    return '"' + name.replace('"', '""') + '"'


def resolve_database_path(root: Path, target: DatabaseTarget) -> Path:
    """Resolve a source-relative database path, refusing anything unsafe.

    A path must be relative, must not escape the root, must not traverse a
    symlink, and must be a regular file. A redirection is never followed.
    """
    pure = PurePosixPath(target.path)
    if pure.is_absolute() or Path(target.path).is_absolute():
        raise DatabaseCutoverError("database path must be relative")
    if ".." in pure.parts:
        raise DatabaseCutoverError("database path must not traverse upward")
    current = root
    for part in pure.parts:
        current = current / part
        if current.is_symlink():
            raise DatabaseCutoverError("database path traverses a symlink")
    candidate = root / target.path
    try:
        resolved = candidate.resolve()
        anchor = root.resolve()
    except (OSError, RuntimeError) as exc:
        raise DatabaseCutoverError("database path could not be resolved") from exc
    if not resolved.is_relative_to(anchor):
        raise DatabaseCutoverError("database path leaves the vault root")
    if not resolved.is_file():
        raise DatabaseCutoverError("approved database is missing or not a regular file")
    return resolved


@structured_reader(category="admin-db")
def _connect(path: Path, *, read_only: bool) -> sqlite3.Connection:
    try:
        if read_only:
            # `#` and `?` in a path are URI syntax. Unescaped, SQLite parses
            # them and opens something other than the approved database.
            return sqlite3.connect(
                f"file:{pathname2url(str(path))}?mode=ro", uri=True
            )
        return sqlite3.connect(path)
    except sqlite3.Error as exc:
        raise DatabaseCutoverError("approved database could not be opened") from exc


def _require_column(conn: sqlite3.Connection, target: DatabaseTarget) -> None:
    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    except sqlite3.DatabaseError as exc:
        raise DatabaseCutoverError("approved database could not be read") from exc
    if target.table not in tables:
        raise DatabaseCutoverError("approved table is absent from its database")
    columns = {
        row[1]
        for row in conn.execute(f"PRAGMA table_info({_quote_identifier(target.table)})")
    }
    if target.column not in columns:
        raise DatabaseCutoverError("approved column is absent from its table")


def _require_no_update_trigger(conn: sqlite3.Connection, target: DatabaseTarget) -> None:
    """Refuse an approved table carrying an UPDATE trigger.

    The owner approved a column rewrite, not whatever a trigger writes when it
    fires. Those writes are unreviewed and would land in the same single
    cutover commit, so the cutover refuses rather than firing them.
    """
    try:
        triggers = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger' AND tbl_name = ?",
            (target.table,),
        ).fetchall()
    except sqlite3.DatabaseError as exc:
        raise DatabaseCutoverError("approved database could not be read") from exc
    if triggers:
        raise DatabaseCutoverError(
            "an approved table carries a trigger; the cutover will not fire "
            "unreviewed side effects"
        )


def _mappings_for(target: DatabaseTarget, mappings: tuple[Mapping, ...]) -> list[Mapping]:
    """Only this target's declared axis. Never another's."""
    return [item for item in mappings if item.axis == target.axis]


def apply_database_mappings(
    root: Path, targets: tuple[DatabaseTarget, ...], mappings: tuple[Mapping, ...]
) -> tuple[DatabaseChange, ...]:
    changes: list[DatabaseChange] = []
    for target in targets:
        path = resolve_database_path(root, target)
        conn = _connect(path, read_only=False)
        try:
            _require_column(conn, target)
            _require_no_update_trigger(conn, target)
            statement = (
                f"UPDATE {_quote_identifier(target.table)} "
                f"SET {_quote_identifier(target.column)} = ? "
                f"WHERE {_quote_identifier(target.column)} = ?"
            )
            for mapping in _mappings_for(target, mappings):
                count = conn.execute(statement, (mapping.new, mapping.old)).rowcount
                if count:
                    changes.append(
                        DatabaseChange(
                            target.path,
                            target.table,
                            target.column,
                            target.axis,
                            mapping.old,
                            mapping.new,
                            count,
                        )
                    )
            conn.commit()
        except sqlite3.DatabaseError as exc:
            conn.rollback()
            raise DatabaseCutoverError("approved database update failed") from exc
        finally:
            conn.close()
    return tuple(changes)


def database_residuals(
    root: Path, targets: tuple[DatabaseTarget, ...], mappings: tuple[Mapping, ...]
) -> list[tuple[str, str, str, str, int]]:
    found: list[tuple[str, str, str, str, int]] = []
    for target in targets:
        path = resolve_database_path(root, target)
        conn = _connect(path, read_only=True)
        try:
            _require_column(conn, target)
            statement = (
                f"SELECT COUNT(*) FROM {_quote_identifier(target.table)} "
                f"WHERE {_quote_identifier(target.column)} = ?"
            )
            for mapping in _mappings_for(target, mappings):
                count = conn.execute(statement, (mapping.old,)).fetchone()[0]
                if count:
                    found.append(
                        (target.path, target.table, target.column, mapping.old, count)
                    )
        except sqlite3.DatabaseError as exc:
            raise DatabaseCutoverError("approved database could not be read") from exc
        finally:
            conn.close()
    return found


def database_schema_inventory(
    root: Path, registered: set[str] | None = None
) -> dict[str, dict[str, list[str]]]:
    """Every database under `root`, with its tables and columns. Read-only and
    deliberately broad: over-reporting only informs the owner's proof."""
    inventory: dict[str, dict[str, list[str]]] = {}
    for path in sorted(root.glob("*/books.db")):
        # Only an entity-root `books.db` is in scope; a nested or `_system`
        # database is not one the registries describe.
        if _ENTITY_SLUG.fullmatch(path.parent.name) is None:
            continue
        # Syntax is not membership: a well-formed slug that no manifest
        # registers names a location no registry describes.
        if registered is not None and path.parent.name not in registered:
            continue
        if path.parent.is_symlink():
            raise DatabaseCutoverError(
                "an entity root is a symlink; the inventory never follows a "
                "redirected database location"
            )
        if path.is_symlink():
            raise DatabaseCutoverError(
                f"{path.relative_to(root).as_posix()} is a symlink; inventory "
                "never follows or silently omits a database redirection"
            )
        conn = _connect(path, read_only=True)
        try:
            tables = sorted(
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            )
            inventory[path.relative_to(root).as_posix()] = {
                table: [
                    row[1]
                    for row in conn.execute(
                        f"PRAGMA table_info({_quote_identifier(table)})"
                    )
                ]
                for table in tables
            }
        except sqlite3.DatabaseError as exc:
            raise DatabaseCutoverError("database could not be read") from exc
        finally:
            conn.close()
    return inventory


def database_reference_inventory(
    root: Path, mappings: tuple[Mapping, ...], registered: set[str] | None = None
) -> list[DatabaseReference]:
    """Exact-value reference counts for owner classification.

    This deliberately enumerates every table and column and reports evidence;
    it does not turn a matching column name or value into writer authority.
    Only the owner's digest-bound `DatabaseTarget` list grants that authority.
    """
    schemas = database_schema_inventory(root, registered)
    found: list[DatabaseReference] = []
    for relative, tables in sorted(schemas.items()):
        path = root / relative
        conn = _connect(path, read_only=True)
        try:
            for table, columns in sorted(tables.items()):
                for column in columns:
                    statement = (
                        f"SELECT COUNT(*) FROM {_quote_identifier(table)} "
                        f"WHERE {_quote_identifier(column)} = ?"
                    )
                    for mapping in mappings:
                        if mapping.axis not in {"product", "member"}:
                            continue
                        count = conn.execute(statement, (mapping.old,)).fetchone()[0]
                        if count:
                            found.append(
                                DatabaseReference(
                                    relative,
                                    table,
                                    column,
                                    mapping.axis,
                                    mapping.old,
                                    count,
                                )
                            )
        except sqlite3.DatabaseError as exc:
            raise DatabaseCutoverError(
                f"{relative} could not be reference-inventoried"
            ) from exc
        finally:
            conn.close()
    return sorted(found)
