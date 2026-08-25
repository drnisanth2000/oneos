# S7 CodeRabbit Fix Report

## Status and scope

Implemented only the independently accepted CodeRabbit corrections for PR #16
from starting HEAD `64b9324750b24a49e266f9ce29014cfd4eb5e835`.

Commit subject: `fix: close S7 CodeRabbit findings`
Commit SHA: `ee491acc6bec7882eb406cd701a9518de3165007`

No Grey Matter access, dependency/configuration/screen changes, full suite,
mutation campaign, private gate, push, pull-request reply, merge, ref cleanup,
or worktree cleanup was performed.

## Finding 1 — stale rename plan

CodeRabbit comment: `3855691498` (MAJOR).

### RED evidence

Added
`tests/test_rename.py::test_stale_rename_plan_refuses_before_mutation_and_preserves_newer_commit`.
The test creates a real plan at commit A, commits a relevant edit as B while
leaving the worktree clean, applies the stale plan, and captures the exact
HEAD, index, cached diff, worktree diff, status, and every non-Git vault
object. It also records every call through rename's Git boundary.

Against the starting implementation:

```text
FAILED tests/test_rename.py::test_stale_rename_plan_refuses_before_mutation_and_preserves_newer_commit
E Failed: DID NOT RAISE <class 'app.rename.RenameError'>
1 failed in 3.68s
```

This is the intended failure: the stale plan committed instead of refusing.

### Implementation

- Added required `RenamePlan.planned_head`; callers cannot construct a plan
  without explicitly supplying the Git state whose bytes were reviewed.
- `plan_rename()` captures exact `HEAD` before computing plan edits.
- Under `action_lock`, `apply_rename()` performs its existing clean-worktree
  check, then compares current `HEAD` with `planned_head` before entering the
  mutation/rollback block or invoking a validator.
- A mismatch raises `RenameError` and performs no write, `git mv`, reset,
  clean, or commit. The regression observes only `git status --short` and
  `git rev-parse HEAD`, and proves commit B and its complete tree remain
  byte-identical.
- Updated the two legitimate post-commit OID-failure test injections so they
  trigger only after the rename commit, not on the new planning/pre-apply HEAD
  reads. Their prior behavioral assertions are unchanged.

Focused GREEN:

```text
.                                                                        [100%]
1 passed in 0.39s
```

## Finding 2 — blocked listing notice

CodeRabbit comment: `3853975133` (P2).

### RED evidence

Added the end-to-end route regression
`tests/test_console_routes.py::test_blocked_outbox_notice_uses_unreadable_row_not_earlier_receipt_error`.
Its real Git-backed fixture contains a lexically earlier malformed matching
receipt, a valid sibling, and a later genuinely unreadable proposal. It
requires the first/listing-level alert to be `E-UNREADABLE`, exactly one
per-ID `E-RECEIPT` card, no action controls or fingerprint, and no unreadable
filename or raw-content marker in rendered HTML.

Against the starting implementation:

```text
FAILED tests/test_console_routes.py::test_blocked_outbox_notice_uses_unreadable_row_not_earlier_receipt_error
E AssertionError: the listing-level notice did not describe the condition that blocked it
E assert 'E-RECEIPT' == 'E-UNREADABLE'
1 failed in 3.83s
```

### Implementation

`app.main._outbox_rows()` now selects `blocked_notice` only from a row whose
raw error is `UnreadableProposalRecord`. Unrelated proposal-less rows retain
their own per-ID errors but cannot masquerade as the listing-wide blocker.

Focused GREEN:

```text
.                                                                        [100%]
1 passed in 0.23s
```

## Verified test-lint corrections

The task contract supplied no separate comment IDs for these two latest-review
items.

- Changed the class-level `_BrowserDom._VOID` tag collection to `frozenset`.
- Bound each route-totality iteration's `injected` list into `_raise` through
  the `__injected=injected` default argument, preventing late binding while
  preserving the existing positive-entry assertion.

## Verification

Focused RED/GREEN coverage across both findings and both lint corrections:

```text
......                                                                   [100%]
6 passed in 1.05s
```

Requested broader checks:

```text
uv run pytest tests/test_rename.py -q
....................                                                     [100%]
20 passed in 3.16s

uv run pytest tests/test_console_projection.py -q
.................................................                        [100%]
49 passed in 4.56s

uv run pytest tests/test_console_routes.py -q -k 'outbox or route_totality_from_declared_catches or registry_tamper_preview_swap'
.....................................................                    [100%]
53 passed, 143 deselected in 6.21s

uv run pytest tests/test_console_invariants.py -q
.................................................................        [100%]
65 passed in 0.77s
```

The first complete rename run exposed two fixture injections that matched the
new pre-commit HEAD reads; after restricting them to the already-committed
state, both focused tests passed (`2 passed in 0.34s`) and the complete rename
file produced the 20-pass result above.

`git diff --check` is included in the final pre-commit verification. The full
suite, mutation campaign, and private gates are intentionally left to the
controller.

Post-commit verification reran the same requested scope on the committed tree:

```text
uv run pytest tests/test_rename.py tests/test_console_projection.py tests/test_console_invariants.py -q
........................................................................ [ 53%]
..............................................................           [100%]
134 passed in 7.78s

uv run pytest tests/test_console_routes.py -q -k 'outbox or route_totality_from_declared_catches or registry_tamper_preview_swap'
.....................................................                    [100%]
53 passed, 143 deselected in 6.42s
```

## Self-review

- Confirmed the planned-HEAD check is under the shared action lock, after the
  clean-worktree check, and outside the exception block that invokes reset and
  clean.
- Confirmed `planned_head` has no default, so hand-built plans must state the
  contract rather than silently bypassing it.
- Confirmed the stale-plan test uses a real newer commit affecting a file in
  the stale edit set and compares the full pre/post boundary, not a mock
  outcome.
- Confirmed blocker selection examines the raw exception type, while error
  description remains solely in the presentation composition root.
- Confirmed the end-to-end response retains one malformed-receipt card,
  withholds all controls/fingerprints listing-wide, and exposes neither the
  unreadable record's filename nor raw content.
- Confirmed rejected/obsolete suggestions were not implemented.

## Concerns

None introduced. The planned-HEAD comparison protects cooperating OneOS
actions that commit before the check; it deliberately does not claim
protection from an out-of-model Git writer that changes repository state after
the final comparison.

---

# Follow-up — offline Gate 3 rename envelopes

Follow-up commit: `433c5fb8ab0475a51f81e0574ed380621379f2e6`
Commit subject: `fix: restore offline rename audit planning`

## Root cause

The exact-head full suite at `ee491acc6bec7882eb406cd701a9518de3165007`
found six Gate 3 failures. `tools/gate3_audit._parent_tree()` materializes an
immutable historical parent into a plain temporary directory with no `.git`.
`_rename_envelope()` called the now-live-only `plan_rename()`, whose required
`git rev-parse HEAD` failed in that directory. `_sanctioned_rename()` caught
the failure while trying each axis, so valid rename commits were classified as
violations.

## RED evidence

Added
`tests/test_gate3_audit.py::test_offline_rename_envelope_uses_explicit_parent_oid_without_git_repo`.
It creates a real rename commit, materializes its real parent tree through Gate
3, proves the tree contains no `.git`, supplies that exact parent OID, and
requires the reconstructed envelope to equal the commit's changes.

Before implementation:

```text
FAILED tests/test_gate3_audit.py::test_offline_rename_envelope_uses_explicit_parent_oid_without_git_repo
E TypeError: _rename_envelope() got an unexpected keyword argument 'parent_oid'
1 failed in 0.31s
```

The intended API was absent: the audit had no way to provide the immutable
historical identity and therefore depended on the live planner's Git lookup.

## Implementation

- Added `build_rename_plan(..., *, planned_head: str)`, a shared constructor
  that computes the same moves/edits without inspecting Git. The keyword-only
  planned HEAD remains required and has no default.
- Kept `plan_rename()` as the public live entry point. It validates the request,
  captures real current `HEAD`, and supplies it explicitly to the shared
  constructor.
- Changed Gate 3's offline envelope builder to require `parent_oid` and pass
  `record.parents[0]` explicitly to `build_rename_plan()`.
- The audit does not initialize or commit a fake repository and does not read
  mutable current HEAD. `RenamePlan.planned_head` and the action-locked stale
  check in `apply_rename()` remain unchanged.

Initial GREEN:

```text
.                                                                        [100%]
1 passed in 0.29s
```

## Targeted verification

The six exact cases reported by the controller:

```text
......                                                                   [100%]
6 passed, 98 deselected in 4.33s
```

Complete live rename suite:

```text
....................                                                     [100%]
20 passed in 2.82s
```

All Gate 3 rename-focused tests:

```text
................                                                         [100%]
16 passed, 88 deselected in 3.43s
```

## Non-vacuity mutation check

Temporarily reintroduced the exact defect by making `build_rename_plan()` run
`git rev-parse HEAD`. The new regression failed on the materialized tree:

```text
FAILED tests/test_gate3_audit.py::test_offline_rename_envelope_uses_explicit_parent_oid_without_git_repo
E subprocess.CalledProcessError: Command '['git', 'rev-parse', 'HEAD']'
E returned non-zero exit status 128.
1 failed in 0.32s
```

After restoring the explicit-OID constructor, the same test returned GREEN:

```text
.                                                                        [100%]
1 passed in 0.33s
```

## Follow-up self-review and concerns

- Confirmed the live planner alone reads current HEAD.
- Confirmed the offline audit supplies the commit record's immutable sole
  parent and never discovers identity from the materialized directory.
- Confirmed every constructor path must state `planned_head`; there is no
  default and therefore no silent bypass.
- Confirmed no fake repository, audit commit, dependency, vault access, or
  unrelated change was introduced.

Concerns: none introduced. Gate 3 still reconstructs the envelope from the
historical parent tree it already materialized; only the source of the plan's
explicit identity changed.

Post-commit verification:

```text
uv run pytest tests/test_gate3_audit.py -q -k rename
................                                                         [100%]
16 passed, 88 deselected in 3.39s

uv run pytest tests/test_rename.py -q
....................                                                     [100%]
20 passed in 3.00s

git diff HEAD^ --check
(exit 0; no output)
```

---

# Final CodeRabbit review — cross-vault plan identity

Starting HEAD: `118ae46c71fedb0f8df15f8ee993dc2a162fab03`
CodeRabbit check status supplied by the controller: green.

## Accepted defect — plan and execution vault can diverge

### Root cause

`apply_rename(vault, plan)` previously acquired the lock and ran Git operations
against the separately supplied `vault`, while `plan.edits` and `plan.moves`
contained paths constructed beneath `plan.vault`. Two distinct repositories
at the same HEAD passed the stale-HEAD check, allowing filesystem writes in
one repository and lock/Git/rollback activity in the other.

### RED evidence

Added
`tests/test_rename.py::test_rename_plan_from_another_vault_refuses_before_lock_or_mutation`.
It creates one real Git repository and a distinct real clone, proves their
HEADs are identical, plans against the first, and applies against the second.
It requires a `RenameError` before `action_lock` or rename's Git boundary and
compares both repositories' exact HEAD, index, cached diff, worktree diff,
status, and every non-Git object before and after.

Before implementation:

```text
FAILED tests/test_rename.py::test_rename_plan_from_another_vault_refuses_before_lock_or_mutation
E AssertionError: cross-vault refusal reached action_lock
1 failed in 0.30s
```

Added the preservation case
`test_rename_plan_accepts_relative_and_absolute_names_for_same_vault`.
Before implementation, the same real root named relatively during planning and
absolutely during apply failed at the move boundary:

```text
FAILED tests/test_rename.py::test_rename_plan_accepts_relative_and_absolute_names_for_same_vault
E ValueError: 'same-vault/oldentity' is not in the subpath of '<absolute>/same-vault'
1 failed in 0.23s
```

### Implementation

At the first line of `apply_rename()` behavior, before lock acquisition,
worktree inspection, Git commands, validators, or mutation:

- resolve `plan.vault` and the supplied execution vault strictly to their real
  roots;
- refuse with `RenameError` when those roots differ; and
- after equivalence is proved, use the plan's original path spelling so every
  stored edit/move path and every Git/lock operation remains rooted coherently.

This accepts relative/absolute aliases (and other aliases that safely resolve
to the same real directory) while rejecting two repositories even when their
HEAD OIDs are byte-identical.

Focused GREEN:

```text
..                                                                       [100%]
2 passed in 3.96s
```

### Non-vacuity mutation check

Temporarily disabled only the resolved-root mismatch branch. The real
cross-vault regression returned RED by reaching the forbidden lock boundary:

```text
FAILED tests/test_rename.py::test_rename_plan_from_another_vault_refuses_before_lock_or_mutation
E AssertionError: cross-vault refusal reached action_lock
1 failed in 0.20s
```

After restoring the branch, the same regression returned GREEN:

```text
.                                                                        [100%]
1 passed in 0.25s
```

## Review findings rejected after verification

### 1. Comment `3856205818`: wrap rename planning/Gate 3 in entity scope

**Rejected.** `app/rename.py` explicitly defines rename as a vault-wide direct
admin operation, never a request-path entity query. It plans all registry and
reference edits needed by five axes (`entity`, `product`, `member`, `project`,
and `workspace`). Gate 3 must reconstruct those same vault-wide envelopes from
historical parent trees. `scope.current_entity()` would select one entity and
make the planner/auditor semantically incomplete; the tenant-boundary invariant
applies to scoped application queries/path resolution, not this explicit
vault-wide admin exception.

### 2. Route rename through the outbox

**Rejected as out of scope and contrary to the current contract.** The rename
module contract and `BUILD.md` explicitly retain the tested rename admin
operation as a direct, atomic, one-commit exception. Moving it through the
outbox would redesign the action model, proposal schema, review surface,
receipts, Gate 3 envelope, and recovery behavior; no code in this review
disproves the existing exception.

### 3. Replace reset/clean for a direct writer after final checks

**Rejected as an out-of-model substantial redesign.** Every supported OneOS
writer cooperates through `action_lock`, which rename holds across mutation,
validation, commit, and its existing rollback. Deliberate/direct repository
mutation after the final cooperative check is explicitly outside that writer
boundary. Replacing the established rollback protocol would broaden this
narrow fix into new ownership/recovery architecture and is not justified by a
supported-writer reproduction.

### 4. Replace synthetic `alpha` in the new route test

**Rejected as a fixture misclassification.** `alpha` is the repository-wide
synthetic entity used throughout the public console route fixtures, not a real
instance value. Changing one test alone would create inconsistent synthetic
fixtures without improving instance isolation or behavior coverage.

## Verification and self-review

```text
uv run pytest tests/test_rename.py -q
......................                                                   [100%]
22 passed in 2.61s

uv run pytest tests/test_gate3_audit.py -q -k rename
................                                                         [100%]
16 passed, 88 deselected in 2.63s
```

- Confirmed the identity check precedes the `try` containing `action_lock`, so
  mismatch cannot acquire/create a lock, run Git, or enter rollback cleanup.
- Confirmed both real repository boundaries stay byte-identical on refusal.
- Confirmed safely equivalent relative/absolute roots still complete the
  original one-commit rename.
- Confirmed the planned-HEAD stale check remains under `action_lock` and Gate
  3's explicit historical-parent planning remains unchanged.
- Confirmed no scope, outbox, rollback architecture, docs evidence/counts,
  dependency, vault/private-gate, branch/PR, or cleanup change was made.

Concerns: none introduced. Root identity is checked before the cooperative
lock; deliberate root replacement after that check remains within the already
documented out-of-model ancestor-relocation limitation.
