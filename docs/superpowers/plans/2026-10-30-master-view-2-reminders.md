# Master view 2: reminders and Needs attention — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The owner can attach a reminder (date, time, subject) to a Waiting or To review card from its popup, and the home page shows every open reminder in a colour-coded "Needs attention" strip above the board.

**Architecture:** A new `app/reminders.py` owns a small SQLite store in the private authentication directory (never the vault). The shell route reads it and passes reminders into `load_master`, which attaches them to cards and builds the strip; nothing is written on page load. Two POST routes save and remove a reminder; the Outbox approval route re-keys a reminder when it files an inbox item.

**Tech Stack:** Python 3.12, FastAPI, Jinja2, HTMX + `alpine-morph`, Alpine.js (vendored), `sqlite3` (stdlib), pytest with synthetic vaults.

**Spec:** `docs/superpowers/specs/2026-10-29-master-view-design.md` — decisions 6, 7 (To review "Review by…", Waiting sorted by deadline), 8 (reminder box), "Reminders and review flags", anti-goals, "Tests the build must prove". Plan 1 (`2026-10-29-master-view-1-read-only.md`) is merged; this plan builds on `app/master.py`, `templates/home.html`, `templates/blocks/master_popup.html`.

**Not in this plan:** stage moves, drag, draft editing, comments, the "To review" flag on a filed note (plan 3); Mac Calendar events (plan 4).

## Global Constraints

- Saving, editing, or removing a reminder writes nothing to the vault and makes no Git commit.
- Nothing is written on page load, filter, sort, refresh, or opening a popup — the store file is created on the first save, not on read.
- The store lives at `<ONEOS_AUTH_STATE_DIR>/reminders.sqlite3` (beside `owner.sqlite3`, so the owner-run `backup` already copies it). Same safety rules as `app/auth.py` `AuthStore`: no symlinked component, directory owned by the process uid with no group/other bits, file mode `0600`, regular file, one link.
- If `ONEOS_AUTH_STATE_DIR` is unset or the store is unsafe, the page still renders: no reminders, the strip says "Reminders are unavailable", and the popup's reminder box is disabled. Never a 500.
- A reminder's time is the local wall time as typed in the browser, stored as ISO 8601 with the UTC offset the browser reports (examples here use a synthetic `+02:00`) (e.g. `2026-11-04T11:00:00+02:00`). Overdue / within a day are decided on the absolute instant; labels show the stored wall time ("Wed 4 Nov 11:00"). No server-side timezone setting.
- Thresholds: **overdue** = due before now (red `--err-ink` on `--err-bg`, alarm-clock icon, first); **soon** = due within the next 24 hours (amber `--warn`, bell icon, next); **later** = everything else (grey `--ink-dim`, calendar icon, last). Within each group, soonest first.
- Reminders are allowed only on cards in the **Waiting** or **To review** stage. A card in **Done** (`done` or `active`) resolves its reminder: it is not shown, and its row is kept untouched.
- Approving a proposal through Outbox files the item with its ingest status `active`, i.e. Done: the review happened, so its "Review by" reminder is resolved and leaves the strip. The row still follows the card (Task 5) so plan 3's moves can bring it back.
- Paths change on an entity rename (`app/rename.py`, axis `entity`): the rename re-keys reminders in the same run (Task 5), so the rename test's "every reference resolves" holds for reminders too.
- Subject: required, trimmed, 1–120 characters, no line breaks; defaults to the card's title in the form.
- Key = the card's `relative_path` (vault-relative, as `entity_cards` returns it); a proposed card uses its inbox path, which it shares. The row also stores the entity slug.
- `app/master.py` must not import `app.console_errors` (invariant test). Every new route uses `@console_route` with a declared catch family and failure contracts (`tests/test_console_invariants.py`).
- Copy: strip heading "Needs attention"; card date line "Due Wed 4 Nov 11:00" (Waiting) / "Review by Wed 4 Nov 11:00" (To review); orphan line "Card moved or removed"; buttons "Save reminder", "Remove".
- Never write "/home/" followed by a path segment anywhere (private-path audit). Run tests as `env -u GIT_EDITOR uv run pytest ...`.

## Review Focus

1. **A reminder for a card that was moved or deleted outside the master view** — must still appear (subject + entity + "Card moved or removed") with a working inline Remove on the strip card, never vanish. Pinned in Tasks 2 and 3.
2. **The store missing, unreadable, or a symlink** (test/disabled mode, a broken install) — page renders, no write happens, strip says unavailable. Pinned in Tasks 1 and 3.
3. **A forged or stale save** (path not a card of that entity, card no longer Waiting/To review, entity slug mismatch) — refused with a described error, nothing stored. Pinned in Task 4.
4. **Times around the boundaries** — exactly now, now+24h, and a reminder saved with a past time (shows red at once). Pinned in Task 2.
5. **An entity that fails to read** — its reminders still show in the strip (without a link), not as "moved or removed". Pinned in Task 2.

---

### Task 1: Reminder store

**Files:**
- Create: `app/reminders.py`
- Modify: `app/console_errors.py` (map `ReminderStoreUnavailable` → E-UNAVAILABLE, `InvalidReminder` → the existing code used for a refused form value; pick the closest existing "refusal" entry, add no new code)
- Test: `tests/test_reminders.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class Reminder: key: str; entity: str; subject: str; due_at: datetime  # timezone-aware`
  - `class ReminderStoreUnavailable(Exception)`, `class InvalidReminder(ValueError)`
  - `class ReminderStore: __init__(self, directory: Path)`; `all(self) -> dict[str, Reminder]` (returns `{}` when the file does not exist, creates nothing); `save(self, reminder: Reminder) -> None` (upsert by key; creates the file `0600` on first use); `remove(self, key: str) -> None` (no-op if absent); `rekey(self, old: str, new: str) -> None` (no-op if `old` absent; replaces an existing `new`)
  - `reminder_store() -> ReminderStore | None` — from `ONEOS_AUTH_STATE_DIR`; `None` when unset
  - `parse_due(date: str, time: str, offset_minutes: str) -> datetime` — `"2026-11-04"`, `"11:00"`, `"120"` → `2026-11-04T11:00+02:00`; raises `InvalidReminder`
  - `clean_subject(text: str) -> str` — trims; raises `InvalidReminder` if empty, > 120 chars, or contains `\r`/`\n`
  - Each public method carries a `failure_contract` declaring `ReminderStoreUnavailable` (and `InvalidReminder` where it can raise).

- [ ] **Step 1: Write the failing tests** in `tests/test_reminders.py`, each with a `tmp_path` directory chmod `0700`:
  - `test_all_on_a_new_directory_is_empty_and_creates_nothing` — `all() == {}` and `list(dir.iterdir()) == []`.
  - `test_save_then_all_round_trips_the_wall_time_and_offset` — saved `due_at.isoformat() == "2026-11-04T11:00:00+02:00"`.
  - `test_save_creates_the_file_owner_only` — `stat.S_IMODE(mode) == 0o600`.
  - `test_save_upserts_by_key`, `test_remove_is_idempotent`, `test_rekey_moves_and_replaces`.
  - `test_unsafe_store_is_unavailable` — parametrised: directory mode `0o755`; `reminders.sqlite3` is a symlink; file mode `0o644` → `ReminderStoreUnavailable` from `all()` and `save()`.
  - `test_parse_due` — the example above; `("2026-02-30", "11:00", "120")`, `("2026-11-04", "25:00", "0")`, `("2026-11-04", "11:00", "x")` raise `InvalidReminder`.
  - `test_clean_subject` — `"  Pay card  "` → `"Pay card"`; `""`, `"a\nb"`, `"x" * 121` raise.
  - `test_store_is_never_written_into_the_vault` — save with a synthetic vault present; the vault's `git status --porcelain` is empty before and after.
  - `test_reminder_store_is_none_without_the_env` — `monkeypatch.delenv("ONEOS_AUTH_STATE_DIR", raising=False)`.
- [ ] **Step 2: Run** `env -u GIT_EDITOR uv run pytest tests/test_reminders.py -q` — expect failures (module missing).
- [ ] **Step 3: Implement** `app/reminders.py`. Table `reminders(key TEXT PRIMARY KEY, entity TEXT NOT NULL, subject TEXT NOT NULL, due_at TEXT NOT NULL, updated_at TEXT NOT NULL)`. Reuse the checking approach of `AuthStore._check` (copy the shape, not the auth-specific parts); open read-only (`mode=ro`) in `all()`; `BEGIN IMMEDIATE` for writes. Add the two `console_errors` mappings.
- [ ] **Step 4: Run** the same command plus `tests/test_console_invariants.py` — all pass.
- [ ] **Step 5: Commit** — `Reminders: a private store beside the auth database`.

---

### Task 2: Reminders in the master view data

**Files:**
- Modify: `app/master.py`
- Test: `tests/test_master.py`

**Interfaces:**
- Consumes: `Reminder` (Task 1).
- Produces:
  - `MasterCard` gains `reminder: Reminder | None = None`.
  - `@dataclass(frozen=True) class AttentionCard: reminder: Reminder; entity_label: str; tone: int; level: str  # "overdue" | "soon" | "later"; card: MasterCard | None  # None = moved/removed or entity unreadable; unreadable: bool`
  - `MasterPage` gains `attention: tuple[AttentionCard, ...]` and `reminders_available: bool`.
  - `load_master(vault, scope_for, *, entities, sort, done_all, now, reminders: Mapping[str, Reminder] | None)` — `now` must be timezone-aware; `reminders=None` means the store is unavailable (`reminders_available=False`, empty strip).
  - `due_label(due: datetime) -> str` — `"Wed 4 Nov 11:00"` from the stored wall time (`%a %-d %b %H:%M`).

- [ ] **Step 1: Write the failing tests** (reuse `_vault` / `_note`; `now = datetime(2026, 11, 3, 10, 0, tzinfo=timezone(timedelta(hours=2)))`):
  - `test_strip_orders_overdue_then_soon_then_later` — reminders at now−1 h, now+2 h, now+3 d on three Waiting cards → `[a.level for a in page.attention] == ["overdue", "soon", "later"]`.
  - `test_strip_boundaries` — due exactly `now` → `"soon"`; `now + 24h` → `"later"`; `now − 1 s` → `"overdue"`.
  - `test_done_card_resolves_its_reminder` — reminder on a `status: done` note and on a `status: active` note → not in `page.attention`.
  - `test_moved_card_reminder_stays_visible` — reminder keyed to a path no card has → one `AttentionCard` with `card is None` and `unreadable is False`.
  - `test_unreadable_entity_reminders_are_not_called_moved` — entity made unreadable as in the existing failure test → its reminder appears with `card is None` and `unreadable is True`.
  - `test_strip_follows_the_entity_filter` — `entities=("alpha",)` hides beta's reminder.
  - `test_waiting_and_review_put_reminders_first_soonest_first` — under `sort="updated"`; under `sort="title"` the order is by title only.
  - `test_card_carries_its_reminder` and `test_store_unavailable_gives_an_empty_strip` (`reminders=None` → `page.reminders_available is False`, `page.attention == ()`).
  - `test_due_label` — `"Wed 4 Nov 11:00"`.
  - Update existing callers/tests to pass an aware `now` and `reminders={}`.
- [ ] **Step 2: Run** `env -u GIT_EDITOR uv run pytest tests/test_master.py -q` — new tests fail.
- [ ] **Step 3: Implement.** Attach reminders by key after cards are built; build the strip from all reminders whose entity is in the filter (or no filter), skipping Done-stage cards; order by (level rank, `due_at`). Reminder-first ordering applies only to the `review` and `waiting` columns under `sort == "updated"`.
- [ ] **Step 4: Run** `tests/test_master.py tests/test_console_invariants.py` — pass.
- [ ] **Step 5: Commit** — `Master view: reminders on cards and the Needs attention order`.

---

### Task 3: Needs attention strip and card dates on the home page

**Files:**
- Modify: `app/main.py` (`shell`: read `reminder_store()`; pass `reminders=store.all()` or `None` if the store is `None` or raises `ReminderStoreUnavailable`; `now=datetime.now(timezone.utc)`)
- Modify: `templates/home.html`, `templates/blocks/master_card.html`, `templates/_icon_sprite.html` (add `i-alarm` and `i-bell`, 24×24 stroke icons matching the sprite's style), `static/app.css`
- Test: `tests/test_master.py`

**Interfaces:**
- Consumes: `MasterPage.attention`, `reminders_available`, `MasterCard.reminder`, `due_label` (Task 2); `reminder_store`, `ReminderStoreUnavailable` (Task 1).
- Produces: `#master-attention` section inside the `#master` morph wrapper (so the 60 s refresh updates it); each strip card is a `<button type="button" class="attention-card is-{level}">` that opens the card popup exactly like a board card (same `hx-get`, `hx-target="#master-popup"`); orphan/unreadable cards are not buttons; each carries a small inline "Remove" button that `hx-post`s to Task 4's remove route (`path` = the reminder key) and then refreshes `#master`.

- [ ] **Step 1: Write the failing tests:**
  - `test_home_shows_needs_attention_in_order_with_icons` — three reminders → the strip's cards appear in overdue, soon, later order with classes `is-overdue`/`is-soon`/`is-later` and `#i-alarm`/`#i-bell`/`#i-calendar`; each shows subject, entity label, `due_label`.
  - `test_home_with_no_reminders_has_no_strip` — no `#master-attention` element.
  - `test_home_without_a_store_says_reminders_are_unavailable` — `ONEOS_AUTH_STATE_DIR` unset → 200, text "Reminders are unavailable".
  - `test_home_with_an_unsafe_store_still_renders` — store dir mode `0o755` → 200, same text, no file created.
  - `test_waiting_card_shows_due_and_review_card_shows_review_by` — "Due Wed 4 Nov 11:00" / "Review by Wed 4 Nov 11:00".
  - `test_orphan_attention_card_is_not_a_link` — contains "Card moved or removed", no `hx-get` on that card, and has an inline Remove posting to `/master/reminder/{entity}/remove` with its key.
  - `test_page_load_writes_nothing` — store directory and vault unchanged after `GET /`, `GET /?entity=…&sort=title`, and a popup GET.
- [ ] **Step 2: Run** them — fail.
- [ ] **Step 3: Implement.** Strip markup sits between the header and the filters; compact cards (not full width), horizontally scrollable at phone width (no page-level horizontal scroll at 375 px), light and dark via existing tokens.
- [ ] **Step 4: Run** `tests/test_master.py tests/test_app.py tests/test_console_invariants.py` — pass. Check in the browser (demo vault, a temp `ONEOS_AUTH_STATE_DIR`) at 1280 and 375 px, light and dark.
- [ ] **Step 5: Commit** — `Home: Needs attention strip and reminder dates on cards`.

---

### Task 4: Reminder box in the popup, save and remove routes

**Files:**
- Modify: `app/main.py` (two routes), `templates/blocks/master_popup.html`, `templates/home.html` (refresh `#master` after a save), `static/app.css`
- Test: `tests/test_master.py`

**Interfaces:**
- Consumes: Task 1 store and parsers; `entity_cards`, `stage_of` (existing).
- Produces:
  - `POST /master/reminder/{entity}` — form `path`, `subject`, `date`, `time`, `offset` (minutes east of UTC, filled by the browser from `-new Date().getTimezoneOffset()`). Re-reads `entity_cards(scope, vault)`, requires `path` to match a card of that entity whose `stage_of` is `waiting` or `review`, then `save`. Responds with the re-rendered popup fragment and `HX-Trigger: master-refresh`.
  - `POST /master/reminder/{entity}/remove` — form `path` (the reminder key); removes it if the stored row's entity is `{entity}`, with no card check (so orphans can be removed); responds with the popup fragment when the card exists, otherwise an empty 200, and always `HX-Trigger: master-refresh`.
  - Both: `@console_route(catches=..., surface="fragment-only", services=(entity_cards, ReminderStore.save | .remove, ...))`; refusals render `blocks/master_popup_error.html`.
  - The popup shows the reminder box only for Waiting and To review cards: date, time, subject (prefilled with the existing reminder or the card title), "Save reminder", and "Remove" when one exists; disabled with "Reminders are unavailable" when the store is unavailable.

- [ ] **Step 1: Write the failing tests:**
  - `test_save_reminder_from_the_popup` — POST for a Waiting card → 200; store has the reminder with the typed wall time and offset; vault `git status` and `git rev-parse HEAD` unchanged; response has `HX-Trigger: master-refresh`.
  - `test_remove_reminder` — then remove → store empty, vault unchanged.
  - `test_remove_orphan_reminder` — a key no card has → removed; `test_remove_refuses_another_entitys_reminder` — row entity `beta`, POST to `/master/reminder/alpha/remove` → refused, row kept.
  - `test_save_is_refused_for_a_draft_or_done_card`, `..._for_a_path_that_is_not_a_card`, `..._for_another_entitys_path`, `..._for_an_empty_or_multiline_subject`, `..._for_a_bad_date` — described error fragment with a Close button; store unchanged.
  - `test_save_without_a_store_is_refused_not_500` — env unset → described error, 200 fragment.
  - `test_popup_shows_reminder_box_only_for_waiting_and_review` and `test_popup_prefills_existing_reminder`.
  - `test_save_requires_csrf` — when auth is required, a POST without the token is refused (follow the existing CSRF test pattern for `/board/{entity}/capture`).
- [ ] **Step 2: Run** — fail.
- [ ] **Step 3: Implement.** `home.html` listens for `master-refresh` on the body and re-issues the `#master` relative `hx-get` (keep it relative).
- [ ] **Step 4: Run** `tests/test_master.py tests/test_console_invariants.py tests/test_app.py` — pass; browser check: save, see the strip update, remove.
- [ ] **Step 5: Commit** — `Master view: set and remove a reminder from the popup`.

---

### Task 5: A reminder follows its card through Outbox approval and entity rename

**Files:**
- Modify: `app/main.py` (`_outbox_approve_response`, after a successful `approve`); `app/reminders.py` (`rename_entity`); `app/rename.py` (after `apply_rename` commits an `entity` rename)
- Test: `tests/test_master.py` (or the Outbox test file that already covers approval)

**Interfaces:**
- Produces: `ReminderStore.rename_entity(self, old: str, new: str) -> int` — rewrites `entity` and the leading `old/` path segment of every key for that entity; returns rows changed.
- Consumes: `ReminderStore.rekey` (Task 1); the approval result's source and destination paths (`approve` returns `ClassificationActionResult = OutboxProposal | SpentAction` in `app/outbox.py`; a `SpentAction` means nothing new was filed, so no re-key; do not change `approve` or its receipt).

- [ ] **Step 1: Write the failing tests:**
  - `test_approving_a_proposal_moves_its_reminder` — reminder on an inbox card with a pending proposal; approve via `POST /outbox/{entity}/approve` with the right `review_sha256` → the store's key is the destination `relative_path`; the reminder now shows on the filed card.
  - `test_rekey_failure_does_not_fail_the_approval` — store unavailable → approval still commits and responds as before; the reminder shows as "Card moved or removed".
  - `test_approval_commit_is_unchanged` — the commit's file list and receipt are byte-identical in shape to an approval with no reminder.
  - `test_approved_review_reminder_is_resolved` — after approval the filed card is Done (`active`) and the reminder is not in the strip, but its row exists under the new key.
  - `test_entity_rename_rekeys_reminders` — in the existing rename-test style: throwaway entity with a reminder, `apply_rename` axis `entity` with `ONEOS_AUTH_STATE_DIR` set → the reminder's key and entity use the new slug and it shows on its card; `.sensitive/` read still denied.
  - `test_entity_rename_without_a_store_still_succeeds` — env unset → the rename commits as before and prints that reminders were not updated.
- [ ] **Step 2: Run** — fail.
- [ ] **Step 3: Implement** both re-keys strictly after the vault commit succeeded; catch `ReminderStoreUnavailable` there and continue (the reminder then shows as "Card moved or removed"). `rename.py` reads the store via `reminder_store()`; it never writes the store on a dry run.
- [ ] **Step 4: Run** the Outbox, master and invariant test files, then the full suite `env -u GIT_EDITOR uv run pytest -q --ignore=tests/test_local_backup.py --ignore=tests/test_local_service.py` and `uv run python -m tools.public_repo_audit --repo .` (CLEAN).
- [ ] **Step 5: Commit** — `Outbox: a filed item keeps its reminder`.

---

## Before merge

- Private integration gate from `BUILD.md` §3 (vault unittests, `check_v2.py`, `public_repo_audit --vault … --history`, vault byte-identical) — this plan reads vault structure.
- Owner check on the running install after update: set a reminder, see it in Needs attention, remove it.

## Rulings in this plan (owner may overturn)

1. The store file sits in the authentication directory (spec: "beside the authentication database"), so no Compose or mount change and the existing backup covers it.
2. Reminders only on Waiting and To review cards in this plan; the "To review" flag on a filed note comes with moves in plan 3.
3. The strip follows the entity filter chips, like the board.
4. A reminder whose card was moved or removed outside OneOS stays in the strip as "Card moved or removed" with an inline Remove, rather than disappearing.
7. Outbox approval files an item as `active` (Done), so it resolves that item's "Review by" reminder; the reminder row still follows the card.
5. Under the default "Updated" sort, Waiting and To review show cards with a reminder first, soonest first; "Title" sort ignores reminders.
6. A reminder can be removed (spec names only Save); removing is needed so a mistaken reminder can go.
