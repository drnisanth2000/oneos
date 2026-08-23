# S6 Visible Console Failures Implementation Plan

> **HISTORICAL.** Every task is implemented, reviewed and committed. The
> branch commands, baselines and stop conditions below are a record of how
> S6 was executed, not current instructions.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every typed Command Center refusal reach the operator as a
specific, safe, actionable message, with no route silently swallowing a failure
and no refusal decision changed.

**Design:** `docs/superpowers/specs/2026-08-16-s6-visible-console-failures-design.md`
— **Approved and normative.** Where this plan and the design differ, the design
wins and the plan is wrong. Codes, messages, tiers, precedence, and the class
map come from the design verbatim; this plan never paraphrases a message.

**Branch:** `codex/s6-visible-console-failures` from `origin/main` at `a42ee12`.
**Baselines:** 603 public tests, 37 private. The 603 pre-existing tests must
pass unmodified at every step except the two listed in the design's regression
table; new tests grow the total, so step expectations say "all tests pass", and
each task additionally asserts the pre-existing suite is untouched.

**Tech Stack:** Python 3.12, FastAPI, Jinja2, HTMX 2.0.4, Alpine + alpine-morph,
pytest, `uv`.

---

## Preconditions

- [ ] Confirm branch, base, clean worktree, baseline

```bash
git branch --show-current          # codex/s6-visible-console-failures
git rev-parse --short origin/main  # a42ee12
git status --short                 # empty
uv run python -m pytest -q         # 603 passed
```

- [ ] Record Grey Matter pre-state into a unique proof directory

```bash
export ONEOS_VAULT="${ONEOS_VAULT:?set the vault path}"
PROOF="$(mktemp -d /private/tmp/s6-proof.XXXXXX)"; echo "$PROOF" > /private/tmp/s6-proof-path
git -C "$ONEOS_VAULT" rev-parse HEAD > "$PROOF/head.before"
git -C "$ONEOS_VAULT" status --porcelain=v2 -z --untracked-files=all > "$PROOF/status.before"
git -C "$ONEOS_VAULT" diff --binary > "$PROOF/worktree.before"
git -C "$ONEOS_VAULT" diff --cached --binary > "$PROOF/cached.before"
```

The vault carries pre-existing uncommitted edits. Preserve them. Never clean,
stash, or normalize private state.

---

## File Structure

| File | Responsibility |
|---|---|
| `app/console_errors.py` (create) | `ConsoleError` (with `page_status`), the class map, `describe()`. Imports domain exceptions; only the composition root imports it. |
| `app/console_render.py` (create) | `status_for(error, fragment)` and fragment selection. Owns no copy and no route list. |
| `app/console_routing.py` (create) | `@console_route(catches=..., surface=...)` and `@structured_reader(category=...)`. Pure metadata; importable by services without touching the taxonomy. |
| `app/scope.py`, `app/outbox.py`, `app/destinations.py`, `app/git_transaction.py`, `app/inbox.py` (modify) | New exception subtypes; raise-site conversions. Type refinements only. |
| `app/vault.py`, `app/registry.py`, `app/entities.py`, `app/classifier.py`, `app/schema.py`, `app/rename.py`, `app/ingest/base.py` (modify) | `@structured_reader` declarations; `registry`-category conversions. |
| `app/main.py` (modify) | Handlers, exception handlers, global fallback. |
| `templates/_head.html` (create) | Shared head with the `htmx-config` meta. |
| `templates/blocks/alert.html`, `templates/error.html`, `templates/blocks/no_bundles.html` (create) | Alert fragment, page notice, templated no-bundles response. |
| `templates/` (modify) | `_head.html` include ×4, `tojson` `hx-vals`, blocked listing, per-row errors, stopwatch event. |
| `static/app.css` (modify) | `.alert`, `.alert-attention`. |
| `tests/test_console_errors.py`, `test_console_invariants.py`, `test_console_render.py`, `test_console_readers.py`, `test_console_projection.py`, `test_console_routes.py` (create) | Per design §8. |

---

## Task 1: `ConsoleError` with structural invariants and `page_status`

**Files:** create `app/console_errors.py`, `tests/test_console_errors.py`

`page_status` lives on the frozen dataclass because the design's code table
carries it per code; `console_render` must not own a parallel code→status map.

- [ ] **Step 1: RED** — write these tests:

```python
import pytest
from app.console_errors import ConsoleError


def test_refusal_cannot_report_a_commit():
    with pytest.raises(ValueError):
        ConsoleError("E-X", "refusal", "refusal", "m", "retry", "yes", 422)

def test_committed_tier_must_stop_and_report_yes():
    with pytest.raises(ValueError):
        ConsoleError("E-X", "committed", "attention", "m", "retry", "yes", 500)
    with pytest.raises(ValueError):
        ConsoleError("E-X", "committed", "attention", "m", "stop", "no", 500)

def test_recovery_tier_must_stop_and_report_unknown():
    with pytest.raises(ValueError):
        ConsoleError("E-X", "recovery", "attention", "m", "stop", "no", 500)

def test_page_status_must_be_a_known_http_status():
    with pytest.raises(ValueError):
        ConsoleError("E-X", "refusal", "refusal", "m", "none", "no", 299)

def test_console_error_is_frozen():
    e = ConsoleError("E-X", "refusal", "refusal", "m", "none", "no", 422)
    with pytest.raises(Exception):
        e.code = "E-Y"
```

- [ ] **Step 2:** `uv run pytest tests/test_console_errors.py -q` — FAIL,
  `ModuleNotFoundError: No module named 'app.console_errors'`.
- [ ] **Step 3: GREEN** — implement:

```python
"""The Console's operator-facing error vocabulary. One table, one resolver."""
from __future__ import annotations

from dataclasses import dataclass

TIERS = ("committed", "recovery", "integrity", "refusal", "unknown")
SEVERITIES = frozenset({"refusal", "attention"})
RETRIES = frozenset({"retry", "reload", "recreate", "stop", "none"})
COMMITTED = frozenset({"no", "yes", "unknown"})
PAGE_STATUSES = frozenset({404, 409, 422, 500})


@dataclass(frozen=True)
class ConsoleError:
    code: str
    tier: str
    severity: str
    message: str
    retry: str
    committed: str
    page_status: int

    def __post_init__(self) -> None:
        if self.tier not in TIERS:
            raise ValueError("tier is not a permitted value")
        if self.severity not in SEVERITIES:
            raise ValueError("severity is not a permitted value")
        if self.retry not in RETRIES:
            raise ValueError("retry is not a permitted value")
        if self.committed not in COMMITTED:
            raise ValueError("committed is not a permitted value")
        if self.page_status not in PAGE_STATUSES:
            raise ValueError("page status is not a permitted value")
        if self.severity == "refusal" and self.committed != "no":
            raise ValueError("a refusal cannot report a commit")
        if self.tier == "committed" and (self.committed != "yes" or self.retry != "stop"):
            raise ValueError("a committed outcome must stop and report yes")
        if self.tier == "recovery" and (self.committed != "unknown" or self.retry != "stop"):
            raise ValueError("a recovery outcome must stop and report unknown")
```

- [ ] **Step 4:** `uv run pytest tests/test_console_errors.py -q` — PASS (5).
- [ ] **Step 5:**

```bash
git add app/console_errors.py tests/test_console_errors.py
git commit -m "feat: add ConsoleError with structural invariants"
```

---

## Task 2: Declare every new exception class

Declarations only — no raise site changes, no behavior. This exists so Task 5's
map can import every class it names without forward references.

**Files:** modify `app/scope.py`, `app/outbox.py`, `app/destinations.py`,
`app/git_transaction.py`; test `tests/test_console_invariants.py`

- [ ] **Step 1: RED** — `tests/test_console_invariants.py`:

```python
import pytest


def test_every_new_subtype_is_caught_as_its_base():
    from app.scope import CrossScopeError, OutOfScopeError, RedirectedPathError
    from app.outbox import ProposalSourceUnavailable, UnreadableProposalRecord, OutboxError
    from app.destinations import (
        UnsafeDestinationPath, RedirectedDestination, MissingDestination,
        InvalidSourceLeaf, RedirectedSourceLeaf, MissingSourceLeaf, NonCanonicalLeaf,
    )
    from app.git_transaction import (
        ReviewedStateConflict, ReviewedPathIntegrityError,
        ReviewedStateChanged, ReviewedPathUnavailable, InvalidTransactionPath,
    )
    pairs = [
        (OutOfScopeError, CrossScopeError),
        (RedirectedPathError, CrossScopeError),
        (ProposalSourceUnavailable, CrossScopeError),
        (UnreadableProposalRecord, OutboxError),
        (RedirectedDestination, UnsafeDestinationPath),
        (MissingDestination, UnsafeDestinationPath),
        (RedirectedSourceLeaf, InvalidSourceLeaf),
        (MissingSourceLeaf, InvalidSourceLeaf),
        (NonCanonicalLeaf, InvalidSourceLeaf),
        (ReviewedPathIntegrityError, ReviewedStateConflict),
        (ReviewedStateChanged, ReviewedStateConflict),
        (ReviewedPathUnavailable, ReviewedStateConflict),
        (InvalidTransactionPath, ReviewedStateConflict),
    ]
    for sub, base in pairs:
        with pytest.raises(base):
            raise sub("x")
```

- [ ] **Step 2:** run it — FAIL, `ImportError` on the first missing name.
- [ ] **Step 3: GREEN** — add each class as `class X(Base):\n    pass` beside its
  base. `ProposalSourceUnavailable` and `UnreadableProposalRecord` live in
  `app/outbox.py` (`ProposalSourceUnavailable` subclasses the imported
  `CrossScopeError`; `UnreadableProposalRecord` subclasses `OutboxError`).
- [ ] **Step 4:** `uv run python -m pytest -q` — all tests pass; no pre-existing
  test modified.
- [ ] **Step 5:**

```bash
git add app/scope.py app/outbox.py app/destinations.py app/git_transaction.py tests/test_console_invariants.py
git commit -m "feat: declare refined exception subtypes"
```

---

## Task 3: Pin the safe-read contract and convert the CrossScopeError sites

**Files:** modify `app/outbox.py`, `app/inbox.py`, `app/scope.py`,
`app/registry.py` (its CrossScopeError raises); tests in
`tests/test_console_invariants.py`

The safe-read contract, stated once and binding on Task 9:

```text
_read_no_follow_bytes(path) -> bytes
  missing leaf                          -> FileNotFoundError (re-raised;
                                           callers translate: approve and
                                           _render_diff -> MissingProposalSource)
  ELOOP / O_NOFOLLOW rejection          -> RedirectedPathError
  fstat says non-regular                -> RedirectedPathError
  any other OSError (perm, IO, race)    -> ProposalSourceUnavailable
```

Both raised types subclass `CrossScopeError`, so every existing `except` clause
is unchanged and no refusal changes.

- [ ] **Step 1: RED** — six tests against the boundary, by name:

```text
test_safe_read_missing_leaf_raises_filenotfound
test_safe_read_symlink_raises_redirected            (symlink -> RedirectedPathError)
test_safe_read_nonregular_raises_redirected         (real file; monkeypatch
    os.fstat to report a non-regular st_mode — opening a FIFO read-only
    blocks forever without a writer, so no FIFO fixture)
test_safe_read_permission_error_raises_unavailable  (chmod 000)
test_safe_read_replacement_race_raises_redirected   (dir swapped in for file)
test_safe_read_other_oserror_raises_unavailable     (EACCES on parent)
```

  Each asserts the exact subtype and that `isinstance(exc, CrossScopeError)`.
- [ ] **Step 2:** run — FAIL: today all non-missing cases raise bare
  `CrossScopeError`.
- [ ] **Step 3: GREEN** — discriminate inside `_read_no_follow_bytes` on
  `exc.errno == errno.ELOOP` / the `fstat` check vs everything else. Then
  convert every remaining bare `raise CrossScopeError` in `app/scope.py`,
  `app/outbox.py`, `app/inbox.py`, `app/registry.py` to `RedirectedPathError`
  (redirection/non-regular/changed-during-creation) or `OutOfScopeError`
  (resolved outside the bound entity), per the design's condition table.
- [ ] **Step 4:** `uv run python -m pytest -q` — all pass; any pre-existing
  failure means a refusal changed: stop.
- [ ] **Step 5:**

```bash
git add app/outbox.py app/inbox.py app/scope.py app/registry.py tests/test_console_invariants.py
git commit -m "refactor: convert CrossScopeError raise sites to truthful subtypes"
```

---

## Task 4: Convert the git_transaction and destinations sites

**Files:** modify `app/git_transaction.py`, `app/destinations.py`; tests in
`tests/test_console_invariants.py`

- [ ] **Step 1: RED** — the invariant-3 AST test itself:

```python
import ast, pathlib

AMBIGUOUS = {"CrossScopeError", "ReviewedStateConflict",
             "UnsafeDestinationPath", "InvalidSourceLeaf"}

def test_no_direct_raise_of_an_ambiguous_base():
    offenders = []
    for path in pathlib.Path("app").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
                name = getattr(node.exc.func, "id", getattr(node.exc.func, "attr", ""))
                if name in AMBIGUOUS:
                    offenders.append(f"{path}:{node.lineno}")
    assert offenders == []
```

- [ ] **Step 2:** run — FAIL listing every unconverted site (the remaining ones
  are all in `git_transaction.py` and `destinations.py` after Task 3).
- [ ] **Step 3: GREEN** — convert each site. `git_transaction.py`: path
  validation before I/O → `InvalidTransactionPath`; `lstat`/read `OSError` →
  `ReviewedPathUnavailable`; symlink/non-regular/type-swap →
  `ReviewedPathIntegrityError` (the "could not be opened safely" site uses the
  Task-3 discrimination rule); content/index changed → `ReviewedStateChanged`.
  `destinations.py`: symlinked → `RedirectedDestination`/`RedirectedSourceLeaf`;
  absent → `MissingDestination`/`MissingSourceLeaf`; bad leaf name →
  `NonCanonicalLeaf`.
- [ ] **Step 4:** `uv run python -m pytest -q` — all pass, including the AST
  test and all 603 pre-existing.
- [ ] **Step 5:**

```bash
git add app/git_transaction.py app/destinations.py tests/test_console_invariants.py
git commit -m "refactor: convert transaction and destination raise sites"
```

---

## Task 5: The class map and `describe()`

**Files:** modify `app/console_errors.py`; test `tests/test_console_errors.py`

- [ ] **Step 1: RED** — one test per row of the design's class map asserting the
  exact code (`test_map_<ClassName>` for each), plus:

```text
test_committed_outcome_survives_the_domain_wrapper     (E-COMMITTED via OutboxTransactionError)
test_recovery_outcome_survives_both_wrappers           (E-RECOVER via both)
test_all_five_s5_outcomes_via_registry_wrapper
test_config_survives_outbox_destination_wrapper        (E-CONFIG through OutboxDestinationError)
test_context_is_never_traversed
test_depth_overflow_fails_closed_to_unknown            (chain of 5 allowlisted links -> E-UNKNOWN)
test_allowlist_membership_is_exact_class_identity      (subclass of allowlisted class stops the walk)
test_exact_mapping_does_not_inherit                    (synthetic subclass of RegistryError-mapped
                                                        exact class resolves via MRO rules, and a
                                                        synthetic subclass of an exact-mapped
                                                        NON-Git class, e.g. StaleProposalSource,
                                                        does NOT inherit E-STALE)
test_closed_family_synthetic_subclass_is_unknown
test_abstract_bases_resolve_nowhere_and_are_never_raised
```

- [ ] **Step 2:** run — FAIL, `describe` undefined.
- [ ] **Step 3: GREEN** — the resolver. **Exact means `type(exc)` only:**

```python
def _lookup(exc: BaseException) -> ConsoleError:
    cls = type(exc)
    if cls in _EXACT:                      # exact: this class only, never MRO
        return _EXACT[cls]
    for ancestor in cls.__mro__:
        if issubclass(ancestor, CLOSED_FAMILY) and ancestor is not Exception:
            return UNKNOWN                 # closed family: no inheritance
        if ancestor in _MRO:
            return _MRO[ancestor]
    return UNKNOWN


def describe(exc: BaseException) -> ConsoleError:
    best, current, depth = None, exc, 0
    while True:
        candidate = _lookup(current)
        if best is None or _TIER_RANK[candidate.tier] <= _TIER_RANK[best.tier]:
            best = candidate               # <= : innermost wins a tie
        if type(current) not in ALLOWLIST:  # exact identity, per the design
            return best
        nxt = current.__cause__
        if nxt is None:
            return best
        depth += 1
        if depth >= MAX_DEPTH:
            return UNKNOWN                 # overflow fails closed
        current = nxt
```

  Populate `_EXACT` / `_MRO` from the design's class map with the design's
  message table verbatim, `page_status` from the codes table.
  `ALLOWLIST = (OutboxTransactionError, RegistryTransactionError,
  OutboxDestinationError, GitTransactionFailure, _ApprovalLockCleanupFailure,
  _ReviewedIndexOwnershipConflict)`.
- [ ] **Step 4:** `uv run pytest tests/test_console_errors.py -q` — PASS.
- [ ] **Step 5:**

```bash
git add app/console_errors.py tests/test_console_errors.py
git commit -m "feat: resolve outcomes across allowlisted cause chains"
```

---

## Task 6: Invariants 1 and 2

**Files:** `tests/test_console_invariants.py`

- [ ] **Step 1:** `test_every_application_exception_resolves_to_its_designed_code`
  — discover exception classes by walking modules under `app/` **excluding**
  `app.main`, whose import executes `build_catalog()` at module scope and
  would read a live vault or fail without `ONEOS_VAULT`; `app.main` defines
  no exception classes (assert that by AST inside the test). Collect from
  the safe imports, assert
  `describe(cls("probe"))` returns a non-`E-UNKNOWN` code, exempting exactly
  the four abstract bases; separately assert each mapped class hits its named
  code (import the expected pairs from a dict in the test, transcribed from the
  design — this dict is test data, not a second runtime map).
- [ ] **Step 2:** `test_closed_family_every_subclass_has_exact_entry` — walk
  `GitTransactionError.__subclasses__()` transitively; assert `_EXACT`
  membership for each, exempting the abstract bases per the design.
- [ ] **Step 3:** `test_no_domain_module_imports_the_taxonomy` — walk `app/`
  excluding `main.py`, `console_render.py`, `console_routing.py`; assert no
  import of `console_errors`/`console_render`.
- [ ] **Step 4:** run all; fix map gaps in `console_errors.py`, never in tests.
- [ ] **Step 5:**

```bash
git add tests/test_console_invariants.py
git commit -m "test: close the taxonomy with source-derived invariants"
```

---

## Task 7: Renderers, route metadata, templates

**Files:** create `app/console_routing.py`, `app/console_render.py`,
`templates/_head.html`, `templates/blocks/alert.html`, `templates/error.html`,
`templates/blocks/no_bundles.html`; modify the four page templates,
`static/app.css`; test `tests/test_console_render.py`

Route-shape ownership: `@console_route(catches=..., surface=...)` with
`surface` ∈ {`"page"`, `"fragment-only"`}. `console_render.is_fragment(request,
endpoint)` reads the decorator metadata — fragment-only routes always fragment;
page routes fragment iff `HX-Request`. No route list exists anywhere else.

- [ ] **Step 1: RED**

```text
test_fragment_refusal_status_is_200
test_fragment_attention_status_is_the_page_status
test_page_status_comes_from_the_error
test_fragment_only_route_ignores_missing_hx_request
test_console_route_rejects_exception_in_catches      (Exception/BaseException -> ValueError)
test_every_page_template_carries_htmx_config_meta    (renders /, /triage/{e}, /outbox/{e},
                                                      /registry/{e}/products and asserts the meta)
test_no_bundles_response_carries_htmx_config_meta
```

- [ ] **Step 2:** run — FAIL.
- [ ] **Step 3: GREEN** — `console_routing.console_route` stores
  `(catches, surface)` on the endpoint and raises `ValueError` if `Exception`
  or `BaseException` is in `catches`. `console_render.status_for(error,
  fragment)`: `200 if fragment and error.severity == "refusal" else
  error.page_status`. `_head.html` carries the five vendored `<script defer>`
  tags plus:

```html
<meta name="htmx-config" content='{"responseHandling":[
  {"code":"204","swap":false},
  {"code":"[23]..","swap":true},
  {"code":"[45]..","swap":true,"error":true}]}'>
```

  Include from all four page templates; convert `triage_default`'s bare
  `HTMLResponse` to `blocks/no_bundles.html` extending the shared head.
  `alert.html` renders `error.code` + `error.message`, `role="alert"`, no
  `| safe`. Add `.alert`/`.alert-attention` to `app.css`.
- [ ] **Step 4:** `uv run python -m pytest -q` — all pass.
- [ ] **Step 5:**

```bash
git add app/console_routing.py app/console_render.py templates/_head.html \
  templates/blocks/alert.html templates/error.html templates/blocks/no_bundles.html \
  templates/shell.html templates/triage.html templates/outbox.html templates/registry.html \
  static/app.css app/main.py tests/test_console_render.py
git commit -m "feat: add Console renderers, route metadata, and shared head"
```

---

## Task 8: Structured readers

**Files:** modify `app/vault.py`, `app/registry.py`, `app/entities.py`,
`app/classifier.py`, `app/schema.py`, `app/rename.py`, `app/inbox.py`,
`app/outbox.py`, `app/ingest/base.py`; test `tests/test_console_readers.py`

Current candidates and their categories — the AST guard exists for *future*
omissions; today's complete set is:

| Site | Category |
|---|---|
| `vault._load_yaml`, `registry` workspaces/products/registry-bytes readers, `classifier` rules reader, `entities` manifest reader | `registry` |
| `outbox` record loads (×2), `registry` delete-proposal loads (×2) | `proposal` |
| `inbox.split_front_matter` + its callers, `registry` front-matter counter, `schema` front-matter parse, `ingest.base` receipt parse | `front-matter` |
| `rename` books.db reader; `registry._count_books_db` | `admin-db` |

- [ ] **Step 1: RED**

```text
test_every_structured_read_site_declares_a_category   (AST: yaml.safe_load /
    sqlite3.connect / split_front_matter call inside an undecorated function fails)
test_registry_reader_unparseable_yaml_becomes_config
test_registry_reader_wrongly_shaped_yaml_becomes_config   (list where mapping expected)
test_registry_reader_absent_products_still_returns_empty  (tolerance pinned)
test_registry_reader_absent_workspaces_still_counts_zero
test_front_matter_malformed_still_returns_empty_mapping
test_proposal_reader_failure_is_unreadable_not_config
```

- [ ] **Step 2:** run — FAIL on the AST test first.
- [ ] **Step 3: GREEN** — decorate every site; convert **escaping** failures in
  `registry`-category readers to `DestinationRegistryError` (narrow, at the
  parse/access); leave absorbed cases untouched.
- [ ] **Step 4:** `uv run python -m pytest -q` — all pass.
- [ ] **Step 5:**

```bash
git add app/vault.py app/registry.py app/entities.py app/classifier.py \
  app/schema.py app/rename.py app/inbox.py app/outbox.py app/ingest/base.py \
  tests/test_console_readers.py
git commit -m "feat: classify structured readers and convert escaping registry failures"
```

---

## Task 9: The projection

**Files:** modify `app/outbox.py`; test `tests/test_console_projection.py`

- [ ] **Step 1: RED**

```text
test_unblocked_listing_renders_all_valid_rows_with_controls
test_malformed_record_blocks_listing_and_withholds_all_controls
test_blocked_state_actions_still_refused_by_strict_loader
test_well_formed_delete_proposal_is_skipped_not_blocking
test_outbox_of_only_deletes_renders_empty_not_blocked
test_projection_never_reenters_strict_loading        (monkeypatch get_proposal,
    load_proposals, preview_diff to raise AssertionError)
test_undiffable_utf8_row_keeps_reject_loses_approve  (non-UTF-8 receipt)
test_undiffable_row_error_matches_approve_outcome    (same receipt: row error code
    == describe(<what approve raises>).code, for missing / redirected / non-UTF-8 /
    permission cases)
test_phase2_config_propagates_and_aborts             (broken archetypes.yaml ->
    E-CONFIG listing-level, zero rows)
test_approval_after_projection_still_revalidates     (tamper the record after
    projecting, before approving; approval refuses)
```

- [ ] **Step 2:** run — FAIL, `project_outbox` undefined.
- [ ] **Step 3: GREEN** — extract `_read_record` / `_validate_record` /
  `_render_diff` from the strict loader body (loader now calls them; behavior
  identical). Phase 1 catches `UnreadableProposalRecord` causes — raise it
  `from` the underlying error at read/schema failures inside `_read_record` /
  `_validate_record`, covering the design's seven-condition table. Phase 2
  propagates. Phase 3: `_render_diff` reads via `_read_no_follow_bytes` and
  translates per the design's table (`FileNotFoundError → MissingProposalSource`,
  `RedirectedPathError` passes through, `UnicodeDecodeError →
  OutboxDestinationError`, other → `ProposalSourceUnavailable` passes through).
  `OutboxRow(proposal, diff, error, can_approve, can_reject)`;
  `OutboxListing(rows, blocked)`.
- [ ] **Step 4:** `uv run python -m pytest -q` — all pass; `tests/test_outbox.py`
  unmodified.
- [ ] **Step 5:**

```bash
git add app/outbox.py tests/test_console_projection.py
git commit -m "feat: add the outbox presentation projection"
```

---

## Task 10: Routes — framework surface

**Files:** modify `app/main.py`; test `tests/test_console_routes.py`

Catch tuples, fixed here for every route (invariant-6 checks these
declarations):

```text
shell, triage_default:      (DestinationRegistryError, EntityManifestError)          page
triage:                     (DestinationError, DestinationRegistryError,
                             CrossScopeError)  per-row                               page
propose:                    (OutboxError, DestinationError, CrossScopeError,
                             DestinationRegistryError)                               fragment-only
outbox_screen:              (OutboxError, CrossScopeError, DestinationRegistryError) page
outbox_approve/reject:      (OutboxError, CrossScopeError, DestinationRegistryError) fragment-only
registry_products:          (RegistryError, DestinationRegistryError)                page
registry_delete_preview:    (RegistryError, CrossScopeError,
                             DestinationRegistryError)                               fragment-only
registry_delete_execute:    (RegistryError, CrossScopeError,
                             DestinationRegistryError)                               fragment-only
```

- [ ] **Step 1: RED**

```text
test_unknown_entity_renders_e_entity_404
test_request_validation_renders_e_request_422_without_echo
test_unmatched_url_keeps_plain_404
test_wrong_method_keeps_405
test_static_miss_keeps_404
test_htmx_unmatched_url_gets_safe_body_at_404        (HX-Request: body replaced,
                                                      status preserved)
test_unhandled_error_reaches_global_fallback_at_500  (TestClient(...,
                                                      raise_server_exceptions=False))
test_described_errors_never_reach_the_global_fallback
```

- [ ] **Step 2:** run — FAIL.
- [ ] **Step 3: GREEN** — `entity_scope` raises `EntitySelectionError`; register
  handlers for `EntitySelectionError`, `RequestValidationError`,
  `StarletteHTTPException` (body replacement under `HX-Request` only, framework
  status preserved), and the global fallback (describes, returns
  `error.page_status`, never 200).
- [ ] **Step 4:** `uv run python -m pytest -q`.
- [ ] **Step 5:**

```bash
git add app/main.py tests/test_console_routes.py
git commit -m "feat: describe framework-surface failures"
```

---

## Task 11: Routes — triage and propose

- [ ] **Step 1: RED**

```text
test_triage_row_with_missing_module_dir_shows_e_dest_not_tamper
test_triage_row_with_symlinked_receipt_shows_e_tamper
test_triage_page_with_broken_registry_shows_e_config_page
test_propose_refusal_renders_alert_into_diff_target_at_200
test_propose_alert_preserves_triage_alpine_scope     (swap shape: innerHTML,
                                                      no duplicate x-data root)
test_stopwatch_counts_only_persisted_proposals       (HX-Trigger present on
    success, absent on refusal; triage.html listens for the event)
```

- [ ] **Step 2-3:** RED then GREEN: per-row `(destination, error)` pairs in
  `triage`; `propose` catches its tuple, renders `alert.html` into
  `#diff-{index}`, and sets the `HX-Trigger` header only after
  `propose_classification` returns.
- [ ] **Step 3a:** Update `tests/test_app.py:393
  test_tampered_proposal_form_writes_nothing` — **status expectation only**,
  `>= 400` becomes `== 200`, across all six parametrized cases. Its three state
  proofs stay verbatim; they are the test's subject and must keep passing
  unchanged. This is the third and final permitted pre-existing test change,
  added to the design's regression table for exactly this reason.

  The test also **gains** an observable-refusal assertion: `role="alert"`, the
  described code and message for the condition, and no echo of the submitted
  value. Replacing `>= 400` with `== 200` on its own would weaken the test — a
  200 carrying no alert would pass — so the added assertion is what keeps the
  refusal proven once the status stops proving it. The six parameters are six
  cases of one declared presentation regression, not six new exceptions.
- [ ] **Step 4:** `uv run python -m pytest -q`.
- [ ] **Step 5:**

```bash
git add app/main.py templates/triage.html templates/blocks/diff.html tests/test_console_routes.py
git commit -m "feat: describe triage and propose failures"
```

---

## Task 12: Routes — outbox

- [ ] **Step 1: RED**

```text
test_outbox_screen_renders_projection_blocked_listing
test_approve_busy_shows_e_busy_at_200
test_approve_conflict_shows_e_conflict
test_approve_rolled_back_shows_e_git
test_approve_recovery_blocked_shows_e_recover        (via real approve wrapper)
test_approve_committed_cleanup_shows_e_committed     (real commit + injected
    cleanup OSError so git_transaction converts post-commit; state proof asserts
    exactly the reviewed paths at one new HEAD)
test_reject_failure_is_visible_not_silent
test_outbox_fragments_reproduce_outbox_list_root     (outerHTML shape)
test_outbox_hx_vals_are_tojson
```

  The five S5-outcome tests monkeypatch `app.outbox.execute_transaction` (and
  the registry twin in Task 13) to raise the real S5 type — the exception then
  flows through the **actual** `approve` wrapper and its `from exc` chain; the
  committed case instead injects a cleanup `OSError` inside a real transaction.
- [ ] **Step 2-3:** RED then GREEN: routes render the projection; blocked
  listing withholds all controls with one described notice; approve/reject
  catch their tuple and describe.
- [ ] **Step 4a:** Re-point
  `test_concurrent_outbox_requests_keep_entity_diffs_isolated`'s monkeypatch
  from `main.load_proposals` to `main.project_outbox`. **Target only** — its
  isolation assertions and concurrency mechanism stay verbatim. Add an explicit
  `hits == 2` assertion so the barrier can never silently stop firing again,
  and delete the now-dead `load_proposals` import from `app/main.py`. Fourth
  row of the design's regression table; added because leaving it untouched
  would ship an isolation proof measured at 0 barrier hits.
- [ ] **Step 4:** `uv run python -m pytest -q` — the first listed regression
  test is updated now (`test_app.py:477-503`, the transaction-error alert
  assertion); every other pre-existing test unmodified. The second listed
  test (`:588`) covers the registry route and is updated in Task 13.
- [ ] **Step 5:**

```bash
git add app/main.py templates/outbox.html templates/blocks/outbox_list.html \
  tests/test_console_routes.py tests/test_app.py
git commit -m "feat: describe outbox failures through the projection"
```

---

## Task 13: Routes — registry

- [ ] **Step 1: RED**

```text
test_delete_preview_hx_vals_survive_hostile_slug     (quotes/braces/second id ->
                                                      exactly one id, equal to preview)
test_delete_execute_success_copy_from_validated_slug (submitted slug unused,
                                                      request no longer sends slug)
test_delete_execute_error_is_templated_and_escaped
test_registry_products_broken_yaml_shows_e_config
test_all_five_s5_outcomes_via_real_execute_delete
test_delete_preview_persistence_outcome              (proposal-written: one new
    untracked outbox file, all else identical)
test_propose_persistence_outcome                     (same, for propose)
```

- [ ] **Step 2-3:** RED then GREEN: `tojson` `hx-vals` in both registry
  templates; success copy from a pre-execute `get_delete_proposal`; both
  branches templated. Update the second listed regression test here —
  `test_app.py:588` asserts a raw internal string renders, which the
  disclosure boundary forbids.
- [ ] **Step 4:** `uv run python -m pytest -q`.
- [ ] **Step 5:**

```bash
git add app/main.py templates/registry.html templates/blocks/delete_impact.html \
  templates/blocks/delete_success.html \
  tests/test_console_routes.py tests/test_app.py tests/test_console_invariants.py
git commit -m "feat: describe registry failures and close hx-vals binding"
```

I4 (review): the original list omitted `templates/blocks/delete_success.html`
(the untracked new template `registry_delete_execute`'s success branch
renders) and `tests/test_console_invariants.py` (Task 13a's `hx-vals` scan,
carried into this same task's working tree). Executed literally, the
original list would have committed a tree where a live route raises
`TemplateNotFound` the first time it succeeds.

---

## Task 14: Cross-cutting proofs

**Files:** `tests/test_console_routes.py`

- [ ] **Step 1:** state proofs as an explicit matrix keyed by
  `(committed, persistence)`:
  `test_state_proof_matrix[no-none]` — one refusal per non-persisting route,
  every fingerprint identical;
  `test_state_proof_matrix[no-proposal-written]` — `propose` and
  `registry_delete_preview` failing after persistence: HEAD, index, and
  tracked content identical, exactly one new untracked outbox file;
  `test_state_proof_matrix[yes]` — committed-cleanup: exactly the reviewed
  paths at one new HEAD;
  `test_state_proof_matrix[unknown]` — recovery-blocked: unrelated state
  identical. Conftest fingerprint helpers with porcelain-v2 status.
- [ ] **Step 2:** disclosure sweep — `test_alerts_never_contain_paths_slugs_or_echoes`
  parametrized over every described error on every route. Parse the response
  (html.parser), take the role="alert" element's visible text and dynamic
  attribute values, and assert those contain no path separator, no fixture
  slug, and no submitted value. Raw-HTML substring checks are wrong here —
  every closing tag contains a slash.
- [ ] **Step 3:** route totality — for each `@console_route` declaration, inject
  each member; assert the global fallback spy is never hit.
- [ ] **Step 4:** `uv run python -m pytest -q`.
- [ ] **Step 5:**

```bash
git add tests/test_console_routes.py
git commit -m "test: prove state, disclosure, and totality"
```

---

## Task 15: Gates, then documentation

Documentation is written **only after** every gate passes.

- [ ] **Step 1: Public gates**

```bash
uv run pytest tests/test_app.py -q
uv run pytest tests/test_outbox.py tests/test_registry.py tests/test_git_transaction.py -q
uv run pytest tests/test_vault.py -q
uv run python -m pytest -q
git diff --check
git diff --stat origin/main...HEAD   # only files named in this plan
```

- [ ] **Step 2: Private gates, read-only** (each command's exit status must be
  checked on its own — no pipelines that mask it):

```bash
(cd "$ONEOS_VAULT/_system/scripts" && python3 -m unittest discover -q)
python3 "$ONEOS_VAULT/_system/scripts/check_v2.py" "$ONEOS_VAULT"
python3 "$ONEOS_VAULT/_system/scripts/policy_enforcer.py" \
  --policy "$ONEOS_VAULT/_system/scripts/action-policy.yaml" test-suite
tools/run_gitleaks.sh .
uv run python -m tools.public_repo_audit --repo . --history
uv run python -m tools.public_repo_audit --repo . --vault "$ONEOS_VAULT" --history
```

- [ ] **Step 3: Fingerprint equality**

```bash
PROOF="$(cat /private/tmp/s6-proof-path)"
git -C "$ONEOS_VAULT" rev-parse HEAD > "$PROOF/head.after"
git -C "$ONEOS_VAULT" status --porcelain=v2 -z --untracked-files=all > "$PROOF/status.after"
git -C "$ONEOS_VAULT" diff --binary > "$PROOF/worktree.after"
git -C "$ONEOS_VAULT" diff --cached --binary > "$PROOF/cached.after"
for f in head status worktree cached; do
  cmp "$PROOF/$f.before" "$PROOF/$f.after" && echo "$f identical" \
    || { echo "$f DIFFERS — stop"; exit 1; }
done
```

- [ ] **Step 4:** whole-branch review (disclosure, HTMX behavior, typed outcome
  accuracy, S1-S5 preservation, instance leakage, non-goal drift) and
  superpowers:verification-before-completion.
- [ ] **Step 5: only now, documentation** — `| S6 | **COMPLETE** |` in
  `BUILD.md`; update `tests/test_publication_docs.py` (asserts `| S6 |
  **NEXT** |`) in the same commit; update `docs/STATUS.md`; mark the design and
  this plan historical.

```bash
git add BUILD.md docs/STATUS.md docs/superpowers tests/test_publication_docs.py
git commit -m "docs: record S6 as complete"
```

- [ ] **Step 6: post-documentation verification.** The documentation commit
  changed tracked files and a test after the gates ran, so the gates run again
  against the final tree:

```bash
uv run python -m pytest -q
git diff --check
tools/run_gitleaks.sh .
uv run python -m tools.public_repo_audit --repo . --history
uv run python -m tools.public_repo_audit --repo . --vault "$ONEOS_VAULT" --history
```

  The combined audit is unconditional: it reads the repository's HEAD as well as
  the vault, so the documentation commit changed its input even though Grey
  Matter did not. Then repeat the **complete** Step 3 fingerprint comparison.
  The private unit, `check_v2`, and policy-enforcer suites need not repeat —
  documentation changes no application code. The branch is complete only when
  this round passes with the documentation commit as HEAD.

---

## Stop conditions

- Any pre-existing test failing that is not one of the two in the design's
  regression table. S6 changes no refusal decision; a third failure means scope
  was breached.
- Any change to vault content, conventions, or registries.
- Any new dependency, route, screen, or schema.
- Any private gate failing, or any fingerprint differing.
- Any need to render a value the disclosure boundary forbids.
- Any point where the honest fix adds a refusal condition — that is S7 or
  later, not an S6 edit. This has been the recurring breach across this step's
  design history; treat "it is only bounded" as the warning sign it has been
  every previous time.
- Publication: the branch stays local until explicitly authorized.

## Known limitations carried from the design

- The review gate does not bind reviewed content (S7).
- `catalog = build_catalog()` runs at module scope, so a manifest failure
  aborts import before any handler exists.
- `E-ENTITY` distinguishes an absent entity from a present one.
- Adapter and ingest failures have no Console surface.
