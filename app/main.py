"""main.py — FastAPI app and route registration.

Step 1 proved the HTMX/Alpine/morph wiring with a static shell. Step 2 wires the
sidebar to the vault: bundles from entities.yaml, modules resolved from each
bundle's flags only. No slug, path, or module list is hardcoded here.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Form, Request
from fastapi.exception_handlers import http_exception_handler as _default_http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.utils import is_body_allowed_for_status_code
from starlette.exceptions import HTTPException as StarletteHTTPException

from .classifier import Classifier
from .config import build_catalog, build_scope
from .console_errors import ConsoleError, describe
from .console_render import is_fragment, status_for
from .console_routing import console_route
from .destinations import DestinationError, resolve_classification_destination
from .entities import (
    EntityManifestError,
    EntitySelectionError,
    SystemRegistryPathError,
)
from .inbox import read_inbox
from .outbox import (
    OutboxDestinationError,
    OutboxError,
    UnreadableProposalRecord,
    approve,
    preview_diff,
    project_outbox,
    propose_classification,
    reject,
)
from .review_tokens import ReviewedProposalChanged, ReviewTokenError
from .registry import (
    RegistryError,
    execute_delete,
    get_delete_review,
    products_for,
    propose_delete,
    reference_count,
)
from .scope import CrossScopeError, RedirectedPathError, Scope
from .vault import DestinationRegistryError, Vault

BASE = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE / "templates"))

app = FastAPI(title="OneOS")
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")

catalog = build_catalog()


def entity_scope(entity: str) -> Scope:
    # Rule 6: no application code raises HTTPException. EntitySelectionError
    # propagates to its own dedicated handler below.
    return build_scope(entity)


EntityScope = Annotated[Scope, Depends(entity_scope)]


# --- Console error rendering (design §4-6) -----------------------------------


def _endpoint_for(request: Request):
    """The endpoint FastAPI matched for this request, if any. Starlette sets
    ``scope["endpoint"]`` during route matching, before dependencies (and
    therefore ``entity_scope``) run, so this is populated even when the
    handler body never executes."""
    return request.scope.get("endpoint")


#: Declared once, ahead of `_render_console_error`, so the decorator, each
#: route's own `except`, AND the sidebar re-entrancy guard below all read the
#: same family — the pattern Tasks 11-13 established after the same gap was
#: found on every other route family in turn.
#:
#: C2' (S6 review round 2): an earlier version of this comment claimed "After
#: Task 8's C2 fix, this is a closed family: `Vault.bundles()` can raise no
#: other type." That was false, and it was the sole load-bearing premise of
#: the C1 sidebar guard. `Vault.system_path` calls `resolve_system_registry`
#: directly, unlike `Scope.system_path`, so `bundles()` can raise
#: `SystemRegistryPathError` — and it re-resolves `_system` on EVERY call, so
#: a `_system` directory redirected AFTER startup reaches this on a live
#: request. That subclasses `EntityManifestError`, so this tuple already
#: catches it; the three route tuples below did not, and now name it
#: explicitly.
#:
#: Task 8 corrective: the two shapes named above (a module spec that is a
#: list or a scalar; a non-iterable `flags:`) — plus two more the reviewer
#: found one level deeper once those landed (a `modules:` that is itself not
#: a mapping, and a `requires_flag:` that is itself a list, unhashable
#: against the active-flags set) — are now converted at the exact access in
#: `Vault.resolve_flags` / `active_modules` / `block_of` (`app/vault.py`).
#: An unknown flag in `entities.yaml` (already fatal, previously an untyped
#: `ValueError`) was folded into the same corrective — same empty-body
#: mechanism, only the type was wrong. Measured with a real hand-edited
#: registry and no monkeypatching: none of the four now blanks any of the
#: five routes `bundles()` serves.
#:
#: **This section has now miscounted its own access points twice, in the same
#: direction, so it no longer counts them.** It first said three; review
#: measured five; the correction said five and review measured **eight**
#: (frame-instrumented during real `bundles()` calls). Each miscount was an
#: enumeration written from reading rather than from measurement, and the
#: first one is what let the key-shaped defect ship. Per design §7's closing
#: rule — "delete the list and add the invariant that would have caught X" —
#: the enumeration is gone.
#:
#: What is covered is a **shape space, not an access list**: `_SHAPE_SPACE`
#: in `tests/test_console_readers.py` crosses four axes — `flags:`,
#: `modules:`, a `modules:` KEY, and a module `spec` — against `[]`, `{}`,
#: `""`, a scalar, `None` and a nested variant, and every row is classified
#: tolerated-or-fatal by measurement against `HEAD`. The key axis exists
#: because a value-only enumeration structurally could not see a `modules:`
#: key resolving to a non-string (a truncated `00-intake` -> `0`, a YAML 1.1
#: bareword `on:`/`no:` -> `bool`, a bare float, mixed key types), which
#: blanked all five routes.
#:
#: The census, if ever needed, is mechanical rather than written down:
#: `grep -n '_boundary(' app/vault.py`.
#:
#: **Still NOT asserted a closed family by a structural test** — the C2'
#: caution above stands on its own terms, not superseded by this fix: a
#: *different* unconverted shape could exist that nobody has hand-edited a
#: registry to find yet, on an access point nobody has enumerated yet
#: either, exactly as this section's own history just demonstrated. What
#: changed is that every shape actually measured — value-shape at three
#: access points across `[]` / `{}` / `""` / a scalar / `None` / a nested
#: variant, AND key-shape at the two access points that read the `modules:`
#: mapping's keys — is now either already-tolerated (unchanged) or typed
#: `DestinationRegistryError`. Coverage is these five access points and no
#: others; see `tests/test_console_readers.py::test_bundles_shape_space_boundary_conversion`
#: for the enumeration.
_SIDEBAR_CATCHES = (DestinationRegistryError, EntityManifestError)


def _render_console_error(
    request: Request, error, *, force_page_status: bool = False
) -> HTMLResponse:
    """Shared renderer for every described Console error (design §5): route
    shape first, then HX-Request, decides fragment vs. page; status follows
    severity for a fragment and the code's own page status for a page.

    `force_page_status` decouples the two for the global fallback, which must
    never return 200 even while rendering a fragment body — a refusal-severity
    escapee is a defect, and laundering it into a success is what §5 forbids.
    The Rule 4 framework handler decouples them the same way.
    """
    endpoint = _endpoint_for(request)
    fragment = is_fragment(request, endpoint)
    status = error.page_status if force_page_status else status_for(error, fragment)
    if fragment:
        return templates.TemplateResponse(
            request, "blocks/alert.html", {"error": error}, status_code=status,
        )
    # design §6: the error page cannot include the sidebar when the sidebar
    # itself is unreadable — `_sidebar.html` iterates bundles and
    # `Vault.bundles()` is what can fail.
    #
    # C1 (S6 review): this used to key on `error.code == "E-CONFIG"` — the
    # code of the error ALREADY BEING RENDERED — rather than on whether
    # `Vault(catalog).bundles()` itself succeeds. Any described error whose
    # code was NOT "E-CONFIG" (including E-UNKNOWN for something wholly
    # unrelated) still re-entered `bundles()` here to build the sidebar, and
    # if the vault's registries were ALSO broken in a way that didn't
    # resolve to "E-CONFIG" for the original error (e.g. a bare exception
    # from a hand-edited entities.yaml, before the C2 fix converted it), the
    # second failure propagated out of this handler uncaught — including out
    # of the GLOBAL FALLBACK itself, past every exception handler, to a
    # completely empty 500 body. Measured against a real
    # `flags: [nosuchflag]` vault with no monkeypatching: `/`, `/triage`,
    # `/triage/alpha`, `/outbox/alpha`, and `/registry/alpha/products` all
    # returned an empty-bodied 500.
    #
    # The fix decides on READABILITY, not on the code: attempt the sidebar
    # and fall back to `None` on failure, exactly as `_sidebar.html`'s own
    # E-CONFIG contract already promises. `_SIDEBAR_CATCHES` is a specific,
    # maintained tuple naming every type `Vault.bundles()` is known to raise
    # (C2' review: NOT asserted closed by a structural test — see the tuple's
    # own declaration comment above), so this is not the blanket
    # `except Exception` invariant 6 forbids for a registered endpoint's own
    # body — `app/main.py` is one.
    try:
        bundles = Vault(catalog).bundles()
    except _SIDEBAR_CATCHES:
        bundles = None
    return templates.TemplateResponse(
        request, "error.html", {"error": error, "bundles": bundles}, status_code=status,
    )


#: Rule 4's replacement body for a framework-owned status under HX-Request.
#: Deliberately not a taxonomy code (Rule 6 excludes HTTPException from the
#: class map and the allowlist) — this is safe, curated text, not a
#: described ConsoleError, and the framework's own status is preserved
#: rather than any code's page_status.
_FRAMEWORK_SAFE_BODY = (
    '<div class="alert" role="alert">'
    '<span class="alert-message">'
    "This request could not be completed."
    "</span></div>"
)


@app.exception_handler(EntitySelectionError)
async def _entity_selection_error_handler(
    request: Request, exc: EntitySelectionError
) -> HTMLResponse:
    return _render_console_error(request, describe(exc))


#: Task 14 corrective (design §5 Rule 6's shape, applied to the ONE other
#: framework-adjacent site that needed it): `entity_scope` -> `build_scope`
#: -> `Scope.__init__` -> `EntityCatalog.load` -> `resolve_system_registry`
#: can raise `EntityManifestError` (a missing/invalid `entities.yaml`) or its
#: narrower subclass `SystemRegistryPathError` (a redirected `_system`) —
#: from inside FastAPI's DEPENDENCY resolution, which runs before any route
#: body. No route-level `except` can ever see either: `shell` and
#: `triage_default` already answer them via `_SIDEBAR_CATCHES` because they
#: reach `EntityManifestError` a different way (`Vault(catalog).bundles()`,
#: not `Scope.__init__`). **Eight** routes take `EntityScope` — `triage`,
#: `propose`, `outbox_screen`, `outbox_approve`, `outbox_reject`,
#: `registry_products`, `registry_delete_preview`, `registry_delete_execute`
#: — and this family reaches all of them **unguarded** only through the
#: dependency. (It also reaches the three page routes inside their bodies via
#: `Vault.bundles()`, which is why `SystemRegistryPathError` sits in their
#: catch tuples; that path is guarded, this one was not.) The five
#: `fragment-only` POSTs are covered by the same handler: `_endpoint_for` is
#: populated during dependency resolution, so the fragment surface is
#: selected correctly and no whole document is swapped into an HTMX target.
#:
#: One handler suffices for both classes: Starlette dispatches on the most
#: specific registered ancestor in the raised instance's MRO, so a raised
#: `SystemRegistryPathError` is routed here (there is no more specific
#: handler for it) while `describe()` — verified, not assumed, since a prior
#: task shipped a false claim about this exact mechanism (see
#: `_TRIAGE_CATCHES`'s own declaration comment) — resolves on the RAISED
#: INSTANCE's own class via `_EXACT`/`_MRO`, never on the handler's declared
#: type. `SystemRegistryPathError` has its own `_EXACT` entry (`E-TAMPER`),
#: so it keeps that code; `EntityManifestError` itself has no `_EXACT` entry
#: and falls through to its `_MRO` entry (`E-CONFIG`). Neither ever reaches
#: `_console_fallback_handler`.
@app.exception_handler(EntityManifestError)
async def _entity_manifest_error_handler(
    request: Request, exc: EntityManifestError
) -> HTMLResponse:
    return _render_console_error(request, describe(exc))


@app.exception_handler(RequestValidationError)
async def _request_validation_error_handler(
    request: Request, exc: RequestValidationError
) -> HTMLResponse:
    # describe() never inspects exc.errors(), so the submitted field name
    # and value can never reach the rendered message (design §5-6).
    return _render_console_error(request, describe(exc))


@app.exception_handler(StarletteHTTPException)
async def _framework_http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> Response:
    """Rule 4: replace only the *body* of a framework-owned status — an
    unmatched URL, a wrong method, a StaticFiles miss — and only when
    HX-Request is present. The framework's own status is always preserved,
    never mapped through the taxonomy (Rule 6), and the plain response is
    left untouched when HX-Request is absent."""
    if request.headers.get("HX-Request") != "true":
        return await _default_http_exception_handler(request, exc)
    headers = getattr(exc, "headers", None)
    if not is_body_allowed_for_status_code(exc.status_code):
        return Response(status_code=exc.status_code, headers=headers)
    return HTMLResponse(
        _FRAMEWORK_SAFE_BODY, status_code=exc.status_code, headers=headers,
    )


@app.exception_handler(Exception)
async def _console_fallback_handler(request: Request, exc: Exception) -> HTMLResponse:
    """Catches only what escapes a route's own declared family; describes it
    and returns the code's page status. Never 200 (design §5)."""
    return _render_console_error(request, describe(exc), force_page_status=True)


@app.get("/", response_class=HTMLResponse)
@console_route(catches=_SIDEBAR_CATCHES, surface="page")
def shell(request: Request) -> HTMLResponse:
    """`Vault(catalog).bundles()` raises every declared member, and it runs
    before any template exists — so the route answers them itself rather than
    relying on the global fallback, which design §5 calls "a failure rather
    than a silent default" and which `ServerErrorMiddleware` re-raises as a
    logged traceback. Task 10 added the declaration; this adds the handler.
    """
    try:
        bundles = Vault(catalog).bundles()
    except _SIDEBAR_CATCHES as exc:
        return _render_console_error(request, describe(exc))
    return templates.TemplateResponse(
        request,
        "shell.html",
        {"bundles": bundles, "now": datetime.now().strftime("%H:%M:%S")},
    )


@app.get("/blocks/pulse", response_class=HTMLResponse)
# `catches=()` is the truthful declaration, not an empty gesture: `pulse` reads
# no registry, resolves no path, and has no domain family to declare.
#
# `fragment-only` is its real shape under §5's normative rule — "a route with
# no full-page template always uses the fragment renderer" — and it renders
# `blocks/pulse.html` with none. §5's parenthetical list of fragment-only
# routes omits `pulse`; §7 governs, since every enumeration in the design was
# "wrong in the direction of omission" at least once.
#
# This is NOT behaviour-neutral, despite the route inventory's `pulse |
# unchanged`: an error escaping `pulse` without `HX-Request` previously
# rendered the full `error.html` page and now renders the alert fragment. The
# status is unaffected (the global fallback forces the code's page status
# either way), and no refusal, validation, or commit decision changes. Pinned
# by test_pulse_declaration_selects_the_fragment_surface.
@console_route(catches=(), surface="fragment-only")
def pulse(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "blocks/pulse.html", {"now": datetime.now().strftime("%H:%M:%S")}
    )


@app.get("/triage", response_class=HTMLResponse)
@console_route(catches=_SIDEBAR_CATCHES, surface="page")
def triage_default(request: Request):
    """Same boundary as `shell`, and for the same reason."""
    try:
        bundles = Vault(catalog).bundles()
    except _SIDEBAR_CATCHES as exc:
        return _render_console_error(request, describe(exc))
    if not bundles:
        return templates.TemplateResponse(request, "blocks/no_bundles.html", {})
    return RedirectResponse(url=f"/triage/{bundles[0].slug}", status_code=307)


#: Gate 1 stopwatch (design §5; ledger's binding resolution of the
#: discrepancy between §5's mechanism sentence and §8's "any unlisted test
#: change is a scope breach"). Emitted only once `propose_classification` has
#: returned below — i.e. only once the proposal is durably persisted — never
#: earlier. `triage.html`'s existing `htmx:afterRequest` listener reads this
#: exact value from the `HX-Trigger` response header rather than from
#: `e.detail.successful`, so a refusal (which never reaches the line that
#: sets this header) can never increment the operator's count.
_PROPOSAL_PERSISTED_EVENT = "console:proposal-persisted"


#: Declared once so the decorator and the route's own `except` cannot drift.
#: `SystemRegistryPathError` is declared explicitly rather than via its
#: `EntityManifestError` base because it is **narrower**: the base would also
#: swallow `RecipientConfigurationError` and any future sibling, which these
#: routes have no business answering.
#:
#: An earlier revision of this comment justified it as "so the `E-TAMPER`
#: mapping is preserved rather than collapsed into `E-CONFIG`". That was
#: false and review disproved it with one command: `describe()` resolves on
#: the raised instance's own class, never on the route's declared tuple, so
#: declaring the base would still render `E-TAMPER`. The decision stands; the
#: reason did not. `bundles()`
#: re-resolves `_system` on every request, so an operator who moves the
#: directory and symlinks it back after startup reaches this on a live
#: request — measured on three routes, each previously escaping to the global
#: fallback with a logged traceback.
#:
#: Declared here rather than converted in `Vault.system_path`: converting
#: would change a service exception contract to close a presentation-layer
#: gap, and would break `tests/test_vault.py`'s standing E4 regression, which
#: asserts the raw `EntityManifestError`. Human ruling, recorded in the ledger.
_TRIAGE_CATCHES = (
    DestinationError, DestinationRegistryError, CrossScopeError,
    SystemRegistryPathError,
)


@app.get("/triage/{entity}", response_class=HTMLResponse)
@console_route(catches=_TRIAGE_CATCHES, surface="page")
def triage(request: Request, scope: EntityScope) -> HTMLResponse:
    """Every member of the declared family is answered by the route itself.

    design §5: the global handler "catches only what escapes a route", and
    "relying on it is a failure rather than a silent default" — so a declared
    member reaching it is a defect, not a fallback. Two members can arise
    outside the per-row guard and would otherwise escape: `read_inbox` raises
    `RedirectedPathError` (a `CrossScopeError`) for a redirected inbox, and
    `Vault.bundles()` raises `DestinationRegistryError` while the template
    context is built. Both abort the page, which is correct — a redirected
    inbox and a broken registry are vault-wide properties, not row-local ones
    — but they abort into *this* handler, not out of the route.
    """
    try:
        return _triage_page(request, scope)
    except _TRIAGE_CATCHES as exc:
        return _render_console_error(request, describe(exc))


def _triage_page(request: Request, scope: Scope) -> HTMLResponse:
    selected = scope.current_entity()
    vault = Vault(catalog)
    clf = Classifier(vault)
    rows = []
    for item in read_inbox(scope):
        classification = clf.classify(item.title, item.summary, item.source)
        destination, error = None, None
        try:
            destination = resolve_classification_destination(
                scope,
                item.path,
                module=classification.module,
                sub=classification.sub,
                claimed_block=classification.block,
            )
        except (DestinationError, CrossScopeError) as exc:
            # DestinationRegistryError is deliberately NOT caught here: a
            # broken registry is a vault-wide property, not a row-local one
            # (design §3's Phase 2 reasoning applies here too), so it
            # propagates to abort the whole page as one described E-CONFIG
            # page rather than a per-row alert.
            error = describe(exc)
        rows.append((item, classification, destination, error))
    return templates.TemplateResponse(
        request,
        "triage.html",
        {
            "bundles": vault.bundles(),
            "entity": selected,
            "rows": rows,
            "persisted_event": _PROPOSAL_PERSISTED_EVENT,
        },
    )


#: Declared once so the decorator and each of the route's two `except`
#: clauses cannot drift (the Task 11 pattern; M5, review: this repeated the
#: same literal tuple three times before).
_PROPOSE_CATCHES = (OutboxError, DestinationError, CrossScopeError,
                     DestinationRegistryError)


@app.post("/triage/{entity}/propose", response_class=HTMLResponse)
@console_route(catches=_PROPOSE_CATCHES, surface="fragment-only")
def propose(
    request: Request,
    scope: EntityScope,
    filename: str = Form(...),
    module: str = Form(...),
    sub: str = Form(""),
    block: str | None = Form(None),
    entity_claim: str | None = Form(None, alias="entity"),
) -> HTMLResponse:
    try:
        if entity_claim is not None:
            raise OutboxDestinationError("entity is owned by request scope")
        # Task 8 corrective (residual of PR #15 must-fix 6): classify a
        # lexical symlink on the inbox lifecycle leaf BEFORE calling
        # `scope.resolve()`, mirroring `_require_real_directory`'s
        # established pattern in destinations.py/inbox.py. Unlike those
        # helpers this function builds no anchor path at all today, so a
        # symlinked `00-inbox/active` reaches `scope.resolve("00-inbox",
        # "active")` first, which raises `OutOfScopeError` (-> E-SCOPE) for
        # what design §2 requires to be a redirection finding (-> E-TAMPER).
        inbox_active_lexical = (
            scope.root / scope.current_entity() / "00-inbox" / "active"
        )
        if inbox_active_lexical.is_symlink():
            raise RedirectedPathError("inbox lifecycle directory is redirected")
        item_path = scope.resolve("00-inbox", "active") / filename
        prop = propose_classification(
            scope, item_path,
            module=module, sub=sub, claimed_block=block,
        )
    except _PROPOSE_CATCHES as exc:
        # Refused before propose_classification returned: nothing was
        # written, so no persisted-proposal signal is emitted.
        return _render_console_error(request, describe(exc))

    # The proposal is durably persisted from this point on. Whatever the
    # rest of this function does — including a described failure below —
    # that fact is now true, so the stopwatch signal below is honest either
    # way.
    try:
        diff = preview_diff(scope, prop)
        response = templates.TemplateResponse(
            request, "blocks/diff.html", {"proposal": prop, "diff": diff},
        )
    except _PROPOSE_CATCHES as exc:
        response = _render_console_error(request, describe(exc))
    response.headers["HX-Trigger"] = _PROPOSAL_PERSISTED_EVENT
    return response


#: Declared once so the decorator and each route's own `except` cannot drift
#: (the Task 11 pattern). `load_proposals` raises bare `CrossScopeError` for a
#: redirected outbox or proposal leaf, which today escapes `except OutboxError:
#: pass` entirely — design §5 names this exact gap for the outbox routes.
#: `ReviewTokenError` (S7): a stale or malformed review fingerprint is a
#: declared outcome of these routes, not an escape. Without it a rewritten
#: proposal reaches the global fallback as an unhandled error instead of the
#: approved refusal, and the operator is never offered the current review.
_OUTBOX_CATCHES = (
    OutboxError, CrossScopeError, DestinationRegistryError,
    SystemRegistryPathError,  # see _TRIAGE_CATCHES
    ReviewTokenError,
)


def _outbox_rows(listing):
    """Describe every row's carried exception once, in the composition root
    (design §3: "the taxonomy stays out of the service" — `OutboxRow.error`
    carries the raw exception, never a code, and only `app/main.py` calls
    `describe()` on it).

    Returns `(rows, blocked_notice)`: `rows` pairs each `OutboxRow` with its
    described error (`None` when the row has none); `blocked_notice` is the
    single listing-level `ConsoleError` for a blocked listing — every
    unreadable row describes to the same `E-UNREADABLE` code (mro), so any one
    of them carries the notice.
    """
    rows = [
        (row, describe(row.error) if row.error is not None else None)
        for row in listing.rows
    ]
    blocked_notice = None
    if listing.blocked:
        blocked_notice = next(
            (error for row, error in rows if row.proposal is None and error is not None),
            None,
        )
    return rows, blocked_notice


def _outbox_list(
    request: Request,
    scope: Scope,
    *,
    approval_error: ConsoleError | None = None,
) -> HTMLResponse:
    """Fragment renderer shared by approve and reject (design §8: both swap
    `#outbox-list` `outerHTML`, so the fragment reproduces that root).

    `approval_error` is the just-attempted action's own described failure, if
    any — never the blocked listing's notice. Design §5: "When one response
    carries several codes … the status is the refusal's" — the listing's own
    condition is already visible in its own notice, so the status here follows
    only `approval_error`, via the same severity rule every fragment uses.
    """
    endpoint = _endpoint_for(request)
    fragment = is_fragment(request, endpoint)
    listing = project_outbox(scope)
    rows, blocked_notice = _outbox_rows(listing)
    if (
        blocked_notice is not None
        and approval_error is not None
        and blocked_notice.code == approval_error.code
    ):
        # design §3: "one listing-level notice". In the blocked state a POST
        # is refused by the strict loader with the identical E-UNREADABLE
        # code the projection's own blocked notice already carries (ledger
        # D2) — rendering both would be two byte-identical alerts saying the
        # same sentence twice. The action's own alert is kept; the listing's
        # redundant one is suppressed.
        blocked_notice = None
    status = status_for(approval_error, fragment) if approval_error is not None else 200
    return templates.TemplateResponse(
        request,
        "blocks/outbox_list.html",
        {
            "entity": scope.current_entity(),
            "rows": rows,
            "blocked": listing.blocked,
            "blocked_notice": blocked_notice,
            "approval_error": approval_error,
            "listing_unavailable": False,
            "listing_error": None,
        },
        status_code=status,
    )


@app.get("/outbox/{entity}", response_class=HTMLResponse)
@console_route(catches=_OUTBOX_CATCHES, surface="page")
def outbox_screen(request: Request, scope: EntityScope) -> HTMLResponse:
    """Every member of the declared family is answered by the route itself
    (the Task 11 `triage` pattern). `project_outbox`'s phase 2 (destination and
    registry validation) deliberately propagates — design §3 — so a broken
    registry or a redirected outbox aborts the projection entirely; that abort
    must land in *this* handler, not escape the route to the global fallback.
    """
    try:
        return _outbox_page(request, scope)
    except _OUTBOX_CATCHES as exc:
        return _render_console_error(request, describe(exc))


def _outbox_page(request: Request, scope: Scope) -> HTMLResponse:
    vault = Vault(catalog)
    listing = project_outbox(scope)
    rows, blocked_notice = _outbox_rows(listing)
    return templates.TemplateResponse(
        request, "outbox.html",
        {
            "bundles": vault.bundles(),
            "entity": scope.current_entity(),
            "rows": rows,
            "blocked": listing.blocked,
            "blocked_notice": blocked_notice,
            "approval_error": None,
            "listing_unavailable": False,
            "listing_error": None,
        },
    )


def _outbox_list_error(
    request: Request,
    scope: Scope,
    exc: BaseException,
    *,
    action_error: ConsoleError | None = None,
) -> HTMLResponse:
    """Fallback fragment for the rare double-failure where even the
    re-rendered listing itself fails to build (`project_outbox`, called from
    `_outbox_list`, escapes for a reason unrelated to — or the same vault-wide
    condition as — the just-attempted action, e.g. a broken registry that
    refuses both).

    Keeps the `#outbox-list` `outerHTML` swap target (design §8's swap-shape
    rule): rendering the route-agnostic `blocks/alert.html` here instead would
    remove `#outbox-list` from the DOM entirely, stranding the operator with
    no element left for a future approve/reject to swap into, AND would
    discard the listing's own state (design §3: a screen must not hide the
    condition it is protecting against — here, that a proposal is genuinely
    still pending). Renders the same fragment `_outbox_list` would, with no
    rows, `listing_unavailable=True` so the template does not lie and claim
    the outbox is empty, and the listing failure carried as its own alert.

    `action_error` is the just-attempted action's own described refusal, if
    the caller already has one (i.e. `approve`/`reject` itself refused before
    the re-render also failed). Design §5: "the status is the refusal's,
    because that is the outcome of the request being answered" — so when
    present it drives the response status, and BOTH conditions are rendered:
    dropping the action's own refusal here would silently discard the answer
    to the request the operator actually made.
    """
    listing_error = describe(exc)
    # Same suppression `_outbox_list` applies (I2): when one vault-wide
    # condition refuses both the action and the re-read — the case this
    # docstring names — the two describe identically, and rendering the same
    # sentence twice is not two pieces of information. The action's refusal
    # is the one kept: design §5, it is "the outcome of the request being
    # answered".
    if action_error is not None and action_error.code == listing_error.code:
        listing_error = None
    endpoint = _endpoint_for(request)
    fragment = is_fragment(request, endpoint)
    status_source = action_error if action_error is not None else listing_error
    return templates.TemplateResponse(
        request,
        "blocks/outbox_list.html",
        {
            "entity": scope.current_entity(),
            "rows": (),
            "blocked": False,
            "blocked_notice": None,
            "approval_error": action_error,
            "listing_error": listing_error,
            "listing_unavailable": True,
        },
        status_code=status_for(status_source, fragment),
    )


def _review_row(scope: Scope, proposal_id: str):
    """The current projected row for one proposal, or `None`.

    Read-only, and deliberately taken from the same projection the listing
    uses: a fresh review must be the listing's own view of the record, not a
    second opinion assembled elsewhere.
    """
    for row in project_outbox(scope).rows:
        if row.proposal is not None and row.proposal.id == proposal_id:
            return row
    return None


def _review_changed_response(
    request: Request,
    scope: Scope,
    proposal_id: str,
    stale_review_sha256: str,
    error: ConsoleError,
) -> HTMLResponse:
    """Reconfirm on the same screen (spec §Presentation).

    The response is appended *beside* the card the operator acted from —
    `HX-Retarget` plus `HX-Reswap: afterend` — rather than swapping the whole
    list, so the version they reviewed stays on screen to compare against.

    Status is 200, not E-REVIEW's 409, and that is deliberate: S6's rule is
    that a fragment refusal renders at 200 (`status_for`), and HTMX does not
    swap a 4xx response at all — a 409 here would leave the operator with no
    current card, no disabled stale controls, and no way to reconfirm. The
    refusal is fully visible in the fragment itself.
    """
    row = _review_row(scope, proposal_id)
    if row is None:
        return _review_unavailable_response(request, scope, proposal_id, error)
    response = templates.TemplateResponse(
        request,
        "blocks/review_changed.html",
        {
            "entity": scope.current_entity(),
            "row": row,
            "error": None,
            "review_error": error,
            "stale_review_sha256": stale_review_sha256,
        },
        status_code=200,
    )
    response.headers["HX-Retarget"] = (
        f"#review-card-{proposal_id}-{stale_review_sha256}"
    )
    response.headers["HX-Reswap"] = "afterend"
    return response


def _review_unavailable_response(
    request: Request,
    scope: Scope,
    proposal_id: str,
    error: ConsoleError,
    *,
    status: int = 200,
) -> HTMLResponse:
    """A safe no-action state: no controls, no fingerprint, no guessing."""
    return templates.TemplateResponse(
        request,
        "blocks/review_unavailable.html",
        {
            "entity": scope.current_entity(),
            "proposal_id": proposal_id,
            "review_error": error,
            "recreatable": error.code in {"E-INVALID", "E-MISSING"},
        },
        status_code=status,
    )


@app.get("/outbox/{entity}/review/{proposal_id}", response_class=HTMLResponse)
@console_route(catches=_OUTBOX_CATCHES, surface="fragment-only")
def outbox_review_fragment(
    request: Request, scope: EntityScope, proposal_id: str
) -> HTMLResponse:
    """`Check again`: read, validate, render. It writes nothing.

    A missing, malformed, redirected or cross-scope record renders the safe
    unavailable state rather than an error page, so the operator keeps a
    place to check again from.
    """
    try:
        row = _review_row(scope, proposal_id)
    except _OUTBOX_CATCHES as exc:
        return _review_unavailable_response(
            request, scope, proposal_id, describe(exc)
        )
    if row is None:
        # The projection lists every readable record, so an id it does not
        # hold names no pending proposal — described through the outbox's own
        # outcome rather than a code chosen here.
        return _review_unavailable_response(
            request, scope, proposal_id,
            describe(OutboxError("no pending proposal for this entity")),
        )
    return templates.TemplateResponse(
        request,
        "blocks/outbox_card.html",
        {
            "entity": scope.current_entity(),
            "row": row,
            "error": describe(row.error) if row.error is not None else None,
            "label": "Current version",
        },
    )


@app.post("/outbox/{entity}/approve", response_class=HTMLResponse)
@console_route(catches=_OUTBOX_CATCHES, surface="fragment-only")
def outbox_approve(
    request: Request,
    scope: EntityScope,
    id: str = Form(...),
    review_sha256: str = Form(...),
) -> HTMLResponse:
    """The route's declared family is answered inside
    `_outbox_approve_response`, which catches `approve` itself refusing and,
    separately, the `_outbox_list` re-render failing on top of it —
    carrying both into `_outbox_list_error` rather than losing the action's
    own refusal (design §5: the status is the refusal's).

    There is deliberately no second `except` wrapping this call. Every
    statement of the response helper is already inside a `try`, so the only
    thing an outer clause could catch is a failure raised by
    `_outbox_list_error` itself — which it could only answer by calling that
    same function again with the same failing inputs. Deleting it changes no
    structural check and no measured outcome; an earlier revision kept one
    and had to invent a reason, which is why it is gone.
    """
    return _outbox_approve_response(request, scope, id, review_sha256)


def _outbox_approve_response(
    request: Request, scope: Scope, id: str, review_sha256: str
) -> HTMLResponse:
    approval_error = None
    try:
        # S7: the operator's own fingerprint, passed through untouched. The
        # route must never derive one — recomputing here would rebind the
        # action to whatever is on disk now, which is the defect S7 closes.
        approve(scope, id, review_sha256)
    except ReviewedProposalChanged as exc:
        # Not a list re-render: the operator keeps the version they reviewed
        # on screen and reconfirms against the current one beside it.
        return _review_changed_response(
            request, scope, id, review_sha256, describe(exc)
        )
    except _OUTBOX_CATCHES as exc:
        approval_error = describe(exc)
    try:
        return _outbox_list(request, scope, approval_error=approval_error)
    except _OUTBOX_CATCHES as exc:
        # I1: the re-render's own failure must not discard approve()'s own
        # refusal — both are carried into the fallback fragment.
        return _outbox_list_error(request, scope, exc, action_error=approval_error)


@app.post("/outbox/{entity}/reject", response_class=HTMLResponse)
@console_route(catches=_OUTBOX_CATCHES, surface="fragment-only")
def outbox_reject(
    request: Request,
    scope: EntityScope,
    id: str = Form(...),
    review_sha256: str = Form(...),
) -> HTMLResponse:
    """The route's declared family is answered inside
    `_outbox_reject_response`, which catches `reject` itself refusing and,
    separately, the `_outbox_list` re-render failing on top of it —
    carrying both into `_outbox_list_error` rather than losing the action's
    own refusal (design §5: the status is the refusal's).

    There is deliberately no second `except` wrapping this call. Every
    statement of the response helper is already inside a `try`, so the only
    thing an outer clause could catch is a failure raised by
    `_outbox_list_error` itself — which it could only answer by calling that
    same function again with the same failing inputs. Deleting it changes no
    structural check and no measured outcome; an earlier revision kept one
    and had to invent a reason, which is why it is gone.
    """
    return _outbox_reject_response(request, scope, id, review_sha256)


def _outbox_reject_response(
    request: Request, scope: Scope, id: str, review_sha256: str
) -> HTMLResponse:
    approval_error = None
    try:
        # S7: same contract as approve — passed through, never derived.
        reject(scope, id, review_sha256)
    except ReviewedProposalChanged as exc:
        return _review_changed_response(
            request, scope, id, review_sha256, describe(exc)
        )
    except _OUTBOX_CATCHES as exc:
        approval_error = describe(exc)
    try:
        return _outbox_list(request, scope, approval_error=approval_error)
    except _OUTBOX_CATCHES as exc:
        # I1: same as approve — the re-render's own failure must not discard
        # reject()'s own refusal.
        return _outbox_list_error(request, scope, exc, action_error=approval_error)


#: Declared once so the decorator and every route's own `except` cannot
#: drift (the Task 11/12 pattern). Before this task `registry_products` and
#: `registry_delete_preview` had no `try`/`except` at all, so literally
#: everything escaped them to the global fallback — including their own
#: declared `RegistryError` — and `registry_delete_execute` caught only
#: `RegistryError`, so everything else escaped it too.
#:
#: C1/I1 (review, first fix round): the initial tuples repeated the same
#: hole one level down. `_REGISTRY_PRODUCTS_CATCHES` omitted
#: `CrossScopeError` even though `products_for` → `scope.system_path(...)`
#: can raise `RedirectedPathError` (a `CrossScopeError`) for a real
#: symlinked `_system/products.yaml` — proven against the filesystem, not
#: an injected exception, since a totality test that only injects the
#: declared family cannot see a member the family itself omits. And
#: `_REGISTRY_DELETE_CATCHES` omitted `UnreadableProposalRecord`, even
#: though `get_delete_proposal` and `execute_delete` are both
#: `@structured_reader(category="proposal")` — design §7 invariant 4
#: requires exactly that category to raise it for a malformed record, so a
#: corrupt delete-proposal file on disk reached the global fallback for a
#: designed, not accidental, failure mode.
_REGISTRY_PRODUCTS_CATCHES = (
    RegistryError, CrossScopeError, DestinationRegistryError,
    SystemRegistryPathError,  # see _TRIAGE_CATCHES
)
#: `ReviewTokenError` (S7): a stale or malformed review fingerprint is a
#: declared outcome of delete-execute, not an escape — the same addition the
#: outbox actions needed.
_REGISTRY_DELETE_CATCHES = (
    RegistryError, CrossScopeError, DestinationRegistryError, UnreadableProposalRecord,
    ReviewTokenError,
)


@app.get("/registry/{entity}/products", response_class=HTMLResponse)
@console_route(catches=_REGISTRY_PRODUCTS_CATCHES, surface="page")
def registry_products(request: Request, scope: EntityScope) -> HTMLResponse:
    # M7 (review): unlike the two delete routes below, the `TemplateResponse`
    # call sits inside this `try`. Intentional, not drift: `vault.bundles()`
    # and `products_for(scope)` are both evaluated as part of building the
    # context dict passed to `TemplateResponse`, so a failure from either
    # must already be inside the guarded region for this route to describe
    # it at all. The delete routes build their context only from values
    # already validated by the point the `try` exits, so nothing left to
    # raise the declared family sits outside it either — the shapes differ
    # because what each route's context construction can raise differs, not
    # because one route is guarded more carefully than the other.
    try:
        selected = scope.current_entity()
        vault = Vault(catalog)
        return templates.TemplateResponse(
            request, "registry.html",
            {"bundles": vault.bundles(), "entity": selected,
             "products": products_for(scope)},
        )
    except _REGISTRY_PRODUCTS_CATCHES as exc:
        return _render_console_error(request, describe(exc))


@app.post("/registry/{entity}/product/delete-preview", response_class=HTMLResponse)
@console_route(catches=_REGISTRY_DELETE_CATCHES, surface="fragment-only")
def registry_delete_preview(
    request: Request, scope: EntityScope, slug: str = Form(...)
) -> HTMLResponse:
    try:
        selected = scope.current_entity()
        written = propose_delete(scope, "product", slug)
        # propose_delete has already written the proposal file by this
        # point (design §8: "the domain action succeeded, and S6 must not
        # roll back a successful write merely because rendering failed"), so
        # a failure from here on is `(committed=no, persistence=
        # proposal-written)`, not `persistence=none`.
        #
        # S7: everything on this screen comes from one review snapshot of
        # the just-written record — the displayed kind and value, the impact,
        # and the fingerprint the button carries. A second live count taken
        # here would describe state the button is not bound to, so the
        # operator would review one thing and act on another.
        review = get_delete_review(scope, written.id)
    except _REGISTRY_DELETE_CATCHES as exc:
        return _render_console_error(request, describe(exc))
    return templates.TemplateResponse(
        request, "blocks/delete_impact.html",
        # No submitted value reaches the fragment: the slug on screen is the
        # one inside the fingerprint, so the screen cannot describe one
        # product while the button is bound to a proposal naming another.
        {"entity": selected, "prop": review.value,
         "review_sha256": review.sha256},
    )


@app.post("/registry/{entity}/product/delete-execute", response_class=HTMLResponse)
@console_route(catches=_REGISTRY_DELETE_CATCHES, surface="fragment-only")
def registry_delete_execute(
    request: Request,
    scope: EntityScope,
    id: str = Form(...),
    review_sha256: str = Form(...),
) -> HTMLResponse:
    # Rule 8 (design §6): the success copy must name the SERVER-derived
    # slug, never the submitted one.
    #
    # S7: `execute_delete` now returns the bound `DeleteProposal` it actually
    # executed, so the validated slug comes from the execution itself. The
    # earlier `get_delete_proposal` read is gone — it was an unbound read of
    # a record the action had not yet compared, and using it for display
    # could describe bytes the deletion never consumed.
    try:
        prop = execute_delete(scope, id, review_sha256)
    except _REGISTRY_DELETE_CATCHES as exc:
        return _render_console_error(request, describe(exc))
    return templates.TemplateResponse(
        request, "blocks/delete_success.html",
        {"kind": prop.kind, "slug": prop.slug},
    )
