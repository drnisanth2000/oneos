# S6 Visible Console Failures Implementation Plan

> **SUPERSEDED — DO NOT EXECUTE.** This plan was written against an earlier
> design structure that two independent reviews rejected. It contradicts the
> current design on at least four points: it omits the HTMX `responseHandling`
> configuration entirely and still asserts the pre-fix premise; it renders a
> working reject control on unreadable outbox records; it discards the per-code
> page-status table; and its route bodies use blanket `except Exception`, which
> is the catch-all the design forbids and would launder every programmer error
> into a 200 fragment. It will be replaced once the rewritten design receives a
> fresh whole-document approval.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every typed Command Center refusal reach the operator as a
specific, safe, actionable message, with no route silently swallowing a failure.

**Architecture:** One frozen description table maps every application exception
to a stable code, severity, message, retry guidance, and commit outcome.
`describe()` resolves by MRO walk, recursing once into `__cause__` for the two
declared transparent transaction wrappers. Two renderers share that one table,
selected by the `HX-Request` header: fragments return 200 so HTMX 2.0.4 swaps
them, full pages return true status.

**Tech Stack:** Python 3.12, FastAPI, Jinja2, HTMX 2.0.4, Alpine + alpine-morph,
pytest, `uv`.

**Design:** `docs/superpowers/specs/2026-08-16-s6-visible-console-failures-design.md`
at `d81599e`.

**Branch:** `codex/s6-visible-console-failures` from `origin/main` at `3585938`.
**Public baseline:** 603 tests. **Private baseline:** 37 tests.

---

## Preconditions

- [ ] Confirm branch, base, and clean worktree

```bash
git branch --show-current
git rev-parse --short origin/main
git status --short
uv run python -m pytest -q
```

Expected: `codex/s6-visible-console-failures`, `3585938`, no tracked
modifications, `603 passed`.

- [ ] Confirm Grey Matter is untouched and record its pre-state

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
| `app/console_errors.py` (create) | The description table and `describe()`. Imports nothing from the app. |
| `app/console_render.py` (create) | Chooses fragment vs page renderer from `HX-Request`. Owns no copy. |
| `templates/blocks/alert.html` (create) | The one inline alert fragment. |
| `templates/error.html` (create) | The one full-page notice. |
| `static/app.css` (modify) | `.alert`, `.alert-attention`. |
| `app/outbox.py` (modify) | Per-record degradation in `load_proposals`. |
| `app/main.py` (modify) | Every route handler, plus the global handler. |
| `templates/blocks/outbox_list.html` (modify) | Alert slot, placeholder rows. |
| `templates/triage.html` (modify) | Per-row destination error. |
| `tests/test_console_errors.py` (create) | Table totality, invariants, transparency. |
| `tests/test_console_routes.py` (create) | Per-route visibility, state proofs, leakage. |

---

## Task 1: The description table

**Files:**
- Create: `app/console_errors.py`
- Test: `tests/test_console_errors.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_console_errors.py
import pytest

from app.console_errors import ConsoleError, describe
from app.outbox import StaleProposalSource


def test_stale_source_describes_as_recreate_refusal():
    result = describe(StaleProposalSource("x"))
    assert result.code == "E-STALE"
    assert result.severity == "refusal"
    assert result.retry == "recreate"
    assert result.committed == "no"
    assert "fresh proposal" in result.message


def test_console_error_is_frozen():
    error = ConsoleError("E-X", "refusal", "m", "none", "no")
    with pytest.raises(Exception):
        error.code = "E-Y"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_console_errors.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'app.console_errors'`

- [ ] **Step 3: Write the module**

```python
# app/console_errors.py
"""The Console's entire operator-facing error vocabulary.

One table, one resolver. No route, template, or service writes error copy.
This module imports nothing from the application so it cannot create a circular
import and can be tested alone.
"""
from __future__ import annotations

from dataclasses import dataclass

SEVERITIES = frozenset({"refusal", "attention"})
RETRIES = frozenset({"retry", "reload", "recreate", "stop", "none"})
COMMITTED = frozenset({"no", "yes", "unknown"})


@dataclass(frozen=True)
class ConsoleError:
    code: str
    severity: str
    message: str
    retry: str
    committed: str
    transparent: bool = False

    def __post_init__(self) -> None:
        if self.severity not in SEVERITIES:
            raise ValueError("severity is not a permitted value")
        if self.retry not in RETRIES:
            raise ValueError("retry is not a permitted value")
        if self.committed not in COMMITTED:
            raise ValueError("committed is not a permitted value")
        if self.severity == "refusal" and self.committed != "no":
            raise ValueError("a refusal cannot report a commit")


UNKNOWN = ConsoleError(
    "E-UNKNOWN", "attention",
    "An unexpected error was not handled. Inspect vault state with git status "
    "before continuing.",
    "stop", "unknown",
)
```

Then the table, keyed by dotted class path so the module imports nothing:

```python
_TABLE: dict[str, ConsoleError] = {
    "app.outbox.StaleProposalSource": ConsoleError(
        "E-STALE", "refusal",
        "Approval refused: the source changed after this proposal was created. "
        "Create a fresh proposal.",
        "recreate", "no",
    ),
}


def _key(cls: type) -> str:
    return f"{cls.__module__}.{cls.__qualname__}"


def describe(exc: BaseException) -> ConsoleError:
    for cls in type(exc).__mro__:
        entry = _TABLE.get(_key(cls))
        if entry is not None:
            return entry
    return UNKNOWN
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_console_errors.py -q`
Expected: PASS, 2 tests.

- [ ] **Step 5: Commit**

```bash
git add app/console_errors.py tests/test_console_errors.py
git commit -m "feat: add Console error description table"
```

---

## Task 2: Complete the table

**Files:**
- Modify: `app/console_errors.py`
- Test: `tests/test_console_errors.py`

- [ ] **Step 1: Write the failing totality test**

This is the load-bearing test. It enumerates the class hierarchy and subtracts
the table, so an exception added later without a description fails here rather
than reaching an operator as `E-UNKNOWN`.

```python
import importlib
import inspect
import pkgutil

import app as app_package
from app.console_errors import COMMITTED, RETRIES, SEVERITIES, describe


def _application_exception_classes() -> list[type]:
    found: list[type] = []
    for info in pkgutil.walk_packages(app_package.__path__, prefix="app."):
        module = importlib.import_module(info.name)
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if issubclass(obj, Exception) and obj.__module__.startswith("app."):
                if obj not in found:
                    found.append(obj)
    return found


def test_every_application_exception_has_a_description():
    undescribed = [
        f"{cls.__module__}.{cls.__qualname__}"
        for cls in _application_exception_classes()
        if describe(cls("probe")).code == "E-UNKNOWN"
    ]
    assert undescribed == []


def test_every_refusal_reports_no_commit():
    from app.console_errors import _TABLE
    offenders = [
        entry.code for entry in _TABLE.values()
        if entry.severity == "refusal" and entry.committed != "no"
    ]
    assert offenders == []


def test_all_entry_fields_are_permitted_values():
    from app.console_errors import _TABLE
    for entry in _TABLE.values():
        assert entry.severity in SEVERITIES
        assert entry.retry in RETRIES
        assert entry.committed in COMMITTED
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_console_errors.py::test_every_application_exception_has_a_description -q`
Expected: FAIL, the assertion lists every application exception except
`StaleProposalSource`.

- [ ] **Step 3: Fill in the table**

Add every remaining entry to `_TABLE`, keyed by dotted path. Messages must name
no path, slug, module, commit id, or request value.

```python
_TABLE.update({
    "app.outbox.MissingProposalSource": ConsoleError(
        "E-MISSING", "refusal",
        "Approval refused: the source is missing. Restore it or reject the "
        "proposal.", "recreate", "no"),
    "app.outbox.OutboxError": ConsoleError(
        "E-INVALID", "refusal",
        "This proposal record is not valid and cannot be approved. Reject it "
        "and create a new one.", "recreate", "no"),
    "app.outbox.OutboxDestinationError": ConsoleError(
        "E-INVALID", "refusal",
        "This proposal record is not valid and cannot be approved. Reject it "
        "and create a new one.", "recreate", "no"),
    "app.proposal_identity.ProposalIdentityError": ConsoleError(
        "E-INVALID", "refusal",
        "This proposal record is not valid and cannot be approved. Reject it "
        "and create a new one.", "recreate", "no"),
    "app.destinations.DestinationError": ConsoleError(
        "E-DEST", "refusal",
        "The destination could not be resolved from the registries. "
        "Re-classify this item.", "recreate", "no"),
    "app.scope.CrossScopeError": ConsoleError(
        "E-SCOPE", "refusal",
        "Refused: the request resolved outside the selected entity.",
        "none", "no"),
    "app.outbox.OutboxScopeError": ConsoleError(
        "E-SCOPE", "refusal",
        "Refused: the request resolved outside the selected entity.",
        "none", "no"),
    "app.git_transaction.VaultBusyError": ConsoleError(
        "E-BUSY", "refusal",
        "Another approval is in progress. Nothing was changed. Try again in a "
        "moment.", "retry", "no"),
    "app.git_transaction.ReviewedStateConflict": ConsoleError(
        "E-CONFLICT", "refusal",
        "The reviewed files changed since this proposal was previewed. Reload "
        "and review again.", "reload", "no"),
    "app.git_transaction.GitTransactionError": ConsoleError(
        "E-GIT", "refusal",
        "The commit failed and was rolled back. Nothing was changed.",
        "retry", "no"),
    "app.git_transaction.GitTransactionRecoveryError": ConsoleError(
        "E-RECOVER", "attention",
        "Rollback was blocked by a concurrent change. Do not retry. Inspect "
        "vault state with git status and resolve it before continuing.",
        "stop", "unknown"),
    "app.git_transaction.GitTransactionCommittedError": ConsoleError(
        "E-COMMITTED", "attention",
        "The commit succeeded; only cleanup afterwards failed. Do not retry — "
        "retrying would commit this action twice. Inspect vault state with "
        "git status.", "stop", "yes"),
    "app.registry.RegistryError": ConsoleError(
        "E-REGISTRY", "refusal",
        "The registry operation was refused. Review the impact report and try "
        "again.", "reload", "no"),
    "app.vault.DestinationRegistryError": ConsoleError(
        "E-CONFIG", "attention",
        "The vault registries could not be read. The Console cannot operate on "
        "this entity until they are valid.", "none", "no"),
    "app.entities.EntityManifestError": ConsoleError(
        "E-CONFIG", "attention",
        "The vault registries could not be read. The Console cannot operate on "
        "this entity until they are valid.", "none", "no"),
    "app.entities.EntitySelectionError": ConsoleError(
        "E-ENTITY", "refusal",
        "That entity is not in the manifest.", "none", "no"),
    "app.ingest.base.IngestError": ConsoleError(
        "E-INGEST", "refusal",
        "Intake failed. Nothing was written to the vault.", "none", "no"),
    "app.rename.RenameError": ConsoleError(
        "E-ADMIN", "refusal",
        "The administrative operation was refused.", "none", "no"),
})
```

`E-CONFIG` is `attention` with `committed = no`, which the `__post_init__`
invariant permits — the constraint is one-directional, binding only refusals.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_console_errors.py -q`
Expected: PASS. If `test_every_application_exception_has_a_description` still
fails, add the named classes rather than weakening the test.

- [ ] **Step 5: Commit**

```bash
git add app/console_errors.py tests/test_console_errors.py
git commit -m "feat: describe every application exception"
```

---

## Task 3: Transparent transaction wrappers

Without this, `E-COMMITTED` and `E-RECOVER` are unreachable — `approve()` wraps
every S5 outcome in `OutboxTransactionError` — and an operator whose commit
succeeded would be told to retry.

**Files:**
- Modify: `app/console_errors.py`
- Test: `tests/test_console_errors.py`

- [ ] **Step 1: Write the failing test**

```python
import pytest

from app.console_errors import describe
from app.git_transaction import (
    GitTransactionCommittedError, GitTransactionRecoveryError,
    ReviewedStateConflict, VaultBusyError,
)
from app.outbox import OutboxTransactionError
from app.registry import RegistryTransactionError


def _wrap(wrapper, cause):
    try:
        raise cause
    except type(cause) as inner:
        try:
            raise wrapper("boundary") from inner
        except type(wrapper("x")) as outer:
            return outer


@pytest.mark.parametrize("wrapper", [OutboxTransactionError, RegistryTransactionError])
@pytest.mark.parametrize("cause,code", [
    (VaultBusyError("b"), "E-BUSY"),
    (ReviewedStateConflict("c"), "E-CONFLICT"),
    (GitTransactionRecoveryError(("p",)), "E-RECOVER"),
])
def test_transaction_outcomes_survive_the_wrapper(wrapper, cause, code):
    assert describe(_wrap(wrapper, cause)).code == code


def test_committed_outcome_never_advises_retry():
    result = describe(_wrap(OutboxTransactionError, GitTransactionCommittedError.__new__(
        GitTransactionCommittedError)))
    assert result.code == "E-COMMITTED"
    assert result.committed == "yes"
    assert result.retry == "stop"


def test_wrapper_without_cause_resolves_to_git():
    assert describe(OutboxTransactionError("no cause")).code == "E-GIT"


def test_non_transparent_entry_ignores_its_cause():
    from app.outbox import OutboxScopeError, StaleProposalSource
    outer = _wrap(OutboxScopeError, StaleProposalSource("s"))
    assert describe(outer).code == "E-SCOPE"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_console_errors.py -q -k transaction or wrapper or committed`
Expected: FAIL — every wrapped outcome currently reports `E-GIT`.

- [ ] **Step 3: Declare the wrappers transparent and walk once**

```python
_TABLE.update({
    "app.outbox.OutboxTransactionError": ConsoleError(
        "E-GIT", "refusal",
        "The commit failed and was rolled back. Nothing was changed.",
        "retry", "no", transparent=True),
    "app.registry.RegistryTransactionError": ConsoleError(
        "E-GIT", "refusal",
        "The commit failed and was rolled back. Nothing was changed.",
        "retry", "no", transparent=True),
})


def _lookup(exc: BaseException) -> ConsoleError:
    for cls in type(exc).__mro__:
        entry = _TABLE.get(_key(cls))
        if entry is not None:
            return entry
    return UNKNOWN


def describe(exc: BaseException) -> ConsoleError:
    entry = _lookup(exc)
    if entry.transparent and exc.__cause__ is not None:
        return _lookup(exc.__cause__)
    return entry
```

The walk is depth-one and declared per entry, never inferred. A non-transparent
entry never reads a cause, so no internal failure can surface through an error
that was not designed to carry it.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_console_errors.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/console_errors.py tests/test_console_errors.py
git commit -m "feat: resolve S5 outcomes through transparent wrappers"
```

---

## Task 4: Alert templates and styling

**Files:**
- Create: `templates/blocks/alert.html`, `templates/error.html`
- Modify: `static/app.css`

- [ ] **Step 1: Write the fragment template**

```html
{# templates/blocks/alert.html — the one inline alert. #}
{% if error %}
<p class="alert{% if error.severity == 'attention' %} alert-attention{% endif %}"
   role="alert">
  <code class="alert-code">{{ error.code }}</code>
  <span class="alert-message">{{ error.message }}</span>
</p>
{% endif %}
```

Jinja autoescaping renders `{{ error.message }}` escaped. No `| safe` anywhere.

- [ ] **Step 2: Write the page template**

```html
{# templates/error.html — full-page notice for a screen that cannot be built. #}
{% extends "base.html" %}
{% block content %}
<main class="error-page">
  {% include "blocks/alert.html" %}
</main>
{% endblock %}
```

If `templates/base.html` does not exist, mirror the `<head>`/`<body
hx-ext="alpine-morph">` shell used by `templates/outbox.html` instead of
introducing a new base.

- [ ] **Step 3: Add styling**

```css
/* static/app.css */
.alert {
  display: flex; gap: .6rem; align-items: baseline;
  padding: .6rem .8rem; margin: 0 0 .8rem;
  border-left: 3px solid currentColor;
}
.alert-code { font-family: ui-monospace, monospace; font-size: .82em; opacity: .8; }
.alert-attention { font-weight: 600; }
```

- [ ] **Step 4: Verify nothing regressed**

Run: `uv run python -m pytest -q`
Expected: `603 passed`.

- [ ] **Step 5: Commit**

```bash
git add templates/blocks/alert.html templates/error.html static/app.css
git commit -m "feat: add Console alert fragment and page notice"
```

---

## Task 5: Renderer selection

**Files:**
- Create: `app/console_render.py`
- Test: `tests/test_console_routes.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_console_routes.py
from app.console_errors import describe
from app.console_render import is_fragment_request, status_for


class _Req:
    def __init__(self, headers): self.headers = headers


def test_htmx_requests_are_fragments():
    assert is_fragment_request(_Req({"hx-request": "true"})) is True
    assert is_fragment_request(_Req({})) is False


def test_fragment_status_is_always_200():
    from app.entities import EntitySelectionError
    assert status_for(describe(EntitySelectionError("x")), fragment=True) == 200


def test_page_status_reflects_the_error():
    from app.entities import EntitySelectionError
    from app.vault import DestinationRegistryError
    assert status_for(describe(EntitySelectionError("x")), fragment=False) == 404
    assert status_for(describe(DestinationRegistryError("x")), fragment=False) == 500
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_console_routes.py -q`
Expected: FAIL, `No module named 'app.console_render'`

- [ ] **Step 3: Write the module**

```python
# app/console_render.py
"""Chooses how a described error is returned. Owns no error copy.

HTMX 2.0.4 does not swap non-2xx responses, so a fragment must return 200 or
the operator sees an unchanged screen and no message. A full page is not being
swapped, so it keeps its true status.
"""
from __future__ import annotations

from .console_errors import ConsoleError

_PAGE_STATUS = {"E-ENTITY": 404}


def is_fragment_request(request) -> bool:
    return request.headers.get("hx-request") is not None


def status_for(error: ConsoleError, *, fragment: bool) -> int:
    if fragment:
        return 200
    return _PAGE_STATUS.get(error.code, 500)
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_console_routes.py -q`
Expected: PASS, 3 tests.

- [ ] **Step 5: Commit**

```bash
git add app/console_render.py tests/test_console_routes.py
git commit -m "feat: select Console renderer from the HX-Request header"
```

---

## Task 6: Outbox per-record degradation

`load_proposals` raises on the first malformed record and abandons the whole
listing, so one bad file hides every valid proposal — and because the alert
renderer re-reads proposals, rendering the alert raises the same exception.

**Files:**
- Modify: `app/outbox.py:261-287`
- Test: `tests/test_outbox.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_outbox.py`, following the existing fixture style in that
file for building a scoped synthetic vault with one valid proposal:

```python
def test_one_bad_record_does_not_hide_valid_proposals(scoped_vault):
    scope, valid_id = scoped_vault
    outbox = scope.resolve("outbox")
    (outbox / "20260816T101010-" + "f" * 32 + ".yaml").write_text(
        "id: mismatched\naction: classify\n", encoding="utf-8")

    entries = load_entries(scope)

    codes = [e.error.code for e in entries if e.error is not None]
    ids = [e.proposal.id for e in entries if e.proposal is not None]
    assert valid_id in ids
    assert codes == ["E-INVALID"]


def test_reading_the_listing_never_raises_on_a_bad_record(scoped_vault):
    scope, _ = scoped_vault
    (scope.resolve("outbox") / "20260816T101011-" + "a" * 32 + ".yaml").write_text(
        ": not yaml :", encoding="utf-8")
    load_entries(scope)  # must not raise
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_outbox.py -q -k bad_record or never_raises`
Expected: FAIL, `ImportError: cannot import name 'load_entries'`

- [ ] **Step 3: Add the degrading reader**

Keep `load_proposals` exactly as it is — S1-S5 callers depend on its
fail-closed behavior. Add a listing-only reader beside it:

```python
@dataclass(frozen=True)
class OutboxEntry:
    """One row of the outbox listing: a usable proposal or a refused record."""
    name: str
    proposal: Proposal | None
    error: ConsoleError | None


def load_entries(scope: Scope) -> list[OutboxEntry]:
    """Read the outbox for display. Degrades per record: an unreadable file
    becomes one refused row instead of hiding every valid proposal."""
    outbox = _require_outbox_path(scope)
    if not outbox.exists():
        return []
    entries: list[OutboxEntry] = []
    for discovered in sorted(outbox.glob("*.yaml")):
        try:
            proposal = _load_one(scope, discovered)
        except Exception as exc:  # described, never swallowed
            entries.append(OutboxEntry(discovered.name, None, describe(exc)))
            continue
        if proposal is not None:
            entries.append(OutboxEntry(discovered.name, proposal, None))
    return entries
```

Extract the existing per-file body of `load_proposals` into `_load_one(scope,
path) -> Proposal | None`, returning `None` for a `delete` action, and have
`load_proposals` call it in its current fail-closed loop. The validation logic
moves; it does not change.

Import `describe` and `ConsoleError` from `.console_errors` at the top of
`app/outbox.py`.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_outbox.py -q`
Expected: PASS, including every pre-existing outbox test unmodified.

- [ ] **Step 5: Commit**

```bash
git add app/outbox.py tests/test_outbox.py
git commit -m "feat: degrade the outbox listing per record"
```

---

## Task 7: Outbox routes

**Files:**
- Modify: `app/main.py:138-193`
- Modify: `templates/blocks/outbox_list.html`
- Test: `tests/test_console_routes.py`

- [ ] **Step 1: Write the failing test**

```python
def test_approve_shows_every_transaction_outcome(client_and_scope, monkeypatch):
    client, entity, proposal_id = client_and_scope
    from app import main
    from app.git_transaction import VaultBusyError
    from app.outbox import OutboxTransactionError

    def _busy(*a, **k):
        raise OutboxTransactionError("boundary") from VaultBusyError("held")
    monkeypatch.setattr(main, "approve", _busy)

    response = client.post(f"/outbox/{entity}/approve",
                           data={"id": proposal_id},
                           headers={"HX-Request": "true"})
    assert response.status_code == 200
    assert "E-BUSY" in response.text
    assert 'id="outbox-list"' in response.text     # the swap target is present
    assert "boundary" not in response.text          # no raw exception text


def test_reject_failure_is_visible(client_and_scope, monkeypatch):
    client, entity, proposal_id = client_and_scope
    from app import main
    from app.outbox import OutboxError
    monkeypatch.setattr(main, "reject",
                        lambda *a, **k: (_ for _ in ()).throw(OutboxError("gone")))
    response = client.post(f"/outbox/{entity}/reject",
                           data={"id": proposal_id},
                           headers={"HX-Request": "true"})
    assert response.status_code == 200
    assert "E-INVALID" in response.text
    assert "gone" not in response.text
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_console_routes.py -q -k approve or reject`
Expected: FAIL — approve renders no code, reject renders nothing at all.

- [ ] **Step 3: Rewrite the two handlers**

```python
def _outbox_list(request, scope, *, error=None):
    return templates.TemplateResponse(
        request, "blocks/outbox_list.html",
        {"entity": scope.current_entity(),
         "entries": load_entries(scope),
         "preview": lambda p: _safe_preview(scope, p),
         "error": error},
    )


@app.post("/outbox/{entity}/approve", response_class=HTMLResponse)
def outbox_approve(request: Request, scope: EntityScope, id: str = Form(...)):
    error = None
    try:
        approve(scope, id)
    except Exception as exc:
        error = describe(exc)
    return _outbox_list(request, scope, error=error)


@app.post("/outbox/{entity}/reject", response_class=HTMLResponse)
def outbox_reject(request: Request, scope: EntityScope, id: str = Form(...)):
    error = None
    try:
        reject(scope, id)
    except Exception as exc:
        error = describe(exc)
    return _outbox_list(request, scope, error=error)
```

`_safe_preview` returns the diff or `None` when `preview_diff` raises, so a
degraded row cannot break the render.

Update `templates/blocks/outbox_list.html` to include the alert and render
placeholder rows:

```html
<div id="outbox-list">
  {% include "blocks/alert.html" %}
  {% if not entries %}
  <p class="muted">No pending proposals. Approve or reject clears them from here.</p>
  {% endif %}
  {% for entry in entries %}
    {% if entry.error %}
    <div class="proposal proposal-refused">
      <div class="prop-head"><code class="prop-id">{{ entry.name }}</code></div>
      <p class="alert" role="alert">
        <code class="alert-code">{{ entry.error.code }}</code>
        <span>{{ entry.error.message }}</span>
      </p>
      <div class="prop-actions">
        <button class="reject" hx-post="/outbox/{{ entity }}/reject"
                hx-vals='{"id": "{{ entry.name[:-5] }}"}'
                hx-target="#outbox-list" hx-swap="outerHTML">Reject</button>
      </div>
    </div>
    {% else %}
    ... existing proposal markup, with `p` replaced by `entry.proposal` ...
    {% endif %}
  {% endfor %}
</div>
```

The refused row offers reject only. Approve is absent, not disabled, so it
cannot be re-enabled from the client.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_console_routes.py tests/test_app.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/main.py templates/blocks/outbox_list.html tests/test_console_routes.py
git commit -m "feat: surface outbox approval and rejection failures"
```

---

## Task 8: Remaining routes

Apply the identical pattern to every remaining site. Each gets its own RED test
first, asserting status, code, absence of raw exception text, and — for
fragments — the presence of the route's `hx-target` element.

**Files:** `app/main.py`, `templates/triage.html`, `templates/registry.html`,
`templates/blocks/delete_impact.html`, `tests/test_console_routes.py`

- [ ] **Step 1: `outbox_screen`** — render `load_entries` instead of
  `load_proposals`; a bad record must not blank the screen.
- [ ] **Step 2: `propose`** — catch and render `blocks/alert.html` into the
  existing `#diff-{index}` target. Do not introduce a target that encloses
  `x-data="triage(...)"`, or the triage keyboard scope is destroyed.
- [ ] **Step 3: `triage`** — replace `destination = None` with a per-row
  `(destination, error)` pair and render the code in the row.
- [ ] **Step 4: `registry_products`** — catch `products_for` failures.
- [ ] **Step 5: `registry_delete_preview`** — catch and render inline.
- [ ] **Step 6: `registry_delete_execute`** — delete the f-string entirely:

```python
@app.post("/registry/{entity}/product/delete-execute", response_class=HTMLResponse)
def registry_delete_execute(request: Request, scope: EntityScope,
                            id: str = Form(...), slug: str = Form(...)):
    try:
        execute_delete(scope, id)
    except Exception as exc:
        return templates.TemplateResponse(
            request, "blocks/alert.html", {"error": describe(exc)})
    return templates.TemplateResponse(
        request, "blocks/delete_deleted.html", {})
```

  The success message must not interpolate `slug` into HTML either.
- [ ] **Step 7: `shell` and `triage_default`** — render the `E-CONFIG` page
  when `Vault().bundles()` raises.
- [ ] **Step 8: `entity_scope`** — return a rendered 404 page rather than an
  empty `HTTPException`.
- [ ] **Step 9: Commit**

```bash
git add app/main.py templates tests/test_console_routes.py
git commit -m "feat: surface failures on every remaining Console route"
```

---

## Task 9: Global handler

**Files:** `app/main.py`, `tests/test_console_routes.py`

- [ ] **Step 1: Write the failing test**

```python
def test_unhandled_error_renders_safely_at_500(client_and_scope, monkeypatch):
    client, entity, _ = client_and_scope
    from app import main
    monkeypatch.setattr(main, "products_for",
                        lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    response = client.get(f"/registry/{entity}/products")
    assert response.status_code == 500
    assert "E-UNKNOWN" in response.text
    assert "boom" not in response.text


def test_described_errors_never_reach_the_global_handler(client_and_scope, monkeypatch):
    """A route relying on the backstop has not satisfied S6."""
    client, entity, proposal_id = client_and_scope
    from app import main
    seen = []
    original = main.console_fallback
    def _spy(request, exc):
        seen.append(exc)
        return original(request, exc)
    monkeypatch.setattr(main, "console_fallback", _spy)
    from app.outbox import OutboxError
    monkeypatch.setattr(main, "approve",
                        lambda *a, **k: (_ for _ in ()).throw(OutboxError("x")))
    client.post(f"/outbox/{entity}/approve", data={"id": proposal_id},
                headers={"HX-Request": "true"})
    assert seen == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_console_routes.py -q -k unhandled or global_handler`
Expected: FAIL — no handler exists; the first test returns a raw 500 with a
traceback.

- [ ] **Step 3: Register the handler**

```python
@app.exception_handler(Exception)
def console_fallback(request: Request, exc: Exception):
    error = describe(exc)
    return templates.TemplateResponse(
        request, "error.html", {"error": error},
        status_code=status_for(error, fragment=False),
    )
```

It returns 500, never 200: a programmer error must not be laundered into a
successful-looking response. `HTTPException` keeps FastAPI's own handling.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_console_routes.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/main.py tests/test_console_routes.py
git commit -m "feat: add a safe Console fallback handler"
```

---

## Task 10: State proofs and disclosure

**Files:** `tests/test_console_routes.py`

- [ ] **Step 1: Write the state-proof test**

Use the existing `conftest.py` fingerprint helpers. The assertion is selected by
the entry's `committed` value — asserting "nothing changed" for `E-COMMITTED`
would pass only if S5 rollback were broken.

```python
from tests.conftest import (git_cached_diff, git_head, git_index_entries,
                            git_status_bytes, git_worktree_diff)


def _fingerprint(root):
    return (git_head(root), git_status_bytes(root),
            git_index_entries(root), git_worktree_diff(root),
            git_cached_diff(root))


def test_refusals_leave_every_byte_unchanged(client_and_scope, monkeypatch, vault_root):
    client, entity, proposal_id = client_and_scope
    before = _fingerprint(vault_root)
    client.post(f"/outbox/{entity}/approve", data={"id": "not-a-real-id"},
                headers={"HX-Request": "true"})
    assert _fingerprint(vault_root) == before
```

- [ ] **Step 2: Write the disclosure tests**

```python
def test_alerts_disclose_nothing_private(client_and_scope, monkeypatch, entity_slug):
    client, entity, proposal_id = client_and_scope
    from app import main
    from app.scope import CrossScopeError
    monkeypatch.setattr(main, "approve",
                        lambda *a, **k: (_ for _ in ()).throw(
                            CrossScopeError("/abs/path/other-entity/outbox/x.yaml")))
    body = client.post(f"/outbox/{entity}/approve", data={"id": proposal_id},
                       headers={"HX-Request": "true"}).text
    assert "E-SCOPE" in body
    assert "/" not in body.split('role="alert"')[1].split("</p>")[0]
    assert "other-entity" not in body


def test_error_text_is_escaped(client_and_scope, monkeypatch):
    client, entity, proposal_id = client_and_scope
    from app import main
    from app.registry import RegistryError
    monkeypatch.setattr(main, "execute_delete",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RegistryError("<script>alert(1)</script>")))
    body = client.post(f"/registry/{entity}/product/delete-execute",
                       data={"id": "x", "slug": "y"}).text
    assert "<script>" not in body
```

- [ ] **Step 3: Run**

Run: `uv run pytest tests/test_console_routes.py -q`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/test_console_routes.py
git commit -m "test: prove Console errors mutate nothing and disclose nothing"
```

---

## Task 11: Documentation

**Files:** `BUILD.md`, `docs/STATUS.md`

- [ ] **Step 1:** Set `| S6 | **COMPLETE** |` in the BUILD.md table.
- [ ] **Step 2:** Update the STATUS.md step table and record the new baselines.
- [ ] **Step 3:** Add the historical banner to this plan file:

```markdown
> **Historical execution plan:** S6 was implemented and merged through PR #NN
> at `<sha>`. Do not create its branch, execute its tasks, or use its old test
> counts and stop conditions as current instructions.
```

- [ ] **Step 4:** Set the design file's `**Status:**` to
  `Implemented and merged; historical design record`.
- [ ] **Step 5:** Run `uv run pytest tests/test_publication_docs.py -q` —
  `test_safety_foundation_status_tracks_merged_s1_through_s5` asserts
  `| S6 | **NEXT** |`, so it must be updated in the same commit.
- [ ] **Step 6: Commit**

```bash
git add BUILD.md docs/STATUS.md docs/superpowers tests/test_publication_docs.py
git commit -m "docs: record S6 as complete"
```

---

## Final verification

- [ ] **Public gates**

```bash
uv run pytest tests/test_app.py -q
uv run pytest tests/test_outbox.py tests/test_registry.py tests/test_git_transaction.py -q
uv run python -m pytest -q
git diff --check
git diff --stat origin/main...HEAD
```

Expected: all pass; the file list contains only the files named in this plan.

- [ ] **Private gates, read-only**

```bash
cd "$ONEOS_VAULT/_system/scripts" && python3 -m unittest discover -q; cd -
python3 "$ONEOS_VAULT/_system/scripts/check_v2.py" "$ONEOS_VAULT" | tail -2
tools/run_gitleaks.sh .
uv run python -m tools.public_repo_audit --repo . --history
uv run python -m tools.public_repo_audit --repo . --vault "$ONEOS_VAULT" --history
```

Expected: 37 private tests OK; `0 error(s), 0 warning(s)`; clean audits.

- [ ] **Fingerprint equality**

```bash
git -C "$ONEOS_VAULT" rev-parse HEAD > /private/tmp/s6-proof/head.after
git -C "$ONEOS_VAULT" status --porcelain=v1 -z --untracked-files=all > /private/tmp/s6-proof/status.after
git -C "$ONEOS_VAULT" diff --binary > /private/tmp/s6-proof/worktree.after
git -C "$ONEOS_VAULT" diff --cached --binary > /private/tmp/s6-proof/cached.after
for f in head status worktree cached; do
  cmp "/private/tmp/s6-proof/$f.before" "/private/tmp/s6-proof/$f.after" \
    && echo "$f identical" || { echo "$f DIFFERS — stop"; exit 1; }
done
```

- [ ] **Whole-branch review** for safe disclosure, HTMX swap behavior, typed
  outcome accuracy, S1-S5 preservation, instance leakage, and non-goal drift.
- [ ] **superpowers:verification-before-completion.**

---

## Stop conditions

Stop and ask rather than deciding:

- Any change to `$ONEOS_VAULT` content, conventions, or registries.
- Any S1-S5 test needing modification. S6 changes no refusal decision, so a test
  requiring a change means scope was breached, not that the test was wrong.
- Any new dependency, route, screen, or schema.
- Any private gate failing, or any fingerprint differing.
- Any need to render a value the design's disclosure boundary forbids.
- Publication: the branch stays local until explicitly authorized.

## Known limitations

- `E-ENTITY` distinguishes an absent entity from a present one. Accepted for a
  single local operator whose sidebar already lists every entity; it must become
  indistinguishable from `E-SCOPE` before the Console serves more than one.
- Adapter and ingest failures have no Console surface. `E-INGEST` is described
  but unreachable until the deferred upload route exists.
- Stdlib exceptions resolve to `E-UNKNOWN` by design.
