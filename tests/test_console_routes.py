"""Routes — framework surface (S6 Task 10, design §4 "Rule 4" and §5 "Rule 6").

Three framework-owned surfaces this task closes:

- `entity_scope` no longer converts `EntitySelectionError` into
  `HTTPException`; a dedicated handler describes it as `E-ENTITY` at 404.
- `RequestValidationError` is described as `E-REQUEST` without ever echoing
  the submitted field name or value (the resolver never reads `exc.errors()`).
- `StarletteHTTPException` — an unmatched URL, a wrong method, a `StaticFiles`
  miss — keeps the framework's own status and plain body, except that an
  HTMX request gets its body replaced with safe text. This closes the live
  regression window Task 7 opened: 4xx/5xx responses now swap under HTMX, so
  without this handler, raw framework text would swap into an operator
  target. `HTTPException` stays outside the taxonomy (Rule 6): the status is
  never remapped through `describe()`.
- A global fallback describes any exception no route has caught and returns
  the code's page status — never 200.

Synthetic vaults only; the real vault is never touched.
"""
from __future__ import annotations

import asyncio
import builtins
import hashlib
import importlib
import inspect
import html
import json
import os
import re
import subprocess

import yaml
from html.parser import HTMLParser
from pathlib import Path

import pytest
from starlette.requests import Request
from starlette.testclient import TestClient

from app.console_routing import console_route
from tests.conftest import (
    git_cached_diff,
    git_changed_paths,
    git_count_commits,
    git_head,
    git_index_entries,
    git_status_apart_from_quarantine,
    git_status_bytes,
    git_worktree_diff,
    scaffold_modules,
    write_vault,
)

ENTITIES = """
version: "1.0"
entities:
  alpha: { label: Alpha, flags: [] }
"""


def _load_main(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entities_yaml: str):
    """Fresh `app.main` module bound to a throwaway vault, as
    tests/test_console_render.py already does for the same reason: the app
    reads `ONEOS_VAULT` at import time."""
    write_vault(tmp_path, entities_yaml)
    scaffold_modules(tmp_path, "alpha", ["00-intake", "01-core", "02-work"])
    monkeypatch.setenv("ONEOS_VAULT", str(tmp_path))
    import app.main as main

    importlib.reload(main)
    return main


def _client(tmp_path, monkeypatch, entities_yaml=ENTITIES, **kwargs) -> TestClient:
    main = _load_main(tmp_path, monkeypatch, entities_yaml)
    return TestClient(main.app, **kwargs)


def _synthetic_request(app, *, endpoint=None, headers=None) -> Request:
    """A minimal ASGI-http scope, built by hand rather than routed, so the
    RequestValidationError test can pin the handler's page/fragment behavior
    directly instead of depending on which real route it happens to reach."""
    encoded_headers = [
        (key.lower().encode(), value.encode()) for key, value in (headers or {}).items()
    ]
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/synthetic",
        "raw_path": b"/synthetic",
        "query_string": b"",
        "headers": encoded_headers,
        "app": app,
        "router": app.router,
        "endpoint": endpoint,
    }
    return Request(scope)


def test_unknown_entity_renders_e_entity_404(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    response = client.get("/triage/hostile-unknown-slug-marker")

    assert response.status_code == 404
    assert "E-ENTITY" in response.text
    # Rule 9 / §6 disclosure: the unresolved slug is never echoed back.
    assert "hostile-unknown-slug-marker" not in response.text


def test_request_validation_renders_e_request_without_echo(tmp_path, monkeypatch):
    """Must drive the app, not the handler function.

    An earlier version called `main._request_validation_error_handler` directly
    with a synthetic scope. Unregistering the `@app.exception_handler` — the
    production wiring — left all eight tests green, because FastAPI's default
    also returns 422 and nothing proved a real malformed form reaches
    `E-REQUEST`.

    The status is **200, not 422**: every route that takes form data is
    `surface="fragment-only"`, and Rule 5 selects by route shape first, so the
    fragment renderer applies and a refusal-severity code returns 200. The
    plan's test name predated the decoration and its `422` no longer describes
    any reachable case — no page-surface route accepts a form.
    """
    client = _client(tmp_path, monkeypatch)

    response = client.post(
        "/triage/alpha/propose",
        data={"filename": "note.md", "hostile-field-name": "hostile-submitted-value"},
    )

    assert response.status_code == 200
    body = response.text
    assert "E-REQUEST" in body
    assert "hostile-field-name" not in body
    assert "hostile-submitted-value" not in body


def test_request_validation_under_htmx_is_a_swappable_fragment(tmp_path, monkeypatch):
    """Same outcome with the header present — the swap target must receive it."""
    client = _client(tmp_path, monkeypatch)

    response = client.post(
        "/triage/alpha/propose",
        data={"filename": "note.md"},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    assert "E-REQUEST" in response.text


def test_refusal_severity_escapee_keeps_page_status_under_htmx(tmp_path, monkeypatch):
    """The global fallback must never return 200, even rendering a fragment.

    A refusal-severity exception escaping a route's declared family is a defect;
    `status_for` would give it 200 under `HX-Request` and monitoring would see a
    success. The existing fallback test is blind to this twice over — it injects
    a `RuntimeError` (E-UNKNOWN, attention, 500 either way) and sends no
    `HX-Request`.
    """
    main = _load_main(tmp_path, monkeypatch, ENTITIES)
    from app.outbox import OutboxError

    # The vehicle must be genuinely OUTSIDE triage's declared family. An
    # earlier revision used `OutOfScopeError`, which is a `CrossScopeError`
    # and therefore declared — the test passed only because triage was, at
    # that point, letting declared members escape to the fallback. Fixing
    # that made the false premise visible. `OutboxError` is undeclared here
    # and describes to E-INVALID: refusal severity, page status 422.
    def _escape(*args, **kwargs):
        raise OutboxError("undeclared by this route's catch family")

    monkeypatch.setattr(main, "read_inbox", _escape)
    client = TestClient(main.app, raise_server_exceptions=False)

    response = client.get("/triage/alpha", headers={"HX-Request": "true"})

    # Refusal severity: the fragment rule would have returned 200.
    assert response.status_code == 422
    assert "E-INVALID" in response.text


def test_unmatched_url_keeps_plain_404(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    response = client.get("/this-route-does-not-exist")

    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}
    assert "E-" not in response.text


def test_wrong_method_keeps_405(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    response = client.put("/")

    assert response.status_code == 405
    assert "E-" not in response.text


def test_static_miss_keeps_404(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    response = client.get("/static/does-not-exist.js")

    assert response.status_code == 404
    assert "E-" not in response.text


def test_htmx_unmatched_url_gets_safe_body_at_404(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    plain = client.get("/this-route-does-not-exist")
    swapped = client.get(
        "/this-route-does-not-exist", headers={"HX-Request": "true"}
    )

    # Status is the framework's own, preserved exactly (design §4: "The
    # framework's status is preserved, not the code's").
    assert swapped.status_code == 404 == plain.status_code
    # The body is replaced only for the HTMX request; the plain response is
    # the framework's own untouched JSON detail.
    assert plain.headers["content-type"].startswith("application/json")
    assert swapped.headers["content-type"].startswith("text/html")
    assert "Not Found" not in swapped.text
    assert 'role="alert"' in swapped.text
    # Never mapped through the taxonomy (Rule 6): no described code appears.
    assert "E-" not in swapped.text


def test_unhandled_error_reaches_global_fallback_at_500(tmp_path, monkeypatch):
    main = _load_main(tmp_path, monkeypatch, ENTITIES)

    def _boom(scope):
        raise RuntimeError("synthetic unmapped failure - must not leak")

    monkeypatch.setattr(main, "read_inbox", _boom)
    # ServerErrorMiddleware always re-raises after handing the response to
    # the handler, specifically so a test client can opt in to seeing it —
    # three existing tests in tests/test_app.py already rely on this.
    route_client = TestClient(main.app, raise_server_exceptions=False)

    response = route_client.get("/triage/alpha")

    assert response.status_code == 500
    assert "E-UNKNOWN" in response.text
    assert "synthetic unmapped failure" not in response.text


def test_described_errors_never_reach_the_global_fallback(tmp_path, monkeypatch):
    main = _load_main(tmp_path, monkeypatch, ENTITIES)
    calls = []
    original = main.app.exception_handlers[Exception]

    async def _spy(request, exc):
        calls.append(exc)
        return await original(request, exc)

    # Mutate before the first dispatch: Starlette's middleware stack is built
    # lazily on first call and cached, so this is picked up cleanly.
    main.app.exception_handlers[Exception] = _spy
    client = TestClient(main.app)

    entity_response = client.get("/triage/hostile-unknown-slug-marker")
    assert entity_response.status_code == 404
    assert calls == []

    validation_response = client.post(
        "/triage/alpha/propose", data={"filename": "marker.md"}
    )
    assert validation_response.status_code < 500
    assert calls == []

    framework_response = client.get("/this-route-does-not-exist")
    assert framework_response.status_code == 404
    assert calls == []

    # Sanity: the fallback genuinely is reachable, so an empty `calls` above
    # is proof of routing, not of a spy that never fires.
    def _boom(scope):
        raise RuntimeError("synthetic unmapped failure")

    monkeypatch.setattr(main, "read_inbox", _boom)
    TestClient(main.app, raise_server_exceptions=False).get("/triage/alpha")
    assert len(calls) == 1


# --- Task 11: triage and propose (design §5 "The Gate 1 stopwatch", §5
# route inventory, §8 test matrix) ---------------------------------------


def test_triage_row_with_missing_module_dir_shows_e_dest_not_tamper(tmp_path, monkeypatch):
    """design §2: 'UnsafeDestinationPath currently fires when a module
    directory is merely absent — a first-class expected state with a
    standing E4 regression — which would raise a tampering alarm for a
    skipped scaffolding step.' Absence must resolve to E-DEST, never E-TAMPER.
    """
    entities_yaml = """
version: "1.0"
entities:
  alpha: { label: Alpha, flags: [] }
"""
    archetypes_yaml = """
version: "2.0"
modules:
  02-work: { block: build }
"""
    write_vault(tmp_path, entities_yaml, archetypes_yaml)
    classifier_dir = tmp_path / "_system/classifier"
    classifier_dir.mkdir(parents=True)
    (classifier_dir / "rules.yaml").write_text(
        "version: \"1.0\"\n"
        "rules:\n"
        "  - id: route-to-missing-module\n"
        "    match: {any: [missing-module-marker]}\n"
        "    route: {module: 02-work, sub: null}\n"
        "default: {module: 02-work, sub: null}\n",
        encoding="utf-8",
    )
    active = tmp_path / "alpha/00-inbox/active"
    active.mkdir(parents=True)
    (active / "receipt.md").write_text(
        "---\ntitle: missing-module-marker\nsub: triage\n---\nbody\n",
        encoding="utf-8",
    )
    # alpha/02-work is deliberately never scaffolded on disk.
    monkeypatch.setenv("ONEOS_VAULT", str(tmp_path))
    import app.main as main

    importlib.reload(main)
    client = TestClient(main.app)

    response = client.get("/triage/alpha")

    assert response.status_code == 200
    assert "E-DEST" in response.text
    assert "E-TAMPER" not in response.text


def test_triage_row_with_symlinked_receipt_shows_e_tamper(tmp_path, monkeypatch):
    """A redirected receipt reaching per-row destination resolution is
    described as E-TAMPER, on its own row, without echoing any path (Rule 9).

    Scope, stated precisely so this is not read as the whole story: for a
    receipt that is *already* a symlink on disk, `read_inbox`'s own
    `_require_real_receipt` (`app/inbox.py:59`) aborts the entire listing
    before any row exists, and `triage`'s outer handler describes that as a
    page-level E-TAMPER at 409 — covered by
    `test_triage_declared_family_never_reaches_the_global_fallback`. That is
    the reachable case, and one symlinked receipt does hide every valid row.

    What this test isolates is the TOCTOU path — a receipt redirected after
    the listing guard passed but before its destination resolves — which is
    the only way the per-row branch is entered. Hence the `read_inbox` stub:
    it is bypassing the list-time guard, not the condition under test.
    """
    from app.inbox import InboxItem

    main = _load_main(tmp_path, monkeypatch, ENTITIES)
    active = tmp_path / "alpha/00-inbox/active"
    active.mkdir(parents=True)
    target = tmp_path / "elsewhere.md"
    target.write_text("outside the vault's inbox lifecycle\n", encoding="utf-8")
    receipt = active / "receipt.md"
    receipt.symlink_to(target)

    def _fake_read_inbox(scope):
        # Bypasses read_inbox's own list-time symlink guard (which would
        # otherwise abort the whole listing before any row exists) so this
        # test isolates resolve_classification_destination's own per-row
        # redirection check — the same one a TOCTOU race would reach.
        return [
            InboxItem(
                path=receipt,
                title="tamper-marker",
                summary="",
                source=None,
                fm={"sub": "triage"},
            )
        ]

    monkeypatch.setattr(main, "read_inbox", _fake_read_inbox)
    client = TestClient(main.app)

    response = client.get("/triage/alpha")

    assert response.status_code == 200
    assert "E-TAMPER" in response.text
    assert "elsewhere.md" not in response.text
    assert "receipt.md" not in response.text


def test_triage_page_with_broken_registry_shows_e_config_page(tmp_path, monkeypatch):
    """A broken archetypes.yaml is a vault-wide property, not a row-local
    one (design §3's Phase 2 reasoning applies here too): it aborts the
    whole page as one described E-CONFIG page, not a per-row alert.

    The break is deliberately placed where only the per-row destination
    resolution can reach it — a non-canonical `block:` value, caught by
    `active_modules_for`'s deep structural validation but not by the shallow
    `block_of` the classifier itself calls — rather than a YAML parse error,
    which would abort before any row is even read and would leave a
    mistakenly widened per-row catch (adding DestinationRegistryError to it)
    undetected.
    """
    entities_yaml = """
version: "1.0"
entities:
  alpha: { label: Alpha, flags: [] }
"""
    archetypes_yaml = """
version: "2.0"
modules:
  02-work: { block: 123 }
"""
    write_vault(tmp_path, entities_yaml, archetypes_yaml)
    classifier_dir = tmp_path / "_system/classifier"
    classifier_dir.mkdir(parents=True)
    (classifier_dir / "rules.yaml").write_text(
        "version: \"1.0\"\n"
        "rules:\n"
        "  - id: route-to-broken-block\n"
        "    match: {any: [broken-registry-marker]}\n"
        "    route: {module: 02-work, sub: null}\n"
        "default: {module: 02-work, sub: null}\n",
        encoding="utf-8",
    )
    active = tmp_path / "alpha/00-inbox/active"
    active.mkdir(parents=True)
    (active / "receipt.md").write_text(
        "---\ntitle: broken-registry-marker\nsub: triage\n---\nbody\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ONEOS_VAULT", str(tmp_path))
    import app.main as main

    importlib.reload(main)
    # `triage` answers this itself, through its outer handler over the
    # declared family — the global fallback is NOT reached, and
    # test_triage_declared_family_never_reaches_the_global_fallback forbids
    # it being reached. An earlier revision of this comment described the
    # escape as intended contract; it was a defect (raw traceback out of
    # ServerErrorMiddleware on every triage request against a broken
    # registry) and is fixed. No `raise_server_exceptions=False` is needed,
    # precisely because nothing escapes any more.
    client = TestClient(main.app)

    response = client.get("/triage/alpha")

    assert response.status_code == 500
    assert "E-CONFIG" in response.text
    # The whole page is the described error, not the triage listing with a
    # per-row alert inside it.
    assert "Triage ·" not in response.text


def _propose_client(tmp_path, monkeypatch):
    main = _load_main(tmp_path, monkeypatch, ENTITIES)
    active = tmp_path / "alpha/00-inbox/active"
    active.mkdir(parents=True)
    (active / "note.md").write_text(
        "---\ntitle: propose-test-marker\nsub: triage\n---\nbody\n",
        encoding="utf-8",
    )
    (tmp_path / "alpha/02-work/active").mkdir(parents=True, exist_ok=True)
    return main, TestClient(main.app)


def test_propose_refusal_renders_alert_into_diff_target_at_200(tmp_path, monkeypatch):
    _main, client = _propose_client(tmp_path, monkeypatch)

    response = client.post(
        "/triage/alpha/propose",
        data={"filename": "note.md", "module": "hostile-unknown-module", "sub": ""},
    )

    assert response.status_code == 200
    assert 'role="alert"' in response.text
    assert "E-DEST" in response.text
    assert "HX-Trigger" not in response.headers


def test_propose_alert_preserves_triage_alpine_scope(tmp_path, monkeypatch):
    """§8 test matrix: `#diff-{index}` swaps `innerHTML`, so the fragment
    must not reproduce the `#diff-N` root (that would nest a duplicate id)
    and must not carry its own `x-data` root (that would shadow the
    enclosing `triage(...)` Alpine scope)."""
    _main, client = _propose_client(tmp_path, monkeypatch)

    response = client.post(
        "/triage/alpha/propose",
        data={"filename": "note.md", "module": "hostile-unknown-module", "sub": ""},
    )

    assert response.status_code == 200
    body = response.text
    assert 'id="diff-' not in body
    assert "x-data" not in body


def test_propose_success_fragment_also_honours_the_innerhtml_shape(
    tmp_path, monkeypatch
):
    """The refusal fragment is not the only thing that swaps into
    `#diff-{index}`. `blocks/diff.html` is what lands there in the normal
    flow, and design §8 requires the declared shape to hold for the route,
    not merely for its error path — an `innerHTML` target must not have its
    root reproduced inside itself.
    """
    _main, client = _propose_client(tmp_path, monkeypatch)

    response = client.post(
        "/triage/alpha/propose",
        data={"filename": "note.md", "module": "02-work", "sub": ""},
    )

    assert response.status_code == 200
    assert 'id="diff-' not in response.text
    assert "x-data" not in response.text


def test_stopwatch_counts_only_persisted_proposals(tmp_path, monkeypatch):
    """ledger's binding resolution of the Gate 1 stopwatch discrepancy: the
    counter keys on the `HX-Trigger` response header read inside the
    existing `htmx:afterRequest` hook, set only once `propose_classification`
    has returned — never on `e.detail.successful`, which a refusal (also a
    successful HTTP exchange) would satisfy just as well."""
    main, client = _propose_client(tmp_path, monkeypatch)

    refused = client.post(
        "/triage/alpha/propose",
        data={"filename": "note.md", "module": "hostile-unknown-module", "sub": ""},
    )
    assert refused.status_code == 200
    assert "HX-Trigger" not in refused.headers

    persisted = client.post(
        "/triage/alpha/propose",
        data={"filename": "note.md", "module": "02-work", "sub": ""},
    )
    assert persisted.status_code == 200
    assert persisted.headers.get("HX-Trigger") == main._PROPOSAL_PERSISTED_EVENT

    triage_html = client.get("/triage/alpha").text
    # The listener itself must survive: tests/test_app.py's unlisted
    # test_triage_screen_has_gate1_timing_instrument asserts this string, and
    # the ledger's binding resolution keeps the hook while changing its key.
    assert "htmx:afterRequest" in triage_html
    assert "e.detail.successful" not in triage_html

    # Pin the whole COMPARISON as rendered, not its tokens. Asserting only
    # that "getResponseHeader" and the event name appear leaves the two
    # mutations that matter green: flipping `===` to `!==` makes the counter
    # increment on refusals ONLY — verbatim the Gate 1 corruption design §5
    # exists to prevent — and drifting the header name makes it never
    # increment at all. Both keep every token present.
    assert (
        "xhr.getResponseHeader('HX-Trigger') === PROPOSAL_PERSISTED_EVENT"
        in triage_html
    )
    assert f'PROPOSAL_PERSISTED_EVENT = "{main._PROPOSAL_PERSISTED_EVENT}"' in triage_html


def test_propose_post_persistence_failure_still_signals_and_keeps_the_proposal(
    tmp_path, monkeypatch
):
    """`propose` persists, then renders. A described failure in the second
    phase must still report the proposal as persisted, because it is.

    design §8's state-proof matrix calls this `(committed=no,
    persistence=proposal-written)` and names `propose` as one of only two
    routes permitted to declare it: "the domain action succeeded, and S6 must
    not roll back a successful write merely because rendering failed."

    Task 11 introduced this branch and asserts a behaviour about it, so Task 11
    pins it. Deleting the post-persistence try/except entirely left the whole
    suite green before this test existed.
    """
    main, client = _propose_client(tmp_path, monkeypatch)
    # `Scope.root` is the vault root, not the entity root; the outbox is the
    # bound entity's, exactly as tests/test_app.py addresses it.
    outbox = tmp_path / "alpha" / "outbox"
    before = set(outbox.glob("*.yaml")) if outbox.exists() else set()

    def _fail_after_persisting(*args, **kwargs):
        raise main.OutboxError("diff rendering refused after persistence")

    monkeypatch.setattr(main, "preview_diff", _fail_after_persisting)

    response = client.post(
        "/triage/alpha/propose",
        data={"filename": "note.md", "module": "02-work", "sub": ""},
    )

    # Described, not a raw fault, and rendered as a fragment refusal.
    assert response.status_code == 200
    assert 'role="alert"' in response.text
    assert "E-INVALID" in response.text
    assert "diff rendering refused" not in response.text

    # The signal is honest: the proposal really is on disk, so the operator's
    # Gate 1 count must include it.
    assert response.headers.get("HX-Trigger") == main._PROPOSAL_PERSISTED_EVENT
    written = set(outbox.glob("*.yaml")) - before
    assert len(written) == 1


def test_triage_declared_family_never_reaches_the_global_fallback(
    tmp_path, monkeypatch
):
    """design §5: the global handler "catches only what escapes a route", and
    "relying on it is a failure rather than a silent default".

    `triage` declares three families. Two of them arise OUTSIDE the per-row
    guard — `read_inbox` raises `RedirectedPathError` (a `CrossScopeError`)
    and `Vault.bundles()` raises `DestinationRegistryError` while the
    template context is built — and both escaped the route before this was
    fixed, so every triage request against a broken registry also logged an
    unhandled-exception traceback from Starlette's ServerErrorMiddleware.

    This is Task 14 Step 3's contract, pinned here for the one route Task 11
    owns rather than discovered two tasks later.
    """
    from app.scope import RedirectedPathError
    from app.vault import DestinationRegistryError

    main = _load_main(tmp_path, monkeypatch, ENTITIES)
    scaffold_modules(tmp_path, "alpha", ["00-intake", "01-core", "02-work"])

    reached = []
    original = main.app.exception_handlers[Exception]

    async def _spy(request, exc):
        reached.append(type(exc).__name__)
        return await original(request, exc)

    main.app.exception_handlers[Exception] = _spy
    client = TestClient(main.app, raise_server_exceptions=False)

    for raiser, expected_code, expected_status in (
        (RedirectedPathError("redirected inbox"), "E-TAMPER", 409),
        (DestinationRegistryError("registries unreadable"), "E-CONFIG", 500),
    ):
        def _raise(*args, __exc=raiser, **kwargs):
            raise __exc

        monkeypatch.setattr(main, "read_inbox", _raise)
        response = client.get("/triage/alpha")

        assert response.status_code == expected_status, expected_code
        assert expected_code in response.text
        assert reached == [], f"global fallback reached for {expected_code}"

    # A refusal-severity declared member now returns 200 under HX-Request,
    # where before the outer handler existed the fallback's
    # `force_page_status` forced 404. That is Rule 5 working as written —
    # "severity = refusal -> 200" — and `force_page_status` exists only
    # because a fallback response is a defect, never an expected refusal.
    # It is a live status change on the same class, so it is pinned here
    # rather than left to be discovered.
    from app.scope import OutOfScopeError

    def _refuse(*args, **kwargs):
        raise OutOfScopeError("resolved outside the selected entity")

    monkeypatch.setattr(main, "read_inbox", _refuse)
    assert client.get("/triage/alpha").status_code == 404          # page
    htmx = client.get("/triage/alpha", headers={"HX-Request": "true"})
    assert htmx.status_code == 200                                  # fragment
    assert "E-SCOPE" in htmx.text
    assert reached == []

    # Sanity: the spy fires for something genuinely undeclared, so the empty
    # list above is proof of routing rather than of a spy that never runs.
    def _boom(*args, **kwargs):
        raise RuntimeError("undeclared by this route")

    monkeypatch.setattr(main, "read_inbox", _boom)
    client.get("/triage/alpha")
    assert reached == ["RuntimeError"]


def test_bundles_shape_failures_never_blank_any_sidebar_route(tmp_path, monkeypatch):
    """Task 8 corrective, the exact reproduction the human ruling names: a
    hand-edited registry driven through all five of `bundles()`'s callers —
    `/`, `/triage`, `/triage/<entity>`, `/outbox/<entity>`, and
    `/registry/<entity>/products` — with NO monkeypatching of application
    code, only real files on a real (throwaway) vault.

    Before this fix, every one of these scenarios returned a COMPLETELY
    EMPTY 500 body on every route: `resolve_flags`/`active_modules` raised a
    bare `AttributeError`, `TypeError`, or untyped `ValueError` instead of
    `DestinationRegistryError`, which is the one type every route's own
    catch family (`_SIDEBAR_CATCHES` / `_TRIAGE_CATCHES` / `_OUTBOX_CATCHES`
    / `_REGISTRY_PRODUCTS_CATCHES`) already declares — so the raw exception
    escaped the route, and (via `app/main.py`'s C1 sidebar-rebuild
    re-entrancy, which raises the identical unconverted error a second time
    while trying to render the very error page) escaped the GLOBAL fallback
    too, straight to Starlette's `ServerErrorMiddleware`.

    Once the type is converted, every route's own handler answers it, so the
    global fallback in particular must never be reached — proved with the
    same exception-handler spy `test_triage_declared_family_never_reaches_
    the_global_fallback` uses, but on real files, not an injected raise.
    """
    scenarios = {
        "unknown_flag_in_entities_yaml": (
            'version: "1.0"\nentities:\n  alpha: {label: Alpha, flags: [nosuchflag]}\n',
            None,
        ),
        "archetypes_module_spec_as_list": (
            ENTITIES,
            'version: "2.0"\nflags: {}\nmodules:\n  00-intake: [block, system]\n',
        ),
        "archetypes_module_spec_as_scalar": (
            ENTITIES,
            'version: "2.0"\nflags: {}\nmodules:\n  00-intake: system\n',
        ),
        "archetypes_flags_as_scalar": (
            ENTITIES,
            'version: "2.0"\nflags: 5\nmodules:\n  00-intake: {block: system}\n',
        ),
        # M5 (S6 corrective review): the comment above named "`modules:`
        # not-a-mapping" and "`requires_flag:`-as-list" as measured route
        # categories, but neither had a route-level test — only the unit
        # shape-space test in tests/test_console_readers.py covered them.
        "archetypes_modules_not_a_mapping": (
            ENTITIES,
            'version: "2.0"\nflags: {}\nmodules:\n  - 00-intake\n',
        ),
        "archetypes_requires_flag_as_list": (
            ENTITIES,
            'version: "2.0"\nflags: {}\nmodules:\n  00-intake: {block: system, requires_flag: [x]}\n',
        ),
        # C-A (S6 corrective review): the module KEY, not just the module
        # VALUE, is a fifth unguarded access point — a truncated
        # `00-intake` -> `00`, read back by YAML as int `0`, raises
        # `TypeError` at `(bundle_dir / name).is_dir()` (app/vault.py).
        "archetypes_modules_key_truncated_to_int": (
            ENTITIES,
            'version: "2.0"\nflags: {}\nmodules:\n  00: {block: system}\n',
        ),
        # A `modules:` mapping with both string and int keys raises at the
        # OTHER key-shaped site, `sorted(out)` in `Vault.active_modules`
        # (app/vault.py) — `str` and `int` do not order.
        "archetypes_modules_mixed_key_types": (
            ENTITIES,
            'version: "2.0"\nflags: {}\nmodules:\n  "00-intake": {block: system}\n  5: {block: system}\n',
        ),
    }

    for name, (entities_yaml, archetypes_yaml) in scenarios.items():
        if archetypes_yaml is None:
            write_vault(tmp_path, entities_yaml)
        else:
            write_vault(tmp_path, entities_yaml, archetypes_yaml)
        scaffold_modules(tmp_path, "alpha", ["00-intake", "01-core", "02-work"])
        monkeypatch.setenv("ONEOS_VAULT", str(tmp_path))
        import app.main as main

        importlib.reload(main)

        reached = []
        original = main.app.exception_handlers[Exception]

        async def _spy(request, exc):
            reached.append(type(exc).__name__)
            return await original(request, exc)

        monkeypatch.setitem(main.app.exception_handlers, Exception, _spy)
        client = TestClient(main.app, raise_server_exceptions=False)

        for url in (
            "/", "/triage", "/triage/alpha", "/outbox/alpha",
            "/registry/alpha/products",
        ):
            response = client.get(url)
            assert response.status_code == 500, f"{name} {url}: {response.status_code}"
            assert response.text != "", f"{name} {url}: completely empty body"
            assert 'role="alert"' in response.text, f"{name} {url}: {response.text}"
            assert "E-CONFIG" in response.text, f"{name} {url}: {response.text}"

        assert reached == [], f"{name}: global fallback reached: {reached}"


# --- Task 12: routes, outbox (design §3 "Rule 3", §5 route inventory,
# §8 test matrix) -----------------------------------------------------------
#
# `outbox_screen` had no try/except at all, and `outbox_approve`/
# `outbox_reject` caught only `OutboxError` — the exact Task 11 trap, unfixed
# here until now: all three declare `(OutboxError, CrossScopeError,
# DestinationRegistryError)`, so `CrossScopeError` and `DestinationRegistryError`
# escaped to the global fallback (and, per Starlette's `ServerErrorMiddleware`,
# logged a raw traceback) for every one of the three routes.


def _outbox_fingerprint(tmp_path, proposal_id, entity="alpha"):
    from app.outbox import get_proposal_review
    from app.scope import Scope

    return get_proposal_review(Scope(tmp_path, entity), proposal_id).sha256


#: A well-formed fingerprint that binds nothing. Used where the id names no
#: reviewable proposal (a hostile id, a poisoned listing), so the refusal
#: under test is reached before any comparison against stored bytes.
_UNBOUND_FINGERPRINT = "0" * 64


def _action_data(tmp_path, proposal_id, entity="alpha"):
    """Form data for an outbox action, carrying the proposal's own
    fingerprint exactly as a rendered button would."""
    return {
        "id": proposal_id,
        "review_sha256": _outbox_fingerprint(tmp_path, proposal_id, entity),
    }


def _outbox_proposal_client(tmp_path, monkeypatch, *, proposal_id=None):
    """A vault with one active module and one valid, loadable outbox
    proposal, in an initialised (but uncommitted) Git repository.

    Sufficient for every test that monkeypatches
    `app.outbox.execute_transaction` (or `main.approve`/`main.reject`
    directly) rather than driving a real transaction.

    S7: `git init` is required now that reject removes the proposal through
    the conditional-removal primitive, which takes the per-vault approval
    lock so a reject cannot race an approval that owns the same record. The
    lock lives in the Git directory, so a vault without one is no longer a
    faithful fixture — a real vault always has it.
    """
    main = _load_main(tmp_path, monkeypatch, ENTITIES)
    active = tmp_path / "alpha/00-inbox/active"
    active.mkdir(parents=True)
    source = active / "marker.md"
    source.write_text(
        "---\ntitle: outbox-route-marker\nsub: triage\n---\nbody\n",
        encoding="utf-8",
    )
    (tmp_path / "alpha/02-work/active").mkdir(parents=True, exist_ok=True)
    outbox_dir = tmp_path / "alpha/outbox"
    outbox_dir.mkdir(parents=True, exist_ok=True)
    proposal_id = proposal_id or "20260815T090703-" + "aa" * 16
    (outbox_dir / f"{proposal_id}.yaml").write_text(
        "\n".join(
            (
                f"id: {proposal_id}",
                "action: classify",
                "entity: alpha",
                "src: alpha/00-inbox/active/marker.md",
                f"source_sha256: {hashlib.sha256(source.read_bytes()).hexdigest()}",
                "dst: alpha/02-work/active/marker.md",
                "module: 02-work",
                "sub:",
                "block: build",
                "status: pending",
                "",
            )
        ),
        encoding="utf-8",
    )
    if not (tmp_path / ".git").exists():
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    return main, TestClient(main.app), proposal_id


def _git_outbox_proposal_client(tmp_path, monkeypatch):
    """Same fixture, but a real Git repository — required to drive an actual
    `execute_transaction` commit (design §8 state-proof matrix: the committed
    case must be a genuine post-commit cleanup failure, not a monkeypatched
    `execute_transaction`, which produces no commit at all)."""
    main, client, proposal_id = _outbox_proposal_client(tmp_path, monkeypatch)
    (tmp_path / ".gitignore").write_text("*/outbox/*.yaml\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"],
        cwd=tmp_path, check=True,
    )
    subprocess.run(["git", "config", "user.name", "test"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "fixture"], cwd=tmp_path, check=True)
    return main, client, proposal_id


def test_outbox_screen_renders_projection_blocked_listing(tmp_path, monkeypatch):
    """design §3 "The blocked listing": one unreadable record blocks the
    whole entity's actions, but valid rows keep rendering (id, destination,
    diff) — no classification control anywhere — with one E-UNREADABLE
    notice. The unreadable file's own name is never echoed (Rule 9).

    I4: also pins two more template branches the review found deletable at
    758 green — the unreadable row's own generic markup (distinct from the
    listing-level alert above it) and the `prop-route` span that carries a
    valid row's destination alongside its diff while blocked. Both verified
    non-vacuous by mutation: stripping either leaves this test red.
    """
    main, client, valid_id = _outbox_proposal_client(tmp_path, monkeypatch)
    outbox_dir = tmp_path / "alpha/outbox"
    (outbox_dir / "unreadable-filename-marker.yaml").write_text(
        "{ not: [valid, yaml", encoding="utf-8",
    )

    response = client.get("/outbox/alpha")

    assert response.status_code == 200
    body = response.text
    assert "E-UNREADABLE" in body
    from app.console_errors import _CODES

    assert _CODES["E-UNREADABLE"].message in body
    assert valid_id in body
    assert "unreadable-filename-marker" not in body
    assert 'class="approve"' not in body
    assert 'class="reject"' not in body
    assert "/outbox/alpha/approve" not in body
    assert "/outbox/alpha/reject" not in body
    # The unreadable row's own generic markup (design §3: "the unreadable
    # row is generic and echoes no filename") — distinct from the one
    # listing-level notice already asserted above. `E-UNREADABLE`'s own
    # message text also contains "could not be read as a proposal", so the
    # row's own CSS class is what actually distinguishes its markup from the
    # notice's.
    assert 'class="proposal proposal-unreadable"' in body
    # The still-blocked valid row keeps its destination AND its diff (design
    # §3: "valid rows render read-only — id, destination, and diff").
    assert 'class="prop-route">02-work' in body
    assert 'class="block-tag">build</span>' in body
    assert "a/alpha/00-inbox/active/marker.md" in body
    assert "b/alpha/02-work/active/marker.md" in body
    assert "-sub: triage" in body


def test_outbox_screen_unblocked_listing_keeps_controls(tmp_path, monkeypatch):
    """Sanity counterpart: with no unreadable record, the same valid proposal
    keeps its approve/reject controls — proving the blocked test above is
    exercising the blocking condition, not a template that never renders
    controls at all."""
    main, client, valid_id = _outbox_proposal_client(tmp_path, monkeypatch)

    response = client.get("/outbox/alpha")

    assert response.status_code == 200
    assert valid_id in response.text
    assert 'class="approve"' in response.text
    assert 'class="reject"' in response.text


def test_approve_busy_shows_e_busy_at_200(tmp_path, monkeypatch):
    from app.console_errors import _CODES
    from app.git_transaction import VaultBusyError
    import app.outbox as outbox_module

    main, client, proposal_id = _outbox_proposal_client(tmp_path, monkeypatch)

    raw_message = "another approval is already running"

    def _raise(*args, **kwargs):
        raise VaultBusyError(raw_message)

    monkeypatch.setattr(outbox_module, "execute_transaction", _raise, raising=False)

    response = client.post("/outbox/alpha/approve", data=_action_data(tmp_path, proposal_id))

    assert response.status_code == 200
    assert 'role="alert"' in response.text
    assert "E-BUSY" in response.text
    assert _CODES["E-BUSY"].message in response.text
    # Raw exception text never reaches HTML (design §6).
    assert raw_message not in response.text


def test_approve_conflict_shows_e_conflict(tmp_path, monkeypatch):
    from app.console_errors import _CODES
    from app.git_transaction import ReviewedStateChanged
    import app.outbox as outbox_module

    main, client, proposal_id = _outbox_proposal_client(tmp_path, monkeypatch)

    raw_message = "reviewed path has an unexpected staged change"

    def _raise(*args, **kwargs):
        raise ReviewedStateChanged(raw_message)

    monkeypatch.setattr(outbox_module, "execute_transaction", _raise, raising=False)

    response = client.post("/outbox/alpha/approve", data=_action_data(tmp_path, proposal_id))

    assert response.status_code == 200
    assert 'role="alert"' in response.text
    assert "E-CONFLICT" in response.text
    assert _CODES["E-CONFLICT"].message in response.text
    assert raw_message not in response.text


def test_approve_rolled_back_shows_e_git(tmp_path, monkeypatch):
    """N3: this test cannot detect a broken cause chain on its own —
    `GitTransactionFailure` is itself `exact`-mapped to `E-GIT` (design §2's
    class map), so even a raise that dropped `from exc` entirely would still
    describe to `E-GIT` here. It pins the route's own catch and rendering,
    not the resolver's chain-walking, which `tests/test_console_errors.py`
    covers separately."""
    from app.console_errors import _CODES
    from app.git_transaction import GitTransactionFailure
    import app.outbox as outbox_module

    main, client, proposal_id = _outbox_proposal_client(tmp_path, monkeypatch)

    raw_message = "approval transaction failed and was rolled back"

    def _raise(*args, **kwargs):
        raise GitTransactionFailure(raw_message)

    monkeypatch.setattr(outbox_module, "execute_transaction", _raise, raising=False)

    response = client.post("/outbox/alpha/approve", data=_action_data(tmp_path, proposal_id))

    assert response.status_code == 200
    assert 'role="alert"' in response.text
    assert "E-GIT" in response.text
    assert _CODES["E-GIT"].message in response.text
    assert raw_message not in response.text


def test_approve_recovery_blocked_shows_e_recover(tmp_path, monkeypatch):
    """Recovery blocked is `attention` severity, unlike the three refusal
    outcomes above — the fragment status follows the code's own page status
    (500), not 200 (design §5)."""
    from app.git_transaction import GitTransactionRecoveryError
    import app.outbox as outbox_module

    main, client, proposal_id = _outbox_proposal_client(tmp_path, monkeypatch)

    # A path distinct from the proposal's own src/dst — the still-pending
    # proposal legitimately shows its own "marker.md" in the listing's diff,
    # so the disclosure check below must target a path that would only ever
    # appear if the *error's* own blocked-path argument leaked.
    blocked_path = "alpha/11-other/active/blocked-path-marker.md"

    def _raise(*args, **kwargs):
        raise GitTransactionRecoveryError((blocked_path,))

    monkeypatch.setattr(outbox_module, "execute_transaction", _raise, raising=False)

    response = client.post("/outbox/alpha/approve", data=_action_data(tmp_path, proposal_id))

    from app.console_errors import _CODES

    assert response.status_code == 500
    assert 'role="alert"' in response.text
    assert "E-RECOVER" in response.text
    assert _CODES["E-RECOVER"].message in response.text
    # Rule 9 / §6 disclosure: the blocked path is never echoed.
    assert "blocked-path-marker" not in response.text


def test_approve_committed_cleanup_shows_e_committed(tmp_path, monkeypatch):
    """The one S5 outcome that must NOT be produced by monkeypatching
    `execute_transaction` — that would produce no commit at all, and the
    design's state-proof matrix requires "exactly the reviewed paths
    committed at one new HEAD". Instead this injects a real post-commit
    cleanup `OSError` (`app/git_transaction.py`'s `_remove_temporary_index`,
    called unconditionally after a successful commit), so the transaction
    genuinely commits and then converts to `GitTransactionCommittedError`.
    """
    import app.git_transaction as git_transaction

    main, client, proposal_id = _git_outbox_proposal_client(tmp_path, monkeypatch)
    vault = Path(tmp_path)
    source = vault / "alpha/00-inbox/active/marker.md"
    destination = vault / "alpha/02-work/active/marker.md"
    proposal_path = vault / "alpha/outbox" / f"{proposal_id}.yaml"
    head_before = git_head(vault)
    commits_before = git_count_commits(vault)

    def _fail_cleanup(temporary_index):
        return OSError("injected post-commit temporary index cleanup failure")

    monkeypatch.setattr(git_transaction, "_remove_temporary_index", _fail_cleanup)

    response = client.post("/outbox/alpha/approve", data=_action_data(tmp_path, proposal_id))

    assert response.status_code == 500
    assert 'role="alert"' in response.text
    assert "E-COMMITTED" in response.text
    from app.console_errors import _CODES

    assert _CODES["E-COMMITTED"].message in response.text

    # State proof: exactly the reviewed paths committed at one new HEAD.
    assert git_count_commits(vault) == commits_before + 1
    new_head = git_head(vault)
    assert new_head != head_before
    assert git_changed_paths(vault, new_head) == sorted(
        ["alpha/00-inbox/active/marker.md", "alpha/02-work/active/marker.md"]
    )
    assert source.exists() is False
    assert destination.exists() is True
    assert proposal_path.exists() is False


def test_reject_failure_is_visible_not_silent(tmp_path, monkeypatch):
    """`outbox_reject` used to swallow every `OutboxError` silently
    (`except OutboxError: pass`). A reject of an id with no matching pending
    proposal must now render a described, visible refusal."""
    main, client, proposal_id = _outbox_proposal_client(tmp_path, monkeypatch)

    response = client.post(
        "/outbox/alpha/reject",
        data={"id": "20260101T000000-" + "00" * 16, "review_sha256": _UNBOUND_FINGERPRINT},
    )

    assert response.status_code == 200
    assert 'role="alert"' in response.text
    assert "E-INVALID" in response.text
    # The real, still-pending proposal is untouched and still listed.
    assert proposal_id in response.text


def test_outbox_fragments_reproduce_outbox_list_root(tmp_path, monkeypatch):
    """design §8: approve/reject target `#outbox-list` with `outerHTML`, so
    every fragment they return — refusal or success — must reproduce that
    root exactly once (never zero, never nested).

    N1: extended past the two already-correct shapes (green at HEAD) to the
    blocked and double-failure shapes C2 found untested — `_outbox_list_error`
    exists specifically to keep this root on the double-failure path, and
    nothing pinned that it actually does.
    """
    from app.vault import DestinationRegistryError

    main, client, proposal_id = _outbox_proposal_client(tmp_path, monkeypatch)
    # Captured before the successful reject below consumes the proposal; the
    # double-failure posts at the end reuse it and are refused anyway.
    action_data = _action_data(tmp_path, proposal_id)

    refusal = client.post(
        "/outbox/alpha/reject",
        data={"id": "hostile-nonexistent-id",
              "review_sha256": _UNBOUND_FINGERPRINT},
    )
    assert refusal.text.count('id="outbox-list"') == 1

    success = client.post("/outbox/alpha/reject", data=action_data)
    assert success.text.count('id="outbox-list"') == 1

    # Blocked shape: a genuinely unreadable record blocks the whole listing.
    (tmp_path / "alpha/outbox/unreadable-shape-marker.yaml").write_text(
        "{ not: [valid, yaml", encoding="utf-8",
    )
    blocked = client.get("/outbox/alpha")
    assert blocked.text.count('id="outbox-list"') == 1
    (tmp_path / "alpha/outbox/unreadable-shape-marker.yaml").unlink()

    # Double-failure shape (C2): the action's own re-render also fails, for
    # BOTH approve and reject — `_outbox_list_error`'s whole purpose is to
    # keep this root as the swap target rather than stranding the operator
    # with nothing left for a future approve/reject to swap into.
    def _fail_listing(*args, **kwargs):
        raise DestinationRegistryError("registries unreadable during re-render")

    monkeypatch.setattr(main, "project_outbox", _fail_listing)
    approve_double_failure = client.post(
        "/outbox/alpha/approve", data=action_data
    )
    assert approve_double_failure.text.count('id="outbox-list"') == 1
    reject_double_failure = client.post(
        "/outbox/alpha/reject", data=action_data
    )
    assert reject_double_failure.text.count('id="outbox-list"') == 1


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_outbox_double_failure_renders_both_the_action_refusal_and_the_listing_failure(
    tmp_path, monkeypatch, action
):
    """I1: the double-failure path (the action's own refusal, followed by the
    re-render itself failing) must not discard the outcome of the request the
    operator actually made. Design §5: "the status is the refusal's, because
    that is the outcome of the request being answered."

    Uses two DISTINCT exceptions on purpose — the pre-fix totality test
    patched both sides with the *same* exception, so `expected_code in text`
    could pass regardless of which one actually drove the response.
    """
    from app.console_errors import _CODES
    from app.outbox import OutboxError
    from app.vault import DestinationRegistryError

    main, client, proposal_id = _outbox_proposal_client(tmp_path, monkeypatch)

    def _refuse_action(*args, **kwargs):
        raise OutboxError("the action itself refuses this exact proposal")

    def _fail_listing(*args, **kwargs):
        raise DestinationRegistryError("registries unreadable during re-render")

    monkeypatch.setattr(main, action, _refuse_action)
    monkeypatch.setattr(main, "project_outbox", _fail_listing)

    response = client.post(f"/outbox/alpha/{action}", data=_action_data(tmp_path, proposal_id))
    body = response.text

    # The action's own refusal (E-INVALID, a refusal) drives the status —
    # not the listing failure (E-CONFIG, attention/500) that happened to be
    # the exception which actually escaped `_outbox_list`.
    assert response.status_code == 200
    assert "E-INVALID" in body
    assert _CODES["E-INVALID"].message in body
    # The listing's own failure must still be visible, not silently dropped.
    assert "E-CONFIG" in body
    assert _CODES["E-CONFIG"].message in body
    assert body.count('role="alert"') == 2
    assert body.count('id="outbox-list"') == 1


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_outbox_same_code_double_failure_renders_one_alert(
    tmp_path, monkeypatch, action
):
    """The scenario `_outbox_list_error`'s own docstring names — "the same
    vault-wide condition as the just-attempted action, e.g. a broken registry
    that refuses both" — described identically on both sides.

    The distinct-exception test above cannot see this: choosing two different
    exceptions is what makes it able to prove the status keying, and is
    exactly what blinds it to the duplicate. Design §3 allows the listing ONE
    notice, and the same sentence twice is not two pieces of information.
    """
    from app.console_errors import _CODES
    from app.vault import DestinationRegistryError

    main, client, proposal_id = _outbox_proposal_client(tmp_path, monkeypatch)

    def _one_condition_refuses_both(*args, **kwargs):
        raise DestinationRegistryError("one broken registry refuses both")

    monkeypatch.setattr(main, action, _one_condition_refuses_both)
    monkeypatch.setattr(main, "project_outbox", _one_condition_refuses_both)

    response = client.post(f"/outbox/alpha/{action}", data=_action_data(tmp_path, proposal_id))
    body = response.text

    assert body.count('role="alert"') == 1
    assert body.count(_CODES["E-CONFIG"].message) == 1
    assert body.count('id="outbox-list"') == 1


def test_outbox_blocked_action_renders_one_alert_not_two(tmp_path, monkeypatch):
    """I2: design §3 promises "one listing-level notice". In the blocked
    state, approve's own refusal — the strict loader's E-UNREADABLE (ledger
    D2) — and the projection's `blocked_notice` (also E-UNREADABLE, since any
    unreadable row's own description carries it) describe the identical
    condition. Rendering both would be two byte-identical alerts."""
    from app.console_errors import _CODES

    main, client, valid_id = _outbox_proposal_client(tmp_path, monkeypatch)
    (tmp_path / "alpha/outbox/unreadable-blocked-action-marker.yaml").write_text(
        "{ not: [valid, yaml", encoding="utf-8",
    )

    response = client.post(
        "/outbox/alpha/approve",
        data={"id": valid_id, "review_sha256": _UNBOUND_FINGERPRINT},
    )

    # E-UNREADABLE is `attention` severity, so the fragment status follows
    # its own page status (422) rather than 200 (design §5).
    assert response.status_code == 422
    body = response.text
    assert body.count('role="alert"') == 1
    assert "E-UNREADABLE" in body
    # No output assertion can say WHICH of the two survived, and this one
    # does not pretend to: `describe()` only selects from the `_CODES` table,
    # so equal codes mean the identical `ConsoleError` — the two candidate
    # renders are byte-identical. Keeping the action's refusal is therefore a
    # preference, not an observable requirement. What is asserted is the thing
    # design §3 actually promises: one notice, not two.
    assert body.count(_CODES["E-UNREADABLE"].message) == 1


def test_outbox_double_failure_does_not_claim_no_pending_proposals(
    tmp_path, monkeypatch
):
    """I3: `_outbox_list_error` cannot build the listing at all, so `rows` is
    empty for a reason unrelated to whether anything is actually pending. It
    must not tell the operator there is nothing pending while a proposal
    genuinely sits on disk — the S6 Objective forbids "a screen that hides
    the condition it is protecting against"."""
    from app.vault import DestinationRegistryError

    main, client, proposal_id = _outbox_proposal_client(tmp_path, monkeypatch)

    def _fail_listing(*args, **kwargs):
        raise DestinationRegistryError("registries unreadable during re-render")

    monkeypatch.setattr(main, "project_outbox", _fail_listing)

    response = client.post(
        "/outbox/alpha/reject", data={"id": "hostile-nonexistent-id", "review_sha256": _UNBOUND_FINGERPRINT}
    )

    assert "No pending proposals" not in response.text


def test_outbox_row_with_non_utf8_receipt_keeps_reject_loses_approve_no_diff(
    tmp_path, monkeypatch
):
    """I4: pins the per-row `{% elif error %}{% include "blocks/alert.html" %}`
    branch (design §3 phase 3: an undiffable row "renders with a described
    error in place of the diff"), and the row's capability shape — keeps
    `can_reject`, loses `can_approve`, no diff. Deleting the per-row include
    leaves the suite green at 758; verified by mutation."""
    main, client, proposal_id = _outbox_proposal_client(tmp_path, monkeypatch)
    source = tmp_path / "alpha/00-inbox/active/marker.md"
    source.write_bytes(b"---\ntitle: x\nsub: triage\n---\n\xff\xfe not valid utf-8\n")

    response = client.get("/outbox/alpha")

    assert response.status_code == 200
    body = response.text
    assert proposal_id in body
    assert "E-INVALID" in body
    assert 'class="reject"' in body
    assert 'class="approve"' not in body
    assert 'class="diff-body"' not in body


def test_outbox_row_with_missing_receipt_keeps_reject_loses_approve_no_diff(
    tmp_path, monkeypatch
):
    """I4, second condition of the same shape: a missing receipt describes to
    `E-MISSING` rather than `E-INVALID`, and the row still keeps `can_reject`
    while losing `can_approve` and its diff."""
    main, client, proposal_id = _outbox_proposal_client(tmp_path, monkeypatch)
    (tmp_path / "alpha/00-inbox/active/marker.md").unlink()

    response = client.get("/outbox/alpha")

    assert response.status_code == 200
    body = response.text
    assert proposal_id in body
    assert "E-MISSING" in body
    assert 'class="reject"' in body
    assert 'class="approve"' not in body
    assert 'class="diff-body"' not in body


def test_outbox_hx_vals_are_tojson(tmp_path, monkeypatch):
    """design Rule 8 / §7 invariant 5: no template hand-builds an `hx-vals`
    mapping. `templates/blocks/outbox_list.html` previously had two —
    `hx-vals='{"id": "{{ p.id }}"}'` for both approve and reject."""
    from tests.test_app import HxValsParser

    main, client, proposal_id = _outbox_proposal_client(tmp_path, monkeypatch)

    response = client.get("/outbox/alpha")

    fingerprint = _outbox_fingerprint(tmp_path, proposal_id)
    parser = HxValsParser()
    parser.feed(response.text)
    assert len(parser.values) == 2  # one approve button, one reject button
    for raw in parser.values:
        # S7 added the fingerprint to the same serialised mapping; it is
        # still `tojson`, still server-rendered, still never hand-built.
        assert json.loads(raw) == {
            "id": proposal_id,
            "review_sha256": fingerprint,
        }

    # S7 Task 5 moved the card — and with it the `hx-vals` — into
    # `blocks/outbox_card.html`, which the list includes. Follow it there:
    # scanning the list alone would now scan zero attributes and pass having
    # proved nothing.
    source = (
        Path(__file__).resolve().parents[1]
        / "templates/blocks/outbox_card.html"
    ).read_text(encoding="utf-8")
    hx_vals_attrs = re.findall(r"hx-vals='([^']*)'", source)
    assert hx_vals_attrs, "expected at least one hx-vals attribute"
    for value in hx_vals_attrs:
        assert re.fullmatch(r"\{\{\s*\S+\s*\|\s*tojson\s*\}\}", value.strip()), value


def test_outbox_declared_family_never_reaches_the_global_fallback(
    tmp_path, monkeypatch
):
    """design §5: "the global handler catches only what escapes a route",
    and "relying on it is a failure rather than a silent default" — pinned
    here for all three outbox routes, the Task 11 pattern
    (`test_triage_declared_family_never_reaches_the_global_fallback`).

    I5: two separate passes, never the same exception object patched onto
    both sides. The single-pass version left `project_outbox` patched for
    the whole iteration, so approve/reject always exited through the OUTER
    guard (`_outbox_list_error`) and the inline `except` around `approve`/
    `reject` itself was never actually carried to completion; and patching
    both sides with one shared exception let `expected_code in text` pass
    regardless of which path answered, since both would describe the
    identical object to the identical code.
    """
    from app.scope import RedirectedPathError
    from app.vault import DestinationRegistryError
    from app.outbox import OutboxError

    main, client, proposal_id = _outbox_proposal_client(tmp_path, monkeypatch)
    # Captured once, before any patching: every pass below refuses rather
    # than consuming the proposal, and recomputing mid-test would read
    # through a deliberately broken reader.
    action_data = _action_data(tmp_path, proposal_id)
    real_approve = main.approve
    real_reject = main.reject
    reached = []
    original = main.app.exception_handlers[Exception]

    async def _spy(request, exc):
        reached.append(type(exc).__name__)
        return await original(request, exc)

    main.app.exception_handlers[Exception] = _spy

    # Pass 1 — the action alone. `project_outbox` is left untouched, so a
    # successful re-render (the still-pending proposal surviving in the
    # response) proves the inline `except` around `approve`/`reject` ran to
    # completion, rather than being masked by an identical listing failure.
    for raiser, expected_code in (
        (RedirectedPathError("redirected proposal leaf"), "E-TAMPER"),
        (DestinationRegistryError("registries unreadable"), "E-CONFIG"),
        (OutboxError("outbox is otherwise broken"), "E-INVALID"),
    ):
        def _raise(*args, __exc=raiser, **kwargs):
            raise __exc

        monkeypatch.setattr(main, "approve", _raise)
        approve_response = client.post(
            "/outbox/alpha/approve", data=action_data
        )
        assert expected_code in approve_response.text, approve_response.text
        assert proposal_id in approve_response.text, (
            "the still-pending proposal must survive the listing re-render"
        )
        assert reached == [], f"outbox_approve: fallback reached for {expected_code}"

        monkeypatch.setattr(main, "reject", _raise)
        reject_response = client.post(
            "/outbox/alpha/reject", data=action_data
        )
        assert expected_code in reject_response.text, reject_response.text
        assert proposal_id in reject_response.text, (
            "the still-pending proposal must survive the listing re-render"
        )
        assert reached == [], f"outbox_reject: fallback reached for {expected_code}"

    monkeypatch.setattr(main, "approve", real_approve)
    monkeypatch.setattr(main, "reject", real_reject)

    # Pass 2 — the projection alone: `approve`/`reject` are restored to the
    # real functions, so any code observed here is driven entirely by
    # `project_outbox` failing, for all three routes that call it.
    for raiser, expected_code in (
        (RedirectedPathError("redirected outbox"), "E-TAMPER"),
        (DestinationRegistryError("registries unreadable"), "E-CONFIG"),
        (OutboxError("outbox is otherwise broken"), "E-INVALID"),
    ):
        def _raise(*args, __exc=raiser, **kwargs):
            raise __exc

        monkeypatch.setattr(main, "project_outbox", _raise)
        screen_response = client.get("/outbox/alpha")
        assert expected_code in screen_response.text, screen_response.text
        assert reached == [], f"outbox_screen: fallback reached for {expected_code}"

        approve_response = client.post(
            "/outbox/alpha/approve", data=action_data
        )
        assert expected_code in approve_response.text, approve_response.text
        assert reached == [], f"outbox_approve: fallback reached for {expected_code}"

        reject_response = client.post(
            "/outbox/alpha/reject", data=action_data
        )
        assert expected_code in reject_response.text, reject_response.text
        assert reached == [], f"outbox_reject: fallback reached for {expected_code}"

    # Sanity: the spy fires for something genuinely undeclared, so the empty
    # lists above are proof of routing rather than of a spy that never runs.
    def _boom(*args, **kwargs):
        raise RuntimeError("undeclared by any outbox route")

    monkeypatch.setattr(main, "project_outbox", _boom)
    TestClient(main.app, raise_server_exceptions=False).get("/outbox/alpha")
    assert reached == ["RuntimeError"]


# --- Task 13: Routes — registry -----------------------------------------------


def _outbox_snapshot(vault: Path) -> set[Path]:
    """Every outbox proposal file anywhere in `vault`, across every entity —
    used by the persistence-outcome tests to prove exactly one new proposal
    appeared. `git status` cannot see these: `write_vault` itself writes no
    `.gitignore`, but every fixture that reaches this helper writes its own
    (four call sites, each `*/outbox/*.yaml`), which is enough to make a
    newly written proposal invisible to git's own porcelain status (not
    merely untracked but reported — genuinely absent from it). Nothing in
    this repo establishes that the pattern matches the real vault's own
    ignore rules, so this docstring does not claim it does. The state proof
    therefore splits in two: the filesystem glob here proves the written
    proposal, and `git_status_bytes` staying byte-identical proves git sees
    nothing else change anywhere.

    I3 (review): scans the WHOLE vault, not one entity's own `outbox/`.
    `.gitignore`'s `*/outbox/*.yaml` already hides every entity's outbox
    from porcelain status alike, so a stray write to a DIFFERENT entity's
    outbox — an escape design invariants S2/S3 forbid — was invisible to
    both halves of the state proof at once when this glob was scoped to the
    bound entity alone. `_outbox_new_path_in_entity` below is what asserts
    *where* the one new path landed; this function only asserts *that*
    something changed, so it must not narrow the search before comparison.
    """
    return set(vault.rglob("*/outbox/*.yaml"))


def _outbox_new_path_in_entity(
    before: set[Path], after: set[Path], vault: Path, entity: str
) -> Path:
    """Asserts exactly one new proposal file appeared between two
    `_outbox_snapshot` calls, AND that it landed inside `entity`'s own
    `outbox/` — the second half of the I3 fix. Scanning the whole vault
    without this check would still only prove "one file changed somewhere",
    which is what let a stray write to another entity's outbox pass
    unnoticed before."""
    new_paths = after - before
    assert len(new_paths) == 1, f"expected exactly one new proposal, got {new_paths}"
    (new_path,) = new_paths
    # Compare resolved PATHS, not directory names. A name-only check accepts
    # `<vault>/beta/alpha/outbox/x.yaml` — a write that escaped the bound
    # entity while still having an ancestor *named* `alpha`. That case is
    # currently caught by `git_status_bytes` only because `.gitignore`'s
    # `*/outbox/*.yaml` is single-level, which is luck, not a guarantee.
    expected_parent = (vault / entity / "outbox").resolve()
    assert new_path.resolve().parent == expected_parent, (
        f"new proposal escaped the bound entity {entity!r}: {new_path}"
    )
    return new_path


def test_outbox_new_path_helper_rejects_an_escape_with_a_matching_name(tmp_path):
    """The helper compares resolved PATHS, not directory names, and this pins
    that difference — reverting it to the name comparison left every other
    test in this file green.

    `<vault>/beta/alpha/outbox/x.yaml` escapes the bound entity while still
    having an ancestor *named* `alpha`. The persistence tests happen to catch
    it through `git_status_bytes`, but only because `.gitignore`'s
    `*/outbox/*.yaml` is single-level — luck, not a guarantee, and the helper's
    own contract has to hold on its own.
    """
    inside = tmp_path / "alpha/outbox/ok.yaml"
    escaped = tmp_path / "beta/alpha/outbox/x.yaml"
    for path in (inside, escaped):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")

    assert _outbox_new_path_in_entity(set(), {inside}, tmp_path, "alpha") == inside
    with pytest.raises(AssertionError, match="escaped the bound entity"):
        _outbox_new_path_in_entity(set(), {escaped}, tmp_path, "alpha")


def _registry_client(tmp_path, monkeypatch, *, slug="widget"):
    """A vault with one registered, unreferenced product and a real Git
    repository — a real repo is required to drive `registry_delete_execute`
    through a genuine commit for the E-COMMITTED outcome. Design §8's state
    proof requires the `committed = yes` injection to be a real post-commit
    cleanup `OSError` so `app/git_transaction.py:476` fires after a genuine
    commit — a monkeypatched `execute_transaction` that raises instead
    produces no commit at all and cannot satisfy it (M4, review: the
    previous wording of this docstring quoted a sentence that appears
    nowhere in the design)."""
    main = _load_main(tmp_path, monkeypatch, ENTITIES)
    (tmp_path / "_system/products.yaml").write_text(
        "version: \"1.0\"\n"
        "products:\n"
        "  alpha:\n"
        f"    {slug}: {{label: Widget}}\n",
        encoding="utf-8",
    )
    (tmp_path / ".gitignore").write_text("*/outbox/*.yaml\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"],
        cwd=tmp_path, check=True,
    )
    subprocess.run(["git", "config", "user.name", "test"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "fixture"], cwd=tmp_path, check=True)
    return main, TestClient(main.app), slug


def _git_propose_client(tmp_path, monkeypatch):
    """`_propose_client` plus a real Git repository, needed for a full
    HEAD/index/tracked-content state proof rather than just a written-file
    count."""
    main, client = _propose_client(tmp_path, monkeypatch)
    (tmp_path / ".gitignore").write_text("*/outbox/*.yaml\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"],
        cwd=tmp_path, check=True,
    )
    subprocess.run(["git", "config", "user.name", "test"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "fixture"], cwd=tmp_path, check=True)
    return main, client


def test_delete_preview_hx_vals_survive_hostile_slug(tmp_path, monkeypatch):
    """design §6 Rule 8, "request rebinding": `templates/blocks/delete_impact.html`
    used to hand-build `hx-vals='{"id": "{{ prop.id }}", "slug": "{{ slug }}"}'`.
    A crafted slug can close the JSON string and inject a second `id` key,
    which — after the browser decodes the HTML entities Jinja's autoescaping
    produces — rebinds the approve request to a proposal other than the one
    just previewed: a preview/approve mismatch, not merely a display bug.

    The fix drops `slug` from the execute request entirely (item D: the
    server derives kind and slug from the validated proposal, so the
    submitted value is never needed again) and builds the surviving
    `{"id": ...}` mapping through `| tojson`. Regardless of how hostile the
    submitted slug is, the parsed `hx-vals` must carry exactly one `id`,
    equal to the proposal this exact preview request just wrote.
    """
    from tests.test_app import HxValsParser

    main, client, slug = _registry_client(tmp_path, monkeypatch)
    outbox_dir = tmp_path / "alpha/outbox"
    before = set(outbox_dir.glob("*.yaml")) if outbox_dir.exists() else set()

    hostile_slug = 'shown", "id": "hostile-injected-id-marker'

    response = client.post(
        "/registry/alpha/product/delete-preview", data={"slug": hostile_slug}
    )

    assert response.status_code == 200
    after = set(outbox_dir.glob("*.yaml"))
    written = after - before
    assert len(written) == 1, "expected exactly one new delete proposal"
    (proposal_path,) = written
    proposal_id = proposal_path.stem

    parser = HxValsParser()
    parser.feed(response.text)
    assert len(parser.values) == 1
    parsed = json.loads(parser.values[0])
    # S7 added the fingerprint to the same serialised mapping; it is still
    # `tojson`, still server-rendered, and the hostile slug still never
    # appears in it.
    assert parsed == {
        "id": proposal_id,
        "review_sha256": hashlib.sha256(
            (tmp_path / "alpha/outbox" / f"{proposal_id}.yaml").read_bytes()
        ).hexdigest(),
    }
    # S7 (blocker: displayed slug outside the snapshot) now renders the
    # *fingerprinted* slug, which for this request is the hostile string the
    # proposal actually stores. That is the correct thing to show — the
    # operator must see what they are approving — so the guarantee is no
    # longer "the marker never appears" but the two that actually matter:
    #
    # 1. it can never reach an `hx-vals` mapping, and
    for raw in parser.values:
        assert "hostile-injected-id-marker" not in raw
    # 2. it is never rendered unescaped, so it cannot close the attribute or
    #    inject a second key once the browser decodes the page.
    assert 'shown", "id": "hostile-injected-id-marker' not in response.text
    assert "hostile-injected-id-marker" in response.text, (
        "the stored slug must be shown to the operator, escaped"
    )

    # M1 (review): `prop.id` is server-generated
    # (`^[0-9]{8}T[0-9]{6}-[0-9a-f]{32}$`, `app/proposal_identity.py`) and can
    # never itself carry a quote or brace, so every assertion above is true
    # both for a `| tojson` render and for the hand-built
    # `hx-vals='{"id": "{{ prop.id }}"}'` this replaced — reverting the
    # template to the hand-built form stays green against a benign id.
    # Pin the template's actual source, not just its behavior on one, so the
    # `tojson` half is pinned by this route test and not only by the
    # `templates/` scan (`test_no_template_hand_builds_hx_vals`).
    template_source = (
        Path(__file__).resolve().parents[1]
        / "templates" / "blocks" / "delete_impact.html"
    ).read_text(encoding="utf-8")
    # An exact source match, deliberately: `prop.id` is server-generated and
    # cannot itself carry a hostile character, so no rendered-output assertion
    # can distinguish `| tojson` from a hand-built mapping here. The cost is
    # that reformatting this attribute reds a route test for a non-behavioural
    # reason; the Task 13a scan is the general guard, this is the local pin.
    assert "hx-vals='{{ delete_execute_values | tojson }}'" in template_source


def test_delete_execute_success_copy_from_validated_slug(tmp_path, monkeypatch):
    """design §6 Rule 8, "reflected copy": `registry_delete_execute` used to
    build its success message from the *submitted* `slug` form field, never
    compared against the proposal's own slug. The fix calls
    `get_delete_proposal` before `execute_delete` (`execute_delete` itself
    removes the proposal file and cannot be re-read afterwards) and holds
    the validated slug from the returned `DeleteProposal` — so the submitted
    value is unused for display, and the route no longer even declares a
    `slug` form parameter.
    """
    import inspect

    import app.registry as registry
    from app.scope import Scope

    main, client, slug = _registry_client(tmp_path, monkeypatch)
    scope = Scope(Path(tmp_path), "alpha")
    proposal = registry.propose_delete(scope, "product", slug)

    # The route no longer reads a `slug` field at all.
    sig = inspect.signature(main.registry_delete_execute)
    assert "slug" not in sig.parameters

    response = client.post(
        "/registry/alpha/product/delete-execute",
        data={**_delete_action_data(tmp_path, proposal),
              "slug": "hostile-spoofed-slug-marker"},
    )

    assert response.status_code == 200
    assert f"'{slug}'" in response.text
    assert "hostile-spoofed-slug-marker" not in response.text


def test_delete_execute_error_is_templated_and_escaped(tmp_path, monkeypatch):
    """design §6: both branches of `registry_delete_execute` are templates,
    with no `| safe` — raw exception text never reaches HTML. Previously the
    error branch built `str(e).replace("\\n", "<br>")` into an unescaped
    `HTMLResponse` f-string; a message containing HTML-significant characters
    would have rendered as markup rather than text."""
    import app.registry as registry
    from app.console_errors import _CODES
    from app.git_transaction import GitTransactionFailure
    from app.scope import Scope

    main, client, slug = _registry_client(tmp_path, monkeypatch)
    scope = Scope(Path(tmp_path), "alpha")
    proposal = registry.propose_delete(scope, "product", slug)

    raw_message = "<script>injected registry deletion transaction failure</script>"

    def _raise(*args, **kwargs):
        raise GitTransactionFailure(raw_message)

    monkeypatch.setattr(registry, "execute_transaction", _raise, raising=False)

    response = client.post(
        "/registry/alpha/product/delete-execute",
        data=_delete_action_data(tmp_path, proposal),
    )

    assert response.status_code == 200
    assert 'role="alert"' in response.text
    assert "E-GIT" in response.text
    assert _CODES["E-GIT"].message in response.text
    assert raw_message not in response.text
    assert "<script>" not in response.text


def test_registry_products_broken_yaml_shows_e_config(tmp_path, monkeypatch):
    """`registry_products` had no `try`/`except` at all — a wrongly-shaped
    or unparseable `products.yaml` (converted to `DestinationRegistryError`
    by `products_for`'s own `@structured_reader(category="registry")`
    contract) escaped straight to the global fallback. `surface="page"`, so
    a non-HTMX request renders the full `error.html` page at the code's own
    page status."""
    from app.console_errors import _CODES

    main, client, slug = _registry_client(tmp_path, monkeypatch)
    (tmp_path / "_system/products.yaml").write_text(
        "{ not: [valid, yaml", encoding="utf-8",
    )

    response = client.get("/registry/alpha/products")

    assert response.status_code == _CODES["E-CONFIG"].page_status
    assert 'role="alert"' in response.text
    assert "E-CONFIG" in response.text
    assert _CODES["E-CONFIG"].message in response.text


def test_all_five_s5_outcomes_via_real_execute_delete(tmp_path, monkeypatch):
    """design §8: "All five S5 outcomes resolve correctly through both real
    service paths — the failure is injected into the transaction layer and
    propagates through the actual approve and execute_delete wrappers." Task
    11/12 covered `approve`'s route; this is `execute_delete`'s own route.

    `execute_delete` itself is untouched by S6 (hard constraint) — it already
    catches only the base `GitTransactionError` and wraps every S5 outcome
    into `RegistryTransactionError(...) from exc`. `describe()`'s allowlisted
    cause-chain walk (Rule 1) is what recovers the specific outcome from that
    one wrapper, exactly as it already does for `approve`'s `OutboxTransactionError`.

    Four of the five never let a transaction actually run
    (`execute_transaction` is monkeypatched to raise immediately), so one
    proposal is reused across them — proven untouched between attempts. The
    fifth, E-COMMITTED, must be a genuine post-commit cleanup failure, not a
    monkeypatched `execute_transaction` (a monkeypatched one produces no
    commit at all): it gets its own proposal, the real `execute_transaction`,
    and a patched post-commit cleanup step.
    """
    import app.git_transaction as git_transaction
    import app.registry as registry
    from app.console_errors import _CODES
    from app.git_transaction import (
        GitTransactionFailure,
        GitTransactionRecoveryError,
        ReviewedStateChanged,
        VaultBusyError,
    )
    from app.scope import Scope

    main, client, slug = _registry_client(tmp_path, monkeypatch)
    scope = Scope(Path(tmp_path), "alpha")
    registry_path = scope.system_path("products.yaml")
    real_execute_transaction = registry.execute_transaction

    proposal = registry.propose_delete(scope, "product", slug)
    registry_bytes = registry_path.read_bytes()
    proposal_bytes = proposal.path.read_bytes()
    head_before = git_head(tmp_path)

    for raiser, expected_code, expected_status in (
        (VaultBusyError("injected busy"), "E-BUSY", 200),
        (ReviewedStateChanged("injected conflict"), "E-CONFLICT", 200),
        (GitTransactionFailure("injected rollback"), "E-GIT", 200),
        (
            GitTransactionRecoveryError(("blocked-path-marker",)),
            "E-RECOVER",
            500,
        ),
    ):
        def _raise(*args, __exc=raiser, **kwargs):
            raise __exc

        monkeypatch.setattr(registry, "execute_transaction", _raise, raising=False)

        response = client.post(
            "/registry/alpha/product/delete-execute",
            data=_delete_action_data(tmp_path, proposal),
        )

        assert response.status_code == expected_status, response.text
        assert 'role="alert"' in response.text
        assert expected_code in response.text, response.text
        assert _CODES[expected_code].message in response.text
        assert str(raiser) not in response.text
        assert "blocked-path-marker" not in response.text

        # Untouched: execute_transaction never actually ran.
        assert registry_path.read_bytes() == registry_bytes
        assert proposal.path.read_bytes() == proposal_bytes
        assert git_head(tmp_path) == head_before

    monkeypatch.setattr(
        registry, "execute_transaction", real_execute_transaction, raising=False
    )

    committed_proposal = registry.propose_delete(scope, "product", slug)
    commits_before = git_count_commits(tmp_path)

    def _fail_cleanup(temporary_index):
        return OSError("injected post-commit temporary index cleanup failure")

    monkeypatch.setattr(git_transaction, "_remove_temporary_index", _fail_cleanup)

    response = client.post(
        "/registry/alpha/product/delete-execute",
        data=_delete_action_data(tmp_path, committed_proposal),
    )

    assert response.status_code == 500
    assert 'role="alert"' in response.text
    assert "E-COMMITTED" in response.text
    assert _CODES["E-COMMITTED"].message in response.text

    # State proof: a genuine commit happened, the slug is really gone from
    # the registry, and the proposal is really gone from the outbox.
    assert git_count_commits(tmp_path) == commits_before + 1
    assert git_head(tmp_path) != head_before
    assert slug.encode("utf-8") not in registry_path.read_bytes()
    assert committed_proposal.path.exists() is False


def _state_proof_proposal_written_registry_delete_preview(tmp_path, monkeypatch):
    """design §8 state proof: `(committed=no, persistence=proposal-written)`.
    `registry_delete_preview` calls `propose_delete`, which writes the
    proposal file, and only then calls `reference_count` while building the
    response. A described failure in that second phase must not roll back
    the write: HEAD, the index, and all tracked content stay identical, and
    exactly one new untracked proposal file appears under the bound entity's
    `outbox/` — and nothing else changes.

    Folded into `test_state_proof_matrix`'s `no-proposal-written` cell (was
    the standalone `test_delete_preview_persistence_outcome`) — the matrix is
    the deliverable design §8 asks for, not a second copy of the same proof.
    """
    main, client, slug = _registry_client(tmp_path, monkeypatch)
    vault = tmp_path
    head_before = git_head(vault)
    index_before = git_index_entries(vault)
    cached_before = git_cached_diff(vault)
    worktree_before = git_worktree_diff(vault)
    status_before = git_status_apart_from_quarantine(vault)
    outbox_before = _outbox_snapshot(vault)

    def _fail_after_persisting(*args, **kwargs):
        raise main.DestinationRegistryError(
            "registries unreadable after proposal write"
        )

    # S7: the route's post-persist step is now the review read, not a live
    # reference count — the failure must be injected where it still reads, or
    # this cell proves nothing about "persisted, then failed".
    monkeypatch.setattr(main, "get_delete_review", _fail_after_persisting)

    response = client.post(
        "/registry/alpha/product/delete-preview", data={"slug": slug}
    )

    from app.console_errors import _CODES

    # E-CONFIG is `attention` severity, so Rule 5 gives it its own page
    # status (500) rather than 200, even on this fragment-only route.
    assert response.status_code == _CODES["E-CONFIG"].page_status
    assert 'role="alert"' in response.text
    assert "E-CONFIG" in response.text

    assert git_head(vault) == head_before
    assert git_index_entries(vault) == index_before
    assert git_cached_diff(vault) == cached_before
    assert git_worktree_diff(vault) == worktree_before
    # git itself sees nothing new anywhere — the proposal file is gitignored,
    # exactly as this fixture's own `.gitignore` says.
    assert git_status_apart_from_quarantine(vault) == status_before

    outbox_after = _outbox_snapshot(vault)
    assert outbox_before - outbox_after == set(), "nothing was removed"
    _outbox_new_path_in_entity(outbox_before, outbox_after, tmp_path, "alpha")


def _state_proof_proposal_written_propose(tmp_path, monkeypatch):
    """design §8 state proof: `(committed=no, persistence=proposal-written)`
    for `propose`, the second of the two routes design §8 permits to declare
    it. `propose_classification` writes the proposal file before
    `preview_diff` is even called while building the template context — a
    described failure there must not roll back the write: HEAD, the index,
    and all tracked content stay identical, and exactly one new untracked
    proposal file appears under the bound entity's `outbox/`, and nothing
    else changes.

    (`tests/test_console_routes.py::test_propose_post_persistence_failure_still_signals_and_keeps_the_proposal`,
    Task 11's own test, already pins the signal and the write count; this
    adds the full HEAD/index/tracked-content state proof design §8 requires
    for this outcome, over a real Git repository.)

    Folded into `test_state_proof_matrix`'s `no-proposal-written` cell (was
    the standalone `test_propose_persistence_outcome`) — the matrix is the
    deliverable design §8 asks for, not a second copy of the same proof.
    """
    main, client = _git_propose_client(tmp_path, monkeypatch)
    vault = tmp_path
    head_before = git_head(vault)
    index_before = git_index_entries(vault)
    cached_before = git_cached_diff(vault)
    worktree_before = git_worktree_diff(vault)
    status_before = git_status_apart_from_quarantine(vault)
    outbox_before = _outbox_snapshot(vault)

    def _fail_after_persisting(*args, **kwargs):
        raise main.OutboxError("diff rendering refused after persistence")

    monkeypatch.setattr(main, "preview_diff", _fail_after_persisting)

    response = client.post(
        "/triage/alpha/propose",
        data={"filename": "note.md", "module": "02-work", "sub": ""},
    )

    assert response.status_code == 200
    assert 'role="alert"' in response.text
    assert response.headers.get("HX-Trigger") == main._PROPOSAL_PERSISTED_EVENT

    assert git_head(vault) == head_before
    assert git_index_entries(vault) == index_before
    assert git_cached_diff(vault) == cached_before
    assert git_worktree_diff(vault) == worktree_before
    assert git_status_apart_from_quarantine(vault) == status_before

    outbox_after = _outbox_snapshot(vault)
    assert outbox_before - outbox_after == set(), "nothing was removed"
    _outbox_new_path_in_entity(outbox_before, outbox_after, tmp_path, "alpha")


def test_registry_declared_family_never_reaches_the_global_fallback(
    tmp_path, monkeypatch
):
    """The Task 11/12 trap, present here too before this task: all three
    registry routes declared their catch families, but `registry_products`
    and `registry_delete_preview` had **no** `try`/`except` at all, so
    literally everything escaped them — including their own declared
    `RegistryError` — and `registry_delete_execute` caught only
    `RegistryError`, so everything else escaped it too. Starlette's
    `ServerErrorMiddleware` re-raises after the fallback answers, so an
    escapee also logs a raw traceback for a first-class described condition
    — exactly what design §5 forbids (the Task 11 `triage` pattern,
    `test_triage_declared_family_never_reaches_the_global_fallback`).

    C1/I1 (review): the first fix round gave the routes real `try`/`except`
    blocks, but the declared tuples repeated the hole one level down —
    `_REGISTRY_PRODUCTS_CATCHES` omitted `CrossScopeError` and
    `_REGISTRY_DELETE_CATCHES` omitted `UnreadableProposalRecord`, so this
    injection-only totality test stayed green while both were still able to
    reach the fallback for real. It is green here again only because it
    injects the members the (now-corrected) family actually declares; the
    real-filesystem proof for each is
    `test_registry_products_real_symlinked_registry_shows_e_tamper` and
    `test_registry_delete_execute_real_corrupt_proposal_shows_e_unreadable`
    below, which drive the condition without injecting anything.
    """
    import app.registry as registry
    from app.outbox import UnreadableProposalRecord
    from app.registry import RegistryError
    from app.scope import RedirectedPathError, Scope
    from app.vault import DestinationRegistryError

    main, client, slug = _registry_client(tmp_path, monkeypatch)
    proposal = registry.propose_delete(Scope(Path(tmp_path), "alpha"), "product", slug)

    reached = []
    original = main.app.exception_handlers[Exception]

    async def _spy(request, exc):
        reached.append(type(exc).__name__)
        return await original(request, exc)

    # M8 (review): restore the real handler afterward rather than leaving
    # the spy installed for the rest of the suite.
    monkeypatch.setitem(main.app.exception_handlers, Exception, _spy)

    # registry_products: declared (RegistryError, CrossScopeError,
    # DestinationRegistryError). `RedirectedPathError` is the CrossScopeError
    # C1 added.
    for raiser, expected_code in (
        (RedirectedPathError("redirected products registry"), "E-TAMPER"),
        (DestinationRegistryError("registries unreadable"), "E-CONFIG"),
        (RegistryError("registry is otherwise broken"), "E-REGISTRY"),
    ):
        def _raise(*args, __exc=raiser, **kwargs):
            raise __exc

        monkeypatch.setattr(main, "products_for", _raise)
        response = client.get("/registry/alpha/products")
        assert expected_code in response.text, response.text
        assert reached == [], f"registry_products: fallback reached for {expected_code}"

    # registry_delete_preview: declared (RegistryError, CrossScopeError,
    # DestinationRegistryError, UnreadableProposalRecord).
    # `UnreadableProposalRecord` is the I1 addition; unreachable from
    # `propose_delete` itself in practice (it only writes), but declared
    # here because `registry_delete_execute` below shares the same tuple and
    # this loop proves the tuple's *declaration*, not each route's own
    # reachability — the same shape as `registry_products` including
    # `CrossScopeError` above it.
    for raiser, expected_code in (
        (RedirectedPathError("redirected outbox"), "E-TAMPER"),
        (DestinationRegistryError("registries unreadable"), "E-CONFIG"),
        (RegistryError("registry is otherwise broken"), "E-REGISTRY"),
        (UnreadableProposalRecord("corrupt delete proposal"), "E-UNREADABLE"),
    ):
        def _raise(*args, __exc=raiser, **kwargs):
            raise __exc

        monkeypatch.setattr(main, "propose_delete", _raise)
        response = client.post(
            "/registry/alpha/product/delete-preview", data={"slug": slug}
        )
        assert expected_code in response.text, response.text
        assert reached == [], (
            f"registry_delete_preview: fallback reached for {expected_code}"
        )
    monkeypatch.setattr(main, "propose_delete", registry.propose_delete)

    # registry_delete_execute: same declared family. Pass 1 covers the
    # route's first call (get_delete_proposal); pass 2 covers its second
    # (execute_delete), so an outer guard wrapping only one of the two could
    # not pass both. `UnreadableProposalRecord` (I1) is the reachable case
    # for both — `get_delete_proposal` and `execute_delete` are each
    # `@structured_reader(category="proposal")`, so a real corrupt record
    # drives this through either call, not only an injected one (see
    # `test_registry_delete_execute_real_corrupt_proposal_shows_e_unreadable`
    # below).
    for raiser, expected_code in (
        (RedirectedPathError("redirected outbox"), "E-TAMPER"),
        (DestinationRegistryError("registries unreadable"), "E-CONFIG"),
        (RegistryError("registry is otherwise broken"), "E-REGISTRY"),
        (UnreadableProposalRecord("corrupt delete proposal"), "E-UNREADABLE"),
    ):
        def _raise(*args, __exc=raiser, **kwargs):
            raise __exc

        # S7: the route no longer reads through a value-only reader —
        # `execute_delete` is its single domain call, and returns the
        # bound proposal used for the success copy.
        monkeypatch.setattr(main, "execute_delete", _raise)
        response = client.post(
            "/registry/alpha/product/delete-execute",
        data=_delete_action_data(tmp_path, proposal),
        )
        assert expected_code in response.text, response.text
        assert reached == [], (
            f"registry_delete_execute (execute_delete): fallback reached "
            f"for {expected_code}"
        )
    monkeypatch.setattr(main, "execute_delete", registry.execute_delete)

    for raiser, expected_code in (
        (RedirectedPathError("redirected outbox"), "E-TAMPER"),
        (DestinationRegistryError("registries unreadable"), "E-CONFIG"),
        (RegistryError("registry is otherwise broken"), "E-REGISTRY"),
        (UnreadableProposalRecord("corrupt delete proposal"), "E-UNREADABLE"),
    ):
        def _raise(*args, __exc=raiser, **kwargs):
            raise __exc

        monkeypatch.setattr(main, "execute_delete", _raise)
        response = client.post(
            "/registry/alpha/product/delete-execute",
        data=_delete_action_data(tmp_path, proposal),
        )
        assert expected_code in response.text, response.text
        assert reached == [], (
            f"registry_delete_execute (execute_delete): fallback reached "
            f"for {expected_code}"
        )
    monkeypatch.setattr(main, "execute_delete", registry.execute_delete)

    # Sanity: the spy fires for something genuinely undeclared, so the empty
    # lists above are proof of routing rather than of a spy that never runs.
    def _boom(*args, **kwargs):
        raise RuntimeError("undeclared by any registry route")

    monkeypatch.setattr(main, "products_for", _boom)
    TestClient(main.app, raise_server_exceptions=False).get("/registry/alpha/products")
    assert reached == ["RuntimeError"]


def test_registry_products_real_symlinked_registry_shows_e_tamper(tmp_path, monkeypatch):
    """C1 (review, third time on this branch): `_REGISTRY_PRODUCTS_CATCHES`
    omitted `CrossScopeError` while `_REGISTRY_DELETE_CATCHES` three lines
    below already included it. `products_for` -> `scope.system_path(...)`
    -> `RedirectedPathError` (a `CrossScopeError`) for a redirected
    `_system/products.yaml`.

    Driven here against a REAL symlink on disk, no monkeypatching, on
    purpose: the injection-only totality test above
    (`test_registry_declared_family_never_reaches_the_global_fallback`) can
    only pass or fail against members the declared family already lists, so
    it stays green even when the family itself is missing one. A default
    `TestClient` (no `raise_server_exceptions=False`) is used deliberately:
    if the fallback were reached, Starlette's `ServerErrorMiddleware`
    re-raises after answering, so this call would raise inside the test
    rather than return a response — the raw server fault the S6 Objective
    forbids.
    """
    from app.console_errors import _CODES

    main, client, slug = _registry_client(tmp_path, monkeypatch)

    products_path = tmp_path / "_system" / "products.yaml"
    outside = tmp_path.parent / f"{tmp_path.name}-outside-products-marker.yaml"
    outside.write_text('version: "1.0"\nproducts: {}\n', encoding="utf-8")
    products_path.unlink()
    products_path.symlink_to(outside)

    reached = []
    original = main.app.exception_handlers[Exception]

    async def _spy(request, exc):
        reached.append(type(exc).__name__)
        return await original(request, exc)

    monkeypatch.setitem(main.app.exception_handlers, Exception, _spy)

    response = TestClient(main.app).get("/registry/alpha/products")

    assert reached == [], f"fallback reached: {reached}"
    assert response.status_code == _CODES["E-TAMPER"].page_status
    assert 'role="alert"' in response.text
    assert "E-TAMPER" in response.text
    assert _CODES["E-TAMPER"].message in response.text
    # Rule 9 / §6 disclosure: no path is echoed either.
    assert "products.yaml" not in response.text
    assert "outside-products-marker" not in response.text


def test_registry_delete_execute_real_corrupt_proposal_shows_e_unreadable(
    tmp_path, monkeypatch
):
    """I1 (review): a corrupted delete-proposal record — valid YAML, wrong
    shape — reaches `get_delete_proposal`, which `registry_delete_execute`
    calls before `execute_delete`. Both are
    `@structured_reader(category="proposal")`, and design §7 invariant 4
    requires exactly that category to raise `UnreadableProposalRecord` for
    this shape: it is the DESIGNED failure mode of the functions this route
    calls, not a family widening that needs escalation.
    `_REGISTRY_DELETE_CATCHES` omitted it, so this real corrupt record on
    disk — no injection — used to reach the global fallback at 500 instead
    of describing E-UNREADABLE at 422.
    """
    import app.registry as registry
    from app.console_errors import _CODES
    from app.scope import Scope

    main, client, slug = _registry_client(tmp_path, monkeypatch)
    scope = Scope(Path(tmp_path), "alpha")
    proposal = registry.propose_delete(scope, "product", slug)
    # Valid YAML, wrong shape: a top-level list where a mapping is expected
    # — design §5's "wrongly shaped but valid" boundary-conversion case,
    # the likelier hand-editing mistake `yaml.safe_load(...) or {}` cannot
    # guard against.
    proposal.path.write_text("- just\n- a\n- list\n", encoding="utf-8")
    # S7: submit the fingerprint of the corrupt bytes themselves. The
    # comparison runs before the parse (design §3), so an unrelated
    # fingerprint would refuse as a changed review and never reach the
    # unreadable-record outcome this test exists to pin.
    corrupt_fingerprint = hashlib.sha256(proposal.path.read_bytes()).hexdigest()

    reached = []
    original = main.app.exception_handlers[Exception]

    async def _spy(request, exc):
        reached.append(type(exc).__name__)
        return await original(request, exc)

    monkeypatch.setitem(main.app.exception_handlers, Exception, _spy)

    response = TestClient(main.app).post(
        "/registry/alpha/product/delete-execute",
        data={"id": proposal.id, "review_sha256": corrupt_fingerprint},
    )

    assert reached == [], f"fallback reached: {reached}"
    assert response.status_code == _CODES["E-UNREADABLE"].page_status
    assert 'role="alert"' in response.text
    assert "E-UNREADABLE" in response.text
    assert _CODES["E-UNREADABLE"].message in response.text
    # Rule 9 / §6 disclosure: no path is echoed either.
    assert str(proposal.path) not in response.text
    assert proposal.path.name not in response.text


def test_registry_products_hx_vals_survive_hostile_operator_slug(tmp_path, monkeypatch):
    """M2 (review): `templates/registry.html`'s
    `hx-vals='{{ delete_preview_values | tojson }}'` embeds `slug`, the one
    remaining operator-controllable value that reaches a registry
    template's `hx-vals` — `products.yaml` is hand-edited YAML and stays
    "the hand-editable source of truth" by design (`app/registry.py`
    module docstring). Review verified fifteen hostile shapes round-trip
    safely by hand; nothing pinned it as a test. This is one of those
    fifteen, kept as a route test so a future change to this template
    cannot silently reopen it.
    """
    import yaml as _yaml

    from tests.test_app import HxValsParser

    main = _load_main(tmp_path, monkeypatch, ENTITIES)
    hostile_slug = 'shown", "extra": "injected", "slug": "'
    products_yaml = _yaml.safe_dump(
        {"version": "1.0", "products": {"alpha": {hostile_slug: {"label": "Widget"}}}},
        sort_keys=False,
    )
    (tmp_path / "_system" / "products.yaml").write_text(products_yaml, encoding="utf-8")
    client = TestClient(main.app)

    response = client.get("/registry/alpha/products")

    assert response.status_code == 200
    parser = HxValsParser()
    parser.feed(response.text)
    assert len(parser.values) == 1
    parsed = json.loads(parser.values[0])
    assert parsed == {"slug": hostile_slug}


def test_delete_preview_fragment_does_not_reproduce_impact_target(tmp_path, monkeypatch):
    """I2 (review) / design §8 test matrix: `#impact-{index}` swaps
    `innerHTML`, so the fragment must not reproduce the `#impact-N` root
    (that would nest a duplicate id) and must never echo the
    client-supplied `HX-Target` header, which is attacker-controlled. The
    same pattern already applied to `#diff-{index}` at
    `test_propose_alert_preserves_triage_alpine_scope` and to
    `#outbox-list` at `test_outbox_fragments_reproduce_outbox_list_root`;
    nothing covered this shape before this task.
    """
    main, client, slug = _registry_client(tmp_path, monkeypatch)

    response = client.post(
        "/registry/alpha/product/delete-preview",
        data={"slug": slug},
        headers={"HX-Target": "hostile-target-marker"},
    )

    assert response.status_code == 200
    assert 'id="impact-' not in response.text
    assert "hostile-target-marker" not in response.text


def test_delete_execute_fragment_does_not_reproduce_delete_impact_root(
    tmp_path, monkeypatch
):
    """I2 (review) / design §8 test matrix: delete-execute targets `closest
    .delete-impact` with `innerHTML` — "a relative selector rather than an
    id, and the route S6 rewrites most heavily" — so its own fragment must
    not reproduce the `.delete-impact` root, and must never echo the
    client-supplied `HX-Target` header. Covers both the success fragment
    (`blocks/delete_success.html`) and the error fragment
    (`blocks/alert.html`, via `_render_console_error`).

    R2-I2 (round-2 review): this docstring claimed error-fragment coverage
    the test did not have — it drove only the success branch (measured:
    `SUCCESS BRANCH? True`), so the error fragment's shape was unpinned.
    Proven exploitable: prefixing `blocks/alert.html` with
    `<div class="delete-impact"></div>` left this file at 51 passed. The
    second request below drives the error branch for real, with an id that
    matches no delete proposal on disk — `get_delete_proposal` raises
    `RegistryError` (a member of `_REGISTRY_DELETE_CATCHES`) before
    `execute_delete` is ever called, landing in `_render_console_error`,
    not the success template.
    """
    import app.registry as registry
    from app.console_errors import _CODES
    from app.scope import Scope

    main, client, slug = _registry_client(tmp_path, monkeypatch)
    scope = Scope(Path(tmp_path), "alpha")
    proposal = registry.propose_delete(scope, "product", slug)

    response = client.post(
        "/registry/alpha/product/delete-execute",
        data=_delete_action_data(tmp_path, proposal),
        headers={"HX-Target": "hostile-target-marker"},
    )

    assert response.status_code == 200
    assert 'class="delete-impact"' not in response.text
    assert "hostile-target-marker" not in response.text
    # This is genuinely the success fragment, not a false pass on a shared
    # shape: `blocks/delete_success.html`'s own text is present, and the
    # error fragment's marker is absent.
    assert "One commit written" in response.text
    assert 'role="alert"' not in response.text

    error_response = client.post(
        "/registry/alpha/product/delete-execute",
        data={"id": "not-a-real-proposal-id",
              "review_sha256": _UNBOUND_FINGERPRINT},
        headers={"HX-Target": "hostile-target-marker"},
    )

    assert error_response.status_code == 200
    assert 'class="delete-impact"' not in error_response.text
    assert "hostile-target-marker" not in error_response.text
    # Genuinely the error fragment: role="alert" plus the described code and
    # message, and no fragment of the success copy.
    assert 'role="alert"' in error_response.text
    assert "E-REGISTRY" in error_response.text
    assert _CODES["E-REGISTRY"].message in error_response.text
    assert "One commit written" not in error_response.text
    assert "not-a-real-proposal-id" not in error_response.text


# --- Task 14: cross-cutting proofs (design §8) ------------------------------
#
# Three deliverables: the state-proof matrix (Step 1), the disclosure sweep
# (Step 2), and route totality (Step 3). Tasks 10-13 already built extensive
# per-route coverage — the three `..._never_reaches_the_global_fallback`
# tests, the real-filesystem symlink/corrupt-record tests, and the two
# persistence-outcome tests folded above — so this section's job is to (a)
# organize the state proof as the explicit matrix design §8 asks for rather
# than as scattered individual tests, (b) build the systematic disclosure
# sweep that did not exist yet, and (c) close the totality gap Task 13 named:
# `shell`, `pulse`, and `triage_default` had zero S6-era failure-injection
# coverage, and `propose` had no totality test at all (only its
# post-persistence branch was covered).


class _AlertContent(HTMLParser):
    """Extracts the plain text and every attribute value of the `role="alert"`
    element and its descendants — there is exactly one alert subtree per
    rendered error (`blocks/alert.html`, included directly or nested inside a
    row/listing template).

    A raw-HTML substring check cannot assert "no path separator": every
    closing tag in this file (`</div>`, `</span>`, …) contains one, so
    checking the whole response text would be a test that can never pass.
    Parsing to plain text — and confining the check to the alert subtree, so
    legitimate sibling content (a still-pending proposal's real id and
    destination) is never mistaken for a leak — is what makes the assertion
    meaningful.
    """

    def __init__(self) -> None:
        super().__init__()
        self._stack: list[bool] = []
        self.texts: list[str] = []
        self.attr_values: list[str] = []

    def _enter(self, attrs) -> None:
        parent_in_alert = self._stack[-1] if self._stack else False
        is_alert = dict(attrs).get("role") == "alert"
        in_alert = parent_in_alert or is_alert
        self._stack.append(in_alert)
        if in_alert:
            for _, value in attrs:
                if value:
                    self.attr_values.append(value)

    def handle_starttag(self, tag, attrs):
        self._enter(attrs)

    def handle_startendtag(self, tag, attrs):
        self._enter(attrs)
        self._stack.pop()

    def handle_endtag(self, tag):
        if self._stack:
            self._stack.pop()

    def handle_data(self, data):
        if self._stack and self._stack[-1]:
            self.texts.append(data)

    @property
    def text(self) -> str:
        return "".join(self.texts)


def test_alert_content_parser_isolates_the_alert_subtree():
    """Pins `_AlertContent` itself before trusting it as a test oracle: text
    and attribute values OUTSIDE the alert subtree must never be collected,
    and a path separator inside legitimate sibling content must not trip a
    check confined to the alert."""
    html = (
        '<div id="outbox-list">'
        '<span class="prop-route">alpha/02-work/active/marker.md</span>'
        '<div class="alert" role="alert" data-marker="inside-alert-marker">'
        "<span>outside text should not leak in</span>"
        "</div>"
        "<span>trailing/sibling/path</span>"
        "</div>"
    )
    parser = _AlertContent()
    parser.feed(html)
    assert parser.text == "outside text should not leak in"
    assert parser.attr_values == ["alert", "alert", "inside-alert-marker"]
    assert "alpha/02-work" not in parser.text
    assert "trailing/sibling/path" not in parser.text


def _alert(html_text: str) -> _AlertContent:
    parser = _AlertContent()
    parser.feed(html_text)
    return parser


#: The entity slug every fixture in this sweep uses (`ENTITIES` above).
#: Checked on EVERY case (C3, review), not only where a caller happened to
#: pass it as a marker — an earlier revision left this out of the base
#: check entirely, so "leak the bound entity slug into alert TEXT" passed
#: green on every one of the sweep's ~20 cases at once, not just one.
_FIXTURE_ENTITY_SLUG = "alpha"


def _assert_alert_discloses_nothing(response, expected_code: str, markers=()) -> None:
    """The core disclosure assertion (design §6, Rule 9): the alert's own
    text carries only the curated message for `expected_code`, no path
    separator, and none of the case's hostile markers or the fixture's own
    entity slug — in either the text OR a dynamic attribute value on the
    alert subtree.

    C3 (review): an earlier revision applied the path-separator check to
    `alert.text` only, leaving `attr_values` checked against per-case
    synthetic markers alone — so leaking `request.url.path` (which contains
    both a `/` and the entity slug `alpha`) into an alert ATTRIBUTE passed
    green, and no assertion anywhere named the fixture slug at all, so
    leaking it into alert TEXT passed green too. Both gaps are closed here,
    unconditionally, rather than by asking every call site to remember to
    pass "alpha" as one more marker.
    """
    from app.console_errors import _CODES

    assert expected_code in response.text, response.text
    alert = _alert(response.text)
    assert alert.text, "expected an alert subtree, found none"
    assert _CODES[expected_code].message in alert.text, (expected_code, alert.text)
    assert "/" not in alert.text, alert.text
    assert "\\" not in alert.text, alert.text
    for value in alert.attr_values:
        assert "/" not in value, value
        assert "\\" not in value, value
    for marker in (*markers, _FIXTURE_ENTITY_SLUG):
        assert marker not in alert.text, (marker, alert.text)
        for value in alert.attr_values:
            assert marker not in value, (marker, value)


# --- Step 1: the state-proof matrix (design §8 "State proof") --------------


def _state_proof_no_none(tmp_path, monkeypatch):
    """`(committed=no, persistence=none)`: one refusal per non-persisting
    route, every fingerprint identical. `propose` and
    `registry_delete_preview` are excluded on purpose — design §8 reserves
    `proposal-written` for exactly those two. `pulse` is excluded too: it
    declares `catches=()` (main.py's own comment: "pulse reads no registry,
    resolves no path, and has no domain family to declare"), so it
    contributes no refusal case — see `test_pulse_declares_no_family`.

    `shell` and `triage_default` are ALSO excluded, and not for the same
    reason: their declared family (`DestinationRegistryError`,
    `EntityManifestError`) is a registry-read failure while COMPOSING the
    page, not a refused action, so neither route contributes a refusal case
    to this fingerprint sweep — there is no action to refuse and nothing
    that could have persisted. Both routes now answer that family
    themselves (`test_shell_and_triage_default_declared_family_never_
    reaches_the_global_fallback`), and their described-error behaviour is
    proved by the three dedicated tests grouped with it.
    """
    from app.console_errors import _CODES
    from app.outbox import OutboxError
    from app.registry import RegistryError
    from app.scope import OutOfScopeError

    main, client, proposal_id = _git_outbox_proposal_client(tmp_path, monkeypatch)

    vault = tmp_path
    head_before = git_head(vault)
    index_before = git_index_entries(vault)
    cached_before = git_cached_diff(vault)
    worktree_before = git_worktree_diff(vault)
    status_before = git_status_apart_from_quarantine(vault)
    outbox_before = _outbox_snapshot(vault)

    real_project_outbox = main.project_outbox

    def _raise_scope(*args, **kwargs):
        raise OutOfScopeError("resolved outside the selected entity")

    monkeypatch.setattr(main, "read_inbox", _raise_scope)
    triage_response = client.get("/triage/alpha")

    def _raise_outbox(*args, **kwargs):
        raise OutboxError("outbox is otherwise broken")

    monkeypatch.setattr(main, "project_outbox", _raise_outbox)
    outbox_screen_response = client.get("/outbox/alpha")
    monkeypatch.setattr(main, "project_outbox", real_project_outbox)

    monkeypatch.setattr(main, "approve", _raise_outbox)
    outbox_approve_response = client.post(
        "/outbox/alpha/approve", data=_action_data(tmp_path, proposal_id)
    )

    monkeypatch.setattr(main, "reject", _raise_outbox)
    outbox_reject_response = client.post(
        "/outbox/alpha/reject", data=_action_data(tmp_path, proposal_id)
    )

    def _raise_registry_error(*args, **kwargs):
        raise RegistryError("registry is otherwise broken")

    monkeypatch.setattr(main, "products_for", _raise_registry_error)
    registry_products_response = client.get("/registry/alpha/products")

    monkeypatch.setattr(main, "execute_delete", _raise_registry_error)
    registry_delete_execute_response = client.post(
        "/registry/alpha/product/delete-execute", data={"id": "irrelevant", "review_sha256": _UNBOUND_FINGERPRINT}
    )

    # (response, expected code, expected status)
    responses = {
        "triage": (triage_response, "E-SCOPE", _CODES["E-SCOPE"].page_status),
        "outbox_screen": (
            outbox_screen_response, "E-INVALID", _CODES["E-INVALID"].page_status,
        ),
        "outbox_approve": (outbox_approve_response, "E-INVALID", 200),
        "outbox_reject": (outbox_reject_response, "E-INVALID", 200),
        "registry_products": (
            registry_products_response, "E-REGISTRY", _CODES["E-REGISTRY"].page_status,
        ),
        "registry_delete_execute": (
            registry_delete_execute_response, "E-REGISTRY", 200,
        ),
    }
    for route, (response, code, status) in responses.items():
        assert response.status_code == status, f"{route}: {response.text}"
        assert code in response.text, f"{route}: expected {code}, got {response.text}"
        assert 'role="alert"' in response.text, route

    assert git_head(vault) == head_before
    assert git_index_entries(vault) == index_before
    assert git_cached_diff(vault) == cached_before
    assert git_worktree_diff(vault) == worktree_before
    assert git_status_apart_from_quarantine(vault) == status_before
    assert _outbox_snapshot(vault) == outbox_before


def _state_proof_committed_outbox_approve(tmp_path, monkeypatch):
    """`(committed=yes)`: committed-cleanup on `outbox_approve`.

    I2 (review): this used to duplicate `test_approve_committed_cleanup_
    shows_e_committed` byte-for-byte — same fixture, same injected failure,
    same state-proof assertions — minus that test's `role="alert"` and
    message checks, making it a strictly WEAKER copy rather than a second
    proof. Folded: this cell now simply invokes the pre-existing,
    Task-12-owned regression, so there is exactly one assertion set for this
    scenario rather than two that can silently drift apart.
    """
    test_approve_committed_cleanup_shows_e_committed(tmp_path, monkeypatch)


def _index_entries_by_path(raw: bytes) -> dict[str, bytes]:
    """Parse `git ls-files --stage -z` output into `{path: "<mode> <hash>
    <stage>"}`, so a path-keyed comparison can exclude a specific reviewed
    path rather than requiring the whole blob byte-identical (I1, review)."""
    entries: dict[str, bytes] = {}
    for chunk in raw.split(b"\0"):
        if not chunk:
            continue
        meta, _, path = chunk.partition(b"\t")
        entries[path.decode("utf-8")] = meta
    return entries


def _status_lines_excluding(raw: bytes, *excluded: str) -> list[bytes]:
    """`git status --porcelain=v1 -z` entries, minus any naming one of
    `excluded` — for a scenario where a specific OWNED path is expected to
    show up (or change) in status while everything else must not (I3,
    review's `_state_proof_unknown_concurrent_writer`)."""
    excluded_bytes = {p.encode("utf-8") for p in excluded}
    kept = []
    for entry in raw.split(b"\0"):
        if not entry:
            continue
        path = entry[3:]  # porcelain v1: "XY path"
        if path in excluded_bytes:
            continue
        kept.append(entry)
    return kept


def _state_proof_committed_registry_delete_execute(tmp_path, monkeypatch):
    """`(committed=yes)`: committed-cleanup on `registry_delete_execute`,
    the other route design §8 names for this outcome. Same real post-commit
    cleanup failure as the outbox case above.

    I1 (review): this cell asserted no "unrelated state identical"
    invariant at all — a stray write anywhere else in the vault during this
    scenario would have passed green. Five fingerprints, before and after,
    now prove it: the worktree, cached diff, and status are expected to
    return to the SAME clean state they started in (the transaction commits
    everything it touches, leaving nothing pending either side of the
    request), so those three compare byte-identical; the index is compared
    path-by-path with the one REVIEWED path excluded, since that blob hash
    is expected to change — that is the commit. `git_changed_paths` then
    proves the commit itself touched exactly that one path and nothing else.
    """
    import app.git_transaction as git_transaction
    import app.registry as registry
    from app.scope import Scope

    main, client, slug = _registry_client(tmp_path, monkeypatch)
    vault = Path(tmp_path)
    scope = Scope(vault, "alpha")
    registry_path = scope.system_path("products.yaml")
    reviewed_path = "_system/products.yaml"
    proposal = registry.propose_delete(scope, "product", slug)

    head_before = git_head(vault)
    commits_before = git_count_commits(vault)
    index_before = _index_entries_by_path(git_index_entries(vault))
    cached_before = git_cached_diff(vault)
    worktree_before = git_worktree_diff(vault)
    status_before = git_status_apart_from_quarantine(vault)

    def _fail_cleanup(temporary_index):
        return OSError("injected post-commit temporary index cleanup failure")

    monkeypatch.setattr(git_transaction, "_remove_temporary_index", _fail_cleanup)

    response = client.post(
        "/registry/alpha/product/delete-execute",
        data=_delete_action_data(tmp_path, proposal),
    )

    from app.console_errors import _CODES

    assert response.status_code == _CODES["E-COMMITTED"].page_status
    assert "E-COMMITTED" in response.text

    assert git_count_commits(vault) == commits_before + 1
    new_head = git_head(vault)
    assert new_head != head_before
    assert git_changed_paths(vault, new_head) == [reviewed_path]

    assert git_worktree_diff(vault) == worktree_before
    assert git_cached_diff(vault) == cached_before
    assert git_status_apart_from_quarantine(vault) == status_before
    index_after = _index_entries_by_path(git_index_entries(vault))
    index_before.pop(reviewed_path, None)
    index_after.pop(reviewed_path, None)
    assert index_after == index_before

    assert slug.encode("utf-8") not in registry_path.read_bytes()
    assert proposal.path.exists() is False


def _state_proof_unknown(tmp_path, monkeypatch):
    """`(committed=unknown)`: recovery-blocked. Design §8: "unrelated state
    identical, and the owned path matches either its pre-request state or
    the concurrent writer's state and nothing else." The injected
    `GitTransactionRecoveryError` fires before `execute_transaction` performs
    any real work, so nothing here simulates a concurrent writer — this
    proves the first of the two allowed disjuncts, "matches its pre-request
    state"."""
    import app.outbox as outbox_module
    from app.console_errors import _CODES
    from app.git_transaction import GitTransactionRecoveryError

    main, client, proposal_id = _git_outbox_proposal_client(tmp_path, monkeypatch)
    vault = Path(tmp_path)
    source = vault / "alpha/00-inbox/active/marker.md"
    destination = vault / "alpha/02-work/active/marker.md"
    proposal_path = vault / "alpha/outbox" / f"{proposal_id}.yaml"
    source_before = source.read_bytes()
    proposal_before = proposal_path.read_bytes()
    head_before = git_head(vault)
    index_before = git_index_entries(vault)
    cached_before = git_cached_diff(vault)
    worktree_before = git_worktree_diff(vault)
    status_before = git_status_apart_from_quarantine(vault)

    blocked_path = "alpha/11-other/active/blocked-path-marker.md"
    real_execute_transaction = outbox_module.execute_transaction

    def _raise(*args, **kwargs):
        raise GitTransactionRecoveryError((blocked_path,))

    # M2 (review): `raising=False` only disables monkeypatch's own
    # check that the symbol already exists — it does, `app/outbox.py`
    # imports `execute_transaction` directly, so the flag was inert.
    monkeypatch.setattr(outbox_module, "execute_transaction", _raise)

    try:
        response = client.post("/outbox/alpha/approve", data=_action_data(tmp_path, proposal_id))

        assert response.status_code == _CODES["E-RECOVER"].page_status
        assert "E-RECOVER" in response.text
        assert "blocked-path-marker" not in response.text

        # Unrelated state identical: no commit happened at all.
        assert git_head(vault) == head_before
        assert git_index_entries(vault) == index_before
        assert git_cached_diff(vault) == cached_before
        assert git_worktree_diff(vault) == worktree_before
        assert git_status_apart_from_quarantine(vault) == status_before

        # The owned path — the proposal's source — matches its pre-request
        # state.
        assert source.read_bytes() == source_before
        assert destination.exists() is False
        assert proposal_path.read_bytes() == proposal_before
    finally:
        # `monkeypatch` is shared across BOTH scenarios the "unknown" cell
        # runs in one test invocation — restore explicitly rather than
        # relying on end-of-test teardown, or the sibling scenario
        # (`_state_proof_unknown_concurrent_writer`) would drive its POST
        # through THIS patch instead of a real transaction, and its
        # `.git/hooks/pre-commit` vehicle would never run at all.
        monkeypatch.setattr(
            outbox_module, "execute_transaction", real_execute_transaction
        )


def _state_proof_unknown_concurrent_writer(tmp_path, monkeypatch):
    """`(committed=unknown)`, the SECOND disjunct design §8 allows — "the
    owned path matches ... the concurrent writer's state" — which
    `_state_proof_unknown` above cannot reach: its own docstring concedes
    "nothing here simulates a concurrent writer", because its injected
    failure fires before `execute_transaction` performs any real work (I3,
    review).

    This drives a REAL concurrent writer through `outbox_approve`: a
    `.git/hooks/pre-commit` hook that overwrites the reviewed destination
    after the transaction has staged it — the same vehicle
    `tests/test_git_transaction.py::
    test_hook_same_path_replacement_after_staging_blocks_success` uses
    directly against `execute_transaction`. No application code is
    monkeypatched here; the recovery-blocked outcome and the conflicting
    bytes on disk are both genuine.
    """
    main, client, proposal_id = _git_outbox_proposal_client(tmp_path, monkeypatch)
    vault = Path(tmp_path)
    source = vault / "alpha/00-inbox/active/marker.md"
    destination = vault / "alpha/02-work/active/marker.md"
    destination_relative = "alpha/02-work/active/marker.md"
    proposal_path = vault / "alpha/outbox" / f"{proposal_id}.yaml"
    source_before = source.read_bytes()
    proposal_before = proposal_path.read_bytes()
    head_before = git_head(vault)
    index_before = git_index_entries(vault)
    cached_before = git_cached_diff(vault)
    status_before = git_status_apart_from_quarantine(vault)

    concurrent_bytes = b"concurrent replacement\n"
    hook = vault / ".git/hooks/pre-commit"
    hook.write_text(
        "#!/bin/sh\n"
        f"printf '{concurrent_bytes.decode()}' > "
        "alpha/02-work/active/marker.md\n",
        encoding="utf-8",
    )
    hook.chmod(0o755)

    from app.console_errors import _CODES

    response = client.post("/outbox/alpha/approve", data=_action_data(tmp_path, proposal_id))

    assert response.status_code == _CODES["E-RECOVER"].page_status
    assert "E-RECOVER" in response.text

    # Unrelated state identical: no commit happened at all (the hook's
    # write is a working-tree mutation, not a commit).
    assert git_head(vault) == head_before
    assert git_index_entries(vault) == index_before
    assert git_cached_diff(vault) == cached_before
    # The destination is the OWNED path here (it now holds the concurrent
    # writer's bytes, per design's second disjunct), so it is EXPECTED to
    # appear as a new untracked entry in status — excluded from the
    # comparison rather than asserted byte-identical, the same "excluding
    # the reviewed/owned path" shape I1's registry fix uses.
    assert _status_lines_excluding(
        git_status_bytes(vault), destination_relative
    ) == _status_lines_excluding(status_before, destination_relative)

    # The owned path — the destination — matches the CONCURRENT WRITER's
    # bytes. This is the disjunct `_state_proof_unknown` cannot exercise.
    assert destination.read_bytes() == concurrent_bytes
    assert source.read_bytes() == source_before
    assert proposal_path.read_bytes() == proposal_before


def _state_proof_shell_and_triage_default(tmp_path, monkeypatch):
    """M6 (review): `shell` and `triage_default` are the two routes whose
    app code Task 14 changed (the C1/C2 re-entrancy and boundary-conversion
    handlers), and neither had a state proof. Both are read-only against a
    broken registry — there is no domain action to refuse — so the only
    claim to prove is design §6's own: "describing and rendering an error
    performs no mutation: no file written, nothing staged, no directory
    created, no lock acquired."
    """
    from app.vault import DestinationRegistryError

    main = _load_main(tmp_path, monkeypatch, ENTITIES)
    vault = Path(tmp_path)
    subprocess.run(["git", "init", "-q"], cwd=vault, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"],
        cwd=vault, check=True,
    )
    subprocess.run(["git", "config", "user.name", "test"], cwd=vault, check=True)
    subprocess.run(["git", "add", "-A"], cwd=vault, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "fixture"], cwd=vault, check=True)

    head_before = git_head(vault)
    index_before = git_index_entries(vault)
    cached_before = git_cached_diff(vault)
    worktree_before = git_worktree_diff(vault)
    status_before = git_status_apart_from_quarantine(vault)

    def _raise(self):
        raise DestinationRegistryError("registries unreadable")

    monkeypatch.setattr(main.Vault, "bundles", _raise)
    client = TestClient(main.app)
    for path in ("/", "/triage"):
        response = client.get(path)
        assert response.status_code == 500
        assert "E-CONFIG" in response.text
        assert 'role="alert"' in response.text

    assert git_head(vault) == head_before
    assert git_index_entries(vault) == index_before
    assert git_cached_diff(vault) == cached_before
    assert git_worktree_diff(vault) == worktree_before
    assert git_status_apart_from_quarantine(vault) == status_before


@pytest.mark.parametrize("cell", ["no-none", "no-proposal-written", "yes", "unknown"])
def test_state_proof_matrix(tmp_path, monkeypatch, cell):
    """design §8 "State proof": the explicit matrix keyed by
    `(committed, persistence)`. Each cell drives every route/scenario design
    §8 assigns to it and proves the matching fingerprint discipline with the
    conftest git helpers plus `_outbox_snapshot`.

    `no-proposal-written` and `yes` each cover two routes/scenarios under one
    cell id — both scenarios in a cell get a fresh sub-vault (`tmp_path /
    <name>`) so neither's monkeypatching or Git state can bleed into the
    other. `no-none` and `unknown` now do too (M6, I3, review).
    """
    if cell == "no-none":
        _state_proof_no_none(tmp_path / "actions", monkeypatch)
        _state_proof_shell_and_triage_default(tmp_path / "shell-triage", monkeypatch)
    elif cell == "no-proposal-written":
        _state_proof_proposal_written_registry_delete_preview(
            tmp_path / "delete-preview", monkeypatch
        )
        _state_proof_proposal_written_propose(tmp_path / "propose", monkeypatch)
    elif cell == "yes":
        _state_proof_committed_outbox_approve(tmp_path / "approve", monkeypatch)
        _state_proof_committed_registry_delete_execute(
            tmp_path / "delete-execute", monkeypatch
        )
    elif cell == "unknown":
        _state_proof_unknown(tmp_path / "injected", monkeypatch)
        _state_proof_unknown_concurrent_writer(tmp_path / "concurrent", monkeypatch)
    else:  # pragma: no cover - parametrize is closed
        raise AssertionError(cell)


# --- Step 2: the disclosure sweep (design §6, §8) ---------------------------


def test_alerts_never_contain_paths_slugs_or_echoes(tmp_path, monkeypatch):
    """design §8 disclosure sweep: every described error each route's
    declared family can produce, over every route. This file already checks
    individual cases with `"marker" not in response.text` (a strictly
    coarser and strictly weaker check, since it covers the whole page); this
    sweep is what proves the specific, sharper claim the plan asks for — "no
    path separator" — which a raw substring check cannot express at all
    (every closing tag contains one), by parsing to the alert's own plain
    text with `_AlertContent`.

    One request per (route, declared exception); a distinct hostile marker
    per case embeds both a fake path segment and a fake slug, so a single
    assertion covers "no path separator", "no fixture slug", and "no raw
    exception text" together. Where the route also accepts attacker-controlled
    form input (a slug, a filename, an id), that field carries its own
    distinct marker to prove Rule 8 (never reflect a submitted value) inside
    the same sweep.

    `pulse` contributes nothing (`catches=()`); `EntityManifestError` on
    `shell`/`triage_default` is excluded — design §11's known limitation:
    `catalog = build_catalog()` runs at module scope, so that exception can
    never arise while a client exists to observe a response.
    """
    from app.outbox import OutboxError, UnreadableProposalRecord
    from app.registry import RegistryError
    from app.scope import OutOfScopeError, RedirectedPathError
    from app.vault import DestinationRegistryError

    n = 0

    def _marker(label: str) -> str:
        nonlocal n
        n += 1
        return f"hostile/marker-{n}-{label}/leaked-secret.md"

    # -- shell, triage_default: DestinationRegistryError -----------------
    # Both routes answer this family themselves (see
    # `test_shell_and_triage_default_declared_family_never_reaches_the_
    # global_fallback`); this sweep is about the SAFETY of whatever alert is
    # rendered, not about which handler rendered it, so it stays agnostic.
    #
    # `main.Vault` is the SAME class object across every `_load_main` reload
    # in this sweep (only `app.main`, not `app.vault`, gets reloaded), so the
    # patch MUST be restored before any later case in this sweep — including
    # `triage`'s own page-level error branch, which calls `Vault(catalog).
    # bundles()` again to build the sidebar (`_render_console_error`) and
    # would otherwise raise a second, unrelated error from a stale patch.
    for route, path in (("shell", "/"), ("triage_default", "/triage")):
        main = _load_main(tmp_path / route, monkeypatch, ENTITIES)
        marker = _marker(route)
        real_bundles = main.Vault.bundles

        def _raise_bundles(self, __marker=marker):
            raise DestinationRegistryError(f"registries unreadable: {__marker}")

        monkeypatch.setattr(main.Vault, "bundles", _raise_bundles)
        response = TestClient(main.app, raise_server_exceptions=False).get(path)
        monkeypatch.setattr(main.Vault, "bundles", real_bundles)
        _assert_alert_discloses_nothing(response, "E-CONFIG", [marker])

    # -- triage: DestinationError is per-row and never the page-level
    #    alert this sweep targets (design §3), so only the two page-level
    #    members are exercised here: RedirectedPathError and
    #    DestinationRegistryError. `OutOfScopeError` is E-SCOPE, whose
    #    message deliberately omits WHERE the request resolved (§6) — no
    #    marker to embed, covered by presence alone.
    for exc_factory, code in (
        (lambda m: RedirectedPathError(f"redirected inbox: {m}"), "E-TAMPER"),
        (lambda m: DestinationRegistryError(f"registries unreadable: {m}"), "E-CONFIG"),
    ):
        subdir = tmp_path / f"triage-{code}"
        main = _load_main(subdir, monkeypatch, ENTITIES)
        scaffold_modules(subdir, "alpha", ["00-intake", "01-core", "02-work"])
        marker = _marker(f"triage-{code}")

        def _raise(*args, __exc=exc_factory(marker), **kwargs):
            raise __exc

        monkeypatch.setattr(main, "read_inbox", _raise)
        response = TestClient(main.app).get("/triage/alpha")
        _assert_alert_discloses_nothing(response, code, [marker])

    scope_subdir = tmp_path / "triage-E-SCOPE"
    main = _load_main(scope_subdir, monkeypatch, ENTITIES)

    def _raise_scope(*args, **kwargs):
        raise OutOfScopeError("resolved outside the selected entity")

    monkeypatch.setattr(main, "read_inbox", _raise_scope)
    response = TestClient(main.app).get("/triage/alpha")
    _assert_alert_discloses_nothing(response, "E-SCOPE")

    # -- propose: submitted module value carries its own marker (Rule 8) -
    # I5 (review): `E-DEST` added — `propose`'s own declared `DestinationError`
    # member and, per review, "a declared member, the most operator-reachable
    # refusal, reached by a plain form value" — the sweep covered only 6 of
    # 21 codes before this and omitted it entirely.
    from app.destinations import MissingDestination

    for exc_factory, code in (
        (lambda m: OutboxError(f"outbox is otherwise broken: {m}"), "E-INVALID"),
        (lambda m: RedirectedPathError(f"redirected source: {m}"), "E-TAMPER"),
        (
            lambda m: DestinationRegistryError(f"registries unreadable: {m}"),
            "E-CONFIG",
        ),
        (lambda m: MissingDestination(f"destination unresolved: {m}"), "E-DEST"),
    ):
        subdir = tmp_path / f"propose-{code}"
        main, client = _propose_client(subdir, monkeypatch)
        marker = _marker(f"propose-{code}")
        submitted_marker = _marker(f"propose-{code}-submitted")

        def _raise(*args, __exc=exc_factory(marker), **kwargs):
            raise __exc

        monkeypatch.setattr(main, "propose_classification", _raise)
        response = client.post(
            "/triage/alpha/propose",
            data={"filename": "note.md", "module": submitted_marker, "sub": ""},
        )
        _assert_alert_discloses_nothing(response, code, [marker, submitted_marker])

    # -- outbox_screen, outbox_approve, outbox_reject ---------------------
    for exc_factory, code in (
        (lambda m: OutboxError(f"outbox is otherwise broken: {m}"), "E-INVALID"),
        (lambda m: RedirectedPathError(f"redirected proposal leaf: {m}"), "E-TAMPER"),
        (
            lambda m: DestinationRegistryError(f"registries unreadable: {m}"),
            "E-CONFIG",
        ),
    ):
        subdir = tmp_path / f"outbox-screen-{code}"
        main, client, proposal_id = _outbox_proposal_client(subdir, monkeypatch)
        marker = _marker(f"outbox-screen-{code}")

        def _raise(*args, __exc=exc_factory(marker), **kwargs):
            raise __exc

        monkeypatch.setattr(main, "project_outbox", _raise)
        response = client.get("/outbox/alpha")
        _assert_alert_discloses_nothing(response, code, [marker])

        for action in ("approve", "reject"):
            subdir = tmp_path / f"outbox-{action}-{code}"
            main, client, proposal_id = _outbox_proposal_client(subdir, monkeypatch)
            marker = _marker(f"outbox-{action}-{code}")
            id_marker = _marker(f"outbox-{action}-{code}-id")
            # S7 adds a second submitted value to the same request, so
            # Rule 8 is proved for the fingerprint field too: a hostile
            # value there must never be reflected into the alert.
            fingerprint_marker = _marker(f"outbox-{action}-{code}-fp")

            def _raise_action(*args, __exc=exc_factory(marker), **kwargs):
                raise __exc

            monkeypatch.setattr(main, action, _raise_action)
            response = client.post(
                f"/outbox/alpha/{action}",
                data={"id": id_marker, "review_sha256": fingerprint_marker},
            )
            _assert_alert_discloses_nothing(
                response, code, [marker, id_marker, fingerprint_marker]
            )

    # -- outbox_approve: E-RECOVER, E-COMMITTED (I5, review: these two
    #    chain-derived outcomes — Rule 1's resolver walking a wrapper's
    #    `__cause__` — were entirely absent from the sweep, and `E-COMMITTED`
    #    is "forbidden by name in §6" (the message must never leak a commit
    #    id). Constructed the same way `app/outbox.py` itself chains a
    #    transaction failure: an `OutboxTransactionError` wrapper with
    #    `__cause__` set to the real `git_transaction` type the resolver's
    #    allowlist walks, so this exercises the actual resolver path a real
    #    transaction failure takes rather than a shortcut around it.
    from app.git_transaction import (
        GitTransactionCommittedError,
        GitTransactionRecoveryError,
        TransactionResult,
    )
    from app.outbox import OutboxTransactionError

    def _chained(wrapper: BaseException, cause: BaseException) -> BaseException:
        wrapper.__cause__ = cause
        return wrapper

    for exc_factory, code in (
        (
            lambda m: _chained(
                OutboxTransactionError("outbox transaction failed"),
                GitTransactionRecoveryError((f"alpha/blocked/{m}.md",)),
            ),
            "E-RECOVER",
        ),
        (
            lambda m: _chained(
                OutboxTransactionError("outbox transaction failed"),
                GitTransactionCommittedError(
                    TransactionResult(
                        commit_oid=f"deadbeef{m}",
                        changed_paths=(f"alpha/committed/{m}.md",),
                    ),
                    OSError(f"cleanup failed: {m}"),
                ),
            ),
            "E-COMMITTED",
        ),
    ):
        subdir = tmp_path / f"outbox-approve-{code}"
        main, client, proposal_id = _outbox_proposal_client(subdir, monkeypatch)
        marker = _marker(f"outbox-approve-{code}")

        def _raise_chained(*args, __exc=exc_factory(marker), **kwargs):
            raise __exc

        monkeypatch.setattr(main, "approve", _raise_chained)
        response = client.post(
            "/outbox/alpha/approve", data=_action_data(subdir, proposal_id)
        )
        _assert_alert_discloses_nothing(response, code, [marker])

    # -- registry_products: declared (RegistryError, CrossScopeError,
    #    DestinationRegistryError) — NOT UnreadableProposalRecord, which is
    #    the two delete routes' own addition (`_REGISTRY_PRODUCTS_CATCHES` vs
    #    `_REGISTRY_DELETE_CATCHES`, app/main.py).
    for exc_factory, code in (
        (lambda m: RegistryError(f"registry is otherwise broken: {m}"), "E-REGISTRY"),
        (lambda m: RedirectedPathError(f"redirected registry: {m}"), "E-TAMPER"),
        (
            lambda m: DestinationRegistryError(f"registries unreadable: {m}"),
            "E-CONFIG",
        ),
    ):
        subdir = tmp_path / f"registry-products-{code}"
        main, client, slug = _registry_client(subdir, monkeypatch)
        marker = _marker(f"registry-products-{code}")

        def _raise(*args, __exc=exc_factory(marker), **kwargs):
            raise __exc

        monkeypatch.setattr(main, "products_for", _raise)
        response = client.get("/registry/alpha/products")
        # C3 (review): the product slug is a fixture value too, and none of
        # the sweep's assertions named it before this.
        _assert_alert_discloses_nothing(response, code, [marker, slug])

    # -- registry_delete_preview, registry_delete_execute: declared family
    #    additionally includes UnreadableProposalRecord.
    for exc_factory, code in (
        (lambda m: RegistryError(f"registry is otherwise broken: {m}"), "E-REGISTRY"),
        (lambda m: RedirectedPathError(f"redirected registry: {m}"), "E-TAMPER"),
        (
            lambda m: DestinationRegistryError(f"registries unreadable: {m}"),
            "E-CONFIG",
        ),
        (
            lambda m: UnreadableProposalRecord(f"corrupt delete proposal: {m}"),
            "E-UNREADABLE",
        ),
    ):
        subdir = tmp_path / f"registry-delete-preview-{code}"
        main, client, slug = _registry_client(subdir, monkeypatch)
        marker = _marker(f"registry-delete-preview-{code}")
        slug_marker = _marker(f"registry-delete-preview-{code}-slug")

        def _raise_preview(*args, __exc=exc_factory(marker), **kwargs):
            raise __exc

        monkeypatch.setattr(main, "propose_delete", _raise_preview)
        response = client.post(
            "/registry/alpha/product/delete-preview", data={"slug": slug_marker}
        )
        # C3 (review): the fixture's OWN product slug ("widget") too, not
        # only the hostile submitted one.
        _assert_alert_discloses_nothing(response, code, [marker, slug_marker, slug])

        subdir = tmp_path / f"registry-delete-execute-{code}"
        main, client, slug = _registry_client(subdir, monkeypatch)
        marker = _marker(f"registry-delete-execute-{code}")
        id_marker = _marker(f"registry-delete-execute-{code}-id")
        # S7: a second submitted value, so Rule 8 is proved for the
        # fingerprint field too.
        fingerprint_marker = _marker(f"registry-delete-execute-{code}-fp")

        def _raise_execute(*args, __exc=exc_factory(marker), **kwargs):
            raise __exc

        monkeypatch.setattr(main, "execute_delete", _raise_execute)
        response = client.post(
            "/registry/alpha/product/delete-execute",
            data={"id": id_marker, "review_sha256": fingerprint_marker},
        )
        _assert_alert_discloses_nothing(response, code, [marker, id_marker, slug])


# --- Step 3: route totality — closing the gap Task 13 named -----------------
#
# Task 13 proved by measurement that a totality test injecting a route's
# DECLARED family cannot discover that the declaration is incomplete (the
# symlinked products.yaml and corrupt delete-proposal escapes). The existing
# `..._never_reaches_the_global_fallback` tests for triage/outbox/registry
# already carry both halves — declaration-driven injection AND at least one
# real-filesystem condition apiece — and are kept as-is rather than folded
# into a single generic sweep: `test_outbox_declared_family_never_reaches_
# the_global_fallback`'s two-pass structure (I5) exists specifically to
# distinguish "the action's own except" from "the re-render's except", which
# a route-declaration-only sweep would collapse and silently stop testing.
# What follows closes the three routes with NO prior S6 coverage (`shell`,
# `pulse`, `triage_default`) and the one route whose totality test was
# entirely missing (`propose` — only the post-persistence branch was ever
# injected), plus the real-filesystem conditions still missing per route.


def _route_totality_plan(main) -> dict:
    """Every registered console route's request and patch targets, keyed by
    the endpoint FUNCTION OBJECT itself — the same identity
    `_registered_console_endpoints` yields (C4/I7, review).

    Every OTHER totality test in this file hand-copies the list of
    `(exception, expected_code)` pairs it injects. That cannot notice a
    route's declared `catches` tuple being silently WIDENED to a member
    NOTHING injects: the review added `EntityManifestError` to
    `_TRIAGE_CATCHES`, `_OUTBOX_CATCHES`, and `propose`'s tuple — three
    routes, a member nothing in this file drove — and the full suite stayed
    green. This plan intentionally carries no exception LISTS: the sweep
    below reads each route's `catches` straight off its own
    `__console_route__` at test time, so the injected set tracks the
    declaration automatically. Only the WHERE-to-inject (a patch target
    already inside the route's own guarded region) is route-specific and
    lives here.

    A patch target need not be where a given exception naturally originates
    in production — the sweep only needs to prove the route's OWN except
    tuple answers whatever DOES reach it, for every declared member. Which
    call site is the REALISTIC origin of a specific member is what the
    route-specific tests elsewhere in this file already prove.
    """
    return {
        main.shell: {
            "request": lambda c: c.get("/"),
            "patch_targets": [(main.Vault, "bundles")],
        },
        main.triage_default: {
            "request": lambda c: c.get("/triage"),
            "patch_targets": [(main.Vault, "bundles")],
        },
        main.triage: {
            "request": lambda c: c.get("/triage/alpha"),
            "patch_targets": [(main, "read_inbox")],
        },
        main.propose: {
            "request": lambda c: c.post(
                "/triage/alpha/propose",
                data={"filename": "note.md", "module": "02-work", "sub": ""},
            ),
            "patch_targets": [(main, "propose_classification")],
        },
        main.outbox_screen: {
            "request": lambda c: c.get("/outbox/alpha"),
            "patch_targets": [(main, "project_outbox")],
        },
        main.outbox_approve: {
            "request": lambda c: c.post(
                "/outbox/alpha/approve", data={"id": "irrelevant", "review_sha256": _UNBOUND_FINGERPRINT}
            ),
            "patch_targets": [(main, "approve")],
        },
        main.outbox_reject: {
            "request": lambda c: c.post(
                "/outbox/alpha/reject", data={"id": "irrelevant", "review_sha256": _UNBOUND_FINGERPRINT}
            ),
            "patch_targets": [(main, "reject")],
        },
        main.registry_products: {
            "request": lambda c: c.get("/registry/alpha/products"),
            "patch_targets": [(main, "products_for")],
        },
        main.registry_delete_preview: {
            "request": lambda c: c.post(
                "/registry/alpha/product/delete-preview", data={"slug": "widget"}
            ),
            "patch_targets": [(main, "propose_delete")],
        },
        main.registry_delete_execute: {
            "request": lambda c: c.post(
                "/registry/alpha/product/delete-execute", data={"id": "irrelevant", "review_sha256": _UNBOUND_FINGERPRINT}
            ),
            "patch_targets": [(main, "execute_delete")],
        },
        main.outbox_review_fragment: {
            "request": lambda c: c.get(
                "/outbox/alpha/review/20260815T090703-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
            ),
            "patch_targets": [(main, "project_outbox")],
        },
        main.registry_delete_review_fragment: {
            "request": lambda c: c.get(
                "/registry/alpha/product/review/"
                "20260815T090703-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
            ),
            "patch_targets": [(main, "get_delete_review")],
        },
        main.pulse: {"request": lambda c: c.get("/blocks/pulse"), "patch_targets": []},
    }


def test_route_totality_from_declared_catches(tmp_path, monkeypatch):
    """design §7 invariant 6 / §8 "Route-level totality", GENERALIZED (C4,
    I7 — review): enumerates every route `_registered_console_endpoints`
    finds, reads each one's OWN `__console_route__.catches` at test time,
    and injects exactly those classes through the per-route patch-target
    map above — so a route's declared family being widened is exercised the
    moment it lands, automatically, with no corresponding hand-written test
    case required.

    This does not replace the route-specific totality tests elsewhere in
    this file: those additionally prove WHICH of several call sites within
    one route answers WHICH member (`test_outbox_declared_family_never_
    reaches_the_global_fallback`'s two-pass split, specifically, which this
    sweep does not attempt to reproduce) and drive real-filesystem
    conditions this sweep cannot. Both are kept — this closes the
    orthogonal, previously-untested claim that declaration and defense
    never drift apart.
    """
    from tests.test_console_invariants import _registered_console_endpoints

    main = _load_main(tmp_path, monkeypatch, ENTITIES)
    plan = _route_totality_plan(main)

    endpoints = list(_registered_console_endpoints(main.app))
    assert len(endpoints) >= 11, f"the sweep saw only {endpoints}"
    missing = [
        getattr(ep, "__qualname__", repr(ep)) for ep in endpoints if ep not in plan
    ]
    assert missing == [], (
        f"route(s) registered but absent from the totality patch-target map: "
        f"{missing}"
    )

    reached = []
    original = main.app.exception_handlers[Exception]

    async def _spy(request, exc):
        reached.append(type(exc).__name__)
        return await original(request, exc)

    monkeypatch.setitem(main.app.exception_handlers, Exception, _spy)
    client = TestClient(main.app, raise_server_exceptions=False)

    for endpoint in endpoints:
        spec = plan[endpoint]
        catches = endpoint.__console_route__.catches
        originals = [
            (owner, attr, getattr(owner, attr)) for owner, attr in spec["patch_targets"]
        ]
        try:
            for exc_class in catches:
                for owner, attr in spec["patch_targets"]:
                    def _raise(*args, __exc=exc_class, **kwargs):
                        raise __exc("injected for route totality")

                    monkeypatch.setattr(owner, attr, _raise)
                    reached.clear()
                    spec["request"](client)
                    assert reached == [], (
                        f"{endpoint.__qualname__}: {exc_class.__name__} via "
                        f"{owner!r}.{attr} reached the global fallback"
                    )
        finally:
            # Restore before moving to the next route: several routes share
            # a patch target (`Vault.bundles`, in particular), and a
            # leftover patch would make the NEXT route's request fail for a
            # reason unrelated to what it declares.
            for owner, attr, original_value in originals:
                monkeypatch.setattr(owner, attr, original_value)

    # Sanity: the spy fires for something genuinely undeclared, so every
    # empty `reached` above is proof of routing rather than of a dead spy.
    def _boom(self):
        raise RuntimeError("undeclared by any route")

    monkeypatch.setattr(main.Vault, "bundles", _boom)
    reached.clear()
    client.get("/")
    assert reached == ["RuntimeError"]


def test_shell_and_triage_default_render_e_config_safely(
    tmp_path, monkeypatch
):
    """Neither route had ANY S6-era failure-injection test before this one.
    Both declare `_SIDEBAR_CATCHES`, i.e. `(DestinationRegistryError, EntityManifestError)`.

    Whatever answers the request, the OPERATOR must see a correct, safe
    E-CONFIG response with no raw exception text. That is what this test
    proves, unconditionally; the separate structural question of WHICH
    handler answers is `test_shell_and_triage_default_declared_family_
    never_reaches_the_global_fallback`, below.

    `EntityManifestError` is injected here via the same `Vault.bundles`
    vehicle used for `DestinationRegistryError`, rather than the function that
    raises it in production.

    An earlier revision of this docstring claimed design §11 records that no
    real request can trigger it, "since `catalog = build_catalog()` runs at
    module scope, before any handler exists". **Both halves are false**, and
    review measured them: `Scope.__init__` calls `EntityCatalog.load(root)` on
    every entity-scoped request, and `Vault.bundles()` re-resolves `_system`
    on every call — module-scope catalog construction is irrelevant to either.
    Deleting `entities.yaml` after startup yields a real `EntityManifestError`
    at 500 with `E-CONFIG` on four routes. It raises inside `entity_scope`
    dependency resolution, so no route-level `except` can answer it; a
    dedicated handler is the fix, and it is app code owned by the Task 8
    corrective. See the ledger's open-items entry.
    """
    from app.entities import EntityManifestError
    from app.vault import DestinationRegistryError

    main = _load_main(tmp_path, monkeypatch, ENTITIES)
    client = TestClient(main.app)

    for exc, expected_code in (
        (DestinationRegistryError("registries unreadable"), "E-CONFIG"),
        (EntityManifestError("manifest is invalid"), "E-CONFIG"),
    ):
        def _raise(self, __exc=exc):
            raise __exc

        monkeypatch.setattr(main.Vault, "bundles", _raise)
        shell_response = client.get("/")
        assert shell_response.status_code == 500
        assert expected_code in shell_response.text, shell_response.text
        assert 'role="alert"' in shell_response.text
        assert str(exc) not in shell_response.text

        triage_default_response = client.get("/triage")
        assert triage_default_response.status_code == 500
        assert expected_code in triage_default_response.text
        assert 'role="alert"' in triage_default_response.text
        assert str(exc) not in triage_default_response.text


def test_shell_and_triage_default_declared_family_never_reaches_the_global_fallback(
    tmp_path, monkeypatch
):
    """Both routes must answer their own declared family rather than relying
    on the global fallback (design §5: "relying on it is a failure rather than
    a silent default").

    Task 14 found this open — Task 10 added the declaration and no handler, so
    every request to `/` or `/triage` against a broken registry reached the
    fallback and `ServerErrorMiddleware` re-raised a logged traceback for a
    first-class described condition. That was the fourth and last occurrence of
    the gap Tasks 11-13 closed on every other route family in turn. It shipped
    first as an `xfail(strict=True)` recording the defect; the handler landed in
    the same task, so this is now a hard assertion.
    """
    from app.vault import DestinationRegistryError

    main = _load_main(tmp_path, monkeypatch, ENTITIES)
    reached = []
    original = main.app.exception_handlers[Exception]

    async def _spy(request, exc):
        reached.append(type(exc).__name__)
        return await original(request, exc)

    monkeypatch.setitem(main.app.exception_handlers, Exception, _spy)
    client = TestClient(main.app, raise_server_exceptions=False)

    def _raise(self):
        raise DestinationRegistryError("registries unreadable")

    monkeypatch.setattr(main.Vault, "bundles", _raise)
    client.get("/")
    client.get("/triage")
    assert reached == [], "design §5: the route itself must answer this, not the fallback"

    # M3 (review): a positive control, unlike this test's three siblings
    # (`test_triage_declared_family_never_reaches_the_global_fallback` etc.),
    # which each prove the spy itself fires for something genuinely
    # undeclared. Restore `Vault.bundles` first — an UNDECLARED exception
    # from it would double-fault through the C1 sidebar re-entrancy guard
    # (which only catches the declared `_SIDEBAR_CATCHES` family, by
    # design), so the vehicle here is deliberately something OUTSIDE the
    # `try` in each route body instead: `shell`'s own `datetime.now()` call
    # (the same vehicle `test_pulse_declares_no_family` uses) and
    # `triage_default`'s own `RedirectResponse(...)` call.
    monkeypatch.undo()
    monkeypatch.setitem(main.app.exception_handlers, Exception, _spy)

    class _BoomDatetime:
        @staticmethod
        def now(*args, **kwargs):
            raise RuntimeError("undeclared by shell")

    monkeypatch.setattr(main, "datetime", _BoomDatetime)
    reached.clear()
    client.get("/")
    assert reached == ["RuntimeError"]

    def _boom_redirect(*args, **kwargs):
        raise RuntimeError("undeclared by triage_default")

    monkeypatch.setattr(main, "RedirectResponse", _boom_redirect)
    reached.clear()
    client.get("/triage")
    assert reached == ["RuntimeError"]


def test_shell_and_triage_default_real_broken_archetypes_shows_e_config(
    tmp_path, monkeypatch
):
    """The real-filesystem condition for both routes' `DestinationRegistryError`
    member — no monkeypatching of application code, a genuinely malformed
    `archetypes.yaml` on disk. `bundles()` -> `resolve_flags` ->
    `self._archetypes` (`app/vault.py`) raises `DestinationRegistryError`
    directly when `modules:` is entirely absent — a hand-editing mistake
    distinct from the non-canonical `block:` value
    `test_triage_page_with_broken_registry_shows_e_config_page` drives (that
    one is invisible to `bundles()` itself; see this test's own investigation
    in the Task 14 report).

    A default `TestClient` (`raise_server_exceptions=True`) is the point:
    it would raise in-test if either route let this escape to the global
    fallback, so the real condition is proved answered by the route itself.
    """
    entities_yaml = """
version: "1.0"
entities:
  alpha: { label: Alpha, flags: [] }
"""
    # No `modules:` key at all — `Vault._archetypes` raises directly on
    # this shape, and every caller of `bundles()` reaches it unguarded.
    archetypes_yaml = 'version: "2.0"\n'
    write_vault(tmp_path, entities_yaml, archetypes_yaml)
    monkeypatch.setenv("ONEOS_VAULT", str(tmp_path))
    import app.main as main

    importlib.reload(main)
    client = TestClient(main.app)

    for response in (client.get("/"), client.get("/triage")):
        assert response.status_code == 500
        assert "E-CONFIG" in response.text
        assert 'role="alert"' in response.text


def test_render_console_error_sidebar_reentrancy_survives_an_undeclared_failure(
    tmp_path, monkeypatch
):
    """C1, isolated: the re-entrancy guard in `_render_console_error` must
    protect the sidebar rebuild regardless of what code the ORIGINAL error
    resolved to — not only when that code happens to be `E-CONFIG`.

    Vehicle: `triage`'s own `read_inbox` call raises a genuinely UNDECLARED
    `RuntimeError` (not in `_TRIAGE_CATCHES`), which escapes the route to
    the global fallback and describes to `E-UNKNOWN` — a code that is, by
    construction, never `"E-CONFIG"`. `Vault.bundles()` is SEPARATELY
    patched to raise `DestinationRegistryError`, so the fallback's own
    sidebar rebuild (triggered because the page is `E-UNKNOWN`, not
    `E-CONFIG`) fails too. Before C1, keying the guard on the ORIGINAL
    error's code meant this combination reached `Vault.bundles()`
    unguarded a second time and the second failure propagated out of the
    global fallback itself — an empty body. After C1, the guard catches on
    READABILITY, so the page still renders with `bundles=None` and the
    ORIGINAL `E-UNKNOWN` alert intact.
    """
    from app.vault import DestinationRegistryError

    main = _load_main(tmp_path, monkeypatch, ENTITIES)

    def _raise_undeclared(*args, **kwargs):
        raise RuntimeError("undeclared by triage")

    def _raise_sidebar(self):
        raise DestinationRegistryError("registries unreadable")

    monkeypatch.setattr(main, "read_inbox", _raise_undeclared)
    monkeypatch.setattr(main.Vault, "bundles", _raise_sidebar)

    response = TestClient(main.app, raise_server_exceptions=False).get(
        "/triage/alpha"
    )

    assert response.status_code == 500
    assert response.text != "", "completely empty body"
    assert 'role="alert"' in response.text
    assert "E-UNKNOWN" in response.text


def test_route_tuples_still_answer_the_leaf_redirect_without_the_dependency_handler(
    tmp_path, monkeypatch
):
    """The dependency-boundary handler must not become the only thing
    answering `SystemRegistryPathError`.

    Review found that adding `@app.exception_handler(EntityManifestError)`
    silently absorbs the whole family, so deleting `SystemRegistryPathError`
    from `_TRIAGE_CATCHES` / `_OUTBOX_CATCHES` / `_REGISTRY_PRODUCTS_CATCHES`
    — the three declarations a human ruling ordered added — left the entire
    suite **green**. Operator-visible output is identical either way, so this
    is a test-strength regression rather than a runtime defect; but it is
    exactly the declaration drift the ledger says must stay pinned, and the
    declaration-driven sweep cannot see it either, since that sweep injects
    only what a route already declares.

    So this test removes the app-level handler for its duration and asserts
    the routes still answer the **leaf** vehicle themselves — the condition
    they genuinely can and must handle, reached inside the body through
    `Vault.bundles()`. The whole-`_system`-directory vehicle is deliberately
    NOT used here: that one raises in dependency resolution and only the
    handler can answer it.
    """
    from app.entities import EntityManifestError

    main = _load_main(tmp_path, monkeypatch, ENTITIES)

    real_archetypes = tmp_path / "_system/archetypes.yaml"
    moved_aside = tmp_path / "archetypes-real-tuple-pin.yaml"
    moved_aside.write_bytes(real_archetypes.read_bytes())
    real_archetypes.unlink()
    real_archetypes.symlink_to(moved_aside)

    reached = []
    original = main.app.exception_handlers[Exception]

    async def _spy(request, exc):
        reached.append(type(exc).__name__)
        return await original(request, exc)

    monkeypatch.setitem(main.app.exception_handlers, Exception, _spy)
    monkeypatch.delitem(main.app.exception_handlers, EntityManifestError)
    client = TestClient(main.app, raise_server_exceptions=False)

    for url in ("/triage/alpha", "/outbox/alpha", "/registry/alpha/products"):
        reached.clear()
        response = client.get(url)
        assert response.status_code == 409, url
        assert "E-TAMPER" in response.text, url
        assert reached == [], (
            f"{url}: the route's OWN declared family must answer the leaf "
            "redirect, not the dependency-boundary handler"
        )

    # Sanity: the spy fires for something genuinely undeclared, so the empty
    # lists above are proof of routing rather than of a dead spy.
    def _boom(scope):
        raise RuntimeError("undeclared by this route")

    monkeypatch.setattr(main, "read_inbox", _boom)
    reached.clear()
    client.get("/triage/alpha")
    assert reached == ["RuntimeError"]


def test_real_post_startup_system_redirect_never_reaches_the_global_fallback(
    tmp_path, monkeypatch
):
    """C2' (S6 review round 2): `Vault.system_path` (app/vault.py) called
    `resolve_system_registry` directly, unlike `Scope.system_path`
    (app/scope.py), which converts `SystemRegistryPathError` (an
    `EntityManifestError`) to `RedirectedPathError` (a `CrossScopeError`).
    `Vault._archetypes` -> `_load_yaml` -> `system_path` could therefore
    raise the raw, unconverted type straight out of `bundles()` — undeclared
    by `_TRIAGE_CATCHES`, `_OUTBOX_CATCHES`, and `_REGISTRY_PRODUCTS_CATCHES`
    (none of which name `EntityManifestError`), reachable on every request
    because `bundles()` re-resolves `_system` every time it runs, not only
    once at import.

    Real filesystem, no monkeypatching of application code: `archetypes.yaml`
    is moved out of `_system` and symlinked back in place AFTER the app has
    already started — a realistic operator action (an editor or sync tool
    replacing a file with a symlink), not a hostile one. `entities.yaml`
    stays a real, resolvable file throughout, so `entity_scope`'s own
    `EntityCatalog.load` (a separate call to `resolve_system_registry`,
    unconverted, and NOT reachable from inside any route's own `try`/`except`
    at all since dependency resolution runs before the route body) is not
    exercised here — a whole-`_system`-directory redirection would ALSO
    break that unrelated, pre-existing path and is out of scope for this
    fix; see this test's docstring note below.
    """
    main = _load_main(tmp_path, monkeypatch, ENTITIES)

    real_archetypes = tmp_path / "_system/archetypes.yaml"
    moved_aside = tmp_path / "archetypes-real.yaml"
    moved_aside.write_bytes(real_archetypes.read_bytes())
    real_archetypes.unlink()
    real_archetypes.symlink_to(moved_aside)

    reached = []
    original = main.app.exception_handlers[Exception]

    async def _spy(request, exc):
        reached.append(type(exc).__name__)
        return await original(request, exc)

    monkeypatch.setitem(main.app.exception_handlers, Exception, _spy)
    client = TestClient(main.app, raise_server_exceptions=False)

    for url in (
        "/", "/triage", "/triage/alpha", "/outbox/alpha", "/registry/alpha/products",
    ):
        reached.clear()
        response = client.get(url)
        assert response.status_code == 409, (url, response.text)
        assert "E-TAMPER" in response.text, (url, response.text)
        assert 'role="alert"' in response.text, url
        assert reached == [], (
            f"{url}: reached the global fallback via {reached} — "
            "the route's own declared family must answer this"
        )


def _fs_snapshot(root: Path) -> list[tuple[str, str]]:
    """`(relative posix path, fingerprint)` for every entry under `root` —
    `symlink->target` for a symlink, `dir` for a directory, and a content
    hash for a regular file. Cheap, git-free proof that a read-only failure
    path writes nothing to disk; these fixtures are plain directories, not
    Git repositories, so `git status`/`git diff` fingerprints (as the
    committed-outcome tests elsewhere in this file use) do not apply here."""
    entries = []
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        if p.is_symlink():
            entries.append((rel, f"symlink->{os.readlink(p)}"))
        elif p.is_dir():
            entries.append((rel, "dir"))
        else:
            entries.append((rel, hashlib.sha256(p.read_bytes()).hexdigest()))
    return entries


def test_real_post_startup_system_directory_redirect_shows_e_tamper_everywhere(
    tmp_path, monkeypatch
):
    """Task 14's open item: `test_real_post_startup_system_redirect_never_
    reaches_the_global_fallback` above symlinks only `archetypes.yaml` back
    into place, and its own docstring concedes that leaves `entity_scope`'s
    `EntityCatalog.load` — a SEPARATE call to `resolve_system_registry`,
    unconverted — unexercised: "a whole-`_system`-directory redirection would
    ALSO break that unrelated, pre-existing path and is out of scope for this
    fix."

    This test is that whole-directory redirection. `resolve_system_registry`'s
    own root-identity check (`resolved_system != lexical_system`) then fires
    from every caller, including `Scope.__init__` -> `EntityCatalog.load`,
    which runs inside `entity_scope`'s DEPENDENCY resolution — before any
    route body executes. No route-level `except` can ever see it (the design
    §5 gap this task closes); only a dedicated
    `@app.exception_handler(EntityManifestError)` can, since
    `SystemRegistryPathError` is a subclass and `describe()` still resolves
    on the raised instance's own (narrower) class, not the handler's.

    Real filesystem, no monkeypatching of application code: `_system` is
    moved aside and symlinked back in place AFTER the app has already
    started — a realistic operator action, not a hostile one.

    `/` and `/triage` are driven too, and must keep answering via their own
    already-declared `_SIDEBAR_CATCHES` family (acceptance criterion 5: their
    pre-existing behaviour is unchanged by adding the new handler) rather
    than falling through to it.
    """
    main = _load_main(tmp_path, monkeypatch, ENTITIES)

    vault = Path(tmp_path)
    real_system = vault / "_system"
    moved_aside = vault / "_system-real"
    real_system.rename(moved_aside)
    real_system.symlink_to(moved_aside)

    reached = []
    original = main.app.exception_handlers[Exception]

    async def _spy(request, exc):
        reached.append(type(exc).__name__)
        return await original(request, exc)

    monkeypatch.setitem(main.app.exception_handlers, Exception, _spy)
    client = TestClient(main.app, raise_server_exceptions=False)

    before = _fs_snapshot(vault)

    for url in (
        "/", "/triage", "/triage/alpha", "/outbox/alpha", "/registry/alpha/products",
    ):
        reached.clear()
        response = client.get(url)
        assert response.status_code == 409, (url, response.text)
        assert response.text != "", (url, "completely empty body")
        # The exact raw message `resolve_system_registry` raises
        # ("system registry root is redirected") is passed as a marker so a
        # handler that leaked `str(exc)` into the rendered alert — rather
        # than only its curated, code-derived description — would be caught
        # here even though that particular raw message happens to carry no
        # path separator of its own.
        _assert_alert_discloses_nothing(
            response, "E-TAMPER", ["system registry root is redirected"]
        )
        assert reached == [], (
            f"{url}: reached the global fallback via {reached} — "
            "a dedicated handler (or the route's own declared family) must "
            "answer this without relying on it (design §5)"
        )

    assert _fs_snapshot(vault) == before, "a read-only failure must not mutate the vault"

    # Positive control (M3 pattern, as the sibling test above): restore the
    # real layout and prove the very same spy fires for something genuinely
    # undeclared, so the all-clear `reached == []` runs above are not simply
    # a spy that never fires at all.
    real_system.unlink()
    moved_aside.rename(real_system)

    def _raise_undeclared(*args, **kwargs):
        raise RuntimeError("undeclared by triage")

    monkeypatch.setattr(main, "read_inbox", _raise_undeclared)
    reached.clear()
    client.get("/triage/alpha")
    assert reached == ["RuntimeError"]


def test_real_post_startup_entities_yaml_missing_shows_e_config_via_entity_scope(
    tmp_path, monkeypatch
):
    """The second half of Task 14's open item: `entities.yaml` itself
    deleted (rather than `_system` redirected) after startup.
    `EntityCatalog.load` (`app/entities.py`) raises `EntityManifestError`
    directly — "entities manifest is missing" — from inside `entity_scope`'s
    dependency resolution on every entity-scoped route, unreachable by any
    route's own `except` for the identical reason as the redirect case above.

    `/` is driven too, and is pinned at 200: `shell` builds its sidebar from
    the module-scope `catalog` global (`build_catalog()`, evaluated once at
    import time) via `Vault(catalog).bundles()`, which never re-reads
    `entities.yaml` — so this condition does not reach it at all. That is
    long-standing, unrelated behaviour, not a target of this fix; asserting
    it here pins that the fix does not accidentally change it.

    `/triage` is driven through `TestClient`'s default `follow_redirects=True`:
    `triage_default` still redirects (307) to `/triage/alpha` using that same
    stale catalog, and the client follows the redirect into the identical
    `entity_scope` failure `/triage/alpha` shows directly — so `/triage`
    surfaces the same `E-CONFIG` outcome as the other three entity-scoped
    routes, unlike `/`.
    """
    main = _load_main(tmp_path, monkeypatch, ENTITIES)

    vault = Path(tmp_path)
    (vault / "_system/entities.yaml").unlink()

    reached = []
    original = main.app.exception_handlers[Exception]

    async def _spy(request, exc):
        reached.append(type(exc).__name__)
        return await original(request, exc)

    monkeypatch.setitem(main.app.exception_handlers, Exception, _spy)
    client = TestClient(main.app, raise_server_exceptions=False)

    before = _fs_snapshot(vault)

    reached.clear()
    root_response = client.get("/")
    assert root_response.status_code == 200, root_response.text
    assert 'role="alert"' not in root_response.text
    assert reached == []

    for url in ("/triage", "/triage/alpha", "/outbox/alpha", "/registry/alpha/products"):
        reached.clear()
        response = client.get(url)
        assert response.status_code == 500, (url, response.text)
        assert response.text != "", (url, "completely empty body")
        # Same rationale as the redirect test's marker above: the exact raw
        # message `EntityCatalog.load` raises for a missing manifest.
        _assert_alert_discloses_nothing(
            response, "E-CONFIG", ["entities manifest is missing"]
        )
        assert reached == [], (
            f"{url}: reached the global fallback via {reached} — "
            "a dedicated handler must answer this without relying on it "
            "(design §5)"
        )

    assert _fs_snapshot(vault) == before, "a read-only failure must not mutate the vault"

    # Positive control: `/` never touches `entity_scope`, so its own
    # undeclared-failure vehicle (`shell`'s `datetime.now()` call, as
    # `test_shell_and_triage_default_declared_family_never_reaches_the_
    # global_fallback` uses) is independent of the deleted manifest and
    # proves the spy fires for something genuinely undeclared.
    class _BoomDatetime:
        @staticmethod
        def now(*args, **kwargs):
            raise RuntimeError("undeclared by shell")

    monkeypatch.setattr(main, "datetime", _BoomDatetime)
    reached.clear()
    client.get("/")
    assert reached == ["RuntimeError"]


def test_pulse_declares_no_family(tmp_path, monkeypatch):
    """`pulse` is the one route with nothing to inject: `catches=()` — no
    registry read, no path resolution, no domain family (main.py's own
    comment on the declaration). This pins that the declaration really is
    empty (so it correctly contributes nothing to the disclosure sweep or
    the `no-none` state-proof cell above) and that an undeclared exception
    still reaches the global fallback rather than being silently absorbed.
    """
    main = _load_main(tmp_path, monkeypatch, ENTITIES)
    assert main.pulse.__console_route__.catches == ()

    # FastAPI captures the endpoint FUNCTION at route registration, so
    # patching `main.pulse` itself would not change what the router calls.
    # `datetime.now()` is the only thing the body touches, and `main.py`
    # binds the class itself (`from datetime import datetime`), so a fake
    # class with a raising `now()` is the vehicle.
    class _BoomDatetime:
        @staticmethod
        def now(*args, **kwargs):
            raise RuntimeError("undeclared by pulse")

    monkeypatch.setattr(main, "datetime", _BoomDatetime)

    client = TestClient(main.app, raise_server_exceptions=False)
    response = client.get("/blocks/pulse")
    assert response.status_code == 500
    assert "E-UNKNOWN" in response.text


def test_propose_declared_family_never_reaches_the_global_fallback(
    tmp_path, monkeypatch
):
    """`propose` had no totality test before this one — only its
    post-persistence branch (`preview_diff` raising `OutboxError`) was ever
    injected. Its declared family is `(OutboxError, DestinationError,
    CrossScopeError, DestinationRegistryError)`; this covers all four via
    `propose_classification`, the pre-persistence call.
    """
    from app.destinations import MissingDestination
    from app.outbox import OutboxError
    from app.scope import RedirectedPathError
    from app.vault import DestinationRegistryError

    main, client = _propose_client(tmp_path, monkeypatch)
    reached = []
    original = main.app.exception_handlers[Exception]

    async def _spy(request, exc):
        reached.append(type(exc).__name__)
        return await original(request, exc)

    monkeypatch.setitem(main.app.exception_handlers, Exception, _spy)

    for exc, expected_code in (
        (OutboxError("outbox is otherwise broken"), "E-INVALID"),
        (MissingDestination("destination is unresolved"), "E-DEST"),
        (RedirectedPathError("redirected source"), "E-TAMPER"),
        (DestinationRegistryError("registries unreadable"), "E-CONFIG"),
    ):
        def _raise(*args, __exc=exc, **kwargs):
            raise __exc

        monkeypatch.setattr(main, "propose_classification", _raise)
        response = client.post(
            "/triage/alpha/propose",
            data={"filename": "note.md", "module": "02-work", "sub": ""},
        )
        assert expected_code in response.text, response.text
        assert reached == [], f"propose: fallback reached for {expected_code}"

    def _boom(*args, **kwargs):
        raise RuntimeError("undeclared by propose")

    monkeypatch.setattr(main, "propose_classification", _boom)
    TestClient(main.app, raise_server_exceptions=False).post(
        "/triage/alpha/propose",
        data={"filename": "note.md", "module": "02-work", "sub": ""},
    )
    assert reached == ["RuntimeError"]


def test_propose_real_symlinked_receipt_shows_e_tamper(tmp_path, monkeypatch):
    """The real-filesystem condition for `propose`'s `DestinationError`
    member: an inbox receipt that is genuinely a symlink on disk, reached
    through a normal POST with no monkeypatching of application code.
    `propose` never calls `read_inbox` — it resolves the item path directly
    (`scope.resolve("00-inbox", "active") / filename`) and hands it to
    `propose_classification`, which is a different call path than `triage`'s
    own real-symlink test uses (that one bypasses `read_inbox`'s list-time
    guard deliberately; this one does not need to, because `propose` never
    goes through `read_inbox` at all).

    I4 (review): an earlier revision of this docstring claimed this drives
    `propose`'s `CrossScopeError` member. Measured, it does not: the raised
    type is `destinations.RedirectedSourceLeaf`
    (`app/destinations.py` — a `DestinationError` subclass, not a
    `CrossScopeError` one), thrown while `propose_classification` resolves
    the classification destination for a symlinked source leaf. `E-TAMPER`
    is correct either way (both subtypes map to it), but the *member*
    exercised was mislabeled — see
    `test_propose_real_symlinked_entity_root_shows_e_scope` below for
    `propose`'s actual real `CrossScopeError` condition, which this file had
    none of until that test.
    """
    main, client = _propose_client(tmp_path, monkeypatch)
    active = tmp_path / "alpha/00-inbox/active"
    target = tmp_path / "elsewhere.md"
    target.write_text("outside the vault's inbox lifecycle\n", encoding="utf-8")
    receipt = active / "symlinked-receipt.md"
    receipt.symlink_to(target)

    reached = []
    original = main.app.exception_handlers[Exception]

    async def _spy(request, exc):
        reached.append(type(exc).__name__)
        return await original(request, exc)

    monkeypatch.setitem(main.app.exception_handlers, Exception, _spy)

    response = TestClient(main.app).post(
        "/triage/alpha/propose",
        data={"filename": "symlinked-receipt.md", "module": "02-work", "sub": ""},
    )

    from app.console_errors import _CODES

    assert reached == [], f"fallback reached: {reached}"
    # E-TAMPER is `attention` severity, so Rule 5 gives it its own page
    # status (409) rather than 200, even on this fragment-only route.
    assert response.status_code == _CODES["E-TAMPER"].page_status
    assert 'role="alert"' in response.text
    assert "E-TAMPER" in response.text
    assert "elsewhere.md" not in response.text
    assert "symlinked-receipt.md" not in response.text


def test_propose_real_symlinked_entity_root_shows_e_scope(tmp_path, monkeypatch):
    """`propose`'s actual real-filesystem `CrossScopeError` condition (I4,
    review): the test above was mislabeled, and once corrected `propose` had
    NO real condition proving that declared member at all. `propose`'s own
    first statement is `scope.resolve("00-inbox", "active") / filename` —
    `Scope.resolve` raises `RedirectedPathError` (a `CrossScopeError`) when
    the bound entity's root itself is a symlink (`if base != anchor: raise
    ...`), reached before `propose_classification` is ever called. No
    application code is monkeypatched.

    `RedirectedPathError` maps to `E-TAMPER` by `mro` (the class map is
    exact-vs-mro per class, not per code), the same code the mislabeled test
    above produces — `Scope.resolve` does not distinguish WHERE along an
    entity-scoped path the redirection occurred, so both symlink shapes
    describe identically. What differs is the declared FAMILY member
    exercised: this is genuinely `CrossScopeError`, not `DestinationError`.
    """
    main = _load_main(tmp_path, monkeypatch, ENTITIES)
    outside = tmp_path.parent / f"{tmp_path.name}-outside-alpha-root-marker"
    outside.mkdir()
    (outside / "00-inbox" / "active").mkdir(parents=True)
    (outside / "00-inbox" / "active" / "note.md").write_text(
        "---\ntitle: t\nsub: triage\n---\nbody\n", encoding="utf-8",
    )
    (outside / "02-work" / "active").mkdir(parents=True)
    import shutil

    shutil.rmtree(tmp_path / "alpha")
    (tmp_path / "alpha").symlink_to(outside)

    reached = []
    original = main.app.exception_handlers[Exception]

    async def _spy(request, exc):
        reached.append(type(exc).__name__)
        return await original(request, exc)

    monkeypatch.setitem(main.app.exception_handlers, Exception, _spy)

    response = TestClient(main.app).post(
        "/triage/alpha/propose",
        data={"filename": "note.md", "module": "02-work", "sub": ""},
    )

    from app.console_errors import _CODES

    assert reached == [], f"fallback reached: {reached}"
    assert response.status_code == _CODES["E-TAMPER"].page_status
    assert 'role="alert"' in response.text
    assert "E-TAMPER" in response.text
    assert "outside-alpha-root-marker" not in response.text


def test_propose_real_symlinked_inbox_active_shows_e_tamper(tmp_path, monkeypatch):
    """The residual PR #15 must-fix-6 site (Task 8 corrective ledger entry):
    `propose`'s very first statement is
    `item_path = scope.resolve("00-inbox", "active") / filename`, with no
    local anchor path built at all — unlike `_require_real_directory`'s
    established pattern. This is a REAL symlink at the inbox LEAF, the
    `00-inbox/active` directory itself, not the entity root (that condition
    is `test_propose_real_symlinked_entity_root_shows_e_scope` above, which
    is already `E-TAMPER` because `Scope.resolve`'s own `base != anchor`
    check catches a symlinked entity root before ever reaching the subpath
    join).

    Here the entity root is real; only `00-inbox/active` redirects outside
    it. `Scope.resolve("00-inbox", "active")` joins-then-resolves the whole
    candidate and only then checks `is_relative_to(base)`, so a symlinked
    leaf component surfaces exactly like an ordinary out-of-scope subpath —
    `OutOfScopeError` (-> E-SCOPE) — even though the cause is a redirection,
    not a request for a genuinely absent or foreign path. Design §2 requires
    the redirection finding to be `E-TAMPER`. No application code is
    monkeypatched; only a real symlink and a real POST.
    """
    main = _load_main(tmp_path, monkeypatch, ENTITIES)
    outside = tmp_path.parent / f"{tmp_path.name}-outside-inbox-active-marker"
    outside.mkdir()
    (outside / "note.md").write_text(
        "---\ntitle: t\nsub: triage\n---\nbody\n", encoding="utf-8",
    )
    (tmp_path / "alpha/00-inbox").mkdir(parents=True)
    (tmp_path / "alpha/00-inbox/active").symlink_to(outside)
    (tmp_path / "alpha/02-work/active").mkdir(parents=True, exist_ok=True)

    reached = []
    original = main.app.exception_handlers[Exception]

    async def _spy(request, exc):
        reached.append(type(exc).__name__)
        return await original(request, exc)

    monkeypatch.setitem(main.app.exception_handlers, Exception, _spy)

    response = TestClient(main.app).post(
        "/triage/alpha/propose",
        data={"filename": "note.md", "module": "02-work", "sub": ""},
    )

    from app.console_errors import _CODES

    assert reached == [], f"fallback reached: {reached}"
    assert response.status_code == _CODES["E-TAMPER"].page_status
    assert 'role="alert"' in response.text
    assert "E-TAMPER" in response.text
    assert "E-SCOPE" not in response.text
    assert "outside-inbox-active-marker" not in response.text


def test_outbox_reject_real_corrupt_sibling_shows_e_unreadable(tmp_path, monkeypatch):
    """The real-filesystem condition for `outbox_reject`, mirroring
    `test_outbox_blocked_action_renders_one_alert_not_two` (`outbox_approve`'s
    own real condition). `get_proposal` — which both `approve` and `reject`
    call first — runs the strict `load_proposals`, so a corrupt sibling
    record poisons `reject` on a perfectly valid id too, with no
    monkeypatching of any application code at all.
    """
    from app.console_errors import _CODES

    main, client, valid_id = _outbox_proposal_client(tmp_path, monkeypatch)
    (tmp_path / "alpha/outbox/unreadable-reject-marker.yaml").write_text(
        "{ not: [valid, yaml", encoding="utf-8",
    )

    reached = []
    original = main.app.exception_handlers[Exception]

    async def _spy(request, exc):
        reached.append(type(exc).__name__)
        return await original(request, exc)

    monkeypatch.setitem(main.app.exception_handlers, Exception, _spy)

    response = TestClient(main.app).post(
        "/outbox/alpha/reject",
        data={"id": valid_id, "review_sha256": _UNBOUND_FINGERPRINT},
    )

    assert reached == [], f"fallback reached: {reached}"
    # E-UNREADABLE is `attention` severity, so the fragment status follows
    # its own page status (422) rather than 200 (design §5).
    assert response.status_code == _CODES["E-UNREADABLE"].page_status
    assert 'role="alert"' in response.text
    assert "E-UNREADABLE" in response.text
    assert "unreadable-reject-marker" not in response.text
    # The still-pending valid proposal was never touched by the refused
    # reject — it is still on disk.
    assert (tmp_path / "alpha/outbox" / f"{valid_id}.yaml").exists()


def test_outbox_screen_real_symlinked_outbox_shows_e_tamper(tmp_path, monkeypatch):
    """The real-filesystem condition for `outbox_screen` (I6, review):
    unlike every other route in this file, it had none — a CORRUPT
    individual proposal RECORD reaches `project_outbox`'s phase-1 handling
    and yields a `blocked` listing at 200
    (`test_outbox_screen_renders_projection_blocked_listing`), never the
    route's own `except`, so that clause had no real proof it ever runs.

    A genuinely symlinked `alpha/outbox` DIRECTORY does: `project_outbox`
    resolves the outbox directory itself through the bound scope before it
    can glob anything inside it, and a symlink there raises before any
    per-record handling even starts. No application code is monkeypatched.

    C2 (S6 review, bounded fix pass): this test previously asserted
    `E-SCOPE` — `app/outbox.py::_require_outbox_path` called
    `scope.resolve("outbox")` (assigning its result) before classifying
    `lexical_outbox.is_symlink()`, so a redirected outbox raised
    `OutOfScopeError` before its own symlink check ever ran. Fixed to check
    the lexical symlink first, matching the correct order already used a
    few lines below in that same function; a redirected outbox is now
    `RedirectedPathError` -> `E-TAMPER`, the tier design §2 requires for a
    redirection finding. `E-TAMPER` is `attention` severity, so Rule 5
    gives it its own page status (409) even on this full-page route.
    """
    from app.console_errors import _CODES

    main, client, proposal_id = _outbox_proposal_client(tmp_path, monkeypatch)
    outbox_dir = tmp_path / "alpha/outbox"
    outside = tmp_path.parent / f"{tmp_path.name}-outside-outbox-screen-marker"
    outside.mkdir()
    import shutil

    shutil.rmtree(outbox_dir)
    outbox_dir.symlink_to(outside)

    reached = []
    original = main.app.exception_handlers[Exception]

    async def _spy(request, exc):
        reached.append(type(exc).__name__)
        return await original(request, exc)

    monkeypatch.setitem(main.app.exception_handlers, Exception, _spy)

    response = TestClient(main.app, raise_server_exceptions=False).get(
        "/outbox/alpha"
    )

    assert reached == [], f"fallback reached: {reached}"
    assert response.status_code == _CODES["E-TAMPER"].page_status
    assert 'role="alert"' in response.text
    assert "E-TAMPER" in response.text
    assert "outside-outbox-screen-marker" not in response.text


def test_registry_delete_preview_real_symlinked_outbox_shows_e_tamper(
    tmp_path, monkeypatch
):
    """The real-filesystem condition for `registry_delete_preview`'s
    `CrossScopeError` member.

    `reference_count` does NOT read `products.yaml` at all — it counts
    front-matter, workspace, and `books.db` references (`app/registry.py`),
    so the symlinked-`products.yaml` vehicle
    `test_registry_products_real_symlinked_registry_shows_e_tamper` uses has
    NO effect on this route (measured directly: a symlinked `products.yaml`
    against this route still returns 200 with the delete-impact fragment).
    The real vehicle is `propose_delete`'s own `_delete_proposal_path` call
    (`app/registry.py`), unconditional and before anything is written: it
    re-resolves the entity's `outbox/` directory and raises when a symlink
    makes the resolved path disagree with the anchored one. A genuinely
    symlinked `alpha/outbox` reaches this for real.

    C2 (S6 review, bounded fix pass): this test previously asserted
    `E-SCOPE` — `_delete_proposal_path` called `scope.resolve("outbox")`
    with no lexical symlink check at all, so a redirected outbox raised
    `OutOfScopeError` before any redirection-specific check ran. Fixed to
    classify the lexical `outbox` symlink first, mirroring
    `app/outbox.py::_require_outbox_path`; a redirected outbox is now
    `RedirectedPathError` -> `E-TAMPER`, the tier design §2 requires for a
    redirection finding, still the same `CrossScopeError` family member the
    route declares, just a different refined subtype. `E-TAMPER` is
    `attention` severity, so Rule 5 gives it its own page status (409)
    rather than 200, even on this fragment-only route.

    This condition also fires strictly BEFORE `propose_delete` opens the
    proposal file, so — unlike the injected `no-proposal-written` cell in
    the state-proof matrix — this is a real-filesystem instance of
    `(committed=no, persistence=none)`: nothing is written at all.
    """
    from app.console_errors import _CODES

    main, client, slug = _registry_client(tmp_path, monkeypatch)
    outbox_dir = tmp_path / "alpha/outbox"
    outbox_before = set(outbox_dir.glob("*.yaml"))

    outside = tmp_path.parent / f"{tmp_path.name}-outside-outbox-preview-marker"
    outside.mkdir()
    # `alpha/outbox` is not scaffolded by any fixture (design: outbox/ and
    # staging/ are `system`, absent from archetypes.yaml `modules:`) and
    # nothing has written to it yet in this test, so it does not exist yet.
    if outbox_dir.exists():
        import shutil

        shutil.rmtree(outbox_dir)
    outbox_dir.symlink_to(outside)

    reached = []
    original = main.app.exception_handlers[Exception]

    async def _spy(request, exc):
        reached.append(type(exc).__name__)
        return await original(request, exc)

    monkeypatch.setitem(main.app.exception_handlers, Exception, _spy)

    response = TestClient(main.app).post(
        "/registry/alpha/product/delete-preview", data={"slug": slug}
    )

    assert reached == [], f"fallback reached: {reached}"
    assert response.status_code == _CODES["E-TAMPER"].page_status
    assert 'role="alert"' in response.text
    assert "E-TAMPER" in response.text
    assert "outside-outbox-preview-marker" not in response.text

    # persistence=none: nothing was written anywhere, including the real
    # (redirected) target directory.
    assert list(outside.glob("*.yaml")) == []
    assert outbox_before == set()


# --- S7 Task 3: the fingerprint travels from the rendered row to the service --
#
# Only the transport is proved here. The same-screen changed-review
# presentation is Task 5's; these tests exist so no commit ships a route
# that requires a fingerprint its own buttons do not send.


def test_outbox_action_buttons_carry_id_and_fingerprint_through_tojson(
    tmp_path, monkeypatch
):
    main, client, proposal_id = _outbox_proposal_client(tmp_path, monkeypatch)
    fingerprint = _outbox_fingerprint(tmp_path, proposal_id)

    from tests.test_app import HxValsParser

    body = client.get("/outbox/alpha").text

    assert "/outbox/alpha/approve" in body
    assert "/outbox/alpha/reject" in body
    parser = HxValsParser()
    parser.feed(body)
    assert len(parser.values) == 2
    for raw in parser.values:
        assert json.loads(raw) == {
            "id": proposal_id,
            "review_sha256": fingerprint,
        }
    # The fingerprint is the one this row was actually rendered from.
    assert fingerprint == hashlib.sha256(
        (tmp_path / "alpha/outbox" / f"{proposal_id}.yaml").read_bytes()
    ).hexdigest()


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_outbox_action_route_requires_a_fingerprint(tmp_path, monkeypatch, action):
    """A missing field is an invalid request, never an id-only fallback."""
    main, client, proposal_id = _outbox_proposal_client(tmp_path, monkeypatch)

    source_before = (tmp_path / "alpha/00-inbox/active/marker.md").read_bytes()

    # Deliberately id-only: the field is omitted, not merely wrong.
    response = client.post(f"/outbox/alpha/{action}", data={"id": proposal_id})

    # An invalid request, described as one. A fragment refusal renders at 200
    # (design §5); what matters is that it is E-REQUEST and nothing moved.
    assert "E-REQUEST" in response.text
    assert 'role="alert"' in response.text
    assert (tmp_path / "alpha/outbox" / f"{proposal_id}.yaml").exists()
    assert (tmp_path / "alpha/00-inbox/active/marker.md").read_bytes() == source_before
    assert not (tmp_path / "alpha/02-work/active/marker.md").exists()


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_outbox_action_route_passes_the_submitted_fingerprint_unchanged(
    tmp_path, monkeypatch, action
):
    main, client, proposal_id = _outbox_proposal_client(tmp_path, monkeypatch)
    submitted = "b" * 64
    seen = {}

    def _spy(scope, id, review_sha256):
        seen["id"] = id
        seen["review_sha256"] = review_sha256
        raise RuntimeError("stop before mutating")

    monkeypatch.setattr(main, action, _spy)
    with pytest.raises(RuntimeError):
        client.post(
            f"/outbox/alpha/{action}",
            data={"id": proposal_id, "review_sha256": submitted},
        )

    # Byte-for-byte: the route neither recomputes nor normalises it.
    assert seen == {"id": proposal_id, "review_sha256": submitted}


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_outbox_action_route_never_recomputes_the_fingerprint(
    tmp_path, monkeypatch, action
):
    """A route that read the proposal to derive its own fingerprint would
    rebind the action to whatever is on disk now — exactly the defect S7
    exists to close."""
    main, client, proposal_id = _outbox_proposal_client(tmp_path, monkeypatch)
    real_fingerprint = _outbox_fingerprint(tmp_path, proposal_id)
    stale = "c" * 64
    seen = {}

    def _spy(scope, id, review_sha256):
        seen["review_sha256"] = review_sha256
        raise RuntimeError("stop before mutating")

    monkeypatch.setattr(main, action, _spy)
    with pytest.raises(RuntimeError):
        client.post(
            f"/outbox/alpha/{action}",
            data={"id": proposal_id, "review_sha256": stale},
        )

    assert seen["review_sha256"] == stale
    assert seen["review_sha256"] != real_fingerprint


def test_no_outbox_action_row_renders_a_button_without_a_fingerprint(
    tmp_path, monkeypatch
):
    """An unreadable record blocks the listing: no buttons, and therefore no
    fingerprints, anywhere in the fragment."""
    main, client, proposal_id = _outbox_proposal_client(tmp_path, monkeypatch)
    (tmp_path / "alpha/outbox/unreadable-marker.yaml").write_text(
        "{ not: [valid, yaml", encoding="utf-8",
    )

    body = client.get("/outbox/alpha").text

    assert "hx-post=\"/outbox/alpha/approve\"" not in body
    assert "hx-post=\"/outbox/alpha/reject\"" not in body
    assert "review_sha256" not in body


# --- S7 Task 3 review: the routes answer the review outcomes ----------------


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_outbox_action_with_a_stale_fingerprint_shows_the_approved_refusal(
    tmp_path, monkeypatch, action
):
    """P1 (review): a stale fingerprint is a declared outcome of these
    routes, not something that escapes to the global fallback."""
    from app.console_errors import _CODES

    main, client, proposal_id = _outbox_proposal_client(tmp_path, monkeypatch)
    proposal_path = tmp_path / "alpha/outbox" / f"{proposal_id}.yaml"
    stale = _action_data(tmp_path, proposal_id)

    # The record is rewritten under the same id after the review was issued.
    proposal_path.write_bytes(proposal_path.read_bytes() + b"# rewritten\n")
    proposal_before = proposal_path.read_bytes()
    source_before = (tmp_path / "alpha/00-inbox/active/marker.md").read_bytes()

    reached = []
    original = main.app.exception_handlers[Exception]

    async def _spy(request, exc):
        reached.append(type(exc).__name__)
        return await original(request, exc)

    monkeypatch.setitem(main.app.exception_handlers, Exception, _spy)

    response = client.post(f"/outbox/alpha/{action}", data=stale)

    assert reached == [], f"fallback reached: {reached}"
    assert 'role="alert"' in response.text
    assert "E-REVIEW" in response.text
    assert _CODES["E-REVIEW"].message in response.text
    # Nothing changed, and the current record is preserved for comparison.
    assert proposal_path.read_bytes() == proposal_before
    assert (tmp_path / "alpha/00-inbox/active/marker.md").read_bytes() == source_before
    assert not (tmp_path / "alpha/02-work/active/marker.md").exists()


@pytest.mark.parametrize("action", ["approve", "reject"])
@pytest.mark.parametrize("bad", ["not-a-hash", "", "A" * 64, "0" * 63])
def test_outbox_action_with_a_malformed_fingerprint_is_an_invalid_request(
    tmp_path, monkeypatch, action, bad
):
    main, client, proposal_id = _outbox_proposal_client(tmp_path, monkeypatch)
    proposal_path = tmp_path / "alpha/outbox" / f"{proposal_id}.yaml"
    proposal_before = proposal_path.read_bytes()

    reached = []
    original = main.app.exception_handlers[Exception]

    async def _spy(request, exc):
        reached.append(type(exc).__name__)
        return await original(request, exc)

    monkeypatch.setitem(main.app.exception_handlers, Exception, _spy)

    response = client.post(
        f"/outbox/alpha/{action}", data={"id": proposal_id, "review_sha256": bad}
    )

    assert reached == [], f"fallback reached: {reached}"
    assert "E-REQUEST" in response.text
    assert proposal_path.read_bytes() == proposal_before
    assert not (tmp_path / "alpha/02-work/active/marker.md").exists()


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_a_stale_refusal_re_renders_the_current_review_with_a_fresh_fingerprint(
    tmp_path, monkeypatch, action
):
    """The operator is left able to act again on what is actually stored.

    Task 5 owns the side-by-side comparison of the old and current values;
    what must already hold is that the refusal does not strand the operator
    with a fingerprint that can never match again.
    """
    from tests.test_app import HxValsParser

    main, client, proposal_id = _outbox_proposal_client(tmp_path, monkeypatch)
    proposal_path = tmp_path / "alpha/outbox" / f"{proposal_id}.yaml"
    stale = _action_data(tmp_path, proposal_id)

    proposal_path.write_bytes(proposal_path.read_bytes() + b"# rewritten\n")
    current = _outbox_fingerprint(tmp_path, proposal_id)
    assert current != stale["review_sha256"]

    response = client.post(f"/outbox/alpha/{action}", data=stale)

    parser = HxValsParser()
    parser.feed(response.text)
    assert parser.values, "the refusal left no actionable review on screen"
    for raw in parser.values:
        values = json.loads(raw)
        assert values["id"] == proposal_id
        # Only the current fingerprint is live; the stale one is never
        # re-offered as though it could still act.
        assert values["review_sha256"] == current
        assert values["review_sha256"] != stale["review_sha256"]


def test_a_second_rewrite_invalidates_the_fingerprint_the_refusal_just_issued(
    tmp_path, monkeypatch
):
    """Approved decision 4: every subsequent rewrite invalidates the
    controls issued for the previous version."""
    from tests.test_app import HxValsParser

    main, client, proposal_id = _outbox_proposal_client(tmp_path, monkeypatch)
    proposal_path = tmp_path / "alpha/outbox" / f"{proposal_id}.yaml"
    stale = _action_data(tmp_path, proposal_id)

    proposal_path.write_bytes(proposal_path.read_bytes() + b"# first rewrite\n")
    first_refusal = client.post("/outbox/alpha/approve", data=stale)
    parser = HxValsParser()
    parser.feed(first_refusal.text)
    reissued = json.loads(parser.values[0])["review_sha256"]

    # A second rewrite lands before the operator reconfirms.
    proposal_path.write_bytes(proposal_path.read_bytes() + b"# second rewrite\n")
    second_refusal = client.post(
        "/outbox/alpha/approve",
        data={"id": proposal_id, "review_sha256": reissued},
    )

    assert "E-REVIEW" in second_refusal.text
    assert not (tmp_path / "alpha/02-work/active/marker.md").exists()
    parser = HxValsParser()
    parser.feed(second_refusal.text)
    assert json.loads(parser.values[0])["review_sha256"] != reissued


# --- S7 Task 4: the delete fingerprint travels and binds --------------------


def _delete_action_data(tmp_path, proposal, entity="alpha"):
    """Form data for delete-execute, carrying the proposal's own fingerprint
    exactly as the rendered button would. Falls back to a well-formed but
    unbound value when the record cannot be reviewed at all — those tests
    are about refusals reached before any comparison."""
    from app.registry import get_delete_review
    from app.scope import Scope

    try:
        fingerprint = get_delete_review(
            Scope(Path(tmp_path), entity), proposal.id
        ).sha256
    except Exception:
        fingerprint = _UNBOUND_FINGERPRINT
    return {"id": proposal.id, "review_sha256": fingerprint}


def _delete_preview_values(main, client, tmp_path, slug):
    """Render the impact fragment and read back the values its button sends."""
    from tests.test_app import HxValsParser

    response = client.post(
        "/registry/alpha/product/delete-preview", data={"slug": slug}
    )
    parser = HxValsParser()
    parser.feed(response.text)
    assert len(parser.values) == 1, response.text
    return json.loads(parser.values[0])


def test_delete_impact_carries_id_and_fingerprint_through_tojson(
    tmp_path, monkeypatch
):
    main, client, slug = _registry_client(tmp_path, monkeypatch)

    values = _delete_preview_values(main, client, tmp_path, slug)

    assert set(values) == {"id", "review_sha256"}
    proposal = tmp_path / "alpha/outbox" / f"{values['id']}.yaml"
    assert values["review_sha256"] == hashlib.sha256(
        proposal.read_bytes()
    ).hexdigest()


def test_delete_execute_requires_a_fingerprint(tmp_path, monkeypatch):
    main, client, slug = _registry_client(tmp_path, monkeypatch)
    values = _delete_preview_values(main, client, tmp_path, slug)
    registry_file = tmp_path / "_system/products.yaml"
    before = registry_file.read_bytes()

    response = client.post(
        "/registry/alpha/product/delete-execute", data={"id": values["id"]}
    )

    assert "E-REQUEST" in response.text
    assert registry_file.read_bytes() == before


def test_delete_execute_with_a_stale_fingerprint_shows_the_approved_refusal(
    tmp_path, monkeypatch
):
    from app.console_errors import _CODES

    main, client, slug = _registry_client(tmp_path, monkeypatch)
    stale = _delete_preview_values(main, client, tmp_path, slug)
    proposal = tmp_path / "alpha/outbox" / f"{stale['id']}.yaml"
    proposal.write_bytes(proposal.read_bytes() + b"# rewritten\n")
    registry_file = tmp_path / "_system/products.yaml"
    before = registry_file.read_bytes()

    response = client.post("/registry/alpha/product/delete-execute", data=stale)

    assert "E-REVIEW" in response.text
    assert _CODES["E-REVIEW"].message in response.text
    assert registry_file.read_bytes() == before
    assert proposal.exists()


def test_delete_execute_passes_the_submitted_fingerprint_unchanged(
    tmp_path, monkeypatch
):
    main, client, slug = _registry_client(tmp_path, monkeypatch)
    values = _delete_preview_values(main, client, tmp_path, slug)
    submitted = "d" * 64
    seen = {}

    def _spy(scope, id, review_sha256):
        seen["id"] = id
        seen["review_sha256"] = review_sha256
        raise RuntimeError("stop before mutating")

    monkeypatch.setattr(main, "execute_delete", _spy)
    with pytest.raises(RuntimeError):
        client.post(
            "/registry/alpha/product/delete-execute",
            data={"id": values["id"], "review_sha256": submitted},
        )

    assert seen == {"id": values["id"], "review_sha256": submitted}


def test_delete_success_copy_comes_from_the_bound_execution(tmp_path, monkeypatch):
    """The route must not perform an earlier, unbound read for display."""
    import inspect as _inspect

    main, client, slug = _registry_client(tmp_path, monkeypatch)
    values = _delete_preview_values(main, client, tmp_path, slug)

    response = client.post("/registry/alpha/product/delete-execute", data=values)

    assert response.status_code == 200
    assert slug in response.text
    source = _inspect.getsource(main.registry_delete_execute)
    assert "get_delete_proposal(" not in source


def test_delete_preview_renders_only_the_fingerprinted_impact(tmp_path, monkeypatch):
    """P1 (review): the impact on screen must be the impact inside the
    fingerprint. A second live count rendered beside it describes state the
    button is not bound to, so the operator reviews one thing and acts on
    another."""
    import app.registry as registry
    from app.scope import Scope

    main, client, slug = _registry_client(tmp_path, monkeypatch)
    scope = Scope(Path(tmp_path), "alpha")

    # The proposal is written with a saved impact of zero; every count taken
    # *after* that says one. Only the saved, fingerprinted value may reach
    # the screen — a second live count would render "would orphan 1" beside
    # a button bound to bytes that record none.
    calls = []
    real_count = registry.reference_count

    def zero_then_one(scope_arg, kind, value):
        calls.append(1)
        if len(calls) == 1:                       # the write's own count
            return registry.ReferenceReport(kind, value, {})
        return registry.ReferenceReport(kind, value, {"front-matter": 1})

    monkeypatch.setattr(registry, "reference_count", zero_then_one)

    response = client.post(
        "/registry/alpha/product/delete-preview", data={"slug": slug}
    )

    body = response.text
    proposal = next((tmp_path / "alpha/outbox").glob("*.yaml"))
    record = yaml.safe_load(proposal.read_text(encoding="utf-8"))
    assert record["total_references"] == 0, "the fixture must save a zero impact"
    assert "would orphan" not in body, body
    assert "No references" in body
    assert registry.reference_count is zero_then_one
    monkeypatch.setattr(registry, "reference_count", real_count)


def test_the_delete_route_takes_no_second_live_count_at_render_time(tmp_path,
                                                                   monkeypatch):
    """Structural: the preview route reads the impact from its review
    snapshot and never calls the live counter for display."""
    import inspect as _inspect

    import app.main as main

    source = _inspect.getsource(main.registry_delete_preview)
    assert "reference_count(" not in source


def test_delete_preview_displays_the_fingerprinted_slug_not_the_submitted_one(
    tmp_path, monkeypatch
):
    """P1 (review): the value on screen must come from inside the
    fingerprint. Rendering the submitted slug lets the screen describe one
    product while the button is bound to a proposal naming another."""
    main, client, slug = _registry_client(tmp_path, monkeypatch)

    import app.registry as registry

    # The stored proposal is replaced between its creation and its review, so
    # the fingerprinted record names a different value than the form did.
    real_propose = registry.propose_delete

    def propose_then_rewrite(scope_arg, kind, value):
        written = real_propose(scope_arg, kind, value)
        record = yaml.safe_load(written.path.read_text(encoding="utf-8"))
        record["slug"] = "fingerprinted-slug-marker"
        written.path.write_text(
            yaml.safe_dump(record, sort_keys=False), encoding="utf-8"
        )
        return written

    monkeypatch.setattr(main, "propose_delete", propose_then_rewrite)

    body = client.post(
        "/registry/alpha/product/delete-preview", data={"slug": slug}
    ).text

    assert "fingerprinted-slug-marker" in body, body
    assert slug not in body, body


def test_the_preview_route_never_renders_the_submitted_slug(tmp_path, monkeypatch):
    """Structural: the fragment's context carries no submitted value."""
    import inspect as _inspect

    import app.main as main

    source = _inspect.getsource(main.registry_delete_preview)
    rendered = source.split("TemplateResponse", 1)[1]
    assert '"slug": slug' not in rendered
    template = (
        Path(__file__).resolve().parents[1] / "templates/blocks/delete_impact.html"
    ).read_text(encoding="utf-8")
    assert "{{ slug }}" not in template


# --- S7 Task 5: reconfirm on the same screen --------------------------------


def _rendered_card_ids(body: str) -> set[str]:
    return set(re.findall(r'id="(review-card-[^"]+)"', body))


def _rendered_control_ids(body: str) -> set[str]:
    return set(re.findall(r'id="(review-controls-[^"]+)"', body))


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_a_changed_review_keeps_the_old_card_and_appends_the_current_one(
    tmp_path, monkeypatch, action
):
    """Spec §Presentation, "Changed-since-review response", points 2-6."""
    from app.console_errors import _CODES

    main, client, proposal_id = _outbox_proposal_client(tmp_path, monkeypatch)
    stale = _action_data(tmp_path, proposal_id)
    proposal = tmp_path / "alpha/outbox" / f"{proposal_id}.yaml"
    proposal.write_bytes(proposal.read_bytes() + b"# rewritten\n")
    current = _outbox_fingerprint(tmp_path, proposal_id)

    response = client.post(f"/outbox/alpha/{action}", data=stale)
    body = response.text

    # 4 + 6: a newly validated current card, and only it offers controls.
    assert f"review-card-{proposal_id}-{current}" in body
    assert _CODES["E-REVIEW"].message in body

    # 3: the old controls are replaced out-of-band with disabled ones.
    old_controls = f"review-controls-{proposal_id}-{stale['review_sha256']}"
    assert old_controls in body
    assert 'hx-swap-oob="true"' in body
    disabled_block = body.split(old_controls, 1)[1].split("</div>", 1)[0]
    assert "disabled" in disabled_block

    # 2: the old card is labelled rather than replaced or re-served.
    assert "Previously reviewed" in body

    # The response is appended beside the old card, not swapped over the list.
    assert response.headers.get("HX-Retarget") == (
        f"#review-card-{proposal_id}-{stale['review_sha256']}"
    )
    assert response.headers.get("HX-Reswap") == "afterend"

    # 6: the stale fingerprint is never re-offered as actionable.
    for raw in _hx_vals(body):
        assert json.loads(raw)["review_sha256"] == current


def _hx_vals(body: str) -> list[str]:
    from tests.test_app import HxValsParser

    parser = HxValsParser()
    parser.feed(body)
    return parser.values


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_a_changed_review_changes_no_state(tmp_path, monkeypatch, action):
    main, client, proposal_id = _outbox_proposal_client(tmp_path, monkeypatch)
    stale = _action_data(tmp_path, proposal_id)
    proposal = tmp_path / "alpha/outbox" / f"{proposal_id}.yaml"
    proposal.write_bytes(proposal.read_bytes() + b"# rewritten\n")

    proposal_before = proposal.read_bytes()
    source_before = (tmp_path / "alpha/00-inbox/active/marker.md").read_bytes()
    status_before = git_status_apart_from_quarantine(tmp_path)

    client.post(f"/outbox/alpha/{action}", data=stale)

    assert proposal.read_bytes() == proposal_before
    assert (tmp_path / "alpha/00-inbox/active/marker.md").read_bytes() == source_before
    assert not (tmp_path / "alpha/02-work/active/marker.md").exists()
    assert git_status_apart_from_quarantine(tmp_path) == status_before


def test_repeated_rewrites_never_accumulate_live_controls(tmp_path, monkeypatch):
    """Approved decision 4: only the newest reviewed version may act."""
    main, client, proposal_id = _outbox_proposal_client(tmp_path, monkeypatch)
    proposal = tmp_path / "alpha/outbox" / f"{proposal_id}.yaml"
    stale = _action_data(tmp_path, proposal_id)

    proposal.write_bytes(proposal.read_bytes() + b"# first\n")
    first = client.post("/outbox/alpha/approve", data=stale).text
    reissued = json.loads(_hx_vals(first)[0])["review_sha256"]

    proposal.write_bytes(proposal.read_bytes() + b"# second\n")
    second = client.post(
        "/outbox/alpha/approve",
        data={"id": proposal_id, "review_sha256": reissued},
    ).text
    newest = _outbox_fingerprint(tmp_path, proposal_id)

    live = {json.loads(raw)["review_sha256"] for raw in _hx_vals(second)}
    assert live == {newest}
    assert reissued not in live
    assert stale["review_sha256"] not in live


# --- read-only refresh ------------------------------------------------------


def test_check_again_returns_the_current_review_and_writes_nothing(
    tmp_path, monkeypatch
):
    main, client, proposal_id = _outbox_proposal_client(tmp_path, monkeypatch)
    proposal = tmp_path / "alpha/outbox" / f"{proposal_id}.yaml"
    proposal.write_bytes(proposal.read_bytes() + b"# rewritten\n")
    current = _outbox_fingerprint(tmp_path, proposal_id)
    status_before = git_status_apart_from_quarantine(tmp_path)
    proposal_before = proposal.read_bytes()

    response = client.get(f"/outbox/alpha/review/{proposal_id}")

    assert response.status_code == 200
    assert f"review-card-{proposal_id}-{current}" in response.text
    assert json.loads(_hx_vals(response.text)[0])["review_sha256"] == current
    # Read-only: nothing written, nothing moved, nothing consumed.
    assert proposal.read_bytes() == proposal_before
    assert git_status_apart_from_quarantine(tmp_path) == status_before


@pytest.mark.parametrize("shape", ["missing", "malformed", "hostile-id"])
def test_check_again_on_an_unreviewable_proposal_offers_no_controls(
    tmp_path, monkeypatch, shape
):
    main, client, proposal_id = _outbox_proposal_client(tmp_path, monkeypatch)
    proposal = tmp_path / "alpha/outbox" / f"{proposal_id}.yaml"
    if shape == "missing":
        proposal.unlink()
    elif shape == "malformed":
        proposal.write_text("{ not: [valid, yaml", encoding="utf-8")
    else:
        proposal_id = "hostile-nonexistent-id"

    response = client.get(f"/outbox/alpha/review/{proposal_id}")

    body = response.text
    assert 'role="alert"' in body
    assert not _rendered_control_ids(body), body
    assert "review_sha256" not in body
    assert "hx-post" not in body


def test_the_review_fragment_route_is_declared_and_read_only():
    """S6 route declarations: the new GET fragment names its own family and
    never broadens to a catch-all."""
    import app.main as main

    declaration = main.outbox_review_fragment.__console_route__
    assert declaration.surface == "fragment-only"
    assert Exception not in declaration.catches
    assert BaseException not in declaration.catches
    source = inspect.getsource(main.outbox_review_fragment)
    for mutating in ("approve(", "reject(", "propose_classification(", "unlink"):
        assert mutating not in source


# --- Task 5 review: differences, delete reconfirmation, composed failure ----


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_a_changed_review_names_the_fields_that_changed(
    tmp_path, monkeypatch, action
):
    """P1 (review): two versions side by side is not "show exactly what
    changed". The operator must be told which values differ, and how.

    The old values come from the browser — the server never saw those bytes
    and must not infer them (approved decision 8) — and are used for this
    comparison only, never as authority."""
    main, client, proposal_id = _outbox_proposal_client(tmp_path, monkeypatch)
    proposal = tmp_path / "alpha/outbox" / f"{proposal_id}.yaml"

    page = client.get("/outbox/alpha").text
    reviewed = _reviewed_fields(page)
    assert reviewed, "the card must carry its reviewed values for comparison"
    stale = _action_data(tmp_path, proposal_id)
    assert _reported(page)["src"] == "alpha/00-inbox/active/marker.md"

    # A genuine rewrite into an equally valid proposal: same module, but it
    # now moves a *different* receipt. Nothing about the comparison is
    # manufactured in the request — the browser reports exactly what it was
    # rendered with, and the new record is one the server validates fully.
    second = tmp_path / "alpha/00-inbox/active/second.md"
    second.write_text(
        "---\ntitle: second-marker\nsub: triage\n---\nbody\n", encoding="utf-8"
    )
    record = yaml.safe_load(proposal.read_text(encoding="utf-8"))
    record["src"] = "alpha/00-inbox/active/second.md"
    record["dst"] = "alpha/02-work/active/second.md"
    record["source_sha256"] = hashlib.sha256(second.read_bytes()).hexdigest()
    proposal.write_text(yaml.safe_dump(record, sort_keys=False), encoding="utf-8")

    response = client.post(
        f"/outbox/alpha/{action}", data={**stale, **reviewed}
    )
    text = response.text

    assert "What changed" in text, text
    differences = text.split("What changed", 1)[1].split("</ul>", 1)[0]
    # Each genuinely changed field is named, with the server's own current
    # value — never the value the browser submitted (Rule 8).
    assert "source path" in differences
    assert "alpha/00-inbox/active/second.md" in differences
    assert "destination path" in differences
    assert "alpha/02-work/active/second.md" in differences
    # The source's recorded contents changed with it, and is named too.
    assert "source contents" in differences
    # Fields that did not change are not reported.
    assert "module" not in differences
    assert "block" not in differences


def _reviewed_fields(body: str) -> dict:
    """The values a rendered card reports back, as the form data to post.

    One JSON field, so "reviewed as empty" stays distinguishable from "not
    reported" — an empty form value arrives at FastAPI as `None`.
    """
    match = re.search(
        r"""<input type="hidden" name="reviewed_values" value='([^']*)'""", body
    )
    if match is None:
        return {}
    return {"reviewed_values": html.unescape(match.group(1))}


def _reported(body: str) -> dict:
    """The reviewed mapping itself, decoded."""
    fields = _reviewed_fields(body)
    return json.loads(fields["reviewed_values"]) if fields else {}


def test_a_byte_only_change_says_so_rather_than_listing_no_differences(
    tmp_path, monkeypatch
):
    """The case the spec calls out: the stored bytes changed but every
    action-relevant value is identical. OneOS still refuses, and must say
    that plainly instead of showing an empty difference list."""
    main, client, proposal_id = _outbox_proposal_client(tmp_path, monkeypatch)
    proposal = tmp_path / "alpha/outbox" / f"{proposal_id}.yaml"

    reviewed = _reviewed_fields(client.get("/outbox/alpha").text)
    stale = _action_data(tmp_path, proposal_id)
    proposal.write_bytes(proposal.read_bytes() + b"# byte-only\n")

    text = client.post(
        "/outbox/alpha/approve", data={**stale, **reviewed}
    ).text

    assert "stored record changed" in text.lower()
    assert "identical" in text.lower()


def test_the_reviewed_values_are_never_treated_as_authority(
    tmp_path, monkeypatch
):
    """Hostile submitted comparison data may reach the screen escaped, but
    must never alter what the action does or what the current card says."""
    main, client, proposal_id = _outbox_proposal_client(tmp_path, monkeypatch)
    proposal = tmp_path / "alpha/outbox" / f"{proposal_id}.yaml"
    stale = _action_data(tmp_path, proposal_id)
    proposal.write_bytes(proposal.read_bytes() + b"# rewritten\n")
    current = _outbox_fingerprint(tmp_path, proposal_id)

    text = client.post(
        f"/outbox/alpha/approve",
        data={
            **stale,
            "reviewed_values": json.dumps(
                {
                    "module": '"><script>alert(1)</script>',
                    "sub": "spoofed-sub-marker",
                }
            ),
        },
    ).text

    assert "<script>" not in text
    # Rule 8: the submitted value is never echoed at all, escaped or not.
    assert "spoofed-sub-marker" not in text
    assert "alert(1)" not in text
    # The current card is built from the server's own validated read.
    assert f"review-card-{proposal_id}-{current}" in text
    for raw in _hx_vals(text):
        assert json.loads(raw)["review_sha256"] == current
    assert not (tmp_path / "alpha/02-work/active/marker.md").exists()


# --- registry delete gets the same treatment --------------------------------


def test_a_changed_delete_review_reconfirms_on_the_same_screen(
    tmp_path, monkeypatch
):
    """P1 (review): registry delete is a reviewed action and must reconfirm
    exactly as the classification actions do."""
    from app.console_errors import _CODES

    main, client, slug = _registry_client(tmp_path, monkeypatch)
    stale = _delete_preview_values(main, client, tmp_path, slug)
    proposal = tmp_path / "alpha/outbox" / f"{stale['id']}.yaml"
    proposal.write_bytes(proposal.read_bytes() + b"# rewritten\n")
    current = hashlib.sha256(proposal.read_bytes()).hexdigest()

    response = client.post("/registry/alpha/product/delete-execute", data=stale)
    body = response.text

    assert _CODES["E-REVIEW"].message in body
    assert f"review-card-{stale['id']}-{current}" in body
    old_controls = f"review-controls-{stale['id']}-{stale['review_sha256']}"
    assert old_controls in body
    assert 'hx-swap-oob="true"' in body
    assert response.headers.get("HX-Retarget") == (
        f"#review-card-{stale['id']}-{stale['review_sha256']}"
    )
    assert response.headers.get("HX-Reswap") == "afterend"
    for raw in _hx_vals(body):
        assert json.loads(raw)["review_sha256"] == current
    # Nothing deleted.
    assert slug in (tmp_path / "_system/products.yaml").read_text()


# --- the composed failure ---------------------------------------------------


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_a_failed_current_review_render_still_reports_the_changed_review(
    tmp_path, monkeypatch, action
):
    """P1 (review): if building the current card fails on top of the
    refusal, both outcomes must survive — S6's composition rule. Losing the
    E-REVIEW would tell the operator their action failed for an unrelated
    reason and hide that their proposal was rewritten."""
    from app.console_errors import _CODES
    from app.vault import DestinationRegistryError

    main, client, proposal_id = _outbox_proposal_client(tmp_path, monkeypatch)
    stale = _action_data(tmp_path, proposal_id)
    proposal = tmp_path / "alpha/outbox" / f"{proposal_id}.yaml"
    proposal.write_bytes(proposal.read_bytes() + b"# rewritten\n")

    reached = []
    original = main.app.exception_handlers[Exception]

    async def _spy(request, exc):
        reached.append(type(exc).__name__)
        return await original(request, exc)

    monkeypatch.setitem(main.app.exception_handlers, Exception, _spy)

    def _fail_projection(*args, **kwargs):
        raise DestinationRegistryError("registries unreadable during re-render")

    monkeypatch.setattr(main, "project_outbox", _fail_projection)

    response = client.post(f"/outbox/alpha/{action}", data=stale)

    assert reached == [], f"fallback reached: {reached}"
    assert _CODES["E-REVIEW"].message in response.text
    assert "E-CONFIG" in response.text
    assert not (tmp_path / "alpha/02-work/active/marker.md").exists()


def test_a_changed_delete_review_names_the_changed_impact(tmp_path, monkeypatch):
    """P1 (review): the delete comparison covers the breakdown, not only the
    total — an impact that moved between sources while summing the same is
    still a different impact."""
    main, client, slug = _registry_client(tmp_path, monkeypatch)
    preview = client.post(
        "/registry/alpha/product/delete-preview", data={"slug": slug}
    ).text
    stale = json.loads(_hx_vals(preview)[0])
    reviewed = _reviewed_fields(preview)
    assert set(_reported(preview)) == {"kind", "slug", "total", "impact"}

    proposal = tmp_path / "alpha/outbox" / f"{stale['id']}.yaml"
    record = yaml.safe_load(proposal.read_text(encoding="utf-8"))
    record["total_references"] = 2
    record["impact"] = {"front-matter": 2}
    proposal.write_text(yaml.safe_dump(record, sort_keys=False), encoding="utf-8")

    body = client.post(
        "/registry/alpha/product/delete-execute", data={**stale, **reviewed}
    ).text

    assert "What changed" in body
    differences = body.split("What changed", 1)[1].split("</ul>", 1)[0]
    assert "total" in differences
    assert "impact" in differences and "front-matter=2" in differences
    assert "slug" not in differences


def test_delete_check_again_is_read_only_and_offers_the_current_review(
    tmp_path, monkeypatch
):
    main, client, slug = _registry_client(tmp_path, monkeypatch)
    preview = client.post(
        "/registry/alpha/product/delete-preview", data={"slug": slug}
    ).text
    proposal_id = json.loads(_hx_vals(preview)[0])["id"]
    proposal = tmp_path / "alpha/outbox" / f"{proposal_id}.yaml"
    proposal.write_bytes(proposal.read_bytes() + b"# rewritten\n")
    current = hashlib.sha256(proposal.read_bytes()).hexdigest()
    before = proposal.read_bytes()
    registry_before = (tmp_path / "_system/products.yaml").read_bytes()

    response = client.get(f"/registry/alpha/product/review/{proposal_id}")

    assert response.status_code == 200
    assert f"review-card-{proposal_id}-{current}" in response.text
    assert json.loads(_hx_vals(response.text)[0])["review_sha256"] == current
    assert proposal.read_bytes() == before
    assert (tmp_path / "_system/products.yaml").read_bytes() == registry_before


@pytest.mark.parametrize("shape", ["missing", "malformed", "hostile-id"])
def test_delete_check_again_on_an_unreviewable_proposal_offers_no_controls(
    tmp_path, monkeypatch, shape
):
    main, client, slug = _registry_client(tmp_path, monkeypatch)
    preview = client.post(
        "/registry/alpha/product/delete-preview", data={"slug": slug}
    ).text
    proposal_id = json.loads(_hx_vals(preview)[0])["id"]
    proposal = tmp_path / "alpha/outbox" / f"{proposal_id}.yaml"
    if shape == "missing":
        proposal.unlink()
    elif shape == "malformed":
        proposal.write_text("{ not: [valid, yaml", encoding="utf-8")
    else:
        proposal_id = "hostile-nonexistent-id"

    body = client.get(f"/registry/alpha/product/review/{proposal_id}").text

    assert 'role="alert"' in body
    assert not _rendered_control_ids(body), body
    assert "review_sha256" not in body
    assert "hx-post" not in body
    # `Check again` points back at the delete review, never the outbox one.
    assert f"/registry/alpha/product/review/{proposal_id}" in body


def test_a_failed_delete_current_review_render_keeps_both_outcomes(
    tmp_path, monkeypatch
):
    """P1 (review): independent composition coverage for the delete path."""
    from app.console_errors import _CODES
    from app.vault import DestinationRegistryError

    main, client, slug = _registry_client(tmp_path, monkeypatch)
    preview = client.post(
        "/registry/alpha/product/delete-preview", data={"slug": slug}
    ).text
    stale = json.loads(_hx_vals(preview)[0])
    proposal = tmp_path / "alpha/outbox" / f"{stale['id']}.yaml"
    proposal.write_bytes(proposal.read_bytes() + b"# rewritten\n")

    reached = []
    original = main.app.exception_handlers[Exception]

    async def _spy(request, exc):
        reached.append(type(exc).__name__)
        return await original(request, exc)

    monkeypatch.setitem(main.app.exception_handlers, Exception, _spy)

    calls = []
    real_review = main.get_delete_review

    def fail_the_second_read(scope, proposal_id):
        calls.append(1)
        raise DestinationRegistryError("registries unreadable during re-render")

    monkeypatch.setattr(main, "get_delete_review", fail_the_second_read)

    response = client.post(
        "/registry/alpha/product/delete-execute", data=stale
    )

    assert reached == [], f"fallback reached: {reached}"
    assert calls, "the current-review read was never attempted"
    assert _CODES["E-REVIEW"].message in response.text
    assert "E-CONFIG" in response.text
    assert slug in (tmp_path / "_system/products.yaml").read_text()


def test_a_changed_source_fingerprint_is_named(tmp_path, monkeypatch):
    """P1 (review): the record's claim about its source contents is an
    action-relevant value. A proposal rewritten to approve a *different
    state of the same file* changes nothing else — same module, same paths —
    and would otherwise be refused with nothing named."""
    main, client, proposal_id = _outbox_proposal_client(tmp_path, monkeypatch)
    proposal = tmp_path / "alpha/outbox" / f"{proposal_id}.yaml"
    reviewed = _reviewed_fields(client.get("/outbox/alpha").text)
    assert "source_sha256" in _reported(client.get("/outbox/alpha").text)
    stale = _action_data(tmp_path, proposal_id)

    source = tmp_path / "alpha/00-inbox/active/marker.md"
    source.write_text(
        "---\ntitle: outbox-route-marker\nsub: triage\n---\nedited\n",
        encoding="utf-8",
    )
    current_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    record = yaml.safe_load(proposal.read_text(encoding="utf-8"))
    record["source_sha256"] = current_hash
    proposal.write_text(yaml.safe_dump(record, sort_keys=False), encoding="utf-8")

    body = client.post("/outbox/alpha/approve", data={**stale, **reviewed}).text

    assert "What changed" in body, body
    differences = body.split("What changed", 1)[1].split("</ul>", 1)[0]
    assert "source contents" in differences
    # Named as changed, with no value: see the dedicated test for why.
    assert current_hash[:12] not in differences
    assert "module" not in differences
    assert "destination path" not in differences


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_an_unreported_review_is_not_called_identical(tmp_path, monkeypatch, action):
    """P1 (review): when the browser reports nothing, OneOS has no evidence
    of what was reviewed. Saying the values "look identical" asserts a
    comparison that never happened."""
    main, client, proposal_id = _outbox_proposal_client(tmp_path, monkeypatch)
    proposal = tmp_path / "alpha/outbox" / f"{proposal_id}.yaml"
    stale = _action_data(tmp_path, proposal_id)
    proposal.write_bytes(proposal.read_bytes() + b"# rewritten\n")

    body = client.post(f"/outbox/alpha/{action}", data=stale).text

    assert "identical" not in body.lower(), body
    assert "What changed" not in body
    # It says plainly that nothing could be compared, names every field it
    # could not compare, and still offers the current version to act on.
    assert "could not be compared" in body.lower()
    for field in ("module", "sub", "block", "source path", "destination path",
                  "source contents"):
        assert field in body, field
    assert f"review-card-{proposal_id}-" in body


def test_a_byte_only_change_with_evidence_still_says_identical(
    tmp_path, monkeypatch
):
    """The control: with evidence in hand, "identical values, changed bytes"
    is a true statement and must still be made."""
    main, client, proposal_id = _outbox_proposal_client(tmp_path, monkeypatch)
    proposal = tmp_path / "alpha/outbox" / f"{proposal_id}.yaml"
    reviewed = _reviewed_fields(client.get("/outbox/alpha").text)
    stale = _action_data(tmp_path, proposal_id)
    proposal.write_bytes(proposal.read_bytes() + b"# byte-only\n")

    body = client.post(
        "/outbox/alpha/approve", data={**stale, **reviewed}
    ).text

    assert "identical" in body.lower()
    assert "could not be compared" not in body.lower()


# --- the refresh integrity matrix, completed --------------------------------


@pytest.mark.parametrize("surface", ["outbox", "registry"])
@pytest.mark.parametrize(
    "shape", ["missing", "malformed", "redirected", "non-file", "cross-scope"]
)
def test_check_again_refuses_every_unreviewable_shape_read_only(
    tmp_path, monkeypatch, surface, shape
):
    """P2 (review): the matrix must cover redirection, non-file and
    cross-scope states too — each renders the safe no-action state, offers
    nothing to act on, and writes nothing."""
    if surface == "outbox":
        main, client, proposal_id = _outbox_proposal_client(tmp_path, monkeypatch)
        url = f"/outbox/alpha/review/{proposal_id}"
    else:
        main, client, slug = _registry_client(tmp_path, monkeypatch)
        preview = client.post(
            "/registry/alpha/product/delete-preview", data={"slug": slug}
        ).text
        proposal_id = json.loads(_hx_vals(preview)[0])["id"]
        url = f"/registry/alpha/product/review/{proposal_id}"

    proposal = tmp_path / "alpha/outbox" / f"{proposal_id}.yaml"
    outside = tmp_path / "outside-marker.yaml"
    outside.write_bytes(proposal.read_bytes())

    if shape == "missing":
        proposal.unlink()
    elif shape == "malformed":
        proposal.write_text("{ not: [valid, yaml", encoding="utf-8")
    elif shape == "redirected":
        proposal.unlink()
        proposal.symlink_to(outside)
    elif shape == "non-file":
        proposal.unlink()
        proposal.mkdir()
    else:
        record = yaml.safe_load(outside.read_text(encoding="utf-8"))
        record["entity"] = "beta-not-bound"
        proposal.write_text(
            yaml.safe_dump(record, sort_keys=False), encoding="utf-8"
        )

    outside_before = outside.read_bytes()
    reached = []
    original = main.app.exception_handlers[Exception]

    async def _spy(request, exc):
        reached.append(type(exc).__name__)
        return await original(request, exc)

    monkeypatch.setitem(main.app.exception_handlers, Exception, _spy)

    response = client.get(url)

    assert reached == [], f"fallback reached: {reached}"
    body = response.text
    assert 'role="alert"' in body
    assert not _rendered_control_ids(body), body
    assert "review_sha256" not in body
    assert "hx-post" not in body
    # Read-only, and the redirect target is never followed or consumed.
    assert outside.read_bytes() == outside_before
    if shape == "redirected":
        assert proposal.is_symlink()
    elif shape == "non-file":
        assert proposal.is_dir()


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_partial_evidence_is_never_reported_as_complete(
    tmp_path, monkeypatch, action
):
    """P1 (review): reporting some fields is not reporting all of them.

    If every field the browser did report happens to match, saying the
    values "look identical" claims a comparison of fields OneOS never saw.
    The uncompared fields must be named instead.
    """
    main, client, proposal_id = _outbox_proposal_client(tmp_path, monkeypatch)
    proposal = tmp_path / "alpha/outbox" / f"{proposal_id}.yaml"
    reviewed = _reviewed_fields(client.get("/outbox/alpha").text)
    stale = _action_data(tmp_path, proposal_id)
    proposal.write_bytes(proposal.read_bytes() + b"# byte-only\n")

    # The browser reports everything except the source state.
    reported = json.loads(reviewed["reviewed_values"])
    reported.pop("source_sha256")
    partial = {"reviewed_values": json.dumps(reported)}
    body = client.post(f"/outbox/alpha/{action}", data={**stale, **partial}).text

    assert "identical" not in body.lower(), body
    assert "could not be compared" in body.lower()
    uncompared = body.split("could not be compared", 1)[1].split("</p>", 1)[0]
    assert "source contents" in uncompared
    # Fields that were reported and matched are not listed as uncompared.
    assert "module" not in uncompared
    assert "destination path" not in uncompared


def test_a_changed_digest_is_shown_readably_not_as_raw_hex(tmp_path, monkeypatch):
    """P2 (review): a 64-character digest tells the operator nothing. It is
    named in words and abbreviated, so the line is readable and still
    identifies the value."""
    main, client, proposal_id = _outbox_proposal_client(tmp_path, monkeypatch)
    proposal = tmp_path / "alpha/outbox" / f"{proposal_id}.yaml"
    reviewed = _reviewed_fields(client.get("/outbox/alpha").text)
    stale = _action_data(tmp_path, proposal_id)

    source = tmp_path / "alpha/00-inbox/active/marker.md"
    source.write_text(
        "---\ntitle: outbox-route-marker\nsub: triage\n---\nedited\n",
        encoding="utf-8",
    )
    current_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    record = yaml.safe_load(proposal.read_text(encoding="utf-8"))
    record["source_sha256"] = current_hash
    proposal.write_text(yaml.safe_dump(record, sort_keys=False), encoding="utf-8")

    body = client.post("/outbox/alpha/approve", data={**stale, **reviewed}).text
    differences = body.split("What changed", 1)[1].split("</ul>", 1)[0]

    assert "source contents" in differences.lower()
    # A digest identifies nothing to a person, abbreviated or not. The field
    # is named as changed and no value is shown for it.
    assert current_hash not in differences
    assert current_hash[:12] not in differences
    assert "changed" in differences.lower()


@pytest.mark.parametrize("surface", ["outbox", "registry"])
def test_check_again_never_reads_through_a_redirected_leaf(
    tmp_path, monkeypatch, surface
):
    """P2 (review): asserting the target is unchanged proves no mutation, not
    that it was never read. Record what each read resolves to."""
    import os as _os

    if surface == "outbox":
        main, client, proposal_id = _outbox_proposal_client(tmp_path, monkeypatch)
        url = f"/outbox/alpha/review/{proposal_id}"
    else:
        main, client, slug = _registry_client(tmp_path, monkeypatch)
        preview = client.post(
            "/registry/alpha/product/delete-preview", data={"slug": slug}
        ).text
        proposal_id = json.loads(_hx_vals(preview)[0])["id"]
        url = f"/registry/alpha/product/review/{proposal_id}"

    proposal = tmp_path / "alpha/outbox" / f"{proposal_id}.yaml"
    outside = tmp_path / "outside-marker.yaml"
    planted = yaml.safe_load(proposal.read_text(encoding="utf-8"))
    planted["planted_marker"] = "REDIRECT-TARGET-CONTENTS-MARKER"
    outside.write_text(yaml.safe_dump(planted, sort_keys=False), encoding="utf-8")

    # The proposal is read through `os.open` with a directory descriptor, so a
    # spy on `Path.read_bytes` records nothing and passes vacuously. Watch
    # every descriptor this process opens and identify what was opened by
    # inode, which no path trick can disguise.
    opened: list[tuple[int, int]] = []

    def _record(descriptor):
        try:
            info = _os.fstat(descriptor)
        except OSError:
            return
        opened.append((info.st_dev, info.st_ino))

    real_os_open = _os.open
    real_builtin_open = builtins.open

    def spy_os_open(*args, **kwargs):
        descriptor = real_os_open(*args, **kwargs)
        _record(descriptor)
        return descriptor

    def spy_builtin_open(*args, **kwargs):
        handle = real_builtin_open(*args, **kwargs)
        try:
            _record(handle.fileno())
        except (OSError, ValueError, AttributeError):
            pass
        return handle

    monkeypatch.setattr(_os, "open", spy_os_open)
    monkeypatch.setattr(builtins, "open", spy_builtin_open)

    # Positive control: with the real leaf in place, the spy sees the
    # proposal itself being opened. Without this the negative assertion
    # below could pass simply by watching the wrong boundary.
    healthy = proposal.stat()
    client.get(url)
    assert (healthy.st_dev, healthy.st_ino) in opened, (
        "the spy is not watching the boundary the reads go through"
    )

    # Now redirect the leaf and prove the target is never opened.
    target = outside.stat()
    proposal.unlink()
    proposal.symlink_to(outside)
    opened.clear()

    body = client.get(url).text

    monkeypatch.undo()
    assert opened, "the redirected request opened nothing at all"
    assert (target.st_dev, target.st_ino) not in opened, (
        "the redirected target was opened"
    )
    assert "REDIRECT-TARGET-CONTENTS-MARKER" not in body
    assert not _rendered_control_ids(body)


@pytest.mark.parametrize(
    "payload",
    [
        '{"module": 7, "sub": ["a"], "block": {"x": 1}}',   # wrong value types
        '["not", "a", "mapping"]',                          # wrong shape
        "not json at all",                                  # unparseable
        '{"module": null}',                                 # explicit null
        "",                                                 # empty
    ],
)
def test_malformed_reported_evidence_is_treated_as_absent(
    tmp_path, monkeypatch, payload
):
    """Evidence the browser sends is parsed defensively.

    Anything that is not a plain string-to-string mapping is treated as not
    reported: it may withhold a line of explanation, never add a false one,
    and never reach the action or the page.
    """
    main, client, proposal_id = _outbox_proposal_client(tmp_path, monkeypatch)
    proposal = tmp_path / "alpha/outbox" / f"{proposal_id}.yaml"
    stale = _action_data(tmp_path, proposal_id)
    proposal.write_bytes(proposal.read_bytes() + b"# rewritten\n")
    current = _outbox_fingerprint(tmp_path, proposal_id)

    reached = []
    original = main.app.exception_handlers[Exception]

    async def _spy(request, exc):
        reached.append(type(exc).__name__)
        return await original(request, exc)

    monkeypatch.setitem(main.app.exception_handlers, Exception, _spy)

    body = client.post(
        "/outbox/alpha/approve",
        data={**stale, "reviewed_values": payload},
    ).text

    assert reached == [], f"fallback reached: {reached}"
    # Nothing is claimed about fields that were not properly reported: they
    # are listed as uncompared, never rendered as differences.
    assert "identical" not in body.lower()
    assert "could not be compared" in body.lower()
    assert "What changed" not in body, body
    uncompared = body.split("could not be compared", 1)[1].split("</p>", 1)[0]
    for label in ("module", "sub", "block", "source path", "destination path",
                  "source contents"):
        assert label in uncompared, f"{label} missing from {uncompared}"
    # Nothing submitted is echoed, and the current card is the server's own.
    for fragment in ("not json at all", "not a mapping", "[&#39;a&#39;]", "{&#39;x&#39;"):
        assert fragment not in body
    assert f"review-card-{proposal_id}-{current}" in body
    assert not (tmp_path / "alpha/02-work/active/marker.md").exists()
