# Master view 4: reminders in the Mac's Calendar — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every reminder the owner saves in OneOS appears as one event in a calendar named "OneOS" in the Mac's Calendar app, at the reminder time with an alert. Moving or editing the reminder updates that event, and Done (or deleting the reminder) removes it. OneOS stores no calendar or iCloud credential.

**Architecture:** The app container cannot reach Calendar, so a host-side step beside `tools/local_service` does the writes. A user LaunchAgent (`local.oneos.calendar`) watches the private auth folder, where `reminders.sqlite3` lives, and runs `calendar-sync`. That command reads the store read-only and reconciles the "OneOS" calendar through a small JavaScript for Automation (JXA) script run by `/usr/bin/osascript`. A pure sync engine decides what to create, update and remove, and it is tested against a fake calendar. The app gains only a stable event id per reminder and a Home deep link that opens the reminder's card.

**Tech Stack:** Python 3.12 (host tools under `tools/`, the app's `app/reminders.py`), macOS `launchd` (`WatchPaths`, `RunAtLoad`), `/usr/bin/osascript -l JavaScript` (no new dependency), FastAPI/Jinja for the deep link, pytest with fakes only.

**Spec:** `docs/superpowers/specs/2026-10-29-master-view-design.md`. The relevant parts are "Getting reminders into the calendar", the anti-goals (no credentials, no network call on page load), "Tests the build must prove" ("The Calendar step creates one event per reminder, updates it in place, removes it on Done, and never touches another calendar (tested against a fake Calendar adapter, not the real app)"), and the owner decision of 2026-10-29 ("Calendar permission on the Mac is OK"). This plan builds on plans 1–3 (merged): `app/reminders.py` (`ReminderStore.all()`, the `Reminder` dataclass) and `tools/local_service.py` (`install_login`, `diagnostics`, `private_file`, `safe_path`).

**Facts checked on the owner's Mac (2026-11-04):**
- The store is `$ONEOS_STATE_DIR/auth/reminders.sqlite3` on the host (`compose.yaml` mounts `…/auth` as `/state/auth`).
- A file created inside the container in that folder raises a host kqueue event on the folder, so `WatchPaths` on the folder sees app writes.
- SQLite runs in its default rollback-journal mode, so every write transaction adds and removes a `-journal` entry in the folder.
- `/usr/bin/osascript` is present.
- Done deletes the reminder row (`ReminderStore.remove`). An inbox → filed move re-keys it (`rekey`).

## Global Constraints

- One event per reminder, only in a calendar whose name is exactly `OneOS`. The sync never creates, renames or deletes a calendar, and never reads or writes any other calendar. An event in "OneOS" without a OneOS id marker is the owner's own event and is never changed or removed.
- Event fields:
  - title = the reminder subject;
  - start = `due_at` (the same instant, converted to UTC for the script);
  - end = start + 15 minutes;
  - one display alert at the start time;
  - notes = `Open in OneOS: https://localhost:8443/?reminder=<id>` followed by a line `OneOS id: <id>`.
- The id is `calendar_event_id(key)` = `"oneos-" + sha256(key.encode("utf-8")).hexdigest()[:32]`. No vault path, entity name or note title goes into the event beyond the subject the owner typed.
- The sync is one-way (OneOS → Calendar). An owner edit to a OneOS event in Calendar is overwritten on the next change. An event the owner deleted comes back on the next forced sync (login) or reminder change.
- The store is read only through `ReminderStore(<state>/auth).all()`. The sync never writes the store, the vault or Git.
  - Store missing, unreadable, tampered, or busy (a `-journal` file is present): change nothing in Calendar this run, and record `skipped`.
  - Only a successfully read store, including an empty one, may remove events.
- Values reach the JXA script only as one JSON argument (`osascript -l JavaScript tools/calendar_mac.js <command> <json>`), never interpolated into script text. Script output is JSON on stdout. A non-zero exit or unparsable output raises `CalendarUnavailable` with no subject in its message.
- Nothing is logged or written that contains a subject: `launchd` stdout/stderr go to `/dev/null`. Status goes to `<state>/calendar/status.json` (owner-only 0600 via `write_json`), holding only `{state, at, created, updated, removed, code}`.
- The skip fingerprint is `<state>/calendar/last-sync.json` (0600): the sha256 of the sorted desired events. When it is unchanged and `--force` is not given, the sync exits without starting `osascript`, so Calendar.app is not launched on every unrelated auth write.
- `launchd` job:
  - label `local.oneos.calendar`;
  - `ProgramArguments` `[python, -m, tools.local_service, --state-dir, <state>, calendar-sync]`;
  - `WatchPaths` `[<state>/auth]`, `RunAtLoad` true, `ThrottleInterval` 30;
  - stdout and stderr to `/dev/null`;
  - installed only by the explicit `install-calendar` command, with the same preflight, refuse-if-exists and rollback rules as `install_login`.
  - `launchd` cannot vary arguments per trigger, so `calendar-sync` decides for itself when to force (ignore the fingerprint and re-list the calendar). It forces when `<state>/calendar/boot-id` does not match `sysctl -n kern.boottime`, or when the last forced run was more than 24 hours ago. A forced run updates both markers. `install-calendar` always forces.
- `doctor` never fails on calendar state (like backup). `status`/`doctor` report `calendar` as one of:
  - `off` — job not installed;
  - `on` — last run ok or skipped;
  - `needs_setup` — the "OneOS" calendar is missing or permission is denied, with the action text "In Calendar, create a calendar named OneOS (in iCloud to reach your iPhone), then run install-calendar again in Terminal.";
  - `failed` — anything else.
- Owner steps, documented in `docs/LOCAL-OPERATIONS.md`:
  1. In Calendar, create a calendar named `OneOS`. Choose iCloud for it to reach the iPhone.
  2. In Terminal, run `OneOS.command install-calendar`. It first runs one forced sync in the foreground, so macOS asks once for permission to control Calendar, then installs the job.
  3. `OneOS.command remove-calendar` unloads and removes the job and leaves events in place. Deleting the "OneOS" calendar in Calendar removes them all.
- The Home deep link `GET /?reminder=<id>`:
  - looks the id up among the current reminders;
  - when found, renders Home with that card's popup opening on load (the same `hx-get` the card uses);
  - when not found, renders Home with the notice "That reminder is no longer in OneOS.";
  - writes nothing, is behind sign-in like every route, and accepts only `oneos-[0-9a-f]{32}` (anything else is ignored).
- Every new or changed route keeps `@console_route` and its declared catches (`tests/test_console_invariants.py`). New host-tool code follows `tools/local_service.py` idioms: `safe_path`, `private_file`, `write_json`, and a 30 s subprocess timeout.
- No new dependency. No new vault convention field. Never write "/home/" followed by a path segment. Run tests as `env -u GIT_EDITOR uv run pytest ...` (locally `tests/test_local_backup.py` and `tests/test_local_service.py` have 2 known environment failures on `main`; compare against `main`, not zero).

## Review Focus

1. **The store is missing, half-written or unreadable at sync time.** No event is removed. Only a good read can delete. Pinned in Task 2.
2. **The owner's own events.** A marker-less event in "OneOS", or any event in another calendar, is never touched. Pinned in Task 2 (fake) and Task 1 (script filters by the marker).
3. **Reminder offsets, DST and travel.** An event lands at the instant the owner chose, whatever offset `due_at` was saved with. Pinned in Task 2.
4. **The "OneOS" calendar is missing or permission is denied.** Nothing is written anywhere else, status says `needs_setup` with the owner action, and `doctor` still passes. Pinned in Tasks 1 and 3.
5. **Many unrelated auth-folder writes (sign-ins, sessions).** No `osascript` runs while the desired events are unchanged. Pinned in Task 2.

---

### Task 1: Calendar adapter for the Mac (JXA), with a live check

**Files:**
- Create: `tools/calendar_mac.js`, `tools/calendar_mac.py`
- Test: `tests/test_calendar_mac.py`

**Interfaces:**
- Produces, in `tools/calendar_mac.py`:
  - `@dataclass(frozen=True) class CalendarEvent: id: str; title: str; start: datetime; end: datetime; notes: str`. All datetimes are timezone-aware UTC.
  - `class CalendarUnavailable(Exception)` with `.code`, one of `"no_calendar"`, `"permission"`, `"script"`.
  - `class MacCalendar` with:
    - `__init__(self, *, run=subprocess.run, name="OneOS")`;
    - `events() -> dict[str, CalendarEvent]`, OneOS-marked events only, keyed by id;
    - `create(ev)`, `update(ev)`, `delete(event_id)`.
  - `main(argv) -> int` with the single command `selftest`: create an event "OneOS test" one hour from now, confirm `events()` lists it, delete it, and print `ok` or the error code. It is used only for the live check.
  - Script commands are `list`, `create`, `update` and `delete`. The script finds the calendar by exact name, and exits with code 3 when it is missing and code 4 when the Automation permission is denied (`-1743`). It finds an event by its `OneOS id:` line in the notes.

- [ ] **Step 1: Failing tests.** The tests use a fake `run` that records argv and returns canned stdout and exit codes.
  - argv is exactly `["/usr/bin/osascript", "-l", "JavaScript", <abs path of calendar_mac.js>, "<cmd>", "<json>"]`.
  - A subject containing `"`, `'`, `\`, a newline and emoji round-trips only through the JSON argument.
  - `list` output parses into `CalendarEvent`s.
  - Exit 3 → `CalendarUnavailable(code="no_calendar")`, exit 4 → `"permission"`, other non-zero or bad JSON → `"script"`. No subject appears in any exception text.
  - `timeout=30`.
- [ ] **Step 2: Run** `env -u GIT_EDITOR uv run pytest -q tests/test_calendar_mac.py`. Expect failures.
- [ ] **Step 3: Implement** the JS (JXA `run(argv)`, `Application('Calendar')`; use `JSON.parse(argv[1])`, never `eval`) and the Python wrapper.
- [ ] **Step 4: Run** the tests. Expect them to pass.
- [ ] **Step 5: Live check on the owner's Mac (controller with the owner present; a gate before Task 2).**
  - The owner creates the "OneOS" calendar.
  - Run once from Terminal: `python -m tools.calendar_mac selftest`. It creates one event "OneOS test", lists it, then deletes it. The owner presses Allow when macOS asks.
  - Then run the same selftest from a throwaway LaunchAgent (`local.oneos.calendar-probe`, `RunAtLoad`) and confirm it succeeds without a new prompt. Remove the probe afterwards.
  - Record the result in the ledger. **If the LaunchAgent run cannot get permission, stop and ask the owner. Do not work around macOS privacy controls.**
- [ ] **Step 6: Commit** "Calendar: a JXA adapter for the Mac's Calendar app".

### Task 2: Sync engine

**Files:**
- Modify: `app/reminders.py`, to add `calendar_event_id(key: str) -> str`. It lives here so the app and the host tool share it.
- Create: `tools/calendar_sync.py`
- Test: `tests/test_calendar_sync.py`

**Interfaces:**
- Consumes: `CalendarEvent`, `CalendarUnavailable` and `MacCalendar` (Task 1); `ReminderStore`, `Reminder` and `ReminderStoreUnavailable` (`app/reminders.py`).
- Produces:
  - `desired_events(reminders: dict[str, Reminder]) -> dict[str, CalendarEvent]`
  - `plan_sync(desired, existing) -> SyncPlan(create: list, update: list, delete: list[str])`. An update is needed only when the title, start, end or notes differ.
  - `run_sync(state_dir: Path, calendar, *, force: bool) -> SyncReport(state: "ok"|"skipped"|"needs_setup"|"failed", created: int, updated: int, removed: int, code: str|None)`. It writes `status.json`, and writes `last-sync.json` only after an `ok` run.

- [ ] **Step 1: Failing tests.** They use a `FakeCalendar` holding OneOS-marked events plus a separate list of "owner" events.
  - A new reminder creates exactly one event with the Global Constraints fields.
  - A subject or time change updates that event in place, with no duplicate.
  - Running twice changes nothing the second time.
  - A removed reminder (Done) removes its event.
  - A re-keyed reminder deletes the old id and creates the new one, leaving one event.
  - Owner events are never touched.
  - `due_at` saved at `+05:30`, at `-08:00`, and across a DST date all produce the same UTC start as `due_at.astimezone(utc)`.
  - The store is missing, has a `-journal` present, or raises `ReminderStoreUnavailable`: state `skipped`, and the fake sees zero calls.
  - An empty but readable store removes all OneOS events.
  - `CalendarUnavailable("no_calendar")` gives state `needs_setup` and creates nothing.
  - An unchanged fingerprint without `force` makes zero fake calls; with `force` it lists and reconciles.
  - `status.json` and `last-sync.json` are mode 0600, and neither contains a subject.
  - `calendar_event_id` is stable and matches `oneos-[0-9a-f]{32}`.
- [ ] **Step 2: Run** `env -u GIT_EDITOR uv run pytest -q tests/test_calendar_sync.py`. Expect failures.
- [ ] **Step 3: Implement.** The fingerprint is sha256 over `json.dumps(sorted(desired as plain dicts), sort_keys=True)`. Check for the journal before reading.
- [ ] **Step 4: Run** the tests. Expect them to pass.
- [ ] **Step 5: Commit** "Calendar: reconcile OneOS reminders with the OneOS calendar".

### Task 3: Service commands, LaunchAgent, status and docs

**Files:**
- Modify: `tools/local_service.py`, `docs/LOCAL-OPERATIONS.md`, `docs/ARCHITECTURE.md` (tools list), `CHANGELOG.md`
- Test: `tests/test_local_service.py`

**Interfaces:**
- Consumes: `run_sync` (Task 2) and `MacCalendar` (Task 1).
- Produces:
  - commands `calendar-sync`, `install-calendar` and `remove-calendar`;
  - `install_calendar(config, *, sync=run_sync, calendar_factory=MacCalendar)`;
  - `remove_calendar(config)`;
  - `calendar_diagnostic(config) -> finding`.

- [ ] **Step 1: Failing tests.** They follow the `test_install_login_*` patterns, with fake `launchctl` and fake `sysctl`.
  - `install-calendar` runs one forced sync first and refuses to install when it ends `needs_setup` or `failed`, printing the action text. On success it writes `local.oneos.calendar.plist` with exactly the Global Constraints keys and bootstraps it.
  - It refuses an existing plist or loaded job, and it rolls back on a bootstrap failure.
  - `remove-calendar` boots out and removes only that plist.
  - `calendar-sync` forces itself when the boot id changed or the last forced run was more than 24 hours ago, and otherwise does not.
  - `status` includes `calendar` with states `off`/`on`/`needs_setup`/`failed`.
  - `doctor` returns 0 even when `calendar` is `needs_setup` or `failed`.
  - `install-login` is unchanged and still installs only `local.oneos.login`.
- [ ] **Step 2: Run** `env -u GIT_EDITOR uv run pytest -q tests/test_local_service.py`. Expect the new tests to fail. The 2 known environment failures stay as they are on `main`.
- [ ] **Step 3: Implement.** Add the three commands to `main()`'s choices: `calendar-sync` does not take the operation lock, and the other two do. In the docs, write the three owner steps and how to turn the feature off. In `CHANGELOG.md` under Unreleased, add: "Reminders reach the Mac's Calendar (plan 4 of 4): one event per reminder in a calendar named OneOS, updated when the reminder changes and removed on Done; set up once with `install-calendar`."
- [ ] **Step 4: Run** the full suite. Expect the same known failures as `main`, and no new ones.
- [ ] **Step 5: Commit** "Calendar: install-calendar, calendar-sync and status".

### Task 4: Home deep link from a calendar event

**Files:**
- Modify: `app/main.py` (the `shell` route), `templates/home.html`
- Test: `tests/test_master.py`

**Interfaces:**
- Consumes: `calendar_event_id` (Task 2) and `reminder_store()`.
- Produces: `GET /?reminder=<id>` per Global Constraints.

- [ ] **Step 1: Failing tests.**
  - With a saved reminder, `/?reminder=<its id>` renders Home containing exactly one load-triggered `hx-get` equal to that card's popup URL.
  - An unknown well-formed id shows "That reminder is no longer in OneOS.".
  - A malformed id (`../x`, `oneos-ZZ`, 1 000 chars) renders plain Home with no notice and no lookup.
  - The vault HEAD and the store bytes are unchanged after each request.
  - The route invariants still pass.
- [ ] **Step 2: Run** `env -u GIT_EDITOR uv run pytest -q tests/test_master.py tests/test_console_invariants.py`. Expect failures.
- [ ] **Step 3: Implement.** Use a `reminder: str = ""` query parameter. Map the id to its key with one `ReminderStore.all()` read. A store failure renders plain Home with the notice "Reminders are unavailable right now."
- [ ] **Step 4: Run** the tests. Expect them to pass.
- [ ] **Step 5: Commit** "Master view: a calendar event opens its card".

## Before merge

- Run the full suite and `tools.public_repo_audit --history` (CLEAN).
- Run the private gate (`BUILD.md` §3). The vault must be byte-identical before and after.
- Live end-to-end on the owner's Mac, with the owner present:
  1. `install-calendar`, then save a reminder on a card. The event appears in "OneOS" at that time with an alert.
  2. Change the reminder time. The same event moves.
  3. Move the card to Done. The event is gone.
  4. Open the event's link. The card's popup opens.
  5. An owner-made event in "OneOS" survives all of the above.
  6. Afterwards, `remove-calendar` is left to the owner's choice.
- Then update the install, and record the update and the live check in a `docs/STATUS.md` PR.

## Rulings in this plan (owner may overturn)

1. The owner creates the "OneOS" calendar once in Calendar. OneOS never creates calendars, so it can never pick the wrong account (iCloud vs On My Mac).
2. Events last 15 minutes.
3. The event link carries an opaque id, not the note's path, so file names don't go to iCloud. Only the subject the owner typed does.
4. The sync is one-way. Edits made in Calendar are overwritten, and a deleted OneOS event comes back on the next change or login.
5. `remove-calendar` leaves existing events in place. Deleting the calendar removes them.
6. JXA through the built-in `osascript` was chosen over a compiled Swift helper: there is no build step and no new dependency.
