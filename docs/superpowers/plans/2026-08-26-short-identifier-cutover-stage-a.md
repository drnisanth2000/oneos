# Short-Identifier Cutover — Stage A Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the public tooling that inventories short registry identifiers, binds an owner-approved mapping by digest, and produces one reversible cutover commit built in an isolated worktree and promoted under quiesce — without enforcing the length floor at read time.

**Architecture:** Six focused modules. `app/identifiers.py` single-sources the five-character floor and the deterministic suffix mapping. `app/cutover_manifest.py` holds the canonical manifest and its separate approval record. `app/cutover_locations.py` owns the closed list of typed rewrite locations, the scoped rewriters, and the scoped residual gate. `app/cutover_db.py` owns allowlisted `books.db` updates and the in-database residual query. `app/cutover_inventory.py` enumerates identifiers, collisions, advisory occurrences, and ignored/untracked stops. `app/cutover.py` orchestrates isolated build and promotion, and provides the CLI. Nothing in Stage A enforces the floor at read time.

**Tech Stack:** Python 3.12, stdlib `re`/`pathlib`/`sqlite3`/`hashlib`/`subprocess`/`tempfile`, PyYAML, pytest, Git CLI.

**Spec:** `docs/superpowers/specs/2026-08-26-short-identifier-cutover-design.md` at `151f54b`.

---

## Scope

**This plan is Stage A only.** The design defines two public stages separated by the private migration, and they cannot ship together: read-time floor enforcement would make the tool unable to read the vault it must migrate. Stage B — floor enforcement at the five validation sites plus sub-floor fixture renames — gets its own plan after the private cutover has been promoted and verified.

Stage A produces working, testable software on its own: a complete cutover tool whose synthetic tests pass, exercising nothing but temporary vaults.

## Global constraints

- Base every branch on freshly fetched `origin/main`; record the SHA before branching.
- Public repository and synthetic fixtures only. Never read, request, infer, or display a live vault, registry value, database, path, or history.
- Add no dependency, no schema, no registry value, no exemption, no second scanner.
- Do **not** enforce the length floor at any read-time validation site in Stage A.
- Do not modify the parked Item 2 branch or its worktree.
- Do not push, open a pull request, merge, delete a branch, or remove a worktree.
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
| Create `app/identifiers.py` | The floor constant, `meets_floor`, `suffix_for_axis`, `map_identifier`. Pure; no I/O. Stage B's validators will consume this. |
| Create `app/cutover_manifest.py` | `Mapping`, `DatabaseTarget`, `Disposition`, `ApprovalManifest`, `ApprovalRecord`; canonical serialisation and digest verification. |
| Create `app/cutover_locations.py` | The closed rewrite-location table, the scoped rewriters, the scoped residual gate, and the advisory scan. |
| Create `app/cutover_db.py` | Allowlisted `UPDATE`s against approved `(path, table, column)` triples, plus the in-database residual query. |
| Create `app/cutover_inventory.py` | Identifier enumeration, the three collision checks, the advisory report, the ignored/untracked check, and the database schema inventory. |
| Create `app/cutover.py` | Isolated worktree lifecycle, build ordering, quiesce and promotion, CLI. |
| Create `tests/test_cutover_identifiers.py` | Floor and mapping. |
| Create `tests/test_cutover_manifest.py` | Manifest, approval record, digest binding. |
| Create `tests/test_cutover_locations.py` | Scoped replacement, partition, residual gate, advisory, fail-open. |
| Create `tests/test_cutover_db.py` | Allowlist narrowness, residual query. |
| Create `tests/test_cutover_inventory.py` | Collisions, ignored/untracked, schema inventory. |
| Create `tests/test_cutover_build.py` | Ordering, one commit, isolation, promotion refusal, revert. |

---

### Task 1: The identifier floor and mapping

**Files:**
- Create: `app/identifiers.py`
- Test: `tests/test_cutover_identifiers.py`

**Interfaces:**
- Produces: `IDENTIFIER_MINIMUM_LENGTH`, `AXES`, `meets_floor(value)`, `suffix_for_axis(axis)`, `map_identifier(axis, old)`, `AxisError`.
- Consumed by: every other cutover module, and Stage B's validation sites.

- [ ] **Step 1: Write the failing test**

Create `tests/test_cutover_identifiers.py`:

```python
import pytest

from app.identifiers import (
    AXES,
    IDENTIFIER_MINIMUM_LENGTH,
    AxisError,
    map_identifier,
    meets_floor,
    suffix_for_axis,
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

_SUFFIXES = {axis: f"-{axis}" for axis in AXES}


class AxisError(ValueError):
    """An unknown axis, or an identifier that must not be mapped."""


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
```

- [ ] **Step 4: Run the test and confirm it passes**

Run: `uv run python -m pytest -q tests/test_cutover_identifiers.py`
Expected: PASS, 8 tests.

- [ ] **Step 5: Commit**

```bash
git add app/identifiers.py tests/test_cutover_identifiers.py
git commit -m "feat: single-source the registry identifier floor"
```

---

### Task 2: The approval manifest and its separate approval record

**Files:**
- Create: `app/cutover_manifest.py`
- Test: `tests/test_cutover_manifest.py`

**Interfaces:**
- Produces: `Mapping`, `DatabaseTarget`, `Disposition`, `ApprovalManifest`, `ApprovalRecord`, `canonical_bytes`, `manifest_digest`, `load_manifest`, `verify_manifest`, `ManifestError`.
- Consumed by: `app/cutover.py` and `app/cutover_inventory.py`.

The manifest never contains its own digest. A document cannot carry the hash of itself: inserting the digest changes the bytes being hashed, so verification would depend on an agreement about what was hashed rather than a plain byte comparison.

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
        ),
        databases=(
            DatabaseTarget(path="ab/books.db", table="ledger", column="product"),
        ),
        dispositions=(
            Disposition(path="notes/one.md", line=3, old="ab", kind="incidental"),
        ),
    )


def test_canonical_bytes_are_stable_across_construction_order():
    first = sample_manifest()
    second = ApprovalManifest(
        source_head="a" * 40,
        mappings=(
            Mapping(axis="product", old="q7", new="q7-product"),
            Mapping(axis="entity", old="ab", new="ab-entity"),
        ),
        databases=(
            DatabaseTarget(path="ab/books.db", table="ledger", column="product"),
        ),
        dispositions=(
            Disposition(path="notes/one.md", line=3, old="ab", kind="incidental"),
        ),
    )
    assert canonical_bytes(first) == canonical_bytes(second)


def test_manifest_never_contains_its_own_digest():
    manifest = sample_manifest()
    raw = canonical_bytes(manifest)
    digest = manifest_digest(manifest)
    assert digest not in raw.decode("utf-8")
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
    record = ApprovalRecord(
        manifest_sha256=manifest_digest(manifest), approved_by="owner"
    )
    verify_manifest(canonical_bytes(manifest), record)


def test_verify_refuses_a_mismatched_record():
    manifest = sample_manifest()
    record = ApprovalRecord(manifest_sha256="b" * 64, approved_by="owner")
    with pytest.raises(ManifestError):
        verify_manifest(canonical_bytes(manifest), record)


def test_verify_refuses_a_single_changed_byte():
    manifest = sample_manifest()
    record = ApprovalRecord(
        manifest_sha256=manifest_digest(manifest), approved_by="owner"
    )
    tampered = canonical_bytes(manifest).replace(b"ab-entity", b"ab-produce")
    with pytest.raises(ManifestError):
        verify_manifest(tampered, record)


def test_round_trip_through_canonical_bytes():
    manifest = sample_manifest()
    assert load_manifest(canonical_bytes(manifest)) == manifest


def test_disposition_kind_is_closed():
    with pytest.raises(ManifestError):
        Disposition(path="a.md", line=1, old="ab", kind="handfix")


def test_structural_disposition_requires_a_typed_location():
    with pytest.raises(ManifestError):
        Disposition(path="a.md", line=1, old="ab", kind="structural")
    allowed = Disposition(
        path="a.md", line=1, old="ab", kind="structural", typed_location="entity:front-matter"
    )
    assert allowed.typed_location == "entity:front-matter"
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
manifest never carries its own digest: a self-referential hash makes
verification depend on an agreement about which bytes were hashed, and that
agreement is exactly what an accident gets to break.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib

import yaml

from .identifiers import AXES

_DISPOSITION_KINDS = frozenset({"incidental", "structural"})


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
    """One approved `(source-relative path, table, column)` triple.

    The path is required: the vault holds one database per entity root and
    their schemas are not proven identical, so an allowlist keyed only by
    table and column would apply one entity's proof to another.
    """

    path: str
    table: str
    column: str

    def __post_init__(self) -> None:
        for field_name in ("path", "table", "column"):
            if not getattr(self, field_name):
                raise ManifestError(f"database target requires {field_name!r}")


@dataclass(frozen=True, order=True)
class Disposition:
    path: str
    line: int
    old: str
    kind: str
    typed_location: str = ""

    def __post_init__(self) -> None:
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
            {"path": item.path, "table": item.table, "column": item.column}
            for item in sorted(manifest.databases)
        ],
        "dispositions": [
            {
                "path": item.path,
                "line": item.line,
                "old": item.old,
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
            mappings=tuple(
                Mapping(**item) for item in document.get("mappings", [])
            ),
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
    actual = hashlib.sha256(raw).hexdigest()
    if actual != record.manifest_sha256:
        raise ManifestError("approval manifest does not match its approval record")
```

- [ ] **Step 4: Run the test and confirm it passes**

Run: `uv run python -m pytest -q tests/test_cutover_manifest.py`
Expected: PASS, 9 tests.

- [ ] **Step 5: Commit**

```bash
git add app/cutover_manifest.py tests/test_cutover_manifest.py
git commit -m "feat: add the digest-bound cutover approval manifest"
```

---

### Task 3: The closed rewrite-location table

**Files:**
- Create: `app/cutover_locations.py`
- Test: `tests/test_cutover_locations.py`

**Interfaces:**
- Produces: `REWRITE_LOCATIONS`, `Location`, `locations_for_axis`, `LocationError`.
- Consumed by: the rewriters, the residual gate, and the partition test.

This task builds the table and its partition invariant only. The rewriters come in Task 4.

- [ ] **Step 1: Write the failing test**

Create `tests/test_cutover_locations.py`:

```python
from collections import Counter

from app.cutover_locations import REWRITE_LOCATIONS, locations_for_axis
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
    product_pairs = {
        (location.file_kind, location.field)
        for location in locations_for_axis("product")
    }
    assert ("workspaces", "id") not in product_pairs


def test_workspace_axis_owns_the_workspace_id():
    workspace_pairs = {
        (location.file_kind, location.field)
        for location in locations_for_axis("workspace")
    }
    assert ("workspaces", "id") in workspace_pairs


def test_members_id_and_workspaces_id_are_distinct_pairs():
    member_pairs = {
        (location.file_kind, location.field)
        for location in locations_for_axis("member")
    }
    assert ("members", "id") in member_pairs
    assert ("workspaces", "id") not in member_pairs


def test_action_policy_rewrites_both_halves_of_the_fail_open_rule():
    entity_fields = {
        location.field
        for location in locations_for_axis("entity")
        if location.file_kind == "action-policy"
    }
    assert entity_fields == {"paths", "except"}
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `uv run python -m pytest -q tests/test_cutover_locations.py`
Expected: collection error, `ModuleNotFoundError: No module named 'app.cutover_locations'`.

- [ ] **Step 3: Write the minimal implementation**

Create `app/cutover_locations.py`:

```python
"""cutover_locations.py — the closed list of typed rewrite locations.

A short identifier may also be an ordinary English word, so nothing is
rewritten because it merely looks like the identifier. Only a location on
this table is ever modified, and the table must partition: no
`(file_kind, field)` pair may appear under two axes, or two mappings would
contend for one field.
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
    Location("product", "books-db", "approved-triple", "value"),
    # --- member -----------------------------------------------------------
    Location("member", "members", "id", "value"),
    Location("member", "front-matter", "member", "value"),
    Location("member", "workspaces", "member", "value"),
    Location("member", "books-db", "approved-triple-member", "value"),
    # --- workspace --------------------------------------------------------
    Location("workspace", "workspaces", "id", "value"),
)


def locations_for_axis(axis: str) -> tuple[Location, ...]:
    if axis not in AXES:
        raise LocationError(f"unknown axis {axis!r}")
    return tuple(item for item in REWRITE_LOCATIONS if item.axis == axis)


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

- [ ] **Step 4: Run the test and confirm it passes**

Run: `uv run python -m pytest -q tests/test_cutover_locations.py`
Expected: PASS, 6 tests.

- [ ] **Step 5: Commit**

```bash
git add app/cutover_locations.py tests/test_cutover_locations.py
git commit -m "feat: declare the closed cutover rewrite locations"
```

---

### Task 4: Scoped rewriters — front matter, registries, and paths

**Files:**
- Modify: `app/cutover_locations.py`
- Test: `tests/test_cutover_locations.py`

**Interfaces:**
- Produces: `rewrite_front_matter_field`, `rewrite_path_head`, `rewrite_yaml_value_field`, `rewrite_mapping_key`.
- Consumes: nothing beyond stdlib `re`.

Each rewriter matches an **exact whole field value** or an **exact path component**, never a substring.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cutover_locations.py`:

```python
import textwrap

from app.cutover_locations import (
    rewrite_front_matter_field,
    rewrite_mapping_key,
    rewrite_path_head,
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


def test_path_head_rewrite_handles_a_glob_pattern():
    assert rewrite_path_head("ab/**", "ab", "ab-entity") == "ab-entity/**"
    assert rewrite_path_head("ab/.sensitive/**", "ab", "ab-entity") == (
        "ab-entity/.sensitive/**"
    )


def test_yaml_value_field_rewrite_matches_the_exact_value():
    text = "workspaces:\n  - {id: ab, product: ab, kind: product}\n"
    result = rewrite_yaml_value_field(text, "product", "ab", "ab-product")
    assert "product: ab-product" in result
    assert "id: ab," in result


def test_mapping_key_rewrite_matches_at_the_given_indent():
    text = "products:\n  zz:\n    ab:\n      label: A\n"
    result = rewrite_mapping_key(text, "ab", "ab-product", indent=4)
    assert "    ab-product:" in result
    assert "  zz:" in result
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `uv run python -m pytest -q tests/test_cutover_locations.py`
Expected: collection error, `ImportError: cannot import name 'rewrite_front_matter_field'`.

- [ ] **Step 3: Write the minimal implementation**

Append to `app/cutover_locations.py`:

```python
import re

#: A whole token: not preceded or followed by a word character or a hyphen.
#: A migrated `ab-entity` therefore does not match a scan for `ab`, because
#: the lookahead fails on the hyphen, while a bare `ab` still does.
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
    """Replace `field: old` with `field: new` inside the leading front matter
    only, and only when `old` is the entire value."""
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
    """Replace `field: old` wherever it appears as a whole scalar value,
    including inside a flow mapping."""
    return re.sub(
        rf"(\b{re.escape(field)}:\s*){re.escape(old)}(?![\w-])",
        rf"\g<1>{new}",
        text,
    )


def rewrite_mapping_key(text: str, old: str, new: str, indent: int) -> str:
    """Rename a mapping key that sits at exactly `indent` spaces."""
    return re.sub(
        rf"(?m)^(\s{{{indent}}}){re.escape(old)}:",
        rf"\g<1>{new}:",
        text,
    )
```

- [ ] **Step 4: Run the test and confirm it passes**

Run: `uv run python -m pytest -q tests/test_cutover_locations.py`
Expected: PASS, 13 tests.

- [ ] **Step 5: Commit**

```bash
git add app/cutover_locations.py tests/test_cutover_locations.py
git commit -m "feat: add scoped cutover rewriters"
```

---

### Task 5: The scoped residual gate and the advisory scan

**Files:**
- Modify: `app/cutover_locations.py`
- Test: `tests/test_cutover_locations.py`

**Interfaces:**
- Produces: `advisory_occurrences(root, olds, enumerated_paths)`, `AdvisoryOccurrence`.
- Consumes: `boundaried` from Task 4.

The gate is scoped to the same locations as the writer. A whole-vault text gate would refuse forever on any short identifier that is also an ordinary word in prose — a gate that cannot pass is not a safety mechanism.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cutover_locations.py`:

```python
from pathlib import Path

from app.cutover_locations import AdvisoryOccurrence, advisory_occurrences


def test_advisory_reports_a_bare_token_outside_the_enumerated_locations(tmp_path: Path):
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "one.md").write_text("the ab pattern\n", encoding="utf-8")

    found = advisory_occurrences(tmp_path, {"ab"}, enumerated_paths=set())

    assert found == [
        AdvisoryOccurrence(path="notes/one.md", line=1, old="ab")
    ]


def test_advisory_ignores_an_enumerated_path(tmp_path: Path):
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "one.md").write_text("the ab pattern\n", encoding="utf-8")

    found = advisory_occurrences(
        tmp_path, {"ab"}, enumerated_paths={"notes/one.md"}
    )

    assert found == []


def test_advisory_does_not_report_a_migrated_token(tmp_path: Path):
    (tmp_path / "note.md").write_text("entity: ab-entity\n", encoding="utf-8")

    assert advisory_occurrences(tmp_path, {"ab"}, enumerated_paths=set()) == []


def test_advisory_does_not_report_a_longer_token(tmp_path: Path):
    (tmp_path / "note.md").write_text("xabx and cab and abx\n", encoding="utf-8")

    assert advisory_occurrences(tmp_path, {"ab"}, enumerated_paths=set()) == []


def test_advisory_skips_binaries_and_git(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "note.md").write_text("ab\n", encoding="utf-8")
    (tmp_path / "books.db").write_bytes(b"\x00ab\x00")

    assert advisory_occurrences(tmp_path, {"ab"}, enumerated_paths=set()) == []


def test_advisory_exempts_former_slugs_lines(tmp_path: Path):
    (tmp_path / "note.md").write_text("    former_slugs: [ab]\n", encoding="utf-8")

    assert advisory_occurrences(tmp_path, {"ab"}, enumerated_paths=set()) == []
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `uv run python -m pytest -q tests/test_cutover_locations.py`
Expected: collection error, `ImportError: cannot import name 'AdvisoryOccurrence'`.

- [ ] **Step 3: Write the minimal implementation**

Append to `app/cutover_locations.py`:

```python
from pathlib import Path

SKIP_DIRS = frozenset({".git", ".obsidian", ".trash"})
BINARY_SUFFIXES = frozenset({
    ".pdf", ".docx", ".xlsx", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico",
    ".db", ".sqlite", ".sqlite3", ".zip", ".gz", ".tar", ".woff", ".woff2",
})


@dataclass(frozen=True, order=True)
class AdvisoryOccurrence:
    path: str
    line: int
    old: str


def advisory_occurrences(
    root: Path, olds: set[str], enumerated_paths: set[str]
) -> list[AdvisoryOccurrence]:
    """Whole-token occurrences of an old identifier outside the enumerated
    locations. Reported for owner disposition, never rewritten."""
    patterns = {old: boundaried(old) for old in olds}
    found: list[AdvisoryOccurrence] = []
    for candidate in sorted(root.rglob("*")):
        if not candidate.is_file():
            continue
        relative = candidate.relative_to(root).as_posix()
        if any(part in SKIP_DIRS for part in Path(relative).parts):
            continue
        if candidate.suffix.lower() in BINARY_SUFFIXES:
            continue
        if relative in enumerated_paths:
            continue
        try:
            text = candidate.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            if "former_slugs" in line:
                continue
            for old, pattern in patterns.items():
                if pattern.search(line):
                    found.append(AdvisoryOccurrence(relative, number, old))
    return sorted(found)
```

- [ ] **Step 4: Run the test and confirm it passes**

Run: `uv run python -m pytest -q tests/test_cutover_locations.py`
Expected: PASS, 19 tests.

- [ ] **Step 5: Commit**

```bash
git add app/cutover_locations.py tests/test_cutover_locations.py
git commit -m "feat: add the scoped advisory occurrence scan"
```

---

### Task 6: Allowlisted database updates and the in-database residual query

**Files:**
- Create: `app/cutover_db.py`
- Test: `tests/test_cutover_db.py`

**Interfaces:**
- Produces: `apply_database_mappings`, `database_residuals`, `database_schema_inventory`, `DatabaseCutoverError`.
- Consumes: `DatabaseTarget` from Task 2.

The writer allowlist is exact `(path, table, column)` triples. It must never be derived from `registry.py::_DB_COLUMNS`, from `rename.py`'s column-name counter, or from column names.

- [ ] **Step 1: Write the failing test**

Create `tests/test_cutover_db.py`:

```python
from pathlib import Path
import sqlite3

import pytest

from app.cutover_db import (
    DatabaseCutoverError,
    apply_database_mappings,
    database_residuals,
    database_schema_inventory,
)
from app.cutover_manifest import DatabaseTarget, Mapping


def make_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE ledger (product TEXT, tag TEXT)")
    conn.execute("CREATE TABLE fund_holdings (member_id TEXT)")
    conn.execute("INSERT INTO ledger VALUES ('ab', 'ab')")
    conn.execute("INSERT INTO fund_holdings VALUES ('ab')")
    conn.commit()
    conn.close()


def read_all(path: Path) -> dict[str, list[tuple]]:
    conn = sqlite3.connect(path)
    try:
        return {
            "ledger": conn.execute("SELECT product, tag FROM ledger").fetchall(),
            "fund_holdings": conn.execute(
                "SELECT member_id FROM fund_holdings"
            ).fetchall(),
        }
    finally:
        conn.close()


def test_only_the_allowlisted_column_is_updated(tmp_path: Path):
    make_db(tmp_path / "ab" / "books.db")
    mappings = (Mapping(axis="product", old="ab", new="ab-product"),)
    targets = (DatabaseTarget(path="ab/books.db", table="ledger", column="product"),)

    apply_database_mappings(tmp_path, targets, mappings)

    rows = read_all(tmp_path / "ab" / "books.db")
    assert rows["ledger"] == [("ab-product", "ab")]
    assert rows["fund_holdings"] == [("ab",)]


def test_a_matching_column_name_in_another_database_is_untouched(tmp_path: Path):
    make_db(tmp_path / "ab" / "books.db")
    make_db(tmp_path / "zz" / "books.db")
    mappings = (Mapping(axis="product", old="ab", new="ab-product"),)
    targets = (DatabaseTarget(path="ab/books.db", table="ledger", column="product"),)

    apply_database_mappings(tmp_path, targets, mappings)

    assert read_all(tmp_path / "zz" / "books.db")["ledger"] == [("ab", "ab")]


def test_a_missing_database_is_a_hard_stop(tmp_path: Path):
    targets = (DatabaseTarget(path="ab/books.db", table="ledger", column="product"),)
    with pytest.raises(DatabaseCutoverError):
        apply_database_mappings(tmp_path, targets, ())


def test_an_unknown_table_or_column_is_a_hard_stop(tmp_path: Path):
    make_db(tmp_path / "ab" / "books.db")
    with pytest.raises(DatabaseCutoverError):
        apply_database_mappings(
            tmp_path,
            (DatabaseTarget(path="ab/books.db", table="ledger", column="nope"),),
            (Mapping(axis="product", old="ab", new="ab-product"),),
        )


def test_residuals_are_zero_after_a_complete_update(tmp_path: Path):
    make_db(tmp_path / "ab" / "books.db")
    mappings = (Mapping(axis="product", old="ab", new="ab-product"),)
    targets = (DatabaseTarget(path="ab/books.db", table="ledger", column="product"),)

    apply_database_mappings(tmp_path, targets, mappings)

    assert database_residuals(tmp_path, targets, mappings) == []


def test_residuals_report_a_remaining_old_value(tmp_path: Path):
    make_db(tmp_path / "ab" / "books.db")
    mappings = (Mapping(axis="product", old="ab", new="ab-product"),)
    targets = (DatabaseTarget(path="ab/books.db", table="ledger", column="product"),)

    assert database_residuals(tmp_path, targets, mappings) == [
        ("ab/books.db", "ledger", "product", "ab", 1)
    ]


def test_schema_inventory_lists_tables_and_columns(tmp_path: Path):
    make_db(tmp_path / "ab" / "books.db")

    inventory = database_schema_inventory(tmp_path)

    assert inventory["ab/books.db"]["ledger"] == ["product", "tag"]
    assert inventory["ab/books.db"]["fund_holdings"] == ["member_id"]
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `uv run python -m pytest -q tests/test_cutover_db.py`
Expected: collection error, `ModuleNotFoundError: No module named 'app.cutover_db'`.

- [ ] **Step 3: Write the minimal implementation**

Create `app/cutover_db.py`:

```python
"""cutover_db.py — allowlisted database updates, and their own residual gate.

`UPDATE` only: no `CREATE`, `ALTER`, or `DROP`, and therefore no schema
change. The writer allowlist is exact `(path, table, column)` triples and is
never derived from a column name: `registry.py` counts over a `member_id`
column that `rename.py` documents as an opaque key rather than a registry id,
and a `tag` column may hold free text that merely coincides with a product id.

The text residual gate skips binaries and can never see inside a database, so
`database_residuals` is the only fail-closed check this half of the migration
has.
"""
from __future__ import annotations

from pathlib import Path
import sqlite3

from .cutover_manifest import DatabaseTarget, Mapping

#: Axes whose values may appear in a database.
_DB_AXES = frozenset({"product", "member"})


class DatabaseCutoverError(Exception):
    pass


def _quote_identifier(name: str) -> str:
    """SQLite's own escaping rule for a quoted identifier. Identifiers cannot
    be parameter-bound; values always are."""
    return '"' + name.replace('"', '""') + '"'


def _connect(path: Path, *, read_only: bool) -> sqlite3.Connection:
    try:
        if read_only:
            return sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        return sqlite3.connect(path)
    except sqlite3.Error as exc:
        raise DatabaseCutoverError("approved database could not be opened") from exc


def _require_column(conn: sqlite3.Connection, target: DatabaseTarget) -> None:
    try:
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
    except sqlite3.DatabaseError as exc:
        raise DatabaseCutoverError("approved database could not be read") from exc
    if target.table not in tables:
        raise DatabaseCutoverError("approved table is absent from its database")
    columns = {
        row[1]
        for row in conn.execute(
            f"PRAGMA table_info({_quote_identifier(target.table)})"
        )
    }
    if target.column not in columns:
        raise DatabaseCutoverError("approved column is absent from its table")


def _resolve(root: Path, target: DatabaseTarget) -> Path:
    path = root / target.path
    if not path.is_file():
        raise DatabaseCutoverError("approved database is missing or unreadable")
    return path


def apply_database_mappings(
    root: Path,
    targets: tuple[DatabaseTarget, ...],
    mappings: tuple[Mapping, ...],
) -> int:
    """Update every approved triple. Returns the number of rows changed."""
    relevant = [item for item in mappings if item.axis in _DB_AXES]
    changed = 0
    for target in targets:
        path = _resolve(root, target)
        conn = _connect(path, read_only=False)
        try:
            _require_column(conn, target)
            statement = (
                f"UPDATE {_quote_identifier(target.table)} "
                f"SET {_quote_identifier(target.column)} = ? "
                f"WHERE {_quote_identifier(target.column)} = ?"
            )
            for mapping in relevant:
                cursor = conn.execute(statement, (mapping.new, mapping.old))
                changed += cursor.rowcount
            conn.commit()
        except sqlite3.DatabaseError as exc:
            conn.rollback()
            raise DatabaseCutoverError("approved database update failed") from exc
        finally:
            conn.close()
    return changed


def database_residuals(
    root: Path,
    targets: tuple[DatabaseTarget, ...],
    mappings: tuple[Mapping, ...],
) -> list[tuple[str, str, str, str, int]]:
    """Any remaining old value in an approved triple. Must be empty."""
    relevant = [item for item in mappings if item.axis in _DB_AXES]
    found: list[tuple[str, str, str, str, int]] = []
    for target in targets:
        path = _resolve(root, target)
        conn = _connect(path, read_only=True)
        try:
            _require_column(conn, target)
            statement = (
                f"SELECT COUNT(*) FROM {_quote_identifier(target.table)} "
                f"WHERE {_quote_identifier(target.column)} = ?"
            )
            for mapping in relevant:
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
    """Every database under `root`, with its tables and columns. Read-only,
    and deliberately broad: over-reporting only informs the owner's proof."""
    inventory: dict[str, dict[str, list[str]]] = {}
    for path in sorted(root.rglob("books.db")):
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
```

- [ ] **Step 4: Run the test and confirm it passes**

Run: `uv run python -m pytest -q tests/test_cutover_db.py`
Expected: PASS, 7 tests.

- [ ] **Step 5: Commit**

```bash
git add app/cutover_db.py tests/test_cutover_db.py
git commit -m "feat: add allowlisted database cutover updates"
```

---

### Task 7: Collision checks

**Files:**
- Create: `app/cutover_inventory.py`
- Test: `tests/test_cutover_inventory.py`

**Interfaces:**
- Produces: `check_collisions`, `CollisionError`.
- Consumes: `Mapping` from Task 2.

Class 3 — one literal on two axes — is **not** a refusal. Scoped replacement removes the contamination hazard that justified refusing it.

- [ ] **Step 1: Write the failing test**

Create `tests/test_cutover_inventory.py`:

```python
import pytest

from app.cutover_inventory import CollisionError, check_collisions
from app.cutover_manifest import Mapping


def test_a_new_value_colliding_with_an_existing_identifier_is_refused():
    mappings = (Mapping(axis="entity", old="ab", new="ab-entity"),)
    existing = {"entity": {"ab", "ab-entity"}}

    with pytest.raises(CollisionError, match="existing"):
        check_collisions(mappings, existing)


def test_duplicate_inputs_on_one_axis_are_refused():
    mappings = (
        Mapping(axis="entity", old="ab", new="ab-entity"),
        Mapping(axis="entity", old="ab", new="ab-entity"),
    )
    with pytest.raises(CollisionError, match="duplicate"):
        check_collisions(mappings, {"entity": {"ab"}})


def test_one_literal_on_two_axes_is_permitted():
    mappings = (
        Mapping(axis="entity", old="ab", new="ab-entity"),
        Mapping(axis="product", old="ab", new="ab-product"),
    )
    check_collisions(mappings, {"entity": {"ab"}, "product": {"ab"}})


def test_a_clean_mapping_passes():
    mappings = (Mapping(axis="entity", old="ab", new="ab-entity"),)
    check_collisions(mappings, {"entity": {"ab", "zzzzz"}})
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `uv run python -m pytest -q tests/test_cutover_inventory.py`
Expected: collection error, `ModuleNotFoundError: No module named 'app.cutover_inventory'`.

- [ ] **Step 3: Write the minimal implementation**

Create `app/cutover_inventory.py`:

```python
"""cutover_inventory.py — read-only enumeration, collisions, and hard stops.

Nothing here writes. The inventory runs against the live vault, produces the
mapping and the advisory report for owner approval, and refuses conditions
that must never reach a build.
"""
from __future__ import annotations

from .cutover_manifest import Mapping


class CollisionError(Exception):
    pass


def check_collisions(
    mappings: tuple[Mapping, ...], existing: dict[str, set[str]]
) -> None:
    """Refuse class 1 and class 2. Class 3 — one literal on two axes — is
    permitted: scoped replacement gives each axis its own typed locations, so
    an entity and a product sharing a literal migrate independently.
    """
    seen: dict[str, set[str]] = {}
    for mapping in mappings:
        axis_seen = seen.setdefault(mapping.axis, set())
        if mapping.old in axis_seen:
            raise CollisionError(
                f"duplicate mapping input on axis {mapping.axis!r}"
            )
        axis_seen.add(mapping.old)

    produced: dict[str, set[str]] = {}
    for mapping in mappings:
        axis_produced = produced.setdefault(mapping.axis, set())
        if mapping.new in axis_produced:
            raise CollisionError(
                f"duplicate mapping output on axis {mapping.axis!r}"
            )
        axis_produced.add(mapping.new)
        if mapping.new in existing.get(mapping.axis, set()):
            raise CollisionError(
                f"new value collides with an existing identifier on axis "
                f"{mapping.axis!r}"
            )
```

- [ ] **Step 4: Run the test and confirm it passes**

Run: `uv run python -m pytest -q tests/test_cutover_inventory.py`
Expected: PASS, 4 tests.

- [ ] **Step 5: Commit**

```bash
git add app/cutover_inventory.py tests/test_cutover_inventory.py
git commit -m "feat: add cutover collision checks"
```

---

### Task 8: The ignored-and-untracked hard stop

**Files:**
- Modify: `app/cutover_inventory.py`
- Test: `tests/test_cutover_inventory.py`

**Interfaces:**
- Produces: `untracked_or_ignored_paths(vault, entity)`, `require_clean_entities(vault, entities)`, `UnmigratableContentError`.

A linked worktree contains only tracked files, so ignored content — `.sensitive/` above all — cannot move with a renamed entity and would be stranded at the old path.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cutover_inventory.py`:

```python
from pathlib import Path

from app.cutover_inventory import (
    UnmigratableContentError,
    require_clean_entities,
    untracked_or_ignored_paths,
)
from tests.conftest import git_vault


def test_an_ignored_path_under_an_affected_entity_is_reported(tmp_path: Path):
    vault = git_vault(
        tmp_path,
        {".gitignore": ".sensitive/\n", "ab/00-inbox/note.md": "x\n"},
    )
    (vault / "ab" / ".sensitive").mkdir()
    (vault / "ab" / ".sensitive" / "secret.md").write_text("s\n", encoding="utf-8")

    found = untracked_or_ignored_paths(vault, "ab")

    assert any(".sensitive" in item for item in found)


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
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `uv run python -m pytest -q tests/test_cutover_inventory.py`
Expected: collection error, `ImportError: cannot import name 'UnmigratableContentError'`.

- [ ] **Step 3: Write the minimal implementation**

Append to `app/cutover_inventory.py`:

```python
from pathlib import Path
import subprocess


class UnmigratableContentError(Exception):
    """An affected entity holds content a linked worktree cannot carry."""


def untracked_or_ignored_paths(vault: Path, entity: str) -> list[str]:
    """Ignored or untracked paths beneath one entity directory.

    A linked worktree materialises tracked content only. If an affected entity
    holds anything else, promoting a renamed tree would strand it at the old
    path — outside the new entity and outside every scope check that assumes
    it lives beneath its entity root.
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
                f"entity {entity!r} holds ignored or untracked content; "
                f"relocate or retire it and re-run from inventory "
                f"({len(found)} path(s))"
            )
```

- [ ] **Step 4: Run the test and confirm it passes**

Run: `uv run python -m pytest -q tests/test_cutover_inventory.py`
Expected: PASS, 9 tests.

- [ ] **Step 5: Commit**

```bash
git add app/cutover_inventory.py tests/test_cutover_inventory.py
git commit -m "feat: stop the cutover on unmigratable entity content"
```

---

### Task 9: The isolated worktree lifecycle

**Files:**
- Create: `app/cutover.py`
- Test: `tests/test_cutover_build.py`

**Interfaces:**
- Produces: `isolated_worktree(vault, source_head)` context manager, `CutoverError`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_cutover_build.py`:

```python
from pathlib import Path

import pytest

from app.cutover import CutoverError, isolated_worktree
from tests.conftest import git_head, git_is_clean, git_vault


def test_isolated_worktree_starts_at_the_requested_head(tmp_path: Path):
    vault = git_vault(tmp_path / "vault", {"a.md": "x\n"})
    head = git_head(vault)

    with isolated_worktree(vault, head) as scratch:
        assert git_head(scratch) == head
        assert scratch != vault


def test_isolated_worktree_writes_do_not_reach_the_live_vault(tmp_path: Path):
    vault = git_vault(tmp_path / "vault", {"a.md": "x\n"})
    head = git_head(vault)

    with isolated_worktree(vault, head) as scratch:
        (scratch / "a.md").write_text("changed\n", encoding="utf-8")

    assert (vault / "a.md").read_text(encoding="utf-8") == "x\n"
    assert git_is_clean(vault)
    assert git_head(vault) == head


def test_isolated_worktree_is_removed_on_success(tmp_path: Path):
    vault = git_vault(tmp_path / "vault", {"a.md": "x\n"})

    with isolated_worktree(vault, git_head(vault)) as scratch:
        recorded = scratch

    assert not recorded.exists()


def test_isolated_worktree_is_removed_on_failure(tmp_path: Path):
    vault = git_vault(tmp_path / "vault", {"a.md": "x\n"})
    recorded: list[Path] = []

    with pytest.raises(RuntimeError):
        with isolated_worktree(vault, git_head(vault)) as scratch:
            recorded.append(scratch)
            raise RuntimeError("injected")

    assert not recorded[0].exists()
    assert git_is_clean(vault)


def test_a_commit_built_in_isolation_is_visible_from_the_vault(tmp_path: Path):
    vault = git_vault(tmp_path / "vault", {"a.md": "x\n"})
    head = git_head(vault)
    built: list[str] = []

    with isolated_worktree(vault, head) as scratch:
        (scratch / "a.md").write_text("changed\n", encoding="utf-8")
        import subprocess

        subprocess.run(["git", "add", "-A"], cwd=scratch, check=True)
        subprocess.run(
            ["git", "-c", "user.email=t@e", "-c", "user.name=t",
             "commit", "-q", "-m", "built"],
            cwd=scratch,
            check=True,
        )
        built.append(git_head(scratch))

    import subprocess

    kind = subprocess.run(
        ["git", "cat-file", "-t", built[0]],
        cwd=vault,
        check=True,
        capture_output=True,
        text=True,
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
Expected: collection error, `ModuleNotFoundError: No module named 'app.cutover'`.

- [ ] **Step 3: Write the minimal implementation**

Create `app/cutover.py`:

```python
"""cutover.py — build the cutover commit in isolation, then promote it.

Nothing is written to the live vault during planning or building. Every edit
happens in a temporary linked worktree, which shares the vault's object
database, so promotion is a fast-forward rather than a file copy. A failure
before promotion discards the worktree; the live vault was never touched, so
there is nothing to roll back and no `reset --hard` or `clean -fd` is ever
issued against it.
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


def _git(root: Path, *args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except subprocess.CalledProcessError as exc:
        raise CutoverError(f"git {args[0]} failed") from exc


@contextmanager
def isolated_worktree(vault: Path, source_head: str) -> Iterator[Path]:
    """A throwaway linked worktree at `source_head`, removed on exit."""
    if _git(vault, "rev-parse", "HEAD").strip() != source_head:
        raise CutoverError("vault HEAD is not the recorded source HEAD")
    parent = Path(tempfile.mkdtemp(prefix="oneos-cutover-"))
    scratch = parent / "tree"
    branch = f"cutover/build-{source_head[:12]}"
    try:
        _git(vault, "worktree", "add", "--quiet", "-b", branch,
             str(scratch), source_head)
        yield scratch
    finally:
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(scratch)],
            cwd=vault,
            check=False,
            capture_output=True,
        )
        subprocess.run(
            ["git", "branch", "-D", branch],
            cwd=vault,
            check=False,
            capture_output=True,
        )
        shutil.rmtree(parent, ignore_errors=True)
```

- [ ] **Step 4: Run the test and confirm it passes**

Run: `uv run python -m pytest -q tests/test_cutover_build.py`
Expected: PASS, 6 tests.

- [ ] **Step 5: Commit**

```bash
git add app/cutover.py tests/test_cutover_build.py
git commit -m "feat: add the isolated cutover worktree"
```

---

### Task 10: Promotion under precheck

**Files:**
- Modify: `app/cutover.py`
- Test: `tests/test_cutover_build.py`

**Interfaces:**
- Produces: `promote(vault, built_commit, source_head, expected_status)`.

`git merge --ff-only` enforces the same guarantees independently: it refuses a non-fast-forward, and Git's checkout safety refuses to overwrite a modified or untracked file. The precheck and the primitive must both agree.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cutover_build.py`:

```python
from app.cutover import promote
from tests.conftest import git_status_bytes


def build_a_commit(vault: Path, head: str, filename: str = "a.md") -> str:
    import subprocess

    with isolated_worktree(vault, head) as scratch:
        (scratch / filename).write_text("changed\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=scratch, check=True)
        subprocess.run(
            ["git", "-c", "user.email=t@e", "-c", "user.name=t",
             "commit", "-q", "-m", "cutover"],
            cwd=scratch,
            check=True,
        )
        return git_head(scratch)


def test_promotion_fast_forwards_the_live_vault(tmp_path: Path):
    vault = git_vault(tmp_path / "vault", {"a.md": "x\n"})
    head = git_head(vault)
    built = build_a_commit(vault, head)

    promote(vault, built, head, git_status_bytes(vault))

    assert git_head(vault) == built
    assert (vault / "a.md").read_text(encoding="utf-8") == "changed\n"


def test_promotion_refuses_a_moved_head(tmp_path: Path):
    import subprocess

    vault = git_vault(tmp_path / "vault", {"a.md": "x\n"})
    head = git_head(vault)
    built = build_a_commit(vault, head)
    (vault / "b.md").write_text("y\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=vault, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@e", "-c", "user.name=t",
         "commit", "-q", "-m", "concurrent"],
        cwd=vault,
        check=True,
    )
    moved = git_head(vault)

    with pytest.raises(CutoverError):
        promote(vault, built, head, git_status_bytes(vault))

    assert git_head(vault) == moved


def test_promotion_refuses_a_changed_status(tmp_path: Path):
    vault = git_vault(tmp_path / "vault", {"a.md": "x\n"})
    head = git_head(vault)
    captured = git_status_bytes(vault)
    built = build_a_commit(vault, head)
    (vault / "stray.md").write_text("s\n", encoding="utf-8")

    with pytest.raises(CutoverError):
        promote(vault, built, head, captured)

    assert git_head(vault) == head


def test_promotion_leaves_an_obstructing_untracked_file_intact(tmp_path: Path):
    vault = git_vault(tmp_path / "vault", {"a.md": "x\n"})
    head = git_head(vault)
    built = build_a_commit(vault, head, filename="new.md")
    (vault / "new.md").write_text("mine\n", encoding="utf-8")

    with pytest.raises(CutoverError):
        promote(vault, built, head, git_status_bytes(vault))

    assert (vault / "new.md").read_text(encoding="utf-8") == "mine\n"
    assert git_head(vault) == head
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `uv run python -m pytest -q tests/test_cutover_build.py`
Expected: collection error, `ImportError: cannot import name 'promote'`.

- [ ] **Step 3: Write the minimal implementation**

Append to `app/cutover.py`:

```python
def promote(
    vault: Path,
    built_commit: str,
    source_head: str,
    expected_status: bytes,
) -> str:
    """Fast-forward the live vault to the commit built in isolation.

    The caller must already have quiesced every writer — OneOS, Hermes, and
    every parser and adapter — and taken the shared action lock. The ref
    update is atomic; the working-tree update is not, which is exactly why
    the writers are stopped.
    """
    if _git(vault, "rev-parse", "HEAD").strip() != source_head:
        raise CutoverError("live HEAD moved since the build; re-run from inventory")
    actual_status = subprocess.run(
        ["git", "status", "--porcelain=v2", "--untracked-files=all"],
        cwd=vault,
        check=True,
        capture_output=True,
    ).stdout
    if actual_status != expected_status:
        raise CutoverError("live status changed since the build; re-run from inventory")

    completed = subprocess.run(
        ["git", "merge", "--ff-only", built_commit],
        cwd=vault,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise CutoverError("fast-forward promotion refused; the vault is unchanged")

    try:
        return _git(vault, "rev-parse", "HEAD").strip()
    except CutoverError as exc:
        raise CutoverCommittedError(
            "the cutover committed but its id could not be read; do not retry"
        ) from exc
```

- [ ] **Step 4: Run the test and confirm it passes**

Run: `uv run python -m pytest -q tests/test_cutover_build.py`
Expected: PASS, 10 tests.

- [ ] **Step 5: Commit**

```bash
git add app/cutover.py tests/test_cutover_build.py
git commit -m "feat: promote the cutover commit under precheck"
```

---

### Task 11: Build ordering — databases first, then mappings, then gates

**Files:**
- Modify: `app/cutover.py`
- Test: `tests/test_cutover_build.py`

**Interfaces:**
- Produces: `build_cutover(vault, manifest_bytes, record)` returning the built commit id.
- Consumes: everything from Tasks 1–10.

Database updates run **before** any directory move, so each approved source-relative path is used verbatim and no path translation is required.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cutover_build.py`:

```python
import sqlite3

from app.cutover import build_cutover
from app.cutover_manifest import (
    ApprovalManifest,
    ApprovalRecord,
    DatabaseTarget,
    Mapping,
    canonical_bytes,
    manifest_digest,
)
from tests.conftest import git_count_commits


def cutover_vault(root: Path) -> Path:
    vault = git_vault(
        root,
        {
            "_system/entities.yaml": "entities:\n  ab:\n    label: A\n",
            "_system/products.yaml": "products:\n  ab:\n    q7:\n      label: Q\n",
            "ab/00-inbox/note.md": "---\nentity: ab\nproduct: q7\n---\n\nthe ab word\n",
        },
    )
    db = vault / "ab" / "books.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE ledger (product TEXT)")
    conn.execute("INSERT INTO ledger VALUES ('q7')")
    conn.commit()
    conn.close()
    import subprocess

    subprocess.run(["git", "add", "-A"], cwd=vault, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@e", "-c", "user.name=t",
         "commit", "-q", "-m", "add db"],
        cwd=vault,
        check=True,
    )
    return vault


def approved(vault: Path) -> tuple[bytes, ApprovalRecord]:
    manifest = ApprovalManifest(
        source_head=git_head(vault),
        mappings=(
            Mapping(axis="entity", old="ab", new="ab-entity"),
            Mapping(axis="product", old="q7", new="q7-product"),
        ),
        databases=(
            DatabaseTarget(path="ab/books.db", table="ledger", column="product"),
        ),
        dispositions=(
            Disposition(path="ab/00-inbox/note.md", line=5, old="ab",
                        kind="incidental"),
        ),
    )
    raw = canonical_bytes(manifest)
    return raw, ApprovalRecord(manifest_sha256=manifest_digest(manifest),
                               approved_by="owner")


def test_build_produces_exactly_one_commit_and_leaves_the_vault_untouched(
    tmp_path: Path,
):
    vault = cutover_vault(tmp_path / "vault")
    head = git_head(vault)
    before = git_count_commits(vault)
    raw, record = approved(vault)

    built = build_cutover(vault, raw, record)

    assert git_head(vault) == head
    assert git_count_commits(vault) == before
    promote(vault, built, head, git_status_bytes(vault))
    assert git_count_commits(vault) == before + 1


def test_build_refuses_a_manifest_that_does_not_match_its_record(tmp_path: Path):
    vault = cutover_vault(tmp_path / "vault")
    raw, _ = approved(vault)
    wrong = ApprovalRecord(manifest_sha256="c" * 64, approved_by="owner")

    with pytest.raises(Exception):
        build_cutover(vault, raw, wrong)

    assert git_is_clean(vault)


def test_database_is_updated_before_the_entity_directory_moves(tmp_path: Path):
    vault = cutover_vault(tmp_path / "vault")
    head = git_head(vault)
    raw, record = approved(vault)

    built = build_cutover(vault, raw, record)
    promote(vault, built, head, git_status_bytes(vault))

    moved = vault / "ab-entity" / "books.db"
    assert moved.is_file()
    conn = sqlite3.connect(moved)
    try:
        assert conn.execute("SELECT product FROM ledger").fetchall() == [
            ("q7-product",)
        ]
    finally:
        conn.close()


def test_ordinary_prose_containing_a_short_identifier_is_untouched(tmp_path: Path):
    vault = cutover_vault(tmp_path / "vault")
    head = git_head(vault)
    raw, record = approved(vault)

    built = build_cutover(vault, raw, record)
    promote(vault, built, head, git_status_bytes(vault))

    note = (vault / "ab-entity" / "00-inbox" / "note.md").read_text(encoding="utf-8")
    assert "entity: ab-entity" in note
    assert "product: q7-product" in note
    assert "the ab word" in note


def test_one_revert_restores_every_identifier(tmp_path: Path):
    import subprocess

    vault = cutover_vault(tmp_path / "vault")
    head = git_head(vault)
    raw, record = approved(vault)
    built = build_cutover(vault, raw, record)
    promote(vault, built, head, git_status_bytes(vault))

    subprocess.run(
        ["git", "-c", "user.email=t@e", "-c", "user.name=t",
         "revert", "--no-edit", built],
        cwd=vault,
        check=True,
        capture_output=True,
    )

    assert (vault / "ab" / "00-inbox" / "note.md").is_file()
    conn = sqlite3.connect(vault / "ab" / "books.db")
    try:
        assert conn.execute("SELECT product FROM ledger").fetchall() == [("q7",)]
    finally:
        conn.close()
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `uv run python -m pytest -q tests/test_cutover_build.py`
Expected: collection error, `ImportError: cannot import name 'build_cutover'`.

- [ ] **Step 3: Write the minimal implementation**

Append to `app/cutover.py`:

```python
import yaml

from .cutover_db import apply_database_mappings, database_residuals
from .cutover_locations import (
    advisory_occurrences,
    rewrite_front_matter_field,
    rewrite_mapping_key,
    rewrite_path_head,
    rewrite_yaml_value_field,
)
from .cutover_inventory import require_clean_entities
from .cutover_manifest import ApprovalRecord, load_manifest, verify_manifest

#: Entity first (its directory move relocates everything beneath it), then the
#: value axes, then workspaces. Within an axis, sorted by old identifier.
_AXIS_ORDER = ("entity", "product", "member", "workspace")


def _mappings_in_order(manifest) -> list:
    return sorted(
        manifest.mappings, key=lambda item: (_AXIS_ORDER.index(item.axis), item.old)
    )


def _apply_entity_mapping(root: Path, old: str, new: str) -> None:
    system = root / "_system"
    for name, indent in (("products.yaml", 2), ("members.yaml", 2)):
        path = system / name
        if path.is_file():
            path.write_text(
                rewrite_mapping_key(
                    path.read_text(encoding="utf-8"), old, new, indent=indent
                ),
                encoding="utf-8",
            )
    entities = system / "entities.yaml"
    if entities.is_file():
        text = rewrite_mapping_key(
            entities.read_text(encoding="utf-8"), old, new, indent=2
        )
        entities.write_text(_insert_former_slug(text, new, old, 4), encoding="utf-8")
    workspaces = system / "workspaces.yaml"
    if workspaces.is_file():
        text = workspaces.read_text(encoding="utf-8")
        for field in ("entity", "primary_entity"):
            text = rewrite_yaml_value_field(text, field, old, new)
        workspaces.write_text(text, encoding="utf-8")
    policy = system / "scripts" / "action-policy.yaml"
    if policy.is_file():
        policy.write_text(
            _rewrite_policy_paths(policy.read_text(encoding="utf-8"), old, new),
            encoding="utf-8",
        )
    for markdown in sorted(root.rglob("*.md")):
        if ".git" in markdown.relative_to(root).parts:
            continue
        text = markdown.read_text(encoding="utf-8")
        rewritten = rewrite_front_matter_field(text, "entity", old, new)
        if rewritten != text:
            markdown.write_text(rewritten, encoding="utf-8")
    for record in sorted(root.rglob("outbox/*.yaml")):
        _rewrite_proposal(record, old, new)
    old_dir = root / old
    if old_dir.is_dir():
        _git(root, "mv", old, new)


def _rewrite_proposal(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    document = yaml.safe_load(text) or {}
    if not isinstance(document, dict):
        return
    changed = False
    if document.get("entity") == old:
        document["entity"] = new
        changed = True
    for field in ("src", "dst"):
        value = document.get(field)
        if isinstance(value, str):
            rewritten = rewrite_path_head(value, old, new)
            if rewritten != value:
                document[field] = rewritten
                changed = True
    if changed:
        path.write_text(
            yaml.safe_dump(document, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )


def _rewrite_policy_paths(text: str, old: str, new: str) -> str:
    """Rewrite the first path component inside every quoted pattern, so an
    allow rule's `paths:` and its `except:` for `.sensitive/` move together.
    Missing one half is the BUILD §4 fail-open."""
    def replace(match: "re.Match[str]") -> str:
        quote, pattern = match.group(1), match.group(2)
        return f"{quote}{rewrite_path_head(pattern, old, new)}{quote}"

    return re.sub(r"([\"'])([^\"']+)\1", replace, text)


def _insert_former_slug(text: str, key: str, old: str, indent: int) -> str:
    """Provenance only, never an alias, and only on the entity and product
    mapping keys that already carry it. Member and workspace entries are list
    items with no key line to anchor to; adding it there would be a registry
    schema change."""
    pattern = re.compile(rf"^(\s*){re.escape(key)}:\s*$")
    out, done = [], False
    for line in text.splitlines(keepends=True):
        out.append(line)
        if not done and pattern.match(line):
            out.append(" " * indent + f"former_slugs: [{old}]\n")
            done = True
    return "".join(out)


def _apply_value_mapping(root: Path, axis: str, old: str, new: str) -> None:
    system = root / "_system"
    if axis == "product":
        registry = system / "products.yaml"
        if registry.is_file():
            text = rewrite_mapping_key(
                registry.read_text(encoding="utf-8"), old, new, indent=4
            )
            registry.write_text(
                _insert_former_slug(text, new, old, 6), encoding="utf-8"
            )
    elif axis == "member":
        registry = system / "members.yaml"
        if registry.is_file():
            registry.write_text(
                rewrite_yaml_value_field(
                    registry.read_text(encoding="utf-8"), "id", old, new
                ),
                encoding="utf-8",
            )
    for markdown in sorted(root.rglob("*.md")):
        if ".git" in markdown.relative_to(root).parts:
            continue
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


def build_cutover(vault: Path, manifest_bytes: bytes, record: ApprovalRecord) -> str:
    """Build the single cutover commit in isolation and return its id."""
    verify_manifest(manifest_bytes, record)
    manifest = load_manifest(manifest_bytes)
    affected = [item.old for item in manifest.mappings if item.axis == "entity"]
    require_clean_entities(vault, affected)

    with isolated_worktree(vault, manifest.source_head) as scratch:
        apply_database_mappings(scratch, manifest.databases, manifest.mappings)
        residual = database_residuals(scratch, manifest.databases, manifest.mappings)
        if residual:
            raise CutoverError(f"database residual after update: {len(residual)} row set(s)")

        for mapping in _mappings_in_order(manifest):
            if mapping.axis == "entity":
                _apply_entity_mapping(scratch, mapping.old, mapping.new)
            elif mapping.axis == "workspace":
                _apply_workspace_mapping(scratch, mapping.old, mapping.new)
            else:
                _apply_value_mapping(scratch, mapping.axis, mapping.old, mapping.new)

        dispositioned = {
            (item.path, item.line, item.old) for item in manifest.dispositions
        }
        olds = {item.old for item in manifest.mappings}
        for occurrence in advisory_occurrences(scratch, olds, enumerated_paths=set()):
            key = (occurrence.path, occurrence.line, occurrence.old)
            if key not in dispositioned:
                raise CutoverError(
                    "undispositioned advisory occurrence; re-run from inventory"
                )

        _git(scratch, "add", "-A")
        _git(scratch, "-c", "user.email=cutover@invalid",
             "-c", "user.name=cutover",
             "commit", "-q", "-m", "cutover: raise registry identifiers to the floor")
        return _git(scratch, "rev-parse", "HEAD").strip()
```

- [ ] **Step 4: Run the test and confirm it passes**

Run: `uv run python -m pytest -q tests/test_cutover_build.py`
Expected: PASS, 15 tests.

If the advisory disposition line numbers in the fixture do not match, print `advisory_occurrences(scratch, olds, set())` and correct the fixture's `Disposition` entries — do **not** loosen the check.

- [ ] **Step 5: Commit**

```bash
git add app/cutover.py tests/test_cutover_build.py
git commit -m "feat: build the single cutover commit in isolation"
```

---

### Task 12: The fail-open guard, provenance scope, and term-collection pin

**Files:**
- Modify: `tests/test_cutover_build.py`
- Verify: `app/cutover.py`

**Interfaces:** none new. This task pins three safety properties the design calls mandatory.

The third is easy to overlook and load-bearing. The design's claim that the combined history audit comes back **clean** after the cutover — with no expected residue — rests entirely on `load_instance_terms` seeding its term set from current registry keys and ids only. Retired identifiers stop being terms, so the history scan never looks for them. If `former_slugs` values, or any other retained provenance, were ever added to term collection, the retired identifiers would be seeded again and the audit would go red. That premise must be pinned **before** the private migration runs, which is why it belongs in Stage A rather than Stage B.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cutover_build.py`:

```python
import textwrap


def policy_vault(root: Path) -> Path:
    return git_vault(
        root,
        {
            "_system/entities.yaml": "entities:\n  ab:\n    label: A\n",
            "_system/members.yaml": "members:\n  ab:\n    - {id: m7, name: M}\n",
            "_system/workspaces.yaml": "workspaces:\n  - {id: w7, entity: ab}\n",
            "_system/scripts/action-policy.yaml": textwrap.dedent(
                """\
                actors:
                  hermes:
                    allow:
                      - {action: read, paths: ["ab/**"], except: ["ab/.sensitive/**"]}
                """
            ),
            "ab/00-inbox/note.md": "---\nentity: ab\nmember: m7\n---\n",
        },
    )


def policy_approved(vault: Path) -> tuple[bytes, ApprovalRecord]:
    manifest = ApprovalManifest(
        source_head=git_head(vault),
        mappings=(
            Mapping(axis="entity", old="ab", new="ab-entity"),
            Mapping(axis="member", old="m7", new="m7-member"),
            Mapping(axis="workspace", old="w7", new="w7-workspace"),
        ),
        databases=(),
        dispositions=(),
    )
    raw = canonical_bytes(manifest)
    return raw, ApprovalRecord(
        manifest_sha256=manifest_digest(manifest), approved_by="owner"
    )


def test_both_halves_of_the_fail_open_rule_are_rewritten(tmp_path: Path):
    vault = policy_vault(tmp_path / "vault")
    head = git_head(vault)
    raw, record = policy_approved(vault)

    built = build_cutover(vault, raw, record)
    promote(vault, built, head, git_status_bytes(vault))

    policy = (vault / "_system" / "scripts" / "action-policy.yaml").read_text(
        encoding="utf-8"
    )
    assert '"ab-entity/**"' in policy
    assert '"ab-entity/.sensitive/**"' in policy
    assert '"ab/**"' not in policy
    assert '"ab/.sensitive/**"' not in policy


def test_former_slugs_is_written_only_on_entity_and_product_keys(tmp_path: Path):
    vault = policy_vault(tmp_path / "vault")
    head = git_head(vault)
    raw, record = policy_approved(vault)

    built = build_cutover(vault, raw, record)
    promote(vault, built, head, git_status_bytes(vault))

    entities = (vault / "_system" / "entities.yaml").read_text(encoding="utf-8")
    members = (vault / "_system" / "members.yaml").read_text(encoding="utf-8")
    workspaces = (vault / "_system" / "workspaces.yaml").read_text(encoding="utf-8")

    assert "former_slugs: [ab]" in entities
    assert "former_slugs" not in members
    assert "former_slugs" not in workspaces
    assert "id: m7-member" in members
    assert "id: w7-workspace" in workspaces
```

- [ ] **Step 1b: Write the term-collection pin**

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

- [ ] **Step 2: Run the tests and confirm they fail or pass for the right reason**

Run: `uv run python -m pytest -q tests/test_cutover_build.py -k "fail_open or former_slugs" tests/test_public_repo_audit.py::test_term_collection_reads_only_registry_keys_and_ids`
Expected: PASS. These pin behaviour Tasks 11 and the existing audit already implement; if any fails, fix the implementation, never the assertion.

- [ ] **Step 3: Prove both tests are real controls by mutation**

Save `app/cutover.py` outside the repository. Then, one at a time:

1. In `_rewrite_policy_paths`, restrict the pattern to `paths` only by changing the regex to `r"(paths: \[)([\"'])([^\"']+)\2"` and rewriting only that group.
   Run: `uv run python -m pytest -q tests/test_cutover_build.py -k fail_open`
   Expected: RED on the `except:` assertion.
2. In `_apply_value_mapping`, add an `_insert_former_slug` call to the `member` branch.
   Run: `uv run python -m pytest -q tests/test_cutover_build.py -k former_slugs`
   Expected: RED on `"former_slugs" not in members`.

3. In `tools/public_repo_audit.py`, extend `load_instance_terms` to also collect
   any `former_slugs` values it encounters.
   Run: `uv run python -m pytest -q tests/test_public_repo_audit.py::test_term_collection_reads_only_registry_keys_and_ids`
   Expected: RED on `"ab" not in short_terms`.

Restore the saved file after each, verify with `cmp`, and re-run to GREEN.

- [ ] **Step 4: Commit**

```bash
git add tests/test_cutover_build.py tests/test_public_repo_audit.py
git commit -m "test: pin the fail-open guard, provenance scope, and term collection"
```

---

### Task 13: The CLI — inventory, dry run, apply

**Files:**
- Modify: `app/cutover.py`
- Test: `tests/test_cutover_build.py`

**Interfaces:**
- Produces: `main(argv)` with subcommands `inventory`, `dry-run`, `apply`.

Dry run is the default posture; `apply` is explicit and additionally requires `--i-have-quiesced-all-writers`, because the working-tree update is not atomic and the action lock does not govern Hermes, parsers, or adapters.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cutover_build.py`:

```python
from app.cutover import main


def test_dry_run_writes_nothing_to_the_live_vault(tmp_path: Path, capsys):
    vault = cutover_vault(tmp_path / "vault")
    head = git_head(vault)
    raw, record = approved(vault)
    manifest_path = tmp_path / "manifest.yaml"
    record_path = tmp_path / "record.yaml"
    manifest_path.write_bytes(raw)
    record_path.write_text(
        yaml.safe_dump(
            {"manifest_sha256": record.manifest_sha256, "approved_by": "owner"}
        ),
        encoding="utf-8",
    )

    code = main(
        ["dry-run", "--vault-root", str(vault),
         "--manifest", str(manifest_path), "--approval", str(record_path)]
    )

    assert code == 0
    assert git_head(vault) == head
    assert git_is_clean(vault)
    assert "DRY RUN" in capsys.readouterr().out


def test_apply_requires_the_quiesce_acknowledgement(tmp_path: Path):
    vault = cutover_vault(tmp_path / "vault")
    raw, record = approved(vault)
    manifest_path = tmp_path / "manifest.yaml"
    record_path = tmp_path / "record.yaml"
    manifest_path.write_bytes(raw)
    record_path.write_text(
        yaml.safe_dump(
            {"manifest_sha256": record.manifest_sha256, "approved_by": "owner"}
        ),
        encoding="utf-8",
    )

    code = main(
        ["apply", "--vault-root", str(vault),
         "--manifest", str(manifest_path), "--approval", str(record_path)]
    )

    assert code == 1
    assert git_is_clean(vault)
```

Add `import yaml` to the test module's imports if it is not already present.

- [ ] **Step 2: Run the test and confirm it fails**

Run: `uv run python -m pytest -q tests/test_cutover_build.py -k "dry_run or quiesce"`
Expected: collection error, `ImportError: cannot import name 'main'`.

- [ ] **Step 3: Write the minimal implementation**

Append to `app/cutover.py`:

```python
import argparse

from .cutover_db import database_schema_inventory
from .cutover_inventory import untracked_or_ignored_paths


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="oneos cutover",
        description="Raise registry identifiers to the five-character floor.",
    )
    parser.add_argument(
        "command", choices=("inventory", "dry-run", "apply")
    )
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
            for path, tables in database_schema_inventory(vault).items():
                for table, columns in tables.items():
                    print(f"database {path} {table}: {', '.join(columns)}")
            print("[INVENTORY] read-only; nothing was written")
            return 0

        if not args.manifest or not args.approval:
            print("[ABORTED] --manifest and --approval are required")
            return 1
        manifest_bytes = Path(args.manifest).read_bytes()
        record = _load_approval(Path(args.approval))

        if args.command == "dry-run":
            manifest = load_manifest(manifest_bytes)
            verify_manifest(manifest_bytes, record)
            for entity in [m.old for m in manifest.mappings if m.axis == "entity"]:
                for stranded in untracked_or_ignored_paths(vault, entity):
                    print(f"# unmigratable: {stranded}")
            for mapping in _mappings_in_order(manifest):
                print(f"{mapping.axis}: {mapping.old} -> {mapping.new}")
            print("\n[DRY RUN] re-run with apply to execute")
            return 0

        if not args.i_have_quiesced_all_writers:
            print(
                "[ABORTED] refusing to promote without "
                "--i-have-quiesced-all-writers: stop OneOS, Hermes, and every "
                "parser and adapter first"
            )
            return 1

        source_head = _git(vault, "rev-parse", "HEAD").strip()
        expected_status = subprocess.run(
            ["git", "status", "--porcelain=v2", "--untracked-files=all"],
            cwd=vault,
            check=True,
            capture_output=True,
        ).stdout
        built = build_cutover(vault, manifest_bytes, record)
        promoted = promote(vault, built, source_head, expected_status)
        print(f"[DONE] cutover promoted as {promoted}")
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

Run: `uv run python -m pytest -q tests/test_cutover_build.py`
Expected: PASS, 17 tests.

- [ ] **Step 5: Commit**

```bash
git add app/cutover.py tests/test_cutover_build.py
git commit -m "feat: add the cutover CLI with an explicit quiesce gate"
```

---

### Task 14: Mutation campaign and closing evidence

**Files:**
- Create: `docs/superpowers/plans/2026-08-26-short-identifier-cutover-mutation-ledger.md`
- Verify: every module from Tasks 1–13

**Interfaces:** none. This task produces the RED→GREEN evidence the design makes mandatory.

- [ ] **Step 1: Run the full suite and record the baseline**

Run: `uv run python -m pytest -q`
Expected: PASS. Record the exact count; it must exceed the 1,476 starting baseline by the number of tests added.

- [ ] **Step 2: Run each mutation**

For each row: copy the target file outside the repository, apply only the stated change, run only the stated node, confirm RED for the stated reason, restore the file, verify with `cmp`, and re-run to GREEN. Record every result in the ledger.

| # | Target | Mutation | Node that must go RED |
|---|---|---|---|
| 1 | `app/identifiers.py` | `IDENTIFIER_MINIMUM_LENGTH = 4` | `tests/test_cutover_identifiers.py::test_floor_is_one_above_the_audit_long_term_threshold` |
| 2 | `app/cutover_db.py` | In `apply_database_mappings`, ignore `target.column` and update every column whose name matches the target's | `tests/test_cutover_db.py::test_only_the_allowlisted_column_is_updated` |
| 3 | `app/cutover_db.py` | In `_resolve`, ignore `target.path` and use the first `books.db` found under `root` | `tests/test_cutover_db.py::test_a_matching_column_name_in_another_database_is_untouched` |
| 4 | `app/cutover.py` | In `build_cutover`, delete the `database_residuals` call and its refusal | `tests/test_cutover_db.py::test_residuals_report_a_remaining_old_value` (re-point at the build path) |
| 5 | `app/cutover_locations.py` | In `boundaried`, drop both lookarounds | `tests/test_cutover_locations.py::test_advisory_does_not_report_a_longer_token` |
| 6 | `app/cutover_inventory.py` | In `check_collisions`, delete the existing-identifier check | `tests/test_cutover_inventory.py::test_a_new_value_colliding_with_an_existing_identifier_is_refused` |
| 7 | `app/cutover.py` | In `build_cutover`, hoist every `_apply_*` call into a list built before any is applied, then apply them | `tests/test_cutover_build.py::test_ordinary_prose_containing_a_short_identifier_is_untouched` |
| 8 | `app/cutover.py` | In `build_cutover`, delete the `require_clean_entities` call | `tests/test_cutover_inventory.py::test_require_clean_entities_raises_for_an_affected_entity` (re-point at the build path) |
| 9 | `app/cutover.py` | In `_apply_value_mapping`, add the product branch's workspace `id:` rewrite | `tests/test_cutover_build.py::test_former_slugs_is_written_only_on_entity_and_product_keys` and `tests/test_cutover_locations.py::test_product_axis_never_claims_a_workspace_id` |
| 10 | `app/cutover.py` | In `promote`, delete the HEAD comparison | `tests/test_cutover_build.py::test_promotion_refuses_a_moved_head` |
| 11 | `app/cutover.py` | In `_apply_value_mapping`, call `_insert_former_slug` in the `member` branch | `tests/test_cutover_build.py::test_former_slugs_is_written_only_on_entity_and_product_keys` |
| 12 | `app/cutover_locations.py` | In `advisory_occurrences`, exempt every line, not only `former_slugs` ones | `tests/test_cutover_locations.py::test_advisory_reports_a_bare_token_outside_the_enumerated_locations` |
| 13 | `app/cutover.py` | In `_rewrite_policy_paths`, rewrite only `paths:` patterns | `tests/test_cutover_build.py::test_both_halves_of_the_fail_open_rule_are_rewritten` |
| 14 | `tools/public_repo_audit.py` | In `load_instance_terms`, also collect `former_slugs` values | `tests/test_public_repo_audit.py::test_term_collection_reads_only_registry_keys_and_ids` |

- [ ] **Step 3: Write the ledger**

Create `docs/superpowers/plans/2026-08-26-short-identifier-cutover-mutation-ledger.md` with one section per row recording: the exact mutation applied, the exact command run, the RED output's assertion line, the `cmp` restoration proof, and the GREEN re-run. A count without its command, or a mutation without its exact failing test, is not evidence.

- [ ] **Step 4: Run the closing suite and the publication gates**

```bash
uv run python -m pytest -q
uv run python -m tools.public_repo_audit --repo . --history
tools/run_gitleaks.sh .
git diff --check
git status --porcelain
```

Expected: suite PASS with the recorded count; audit `CLEAN`; Gitleaks no leaks; diff check clean; status empty.

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/plans/2026-08-26-short-identifier-cutover-mutation-ledger.md
git commit -m "docs: record the cutover mutation ledger"
```

---

## Deferred to Stage B

Not in this plan, and not to be started until the private cutover has been promoted and verified:

- The five-character floor at [app/entities.py:12](app/entities.py:12), [app/vault.py:31](app/vault.py:31), [app/destinations.py:57](app/destinations.py:57), [app/rename.py:46](app/rename.py:46), and [app/action_receipts.py:32](app/action_receipts.py:32), each consuming `app/identifiers.meets_floor`.
- A structural test asserting no module defines its own registry-identifier length rule.
- Renaming the sub-floor synthetic fixtures — the dominant one is a four-character entity slug appearing in the low hundreds of occurrences.

The term-collection pin is deliberately **not** deferred; it is Task 12, because the migration's premise depends on it holding beforehand.

## Handoff

Return the recorded base SHA, branch, worktree, commit list, each task's RED and GREEN output, the mutation ledger, the full public suite count with its exact command, the publication audit and Gitleaks results, `git diff --check`, and the final `git status --porcelain`. State explicitly that no live vault was accessed and that no private gate was run.

The trusted local reviewer — not the external agent — runs the vault-seeded audits, the private suite, `check_v2`, and the opaque byte-preservation comparison, and performs the migration itself.
