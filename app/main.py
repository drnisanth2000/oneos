"""main.py — FastAPI app and route registration.

Step 1 proved the HTMX/Alpine/morph wiring with a static shell. Step 2 wires the
sidebar to the vault: bundles from entities.yaml, modules resolved from each
bundle's flags only. No slug, path, or module list is hardcoded here.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .classifier import Classifier
from .config import build_scope
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
    propose_delete,
    reference_count,
)
from .vault import Vault

BASE = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE / "templates"))

app = FastAPI(title="OneOS")
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")

# One scope for the process — the tenant boundary. Every path resolution and
# query goes through it (invariant 4).
scope = build_scope()


@app.get("/", response_class=HTMLResponse)
def shell(request: Request) -> HTMLResponse:
    bundles = Vault(scope).bundles()
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
    bundles = Vault(scope).bundles()
    if not bundles:
        return HTMLResponse("<p>No entity bundles found.</p>")
    return RedirectResponse(url=f"/triage/{bundles[0].slug}", status_code=307)


@app.get("/triage/{entity}", response_class=HTMLResponse)
def triage(request: Request, entity: str) -> HTMLResponse:
    # current_entity() is the tenant boundary; set it before any read.
    scope.set_current_entity(entity)
    vault = Vault(scope)
    clf = Classifier(vault)
    rows = [
        (item, clf.classify(item.title, item.summary, item.source))
        for item in read_inbox(scope, entity)
    ]
    return templates.TemplateResponse(
        request,
        "triage.html",
        {"bundles": vault.bundles(), "entity": entity, "rows": rows},
    )


@app.post("/triage/{entity}/propose", response_class=HTMLResponse)
def propose(
    request: Request,
    entity: str,
    filename: str = Form(...),
    module: str = Form(...),
    sub: str = Form(...),
    block: str = Form(...),
    rule_id: str = Form(""),
) -> HTMLResponse:
    scope.set_current_entity(entity)
    # filename is untrusted form input — take the basename, never a path.
    safe = Path(filename).name
    item_path = scope.resolve(entity, "00-inbox", "active", safe)
    prop = propose_classification(
        scope, entity, item_path,
        module=module, sub=sub, block=block, rule_id=(rule_id or None),
    )
    return templates.TemplateResponse(
        request,
        "blocks/diff.html",
        {"proposal": prop, "diff": preview_diff(scope, prop)},
    )


def _outbox_list(request: Request, entity: str) -> HTMLResponse:
    props = [(p, preview_diff(scope, p)) for p in load_proposals(scope, entity)]
    return templates.TemplateResponse(
        request, "blocks/outbox_list.html", {"entity": entity, "props": props}
    )


@app.get("/outbox/{entity}", response_class=HTMLResponse)
def outbox_screen(request: Request, entity: str) -> HTMLResponse:
    scope.set_current_entity(entity)
    vault = Vault(scope)
    props = [(p, preview_diff(scope, p)) for p in load_proposals(scope, entity)]
    return templates.TemplateResponse(
        request, "outbox.html",
        {"bundles": vault.bundles(), "entity": entity, "props": props},
    )


@app.post("/outbox/{entity}/approve", response_class=HTMLResponse)
def outbox_approve(request: Request, entity: str, id: str = Form(...)) -> HTMLResponse:
    scope.set_current_entity(entity)
    try:
        approve(scope, entity, id)
    except OutboxError:
        pass
    return _outbox_list(request, entity)


@app.post("/outbox/{entity}/reject", response_class=HTMLResponse)
def outbox_reject(request: Request, entity: str, id: str = Form(...)) -> HTMLResponse:
    scope.set_current_entity(entity)
    try:
        reject(scope, entity, id)
    except OutboxError:
        pass
    return _outbox_list(request, entity)


def _products_for(entity: str) -> list[str]:
    import yaml

    path = scope.system_path("products.yaml")
    if not path.is_file():
        return []
    cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return list(((cfg.get("products") or {}).get(entity) or {}).keys())


@app.get("/registry/{entity}/products", response_class=HTMLResponse)
def registry_products(request: Request, entity: str) -> HTMLResponse:
    scope.set_current_entity(entity)
    vault = Vault(scope)
    return templates.TemplateResponse(
        request, "registry.html",
        {"bundles": vault.bundles(), "entity": entity, "products": _products_for(entity)},
    )


@app.post("/registry/{entity}/product/delete-preview", response_class=HTMLResponse)
def registry_delete_preview(request: Request, entity: str, slug: str = Form(...)) -> HTMLResponse:
    scope.set_current_entity(entity)
    prop = propose_delete(scope, entity, "product", slug)
    return templates.TemplateResponse(
        request, "blocks/delete_impact.html",
        {"entity": entity, "slug": slug, "prop": prop,
         "report": reference_count(scope, "product", slug)},
    )


@app.post("/registry/{entity}/product/delete-execute", response_class=HTMLResponse)
def registry_delete_execute(request: Request, entity: str,
                            id: str = Form(...), slug: str = Form(...)) -> HTMLResponse:
    scope.set_current_entity(entity)
    try:
        execute_delete(scope, entity, id)
        msg = f"Deleted product '{slug}'. One commit written."
    except RegistryError as e:
        msg = str(e).replace("\n", "<br>")
    return HTMLResponse(f'<div class="diff-head">{msg}</div>')
