# S4 Fresh, Collision-Safe Proposals Implementation Plan

> **Historical execution plan:** S4 is implemented and merged through PR #9 at
> `3c56119`. Retain this file for design/test rationale; do not run its branch,
> commit, stop, or test-count instructions again. Current state is in
> `BUILD.md`, `docs/STATUS.md`, and `docs/SAFETY-FOUNDATION-S1-S4.md`.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every proposal collision-safe and bind each classification approval to the exact source receipt bytes present when the proposal was created.

**Architecture:** A dependency-free proposal-identity module owns readable cryptographic IDs and strict filename equality. Classification proposals persist a validated exact-byte SHA-256; approval reads one no-follow snapshot, verifies it before mutation, and derives the committed content from that same snapshot. The FastAPI route exposes only typed missing/stale refusals, leaving S5 transaction isolation and general S6 presentation untouched.

**Tech Stack:** Python 3.12+, FastAPI, Jinja2, PyYAML, pytest, standard-library `hashlib`, `os`, `secrets`, `stat`, and Git CLI.

**Spec:** `docs/superpowers/specs/2026-08-15-s4-fresh-collision-safe-proposals-design.md`

## Global Constraints

- S4 branches from `origin/main` at `faa3894`, which includes merged S1 containment and the standalone Gitleaks baseline hotfix; do not add commits to PR #6 or replay old S1 branch commits.
- Proposal IDs are exactly `YYYYMMDDTHHMMSS-<32 lowercase hex>`; entropy is `secrets.token_hex(16)`.
- The stored proposal ID must exactly equal the proposal filename stem.
- Every classification proposal requires `source_sha256` matching `^[0-9a-f]{64}$`.
- Pre-S4 classification proposals without `source_sha256` fail closed and must be recreated.
- Approval verifies a single no-follow source-byte snapshot before any filesystem or Git mutation and consumes that same snapshot.
- Changed or missing sources preserve the proposal, source precondition, destination absence, Git `HEAD`, index, worktree state, and unrelated bytes.
- Preserve S2 request-local scope, S3 canonical destinations, lexical no-follow boundaries, exclusive proposal creation, and mixed outbox action dispatch.
- Do not implement S5 Git transaction isolation, rollback, or path-limited staging.
- Do not implement general S6 error presentation; add only missing/stale approval alerts.
- Do not modify the S1 folder-adapter raw-archive issue.
- Add no dependency, instance-specific value, physical subfolder, or private-vault content.
- Keep Grey Matter read-only and require byte-identical pre/post Git status and binary diffs.
- Follow strict red-green-refactor TDD; every production behavior begins with a focused failing test.

---

### Task 1: Shared readable, collision-safe proposal identity

**Files:**
- Create: `app/proposal_identity.py`
- Create: `tests/test_proposal_identity.py`
- Modify: `app/registry.py:9-29,182-232`
- Modify: `tests/test_registry.py`

**Interfaces:**
- Produces: `ProposalIdentityError(ValueError)`.
- Produces: `generate_proposal_id(created: datetime) -> str`.
- Produces: `proposal_id_candidates(created: datetime) -> Iterator[str]`, yielding exactly four independently random candidates.
- Produces: `require_proposal_id(value: object) -> str`.
- Produces: `require_proposal_identity(path: Path, record_id: object) -> str`.
- Consumes later: Task 2 imports all four functions into `app/outbox.py`.

- [ ] **Step 1: Write failing identity behavior tests**

Create `tests/test_proposal_identity.py`:

```python
from datetime import datetime
from pathlib import Path

import pytest

import app.proposal_identity as identity


def test_id_combines_readable_timestamp_with_128_bit_random_suffix(monkeypatch):
    monkeypatch.setattr(identity.secrets, "token_hex", lambda size: "ab" * size)

    proposal_id = identity.generate_proposal_id(datetime(2026, 8, 15, 9, 7, 3))

    assert proposal_id == "20260815T090703-" + "ab" * 16


@pytest.mark.parametrize(
    "value",
    [
        None,
        7,
        "20260815T090703-delete",
        "20260815T090703-" + "AB" * 16,
        "20261315T090703-" + "ab" * 16,
        "20260815T250703-" + "ab" * 16,
        "20260815T090703-" + "ab" * 15,
        "../20260815T090703-" + "ab" * 16,
    ],
)
def test_id_validation_rejects_noncanonical_values(value):
    with pytest.raises(identity.ProposalIdentityError):
        identity.require_proposal_id(value)


def test_record_id_must_equal_yaml_filename_stem():
    proposal_id = "20260815T090703-" + "ab" * 16
    with pytest.raises(identity.ProposalIdentityError):
        identity.require_proposal_identity(
            Path("/vault/demo/outbox/20260815T090703-" + "cd" * 16 + ".yaml"),
            proposal_id,
        )
```

- [ ] **Step 2: Run identity tests and confirm RED**

Run:

```bash
uv run pytest tests/test_proposal_identity.py -q
```

Expected: collection fails because `app.proposal_identity` does not exist.

- [ ] **Step 3: Implement the minimal identity module**

Create `app/proposal_identity.py` with this behavior:

```python
from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
import re
import secrets


_PROPOSAL_ID = re.compile(
    r"^(?P<timestamp>[0-9]{8}T[0-9]{6})-(?P<random>[0-9a-f]{32})$"
)
PROPOSAL_ID_ATTEMPTS = 4


class ProposalIdentityError(ValueError):
    pass


def generate_proposal_id(created: datetime) -> str:
    return f"{created:%Y%m%dT%H%M%S}-{secrets.token_hex(16)}"


def proposal_id_candidates(created: datetime) -> Iterator[str]:
    for _ in range(PROPOSAL_ID_ATTEMPTS):
        yield generate_proposal_id(created)


def require_proposal_id(value: object) -> str:
    if not isinstance(value, str):
        raise ProposalIdentityError("proposal id must be a string")
    match = _PROPOSAL_ID.fullmatch(value)
    if match is None:
        raise ProposalIdentityError("proposal id is non-canonical")
    try:
        datetime.strptime(match.group("timestamp"), "%Y%m%dT%H%M%S")
    except ValueError as exc:
        raise ProposalIdentityError("proposal timestamp is invalid") from exc
    return value


def require_proposal_identity(path: Path, record_id: object) -> str:
    proposal_id = require_proposal_id(record_id)
    if path.name != f"{proposal_id}.yaml" or path.stem != proposal_id:
        raise ProposalIdentityError("proposal id does not match its filename")
    return proposal_id
```

- [ ] **Step 4: Run identity tests and confirm GREEN**

Run:

```bash
uv run pytest tests/test_proposal_identity.py -q
```

Expected: all identity tests pass.

- [ ] **Step 5: Write failing registry collision and identity tests**

Add to `tests/test_registry.py` a fixed clock and these behaviors:

```python
from datetime import datetime


class _FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 8, 15, 9, 7, 3, tzinfo=tz)


def test_same_second_delete_proposals_are_distinct_and_preserved(
    tmp_path, monkeypatch
):
    vault = _products_vault(tmp_path, referenced=False)
    scope = Scope(vault, "demo")
    monkeypatch.setattr(registry, "datetime", _FixedDatetime)

    first = propose_delete(scope, "product", "widgetx")
    second = propose_delete(scope, "product", "widgetx")

    assert first.id != second.id
    assert first.path != second.path
    assert first.path.exists() and second.path.exists()
    assert first.path.stem == first.id
    assert second.path.stem == second.id


def test_delete_record_id_must_equal_filename(tmp_path):
    vault = _products_vault(tmp_path, referenced=False)
    scope = Scope(vault, "demo")
    prop = propose_delete(scope, "product", "widgetx")
    record = yaml.safe_load(prop.path.read_text(encoding="utf-8"))
    record["id"] = "20260815T090703-" + "ab" * 16
    prop.path.write_text(yaml.safe_dump(record), encoding="utf-8")

    with pytest.raises(RegistryError):
        get_delete_proposal(scope, prop.path.stem)
```

- [ ] **Step 6: Run registry tests and confirm RED**

Run:

```bash
uv run pytest tests/test_registry.py \
  -k 'same_second_delete or delete_record_id' -q
```

Expected: the first test overwrites/collides under the frozen clock or produces
the old `-delete` ID, and the mismatch test is accepted.

- [ ] **Step 7: Adopt shared identity in registry delete proposals**

In `app/registry.py`:

- remove `re` and `_PROPOSAL_ID`;
- import `ProposalIdentityError`, `proposal_id_candidates`,
  `require_proposal_id`, and `require_proposal_identity`;
- have `_delete_proposal_path()` call `require_proposal_id()` and translate
  `ProposalIdentityError` to `RegistryError("invalid delete proposal id")`;
- capture `created_at = datetime.now()` once;
- generate candidates from `proposal_id_candidates(created_at)`;
- write with `path.open("x", encoding="utf-8")`;
- continue to the next candidate only on `FileExistsError`;
- raise `RegistryError("unable to allocate a unique delete proposal id")` after
  all four collisions; and
- in `get_delete_proposal()`, call `require_proposal_identity(path, rec.get("id"))`
  before action/entity dispatch, translating its exception to `RegistryError`.

The core creation loop is:

```python
created_at = datetime.now()
outbox = scope.resolve("outbox")
outbox.mkdir(parents=True, exist_ok=True)
for pid in proposal_id_candidates(created_at):
    path = _delete_proposal_path(scope, pid)
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
    return DeleteProposal(pid, path, entity, kind, slug, report.total, report.sources)
raise RegistryError("unable to allocate a unique delete proposal id")
```

- [ ] **Step 8: Run identity and registry suites**

Run:

```bash
uv run pytest tests/test_proposal_identity.py tests/test_registry.py -q
```

Expected: all tests pass.

- [ ] **Step 9: Commit Task 1**

```bash
git add app/proposal_identity.py app/registry.py \
  tests/test_proposal_identity.py tests/test_registry.py
git commit -m "feat: add collision-safe proposal identities"
```

---

### Task 2: Persist exact source hashes and validate stored classification records

**Files:**
- Modify: `app/outbox.py:11-207`
- Modify: `tests/test_outbox.py`
- Modify: `tests/test_app.py` (classification proposal fixtures only)

**Interfaces:**
- Consumes: Task 1 `proposal_id_candidates()` and `require_proposal_identity()`.
- Produces: `Proposal.source_sha256: str`.
- Produces: `_read_no_follow_bytes(path: Path) -> bytes`, which raises
  `FileNotFoundError` for absence and `CrossScopeError` for redirected or
  non-regular objects.
- Produces: strict classification loading that validates identity before mixed
  `classify`/`delete` dispatch and requires a lowercase 64-hex digest.

- [ ] **Step 1: Write failing same-second and exact-byte hash tests**

Add to `tests/test_outbox.py`:

```python
import hashlib
import app.proposal_identity as proposal_identity


def test_same_second_classification_proposals_are_distinct_and_preserved(
    tmp_path, monkeypatch
):
    vault = _vault(tmp_path)
    scope = Scope(vault, "demo")
    source = scope.resolve("00-inbox", "active", "note.md")
    monkeypatch.setattr(outbox, "datetime", _FixedDatetime)
    suffixes = iter(("11" * 16, "22" * 16))
    monkeypatch.setattr(
        proposal_identity.secrets, "token_hex", lambda size: next(suffixes)
    )

    first = propose_classification(
        scope, source, module="11-knowledge", sub="kb"
    )
    second = propose_classification(
        scope, source, module="11-knowledge", sub="kb"
    )

    assert first.id == "20260102T030405-" + "11" * 16
    assert second.id == "20260102T030405-" + "22" * 16
    assert first.path != second.path
    assert first.path.exists() and second.path.exists()
    assert len(list(scope.resolve("outbox").glob("*.yaml"))) == 2


def test_proposal_hash_is_sha256_of_exact_receipt_bytes(tmp_path):
    vault = _vault(tmp_path)
    scope = Scope(vault, "demo")
    source = scope.resolve("00-inbox", "active", "note.md")
    exact = source.read_bytes()

    prop = propose_classification(
        scope, source, module="11-knowledge", sub="kb"
    )
    record = yaml.safe_load(prop.path.read_text(encoding="utf-8"))

    assert record["source_sha256"] == hashlib.sha256(exact).hexdigest()
    assert prop.source_sha256 == hashlib.sha256(exact).hexdigest()
```

- [ ] **Step 2: Run the two tests and confirm RED**

Run:

```bash
uv run pytest tests/test_outbox.py \
  -k 'same_second_classification or exact_receipt_bytes' -q
```

Expected: IDs still contain the source stem and `source_sha256` is absent.

- [ ] **Step 3: Add no-follow byte snapshots, hash persistence, and unique creation**

In `app/outbox.py`:

- import `hashlib`, `os`, and `stat`;
- import `ProposalIdentityError`, `proposal_id_candidates`, and
  `require_proposal_identity`;
- add `source_sha256: str` to `Proposal` immediately after `src`;
- capture `created_at = datetime.now()` once;
- read one exact-byte source snapshot after S3 destination resolution;
- set `source_sha256 = hashlib.sha256(source_bytes).hexdigest()`;
- generate IDs with Task 1 candidates and retain exclusive `"x"` creation; and
- raise `OutboxError("unable to allocate a unique classification proposal id")`
  after four collisions.

Use this no-follow primitive:

```python
def _read_no_follow_bytes(path: Path) -> bytes:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise CrossScopeError("source receipt is redirected or unsafe") from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise CrossScopeError("source receipt is not a regular file")
        with os.fdopen(descriptor, "rb", closefd=True) as stream:
            descriptor = -1
            return stream.read()
    finally:
        if descriptor >= 0:
            os.close(descriptor)
```

At proposal creation, translate a `FileNotFoundError` race to
`OutboxDestinationError("source receipt is missing")` before creating the
outbox directory or proposal.

- [ ] **Step 4: Run the two tests and confirm GREEN**

```bash
uv run pytest tests/test_outbox.py \
  -k 'same_second_classification or exact_receipt_bytes' -q
```

Expected: both tests pass.

- [ ] **Step 5: Write failing strict record-validation tests**

Add parameterized tests to `tests/test_outbox.py`:

```python
@pytest.mark.parametrize(
    "source_hash",
    [None, 7, "a" * 63, "a" * 65, "A" * 64, "g" * 64],
)
def test_classification_hash_must_be_lowercase_sha256(two_entity_vault, source_hash):
    scope = Scope(two_entity_vault, "alpha")
    record = _canonical_alpha_record(scope)
    if source_hash is None:
        record.pop("source_sha256")
    else:
        record["source_sha256"] = source_hash
    _write_record(scope, f"{record['id']}.yaml", yaml.safe_dump(record))

    _assert_destination_error(lambda: load_proposals(scope))


@pytest.mark.parametrize(
    "filename,stored_id",
    [
        ("legacy.yaml", "legacy"),
        (
            "20260815T090703-" + "11" * 16 + ".yaml",
            "20260815T090703-" + "22" * 16,
        ),
        ("20261315T090703-" + "11" * 16 + ".yaml", "20261315T090703-" + "11" * 16),
    ],
)
def test_classification_id_and_filename_must_be_canonical(
    two_entity_vault, filename, stored_id
):
    scope = Scope(two_entity_vault, "alpha")
    record = _canonical_alpha_record(scope)
    record["id"] = stored_id
    _write_record(scope, filename, yaml.safe_dump(record))

    _assert_destination_error(lambda: load_proposals(scope))
```

Change `_canonical_alpha_record` to accept `scope: Scope`, use a valid literal
ID (`20260815T090703-` plus `11` repeated 16 times), and populate
`source_sha256` from the actual alpha fixture bytes. Update every existing
classification fixture whose test target is not identity/hash so it carries a
valid matching ID, filename, and exact source hash. Add `source_sha256` to every
direct `Proposal(...)` construction. Replace static forged YAML with
helper-built YAML where the hash depends on fixture bytes.

In `tests/test_app.py`, update preloaded classification records the same way:
valid ID grammar, matching filename, and SHA-256 of that record's canonical
receipt. Registry-delete fixtures remain hashless but receive valid matching
IDs.

- [ ] **Step 6: Run strict validation tests and confirm RED**

```bash
uv run pytest tests/test_outbox.py \
  -k 'hash_must_be or id_and_filename' -q
```

Expected: missing/malformed hashes and mismatched identity are currently loaded.

- [ ] **Step 7: Enforce identity and hash before mixed-action dispatch**

In `load_proposals()`:

1. retain `_require_outbox_path()` before reading;
2. parse a mapping;
3. call `require_proposal_identity(path, record.get("id"))` and translate
   `ProposalIdentityError` to `OutboxDestinationError("proposal identity is invalid")`;
4. then inspect `action` and skip only a valid `delete` record; and
5. for `classify`, call `_to_proposal()` which requires `source_sha256`.

Add:

```python
_SOURCE_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _required_source_hash(record: dict) -> str:
    value = record.get("source_sha256")
    if not isinstance(value, str) or _SOURCE_SHA256.fullmatch(value) is None:
        raise OutboxDestinationError("proposal source hash is malformed")
    return value
```

Have `_to_proposal()` validate identity even when called directly for a newly
created record, and assign `source_sha256=_required_source_hash(record)`.

- [ ] **Step 8: Run outbox, registry, and route regressions**

```bash
uv run pytest tests/test_proposal_identity.py tests/test_outbox.py \
  tests/test_registry.py tests/test_app.py -q
```

Expected: all tests pass, including mixed delete-record skipping and S2/S3
scope/destination regressions.

- [ ] **Step 9: Commit Task 2**

```bash
git add app/outbox.py tests/test_outbox.py tests/test_app.py
git commit -m "feat: bind proposals to exact source hashes"
```

---

### Task 3: Refuse stale or missing approval from one verified snapshot

**Files:**
- Modify: `app/outbox.py:28-37,251-305`
- Modify: `tests/test_outbox.py`

**Interfaces:**
- Produces: `ProposalFreshnessError(OutboxError)`.
- Produces: `MissingProposalSource(ProposalFreshnessError)`.
- Produces: `StaleProposalSource(ProposalFreshnessError)`.
- Changes: `approve(scope: Scope, proposal_id: str) -> Proposal` verifies
  `source_sha256` before any mutation and writes transformed bytes derived from
  the verified snapshot.

- [ ] **Step 1: Add a reusable no-mutation snapshot helper in tests**

Add to `tests/test_outbox.py`:

```python
import subprocess


def _approval_state(vault: Path):
    def git_bytes(*args: str) -> bytes:
        return subprocess.run(
            ["git", *args], cwd=vault, check=True, capture_output=True
        ).stdout

    return {
        "head": git_bytes("rev-parse", "HEAD"),
        "status": git_bytes("status", "--porcelain=v1", "-z"),
        "index": git_bytes("diff", "--cached", "--binary"),
        "worktree": git_bytes("diff", "--binary"),
        "tree": _vault_tree(vault),
    }
```

- [ ] **Step 2: Write failing changed-source refusal test**

```python
def test_approval_refuses_changed_source_without_any_added_mutation(tmp_path):
    vault = _vault(tmp_path)
    scope, prop = _propose(vault)
    source = scope.resolve("00-inbox", "active", "note.md")
    source.write_bytes(source.read_bytes() + b"changed-after-proposal\n")
    proposal_bytes = prop.path.read_bytes()
    before = _approval_state(vault)

    with pytest.raises(outbox.StaleProposalSource):
        approve(scope, prop.id)

    assert _approval_state(vault) == before
    assert prop.path.read_bytes() == proposal_bytes
    assert source.exists()
    assert not scope.resolve("11-knowledge", "active", "note.md").exists()
```

- [ ] **Step 3: Run changed-source test and confirm RED**

```bash
uv run pytest tests/test_outbox.py \
  -k 'refuses_changed_source_without_any_added_mutation' -q
```

Expected: current approval moves and commits the changed source.

- [ ] **Step 4: Implement typed hash freshness refusal before mutation**

Add the exception hierarchy exactly as specified. In `approve()`:

```python
prop = _require_destination(scope, get_proposal(scope, proposal_id))
vault = scope.root
src = scope.resolve_stored(prop.src)
dst = scope.resolve_stored(prop.dst)
try:
    source_bytes = _read_no_follow_bytes(src)
except FileNotFoundError as exc:
    raise MissingProposalSource("proposal source is missing") from exc
actual_sha256 = hashlib.sha256(source_bytes).hexdigest()
if actual_sha256 != prop.source_sha256:
    raise StaleProposalSource("proposal source has changed")
try:
    approved_bytes = _apply_sub(
        source_bytes.decode("utf-8"), prop.sub
    ).encode("utf-8")
except UnicodeDecodeError as exc:
    raise OutboxDestinationError("proposal source is not UTF-8 markdown") from exc
```

Do not call `_git`, `write_*`, `unlink`, or any mutating path operation before
these checks finish.

- [ ] **Step 5: Run changed-source test and confirm GREEN**

```bash
uv run pytest tests/test_outbox.py \
  -k 'refuses_changed_source_without_any_added_mutation' -q
```

Expected: it passes.

- [ ] **Step 6: Write failing missing-source refusal test**

```python
def test_approval_refuses_missing_source_and_preserves_proposal(tmp_path):
    vault = _vault(tmp_path)
    scope, prop = _propose(vault)
    source = scope.resolve("00-inbox", "active", "note.md")
    source.unlink()
    proposal_bytes = prop.path.read_bytes()
    before = _approval_state(vault)

    with pytest.raises(outbox.MissingProposalSource):
        approve(scope, prop.id)

    assert _approval_state(vault) == before
    assert prop.path.read_bytes() == proposal_bytes
    assert not scope.resolve("11-knowledge", "active", "note.md").exists()
```

- [ ] **Step 7: Run missing-source test and confirm RED, then GREEN**

Run before the `FileNotFoundError` translation is present and observe the wrong
exception, implement the translation shown in Step 4, then rerun:

```bash
uv run pytest tests/test_outbox.py \
  -k 'refuses_missing_source_and_preserves_proposal' -q
```

Expected final result: pass.

- [ ] **Step 8: Write failing verified-snapshot consumption test**

This test catches a second content read after freshness verification:

```python
def test_approval_commits_bytes_from_verified_snapshot(tmp_path, monkeypatch):
    vault = _vault(tmp_path)
    scope, prop = _propose(vault)
    source = scope.resolve("00-inbox", "active", "note.md")
    reviewed_marker = b"Randomised trial protocol body."
    raced_marker = b"replacement-after-verification"
    real_git = outbox._git

    def race_before_move(root, *args):
        if args[:1] == ("mv",):
            source.write_bytes(source.read_bytes().replace(reviewed_marker, raced_marker))
        return real_git(root, *args)

    monkeypatch.setattr(outbox, "_git", race_before_move)

    approve(scope, prop.id)

    destination = scope.resolve("11-knowledge", "active", "note.md")
    assert reviewed_marker in destination.read_bytes()
    assert raced_marker not in destination.read_bytes()
    assert git_is_clean(vault)
```

- [ ] **Step 9: Run snapshot test and confirm RED**

```bash
uv run pytest tests/test_outbox.py \
  -k 'commits_bytes_from_verified_snapshot' -q
```

Expected: current `dst.read_text()` consumes the replacement bytes.

- [ ] **Step 10: Make mutation consume only verified bytes**

Immediately before mutation, call `_require_destination(scope, prop)` and
`_require_outbox_path(scope, prop.path, require_leaf=True)` again. Retain the
existing `git mv`, but replace the destination reread with:

```python
_git(vault, "mv", prop.src, prop.dst)
dst.write_bytes(approved_bytes)
_git(vault, "add", prop.dst)
_require_outbox_path(scope, prop.path, require_leaf=True).unlink()
_git(
    vault,
    "commit",
    "-q",
    "-m",
    f"outbox: approve {prop.id} ({prop.src} → {prop.dst})",
)
```

This is S4 source consumption only. Do not add rollback or alternate index
management.

- [ ] **Step 11: Run focused freshness and full outbox tests**

```bash
uv run pytest tests/test_outbox.py \
  -k 'changed_source or missing_source or verified_snapshot or approve or revert' -q
uv run pytest tests/test_outbox.py -q
```

Expected: all pass, including successful approval and revert regressions.

- [ ] **Step 12: Commit Task 3**

```bash
git add app/outbox.py tests/test_outbox.py
git commit -m "feat: refuse stale classification approvals"
```

---

### Task 4: Show freshness-specific approval errors without expanding S6

**Files:**
- Modify: `app/main.py:18-30,136-174`
- Modify: `templates/blocks/outbox_list.html:1-24`
- Modify: `tests/test_app.py`

**Interfaces:**
- Consumes: Task 3 `MissingProposalSource` and `StaleProposalSource`.
- Changes: `_outbox_list(request, scope, *, approval_error: str | None = None)`.
- Produces: an outbox partial with a visible `role="alert"` only for the two
  freshness refusals.

- [ ] **Step 1: Write failing route tests for stale and missing sources**

Add a helper and parameterized test to `tests/test_app.py`:

```python
@pytest.mark.parametrize(
    "precondition,expected",
    [
        (
            "changed",
            "Approval refused: source changed since this proposal was created. "
            "Create a fresh proposal.",
        ),
        (
            "missing",
            "Approval refused: source is missing. Restore it or reject the proposal.",
        ),
    ],
)
def test_approval_route_visibly_refuses_unfresh_source(
    client, precondition, expected
):
    outbox_dir = client.vault / "alpha/outbox"
    for path in outbox_dir.glob("*.yaml"):
        path.unlink()
    response = client.post(
        "/triage/alpha/propose",
        data={"filename": "marker.md", "module": "02-work", "sub": "general"},
    )
    assert response.status_code == 200
    (proposal_path,) = tuple(outbox_dir.glob("*.yaml"))
    record = yaml.safe_load(proposal_path.read_text(encoding="utf-8"))
    source = client.vault / "alpha/00-inbox/active/marker.md"
    if precondition == "changed":
        source.write_bytes(source.read_bytes() + b"changed-after-proposal\n")
    else:
        source.unlink()
    head_before = git_head(client.vault)
    proposal_before = proposal_path.read_bytes()

    refusal = client.post(
        "/outbox/alpha/approve", data={"id": record["id"]}
    )

    assert refusal.status_code == 200
    assert 'role="alert"' in refusal.text
    assert expected in refusal.text
    assert record["id"] in refusal.text
    assert proposal_path.read_bytes() == proposal_before
    assert git_head(client.vault) == head_before
    assert not (client.vault / "alpha/02-work/active/marker.md").exists()
```

- [ ] **Step 2: Run route tests and confirm RED**

```bash
uv run pytest tests/test_app.py \
  -k 'visibly_refuses_unfresh_source' -q
```

Expected: the route silently catches `OutboxError`; no alert appears.

- [ ] **Step 3: Implement only freshness-specific route copy**

Import the two typed exceptions. Change `_outbox_list()` to pass
`approval_error` into the template:

```python
def _outbox_list(
    request: Request,
    scope: Scope,
    *,
    approval_error: str | None = None,
) -> HTMLResponse:
    selected = scope.current_entity()
    props = [(p, preview_diff(scope, p)) for p in load_proposals(scope)]
    return templates.TemplateResponse(
        request,
        "blocks/outbox_list.html",
        {"entity": selected, "props": props, "approval_error": approval_error},
    )
```

Map only the two exceptions in `outbox_approve()`:

```python
approval_error = None
try:
    approve(scope, id)
except MissingProposalSource:
    approval_error = (
        "Approval refused: source is missing. Restore it or reject the proposal."
    )
except StaleProposalSource:
    approval_error = (
        "Approval refused: source changed since this proposal was created. "
        "Create a fresh proposal."
    )
except OutboxError:
    pass
return _outbox_list(request, scope, approval_error=approval_error)
```

At the top of `templates/blocks/outbox_list.html`, inside the existing outer
`div`, add:

```html
  {% if approval_error %}
  <p class="diff-head" role="alert">{{ approval_error }}</p>
  {% endif %}
```

Do not alter reject behavior or broad error handling.

- [ ] **Step 4: Run route tests and confirm GREEN**

```bash
uv run pytest tests/test_app.py \
  -k 'visibly_refuses_unfresh_source' -q
```

Expected: both cases pass and still return a swappable HTTP 200 partial.

- [ ] **Step 5: Run route, scope, outbox, and registry regressions**

```bash
uv run pytest tests/test_app.py tests/test_scope.py tests/test_outbox.py \
  tests/test_registry.py tests/test_folder_adapter.py tests/test_email_adapter.py -q
```

Expected: all pass.

- [ ] **Step 6: Commit Task 4**

```bash
git add app/main.py templates/blocks/outbox_list.html tests/test_app.py
git commit -m "feat: surface proposal freshness refusals"
```

---

### Task 5: Complete all S4 gates and whole-branch review

**Files:**
- Review only: all changes from `faa3894..HEAD`
- Modify only if a focused S4 defect is found through a new failing test.

**Interfaces:**
- Consumes: Tasks 1-4 completed behavior.
- Produces: reproducible completion evidence without merge, push, PR creation,
  or worktree removal.

- [ ] **Step 1: Run focused S4 and affected regression tests**

```bash
uv run pytest tests/test_proposal_identity.py tests/test_outbox.py \
  tests/test_app.py tests/test_registry.py tests/test_scope.py \
  tests/test_folder_adapter.py tests/test_email_adapter.py -q
```

Expected: all pass.

- [ ] **Step 2: Run the full public suite**

```bash
uv run python -m pytest -q
```

Expected: 387 baseline tests plus all new S4 tests pass with no failures or
warnings.

- [ ] **Step 3: Capture private-vault pre-gate fingerprints**

With `ONEOS_VAULT` set to the supplied Grey Matter root, capture these outside
the vault without printing private content:

```bash
git -C "$ONEOS_VAULT" status --porcelain=v1 -z | shasum -a 256
git -C "$ONEOS_VAULT" diff --binary | shasum -a 256
git -C "$ONEOS_VAULT" diff --cached --binary | shasum -a 256
```

Record all three hashes for the post-gate comparison.

- [ ] **Step 4: Run the full private read-only gate**

```bash
(cd "$ONEOS_VAULT/_system/scripts" && python3 -m unittest discover -q)
python3 "$ONEOS_VAULT/_system/scripts/check_v2.py" "$ONEOS_VAULT"
python3 "$ONEOS_VAULT/_system/scripts/policy_enforcer.py" \
  --policy "$ONEOS_VAULT/_system/scripts/action-policy.yaml" test-suite
```

Expected: 34 or more private tests pass, `check_v2` ends with `0 error(s), 0
warning(s)`, and the policy self-test passes.

- [ ] **Step 5: Run secret and repository audits**

```bash
tools/run_gitleaks.sh .
uv run python -m tools.public_repo_audit --repo . --history
uv run python -m tools.public_repo_audit \
  --repo . --vault "$ONEOS_VAULT" --history
```

Expected: pinned Gitleaks, public audit, and combined registry-derived audit all
exit 0.

- [ ] **Step 6: Prove the private vault remained byte-identical**

Repeat the three Step 3 fingerprint commands. Expected: each post-gate hash is
byte-for-byte identical to its pre-gate hash. If any differs, stop and report;
do not clean or alter Grey Matter.

- [ ] **Step 7: Run public diff hygiene and inspect branch scope**

```bash
git diff --check faa3894..HEAD
git status --short
git diff --stat faa3894..HEAD
git diff faa3894..HEAD -- \
  app/proposal_identity.py app/outbox.py app/registry.py app/main.py \
  templates/blocks/outbox_list.html tests/test_proposal_identity.py \
  tests/test_outbox.py tests/test_registry.py tests/test_app.py
```

Expected: no whitespace errors; clean worktree; changes limited to approved S4
code, tests, spec, and plan.

- [ ] **Step 8: Perform a whole-branch review**

Review every commit and diff from `faa3894..HEAD`. Verify explicitly:

- no ID accepts uppercase, missing entropy, invalid calendar time, separators,
  or ID/filename mismatch;
- no classification record without a valid lowercase `source_sha256` reaches
  load, preview, approve, or reject;
- missing/stale checks finish before `_git`, destination writes, proposal
  deletion, or any other mutation;
- approved output derives from the single verified snapshot;
- S2/S3 scope and canonical destination checks remain in place;
- registry-delete dispatch is preserved and uses the shared identity contract;
- only typed freshness errors gain route presentation;
- no S5 rollback/index isolation or broad S6 handler was added;
- no private or instance-specific value appears in the branch.

If review finds a defect, write one focused failing test, observe RED, implement
the minimal fix, rerun the affected suite, and create a narrow fix commit.

- [ ] **Step 9: Report the local branch without publishing**

Report:

- branch `codex/s4-fresh-collision-safe-proposals`;
- base `faa3894` (merged `origin/main`) and final S4 commit SHA;
- focused/public/private/audit results;
- private-vault fingerprint equality;
- files changed and key safety behavior; and
- confirmation that the branch was not merged, pushed, submitted as a PR, or
  removed.
