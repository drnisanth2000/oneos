# Short-Identifier Cutover — Stage A Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the public tooling that inventories sub-floor registry identifiers, binds an owner-approved typed mapping by digest, and produces one cutover commit with a verified pre-writer-restart revert window — built in an isolated detached worktree, gated by two independent residual checks, and promoted under the shared action lock — without enforcing the length floor at read time.

**Architecture:** Seven focused modules. `app/identifiers.py` single-sources the five-character floor, the deterministic suffix mapping, and mapping validation. `app/cutover_manifest.py` holds the canonical manifest and its separate approval record, with every database target typed by axis. `app/cutover_locations.py` owns the closed rewrite-location table, syntax-confined rewriters, the advisory scan over locations outside that table, and the scoped residual gate. `app/cutover_db.py` owns path-confined, axis-filtered database updates, reference-count inventory, and the axis-typed in-database residual query. `app/cutover_inventory.py` enumerates identifiers, collisions, clean-state preconditions, and unmigratable content. `app/cutover_build.py` orchestrates the isolated build, validator gate, and regenerated-advisory comparison. `app/cutover.py` owns promotion under the action lock and the CLI. Dry-run and apply consume the same `BuildResult`; the database row summary is never reconstructed by running a second migration.

**Tech Stack:** Python 3.12, stdlib `re`/`pathlib`/`sqlite3`/`hashlib`/`subprocess`/`tempfile`, PyYAML, pytest, Git CLI.

**Spec:** `docs/superpowers/specs/2026-08-26-short-identifier-cutover-design.md` at revision 9, commit `9ad53a0`.

**Supersedes:** the rejected plans at `5f3e82b` and `a96fc18`. Neither is to be consulted during implementation; their code blocks contained the defects listed below and are not a starting point.

---

## Why this plan replaces the previous one

Review of `5f3e82b` and the attempted rewrite at `a96fc18` confirmed the original eight defects plus eight plan-level defects. Each is fixed here by construction rather than deferred to implementation:

| # | Defect in `5f3e82b` | Fixed by |
|---|---|---|
| 1 | Every product *and* member mapping was applied to every approved column, so a product column could receive `ab-member` | Task 2 types `DatabaseTarget` with `axis`; Task 7 filters on it |
| 2 | `inventory` listed only database schemas; `dry-run` printed mappings and never built | Task 9 enumerates identifiers; Task 16 implements both commands against the real build |
| 3 | `check_collisions` was never called; no scoped residual gate existed; structural dispositions were never validated | Task 6 builds the gate; Task 11 wires all three into the build |
| 4 | The advisory scan exempted any line containing `former_slugs` anywhere and silently skipped unreadable files | Task 5 |
| 5 | Promotion claimed the action lock in a docstring but no caller took it, and it never re-checked ignored content | Task 12 |
| 6 | A deterministic temporary branch was deleted in `finally` even when creation failed | Task 10 uses a detached, uniquely named worktree with no branch |
| 7 | The policy rewriter rewrote every quoted string; its test asserted text, not denial | Tasks 4 and 14 |
| 8 | Two mutation rows said "re-point at the build path"; Task 1 claimed eight tests and had nine | Task 17: every row is an exact edit with a named node, and every count is stated from the test bodies |

The `a96fc18` review added these binding corrections:

1. Advisory occurrences are **outside** the typed rewrite table. Typed registry,
   front-matter, proposal and policy references never require owner disposition.
2. Rewriters match exact YAML scalars or keys, never a scalar prefix or text inside
   another string. Existing `former_slugs` lists are appended to, never duplicated.
3. Inventory requires a clean live status, refuses affected ignored/untracked
   content, reports database reference counts, and emits typed candidate targets.
4. A symlink encountered where a text or `books.db` artifact is expected is a hard
   refusal, never a silent skip.
5. The isolated build regenerates the advisory report, compares it exactly to the
   approved incidental dispositions, runs the validators, and only then commits.
6. `BuildResult` carries the exact built commit and per-target database row counts;
   dry-run never rebuilds SQLite merely to reconstruct its summary.
7. Promotion verifies that live `HEAD` equals the exact built commit and tests that
   the promoted database blob is the blob verified in the isolated worktree.
8. Every mutation is one independently applicable exact substitution tied to one
   pre-existing full node id and the intended failing assertion. Each node runs
   alone with `--tb=line`, so the recorded assertion cannot come from surrounding
   source or another test. Compound mutations and "wrong reason or passes"
   evidence are forbidden.
9. The validator fixture and parser use the public `BUILD.md` contract's exact
   `0 error(s), 0 warning(s)` summary. The parser requires that complete line and
   has a false-zero regression, so `10 error(s)` cannot be accepted as zero.
10. Inventory captures one source HEAD and reads all tracked registry, advisory,
    and database evidence from a detached worktree at that immutable commit.
    Live HEAD, status, and affected ignored content are rechecked before return.
11. Advisory dispositions bind path, axis, old value, file-level token ordinal,
    and exact-line context digest. Line number remains display data only.
12. Plain `git revert` is documented as safe only before writers restart.
    Rollback after later SQLite writes is a separate quiesced recovery migration.

Seven further requirements from the same review are covered: database paths validated as relative, confined, and non-symlinked (Task 7); each target applied only to its declared axis (Task 7); every approved mapping validated against the deterministic rule (Tasks 1 and 11); dispositions checked before paths move with a separate scoped gate afterward (Task 11); failed promotion distinguished from committed-but-unconfirmed (Task 12); every design acceptance test mapped to an exact node (Task 17); and every mutation an exact reproducible edit (Task 17).

## Scope

**Stage A only.** The design defines two public stages separated by the private migration, and they cannot ship together: read-time floor enforcement would make the tool unable to read the vault it must migrate. Stage A is a complete, testable deliverable on its own — nothing in it enforces the floor at a read-time validation site.

## Global constraints

- Base the branch on freshly fetched `origin/main`; record the SHA before branching.
- Public repository and synthetic fixtures only. Never read, request, infer, or display a live vault, registry value, database, path, or history.
- Add no dependency, no schema, no registry value, no exemption, no second scanner.
- Do **not** enforce the length floor at any read-time validation site in Stage A.
- Do not modify the parked Item 2 branch or its worktree.
- Do not push, open a pull request, merge, delete a branch, or remove a worktree.
- Never run `git reset --hard` or `git clean -fd` against the live vault, in code or by hand.
- Stop on any dependency, schema, convention, security-boundary, destructive-action, deployment, private-material, or unresolved-product decision.

## Execution preconditions

```bash
git fetch origin
BASE_SHA="$(git rev-parse origin/main)"
WORKTREE="$(dirname "$(git rev-parse --show-toplevel)")/oneos-cutover-stage-a"
git worktree add "$WORKTREE" -b codex/short-id-cutover-stage-a "$BASE_SHA"
cd "$WORKTREE"
test "$(git rev-parse HEAD)" = "$BASE_SHA"
test -z "$(git status --porcelain)"
uv run python -m pytest -q
```

The baseline must report **1,476 or more passing tests** and zero failures. If it does not, stop and return the exact SHA and failure output.

## File structure

| File | Responsibility |
|---|---|
| Create `app/identifiers.py` | Floor constant, `meets_floor`, `suffix_for_axis`, `map_identifier`, `validate_mapping_pair`. Pure. Stage B's validators consume this. |
| Create `app/cutover_manifest.py` | `Mapping`, `DatabaseTarget` (axis-typed), `Disposition`, `ApprovalManifest`, `ApprovalRecord`, canonical bytes, digest verification. |
| Create `app/cutover_locations.py` | Closed location table, scoped rewriters, strict advisory scan, scoped residual gate. |
| Create `app/cutover_db.py` | Path-confined axis-filtered updates, axis-typed residual query, schema and reference-count inventory. |
| Create `app/cutover_inventory.py` | Collision checks, clean-status precondition, ignored/untracked hard stop. |
| Create `app/cutover_build.py` | Isolated detached worktree, build ordering, both residual gates, regenerated advisory comparison, validators, exact `BuildResult`. |
| Create `app/cutover.py` | Promotion under the action lock, outcome classification, CLI. |
| Create `tests/test_cutover_identifiers.py` | Floor, mapping, mapping validation. |
| Create `tests/test_cutover_manifest.py` | Manifest, approval record, digest binding, axis typing. |
| Create `tests/test_cutover_locations.py` | Partition, scoped rewriters, advisory strictness, residual gate. |
| Create `tests/test_cutover_db.py` | Axis filtering, path confinement, allowlist narrowness, residual query. |
| Create `tests/test_cutover_inventory.py` | Collisions, ignored/untracked. |
| Create `tests/test_cutover_build.py` | Ordering, one commit, isolation, revert, receipts, proposals, fail-open. |
| Create `tests/test_cutover_promotion.py` | Lock, prechecks, refusal, outcome distinction. |
| Create `tests/test_cutover_cli.py` | Inventory, dry run, apply gating. |

---

### Task 1: The identifier floor, mapping, and mapping validation

**Files:**
- Create: `app/identifiers.py`
- Test: `tests/test_cutover_identifiers.py`

**Interfaces:**
- Produces: `IDENTIFIER_MINIMUM_LENGTH`, `AXES`, `DATABASE_AXES`, `meets_floor`, `suffix_for_axis`, `map_identifier`, `validate_mapping_pair`, `AxisError`.
- Consumed by: every other cutover module, and Stage B's validation sites.

- [ ] **Step 1: Write the failing test**

Create `tests/test_cutover_identifiers.py`:

```python
import pytest

from app.identifiers import (
    AXES,
    DATABASE_AXES,
    IDENTIFIER_MINIMUM_LENGTH,
    AxisError,
    map_identifier,
    meets_floor,
    suffix_for_axis,
    validate_mapping_pair,
)


def test_floor_is_one_above_the_audit_long_term_threshold():
    assert IDENTIFIER_MINIMUM_LENGTH == 5


def test_meets_floor_counts_hyphens():
    assert not meets_floor("ab")
    assert not meets_floor("abcd")
    assert meets_floor("abcde")
    assert meets_floor("a-cde")


def test_axes_are_the_four_registry_axes():
    assert AXES == ("entity", "product", "member", "workspace")


def test_database_axes_exclude_entity_and_workspace():
    assert DATABASE_AXES == frozenset({"product", "member"})


def test_suffix_for_each_axis():
    assert suffix_for_axis("entity") == "-entity"
    assert suffix_for_axis("product") == "-product"
    assert suffix_for_axis("member") == "-member"
    assert suffix_for_axis("workspace") == "-workspace"


def test_unknown_axis_is_refused():
    with pytest.raises(AxisError):
        suffix_for_axis("project")
    with pytest.raises(AxisError):
        map_identifier("project", "ab")


def test_mapping_is_deterministic_and_appends_the_axis_suffix():
    assert map_identifier("entity", "ab") == "ab-entity"
    assert map_identifier("workspace", "q7") == "q7-workspace"
    assert map_identifier("entity", "ab") == map_identifier("entity", "ab")


def test_every_output_satisfies_the_floor():
    for axis in AXES:
        assert meets_floor(map_identifier(axis, "a"))


def test_mapping_refuses_an_identifier_that_already_meets_the_floor():
    with pytest.raises(AxisError):
        map_identifier("entity", "abcde")


def test_mapping_refuses_an_already_suffixed_identifier():
    with pytest.raises(AxisError):
        map_identifier("entity", "a-entity")


def test_validate_mapping_pair_accepts_the_deterministic_result():
    validate_mapping_pair("entity", "ab", "ab-entity")


def test_validate_mapping_pair_refuses_a_hand_edited_new_value():
    with pytest.raises(AxisError):
        validate_mapping_pair("entity", "ab", "ab-entity-2")
    with pytest.raises(AxisError):
        validate_mapping_pair("entity", "ab", "ab-product")
    with pytest.raises(AxisError):
        validate_mapping_pair("entity", "ab", "something-else")
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `uv run python -m pytest -q tests/test_cutover_identifiers.py`
Expected: collection error, `ModuleNotFoundError: No module named 'app.identifiers'`.

- [ ] **Step 3: Write the minimal implementation**

Create `app/identifiers.py`:

```python
"""identifiers.py — the single source of the registry-identifier length rule.

The grammar itself is still restated in several modules; only the *length*
rule lives here, because five independent length checks would reproduce the
sidebar/validator disagreement AGENTS.md warns about.

Five is one character above the publication audit's long-term threshold of
four, so every registry identifier is matched by the audit's strongest rule
with one character to spare.
"""
from __future__ import annotations

#: Minimum identifier length, counting hyphens.
IDENTIFIER_MINIMUM_LENGTH = 5

#: The four registry axes this cutover governs. `project` is a pipeline
#: directory name, not a registry identifier, and is deliberately absent.
AXES = ("entity", "product", "member", "workspace")

#: The only axes whose values are stored in a database column. An `entity` or
#: `workspace` database target is a hard stop.
DATABASE_AXES = frozenset({"product", "member"})

_SUFFIXES = {axis: f"-{axis}" for axis in AXES}


class AxisError(ValueError):
    """An unknown axis, an identifier that must not be mapped, or a mapping
    whose new value is not the deterministic result."""


def meets_floor(value: str) -> bool:
    return len(value) >= IDENTIFIER_MINIMUM_LENGTH


def suffix_for_axis(axis: str) -> str:
    try:
        return _SUFFIXES[axis]
    except KeyError as exc:
        raise AxisError(f"unknown axis {axis!r}") from exc


def map_identifier(axis: str, old: str) -> str:
    """The new identifier for a sub-floor `old` on `axis`.

    Total and deterministic in `(axis, old)`: no lookup, no counter, no
    tie-break. That is what lets a dry-run diff be trusted as a preview.
    """
    suffix = suffix_for_axis(axis)
    if meets_floor(old):
        raise AxisError("identifier already meets the floor and is not rewritten")
    if any(old.endswith(candidate) for candidate in _SUFFIXES.values()):
        # Unreachable by arithmetic — every suffix is >= 7 characters, so an
        # already-suffixed value is >= 8 and cannot be sub-floor. Asserted
        # anyway so a future edit to the floor cannot silently double-suffix.
        raise AxisError("identifier already carries an axis suffix")
    return f"{old}{suffix}"


def validate_mapping_pair(axis: str, old: str, new: str) -> None:
    """Refuse any approved mapping whose new value is not the deterministic
    result. An owner approves a table; this proves the table was produced by
    the rule rather than typed by hand."""
    expected = map_identifier(axis, old)
    if new != expected:
        raise AxisError(
            f"mapping for axis {axis!r} does not match the deterministic rule"
        )
```

- [ ] **Step 4: Run the test and confirm it passes**

Run: `uv run python -m pytest -q tests/test_cutover_identifiers.py`
Expected: PASS, 12 tests.

- [ ] **Step 5: Commit**

```bash
git add app/identifiers.py tests/test_cutover_identifiers.py
git commit -m "feat: single-source the registry identifier floor and mapping"
```

---

### Task 2: The approval manifest with axis-typed database targets

**Files:**
- Create: `app/cutover_manifest.py`
- Test: `tests/test_cutover_manifest.py`

**Interfaces:**
- Produces: `Mapping`, `DatabaseTarget`, `Disposition`, `ApprovalManifest`, `ApprovalRecord`, `canonical_bytes`, `manifest_digest`, `load_manifest`, `verify_manifest`, `ManifestError`.

The manifest never contains its own digest: a self-referential hash makes verification depend on an agreement about which bytes were hashed, and that agreement is what an accident gets to break.

- [ ] **Step 1: Write the failing test**

Create `tests/test_cutover_manifest.py`:

```python
import hashlib

import pytest
import yaml

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
    loaded = yaml.safe_load(raw)
    assert "digest" not in loaded
    assert "sha256" not in loaded


def test_digest_is_the_sha256_of_the_canonical_bytes():
    manifest = sample_manifest()
    assert manifest_digest(manifest) == hashlib.sha256(
        canonical_bytes(manifest)
    ).hexdigest()


def test_verify_accepts_a_matching_record():
    manifest = sample_manifest()
    verify_manifest(
        canonical_bytes(manifest),
        ApprovalRecord(manifest_sha256=manifest_digest(manifest), approved_by="owner"),
    )


def test_verify_refuses_a_mismatched_record():
    manifest = sample_manifest()
    with pytest.raises(ManifestError):
        verify_manifest(
            canonical_bytes(manifest),
            ApprovalRecord(manifest_sha256="b" * 64, approved_by="owner"),
        )


def test_verify_refuses_a_single_changed_byte():
    manifest = sample_manifest()
    record = ApprovalRecord(
        manifest_sha256=manifest_digest(manifest), approved_by="owner"
    )
    tampered = canonical_bytes(manifest).replace(b"ab-entity", b"ab-produce")
    with pytest.raises(ManifestError):
        verify_manifest(tampered, record)


def test_verify_refuses_digest_matching_but_noncanonical_bytes():
    manifest = sample_manifest()
    canonical = canonical_bytes(manifest)
    noncanonical = yaml.safe_dump(
        yaml.safe_load(canonical), sort_keys=True, allow_unicode=True
    ).encode("utf-8")
    assert noncanonical != canonical
    record = ApprovalRecord(
        manifest_sha256=hashlib.sha256(noncanonical).hexdigest(),
        approved_by="owner",
    )

    with pytest.raises(ManifestError, match="canonical"):
        verify_manifest(noncanonical, record)


def test_round_trip_through_canonical_bytes():
    manifest = sample_manifest()
    assert load_manifest(canonical_bytes(manifest)) == manifest


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
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `uv run python -m pytest -q tests/test_cutover_manifest.py`
Expected: collection error, `ModuleNotFoundError: No module named 'app.cutover_manifest'`.

- [ ] **Step 3: Write the minimal implementation**

Create `app/cutover_manifest.py`:

```python
"""cutover_manifest.py — what the owner approved, and the record that binds it.

Two artifacts, deliberately separate. The manifest states what will happen.
The approval record holds the manifest's SHA-256 plus the approval. The
manifest never carries its own digest.

Every database target carries an `axis`. Without it an implementation has
nothing to filter on and applies every product *and* member mapping to every
approved column; where one literal is short on both axes, an approved product
column silently receives a member identifier, invisibly, because the file is
binary.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re

import yaml

from .identifiers import AXES, DATABASE_AXES

_DISPOSITION_KINDS = frozenset({"incidental", "structural"})
_SHA256 = re.compile(r"[0-9a-f]{64}")


class ManifestError(Exception):
    pass


@dataclass(frozen=True, order=True)
class Mapping:
    axis: str
    old: str
    new: str

    def __post_init__(self) -> None:
        if self.axis not in AXES:
            raise ManifestError(f"unknown axis {self.axis!r}")
        if not self.old or not self.new:
            raise ManifestError("mapping requires both old and new values")


@dataclass(frozen=True, order=True)
class DatabaseTarget:
    """One approved `(source-relative path, table, column, axis)` target.

    All four parts are mandatory. The path is required because the vault holds
    one database per entity root and their schemas are not proven identical.
    The axis is required because a column stores identifiers of exactly one
    axis, and a target must never receive another axis's mapping.
    """

    path: str
    table: str
    column: str
    axis: str

    def __post_init__(self) -> None:
        for field_name in ("path", "table", "column", "axis"):
            if not getattr(self, field_name):
                raise ManifestError(f"database target requires {field_name!r}")
        if self.axis not in DATABASE_AXES:
            raise ManifestError(
                f"database target axis must be product or member, not {self.axis!r}"
            )


@dataclass(frozen=True, order=True)
class Disposition:
    path: str
    axis: str
    old: str
    ordinal: int
    context_sha256: str
    line: int
    kind: str
    typed_location: str = ""

    def __post_init__(self) -> None:
        if (
            not self.path
            or self.axis not in AXES
            or not self.old
            or self.ordinal < 1
            or _SHA256.fullmatch(self.context_sha256) is None
            or self.line < 1
        ):
            raise ManifestError("disposition requires a complete stable identity")
        if self.kind not in _DISPOSITION_KINDS:
            raise ManifestError(f"unknown disposition kind {self.kind!r}")
        if self.kind == "structural" and not self.typed_location:
            raise ManifestError(
                "a structural disposition must name the typed location that "
                "will rewrite it; there is no hand-fix option"
            )


@dataclass(frozen=True)
class ApprovalManifest:
    source_head: str
    mappings: tuple[Mapping, ...]
    databases: tuple[DatabaseTarget, ...]
    dispositions: tuple[Disposition, ...]


@dataclass(frozen=True)
class ApprovalRecord:
    manifest_sha256: str
    approved_by: str


def canonical_bytes(manifest: ApprovalManifest) -> bytes:
    """Deterministic serialisation: sorted members, fixed key order, UTF-8."""
    document = {
        "source_head": manifest.source_head,
        "mappings": [
            {"axis": item.axis, "old": item.old, "new": item.new}
            for item in sorted(manifest.mappings)
        ],
        "databases": [
            {
                "path": item.path,
                "table": item.table,
                "column": item.column,
                "axis": item.axis,
            }
            for item in sorted(manifest.databases)
        ],
        "dispositions": [
            {
                "path": item.path,
                "axis": item.axis,
                "old": item.old,
                "ordinal": item.ordinal,
                "context_sha256": item.context_sha256,
                "line": item.line,
                "kind": item.kind,
                "typed_location": item.typed_location,
            }
            for item in sorted(manifest.dispositions)
        ],
    }
    return yaml.safe_dump(
        document, sort_keys=False, allow_unicode=True, default_flow_style=False
    ).encode("utf-8")


def manifest_digest(manifest: ApprovalManifest) -> str:
    return hashlib.sha256(canonical_bytes(manifest)).hexdigest()


def load_manifest(raw: bytes) -> ApprovalManifest:
    try:
        document = yaml.safe_load(raw.decode("utf-8")) or {}
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ManifestError("approval manifest is unreadable") from exc
    if not isinstance(document, dict):
        raise ManifestError("approval manifest must be a mapping")
    try:
        return ApprovalManifest(
            source_head=document["source_head"],
            mappings=tuple(Mapping(**item) for item in document.get("mappings", [])),
            databases=tuple(
                DatabaseTarget(**item) for item in document.get("databases", [])
            ),
            dispositions=tuple(
                Disposition(**item) for item in document.get("dispositions", [])
            ),
        )
    except (KeyError, TypeError) as exc:
        raise ManifestError("approval manifest is malformed") from exc


def verify_manifest(raw: bytes, record: ApprovalRecord) -> None:
    """Unconditional byte comparison: hash what is there, compare, refuse."""
    if hashlib.sha256(raw).hexdigest() != record.manifest_sha256:
        raise ManifestError("approval manifest does not match its approval record")
    manifest = load_manifest(raw)
    if raw != canonical_bytes(manifest):
        raise ManifestError("approval manifest is not in canonical form")
```

- [ ] **Step 4: Run the test and confirm it passes**

Run: `uv run python -m pytest -q tests/test_cutover_manifest.py`
Expected: PASS, 13 tests.

- [ ] **Step 5: Commit**

```bash
git add app/cutover_manifest.py tests/test_cutover_manifest.py
git commit -m "feat: add the digest-bound axis-typed cutover manifest"
```

---

### Task 3: The closed rewrite-location table

**Files:**
- Create: `app/cutover_locations.py`
- Test: `tests/test_cutover_locations.py`

**Interfaces:**
- Produces: `REWRITE_LOCATIONS`, `Location`, `locations_for_axis`, `location_keys`, `LocationError`.

`_assert_partition()` runs at import, so a `(file_kind, field)` pair claimed by two axes is an immediate error rather than a runtime surprise.

- [ ] **Step 1: Write the failing test**

Create `tests/test_cutover_locations.py`:

```python
from collections import Counter

from app.cutover_locations import REWRITE_LOCATIONS, location_keys, locations_for_axis
from app.identifiers import AXES


def test_every_axis_has_at_least_one_location():
    for axis in AXES:
        assert locations_for_axis(axis)


def test_no_file_kind_and_field_pair_appears_under_two_axes():
    pairs = Counter(
        (location.file_kind, location.field) for location in REWRITE_LOCATIONS
    )
    assert [pair for pair, count in pairs.items() if count > 1] == []


def test_product_axis_never_claims_a_workspace_id():
    assert ("workspaces", "id") not in {
        (item.file_kind, item.field) for item in locations_for_axis("product")
    }


def test_workspace_axis_owns_the_workspace_id():
    assert ("workspaces", "id") in {
        (item.file_kind, item.field) for item in locations_for_axis("workspace")
    }


def test_members_id_and_workspaces_id_are_distinct_pairs():
    member_pairs = {
        (item.file_kind, item.field) for item in locations_for_axis("member")
    }
    assert ("members", "id") in member_pairs
    assert ("workspaces", "id") not in member_pairs


def test_action_policy_rewrites_both_halves_of_the_fail_open_rule():
    assert {
        item.field
        for item in locations_for_axis("entity")
        if item.file_kind == "action-policy"
    } == {"paths", "except"}


def test_location_keys_are_stable_identifiers_for_dispositions():
    assert "entity:front-matter:entity" in location_keys()
    assert "workspace:workspaces:id" in location_keys()
    assert "product:workspaces:id" not in location_keys()
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `uv run python -m pytest -q tests/test_cutover_locations.py`
Expected: collection error, `ModuleNotFoundError: No module named 'app.cutover_locations'`.

- [ ] **Step 3: Write the minimal implementation**

Create `app/cutover_locations.py`:

```python
"""cutover_locations.py — the closed list of typed rewrite locations.

A short identifier may also be an ordinary English word, so nothing is
rewritten because it merely looks like the identifier. Only a location on this
table is ever modified, and the table must partition: no `(file_kind, field)`
pair may appear under two axes, or two mappings would contend for one field.
"""
from __future__ import annotations

from dataclasses import dataclass

from .identifiers import AXES


class LocationError(ValueError):
    pass


@dataclass(frozen=True)
class Location:
    axis: str
    file_kind: str
    field: str
    #: "value" matches an exact whole field value; "key" a mapping key;
    #: "path-head" the first component of a path; "dirname" a directory name.
    match: str

    @property
    def key(self) -> str:
        return f"{self.axis}:{self.file_kind}:{self.field}"


REWRITE_LOCATIONS: tuple[Location, ...] = (
    # --- entity -----------------------------------------------------------
    Location("entity", "entities", "key", "key"),
    Location("entity", "vault-root", "dirname", "dirname"),
    Location("entity", "products", "entity-group", "key"),
    Location("entity", "members", "entity-group", "key"),
    Location("entity", "workspaces", "entity", "value"),
    Location("entity", "workspaces", "primary_entity", "value"),
    Location("entity", "front-matter", "entity", "value"),
    Location("entity", "proposal", "entity", "value"),
    Location("entity", "proposal", "src", "path-head"),
    Location("entity", "proposal", "dst", "path-head"),
    Location("entity", "action-policy", "paths", "path-head"),
    Location("entity", "action-policy", "except", "path-head"),
    # --- product ----------------------------------------------------------
    Location("product", "products", "key", "key"),
    Location("product", "front-matter", "product", "value"),
    Location("product", "workspaces", "product", "value"),
    Location("product", "books-db", "approved-target", "value"),
    # --- member -----------------------------------------------------------
    Location("member", "members", "id", "value"),
    Location("member", "front-matter", "member", "value"),
    Location("member", "workspaces", "member", "value"),
    Location("member", "books-db", "approved-target-member", "value"),
    # --- workspace --------------------------------------------------------
    Location("workspace", "workspaces", "id", "value"),
)


def locations_for_axis(axis: str) -> tuple[Location, ...]:
    if axis not in AXES:
        raise LocationError(f"unknown axis {axis!r}")
    return tuple(item for item in REWRITE_LOCATIONS if item.axis == axis)


def location_keys() -> frozenset[str]:
    """Every valid `axis:file_kind:field` key. A structural disposition must
    name one of these; anything else is an unbuildable promise."""
    return frozenset(item.key for item in REWRITE_LOCATIONS)


def _assert_partition() -> None:
    seen: dict[tuple[str, str], str] = {}
    for location in REWRITE_LOCATIONS:
        key = (location.file_kind, location.field)
        if key in seen and seen[key] != location.axis:
            raise LocationError(
                f"{key} is claimed by both {seen[key]!r} and {location.axis!r}"
            )
        seen[key] = location.axis


_assert_partition()
```

The two `books-db` rows carry distinct `field` values on purpose. A shared
`("books-db", "approved-target")` pair would be claimed by both the product and
member axes and would trip `_assert_partition()` at import — correctly, since
that is exactly the untyped-column defect this revision exists to prevent.

- [ ] **Step 4: Run the test and confirm it passes**

Run: `uv run python -m pytest -q tests/test_cutover_locations.py`
Expected: PASS, 7 tests.

- [ ] **Step 5: Commit**

```bash
git add app/cutover_locations.py tests/test_cutover_locations.py
git commit -m "feat: declare the closed cutover rewrite locations"
```

---

### Task 4: Scoped rewriters, including a targeted policy rewriter

**Files:**
- Modify: `app/cutover_locations.py`
- Test: `tests/test_cutover_locations.py`

**Interfaces:**
- Produces: `boundaried`, `rewrite_front_matter_field`, `rewrite_path_head`,
  `rewrite_yaml_path_head_field`, `rewrite_yaml_value_field`,
  `rewrite_mapping_key`, `rewrite_policy_path_heads`.

`rewrite_policy_path_heads` rewrites path heads **only inside `paths:` and `except:` list bodies**, never every quoted string in the file.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cutover_locations.py`:

```python
import textwrap

from app.cutover_locations import (
    rewrite_front_matter_field,
    rewrite_mapping_key,
    rewrite_path_head,
    rewrite_policy_path_heads,
    rewrite_yaml_path_head_field,
    rewrite_yaml_value_field,
)


def test_front_matter_rewrite_matches_only_the_exact_whole_value():
    text = textwrap.dedent(
        """\
        ---
        entity: ab
        title: ab is a common word
        ---

        The ab pattern is discussed here, and abx is not ab.
        """
    )
    result = rewrite_front_matter_field(text, "entity", "ab", "ab-entity")
    assert "entity: ab-entity\n" in result
    assert "title: ab is a common word" in result
    assert "The ab pattern is discussed here, and abx is not ab." in result


def test_front_matter_rewrite_ignores_a_value_that_merely_contains_the_term():
    text = "---\nentity: abx\n---\n"
    assert rewrite_front_matter_field(text, "entity", "ab", "ab-entity") == text


def test_front_matter_rewrite_ignores_body_occurrences_of_the_field_name():
    text = "---\nentity: zz\n---\n\nentity: ab\n"
    assert rewrite_front_matter_field(text, "entity", "ab", "ab-entity") == text


def test_path_head_rewrite_replaces_only_the_first_component():
    assert rewrite_path_head("ab/00-inbox/ab.md", "ab", "ab-entity") == (
        "ab-entity/00-inbox/ab.md"
    )
    assert rewrite_path_head("zz/ab/note.md", "ab", "ab-entity") == "zz/ab/note.md"
    assert rewrite_path_head("abx/note.md", "ab", "ab-entity") == "abx/note.md"
    assert rewrite_path_head("ab", "ab", "ab-entity") == "ab-entity"


def test_yaml_path_head_field_rewrites_only_the_named_plain_scalar():
    text = (
        "src: ab/00-inbox/active/x.md\n"
        "dst: zz/ab/active/x.md\n"
        'note: "src: ab/this-is-prose"\n'
    )

    result = rewrite_yaml_path_head_field(text, "src", "ab", "ab-entity")

    assert "src: ab-entity/00-inbox/active/x.md" in result
    assert "dst: zz/ab/active/x.md" in result
    assert 'note: "src: ab/this-is-prose"' in result


def test_yaml_path_head_field_preserves_every_unrelated_byte():
    text = 'src: ab/x.md\nopaque: "keep: [x]"  # exact\n'

    result = rewrite_yaml_path_head_field(text, "src", "ab", "ab-entity")

    assert result == 'src: ab-entity/x.md\nopaque: "keep: [x]"  # exact\n'


def test_yaml_value_field_rewrite_matches_the_exact_value():
    text = "workspaces:\n  - {id: ab, product: ab, kind: product}\n"
    result = rewrite_yaml_value_field(text, "product", "ab", "ab-product")
    assert "product: ab-product" in result
    assert "id: ab," in result


def test_yaml_value_field_rewrite_does_not_match_a_scalar_prefix():
    text = "members:\n  ab:\n    - {id: ab note, label: Keep}\n"

    assert rewrite_yaml_value_field(text, "id", "ab", "ab-member") == text


def test_yaml_value_field_rewrite_does_not_match_text_inside_another_scalar():
    text = 'note: "the text id: ab is explanatory"\n'

    assert rewrite_yaml_value_field(text, "id", "ab", "ab-member") == text


def test_yaml_value_field_rewrite_accepts_block_and_flow_entries():
    text = "members:\n  ab:\n    - id: ab\n    - {id: ab, label: A}\n"

    result = rewrite_yaml_value_field(text, "id", "ab", "ab-member")

    assert result.count("id: ab-member") == 2


def test_mapping_key_rewrite_matches_at_the_given_indent():
    text = "products:\n  zz:\n    ab:\n      label: A\n"
    result = rewrite_mapping_key(text, "ab", "ab-product", indent=4)
    assert "    ab-product:" in result
    assert "  zz:" in result


POLICY = textwrap.dedent(
    """\
    version: 1.0
    default: deny
    description: "ab is mentioned here and must not change"
    actors:
      hermes:
        allow:
          - {action: read, paths: ["ab/**"], except: ["ab/.sensitive/**"]}
          - {action: write, paths: ["ab/00-inbox/**"]}
        deny:
          - {paths: [".sensitive/**"]}
    """
)


def test_policy_rewrite_touches_paths_and_except_only():
    result = rewrite_policy_path_heads(POLICY, "ab", "ab-entity")

    assert '"ab-entity/**"' in result
    assert '"ab-entity/.sensitive/**"' in result
    assert '"ab-entity/00-inbox/**"' in result
    assert '"ab/**"' not in result
    assert '"ab/.sensitive/**"' not in result


def test_policy_rewrite_leaves_other_quoted_strings_alone():
    result = rewrite_policy_path_heads(POLICY, "ab", "ab-entity")

    assert 'description: "ab is mentioned here and must not change"' in result


def test_policy_rewrite_does_not_treat_paths_text_inside_a_description_as_a_key():
    text = 'description: \'example paths: ["ab/**"] is prose\'\n' + POLICY

    result = rewrite_policy_path_heads(text, "ab", "ab-entity")

    assert 'example paths: ["ab/**"] is prose' in result


def test_policy_rewrite_leaves_a_non_matching_path_head_alone():
    result = rewrite_policy_path_heads(POLICY, "ab", "ab-entity")

    assert '".sensitive/**"' in result
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `uv run python -m pytest -q tests/test_cutover_locations.py`
Expected: collection error, `ImportError: cannot import name 'rewrite_front_matter_field'`.

- [ ] **Step 3: Write the minimal implementation**

Append to `app/cutover_locations.py`:

```python
import re

#: A whole token: not preceded or followed by a word character or a hyphen. A
#: migrated `ab-entity` therefore does not match a scan for `ab`, because the
#: lookahead fails on the hyphen, while a bare `ab` still does.
def boundaried(term: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![\w-]){re.escape(term)}(?![\w-])")


def _split_front_matter(text: str) -> tuple[str, str, str] | None:
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    return text[:3], text[3:end], text[end:]


def rewrite_front_matter_field(text: str, field: str, old: str, new: str) -> str:
    """Replace `field: old` inside the leading front matter only, and only
    when `old` is the entire value."""
    parts = _split_front_matter(text)
    if parts is None:
        return text
    head, block, tail = parts
    rewritten = re.sub(
        rf"(?m)^(\s*{re.escape(field)}:\s*){re.escape(old)}[ \t]*$",
        rf"\g<1>{new}",
        block,
    )
    return head + rewritten + tail


def rewrite_path_head(path: str, old: str, new: str) -> str:
    """Replace the first path component when it is exactly `old`."""
    head, separator, rest = path.partition("/")
    if head != old:
        return path
    return f"{new}{separator}{rest}"


def rewrite_yaml_value_field(text: str, field: str, old: str, new: str) -> str:
    """Replace an exact plain YAML scalar in a block or flow mapping.

    The left boundary is a YAML mapping boundary (line start, `{`, or `,`),
    not an arbitrary occurrence of ``field:`` inside another scalar. The right
    boundary is a complete plain-scalar delimiter. In particular, ``id: ab
    note`` and ``note: "id: ab is prose"`` are not matches.

    Registry identifiers are constrained to lowercase letters, digits and
    hyphens, so the canonical registries write them as plain scalars. A quoted
    or otherwise unsupported representation is left untouched here and then
    refused by Task 11's scoped residual gate rather than guessed at.
    """
    pattern = re.compile(
        rf"(?m)(^|[{{,])"
        rf"([ \t]*(?:-[ \t]*)?{re.escape(field)}:[ \t]*)"
        rf"{re.escape(old)}"
        rf"(?=[ \t]*(?:[,}}#]|$))"
    )
    return pattern.sub(rf"\g<1>\g<2>{new}", text)


def rewrite_yaml_path_head_field(
    text: str, field: str, old: str, new: str
) -> str:
    """Rewrite only the first component of one plain YAML path scalar.

    This is deliberately textual and boundary-scoped. Parsing and dumping the
    complete proposal would rewrite quoting, key order, and other fields that
    the approved location table does not own.
    """
    pattern = re.compile(
        rf"(?m)(^|[{{,])"
        rf"([ \t]*(?:-[ \t]*)?{re.escape(field)}:[ \t]*)"
        rf"{re.escape(old)}/"
    )
    return pattern.sub(rf"\g<1>\g<2>{new}/", text)


def rewrite_mapping_key(text: str, old: str, new: str, indent: int) -> str:
    """Rename a mapping key sitting at exactly `indent` spaces."""
    return re.sub(
        rf"(?m)^(\s{{{indent}}}){re.escape(old)}:",
        rf"\g<1>{new}:",
        text,
    )


_POLICY_LIST = re.compile(
    r"(?m)(^|[,{])([ \t-]*)(paths|except):\s*\[([^\]]*)\]"
)
_QUOTED = re.compile(r"([\"'])([^\"']*)\1")


def rewrite_policy_path_heads(text: str, old: str, new: str) -> str:
    """Rewrite path heads inside `paths:` and `except:` list bodies only.

    Rewriting every quoted string in the file would edit descriptions and
    unrelated values — the blind substitution this design exists to avoid. An
    allow rule's `paths:` and its `except:` for `.sensitive/` are both matched
    here, so they move together; rewriting one without the other is the
    BUILD §4 fail-open.
    """
    def rewrite_body(match: re.Match[str]) -> str:
        boundary, spacing, key, body = match.groups()
        rewritten = _QUOTED.sub(
            lambda item: (
                f"{item.group(1)}"
                f"{rewrite_path_head(item.group(2), old, new)}"
                f"{item.group(1)}"
            ),
            body,
        )
        return f"{boundary}{spacing}{key}: [{rewritten}]"

    return _POLICY_LIST.sub(rewrite_body, text)
```

- [ ] **Step 4: Run the test and confirm it passes**

Run: `uv run python -m pytest -q tests/test_cutover_locations.py`
Expected: PASS, 22 tests.

- [ ] **Step 5: Commit**

```bash
git add app/cutover_locations.py tests/test_cutover_locations.py
git commit -m "feat: add scoped cutover rewriters"
```

---

### Task 5: The strict advisory scan

**Files:**
- Modify: `app/cutover_locations.py`
- Test: `tests/test_cutover_locations.py`

**Interfaces:**
- Produces: `AdvisoryOccurrence`, `advisory_occurrences`, `UnreadableFile`.

Two fail-open holes from the rejected plan are closed here. The `former_slugs` exemption applies **only** to the entity and product registry files, not to any line containing the substring. An unreadable text file is a **hard failure**, not a skip.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cutover_locations.py`:

```python
import hashlib
from pathlib import Path

import pytest

from app.cutover_locations import (
    AdvisoryOccurrence,
    UnreadableFile,
    advisory_occurrences,
)
from app.cutover_manifest import Mapping


ADVISORY_MAPPINGS = (
    Mapping(axis="entity", old="ab", new="ab-entity"),
    Mapping(axis="product", old="q7", new="q7-product"),
    Mapping(axis="member", old="m7", new="m7-member"),
    Mapping(axis="workspace", old="w7", new="w7-workspace"),
)


def occurrence(
    path: str,
    line: int,
    axis: str,
    old: str,
    text: str,
    ordinal: int = 1,
) -> AdvisoryOccurrence:
    return AdvisoryOccurrence(
        path=path,
        axis=axis,
        old=old,
        ordinal=ordinal,
        context_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        line=line,
    )


def test_advisory_reports_a_bare_token_outside_the_enumerated_locations(tmp_path: Path):
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "one.md").write_text("the ab pattern\n", encoding="utf-8")

    assert advisory_occurrences(tmp_path, ADVISORY_MAPPINGS[:1]) == [
        occurrence("notes/one.md", 1, "entity", "ab", "the ab pattern")
    ]


def test_advisory_identity_distinguishes_two_tokens_on_one_line(tmp_path: Path):
    (tmp_path / "note.md").write_text("ab and ab\n", encoding="utf-8")

    assert advisory_occurrences(tmp_path, ADVISORY_MAPPINGS[:1]) == [
        occurrence("note.md", 1, "entity", "ab", "ab and ab", ordinal=1),
        occurrence("note.md", 1, "entity", "ab", "ab and ab", ordinal=2),
    ]


def test_advisory_does_not_report_a_migrated_token(tmp_path: Path):
    (tmp_path / "note.md").write_text("entity: ab-entity\n", encoding="utf-8")

    assert advisory_occurrences(tmp_path, ADVISORY_MAPPINGS[:1]) == []


def test_advisory_does_not_report_a_longer_token(tmp_path: Path):
    (tmp_path / "note.md").write_text("xabx and cab and abx\n", encoding="utf-8")

    assert advisory_occurrences(tmp_path, ADVISORY_MAPPINGS[:1]) == []


def test_advisory_skips_git_and_binaries(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "note.md").write_text("ab\n", encoding="utf-8")
    (tmp_path / "books.db").write_bytes(b"\x00ab\x00")

    assert advisory_occurrences(tmp_path, ADVISORY_MAPPINGS[:1]) == []


def test_former_slugs_is_exempt_only_in_the_entity_and_product_registries(
    tmp_path: Path,
):
    system = tmp_path / "_system"
    system.mkdir()
    (system / "entities.yaml").write_text(
        "entities:\n  ab-entity:\n    former_slugs: [ab]\n", encoding="utf-8"
    )
    (system / "products.yaml").write_text(
        "products:\n  ab-entity:\n    q7-product:\n      former_slugs: [q7]\n",
        encoding="utf-8",
    )
    (tmp_path / "note.md").write_text("former_slugs: [ab]\n", encoding="utf-8")

    found = advisory_occurrences(tmp_path, ADVISORY_MAPPINGS[:2])

    assert found == [
        occurrence("note.md", 1, "entity", "ab", "former_slugs: [ab]")
    ]


def test_former_slugs_is_not_exempt_in_the_member_registry(tmp_path: Path):
    system = tmp_path / "_system"
    system.mkdir()
    (system / "members.yaml").write_text(
        "members:\n  ab-entity:\n    - {id: m7-member, former_slugs: [m7]}\n",
        encoding="utf-8",
    )

    assert advisory_occurrences(tmp_path, ADVISORY_MAPPINGS[2:3]) == [
        occurrence(
            "_system/members.yaml",
            3,
            "member",
            "m7",
            "    - {id: m7-member, former_slugs: [m7]}",
        )
    ]


def test_an_unreadable_text_file_is_a_hard_failure(tmp_path: Path):
    (tmp_path / "note.md").write_bytes(b"\xff\xfe not utf-8 \xff")

    with pytest.raises(UnreadableFile):
        advisory_occurrences(tmp_path, ADVISORY_MAPPINGS[:1])


def test_typed_registry_front_matter_workspace_policy_and_proposal_lines_are_not_advisory(
    tmp_path: Path,
):
    system = tmp_path / "_system"
    (system / "scripts").mkdir(parents=True)
    (system / "entities.yaml").write_text(
        "entities:\n  ab:\n    label: A\n", encoding="utf-8"
    )
    (system / "products.yaml").write_text(
        "products:\n  ab:\n    q7:\n      label: Q\n", encoding="utf-8"
    )
    (system / "members.yaml").write_text(
        "members:\n  ab:\n    - {id: m7}\n", encoding="utf-8"
    )
    (system / "workspaces.yaml").write_text(
        "workspaces:\n  - {id: w7, entity: ab, product: q7, member: m7}\n",
        encoding="utf-8",
    )
    (system / "scripts" / "action-policy.yaml").write_text(
        'allow:\n  - {paths: ["ab/**"], except: ["ab/.sensitive/**"]}\n',
        encoding="utf-8",
    )
    inbox = tmp_path / "ab" / "00-inbox"
    inbox.mkdir(parents=True)
    (inbox / "note.md").write_text(
        "---\nentity: ab\nproduct: q7\nmember: m7\n---\n\nordinary ab prose\n",
        encoding="utf-8",
    )
    outbox = tmp_path / "ab" / "outbox"
    outbox.mkdir()
    (outbox / "p.yaml").write_text(
        "entity: ab\nsrc: ab/00-inbox/a.md\ndst: ab/09-marketing/a.md\n",
        encoding="utf-8",
    )

    assert advisory_occurrences(tmp_path, ADVISORY_MAPPINGS) == [
        occurrence(
            "ab/00-inbox/note.md", 7, "entity", "ab", "ordinary ab prose"
        )
    ]


def test_a_symlink_is_scanned_as_link_text_without_following_its_target(tmp_path: Path):
    link = tmp_path / "ab-link"
    link.symlink_to("ab/target")

    assert advisory_occurrences(tmp_path, ADVISORY_MAPPINGS[:1]) == [
        occurrence("ab-link", 1, "entity", "ab", "ab/target")
    ]
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `uv run python -m pytest -q tests/test_cutover_locations.py`
Expected: collection error, `ImportError: cannot import name 'AdvisoryOccurrence'`.

- [ ] **Step 3: Write the minimal implementation**

Append to `app/cutover_locations.py`:

```python
from pathlib import Path
import hashlib
import os

from .cutover_manifest import Mapping

SKIP_DIRS = frozenset({".git", ".obsidian", ".trash"})
BINARY_SUFFIXES = frozenset({
    ".pdf", ".docx", ".xlsx", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico",
    ".db", ".sqlite", ".sqlite3", ".zip", ".gz", ".tar", ".woff", ".woff2",
})

#: The only two files where a `former_slugs:` line is legitimate. The rejected
#: plan exempted every line containing the substring, in any file — a blanket
#: exemption that would mask a genuine residual anywhere in the vault.
FORMER_SLUGS_FILES = frozenset({
    "_system/entities.yaml",
    "_system/products.yaml",
})


class UnreadableFile(Exception):
    """A text file the advisory scan could not read.

    Never skipped: an unreadable file could hold a residual, and skipping it
    would let the gate pass on evidence it never saw.
    """


@dataclass(frozen=True, order=True)
class AdvisoryOccurrence:
    path: str
    axis: str
    old: str
    ordinal: int
    context_sha256: str
    line: int


def _yaml_scalar_occurs(line: str, field: str, old: str) -> bool:
    pattern = re.compile(
        rf"(^|[{{,])"
        rf"([ \t]*(?:-[ \t]*)?{re.escape(field)}:[ \t]*)"
        rf"{re.escape(old)}"
        rf"(?=[ \t]*(?:[,}}#]|$))"
    )
    return pattern.search(line) is not None


def _mapping_key_occurs(line: str, old: str, indent: int) -> bool:
    return re.match(rf"^\s{{{indent}}}{re.escape(old)}:\s*(?:#.*)?$", line) is not None


def _typed_line_keys(
    root: Path, mappings: tuple[Mapping, ...]
) -> set[tuple[str, int, str, str]]:
    """Line-level occurrences owned by the closed rewrite-location table.

    Advisory reporting is the complement of this set. Keeping the exclusion
    structural and shared with the writer prevents registry keys and front
    matter from being presented to the owner as unexplained prose.
    """
    by_axis = {
        axis: {item.old for item in mappings if item.axis == axis}
        for axis in AXES
    }
    typed: set[tuple[str, int, str, str]] = set()
    for candidate in sorted(root.rglob("*")):
        relative = candidate.relative_to(root).as_posix()
        if any(part in SKIP_DIRS for part in Path(relative).parts):
            continue
        if candidate.is_symlink() or not candidate.is_file():
            continue
        if candidate.suffix.lower() in BINARY_SUFFIXES:
            continue
        try:
            lines = candidate.read_text(encoding="utf-8").splitlines()
        except (UnicodeDecodeError, OSError) as exc:
            raise UnreadableFile(f"{relative} could not be read") from exc

        in_front_matter = False
        for number, line in enumerate(lines, start=1):
            if candidate.suffix.lower() == ".md":
                if number == 1 and line.strip() == "---":
                    in_front_matter = True
                    continue
                if in_front_matter and line.strip() == "---":
                    in_front_matter = False
                    continue
                if in_front_matter:
                    for axis, field in (
                        ("entity", "entity"),
                        ("product", "product"),
                        ("member", "member"),
                    ):
                        for old in by_axis[axis]:
                            if _yaml_scalar_occurs(line, field, old):
                                typed.add((relative, number, axis, old))

            if relative == "_system/entities.yaml":
                for old in by_axis["entity"]:
                    if _mapping_key_occurs(line, old, 2):
                        typed.add((relative, number, "entity", old))
            elif relative == "_system/products.yaml":
                for axis, indent in (("entity", 2), ("product", 4)):
                    for old in by_axis[axis]:
                        if _mapping_key_occurs(line, old, indent):
                            typed.add((relative, number, axis, old))
            elif relative == "_system/members.yaml":
                for old in by_axis["entity"]:
                    if _mapping_key_occurs(line, old, 2):
                        typed.add((relative, number, "entity", old))
                for old in by_axis["member"]:
                    if _yaml_scalar_occurs(line, "id", old):
                        typed.add((relative, number, "member", old))
            elif relative == "_system/workspaces.yaml":
                for axis, fields in (
                    ("workspace", ("id",)),
                    ("entity", ("entity", "primary_entity")),
                    ("product", ("product",)),
                    ("member", ("member",)),
                ):
                    for field in fields:
                        for old in by_axis[axis]:
                            if _yaml_scalar_occurs(line, field, old):
                                typed.add((relative, number, axis, old))
            elif relative == "_system/scripts/action-policy.yaml":
                for match in _POLICY_LIST.finditer(line):
                    for quoted in _QUOTED.finditer(match.group(4)):
                        head = quoted.group(2).partition("/")[0]
                        if head in by_axis["entity"]:
                            typed.add((relative, number, "entity", head))
            elif candidate.suffix.lower() == ".yaml" and "outbox" in Path(relative).parts:
                for old in by_axis["entity"]:
                    if _yaml_scalar_occurs(line, "entity", old):
                        typed.add((relative, number, "entity", old))
                    for field in ("src", "dst"):
                        pattern = re.compile(
                            rf"(^|[{{,])([ \t]*{field}:[ \t]*)"
                            rf"{re.escape(old)}/"
                        )
                        if pattern.search(line):
                            typed.add((relative, number, "entity", old))
    return typed


def advisory_occurrences(
    root: Path, mappings: tuple[Mapping, ...]
) -> list[AdvisoryOccurrence]:
    """Whole-token occurrences of an old identifier, for owner disposition.

    Reported, never rewritten. A short identifier may be an ordinary word, so
    the owner decides which occurrences are structural references and which are
    incidental prose.
    """
    patterns = {
        (item.axis, item.old): boundaried(item.old) for item in mappings
    }
    typed = _typed_line_keys(root, mappings)
    found: list[AdvisoryOccurrence] = []
    for candidate in sorted(root.rglob("*")):
        relative = candidate.relative_to(root).as_posix()
        if any(part in SKIP_DIRS for part in Path(relative).parts):
            continue
        if candidate.is_symlink():
            try:
                text = os.readlink(candidate)
            except OSError as exc:
                raise UnreadableFile(f"{relative} link text could not be read") from exc
        elif candidate.is_file():
            if candidate.suffix.lower() in BINARY_SUFFIXES:
                continue
            try:
                text = candidate.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError) as exc:
                raise UnreadableFile(
                    f"{relative} could not be read; the advisory scan cannot pass "
                    f"on a file it never saw"
                ) from exc
        else:
            continue
        exempt_former_slugs = relative in FORMER_SLUGS_FILES
        ordinals: dict[tuple[str, str], int] = {}
        for number, line in enumerate(text.splitlines(), start=1):
            if exempt_former_slugs and re.match(
                r"^\s*former_slugs:\s*\[", line
            ):
                continue
            context_sha256 = hashlib.sha256(line.encode("utf-8")).hexdigest()
            for (axis, old), pattern in patterns.items():
                if (relative, number, axis, old) in typed:
                    continue
                for _match in pattern.finditer(line):
                    key = (axis, old)
                    ordinals[key] = ordinals.get(key, 0) + 1
                    found.append(
                        AdvisoryOccurrence(
                            path=relative,
                            axis=axis,
                            old=old,
                            ordinal=ordinals[key],
                            context_sha256=context_sha256,
                            line=number,
                        )
                    )
    return sorted(found)
```

- [ ] **Step 4: Run the test and confirm it passes**

Run: `uv run python -m pytest -q tests/test_cutover_locations.py`
Expected: PASS, 32 tests.

- [ ] **Step 5: Commit**

```bash
git add app/cutover_locations.py tests/test_cutover_locations.py
git commit -m "feat: add a strict advisory occurrence scan"
```

---

### Task 6: The scoped residual gate

**Files:**
- Modify: `app/cutover_locations.py`
- Test: `tests/test_cutover_locations.py`

**Interfaces:**
- Produces: `ScopedResidual`, `scoped_residuals(root, mappings)`.

This is the gate the rejected plan never had. It asserts that no **enumerated location** still holds an old identifier. It is scoped to the same locations as the writer, because a whole-vault text gate would refuse forever on any short identifier that is also an ordinary word — and a gate that cannot pass is not a safety mechanism.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cutover_locations.py`:

```python
from app.cutover_locations import ScopedResidual, scoped_residuals
from app.cutover_manifest import Mapping


def migrated_tree(root: Path) -> None:
    system = root / "_system"
    (system / "scripts").mkdir(parents=True)
    (system / "entities.yaml").write_text(
        "entities:\n  ab-entity:\n    label: A\n", encoding="utf-8"
    )
    (system / "products.yaml").write_text(
        "products:\n  ab-entity:\n    q7-product:\n      label: Q\n", encoding="utf-8"
    )
    (system / "members.yaml").write_text(
        "members:\n  ab-entity:\n    - {id: m7-member}\n", encoding="utf-8"
    )
    (system / "workspaces.yaml").write_text(
        "workspaces:\n  - {id: w7-workspace, entity: ab-entity, product: q7-product}\n",
        encoding="utf-8",
    )
    (system / "scripts" / "action-policy.yaml").write_text(
        'actors:\n  h:\n    allow:\n      - {paths: ["ab-entity/**"], '
        'except: ["ab-entity/.sensitive/**"]}\n',
        encoding="utf-8",
    )
    inbox = root / "ab-entity" / "00-inbox"
    inbox.mkdir(parents=True)
    (inbox / "n.md").write_text(
        "---\nentity: ab-entity\nproduct: q7-product\nmember: m7-member\n---\n\n"
        "the ab word is ordinary prose\n",
        encoding="utf-8",
    )


MAPPINGS = (
    Mapping(axis="entity", old="ab", new="ab-entity"),
    Mapping(axis="product", old="q7", new="q7-product"),
    Mapping(axis="member", old="m7", new="m7-member"),
    Mapping(axis="workspace", old="w7", new="w7-workspace"),
)


def test_a_fully_migrated_tree_has_no_scoped_residual(tmp_path: Path):
    migrated_tree(tmp_path)

    assert scoped_residuals(tmp_path, MAPPINGS) == []


def test_ordinary_prose_containing_an_old_identifier_is_not_a_residual(tmp_path: Path):
    migrated_tree(tmp_path)
    (tmp_path / "ab-entity" / "00-inbox" / "prose.md").write_text(
        "ab ab ab everywhere in the body\n", encoding="utf-8"
    )

    assert scoped_residuals(tmp_path, MAPPINGS) == []


def test_a_missed_front_matter_field_is_a_residual(tmp_path: Path):
    migrated_tree(tmp_path)
    (tmp_path / "ab-entity" / "00-inbox" / "n.md").write_text(
        "---\nentity: ab\n---\n", encoding="utf-8"
    )

    assert ScopedResidual(
        location="entity:front-matter:entity",
        path="ab-entity/00-inbox/n.md",
        old="ab",
    ) in scoped_residuals(tmp_path, MAPPINGS)


def test_a_missed_registry_key_is_a_residual(tmp_path: Path):
    migrated_tree(tmp_path)
    (tmp_path / "_system" / "entities.yaml").write_text(
        "entities:\n  ab:\n    label: A\n", encoding="utf-8"
    )

    assert any(
        item.location == "entity:entities:key" for item in scoped_residuals(tmp_path, MAPPINGS)
    )


def test_a_missed_policy_except_half_is_a_residual(tmp_path: Path):
    migrated_tree(tmp_path)
    (tmp_path / "_system" / "scripts" / "action-policy.yaml").write_text(
        'actors:\n  h:\n    allow:\n      - {paths: ["ab-entity/**"], '
        'except: ["ab/.sensitive/**"]}\n',
        encoding="utf-8",
    )

    assert any(
        item.location == "entity:action-policy:except"
        for item in scoped_residuals(tmp_path, MAPPINGS)
    )


def test_a_surviving_entity_directory_is_a_residual(tmp_path: Path):
    migrated_tree(tmp_path)
    (tmp_path / "ab").mkdir()

    assert any(
        item.location == "entity:vault-root:dirname"
        for item in scoped_residuals(tmp_path, MAPPINGS)
    )


def test_a_missed_workspace_id_is_a_residual(tmp_path: Path):
    migrated_tree(tmp_path)
    (tmp_path / "_system" / "workspaces.yaml").write_text(
        "workspaces:\n  - {id: w7, entity: ab-entity}\n", encoding="utf-8"
    )

    assert any(
        item.location == "workspace:workspaces:id"
        for item in scoped_residuals(tmp_path, MAPPINGS)
    )
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `uv run python -m pytest -q tests/test_cutover_locations.py`
Expected: collection error, `ImportError: cannot import name 'ScopedResidual'`.

- [ ] **Step 3: Write the minimal implementation**

Append to `app/cutover_locations.py`:

```python
import yaml


@dataclass(frozen=True, order=True)
class ScopedResidual:
    location: str
    path: str
    old: str


def _front_matter_values(text: str) -> dict[str, str]:
    parts = _split_front_matter(text)
    if parts is None:
        return {}
    try:
        loaded = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        return {}
    if not isinstance(loaded, dict):
        return {}
    return {k: v for k, v in loaded.items() if isinstance(v, str)}


def _load_yaml_file(path: Path) -> object:
    if not path.is_file():
        return None
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, OSError, yaml.YAMLError) as exc:
        raise UnreadableFile(f"{path.name} could not be read for the residual gate") from exc


def scoped_residuals(
    root: Path, mappings: tuple[Mapping, ...]
) -> list[ScopedResidual]:
    """Any enumerated location still holding an old identifier.

    Scoped to the writer's own locations. Prose is never inspected, because a
    retired identifier may legitimately survive there as an ordinary word.
    """
    by_axis: dict[str, set[str]] = {}
    for mapping in mappings:
        by_axis.setdefault(mapping.axis, set()).add(mapping.old)
    entities = by_axis.get("entity", set())
    products = by_axis.get("product", set())
    members = by_axis.get("member", set())
    workspaces = by_axis.get("workspace", set())
    found: list[ScopedResidual] = []

    def report(location: str, path: str, old: str) -> None:
        found.append(ScopedResidual(location, path, old))

    system = root / "_system"

    # entity: bundle directory names
    for old in entities:
        if (root / old).is_dir():
            report("entity:vault-root:dirname", old, old)

    # entity / product: registry mapping keys
    entities_doc = _load_yaml_file(system / "entities.yaml")
    if isinstance(entities_doc, dict):
        for key in (entities_doc.get("entities") or {}):
            if key in entities:
                report("entity:entities:key", "_system/entities.yaml", key)
    products_doc = _load_yaml_file(system / "products.yaml")
    if isinstance(products_doc, dict):
        for group, values in (products_doc.get("products") or {}).items():
            if group in entities:
                report("entity:products:entity-group", "_system/products.yaml", group)
            if isinstance(values, dict):
                for key in values:
                    if key in products:
                        report("product:products:key", "_system/products.yaml", key)
    members_doc = _load_yaml_file(system / "members.yaml")
    if isinstance(members_doc, dict):
        for group, values in (members_doc.get("members") or {}).items():
            if group in entities:
                report("entity:members:entity-group", "_system/members.yaml", group)
            if isinstance(values, list):
                for entry in values:
                    if isinstance(entry, dict) and entry.get("id") in members:
                        report("member:members:id", "_system/members.yaml", entry["id"])

    # workspaces: four typed fields, each owned by exactly one axis
    workspaces_doc = _load_yaml_file(system / "workspaces.yaml")
    if isinstance(workspaces_doc, dict):
        for entry in workspaces_doc.get("workspaces") or []:
            if not isinstance(entry, dict):
                continue
            checks = (
                ("workspace:workspaces:id", "id", workspaces),
                ("entity:workspaces:entity", "entity", entities),
                ("entity:workspaces:primary_entity", "primary_entity", entities),
                ("product:workspaces:product", "product", products),
                ("member:workspaces:member", "member", members),
            )
            for location, field, olds in checks:
                if entry.get(field) in olds:
                    report(location, "_system/workspaces.yaml", entry[field])

    # action-policy: both halves of every rule
    policy = system / "scripts" / "action-policy.yaml"
    if policy.is_file():
        try:
            text = policy.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError) as exc:
            raise UnreadableFile("action-policy.yaml could not be read") from exc
        for match in _POLICY_LIST.finditer(text):
            key, body = match.group(3), match.group(4)
            for quoted in _QUOTED.finditer(body):
                head = quoted.group(2).partition("/")[0]
                if head in entities:
                    report(
                        f"entity:action-policy:{key}",
                        "_system/scripts/action-policy.yaml",
                        head,
                    )

    # front matter and proposals
    for candidate in sorted(root.rglob("*")):
        if not candidate.is_file() or candidate.is_symlink():
            continue
        relative = candidate.relative_to(root).as_posix()
        if any(part in SKIP_DIRS for part in Path(relative).parts):
            continue
        if candidate.suffix.lower() == ".md":
            try:
                text = candidate.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError) as exc:
                raise UnreadableFile(f"{relative} could not be read") from exc
            values = _front_matter_values(text)
            for location, field, olds in (
                ("entity:front-matter:entity", "entity", entities),
                ("product:front-matter:product", "product", products),
                ("member:front-matter:member", "member", members),
            ):
                if values.get(field) in olds:
                    report(location, relative, values[field])
        elif candidate.suffix.lower() == ".yaml" and "outbox" in Path(relative).parts:
            document = _load_yaml_file(candidate)
            if not isinstance(document, dict):
                continue
            if document.get("entity") in entities:
                report("entity:proposal:entity", relative, document["entity"])
            for field in ("src", "dst"):
                value = document.get(field)
                if isinstance(value, str) and value.partition("/")[0] in entities:
                    report(
                        f"entity:proposal:{field}", relative, value.partition("/")[0]
                    )

    return sorted(found)
```

`Mapping` was imported in Task 5 because the typed-occurrence and advisory
interfaces already consume it; do not add a duplicate import here.

- [ ] **Step 4: Run the test and confirm it passes**

Run: `uv run python -m pytest -q tests/test_cutover_locations.py`
Expected: PASS, 38 tests.

- [ ] **Step 5: Commit**

```bash
git add app/cutover_locations.py tests/test_cutover_locations.py
git commit -m "feat: add the scoped residual gate"
```

---

### Task 7: Path-confined, axis-filtered database updates

**Files:**
- Create: `app/cutover_db.py`
- Test: `tests/test_cutover_db.py`

**Interfaces:**
- Produces: `DatabaseReference`, `DatabaseChange`,
  `apply_database_mappings`, `database_residuals`,
  `database_schema_inventory`, `database_reference_inventory`,
  `resolve_database_path`, `DatabaseCutoverError`.

Two rules from design revision 7 are enforced here: a target applies **only** to mappings on its declared axis, and its path must be relative, confined beneath the root, and free of symlinked components.

- [ ] **Step 1: Write the failing test**

Create `tests/test_cutover_db.py`:

```python
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
from app.cutover_manifest import DatabaseTarget, Mapping


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
    with pytest.raises(DatabaseCutoverError):
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
    with pytest.raises(DatabaseCutoverError):
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
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `uv run python -m pytest -q tests/test_cutover_db.py`
Expected: collection error, `ModuleNotFoundError: No module named 'app.cutover_db'`.

- [ ] **Step 3: Write the minimal implementation**

Create `app/cutover_db.py`:

```python
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
import sqlite3

from .cutover_manifest import DatabaseTarget, Mapping


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


def _connect(path: Path, *, read_only: bool) -> sqlite3.Connection:
    try:
        if read_only:
            return sqlite3.connect(f"file:{path}?mode=ro", uri=True)
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


def database_schema_inventory(root: Path) -> dict[str, dict[str, list[str]]]:
    """Every database under `root`, with its tables and columns. Read-only and
    deliberately broad: over-reporting only informs the owner's proof."""
    inventory: dict[str, dict[str, list[str]]] = {}
    for path in sorted(root.rglob("books.db")):
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
    root: Path, mappings: tuple[Mapping, ...]
) -> list[DatabaseReference]:
    """Exact-value reference counts for owner classification.

    This deliberately enumerates every table and column and reports evidence;
    it does not turn a matching column name or value into writer authority.
    Only the owner's digest-bound `DatabaseTarget` list grants that authority.
    """
    schemas = database_schema_inventory(root)
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
```

- [ ] **Step 4: Run the test and confirm it passes**

Run: `uv run python -m pytest -q tests/test_cutover_db.py`
Expected: PASS, 15 tests.

- [ ] **Step 5: Commit**

```bash
git add app/cutover_db.py tests/test_cutover_db.py
git commit -m "feat: add path-confined axis-filtered database updates"
```

---

### Task 8: Collision checks and unmigratable content

**Files:**
- Create: `app/cutover_inventory.py`
- Test: `tests/test_cutover_inventory.py`

**Interfaces:**
- Produces: `check_collisions`, `CollisionError`, `untracked_or_ignored_paths`,
  `require_clean_entities`, `require_clean_status`,
  `UnmigratableContentError`.

Class 3 — one literal on two axes — is **not** a refusal: scoped replacement removes the contamination hazard that once justified refusing it.

- [ ] **Step 1: Write the failing test**

Create `tests/test_cutover_inventory.py`:

```python
from pathlib import Path

import pytest

from app.cutover_inventory import (
    CollisionError,
    UnmigratableContentError,
    check_collisions,
    require_clean_status,
    require_clean_entities,
    untracked_or_ignored_paths,
)
from app.cutover_manifest import Mapping
from tests.conftest import git_vault


def test_a_new_value_colliding_with_an_existing_identifier_is_refused():
    with pytest.raises(CollisionError, match="existing"):
        check_collisions(
            (Mapping(axis="entity", old="ab", new="ab-entity"),),
            {"entity": {"ab", "ab-entity"}},
        )


def test_duplicate_inputs_on_one_axis_are_refused():
    with pytest.raises(CollisionError, match="duplicate"):
        check_collisions(
            (
                Mapping(axis="entity", old="ab", new="ab-entity"),
                Mapping(axis="entity", old="ab", new="ab-entity"),
            ),
            {"entity": {"ab"}},
        )


def test_one_literal_on_two_axes_is_permitted():
    check_collisions(
        (
            Mapping(axis="entity", old="ab", new="ab-entity"),
            Mapping(axis="product", old="ab", new="ab-product"),
        ),
        {"entity": {"ab"}, "product": {"ab"}},
    )


def test_a_clean_mapping_passes():
    check_collisions(
        (Mapping(axis="entity", old="ab", new="ab-entity"),),
        {"entity": {"ab", "zzzzz"}},
    )


def test_an_ignored_path_under_an_affected_entity_is_reported(tmp_path: Path):
    vault = git_vault(
        tmp_path, {".gitignore": ".sensitive/\n", "ab/00-inbox/note.md": "x\n"}
    )
    (vault / "ab" / ".sensitive").mkdir()
    (vault / "ab" / ".sensitive" / "secret.md").write_text("s\n", encoding="utf-8")

    assert any(".sensitive" in item for item in untracked_or_ignored_paths(vault, "ab"))


def test_an_untracked_path_under_an_affected_entity_is_reported(tmp_path: Path):
    vault = git_vault(tmp_path, {"ab/00-inbox/note.md": "x\n"})
    (vault / "ab" / "stray.md").write_text("s\n", encoding="utf-8")

    assert untracked_or_ignored_paths(vault, "ab") == ["ab/stray.md"]


def test_a_clean_entity_reports_nothing(tmp_path: Path):
    vault = git_vault(tmp_path, {"ab/00-inbox/note.md": "x\n"})

    assert untracked_or_ignored_paths(vault, "ab") == []


def test_require_clean_entities_raises_for_an_affected_entity(tmp_path: Path):
    vault = git_vault(tmp_path, {"ab/00-inbox/note.md": "x\n"})
    (vault / "ab" / "stray.md").write_text("s\n", encoding="utf-8")

    with pytest.raises(UnmigratableContentError):
        require_clean_entities(vault, ["ab"])


def test_require_clean_entities_ignores_an_unaffected_entity(tmp_path: Path):
    vault = git_vault(
        tmp_path, {"ab/00-inbox/note.md": "x\n", "zz/00-inbox/note.md": "y\n"}
    )
    (vault / "zz" / "stray.md").write_text("s\n", encoding="utf-8")

    require_clean_entities(vault, ["ab"])


def test_inventory_requires_a_globally_clean_tracked_and_untracked_status(
    tmp_path: Path,
):
    vault = git_vault(tmp_path, {"ab/00-inbox/note.md": "x\n"})
    (vault / "unrelated.txt").write_text("not approved\n", encoding="utf-8")

    with pytest.raises(UnmigratableContentError, match="clean status"):
        require_clean_status(vault)
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `uv run python -m pytest -q tests/test_cutover_inventory.py`
Expected: collection error, `ModuleNotFoundError: No module named 'app.cutover_inventory'`.

- [ ] **Step 3: Write the minimal implementation**

Create `app/cutover_inventory.py`:

```python
"""cutover_inventory.py — read-only enumeration, collisions, and hard stops.

Nothing here writes. The inventory runs against the live vault, produces the
material the owner approves, and refuses conditions that must never reach a
build.
"""
from __future__ import annotations

from pathlib import Path
import subprocess

from .cutover_manifest import Mapping


class CollisionError(Exception):
    pass


class UnmigratableContentError(Exception):
    """An affected entity holds content a linked worktree cannot carry."""


def check_collisions(
    mappings: tuple[Mapping, ...], existing: dict[str, set[str]]
) -> None:
    """Refuse class 1 and class 2.

    Class 3 — one literal on two axes — is permitted: scoped replacement gives
    each axis its own typed locations, so an entity and a product sharing a
    literal migrate independently and correctly.
    """
    seen: dict[str, set[str]] = {}
    for mapping in mappings:
        axis_seen = seen.setdefault(mapping.axis, set())
        if mapping.old in axis_seen:
            raise CollisionError(f"duplicate mapping input on axis {mapping.axis!r}")
        axis_seen.add(mapping.old)

    produced: dict[str, set[str]] = {}
    for mapping in mappings:
        axis_produced = produced.setdefault(mapping.axis, set())
        if mapping.new in axis_produced:
            raise CollisionError(f"duplicate mapping output on axis {mapping.axis!r}")
        axis_produced.add(mapping.new)
        if mapping.new in existing.get(mapping.axis, set()):
            raise CollisionError(
                f"new value collides with an existing identifier on axis "
                f"{mapping.axis!r}"
            )


def untracked_or_ignored_paths(vault: Path, entity: str) -> list[str]:
    """Ignored or untracked paths beneath one entity directory.

    A linked worktree materialises tracked content only. If an affected entity
    holds anything else, promoting a renamed tree would strand it at the old
    path — outside the new entity and outside every scope check that assumes it
    lives beneath its entity root.
    """
    completed = subprocess.run(
        [
            "git", "status", "--porcelain", "--untracked-files=all",
            "--ignored", "--", entity,
        ],
        cwd=vault,
        check=True,
        capture_output=True,
        text=True,
    )
    found: list[str] = []
    for line in completed.stdout.splitlines():
        if not line:
            continue
        status, _, path = line.partition(" ")
        if status in {"??", "!!"}:
            found.append(path.strip())
    return sorted(found)


def require_clean_entities(vault: Path, entities: list[str]) -> None:
    for entity in sorted(entities):
        found = untracked_or_ignored_paths(vault, entity)
        if found:
            raise UnmigratableContentError(
                f"entity {entity!r} holds ignored or untracked content; relocate "
                f"or retire it and re-run from inventory ({len(found)} path(s))"
            )


def require_clean_status(vault: Path) -> None:
    """Planning begins only from HEAD with no tracked or untracked overlay."""
    completed = subprocess.run(
        ["git", "status", "--porcelain=v2", "--untracked-files=all"],
        cwd=vault,
        check=True,
        capture_output=True,
    )
    if completed.stdout:
        raise UnmigratableContentError(
            "inventory requires a clean status; preserve or commit current "
            "work and re-run from a newly recorded source HEAD"
        )
```

- [ ] **Step 4: Run the test and confirm it passes**

Run: `uv run python -m pytest -q tests/test_cutover_inventory.py`
Expected: PASS, 10 tests.

- [ ] **Step 5: Commit**

```bash
git add app/cutover_inventory.py tests/test_cutover_inventory.py
git commit -m "feat: add cutover collision and content checks"
```

---

### Task 9: Registry enumeration for the inventory

**Files:**
- Modify: `app/cutover_inventory.py`
- Test: `tests/test_cutover_inventory.py`

**Interfaces:**
- Produces: `existing_identifiers(vault)`, `proposed_mappings(vault)`.

This is what makes `inventory` a real command rather than a schema dump.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cutover_inventory.py`:

```python
from app.cutover_inventory import existing_identifiers, proposed_mappings


def registry_vault(root: Path) -> Path:
    return git_vault(
        root,
        {
            "_system/entities.yaml": "entities:\n  ab:\n    label: A\n  longenough:\n    label: L\n",
            "_system/products.yaml": "products:\n  ab:\n    q7:\n      label: Q\n",
            "_system/members.yaml": "members:\n  ab:\n    - {id: m7}\n",
            "_system/workspaces.yaml": "workspaces:\n  - {id: w7, entity: ab}\n",
        },
    )


def test_existing_identifiers_are_read_per_axis(tmp_path: Path):
    vault = registry_vault(tmp_path)

    existing = existing_identifiers(vault)

    assert existing["entity"] == {"ab", "longenough"}
    assert existing["product"] == {"q7"}
    assert existing["member"] == {"m7"}
    assert existing["workspace"] == {"w7"}


def test_proposed_mappings_cover_only_sub_floor_identifiers(tmp_path: Path):
    vault = registry_vault(tmp_path)

    mappings = proposed_mappings(vault)

    assert Mapping(axis="entity", old="ab", new="ab-entity") in mappings
    assert Mapping(axis="product", old="q7", new="q7-product") in mappings
    assert Mapping(axis="member", old="m7", new="m7-member") in mappings
    assert Mapping(axis="workspace", old="w7", new="w7-workspace") in mappings
    assert all(item.old != "longenough" for item in mappings)


def test_proposed_mappings_are_deterministically_ordered(tmp_path: Path):
    vault = registry_vault(tmp_path)

    assert proposed_mappings(vault) == proposed_mappings(vault)
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `uv run python -m pytest -q tests/test_cutover_inventory.py`
Expected: collection error, `ImportError: cannot import name 'existing_identifiers'`.

- [ ] **Step 3: Write the minimal implementation**

Append to `app/cutover_inventory.py`:

```python
import yaml

from .identifiers import AXES, map_identifier, meets_floor


def _load(path: Path) -> object:
    if not path.is_file():
        return None
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, OSError, yaml.YAMLError) as exc:
        raise UnmigratableContentError(f"{path.name} could not be read") from exc


def existing_identifiers(vault: Path) -> dict[str, set[str]]:
    """Every current identifier, per axis, read from the registries."""
    system = vault / "_system"
    found: dict[str, set[str]] = {axis: set() for axis in AXES}

    entities = _load(system / "entities.yaml")
    if isinstance(entities, dict):
        found["entity"].update(
            key for key in (entities.get("entities") or {}) if isinstance(key, str)
        )

    products = _load(system / "products.yaml")
    if isinstance(products, dict):
        for values in (products.get("products") or {}).values():
            if isinstance(values, dict):
                found["product"].update(k for k in values if isinstance(k, str))

    members = _load(system / "members.yaml")
    if isinstance(members, dict):
        for values in (members.get("members") or {}).values():
            if isinstance(values, list):
                found["member"].update(
                    entry["id"]
                    for entry in values
                    if isinstance(entry, dict) and isinstance(entry.get("id"), str)
                )

    workspaces = _load(system / "workspaces.yaml")
    if isinstance(workspaces, dict):
        for entry in workspaces.get("workspaces") or []:
            if isinstance(entry, dict) and isinstance(entry.get("id"), str):
                found["workspace"].add(entry["id"])

    return found


def proposed_mappings(vault: Path) -> tuple[Mapping, ...]:
    """The deterministic mapping for every sub-floor identifier."""
    existing = existing_identifiers(vault)
    mappings: list[Mapping] = []
    for axis in AXES:
        for old in sorted(existing[axis]):
            if meets_floor(old):
                continue
            mappings.append(Mapping(axis=axis, old=old, new=map_identifier(axis, old)))
    return tuple(mappings)
```

- [ ] **Step 4: Run the test and confirm it passes**

Run: `uv run python -m pytest -q tests/test_cutover_inventory.py`
Expected: PASS, 13 tests.

- [ ] **Step 5: Commit**

```bash
git add app/cutover_inventory.py tests/test_cutover_inventory.py
git commit -m "feat: enumerate sub-floor registry identifiers"
```

---

### Task 10: The isolated detached worktree

**Files:**
- Create: `app/cutover_build.py`
- Test: `tests/test_cutover_build.py`

**Interfaces:**
- Produces: `isolated_worktree(vault, source_head)`, `CutoverError`, `CutoverCommittedError`.

The worktree is **detached** and uniquely named. The rejected plan created a deterministically named branch and deleted it in `finally` — so a failed `worktree add` caused by that branch already existing would delete someone else's branch. No branch is created here, so none can be deleted.

- [ ] **Step 1: Write the failing test**

Create `tests/test_cutover_build.py`:

```python
import os
from pathlib import Path
import subprocess

import pytest

from app.cutover_build import CutoverError, isolated_worktree
from tests.conftest import git_head, git_is_clean, git_vault


def commit_in(root: Path, message: str) -> str:
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=t@e", "-c", "user.name=t",
         "commit", "-q", "-m", message],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return git_head(root)


def test_isolated_worktree_starts_at_the_requested_head(tmp_path: Path):
    vault = git_vault(tmp_path / "vault", {"a.md": "x\n"})
    head = git_head(vault)

    with isolated_worktree(vault, head) as scratch:
        assert git_head(scratch) == head
        assert scratch != vault


def test_isolated_worktree_is_detached_and_creates_no_branch(tmp_path: Path):
    vault = git_vault(tmp_path / "vault", {"a.md": "x\n"})
    before = subprocess.run(
        ["git", "branch", "--format=%(refname)"],
        cwd=vault, check=True, capture_output=True, text=True,
    ).stdout

    with isolated_worktree(vault, git_head(vault)) as scratch:
        symbolic = subprocess.run(
            ["git", "symbolic-ref", "-q", "HEAD"],
            cwd=scratch, check=False, capture_output=True, text=True,
        )
        assert symbolic.returncode != 0  # detached HEAD

    after = subprocess.run(
        ["git", "branch", "--format=%(refname)"],
        cwd=vault, check=True, capture_output=True, text=True,
    ).stdout
    assert before == after


def test_a_pre_existing_branch_is_never_deleted(tmp_path: Path):
    vault = git_vault(tmp_path / "vault", {"a.md": "x\n"})
    head = git_head(vault)
    subprocess.run(
        ["git", "branch", f"cutover/build-{head[:12]}"],
        cwd=vault, check=True, capture_output=True,
    )

    with isolated_worktree(vault, head):
        pass

    remaining = subprocess.run(
        ["git", "branch", "--format=%(refname:short)"],
        cwd=vault, check=True, capture_output=True, text=True,
    ).stdout
    assert f"cutover/build-{head[:12]}" in remaining


def test_isolated_worktree_writes_do_not_reach_the_live_vault(tmp_path: Path):
    vault = git_vault(tmp_path / "vault", {"a.md": "x\n"})
    head = git_head(vault)

    with isolated_worktree(vault, head) as scratch:
        (scratch / "a.md").write_text("changed\n", encoding="utf-8")

    assert (vault / "a.md").read_text(encoding="utf-8") == "x\n"
    assert git_is_clean(vault)
    assert git_head(vault) == head


def test_isolated_worktree_is_removed_on_success_and_failure(tmp_path: Path):
    vault = git_vault(tmp_path / "vault", {"a.md": "x\n"})
    recorded: list[Path] = []

    with isolated_worktree(vault, git_head(vault)) as scratch:
        recorded.append(scratch)
    assert not recorded[0].exists()

    with pytest.raises(RuntimeError):
        with isolated_worktree(vault, git_head(vault)) as scratch:
            recorded.append(scratch)
            raise RuntimeError("injected")
    assert not recorded[1].exists()
    assert git_is_clean(vault)


def test_a_commit_built_in_isolation_is_visible_from_the_vault(tmp_path: Path):
    vault = git_vault(tmp_path / "vault", {"a.md": "x\n"})
    head = git_head(vault)

    with isolated_worktree(vault, head) as scratch:
        (scratch / "a.md").write_text("changed\n", encoding="utf-8")
        built = commit_in(scratch, "built")

    kind = subprocess.run(
        ["git", "cat-file", "-t", built],
        cwd=vault, check=True, capture_output=True, text=True,
    ).stdout.strip()
    assert kind == "commit"
    assert git_head(vault) == head


def test_a_head_that_is_not_the_requested_source_is_refused(tmp_path: Path):
    vault = git_vault(tmp_path / "vault", {"a.md": "x\n"})

    with pytest.raises(CutoverError):
        with isolated_worktree(vault, "b" * 40):
            pass
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `uv run python -m pytest -q tests/test_cutover_build.py`
Expected: collection error, `ModuleNotFoundError: No module named 'app.cutover_build'`.

- [ ] **Step 3: Write the minimal implementation**

Create `app/cutover_build.py`:

```python
"""cutover_build.py — build the single cutover commit in isolation.

Nothing is written to the live vault. Every edit happens in a temporary
detached linked worktree, which shares the vault's object database, so the
resulting commit is already reachable from the vault and promotion is a
fast-forward rather than a file copy.

A failure before promotion discards the worktree. The live vault was never
touched, so there is nothing to roll back, and no `reset --hard` or
`clean -fd` is ever issued against it.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
import shutil
import subprocess
import tempfile


class CutoverError(Exception):
    pass


class CutoverCommittedError(CutoverError):
    """The promotion committed but confirmation or cleanup failed."""


def git(root: Path, *args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args], cwd=root, check=True, capture_output=True, text=True
        ).stdout
    except subprocess.CalledProcessError as exc:
        raise CutoverError(f"git {args[0]} failed") from exc


@contextmanager
def isolated_worktree(vault: Path, source_head: str) -> Iterator[Path]:
    """A throwaway **detached** worktree at `source_head`, removed on exit.

    Detached on purpose. A named branch would have to be cleaned up, and a
    cleanup that deletes a branch it did not create can destroy an unrelated
    one when creation failed precisely because that branch already existed.
    Creating no ref means there is no ref to delete.
    """
    if git(vault, "rev-parse", "HEAD").strip() != source_head:
        raise CutoverError("vault HEAD is not the recorded source HEAD")
    parent = Path(tempfile.mkdtemp(prefix="oneos-cutover-"))
    scratch = parent / "tree"
    try:
        git(vault, "worktree", "add", "--quiet", "--detach", str(scratch), source_head)
        yield scratch
    finally:
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(scratch)],
            cwd=vault, check=False, capture_output=True,
        )
        shutil.rmtree(parent, ignore_errors=True)
```

- [ ] **Step 4: Run the test and confirm it passes**

Run: `uv run python -m pytest -q tests/test_cutover_build.py`
Expected: PASS, 7 tests.

- [ ] **Step 5: Commit**

```bash
git add app/cutover_build.py tests/test_cutover_build.py
git commit -m "feat: add the detached isolated cutover worktree"
```

---

### Task 11: The build — validate, disposition, database, mappings, gates, commit

**Files:**
- Modify: `app/cutover_build.py`
- Test: `tests/test_cutover_build.py`

**Interfaces:**
- Produces: `BuildResult(source_head, commit, tree, database_changes)`,
  `build_cutover(vault, manifest_bytes, record, validator=run_vault_validators)`,
  `run_vault_validators(root)`.

Ordering is fixed and each step exists for a reason:

1. **Verify the manifest** against the approval record.
2. **Validate every mapping** against the deterministic rule.
3. **Check collisions** against the live registries.
4. **Refuse unmigratable content** in every affected entity.
5. **Check advisory dispositions — before any path moves**, because a disposition records a source-relative path and an entity move would invalidate every one of them.
6. **Apply database updates**, using each approved source-relative path verbatim, then query the in-database residual immediately.
7. **Apply mappings** in fixed order, each planned against the tree the previous one produced.
8. **Run the scoped residual gate** over the migrated tree.
9. **Regenerate the advisory report** and compare it to the approved incidental
   dispositions after translating entity path heads.
10. **Run the vault validators** on the fully migrated, still-uncommitted tree.
11. **Commit exactly once** and return the commit tree plus the database row
    changes captured from this same build.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cutover_build.py`:

```python
import hashlib
import sqlite3

import yaml

import app.cutover_build as cutover_build

from app.cutover_build import (
    BuildResult,
    build_cutover,
    git,
    run_vault_validators,
)
from app.cutover_manifest import (
    ApprovalManifest,
    ApprovalRecord,
    DatabaseTarget,
    Disposition,
    Mapping,
    canonical_bytes,
    load_manifest,
    manifest_digest,
)
from tests.conftest import git_count_commits, git_status_bytes


def disposition(
    path: str,
    line: int,
    axis: str,
    old: str,
    text: str,
    *,
    ordinal: int = 1,
    kind: str = "incidental",
    typed_location: str = "",
) -> Disposition:
    return Disposition(
        path=path,
        axis=axis,
        old=old,
        ordinal=ordinal,
        context_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        line=line,
        kind=kind,
        typed_location=typed_location,
    )


def cutover_vault(root: Path) -> Path:
    vault = git_vault(
        root,
        {
            "_system/entities.yaml": "entities:\n  ab:\n    label: A\n",
            "_system/products.yaml": "products:\n  ab:\n    q7:\n      label: Q\n",
            "_system/members.yaml": "members:\n  ab:\n    - {id: m7}\n",
            "_system/workspaces.yaml":
                "workspaces:\n  - {id: w7, entity: ab, product: q7, member: m7, kind: product}\n",
            "_system/scripts/action-policy.yaml":
                'actors:\n  h:\n    allow:\n      - {action: read, paths: ["ab/**"], '
                'except: ["ab/.sensitive/**"]}\n',
            "_system/scripts/check_v2.py":
                'print("0 error(s), 0 warning(s)")\n',
            "_system/scripts/test_cutover_validator.py":
                "import unittest\n\n"
                "class ValidatorSmokeTest(unittest.TestCase):\n"
                "    def test_ok(self):\n"
                "        self.assertTrue(True)\n",
            "ab/00-inbox/note.md":
                "---\nentity: ab\nproduct: q7\nmember: m7\n---\n\nthe ab word\n",
        },
    )
    db = vault / "ab" / "books.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE ledger (product TEXT)")
    conn.execute("INSERT INTO ledger VALUES ('q7')")
    conn.execute("CREATE TABLE roster (member TEXT)")
    conn.execute("INSERT INTO roster VALUES ('m7')")
    conn.commit()
    conn.close()
    commit_in(vault, "add db")
    return vault


def approved(vault: Path, dispositions=None) -> tuple[bytes, ApprovalRecord]:
    manifest = ApprovalManifest(
        source_head=git_head(vault),
        mappings=(
            Mapping(axis="entity", old="ab", new="ab-entity"),
            Mapping(axis="product", old="q7", new="q7-product"),
            Mapping(axis="member", old="m7", new="m7-member"),
            Mapping(axis="workspace", old="w7", new="w7-workspace"),
        ),
        databases=(
            DatabaseTarget(
                path="ab/books.db", table="ledger", column="product", axis="product"
            ),
            DatabaseTarget(
                path="ab/books.db", table="roster", column="member", axis="member"
            ),
        ),
        dispositions=dispositions
        if dispositions is not None
        else (
            disposition(
                "ab/00-inbox/note.md",
                7,
                "entity",
                "ab",
                "the ab word",
            ),
        ),
    )
    raw = canonical_bytes(manifest)
    return raw, ApprovalRecord(
        manifest_sha256=manifest_digest(manifest), approved_by="owner"
    )


def test_build_leaves_the_live_vault_untouched(tmp_path: Path):
    vault = cutover_vault(tmp_path / "vault")
    head = git_head(vault)
    before = git_count_commits(vault)

    raw, record = approved(vault)
    build_cutover(vault, raw, record)

    assert git_head(vault) == head
    assert git_count_commits(vault) == before
    assert git_is_clean(vault)


def test_build_refuses_a_manifest_that_does_not_match_its_record(tmp_path: Path):
    vault = cutover_vault(tmp_path / "vault")
    raw, _ = approved(vault)

    with pytest.raises(Exception):
        build_cutover(
            vault, raw, ApprovalRecord(manifest_sha256="c" * 64, approved_by="owner")
        )
    assert git_is_clean(vault)


def test_build_refuses_a_mapping_that_is_not_the_deterministic_result(tmp_path: Path):
    vault = cutover_vault(tmp_path / "vault")
    manifest = ApprovalManifest(
        source_head=git_head(vault),
        mappings=(Mapping(axis="entity", old="ab", new="ab-entity-2"),),
        databases=(),
        dispositions=(),
    )
    raw = canonical_bytes(manifest)
    record = ApprovalRecord(
        manifest_sha256=manifest_digest(manifest), approved_by="owner"
    )

    with pytest.raises(Exception, match="deterministic"):
        build_cutover(vault, raw, record)


def test_build_refuses_an_undispositioned_advisory_occurrence(tmp_path: Path):
    vault = cutover_vault(tmp_path / "vault")
    raw, record = approved(vault, dispositions=())

    with pytest.raises(Exception, match="disposition"):
        build_cutover(vault, raw, record)


def test_build_refuses_a_structural_disposition_naming_no_typed_location(
    tmp_path: Path,
):
    vault = cutover_vault(tmp_path / "vault")
    raw, record = approved(
        vault,
        dispositions=(
            disposition(
                "ab/00-inbox/note.md",
                7,
                "entity",
                "ab",
                "the ab word",
                kind="structural",
                typed_location="entity:nowhere:nothing",
            ),
        ),
    )

    with pytest.raises(Exception, match="typed location"):
        build_cutover(vault, raw, record)


def test_build_refuses_a_colliding_mapping(tmp_path: Path):
    vault = git_vault(
        tmp_path / "vault",
        {"_system/entities.yaml": "entities:\n  ab:\n    label: A\n  ab-entity:\n    label: B\n"},
    )
    manifest = ApprovalManifest(
        source_head=git_head(vault),
        mappings=(Mapping(axis="entity", old="ab", new="ab-entity"),),
        databases=(),
        dispositions=(),
    )
    raw = canonical_bytes(manifest)
    record = ApprovalRecord(manifest_sha256=manifest_digest(manifest), approved_by="owner")

    with pytest.raises(Exception, match="collides"):
        build_cutover(vault, raw, record, validator=lambda _root: None)


def test_build_collision_check_uses_the_manifest_source_head(
    tmp_path: Path, monkeypatch
):
    vault = cutover_vault(tmp_path / "vault")
    raw, record = approved(vault)
    registry = vault / "_system" / "entities.yaml"
    original = registry.read_bytes()
    real = cutover_build.require_clean_status
    fired = False

    def live_changes_after_the_initial_status_check(root):
        nonlocal fired
        real(root)
        registry.write_text(
            "entities:\n  ab:\n    label: A\n  ab-entity:\n    label: Later\n",
            encoding="utf-8",
        )
        fired = True

    monkeypatch.setattr(
        cutover_build,
        "require_clean_status",
        live_changes_after_the_initial_status_check,
    )
    try:
        build_cutover(vault, raw, record)
    finally:
        registry.write_bytes(original)

    assert fired, "the live-race probe never ran"


def test_build_refuses_an_entity_with_ignored_content(tmp_path: Path):
    vault = git_vault(
        tmp_path / "vault",
        {
            ".gitignore": ".sensitive/\n",
            "_system/entities.yaml": "entities:\n  ab:\n    label: A\n",
            "ab/00-inbox/n.md": "---\nentity: ab\n---\n",
        },
    )
    (vault / "ab" / ".sensitive").mkdir()
    (vault / "ab" / ".sensitive" / "s.md").write_text("s\n", encoding="utf-8")
    manifest = ApprovalManifest(
        source_head=git_head(vault),
        mappings=(Mapping(axis="entity", old="ab", new="ab-entity"),),
        databases=(),
        dispositions=(),
    )
    raw = canonical_bytes(manifest)
    record = ApprovalRecord(manifest_sha256=manifest_digest(manifest), approved_by="owner")

    with pytest.raises(Exception, match="ignored or untracked"):
        build_cutover(vault, raw, record, validator=lambda _root: None)


def test_dispositions_are_checked_before_any_path_move(tmp_path: Path, monkeypatch):
    vault = cutover_vault(tmp_path / "vault")
    raw, record = approved(vault)
    real = cutover_build._require_dispositions

    def assert_source_shape(root, manifest):
        assert (root / "ab").is_dir(), "dispositions were not checked on the source tree"
        assert not (root / "ab-entity").exists(), "an entity moved before disposition checking"
        return real(root, manifest)

    monkeypatch.setattr(cutover_build, "_require_dispositions", assert_source_shape)

    build_cutover(vault, raw, record)


def test_build_gate_refuses_a_writer_that_misses_the_policy_except_half(
    tmp_path: Path, monkeypatch
):
    vault = cutover_vault(tmp_path / "vault")
    raw, record = approved(vault)
    real = cutover_build.rewrite_policy_path_heads

    def paths_only(text, old, new):
        rewritten = real(text, old, new)
        return rewritten.replace(
            f'except: ["{new}/.sensitive/**"]',
            f'except: ["{old}/.sensitive/**"]',
        )

    monkeypatch.setattr(cutover_build, "rewrite_policy_path_heads", paths_only)

    with pytest.raises(CutoverError, match="entity:action-policy:except"):
        build_cutover(vault, raw, record)


def test_build_gate_refuses_a_database_writer_that_leaves_the_old_value(
    tmp_path: Path, monkeypatch
):
    vault = cutover_vault(tmp_path / "vault")
    raw, record = approved(vault)
    monkeypatch.setattr(cutover_build, "apply_database_mappings", lambda *_: ())

    with pytest.raises(CutoverError, match="database residual after update"):
        build_cutover(vault, raw, record)


def test_sequential_application_preserves_every_mapping_touching_one_file(
    tmp_path: Path,
):
    vault = cutover_vault(tmp_path / "vault")
    raw, _record = approved(vault)
    manifest = load_manifest(raw)

    cutover_build._apply_mappings_in_order(vault, manifest)

    workspaces = (vault / "_system" / "workspaces.yaml").read_text(encoding="utf-8")
    assert "id: w7-workspace" in workspaces, "workspace rewrite was lost"
    assert "entity: ab-entity" in workspaces, "entity rewrite was lost"
    assert "product: q7-product" in workspaces, "product rewrite was lost"
    assert "member: m7-member" in workspaces, "member rewrite was lost"


def test_build_refuses_an_extra_or_stale_disposition(tmp_path: Path):
    vault = cutover_vault(tmp_path / "vault")
    raw, record = approved(
        vault,
        dispositions=(
            disposition(
                "ab/00-inbox/note.md", 7, "entity", "ab", "the ab word"
            ),
            disposition("missing.md", 1, "entity", "ab", "missing ab"),
        ),
    )

    with pytest.raises(Exception, match="exactly match"):
        build_cutover(vault, raw, record)


def test_build_regenerates_the_advisory_report_after_rewriting(
    tmp_path: Path, monkeypatch
):
    vault = cutover_vault(tmp_path / "vault")
    raw, record = approved(vault)
    real = cutover_build._apply_entity_mapping

    def introduces_unapproved_occurrence(root, old, new):
        real(root, old, new)
        note = root / new / "00-inbox" / "note.md"
        note.write_text(note.read_text(encoding="utf-8") + "new ab occurrence\n", encoding="utf-8")

    monkeypatch.setattr(cutover_build, "_apply_entity_mapping", introduces_unapproved_occurrence)

    with pytest.raises(Exception, match="advisory report changed"):
        build_cutover(vault, raw, record)


def test_post_advisory_identity_survives_a_display_line_shift(
    tmp_path: Path, monkeypatch
):
    vault = cutover_vault(tmp_path / "vault")
    raw, record = approved(vault)
    real = cutover_build._apply_entity_mapping

    def shifts_only_the_display_line(root, old, new):
        real(root, old, new)
        note = root / new / "00-inbox" / "note.md"
        text = note.read_text(encoding="utf-8")
        note.write_text(
            text.replace("\nthe ab word\n", "\nunrelated line\nthe ab word\n"),
            encoding="utf-8",
        )

    monkeypatch.setattr(
        cutover_build, "_apply_entity_mapping", shifts_only_the_display_line
    )

    build_cutover(vault, raw, record)


def test_post_advisory_identity_rejects_same_count_with_changed_context(
    tmp_path: Path, monkeypatch
):
    vault = cutover_vault(tmp_path / "vault")
    raw, record = approved(vault)
    real = cutover_build._apply_entity_mapping

    def changes_context_without_changing_the_count(root, old, new):
        real(root, old, new)
        note = root / new / "00-inbox" / "note.md"
        text = note.read_text(encoding="utf-8")
        note.write_text(
            text.replace("the ab word", "changed ab word"), encoding="utf-8"
        )

    monkeypatch.setattr(
        cutover_build,
        "_apply_entity_mapping",
        changes_context_without_changing_the_count,
    )

    with pytest.raises(Exception, match="advisory report changed"):
        build_cutover(vault, raw, record)


def test_validators_run_after_migration_and_before_commit(tmp_path: Path):
    vault = cutover_vault(tmp_path / "vault")
    raw, record = approved(vault)
    observed: list[tuple[bool, str]] = []

    def validator(root: Path) -> None:
        observed.append(((root / "ab-entity").is_dir(), git_head(root)))

    result = build_cutover(vault, raw, record, validator=validator)

    assert observed == [(True, result.source_head)]


def test_validator_failure_discards_the_build_and_preserves_the_live_vault(
    tmp_path: Path,
):
    vault = cutover_vault(tmp_path / "vault")
    raw, record = approved(vault)
    before = (git_head(vault), git_status_bytes(vault), (vault / "ab/books.db").read_bytes())

    def fail(_root: Path) -> None:
        raise CutoverError("validator failed deliberately")

    with pytest.raises(CutoverError, match="validator failed deliberately"):
        build_cutover(vault, raw, record, validator=fail)

    assert (git_head(vault), git_status_bytes(vault), (vault / "ab/books.db").read_bytes()) == before


def test_default_validator_accepts_the_documented_check_v2_summary(tmp_path: Path):
    vault = cutover_vault(tmp_path / "vault")

    run_vault_validators(vault)


def test_default_validator_does_not_read_ten_errors_as_zero(tmp_path: Path):
    vault = cutover_vault(tmp_path / "vault")
    (vault / "_system" / "scripts" / "check_v2.py").write_text(
        'print("10 error(s), 0 warning(s)")\n', encoding="utf-8"
    )

    with pytest.raises(CutoverError, match="0 error"):
        run_vault_validators(vault)


def _opaque_vault_tree(vault: Path) -> dict[str, tuple[str, bytes]]:
    captured = {}
    for path in sorted(vault.rglob("*")):
        relative = path.relative_to(vault)
        if ".git" in relative.parts:
            continue
        if path.is_symlink():
            captured[relative.as_posix()] = ("symlink", os.readlink(path).encode())
        elif path.is_file():
            captured[relative.as_posix()] = ("file", path.read_bytes())
        elif path.is_dir():
            captured[relative.as_posix()] = ("dir", b"")
    return captured


@pytest.mark.parametrize(
    "stage",
    ("mapping-validation", "database", "mapping", "residual", "advisory", "validator", "commit"),
)
def test_every_precommit_stage_failure_preserves_the_complete_live_vault(
    tmp_path: Path, monkeypatch, stage: str
):
    vault = cutover_vault(tmp_path / "vault")
    (vault / ".gitignore").write_text("zz/.sensitive/\n", encoding="utf-8")
    commit_in(vault, "ignore unrelated private state")
    hidden = vault / "zz" / ".sensitive" / "keep.bin"
    hidden.parent.mkdir(parents=True)
    hidden.write_bytes(b"must survive")
    raw, record = approved(vault)
    boundary = (git_head(vault), git_status_bytes(vault), _opaque_vault_tree(vault))

    if stage == "mapping-validation":
        monkeypatch.setattr(cutover_build, "validate_mapping_pair", lambda *_: (_ for _ in ()).throw(CutoverError("stage mapping-validation")))
    elif stage == "database":
        monkeypatch.setattr(cutover_build, "apply_database_mappings", lambda *_: (_ for _ in ()).throw(CutoverError("stage database")))
    elif stage == "mapping":
        monkeypatch.setattr(cutover_build, "_apply_entity_mapping", lambda *_: (_ for _ in ()).throw(CutoverError("stage mapping")))
    elif stage == "residual":
        monkeypatch.setattr(cutover_build, "scoped_residuals", lambda *_: (_ for _ in ()).throw(CutoverError("stage residual")))
    elif stage == "advisory":
        monkeypatch.setattr(cutover_build, "_require_post_advisory", lambda *_: (_ for _ in ()).throw(CutoverError("stage advisory")))
    elif stage == "commit":
        real_git = cutover_build.git

        def fail_commit(root, *args):
            if "commit" in args:
                raise CutoverError("stage commit")
            return real_git(root, *args)

        monkeypatch.setattr(cutover_build, "git", fail_commit)

    def validator(_root: Path) -> None:
        if stage == "validator":
            raise CutoverError("stage validator")

    with pytest.raises(CutoverError, match=f"stage {stage}"):
        build_cutover(vault, raw, record, validator=validator)

    assert (
        git_head(vault),
        git_status_bytes(vault),
        _opaque_vault_tree(vault),
    ) == boundary, f"precommit stage {stage} mutated the live vault"
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `uv run python -m pytest -q tests/test_cutover_build.py`
Expected: collection error, `ImportError: cannot import name 'build_cutover'`.

- [ ] **Step 3: Write the minimal implementation**

Append to `app/cutover_build.py`:

```python
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
import re
import subprocess
import sys

import yaml

from .cutover_db import DatabaseChange, apply_database_mappings, database_residuals
from .cutover_inventory import (
    check_collisions,
    existing_identifiers,
    require_clean_entities,
    require_clean_status,
)
from .cutover_locations import (
    advisory_occurrences,
    location_keys,
    rewrite_front_matter_field,
    rewrite_mapping_key,
    rewrite_path_head,
    rewrite_policy_path_heads,
    rewrite_yaml_path_head_field,
    rewrite_yaml_value_field,
    scoped_residuals,
)
from .cutover_manifest import (
    ApprovalManifest,
    ApprovalRecord,
    load_manifest,
    verify_manifest,
)
from .identifiers import validate_mapping_pair

#: Entity first: its directory move relocates everything beneath it. Then the
#: value axes, then workspaces. Within an axis, sorted by old identifier.
_AXIS_ORDER = ("entity", "product", "member", "workspace")
_CHECK_V2_ZERO = re.compile(r"(?m)^0 error\(s\), 0 warning\(s\)\s*$")


@dataclass(frozen=True)
class BuildResult:
    source_head: str
    commit: str
    tree: str
    database_changes: tuple[DatabaseChange, ...]


def mappings_in_order(manifest: ApprovalManifest) -> list:
    return sorted(
        manifest.mappings, key=lambda item: (_AXIS_ORDER.index(item.axis), item.old)
    )


def _record_former_slug(text: str, key: str, old: str, indent: int) -> str:
    """Append inert provenance beneath one entity/product mapping key.

    A key may already carry provenance from a previous sanctioned rename. A
    second `former_slugs` key would be duplicate YAML and could change meaning
    by parser, so this function updates the existing list or inserts exactly
    one new child. Member and workspace entries never call it.
    """
    import re

    lines = text.splitlines(keepends=True)
    key_re = re.compile(rf"^(\s*){re.escape(key)}:\s*(?:#.*)?$")
    parent_index = next(
        (index for index, line in enumerate(lines) if key_re.match(line.rstrip("\n"))),
        None,
    )
    if parent_index is None:
        raise CutoverError(f"renamed registry key {key!r} is absent")
    parent_indent = len(key_re.match(lines[parent_index].rstrip("\n")).group(1))
    existing: list[int] = []
    end = len(lines)
    for index in range(parent_index + 1, len(lines)):
        stripped = lines[index].strip()
        if stripped:
            current_indent = len(lines[index]) - len(lines[index].lstrip(" "))
            if current_indent <= parent_indent:
                end = index
                break
        if re.match(r"^\s*former_slugs:\s*", lines[index]):
            existing.append(index)
    if len(existing) > 1:
        raise CutoverError(f"registry key {key!r} has duplicate former_slugs")
    if existing:
        index = existing[0]
        try:
            document = yaml.safe_load(lines[index].strip()) or {}
            values = document["former_slugs"]
        except (KeyError, TypeError, yaml.YAMLError) as exc:
            raise CutoverError(f"former_slugs for {key!r} is malformed") from exc
        if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
            raise CutoverError(f"former_slugs for {key!r} must be a string list")
        if old not in values:
            values.append(old)
        prefix = lines[index][: len(lines[index]) - len(lines[index].lstrip(" "))]
        lines[index] = prefix + f"former_slugs: [{', '.join(values)}]\n"
        return "".join(lines)
    lines.insert(parent_index + 1, " " * indent + f"former_slugs: [{old}]\n")
    return "".join(lines)


def _rewrite_proposal(path: Path, old: str, new: str) -> None:
    """Rewrite only the three entity-owned proposal fields.

    Do not parse and re-serialize the record. That would alter quoting, key
    order, comments, or unrelated values outside the closed location table.
    The scoped residual gate subsequently parses the result and refuses any
    unsupported representation that these exact textual writers left behind.
    """
    text = path.read_text(encoding="utf-8")
    rewritten = rewrite_yaml_value_field(text, "entity", old, new)
    for field in ("src", "dst"):
        rewritten = rewrite_yaml_path_head_field(rewritten, field, old, new)
    if rewritten != text:
        path.write_text(rewritten, encoding="utf-8")


def _markdown_files(root: Path):
    for candidate in sorted(root.rglob("*.md")):
        if ".git" in candidate.relative_to(root).parts or candidate.is_symlink():
            continue
        yield candidate


def _apply_entity_mapping(root: Path, old: str, new: str) -> None:
    system = root / "_system"
    for name in ("products.yaml", "members.yaml"):
        path = system / name
        if path.is_file():
            path.write_text(
                rewrite_mapping_key(path.read_text(encoding="utf-8"), old, new, 2),
                encoding="utf-8",
            )
    entities = system / "entities.yaml"
    if entities.is_file():
        text = rewrite_mapping_key(entities.read_text(encoding="utf-8"), old, new, 2)
        entities.write_text(_record_former_slug(text, new, old, 4), encoding="utf-8")
    workspaces = system / "workspaces.yaml"
    if workspaces.is_file():
        text = workspaces.read_text(encoding="utf-8")
        for field in ("entity", "primary_entity"):
            text = rewrite_yaml_value_field(text, field, old, new)
        workspaces.write_text(text, encoding="utf-8")
    policy = system / "scripts" / "action-policy.yaml"
    if policy.is_file():
        policy.write_text(
            rewrite_policy_path_heads(policy.read_text(encoding="utf-8"), old, new),
            encoding="utf-8",
        )
    for markdown in _markdown_files(root):
        text = markdown.read_text(encoding="utf-8")
        rewritten = rewrite_front_matter_field(text, "entity", old, new)
        if rewritten != text:
            markdown.write_text(rewritten, encoding="utf-8")
    for record in sorted(root.rglob("outbox/*.yaml")):
        _rewrite_proposal(record, old, new)
    if (root / old).is_dir():
        git(root, "mv", old, new)


def _apply_value_mapping(root: Path, axis: str, old: str, new: str) -> None:
    system = root / "_system"
    if axis == "product":
        registry = system / "products.yaml"
        if registry.is_file():
            text = rewrite_mapping_key(registry.read_text(encoding="utf-8"), old, new, 4)
            registry.write_text(_record_former_slug(text, new, old, 6), encoding="utf-8")
    else:
        registry = system / "members.yaml"
        if registry.is_file():
            registry.write_text(
                rewrite_yaml_value_field(
                    registry.read_text(encoding="utf-8"), "id", old, new
                ),
                encoding="utf-8",
            )
    for markdown in _markdown_files(root):
        text = markdown.read_text(encoding="utf-8")
        rewritten = rewrite_front_matter_field(text, axis, old, new)
        if rewritten != text:
            markdown.write_text(rewritten, encoding="utf-8")
    workspaces = system / "workspaces.yaml"
    if workspaces.is_file():
        workspaces.write_text(
            rewrite_yaml_value_field(
                workspaces.read_text(encoding="utf-8"), axis, old, new
            ),
            encoding="utf-8",
        )


def _apply_workspace_mapping(root: Path, old: str, new: str) -> None:
    workspaces = root / "_system" / "workspaces.yaml"
    if workspaces.is_file():
        workspaces.write_text(
            rewrite_yaml_value_field(
                workspaces.read_text(encoding="utf-8"), "id", old, new
            ),
            encoding="utf-8",
        )


def _apply_mappings_in_order(root: Path, manifest: ApprovalManifest) -> None:
    """Plan each mapping from the tree produced by its predecessor."""
    for mapping in mappings_in_order(manifest):
        if mapping.axis == "entity":
            _apply_entity_mapping(root, mapping.old, mapping.new)
        elif mapping.axis == "workspace":
            _apply_workspace_mapping(root, mapping.old, mapping.new)
        else:
            _apply_value_mapping(root, mapping.axis, mapping.old, mapping.new)


def _occurrence_key(item, *, path: str | None = None, include_line: bool = False):
    key = (
        item.path if path is None else path,
        item.axis,
        item.old,
        item.ordinal,
        item.context_sha256,
    )
    return key + ((item.line,) if include_line else ())


def _require_dispositions(root: Path, manifest: ApprovalManifest) -> None:
    """Bind the source advisory report exactly before any path moves."""
    valid = location_keys()
    for disposition in manifest.dispositions:
        if disposition.kind == "structural":
            if disposition.typed_location not in valid:
                raise CutoverError(
                    "structural disposition names an unknown typed location"
                )
            raise CutoverError(
                "structural advisory occurrence requires a Stage A location-table "
                "change, fresh inventory, and fresh approval"
            )
    expected = {
        _occurrence_key(item, include_line=True)
        for item in manifest.dispositions
    }
    actual = {
        _occurrence_key(item, include_line=True)
        for item in advisory_occurrences(root, manifest.mappings)
    }
    if actual != expected:
        raise CutoverError(
            "approved dispositions do not exactly match the source advisory report; "
            "re-run from inventory"
        )


def _translated_advisory_path(path: str, manifest: ApprovalManifest) -> str:
    translated = path
    for mapping in mappings_in_order(manifest):
        if mapping.axis == "entity":
            translated = rewrite_path_head(translated, mapping.old, mapping.new)
    return translated


def _require_post_advisory(root: Path, manifest: ApprovalManifest) -> None:
    """Regenerate the report after migration and compare approved evidence.

    Entity moves translate source-relative path heads and provenance insertion
    can shift display line numbers. The stable identity therefore retains axis,
    old value, file-level token ordinal, and exact-line context digest while
    translating only the path. The source display line was already
    digest-bound and checked by `_require_dispositions` before mutation.
    """
    expected = Counter(
        _occurrence_key(
            item, path=_translated_advisory_path(item.path, manifest)
        )
        for item in manifest.dispositions
        if item.kind == "incidental"
    )
    actual = Counter(
        _occurrence_key(item)
        for item in advisory_occurrences(root, manifest.mappings)
    )
    if actual != expected:
        raise CutoverError(
            "post-migration advisory report changed from the approved incidental set"
        )


def run_vault_validators(root: Path) -> None:
    """Run both existing read-only vault validators on the migrated tree."""
    scripts = root / "_system" / "scripts"
    check_v2 = scripts / "check_v2.py"
    if not check_v2.is_file():
        raise CutoverError("check_v2.py is absent from the isolated tree")
    structural = subprocess.run(
        [sys.executable, str(check_v2), "."],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    combined = structural.stdout + structural.stderr
    if structural.returncode != 0 or not _CHECK_V2_ZERO.search(combined):
        raise CutoverError("check_v2 did not report 0 error(s), 0 warning(s)")
    private_suite = subprocess.run(
        [sys.executable, "-m", "unittest", "discover"],
        cwd=scripts,
        check=False,
        capture_output=True,
        text=True,
    )
    if private_suite.returncode != 0:
        raise CutoverError("vault script tests failed on the isolated tree")


def build_cutover(
    vault: Path,
    manifest_bytes: bytes,
    record: ApprovalRecord,
    *,
    validator: Callable[[Path], None] = run_vault_validators,
) -> BuildResult:
    """Build and verify the one exact artifact later consumed by promotion."""
    verify_manifest(manifest_bytes, record)
    manifest = load_manifest(manifest_bytes)

    require_clean_status(vault)
    for mapping in manifest.mappings:
        validate_mapping_pair(mapping.axis, mapping.old, mapping.new)
    affected = [item.old for item in manifest.mappings if item.axis == "entity"]
    require_clean_entities(vault, affected)

    with isolated_worktree(vault, manifest.source_head) as scratch:
        check_collisions(manifest.mappings, existing_identifiers(scratch))
        _require_dispositions(scratch, manifest)

        database_changes = apply_database_mappings(
            scratch, manifest.databases, manifest.mappings
        )
        residual = database_residuals(scratch, manifest.databases, manifest.mappings)
        if residual:
            raise CutoverError(
                f"database residual after update: {len(residual)} row set(s)"
            )

        _apply_mappings_in_order(scratch, manifest)

        remaining = scoped_residuals(scratch, manifest.mappings)
        if remaining:
            raise CutoverError(
                f"scoped residual after migration: {remaining[0].location}"
            )
        remaining_rows = database_residuals(
            scratch, manifest.databases, manifest.mappings
        )
        if remaining_rows:
            raise CutoverError("database residual after migration")

        _require_post_advisory(scratch, manifest)
        validator(scratch)

        git(scratch, "add", "-A")
        git(
            scratch,
            "-c", "user.email=cutover@invalid", "-c", "user.name=cutover",
            "commit", "-q", "-m",
            "cutover: raise registry identifiers to the floor",
        )
        commit = git(scratch, "rev-parse", "HEAD").strip()
        tree = git(scratch, "rev-parse", "HEAD^{tree}").strip()
        return BuildResult(manifest.source_head, commit, tree, database_changes)
```

- [ ] **Step 4: Run the test and confirm it passes**

Run: `uv run python -m pytest -q tests/test_cutover_build.py`
Expected: PASS, 34 tests (the seven Task 10 tests, twenty ordinary Task 11
tests, and seven parameter cases in the precommit-stage matrix).

If a fixture's disposition line number does not match, print
`advisory_occurrences(scratch, manifest.mappings)` and correct the fixture's
`Disposition` entries. Do **not** loosen the check.

- [ ] **Step 5: Commit**

```bash
git add app/cutover_build.py tests/test_cutover_build.py
git commit -m "feat: build the single cutover commit under both gates"
```

---

### Task 12: Promotion under the action lock

**Files:**
- Create: `app/cutover.py`
- Test: `tests/test_cutover_promotion.py`

**Interfaces:**
- Produces: `promote(vault, built_commit, source_head, expected_status, affected_entities)`.

Three defects from the rejected plan are fixed: the shared action lock is actually acquired, the ignored/untracked check is repeated immediately before the fast-forward, and a failed promotion is distinguished from a promotion that committed but could not be confirmed.

Promotion does not authorize writers to restart. The operator first verifies
the promoted state while every writer remains quiesced. Plain `git revert` is
safe only in that window. Once writers restart, later SQLite writes make an
ordinary revert unsafe; recovery then requires writers to be stopped again,
current databases backed up, later writes inventoried, and a separately
reviewed typed reverse migration or replay.

- [ ] **Step 1: Write the failing test**

Create `tests/test_cutover_promotion.py`:

```python
from pathlib import Path
import subprocess

import pytest

import app.cutover as cutover
from app.cutover import promote
from app.cutover_build import CutoverCommittedError, CutoverError, isolated_worktree
from tests.conftest import git_head, git_status_bytes, git_vault


def commit_in(root: Path, message: str) -> str:
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=t@e", "-c", "user.name=t",
         "commit", "-q", "-m", message],
        cwd=root, check=True, capture_output=True,
    )
    return git_head(root)


def build_a_commit(vault: Path, head: str, filename: str = "a.md") -> str:
    with isolated_worktree(vault, head) as scratch:
        (scratch / filename).write_text("changed\n", encoding="utf-8")
        return commit_in(scratch, "cutover")


def test_promotion_fast_forwards_the_live_vault(tmp_path: Path):
    vault = git_vault(tmp_path / "vault", {"a.md": "x\n"})
    head = git_head(vault)
    built = build_a_commit(vault, head)

    assert promote(vault, built, head, git_status_bytes(vault), []) == built
    assert (vault / "a.md").read_text(encoding="utf-8") == "changed\n"


def test_promotion_takes_the_shared_action_lock(tmp_path: Path, monkeypatch):
    vault = git_vault(tmp_path / "vault", {"a.md": "x\n"})
    head = git_head(vault)
    built = build_a_commit(vault, head)
    taken: list[Path] = []

    real = cutover.action_lock

    import contextlib

    @contextlib.contextmanager
    def recording(target):
        taken.append(target)
        with real(target):
            yield

    monkeypatch.setattr(cutover, "action_lock", recording)
    promote(vault, built, head, git_status_bytes(vault), [])

    assert taken == [vault]


def test_promotion_refuses_a_moved_head(tmp_path: Path):
    vault = git_vault(tmp_path / "vault", {"a.md": "x\n"})
    head = git_head(vault)
    built = build_a_commit(vault, head)
    (vault / "b.md").write_text("y\n", encoding="utf-8")
    moved = commit_in(vault, "concurrent")

    with pytest.raises(CutoverError):
        promote(vault, built, head, git_status_bytes(vault), [])
    assert git_head(vault) == moved


def test_promotion_refuses_a_changed_status(tmp_path: Path):
    vault = git_vault(tmp_path / "vault", {"a.md": "x\n"})
    head = git_head(vault)
    captured = git_status_bytes(vault)
    built = build_a_commit(vault, head)
    (vault / "stray.md").write_text("s\n", encoding="utf-8")

    with pytest.raises(CutoverError):
        promote(vault, built, head, captured, [])
    assert git_head(vault) == head


def test_promotion_repeats_the_ignored_content_check(tmp_path: Path):
    vault = git_vault(
        tmp_path / "vault", {".gitignore": ".sensitive/\n", "ab/n.md": "x\n"}
    )
    head = git_head(vault)
    built = build_a_commit(vault, head, filename="ab/other.md")
    captured = git_status_bytes(vault)
    (vault / "ab" / ".sensitive").mkdir()
    (vault / "ab" / ".sensitive" / "s.md").write_text("s\n", encoding="utf-8")

    with pytest.raises(CutoverError):
        promote(vault, built, head, captured, ["ab"])
    assert git_head(vault) == head


def test_promotion_leaves_an_obstructing_untracked_file_intact(tmp_path: Path):
    vault = git_vault(tmp_path / "vault", {"a.md": "x\n"})
    head = git_head(vault)
    built = build_a_commit(vault, head, filename="new.md")
    (vault / "new.md").write_text("mine\n", encoding="utf-8")

    with pytest.raises(CutoverError):
        promote(vault, built, head, git_status_bytes(vault), [])
    assert (vault / "new.md").read_text(encoding="utf-8") == "mine\n"
    assert git_head(vault) == head


def test_a_failed_promotion_is_not_reported_as_committed(tmp_path: Path):
    vault = git_vault(tmp_path / "vault", {"a.md": "x\n"})
    head = git_head(vault)
    built = build_a_commit(vault, head)
    (vault / "stray.md").write_text("s\n", encoding="utf-8")

    with pytest.raises(CutoverError) as caught:
        promote(vault, built, head, git_status_bytes(vault), [])
    assert not isinstance(caught.value, CutoverCommittedError)


def test_a_commit_that_cannot_be_confirmed_is_reported_as_committed(
    tmp_path: Path, monkeypatch
):
    vault = git_vault(tmp_path / "vault", {"a.md": "x\n"})
    head = git_head(vault)
    built = build_a_commit(vault, head)
    captured = git_status_bytes(vault)
    state = {"merged": False}

    real_run = subprocess.run

    def flaky(args, **kwargs):
        if args[:2] == ["git", "merge"]:
            state["merged"] = True
            return real_run(args, **kwargs)
        if state["merged"] and args[:3] == ["git", "rev-parse", "HEAD"]:
            raise OSError("injected confirmation failure")
        return real_run(args, **kwargs)

    monkeypatch.setattr(cutover.subprocess, "run", flaky)

    with pytest.raises(CutoverCommittedError):
        promote(vault, built, head, captured, [])


def test_a_commit_confirmed_as_the_wrong_head_is_reported_as_committed_but_unresolved(
    tmp_path: Path, monkeypatch
):
    vault = git_vault(tmp_path / "vault", {"a.md": "x\n"})
    head = git_head(vault)
    built = build_a_commit(vault, head)
    captured = git_status_bytes(vault)
    real_run = subprocess.run
    merged = {"done": False}

    def wrong_head(args, **kwargs):
        if args[:2] == ["git", "merge"]:
            result = real_run(args, **kwargs)
            merged["done"] = True
            return result
        if merged["done"] and args[:3] == ["git", "rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(args, 0, stdout=head + "\n", stderr="")
        return real_run(args, **kwargs)

    monkeypatch.setattr(cutover.subprocess, "run", wrong_head)

    with pytest.raises(CutoverCommittedError, match="does not equal the built commit"):
        promote(vault, built, head, captured, [])
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `uv run python -m pytest -q tests/test_cutover_promotion.py`
Expected: collection error, `ModuleNotFoundError: No module named 'app.cutover'`.

- [ ] **Step 3: Write the minimal implementation**

Create `app/cutover.py`:

```python
"""cutover.py — promotion under quiesce and the shared action lock, and the CLI.

`git merge --ff-only` advances the ref atomically, but updating the working
tree is many creates, deletes, and renames. A process reading the vault during
that window can observe a half-migrated tree, so every writer must be stopped
first. The shared action lock is taken as well, for the cooperative OneOS
writers it does govern — an addition to the quiesce, never a substitute, since
Hermes, parsers, and adapters need not take it.
"""
from __future__ import annotations

from pathlib import Path
import subprocess

from .cutover_build import (
    CutoverCommittedError,
    CutoverError,
    build_cutover,
    git,
)
from .cutover_inventory import require_clean_entities
from .git_transaction import (
    ActionLockCleanupFailure,
    GitTransactionFailure,
    VaultBusyError,
    action_lock,
)


def _status_bytes(vault: Path) -> bytes:
    return subprocess.run(
        ["git", "status", "--porcelain=v2", "--untracked-files=all"],
        cwd=vault, check=True, capture_output=True,
    ).stdout


def promote(
    vault: Path,
    built_commit: str,
    source_head: str,
    expected_status: bytes,
    affected_entities: list[str],
) -> str:
    """Fast-forward the live vault to the commit built in isolation.

    The caller must already have stopped OneOS, Hermes, and every parser and
    adapter. This function takes the shared action lock, repeats every
    precheck, and only then moves the ref.
    """
    committed = False
    try:
        with action_lock(vault):
            if git(vault, "rev-parse", "HEAD").strip() != source_head:
                raise CutoverError(
                    "live HEAD moved since the build; re-run from inventory"
                )
            if _status_bytes(vault) != expected_status:
                raise CutoverError(
                    "live status changed since the build; re-run from inventory"
                )
            # Repeated here because ignored content can appear at any moment,
            # and a linked worktree could never have carried it.
            require_clean_entities(vault, affected_entities)

            completed = subprocess.run(
                ["git", "merge", "--ff-only", built_commit],
                cwd=vault, check=False, capture_output=True, text=True,
            )
            if completed.returncode != 0:
                raise CutoverError(
                    "fast-forward promotion refused; the vault is unchanged"
                )
            committed = True
            try:
                confirmed = subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=vault, check=True, capture_output=True, text=True,
                ).stdout.strip()
                if confirmed != built_commit:
                    raise CutoverCommittedError(
                        "the cutover command succeeded but confirmed HEAD does not "
                        "equal the built commit; do not retry"
                    )
                return confirmed
            except (OSError, subprocess.CalledProcessError) as exc:
                raise CutoverCommittedError(
                    "the cutover committed but its id could not be read; "
                    "do not retry"
                ) from exc
    except ActionLockCleanupFailure as exc:
        if not committed:
            raise CutoverError(
                "shared action lock cleanup failed before the cutover committed"
            ) from exc
        raise CutoverCommittedError(
            "the cutover committed but the action lock could not be released; "
            "do not retry"
        ) from exc
    except VaultBusyError as exc:
        raise CutoverError(
            "vault is busy; another OneOS action is already running"
        ) from exc
    except GitTransactionFailure as exc:
        if committed:
            raise CutoverCommittedError(
                "the cutover committed but the lock layer failed; do not retry"
            ) from exc
        raise CutoverError(
            "shared action lock is unavailable; the cutover was not started"
        ) from exc
```

- [ ] **Step 4: Run the test and confirm it passes**

Run: `uv run python -m pytest -q tests/test_cutover_promotion.py`
Expected: PASS, 9 tests.

- [ ] **Step 5: Commit**

```bash
git add app/cutover.py tests/test_cutover_promotion.py
git commit -m "feat: promote the cutover under the shared action lock"
```

---

### Task 13: End-to-end preservation — prose, receipts, proposals, pre-restart revert

**Files:**
- Modify: `tests/test_cutover_build.py`
- Verify: `app/cutover_build.py`, `app/cutover.py`

**Interfaces:** none new. These are the design's acceptance tests for receipts and proposals, which the rejected plan omitted entirely.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cutover_build.py`:

```python
import hashlib

from app.action_receipts import make_action_receipt, render_action_receipt, resolve_head_receipt
from app.cutover import promote


def promoted(vault: Path) -> BuildResult:
    head = git_head(vault)
    raw, record = approved(vault)
    result = build_cutover(vault, raw, record)
    promote(vault, result.commit, head, git_status_bytes(vault), ["ab"])
    return result


def test_one_commit_is_produced_and_revert_restores_everything(tmp_path: Path):
    vault = cutover_vault(tmp_path / "vault")
    before = git_count_commits(vault)

    result = promoted(vault)

    assert git_count_commits(vault) == before + 1
    assert (vault / "ab-entity" / "00-inbox" / "note.md").is_file()

    # This is the approved rollback window: promotion has completed but every
    # writer is still quiesced, so no later SQLite write can be overwritten.
    subprocess.run(
        ["git", "-c", "user.email=t@e", "-c", "user.name=t",
         "revert", "--no-edit", result.commit],
        cwd=vault, check=True, capture_output=True,
    )

    assert (vault / "ab" / "00-inbox" / "note.md").is_file()
    conn = sqlite3.connect(vault / "ab" / "books.db")
    try:
        assert conn.execute("SELECT product FROM ledger").fetchall() == [("q7",)]
        assert conn.execute("SELECT member FROM roster").fetchall() == [("m7",)]
    finally:
        conn.close()


def test_ordinary_prose_containing_a_short_identifier_is_untouched(tmp_path: Path):
    vault = cutover_vault(tmp_path / "vault")
    promoted(vault)

    note = (vault / "ab-entity" / "00-inbox" / "note.md").read_text(encoding="utf-8")
    assert "entity: ab-entity" in note
    assert "product: q7-product" in note
    assert "member: m7-member" in note
    assert "the ab word" in note


def test_the_database_is_updated_before_the_entity_directory_moves(tmp_path: Path):
    vault = cutover_vault(tmp_path / "vault")
    promoted(vault)

    moved = vault / "ab-entity" / "books.db"
    assert moved.is_file()
    conn = sqlite3.connect(moved)
    try:
        assert conn.execute("SELECT product FROM ledger").fetchall() == [
            ("q7-product",)
        ]
        assert conn.execute("SELECT member FROM roster").fetchall() == [
            ("m7-member",)
        ]
    finally:
        conn.close()


def test_the_promoted_database_blob_is_the_exact_verified_build_artifact(
    tmp_path: Path,
):
    vault = cutover_vault(tmp_path / "vault")

    result = promoted(vault)

    built_blob = git(
        vault, "rev-parse", f"{result.commit}:ab-entity/books.db"
    ).strip()
    promoted_blob = git(vault, "rev-parse", "HEAD:ab-entity/books.db").strip()
    assert promoted_blob == built_blob
    assert git(vault, "rev-parse", "HEAD^{tree}").strip() == result.tree


def test_a_spent_proposal_id_is_still_refused_after_an_entity_cutover(tmp_path: Path):
    vault = cutover_vault(tmp_path / "vault")
    proposal_id = "20260826T120000-" + "ab" * 16
    receipt = make_action_receipt(proposal_id, "a" * 64, "approval")
    store = vault / "ab" / "outbox" / ".receipts"
    store.mkdir(parents=True)
    (store / f"{proposal_id}.yaml").write_bytes(render_action_receipt(receipt))
    commit_in(vault, "add receipt")

    promoted(vault)

    resolution = resolve_head_receipt(vault, "ab-entity", proposal_id)
    assert resolution.error is None
    assert resolution.receipt == receipt


def test_proposal_prefixes_are_rewritten_and_a_pre_cutover_token_is_refused(
    tmp_path: Path,
):
    vault = cutover_vault(tmp_path / "vault")
    outbox = vault / "ab" / "outbox"
    outbox.mkdir(parents=True)
    proposal_id = "20260826T120000-" + "cd" * 16
    record_path = outbox / f"{proposal_id}.yaml"
    record_path.write_text(
        f"id: {proposal_id}\n"
        "action: classify\n"
        "entity: ab\n"
        "src: ab/00-inbox/active/x.md\n"
        "dst: ab/09-marketing/active/x.md\n"
        'opaque: "keep: [x]"  # exact\n',
        encoding="utf-8",
    )
    before_token = hashlib.sha256(record_path.read_bytes()).hexdigest()
    commit_in(vault, "add proposal")

    promoted(vault)

    moved = vault / "ab-entity" / "outbox" / record_path.name
    text = moved.read_text(encoding="utf-8")
    assert "entity: ab-entity" in text
    assert "src: ab-entity/00-inbox/active/x.md" in text
    assert "dst: ab-entity/09-marketing/active/x.md" in text
    assert 'opaque: "keep: [x]"  # exact' in text, (
        "proposal rewrite altered bytes outside the approved fields"
    )
    # S7 binds an approval to exact proposal bytes, so a token issued before the
    # cutover no longer matches. Failing closed is correct.
    assert hashlib.sha256(moved.read_bytes()).hexdigest() != before_token
```

- [ ] **Step 2: Run the end-to-end acceptance tests**

Run: `uv run python -m pytest -q tests/test_cutover_build.py -k "revert or prose or before_the_entity or exact_verified_build_artifact or spent or prefixes"`
Expected: PASS, 6 tests. These exercise the full build-and-promote path for
the first time. A failure is an implementation defect in Tasks 11–12: stop,
identify the failing boundary, add the narrow regression at that earlier task,
and correct the implementation before continuing. Never weaken these
acceptance assertions.

- [ ] **Step 3: Re-run after any correction**

Run the same command.
Expected: PASS, 6 tests.

- [ ] **Step 4: Commit**

```bash
git add tests/test_cutover_build.py
git commit -m "test: pin end-to-end cutover preservation"
```

---

### Task 14: The fail-open guard proved behaviourally

**Files:**
- Modify: `tests/test_cutover_build.py`
- Verify: `app/cutover_locations.py`

**Interfaces:** none new.

The rejected plan asserted on policy *text*. `AGENTS.md` requires proving a `.sensitive/` read is still **denied**. This task adds a minimal policy evaluator to the test module and asserts the denial itself, so a half-rewritten rule fails the test even when the text looks plausible.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cutover_build.py`:

```python
import re as _re


def _glob_to_regex(pattern: str) -> _re.Pattern[str]:
    out, index = [], 0
    while index < len(pattern):
        if pattern.startswith("**/", index):
            out.append("(?:.*/)?")
            index += 3
        elif pattern.startswith("**", index):
            out.append(".*")
            index += 2
        elif pattern[index] == "*":
            out.append("[^/]*")
            index += 1
        else:
            out.append(_re.escape(pattern[index]))
            index += 1
    return _re.compile("^" + "".join(out) + "$")


def policy_allows_read(policy_text: str, path: str) -> bool:
    """Minimal allow/except evaluator: default deny; an allow rule grants a
    path only when it matches `paths:` and no `except:` pattern."""
    document = yaml.safe_load(policy_text) or {}
    for actor in (document.get("actors") or {}).values():
        for rule in (actor or {}).get("allow", []) or []:
            if rule.get("action") not in (None, "read"):
                continue
            if not any(
                _glob_to_regex(p).match(path) for p in rule.get("paths", []) or []
            ):
                continue
            if any(
                _glob_to_regex(p).match(path) for p in rule.get("except", []) or []
            ):
                continue
            return True
    return False


def test_sensitive_reads_are_denied_before_and_after_the_cutover(tmp_path: Path):
    vault = cutover_vault(tmp_path / "vault")
    policy_path = vault / "_system" / "scripts" / "action-policy.yaml"

    before = policy_path.read_text(encoding="utf-8")
    assert policy_allows_read(before, "ab/00-inbox/note.md")
    assert not policy_allows_read(before, "ab/.sensitive/secret.md")

    promoted(vault)

    after = policy_path.read_text(encoding="utf-8")
    assert policy_allows_read(after, "ab-entity/00-inbox/note.md")
    assert not policy_allows_read(after, "ab-entity/.sensitive/secret.md")


def test_former_slugs_is_written_only_on_entity_and_product_keys(tmp_path: Path):
    vault = cutover_vault(tmp_path / "vault")
    promoted(vault)

    system = vault / "_system"
    entities = (system / "entities.yaml").read_text(encoding="utf-8")
    products = (system / "products.yaml").read_text(encoding="utf-8")
    members = (system / "members.yaml").read_text(encoding="utf-8")
    workspaces = (system / "workspaces.yaml").read_text(encoding="utf-8")

    assert "former_slugs: [ab]" in entities
    assert "former_slugs: [q7]" in products
    assert "former_slugs" not in members
    assert "former_slugs" not in workspaces
    assert "id: m7-member" in members
    assert "id: w7-workspace" in workspaces


def test_existing_former_slugs_are_preserved_and_no_duplicate_key_is_created(
    tmp_path: Path,
):
    vault = cutover_vault(tmp_path / "vault")
    entities = vault / "_system" / "entities.yaml"
    entities.write_text(
        "entities:\n  ab:\n    former_slugs: [older]\n    label: A\n",
        encoding="utf-8",
    )
    commit_in(vault, "seed provenance")

    promoted(vault)

    text = entities.read_text(encoding="utf-8")
    assert text.count("former_slugs:") == 1
    assert "former_slugs: [older, ab]" in text


def test_a_product_kind_workspace_id_takes_the_workspace_suffix(tmp_path: Path):
    vault = cutover_vault(tmp_path / "vault")
    promoted(vault)

    workspaces = (vault / "_system" / "workspaces.yaml").read_text(encoding="utf-8")
    assert "id: w7-workspace" in workspaces
    assert "product: q7-product" in workspaces
    assert "id: q7-product" not in workspaces
```

- [ ] **Step 2: Run the tests and confirm they pass for the right reason**

Run: `uv run python -m pytest -q tests/test_cutover_build.py -k "sensitive or former_slugs or product_kind"`
Expected: PASS, 4 tests. If any fails, fix the implementation, never the assertion.

- [ ] **Step 3: Commit**

```bash
git add tests/test_cutover_build.py
git commit -m "test: prove the fail-open guard behaviourally"
```

---

### Task 15: The term-collection pin

**Files:**
- Modify: `tests/test_public_repo_audit.py`

**Interfaces:** none new.

The design's claim that the combined history audit comes back **clean** after the cutover rests entirely on `load_instance_terms` seeding from current registry keys and ids only. Retired identifiers stop being terms, so the history scan never looks for them. That premise must be pinned **before** the private migration runs, not after.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_public_repo_audit.py`:

```python
def test_term_collection_reads_only_registry_keys_and_ids(tmp_path: Path):
    vault = synthetic_vault(tmp_path / "vault", entity="alpha")
    system = vault / "_system"
    system.joinpath("entities.yaml").write_text(
        "entities:\n  alpha:\n    label: A\n    former_slugs: [ab]\n",
        encoding="utf-8",
    )
    system.joinpath("products.yaml").write_text(
        "products:\n  alpha:\n    bravo:\n      former_slugs: [q7]\n",
        encoding="utf-8",
    )

    long_terms, short_terms = load_instance_terms(vault)

    assert "alpha" in long_terms
    assert "bravo" in long_terms
    # Retained provenance must never be seeded, or the post-cutover history
    # audit would look for retired identifiers again and go red.
    assert "ab" not in short_terms and "ab" not in long_terms
    assert "q7" not in short_terms and "q7" not in long_terms
```

Add `load_instance_terms` to that module's existing `from tools.public_repo_audit import ...` line.

- [ ] **Step 2: Run the test and confirm it passes for the right reason**

Run: `uv run python -m pytest -q tests/test_public_repo_audit.py::test_term_collection_reads_only_registry_keys_and_ids`
Expected: PASS. It pins existing behaviour; mutation 24 in Task 17 proves it is a real control.

- [ ] **Step 3: Commit**

```bash
git add tests/test_public_repo_audit.py
git commit -m "test: pin publication audit term collection"
```

---

### Task 16: The CLI — real inventory, real dry run, gated apply

**Files:**
- Modify: `app/cutover.py`
- Test: `tests/test_cutover_cli.py`

**Interfaces:**
- Produces: `main(argv)` with subcommands `inventory`, `dry-run`, `apply`.

`inventory` enumerates identifiers, collisions, advisory occurrences, unmigratable content, and database schemas. `dry-run` **builds in isolation** and renders the complete diff and the database row-change summary, then discards. `apply` additionally requires `--i-have-quiesced-all-writers`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_cutover_cli.py`:

```python
from contextlib import contextmanager
from pathlib import Path

import yaml

import app.cutover as cutover
from app.cutover import main
from tests.conftest import git_head, git_is_clean, git_vault
from tests.test_cutover_build import approved, commit_in, cutover_vault


def write_artifacts(tmp_path: Path, raw: bytes, record) -> tuple[Path, Path]:
    manifest_path = tmp_path / "manifest.yaml"
    record_path = tmp_path / "record.yaml"
    manifest_path.write_bytes(raw)
    record_path.write_text(
        yaml.safe_dump(
            {"manifest_sha256": record.manifest_sha256, "approved_by": "owner"}
        ),
        encoding="utf-8",
    )
    return manifest_path, record_path


def test_inventory_reports_identifiers_advisory_and_schema(tmp_path: Path, capsys):
    vault = cutover_vault(tmp_path / "vault")

    code = main(["inventory", "--vault-root", str(vault)])
    out = capsys.readouterr().out

    assert code == 0
    assert f"source HEAD: {git_head(vault)}" in out
    assert "entity: ab -> ab-entity" in out
    assert "workspace: w7 -> w7-workspace" in out
    assert "advisory" in out
    assert "ab/books.db ledger" in out
    assert "path=ab/books.db table=ledger column=product axis=product" in out
    assert "count=1" in out
    assert "UNPROVEN" in out
    assert git_is_clean(vault)


def test_inventory_writes_nothing(tmp_path: Path):
    vault = cutover_vault(tmp_path / "vault")
    head = git_head(vault)

    main(["inventory", "--vault-root", str(vault)])

    assert git_head(vault) == head
    assert git_is_clean(vault)


def test_inventory_reads_tracked_evidence_from_the_captured_head(
    tmp_path: Path, monkeypatch, capsys
):
    vault = cutover_vault(tmp_path / "vault")
    registry = vault / "_system" / "entities.yaml"
    original = registry.read_bytes()
    real = cutover.isolated_worktree

    @contextmanager
    def live_differs_while_snapshot_is_read(root, source_head):
        with real(root, source_head) as snapshot:
            registry.write_text(
                "entities:\n  zz:\n    label: Later\n", encoding="utf-8"
            )
            try:
                yield snapshot
            finally:
                registry.write_bytes(original)

    monkeypatch.setattr(
        cutover, "isolated_worktree", live_differs_while_snapshot_is_read
    )

    assert main(["inventory", "--vault-root", str(vault)]) == 0
    out = capsys.readouterr().out
    assert "entity: ab -> ab-entity" in out
    assert "entity: zz" not in out


def test_inventory_discards_results_when_live_status_changes_before_return(
    tmp_path: Path, monkeypatch, capsys
):
    vault = cutover_vault(tmp_path / "vault")
    real = cutover.isolated_worktree

    @contextmanager
    def live_changes_before_return(root, source_head):
        with real(root, source_head) as snapshot:
            yield snapshot
        (vault / "changed-after-snapshot.txt").write_text(
            "late\n", encoding="utf-8"
        )

    monkeypatch.setattr(cutover, "isolated_worktree", live_changes_before_return)

    assert main(["inventory", "--vault-root", str(vault)]) == 1
    assert "clean status" in capsys.readouterr().out


def test_inventory_refuses_a_dirty_live_vault(tmp_path: Path, capsys):
    vault = cutover_vault(tmp_path / "vault")
    (vault / "untracked.txt").write_text("not approved\n", encoding="utf-8")

    code = main(["inventory", "--vault-root", str(vault)])

    assert code == 1
    assert "clean status" in capsys.readouterr().out


def test_dry_run_builds_and_shows_the_diff_without_touching_the_vault(
    tmp_path: Path, capsys
):
    vault = cutover_vault(tmp_path / "vault")
    head = git_head(vault)
    manifest_path, record_path = write_artifacts(tmp_path, *approved(vault))

    code = main(
        ["dry-run", "--vault-root", str(vault),
         "--manifest", str(manifest_path), "--approval", str(record_path)]
    )
    out = capsys.readouterr().out

    assert code == 0
    assert "diff --git" in out
    assert "path=ab/books.db table=ledger column=product axis=product" in out
    assert "old=q7 new=q7-product count=1" in out
    assert "DRY RUN" in out
    assert git_head(vault) == head
    assert git_is_clean(vault)


def test_dry_run_and_apply_build_identical_trees_for_one_source_head(
    tmp_path: Path, monkeypatch
):
    vault = cutover_vault(tmp_path / "vault")
    manifest_path, record_path = write_artifacts(tmp_path, *approved(vault))
    results = []
    real = cutover.build_cutover

    def recording(*args, **kwargs):
        result = real(*args, **kwargs)
        results.append(result)
        return result

    monkeypatch.setattr(cutover, "build_cutover", recording)

    assert main(
        ["dry-run", "--vault-root", str(vault),
         "--manifest", str(manifest_path), "--approval", str(record_path)]
    ) == 0
    assert main(
        ["apply", "--vault-root", str(vault),
         "--manifest", str(manifest_path), "--approval", str(record_path),
         "--i-have-quiesced-all-writers"]
    ) == 0

    assert len(results) == 2
    assert results[0].source_head == results[1].source_head
    assert results[0].tree == results[1].tree


def test_dry_run_reports_a_refusal_instead_of_a_diff(tmp_path: Path, capsys):
    vault = cutover_vault(tmp_path / "vault")
    raw, record = approved(vault, dispositions=())
    manifest_path, record_path = write_artifacts(tmp_path, raw, record)

    code = main(
        ["dry-run", "--vault-root", str(vault),
         "--manifest", str(manifest_path), "--approval", str(record_path)]
    )

    assert code == 1
    assert "ABORTED" in capsys.readouterr().out
    assert git_is_clean(vault)


def test_apply_requires_the_quiesce_acknowledgement(tmp_path: Path, capsys):
    vault = cutover_vault(tmp_path / "vault")
    head = git_head(vault)
    manifest_path, record_path = write_artifacts(tmp_path, *approved(vault))

    code = main(
        ["apply", "--vault-root", str(vault),
         "--manifest", str(manifest_path), "--approval", str(record_path)]
    )

    assert code == 1
    assert "quiesced" in capsys.readouterr().out
    assert git_head(vault) == head


def test_apply_promotes_when_acknowledged(tmp_path: Path, capsys):
    vault = cutover_vault(tmp_path / "vault")
    head = git_head(vault)
    manifest_path, record_path = write_artifacts(tmp_path, *approved(vault))

    code = main(
        ["apply", "--vault-root", str(vault),
         "--manifest", str(manifest_path), "--approval", str(record_path),
         "--i-have-quiesced-all-writers"]
    )

    assert code == 0
    out = capsys.readouterr().out
    assert "DONE" in out
    assert "before writers restart" in out
    assert git_head(vault) != head
    assert (vault / "ab-entity").is_dir()
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `uv run python -m pytest -q tests/test_cutover_cli.py`
Expected: collection error, `ImportError: cannot import name 'main'`.

- [ ] **Step 3: Write the minimal implementation**

Append to `app/cutover.py`:

```python
import argparse

import yaml

from .cutover_build import isolated_worktree, mappings_in_order
from .cutover_db import (
    database_reference_inventory,
    database_schema_inventory,
)
from .cutover_inventory import (
    check_collisions,
    existing_identifiers,
    proposed_mappings,
    require_clean_entities,
    require_clean_status,
)
from .cutover_locations import advisory_occurrences
from .cutover_manifest import ApprovalRecord, load_manifest, verify_manifest


def _load_approval(path: Path) -> ApprovalRecord:
    document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(document, dict):
        raise CutoverError("approval record must be a mapping")
    try:
        return ApprovalRecord(
            manifest_sha256=document["manifest_sha256"],
            approved_by=document["approved_by"],
        )
    except KeyError as exc:
        raise CutoverError("approval record is missing a required field") from exc


def _run_inventory(vault: Path) -> int:
    require_clean_status(vault)
    source_head = git(vault, "rev-parse", "HEAD").strip()
    with isolated_worktree(vault, source_head) as snapshot:
        mappings = proposed_mappings(snapshot)
        check_collisions(mappings, existing_identifiers(snapshot))
        affected = sorted({m.old for m in mappings if m.axis == "entity"})
        occurrences = advisory_occurrences(snapshot, mappings)
        schemas = database_schema_inventory(snapshot)
        references = database_reference_inventory(snapshot, mappings)

    require_clean_entities(vault, affected)
    if git(vault, "rev-parse", "HEAD").strip() != source_head:
        raise CutoverError("live HEAD changed during inventory; discard the result")
    require_clean_status(vault)
    require_clean_entities(vault, affected)

    print(f"source HEAD: {source_head}")
    for mapping in mappings:
        print(f"{mapping.axis}: {mapping.old} -> {mapping.new}")
    for occurrence in occurrences:
        print(
            f"advisory: {occurrence.path}:{occurrence.line} "
            f"({occurrence.axis}:{occurrence.old} #{occurrence.ordinal} "
            f"context={occurrence.context_sha256}) — disposition required"
        )
    for path, tables in schemas.items():
        for table, columns in tables.items():
            print(f"database {path} {table}: {', '.join(columns)}")
    for reference in references:
        print(
            "database candidate (UNPROVEN — owner approval required): "
            f"path={reference.path} table={reference.table} "
            f"column={reference.column} axis={reference.axis} "
            f"old={reference.old} count={reference.count}"
        )
    print("[INVENTORY] read-only; nothing was written")
    return 0


def _run_dry_run(vault: Path, manifest_bytes: bytes, record: ApprovalRecord) -> int:
    """Build the real result in isolation and render it, then discard.

    A dry run that only printed the mapping table would not be a preview: the
    planners read from disk, so the only faithful preview is the tree the apply
    would actually produce.
    """
    verify_manifest(manifest_bytes, record)
    manifest = load_manifest(manifest_bytes)
    result = build_cutover(vault, manifest_bytes, record)
    print(git(vault, "diff", f"{manifest.source_head}..{result.commit}"))
    for change in result.database_changes:
        print(
            "database rows changed: "
            f"path={change.path} table={change.table} column={change.column} "
            f"axis={change.axis} old={change.old} new={change.new} "
            f"count={change.count}"
        )
    print(
        "database rows changed (total): "
        f"{sum(item.count for item in result.database_changes)}"
    )
    for mapping in mappings_in_order(manifest):
        print(f"{mapping.axis}: {mapping.old} -> {mapping.new}")
    print("\n[DRY RUN] re-run with apply to execute")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="oneos cutover",
        description="Raise registry identifiers to the five-character floor.",
    )
    parser.add_argument("command", choices=("inventory", "dry-run", "apply"))
    parser.add_argument("--vault-root", default=".")
    parser.add_argument("--manifest")
    parser.add_argument("--approval")
    parser.add_argument(
        "--i-have-quiesced-all-writers",
        action="store_true",
        help=(
            "confirm OneOS, Hermes, and every parser and adapter are stopped; "
            "the working-tree update is not atomic and the action lock does "
            "not govern them"
        ),
    )
    args = parser.parse_args(argv)
    vault = Path(args.vault_root).expanduser().resolve()

    try:
        if args.command == "inventory":
            return _run_inventory(vault)

        if not args.manifest or not args.approval:
            print("[ABORTED] --manifest and --approval are required")
            return 1
        manifest_bytes = Path(args.manifest).read_bytes()
        record = _load_approval(Path(args.approval))

        if args.command == "dry-run":
            return _run_dry_run(vault, manifest_bytes, record)

        if not args.i_have_quiesced_all_writers:
            print(
                "[ABORTED] refusing to promote without "
                "--i-have-quiesced-all-writers: stop OneOS, Hermes, and every "
                "parser and adapter first"
            )
            return 1

        manifest = load_manifest(manifest_bytes)
        source_head = git(vault, "rev-parse", "HEAD").strip()
        expected_status = _status_bytes(vault)
        affected = [m.old for m in manifest.mappings if m.axis == "entity"]
        result = build_cutover(vault, manifest_bytes, record)
        promoted_id = promote(
            vault, result.commit, source_head, expected_status, affected
        )
        print(f"[DONE] cutover promoted as {promoted_id}")
        print(
            "[ROLLBACK WINDOW] keep writers stopped while verifying. Plain git "
            "revert is safe only before writers restart; later rollback needs a "
            "separately reviewed database recovery migration."
        )
        return 0
    except CutoverCommittedError as exc:
        print(f"[COMMITTED] {exc}")
        return 2
    except Exception as exc:  # noqa: BLE001 — CLI boundary
        print(f"[ABORTED] {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the test and confirm it passes**

Run: `uv run python -m pytest -q tests/test_cutover_cli.py`
Expected: PASS, 10 tests.

- [ ] **Step 5: Commit**

```bash
git add app/cutover.py tests/test_cutover_cli.py
git commit -m "feat: add the cutover CLI with a real inventory and dry run"
```

---

### Task 17: Design-test mapping, mutation campaign, and closing evidence

**Files:**
- Create: `docs/superpowers/plans/2026-08-26-short-identifier-cutover-mutation-ledger.md`
- Verify: every module from Tasks 1–16

**Interfaces:** none. This task produces the coverage mapping and the RED→GREEN evidence the design makes mandatory.

- [ ] **Step 1: Confirm every design acceptance test has an exact node**

Every requirement in the design's "Public synthetic tests" section maps to an
exact node below. Run each full node id and record its result. A requirement
whose node does not exist is a gap, not a rounding error: stop and revise this
plan before proceeding rather than inventing a test during the campaign.

| Design requirement | Exact test node |
|---|---|
| Mapping — determinism, suffix per axis, floor untouched, output meets floor | `tests/test_cutover_identifiers.py::test_mapping_is_deterministic_and_appends_the_axis_suffix`, `tests/test_cutover_identifiers.py::test_suffix_for_each_axis`, `tests/test_cutover_identifiers.py::test_mapping_refuses_an_identifier_that_already_meets_the_floor`, `tests/test_cutover_identifiers.py::test_every_output_satisfies_the_floor` |
| One field, one axis — partition | `tests/test_cutover_locations.py::test_no_file_kind_and_field_pair_appears_under_two_axes` |
| One field, one axis — product never claims a workspace id | `tests/test_cutover_locations.py::test_product_axis_never_claims_a_workspace_id`, `tests/test_cutover_build.py::test_a_product_kind_workspace_id_takes_the_workspace_suffix` |
| Collisions — class 1 refuses | `tests/test_cutover_inventory.py::test_a_new_value_colliding_with_an_existing_identifier_is_refused` |
| Collisions — class 2 refuses | `tests/test_cutover_inventory.py::test_duplicate_inputs_on_one_axis_are_refused` |
| Collisions — class 3 permitted | `tests/test_cutover_inventory.py::test_one_literal_on_two_axes_is_permitted` |
| Build collision authority — manifest source HEAD, never mutable live tracked bytes | `tests/test_cutover_build.py::test_build_collision_check_uses_the_manifest_source_head` |
| Scoped replacement — prose untouched | `tests/test_cutover_build.py::test_ordinary_prose_containing_a_short_identifier_is_untouched`, `tests/test_cutover_locations.py::test_ordinary_prose_containing_an_old_identifier_is_not_a_residual` |
| Scoped replacement — containing value unchanged | `tests/test_cutover_locations.py::test_front_matter_rewrite_ignores_a_value_that_merely_contains_the_term` |
| Advisory report — reported not rewritten | `tests/test_cutover_locations.py::test_advisory_reports_a_bare_token_outside_the_enumerated_locations` |
| Advisory report — every token has a stable identity; display-line shifts tolerated; context changes refused | `tests/test_cutover_locations.py::test_advisory_identity_distinguishes_two_tokens_on_one_line`, `tests/test_cutover_build.py::test_post_advisory_identity_survives_a_display_line_shift`, `tests/test_cutover_build.py::test_post_advisory_identity_rejects_same_count_with_changed_context` |
| Advisory report — undispositioned aborts | `tests/test_cutover_build.py::test_build_refuses_an_undispositioned_advisory_occurrence` |
| Advisory report — structural without typed location aborts | `tests/test_cutover_build.py::test_build_refuses_a_structural_disposition_naming_no_typed_location` |
| Clean inventory status | `tests/test_cutover_inventory.py::test_inventory_requires_a_globally_clean_tracked_and_untracked_status`, `tests/test_cutover_cli.py::test_inventory_refuses_a_dirty_live_vault` |
| Inventory — tracked evidence comes from one immutable HEAD snapshot and live state is rechecked | `tests/test_cutover_cli.py::test_inventory_reads_tracked_evidence_from_the_captured_head`, `tests/test_cutover_cli.py::test_inventory_discards_results_when_live_status_changes_before_return` |
| Ignored and untracked — stops at inventory and build | `tests/test_cutover_inventory.py::test_require_clean_entities_raises_for_an_affected_entity`, `tests/test_cutover_build.py::test_build_refuses_an_entity_with_ignored_content` |
| Ignored and untracked — stops at the promotion precheck | `tests/test_cutover_promotion.py::test_promotion_repeats_the_ignored_content_check` |
| Ignored and untracked — unaffected entity ignored | `tests/test_cutover_inventory.py::test_require_clean_entities_ignores_an_unaffected_entity` |
| Already-suffixed refuses | `tests/test_cutover_identifiers.py::test_mapping_refuses_an_already_suffixed_identifier` |
| Floor enforcement at every validation site | **Stage B** — deferred with the private migration; see "Deferred to Stage B" |
| One commit and pre-writer-restart revert | `tests/test_cutover_build.py::test_one_commit_is_produced_and_revert_restores_everything` |
| Isolation — vault byte-identical at every precommit stage, ignored/untracked intact | `tests/test_cutover_build.py::test_every_precommit_stage_failure_preserves_the_complete_live_vault`, `tests/test_cutover_build.py::test_isolated_worktree_writes_do_not_reach_the_live_vault`, `tests/test_cutover_build.py::test_isolated_worktree_is_removed_on_success_and_failure`, `tests/test_cutover_promotion.py::test_promotion_leaves_an_obstructing_untracked_file_intact` |
| Isolation — no `reset --hard` or `clean -fd` | `tests/test_cutover_build.py::test_no_destructive_git_command_appears_in_the_cutover_modules` (Step 2) |
| Promotion refusal — moved HEAD, changed status, new ignored content | `tests/test_cutover_promotion.py::test_promotion_refuses_a_moved_head`, `tests/test_cutover_promotion.py::test_promotion_refuses_a_changed_status`, `tests/test_cutover_promotion.py::test_promotion_repeats_the_ignored_content_check` |
| Approval binding — digest not in manifest; bytes canonical | `tests/test_cutover_manifest.py::test_manifest_never_contains_its_own_digest`, `tests/test_cutover_manifest.py::test_verify_refuses_digest_matching_but_noncanonical_bytes` |
| Approval binding — any difference refused | `tests/test_cutover_manifest.py::test_verify_refuses_a_single_changed_byte`, `tests/test_cutover_build.py::test_build_refuses_a_manifest_that_does_not_match_its_record`, `tests/test_cutover_build.py::test_build_refuses_a_mapping_that_is_not_the_deterministic_result` |
| Database allowlist — narrowness | `tests/test_cutover_db.py::test_only_the_allowlisted_column_is_updated`, `tests/test_cutover_db.py::test_a_matching_column_name_in_another_database_is_untouched`, `tests/test_cutover_db.py::test_an_unknown_table_or_column_is_a_hard_stop`, `tests/test_cutover_db.py::test_a_missing_database_is_a_hard_stop` |
| Database axis typing | `tests/test_cutover_db.py::test_a_product_target_receives_only_the_product_mapping`, `tests/test_cutover_db.py::test_a_member_target_receives_only_the_member_mapping`, `tests/test_cutover_db.py::test_the_residual_query_ignores_another_axis_old_value`, `tests/test_cutover_manifest.py::test_database_target_axis_must_be_product_or_member` |
| Database path confinement | `tests/test_cutover_db.py::test_an_absolute_path_is_refused`, `tests/test_cutover_db.py::test_a_path_escaping_the_root_is_refused`, `tests/test_cutover_db.py::test_a_symlinked_component_is_refused` |
| Database inventory — schema, typed reference counts, symlink refusal | `tests/test_cutover_db.py::test_schema_inventory_lists_tables_and_columns`, `tests/test_cutover_db.py::test_reference_inventory_is_path_column_and_axis_typed`, `tests/test_cutover_db.py::test_schema_inventory_refuses_a_books_db_symlink_without_following_it`, `tests/test_cutover_cli.py::test_inventory_reports_identifiers_advisory_and_schema` |
| Residual gates — scoped | `tests/test_cutover_locations.py::test_a_missed_front_matter_field_is_a_residual`, `tests/test_cutover_locations.py::test_a_missed_registry_key_is_a_residual`, `tests/test_cutover_locations.py::test_a_missed_policy_except_half_is_a_residual`, `tests/test_cutover_locations.py::test_a_surviving_entity_directory_is_a_residual`, `tests/test_cutover_locations.py::test_a_missed_workspace_id_is_a_residual` |
| Residual gates — boundary behaviour | `tests/test_cutover_locations.py::test_advisory_does_not_report_a_migrated_token`, `tests/test_cutover_locations.py::test_advisory_does_not_report_a_longer_token` |
| Residual gates — in-database | `tests/test_cutover_db.py::test_residuals_report_a_remaining_old_value` |
| Ordering — database before moves | `tests/test_cutover_build.py::test_the_database_is_updated_before_the_entity_directory_moves` |
| Ordering — sequential mappings preserve every rewrite touching one file | `tests/test_cutover_build.py::test_sequential_application_preserves_every_mapping_touching_one_file` |
| Ordering — dry run equals apply | `tests/test_cutover_cli.py::test_dry_run_and_apply_build_identical_trees_for_one_source_head` |
| Exact artifact — promoted SQLite blob and tree are the verified build | `tests/test_cutover_build.py::test_the_promoted_database_blob_is_the_exact_verified_build_artifact` |
| Advisory complement and regeneration | `tests/test_cutover_locations.py::test_typed_registry_front_matter_workspace_policy_and_proposal_lines_are_not_advisory`, `tests/test_cutover_build.py::test_build_regenerates_the_advisory_report_after_rewriting`, `tests/test_cutover_build.py::test_build_refuses_an_extra_or_stale_disposition` |
| Validators run after migration and before commit; documented `check_v2` summary parsed exactly | `tests/test_cutover_build.py::test_validators_run_after_migration_and_before_commit`, `tests/test_cutover_build.py::test_validator_failure_discards_the_build_and_preserves_the_live_vault`, `tests/test_cutover_build.py::test_default_validator_accepts_the_documented_check_v2_summary`, `tests/test_cutover_build.py::test_default_validator_does_not_read_ten_errors_as_zero` |
| Receipts survive | `tests/test_cutover_build.py::test_a_spent_proposal_id_is_still_refused_after_an_entity_cutover` |
| Proposals rewritten without reserializing unrelated bytes; pre-cutover token refused | `tests/test_cutover_locations.py::test_yaml_path_head_field_preserves_every_unrelated_byte`, `tests/test_cutover_build.py::test_proposal_prefixes_are_rewritten_and_a_pre_cutover_token_is_refused` |
| `former_slugs` — no resolver, scoped placement, existing provenance preserved | `tests/test_cutover_build.py::test_former_slugs_is_written_only_on_entity_and_product_keys`, `tests/test_cutover_build.py::test_existing_former_slugs_are_preserved_and_no_duplicate_key_is_created`, `tests/test_cutover_locations.py::test_former_slugs_is_exempt_only_in_the_entity_and_product_registries`, `tests/test_cutover_locations.py::test_former_slugs_is_not_exempt_in_the_member_registry` |
| `former_slugs` — term collection unchanged | `tests/test_public_repo_audit.py::test_term_collection_reads_only_registry_keys_and_ids` |
| Fail-open guard — `.sensitive/` still denied | `tests/test_cutover_build.py::test_sensitive_reads_are_denied_before_and_after_the_cutover` |

- [ ] **Step 2: Add the destructive-command guard**

Append to `tests/test_cutover_build.py`:

```python
import ast


def test_no_destructive_git_command_appears_in_the_cutover_modules():
    modules = [
        "app/cutover.py",
        "app/cutover_build.py",
        "app/cutover_db.py",
        "app/cutover_inventory.py",
        "app/cutover_locations.py",
        "app/identifiers.py",
        "app/cutover_manifest.py",
    ]
    for name in modules:
        source = Path(name).read_text(encoding="utf-8")
        tree = ast.parse(source, filename=name)
        for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
            literals = {
                item.value
                for item in ast.walk(call)
                if isinstance(item, ast.Constant) and isinstance(item.value, str)
            }
            assert not {"reset", "--hard"} <= literals, (
                f"{name} can invoke git reset --hard"
            )
            assert not {"clean", "-fd"} <= literals, (
                f"{name} can invoke git clean -fd"
            )
```

Run: `uv run python -m pytest -q tests/test_cutover_build.py::test_no_destructive_git_command_appears_in_the_cutover_modules`
Expected: PASS.

- [ ] **Step 3: Run the full suite and record the baseline**

Run: `uv run python -m pytest -q`
Expected: PASS. Record the exact count. It must exceed the 1,476 starting baseline by the number of tests this plan adds.

- [ ] **Step 4: Run the mutation campaign**

For each row: copy the target file to `/private/tmp/cutover-preimage-<n>.py`, apply the exact edit shown, run only the stated node, confirm RED at the stated assertion, restore with `cp`, verify with `cmp`, and re-run to GREEN. Record every result in the ledger.

| # | File | One exact substitution | Full node id that must go RED | Intended failing assertion / witness |
|---|---|---|---|---|
| 1 | `app/identifiers.py` | `IDENTIFIER_MINIMUM_LENGTH = 5` → `IDENTIFIER_MINIMUM_LENGTH = 4` | `tests/test_cutover_identifiers.py::test_floor_is_one_above_the_audit_long_term_threshold` | `assert IDENTIFIER_MINIMUM_LENGTH == 5` |
| 2 | `app/identifiers.py` | `if new != expected:` → `if False:` inside `validate_mapping_pair` | `tests/test_cutover_build.py::test_build_refuses_a_mapping_that_is_not_the_deterministic_result` | `DID NOT RAISE` with `deterministic` |
| 3 | `app/cutover_db.py` | `if item.axis == target.axis` → `if item.axis in {"product", "member"}` | `tests/test_cutover_db.py::test_a_product_target_receives_only_the_product_mapping` | product target received the member value |
| 4 | `app/cutover_db.py` | First `f"SET {_quote_identifier(target.column)} = ? "` → `f'SET "tag" = ? '` | `tests/test_cutover_db.py::test_only_the_allowlisted_column_is_updated` | non-allowlisted `tag` changed |
| 5 | `app/cutover_db.py` | Replace the full `resolve_database_path` body with `return next(root.rglob("books.db"))` | `tests/test_cutover_db.py::test_a_matching_column_name_in_another_database_is_untouched` | another database changed |
| 6 | `app/cutover_db.py` | `if current.is_symlink():` → `if False:` | `tests/test_cutover_db.py::test_a_symlinked_component_is_refused` | unsafe database path was accepted |
| 7 | `app/cutover_build.py` | `if residual:` → `if False:` for the immediate post-update database residual | `tests/test_cutover_build.py::test_build_gate_refuses_a_database_writer_that_leaves_the_old_value` | `DID NOT RAISE` database residual |
| 8 | `app/cutover_build.py` | `if remaining:` → `if False:` for `scoped_residuals` | `tests/test_cutover_build.py::test_build_gate_refuses_a_writer_that_misses_the_policy_except_half` | `DID NOT RAISE` `entity:action-policy:except` |
| 9 | `app/cutover_build.py` | `check_collisions(manifest.mappings, existing_identifiers(scratch))` → `None` | `tests/test_cutover_build.py::test_build_refuses_a_colliding_mapping` | `DID NOT RAISE` collision |
| 10 | `app/cutover_build.py` | `require_clean_entities(vault, affected)` → `None` | `tests/test_cutover_build.py::test_build_refuses_an_entity_with_ignored_content` | `DID NOT RAISE` ignored content |
| 11 | `app/cutover_build.py` | Move the exact `_require_dispositions(scratch, manifest)` line from immediately after worktree creation to immediately after `_apply_mappings_in_order` | `tests/test_cutover_build.py::test_dispositions_are_checked_before_any_path_move` | `dispositions were not checked on the source tree` |
| 12 | `app/cutover_locations.py` | `_POLICY_LIST` group `paths|except` → `paths` | `tests/test_cutover_build.py::test_sensitive_reads_are_denied_before_and_after_the_cutover` | sensitive read became allowed |
| 13 | `app/cutover_locations.py` | `rf"(?<![\w-]){re.escape(term)}(?![\w-])"` → `rf"{re.escape(term)}"` | `tests/test_cutover_locations.py::test_advisory_does_not_report_a_longer_token` | longer token reported |
| 14 | `app/cutover_locations.py` | `exempt_former_slugs = relative in FORMER_SLUGS_FILES` → `exempt_former_slugs = True` | `tests/test_cutover_locations.py::test_former_slugs_is_not_exempt_in_the_member_registry` | member provenance was hidden |
| 15 | `app/cutover_locations.py` | First advisory `raise UnreadableFile(` → `return []  # MUTANT` | `tests/test_cutover_locations.py::test_an_unreadable_text_file_is_a_hard_failure` | `DID NOT RAISE` unreadable file |
| 16 | `app/cutover_build.py` | In the member registry branch, wrap its result with `_record_former_slug(..., new, old, 6)` | `tests/test_cutover_build.py::test_former_slugs_is_written_only_on_entity_and_product_keys` | member gained provenance field |
| 17 | `app/cutover_build.py` | In the product mapping branch, additionally rewrite workspace `id` | `tests/test_cutover_build.py::test_a_product_kind_workspace_id_takes_the_workspace_suffix` | product claimed workspace id |
| 18 | `app/cutover_build.py` | At the start of `_apply_mappings_in_order`, capture `workspaces.yaml`; immediately before each `_apply_*` call restore that captured text | `tests/test_cutover_build.py::test_sequential_application_preserves_every_mapping_touching_one_file` | an earlier workspace rewrite was lost |
| 19 | `app/cutover.py` | Live-HEAD precheck condition → `if False:` | `tests/test_cutover_promotion.py::test_promotion_refuses_a_moved_head` | moved HEAD was accepted |
| 20 | `app/cutover.py` | `require_clean_entities(vault, affected_entities)` → `None` | `tests/test_cutover_promotion.py::test_promotion_repeats_the_ignored_content_check` | new ignored content was accepted |
| 21 | `app/cutover.py` | `with action_lock(vault):` → `with contextlib.nullcontext():` and add `import contextlib` | `tests/test_cutover_promotion.py::test_promotion_takes_the_shared_action_lock` | lock was not taken |
| 22 | `app/cutover.py` | Move `committed = True` from after successful merge to immediately before the merge call | `tests/test_cutover_promotion.py::test_a_failed_promotion_is_not_reported_as_committed` | uncommitted failure reported committed |
| 23 | `app/cutover.py` | `_run_dry_run`: `result = build_cutover(...)` → `raise CutoverError("dry run skipped build")` | `tests/test_cutover_cli.py::test_dry_run_builds_and_shows_the_diff_without_touching_the_vault` | dry run produced no diff |
| 24 | `tools/public_repo_audit.py` | Add `terms.update(spec.get("former_slugs") or [])` inside the entity loop | `tests/test_public_repo_audit.py::test_term_collection_reads_only_registry_keys_and_ids` | retired provenance became an audit term |
| 25 | `app/cutover_locations.py` | Remove `(?=[ \t]*(?:[,}#]|$))` from `rewrite_yaml_value_field` | `tests/test_cutover_locations.py::test_yaml_value_field_rewrite_does_not_match_a_scalar_prefix` | scalar prefix was rewritten |
| 26 | `app/cutover_locations.py` | `typed = _typed_line_keys(root, mappings)` → `typed = set()` | `tests/test_cutover_locations.py::test_typed_registry_front_matter_workspace_policy_and_proposal_lines_are_not_advisory` | typed lines were reported |
| 27 | `app/cutover_locations.py` | Symlink branch `text = os.readlink(candidate)` → `continue` | `tests/test_cutover_locations.py::test_a_symlink_is_scanned_as_link_text_without_following_its_target` | symlink occurrence disappeared |
| 28 | `app/cutover_db.py` | Inventory symlink `raise DatabaseCutoverError(` → `continue  # MUTANT` (replace that complete branch) | `tests/test_cutover_db.py::test_schema_inventory_refuses_a_books_db_symlink_without_following_it` | symlink database silently omitted |
| 29 | `app/cutover_build.py` | `_require_post_advisory(scratch, manifest)` → `None` | `tests/test_cutover_build.py::test_build_regenerates_the_advisory_report_after_rewriting` | changed advisory report accepted |
| 30 | `app/cutover_build.py` | `validator(scratch)` → `None` | `tests/test_cutover_build.py::test_validators_run_after_migration_and_before_commit` | validator observation missing |
| 31 | `app/cutover.py` | `if confirmed != built_commit:` → `if False:` | `tests/test_cutover_promotion.py::test_a_commit_confirmed_as_the_wrong_head_is_reported_as_committed_but_unresolved` | wrong confirmed HEAD accepted |
| 32 | `app/cutover_inventory.py` | `if completed.stdout:` → `if False:` in `require_clean_status` | `tests/test_cutover_cli.py::test_inventory_refuses_a_dirty_live_vault` | dirty inventory returned success |
| 33 | `app/cutover_build.py` | `if existing:` → `if False:` in `_record_former_slug` | `tests/test_cutover_build.py::test_existing_former_slugs_are_preserved_and_no_duplicate_key_is_created` | duplicate provenance key created |
| 34 | `app/cutover_build.py` | In `_rewrite_proposal`, replace the exact `path.write_text(rewritten, encoding="utf-8")` call with `path.write_text(yaml.safe_dump(yaml.safe_load(rewritten), sort_keys=False), encoding="utf-8")` | `tests/test_cutover_build.py::test_proposal_prefixes_are_rewritten_and_a_pre_cutover_token_is_refused` | proposal rewrite altered bytes outside the approved fields |
| 35 | `app/cutover_build.py` | `_CHECK_V2_ZERO.search(combined)` → `"0 error(s), 0 warning(s)" in combined` | `tests/test_cutover_build.py::test_default_validator_does_not_read_ten_errors_as_zero` | ten errors were accepted as zero |
| 36 | `app/cutover_build.py` | Delete the unique `item.context_sha256,` field from `_occurrence_key` | `tests/test_cutover_build.py::test_post_advisory_identity_rejects_same_count_with_changed_context` | changed context was accepted because only count and location remained |
| 37 | `app/cutover.py` | `mappings = proposed_mappings(snapshot)` → `mappings = proposed_mappings(vault)` | `tests/test_cutover_cli.py::test_inventory_reads_tracked_evidence_from_the_captured_head` | inventory read the mutable live registry instead of captured HEAD |
| 38 | `app/cutover.py` | Delete the final `require_clean_status(vault)` between the live-HEAD comparison and repeated `require_clean_entities` call | `tests/test_cutover_cli.py::test_inventory_discards_results_when_live_status_changes_before_return` | changed live status was accepted after snapshot inventory |
| 39 | `app/cutover_locations.py` | `for _match in pattern.finditer(line):` → `for _match in list(pattern.finditer(line))[:1]:` | `tests/test_cutover_locations.py::test_advisory_identity_distinguishes_two_tokens_on_one_line` | second token on one line had no disposition identity |
| 40 | `app/cutover_build.py` | `existing_identifiers(scratch)` → `existing_identifiers(vault)` | `tests/test_cutover_build.py::test_build_collision_check_uses_the_manifest_source_head` | collision authority came from mutable live bytes instead of manifest source HEAD |

Before applying a row, assert its OLD anchor appears exactly once in the target
file. Rows 11, 16, 17, 18, 21, 22, 28, and 38 are explicitly multi-line exact
substitutions: record their complete OLD and NEW blocks in the ledger before
running them. They are still one mutation each; no row may depend on another
row being active. Run every full node id alone with `--tb=line`, require exit 1
with no collection errors, and verify that the one emitted assertion line is
the intended witness named in the table. This is why `--tb=line` is mandatory:
a full traceback prints nearby source and can make a passing assertion look like
the cause. Restore from the preimage in a `finally`, prove `cmp` equality, then
require that same node to exit 0. An anchor miss, zero tests collected, wrong
node, wrong assertion, or non-1 mutated exit is **not evidence**, never a
detected mutant.

- [ ] **Step 5: Run the restored closing suite**

Run: `uv run python -m pytest -q`
Expected: PASS. Capture the exact count and duration from this restored tree;
do not reuse the pre-campaign count.

- [ ] **Step 6: Write the ledger from actual output**

Create `docs/superpowers/plans/2026-08-26-short-identifier-cutover-mutation-ledger.md` with one section per row recording: the exact edit applied, the exact command run, the RED output's assertion line, the `cmp` restoration proof, and the GREEN re-run. Add the exact restored closing-suite command, count, and duration from Step 5. A count without its command, or a mutation without its exact failing test, is not evidence.

- [ ] **Step 7: Verify the restored pre-commit tree**

```bash
git diff --check
git status --porcelain
```

Expected: diff check clean. Status must name only
`tests/test_cutover_build.py` and the new mutation ledger. Any application file
at this point means a mutant was not restored: stop without committing.

- [ ] **Step 8: Commit the guard and evidence together**

```bash
git add docs/superpowers/plans/2026-08-26-short-identifier-cutover-mutation-ledger.md tests/test_cutover_build.py
git commit -m "docs: record the cutover mutation ledger"
```

- [ ] **Step 9: Run publication gates against the committed checkpoint**

```bash
uv run python -m tools.public_repo_audit --repo . --history
tools/run_gitleaks.sh .
git diff --check
git status --porcelain --untracked-files=all
```

Expected: audit `CLEAN`; Gitleaks no leaks; diff check and status produce no
output. Do not amend the evidence commit after this check; any correction gets
a new, separately verified commit.

---

## Deferred to Stage B

Not in this plan, and not to be started until the private cutover has been promoted and verified:

- The five-character floor at [app/entities.py:12](app/entities.py:12), [app/vault.py:31](app/vault.py:31), [app/destinations.py:57](app/destinations.py:57), [app/rename.py:46](app/rename.py:46), and [app/action_receipts.py:32](app/action_receipts.py:32), each consuming `app.identifiers.meets_floor`.
- A structural test asserting no module defines its own registry-identifier length rule.
- Renaming the sub-floor synthetic fixtures — the dominant one is a four-character entity slug appearing in the low hundreds of occurrences.

The term-collection pin is deliberately **not** deferred; it is Task 15, because the migration's premise depends on it holding beforehand.

## Handoff

Return the recorded base SHA, branch, worktree, commit list, each task's RED and GREEN output, the design-test mapping table with every node's result, the mutation ledger, the full public suite count with its exact command, the publication audit and Gitleaks results, `git diff --check`, and the final `git status --porcelain`. State explicitly that no live vault was accessed and that no private gate was run.

The trusted local reviewer — not the external agent — runs the vault-seeded audits, the private suite, `check_v2`, and the opaque byte-preservation comparison, and performs the migration itself.
