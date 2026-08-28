"""PR #15 must-fix 3 & 4: SQLite connection-failure normalization and safe
identifier quoting in `app/registry.py::_count_books_db`.

Must-fix 3 — `sqlite3.connect()` ran BEFORE the `try` block that converts
`sqlite3.DatabaseError` to `RegistryError`, so a permission/lock/open failure
escaped raw (already fatal today — an uncaught `sqlite3.OperationalError` —
this only retypes it, per design §5's "boundary conversions" rule). Verified
`app/rename.py::_books_db_refs` is NOT the same bug: its `sqlite3.connect()`
call is already inside its own `try: ... except sqlite3.Error: continue`,
which *tolerates* (skips) a connection failure exactly like the front-matter
reader tolerates an unreadable file — that tolerance is pre-existing and must
not become fatal, so it is pinned here as a control rather than "fixed".

Must-fix 4 — `table` and `col` are read from `sqlite_master` of a
`books.db` discovered by path, then interpolated directly into f-string SQL
(`PRAGMA table_info({table})`, `... FROM {table} WHERE {col} = ...`). A table
or column name containing a double quote, therefore, breaks out of the
identifier position. Both call sites must double-quote the identifier
(doubling embedded `"`), while keeping parameter binding for the searched
*value*.
"""
from __future__ import annotations

import os
import sqlite3

import pytest

_DB_COLUMNS_KIND = "product"


def _entity_root(tmp_path):
    root = tmp_path / "demo1"
    root.mkdir()
    return root


def _fixture_quote(name: str) -> str:
    """The fixture's OWN identifier quoting, used only to build the synthetic
    hostile database — independent of (and predating) the `_quote_identifier`
    helper under test, so this is not circular."""
    return '"' + name.replace('"', '""') + '"'


def _make_db_with_hostile_table(root, table_name: str, column: str, value: str):
    db = root / "books.db"
    conn = sqlite3.connect(db)
    try:
        qtable, qcol = _fixture_quote(table_name), _fixture_quote(column)
        conn.execute(f"CREATE TABLE {qtable} ({qcol} TEXT)")
        conn.execute(f"INSERT INTO {qtable} ({qcol}) VALUES (?)", (value,))
        conn.commit()
    finally:
        conn.close()
    return db


# --- must-fix 4: identifier quoting ---------------------------------------


def test_count_books_db_tolerates_a_quote_in_the_table_name(tmp_path):
    """A table name containing a double quote must not break the query — it
    must be treated as an ordinary (if unusual) identifier, not SQL syntax."""
    from app.registry import _count_books_db

    root = _entity_root(tmp_path)
    hostile_table = 'products"; DROP TABLE products; --'
    _make_db_with_hostile_table(root, hostile_table, "product", "widget")

    # Must not raise (a naive f-string interpolation raises sqlite3.OperationalError
    # for the malformed generated SQL) and must still find the real row.
    assert _count_books_db(root, "product", "widget") == 1


def test_quote_identifier_doubles_embedded_quotes():
    """Unit test on the quoting helper itself. `col` in both
    `_count_books_db` and `_books_db_refs` is always drawn from a small
    hardcoded vocabulary (`_DB_COLUMNS` / the caller's `column_values` dict)
    and therefore never actually attacker-controlled — but both sites quote
    it anyway (uniform treatment of every interpolated identifier, per the
    must-fix), so the helper's own correctness is pinned directly rather than
    through an unreachable end-to-end column attack."""
    from app.registry import _quote_identifier as registry_quote
    from app.rename import _quote_identifier as rename_quote

    for quote in (registry_quote, rename_quote):
        assert quote("plain") == '"plain"'
        assert quote('has"quote') == '"has""quote"'
        assert quote('a"b"c') == '"a""b""c"'


def test_count_books_db_still_counts_ordinary_tables(tmp_path):
    """Control: an ordinary schema is unaffected by quoting."""
    from app.registry import _count_books_db

    root = _entity_root(tmp_path)
    _make_db_with_hostile_table(root, "products", "product", "widget")
    _make_db_with_hostile_table.__wrapped__ = None  # no-op, keeps linters quiet

    assert _count_books_db(root, "product", "widget") == 1
    assert _count_books_db(root, "product", "nonexistent") == 0


# --- must-fix 3: connection-failure normalization -------------------------


def test_count_books_db_connect_failure_becomes_registry_error(tmp_path, monkeypatch):
    """Simulate a `sqlite3.connect()` failure (permission/lock/corruption at
    open time) and assert it is retyped to `RegistryError` rather than
    escaping raw. `sqlite3.connect()` itself is monkeypatched rather than
    relying on OS permission bits, which a root process ignores."""
    from app import registry
    from app.registry import RegistryError, _count_books_db

    root = _entity_root(tmp_path)
    _make_db_with_hostile_table(root, "products", "product", "widget")

    def _boom(*args, **kwargs):
        raise sqlite3.OperationalError("unable to open database file")

    monkeypatch.setattr(registry.sqlite3, "connect", _boom)

    with pytest.raises(RegistryError):
        _count_books_db(root, "product", "widget")


def test_count_books_db_connect_failure_never_attempts_close_on_the_failed_object(
    tmp_path, monkeypatch
):
    """Must-fix 3's second half: 'close only when creation succeeded'. If
    `conn.close()` were attempted after a failed `connect()`, referencing
    the never-assigned `conn` name would itself raise `UnboundLocalError`
    instead of the intended `RegistryError` — this proves the fixed code
    path never reaches such a call."""
    from app import registry
    from app.registry import RegistryError, _count_books_db

    root = _entity_root(tmp_path)
    _make_db_with_hostile_table(root, "products", "product", "widget")

    def _boom(*args, **kwargs):
        raise sqlite3.OperationalError("unable to open database file")

    monkeypatch.setattr(registry.sqlite3, "connect", _boom)

    try:
        _count_books_db(root, "product", "widget")
    except RegistryError:
        pass
    except UnboundLocalError:
        pytest.fail("close() was attempted on a connection that was never created")


# --- control: rename.py's equivalent site is already correctly tolerant ---


def test_rename_books_db_refs_already_tolerates_connect_failure(tmp_path, monkeypatch):
    """`app/rename.py::_books_db_refs` wraps `sqlite3.connect()` in its own
    `try/except sqlite3.Error: continue` — a PRE-EXISTING tolerance (skip a
    db this process cannot open, mirroring the front-matter reader's
    `except OSError: continue`), not an escaping-raw bug. This control pins
    that the tolerance is untouched: a connect failure must NOT raise, and
    must simply omit that database's rows from the report."""
    from app import rename

    vault = tmp_path
    root = _entity_root(vault)
    _make_db_with_hostile_table(root, "products", "product", "widget")

    def _boom(*args, **kwargs):
        raise sqlite3.OperationalError("unable to open database file")

    monkeypatch.setattr(rename.sqlite3, "connect", _boom)

    # Must not raise.
    reports = rename._books_db_refs(vault, {"product": "widget"})
    assert reports == []


def test_rename_books_db_refs_tolerates_a_quote_in_the_table_name(tmp_path):
    """must-fix 4 applies to `app/rename.py` too (`_books_db_refs`)."""
    from app import rename

    vault = tmp_path
    root = _entity_root(vault)
    hostile_table = 'members"; DROP TABLE members; --'
    _make_db_with_hostile_table(root, hostile_table, "member", "alice")

    reports = rename._books_db_refs(vault, {"member": "alice"})
    assert len(reports) == 1
    assert "alice" in reports[0]


def test_rename_books_db_refs_tolerates_a_quote_in_the_column_name(tmp_path):
    """Unlike `_count_books_db` (whose `cols` always comes from the hardcoded
    `_DB_COLUMNS` vocabulary), `_books_db_refs` takes `column_values` as a
    direct parameter — so a hostile column name really is reachable through
    its own signature, independent of today's only (safe) caller."""
    from app import rename

    vault = tmp_path
    root = _entity_root(vault)
    hostile_column = 'member"; DROP TABLE members; --'
    _make_db_with_hostile_table(root, "members", hostile_column, "alice")

    reports = rename._books_db_refs(vault, {hostile_column: "alice"})
    assert len(reports) == 1
    assert "alice" in reports[0]
