# S6 Visible Console Failures Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every typed Command Center refusal reach the operator as a
specific, safe, actionable message, with no route silently swallowing a failure
and no refusal decision changed.

**Architecture:** One frozen table maps every application exception to a code,
tier, severity, retry guidance, and commit outcome. `describe()` resolves an
outcome across an allowlisted `__cause__` chain by precedence. Ambiguous
exception bases are split so every raise site names a truthful subtype, enforced
by AST tests rather than by lists in a document. A presentation projection reads
the outbox per record, carrying capabilities rather than kinds. Two renderers
share the one table, selected by route shape then `HX-Request`.

**Tech Stack:** Python 3.12, FastAPI, Jinja2, HTMX 2.0.4, Alpine + alpine-morph,
pytest, `uv`.

**Design:** `docs/superpowers/specs/2026-08-16-s6-visible-console-failures-design.md`
— **Approved**. The design is normative. Where this plan and the design differ,
the design wins and the plan is wrong.

**Branch:** `codex/s6-visible-console-failures` from `origin/main` at `a42ee12`.
**Baselines:** 603 public tests, 37 private.

---

## Preconditions

- [ ] Confirm branch, base, clean worktree, and baseline

```bash
git branch --show-current
git rev-parse --short origin/main
git status --short
uv run python -m pytest -q
```

Expected: `codex/s6-visible-console-failures`, `a42ee12`, no modifications,
`603 passed`.

- [ ] Record Grey Matter pre-state

```bash
export ONEOS_VAULT="${ONEOS_VAULT:?set the vault path}"
mkdir -p /private/tmp/s6-proof
git -C "$ONEOS_VAULT" rev-parse HEAD > /private/tmp/s6-proof/head.before
git -C "$ONEOS_VAULT" status --porcelain=v1 -z --untracked-files=all > /private/tmp/s6-proof/status.before
git -C "$ONEOS_VAULT" diff --binary > /private/tmp/s6-proof/worktree.before
git -C "$ONEOS_VAULT" diff --cached --binary > /private/tmp/s6-proof/cached.before
```

The vault carries pre-existing uncommitted edits. Preserve them. Never clean,
stash, or normalize private state.

---

## File Structure

| File | Responsibility |
|---|---|
| `app/console_errors.py` (create) | `ConsoleError`, the class map, `describe()`. Imports domain exceptions; nothing imports it except the composition root. |
| `app/console_render.py` (create) | Renderer selection and status. Owns no copy. |
| `app/console_routing.py` (create) | `@console_route(catches=...)` and `@structured_reader(category=...)` declarations. |
| `app/scope.py`, `app/outbox.py`, `app/destinations.py`, `app/git_transaction.py` (modify) | Ambiguous-base subtypes. Type refinements only. |
| `app/outbox.py` (modify) | `_read_record`, `_validate_record`, `_render_diff`, `project_outbox`, `OutboxRow`, `OutboxListing`. |
| `app/vault.py`, `app/registry.py` (modify) | Boundary conversions for the `registry` reader category. |
| `app/main.py` (modify) | Every handler, the exception handlers, the global fallback. |
| `templates/_head.html` (create) | Shared head with the `htmx-config` meta tag. |
| `templates/blocks/alert.html`, `templates/error.html` (create) | The one alert fragment and the one page notice. |
| `templates/` (modify) | `_head.html` include, `tojson` `hx-vals`, blocked listing, per-row errors. |
| `static/app.css` (modify) | `.alert`, `.alert-attention`. |
| `tests/test_console_errors.py`, `test_console_invariants.py`, `test_console_routes.py`, `test_console_projection.py` (create) | Per §8 of the design. |

---

## Task 1: `ConsoleError` and its structural invariants

**Files:** create `app/console_errors.py`, `tests/test_console_errors.py`

- [ ] **Step 1: Write the failing test**

```python
import pytest
from app.console_errors import ConsoleError


def test_refusal_cannot_report_a_commit():
    with pytest.raises(ValueError):
        ConsoleError("E-X", "refusal", "refusal", "m", "retry", "yes")


def test_committed_tier_must_stop_and_report_yes():
    with pytest.raises(ValueError):
        ConsoleError("E-X", "committed", "attention", "m", "retry", "yes")
    with pytest.raises(ValueError):
        ConsoleError("E-X", "committed", "attention", "m", "stop", "no")


def test_recovery_tier_must_stop_and_report_unknown():
    with pytest.raises(ValueError):
        ConsoleError("E-X", "recovery", "attention", "m", "stop", "no")


def test_console_error_is_frozen():
    e = ConsoleError("E-X", "refusal", "refusal", "m", "none", "no")
    with pytest.raises(Exception):
        e.code = "E-Y"
```

- [ ] **Step 2: Run to verify it fails** — `uv run pytest tests/test_console_errors.py -q`. Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
"""The Console's operator-facing error vocabulary. One table, one resolver."""
from __future__ import annotations

from dataclasses import dataclass

TIERS = ("committed", "recovery", "integrity", "refusal", "unknown")
SEVERITIES = frozenset({"refusal", "attention"})
RETRIES = frozenset({"retry", "reload", "recreate", "stop", "none"})
COMMITTED = frozenset({"no", "yes", "unknown"})


@dataclass(frozen=True)
class ConsoleError:
    code: str
    tier: str
    severity: str
    message: str
    retry: str
    committed: str

    def __post_init__(self) -> None:
        if self.tier not in TIERS:
            raise ValueError("tier is not a permitted value")
        if self.severity not in SEVERITIES:
            raise ValueError("severity is not a permitted value")
        if self.retry not in RETRIES:
            raise ValueError("retry is not a permitted value")
        if self.committed not in COMMITTED:
            raise ValueError("committed is not a permitted value")
        if self.severity == "refusal" and self.committed != "no":
            raise ValueError("a refusal cannot report a commit")
        if self.tier == "committed" and (
            self.committed != "yes" or self.retry != "stop"
        ):
            raise ValueError("a committed outcome must stop and report yes")
        if self.tier == "recovery" and (
            self.committed != "unknown" or self.retry != "stop"
        ):
            raise ValueError("a recovery outcome must stop and report unknown")
```

- [ ] **Step 4: Verify** — `uv run pytest tests/test_console_errors.py -q`. Expected: PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat: add ConsoleError with structural invariants"`

---

## Task 2: Ambiguous-base subtypes

Type refinements only. Every existing `except` clause must still catch every
refined type, so no refusal changes. Characterize each site before converting.

**Files:** modify `app/scope.py`, `app/outbox.py`, `app/destinations.py`,
`app/git_transaction.py`; test `tests/test_console_invariants.py`

- [ ] **Step 1: Characterize** — for each ambiguous base, write a test asserting
  current behavior at each raise site: the same input is refused, with the same
  base type caught by the same `except`. These must stay green through the
  conversion.

- [ ] **Step 2: Write the failing subtype tests**

```python
import pytest
from app.scope import CrossScopeError, OutOfScopeError, RedirectedPathError
from app.outbox import ProposalSourceUnavailable


def test_every_subtype_is_still_caught_as_its_base():
    for cls in (OutOfScopeError, RedirectedPathError, ProposalSourceUnavailable):
        with pytest.raises(CrossScopeError):
            raise cls("x")
```

- [ ] **Step 3: Add the subtypes** per the design's ambiguous-base table:
  `CrossScopeError` → `RedirectedPathError`, `OutOfScopeError`,
  `ProposalSourceUnavailable`; `ReviewedStateConflict` →
  `ReviewedPathIntegrityError`, `ReviewedStateChanged`, `ReviewedPathUnavailable`,
  `InvalidTransactionPath`; `UnsafeDestinationPath` → `RedirectedDestination`,
  `MissingDestination`; `InvalidSourceLeaf` → `RedirectedSourceLeaf`,
  `MissingSourceLeaf`, `NonCanonicalLeaf`.

- [ ] **Step 4: Convert every raise site** to a subtype. Where one `except
  OSError` covers two conditions — `_read_no_follow_bytes` and the transaction
  site whose message is "could not be opened safely" — apply the design's
  discrimination rule: `ELOOP`, `O_NOFOLLOW` rejection, or a non-regular
  `fstat` is the integrity subtype; any other `OSError` is the unavailable
  subtype.

- [ ] **Step 5: Verify** — `uv run python -m pytest -q`. Expected: `603 passed`.
  Any S1-S5 failure here means a refusal changed; stop and investigate rather
  than adjusting the test.
- [ ] **Step 6: Commit** — `git commit -m "refactor: split ambiguous exception bases into truthful subtypes"`

---

## Task 3: The class map and `describe()`

**Files:** modify `app/console_errors.py`, `tests/test_console_errors.py`

- [ ] **Step 1: Write the failing tests** — one per row of the design's class
  map, asserting the exact code. Plus chain resolution:

```python
def test_committed_outcome_survives_the_domain_wrapper():
    from app.git_transaction import GitTransactionCommittedError
    from app.outbox import OutboxTransactionError
    try:
        try:
            raise GitTransactionCommittedError.__new__(GitTransactionCommittedError)
        except Exception as inner:
            raise OutboxTransactionError("boundary") from inner
    except OutboxTransactionError as outer:
        result = describe(outer)
    assert result.code == "E-COMMITTED"
    assert result.committed == "yes"
    assert result.retry == "stop"


def test_context_is_never_traversed():
    from app.outbox import OutboxScopeError, StaleProposalSource
    try:
        try:
            raise StaleProposalSource("s")
        except StaleProposalSource:
            raise OutboxScopeError("outer")   # implicit __context__, no `from`
    except OutboxScopeError as exc:
        assert describe(exc).code == "E-SCOPE"
```

- [ ] **Step 2: Run to verify it fails.**

- [ ] **Step 3: Implement the map and resolver**

```python
ALLOWLIST = (
    OutboxTransactionError, RegistryTransactionError,
    OutboxDestinationError, GitTransactionFailure,
    _ApprovalLockCleanupFailure, _ReviewedIndexOwnershipConflict,
)
CLOSED_FAMILY = GitTransactionError
ABSTRACT_BASES = (
    CrossScopeError, ReviewedStateConflict,
    UnsafeDestinationPath, InvalidSourceLeaf,
)
MAX_DEPTH = 4
_TIER_RANK = {t: i for i, t in enumerate(TIERS)}


def _lookup(exc: BaseException) -> ConsoleError:
    for cls in type(exc).__mro__:
        if cls in _EXACT:
            return _EXACT[cls]
        if issubclass(cls, CLOSED_FAMILY):
            return UNKNOWN          # closed family: no MRO inheritance
        if cls in _MRO:
            return _MRO[cls]
    return UNKNOWN


def describe(exc: BaseException) -> ConsoleError:
    best, current, depth = None, exc, 0
    while current is not None and depth < MAX_DEPTH:
        candidate = _lookup(current)
        if best is None or _TIER_RANK[candidate.tier] <= _TIER_RANK[best.tier]:
            best = candidate          # <= keeps the innermost on a tie
        if type(current) not in ALLOWLIST:
            return best
        current, depth = current.__cause__, depth + 1
    if current is not None:
        return UNKNOWN                # depth exceeded: fail closed
    return best or UNKNOWN
```

Populate `_EXACT` and `_MRO` from the design's class map, using the exact
messages from the design's message table. Do not paraphrase them.

- [ ] **Step 4: Verify** — `uv run pytest tests/test_console_errors.py -q`.
- [ ] **Step 5: Commit** — `git commit -m "feat: resolve outcomes across allowlisted cause chains"`

---

## Task 4: Invariants 1, 2, 3

**Files:** create `tests/test_console_invariants.py`

- [ ] **Step 1: Invariant 1** — walk every exception class under `app/`; assert
  each resolves to the code named in the design's map, exempting the four
  abstract bases.
- [ ] **Step 2: Invariant 2** — walk `GitTransactionError.__subclasses__()`
  transitively; assert an exact entry for each, exempting the abstract bases.
  Add a synthetic subclass in-test and assert it resolves to `E-UNKNOWN`.
- [ ] **Step 3: Invariant 3** — AST-walk every `raise` under `app/`; fail on any
  whose type is one of the four abstract bases.
- [ ] **Step 4: Run** — all three must pass; if invariant 3 fails, fix the raise
  site rather than the test.
- [ ] **Step 5: Commit** — `git commit -m "test: close the taxonomy with source-derived invariants"`

---

## Task 5: Renderer selection and templates

**Files:** create `app/console_render.py`, `templates/_head.html`,
`templates/blocks/alert.html`, `templates/error.html`; modify `static/app.css`
and the four page templates.

- [ ] **Step 1: Failing tests** — fragment status by severity (refusal → 200,
  attention → page status); route-shape-first (the five template-less POSTs
  always fragment); every full-page route contains the `htmx-config` meta.
- [ ] **Step 2: Implement `console_render`** with `is_fragment(request, route)`
  and `status_for(error, fragment)`.
- [ ] **Step 3: Create `_head.html`** carrying the vendored script tags and:

```html
<meta name="htmx-config" content='{"responseHandling":[
  {"code":"204","swap":false},
  {"code":"[23]..","swap":true},
  {"code":"[45]..","swap":true,"error":true}]}'>
```

Include it from `shell.html`, `triage.html`, `outbox.html`, `registry.html`.
Give `triage_default`'s no-bundles response a template so it can carry the tag.

- [ ] **Step 4: Create `alert.html`** rendering `error.code` and `error.message`
  with `role="alert"`, escaped, plus `.alert` / `.alert-attention` CSS.
- [ ] **Step 5: Verify and commit** — `git commit -m "feat: add Console renderers, shared head, and alert markup"`

---

## Task 6: Structured readers and boundary conversions

**Files:** create `app/console_routing.py`; modify `app/vault.py`,
`app/registry.py`, `app/outbox.py`, `app/inbox.py`.

- [ ] **Step 1: Failing tests (invariant 4)** — AST-find every function that
  parses YAML, opens a `system_path` result, or connects to SQLite; fail on any
  without a `@structured_reader(category=...)` declaration.
- [ ] **Step 2: Declare categories** — `registry`, `proposal`, `front-matter`,
  `admin-db` per the design.
- [ ] **Step 3: Failing conversion tests** — inject unparseable and
  wrongly-shaped-but-valid input through each `registry` reader; assert
  `E-CONFIG`. Assert each reader's absorbed cases still return their tolerant
  value. Assert a `proposal`-category failure yields `E-UNREADABLE`, not
  `E-CONFIG`.
- [ ] **Step 4: Convert** only failures that already escape. Narrow each
  conversion to the specific parse or access it guards.
- [ ] **Step 5: Verify and commit** — `git commit -m "feat: classify structured readers and convert escaping registry failures"`

---

## Task 7: The projection

**Files:** modify `app/outbox.py`; create `tests/test_console_projection.py`

- [ ] **Step 1: Failing tests** — per the design's projection test list: the
  unblocked case, the blocked case, delete-skip, an outbox of only deletes, the
  no-re-entry assertion (patch `get_proposal`, `load_proposals`, `preview_diff`
  to raise), and the undiffable row keeping `can_reject`.
- [ ] **Step 2: Extract** `_read_record`, `_validate_record`, `_render_diff` from
  the strict loader's body; have `load_proposals` call them in its existing
  fail-closed loop. Validation logic moves, it does not change.
- [ ] **Step 3: Add `UnreadableProposalRecord`** covering every phase-1
  condition in the design's table, including non-UTF-8 record bytes, record-read
  `OSError`, and malformed required fields.
- [ ] **Step 4: Implement `project_outbox`** with the three-phase rule: phase 1
  caught per row and sets `blocked`; phase 2 propagates; phase 3 is row-local,
  read through the same safe-read boundary as `approve`, translated per the
  design's normative table.
- [ ] **Step 5: Verify** — projection tests pass and `tests/test_outbox.py`
  passes unmodified.
- [ ] **Step 6: Commit** — `git commit -m "feat: add the outbox presentation projection"`

---

## Task 8: Routes

**Files:** modify `app/main.py` and the templates it renders.

Each route gets its own RED test first, asserting status, code, message, absence
of raw exception text, and the declared swap shape.

- [ ] **Step 1: `@console_route(catches=...)`** on every handler; reject
  `Exception`/`BaseException` in the declaration and bare `except Exception` in
  the body (invariant 6).
- [ ] **Step 2: outbox routes** — render the projection; blocked listing withholds
  every classification control and carries one described notice.
- [ ] **Step 3: `propose`** — alert into the existing `#diff-{index}` target;
  emit the success-only `HX-Trigger` after persistence.
- [ ] **Step 4: `triage`** — per-row errors; extend the catch tuple with
  `CrossScopeError`.
- [ ] **Step 5: registry routes** — `tojson` `hx-vals` everywhere (invariant 5),
  success copy from a pre-execute `get_delete_proposal`, both branches templated,
  execute request stops sending `slug`.
- [ ] **Step 6: reading routes** — `E-CONFIG` page.
- [ ] **Step 7: `entity_scope`** — raise `EntitySelectionError`; dedicated
  handler renders `E-ENTITY` at 404.
- [ ] **Step 8: handlers** — `RequestValidationError` → `E-REQUEST`;
  `StarletteHTTPException` body replacement preserving the framework's status;
  global fallback returning the code's page status, never 200.
- [ ] **Step 9: stopwatch** — move `triage.html` to the `HX-Trigger` event.
- [ ] **Step 10: Verify and commit** — `git commit -m "feat: surface described failures on every Console route"`

---

## Task 9: Proofs

**Files:** `tests/test_console_routes.py`

- [ ] **Step 1: State proofs** keyed to `committed` and to the declared
  persistence outcome, using the `conftest.py` fingerprint helpers. `propose` and
  `registry_delete_preview` declare `proposal-written`.
- [ ] **Step 2: Disclosure** — no path separators, slugs, commit ids, or echoed
  request values in any alert; markup escaped.
- [ ] **Step 3: `hx-vals` rebinding** — a slug with quotes, braces, and a second
  `id` yields exactly one `id`, equal to the previewed proposal.
- [ ] **Step 4: Route-level totality** — each declared catch family injected;
  global handler asserted not to be the responder.
- [ ] **Step 5: Commit** — `git commit -m "test: prove state, disclosure, and binding"`

---

## Task 10: Documentation

- [ ] Set `| S6 | **COMPLETE** |` in `BUILD.md` and update
  `tests/test_publication_docs.py`, which asserts `| S6 | **NEXT** |`.
- [ ] Update `docs/STATUS.md`: S6 complete, defect list resolved or carried, S7
  still proposed.
- [ ] Mark the design and this plan historical.
- [ ] Commit — `git commit -m "docs: record S6 as complete"`

---

## Final verification

```bash
uv run pytest tests/test_app.py -q
uv run pytest tests/test_outbox.py tests/test_registry.py tests/test_git_transaction.py -q
uv run pytest tests/test_vault.py -q
uv run python -m pytest -q
git diff --check
git diff --stat origin/main...HEAD
```

Then the private gates, read-only:

```bash
cd "$ONEOS_VAULT/_system/scripts" && python3 -m unittest discover -q; cd -
python3 "$ONEOS_VAULT/_system/scripts/check_v2.py" "$ONEOS_VAULT" | tail -2
tools/run_gitleaks.sh .
uv run python -m tools.public_repo_audit --repo . --history
uv run python -m tools.public_repo_audit --repo . --vault "$ONEOS_VAULT" --history
```

Then fingerprint equality:

```bash
for f in head status worktree cached; do
  case $f in
    head)     git -C "$ONEOS_VAULT" rev-parse HEAD > /private/tmp/s6-proof/head.after ;;
    status)   git -C "$ONEOS_VAULT" status --porcelain=v1 -z --untracked-files=all > /private/tmp/s6-proof/status.after ;;
    worktree) git -C "$ONEOS_VAULT" diff --binary > /private/tmp/s6-proof/worktree.after ;;
    cached)   git -C "$ONEOS_VAULT" diff --cached --binary > /private/tmp/s6-proof/cached.after ;;
  esac
  cmp "/private/tmp/s6-proof/$f.before" "/private/tmp/s6-proof/$f.after" \
    && echo "$f identical" || { echo "$f DIFFERS — stop"; exit 1; }
done
```

- [ ] Whole-branch review for safe disclosure, HTMX behavior, typed outcome
  accuracy, S1-S5 preservation, instance leakage, and non-goal drift.
- [ ] superpowers:verification-before-completion.

---

## Stop conditions

- Any S1-S5 test failing that is not one of the two listed in the design's
  regression table. S6 changes no refusal decision, so a third failure means
  scope was breached.
- Any change to vault content, conventions, or registries.
- Any new dependency, route, screen, or schema.
- Any private gate failing, or any fingerprint differing.
- Any need to render a value the disclosure boundary forbids.
- Any point where the honest fix adds a refusal condition — that is S7 or later,
  not an S6 edit. This has been the recurring breach; treat "it is only bounded"
  as the warning sign it has been every previous time.
- Publication: the branch stays local until explicitly authorized.

## Known limitations carried from the design

- The review gate does not bind reviewed content (S7).
- `catalog = build_catalog()` runs at module scope, so a manifest failure aborts
  import before any handler exists.
- `E-ENTITY` distinguishes an absent entity from a present one.
- Adapter and ingest failures have no Console surface.
