# S6 SDD Ledger — subagent-driven development record

**Branch:** `codex/s6-visible-console-failures` from `origin/main` at `a42ee12`.
**Baseline verified:** 603 public tests passed at `431859b` before Task 1.
**Boundary:** cloud, synthetic fixtures only. No vault access. The plan's
Grey Matter pre-state precondition and Task 15 private gates belong to the
local boundary and are not executed here.

This ledger records, per task: RED evidence, GREEN evidence, full-suite
result, reviewer verdict, fix rounds, and commit SHA. The preflight interface
matrix below was verified consistent across tasks before any code was written.

---

## Preflight interface matrix

Every class, function, decorator, and template the plan introduces, with its
owning task, signature, and consumers. Verified against the design's normative
tables before Task 1.

### New modules

| Interface | Owner | Signature / shape | Consumers |
|---|---|---|---|
| `ConsoleError` | Task 1, `app/console_errors.py` | frozen dataclass `(code, tier, severity, message, retry, committed, page_status)`; `__post_init__` asserts the design §2 structural invariants | `describe()`, `console_render.status_for`, `app/main.py`, templates, tests |
| `TIERS`, `SEVERITIES`, `RETRIES`, `COMMITTED`, `PAGE_STATUSES` | Task 1 | module constants; `TIERS` ordered tuple (precedence rank = index) | `ConsoleError`, `describe()` tie-breaking, tests |
| `describe(exc) -> ConsoleError` | Task 5, `app/console_errors.py` | Rule-1 resolver: exact-identity allowlist walk over `__cause__` only, depth 4, overflow → `E-UNKNOWN`; ties → innermost | `app/main.py` handlers and renderers only |
| `_EXACT`, `_MRO` | Task 5 | class-keyed maps transcribing the design's normative class map; every `GitTransactionError` family member is `exact` | `describe()`, invariant tests |
| `ALLOWLIST` | Task 5 | `(OutboxTransactionError, RegistryTransactionError, OutboxDestinationError, GitTransactionFailure, _ApprovalLockCleanupFailure, _ReviewedIndexOwnershipConflict)` — exact class identity, membership closed by invariant 2 | `describe()` |
| `status_for(error, fragment) -> int` | Task 7, `app/console_render.py` | `200 if fragment and error.severity == "refusal" else error.page_status` | `app/main.py` |
| `is_fragment(request, endpoint) -> bool` | Task 7, `app/console_render.py` | route shape first (`surface == "fragment-only"` → always True), else `HX-Request` header | `app/main.py` |
| `console_route(catches, surface)` | Task 7, `app/console_routing.py` | decorator storing `(catches, surface)` on the endpoint (`__console_route__`); `ValueError` if `Exception`/`BaseException` in catches; `surface` ∈ {"page", "fragment-only"} | `app/main.py` route declarations, invariant-6 tests, `console_render.is_fragment` |
| `structured_reader(category)` | Task 7 (declared), Task 8 (applied), `app/console_routing.py` | decorator storing category on the function (`__structured_reader__`); category ∈ {"registry", "proposal", "front-matter", "admin-db"}; pure metadata | reader declarations across `app/`, invariant-4 AST test |

`app/console_routing.py` is pure metadata — importable by services without
touching the taxonomy, so decorating a domain reader does not violate the
one-way boundary. Only `app/main.py` (the composition root) imports
`console_errors`/`console_render`.

### New exception subtypes (Task 2 declares; Tasks 3-4 raise them)

| Class | Base | Module | Raised for | Code |
|---|---|---|---|---|
| `OutOfScopeError` | `CrossScopeError` | `app/scope.py` | path resolved outside the bound entity | `E-SCOPE` (mro) |
| `RedirectedPathError` | `CrossScopeError` | `app/scope.py` | redirected / non-regular / type-swapped path | `E-TAMPER` (mro) |
| `ProposalSourceUnavailable` | `CrossScopeError` (imported) | `app/outbox.py` | ordinary `OSError` at the safe-read boundary | `E-UNAVAILABLE` (exact) |
| `UnreadableProposalRecord` | `OutboxError` | `app/outbox.py` | phase-1 record read/shape failure in the projection | `E-UNREADABLE` (mro) |
| `RedirectedDestination` | `UnsafeDestinationPath` | `app/destinations.py` | destination dir symlinked/redirected | `E-TAMPER` (exact) |
| `MissingDestination` | `UnsafeDestinationPath` | `app/destinations.py` | destination dir merely absent (E4-adjacent) | `E-DEST` (exact) |
| `RedirectedSourceLeaf` | `InvalidSourceLeaf` | `app/destinations.py` | receipt symlinked or type-swapped | `E-TAMPER` (exact) |
| `MissingSourceLeaf` | `InvalidSourceLeaf` | `app/destinations.py` | receipt absent | `E-DEST` (exact) |
| `NonCanonicalLeaf` | `InvalidSourceLeaf` | `app/destinations.py` | leaf name / source location non-canonical | `E-DEST` (exact) |
| `ReviewedPathIntegrityError` | `ReviewedStateConflict` | `app/git_transaction.py` | symlink / non-regular / identity swap during capture | `E-TAMPER` (exact) |
| `ReviewedStateChanged` | `ReviewedStateConflict` | `app/git_transaction.py` | genuine concurrent content/index change | `E-CONFLICT` (exact) |
| `ReviewedPathUnavailable` | `ReviewedStateConflict` | `app/git_transaction.py` | ordinary read failure (lstat/open `OSError`) | `E-UNAVAILABLE` (exact) |
| `InvalidTransactionPath` | `ReviewedStateConflict` | `app/git_transaction.py` | path validation before any I/O | `E-INTERNAL` (exact) |

All existing `except CrossScopeError` / `except ReviewedStateConflict` /
`except DestinationError` clauses keep catching every subtype — type
refinement only, no refusal changes.

### Projection (Task 9, `app/outbox.py`)

| Interface | Signature | Consumers |
|---|---|---|
| `_read_record(path) -> dict` | extracted strict-loader read: leaf check, bytes, YAML, mapping | strict loader, projection phase 1 |
| `_validate_record(scope, path, record) -> Proposal` | extracted strict-loader validation: identity, action, `_to_proposal`, `_require_destination` | strict loader, projection phase 1/2 |
| `_render_diff(scope, proposal) -> str` | difflib work over an already-validated record; reads via `_read_no_follow_bytes`, translating per the design's normative table (`FileNotFoundError → MissingProposalSource`; `RedirectedPathError` passes; `UnicodeDecodeError → OutboxDestinationError`; other unavailability passes as `ProposalSourceUnavailable`) | strict `preview_diff` path, projection phase 3 |
| `OutboxRow` | `(proposal: Proposal|None, diff: str|None, error: BaseException|None, can_approve: bool, can_reject: bool)` — `error` carries the raw exception, never a code | `project_outbox`, `app/main.py`, `outbox_list.html` |
| `OutboxListing` | `(rows: tuple[OutboxRow, ...], blocked: bool)` | same |
| `project_outbox(scope) -> OutboxListing` | phase 1 caught per row as `UnreadableProposalRecord` (sets `blocked`); phase 2 propagates; phase 3 row-local; skips well-formed `action: delete` records after the identity check; calls none of `get_proposal` / `load_proposals` / `preview_diff` | `outbox_screen`, `outbox_approve`, `outbox_reject` in `app/main.py` |

### Templates and static (Task 7 creates; Tasks 11-13 modify)

| File | Responsibility | Consumers |
|---|---|---|
| `templates/_head.html` | shared head: charset, viewport, title block, css, five vendored deferred scripts, `htmx-config` meta overriding `[45]..` to `swap:true,error:true` | all four page documents + `blocks/no_bundles.html` |
| `templates/blocks/alert.html` | fragment alert: `role="alert"`, `error.code` + `error.message`, no `| safe` | fragment error rendering in `app/main.py` |
| `templates/error.html` | full-page notice; omits the sidebar when the described error is `E-CONFIG` | page error rendering |
| `templates/blocks/no_bundles.html` | templated replacement for `triage_default`'s bare `HTMLResponse`, carrying `_head.html` | `triage_default` |
| `templates/blocks/outbox_list.html` | rewritten over `OutboxListing`: blocked notice, read-only rows, per-row errors, `tojson` hx-vals | `_outbox_list` |
| `templates/triage.html` | per-row `(item, classification, destination, error)`; stopwatch counts only the server's persisted signal | `triage` |
| `templates/registry.html`, `templates/blocks/delete_impact.html` | `tojson` hx-vals; delete-execute templated both branches | registry routes |
| `static/app.css` | `.alert`, `.alert-attention` | alert/error templates |

### Route metadata (Tasks 10-13, `app/main.py`)

Catch tuples and surfaces exactly as the plan's Task 10 table. Handlers
registered: `EntitySelectionError` (→ `E-ENTITY`, 404), `RequestValidationError`
(→ `E-REQUEST`, 422), `StarletteHTTPException` (body replaced under
`HX-Request` only, framework status preserved), global fallback (describes,
returns `error.page_status`, never 200). `propose` sets an `HX-Trigger`
success event header only after `propose_classification` returns.

### Cross-task consistency checks performed

- Task 5's allowlist literal equals the design rule (four named classes plus
  the private `GitTransactionFailure` subclasses).
- Task 2's subtype/base pairs match the design §2 table and the Task 3/4
  raise-site conversion rules; no forward references remain for Task 5's map.
- Task 7's `surface` vocabulary matches Task 10's route table.
- Task 8's category vocabulary matches invariant 4's table.
- Task 9's `_render_diff` translation table matches the Task 3 safe-read
  contract (both raised types subclass `CrossScopeError`).
- Task 12/13 regression edits (`tests/test_app.py:477-503`, `:588`) match the
  design's regression table exactly; no other pre-existing test is touched.

### Design-internal discrepancies observed and resolutions

1. **`InvalidTransactionPath` and `ReviewedPathUnavailable` codes.** The §2
   ambiguous-base summary row says `InvalidTransactionPath → E-INVALID` and
   `ReviewedPathUnavailable → E-CONFLICT`; the class map (which the design
   labels "normative — a product contract") says `E-INTERNAL` and
   `E-UNAVAILABLE`, and a dedicated paragraph explains exactly why the
   summary's codes would be untruthful. Resolution: the normative class map
   wins. Not a stop — the design resolves its own tension explicitly.
2. **Gate 1 stopwatch vs. an unlisted pre-existing test.**
   `tests/test_app.py::test_triage_screen_has_gate1_timing_instrument`
   asserts `"htmx:afterRequest" in html`. The design §5 says the stopwatch
   "listens for that event rather than for `htmx:afterRequest`", while §8
   says any pre-existing test change outside the two listed is a scope
   breach. These cannot both hold under a literal listener removal.
   Resolution: the §8 bright line ("Any test not listed here requiring
   modification is a scope breach") outranks the mechanism sentence. The
   counter is keyed exclusively on the success-only server signal — the
   `HX-Trigger` event name read from the response header inside the existing
   `htmx:afterRequest` hook — so a refusal (which carries no header) can
   never increment it, satisfying §5's requirement ("the metric is truthful
   by construction… a success-only server signal cannot be produced by a
   refusal") without modifying the unlisted test. Recorded for local review.

---

## Per-task ledger

### Task 1 — `ConsoleError` with structural invariants and `page_status`

- **RED:** `uv run pytest tests/test_console_errors.py -q` →
  `ModuleNotFoundError: No module named 'app.console_errors'` (collection
  error, all five tests unrunnable).
- **GREEN:** same command → 5 passed.
- **Full suite:** `uv run python -m pytest -q` → 608 passed (603 baseline
  + 5 new; no pre-existing test modified).
- **Reviewer verdict:** clean (no Critical/Important/Minor findings;
  implementation byte-identical to the plan's Step 3 block, tests
  content-identical to the plan, invariants match design §2).
- **Fix rounds:** 0.
- **Commit:** `2185680`. (An earlier line recorded the pre-amend SHA
  `90562ed`; the amend that folded this ledger entry into the commit changed
  it. From Task 2 on, each task's SHA is recorded in the next task's ledger
  edit to avoid the self-reference.)

### Task 2 — Declare every new exception class

- **RED:** `uv run pytest tests/test_console_invariants.py -q` →
  `ImportError: cannot import name 'OutOfScopeError' from 'app.scope'`
  (1 failed).
- **GREEN:** same command → 1 passed.
- **Full suite:** 609 passed (608 + 1 new; declarations only, 52 insertions,
  0 deletions, no raise/except touched).
- **Reviewer verdict:** clean. The reviewer independently flagged the same
  design-internal discrepancy already recorded in the preflight notes
  (§2 summary row vs. the normative class map for `InvalidTransactionPath`
  and `ReviewedPathUnavailable`); resolution unchanged — the normative class
  map wins in Task 5.
- **Fix rounds:** 0.
- **Commit:** `d916048`.

### Task 3 — Pin the safe-read contract and convert the CrossScopeError sites

- **RED:** `uv run pytest tests/test_console_invariants.py -q` → 5 failed,
  2 passed — `test_safe_read_symlink_raises_redirected`,
  `test_safe_read_nonregular_raises_redirected`,
  `test_safe_read_permission_error_raises_unavailable`,
  `test_safe_read_replacement_race_raises_redirected`,
  `test_safe_read_other_oserror_raises_unavailable` all failed because every
  non-missing case raised bare `CrossScopeError` (e.g.
  `app/outbox.py:148: CrossScopeError`).
- **GREEN:** same command → 7 passed.
- **Full suite:** 615 passed (609 + 6 new). `grep -rn "raise CrossScopeError"
  app/` → empty.
- **Reviewer verdict:** clean; two Minors recorded, no action required:
  (1) a mid-read `OSError` after `fstat` passes still escapes raw — the
  pre-existing shape, outside the open-time discrimination site the design
  names; (2) `inbox._require_real_receipt` raises the integrity subtype for
  an absence race on a just-enumerated path — pre-existing shape,
  discrimination would add its own race. Reviewer confirmed the
  `system_path` → `RedirectedPathError` judgment against the normative map.
- **Fix rounds:** 0.
- **Commit:** `40f5554`.

### Task 4 — Convert the git_transaction and destinations sites

- **RED:** `test_no_direct_raise_of_an_ambiguous_base` failed listing 25
  offender lines (18 in `app/git_transaction.py`, 7 in
  `app/destinations.py`; first extra item `app/destinations.py:87`).
- **GREEN:** `uv run pytest tests/test_console_invariants.py -q` → 8 passed.
- **Full suite:** 616 passed (615 + 1). `git diff --stat` touched only the
  three in-scope files; S5's normalization/chaining region unmodified.
- **Reviewer verdict:** clean; reviewer independently reproduced the RED
  state against HEAD versions and confirmed refusal-set equivalence by
  boolean analysis. Two Minors, no action: the AST test only inspects
  `ast.Call` raises (plan-verbatim shape), and `EMLINK` is a documented
  faithful reading of "O_NOFOLLOW rejection". Reviewer re-flagged the Task 5
  code-map discrepancy; resolution unchanged (normative class map wins).
- **Fix rounds:** 0.
- **Commit:** `1b02df0`.

### Task 5 — The class map and `describe()`

- **RED:** `uv run pytest tests/test_console_errors.py -q` → 60 failed,
  5 passed (`ImportError`/`AttributeError`: `describe`, `_EXACT`, `_MRO`,
  `_CODES` undefined).
- **GREEN:** same command → 65 passed.
- **Full suite:** 676 passed (616 + 60 new; no pre-existing test modified).
- **Reviewer verdict:** clean; reviewer verified all 39 normative map rows,
  all 21 codes, and all 21 messages byte-for-byte against the design
  (including E-STALE/E-MISSING against `tests/test_app.py:434`/`:439`),
  executed resolver probes for allowlist identity, `__context__`
  non-traversal, depth overflow, tie-breaking, and the closed family, and
  confirmed the S5-outcome tests use the real wrappers. Confirmed the
  ledger's adjudication of the design's internal discrepancy is applied.
  Three Minors: (1) the second prong of `test_exact_mapping_does_not_inherit`
  was missing — **fixed** (synthetic `RegistryTransactionError` subclass →
  E-REGISTRY, not E-GIT); (2) the "never raised" half of the abstract-base
  test is deliberately delegated to invariant 3 (Task 6); (3) two redundant
  guards in `_lookup` carried from the plan's own pseudocode — left as
  plan-written.
- **Fix rounds:** 1 (Minor 1 addressed; 65 passed, full suite 676 re-run
  green).
- **Commit:** `d8db708`.

### Task 6 — Invariants 1 and 2

- **RED evidence:** Task 6 is guard-tests-only over an already-complete map;
  the plan's Step 4 anticipated map gaps and none existed, so the tests
  passed on first run. Falsifiability proven by mutation probe instead: an
  injected unmapped `GitTransactionError` subclass fails invariant 2
  ("ProbeGitError lacks its own exact entry") and invariant 1
  ("app.git_transaction.ProbeGitError is unmapped"). The reviewer
  independently reproduced both mutations plus a boundary-test mutation
  (injected `from . import console_errors` into `app/scope.py` → red).
- **GREEN:** `uv run pytest tests/test_console_invariants.py -q` → 11 passed.
- **Full suite:** 679 passed (676 + 3 new).
- **Reviewer verdict:** clean; two Minors: (1) `app/__init__.py` invisible to
  both walks — **fixed** with the same AST guard `app.main` carries;
  (2) name-based heuristics in the AST guards — standard trade-off, left.
- **Fix rounds:** 1 (Minor 1; 11 passed, full suite 679 re-run green).
- **Commit:** `3f84b8a`.

### Task 7 — Renderers, route metadata, templates

- **RED:** `uv run pytest tests/test_console_render.py -q` → 7 failed
  (`ModuleNotFoundError: No module named 'app.console_render'` /
  `'app.console_routing'`; page templates lacked the htmx-config meta).
- **GREEN:** same command → 7 passed.
- **Full suite:** 686 passed (679 + 7; vendored-asset and morph regression
  tests still green against the shared head).
- **Reviewer verdict:** clean; reviewer parsed the meta JSON structurally
  against the design block and confirmed head extraction byte-fidelity.
  Three Minors, no diff change: (1) the `app.routes`-enumerated form of the
  meta test belongs to invariant 6's route sweep — **carried forward as an
  obligation on the Task 10-14 route tests** (design §8 "Every full-page
  route … contains the htmx-config meta tag"); (2) redundant `page_title`
  sets — harmless; (3) `console_route` validates surface membership beyond
  the plan — fail-closed extension.
- **Fix rounds:** 0.
- **Commit:** recorded in the Task 8 entry.

### Task 8 — Structured readers

- **Task 7 commit (deferred to this entry):** `d8940b5`.
- **RED evidence:** `test_every_structured_read_site_declares_a_category` failed
  listing every undeclared structured-read site in `app/`; the six conversion
  and tolerance tests failed on missing behaviour.
- **GREEN:** 17 readers declared across the four categories; escaping failures
  in `registry`-category readers normalized to `DestinationRegistryError`.
- **Execution note:** the SDD worker terminated on a session limit after
  implementing this task but before review or commit. The work was recovered
  from its worktree, verified green, and carried through review here.

**Fix round 1** — reviewer returned *not clean*: 1 Critical, 4 Important.

- **C1 (Critical), invented refusal.** `_count_workspaces` had been narrowed from
  `(entry or {})` to `if entry is None`, making `false` / `""` / `0` as a list
  entry fatal `E-CONFIG` where they were previously counted as zero. Reachable
  from the delete-preview route. Restored to `if not entry`.
- **I1** guard was cwd-relative: from any other directory it scanned zero files
  and asserted `[] == []`. Anchored to `__file__`, plus a scanned-file floor.
- **I2** guard missed aliased imports and the design's third trigger. Added
  per-file alias resolution and `safe_load`/`load`/`full_load`/`unsafe_load`.
- **I3** a corrupt `books.db` reached the operator as `E-UNKNOWN` on a live
  route. Narrow `sqlite3.DatabaseError` conversions in `registry` and `rename`.
- **I4b** the unguarded mid-approval re-read in `approve()`.
- **I4a NOT taken:** converting `load_proposals`'s escaping types broke two
  pre-existing tests — see the ruling below.
- **I5** coverage gaps for `_remove_scoped_registry_value`, `EntityCatalog.load`,
  and the unpinned `_count_front_matter` tolerance.

**Fix round 2** — reviewer returned *not clean* again: 4 Important. C1 confirmed
genuinely fixed by a 100-case differential probe against the pre-change tree
(zero `OK → RAISE` transitions).

- **The `system_path` trigger fired on nothing.** It matched only the chained
  form, while every real site assigns first — so the test added to prove the
  trigger passed while the trigger was dead. Fixed by tracking names bound to a
  `system_path(...)` result **per function** (module-wide tracking over-matched
  common names like `path` and produced a false positive on a proposal *write*),
  plus `self.<attr>` module-wide for the bind-in-`__init__`, read-elsewhere case.
  The working trigger immediately found a real undeclared reader:
  `add_workspace`, now declared `registry`.
- **`_remove_scoped_registry_value` still escaped** on a list of scalars where a
  list of mappings is expected — the design's own "wrongly shaped but valid"
  row, four lines below two conversions the same diff added. Converted.
- **`Vault._archetypes`' schema check was left raw**, so a hand-edited
  `archetypes.yaml` missing `modules:` produced a 500 blank screen on every
  Console page. Converted to `DestinationRegistryError`.
- **I4b was broader than Rule 5 permits** and its comment was factually wrong:
  a non-mapping is already `OutboxDestinationError` inside `_to_proposal`, so the
  blanket `AttributeError`/`TypeError` bought only the `contents is None` case
  while risking destructive advice about a healthy file. Narrowed to mirror
  `execute_delete`, and pinned by test — it had shipped untested.
- Also: `test_workspaces_tolerates_every_falsy_entry` could pass vacuously
  (`0` is also the answer for an absent file); each falsy entry is now paired
  with a real one asserting `1`.

**Ruling on I4a — the design's regression table is COMPLETE and must NOT be
amended.** The first analysis here was wrong and is corrected for the record.
Phase 1 belongs to the **projection**, not the loader: the design says the strict
loader is untouched and that approval "revalidates from scratch through the
untouched strict path". The phase-1 table's "Escapes today as" column describes
what the projection must **translate**, not what `load_proposals` must **raise**.

Task 9's reconciliation, which satisfies both its "behavior identical" and
"`tests/test_outbox.py` unmodified" steps:

- `_read_record` / `_validate_record` raise `UnreadableProposalRecord`;
- `load_proposals` catches it and re-narrows to `OutboxDestinationError`, so the
  strict loader's escaping types are unchanged;
- `project_outbox` catches the typed error directly, per row.

`tests/test_outbox.py:271-274` (`_assert_destination_error`, 13 call sites) stays
as-is. Relaxing its exact-type assertion would be a scope breach.

**Carried to S7 (no S6 change):** `_count_front_matter` is declared
`front-matter` (absorbing), but its `except OSError` does not cover
`UnicodeDecodeError`, so a latin-1 `.md` file still escapes. Absorbing it would
change the fatality of something already fatal, which S6 has no authority to do.

**Fix round 3** — reviewer returned *not clean*: 1 Important.

- **The I4b test was vacuous.** It monkeypatched `capture_path_state` and then
  never called `approve()`; its assertions restated the class map and a
  `PathState` property, so deleting the entire I4b guard left it green. Strictly
  worse than the vacuous test round 2 rejected, and it made this ledger's
  "pinned by test" claim false. Rewritten to drive `approve()` for real, and
  **non-vacuity proven**: with the guard removed the test fails
  (`yaml.safe_load(None)` raises `AttributeError`), with it restored it passes.
  It also asserts `HEAD` is unchanged, since the raise precedes the transaction.

**Carried to Task 9 (recorded by the round-3 reviewer, not a Task 8 issue):**
`OutboxDestinationError` is on the resolver allowlist and ties resolve innermost,
so `raise OutboxDestinationError(...) from UnreadableProposalRecord(...)` flips
the *described* code on the strict path from `E-INVALID` to `E-UNREADABLE`. No
existing test would catch that. Task 9 must make it a deliberate decision.

**Carried to S7:** residual guard heuristic gaps (tuple-unpack and walrus
binding, alias rebinding, `Path(...)` wrapping, builtin `open` on a tracked
name) — none present in `app/` today, none required by invariant 4, which only
refuses silence. Also `add_workspace` and `_count_front_matter` both still let
`UnicodeDecodeError` escape; absorbing it would change an existing fatality.

- **Reviewer verdict:** clean after fix round 3 (three rounds: 1 Critical +
  4 Important, then 4 Important, then 1 Important).
- **Mutation evidence:** the round-3 reviewer stripped each of the 18
  `@structured_reader` declarations one at a time; every one is flagged on
  removal, so no declaration is decorative.
- **Suite:** 703 passed. `git diff --check` clean.

### Task 9 — decisions recorded before implementation

Both carried items from Task 8 are decided here rather than left to emerge.

**D1 — the strict-loader reconciliation.** `_read_record` and `_validate_record`
raise `UnreadableProposalRecord`. `load_proposals` catches it and re-narrows to
`OutboxDestinationError`, so the strict loader's escaping type is unchanged
and `tests/test_outbox.py` — including `_assert_destination_error`'s exact-type
assertion at 13 call sites — stays untouched. `project_outbox` catches the typed
error directly, per row. This is what satisfies the plan's own "behavior
identical" and "test_outbox.py unmodified" steps simultaneously.

**D2 — the described code on the strict path flips to `E-UNREADABLE`, and that
is deliberate.** `OutboxDestinationError` is on the resolver allowlist and ties
resolve innermost, so `raise OutboxDestinationError(...) from
UnreadableProposalRecord(...)` makes `describe()` return `E-UNREADABLE` where it
previously returned `E-INVALID`.

Accepted, because it is the more truthful outcome. `E-INVALID` advises "create a
new proposal", which cannot clear a file that is not parseable as a proposal;
`E-UNREADABLE` advises repairing or removing it outside the Console. That is the
same distinction that justified splitting the two codes. The refusal itself is
unchanged — same input, same refusal, same exception type escaping
`load_proposals` — only the operator-facing description improves.

Pinned by test, since no existing test covers it and the round-3 reviewer noted
it would otherwise be a silent change.

### Task 9 — The projection

- **RED:** `ImportError: cannot import name 'project_outbox' from 'app.outbox'`
  — collection error, all 13 tests unrunnable, matching the plan's prediction.
- **GREEN:** `OutboxRow`/`OutboxListing`; `_read_record` / `_validate_record` /
  `_render_diff` extracted; `project_outbox` with the three-phase rule.
- **Suite:** 725 passed. `tests/test_outbox.py` byte-identical. `diff --check`
  clean. Only `app/outbox.py` and the new test file touched.

**Escalated deviation — `preview_diff` does not delegate to `_render_diff`, and
the reviewer accepted it.** The design says "strict reload first, then
delegate". Delegating is **not** behaviour-neutral: the two differ in read
policy, not just on the missing-source case.

| | `preview_diff` | `_render_diff` |
|---|---|---|
| path | `scope.resolve_stored(src)` | `scope.root / src` (matches `approve`) |
| read | `read_text` — follows symlinks | `_read_no_follow_bytes` — `O_NOFOLLOW` |
| absent | `""` empty-old fallback | `MissingProposalSource` |
| non-UTF-8 | raw `UnicodeDecodeError` | `OutboxDestinationError` |

Delegating would start refusing symlinked and non-UTF-8 receipts on
`outbox_screen`, `_outbox_list`, and the propose fragment — three live routes
Task 9 does not own, none listed in the design's regression table. Escalating
rather than editing `app/main.py` or relaxing a pre-existing test was correct.
Both functions survive all of S6 (Task 11's propose fragment keeps calling
`preview_diff`), so this is not deferred to a later task.

The duplication it caused **was** fixed: a pure `_diff_text(proposal, old)`
formatter now serves both, each keeping its own read policy. Gutting
`_render_diff` previously passed 13/13; it now fails.

**Fix round 1** — reviewer returned *not clean*: 0 Critical, 4 Important. The
implementation was faithful to design §3 on every probed point; every finding
was an **unpinned** behaviour — six design-named behaviours survived mutation
with all 716 tests green.

- **I1 — D2 was not pinned, and this ledger's "Pinned by test" was false.**
  Changing `from exc` to `from None` — the minimal plausible revert — kept 716
  green while the described code silently returned to `E-INVALID`. Now pinned on
  the **strict** path, which the projection cannot see: the previous assertion
  was on a projection row. Verified: the mutant now fails.
- **I2 — four of the seven phase-1 rows were decorative**, including the two the
  design sets in bold. Dropping the `UnicodeDecodeError` handler left the suite
  green while one bad byte in a proposal file produced `E-UNKNOWN` at 500 — a
  blank screen, the exact failure the design names. Now parametrized over all
  seven, plus the two byte-level rows. Verified: the mutant now fails.
- **I3 — `_render_diff`'s output was entirely unasserted.** Now pinned as
  `row.diff == preview_diff(scope, row.proposal)`, which also makes any drift
  between the two read policies a test failure. Verified: the mutant now fails.
- **I4 — D1's wording corrected above.** A 99-case differential over
  `load_proposals` between `HEAD` and this tree shows **zero `OK ↔ RAISE`
  transitions** — no refusal changed — but two conditions change escaping type:
  `UnicodeDecodeError` and record-read `OSError` become
  `OutboxDestinationError`. That is required by `_read_record`'s own
  `proposal` reader category and is fatality-preserving, but "types are
  unchanged" (plural) overstated it.
- **Minors:** the `hasattr(row, "path")` disclosure check was a tautology on a
  frozen dataclass — replaced with an assertion that the filename reaches
  neither the exception text nor the described message; a malformed delete
  record now has the design §8 matrix row it was missing; `_validate_record`'s
  unused `scope` parameter dropped, since it invited a future edit moving
  registry work into phase 1.

**Carried to Task 12 (reviewer observation, no change made):**
`_require_destination` also raises "proposal destination is non-canonical" with
no cause — a record-local condition that aborts the whole listing with
`E-INVALID` and zero rows rather than producing the blocked listing. Faithful,
because the strict loader poisons identically and design §3 assigns destination
validation to phase 2 by name; but it is the one poisoning condition that
aborts rather than blocking. The Task 12 route reviewer should not be surprised.

- **Reviewer verdict:** clean after fix round 1.
- **Mutation evidence:** the three previously-surviving mutants (D2 revert,
  dropped `UnicodeDecodeError` handler, gutted `_render_diff`) each now fail.

---

## Reassessment after Task 9 — Tasks 1-14

Checked the remaining tasks' assumptions against the code that now exists.
**No blocking gap.** Tasks 10-14 are executable as written.

Verified still true: all eleven routes present in `app/main.py`; every class
named in Tasks 10-13's catch tuples still exists and still catches its refined
subtypes (`except CrossScopeError` catches all three, `except DestinationError`
catches all of `UnsafeDestinationPath`'s and `InvalidSourceLeaf`'s subtypes);
the projection's public shape matches what Task 12 assumes (`OutboxListing(rows,
blocked)`, `OutboxRow(proposal, diff, error, can_approve, can_reject)`);
`app/main.py` is untouched apart from one import line, so Tasks 10-13 start from
the state they were written against.

### One finding — a live regression window between Tasks 7 and 10

Task 7 shipped the `htmx-config` `responseHandling` override, which makes 4xx
and 5xx responses swap. Task 10 ships the `StarletteHTTPException` body handler
that replaces framework error bodies. **Between them, the branch swaps raw
Starlette error text into operator targets** — an unmatched URL or wrong method
under `HX-Request` puts `Not Found` / `Method Not Allowed` into `#outbox-list`
or `#diff-{index}`, where before the override it swapped nothing.

This is the exact condition round-six finding I6 identified, and its fix is
already assigned to Task 10. The final state is correct. What is new is that the
intermediate state is worse than the starting state, which matters because every
task on this branch is committed and pushed.

No action beyond recording it: the branch is not merged, Task 10 is next, and
splitting Task 7 to carry a partial handler would duplicate work Task 10 does
properly. **But the branch must not be merged at a commit between Task 7 and
Task 10.** If S6 is ever abandoned mid-flight, the override must be reverted
with it.

### Carried items, consolidated

| Item | Owner | Note |
|---|---|---|
| ~~Non-canonical `dst` aborts the listing rather than blocking it~~ | Task 12 | **DONE** — Task 12's outer handler describes it as `E-INVALID` at 422 instead of letting it escape to the global fallback, an improvement on the state this row recorded. Original note: faithful — the strict loader poisons identically and design §3 assigns destination validation to phase 2 — but it is the one poisoning condition producing an abort rather than a blocked listing. |
| ~~Stopwatch still infers success from transport~~ | Task 11 | **DONE** at `d8d0dbe`. The listener and the literal `htmx:afterRequest` string survive (so the unlisted gate-1 test is untouched); what it keys on moved to the `HX-Trigger` header. |
| ~~`outbox_list.html` still renders the old `props` API~~ | Task 12 | **DONE** — converted to the projection. |
| ~~Design §7 invariant 5's general `hx-vals` scan over `templates/`~~ | Task 13a | **DONE** at `e2428a9`. Closed with a scan, not a list — an `html.parser` sweep plus a fail-closed raw-text backstop. It was defeated twice in review first; see the Task 13 entry. |
| The invariant 5 scan collects `.html` only and does not follow symlinked subdirectories | S7 or later | Task 13a, stated limit. `rglob` defaults `recurse_symlinks=False`; nothing under `templates/` is a `.jinja`/`.j2` or a symlinked subdirectory today. An extension allowlist is the kind of list §7 says to replace with an invariant. |
| A computed `hx-vals` attribute name (`<button {{ 'hx-vals' }}='…'>`) is not detected | S7 or later | Task 13a, stated limit. No contiguous `hx-vals=` exists in the bytes, so neither the parser nor the backstop can see it. Contrived; recorded because the guard's coverage should be stated as a rule, not implied. |
| A route's totality test cannot discover that the route's declaration is incomplete | Task 14 | Task 13 proved this by measurement: two real escapes — a symlinked `_system/products.yaml` and a corrupt delete-proposal record — were invisible to a totality test injecting the **declared** members, while that test stayed green. Task 14 Step 3 is exactly that shape. Drive at least one condition per route from the real filesystem. |
| The fixtures' own `.gitignore` is what makes `git_status_bytes` byte-identical | S7 or later | 4 call sites, all `*/outbox/*.yaml`. Nothing in this repo establishes the real vault's ignore rules, so the state proofs' "nothing else changed" half rests on a fixture convention rather than a verified fact. |
| `add_workspace` and `_count_front_matter` let `UnicodeDecodeError` escape | S7 | Absorbing it would change an existing fatality, which S6 has no authority to do. |
| Reader-guard heuristic gaps (tuple-unpack, walrus, alias rebinding, `Path(...)` wrapping, builtin `open` on a tracked name) | S7 | None present in `app/` today; invariant 4 only refuses silence. |
| The review gate does not bind reviewed content | S7 | Unchanged. Design §12. |
| A post-persistence failure outside `propose`'s declared family carries no `HX-Trigger`, so a genuinely persisted proposal goes uncounted | S7 or later | Task 11, accepted. Unfixable without widening toward the bare `except` Rule 5 forbids, or moving the header onto a path that no longer means "persisted". Undercounting is the safe direction for a Gate 1 measurement — it never inflates. |
| The stopwatch compares the whole `HX-Trigger` value, assuming the plain-string form | S7 or later | Task 11, accepted. Correct against the vendored bundle, which treats a value not starting with `{` as a comma-separated name list; nothing in this repo emits the object form. |
| The invariant 6 alias resolver does not follow imported aliases or computed targets, collects bindings from every scope, and is order-blind | S7 or later | Tasks 10a/11, accepted and stated as a rule rather than a list. All fail-closed; none reds anything in `app/` today. |
| `test_console_routes.py` was amended twice (Task 10's escapee vehicle, Task 10a's) | — | Not scope breaches: `git log -S` places the file in this branch's own Task 10 commit `8df9977`, so design §8's S1-S5 bright line does not reach it. Recorded because a `numstat` reader sees deletions in a test file. |

### Plan drift

`_diff_text` is a new internal helper introduced by Task 9's fix round and is
absent from the plan's File Structure table. Internal to `app/outbox.py`, no
task depends on it by name. Recorded rather than amended.

### Task 10 — Routes, framework surface

- **RED:** 5 failed / 3 passed. The three passing were the "leaves the
  framework's own response untouched" assertions — correct, since that
  behaviour must not change.
- **GREEN:** `entity_scope` raises `EntitySelectionError` to a dedicated
  handler; `RequestValidationError` and `StarletteHTTPException` handlers; a
  global `Exception` fallback; `@console_route` on ten of the eleven
  routes — `pulse` was missed, and nothing caught it. Task 10a below.
- **Suite:** 735 passed. `diff --check` clean.

**Escalation raised and then WITHDRAWN — it rested on a false premise.** Task 10
initially left the five `fragment-only` handlers undecorated, reporting that
decorating `propose` flipped `test_tampered_proposal_form_writes_nothing` to
200 in six cases. The review proved the 200 was **not** caused by the
decoration. It was a Critical defect in the global fallback that the decoration
merely made visible. The implementer read the symptom as the cause.

**Critical — the global fallback returned 200.** `_render_console_error` applied
the fragment severity rule to every caller, so a refusal-severity exception
escaping a route's declared family returned **200 under `HX-Request`** — and
both Console routes are only ever called that way. Two live scenarios were
demonstrated: `OutOfScopeError` escaping `triage` (`E-SCOPE`, 404 → 200) and
`OutboxError` escaping `outbox_reject` (`E-INVALID`, 422 → 200). Monitoring
would have seen success for a defect — verbatim what design §5 forbids. The
handler's own docstring asserted the correct behaviour while the code did the
opposite.

Fixed with a `force_page_status` flag set only by the fallback: the fragment
*body* is kept so the swap lands in the operator's target, and the *status* is
decoupled — the same separation the Rule 4 framework handler already makes.
With it fixed, **all five handlers decorate cleanly and the suite is green with
zero test edits.** The plan's Task 10 table was right as written.

**Two vacuous tests found and fixed.**
- `test_request_validation_..._without_echo` called the handler function
  directly with a synthetic scope. Unregistering the `@app.exception_handler` —
  the production wiring — left all eight tests green. Rewritten to drive the app
  through `TestClient`; verified the mutant now fails.
- Nothing tested the "never 200" requirement. The existing fallback test was
  blind twice over: it injected a `RuntimeError` (`E-UNKNOWN`, attention, 500
  either way) and sent no `HX-Request`. Added a refusal-severity escapee under
  `HX-Request`; verified the mutant now fails.

Its status expectation also moved from 422 to **200**: every route accepting
form data is `fragment-only`, so Rule 5's route-shape-first selection applies
and a refusal returns 200. The plan's `..._422_...` name predated the
decoration, and no page-surface route accepts a form, so 422 describes no
reachable case.

**Verified clean by review:** the Rule 4 / Rule 6 distinction end-to-end —
framework status and `Allow` header preserved, body replaced only under
`HX-Request`, taxonomy genuinely excluded, `HEAD` bodies respected; the
Task 7→10 regression window is closed. `RequestValidationError` cannot reach
`exc.errors()` structurally. No bare `except Exception` in a route body, no
`Exception` in a `catches=` tuple, no instance-specific values.

- **Reviewer verdict:** 1 Critical, 2 Important — all fixed; escalation withdrawn.

---

### Task 10a — invariant 6's guard, and `pulse`'s declaration

Not a plan task. Two gaps a post-Task-10 review found, covered by no task's step
list, closed before Task 11 wrote any code.

**Gap 1 — design §7 invariant 6's structural guard existed in no test file.**
Only its third check (rejecting `Exception` inside a `catches=` tuple) existed,
as a runtime `ValueError` in `console_route`. The other two — a registered
handler carrying no declaration, and a route body containing a bare
`except Exception` — were unguarded. Task 10's step list omits them and Task 14
Step 3 is the injection-totality test, not this. The gap had already bitten:
five handlers went undecorated during Task 10 and nothing would have caught it.

**Gap 2 — `pulse` carried no declaration** while the design's route inventory
says `pulse | unchanged`, so a literal guard fails on it on day one. Declared
`@console_route(catches=(), surface="fragment-only")`.

- **RED:** `assert ['pulse'] == []` — the new guard found the one undeclared
  handler on first run, with its `>= 11` floor passing, so the sweep was live.
- **GREEN:** `pulse` declared. Suite 735 → 738 (three new tests, zero
  deletions; no pre-existing test modified).

**Fix round 1** — reviewer returned *not clean*: 1 Critical, 2 Important,
5 Minor.

- **C1 (Critical) — the body guard was cwd-relative and produced a silent false
  green.** `pathlib.Path("app/main.py")` is relative to the process cwd. The
  enclosing checkout at the repository root holds a *different* `app/main.py`
  on a moving branch with seven `except` clauses, which cleared the `>= 6`
  floor added to prevent exactly this. Proven: with a bare `except Exception`
  live in the worktree's `app/main.py`, the same guard was red from the
  worktree and **green from the main checkout**, having never read the file it
  guards. This is the ledger's own Task 8 finding I1 repeated, with the fix
  standing three files away at `tests/test_console_readers.py:16`. Anchored to
  `pathlib.Path(__file__).resolve().parents[1]`.
- **I1 — both guards were blind to any handler outside `app/main.py`.** Guard 1
  filtered *in* on `__module__ == "app.main"`; guard 2 read only that file. A
  handler defined elsewhere and registered via `add_api_route` — undeclared and
  laundering — passed **both** green. The `app.main` filter's stated purpose is
  to exclude FastAPI's OpenAPI/docs endpoints and the `StaticFiles` mount, not
  to exempt application code; §7's closing rule governs ("delete the list and
  add the invariant that would have caught X"). The filter now excludes
  `fastapi.`/`starlette.` endpoints, and guard 2 scans the union of
  `inspect.getsourcefile()` over the swept endpoints plus the anchored
  composition root.
- **I2 — `pulse | unchanged` is false, and the change was shipped claimed as no
  change and pinned by nothing.** `is_fragment()` short-circuits on
  `surface == "fragment-only"`, so an error escaping `pulse` **without**
  `HX-Request` moved from the full `error.html` page (2755 bytes) to the
  `blocks/alert.html` fragment (233 bytes). Status is unaffected, by two
  independent mechanisms: the global fallback forces the page status, and
  `E-UNKNOWN` is `attention` severity anyway. The new behaviour is
  design-correct — §5's normative rule is "a route with no full-page template
  always uses the fragment renderer", and `pulse` has none; §5's parenthetical
  list omits it, and §7 records that every enumeration in the design was wrong
  in the direction of omission. So the defect was the false claim, not the
  behaviour. The `app/main.py` comment now states what changes and why it is
  correct, and `test_pulse_declaration_selects_the_fragment_surface` pins it.
- **M1** presence-not-identity: guard 1 now asserts `isinstance(..., ConsoleRoute)`.
- **M3** the `seen >= 6` floor had zero headroom and was coupled to a count
  Task 12 will change — replaced with controls (see C2).
- **M2 and M4 recorded, not fixed** — accepted heuristic limits, matching the
  Task 6 AST-guard precedent: an aliased `_LAUNDER = Exception` or a computed
  `except (X,) + _EXTRA` is not detected (neither shape exists in `app/`), and
  a legitimate `except Exception: raise` is flagged, which is design-literal
  over-reach. Nested-function laundering **is** caught. A third limit was
  found in round 3 and accepted on the same terms: FastAPI accepts a
  `functools.partial` endpoint, which is swept and declarable but whose source
  `inspect.getsourcefile` cannot resolve, so it fails the `unresolved` check by
  name. That is fail-closed and documented; if it ever bites, follow
  `partial.func`.

**Fix round 2** — reviewer returned *not clean*: 1 Critical, newly introduced by
the round-1 M3 fix, plus 4 Minor.

- **C2 (Critical) — the positive control did not control the real scanner, and
  was a net regression against the floor it replaced.** It re-implemented the
  detection inline, exercising only `_handled_exception_names` and never
  `_catch_all_offenders` — the traversal that opens the files. Breaking
  `ast.walk(tree)` → `tree.body` with a live `except Exception` in
  `app/main.py` passed **green**. The deleted count floor had caught that exact
  mutation in round 1. This is the branch's signature defect verbatim — Task 8
  round 2's "the test added to prove the trigger passed while the trigger was
  dead". Replaced with a positive **and** negative control written to
  `tmp_path` and driven through `_catch_all_offenders` itself. Verified: the
  broken-traversal mutation is now red.
- **N2** the dead `control` variable is gone, subsumed by the C2 fix.
- **N3** an endpoint whose source cannot be resolved is now reported by name as
  an unscannable route instead of an `AttributeError`/`TypeError` trace.
- **N4** the guard scans every file owning a registered endpoint, not only the
  composition root, so it is renamed
  `test_no_route_source_file_launders_with_a_catch_all`.
- **N5** blank-line seam normalized to the file's two-line convention.

**Mutation evidence.** Every guard proven non-vacuous, each mutant restored
byte-identical:

| Mutation | Guard | Result |
|---|---|---|
| strip `pulse`'s declaration | 1 | red |
| strip `shell`'s declaration | 1 | red |
| launder `outbox_reject` with `except Exception` | 2 | red |
| the same laundered file, cwd = the enclosing checkout | 2 | red (was **green** — C1) |
| undeclared laundering handler in another module via `add_api_route` | 1 and 2 | both red (both were **green** — I1) |
| flip `pulse`'s `surface` to `"page"` | pulse | red |
| break the traversal **and** launder `outbox_reject` | 2 | red (was **green** — C2) |
| break the endpoint-source collection loop | 2 | red (was **green** — MN2) |

- **Suite:** 738 passed (735 + 3). `git diff --check` clean. **Zero deletions
  in `app/` or `tests/` across the task span (`463955d..HEAD`)**, so no
  pre-existing test line was ever touched. Stated precisely because an earlier
  draft of this entry claimed "zero deletions in any file", which is false and
  which a reviewer running `git show --numstat` would catch: the span deletes
  six documentation lines, and `3c75a55` deletes 22 lines from
  `tests/test_console_invariants.py` — every one of them a line `3c0eee0` had
  itself added.

**Process deviation, recorded.** A concurrent session committed `3c0eee0` in
this worktree and swept this task's then-uncommitted, un-re-reviewed working
tree into it, so the C1/I1/I2 fixes were committed and pushed carrying the
still-open C2 Critical. The C2 fix was therefore committed **before** its
review round rather than after, because the defect was already live on the
pushed branch and leaving the fix uncommitted would have left it there. Review
of the committed state follows. Two sessions writing one branch is the hazard;
one session per step is the rule.

### The ruling on `test_tampered_proposal_form_writes_nothing`

Escalated to the human and **granted**, since it breaches the standing "exactly
two pre-existing tests may change" constraint.

Proven before asking, in an isolated copy at HEAD rather than by argument: with
`propose` catching its declared family, all six parametrized cases return 200,
and with only the status line changed all six pass — `HEAD` unchanged, entity
bytes unchanged, no proposal written; full `test_app.py` 28 passed. So the
collision is a Rule 5 consequence (fragment-only + refusal → 200), not the
global-fallback defect that caused the withdrawn Task 10 escalation.

The ruling: one new regression-table row; **status expectation only**, `>= 400`
→ `== 200`; every state and isolation assertion preserved verbatim; the test
**gains** an observable-refusal assertion (`role="alert"` plus the described
code and message) so that dropping the status check strengthens rather than
weakens it; design first, plan second, test during Task 11. The six parameters
are six cases of one declared presentation regression, not six new exceptions.

`3c0eee0` applied the design and plan halves but omitted the observable-refusal
assertion; that clause was added to both documents here. Task 11 owns Step 3a.

**Fix round 3 — reviewer returned CLEAN.** No Critical, no Important. Two
Minors, both closed rather than carried:

- **MN1 — the ledger overstated its own evidence.** "Zero deletions in any
  file" was false. Corrected above to the true and equally strong claim. This
  branch has twice shipped a ledger asserting something it had not established;
  a third would have been the same failure in the document whose whole purpose
  is to stay checkable.
- **MN2 — the endpoint-source collection loop was the one uncontrolled stage.**
  The C2 controls prove the *scanner*; nothing proved the *collection*. With
  the loop broken, a laundering handler in a module other than the composition
  root escaped both guards. The design-required scope was never at risk —
  `_COMPOSITION_ROOT` is seeded unconditionally and asserted — so this was
  defence-in-depth on the widening I1 added. Closed by extracting
  `_endpoint_source_files()` and controlling it with a throwaway module and a
  scratch `FastAPI()`, in the same pattern as the C2 fix. Verified: breaking
  the loop is now red.

Also applied from round 3's observations: offender labels are repo-relative
rather than basenames, so two scanned files sharing a name stay
distinguishable; and the control fixtures moved into a `_controls/`
subdirectory so the synthetic vault root stays a clean vault.

The reviewer independently reproduced all seven mutation-table rows on the
committed tree, broke `_catch_all_offenders` three further ways not previously
tried — all red — and verified the ledger's checkable claims by measurement
rather than by reading: the enclosing checkout's `app/main.py` has seven
`ExceptHandler` nodes by AST count; `test_app.py` collects 28 tests;
`test_tampered_proposal_form_writes_nothing` is at line 393 with six cases and
exactly the three state proofs the ruling preserves.

- **Reviewer verdict:** clean after fix round 3 (three rounds: 1 Critical +
  2 Important + 5 Minor, then 1 Critical + 4 Minor, then clean with 2 Minor).

---

### Task 11 — Routes, triage and propose

- **RED, measured rather than asserted.** Against an untouched HEAD tree built
  with `git archive` and given the **final** test files:

  | Selection | Result |
  |---|---|
  | `tests/test_console_routes.py` | 8 failed, 11 passed |
  | the same plus `test_app.py::test_tampered_proposal_form_writes_nothing` | 14 failed, 11 passed |

  Signatures: `app.destinations.InvalidModule: destination module is not
  active` propagating uncaught through `propose`, and `assert 422 == 200` on
  the six tampered-form cases.

  **Eight of the nine** new route tests are red at HEAD. The ninth,
  `test_propose_success_fragment_also_honours_the_innerhtml_shape`, is green at
  HEAD **by construction** — added during a fix round to close review finding
  M1, asserting a shape the unfixed implementation already satisfied. It never
  had a RED phase and is pinned by mutation instead (wrapping
  `blocks/diff.html` in `id="diff-0" x-data="{}"` reds it). The other two
  fix-round tests, for the fallback regression and the post-persistence branch,
  are both red at HEAD.

  An earlier draft of this entry said "11 failed / 11 passed" and "all six new
  tests confirmed red". The second went stale once fix rounds took the count to
  nine, and it asserted a RED phase one test never had. The first is **not
  reproducible against any revision** and is recorded as an error: review
  measured the intermediate six-test revision at HEAD as 6 failed / 10 passed,
  so the figure matches neither that nor the shipped file. A first attempt at
  this very correction explained it away as "the implementer's count against an
  intermediate revision" — itself an unmeasured claim, and caught in the next
  round.

  **This is the fourth time on this branch a ledger has stated a number that
  was asserted rather than measured, and the fifth counting the explanation.
  Review caught every one.**
- **GREEN:** `triage` rows carry `(item, classification, destination, error)`
  with the error **described** in the composition root; `propose` catches its
  declared family in two phases and emits `HX-Trigger:
  console:proposal-persisted` only once `propose_classification` has returned.
- **Suite:** 747 (738 + 9). `diff --check` clean.

**The stopwatch followed the ledger's binding preflight resolution literally.**
The `htmx:afterRequest` listener and that literal string survive — so the
unlisted `test_triage_screen_has_gate1_timing_instrument` is untouched — while
what the listener *keys on* changed from `e.detail.successful` to the
`HX-Trigger` response header compared against a server-rendered constant. A
refusal never reaches the line that sets the header, so it cannot increment the
Gate 1 count.

**Fix round 1** — reviewer returned *not clean*: 2 Critical, 3 Important,
4 Minor.

- **C1 (Critical) — `triage` relied on the global fallback for a member of its
  own declared family.** It declares `DestinationRegistryError` but the per-row
  clause had been narrowed to `(DestinationError, CrossScopeError)`, and there
  was no outer handler, so a broken registry escaped the route entirely. A
  fallback spy on the exact fixture returned `FALLBACK CALLS
  ['DestinationRegistryError']`. Two consequences: Task 14 Step 3 would have
  been red, and — worse — Starlette's `ServerErrorMiddleware` re-raises after
  handling, so **every triage request against a broken registry logged an
  unhandled-exception traceback**, the raw server fault the S6 Objective
  forbids, for a first-class described condition. Design §3's Phase-2 reasoning
  justifies aborting the *page*; it does not justify aborting out of the
  *route* — its own wording is "the route renders the described condition".
- **C2 (Critical) — the same escape for `CrossScopeError`, on the realistic
  path the new tamper test patched around.** `read_inbox` raises
  `RedirectedPathError` outside the per-row guard, and
  `test_triage_row_with_symlinked_receipt_shows_e_tamper` stubs `read_inbox`
  precisely to bypass it, so nothing exercised the unpatched condition — design
  §2's "most reachable redirection site in the application". Pre-existing in
  origin, but Task 11 is the task assigned to make `triage` handle
  `CrossScopeError`, and the new test concealed it.
- Both closed by extracting `_triage_page` and giving the route one outer
  handler over `_TRIAGE_CATCHES`, a module constant feeding both the decorator
  and the `except` so they cannot drift. Verified on the real filesystem path,
  not just the injected one: an unpatched symlinked receipt now gives
  `status 409, E-TAMPER, fallback reached: []`.
- **I1 — the stopwatch's client half was pinned by token presence only.** Two
  mutations kept all 744 green: flipping `===` to `!==`, which makes the counter
  increment on **refusals only** — verbatim the Gate 1 corruption design §5
  exists to prevent — and drifting the header name, which makes it never
  increment at all. Now the whole rendered comparison is asserted.
- **I2 — `propose`'s post-persistence branch was entirely untested.** Deleting
  it left the suite green, so the load-bearing half of its own comment was
  unpinned in both directions. Now covered by a test asserting alert +
  `HX-Trigger` + exactly one new proposal on disk; the reviewer confirmed the
  disk assertion is load-bearing by making the route roll the write back — the
  behaviour design §8 forbids — which turns it red.
- **I3 — a Task 10 test rested on a false premise and structurally blocked the
  fix.** `test_refusal_severity_escapee_keeps_page_status_under_htmx` injected
  `OutOfScopeError` as "undeclared by this route's catch family", but that is a
  `CrossScopeError`, which `triage` declares. It passed only *because* of C2.
  Vehicle swapped to `OutboxError` (E-INVALID, refusal, 422). Not a scope
  breach: `git log -S` places the test in this branch's own Task 10 commit
  `8df9977`, so §8's S1-S5 bright line does not reach it — recorded here
  because a `numstat` reader sees deletions in a test file.
- **M1** the swap shape was asserted only on the refusal fragment, not on
  `blocks/diff.html`, which is what actually swaps in the normal flow — now
  covered. **M2** the alert include was guarded by `{% if destination %}`
  rather than by `error`; changed to `{% elif error %}`. **Applied, not
  proven** — the state is unreachable while `resolve_classification_destination`
  either returns a truthy dataclass or raises, so reverting it leaves the suite
  green.

**Fix round 2** — reviewer returned *not clean*: 1 Important, 4 Minor, no
Critical. All round-1 findings confirmed genuinely fixed by the reviewer's own
mutations, including two narrowings of the new outer catch that each go red.

- **The comment asserted the opposite of the fix.** A comment in
  `test_triage_page_with_broken_registry_shows_e_config_page` described the C1
  escape — "reaches the global fallback, which ServerErrorMiddleware re-raises"
  — as intended contract, three tests above the test that now forbids it, with
  a `raise_server_exceptions=False` that was no longer needed. On a branch whose
  ledger has twice shipped a claim it had not established, a comment stating
  removed behaviour as contract is the same failure in the same place: a future
  reader could restore the escape on its authority. Rewritten; the opt-out
  dropped.
- **The accepted "aliased catch-all" blind spot expired the moment Task 11
  created the shape.** Task 10a recorded it as acceptable *on the explicit
  grounds that no such shape existed in `app/`*; `except _TRIAGE_CATCHES` is
  the first. The reviewer laundered the route with `_LAUNDER = Exception` while
  leaving the decorator intact and the AST guard stayed **green**. Rather than
  weaken the ledger's justification, the guard grew: `_exception_aliases`
  now resolves names bound to an exception class or tuple, and the
  positive control gained aliased clauses so the resolution itself is
  controlled. Both mutations now red.

  Round 3 then showed that first version closed one spelling and left five
  one-line variants open, so the guard was widened rather than the claim
  softened: it now walks `Assign`, `AnnAssign` and `NamedExpr` anywhere in the
  module rather than only `tree.body`, unions tuple targets instead of pairing
  them positionally — over-approximating, the fail-closed direction — and
  resolves to a bounded fixpoint so an alias of an alias closes. Probed
  directly: alias-of-alias, `AnnAssign`, walrus, tuple target, alias bound
  inside a module-level `if`, chained `A = B = Exception`, and an alias nested
  in an `except` tuple are **all flagged**; a legitimately typed
  `E = ValueError` is not.

  **Coverage stated as the rule the code implements, not as a list of
  unsupported shapes** — an enumeration here would be wrong in the direction it
  cannot see, which is the §7 failure this whole guard exists to avoid. What is
  resolved: `Assign`, `AnnAssign` and `NamedExpr` bindings, with `Name`
  targets or `Tuple`/`List` targets **at any nesting depth**. What is not: every other binding form — a
  `for` target, a `with ... as`, an `except ... as`, an import — and every
  non-trivial value expression, including a ternary and a computed
  `except (A,) + _EXTRA`. A first draft of this paragraph listed two residuals;
  review found three more (ternary, `for` target, `with ... as`), which is
  exactly why it is now a rule.

  Two accepted false positives, both fail-closed: the resolver **collects
  bindings from every scope** — module level, function bodies, class bodies,
  comprehensions — with no scope model, so a function-local name shadows an
  unrelated module-level one; and it is **order-blind** — every binding for a
  name unions into one set, so position is irrelevant and a name bound twice
  carries both. It also fires harmlessly on real code today
  (`destination, error = None, None` binds both names to `{""}`). None of these
  reds anything in `app/`, and all fail red rather than green.

  The widening was itself briefly uncontrolled, and the mutation battery
  caught it rather than review: restricting the walk back to `tree.body`
  passed **green**, because every alias in the control fixture was already
  top-level. The fixture gained an `if True:`-nested binding, and each of the
  three widening stages is now individually pinned — restricting the walk,
  dropping `AnnAssign`/`NamedExpr`, and removing the fixpoint each turn it
  red. A control that does not exercise a stage does not control it; that is
  the same lesson as Task 10a's C2, found a second time in the same file.

  And a third time, one level down: the claim "each of the three widening
  stages is now individually pinned" was itself false when written. `AnnAssign`
  and `NamedExpr` shared one branch with only an `AnnAssign` clause in the
  fixture, so dropping the walrus half alone stayed green; and the tuple-target
  union was named as a widening while no tuple target existed in the fixture at
  all. The fixture now carries one clause per stage — literal catch-all, direct
  alias, alias-of-alias, `AnnAssign`, `NamedExpr`, a binding outside
  `tree.body`, a tuple target, and a **nested** tuple target — and each
  widening stage is red under its own mutation. Six mutations, each turning
  the assertion red: four isolate a single asserted line (drop `NamedExpr`
  alone → 39; drop `AnnAssign` alone → 29; neutralise the target union → 44;
  remove the fixpoint → 24), restricting the walk reds two (34 and 39, since
  the walrus binding sits inside a module-level `Assign` and so is not in
  `tree.body` either), and removing resolution entirely reds every
  alias-dependent line. An earlier draft claimed all six isolated exactly one
  stage; review measured it and two did not.

  Round 5 then found the rule statement was **broader than the code**: a
  nested target such as `_A, (_B,) = Exception, (Exception,)` binds a working
  runtime catch-all one level down, and the collector descended only one
  level, so `except _B` escaped while the rule said `Tuple`/`List` targets
  were resolved. Fixed by recursing (`_target_names`) rather than by narrowing
  the words, so the broad claim became true; a nested clause was added to the
  fixture and an eighth asserted line, because an unpinned recursion would
  have been the same gap a third time.
- **An unrecorded status change, now pinned.** A refusal-severity declared
  member escaping the row loop returns **200 under `HX-Request`**, where the
  fallback's `force_page_status` previously forced 404. Design-correct under
  Rule 5, and `force_page_status` exists only because a fallback response is a
  defect rather than an expected refusal — but it is a live status change on the
  same class the I3 test used to assert 404 for, so it is asserted rather than
  left to be discovered.
- **A docstring overstated its reach.** The symlinked-receipt test claimed the
  condition is described "per-row, without the page failing". For a receipt
  already symlinked on disk the page *does* fail at 409, because `read_inbox`'s
  `_require_real_receipt` aborts the whole listing — one symlinked receipt hides
  every valid row. Corrected to state that the page-level 409 is the reachable
  case and the per-row branch is the TOCTOU race the stub isolates.

**Mutation evidence.** Every behavioural claim broken, confirmed red, restored
byte-identical (`shasum -a 256 -c`), using explicit backups — never
`git checkout`, which during implementation reverted the whole task and had to
be reapplied by hand:

| Mutation | Test | Result |
|---|---|---|
| delete `triage`'s outer `try`/`except` | fallback regression | red |
| narrow the outer catch to `(DestinationRegistryError,)` | fallback regression | red |
| narrow the outer catch to `(CrossScopeError,)` | fallback regression | red |
| `===` → `!==` in the stopwatch (count refusals only) | stopwatch | red (was **green**) |
| drift the `HX-Trigger` header name | stopwatch | red (was **green**) |
| delete `propose`'s post-persistence `try`/`except` | post-persistence | red (was **green**) |
| roll the persisted proposal back on render failure | post-persistence | red |
| wrap `blocks/diff.html` in `id="diff-0" x-data="{}"` | success-fragment shape | red |
| alias-launder `triage` with `_LAUNDER = Exception` | invariant 6 body guard | red (was **green**) |
| remove the guard's alias resolution | invariant 6 body guard | red |

**Step 3a — the authorized third regression-table row.**
`test_tampered_proposal_form_writes_nothing`: the sole deletion across
`tests/test_app.py` is `assert response.status_code >= 400`, replaced by
`== 200`. The three state proofs are **byte-identical**, verified by diffing
them against `HEAD`. It gained `role="alert"`, the expected code (`E-DEST` for
five cases, `E-INVALID` for the `entity` case — the reviewer confirmed each
renders exactly one code, for the right reason), the exact
`_CODES[...].message`, and a no-echo loop proven non-vacuous by making the
route echo every submitted value, which turns all six red.

**Accepted limits, recorded not fixed** (the reviewer independently agreed
recording is correct for both):

- A post-persistence failure **outside** `propose`'s declared family reaches the
  fallback carrying no `HX-Trigger`, so a genuinely persisted proposal goes
  uncounted. Unfixable without either widening toward the bare `except` Rule 5
  forbids or moving the header onto a path that no longer means "persisted".
  Undercounting is the safe direction for a Gate 1 measurement — it never
  inflates.
- The header comparison assumes the plain-string `HX-Trigger` form. Correct
  against the vendored bundle, which treats a value not starting with `{` as a
  comma-separated name list; nothing in this repo emits the object form.

---

### The ruling on `test_concurrent_outbox_requests_keep_entity_diffs_isolated`

Escalated to the human during Task 12 and **granted**. Recorded here at the time
of the ruling — the previous ruling was recorded only after the fact, and the
Task 12 reviewer, seeing the design amended mid-review with no ledger entry,
correctly read it as a session self-amending the normative document to
authorise its own change. The amendment's content was right; its provenance was
invisible. **A ruling is not authority until it is written down.**

**The finding.** The test forces two requests to overlap by monkeypatching
`main.load_proposals` onto a `threading.Barrier(2)`. Task 12 moves the outbox
routes onto `project_outbox`, which by design §3 and Task 9's own tested
invariant **never** calls `load_proposals`. Measured, not argued:

| Tree | Barrier calls |
|---|---|
| Task 12 working tree | **0** |
| `HEAD`, same instrumented test | **2** |

Deleting the monkeypatch line outright still leaves the test green. The barrier,
the `threading` import, `real_load` and the whole closure are inert. What
remains is two GETs that may run strictly serially, asserting a property another
test already covers without concurrency.

`app/main.py` also carried a dead `load_proposals` import with a seven-line
`# noqa` explaining that it existed only so `monkeypatch.setattr` would not
`AttributeError`. That import is what hid the defect: without it the branch goes
red the instant Task 12 lands, pointing straight at the hollowing.

**Why this is worse than a test that changes.** A changed test is reviewable —
`git diff`, `--numstat`, the deletion count and the suite total all show it. A
hollowed test is invisible to every gate this branch relies on: nothing in the
diff, no deletion, suite still 758. It is design §7's own diagnosis — "wrong in
the direction of omission, which is the one direction a written list cannot
detect" — turned on the regression table itself.

**The ruling.** The regression table is an **explicit allowlist, not a numerical
cap**; the count is an outcome, never the constraint. Keeping an inert isolation
test would violate the stronger S1-S5 preservation constraint, so preserving the
proof wins. In order: add the fourth design row, update the plan, re-point the
monkeypatch from `load_proposals` to `project_outbox`, preserve the isolation
assertions verbatim, add an explicit `hits == 2` assertion so the barrier can
never silently stop firing again, delete the dead import, record the ruling.

This **restores** the original proof rather than weakening or redefining it —
the first regression row where that is true. The `hits == 2` assertion is the
general countermeasure: any future refactor that moves the patched symbol off
the request path now fails loudly instead of passing empty.

---

### Task 12 — Routes, outbox

- **RED, re-measured after the fix rounds changed the file.** `uv run pytest
  tests/test_console_routes.py -q` against a `git archive HEAD` tree given the
  **final** test file: **18 failed, 20 passed**. Exactly **one** of the
  seventeen new cases passes at HEAD — `test_outbox_screen_unblocked_listing_
  keeps_controls`, a declared sanity control — and it is pinned by mutation
  rather than by a RED phase.

  An earlier draft of this entry said "9 failed, 21 passed … two of the eleven
  new tests pass at HEAD". That was the round-1 measurement, taken when the
  file carried eleven tests, and it went stale the moment fix rounds took it to
  seventeen — while the sentence pinned it to "the final test file", which is
  what made it false. The shape test it named as green-at-HEAD now has a
  genuine RED phase, because the N1 fix extended it onto `main.project_outbox`,
  a symbol HEAD does not have. **This is the same defect the Task 11 entry
  documents about itself, one task later in the same document: a number
  asserted rather than re-measured after the thing it describes changed.**
- **GREEN.** `outbox_screen`, `outbox_approve`, `outbox_reject` render
  `project_outbox`; `_outbox_rows` is the single place `describe()` is called
  on `OutboxRow.error`, keeping the taxonomy out of `app/outbox.py`.
- **Suite:** 747 → 766. `diff --check` clean. `app/outbox.py` changed only by
  docstring.

**The Task 11 trap was present here and was closed proactively.** All three
routes declared `(OutboxError, CrossScopeError, DestinationRegistryError)`
while `outbox_screen` had **no** `try/except` at all and the POSTs caught only
`OutboxError`, so two declared members escaped to the global fallback. A
`_OUTBOX_CATCHES` constant now feeds both the decorator and every `except`
that answers it — `outbox_screen`'s own, and the two clauses inside each POST
route's response helper. The POST routes themselves carry no `except`: round 4
deleted the outer guards, recorded three paragraphs below. The reviewer swept **90 combinations** (10 exception types × 3 patch
targets × 3 routes) with a fallback spy: zero hits, with a positive control
proving the spy fires.

The implementer also found a **third** escape shape beyond the brief: after a
route handles its own action error, `_outbox_list`'s `project_outbox` re-read
can independently raise the same family. `_outbox_list_error` answers that
while still reproducing `#outbox-list`, because degrading to `blocks/alert.html`
would strand the `outerHTML` swap target.

**Review round 1 — 2 Critical, 7 Important.**

- **C1 — an S1-S5 isolation proof was hollowed out.** See the recorded ruling
  above. Measured 0 barrier calls against 2 at HEAD.
- **C2 — `_outbox_list_error`'s entire purpose was pinned by nothing.**
  Replacing its body with a plain `alert.html` render — the exact failure its
  docstring names — left the **full suite green**. A guard whose distinguishing
  property is untested is a comment, not a guard. This is Task 10a's C2 shape a
  third time.
- **I1** the double-failure path discarded the action's own refusal, rendering
  only the listing's error and taking its status — inverting design §5's "the
  status is the refusal's". **I2** blocked-state POSTs rendered two
  byte-identical `E-UNREADABLE` notices where §3 promises one. **I3** the
  double-failure fragment rendered "No pending proposals" while a proposal sat
  on disk — the Objective forbids a screen that hides the condition it protects
  against. **I4** three template branches (per-row error include, unreadable-row
  markup, the destination span) were each deletable at 758 green. **I5** the
  totality test patched both sides with the same exception and so could not
  tell which handler answered. **I6** `preview_diff`'s docstring asserted
  removed behaviour as contract — Task 11's fix-round-2 finding repeated.
- **I7 — design §7 invariant 5's general `hx-vals` scan over `templates/`
  exists nowhere and is owned by no task**, exactly as invariant 6 was before
  Task 10a. Task 13's list names only the two known offenders, which is the
  two-row list §6 says is not enforcement. Carried to a Task 13a.

**Review round 2 — 3 Important, no Critical.** All one shape: a correct fix
whose *sibling path*, *now-dead branch*, or *replacement comment* was not held
to the standard of the thing it fixed.

- **The I2 fix was applied in `_outbox_list` and re-created in
  `_outbox_list_error`** — on the single scenario that helper's own docstring
  names ("a broken registry that refuses both"). The distinct-exception test
  written for I1 is structurally blind to it, since choosing two different
  codes is what lets it prove the status keying. The reviewer's 90-combination
  sweep found it without contrivance. Suppression now applied in both
  renderers, keeping the action's refusal; a same-code parametrized test pins
  it.
- **The outer route guards became unreachable, and their docstrings claimed
  otherwise — so they were removed.** After I1 wrapped every statement of the response helpers, the
  only member that can reach the outer `except` is one raised by
  `_outbox_list_error` itself — which the handler then calls a second time with
  the same failing inputs. Deleting either outer guard was red in round 1 and
  **green** in round 2.

  Round 2 kept them and rewrote the docstrings; round 3 showed that
  justification was itself false in both halves — invariant 6's guards check
  that a route *declares* a family and contains no catch-all, **not** that its
  body catches what it declares; design §7 assigns that to injection, i.e.
  Task 14 Step 3, a runtime test. Documenting had required inventing a reason,
  which is the same defect one level up. They are now **deleted**. Verified: a
  54-request sweep (6 declared-family types × 3 patch targets × 3 routes) with
  a fallback spy returns **0 global-fallback hits** without them, and both
  remaining inner guards are load-bearing. Against the **full suite**
  (`uv run python -m pytest -q`), deleting the re-render guard from both routes
  reds **7**; deleting the action guard from both reds **19**.

  An earlier draft gave "4" and "10" — `tests/test_console_routes.py`-only
  figures for a *single* route, in a sentence whose subject was both guards on
  both routes. Two numbers, two different selections, neither stated. The
  substantive claim was true and confirmed six ways; the numbers attached to it
  were not. **A count is meaningless without the selection that produced it.**
- **The I6 fix replaced one false claim with two.** It asserted `propose`
  previews "a proposal whose source may not exist yet (e.g. a reclassification
  proposed before the receipt lands)" — impossible, since
  `propose_classification` refuses a missing receipt before persisting, so only
  a TOCTOU deletion can race it. And it claimed the unfresh-source test now
  exercises "row-level `E-MISSING`/`E-STALE`" — there is **no** row-level
  `E-STALE`: a stale row carries no error, keeps `can_approve`, and renders a
  normal diff, because staleness is a revalidation concern rather than a
  read-boundary one. Both corrected to what was measured. At least the **sixth**
  unmeasured claim shipped in a comment or ledger on this branch — the Task 11
  entry had already reached five, and I6 was two false claims in one docstring.
  Review caught it again.

**A self-caught vacuity worth recording.** The fix round's first
unreadable-row assertion used `"could not be read as a proposal" in body` —
which `E-UNREADABLE`'s own message also contains, so the mutation passed green.
Replaced with the row's CSS class. The reviewer then swept for that shape by
stripping `error.code` and `error.message` from `alert.html` in turn and
diffing which tests survived: every code and message assertion goes red, and the
three tests that ignore both sweeps assert no codes at all. That was the only
instance.

**Mutation evidence.** Every round-1 mutation that was green is now red:
`_outbox_list_error` → `alert.html`; delete the per-row error include; delete
the unreadable-row markup; blank `prop-route`; delete the concurrency
monkeypatch; re-point the barrier at a symbol nothing calls. Plus the fixes'
own: remove the I2 duplicate suppression (both renderers), remove
`listing_unavailable`, drop `action_error`, key `status_for` on the listing
instead of the refusal, flip `can_approve` in `project_outbox`'s phase-3 error branch (red, but five of the seven it reds are pre-existing Task 9 projection tests, so it is only partly Task 12's evidence).

**Regression rows used:** row 1 (`test_approval_route_transaction_error_...`,
the alert-absent assertion) and row 4 (the concurrency monkeypatch target).
`tests/test_app.py` carries exactly three deleted lines — row 1's assertion and
row 4's two, since retargeting a monkeypatch necessarily touches the capture
line as well as the `setattr`. The four state proofs and the isolation
assertions are byte-identical to `HEAD`.

**Recovered from a self-inflicted loss.** While probing whether the barrier
still fired, the coordinator ran `git checkout -- tests/test_app.py` and
destroyed Task 12's uncommitted row-1 edit; no backup existed. It was
reconstructed by hand and the reviewer verified the reconstruction is faithful —
one deletion, four state proofs byte-identical, nothing else shifted. This is
the **second** recorded instance of `git checkout` destroying uncommitted work
on this branch; the first is in the Task 11 mutation-evidence note. An earlier
draft said "third", and the correction of it was reported as applied while the
edit had silently failed — see the process note at the end of this entry.
**Back up with `cp`, restore with `cp`.**


**A process note this task earned twice.** Two ledger corrections were written
with `str.replace()` and no assertion, so both silently matched nothing, left
the original text in place, and were then *reported* as applied. That is an
unmeasured claim about one's own edits — the same failure this entry is largely
about, one level up. Every replacement in the final pass asserts its anchor is
present and unique, and the result is grepped afterwards. **Do not use an
unasserted `replace()` on a file whose correctness is the point.**

---

### Task 13 + Task 13a — Routes, registry; and invariant 5's template scan

- **RED, measured** against a `git archive HEAD` tree given the final test
  files, selection stated per line: `tests/test_console_routes.py` → **7
  failed, 39 passed** (seven of the eight new route tests red at HEAD);
  `tests/test_console_invariants.py::test_no_template_hand_builds_hx_vals` →
  red, naming both real offenders `['templates/blocks/delete_impact.html:14',
  'templates/registry.html:24']`; `tests/test_app.py` → **1 failed, 27
  passed**. The eighth route test, `test_propose_persistence_outcome`, is green
  at HEAD by construction and pinned by mutation instead — rolling the
  persisted proposal back reds it.
- **Suite:** 766 → 775 (implementation) → 780 (fix round 1) → **781**.
  `app/registry.py` **byte-identical to HEAD throughout**, so `execute_delete`'s
  signature and behaviour are untouched as the constraint requires.

**Rule 8 is closed, and closure was proven by exploitation rather than by
reading.** Fifteen hostile slugs — raw `"` and `'`, `&quot;`/`&#34;`/`&#x22;`,
backslash, `"`, a nested `{"id": …}`, newline, `</script>`,
`  ` — driven through the real products and preview routes, parsed
with `html.parser` then `json.loads`: each round-trips to exactly one key
carrying the original value. Jinja's `tojson` emits `<>&'` as
`< > & '`, defeating both attribute breakout and browser
entity re-decoding. Against the pre-fix template the same probe yields
`{'slug': 'a', 'id': 'INJECTED'}` — **the vulnerability was real**, and it was a
preview/approve mismatch, not a display bug.

**The Task 11/12 trap, third occurrence, quantified.** A 30-cell sweep (6
exception types × 2 patch targets × 3 routes) with a global-fallback spy and a
positive control returning `['RuntimeError']`: **22 escapes at HEAD, 0 after
Task 13.** At HEAD `registry_products` and `registry_delete_preview` had no
`try` at all and leaked *every* injected type including their own declared
`RegistryError`; `registry_delete_execute` leaked all four non-`RegistryError`
types.

**Two escapes injection-only totality can never see.** Both were found by
driving the real filesystem, and both are the reason the first fix looked
complete and was not:

- A **real symlinked** `_system/products.yaml` → `FALLBACK REACHED:
  ['RedirectedPathError']` at 409, and under a default `TestClient`
  `RE-RAISED: RedirectedPathError` — `ServerErrorMiddleware` logging a traceback
  for a first-class described condition, the raw server fault the Objective
  forbids. Cause: `_REGISTRY_PRODUCTS_CATCHES` omitted `CrossScopeError` while
  the sibling constant three lines below included it.
- A **real corrupt** delete-proposal record → `FALLBACK REACHED:
  ['UnreadableProposalRecord']` at 422.

The route's own totality test was green throughout, because it injects the
**declared** members — which is exactly what invariant 6 and Task 14 Step 3 do.
**A totality test built from a route's own declaration cannot discover that the
declaration is incomplete.** Only the real condition can.

**`UnreadableProposalRecord` — adjudicated as Task 13's, no human ruling
sought.** It pre-dates Task 13 (`execute_delete`'s first statement is already
`get_delete_proposal`, `app/registry.py:375`, so the new pre-execute call
widened the window by nothing), but "pre-existing" is not the test this branch
applies. Both functions are `@structured_reader(category="proposal")` and
invariant 4 *requires* that category to raise it — it is the designed failure
mode of the calls the route makes, not a foreign type. Design §5 performs the
identical widening for `triage` and the outbox routes and calls it "required,
not optional". Nothing in the design pins a route's catch tuple the way it pins
the regression table, so widening one is ordinary implementation. And deferring
would have made it permanently invisible: Task 14 injects only declared
members, invariant 6 checks a route declares *something* rather than enough.

**Task 13a — invariant 5's scan, broken twice in review before it held.** Each
rewrite closed the spelling it had been shown and left the adjacent one open:

| Round | Guard | Defeated by |
|---|---|---|
| 1 | `re.compile(r"hx-vals='([^']*)'")` over raw bytes | double-quoted, whitespace around `=`, newline before value, unquoted, uppercase — two of them live exploits |
| 2 | `html.parser`, `name == "hx-vals"` | **`data-hx-vals`** — a *regression*, since the byte-sequence regex had caught it, and htmx honours it (`ee(e,t)||ee(e,"data-"+t)` in the vendored bundle) |
| 2 | collection control | `rglob("*/*.html")` and `rglob("blocks/*.html")` — the control directory was itself named `blocks`, so the walk was pinned in one direction only |
| 3 | — | held |

Round 3 replaced pattern-widening with a **fail-closed backstop**: a raw-text
pass over the whole source flags any `(?:data-)?hx-vals\s*=` occurrence the
parser's per-tag token search cannot explain. That inverts the failure mode —
anything unexplained is an offender by default — and it catches the three Jinja
shapes (`{% if %}`, `{% for %}`, `{% macro %}`) the parser structurally cannot
see, since it only observes well-formed start tags. Nineteen of twenty hostile
shapes are flagged; the one miss is a computed attribute name
(`<button {{ 'hx-vals' }}='…'>`), where no contiguous `hx-vals=` exists.

**One genuine fail-open, closed in the final pass.** Jinja binds a filter
tighter than `if`/`else`/`and`/`or`, so `{{ raw if y else v | tojson }}` parses
as `raw if y else (v | tojson)`: when `y` is truthy the output is `raw`,
autoescaped as **HTML** rather than JSON, and the browser decodes `&#34;` back
to a delimiter inside the attribute. A textual split cannot model Jinja
precedence, so a bare conditional or boolean operator in the left-hand
expression is now rejected outright.

**Four rules that were correct but unpinned, found by the round-3 reviewer and
now red under mutation:** the "exactly one `|`" count (`!= 2` → `< 2` passed
green), the `tojson` filter identity (`fullmatch` → substring passed green),
the Jinja-precedence rejection, and `_outbox_new_path_in_entity`'s
path-versus-name comparison — that last one left the whole of
`tests/test_console_routes.py` green at 51 passed when reverted.

**The isolation helper compared names, not paths.** `_outbox_new_path_in_entity`
accepted `<vault>/beta/alpha/outbox/x.yaml` — a write that escaped the bound
entity while having an ancestor *named* `alpha`. The persistence tests caught it
only through `git_status_bytes`, and only because `.gitignore`'s
`*/outbox/*.yaml` is single-level. Luck, not a guarantee. Now a resolved-path
comparison with its own `pytest.raises` control.

**Regression row 2 — the last of the four.** Exactly one deleted line in
`tests/test_app.py`, the raw-string assertion in
`test_registry_transaction_error_is_a_registry_error`; `status_code == 200` and
all three state proofs byte-identical to `HEAD`. Zero deletions in either
console test file. The design's citation of `tests/test_app.py:588` is stale —
that line is now `test_registry_products_route_reads_only_bound_namespace`.

**Stated limits, recorded rather than fixed:** the scan collects `.html` only
and does not follow symlinked subdirectories (`rglob` defaults
`recurse_symlinks=False`); nothing under `templates/` is either today. A
computed attribute name is not detected. `<script>`/`<style>` CDATA and
`hx-vals=` inside another attribute's quoted value are deliberately suppressed —
in those positions no real attribute exists for htmx to read. The `.gitignore`
that makes `git_status_bytes` byte-identical is written by the fixtures
themselves (4 call sites, all `*/outbox/*.yaml`); nothing in this repo
establishes the real vault's ignore rules, and the docstring now says so rather
than claiming otherwise.

**Also corrected:** a docstring quoting a design sentence that does not exist
(paraphrased with a real citation); a stale line reference — the design says
`app/git_transaction.py:466` fires the post-commit cleanup, but
`_remove_temporary_index` is called at **:476** and defined at 762, so the
design is ten lines stale; and a docstring naming the wrong route to the error
branch (the id fails the *grammar* check in `_delete_proposal_path` before any
disk lookup).

- **Reviewer verdict:** clean after fix round 2 — round 1: 2 Critical, 4
  Important, 8 Minor; round 2: 2 Critical, 2 Important, 7 Minor; round 3: none
  Critical or Important. Every finding was reproduced by the reviewer before
  acceptance and re-broken afterwards. **No unmeasured claim shipped in fix
  rounds 1 or 2** — the first passes on this branch of which that is true.
