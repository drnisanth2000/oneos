from pathlib import Path
import sqlite3

import pytest

from app.cutover_db import (
    DatabaseCutoverError,
    DatabaseReference,
    apply_database_mappings,
    database_reference_inventory,
    database_residuals,
    database_schema_inventory,
    resolve_database_path,
)
from app.cutover_manifest import DatabaseTarget, ManifestError, Mapping


def make_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE ledger (product TEXT, tag TEXT)")
    conn.execute("CREATE TABLE roster (member TEXT)")
    conn.execute("CREATE TABLE fund_holdings (member_id TEXT)")
    conn.execute("INSERT INTO ledger VALUES ('ab', 'ab')")
    conn.execute("INSERT INTO roster VALUES ('ab')")
    conn.execute("INSERT INTO fund_holdings VALUES ('ab')")
    conn.commit()
    conn.close()


def read(path: Path, sql: str) -> list[tuple]:
    conn = sqlite3.connect(path)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


PRODUCT_MAPPING = Mapping(axis="product", old="ab", new="ab-product")
MEMBER_MAPPING = Mapping(axis="member", old="ab", new="ab-member")
BOTH = (MEMBER_MAPPING, PRODUCT_MAPPING)


def test_a_product_target_receives_only_the_product_mapping(tmp_path: Path):
    make_db(tmp_path / "ab" / "books.db")
    target = DatabaseTarget(
        path="ab/books.db", table="ledger", column="product", axis="product"
    )

    apply_database_mappings(tmp_path, (target,), BOTH)

    assert read(tmp_path / "ab" / "books.db", "SELECT product FROM ledger") == [
        ("ab-product",)
    ]


def test_a_member_target_receives_only_the_member_mapping(tmp_path: Path):
    make_db(tmp_path / "ab" / "books.db")
    target = DatabaseTarget(
        path="ab/books.db", table="roster", column="member", axis="member"
    )

    apply_database_mappings(tmp_path, (target,), BOTH)

    assert read(tmp_path / "ab" / "books.db", "SELECT member FROM roster") == [
        ("ab-member",)
    ]


def test_only_the_allowlisted_column_is_updated(tmp_path: Path):
    make_db(tmp_path / "ab" / "books.db")
    target = DatabaseTarget(
        path="ab/books.db", table="ledger", column="product", axis="product"
    )

    apply_database_mappings(tmp_path, (target,), (PRODUCT_MAPPING,))

    assert read(tmp_path / "ab" / "books.db", "SELECT tag FROM ledger") == [("ab",)]
    assert read(
        tmp_path / "ab" / "books.db", "SELECT member_id FROM fund_holdings"
    ) == [("ab",)]


def test_a_matching_column_name_in_another_database_is_untouched(tmp_path: Path):
    make_db(tmp_path / "ab" / "books.db")
    make_db(tmp_path / "zz" / "books.db")
    target = DatabaseTarget(
        path="ab/books.db", table="ledger", column="product", axis="product"
    )

    apply_database_mappings(tmp_path, (target,), (PRODUCT_MAPPING,))

    assert read(tmp_path / "zz" / "books.db", "SELECT product FROM ledger") == [("ab",)]


def unchecked_target(path: str) -> DatabaseTarget:
    """A real DatabaseTarget carrying a path its constructor would refuse.

    `resolve_database_path` is a second, independent layer: a caller that
    obtained a target by any other route must still be refused. Constructing
    through `DatabaseTarget(path=...)` raises `ManifestError` before the
    function under test is entered, so the guard would go untested.
    """
    target = DatabaseTarget(
        path="ab/books.db", table="ledger", column="product", axis="product"
    )
    object.__setattr__(target, "path", path)
    return target


def test_resolve_refuses_an_absolute_path_before_opening_a_connection(tmp_path: Path):
    make_db(tmp_path / "ab" / "books.db")

    # The message matters: without this guard a later check may still refuse
    # on some platforms and not others, so a type-only assertion is not
    # reliably RED.
    with pytest.raises(DatabaseCutoverError, match="must be relative"):
        resolve_database_path(
            tmp_path, unchecked_target(str(tmp_path / "ab" / "books.db"))
        )


def test_resolve_refuses_an_upward_traversal_that_escapes_the_root(tmp_path: Path):
    make_db(tmp_path / "inside" / "ab" / "books.db")
    make_db(tmp_path / "outside" / "books.db")

    # Without the `..` guard the confinement check still refuses this shape
    # with a different diagnosis, so a type-only assertion stays green.
    with pytest.raises(DatabaseCutoverError, match="must not traverse upward"):
        resolve_database_path(
            tmp_path / "inside", unchecked_target("../outside/books.db")
        )


def test_resolve_refuses_an_upward_traversal_that_re_enters_the_root(tmp_path: Path):
    """A `..` landing back inside the root is still not the path the owner
    read, and no later check refuses it."""
    make_db(tmp_path / "ab" / "books.db")

    with pytest.raises(DatabaseCutoverError, match="must not traverse upward"):
        resolve_database_path(tmp_path, unchecked_target("ab/../ab/books.db"))


def test_manifest_construction_also_refuses_those_paths(tmp_path: Path):
    """The first layer stays in place; these tests pin the second."""
    for bad in (str(tmp_path / "ab" / "books.db"), "../outside/books.db"):
        with pytest.raises(ManifestError):
            DatabaseTarget(
                path=bad, table="ledger", column="product", axis="product"
            )


def test_a_symlinked_component_is_refused(tmp_path: Path):
    make_db(tmp_path / "real" / "books.db")
    (tmp_path / "link").symlink_to(tmp_path / "real", target_is_directory=True)

    with pytest.raises(DatabaseCutoverError):
        resolve_database_path(
            tmp_path,
            DatabaseTarget(
                path="link/books.db", table="ledger", column="product", axis="product"
            ),
        )


def test_a_missing_database_is_a_hard_stop(tmp_path: Path):
    with pytest.raises(DatabaseCutoverError):
        apply_database_mappings(
            tmp_path,
            (
                DatabaseTarget(
                    path="ab/books.db",
                    table="ledger",
                    column="product",
                    axis="product",
                ),
            ),
            (PRODUCT_MAPPING,),
        )


def test_an_unknown_table_or_column_is_a_hard_stop(tmp_path: Path):
    make_db(tmp_path / "ab" / "books.db")
    with pytest.raises(DatabaseCutoverError):
        apply_database_mappings(
            tmp_path,
            (
                DatabaseTarget(
                    path="ab/books.db", table="ledger", column="nope", axis="product"
                ),
            ),
            (PRODUCT_MAPPING,),
        )


def test_residuals_are_zero_after_a_complete_update(tmp_path: Path):
    make_db(tmp_path / "ab" / "books.db")
    target = DatabaseTarget(
        path="ab/books.db", table="ledger", column="product", axis="product"
    )

    apply_database_mappings(tmp_path, (target,), (PRODUCT_MAPPING,))

    assert database_residuals(tmp_path, (target,), (PRODUCT_MAPPING,)) == []


def test_the_residual_query_ignores_another_axis_old_value(tmp_path: Path):
    make_db(tmp_path / "ab" / "books.db")
    target = DatabaseTarget(
        path="ab/books.db", table="ledger", column="product", axis="product"
    )

    apply_database_mappings(tmp_path, (target,), BOTH)

    # The column now holds `ab-product`. An untyped query would also look for
    # the member mapping's old value and report a false residual.
    assert database_residuals(tmp_path, (target,), BOTH) == []


def test_residuals_report_a_remaining_old_value(tmp_path: Path):
    make_db(tmp_path / "ab" / "books.db")
    target = DatabaseTarget(
        path="ab/books.db", table="ledger", column="product", axis="product"
    )

    assert database_residuals(tmp_path, (target,), (PRODUCT_MAPPING,)) == [
        ("ab/books.db", "ledger", "product", "ab", 1)
    ]


def test_schema_inventory_lists_tables_and_columns(tmp_path: Path):
    make_db(tmp_path / "ab" / "books.db")

    inventory = database_schema_inventory(tmp_path)

    assert inventory["ab/books.db"]["ledger"] == ["product", "tag"]
    assert inventory["ab/books.db"]["fund_holdings"] == ["member_id"]


def test_schema_inventory_refuses_a_books_db_symlink_without_following_it(
    tmp_path: Path,
):
    outside = tmp_path / "outside.db"
    make_db(outside)
    entity = tmp_path / "ab"
    entity.mkdir()
    (entity / "books.db").symlink_to(outside)

    with pytest.raises(DatabaseCutoverError, match="symlink"):
        database_schema_inventory(tmp_path)


def test_reference_inventory_is_path_column_and_axis_typed(tmp_path: Path):
    make_db(tmp_path / "ab" / "books.db")

    found = database_reference_inventory(tmp_path, BOTH)

    assert DatabaseReference(
        path="ab/books.db",
        table="ledger",
        column="product",
        axis="product",
        old="ab",
        count=1,
    ) in found
    assert DatabaseReference(
        path="ab/books.db",
        table="roster",
        column="member",
        axis="member",
        old="ab",
        count=1,
    ) in found
    # These are evidence for owner classification, never an automatically
    # approved writer allowlist. In particular, the broad counter does not
    # make `tag` or `fund_holdings.member_id` safe to write.
    assert all(item.count > 0 for item in found)


def test_read_only_uri_escapes_special_characters(tmp_path: Path):
    """A `#` or `?` in a path is URI syntax, not a filename character.

    Unescaped, SQLite parses the fragment/query and opens something other than
    the approved database — silently, since the open succeeds.
    """
    odd = tmp_path / "we#ird?dir"
    odd.mkdir()
    make_db(odd / "books.db")
    conn = sqlite3.connect(odd / "books.db")
    conn.execute("INSERT INTO ledger VALUES ('marker', 'x')")
    conn.commit()
    conn.close()

    from app.cutover_db import _connect

    handle = _connect(odd / "books.db", read_only=True)
    try:
        assert handle.execute(
            "SELECT product FROM ledger WHERE product = 'marker'"
        ).fetchall() == [("marker",)]
    finally:
        handle.close()


def make_db_with_update_trigger(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE ledger (product TEXT, tag TEXT)")
    conn.execute("CREATE TABLE audit (seen TEXT)")
    conn.execute(
        "CREATE TRIGGER ledger_audit AFTER UPDATE ON ledger "
        "BEGIN INSERT INTO audit VALUES (new.product); END"
    )
    conn.execute("INSERT INTO ledger VALUES ('ab', 'ab')")
    conn.commit()
    conn.close()


def test_an_update_trigger_on_an_approved_table_is_refused(tmp_path: Path):
    """A trigger turns one reviewed UPDATE into unreviewed side effects.

    The owner approved a column rewrite, not whatever a trigger writes when it
    fires — and those writes land in the same single cutover commit.
    """
    make_db_with_update_trigger(tmp_path / "ab" / "books.db")
    target = DatabaseTarget(
        path="ab/books.db", table="ledger", column="product", axis="product"
    )

    with pytest.raises(DatabaseCutoverError, match="trigger"):
        apply_database_mappings(tmp_path, (target,), (PRODUCT_MAPPING,))

    conn = sqlite3.connect(tmp_path / "ab" / "books.db")
    try:
        assert conn.execute("SELECT * FROM audit").fetchall() == []
        assert conn.execute("SELECT product FROM ledger").fetchall() == [("ab",)]
    finally:
        conn.close()


def test_a_trigger_on_another_table_does_not_block_the_cutover(tmp_path: Path):
    """The refusal is specific to the approved table."""
    make_db(tmp_path / "ab" / "books.db")
    conn = sqlite3.connect(tmp_path / "ab" / "books.db")
    conn.execute("CREATE TABLE other (v TEXT)")
    conn.execute(
        "CREATE TRIGGER other_audit AFTER UPDATE ON other "
        "BEGIN SELECT 1; END"
    )
    conn.commit()
    conn.close()
    target = DatabaseTarget(
        path="ab/books.db", table="ledger", column="product", axis="product"
    )

    apply_database_mappings(tmp_path, (target,), (PRODUCT_MAPPING,))

    assert read(tmp_path / "ab" / "books.db", "SELECT product FROM ledger") == [
        ("ab-product",)
    ]


def test_database_inventory_only_reports_entity_books_db(tmp_path: Path):
    make_db(tmp_path / "ab" / "books.db")
    make_db(tmp_path / "ab" / "nested" / "books.db")
    make_db(tmp_path / "_system" / "books.db")

    assert list(database_schema_inventory(tmp_path)) == ["ab/books.db"]
