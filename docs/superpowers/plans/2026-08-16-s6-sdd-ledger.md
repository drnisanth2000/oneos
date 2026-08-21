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
- **Commit:** `90562ed`.
