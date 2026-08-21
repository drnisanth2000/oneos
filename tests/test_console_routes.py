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
import importlib
from pathlib import Path

import pytest
from starlette.requests import Request
from starlette.testclient import TestClient

from app.console_routing import console_route
from tests.conftest import scaffold_modules, write_vault

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
