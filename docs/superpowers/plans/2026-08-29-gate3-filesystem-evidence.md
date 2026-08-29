# Gate 3 Filesystem Evidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Gate 3 compare deterministic filesystem metadata evidence at
snapshot and check so Git-invisible non-regular entries and directories cannot
evade the unsanctioned-write audit.

**Architecture:** Keep Git-derived dirty evidence and every existing S7 record
predicate intact. Add a separate closed filesystem-evidence map, collect it by
descriptor-relative no-follow traversal, compare it endpoint-to-endpoint, and
compose its path dispositions with the existing commit and dirty audits. Only
the exact canonical S7 quarantine-directory addition receives a directory-only
sanction.

**Tech Stack:** Python 3.12+, standard-library `dataclasses`, `hashlib`, `os`,
`stat`, `subprocess`, JSON, pytest, and Git. No dependency or build-system
change.

**Spec:**
`docs/superpowers/specs/2026-08-29-gate3-filesystem-evidence-design.md`
at approved commit `03be199cee333641700a0c347595d7d88125b194`.

## Global Constraints

- Read the approved specification before execution and keep its authority
  reconciliation unchanged.
- Modify only `tools/gate3_audit.py` and `tests/test_gate3_audit.py`, apart from
  this already-approved plan checkpoint. Do not create another module.
- Preserve snapshot/check baseline semantics: identical pre-existing dirty and
  filesystem evidence is neither sanctioned nor violating.
- Use snapshot schema version 4. Refuse every earlier or unknown version; do not
  infer missing initial filesystem evidence.
- Supplemental evidence contains every included real directory and every
  included non-regular entry, but no regular-file content.
- Keep ignored regular-file contents out of scope.
- Never follow a directory symlink, symlink target, or special device.
- Exclusions come only from generic conventions and executable constants.
  Runtime registry values may validate the exact canonical S7 location but may
  never define an exclusion.
- Descriptor lifetime is bounded by current traversal depth. Close a child on
  depth-first unwind before opening the next sibling.
- Every observed traversal, classification, encoding, consistency, or race
  error fails through a constant, controlled error; never print an underlying
  path, target, registry value, or operating-system message.
- Preserve all existing exact S7 regular-record sanction predicates and
  classifier taxonomy. The new canonical directory exception cannot authorize
  a child or sibling.
- Use only portable synthetic repositories and temporary fixtures. Socket
  tests may skip when the host lacks safe support. Test device kinds through
  mode classification; never create a privileged device.
- Before the first test or product-code edit, complete Task 1. Stop on any
  baseline or preservation failure.
- Set the private vault environment only inline for one trusted-local command.
  Never export or persist it, print its value, or retain command output that
  contains instance paths or values.
- Do not mutate, clean, reset, restore, delete, or otherwise change the live
  vault or any preserved branch, worktree, task, or evidence.
- Do not run live Gate 3, Gate 1 timing, deployment, Phase 2, or deferred work.
- Do not refactor unrelated Gate 3 commit rules, transactions, registries,
  conventions, dependencies, quarantine behavior, or curated data.
- Do not push or open a pull request until all local gates and independent
  review pass. Do not merge without explicit owner authorization.

## File Structure

- Modify `tools/gate3_audit.py`: closed evidence types, version 4 snapshot
  parsing, metadata fingerprints, traversal, Git consistency bracket,
  comparison, path disposition, exact directory exception, CLI composition,
  controlled failures, and removal of the obsolete canonical-only discovery
  helper.
- Modify `tests/test_gate3_audit.py`: all RED/GREEN synthetic coverage and
  preservation of the existing exact S7 record tests.

No other tracked file changes during implementation.

## Interface Ledger

Define these interfaces in Task 2 before any later task consumes them. Keep the
names and signatures exact throughout the plan.

```python
from typing import Literal, TypeAlias

FilesystemKind: TypeAlias = Literal[
    "directory",
    "symlink",
    "fifo",
    "socket",
    "char-device",
    "block-device",
    "other",
]
ChangeKind: TypeAlias = Literal["added", "removed", "changed"]
Disposition: TypeAlias = Literal["sanctioned", "violating"]


@dataclass(frozen=True)
class GitDirtyInputs:
    statuses: dict[str, str]
    index_entries: dict[str, tuple[str, ...]]


@dataclass(frozen=True)
class FilesystemFingerprint:
    kind: FilesystemKind
    mode: int
    identity_digest: str
    target_digest: str | None


@dataclass(frozen=True)
class Gate3Evidence:
    dirty: dict[str, DirtyFingerprint]
    filesystem: dict[str, FilesystemFingerprint]


@dataclass(frozen=True)
class Gate3Snapshot:
    head: str
    evidence: Gate3Evidence


@dataclass(frozen=True)
class FilesystemChange:
    path: str
    kind: ChangeKind
    before: FilesystemFingerprint | None
    after: FilesystemFingerprint | None


@dataclass(frozen=True)
class ClassifiedPathChange:
    path: str
    kind: ChangeKind
    disposition: Disposition


class FilesystemEvidenceError(ValueError):
    """Gate 3 could not obtain one coherent filesystem observation."""
```

Define this result immediately after the existing `Audit` type, before the
first function that returns it:

```python
@dataclass(frozen=True)
class CommitAuditResult:
    audit: Audit
    path_changes: tuple[ClassifiedPathChange, ...]
```

The final function signatures are listed as declarations, not source bodies:

```text
_collect_git_dirty_inputs(vault: Path) -> GitDirtyInputs
_fingerprint_git_dirty_inputs(vault: Path, inputs: GitDirtyInputs)
    -> dict[str, DirtyFingerprint]
collect_filesystem_fingerprints(vault: Path)
    -> dict[str, FilesystemFingerprint]
collect_gate3_evidence(vault: Path) -> Gate3Evidence
compare_filesystem_evidence(
    before: dict[str, FilesystemFingerprint],
    after: dict[str, FilesystemFingerprint],
) -> tuple[FilesystemChange, ...]
_classify_dirty_path_changes(
    before: dict[str, DirtyFingerprint],
    after: dict[str, DirtyFingerprint],
    audit: Audit,
) -> tuple[ClassifiedPathChange, ...]
_audit_commit_history(
    records: tuple[CommitRecord, ...],
    vault: Path,
    snapshot_head: str,
    audit_head: str,
) -> CommitAuditResult
audit_filesystem(
    before: dict[str, FilesystemFingerprint],
    after: dict[str, FilesystemFingerprint],
    rules: AuditRules,
    *,
    classified_paths: tuple[ClassifiedPathChange, ...],
) -> Audit
_snapshot_payload(vault: Path) -> dict[str, object]
_load_snapshot(path: Path) -> Gate3Snapshot
```

No implementation step may leave an ellipsis, stub, or empty branch in
source.

## Checkpoint Gate After Every Product-Code Task

After each Task 2–8 GREEN step:

1. Run that task's focused command and the complete Gate 3 test module.
2. Run `uv run python -m pytest -q`; require at least 1,847 passing tests.
3. Through the trusted-local runner, run the exact private read-only unittest
   and structural-validation commands prescribed by `BUILD.md` section 3.
   Require at least 39 passing private tests and zero errors/zero warnings.
4. Byte-compare the protected vault HEAD, NUL-delimited status, binary
   worktree diff, and binary cached diff with Task 1's opaque preimages. Print
   only four equal/not-equal results. Stop on the first mismatch.
5. Run `git diff --check`, inspect `git status --short`, and confirm only the
   two approved implementation files differ.
6. Create one sanitized implementation checkpoint containing only those two
   files. Do not print or retain its commit message.

---

### Task 1: Establish the trusted-local preservation envelope

**Files:**
- Modify: none
- Test: none

**Interfaces:**
- Consumes: the fresh isolated worktree descending from the canonical baseline,
  the approved design and plan checkpoints, and the trusted-local gate commands
  prescribed by `BUILD.md`.
- Produces: an opaque mode-0700 proof directory outside both repositories,
  four byte-exact vault preimages, sanitized dirty-state counts, and passing
  public/private baselines retained for final comparison.

- [ ] **Step 1: Fetch and verify the canonical baseline and checkpoint chain**

Run before any public/private baseline or test/product-code edit. Do not query
or print commit subjects:

```bash
set -euo pipefail
gate3_canonical=fecafea674cc254217d24950e716e42f71353fdc
gate3_design=03be199cee333641700a0c347595d7d88125b194
gate3_plan=e0a0e990679a7af6e01d2a63c06f71bd2a86b109
git fetch --prune origin
test "$(git rev-parse refs/remotes/origin/main)" = "$gate3_canonical"
git merge-base --is-ancestor "$gate3_canonical" HEAD
test "$(git rev-parse "$gate3_design^")" = "$gate3_canonical"
test "$(git rev-parse "$gate3_plan^")" = "$gate3_design"
test "$(git rev-parse HEAD^)" = "$gate3_plan"
test "$(git rev-list --count "$gate3_canonical"..HEAD)" = 3
test "$(git diff-tree --no-commit-id --name-only -r "$gate3_design")" = \
  docs/superpowers/specs/2026-08-29-gate3-filesystem-evidence-design.md
test "$(git diff-tree --no-commit-id --name-only -r "$gate3_plan")" = \
  docs/superpowers/plans/2026-08-29-gate3-filesystem-evidence.md
test "$(git diff-tree --no-commit-id --name-only -r HEAD)" = \
  docs/superpowers/plans/2026-08-29-gate3-filesystem-evidence.md
test -z "$(git status --porcelain=v1 --untracked-files=all)"
```

Expected: fetched `origin/main` is exactly the canonical SHA; the task branch
descends from it; the design checkpoint, original plan checkpoint, and this
plan-revision checkpoint form the complete three-commit branch chain; both plan
commits touch only this plan document; and the worktree is clean. Local `main`
is never authority.

If fetched `origin/main` differs, ancestry fails, a checkpoint parent or path
set differs, the commit count differs, or the worktree is not clean, stop
before baselines or edits and return a bounded decision memo containing only
the expected/observed public OIDs and the failed generic check. Do not repair
the condition. After this verification, do not rebase, retarget, reset, merge,
amend, or otherwise rewrite the branch.

- [ ] **Step 2: Run the fresh public baseline before any test/code edit**

Run:

```bash
uv run python -m pytest -q
```

Expected: at least 1,847 tests pass. Stop without editing if any test fails or
the count is below the floor.

- [ ] **Step 3: Capture opaque protected-vault preimages**

Use the trusted-local runner to allocate a new unpredictable mode-0700
directory under the system temporary area without printing its path. Capture
these byte streams into separate files there:

1. exact vault `HEAD` object id, with no log or subject query;
2. `git status --porcelain=v2 -z --untracked-files=all`;
3. `git diff --binary --no-ext-diff`; and
4. `git diff --cached --binary --no-ext-diff`.

Do not display, summarize by pathname, or copy any captured bytes into the
repository. Retain the opaque directory handle privately for every checkpoint
and final comparison.

- [ ] **Step 4: Verify minimum private authority and record safe aggregates**

Without requesting a log message, prove the minimum required vault commit is
an ancestor of vault `HEAD`. Parse the captured NUL-delimited status to report
only aggregate staged, unstaged, and untracked counts. Do not print a path,
status record, object subject, or source value. Stop if ancestry cannot be
proved or status parsing is ambiguous.

- [ ] **Step 5: Run the private read-only baseline and prove preservation**

Through the trusted-local runner, execute the exact private unittest,
structural validation, policy self-test, and combined history-audit commands
prescribed by `BUILD.md`. Set the vault environment inline for each individual
command and suppress command echo. Require:

- private unittest: at least 39 passing;
- structural validation: zero errors and zero warnings;
- policy self-test: pass; and
- combined history audit: clean.

Immediately recapture the same four byte streams and compare each with the
preimage using a byte comparator. Report only four equality booleans. Stop if
any private gate fails or any pre/post stream differs. Do not mutate or clean
the vault to make a comparison pass.

---

### Task 2: Define closed evidence types and snapshot schema version 4

**Files:**
- Modify: `tools/gate3_audit.py:17-165,1409-1484`
- Test: `tests/test_gate3_audit.py:1643-1699`

**Interfaces:**
- Consumes: existing `DirtyFingerprint`, `_OID`, `_path_parts()`, and external
  snapshot location validation.
- Produces: every type in the Interface Ledger, `SNAPSHOT_VERSION = 4`, exact
  version 4 serialization, and `_load_snapshot(path: Path) -> Gate3Snapshot`.

- [ ] **Step 1: Write the version 4 RED tests**

Rename the current version-three snapshot test to
`test_cli_snapshot_writes_version_four_evidence_outside_vault`. Require the
closed top-level key set and an initially empty supplemental map before the
collector is added:

```python
assert set(data) == {"version", "head", "dirty", "filesystem"}
assert data["version"] == 4
assert data["filesystem"] == {}
```

Add `test_load_snapshot_rejects_version_three_without_upgrade` with a complete
version 3 JSON object and assert `FilesystemEvidenceError` or `ValueError` with
the constant unsupported-version message.

Add a parameterized
`test_load_snapshot_rejects_malformed_filesystem_fingerprint` whose mutations
are exactly:

- missing `kind`;
- extra field;
- kind outside the closed literal set;
- negative or Boolean mode;
- non-lowercase-hex or wrong-length digest;
- non-null target digest on a non-symlink;
- null target digest on a symlink;
- absolute, empty-component, dot-component, parent-component, NUL, or
  surrogate-containing path; and
- duplicate JSON object keys, including a duplicate filesystem path key.

Each case must raise a constant controlled message that does not contain the
path or malformed value.

- [ ] **Step 2: Run the schema tests and observe RED**

Run:

```bash
uv run python -m pytest tests/test_gate3_audit.py \
  -k 'snapshot_writes_version_four or load_snapshot_rejects' -q
```

Expected: FAIL because the current schema is version 3, has no `filesystem`
map, and returns a tuple rather than `Gate3Snapshot`.

- [ ] **Step 3: Define every closed type before its consumer**

Add the Interface Ledger aliases and dataclasses in dependency order. Add:

```python
_FILESYSTEM_KINDS = frozenset(
    {
        "directory",
        "symlink",
        "fifo",
        "socket",
        "char-device",
        "block-device",
        "other",
    }
)
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
SNAPSHOT_VERSION = 4
```

Reject Boolean modes explicitly because `bool` is an `int` subclass. Require
`target_digest` exactly for symlinks and never for another kind.

- [ ] **Step 4: Implement exact version 4 loading and serialization**

Keep the top-level JSON shape exactly `version`, `head`, `dirty`, and
`filesystem`. Preserve the current dirty-fingerprint field validation. Parse
filesystem values into `FilesystemFingerprint` and return:

```python
return Gate3Snapshot(
    head=head,
    evidence=Gate3Evidence(
        dirty=dirty_fingerprints,
        filesystem=filesystem_fingerprints,
    ),
)
```

Load JSON with an `object_pairs_hook` that rejects a repeated key before Python
can collapse it into a dictionary. Use the same constant malformed-snapshot
error for duplicate top-level, dirty-map, filesystem-map, or fingerprint keys.

Until Task 5 installs the collector, `_snapshot_payload()` must serialize
`filesystem` as an empty sorted map. Update `cmd_check()` to consume the typed
snapshot without changing its audit behavior yet.

- [ ] **Step 5: Run GREEN and the checkpoint gate**

Run:

```bash
uv run python -m pytest tests/test_gate3_audit.py \
  -k 'snapshot or load_snapshot' -q
uv run python -m pytest tests/test_gate3_audit.py -q
```

Expected: all selected and Gate 3 tests pass. Then complete the global
Checkpoint Gate and create the Task 2 checkpoint.

---

### Task 3: Fingerprint filesystem identity without following targets

**Files:**
- Modify: `tools/gate3_audit.py` adjacent to `DirtyFingerprint` helpers
- Test: `tests/test_gate3_audit.py` adjacent to dirty-fingerprint tests

**Interfaces:**
- Consumes: `FilesystemKind`, `FilesystemFingerprint`,
  `FilesystemEvidenceError`, `hashlib`, `os`, and `stat`.
- Produces:

```text
_filesystem_kind(mode: int) -> FilesystemKind
_filesystem_identity_digest(
    kind: FilesystemKind, metadata: os.stat_result
) -> str
_filesystem_fingerprint(
    parent_descriptor: int,
    name: str,
    metadata: os.stat_result,
) -> FilesystemFingerprint
```

- [ ] **Step 1: Write RED tests for the closed kind map and identity**

Add `test_filesystem_kind_is_closed_without_type_confusion` and assert the
mapping for directory, symlink, regular, FIFO, socket, character-device,
block-device, and an unsupported mode. Regular mode must raise the constant
internal misuse error because regular files are not supplemental evidence;
unsupported special mode returns `other`.

Add `test_filesystem_identity_digest_changes_on_same_kind_replacement`:
create a directory, record no-follow metadata and digest, remove/recreate it,
and assert equal kind but unequal digest.

Add `test_filesystem_symlink_hashes_raw_target_without_following`: create an
external regular file and an in-boundary link, call `_filesystem_fingerprint`
using the parent descriptor, and assert:

```python
assert fingerprint.kind == "symlink"
assert fingerprint.target_digest == hashlib.sha256(
    b"oneos-gate3-target-v1\0" + os.fsencode(os.readlink(link))
).hexdigest()
assert fingerprint.target_digest != hashlib.sha256(external.read_bytes()).hexdigest()
```

Add a safely supported UNIX socket case and direct `_filesystem_kind()` cases
for device modes. Do not create a device.

- [ ] **Step 2: Run the primitive tests and observe RED**

Run:

```bash
uv run python -m pytest tests/test_gate3_audit.py \
  -k 'filesystem_kind or filesystem_identity or filesystem_symlink' -q
```

Expected: FAIL because none of the fingerprint helpers exists.

- [ ] **Step 3: Implement the kind classifier and domain-separated digests**

Use `stat.S_ISDIR`, `S_ISLNK`, `S_ISFIFO`, `S_ISSOCK`, `S_ISCHR`, and
`S_ISBLK` in that order after rejecting regular mode. Encode identity fields as
ASCII decimal values with an explicit length prefix and domain tag
`b"oneos-gate3-identity-v1\0"`. Include kind, `st_dev`, `st_ino`, and `st_rdev`
only for character/block devices. Use `stat.S_IMODE(metadata.st_mode)` for
`mode`.

For a symlink, call `os.readlink(name, dir_fd=parent_descriptor)`, encode with
`os.fsencode`, hash `b"oneos-gate3-target-v1\0" + raw_target`, and never call
`resolve()`, `stat()` with follow enabled, or `open()` on the target.

Wrap readlink/classification failures in `FilesystemEvidenceError` with one of
these constant messages only:

```python
"Gate 3 filesystem entry is unclassifiable"
"Gate 3 filesystem traversal failed"
```

- [ ] **Step 4: Run GREEN and the checkpoint gate**

Run:

```bash
uv run python -m pytest tests/test_gate3_audit.py \
  -k 'filesystem_kind or filesystem_identity or filesystem_symlink' -q
uv run python -m pytest tests/test_gate3_audit.py -q
```

Expected: all selected and Gate 3 tests pass. Then complete the global
Checkpoint Gate and create the Task 3 checkpoint.

---

### Task 4: Traverse the precise boundary deterministically with bounded descriptors

**Files:**
- Modify: `tools/gate3_audit.py` after filesystem fingerprint helpers
- Test: `tests/test_gate3_audit.py` in a new filesystem-traversal group

**Interfaces:**
- Consumes: `_filesystem_fingerprint()`, `_path_parts()`, Git helpers, and the
  generic exclusion constants defined in this task.
- Produces:

```text
@dataclass(frozen=True)
class FilesystemExclusions:
    exact_directories: frozenset[tuple[str, ...]]
    directory_names: frozenset[str]

_filesystem_exclusions(vault: Path) -> FilesystemExclusions
_open_directory(
    path: str | Path, *, parent_descriptor: int | None = None
) -> int
_close_directory(descriptor: int) -> None
_list_directory(descriptor: int) -> tuple[str, ...]
collect_filesystem_fingerprints(
    vault: Path,
) -> dict[str, FilesystemFingerprint]
```

Define these generic public executable constants before
`FilesystemExclusions`:

```python
_SENSITIVE_DIRECTORY_NAME = ".sensitive"
_ROOT_SCRATCH_DIRECTORY_NAME = "_scratch"
_ROOT_CACHE_DIRECTORY_NAME = ".obsidian"
_OUTBOX_DIRECTORY_NAME = "outbox"
_QUARANTINE_DIRECTORY_NAME = ".consumed"
```

Use the first three only for the approved traversal exclusions. Use the last
two for the new exact directory exception and reconcile existing S7 path-shape
literals to those constants without changing any predicate. Do not derive an
exclusion from a runtime entity or other registry value.

- [ ] **Step 1: Write RED boundary and ordering tests**

Add these exact tests:

- `test_filesystem_walk_records_real_directories_and_special_entries_in_byte_order`:
  create nested real directories, an empty directory, FIFO, symlink, and socket
  where supported; assert sorted keys and exact kinds.
- `test_filesystem_walk_excludes_only_authoritative_real_directories`: put a
  FIFO below each real exclusion directory and assert the directory itself is
  recorded while its child is absent.
- `test_exclusion_name_symlink_is_evidence_and_is_not_followed`: point an
  exclusion-name symlink at an external directory containing a FIFO; assert the
  symlink is recorded and no target child is present.
- `test_directory_symlink_is_not_followed`: use a non-exclusion directory
  symlink and make the same assertions.
- `test_git_administrative_directory_is_derived_and_not_traversed`: use a
  synthetic repository whose administrative directory lies below its root;
  assert no administrative descendant enters evidence.
- `test_undecodable_entry_name_fails_closed`: create a raw-byte name containing
  invalid UTF-8 where the host supports it and assert the constant controlled
  error; skip only on filesystems that reject creation.
- `test_filesystem_walk_closes_siblings_during_depth_first_unwind`: wrap
  `_open_directory` and `_close_directory` to record active descriptors and
  events across two deep sibling trees; assert every descriptor for the first
  sibling closes before the second sibling opens, the final active set is
  empty, and peak active descriptors are at most root plus maximum depth.

- [ ] **Step 2: Run the boundary tests and observe RED**

Run:

```bash
uv run python -m pytest tests/test_gate3_audit.py \
  -k 'filesystem_walk or exclusion_name or directory_symlink or git_administrative or undecodable_entry' -q
```

Expected: FAIL because the traversal and exclusion contract is absent.

- [ ] **Step 3: Implement descriptor-relative depth-first traversal**

Use directory flags `O_RDONLY | O_DIRECTORY | O_NOFOLLOW`; if either safety
flag is unavailable, raise `FilesystemEvidenceError` rather than weakening the
walk. `_list_directory()` must return a tuple sorted by `os.fsencode(name)` and
must reject surrogate-containing names. Validate every joined relative path
through `_path_parts()` before recording it.

No-follow stat the root, open it with the same directory flags, and require
root identity/type agreement before walking. Do not prune a real mount point
merely because its device identity differs from the root; it remains inside the
working-tree namespace unless one of the exact authoritative exclusions
applies.

For each name, implement the ordinary no-concurrency path first:

1. `os.stat(name, dir_fd=parent, follow_symlinks=False)`;
2. if real directory, open relative to the parent, compare `st_dev`, `st_ino`,
   kind, and permission mode between pre-open metadata and `fstat`;
3. record the directory fingerprint;
4. if excluded, close it without listing children;
5. otherwise recurse;
6. close the child in `finally` before visiting the next sibling;
7. for a non-directory non-regular entry, fingerprint it;
8. for a regular file, classify only enough to avoid type confusion and do not
   add it to the supplemental map.

This step makes the ordinary boundary and descriptor-lifetime tests green. The
post-observation race checks remain RED until Steps 4–6.

- [ ] **Step 4: Write RED descriptor-lifetime and race tests**

Add parameterized `test_filesystem_walk_fails_closed_on_observed_race` with
injections after initial list, pre-stat, child open, child `fstat`, readlink,
post-stat, and relist. Each mutation removes or replaces the observed entry and
must raise `FilesystemEvidenceError` with no path in `str(exc)`.

Add `test_filesystem_walk_closes_all_descriptors_after_failure` and inject a
deep failure; assert every opened descriptor receives exactly one close call.

- [ ] **Step 5: Run the race/error tests and observe RED**

Run:

```bash
uv run python -m pytest tests/test_gate3_audit.py \
  -k 'observed_race or descriptors_after_failure' -q
```

Expected: FAIL because the ordinary traversal does not yet perform every
post-stat, relist, and transient-directory consistency check.

- [ ] **Step 6: Implement fail-closed revalidation**

After each child recursion, re-stat its name from the parent and compare the
same identity fields. Post-stat every special entry after fingerprinting.
After all names, relist the directory and compare the sorted name tuple, then
compare the directory's transient identity, mode, `st_mtime_ns`, and
`st_ctime_ns` with its entry observation. Wrap OS errors with a constant
controlled message and retain the original only as exception chaining, never
in printed output. Close every owned descriptor exactly once on all exits.

- [ ] **Step 7: Run traversal GREEN and the checkpoint gate**

Run:

```bash
uv run python -m pytest tests/test_gate3_audit.py \
  -k 'filesystem_walk or exclusion_name or directory_symlink or git_administrative or undecodable_entry' -q
uv run python -m pytest tests/test_gate3_audit.py -q
```

Expected: all selected and Gate 3 tests pass with no leaked descriptors. Then
complete the global Checkpoint Gate and create the Task 4 checkpoint.

---

### Task 5: Collect one coherent Git-plus-filesystem observation

**Files:**
- Modify: `tools/gate3_audit.py:496-519,1409-1489`
- Test: `tests/test_gate3_audit.py` traversal and snapshot groups

**Interfaces:**
- Consumes: `GitDirtyInputs`, `Gate3Evidence`,
  `collect_filesystem_fingerprints()`, `_parse_porcelain()`,
  `_parse_index_entries()`, and `_fingerprint_path()`.
- Produces `_collect_git_dirty_inputs()`,
  `_fingerprint_git_dirty_inputs()`, and `collect_gate3_evidence()` with the
  exact Interface Ledger signatures. `collect_dirty_fingerprints(vault)`
  remains a compatibility wrapper for existing focused tests until Task 8
  removes the obsolete supplement.

- [ ] **Step 1: Write RED Git-bracket and snapshot integration tests**

Add `test_gate3_evidence_brackets_the_filesystem_walk_with_equal_git_inputs`.
Monkeypatch `_collect_git_dirty_inputs` to return the same immutable values
twice, wrap the filesystem collector, and assert the call order is exactly
`git-before`, `filesystem`, `fingerprint`, `git-after`.

Add `test_gate3_evidence_rejects_changed_status_or_index_across_walk`,
parameterized so either `statuses` or `index_entries` differs on the second
read. Assert `FilesystemEvidenceError` with the constant consistency message.

Update the version 4 CLI snapshot test to create an empty real directory and a
FIFO before snapshot, then assert both appear in `data["filesystem"]` while a
regular file appears only in `data["dirty"]` when Git reports it.

- [ ] **Step 2: Run the coherence tests and observe RED**

Run:

```bash
uv run python -m pytest tests/test_gate3_audit.py \
  -k 'gate3_evidence or snapshot_writes_version_four' -q
```

Expected: FAIL because snapshot still emits an empty filesystem map and has no
Git input bracket.

- [ ] **Step 3: Split raw Git inputs from dirty fingerprinting**

`_collect_git_dirty_inputs()` runs exactly the existing porcelain and staged
index commands and returns `GitDirtyInputs` without mutating either map.
`_fingerprint_git_dirty_inputs()` fingerprints only sorted status keys using
the supplied index entries.

Implement `collect_gate3_evidence()` in this exact order:

```python
before = _collect_git_dirty_inputs(vault)
filesystem = collect_filesystem_fingerprints(vault)
dirty = _fingerprint_git_dirty_inputs(vault, before)
after = _collect_git_dirty_inputs(vault)
if after != before:
    raise FilesystemEvidenceError(
        "Gate 3 Git evidence changed during filesystem traversal"
    )
return Gate3Evidence(dirty=dirty, filesystem=filesystem)
```

Resolve the vault once at entry. Do not retry a mismatched observation.

- [ ] **Step 4: Wire version 4 snapshot to coherent evidence**

`_snapshot_payload()` calls `collect_gate3_evidence()` once and serializes both
maps with sorted keys. `cmd_snapshot()` may report only aggregate dirty and
filesystem counts. It must not print evidence paths.

- [ ] **Step 5: Run GREEN and the checkpoint gate**

Run:

```bash
uv run python -m pytest tests/test_gate3_audit.py \
  -k 'gate3_evidence or snapshot_writes_version_four or snapshot_reports' -q
uv run python -m pytest tests/test_gate3_audit.py -q
```

Expected: all selected and Gate 3 tests pass. Then complete the global
Checkpoint Gate and create the Task 5 checkpoint.

---

### Task 6: Compare supplemental evidence without changing baseline semantics

**Files:**
- Modify: `tools/gate3_audit.py` after evidence collection
- Test: `tests/test_gate3_audit.py` in a new filesystem-comparison group

**Interfaces:**
- Consumes: `FilesystemFingerprint`, `FilesystemChange`, and `ChangeKind`.
- Produces `compare_filesystem_evidence()` with the Interface Ledger signature.

- [ ] **Step 1: Write RED endpoint-comparison tests**

Create one helper in the test module:

```python
def _fs_fp(
    kind: gate3.FilesystemKind = "fifo",
    *,
    mode: int = 0o600,
    identity: str = "1" * 64,
    target: str | None = None,
) -> gate3.FilesystemFingerprint:
    return gate3.FilesystemFingerprint(kind, mode, identity, target)
```

Add:

- `test_filesystem_comparison_preserves_identical_preexisting_evidence`;
- `test_filesystem_comparison_reports_added_removed_and_changed_in_sorted_order`;
- `test_filesystem_comparison_detects_same_kind_identity_replacement`;
- `test_filesystem_comparison_detects_directory_mode_change`;
- `test_filesystem_comparison_detects_symlink_target_change`; and
- `test_filesystem_comparison_never_confuses_directory_symlink_fifo_or_socket`.

Assert exact `FilesystemChange` tuples, including `before=None` for additions,
`after=None` for removals, and `kind="changed"` for any unequal pair.

- [ ] **Step 2: Run the comparison tests and observe RED**

Run:

```bash
uv run python -m pytest tests/test_gate3_audit.py \
  -k 'filesystem_comparison' -q
```

Expected: FAIL because the comparison function does not exist.

- [ ] **Step 3: Implement the minimal deterministic comparison**

Iterate `sorted(set(before) | set(after))`. Skip equality. Emit exactly one
`FilesystemChange` per unequal path using current-only as `added`,
snapshot-only as `removed`, and present-at-both as `changed`. Do not perform
sanctioning, ancestry, filesystem I/O, or Git I/O in this pure function.

- [ ] **Step 4: Run GREEN and the checkpoint gate**

Run:

```bash
uv run python -m pytest tests/test_gate3_audit.py \
  -k 'filesystem_comparison' -q
uv run python -m pytest tests/test_gate3_audit.py -q
```

Expected: all selected and Gate 3 tests pass. Then complete the global
Checkpoint Gate and create the Task 6 checkpoint.

---

### Task 7: Compose path dispositions and the exact directory exception

**Files:**
- Modify: `tools/gate3_audit.py:769-889,1290-1400`
- Test: `tests/test_gate3_audit.py` dirty, consumed, and new filesystem-audit groups

**Interfaces:**
- Consumes: `Audit`, `AuditRules`, `CommitRecord`, `PathChangeRecord`,
  `ClassifiedPathChange`, `FilesystemChange`, existing `audit_dirty()`, and
  every unchanged S7 record-sanctioning helper.
- Produces `_classify_dirty_path_changes()`, the revised
  `_audit_commit_history() -> CommitAuditResult`, and `audit_filesystem()` with
  the Interface Ledger signatures.

- [ ] **Step 1: Write RED tests for baseline and non-directory violations**

Add `test_filesystem_audit_preserves_unchanged_preexisting_special_entry`:
collect before/after around an unchanged wrong-location FIFO and assert no
sanctioned or violating write.

Add parameterized
`test_filesystem_audit_rejects_new_removed_replaced_or_changed_special_entry`
covering new FIFO, removed FIFO, FIFO replacement, FIFO-to-symlink,
symlink-target change, and safely supported socket. Assert the changed path is
the sole violation.

- [ ] **Step 2: Write RED tests for directory ancestry disposition**

Add these exact cases against `audit_filesystem()`:

- added directory plus sanctioned added descendant: directory is sanctioned;
- removed directory plus sanctioned removed descendant: directory is
  sanctioned;
- directory plus violating matching descendant: only descendant is reported
  violating, avoiding a duplicate ancestor;
- directory plus mixed sanctioned/violating descendants: violating descendant
  wins and the directory cannot erase it;
- added directory plus only a `changed` descendant: directory violates because
  changed is not matching addition topology;
- empty added/removed directory with no descendant: directory violates;
- unrelated empty sibling beside a sanctioned descendant: sibling violates;
- directory containing only ignored regular content: directory violates;
- existing-directory identity or mode change: directory violates even with a
  sanctioned descendant; and
- a directory-to-symlink replacement: the path violates as a changed
  non-directory outcome, never as ancestry.

- [ ] **Step 3: Write RED tests for the exact S7 directory exception**

Add:

- `test_exact_canonical_quarantine_directory_addition_is_sanctioned`;
- `test_canonical_quarantine_directory_removal_or_replacement_is_a_violation`;
- `test_wrong_location_quarantine_lookalike_directory_is_a_violation`;
- `test_unknown_entity_quarantine_lookalike_is_a_violation`;
- `test_canonical_quarantine_directory_does_not_sanction_unrelated_sibling`;
  and
- `test_nonregular_quarantine_record_cannot_enter_record_sanctioning`.

Keep every existing regular approval, reject, registry-delete, pending-pair,
receipt-correlation, byte-identity, and unrelated-sibling S7 test unchanged.

- [ ] **Step 4: Run the audit tests and observe RED**

Run:

```bash
uv run python -m pytest tests/test_gate3_audit.py \
  -k 'filesystem_audit or quarantine_directory or nonregular_quarantine' -q
```

Expected: FAIL because filesystem changes have no dispositions or exception.

- [ ] **Step 5: Classify endpoint commit and dirty paths**

Revise `_audit_commit_history()` to keep its current per-commit parent-tree
audit, record each OID as sanctioned or violating, and collect the net
snapshot-to-audit name-status diff with `--no-renames -z`. For every net path,
emit:

- `added` for status `A`;
- `removed` for status `D`; and
- `changed` for every other status.

The path disposition is `violating` if any audited record touching the path is
violating; otherwise it is `sanctioned`. Sort and deduplicate by path; a
conflicting disposition resolves to violating.

`_classify_dirty_path_changes()` uses `audit.sanctioned_writes` and
`audit.violating_writes`. Infer removal when the path disappears or the final
dirty fingerprint kind is `absence`, addition when it is current-only and not
`absence`, and change otherwise. A conflict resolves to violating.

- [ ] **Step 6: Implement pure ancestry composition**

`audit_filesystem()` first calls `compare_filesystem_evidence()`. Every
non-directory addition/removal/change is violating and becomes a local
`ClassifiedPathChange`. Only a directory presence change can inherit a
descendant disposition.

A descendant is relevant only when:

```python
descendant.path.startswith(directory.path + "/")
and descendant.kind == directory.kind
and directory.kind in {"added", "removed"}
```

Apply this order:

1. existing-at-both directory change -> violation;
2. any relevant violating descendant -> suppress the duplicate ancestor;
3. one or more relevant sanctioned descendants and no violating descendant ->
   sanctioned ancestor;
4. exact canonical S7 quarantine-directory addition -> sanctioned directory;
5. otherwise -> violating directory.

The canonical helper requires exactly three path components: a current runtime
entity, the existing outbox component, and the single executable quarantine
directory constant. It also requires `before is None` and current kind
`directory`. It never examines or authorizes a child.

- [ ] **Step 7: Run GREEN and the exact S7 regression gate**

Run:

```bash
uv run python -m pytest tests/test_gate3_audit.py \
  -k 'filesystem_audit or quarantine_directory or nonregular_quarantine' -q
uv run python -m pytest tests/test_gate3_audit.py \
  -k 'consumed or pending or receipt or unrelated' -q
uv run python -m pytest tests/test_gate3_audit.py -q
```

Expected: new filesystem tests pass and every existing exact regular-record S7
test remains green. Then complete the global Checkpoint Gate and create the
Task 7 checkpoint.

---

### Task 8: Integrate CLI audit, fail closed, and retire narrow discovery

**Files:**
- Modify: `tools/gate3_audit.py:496-580,1409-1528`
- Test: `tests/test_gate3_audit.py:1200-1260,1643-end` plus filesystem CLI tests

**Interfaces:**
- Consumes: typed `Gate3Snapshot`, `collect_gate3_evidence()`,
  `CommitAuditResult`, `audit_dirty()`, `_classify_dirty_path_changes()`, and
  `audit_filesystem()`.
- Produces: final snapshot/check CLI composition and no
  `_supplement_untracked_consumed_entries` symbol or call.

- [ ] **Step 1: Write RED end-to-end synthetic regressions**

Add CLI snapshot/check tests for:

- a FIFO created after snapshot at an entity-local wrong-location
  quarantine-like leaf: `check` returns 1;
- FIFO and safely supported socket at unrelated entity-local locations:
  `check` returns 1;
- unchanged pre-existing wrong-location FIFO: `check` returns 0 when no other
  activity occurs;
- new and removed empty directories: `check` returns 1;
- valid tracked and untracked regular evidence: existing outcomes do not
  regress;
- exact canonical empty quarantine-directory creation: `check` returns 0;
- canonical regular record plus directory created by a sanctioned action:
  `check` returns 0 with unchanged exact record predicates;
- canonical directory plus unrelated sibling special or empty directory:
  `check` returns 1; and
- a directory symlink whose target contains specials: the link itself is
  classified and the target children never appear in results.

Assert only safe aggregate/relative synthetic output. Never use a live vault.

- [ ] **Step 2: Write RED controlled-error CLI tests**

Parameterize injections for list, open, stat, child identity, readlink, relist,
close, path decoding, and unequal Git brackets. For both `snapshot` and
`check`, assert:

```python
assert gate3.main([command]) == 2
assert "GATE 3 ERROR:" in captured.err
assert str(vault) not in captured.err
assert outside_marker not in captured.err
```

For snapshot failure, assert no successful version 4 snapshot is produced. For
check failure, assert no `GATE 3: PASS` line is produced.

- [ ] **Step 3: Run end-to-end tests and observe RED**

Run:

```bash
uv run python -m pytest tests/test_gate3_audit.py \
  -k 'wrong_location or unrelated_entity_local or empty_directory or canonical_empty or controlled_error or unequal_git' -q
```

Expected: FAIL because `cmd_check()` does not load, collect, or compose the
filesystem map and the old helper is still present.

- [ ] **Step 4: Compose all three evidence channels in `cmd_check()`**

Use this data flow:

```python
snapshot = _load_snapshot(snapshot_path)
audit_head = _head_oid(vault)
rules = AuditRules.load(vault)
_validate_receipt_stores(vault, rules)
records = collect_commit_records(vault, snapshot.head, audit_head)
current = collect_gate3_evidence(vault)
commit_result = _audit_commit_history(
    records, vault, snapshot.head, audit_head
)
dirty_audit = audit_dirty(
    snapshot.evidence.dirty,
    current.dirty,
    rules,
    vault,
    records=records,
)
dirty_paths = _classify_dirty_path_changes(
    snapshot.evidence.dirty, current.dirty, dirty_audit
)
filesystem_audit = audit_filesystem(
    snapshot.evidence.filesystem,
    current.filesystem,
    rules,
    classified_paths=commit_result.path_changes + dirty_paths,
)
```

Merge the three `Audit` values into one result with sorted, deduplicated lists.
Keep the existing final HEAD equality check after all evidence and audits.
Print aggregate counts plus violating synthetic-relative paths through the
existing CLI behavior; never print raw exception causes.

- [ ] **Step 5: Retire the canonical-only supplement**

Delete `_supplement_untracked_consumed_entries()` and its call. Make
`collect_dirty_fingerprints()` a compatibility wrapper over
`_collect_git_dirty_inputs()` plus `_fingerprint_git_dirty_inputs()` only.
Replace the old canonical-store-only FIFO test with the boundary-wide CLI and
collector tests above. Do not remove or weaken any exact S7 record test.

- [ ] **Step 6: Normalize controlled failures**

At the public collector boundary, translate observed OS/classification errors
to `FilesystemEvidenceError` with one of the approved constant messages.
`main()` already catches `ValueError`; keep that controlled exit path. Ensure
exception chaining is never interpolated into CLI output.

- [ ] **Step 7: Run GREEN, mutation proof, and the checkpoint gate**

Run:

```bash
uv run python -m pytest tests/test_gate3_audit.py \
  -k 'wrong_location or unrelated_entity_local or empty_directory or canonical_empty or controlled_error or unequal_git' -q
uv run python -m pytest tests/test_gate3_audit.py -q
```

Then deliberately disable the new boundary-wide filesystem comparison call in
`cmd_check()` without committing, run the wrong-location FIFO regression, and
require RED. Restore the exact implementation, rerun the same test, and require
GREEN. Confirm `git diff` matches the pre-mutation implementation exactly.

Complete the global Checkpoint Gate and create the Task 8 checkpoint.

---

### Task 9: Final verification, review, publication, and merge-authorization stop

**Files:**
- Modify: only `tools/gate3_audit.py` and `tests/test_gate3_audit.py` if a
  verified review finding requires a correction
- Review: approved design commit through final branch head

**Interfaces:**
- Consumes: completed Tasks 1–8, the opaque preservation envelope,
  `superpowers:verification-before-completion`,
  `superpowers:requesting-code-review`, `superpowers:receiving-code-review`,
  and `superpowers:finishing-a-development-branch`.
- Produces: fresh local acceptance evidence, an independently reviewed final
  branch, a sanitized pull request, CodeRabbit review of the final pushed head,
  and a stop for owner merge authorization.

- [ ] **Step 1: Run focused final RED/GREEN evidence**

Run the named Task 8 wrong-location FIFO mutation proof once more. Record only
the test name and RED/GREEN result, not temporary paths or source values.

Run the full focused module:

```bash
uv run python -m pytest tests/test_gate3_audit.py -q
```

Expected: all Gate 3 tests pass.

- [ ] **Step 2: Run the complete public acceptance suite**

Run:

```bash
uv run python -m pytest -q
git diff --check
tools/run_gitleaks.sh .
uv run python -m tools.public_repo_audit --repo .
uv run python -m tools.public_repo_audit --repo . --history
```

Expected: at least 1,847 tests pass; whitespace is clean; Gitleaks is clean;
both public audits report clean. If Gitleaks differs across clones, identify a
retaining ref without deleting or weakening it and stop for owner direction.

- [ ] **Step 3: Run final trusted-local gates inside the original envelope**

Through the non-echo trusted-local runner, run the exact private unittest,
structural validation, policy self-test, and combined repository-plus-vault
history audit prescribed by `BUILD.md`. Set the vault environment inline for
each command. Require at least 39 private tests, zero errors/zero warnings,
policy pass, and clean combined audit.

Immediately recapture and byte-compare the four protected streams with Task
1's preimages. Require four equal results. Do not print or repair differences;
stop if any differs.

- [ ] **Step 4: Obtain independent scoped review before pushing**

Invoke `superpowers:requesting-code-review` over the approved design checkpoint
through current HEAD. The review prompt must include only public requirements
and synthetic evidence. Ask for findings grouped as Critical, Important, and
Minor, covering:

- schema closure and compatibility refusal;
- no-follow traversal and descriptor closure;
- exclusion precision and ignored-regular scope;
- race/error fail-closed behavior;
- baseline preservation and type confusion;
- directory ancestry and exact quarantine exception;
- unchanged S7 record predicates; and
- test gaps or unrelated scope.

If findings arrive, use `superpowers:receiving-code-review`: verify each finding
against the spec, add a RED regression for an accepted behavioral defect,
implement the smallest fix, rerun its GREEN command, repeat Tasks 9.2–9.3, and
request another scoped review. Return disputed or scope-expanding findings to
the owner. Do not push with an unresolved finding.

- [ ] **Step 5: Re-fetch the canonical baseline, verify branch relationships, and push**

Run in one shell without printing subjects:

```bash
set -euo pipefail
gate3_canonical=fecafea674cc254217d24950e716e42f71353fdc
gate3_branch=$(git branch --show-current)
gate3_reviewed_head=$(git rev-parse HEAD)
gate3_plan_head=$(git rev-list -1 HEAD -- \
  docs/superpowers/plans/2026-08-29-gate3-filesystem-evidence.md)
git status --short --branch
git diff --name-only "$gate3_plan_head"..HEAD
git rev-list --reverse "$gate3_plan_head"..HEAD
git fetch --prune origin
test "$(git rev-parse refs/remotes/origin/main)" = "$gate3_canonical"
git merge-base --is-ancestor "$gate3_canonical" "$gate3_reviewed_head"
gate3_remote_ref="refs/remotes/origin/$gate3_branch"
if git show-ref --verify --quiet "$gate3_remote_ref"; then
  gate3_previous_remote=$(git rev-parse "$gate3_remote_ref")
  git merge-base --is-ancestor "$gate3_canonical" "$gate3_previous_remote"
  git merge-base --is-ancestor "$gate3_previous_remote" "$gate3_reviewed_head"
fi
```

Expected: clean worktree; only `tools/gate3_audit.py` and
`tests/test_gate3_audit.py` differ after the planning checkpoint; every
implementation checkpoint is present; fetched `origin/main` remains exactly
the canonical SHA; the reviewed local head descends from it; and any existing
remote task branch is either equal to or an ancestor of the reviewed local
head. Do not copy commit subjects into task, review, or pull-request output.

If `origin/main` moved, the task branch lost canonical ancestry, or an existing
remote task branch is ahead or divergent, stop before pushing or opening a
pull request and return a bounded decision memo. Do not automatically rebase,
merge, reset, retarget, force-push, or rewrite any commit.

Push the dedicated branch without force only after the evidence, canonical
baseline check, branch-relationship check, and independent review are clean:

```bash
set -euo pipefail
gate3_branch=$(git branch --show-current)
gate3_reviewed_head=$(git rev-parse HEAD)
git push --set-upstream origin "$gate3_branch"
git fetch origin \
  "refs/heads/$gate3_branch:refs/remotes/origin/$gate3_branch"
test "$(git rev-parse "refs/remotes/origin/$gate3_branch")" = \
  "$gate3_reviewed_head"
test "$(git rev-parse HEAD)" = "$gate3_reviewed_head"
```

The exact fetched remote branch head must equal the locally reviewed head.
Stop without opening a pull request if either equality fails.

- [ ] **Step 6: Reverify the remote head, open a sanitized pull request, and obtain CodeRabbit review**

Immediately before opening the pull request, fetch again and repeat both the
canonical-main and reviewed-head equalities:

```bash
set -euo pipefail
gate3_canonical=fecafea674cc254217d24950e716e42f71353fdc
gate3_branch=$(git branch --show-current)
gate3_reviewed_head=$(git rev-parse HEAD)
git fetch --prune origin
test "$(git rev-parse refs/remotes/origin/main)" = "$gate3_canonical"
test "$(git rev-parse "refs/remotes/origin/$gate3_branch")" = \
  "$gate3_reviewed_head"
test "$(git rev-parse HEAD)" = "$gate3_reviewed_head"
```

Stop before pull-request creation if `origin/main` moved or either branch-head
equality fails. Do not rebase or merge a moved baseline and do not force-push a
mismatched task branch.

Open one pull request whose title/body contain only the generic problem,
approved design link, synthetic RED/GREEN proof, aggregate test counts, gate
outcomes, and explicit non-goals. Include no local path, private filename,
registry value, source content, preserved-state detail, or commit subject.

Immediately before requesting CodeRabbit, fetch the task branch again and
require the exact remote branch head still equals both the locally reviewed
head and local `HEAD`:

```bash
set -euo pipefail
gate3_branch=$(git branch --show-current)
gate3_reviewed_head=$(git rev-parse HEAD)
git fetch origin \
  "refs/heads/$gate3_branch:refs/remotes/origin/$gate3_branch"
test "$(git rev-parse "refs/remotes/origin/$gate3_branch")" = \
  "$gate3_reviewed_head"
test "$(git rev-parse HEAD)" = "$gate3_reviewed_head"
```

Request CodeRabbit review only after both equalities pass. Confirm CodeRabbit
reviewed that exact remote OID. For each finding:

- verify it technically with `superpowers:receiving-code-review`;
- add RED first for an accepted behavior correction;
- implement minimal GREEN;
- rerun all of Tasks 9.1–9.4;
- repeat Steps 9.5–9.6 with the corrected reviewed head; and
- request CodeRabbit review again only after the repeated remote-head checks.

Resolve all findings or return them to the owner for a bounded decision. Never
merge while a finding or head mismatch remains.

- [ ] **Step 7: Invoke branch-finishing workflow and stop before merge**

Use `superpowers:finishing-a-development-branch` only after final verification,
independent review, and CodeRabbit are clean. Select the option that leaves the
reviewed pull request open and preserves the branch/worktree. Do not delete or
clean any branch, worktree, or evidence.

Report final pushed head, changed files, aggregate public/private gate results,
four preservation equalities, independent findings by severity, CodeRabbit
status, and pull-request link. Stop and request explicit owner merge
authorization. Do not run live Gate 3, Gate 1 timing, deployment, or Phase 2.
