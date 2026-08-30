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

**Spec:** `docs/superpowers/specs/2026-08-29-gate3-filesystem-evidence-design.md`

- Tasks 1–9 were written against **Design Revision 0**, approved at
  `03be199cee333641700a0c347595d7d88125b194`. They are complete and are
  retained here as a historical record. Do not re-run them.
- Task 10 is written against **Design Revision 2**, approved at
  `acc3f309f04a285fbec46acf0a0cc99d0175e101`, specifically its
  "Sanctioned rename topology" rule and its "Evidence-model limitation:
  identity is not proof of a move".
- Tasks 11 and 12 are written against **Design Revision 5**, approved and
  recorded at `4a6530168d0299a0ac895f49c873693a75875a12`. Task 11 implements its
  non-directory rename-topology inheritance and three-phase disposition
  order; Task 12 implements its one immutable per-record rename analysis.

**Plan revision history:**

- Revision 1 — Tasks 1–9, executed to completion. Its Task 9 independent
  review reproduced finding I1: a sanctioned entity rename moving a
  directory with no tracked descendant reported both endpoints as
  unsanctioned direct writes.
- Revision 2 — added Task 10 for I1. Superseded.
- Revision 3 — Task 10 corrected after owner review. Superseded: its
  composition regressed the exact chain `a → b → c` to a fail-closed empty
  result, because the forward rewrite already produced `a → c` and the
  derivation step appended a duplicate that the conflict check then rejected.
  Its `_source_preimage()` also rejected every case with more than one
  matching accumulated destination, which is the normal shape after a nested
  rename.
- Revision 4 — Task 10 corrected again. Executed to completion at
  `69cbff7681c00a9347ec1be22cc40ef72788730e`.
- Revision 5 — added Tasks 11 and 12. Superseded.
- Revision 6 — corrected Task 12's authority and evidence model. Superseded.
- Revision 7 — superseded. Tasks 1–10 are historical and must not be
  re-run. Adds **Task 11** (I5, non-directory rename inheritance — a
  regression this branch introduces relative to canonical `origin/main`) and
  **Task 12** (I4, one immutable per-record rename analysis), sequential and
  in that order. Plan
  Revision 2's composition was wrong for the **ancestor-then-nested** order:
  it retained the general `a → b` mapping plus a mapping still rooted beneath
  `b`, so an original path under `a/.../oldproduct/...` predicted the
  intermediate `b/.../oldproduct/...` destination and never paired.
  Composition now derives a later mapping's original source pre-image, and
  pairing selects the unique most-specific applicable mapping instead of the
  first tuple entry. Adds the missing wrong-old-root case and replaces the
  grep-based unchanged-helpers claim with an exact AST comparison. Tasks 1–9
  remain unchanged. Revision 6 also binds Task 12 to Design Revision 5:
  permanent tests use literal expectations rather than a branch-local Git
  object, the historical differential oracle is disposable and untracked,
  expected and unexpected later-axis failures are exercised separately, the
  two-record test is included in RED/GREEN commands, and both mutation
  ledgers and collected-case forecasts are internally consistent. The
  development oracle then proved that `changes[:1]` is not malformed for the
  one-change workspace envelope; Revision 7 uses the explicitly empty tuple
  for a portable malformed/empty-envelope refusal on all five axes.
- Revision 8 — this document. Task 13's first independent review found two
  accepted defects. A paired tracked special entry retained its sanctioned
  Git classification and could therefore sanction a refused enclosing
  directory pair through ancestry. The correction removes only sanctioned
  classifications for paired non-directories; violating classifications
  remain authoritative. The same review found that the checkout-setup error
  boundary also covered per-axis analysis, silently converting an unexpected
  later-axis `ValueError` or `CalledProcessError` into an ordinary refusal.
  The correction scopes that handler to `_parent_tree`; unexpected per-axis
  failures now reach the controlled CLI error boundary. These corrections add
  three collected cases, require two additional mutation proofs, and permit
  `audit_filesystem` to differ in the final Task 12 AST comparison.

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

---

## Task 10 status and standing constraints

Task 10 is the only outstanding work in this plan. Tasks 1–9 are historical:
their commands, counts and stop conditions are a record of what was done, not
current instructions.

**Baseline for Task 10:** branch `codex/gate3-finding-a-filesystem-evidence-20260829`
at `acc3f309f04a285fbec46acf0a0cc99d0175e101`, whose public suite stands at
**1950 passed, 1 skipped** and whose Gate 3 module stands at **228 passed,
1 skipped**. The one skip is the genuine APFS undecodable-name limitation.

**Gates deferred by owner amendment, still deferred:**

- The trusted-local private integration gate is **deferred to the new final
  HEAD** produced by Task 10. This session has no trusted-local vault
  capability; `ONEOS_VAULT` stays unset and no private command runs.
- **Push, pull-request creation, and CodeRabbit remain blocked** until the
  owner runs that private gate against the exact final HEAD and returns a
  sanitized PASS.
- If CodeRabbit later requires any code change, the resulting HEAD requires
  **another** trusted-local private integration gate before merge. A private
  gate result is bound to the exact HEAD it ran against and never carries
  forward across a new commit.

**Evidence-model constraint (Design Revision 2).** `identity_digest` covers
object kind, device and inode. A reused inode can make a delete-plus-create
indistinguishable from a move. This is an accepted observational limitation.
Task 10 must not introduce timestamps, platform birth time, content reads,
any new dependency, or a general directory-move whitelist to close it.

---

### Task 10: Inherit directory topology from a verified sanctioned rename

**Files:**
- Modify: `tools/gate3_audit.py`
- Test: `tests/test_gate3_audit.py`

No other tracked file changes. The existing commit-sanctioning decision and
the classifier taxonomy are unchanged by every step below, and Step 21 proves
it rather than asserting it.

**Interfaces:**
- Consumes: `CommitRecord`, `AuditRules`, `Audit`, `CommitAuditResult`,
  `ClassifiedPathChange`, `FilesystemChange`, `FilesystemFingerprint`,
  `_parent_tree()`, `_rename_envelope()`, `_sanctioned_rename()`,
  `_audit_commit_history()`, `audit_filesystem()`, `_RENAME_MESSAGE`,
  `AXES`, `RenameError`, and `build_rename_plan()` from `app.rename`.
- Produces:

```text
@dataclass(frozen=True)
class RenameMapping:
    old_root: str
    new_root: str

_rename_move_pairs(
    tree: Path, axis: str, old: str, new: str, *, parent_oid: str
) -> tuple[RenameMapping, ...]
_matching_rename_axes(
    record: CommitRecord, vault: Path, old: str, new: str
) -> tuple[tuple[str, tuple[RenameMapping, ...]], ...]
_verified_rename_mappings(
    record: CommitRecord, vault: Path
) -> tuple[RenameMapping, ...]
_rewrite_destination(destination: str, mapping: RenameMapping) -> str | None
_source_preimage(
    old_root: str, composed: tuple[RenameMapping, ...]
) -> str | None
_compose_rename_mappings(
    ordered: tuple[tuple[RenameMapping, ...], ...]
) -> tuple[RenameMapping, ...]
_predict_rename_destination(
    path: str, mappings: tuple[RenameMapping, ...]
) -> str | None
_paired_rename_directories(
    changes: tuple[FilesystemChange, ...],
    mappings: tuple[RenameMapping, ...],
) -> frozenset[str]
```

`CommitAuditResult` gains one field:

```python
@dataclass(frozen=True)
class CommitAuditResult:
    audit: Audit
    path_changes: tuple[ClassifiedPathChange, ...]
    rename_mappings: tuple[RenameMapping, ...]
```

`audit_filesystem()` gains one keyword-only parameter:

```text
audit_filesystem(
    before: dict[str, FilesystemFingerprint],
    after: dict[str, FilesystemFingerprint],
    rules: AuditRules,
    *,
    classified_paths: tuple[ClassifiedPathChange, ...],
    rename_mappings: tuple[RenameMapping, ...] = (),
) -> Audit
```

**Composition semantics.** Mappings fold oldest-first. A later mapping stands
in exactly one of three relations to the accumulated set, and each is handled
differently. Collapsing any two of them breaks one of the others.

1. **Later root *equals* an accumulated destination** (`a → b`, then
   `b → c`). The forward rewrite already carries `a` through to `c`, so the
   later mapping is **consumed** and nothing is appended. Appending a derived
   `a → c` here would duplicate the source and trip the conflict check,
   turning an ordinary sequential rename into a fail-closed empty result.
2. **Later root is *strictly beneath* an accumulated destination** (`a → b`,
   then `b/M/op → b/M/np`). The forward rewrite does not apply. The later
   mapping is appended under its **original source pre-image**, derived
   through the unique most-specific accumulated mapping. Without this an
   original path `a/M/op/x` matches only the general mapping and predicts the
   intermediate `b/M/op/x`, which never exists on disk.
3. **Later root is an *ancestor* of an accumulated destination**
   (`a/M/op → a/M/np`, then `a → b`). The accumulated destination is
   rewritten forward, **and** the later general mapping is also retained so
   original tails the nested rename never touched still map.

Pre-image derivation selects the unique **longest** matching accumulated
destination. General and specific mappings legitimately coexist after a
nested rename, so more than one match is normal and must not fail closed; a
third rename beneath the second would otherwise be rejected. Only equally
specific candidates predicting *different* sources are ambiguous.

The empty tuple is returned for: equally specific disagreeing pre-images, two
rewrites applying to one accumulated destination, a duplicated source, or a
duplicated destination.

**Pairing semantics.** Pairing never depends on tuple order. For a removed
path, every mapping whose `old_root` matches is a candidate; the unique
**longest** `old_root` wins. Equally specific candidates predicting different
destinations fail closed, as do two destinations for one removed path and two
removed paths for one added path.

**Data flow.** `_audit_commit_history()` walks records oldest-first and, for
each record whose commit its existing per-commit audit sanctioned, appends
`_verified_rename_mappings(record, vault)`. The ordered groups fold through
`_compose_rename_mappings()` onto `CommitAuditResult.rename_mappings`.
`cmd_check()` passes that to `audit_filesystem()`, which consults it only
after the violating-descendant rule and only for directory presence deltas.

**Composition probe (executed before this plan was committed).** Every
composition case below was run against the exact algorithm in Step 10, in a
standalone synthetic probe outside the repository. These are the literal
resulting tuples:

```text
exact chain a->b->c          -> (('a', 'c'),)
nested-then-ancestor         -> (('a/M/op', 'b/M/np'), ('a', 'b'))
                                predict(a/M/op/x) = b/M/np/x
ancestor-then-nested         -> (('a', 'b'), ('a/M/op', 'b/M/np'))
                                predict(a/M/op/x) = b/M/np/x
ancestor->nested->deeper     -> (('a', 'b'), ('a/M/op', 'b/M/np'),
                                 ('a/M/op/dp', 'b/M/np/dpr'))
                                predict(a/M/op/dp/x) = b/M/np/dpr/x
general tail retained        -> (('a', 'b'), ('a/M/op', 'b/M/np'))
                                predict(a/other/x) = b/other/x
conflict one-src-two-dst     -> ()
conflict two-src-one-dst     -> ()
equally-specific preimage    -> None (ambiguous, hand-built)
overlap general-first        -> predict(a/M/op/x) = b/M/np/x
overlap specific-first       -> predict(a/M/op/x) = b/M/np/x
```

The implementer should reproduce these exact tuples; a difference means the
transcribed algorithm diverged from the probed one.

- [ ] **Step 1: Write the RED reproduction of I1**

Add to `tests/test_gate3_audit.py`:

```python
def test_cli_sanctioned_rename_of_an_untracked_only_directory_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A sanctioned rename must not fail the gate on a directory Git cannot see.

    `archive/` holds no tracked file, so nothing is ever classified beneath
    it. Both endpoints were reported as unsanctioned direct writes even though
    the rename commit itself was sanctioned, and the operator had no remedy
    but to delete the directory.
    """
    files, old, new = _rename_files("entity")
    vault = git_vault(tmp_path / "vault", files)
    (vault / old / "11-library" / "archive").mkdir(parents=True, exist_ok=True)
    snapshot = tmp_path / "gate3.json"
    monkeypatch.setenv("ONEOS_VAULT", os.fspath(vault))
    monkeypatch.setenv("GATE3_SNAP", os.fspath(snapshot))
    assert gate3.main(["snapshot"]) == 0

    apply_rename(vault, plan_rename(vault, "entity", old, new), validators=[])

    assert gate3.main(["check"]) == 0
```

- [ ] **Step 2: Run it and observe RED**

```bash
uv run python -m pytest tests/test_gate3_audit.py \
  -k 'sanctioned_rename_of_an_untracked_only_directory' -q
```

Expected: FAIL. `check` exits 1 and prints
`VIOLATION direct write: <new>/11-library/archive` and
`VIOLATION direct write: <old>/11-library/archive`. Record both lines as the
RED evidence for Task 10.

- [ ] **Step 3: Extract move pairs from the rename planner**

Add beside `_rename_envelope()` in `tools/gate3_audit.py`:

```python
@dataclass(frozen=True)
class RenameMapping:
    """One verified old-root to new-root move, vault-relative."""

    old_root: str
    new_root: str


def _rename_move_pairs(
    tree: Path, axis: str, old: str, new: str, *, parent_oid: str
) -> tuple[RenameMapping, ...]:
    """The move pairs of one rename plan, in the planner's own order.

    `_rename_envelope` computes these and discards them. Rebuilding the plan
    here leaves the envelope comparison byte-for-byte unchanged, so the
    sanctioning decision cannot shift.
    """
    plan = build_rename_plan(tree, axis, old, new, planned_head=parent_oid)
    planned_root = plan.vault
    return tuple(
        RenameMapping(
            old_root=source.relative_to(planned_root).as_posix(),
            new_root=destination.relative_to(planned_root).as_posix(),
        )
        for source, destination in plan.moves
    )
```

- [ ] **Step 4: Write RED tests for mapping authorization and axis ambiguity**

```python
def _rec(oid: str, message: str, changes) -> gate3.CommitRecord:
    return gate3.CommitRecord(
        oid=oid,
        message=message,
        parents=("e" * 40,),
        changes=tuple(
            gate3.PathChangeRecord(status, path) for status, path in changes
        ),
    )


def test_unsanctioned_rename_commit_contributes_no_mapping(tmp_path: Path):
    """Only a commit the existing verification accepted may map anything."""
    files, old, new = _rename_files("entity")
    vault = git_vault(tmp_path / "vault", files)
    record = _rec("a" * 40, f"rename: {old} → {new}", (("M", "unrelated.md"),))

    assert gate3._verified_rename_mappings(record, vault) == ()


def test_ambiguous_axis_match_contributes_no_mapping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Two axes reproducing one envelope is ambiguous, so nothing is mapped."""
    files, old, new = _rename_files("entity")
    vault = git_vault(tmp_path / "vault", files)
    monkeypatch.setattr(gate3, "_sanctioned_rename", lambda *_a, **_k: True)
    monkeypatch.setattr(
        gate3,
        "_matching_rename_axes",
        lambda *_a, **_k: (
            ("entity", (gate3.RenameMapping(old, new),)),
            ("product", (gate3.RenameMapping(old, new),)),
        ),
    )
    record = _rec("a" * 40, f"rename: {old} → {new}", (("M", "x.md"),))

    assert gate3._verified_rename_mappings(record, vault) == ()
```

- [ ] **Step 5: Run them and observe RED**

```bash
uv run python -m pytest tests/test_gate3_audit.py \
  -k 'contributes_no_mapping' -q
```

Expected: FAIL, `AttributeError` on `_verified_rename_mappings`.

- [ ] **Step 6: Evaluate every axis without changing sanctioning**

```python
def _matching_rename_axes(
    record: CommitRecord, vault: Path, old: str, new: str
) -> tuple[tuple[str, tuple[RenameMapping, ...]], ...]:
    """Every axis whose envelope equals the commit, evaluated to completion.

    `_sanctioned_rename` returns on its first matching axis and so can never
    observe a second one. Ambiguity detection needs the full set. This is a
    separate pass on purpose: the sanctioning decision keeps its early return
    and its behaviour is not altered here.
    """
    actual = frozenset((change.status, change.path) for change in record.changes)
    matches: list[tuple[str, tuple[RenameMapping, ...]]] = []
    temporary: tempfile.TemporaryDirectory[str] | None = None
    try:
        temporary, tree, tracked = _parent_tree(vault, record.parents[0])
        for axis in sorted(AXES):
            try:
                expected = _rename_envelope(
                    tree, tracked, axis, old, new, parent_oid=record.parents[0]
                )
                if not expected or actual != expected:
                    continue
                pairs = _rename_move_pairs(
                    tree, axis, old, new, parent_oid=record.parents[0]
                )
            except (OSError, RenameError, UnicodeError, sqlite3.Error):
                continue
            matches.append((axis, pairs))
    except (OSError, subprocess.CalledProcessError, ValueError):
        return ()
    finally:
        if temporary is not None:
            temporary.cleanup()
    return tuple(matches)


def _verified_rename_mappings(
    record: CommitRecord, vault: Path
) -> tuple[RenameMapping, ...]:
    """Mappings only from a commit the existing verification already accepted."""
    match = _RENAME_MESSAGE.fullmatch(record.message)
    if match is None or len(record.parents) != 1:
        return ()
    if not _sanctioned_rename(record, vault):
        return ()
    old, new = match.groups()
    matches = _matching_rename_axes(record, vault, old, new)
    if len(matches) != 1:
        # Zero means the envelope could not be reproduced here; more than one
        # is ambiguous. Both contribute nothing rather than a best guess.
        return ()
    return matches[0][1]
```

- [ ] **Step 7: Run and confirm GREEN**

```bash
uv run python -m pytest tests/test_gate3_audit.py \
  -k 'contributes_no_mapping' -q
```

Expected: 2 passed.

- [ ] **Step 8: Write RED composition tests for both nesting orders**

```python
def _m(old_root: str, new_root: str) -> gate3.RenameMapping:
    return gate3.RenameMapping(old_root, new_root)


def test_rename_mappings_compose_oldest_first_over_exact_roots():
    """An exact chain is consumed by the forward rewrite, not duplicated.

    Appending a derived `a → c` beside the rewritten one duplicates the
    source, which the conflict check then rejects — turning an ordinary
    sequential rename into a fail-closed empty result.
    """
    composed = gate3._compose_rename_mappings(((_m("a", "b"),), (_m("b", "c"),)))

    assert composed == (_m("a", "c"),)


def test_rename_mappings_compose_through_a_deeper_nested_chain():
    """A third rename beneath the second must find the most-specific source.

    Two accumulated mappings match `b/M/np/dp` — the general `a → b` and the
    specific `a/M/op → b/M/np`. Rejecting multiple matches would fail this
    ordinary three-rename sequence closed.
    """
    composed = gate3._compose_rename_mappings(
        (
            (_m("a", "b"),),
            (_m("b/M/op", "b/M/np"),),
            (_m("b/M/np/dp", "b/M/np/dpr"),),
        )
    )

    assert composed == (
        _m("a", "b"),
        _m("a/M/op", "b/M/np"),
        _m("a/M/op/dp", "b/M/np/dpr"),
    )
    assert (
        gate3._predict_rename_destination("a/M/op/dp/x", composed)
        == "b/M/np/dpr/x"
    )


@pytest.mark.parametrize("order", ("general-first", "specific-first"))
def test_source_preimage_selects_the_unique_most_specific_mapping(order: str):
    """Accumulated tuple order must not decide the derived source."""
    composed = (_m("a", "b"), _m("a/M/op", "b/M/np"))
    if order == "specific-first":
        composed = tuple(reversed(composed))

    assert gate3._source_preimage("b/M/np/dp", composed) == "a/M/op/dp"


def test_source_preimage_fails_closed_on_equally_specific_disagreement():
    """Two same-length destinations predicting different sources is ambiguous.

    Destination uniqueness makes this unreachable through composition today,
    so the guard is defensive and is asserted directly rather than through a
    scenario that cannot occur.
    """
    assert gate3._source_preimage("p/q/z", (_m("x", "p/q"), _m("y", "p/q"))) is None


@pytest.mark.parametrize(
    ("ordered", "expected"),
    (
        (
            ((_m("a/M/op", "a/M/np"),), (_m("a", "b"),)),
            (_m("a/M/op", "b/M/np"), _m("a", "b")),
        ),
        (
            ((_m("a", "b"),), (_m("b/M/op", "b/M/np"),)),
            (_m("a", "b"), _m("a/M/op", "b/M/np")),
        ),
    ),
    ids=("nested-then-ancestor", "ancestor-then-nested"),
)
def test_rename_mappings_compose_across_nesting_orders(ordered, expected):
    """Both nesting orders must end at the original source and final target.

    Ancestor-then-nested is the order Plan Revision 2 got wrong: it kept
    `a → b` beside `b/M/op → b/M/np`, so `a/M/op/x` matched only the general
    mapping and predicted the intermediate `b/M/op/x`.
    """
    assert gate3._compose_rename_mappings(ordered) == expected


def test_composed_mappings_retain_the_general_tail():
    """A nested rename must not shadow tails it does not touch."""
    composed = gate3._compose_rename_mappings(
        ((_m("a", "b"),), (_m("b/M/op", "b/M/np"),))
    )

    assert gate3._predict_rename_destination("a/other/x", composed) == "b/other/x"
    assert (
        gate3._predict_rename_destination("a/M/op/x", composed) == "b/M/np/x"
    )


@pytest.mark.parametrize(
    "ordered",
    (
        ((_m("a", "b"), _m("a", "c")),),
        ((_m("a", "c"), _m("b", "c")),),
    ),
    ids=("one-source-two-destinations", "two-sources-one-destination"),
)
def test_conflicting_rename_mappings_contribute_nothing(ordered):
    assert gate3._compose_rename_mappings(ordered) == ()
```

- [ ] **Step 9: Run them and observe RED**

```bash
uv run python -m pytest tests/test_gate3_audit.py \
  -k 'rename_mappings_compose or composed_mappings_retain or source_preimage or conflicting_rename_mappings' -q
```

Expected: FAIL, `AttributeError` on `_compose_rename_mappings` and
`_source_preimage`.

- [ ] **Step 10: Implement bidirectional prefix composition**

```python
def _rewrite_destination(destination: str, mapping: RenameMapping) -> str | None:
    """Apply one later mapping to an earlier destination, or None."""
    if destination == mapping.old_root:
        return mapping.new_root
    prefix = mapping.old_root + "/"
    if destination.startswith(prefix):
        return mapping.new_root + "/" + destination[len(prefix):]
    return None


def _source_preimage(
    old_root: str, composed: tuple[RenameMapping, ...]
) -> str | None:
    """The original source a later mapping's root sits under, if any.

    Returns the sentinel `""` when nothing matches, so the caller can
    distinguish "no earlier mapping applies" from ambiguity, which returns
    None.

    Selection is by longest matching destination. A general and a specific
    mapping legitimately coexist after a nested rename, so several matches is
    the normal shape, not an error: rejecting it would fail an ordinary
    three-rename sequence closed. Only equally specific candidates predicting
    different sources are ambiguous.
    """
    matches = [
        mapping
        for mapping in composed
        if old_root == mapping.new_root
        or old_root.startswith(mapping.new_root + "/")
    ]
    if not matches:
        return ""
    best = max(len(mapping.new_root) for mapping in matches)
    predicted = set()
    for mapping in matches:
        if len(mapping.new_root) != best:
            continue
        if old_root == mapping.new_root:
            predicted.add(mapping.old_root)
        else:
            tail = old_root[len(mapping.new_root) + 1:]
            predicted.add(mapping.old_root + "/" + tail)
    if len(predicted) != 1:
        return None
    return predicted.pop()


def _compose_rename_mappings(
    ordered: tuple[tuple[RenameMapping, ...], ...],
) -> tuple[RenameMapping, ...]:
    """Fold per-commit mappings oldest-first, failing closed on ambiguity.

    Three relations matter and each is handled differently. A later root that
    equals an accumulated destination is consumed by the forward rewrite; one
    strictly beneath it is appended under its original source pre-image; one
    that is an ancestor rewrites the accumulated destination forward and is
    also retained for untouched tails. Collapsing any two of these breaks one
    of the others: appending on the exact match duplicates a source, and
    skipping the pre-image leaves an original path predicting an intermediate
    destination that never exists on disk.
    """
    composed: tuple[RenameMapping, ...] = ()
    for group in ordered:
        rewritten: list[RenameMapping] = []
        for existing in composed:
            applied = [
                candidate
                for mapping in group
                if (candidate := _rewrite_destination(existing.new_root, mapping))
                is not None
            ]
            if len(applied) > 1:
                return ()
            rewritten.append(
                RenameMapping(existing.old_root, applied[0])
                if applied
                else existing
            )
        added: list[RenameMapping] = []
        for mapping in group:
            # Exact-chain consumption. The forward rewrite above already
            # carried an accumulated mapping through to this destination;
            # appending a derived duplicate would trip the conflict check and
            # fail an ordinary sequential rename closed.
            if any(
                existing.new_root == mapping.old_root for existing in composed
            ):
                continue
            preimage = _source_preimage(mapping.old_root, composed)
            if preimage is None:
                return ()
            added.append(
                RenameMapping(preimage or mapping.old_root, mapping.new_root)
            )
        composed = tuple(rewritten + added)
        sources = [mapping.old_root for mapping in composed]
        destinations = [mapping.new_root for mapping in composed]
        if len(set(sources)) != len(sources) or len(set(destinations)) != len(
            destinations
        ):
            return ()
    return composed


def _predict_rename_destination(
    path: str, mappings: tuple[RenameMapping, ...]
) -> str | None:
    """Where a verified rename says this path went, or None.

    Selection is by longest matching `old_root`, never by tuple order: a
    general and a specific mapping both apply to a nested path, and only the
    specific one names the destination that exists on disk. Equally specific
    candidates disagreeing is ambiguity, and fails closed.
    """
    candidates: list[tuple[int, str]] = []
    for mapping in mappings:
        if path == mapping.old_root:
            candidates.append((len(mapping.old_root), mapping.new_root))
        elif path.startswith(mapping.old_root + "/"):
            tail = path[len(mapping.old_root) + 1:]
            candidates.append(
                (len(mapping.old_root), mapping.new_root + "/" + tail)
            )
    if not candidates:
        return None
    best = max(length for length, _ in candidates)
    predicted = {value for length, value in candidates if length == best}
    if len(predicted) != 1:
        return None
    return predicted.pop()
```

- [ ] **Step 11: Run and confirm GREEN**

```bash
uv run python -m pytest tests/test_gate3_audit.py \
  -k 'rename_mappings_compose or composed_mappings_retain or source_preimage or conflicting_rename_mappings' -q
```

Expected: 10 passed.

- [ ] **Step 12: Write RED pairing tests, including the wrong-old-root case**

```python
def _pair_fp(mode: int = 0o755, identity: str = "1" * 64):
    return gate3.FilesystemFingerprint("directory", mode, identity, None)


def _pair_audit(before, after, rules, mappings, classified=()):
    return gate3.audit_filesystem(
        before,
        after,
        rules,
        classified_paths=tuple(classified),
        rename_mappings=tuple(mappings),
    )


def test_paired_rename_directories_are_sanctioned_at_both_endpoints(
    tmp_path: Path,
):
    vault = _audit_vault(tmp_path / "vault", initialize_git=True)
    rules = gate3.AuditRules.load(vault)

    audit = _pair_audit(
        {"synthetic/d/empty": _pair_fp()},
        {"renamed/d/empty": _pair_fp()},
        rules,
        (_m("synthetic", "renamed"),),
    )

    assert audit.sanctioned_writes == ["renamed/d/empty", "synthetic/d/empty"]
    assert audit.violating_writes == []


@pytest.mark.parametrize(
    ("before_path", "after_path", "before_fp", "after_fp", "mappings"),
    (
        ("synthetic/d/empty", "renamed/d/empty", _pair_fp(), _pair_fp(), ()),
        (
            "stranger/d/empty",
            "renamed/d/empty",
            _pair_fp(),
            _pair_fp(),
            (_m("synthetic", "renamed"),),
        ),
        (
            "synthetic/d/empty",
            "stranger/d/empty",
            _pair_fp(),
            _pair_fp(),
            (_m("synthetic", "renamed"),),
        ),
        (
            "synthetic/d/empty",
            "renamed/d/other",
            _pair_fp(),
            _pair_fp(),
            (_m("synthetic", "renamed"),),
        ),
        (
            "synthetic/d/empty",
            "renamed/d/empty",
            _pair_fp(),
            _pair_fp(identity="2" * 64),
            (_m("synthetic", "renamed"),),
        ),
        (
            "synthetic/d/empty",
            "renamed/d/empty",
            _pair_fp(),
            _pair_fp(mode=0o700),
            (_m("synthetic", "renamed"),),
        ),
        (
            "synthetic/d/empty",
            "renamed/d/empty",
            _pair_fp(),
            gate3.FilesystemFingerprint("symlink", 0o777, "1" * 64, "3" * 64),
            (_m("synthetic", "renamed"),),
        ),
    ),
    ids=(
        "no-sanctioned-rename",
        "wrong-old-root",
        "wrong-new-root",
        "different-tail",
        "identity-mismatch",
        "mode-mismatch",
        "non-directory-endpoint",
    ),
)
def test_unpaired_rename_shapes_remain_violations(
    tmp_path: Path, before_path, after_path, before_fp, after_fp, mappings
):
    """Every shape outside the verified mapping stays a direct-write violation.

    `wrong-old-root` is the removed path sitting outside the verified old
    root; `wrong-new-root` is the added path sitting outside the verified new
    root. Both must fail, and for different reasons.
    """
    vault = _audit_vault(tmp_path / "vault", initialize_git=True)
    rules = gate3.AuditRules.load(vault)

    audit = _pair_audit(
        {before_path: before_fp}, {after_path: after_fp}, rules, mappings
    )

    assert audit.sanctioned_writes == []
    assert sorted(audit.violating_writes) == sorted({before_path, after_path})


def test_pairing_does_not_sanction_an_unrelated_sibling(tmp_path: Path):
    vault = _audit_vault(tmp_path / "vault", initialize_git=True)
    rules = gate3.AuditRules.load(vault)

    audit = _pair_audit(
        {"synthetic/d/empty": _pair_fp()},
        {"renamed/d/empty": _pair_fp(), "renamed/d/extra": _pair_fp()},
        rules,
        (_m("synthetic", "renamed"),),
    )

    assert audit.violating_writes == ["renamed/d/extra"]
    assert audit.sanctioned_writes == ["renamed/d/empty", "synthetic/d/empty"]


def test_violating_descendant_beneath_a_paired_directory_still_fails(
    tmp_path: Path,
):
    """Pairing is evaluated after the descendant rule and cannot hide it."""
    vault = _audit_vault(tmp_path / "vault", initialize_git=True)
    rules = gate3.AuditRules.load(vault)

    audit = _pair_audit(
        {"synthetic/d/empty": _pair_fp()},
        {"renamed/d/empty": _pair_fp()},
        rules,
        (_m("synthetic", "renamed"),),
        classified=[_classified("renamed/d/empty/bad.md", "added", "violating")],
    )

    assert "renamed/d/empty" not in audit.sanctioned_writes
```

- [ ] **Step 13: Run them and observe RED**

```bash
uv run python -m pytest tests/test_gate3_audit.py \
  -k 'paired_rename or unpaired_rename or pairing_does_not or violating_descendant_beneath' -q
```

Expected: FAIL — `audit_filesystem()` does not accept `rename_mappings`.

- [ ] **Step 14: Write RED order-independence and candidate-ambiguity tests**

```python
@pytest.mark.parametrize("order", ("general-first", "specific-first"))
def test_pairing_selects_the_most_specific_mapping_regardless_of_order(
    tmp_path: Path, order: str
):
    """Tuple order must not decide which destination is predicted."""
    vault = _audit_vault(tmp_path / "vault", initialize_git=True)
    rules = gate3.AuditRules.load(vault)
    mappings = (_m("synthetic", "renamed"), _m("synthetic/d", "renamed/e"))
    if order == "specific-first":
        mappings = tuple(reversed(mappings))

    audit = _pair_audit(
        {"synthetic/d/empty": _pair_fp()},
        {"renamed/e/empty": _pair_fp()},
        rules,
        mappings,
    )

    assert audit.sanctioned_writes == ["renamed/e/empty", "synthetic/d/empty"]
    assert audit.violating_writes == []


def test_equally_specific_mappings_that_disagree_fail_closed(tmp_path: Path):
    """Two candidates of the same specificity are ambiguous, not a choice."""
    vault = _audit_vault(tmp_path / "vault", initialize_git=True)
    rules = gate3.AuditRules.load(vault)

    audit = _pair_audit(
        {"synthetic/d/empty": _pair_fp()},
        {"renamed/d/empty": _pair_fp()},
        rules,
        (_m("synthetic", "renamed"), _m("synthetic", "other")),
    )

    assert audit.sanctioned_writes == []
    assert "synthetic/d/empty" in audit.violating_writes


def test_one_removed_path_never_sanctions_two_destinations(tmp_path: Path):
    vault = _audit_vault(tmp_path / "vault", initialize_git=True)
    rules = gate3.AuditRules.load(vault)

    audit = _pair_audit(
        {"synthetic/d/empty": _pair_fp()},
        {"renamed/d/empty": _pair_fp(), "other/d/empty": _pair_fp()},
        rules,
        (_m("synthetic", "renamed"),),
    )

    assert "other/d/empty" in audit.violating_writes
    assert audit.sanctioned_writes == ["renamed/d/empty", "synthetic/d/empty"]


def test_two_removed_paths_never_share_one_added_path(tmp_path: Path):
    vault = _audit_vault(tmp_path / "vault", initialize_git=True)
    rules = gate3.AuditRules.load(vault)

    audit = _pair_audit(
        {"synthetic/d/empty": _pair_fp(), "second/d/empty": _pair_fp()},
        {"renamed/d/empty": _pair_fp()},
        rules,
        (_m("synthetic", "renamed"), _m("second", "renamed")),
    )

    assert audit.sanctioned_writes == []
    assert sorted(audit.violating_writes) == [
        "renamed/d/empty",
        "second/d/empty",
        "synthetic/d/empty",
    ]
```

- [ ] **Step 15: Run them and observe RED**

```bash
uv run python -m pytest tests/test_gate3_audit.py \
  -k 'most_specific_mapping or equally_specific or two_destinations or two_removed_paths' -q
```

Expected: FAIL — `audit_filesystem()` does not accept `rename_mappings`.

- [ ] **Step 16: Implement order-independent pairing**

```python
def _paired_rename_directories(
    changes: tuple[FilesystemChange, ...],
    mappings: tuple[RenameMapping, ...],
) -> frozenset[str]:
    """Paths a verified sanctioned rename explains, at both endpoints.

    Identity equality is necessary and never sufficient: every other
    requirement is checked here independently, and a reused inode cannot
    substitute for any of them (design "Evidence-model limitation").
    """
    removed = {
        change.path: change.before
        for change in changes
        if change.kind == "removed"
        and change.before is not None
        and change.before.kind == "directory"
    }
    added = {
        change.path: change.after
        for change in changes
        if change.kind == "added"
        and change.after is not None
        and change.after.kind == "directory"
    }
    proposed: dict[str, str] = {}
    for old_path, old_fingerprint in sorted(removed.items()):
        new_path = _predict_rename_destination(old_path, mappings)
        if new_path is None:
            continue
        new_fingerprint = added.get(new_path)
        if new_fingerprint is None:
            continue
        if (
            new_fingerprint.mode != old_fingerprint.mode
            or new_fingerprint.identity_digest != old_fingerprint.identity_digest
        ):
            continue
        proposed[old_path] = new_path
    claimed: dict[str, list[str]] = {}
    for old_path, new_path in proposed.items():
        claimed.setdefault(new_path, []).append(old_path)
    paired: set[str] = set()
    for new_path, sources in claimed.items():
        if len(sources) != 1:
            # Two removed directories cannot both be this one added
            # directory; neither claim is trustworthy.
            continue
        paired.update({sources[0], new_path})
    return frozenset(paired)
```

Change `audit_filesystem()` to accept
`rename_mappings: tuple[RenameMapping, ...] = ()`, compute
`paired = _paired_rename_directories(changes, rename_mappings)` once after
`changes` is built, and insert exactly one clause into the existing
directory-disposition order — **after** the violating-descendant suppression
and the sanctioned-descendant inheritance, and **before** the canonical
quarantine exception:

```python
        if change.path in paired:
            result.sanctioned_writes.append(change.path)
            continue
```

- [ ] **Step 17: Run Steps 12 and 14 GREEN**

```bash
uv run python -m pytest tests/test_gate3_audit.py \
  -k 'paired_rename or unpaired_rename or pairing_does_not or violating_descendant_beneath or most_specific_mapping or equally_specific or two_destinations or two_removed_paths' -q
```

Expected: 15 passed.

- [ ] **Step 18: Thread mappings through the commit audit and the CLI**

```python
@dataclass(frozen=True)
class CommitAuditResult:
    audit: Audit
    path_changes: tuple[ClassifiedPathChange, ...]
    rename_mappings: tuple[RenameMapping, ...]
```

In `_audit_commit_history()`, inside the existing oldest-first loop and
immediately after the per-commit audit:

```python
            if audited.sanctioned_commits:
                ordered_mappings.append(_verified_rename_mappings(record, vault))
```

and return:

```python
    return CommitAuditResult(
        audit=result,
        path_changes=tuple(changes[path] for path in sorted(changes)),
        rename_mappings=_compose_rename_mappings(
            tuple(group for group in ordered_mappings if group)
        ),
    )
```

In `cmd_check()`:

```python
    filesystem_audit = audit_filesystem(
        snapshot.evidence.filesystem,
        current.filesystem,
        rules,
        classified_paths=commit_result.path_changes + dirty_paths,
        rename_mappings=commit_result.rename_mappings,
    )
```

- [ ] **Step 19: Run the I1 reproduction and confirm GREEN**

```bash
uv run python -m pytest tests/test_gate3_audit.py \
  -k 'sanctioned_rename_of_an_untracked_only_directory' -q
```

Expected: 1 passed — `check` returns 0 where it printed two violations.

- [ ] **Step 20: Add CLI regressions for both nesting orders**

```python
def _nested_rename_files(order: str):
    files, old, new = _rename_files("entity")
    files["_system/products.yaml"] = (
        'version: "1.0"\nproducts:\n  ' + old + ":\n"
        "    oldproduct:\n      label: Old\n"
    )
    return files, old, new


@pytest.mark.parametrize("order", ("nested-then-ancestor", "ancestor-then-nested"))
def test_cli_nested_renames_pair_an_untracked_only_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, order: str
):
    """Both nesting orders must reach the same final destination.

    Ancestor-then-nested is the order Plan Revision 2's composition failed:
    the original path matched only the general mapping and predicted the
    intermediate destination.
    """
    files, old, new = _nested_rename_files(order)
    vault = git_vault(tmp_path / "vault", files)
    (vault / old / "11-library" / "archive").mkdir(parents=True, exist_ok=True)
    snapshot = tmp_path / "gate3.json"
    monkeypatch.setenv("ONEOS_VAULT", os.fspath(vault))
    monkeypatch.setenv("GATE3_SNAP", os.fspath(snapshot))
    assert gate3.main(["snapshot"]) == 0

    if order == "nested-then-ancestor":
        apply_rename(
            vault,
            plan_rename(vault, "product", "oldproduct", "newproduct"),
            validators=[],
        )
        apply_rename(
            vault, plan_rename(vault, "entity", old, new), validators=[]
        )
    else:
        apply_rename(
            vault, plan_rename(vault, "entity", old, new), validators=[]
        )
        apply_rename(
            vault,
            plan_rename(vault, "product", "oldproduct", "newproduct"),
            validators=[],
        )

    assert gate3.main(["check"]) == 0
```

Run:

```bash
uv run python -m pytest tests/test_gate3_audit.py \
  -k 'nested_renames_pair' -q
```

Expected: 2 passed. If `ancestor-then-nested` fails, composition is not
deriving the source pre-image; fix `_source_preimage()` rather than relaxing
any pairing requirement.

- [ ] **Step 21: Prove the sanctioning definitions are AST-equivalent**

A name grep proves nothing about a function body. Compare the **normalised
structure** of each named definition against the approved design checkpoint.
`ast.unparse()` round-trips through the parser, so this proves AST
equivalence — identical structure after normalisation — not byte identity:
reformatting, comment edits, and string-quote changes pass. That is the
right strength here, because the claim being defended is that no
sanctioning *behaviour* moved. Write it as an **untracked** script outside
the repository — do not add a tracked utility:

```bash
cat > /tmp/gate3_unchanged.py <<'PY'
import ast, subprocess, sys
BASE = "acc3f309f04a285fbec46acf0a0cc99d0175e101"
NAMES = {
    "_commit_is_sanctioned", "_sanctioned_outbox", "_sanctioned_registry",
    "_sanctioned_ingest", "_sanctioned_rename", "_rename_envelope",
    "_load_consumed_record", "_receipt_authorizations",
    "_sanctioned_consumed_paths", "_new_proposal_is_sanctioned",
    "audit_commits", "_authorization_matches",
}
def defs(source):
    tree = ast.parse(source)
    return {
        node.name: ast.unparse(node)
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in NAMES
    }
old = defs(subprocess.run(
    ["git", "show", f"{BASE}:tools/gate3_audit.py"],
    capture_output=True, text=True, check=True).stdout)
new = defs(open("tools/gate3_audit.py").read())
missing = sorted(NAMES - set(old))
if missing:
    sys.exit(f"FAIL: not found at baseline: {missing}")
changed = sorted(n for n in old if old[n] != new.get(n))
absent = sorted(n for n in old if n not in new)
if changed or absent:
    sys.exit(f"FAIL changed={changed} absent={absent}")
print(f"OK: {len(old)} sanctioning definitions AST-equivalent to {BASE[:8]}")
PY
uv run python /tmp/gate3_unchanged.py
```

Expected: `OK: 12 sanctioning definitions AST-equivalent to acc3f309`. Any
`FAIL` is a stop condition: the sanctioning decision or the classifier
taxonomy moved, which this task forbids.

- [ ] **Step 22: Count the suites and compare against the enumerated total**

Task 10 adds exactly **30** pytest cases. Every parameterization is expanded,
because a parameterized function is one `def` but many collected cases:

| Group | Functions | Collected cases |
|---|---|---|
| I1 CLI reproduction (Step 1) | 1 | 1 |
| mapping authorization and axis ambiguity (Step 4) | 2 | 2 |
| composition (Step 8): exact chain 1, nesting orders ×2, deeper nested chain 1, general tail 1, pre-image most-specific ×2, pre-image ambiguity 1, conflicts ×2 | 7 | 10 |
| pairing (Step 12): both endpoints 1, unpaired shapes ×7, unrelated sibling 1, violating descendant 1 | 4 | 10 |
| order independence (Step 14): most-specific ×2, equally specific 1, two destinations 1, two sources 1 | 4 | 5 |
| nested CLI regressions (Step 20) ×2 | 1 | 2 |
| **Total** | **19** | **30** |

Treat the collector, not this table, as authoritative. If they disagree the
plan is wrong and must be corrected before proceeding:

```bash
uv run python -m pytest tests/test_gate3_audit.py --collect-only -q | tail -1
uv run python -m pytest tests/test_gate3_audit.py -q
uv run python -m pytest -q
```

The Gate 3 module stands at **228 passed, 1 skipped** and the full public
suite at **1950 passed, 1 skipped** before this task. Require afterwards:

- Gate 3 module: **at least 258 passed**, 1 skipped;
- full public suite: **at least 1980 passed**, 1 skipped.

These are floors, not equalities, because a review correction may add a
regression. A count *below* a floor, or any new skip, is a stop condition.

- [ ] **Step 23: Mutation-prove the pairing rule and the composition direction**

Without committing, apply each mutation, run its command, restore the file,
verify byte-identity with `cmp` and SHA-256, and re-run to GREEN.

1. Replace the inserted clause in `audit_filesystem()` with `if False:`.
   Run the Step 19 command. Expected RED with two `VIOLATION direct write:`
   lines.
2. Make `_paired_rename_directories()` skip its `identity_digest`
   comparison. Run the Step 13 command. Expected RED on `identity-mismatch`.
3. Make `_source_preimage()` return `""` unconditionally. Run the Step 20
   command. Expected RED on `ancestor-then-nested` only — this is what
   distinguishes the corrected composition from Plan Revision 2's.
4. Make `_predict_rename_destination()` return the first candidate instead of
   the longest. Run the Step 14 command. Expected RED on
   `most_specific_mapping[general-first]`.
5. Remove the exact-chain consumption guard so a derived mapping is always
   appended. Run the Step 9 command. Expected RED on
   `compose_oldest_first_over_exact_roots`, which returns `()` instead of
   `(a → c)` — the Plan Revision 3 regression this revision fixes.
6. Make `_source_preimage()` return `None` whenever more than one accumulated
   mapping matches. Run the Step 9 command. Expected RED on
   `compose_through_a_deeper_nested_chain` and on
   `source_preimage_selects_the_unique_most_specific_mapping` — the other
   Plan Revision 3 regression.

- [ ] **Step 24: Run the public acceptance gates**

```bash
git diff --check
tools/run_gitleaks.sh .
uv run python -m tools.public_repo_audit --repo .
uv run python -m tools.public_repo_audit --repo . --history
git status --short
```

Expected: whitespace clean; Gitleaks clean; both audits CLEAN; only
`tools/gate3_audit.py` and `tests/test_gate3_audit.py` modified. Confirm
`ONEOS_VAULT` is still unset and remove `/tmp/gate3_unchanged.py`.

- [ ] **Step 25: Commit the implementation checkpoint**

Commit only those two files, in one sanitized commit recording the RED
evidence, all six mutation proofs, the Step 21 result, and both counts.

- [ ] **Step 26: Obtain an independent scoped review**

Invoke `superpowers:requesting-code-review` over
`acc3f309f04a285fbec46acf0a0cc99d0175e101`..HEAD, carrying only public
requirements and synthetic evidence. Ask for Critical / Important / Minor
findings covering: mapping provenance and the unchanged sanctioning
decision; axis ambiguity and conflict handling; composition in **both**
nesting orders; order-independent most-specific selection; the five pairing
requirements; descendant precedence; the inode-reuse limitation being
neither widened nor worked around; and test gaps.

Verify every finding with `superpowers:receiving-code-review` before acting.
Add a RED regression for each accepted behavioural defect, implement the
smallest fix, re-run Steps 21–24, and request another scoped review. Return
disputed or scope-expanding findings to the owner.

- [ ] **Step 27: Stop for the trusted-local private integration gate**

Report the final HEAD, changed files, RED/GREEN and mutation evidence, the
collector-confirmed counts, Gitleaks and both public audits, the Step 21
result, independent findings by severity, clean worktree, and confirmation
that `ONEOS_VAULT` remained unset.

Then **stop**. Do not push, open a pull request, request CodeRabbit, run the
live Gate 3 trial, begin Gate 1 timing, deploy, or start Phase 2. The owner
runs the trusted-local private integration gate against that exact HEAD —
fresh opaque preimages, 39+ private tests, `check_v2` 0 errors and 0
warnings, policy pass, clean combined repository-plus-vault history audit,
and four byte-identical preservation comparisons. Only after a sanitized
PASS may publication resume, and any later CodeRabbit code change requires a
further private gate on the resulting HEAD.

---

## Tasks 11–12 status and standing constraints

Tasks 1–10 are historical. Their commands, counts and stop conditions record
what was done and are **not** current instructions.

**Baseline for Tasks 11–12:** product-code checkpoint
`69cbff7681c00a9347ec1be22cc40ef72788730e` on branch
`codex/gate3-finding-a-filesystem-evidence-20260829`, whose measured public
state is **261 passed, 1 skipped** in the Gate 3 module (262 collected) and
**1983 passed, 1 skipped** in the full suite. The one skip is the genuine
APFS undecodable-filename case and must remain the only skip on hosts that
support UNIX sockets.

**Design authority:** Revision 5 at
`4a6530168d0299a0ac895f49c873693a75875a12`.

**Standing constraints for both tasks:**

- Modify only `tools/gate3_audit.py` and `tests/test_gate3_audit.py`.
- `ONEOS_VAULT` stays unset **in the operator shell** and **no private
  command runs**. Tests may monkeypatch it to a synthetic `tmp_path` vault —
  the existing suite already does — so the confirmation to report is that the
  shell variable is unset and no vault path was used outside pytest fixtures.
- The trusted-local private integration gate is deferred to the exact new
  final HEAD. **Push, pull-request creation and CodeRabbit stay blocked**
  until the owner runs it and returns a sanitized PASS. Any later CodeRabbit
  code change requires **another** private gate on the resulting HEAD; a
  private gate result binds only to the HEAD it ran against.
- Live Gate 3, Gate 1 timing, deployment and Phase 2 remain blocked.
- Preserve every S7 record-sanctioning predicate and the classifier taxonomy.
- Preserve the accepted inode-reuse limitation. Do not introduce timestamps,
  platform birth time, content reads, a new dependency, or a general
  move whitelist.
- Before executing either task, and again before any later publication:
  fetch origin and re-verify the branch descends from canonical
  `origin/main` at `fecafea674cc254217d24950e716e42f71353fdc`. On any drift,
  **stop with a bounded memo** — never automatically merge, rebase, reset or
  force-push.
- Append new tests; never rewrite a region by slicing between two anchors.
  Run the repository's own shadowing guard after every test-file edit:
  `uv run python -m pytest tests/test_console_invariants.py -k shadow -q`.

---

### Task 11: Extend rename inheritance to every included filesystem kind

**Files:**
- Modify: `tools/gate3_audit.py:1331-1449`
- Test: `tests/test_gate3_audit.py` (append only)

**Interfaces:**
- Consumes: `FilesystemChange`, `FilesystemFingerprint`, `RenameMapping`,
  `ClassifiedPathChange`, `Audit`, `AuditRules`,
  `_predict_rename_destination()`, `compare_filesystem_evidence()`,
  `_is_canonical_quarantine_directory()`.
- Produces: a generalised `_paired_rename_entries()` replacing
  `_paired_rename_directories()`, and a three-phase `audit_filesystem()`.

```text
_paired_rename_entries(
    changes: tuple[FilesystemChange, ...],
    mappings: tuple[RenameMapping, ...],
) -> frozenset[str]
```

`audit_filesystem()` keeps its existing signature exactly. Only its body
changes.

**Normative disposition order (Design Revision 5).** Three phases, in this
order:

1. **Pair non-directory deltas.** Sanctioned pairs are recorded and become
   **neutral** ancestry evidence — contributing *no* `ClassifiedPathChange`.
   Refused ones are violating and become violating classified descendants.
2. **Run directory ancestry** over classified descendants from commit
   evidence, dirty evidence, and phase 1's violating entries.
3. **Pair leftover directory presence deltas**, then apply the exact
   quarantine-directory exception.

Phase 1's neutrality is security-bearing: a sanctioned non-directory pair
must never satisfy the sanctioned-descendant rule for an enclosing directory
whose own `mode` or `identity_digest` check refused it.

- [ ] **Step 1: Write the RED positive-kind tests**

Append to `tests/test_gate3_audit.py`:

```python
def _kind_fp(kind: str, *, mode: int = 0o755, identity: str = "1" * 64,
             target: str | None = None):
    return gate3.FilesystemFingerprint(kind, mode, identity, target)


_PAIRABLE_KINDS = (
    ("symlink", "9" * 64),
    ("fifo", None),
    ("socket", None),
    ("char-device", None),
    ("block-device", None),
    ("other", None),
)


@pytest.mark.parametrize(("kind", "target"), _PAIRABLE_KINDS,
                         ids=[k for k, _ in _PAIRABLE_KINDS])
def test_verified_rename_pairs_every_included_kind(
    tmp_path: Path, kind: str, target: str | None
):
    """A sanctioned rename carries special entries; none may false-violate.

    Device kinds are exercised through synthetic fingerprints only — this
    never creates a device node.
    """
    vault = _audit_vault(tmp_path / "vault", initialize_git=True)
    rules = gate3.AuditRules.load(vault)

    audit = _pair_audit(
        {"synthetic/d/x": _kind_fp(kind, target=target)},
        {"renamed/d/x": _kind_fp(kind, target=target)},
        rules,
        (_m("synthetic", "renamed"),),
    )

    assert audit.sanctioned_writes == ["renamed/d/x", "synthetic/d/x"]
    assert audit.violating_writes == []


def test_regular_files_are_never_pairable_supplemental_evidence(
    tmp_path: Path,
):
    """Regular files stay under Git's rules and never enter the supplement."""
    vault = _audit_vault(tmp_path / "vault", initialize_git=True)
    rules = gate3.AuditRules.load(vault)

    audit = _pair_audit(
        {"synthetic/d/x": _kind_fp("regular")},
        {"renamed/d/x": _kind_fp("regular")},
        rules,
        (_m("synthetic", "renamed"),),
    )

    assert audit.sanctioned_writes == []
```

- [ ] **Step 2: Run them and observe RED**

```bash
uv run python -m pytest tests/test_gate3_audit.py \
  -k 'pairs_every_included_kind or never_pairable_supplemental' -q
uv run python -m pytest tests/test_console_invariants.py -k shadow -q
```

Expected: the six kind cases FAIL — `audit_filesystem` reports both endpoints
violating, because pairing is directory-only. The shadowing guard passes.

- [ ] **Step 3: Write the RED negative and adversarial tests**

```python
_UNPAIRED_KIND_CASES = {
    "no-sanctioned-rename": ("synthetic/d/x", "renamed/d/x",
                             _kind_fp("fifo"), _kind_fp("fifo"), ()),
    "wrong-old-root": ("stranger/d/x", "renamed/d/x",
                       _kind_fp("fifo"), _kind_fp("fifo"),
                       (("synthetic", "renamed"),)),
    "wrong-new-root": ("synthetic/d/x", "stranger/d/x",
                       _kind_fp("fifo"), _kind_fp("fifo"),
                       (("synthetic", "renamed"),)),
    "different-tail": ("synthetic/d/x", "renamed/d/y",
                       _kind_fp("fifo"), _kind_fp("fifo"),
                       (("synthetic", "renamed"),)),
    "prefix-only-root": ("synthetic-x/d/x", "renamed/d/x",
                         _kind_fp("fifo"), _kind_fp("fifo"),
                         (("synthetic", "renamed"),)),
    "kind-change": ("synthetic/d/x", "renamed/d/x",
                    _kind_fp("fifo"), _kind_fp("socket"),
                    (("synthetic", "renamed"),)),
    "mode-change": ("synthetic/d/x", "renamed/d/x",
                    _kind_fp("fifo"), _kind_fp("fifo", mode=0o700),
                    (("synthetic", "renamed"),)),
    "identity-change": ("synthetic/d/x", "renamed/d/x",
                        _kind_fp("fifo"), _kind_fp("fifo", identity="2" * 64),
                        (("synthetic", "renamed"),)),
    "symlink-target-change": ("synthetic/d/x", "renamed/d/x",
                              _kind_fp("symlink", target="9" * 64),
                              _kind_fp("symlink", target="8" * 64),
                              (("synthetic", "renamed"),)),
    # A non-symlink must never carry a target; the same unconditional
    # comparison enforces both halves of that invariant.
    "non-symlink-carries-target": ("synthetic/d/x", "renamed/d/x",
                                   _kind_fp("fifo"),
                                   _kind_fp("fifo", target="9" * 64),
                                   (("synthetic", "renamed"),)),
}


@pytest.mark.parametrize("case", sorted(_UNPAIRED_KIND_CASES))
def test_unpaired_kind_shapes_remain_violations(tmp_path: Path, case: str):
    """Every shape outside the verified mapping stays a direct write."""
    before_path, after_path, before_fp, after_fp, raw = _UNPAIRED_KIND_CASES[case]
    vault = _audit_vault(tmp_path / "vault", initialize_git=True)
    rules = gate3.AuditRules.load(vault)

    audit = _pair_audit(
        {before_path: before_fp}, {after_path: after_fp}, rules,
        tuple(_m(o, n) for o, n in raw),
    )

    assert audit.sanctioned_writes == []
    assert sorted(audit.violating_writes) == sorted({before_path, after_path})


def test_standalone_new_special_entry_is_never_sanctioned(tmp_path: Path):
    """Both endpoints must exist in the delta; a creation has no removal."""
    vault = _audit_vault(tmp_path / "vault", initialize_git=True)
    rules = gate3.AuditRules.load(vault)

    audit = _pair_audit(
        {}, {"renamed/d/x": _kind_fp("fifo")}, rules,
        (_m("synthetic", "renamed"),),
    )

    assert audit.violating_writes == ["renamed/d/x"]
    assert audit.sanctioned_writes == []


@pytest.mark.parametrize("side", ("added", "removed"))
def test_unrelated_special_sibling_is_not_sanctioned(tmp_path: Path, side: str):
    vault = _audit_vault(tmp_path / "vault", initialize_git=True)
    rules = gate3.AuditRules.load(vault)
    before = {"synthetic/d/x": _kind_fp("fifo")}
    after = {"renamed/d/x": _kind_fp("fifo")}
    if side == "added":
        after["renamed/d/extra"] = _kind_fp("fifo")
        expected = "renamed/d/extra"
    else:
        before["synthetic/d/extra"] = _kind_fp("fifo")
        expected = "synthetic/d/extra"

    audit = _pair_audit(before, after, rules, (_m("synthetic", "renamed"),))

    assert audit.violating_writes == [expected]
    assert audit.sanctioned_writes == ["renamed/d/x", "synthetic/d/x"]


@pytest.mark.parametrize("shape", ("one-to-two", "two-to-one"))
def test_ambiguous_special_pairing_fails_closed(tmp_path: Path, shape: str):
    vault = _audit_vault(tmp_path / "vault", initialize_git=True)
    rules = gate3.AuditRules.load(vault)
    if shape == "one-to-two":
        before = {"synthetic/d/x": _kind_fp("fifo")}
        after = {"renamed/d/x": _kind_fp("fifo"), "other/d/x": _kind_fp("fifo")}
        mappings = (_m("synthetic", "renamed"),)
        expected_sanctioned = ["renamed/d/x", "synthetic/d/x"]
    else:
        before = {"synthetic/d/x": _kind_fp("fifo"),
                  "second/d/x": _kind_fp("fifo")}
        after = {"renamed/d/x": _kind_fp("fifo")}
        mappings = (_m("synthetic", "renamed"), _m("second", "renamed"))
        expected_sanctioned = []

    audit = _pair_audit(before, after, rules, mappings)

    assert audit.sanctioned_writes == expected_sanctioned


def test_conflicting_mapping_never_sanctions_a_special_entry(tmp_path: Path):
    vault = _audit_vault(tmp_path / "vault", initialize_git=True)
    rules = gate3.AuditRules.load(vault)

    audit = _pair_audit(
        {"synthetic/d/x": _kind_fp("fifo")},
        {"renamed/d/x": _kind_fp("fifo")},
        rules,
        (_m("synthetic", "renamed"), _m("synthetic", "other")),
    )

    assert audit.sanctioned_writes == []
```

- [ ] **Step 4: Write the RED neutral-ancestry regression**

This is the Critical case an independent review found in the design. It must
exist before the generalisation lands.

```python
@pytest.mark.parametrize("refusal", ("mode", "identity"))
def test_paired_special_entry_never_sanctions_its_enclosing_directory(
    tmp_path: Path, refusal: str
):
    """A paired symlink is neutral for ancestry, never a sanctioning descendant.

    The enclosing directory moved in the same rename but its `mode` changed,
    so its own pairing is refused. If the symlink counted as a sanctioned
    descendant it would satisfy the ancestry rule and the mode change would
    never be reported — a requirement bypass reached through inheritance.
    """
    vault = _audit_vault(tmp_path / "vault", initialize_git=True)
    rules = gate3.AuditRules.load(vault)

    refused = (
        _kind_fp("directory", mode=0o777)
        if refusal == "mode"
        else _kind_fp("directory", identity="2" * 64)
    )
    audit = _pair_audit(
        {
            "synthetic/d": _kind_fp("directory"),
            "synthetic/d/link": _kind_fp("symlink", target="9" * 64),
        },
        {
            "renamed/d": refused,
            "renamed/d/link": _kind_fp("symlink", target="9" * 64),
        },
        rules,
        (_m("synthetic", "renamed"),),
    )

    assert sorted(audit.violating_writes) == ["renamed/d", "synthetic/d"]
    assert audit.sanctioned_writes == ["renamed/d/link", "synthetic/d/link"]


def test_refused_special_pair_still_suppresses_its_ancestor(tmp_path: Path):
    """A violating descendant suppresses, exactly as before.

    This one is GREEN against the current implementation: it is a
    preservation guard proving the three-phase restructure did not change
    existing behaviour, not a RED-first behaviour test.
    """
    vault = _audit_vault(tmp_path / "vault", initialize_git=True)
    rules = gate3.AuditRules.load(vault)

    audit = _pair_audit(
        {},
        {"renamed/d": _kind_fp("directory"),
         "renamed/d/x": _kind_fp("fifo")},
        rules,
        (),
    )

    assert audit.violating_writes == ["renamed/d/x"]
    assert audit.sanctioned_writes == []
```

- [ ] **Step 5: Write the RED end-to-end CLI kind tests**

```python
def _ignored_symlink_files():
    """`.gitignore` pattern without a slash matches at any depth.

    That matters: the ignored symlink must live *inside* the renamed root so
    the transaction actually carries it, and it must stay ignored at its new
    path too.
    """
    files, old, new = _rename_files("entity")
    files[".gitignore"] = "ignored-link\n"
    return files, old, new


@pytest.mark.parametrize(
    "entry", ("tracked-symlink", "ignored-symlink", "fifo", "socket")
)
def test_cli_sanctioned_rename_carries_special_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entry: str
):
    """The gate must pass for every entry the transaction can carry."""
    files, old, new = _ignored_symlink_files()
    vault = git_vault(tmp_path / "vault", files)
    holder = vault / old / "11-library" / "archive"
    holder.mkdir(parents=True, exist_ok=True)
    if entry == "tracked-symlink":
        os.symlink("target", holder / "link")
        _git(vault, "add", "-A")
        _git(vault, "commit", "-q", "-m", "ingest: add redacted receipt")
    elif entry == "ignored-symlink":
        # Inside the moved root. At the vault root it would be untouched by
        # the rename, produce no delta, and prove nothing about pairing.
        os.symlink("target", holder / "ignored-link")
    elif entry == "fifo":
        os.mkfifo(holder / "pipe", 0o600)
    else:
        if not _make_socket(holder / "sock"):
            pytest.skip("host does not safely support UNIX sockets here")
    snapshot = tmp_path / "gate3.json"
    monkeypatch.setenv("ONEOS_VAULT", os.fspath(vault))
    monkeypatch.setenv("GATE3_SNAP", os.fspath(snapshot))
    assert gate3.main(["snapshot"]) == 0

    apply_rename(vault, plan_rename(vault, "entity", old, new), validators=[])

    assert gate3.main(["check"]) == 0


def test_cli_git_visible_untracked_symlink_refuses_the_transaction(
    tmp_path: Path,
):
    """Pins *why* Git-visible untracked entries are outside inheritance.

    They are excluded because the transaction cannot start, not by policy.
    """
    files, old, new = _rename_files("entity")
    vault = git_vault(tmp_path / "vault", files)
    os.symlink("target", vault / old / "11-library" / "stray-link")

    with pytest.raises(RenameError):
        apply_rename(
            vault, plan_rename(vault, "entity", old, new), validators=[]
        )
```

Import `RenameError` from `app.rename` in the test module's existing import
block.

- [ ] **Step 6: Run every Task 11 test and observe RED**

```bash
uv run python -m pytest tests/test_gate3_audit.py \
  -k 'included_kind or never_pairable or unpaired_kind or standalone_new_special or unrelated_special or ambiguous_special or conflicting_mapping_never or paired_special_entry or refused_special_pair or carries_special_entries or git_visible_untracked_symlink' -q
```

Expected: FAIL. Record which cases fail and why — the socket CLI case may
skip on an unsupported host, and that skip must be reported, not hidden.

- [ ] **Step 7: Generalise pairing and install the three phases**

Rename `_paired_rename_directories` to `_paired_rename_entries` and change
its two kind filters so both endpoints must share the *same included* kind
rather than both being directories:

```python
def _paired_rename_entries(
    changes: tuple[FilesystemChange, ...],
    mappings: tuple[RenameMapping, ...],
) -> frozenset[str]:
    """Paths a verified sanctioned rename explains, at both endpoints.

    Covers every included kind, not directories alone: the transaction moves
    the whole verified root topology, and a special entry it carried is not a
    direct write. Identity equality stays necessary and never sufficient —
    every other requirement is checked here independently.
    """
    removed = {
        change.path: change.before
        for change in changes
        if change.kind == "removed"
        and change.before is not None
        and change.before.kind in _FILESYSTEM_KINDS
    }
    added = {
        change.path: change.after
        for change in changes
        if change.kind == "added"
        and change.after is not None
        and change.after.kind in _FILESYSTEM_KINDS
    }
    proposed: dict[str, str] = {}
    for old_path, old_fingerprint in sorted(removed.items()):
        new_path = _predict_rename_destination(old_path, mappings)
        if new_path is None:
            continue
        new_fingerprint = added.get(new_path)
        if new_fingerprint is None:
            continue
        if (
            new_fingerprint.kind != old_fingerprint.kind
            or new_fingerprint.mode != old_fingerprint.mode
            or new_fingerprint.identity_digest != old_fingerprint.identity_digest
            or new_fingerprint.target_digest != old_fingerprint.target_digest
        ):
            continue
        proposed[old_path] = new_path
    claimed: dict[str, list[str]] = {}
    for old_path, new_path in proposed.items():
        claimed.setdefault(new_path, []).append(old_path)
    paired: set[str] = set()
    for new_path, sources in claimed.items():
        if len(sources) != 1:
            continue
        paired.update({sources[0], new_path})
    return frozenset(paired)
```

`target_digest` is compared unconditionally: it is `None` for every non-symlink
kind, so one comparison covers both the symlink requirement and the
"non-symlink must not carry a target" invariant.

Then restructure `audit_filesystem`'s first loop into phase 1:

```python
    changes = compare_filesystem_evidence(before, after)
    paired = _paired_rename_entries(changes, rename_mappings)
    directory_changes: list[FilesystemChange] = []
    candidates: list[ClassifiedPathChange] = list(classified_paths)
    for change in changes:
        directory_presence = (
            change.kind in {"added", "removed"}
            and (change.before or change.after).kind == "directory"
            and (change.before is None or change.after is None)
        )
        if directory_presence:
            directory_changes.append(change)
            continue
        if change.path in paired:
            # Phase 1: sanctioned, and deliberately NOT appended to
            # `candidates`. A paired special entry is neutral ancestry
            # evidence; counting it as a sanctioned descendant would satisfy
            # the ancestry rule for an enclosing directory whose own mode or
            # identity check refused it.
            result.sanctioned_writes.append(change.path)
            continue
        result.violating_writes.append(change.path)
        candidates.append(
            ClassifiedPathChange(change.path, change.kind, "violating")
        )
```

Phases 2 and 3 are the existing directory loop, unchanged, still consulting
`paired` for leftover directory presence deltas before the quarantine
exception. Update the docstring, which currently asserts the superseded
blanket rule.

- [ ] **Step 8: Run Task 11 GREEN and the shadowing guard**

```bash
uv run python -m pytest tests/test_gate3_audit.py \
  -k 'included_kind or never_pairable or unpaired_kind or standalone_new_special or unrelated_special or ambiguous_special or conflicting_mapping_never or paired_special_entry or refused_special_pair or carries_special_entries or git_visible_untracked_symlink' -q
uv run python -m pytest tests/test_gate3_audit.py -q
uv run python -m pytest tests/test_console_invariants.py -k shadow -q
```

Expected: all selected pass; the full Gate 3 module passes; no shadowed
definition.

- [ ] **Step 9: Mutation-prove every security-bearing condition**

Seven mutations. For each: apply without committing, run the named command,
restore from a preimage, verify with `cmp` **and** SHA-256 that the file is
byte-identical, then re-run to GREEN.

| # | Mutation | Command | Expected RED |
|---|---|---|---|
| 1 | drop the `kind` equality in `_paired_rename_entries` | `-k unpaired_kind` | `kind-change` |
| 2 | drop the `mode` equality | `-k unpaired_kind` | `mode-change` |
| 3 | drop the `identity_digest` equality | `-k unpaired_kind` | `identity-change` |
| 4 | drop the `target_digest` equality | `-k unpaired_kind` | `symlink-target-change` |
| 5 | append paired non-directories to `candidates` as `"sanctioned"` | `-k paired_special_entry` | the neutral-ancestry regression |
| 6 | remove the `len(sources) != 1` claim check | `-k ambiguous_special` | `two-to-one` |
| 7 | drop the `kind in _FILESYSTEM_KINDS` filters so regular files enter the supplement | `-k never_pairable` | the regular-file exclusion |

- [ ] **Step 10: Prove the S7 predicates and taxonomy did not move**

```bash
uv run python -m pytest tests/test_gate3_audit.py \
  -k 'consumed or pending or receipt or unrelated or quarantine' -q
```

Then run the full-file AST comparison against
`f8003a500881a9bb612a6c18999590b6be17ead4`, requiring that only
`_paired_rename_directories` disappears, only `_paired_rename_entries`
appears, and only `audit_filesystem` differs among retained definitions.

- [ ] **Step 11: Checkpoint gate and commit**

```bash
uv run python -m pytest tests/test_gate3_audit.py --collect-only -q | tail -1
uv run python -m pytest tests/test_gate3_audit.py -q
uv run python -m pytest -q
git diff --check
tools/run_gitleaks.sh .
uv run python -m tools.public_repo_audit --repo .
uv run python -m tools.public_repo_audit --repo . --history
git status --short
```

Require the floors in "Collected-case forecast" below, both audits CLEAN,
Gitleaks clean, only the two approved files modified, and `ONEOS_VAULT`
still unset. Commit those two files in one sanitized checkpoint recording the
RED evidence, all seven mutation results, and both counts.

---

### Task 12: One immutable per-record rename analysis

**Files:**
- Modify: `tools/gate3_audit.py:1233-1290,1451-1600,1704-1770`
- Test: `tests/test_gate3_audit.py` (append only)

**Interfaces:**
- Consumes: `CommitRecord`, `AuditRules`, `Audit`, `CommitAuditResult`,
  `RenameMapping`, `_parent_tree()`, `_compose_rename_mappings()`,
  `build_rename_plan()`, `AXES`, `RenameError`.
- Produces:

```text
@dataclass(frozen=True)
class RenameAnalysis:
    sanctioned: bool
    matched_axes: tuple[str, ...]
    mappings: tuple[RenameMapping, ...]

_axis_envelope_and_moves(
    tree: Path, tracked: tuple[str, ...], axis: str, old: str, new: str,
    *, parent_oid: str,
) -> tuple[frozenset[tuple[str, str]], tuple[RenameMapping, ...]]
_analyze_rename(record: CommitRecord, vault: Path) -> RenameAnalysis
```

`_sanctioned_rename()` and `_commit_is_sanctioned()` gain a keyword-only
`analysis: RenameAnalysis | None = None`. `audit_commits()` gains a
keyword-only `analyses: dict[str, RenameAnalysis] | None = None` keyed by
`record.oid`, populated by `_audit_commit_history()` for the single record it
passes. That dictionary is built per record, consumed immediately, and
discarded — it is **not** a cross-record cache.

`_matching_rename_axes()` and `_verified_rename_mappings()` are removed;
`_rename_move_pairs()` is absorbed into `_axis_envelope_and_moves()`.

- [ ] **Step 1: Write the RED call-count tests**

```python
def _instrumented_check(vault, snapshot, monkeypatch):
    counts = {"parent_tree": 0, "plan": 0, "analysis": 0, "sanctioned": 0}
    for name, key in (
        ("_parent_tree", "parent_tree"),
        ("build_rename_plan", "plan"),
        ("_analyze_rename", "analysis"),
        ("_sanctioned_rename", "sanctioned"),
    ):
        real = getattr(gate3, name, None)
        if real is None:
            continue

        def wrapper(*a, _real=real, _key=key, **k):
            counts[_key] += 1
            return _real(*a, **k)

        monkeypatch.setattr(gate3, name, wrapper)
    monkeypatch.setenv("ONEOS_VAULT", os.fspath(vault))
    monkeypatch.setenv("GATE3_SNAP", os.fspath(snapshot))
    assert gate3.main(["check"]) in (0, 1)
    return counts


def test_rename_record_performs_one_analysis_and_bounded_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """One analysis per record; no repeated sanction verification."""
    files, old, new = _rename_files("entity")
    vault = git_vault(tmp_path / "vault", files)
    snapshot = tmp_path / "gate3.json"
    monkeypatch.setenv("ONEOS_VAULT", os.fspath(vault))
    monkeypatch.setenv("GATE3_SNAP", os.fspath(snapshot))
    assert gate3.main(["snapshot"]) == 0
    apply_rename(vault, plan_rename(vault, "entity", old, new), validators=[])

    counts = _instrumented_check(vault, snapshot, monkeypatch)

    assert counts["parent_tree"] <= 2
    assert counts["plan"] <= len(gate3.AXES)
    assert counts["analysis"] == 1
    assert counts["sanctioned"] <= 1


def test_non_rename_record_builds_no_rename_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    files, old, _new = _rename_files("entity")
    vault = git_vault(tmp_path / "vault", files)
    snapshot = tmp_path / "gate3.json"
    monkeypatch.setenv("ONEOS_VAULT", os.fspath(vault))
    monkeypatch.setenv("GATE3_SNAP", os.fspath(snapshot))
    assert gate3.main(["snapshot"]) == 0
    receipt = f"{old}/00-inbox/active/new receipt.md"
    (vault / receipt).write_text("redacted\n")
    _git(vault, "add", receipt)
    _git(vault, "commit", "-q", "-m", "ingest: add redacted receipt")

    counts = _instrumented_check(vault, snapshot, monkeypatch)

    assert counts["plan"] == 0
    assert counts["parent_tree"] <= 1


def test_two_rename_records_each_get_their_own_analysis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Per-record, not cached: two rename commits mean two analyses.

    A cross-record cache would satisfy every other call-count assertion while
    violating the design's no-shared-state rule, so it needs its own test.
    """
    files, old, new = _rename_files("entity")
    vault = git_vault(tmp_path / "vault", files)
    snapshot = tmp_path / "gate3.json"
    monkeypatch.setenv("ONEOS_VAULT", os.fspath(vault))
    monkeypatch.setenv("GATE3_SNAP", os.fspath(snapshot))
    assert gate3.main(["snapshot"]) == 0
    apply_rename(vault, plan_rename(vault, "entity", old, new), validators=[])
    apply_rename(
        vault, plan_rename(vault, "entity", new, "thirdentity"), validators=[]
    )

    counts = _instrumented_check(vault, snapshot, monkeypatch)

    assert counts["analysis"] == 2
```

The plan-build assertion compares against `len(gate3.AXES)`, never a literal.
The current implementation measures **4 checkouts and 2 sanction
verifications on every axis**, and **8 to 16 plan builds** depending on the
matching axis's position in `sorted(AXES)`.

The non-rename bound uses an `ingest:` commit deliberately. A
**receipt-bearing** record — an outbox approval or a registry delete —
incurs a second checkout of the same object id inside the dirty audit's
commit-relative rules loader, which is out of scope for this task. An
`ingest:` commit is not receipt-bearing, so it never reaches that loader and
one checkout is the true total. The counter is global; the bound comes from
the fixture's shape, not from scoping the count.

- [ ] **Step 2: Run them and observe RED**

```bash
uv run python -m pytest tests/test_gate3_audit.py \
  -k 'one_analysis_and_bounded_work or builds_no_rename_plan or two_rename_records' -q
```

Expected: `test_rename_record_performs_one_analysis_and_bounded_work` FAILS —
`parent_tree` is 4, `plan` is 8–16, `analysis` is 0 (`_analyze_rename` does
not exist), and `sanctioned` is 2.

`test_non_rename_record_builds_no_rename_plan` **already passes** against the
current implementation, measured `{parent_tree: 1, plan: 0, sanctioned: 0}`.
It is a **preservation guard**, not a RED-first behaviour test: record that
explicitly rather than reporting it as RED. Its bound holds because an
`ingest:` commit is not receipt-bearing, so it never reaches the second
same-object checkout inside the dirty audit's commit-relative rules loader —
that, not any scoping of the counter, is the mechanism, and the counter is
global.

`test_two_rename_records_each_get_their_own_analysis` FAILS with
`analysis == 0`; it prevents a later implementation from satisfying the
single-record bounds with shared or cached state.

- [ ] **Step 3: Run the untracked development differential oracle**

Before changing product code, run a disposable script outside the repository.
It must load
`acc3f309f04a285fbec46acf0a0cc99d0175e101:tools/gate3_audit.py` with
`git show` using `check=True`, load it under a unique module name, and
compare its completed `_sanctioned_rename` outcomes with HEAD over every
axis and these literal variants:

```python
_EXPECTED_SANCTIONING = {
    "sanctioned": True,
    "duplicate-change": False,
    "wrong-parent": False,
    "malformed-envelope": False,
    "non-rename-message": False,
}
```

The disposable oracle lives under `/private/tmp`, is never staged, and is
removed after the post-change comparison. If the approved object cannot be
read, the development oracle **fails**; it never skips. Do not include
multi-axis ambiguity or injected planner exceptions in the differential
comparison because the historical function cannot complete those new
all-axis observations. Record the before-change equality now and repeat it
after Step 7.

- [ ] **Step 4: Write the permanent self-contained RED corpus**

Append tests whose expected results are literal and independent of repository
history. The multi-axis body below retargets the existing
`test_ambiguous_axis_match_contributes_no_mapping` regression rather than
adding duplicate coverage:

```python
def _rename_record(tmp_path: Path, axis: str = "entity"):
    files, old, new = _rename_files(axis)
    vault = git_vault(tmp_path / f"vault-{axis}", files)
    head = _git(vault, "rev-parse", "HEAD").strip()
    apply_rename(vault, plan_rename(vault, axis, old, new), validators=[])
    (record,) = gate3.collect_commit_records(vault, head)
    return vault, record


@pytest.mark.parametrize("axis", sorted(("entity", "product", "member",
                                         "project", "workspace")))
def test_rename_analysis_preserves_literal_sanctioning_results(
    tmp_path: Path, axis: str
):
    vault, record = _rename_record(tmp_path, axis)
    variants = {
        "sanctioned": (record, True),
        "duplicate-change": (
            dataclasses.replace(
                record, changes=record.changes + (record.changes[0],)
            ),
            False,
        ),
        "wrong-parent": (
            dataclasses.replace(record, parents=("e" * 40,)),
            False,
        ),
        "malformed-envelope": (
            dataclasses.replace(record, changes=()),
            False,
        ),
        "non-rename-message": (
            dataclasses.replace(
                record, message="ingest: add redacted receipt"
            ),
            False,
        ),
    }

    for name, (candidate, expected) in variants.items():
        analysis = gate3._analyze_rename(candidate, vault)
        assert analysis.sanctioned is expected, name
        if not expected:
            assert analysis.matched_axes == (), name
            assert analysis.mappings == (), name


def test_expected_later_axis_failures_preserve_an_earlier_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    vault, record = _rename_record(tmp_path)
    real = gate3._axis_envelope_and_moves

    def expected_failure_after_match(tree, tracked, axis, old, new, *,
                                     parent_oid):
        if axis == "entity":
            return real(
                tree, tracked, axis, old, new, parent_oid=parent_oid
            )
        raise OSError("synthetic expected axis-local failure")

    monkeypatch.setattr(
        gate3, "_axis_envelope_and_moves", expected_failure_after_match
    )

    analysis = gate3._analyze_rename(record, vault)

    assert analysis.sanctioned is True
    assert analysis.matched_axes == ("entity",)
    assert analysis.mappings


def test_ambiguous_axis_match_contributes_no_mapping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    vault, record = _rename_record(tmp_path)
    real = gate3._axis_envelope_and_moves

    def every_axis_matches(tree, tracked, axis, old, new, *, parent_oid):
        envelope, _ = real(
            tree, tracked, "entity", old, new, parent_oid=parent_oid
        )
        return envelope, (gate3.RenameMapping(old, f"{new}-{axis}"),)

    monkeypatch.setattr(gate3, "_axis_envelope_and_moves", every_axis_matches)

    analysis = gate3._analyze_rename(record, vault)

    assert analysis.sanctioned is True
    assert len(analysis.matched_axes) == len(gate3.AXES)
    assert analysis.mappings == ()


def test_unexpected_later_axis_error_fails_check_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    files, old, new = _rename_files("entity")
    vault = git_vault(tmp_path / "vault", files)
    snapshot = tmp_path / "gate3.json"
    monkeypatch.setenv("ONEOS_VAULT", os.fspath(vault))
    monkeypatch.setenv("GATE3_SNAP", os.fspath(snapshot))
    assert gate3.main(["snapshot"]) == 0
    apply_rename(vault, plan_rename(vault, "entity", old, new), validators=[])
    real = gate3._axis_envelope_and_moves

    def unexpected_failure_after_match(tree, tracked, axis, old, new, *,
                                       parent_oid):
        if axis == "entity":
            return real(
                tree, tracked, axis, old, new, parent_oid=parent_oid
            )
        raise TypeError("synthetic unexpected axis failure")

    monkeypatch.setattr(
        gate3, "_axis_envelope_and_moves", unexpected_failure_after_match
    )

    assert gate3.main(["check"]) == 2
    captured = capsys.readouterr()
    assert "GATE 3 ERROR:" in captured.err
    assert "GATE 3: PASS" not in captured.out
```

Import `dataclasses` if it is not already present. These tests permanently
pin the five-axis result corpus, expected later-axis continuation,
multi-axis ambiguity, and the controlled boundary for an unexpected later
error. They contain no `git show`, historical SHA, dynamic baseline module,
or history-dependent skip.

Run:

```bash
uv run python -m pytest tests/test_gate3_audit.py \
  -k 'literal_sanctioning_results or expected_later_axis or ambiguous_axis_match or unexpected_later_axis' -q
uv run python -m pytest tests/test_console_invariants.py -k shadow -q
```

Expected: RED because `RenameAnalysis`, `_analyze_rename`, and
`_axis_envelope_and_moves` do not exist. The unexpected-error case may fail
earlier for that same missing interface, but after the interfaces exist it
must reach the real `main(["check"])` boundary and return 2.

- [ ] **Step 5: Implement the analysis**

```python
@dataclass(frozen=True)
class RenameAnalysis:
    """One record's rename evidence, derived once and never recomputed."""

    sanctioned: bool
    matched_axes: tuple[str, ...]
    mappings: tuple[RenameMapping, ...]


def _axis_envelope_and_moves(
    tree: Path, tracked: tuple[str, ...], axis: str, old: str, new: str,
    *, parent_oid: str,
) -> tuple[frozenset[tuple[str, str]], tuple[RenameMapping, ...]]:
    """Build one axis's plan once and derive both products from it."""
    plan = build_rename_plan(tree, axis, old, new, planned_head=parent_oid)
    planned_root = plan.vault
    moves = tuple(
        (source.relative_to(planned_root), destination.relative_to(planned_root))
        for source, destination in plan.moves
    )
    envelope: set[tuple[str, str]] = set()
    for tracked_path in tracked:
        candidate = Path(tracked_path)
        for source, destination in moves:
            tail = _relative_to(candidate, source)
            if tail is not None:
                envelope.add(("D", candidate.as_posix()))
                envelope.add(("A", (destination / tail).as_posix()))
                break
    for edited in plan.edits:
        relative = edited.relative_to(planned_root)
        # An entity rename rewrites every text file carrying the old slug,
        # including files *inside* the moved directory. Those already appear
        # as the D/A pair above; adding an ("M", <old path>) as well makes
        # the envelope disagree with the commit and every legitimate rename
        # stops being sanctioned. `.relative_to` is kept rather than
        # `_relative_to` so an unexpected path fails closed.
        if any(_relative_to(relative, source) is not None for source, _ in moves):
            continue
        envelope.add(("M", relative.as_posix()))
    mappings = tuple(
        RenameMapping(source.as_posix(), destination.as_posix())
        for source, destination in moves
    )
    return frozenset(envelope), mappings
```

The envelope body must reproduce `_rename_envelope`'s exactly — including
the edits filter and `.relative_to`, both of which are load-bearing. The
untracked development oracle and permanent literal corpus prove the result.
Before writing it, diff the two bodies line by line and confirm no guard was
dropped.

```python
def _analyze_rename(record: CommitRecord, vault: Path) -> RenameAnalysis:
    """Derive this record's rename evidence with one checkout, once.

    The sanctioning decision keeps its exact previous *result*, including the
    duplicate-change guard: `sanctioned` is true when any axis matches, which
    is what first-match acceptance computed.

    It does **not** keep the historical physical early return. Design Revision
    5 resolves that contradiction: every axis runs exactly once, while the
    sanctioning *result* remains `True` when any axis exactly matches.
    Expected axis-local planning failures contribute no match and evaluation
    continues. An unexpected failure is not caught here; it reaches the
    controlled Gate 3 command boundary and fails the command closed rather
    than returning partial evidence.

    Ambiguity applies only to the mappings: more than one matching axis
    yields none.
    """
    empty = RenameAnalysis(sanctioned=False, matched_axes=(), mappings=())
    match = _RENAME_MESSAGE.fullmatch(record.message)
    if match is None or len(record.parents) != 1:
        return empty
    actual = frozenset((change.status, change.path) for change in record.changes)
    if len(actual) != len(record.changes) or not actual:
        return empty
    old, new = match.groups()
    matched: list[tuple[str, tuple[RenameMapping, ...]]] = []
    temporary: tempfile.TemporaryDirectory[str] | None = None
    try:
        temporary, tree, tracked = _parent_tree(vault, record.parents[0])
        for axis in sorted(AXES):
            try:
                envelope, mappings = _axis_envelope_and_moves(
                    tree, tracked, axis, old, new, parent_oid=record.parents[0]
                )
            except (OSError, RenameError, UnicodeError, sqlite3.Error):
                continue
            if envelope and actual == envelope:
                matched.append((axis, mappings))
    except (OSError, subprocess.CalledProcessError, ValueError):
        return empty
    finally:
        if temporary is not None:
            temporary.cleanup()
    return RenameAnalysis(
        sanctioned=bool(matched),
        matched_axes=tuple(axis for axis, _ in matched),
        mappings=matched[0][1] if len(matched) == 1 else (),
    )
```

- [ ] **Step 6: Thread the analysis explicitly**

`_sanctioned_rename(record, vault, *, analysis=None)` returns
`(analysis if analysis is not None else _analyze_rename(record, vault)).sanctioned`.
`_commit_is_sanctioned` forwards its `analysis`. `audit_commits` accepts
`analyses` and forwards `analyses.get(record.oid)`. `_audit_commit_history`
computes `analysis = _analyze_rename(record, vault)` once per record, passes
`{record.oid: analysis}` to `audit_commits`, and appends
`analysis.mappings` when `audited.sanctioned_commits` is non-empty. Delete `_rename_envelope`, `_matching_rename_axes`,
`_verified_rename_mappings` and `_rename_move_pairs`. Four existing tests
name them and must be rewritten, not deleted — each carries coverage the
design still requires:

1. `test_offline_rename_envelope_uses_explicit_parent_oid_without_git_repo`
   (`tests/test_gate3_audit.py:685`) — retarget to
   `_axis_envelope_and_moves`, asserting the returned envelope equals the
   commit's change set. It is the only offline explicit-parent-OID
   regression.
2. `test_unsanctioned_rename_commit_contributes_no_mapping`
   (`:3306`) — retarget to `_analyze_rename`, keeping its assertion that a
   duplicated change entry yields `sanctioned is False` **and** now also
   `mappings == ()`. The design requires this sanctioning regression be
   retained unchanged in substance.
3. `test_ambiguous_axis_match_contributes_no_mapping` (`:3335`) — retarget
   to `_analyze_rename` by patching `_axis_envelope_and_moves` so two axes
   match, asserting `len(matched_axes) > 1` and `mappings == ()`.
4. `test_rename_mappings_compose_*` and the pairing tests are untouched —
   they consume `RenameMapping`, not the deleted helpers.

- [ ] **Step 7: Run GREEN**

```bash
uv run python -m pytest tests/test_gate3_audit.py \
  -k 'one_analysis_and_bounded_work or builds_no_rename_plan or two_rename_records or literal_sanctioning_results or expected_later_axis or ambiguous_axis_match or unexpected_later_axis' -q
uv run python -m pytest tests/test_gate3_audit.py -q
uv run python -m pytest tests/test_console_invariants.py -k shadow -q
```

- [ ] **Step 8: Record the permitted and frozen definitions**

**May change:** `_rename_envelope` (**deleted** — absorbed into
`_axis_envelope_and_moves`, which is its only remaining caller's
replacement), `_rename_move_pairs` (deleted, absorbed),
`_matching_rename_axes` (deleted), `_verified_rename_mappings` (deleted),
`_sanctioned_rename`,
`_commit_is_sanctioned`, `audit_commits`, `_audit_commit_history`,
`cmd_check`.

**Must remain AST-equivalent to the Task 11 checkpoint** recorded at Task 11
Step 11 — not to `f8003a5`, which predates Task 11 and where
`_paired_rename_entries` does not yet exist and `audit_filesystem` still
carries the superseded body: `_sanctioned_outbox`,
`_sanctioned_registry`, `_sanctioned_ingest`, `_load_consumed_record`,
`_receipt_authorizations`, `_sanctioned_consumed_paths`,
`_new_proposal_is_sanctioned`, `_authorization_matches`, `audit_dirty`,
`_commit_relative_rules`, `_is_canonical_quarantine_directory`,
`_filesystem_kind`, `_filesystem_identity_digest`,
`_filesystem_fingerprint`, `collect_filesystem_fingerprints`,
`_walk_directory`, `compare_filesystem_evidence`,
`_compose_rename_mappings`, `_source_preimage`,
`_predict_rename_destination`, `_paired_rename_entries`.

Assert this with an untracked AST script outside the repository, as Task 10
Step 21 did. Any definition changing outside the permitted list is a stop
condition.

- [ ] **Step 9: Mutation-prove the consolidation**

| # | Mutation | Command | Expected RED |
|---|---|---|---|
| 1 | make `_audit_commit_history` call `_sanctioned_rename(record, vault)` again before appending `analysis.mappings` | `-k one_analysis_and_bounded_work` | `sanctioned <= 1` |
| 2 | make `_sanctioned_rename` ignore its `analysis` argument and always recompute | `-k one_analysis_and_bounded_work` | `parent_tree <= 2` |
| 3 | call `build_rename_plan` a second time for the move pairs | `-k one_analysis_and_bounded_work` | `plan <= len(AXES)` |
| 4 | open a second `_parent_tree` inside `_analyze_rename` | `-k one_analysis_and_bounded_work` | `parent_tree <= 2` |
| 5 | drop the duplicate-change guard from `_analyze_rename` | `-k literal_sanctioning_results` | `duplicate-change` |
| 6 | make `sanctioned` require exactly one matched axis | `-k ambiguous_axis_match` | the multi-axis case |
| 7 | build a plan for a non-rename record | `-k builds_no_rename_plan` | `plan == 0` |

Restore byte-identically after each, verified by `cmp` and SHA-256.

- [ ] **Step 10: Checkpoint gate and commit**

Same gate as Task 11 Step 11, against the floors below. Commit only the two
approved files.

---

### Collected-case forecast and suite floors

Baseline at `69cbff7`: Gate 3 module **262 collected, 261 passed, 1 skipped**;
full suite **1983 passed, 1 skipped**.

| Task | Group | Functions | Collected cases |
|---|---|---|---|
| 11 | positive kinds (Step 1) ×6 + regular-file exclusion | 2 | 7 |
| 11 | unpaired kind shapes (Step 3) ×10 | 1 | 10 |
| 11 | standalone, conflicting mapping | 2 | 2 |
| 11 | unrelated special sibling ×2 | 1 | 2 |
| 11 | ambiguous special pairing ×2 | 1 | 2 |
| 11 | neutral ancestry ×2 + refused-pair suppression (Step 4) | 2 | 3 |
| 11 | CLI carried entries ×4 + refusal (Step 5) | 2 | 5 |
| **11 total** | | **11** | **31** |
| 12 | call-count tests, incl. two-record analysis (Step 1) | 3 | 3 |
| 12 | literal sanctioning corpus ×5 axes (Step 4) | 1 | 5 |
| 12 | expected and unexpected later-axis failures (Step 4) | 2 | 2 |
| **12 new** | | **6** | **10** |
| 12 | four retargeted tests (Step 6) — rewritten, not added | 0 | 0 |
| **Both** | | **17** | **41** |

Task 12 Step 6 **retargets** four existing tests rather than deleting them,
so it adds no net retirement: the offline-envelope regression, the
duplicate-change sanctioning regression, and the axis-ambiguity regression
all survive under new names against `_analyze_rename` and
`_axis_envelope_and_moves`. The forecast therefore assumes **no** net
removals. **The collector is authoritative.** Run `pytest --collect-only -q`
at each checkpoint and reconcile against this table; if they disagree, the
plan is wrong and must be corrected before proceeding.

Floors, from the measured baseline of 262 collected / 261 passed / 1 skipped
and 1983 passed / 1 skipped:

- after Task 11: Gate 3 module **at least 292 passed**, 1 skipped; full suite
  **at least 2014 passed**, 1 skipped;
- after Task 12: Gate 3 module **at least 302 passed**, 1 skipped; full suite
  **at least 2024 passed**, 1 skipped.
- after the accepted Task 13 review corrections: Gate 3 module **at least 305
  passed**, 1 skipped; full suite **at least 2027 passed**, 1 skipped.

A socket case may skip where the host cannot safely bind one; any such skip
is reported, not hidden, and the floor is reduced by exactly the number of
reported socket skips.

A count below a floor, or any new skip on a host that supports UNIX sockets,
is a stop condition. Socket cases skip only where the host cannot safely bind
one, and any such skip must be reported.

---

### Task 13: Final verification, review, and stop

- [ ] **Step 1: Re-run the complete acceptance set**

Both mutation matrices, the focused suites, the Gate 3 module, the full
public suite, `git diff --check`, Gitleaks, both public audits, the AST
comparison, the shadowing guard, and the two Task 13 review mutations. The
first review mutation restores a paired non-directory's sanctioned Git
classification as an ancestry candidate; its tracked-special regression must
turn RED. The second treats later-axis `ValueError` and
`CalledProcessError` as expected; both parameterized cases must turn RED.

The final AST comparison against the Task 11 checkpoint permits
`audit_filesystem` in addition to Task 12's four intended retained-definition
changes. `_paired_rename_entries` itself and every S7 sanction predicate must
remain unchanged.

- [ ] **Step 2: Obtain an independent scoped review**

Invoke `superpowers:requesting-code-review` over
`99a0ff522703d0ec281d2876b49d8ca7cc7d535a`..HEAD with only public
requirements and synthetic evidence. Ask for Critical / Important / Minor
findings covering: kind generalisation without requirement relaxation;
three-phase order and neutral ancestry; target-digest handling; unique
order-independent pairing; analysis immutability and absence of caching;
call-count correctness; differential-oracle adequacy; unchanged S7
predicates; and test gaps.

Verify each finding with `superpowers:receiving-code-review`. Add a RED
regression for every accepted behavioural defect, implement the smallest fix,
re-run Steps 1–2, and request another review. **Return any design-changing or
scope-expanding finding to the owner rather than improvising.**

- [ ] **Step 3: Stop for the trusted-local private gate**

Report the final HEAD, ordered new commits, changed files, RED/GREEN
evidence, collector-confirmed counts and the single expected skip, both
mutation matrices, the AST result, Gitleaks and both public audits,
independent findings by severity, clean worktree, and confirmation that
`ONEOS_VAULT` remained unset.

Then **stop**. Do not push, open a pull request, request CodeRabbit, run the
live Gate 3 trial, begin Gate 1 timing, deploy, or start Phase 2. The owner
runs the trusted-local private integration gate against that exact HEAD —
fresh opaque preimages, 39+ private tests, `check_v2` 0 errors and 0
warnings, policy pass, clean combined repository-plus-vault history audit,
and four byte-identical preservation comparisons. Only after a sanitized
PASS may publication resume, and any later CodeRabbit code change requires a
further private gate on the resulting HEAD.
