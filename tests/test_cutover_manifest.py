import hashlib
import json

import pytest

from app.cutover_manifest import (
    ApprovalManifest,
    ApprovalRecord,
    DatabaseTarget,
    Disposition,
    ManifestError,
    Mapping,
    canonical_bytes,
    load_manifest,
    manifest_digest,
    verify_manifest,
)

EXECUTOR_COMMIT = "e" * 40


def sample_manifest() -> ApprovalManifest:
    return ApprovalManifest(
        source_head="a" * 40,
        mappings=(
            Mapping(axis="entity", old="ab", new="ab-entity"),
            Mapping(axis="product", old="q7", new="q7-product"),
            Mapping(axis="member", old="m7", new="m7-member"),
        ),
        databases=(
            DatabaseTarget(
                path="ab/books.db", table="ledger", column="product", axis="product"
            ),
            DatabaseTarget(
                path="ab/books.db", table="roster", column="member", axis="member"
            ),
        ),
        dispositions=(
            Disposition(
                path="notes/one.md",
                axis="entity",
                old="ab",
                ordinal=1,
                context_sha256="0" * 64,
                line=3,
                kind="incidental",
            ),
        ),
    )


def record_for(manifest: ApprovalManifest) -> ApprovalRecord:
    return ApprovalRecord(
        manifest_sha256=manifest_digest(manifest),
        executor_commit=EXECUTOR_COMMIT,
        approved_by="owner",
    )


def test_canonical_bytes_are_stable_across_construction_order():
    first = sample_manifest()
    second = ApprovalManifest(
        source_head="a" * 40,
        mappings=(
            Mapping(axis="member", old="m7", new="m7-member"),
            Mapping(axis="product", old="q7", new="q7-product"),
            Mapping(axis="entity", old="ab", new="ab-entity"),
        ),
        databases=sample_manifest().databases,
        dispositions=sample_manifest().dispositions,
    )
    assert canonical_bytes(first) == canonical_bytes(second)


def test_manifest_never_contains_its_own_digest():
    manifest = sample_manifest()
    raw = canonical_bytes(manifest)
    assert manifest_digest(manifest) not in raw.decode("utf-8")
    loaded = json.loads(raw)
    assert "digest" not in loaded
    assert "sha256" not in loaded


def test_digest_is_the_sha256_of_the_canonical_bytes():
    manifest = sample_manifest()
    assert manifest_digest(manifest) == hashlib.sha256(
        canonical_bytes(manifest)
    ).hexdigest()


def test_verify_accepts_a_matching_record():
    manifest = sample_manifest()
    verify_manifest(canonical_bytes(manifest), record_for(manifest))


def test_verify_refuses_a_mismatched_record():
    manifest = sample_manifest()
    with pytest.raises(ManifestError):
        verify_manifest(
            canonical_bytes(manifest),
            ApprovalRecord(
                manifest_sha256="b" * 64,
                executor_commit=EXECUTOR_COMMIT,
                approved_by="owner",
            ),
        )


def test_verify_refuses_a_single_changed_byte():
    manifest = sample_manifest()
    record = record_for(manifest)
    tampered = canonical_bytes(manifest).replace(b"ab-entity", b"ab-produce")
    with pytest.raises(ManifestError):
        verify_manifest(tampered, record)


def test_verify_refuses_digest_matching_but_noncanonical_bytes():
    manifest = sample_manifest()
    canonical = canonical_bytes(manifest)
    noncanonical = (
        json.dumps(json.loads(canonical), indent=2, ensure_ascii=False) + "\n"
    ).encode("utf-8")
    assert noncanonical != canonical
    record = ApprovalRecord(
        manifest_sha256=hashlib.sha256(noncanonical).hexdigest(),
        executor_commit=EXECUTOR_COMMIT,
        approved_by="owner",
    )

    with pytest.raises(ManifestError, match="canonical"):
        verify_manifest(noncanonical, record)


def test_round_trip_through_canonical_bytes():
    manifest = sample_manifest()
    assert load_manifest(canonical_bytes(manifest)) == manifest


def test_canonical_bytes_have_exact_utf8_json_framing():
    manifest = sample_manifest()
    raw = canonical_bytes(manifest)

    assert raw.endswith(b"\n") and not raw.endswith(b"\n\n")
    assert b"\r" not in raw
    assert raw == (
        json.dumps(
            json.loads(raw),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=False,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def test_duplicate_object_keys_are_refused_even_with_a_matching_digest():
    manifest = sample_manifest()
    raw = canonical_bytes(manifest)
    duplicate = raw.replace(
        b'{"source_head":',
        b'{"source_head":"' + b"b" * 40 + b'","source_head":',
        1,
    )
    record = ApprovalRecord(
        manifest_sha256=hashlib.sha256(duplicate).hexdigest(),
        executor_commit=EXECUTOR_COMMIT,
        approved_by="owner",
    )

    with pytest.raises(ManifestError, match="duplicate"):
        verify_manifest(duplicate, record)


@pytest.mark.parametrize(
    "path",
    ("/absolute/books.db", "../escape/books.db", "a\\books.db", "a//books.db", "a/./books.db", "a/../books.db"),
    ids=("absolute", "parent", "backslash", "empty-segment", "dot", "parent-segment"),
)
def test_manifest_paths_must_be_canonical_relative_posix(path: str):
    with pytest.raises(ManifestError, match="path"):
        DatabaseTarget(path=path, table="t", column="c", axis="product")


def test_approval_record_requires_an_executor_commit():
    with pytest.raises(ManifestError, match="executor"):
        ApprovalRecord(
            manifest_sha256="a" * 64,
            executor_commit="not-a-commit",
            approved_by="owner",
        )


def test_database_target_requires_every_part():
    for missing in ("path", "table", "column", "axis"):
        fields = {
            "path": "ab/books.db",
            "table": "ledger",
            "column": "product",
            "axis": "product",
        }
        fields[missing] = ""
        with pytest.raises(ManifestError):
            DatabaseTarget(**fields)


def test_database_target_axis_must_be_product_or_member():
    DatabaseTarget(path="a/books.db", table="t", column="c", axis="product")
    DatabaseTarget(path="a/books.db", table="t", column="c", axis="member")
    for refused in ("entity", "workspace", "project"):
        with pytest.raises(ManifestError):
            DatabaseTarget(path="a/books.db", table="t", column="c", axis=refused)


def test_disposition_kind_is_closed():
    with pytest.raises(ManifestError):
        Disposition(
            path="a.md", axis="entity", old="ab", ordinal=1,
            context_sha256="0" * 64, line=1, kind="handfix"
        )


def test_disposition_requires_the_complete_stable_identity():
    valid = {
        "path": "a.md",
        "axis": "entity",
        "old": "ab",
        "ordinal": 1,
        "context_sha256": "0" * 64,
        "line": 1,
        "kind": "incidental",
    }
    for field, bad in (
        ("path", ""),
        ("axis", "unknown"),
        ("old", ""),
        ("ordinal", 0),
        ("context_sha256", "not-a-digest"),
        ("line", 0),
    ):
        fields = valid | {field: bad}
        with pytest.raises(ManifestError, match="disposition"):
            Disposition(**fields)


def test_structural_disposition_requires_a_typed_location():
    with pytest.raises(ManifestError):
        Disposition(
            path="a.md", axis="entity", old="ab", ordinal=1,
            context_sha256="0" * 64, line=1, kind="structural"
        )
    allowed = Disposition(
        path="a.md",
        axis="entity",
        old="ab",
        ordinal=1,
        context_sha256="0" * 64,
        line=1,
        kind="structural",
        typed_location="entity:front-matter:entity",
    )
    assert allowed.typed_location == "entity:front-matter:entity"


def test_two_targets_may_not_claim_one_column_on_different_axes():
    """Per-target axis filtering is correct, but nothing forbade two targets
    naming one column under different axes.

    With a literal short on both axes the canonical sort applies `member`
    first, the column ends up holding the member value, and the residual query
    reports nothing — the exact silent corruption the axis rule exists to
    prevent, in a binary file no text gate can inspect.
    """
    with pytest.raises(ManifestError, match="claimed by more than one"):
        ApprovalManifest(
            source_head="a" * 40,
            mappings=(Mapping(axis="entity", old="ab", new="ab-entity"),),
            databases=(
                DatabaseTarget(
                    path="ab/books.db", table="ledger", column="ref", axis="product"
                ),
                DatabaseTarget(
                    path="ab/books.db", table="ledger", column="ref", axis="member"
                ),
            ),
            dispositions=(),
        )


def test_the_same_column_in_a_different_database_is_permitted():
    """The key is the whole triple: a different database is a different column."""
    manifest = ApprovalManifest(
        source_head="a" * 40,
        mappings=(Mapping(axis="entity", old="ab", new="ab-entity"),),
        databases=(
            DatabaseTarget(
                path="ab/books.db", table="ledger", column="ref", axis="product"
            ),
            DatabaseTarget(
                path="zz/books.db", table="ledger", column="ref", axis="product"
            ),
        ),
        dispositions=(),
    )
    assert len(manifest.databases) == 2


def test_a_database_target_must_be_an_entity_books_db():
    """The only database the cutover migrates is `<entity>/books.db`.

    `books.db` sits at an entity root and serves all its modules. Accepting an
    arbitrary path lets an approved target name a database the design never
    contemplated, in a location no registry describes.
    """
    for refused in (
        "ab/nested/other.db",
        "ab/nested/books.db",
        "books.db",
        "ab/ledger.db",
        "_system/books.db",
    ):
        with pytest.raises(ManifestError, match="entity"):
            DatabaseTarget(
                path=refused, table="ledger", column="product", axis="product"
            )


def test_an_entity_books_db_is_accepted():
    target = DatabaseTarget(
        path="ab/books.db", table="ledger", column="product", axis="product"
    )
    assert target.path == "ab/books.db"
