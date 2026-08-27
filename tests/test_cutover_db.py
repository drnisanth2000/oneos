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


def test_an_absolute_path_is_refused(tmp_path: Path):
    make_db(tmp_path / "ab" / "books.db")
    with pytest.raises(ManifestError):
        resolve_database_path(
            tmp_path,
            DatabaseTarget(
                path=str(tmp_path / "ab" / "books.db"),
                table="ledger",
                column="product",
                axis="product",
            ),
        )


def test_a_path_escaping_the_root_is_refused(tmp_path: Path):
    make_db(tmp_path / "inside" / "ab" / "books.db")
    with pytest.raises(ManifestError):
        resolve_database_path(
            tmp_path / "inside",
            DatabaseTarget(
                path="../outside/books.db",
                table="ledger",
                column="product",
                axis="product",
            ),
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
