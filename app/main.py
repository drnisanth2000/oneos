"""main.py — FastAPI app and route registration.

Step 1 proved the HTMX/Alpine/morph wiring with a static shell. Step 2 wires the
sidebar to the vault: bundles from entities.yaml, modules resolved from each
bundle's flags only. No slug, path, or module list is hardcoded here.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .classifier import Classifier
from .config import build_catalog, build_scope
from .entities import EntitySelectionError
from .inbox import read_inbox
from .outbox import (
    OutboxError,
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
from .scope import Scope
from .vault import Vault

BASE = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE / "templates"))

app = FastAPI(title="OneOS")
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")

catalog = build_catalog()


def entity_scope(entity: str) -> Scope:
    try:
        return build_scope(entity)
    except EntitySelectionError as exc:
        raise HTTPException(status_code=404) from exc


EntityScope = Annotated[Scope, Depends(entity_scope)]


@app.get("/", response_class=HTMLResponse)
def shell(request: Request) -> HTMLResponse:
    bundles = Vault(catalog).bundles()
    return templates.TemplateResponse(
        request,
        "shell.html",
        {"bundles": bundles, "now": datetime.now().strftime("%H:%M:%S")},
    )


@app.get("/blocks/pulse", response_class=HTMLResponse)
def pulse(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "blocks/pulse.html", {"now": datetime.now().strftime("%H:%M:%S")}
    )


@app.get("/triage", response_class=HTMLResponse)
def triage_default(request: Request):
    bundles = Vault(catalog).bundles()
    if not bundles:
        return HTMLResponse("<p>No entity bundles found.</p>")
    return RedirectResponse(url=f"/triage/{bundles[0].slug}", status_code=307)


@app.get("/triage/{entity}", response_class=HTMLResponse)
def triage(request: Request, scope: EntityScope) -> HTMLResponse:
    selected = scope.current_entity()
    vault = Vault(catalog)
    clf = Classifier(vault)
    rows = [
        (item, clf.classify(item.title, item.summary, item.source))
        for item in read_inbox(scope)
    ]
    return templates.TemplateResponse(
        request,
        "triage.html",
        {"bundles": vault.bundles(), "entity": selected, "rows": rows},
    )


@app.post("/triage/{entity}/propose", response_class=HTMLResponse)
def propose(
    request: Request,
    scope: EntityScope,
    filename: str = Form(...),
    module: str = Form(...),
    sub: str = Form(...),
    block: str = Form(...),
    rule_id: str = Form(""),
) -> HTMLResponse:
    # filename is untrusted form input — take the basename, never a path.
    safe = Path(filename).name
    item_path = scope.resolve("00-inbox", "active", safe)
    prop = propose_classification(
        scope, item_path,
        module=module, sub=sub, block=block, rule_id=(rule_id or None),
    )
    return templates.TemplateResponse(
        request,
        "blocks/diff.html",
        {"proposal": prop, "diff": preview_diff(scope, prop)},
    )


def _outbox_list(request: Request, scope: Scope) -> HTMLResponse:
    selected = scope.current_entity()
    props = [(p, preview_diff(scope, p)) for p in load_proposals(scope)]
    return templates.TemplateResponse(
        request, "blocks/outbox_list.html", {"entity": selected, "props": props}
    )


@app.get("/outbox/{entity}", response_class=HTMLResponse)
def outbox_screen(request: Request, scope: EntityScope) -> HTMLResponse:
    selected = scope.current_entity()
    vault = Vault(catalog)
    props = [(p, preview_diff(scope, p)) for p in load_proposals(scope)]
    return templates.TemplateResponse(
        request, "outbox.html",
        {"bundles": vault.bundles(), "entity": selected, "props": props},
    )


@app.post("/outbox/{entity}/approve", response_class=HTMLResponse)
def outbox_approve(
    request: Request, scope: EntityScope, id: str = Form(...)
) -> HTMLResponse:
    try:
        approve(scope, id)
    except OutboxError:
        pass
    return _outbox_list(request, scope)


@app.post("/outbox/{entity}/reject", response_class=HTMLResponse)
def outbox_reject(
    request: Request, scope: EntityScope, id: str = Form(...)
) -> HTMLResponse:
    try:
        reject(scope, id)
    except OutboxError:
        pass
    return _outbox_list(request, scope)


@app.get("/registry/{entity}/products", response_class=HTMLResponse)
def registry_products(request: Request, scope: EntityScope) -> HTMLResponse:
    selected = scope.current_entity()
    vault = Vault(catalog)
    return templates.TemplateResponse(
        request, "registry.html",
        {"bundles": vault.bundles(), "entity": selected, "products": products_for(scope)},
    )


@app.post("/registry/{entity}/product/delete-preview", response_class=HTMLResponse)
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
def registry_delete_execute(request: Request, scope: EntityScope,
                            id: str = Form(...), slug: str = Form(...)) -> HTMLResponse:
    try:
        execute_delete(scope, id)
        msg = f"Deleted product '{slug}'. One commit written."
    except RegistryError as e:
        msg = str(e).replace("\n", "<br>")
    return HTMLResponse(f'<div class="diff-head">{msg}</div>')
