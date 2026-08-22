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
import hashlib
import importlib
import json
import re
import subprocess
from pathlib import Path

import pytest
from starlette.requests import Request
from starlette.testclient import TestClient

from app.console_routing import console_route
from tests.conftest import (
    git_changed_paths,
    git_count_commits,
    git_head,
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


# --- Task 12: routes, outbox (design §3 "Rule 3", §5 route inventory,
# §8 test matrix) -----------------------------------------------------------
#
# `outbox_screen` had no try/except at all, and `outbox_approve`/
# `outbox_reject` caught only `OutboxError` — the exact Task 11 trap, unfixed
# here until now: all three declare `(OutboxError, CrossScopeError,
# DestinationRegistryError)`, so `CrossScopeError` and `DestinationRegistryError`
# escaped to the global fallback (and, per Starlette's `ServerErrorMiddleware`,
# logged a raw traceback) for every one of the three routes.


def _outbox_proposal_client(tmp_path, monkeypatch, *, proposal_id=None):
    """A vault with one active module and one valid, loadable outbox
    proposal — no Git repo. Sufficient for every test that monkeypatches
    `app.outbox.execute_transaction` (or `main.approve`/`main.reject`
    directly) rather than driving a real transaction.
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

    response = client.post("/outbox/alpha/approve", data={"id": proposal_id})

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

    response = client.post("/outbox/alpha/approve", data={"id": proposal_id})

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

    response = client.post("/outbox/alpha/approve", data={"id": proposal_id})

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

    response = client.post("/outbox/alpha/approve", data={"id": proposal_id})

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

    response = client.post("/outbox/alpha/approve", data={"id": proposal_id})

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
        data={"id": "20260101T000000-" + "00" * 16},
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

    refusal = client.post(
        "/outbox/alpha/reject", data={"id": "hostile-nonexistent-id"}
    )
    assert refusal.text.count('id="outbox-list"') == 1

    success = client.post("/outbox/alpha/reject", data={"id": proposal_id})
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
        "/outbox/alpha/approve", data={"id": proposal_id}
    )
    assert approve_double_failure.text.count('id="outbox-list"') == 1
    reject_double_failure = client.post(
        "/outbox/alpha/reject", data={"id": proposal_id}
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

    response = client.post(f"/outbox/alpha/{action}", data={"id": proposal_id})
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

    response = client.post(f"/outbox/alpha/{action}", data={"id": proposal_id})
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

    response = client.post("/outbox/alpha/approve", data={"id": valid_id})

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
        "/outbox/alpha/reject", data={"id": "hostile-nonexistent-id"}
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

    parser = HxValsParser()
    parser.feed(response.text)
    assert len(parser.values) == 2  # one approve button, one reject button
    for raw in parser.values:
        assert json.loads(raw) == {"id": proposal_id}

    source = (
        Path(__file__).resolve().parents[1]
        / "templates/blocks/outbox_list.html"
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
            "/outbox/alpha/approve", data={"id": proposal_id}
        )
        assert expected_code in approve_response.text, approve_response.text
        assert proposal_id in approve_response.text, (
            "the still-pending proposal must survive the listing re-render"
        )
        assert reached == [], f"outbox_approve: fallback reached for {expected_code}"

        monkeypatch.setattr(main, "reject", _raise)
        reject_response = client.post(
            "/outbox/alpha/reject", data={"id": proposal_id}
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
            "/outbox/alpha/approve", data={"id": proposal_id}
        )
        assert expected_code in approve_response.text, approve_response.text
        assert reached == [], f"outbox_approve: fallback reached for {expected_code}"

        reject_response = client.post(
            "/outbox/alpha/reject", data={"id": proposal_id}
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
