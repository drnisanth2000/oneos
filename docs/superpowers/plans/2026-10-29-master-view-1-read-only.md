# Master View — Plan 1: Read-only Home Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the "Wiring check" home page with a read-only master view across every entity: greeting and clock, entity filters, sort, and four columns (Draft · Waiting · To review · Done) whose cards open a read-only popup.

**Architecture:** `app/board.py` gains one public per-entity reader (`entity_cards`) that `load_board` and a new `app/master.py` both use, so no card logic is copied. `load_master` walks every on-disk bundle, builds each entity's scope, sorts cards into the four stages, and records a described failure for an entity it cannot read instead of failing the page. `/` renders `home.html`; `/master/card/{entity}` returns the popup fragment.

**Tech Stack:** Python 3.12, FastAPI, Jinja2, HTMX + `alpine-morph`, Alpine.js (vendored), pytest with synthetic vaults.

**Spec:** `docs/superpowers/specs/2026-10-29-master-view-design.md` (owner-approved 2026-10-29). This plan is part (1) of four; reminders/Needs attention (2), moves and edits (3), and Calendar sync (4) are later plans.

**Deferred on purpose, not forgotten:** the Needs attention strip, deadline and review-by dates, and the per-column sorts (Waiting by deadline, To review by review-by) arrive with plan 2, because they need reminders. Dragging, the popup buttons, the comment box and draft editing arrive with plan 3. Plan 1 ships one global sort (Updated/Title) and a read-only popup.

## Global Constraints

- Stack fixed: FastAPI, Jinja, HTMX, Alpine, `alpine-morph` required, vendored JS only, no build step, **no new dependency**.
- This plan writes nothing to the vault: no route added here performs a write.
- Stages: `draft` → Draft; `waiting` → Waiting; `done` **and `active`** → Done; inbox items and pending classify proposals → To review; any other `status` (including blank) → "another status" list, never dropped.
- Column order exactly: **Draft · Waiting · To review · Done**.
- Done shows the 10 most recent cards plus "Show all N"; `?done=all` shows all.
- Greeting by local hour: before 12:00 "Good morning", before 17:00 "Good afternoon", otherwise "Good evening"; date as `Wednesday, 29 October 2026`; time `HH:MM`, kept live client-side.
- Values come from registries and vault state only; tests use synthetic vaults (`tests/conftest.py`), never instance names.
- `/` keeps an `hx-swap="morph"` element (`tests/test_app.py::test_morph_is_wired`).
- The master view reads every entity's vault structure: run the local private integration gate in `BUILD.md` before merge.
- Visual system from PR #45 (tokens, icon sprite, light/dark, phone/tablet).

## Review Focus

- **One entity unreadable** (missing manifest, redirected directory, unreadable outbox): the page still renders 200 with the other entities and one described alert naming that entity's code. `/` is pinned at 200 by `tests/test_console_routes.py` (deleted `entities.yaml` case).
- **Status spelling** (`Done`, ` waiting `, `ACTIVE`): compare stripped and lower-cased.
- **An inbox item that already has a pending proposal** appears once in To review (the proposal), not twice.
- **Popup path not belonging to that entity** (`../other/...`, another entity's note, an `archive/` path): refused with the "no longer here" fragment; nothing outside the entity is read.
- **No bundles at all**: `/` renders the existing `blocks/no_bundles.html`.

---

### Task 1: Shared per-entity card reader in `app/board.py`

**Files:**
- Modify: `app/board.py` (new `EntityCards`, `entity_cards`, rename `_filed_body` → `note_body`; `load_board` calls `entity_cards`)
- Test: `tests/test_board.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class EntityCards: inbox: tuple[BoardCard, ...]; proposed: tuple[BoardCard, ...]; filed: tuple[BoardCard, ...]; blocked: bool`
  - `entity_cards(scope: Scope, vault: Vault) -> EntityCards`, decorated `@failure_contract(raises=(DestinationRegistryError, RedirectedPathError), calls=(read_inbox, project_outbox))`.
  - `note_body(scope: Scope, relative: str) -> str` (the existing `_filed_body`, unchanged behaviour, now public; keep its `@structured_reader(category="front-matter")`).

- [ ] **Step 1: Write the failing test**

```python
def test_entity_cards_returns_each_lane(tmp_path, monkeypatch):
    main, _client_ = _client(tmp_path, monkeypatch)
    _note(tmp_path, "alpha", "02-work", "Filed")
    _note(tmp_path, "alpha", "00-inbox", "Waiting", sub="triage", title="Waiting")
    from app.board import entity_cards, note_body
    scope = main.build_scope("alpha")
    cards = entity_cards(scope, main.Vault(main.current_catalog()))
    assert [c.title for c in cards.filed] == ["Filed"]
    assert [c.title for c in cards.inbox] == ["Waiting"]
    assert cards.proposed == () and cards.blocked is False
    assert note_body(scope, cards.filed[0].relative_path).strip() == "Body"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_board.py::test_entity_cards_returns_each_lane -q`
Expected: FAIL, `ImportError: cannot import name 'entity_cards'`.

- [ ] **Step 3: Implement `entity_cards` and `note_body`**

`entity_cards` wraps the three existing reads (`_inbox_cards(scope, vault, read_inbox(scope))`, `_proposal_cards(scope, project_outbox(scope))`, `_read_filed(scope, vault)`) and returns tuples. In `load_board`, replace those three lines with one `entity_cards` call. Rename `_filed_body` to `note_body` and update its one caller in `load_board`. Behaviour is otherwise unchanged.

- [ ] **Step 4: Run the Board and invariant tests**

Run: `uv run pytest tests/test_board.py tests/test_console_invariants.py -q`
Expected: all pass. If `test_console_invariants` lists reader names, add `entity_cards` and `note_body` where it lists `load_board`'s readers, and nothing else.

- [ ] **Step 5: Commit**

```bash
git add app/board.py tests/test_board.py tests/test_console_invariants.py
git commit -m "Board: share one per-entity card reader"
```

---

### Task 2: `app/master.py` — the master view reader

**Files:**
- Create: `app/master.py`
- Test: `tests/test_master.py`

**Interfaces:**
- Consumes: `entity_cards(scope, vault) -> EntityCards` and `BoardCard` (Task 1); `Vault.bundles()` (`Bundle.slug`, `.label`, `.on_disk`); `describe(exc) -> ConsoleError` from `app/console_errors.py`.
- Produces:
  - `STAGES = ("draft", "waiting", "review", "done")` with labels `{"draft": "Draft", "waiting": "Waiting", "review": "To review", "done": "Done"}`.
  - `@dataclass(frozen=True) class MasterCard: card: BoardCard; entity: str; entity_label: str; tone: int` (`tone` = 1-based position of the entity in `Vault.bundles()` order, cycled modulo 6).
  - `@dataclass(frozen=True) class MasterColumn: key: str; label: str; cards: tuple[MasterCard, ...]; total: int`.
  - `@dataclass(frozen=True) class EntityChip: slug: str; label: str; tone: int; active: bool; href: str`.
  - `@dataclass(frozen=True) class EntityFailure: entity_label: str; error: ConsoleError`.
  - `@dataclass(frozen=True) class MasterPage: greeting: str; date_label: str; time_label: str; chips: tuple[EntityChip, ...]; sort: str; sort_links: tuple[BoardLink, ...]; columns: tuple[MasterColumn, ...]; other: tuple[MasterCard, ...]; failures: tuple[EntityFailure, ...]; done_all: bool; done_all_href: str`.
  - `load_master(vault: Vault, scope_for: Callable[[str], Scope], *, entities: tuple[str, ...], sort: str, done_all: bool, now: datetime) -> MasterPage`, decorated `@failure_contract(raises=(DestinationRegistryError,), calls=(entity_cards,))`.
  - `_ENTITY_FAILURES`: the per-entity catch tuple, the same classes as `main._BOARD_CATCHES` (`OutboxError, CrossScopeError, DestinationRegistryError, EntityManifestError, EntitySelectionError, InvalidActionReceipt, ReceiptStoreIntegrityError, ReceiptStoreUnavailable`) plus `RedirectedPathError`, imported from their own modules.

Rules `load_master` must follow:
- Skip bundles with `on_disk == False`. `entities` empty means all; unknown slugs are ignored.
- Per entity: call `scope_for(slug)` and `entity_cards`; on an `_ENTITY_FAILURES` exception, append `EntityFailure(label, describe(exc))` and continue.
- To review = proposals + inbox items whose `relative_path` is not the `relative_path` of any proposal.
- Stage by `card.status.strip().lower()`: as in Global Constraints. Others go to `other`.
- Sort: `sort` in `{"updated", "title"}` (default `"updated"`), reusing Board's `_sort_cards` ordering (newest updated first, blank last; title A→Z). The Done column is always newest first, then cut to 10 unless `done_all`.
- `MasterColumn.total` is the count before the Done cut.
- Hrefs keep the other query params: `/?entity=a&entity=b&sort=title&done=all`.

- [ ] **Step 1: Write the failing tests** in `tests/test_master.py`, using a `_vault(tmp_path, monkeypatch)` helper that writes `entities_yaml("alpha", "beta1")`, scaffolds `["00-inbox", "01-core", "02-work"]` for both, and returns `(main, vault)`. Reuse the `_note` shape from `tests/test_board.py` (copy the helper; do not import test modules).

```python
def test_cards_land_in_their_stage_across_entities(tmp_path, monkeypatch):
    main, vault = _vault(tmp_path, monkeypatch)
    _note(tmp_path, "alpha", "02-work", "Spec", status="draft")
    _note(tmp_path, "beta1", "02-work", "Filing", status=" Waiting ")
    _note(tmp_path, "alpha", "01-core", "Old", status="active")
    _note(tmp_path, "beta1", "01-core", "Closed", status="DONE")
    _note(tmp_path, "alpha", "02-work", "Odd", status="open")
    _note(tmp_path, "alpha", "00-inbox", "New mail", sub="triage", title="New mail")
    page = load_master(vault, main.build_scope, entities=(), sort="updated",
                       done_all=False, now=datetime(2026, 10, 29, 9, 12))
    titles = {c.key: [m.card.title for m in c.cards] for c in page.columns}
    assert [c.key for c in page.columns] == ["draft", "waiting", "review", "done"]
    assert [c.label for c in page.columns] == ["Draft", "Waiting", "To review", "Done"]
    assert titles["draft"] == ["Spec"] and titles["waiting"] == ["Filing"]
    assert titles["review"] == ["New mail"]
    assert sorted(titles["done"]) == ["Closed", "Old"]
    assert [m.card.title for m in page.other] == ["Odd"]
    assert {m.entity_label for m in page.columns[1].cards} == {"Beta1"}

def test_greeting_and_date(tmp_path, monkeypatch):
    main, vault = _vault(tmp_path, monkeypatch)
    page = load_master(vault, main.build_scope, entities=(), sort="", done_all=False,
                       now=datetime(2026, 10, 29, 16, 59))
    assert page.greeting == "Good afternoon"
    assert page.date_label == "Wednesday, 29 October 2026"
    assert page.time_label == "16:59"

def test_entity_filter_and_done_limit(tmp_path, monkeypatch):
    main, vault = _vault(tmp_path, monkeypatch)
    for i in range(12):
        _note(tmp_path, "alpha", "02-work", f"Done {i:02}", status="done", updated=f"2026-10-{i + 1:02}")
    _note(tmp_path, "beta1", "02-work", "Beta done", status="done")
    page = load_master(vault, main.build_scope, entities=("alpha",), sort="updated",
                       done_all=False, now=datetime(2026, 10, 29, 9))
    done = page.columns[3]
    assert done.total == 12 and len(done.cards) == 10
    assert done.cards[0].card.title == "Done 11"
    assert all(m.entity == "alpha" for m in done.cards)
    assert [c.active for c in page.chips] == [True, False]
    assert page.done_all_href == "/?entity=alpha&sort=updated&done=all"

def test_proposed_inbox_item_appears_once(tmp_path, monkeypatch):
    main, vault = _vault(tmp_path, monkeypatch)
    _inbox_with_proposal(tmp_path, "alpha", "marker.md")  # helper: same YAML as tests/test_app.py's client fixture
    page = load_master(vault, main.build_scope, entities=(), sort="", done_all=False,
                       now=datetime(2026, 10, 29, 9))
    review = page.columns[2].cards
    assert len(review) == 1 and review[0].card.lane == "proposed"

def test_unreadable_entity_is_reported_not_fatal(tmp_path, monkeypatch):
    main, vault = _vault(tmp_path, monkeypatch)
    _note(tmp_path, "beta1", "02-work", "Still here", status="draft")
    def scope_for(slug):
        if slug == "alpha":
            raise main.CrossScopeError("synthetic")
        return main.build_scope(slug)
    page = load_master(vault, scope_for, entities=(), sort="", done_all=False,
                       now=datetime(2026, 10, 29, 9))
    assert [m.card.title for m in page.columns[0].cards] == ["Still here"]
    assert [f.entity_label for f in page.failures] == ["Alpha"]
    assert page.failures[0].error.code
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_master.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'app.master'`.

- [ ] **Step 3: Implement `app/master.py`** to the interface and rules above. Module docstring: read-only, never writes.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_master.py tests/test_board.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add app/master.py tests/test_master.py
git commit -m "Master view reader: every entity, four stages"
```

---

### Task 3: Routes — `/` renders the master view, `/master/card/{entity}` the popup

**Files:**
- Modify: `app/main.py` (`shell` at the `@app.get("/")` route; new `master_card` route)
- Modify: `tests/test_console_invariants.py` (`_expected_route_services`, the services total `50` → `53`)
- Test: `tests/test_master.py`, `tests/test_app.py`

**Interfaces:**
- Consumes: `load_master` (Task 2), `entity_cards`, `note_body` (Task 1).
- Produces:
  - `shell(request)` keeps its name and `@console_route(catches=_SIDEBAR_CATCHES, surface="page", services=(load_master, Vault.bundles))`. Query params: `entity` (repeatable), `sort`, `done` (`"all"` → `done_all=True`). `now=datetime.now()`. Renders `home.html` with `{"bundles": ..., "page": page}`; renders `blocks/no_bundles.html` when there are no bundles.
  - `master_card(request, scope: EntityScope, path: str = "")` at `GET /master/card/{entity}`, `@console_route(catches=_BOARD_CATCHES, surface="fragment-only", services=(entity_cards, note_body))`. It finds the card in `entity_cards(scope, vault)` whose `relative_path == path` (any lane). Found: render `blocks/master_popup.html` with `{"card", "stage", "entity_label", "body"}`, where body is `note_body(scope, path)`. Not found: render the same template with `card=None`, which shows "This card is no longer here. Refresh the page.". It never resolves `path` before it is matched.
  - `_expected_route_services` gains `main.shell: (main.load_master, main.Vault.bundles)` (replacing `(main.Vault.bundles,)`) and `main.master_card: (main.entity_cards, main.note_body)`.

- [ ] **Step 1: Write the failing tests**

```python
# `_client(tmp_path, monkeypatch)` returns `(main, TestClient(main.app))` over `_vault()`.

def test_home_is_the_master_view(tmp_path, monkeypatch):
    _main, client = _client(tmp_path, monkeypatch)
    html = client.get("/").text
    assert "Wiring check" not in html
    for label in ("Draft", "Waiting", "To review", "Done"):
        assert f">{label}<" in html
    assert html.index(">Draft<") < html.index(">Waiting<") < html.index(">To review<") < html.index(">Done<")
    assert 'hx-swap="morph"' in html

def test_popup_shows_the_note_body_read_only(tmp_path, monkeypatch):
    _main, client = _client(tmp_path, monkeypatch)
    _note(tmp_path, "alpha", "02-work", "Spec", body="Decision text", status="draft")
    response = client.get("/master/card/alpha", params={"path": "alpha/02-work/active/Spec.md"})
    assert response.status_code == 200
    assert "Decision text" in response.text and ">Draft<" in response.text
    assert "<form" not in response.text and "hx-post" not in response.text and "<textarea" not in response.text

def test_popup_refuses_paths_outside_the_entity(tmp_path, monkeypatch):
    _main, client = _client(tmp_path, monkeypatch)
    _note(tmp_path, "beta1", "02-work", "Beta")
    for path in ("../beta1/02-work/active/x.md", "beta1/02-work/active/Beta.md",
                 "alpha/02-work/archive/old.md", ""):
        html = client.get("/master/card/alpha", params={"path": path}).text
        assert "no longer here" in html

def test_home_survives_a_deleted_manifest(tmp_path, monkeypatch):
    _main, client = _client(tmp_path, monkeypatch)
    (tmp_path / "_system/entities.yaml").unlink()
    assert client.get("/").status_code == 200
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_master.py -q -k "home or popup"`
Expected: FAIL: "Wiring check" is still present, and `/master/card/alpha` returns 404.

- [ ] **Step 3: Implement both routes** and update `_expected_route_services` and its total.

- [ ] **Step 4: Run the route and invariant suites**

Run: `uv run pytest tests/test_master.py tests/test_app.py tests/test_console_invariants.py tests/test_console_routes.py -q`
Expected: all pass. `test_shell_boots_and_lists_bundles_from_vault` still finds `Alpha`, `Beta` and `zz-extra` in the sidebar.

- [ ] **Step 5: Commit**

```bash
git add app/main.py tests/test_master.py tests/test_console_invariants.py
git commit -m "Home: master view at /, read-only card popup"
```

---

### Task 4: Templates, styles, sidebar Home row, docs

**Files:**
- Create: `templates/home.html`, `templates/blocks/master_card.html`, `templates/blocks/master_popup.html`
- Modify: `templates/_sidebar.html` (a "Home" row first under Workspaces, current on `/`), `static/app.css` (a `/* --- master view --- */` section), `templates/shell.html` (delete if nothing renders it after Task 3), `CHANGELOG.md`, `docs/STATUS.md` (one paragraph under the Board section: the master view, read-only, plan 1 of 4)
- Test: `tests/test_master.py`

**Interfaces:**
- Consumes: `MasterPage` and friends (Task 2), `/master/card/{entity}` (Task 3), icons from `templates/_icon.html`.

Layout, from the frozen mockup:
- **Header:** `<h1>` with the greeting; the date and an Alpine-updated `HH:MM` clock (`x-data` with a 30-second `setInterval`; the server value renders first).
- **Wrapper:** `<div id="master" hx-get="{{ request.url }}" hx-trigger="every 60s" hx-select="#master" hx-swap="morph">`.
- **Filters:**
  - The chip row (`EntityChip`; "All" active when no chip is active) and a `segmented` Sort (`Updated`/`Title`).
  - Chips and sort are links, so filtering is a GET with no writes.
- **Board:**
  - Four columns in `STAGES` order. Each shows its label and count (`total`), and placeholders "No drafts", "Nothing waiting", "Nothing to review", "Nothing done yet".
  - Done ends with "Show all N" when `total > len(cards)`.
- **Card** (`master_card.html`):
  - File-type icon, title, entity chip (`.ent .ent-{{ tone }}`) and date (`card.updated`).
  - `hx-get="/master/card/{{ entity }}?path={{ relative_path | urlencode }}" hx-target="#master-popup"`, and Alpine opens `<dialog id="master-popup-dialog">` on `htmx:afterSwap`.
  - The card is a `<button>` for keyboard access.
- **Popup** (`master_popup.html`):
  - Title, entity chip, stage label, and location (`module` / `sub`; for proposals "proposed for {module}").
  - The body as escaped preformatted text, in a scrollable box.
  - A ✕ close button. No form or buttons in plan 1.
- **Other statuses:** a `<details>` below the board, titled "1 note with another status" or "N notes with another status", listing the cards. It is hidden when there are none.
- **Failures:** one `blocks/alert.html` per `EntityFailure`, prefixed with the entity label, above the board.
- **Responsive:**
  - At ≤760px a segmented stage switcher (Alpine `stage`, default `review`) shows one column at a time.
  - At 761–1100px, two columns per row.

- [ ] **Step 1: Write the failing tests**

```python
def test_home_cards_open_the_popup_and_filters_are_links(tmp_path, monkeypatch):
    _main, client = _client(tmp_path, monkeypatch)
    _note(tmp_path, "alpha", "02-work", "Spec", status="draft")
    html = client.get("/").text
    assert 'hx-get="/master/card/alpha?path=' in html
    assert 'id="master-popup-dialog"' in html
    assert 'href="/?entity=alpha' in html          # entity chip
    assert "<form" not in html.split('id="master"')[1].split("</main>")[0]  # nothing posts

def test_other_statuses_and_failures_render(tmp_path, monkeypatch):
    main, client = _client(tmp_path, monkeypatch)
    _note(tmp_path, "alpha", "02-work", "Odd", status="open")
    outside = tmp_path / "outside"
    _note(outside, "other", "02-work", "Secretnote")
    (tmp_path / "beta1" / "02-work" / "active").symlink_to(
        outside / "other" / "02-work" / "active", target_is_directory=True)
    response = client.get("/")
    assert response.status_code == 200                      # one bad entity never blanks home
    assert "1 note with another status" in response.text
    assert "E-TAMPER" in response.text and "Beta1" in response.text
    assert "Secretnote" not in response.text

def test_sidebar_has_home_row_marked_current(tmp_path, monkeypatch):
    _main, client = _client(tmp_path, monkeypatch)
    html = client.get("/").text
    assert 'href="/"' in html and 'aria-current="page"' in html.split("Workspaces")[1][:600]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_master.py -q -k "cards_open or other_statuses or sidebar_has_home"`
Expected: FAIL.

- [ ] **Step 3: Build the templates, CSS and sidebar row**, reusing existing classes (`segmented`, `segment`, `board-card`, `card-icon`, `count`, `settings-sheet` patterns) and adding only master-specific ones (`.master-head`, `.master-cols`, `.master-col`, `.ent-1`…`.ent-6` tones, `.master-popup`). Delete `templates/shell.html` if no route renders it.

- [ ] **Step 4: Run the full suite and look at it**

Run: `uv run pytest -q -p no:cacheprovider --ignore=tests/test_local_backup.py --ignore=tests/test_local_service.py`
Expected: all pass.

Then start the preview (`.claude/launch.json` → `oneos-ui-preview`, a synthetic demo vault). Check `/` at 375px, 768px and 1280px in light and dark:
- the columns are in order;
- a card click opens the popup with the body;
- a chip filters;
- Sort changes the order;
- "Show all" appears past 10 Done.

- [ ] **Step 5: Run the public gates**

Run: `uv run python -m tools.public_repo_audit --repo .`, then `tools/run_gitleaks.sh .`
Expected: `CLEAN`, and "no leaks found".

- [ ] **Step 6: Commit**

```bash
git add templates static app tests CHANGELOG.md docs/STATUS.md
git commit -m "Home: master view layout, popup, filters, phone and tablet"
```

- [ ] **Step 7: Run the local private integration gate before merge.** Follow `BUILD.md` against the owner's vault, on the trusted local machine only. Record the result in the PR body.
