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
`OutboxDestinationError`, so the strict loader's escaping types are unchanged
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
