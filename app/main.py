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
from .console_errors import describe
from .console_render import is_fragment, status_for
from .console_routing import console_route
from .destinations import DestinationError, resolve_classification_destination
from .entities import EntityManifestError, EntitySelectionError
from .inbox import read_inbox
from .outbox import (
    MissingProposalSource,
    OutboxDestinationError,
    OutboxError,
    StaleProposalSource,
    approve,
    load_proposals,
    preview_diff,
    propose_classification,
    reject,
)
from .registry import (
    RegistryError,
    execute_delete,
    products_for,
    propose_delete,
    reference_count,
)
from .scope import CrossScopeError, Scope
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
    # design §6: the error page cannot include the sidebar when the
    # described error is E-CONFIG, since the sidebar iterates bundles and
    # Vault.bundles() is exactly what failed.
    bundles = None if error.code == "E-CONFIG" else Vault(catalog).bundles()
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
@console_route(catches=(DestinationRegistryError, EntityManifestError), surface="page")
def shell(request: Request) -> HTMLResponse:
    bundles = Vault(catalog).bundles()
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
@console_route(catches=(DestinationRegistryError, EntityManifestError), surface="page")
def triage_default(request: Request):
    bundles = Vault(catalog).bundles()
    if not bundles:
        return templates.TemplateResponse(request, "blocks/no_bundles.html", {})
    return RedirectResponse(url=f"/triage/{bundles[0].slug}", status_code=307)


@app.get("/triage/{entity}", response_class=HTMLResponse)
@console_route(
    catches=(DestinationError, DestinationRegistryError, CrossScopeError),
    surface="page",
)
def triage(request: Request, scope: EntityScope) -> HTMLResponse:
    selected = scope.current_entity()
    vault = Vault(catalog)
    clf = Classifier(vault)
    rows = []
    for item in read_inbox(scope):
        classification = clf.classify(item.title, item.summary, item.source)
        try:
            destination = resolve_classification_destination(
                scope,
                item.path,
                module=classification.module,
                sub=classification.sub,
                claimed_block=classification.block,
            )
        except (DestinationError, DestinationRegistryError):
            destination = None
        rows.append((item, classification, destination))
    return templates.TemplateResponse(
        request,
        "triage.html",
        {"bundles": vault.bundles(), "entity": selected, "rows": rows},
    )


@app.post("/triage/{entity}/propose", response_class=HTMLResponse)
@console_route(
    catches=(OutboxError, DestinationError, CrossScopeError,
             DestinationRegistryError),
    surface="fragment-only",
)
def propose(
    request: Request,
    scope: EntityScope,
    filename: str = Form(...),
    module: str = Form(...),
    sub: str = Form(""),
    block: str | None = Form(None),
    entity_claim: str | None = Form(None, alias="entity"),
) -> HTMLResponse:
    if entity_claim is not None:
        raise OutboxDestinationError("entity is owned by request scope")
    item_path = scope.resolve("00-inbox", "active") / filename
    prop = propose_classification(
        scope, item_path,
        module=module, sub=sub, claimed_block=block,
    )
    return templates.TemplateResponse(
        request,
        "blocks/diff.html",
        {"proposal": prop, "diff": preview_diff(scope, prop)},
    )


def _outbox_list(
    request: Request,
    scope: Scope,
    *,
    approval_error: str | None = None,
) -> HTMLResponse:
    selected = scope.current_entity()
    props = [(p, preview_diff(scope, p)) for p in load_proposals(scope)]
    return templates.TemplateResponse(
        request,
        "blocks/outbox_list.html",
        {"entity": selected, "props": props, "approval_error": approval_error},
    )


@app.get("/outbox/{entity}", response_class=HTMLResponse)
@console_route(
    catches=(OutboxError, CrossScopeError, DestinationRegistryError),
    surface="page",
)
def outbox_screen(request: Request, scope: EntityScope) -> HTMLResponse:
    selected = scope.current_entity()
    vault = Vault(catalog)
    props = [(p, preview_diff(scope, p)) for p in load_proposals(scope)]
    return templates.TemplateResponse(
        request, "outbox.html",
        {"bundles": vault.bundles(), "entity": selected, "props": props},
    )


@app.post("/outbox/{entity}/approve", response_class=HTMLResponse)
@console_route(
    catches=(OutboxError, CrossScopeError, DestinationRegistryError),
    surface="fragment-only",
)
def outbox_approve(
    request: Request, scope: EntityScope, id: str = Form(...)
) -> HTMLResponse:
    approval_error = None
    try:
        approve(scope, id)
    except MissingProposalSource:
        approval_error = (
            "Approval refused: source is missing. Restore it or reject the proposal."
        )
    except StaleProposalSource:
        approval_error = (
            "Approval refused: source changed since this proposal was created. "
            "Create a fresh proposal."
        )
    except OutboxError:
        pass
    return _outbox_list(request, scope, approval_error=approval_error)


@app.post("/outbox/{entity}/reject", response_class=HTMLResponse)
@console_route(
    catches=(OutboxError, CrossScopeError, DestinationRegistryError),
    surface="fragment-only",
)
def outbox_reject(
    request: Request, scope: EntityScope, id: str = Form(...)
) -> HTMLResponse:
    try:
        reject(scope, id)
    except OutboxError:
        pass
    return _outbox_list(request, scope)


@app.get("/registry/{entity}/products", response_class=HTMLResponse)
@console_route(catches=(RegistryError, DestinationRegistryError), surface="page")
def registry_products(request: Request, scope: EntityScope) -> HTMLResponse:
    selected = scope.current_entity()
    vault = Vault(catalog)
    return templates.TemplateResponse(
        request, "registry.html",
        {"bundles": vault.bundles(), "entity": selected, "products": products_for(scope)},
    )


@app.post("/registry/{entity}/product/delete-preview", response_class=HTMLResponse)
@console_route(
    catches=(RegistryError, CrossScopeError, DestinationRegistryError),
    surface="fragment-only",
)
def registry_delete_preview(
    request: Request, scope: EntityScope, slug: str = Form(...)
) -> HTMLResponse:
    selected = scope.current_entity()
    prop = propose_delete(scope, "product", slug)
    return templates.TemplateResponse(
        request, "blocks/delete_impact.html",
        {"entity": selected, "slug": slug, "prop": prop,
         "report": reference_count(scope, "product", slug)},
    )


@app.post("/registry/{entity}/product/delete-execute", response_class=HTMLResponse)
@console_route(
    catches=(RegistryError, CrossScopeError, DestinationRegistryError),
    surface="fragment-only",
)
def registry_delete_execute(request: Request, scope: EntityScope,
                            id: str = Form(...), slug: str = Form(...)) -> HTMLResponse:
    try:
        execute_delete(scope, id)
        msg = f"Deleted product '{slug}'. One commit written."
    except RegistryError as e:
        msg = str(e).replace("\n", "<br>")
    return HTMLResponse(f'<div class="diff-head">{msg}</div>')
