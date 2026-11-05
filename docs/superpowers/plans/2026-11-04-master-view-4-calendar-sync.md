# Master view 4: reminders in the Mac's Calendar — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every open OneOS reminder (one whose card is not Done) appears as one event in a calendar named "OneOS" in the Mac's Calendar app, at the reminder time with an alert. Changing the reminder updates that event, and Done (or deleting the reminder) removes it. OneOS stores no calendar or iCloud credential.

**Architecture:**
- The app container cannot reach Calendar, so a host-side step beside `tools/local_service` does the writes.
- A user LaunchAgent (`local.oneos.calendar`) watches the private auth folder and the reminder store, and runs `calendar-sync`.
- `calendar-sync` reads the store read-only and reconciles the "OneOS" calendar. It does this in one call to a small JavaScript for Automation (JXA) script run by `/usr/bin/osascript`, under a calendar lock.
- A pure sync engine decides what to create, update and remove. It is tested against a fake calendar.
- The app gains three things:
  - a stable event id per reminder;
  - a private "resolved" mark for reminders kept on Done cards, so they get no event;
  - a Home deep link that opens the reminder's card, surviving sign-in.

**Tech Stack:** Python 3.12 (host tools under `tools/`, the app's `app/reminders.py`, `app/main.py`, `app/auth_web.py`), macOS `launchd` (`WatchPaths`, `RunAtLoad`), `/usr/bin/osascript -l JavaScript` (no new dependency), `fcntl` locks, pytest with fakes only.

**Spec:** `docs/superpowers/specs/2026-10-29-master-view-design.md`. The relevant parts are:
- "Getting reminders into the calendar";
- "Moving a card to Done resolves its reminder";
- the anti-goals: no credentials, and no network call on page load;
- "Tests the build must prove": "The Calendar step creates one event per reminder, updates it in place, removes it on Done, and never touches another calendar (tested against a fake Calendar adapter, not the real app)";
- the owner decision of 2026-10-29: "Calendar permission on the Mac is OK".

It builds on plans 1–3 (merged): `app/reminders.py`, `_stage_follows_move` / `_reminder_follows_approval` in `app/main.py`, and `install_login` / `diagnostics` / `private_file` / `safe_path` / `write_json` in `tools/local_service.py`.

**Facts checked on the owner's Mac (2026-11-04):**
- The store is `$ONEOS_STATE_DIR/auth/reminders.sqlite3` on the host (`compose.yaml` mounts `…/auth` as `/state/auth`).
- A file created inside the container in that folder raises a host kqueue event on the folder.
- SQLite runs in its default rollback-journal mode, so a write transaction adds and removes `reminders.sqlite3-journal`.
- `/usr/bin/osascript` is present.

The same folder also holds `owner.sqlite3`, whose journal comes and goes on every signed-in request (Cursor review of #64).

## Global Constraints

### Which reminders become events

- Every **open** row in the store becomes an event: a row with no resolved mark. Today's behaviour is kept (owner decision on #64, 2026-11-04):
  - a stage press to `done` deletes the row, as it does now;
  - a classify approval that files a note as `active` (which counts as Done) keeps the row, and the Done card still shows its reminder.

  Task 1 adds a private `resolved_reminders` table (`key TEXT PRIMARY KEY, resolved_at TEXT NOT NULL`) in the same file. It is created only inside the write transaction that first resolves a reminder, as `review_flags` is.
  - Every path that lands a card in Done and keeps its row marks it resolved.
  - A move out of Done clears the mark.
  - `rekey`, `remove` and `rename_entity` carry the mark with the row.
  - The sync skips resolved rows, so their events are removed.
- If the store is unsafe at that moment, the commit still lands and the row is kept, as today. The event then stays until the owner removes the reminder. Status reports nothing about it (known limit).

### Event contents

- One event per reminder, only in **the single** calendar whose name `=== "OneOS"` in JavaScript (case-sensitive).
  - Zero matches, more than one match, or a read-only match → `needs_setup`, with codes `no_calendar`, `duplicate_calendar` and `read_only`. Nothing is written.
  - The sync never creates, renames or deletes a calendar, and never reads or writes another one.
- An event is OneOS's only if its notes end with the exact line `OneOS id: oneos-<32 lowercase hex>`. Any other event in "OneOS" is the owner's and is never changed or removed. An owner event that copies that exact line is treated as OneOS's (accepted: the line is not something people type).
- Event fields:
  - title = the reminder subject;
  - start = `due_at`, the same instant, as a zoned instant (no floating date);
  - end = start + 15 minutes;
  - notes = `Open in OneOS on this Mac: https://localhost:8443/?reminder=<id>` and then the `OneOS id:` line.
  - **Alarm:** exactly one display alert at the start time when the start is in the future at sync time, and no alert when it is already past. That stops a burst of instant notifications on the first sync.
  - Notes are set in the same properties record as the event's creation, so no event can exist without its marker.
- **Update** sets the title, start, end and notes, removes all of that event's alarms, then adds the one alert (if any). Other properties the owner set (recurrence, location) are left alone.
- **Duplicates:** when two events carry the same marker, keep the earliest-created and delete the others before any other delete.
- The id is `calendar_event_id(key)` = `"oneos-" + sha256(key.encode("utf-8")).hexdigest()[:32]`. No vault path, entity name or note title goes into the event beyond the subject the owner typed.

### Running the script

- All writes for one sync go in **one** script call: `apply` with `{create: [...], update: [...], delete: [...]}`. Reading is a second call, `list`.
- Values reach the script only as one JSON argument (`osascript -l JavaScript tools/calendar_mac.js <cmd> <json>`), parsed with `JSON.parse`, never interpolated.
- Output is JSON on stdout.
- Each call has a timeout of 60 s, and a timeout is retried once.
- Exit codes:
  - 3 = `no_calendar`;
  - 5 = `duplicate_calendar`;
  - 6 = `read_only`;
  - 4 = `permission` (Apple event error `-1743`).
  - Any other non-zero exit, a second timeout, or unparsable output raises `CalendarUnavailable("script")`.
- Raw stderr is never put into an exception, the status file or a log.

### The sync is one-way

OneOS → Calendar only.
- An owner edit to a OneOS event in Calendar is overwritten on the next change.
- A OneOS event the owner deleted comes back on the next reminder change, at the next boot, or within 24 hours (the forced re-check).

### Reading the store

- Use the new `ReminderStore.snapshot() -> dict[str, Reminder] | None`. It holds the open rows only: resolved rows are left out, and a file with no `resolved_reminders` table has none resolved. It returns `None` (no basis to delete) when:
  - the file is missing;
  - the file is 0 bytes;
  - it has no `reminders` table;
  - or the read fails.
- A store with the table and no rows returns `{}`. Only a non-`None` snapshot may remove events.
- "Busy" means only `reminders.sqlite3-journal` is present. On busy or `sqlite3.OperationalError`, retry the read in-process for up to 5 s (0.25 s steps). If it is still busy, the outcome is `skipped` (code `busy`); the app's next write fires the watcher again.
- The sync never writes the store, the vault or Git.

### Lock, skipping and cool-down

- Each run holds an exclusive `fcntl.flock` on `<state>/calendar/lock`, waiting up to 120 s, around reading the markers, listing, applying and writing status. If it cannot get the lock: `skipped`, code `locked`.
- **Skip fingerprint:** `<state>/calendar/last-sync.json` (0600) holds the sha256 of the sorted desired events, plus the outcome and time. While it is unchanged, the sync exits without starting `osascript`, unless one of these holds:
  - the run is forced;
  - the last outcome was `ok` and the fingerprint differs;
  - the last outcome was `needs_setup` or `failed` and 10 minutes have passed.

  This stops Calendar.app being woken on every page view.
- **Forced runs:** the run is forced when `<state>/calendar/boot-id` differs from `sysctl -n kern.boottime`, or when the last forced run was more than 24 hours ago. A forced run updates both markers. `install-calendar` always forces.
- The status file is `<state>/calendar/status.json` (0600 via `write_json`), holding only `{state, at, created, updated, removed, code}`. It never contains a subject.

### The background job

- label `local.oneos.calendar`;
- `ProgramArguments` `[sys.executable, -m, tools.local_service, --state-dir, <state>, calendar-sync]`;
- `WorkingDirectory` = `config['repo_dir']` (needed for `-m tools…`, as `install_login` does);
- `WatchPaths` `[<state>/auth, <state>/auth/reminders.sqlite3]`;
- `RunAtLoad` true, `ThrottleInterval` 30, `StartInterval` 3600 (an hourly idle check that, through the fingerprint skip, opens Calendar only when something changed or a retry or the daily re-check is due; added after the Codex review of #65);
- `StandardOutPath` / `StandardErrorPath` `/dev/null`;
- `EnvironmentVariables` `{PATH}`, as `install_login` sets it.
- The job always exits 0 once it has written its status. A non-zero exit is reserved for the foreground `install-calendar` refusal.

### Status and doctor

- `doctor` never fails on calendar state.
- `status`/`doctor` report `calendar` as one of:
  - `off` — job not installed;
  - `on` — last run `ok` or `skipped`;
  - `needs_setup`;
  - `failed`.
- `needs_setup` action text depends on the code:
  - `no_calendar`: "In Calendar, create a calendar named OneOS (choose iCloud so its alerts reach your iPhone), then run install-calendar again in Terminal."
  - `duplicate_calendar`: "More than one calendar is named OneOS. Keep one (the iCloud one), rename or delete the others, then run install-calendar again."
  - `read_only`: "The OneOS calendar is read-only. Use a calendar you can edit, then run install-calendar again."
  - `permission`: "Open System Settings → Privacy & Security → Automation and allow OneOS's Python to control Calendar, then run install-calendar again in Terminal."

### Owner steps

These are documented in `docs/LOCAL-OPERATIONS.md`:
1. Create the calendar in Calendar.
2. Run `OneOS.command install-calendar` in Terminal. It runs one forced sync in the foreground, so macOS asks once for permission, then installs the job.
3. `OneOS.command remove-calendar` unloads and removes the job and leaves events in place. Deleting the "OneOS" calendar removes them all.
4. Event alerts reach the iPhone through iCloud. The link in the notes opens OneOS only on this Mac, because OneOS answers only at `localhost`.

### Home deep link

`GET /?reminder=<id>` accepts only `oneos-[0-9a-f]{32}`; anything else is ignored with no lookup. It writes nothing.
- One `snapshot()` read maps the id to `(key, entity)`. The card URL is built for that reminder's entity, through `build_scope(entity)` and that entity's own path checks; `scope.current_entity()` wraps that work.
- When found, Home renders a load-triggered `hx-get` of that card's popup URL. It targets `#master-popup` and sits **outside** `#master`, so the 60-second morph refresh never reopens it.
- Not found, or an entity that no longer exists: Home shows the notice "That reminder is no longer in OneOS."
- Store unavailable: Home shows "Reminders are unavailable right now."
- **Signed out:**
  - the sign-in redirect carries `next=/?reminder=<id>`, and only that exact shape is kept: path `/`, query `reminder=oneos-[0-9a-f]{32}`, nothing else;
  - both password and passkey sign-in return to it when it is valid, and to `/` otherwise;
  - no other `next` value is ever followed (no open redirect).

### Project rules

- Every new or changed route keeps `@console_route` and its declared catches (`tests/test_console_invariants.py`).
- Host-tool code follows `tools/local_service.py` idioms.
- No new dependency and no new vault convention field.
- Never write "/home/" followed by a path segment.
- Run tests as `env -u GIT_EDITOR uv run pytest ...`. Locally, `tests/test_local_backup.py` and `tests/test_local_service.py` have 2 known environment failures on `main`; compare against `main`, not zero.

## Review Focus

1. **The store is missing, empty-file, table-less, half-written or locked at sync time.** No event is removed. Pinned in Task 3.
2. **The owner's own events, a second "OneOS" calendar, or duplicate markers.** Nothing outside OneOS's own events changes. Duplicates are collapsed to one. Pinned in Tasks 2 and 3.
3. **Offsets, DST and travel.** The event lands at the chosen instant as a zoned time. Past reminders get no alert. Pinned in Task 3 and the live check.
4. **Setup problems: missing calendar, denied permission, read-only calendar, cold Calendar timeout.** Each gives a specific owner action. `doctor` passes. Calendar is not re-woken on every page view. Pinned in Tasks 2 and 4.
5. **Two runs at once (the watcher plus a manual run or `install-calendar`).** Still one event per reminder. Pinned in Task 3.

---

### Task 1: Resolved mark for reminders kept on Done cards; shared event id; store snapshot

**Files:**
- Modify: `app/reminders.py` (add `calendar_event_id`, `ReminderStore.snapshot`) and `app/main.py` (`_stage_follows_move`, `_reminder_follows_approval`)
- Test: `tests/test_reminders.py`, `tests/test_master.py`

**Interfaces:**
- Produces:
  - `calendar_event_id(key: str) -> str`;
  - `ReminderStore.snapshot() -> dict[str, Reminder] | None`, per Global Constraints. It is read-only and creates nothing.
  - `ReminderStore.resolve(key: str) -> None` and `ReminderStore.unresolve(key: str) -> None`. `resolve` creates the table only when it writes; `unresolve` on a file without the table writes nothing.

- [ ] **Step 1: Failing tests.**
  - `snapshot()` returns `None` for a missing file, a 0-byte file and a file with no `reminders` table; `{}` for an empty table; the rows otherwise. Directory and file bytes are unchanged after each call.
  - `calendar_event_id` is stable and matches `oneos-[0-9a-f]{32}`.
  - A classify approval that files a reminder card as `active` (Done) keeps its row (the assertion at `tests/test_master.py` ~1621 is unchanged), marks it resolved, and leaves it out of `snapshot()`.
  - Moving that card back to Waiting clears the mark, and the row is in `snapshot()` again.
  - A stage press to `done` still deletes. Waiting and To review moves keep the row unresolved.
  - `rekey` and `rename_entity` keep the mark with the row, and `remove` drops it.
  - `snapshot()`, `all()` and every no-op write never create `resolved_reminders`.
  - The Done card still shows its reminder.
  - An unsafe store still lets the commit land.
- [ ] **Step 2: Run** `env -u GIT_EDITOR uv run pytest -q tests/test_reminders.py tests/test_master.py`. Expect failures.
- [ ] **Step 3: Implement.** Done = the resulting status is `done` or `active`, the same rule as `stage_of`. The calls go in the same best-effort post-commit spots as today's `remove`/`rekey` (`_stage_follows_move`, `_reminder_follows_approval`). A store failure never turns a landed commit into an error.
- [ ] **Step 4: Run** the tests. Expect them to pass, and `tests/test_console_invariants.py` too.
- [ ] **Step 5: Commit** "Reminders: a resolved mark for Done cards; event id; snapshot".

### Task 2: Calendar adapter for the Mac (JXA), with a live check

**Files:**
- Create: `tools/calendar_mac.js`, `tools/calendar_mac.py`
- Test: `tests/test_calendar_mac.py`

**Interfaces:**
- Produces, in `tools/calendar_mac.py`:
  - `@dataclass(frozen=True) class CalendarEvent: id: str; title: str; start: datetime; end: datetime; notes: str; alarm: bool; uid: str = ""; created: datetime | None = None`. All datetimes are timezone-aware UTC. `uid` and `created` are Calendar's own values: they are filled by `events()` and empty on events still to be created.
  - `class CalendarUnavailable(Exception)` with `.code` in `{"no_calendar", "duplicate_calendar", "read_only", "permission", "script"}`.
  - `class MacCalendar(*, run=subprocess.run, name="OneOS")`:
    - `.events() -> list[CalendarEvent]` returns OneOS-marked events only, duplicates included (the engine collapses them);
    - `.apply(create: list[CalendarEvent], update: list[CalendarEvent], delete: list[str]) -> None`. Updates are addressed by each event's `uid`, and `delete` is a list of uids.
  - `main(argv) -> int` with `selftest`: create "OneOS test" one hour ahead, list it, delete it, and print `ok` or the code.

- [ ] **Step 1: Failing tests,** with a fake `run`:
  - argv is exactly `["/usr/bin/osascript", "-l", "JavaScript", <abs path>, "<cmd>", "<json>"]` with `timeout=60`.
  - A timeout is retried once, then `CalendarUnavailable("script")`.
  - A subject containing `"`, `'`, `\`, a newline and emoji passes only through the JSON argument.
  - Exit codes 3/4/5/6 map to their codes.
  - Fake stderr containing a subject never appears in the exception text.
  - The `list` JSON parses, including two events sharing a marker.
- [ ] **Step 2: Run** `env -u GIT_EDITOR uv run pytest -q tests/test_calendar_mac.py`. Expect failures.
- [ ] **Step 3: Implement.**
  - The JS follows Global Constraints: exact-match calendar, writability check, notes set in the create properties, update replaces alarms, alarm only when the start is in the future, zoned `Date`.
  - The Python wrapper maps exit codes.
- [ ] **Step 4: Run** the tests. Expect them to pass.
- [ ] **Step 5: Live check on the owner's Mac** (controller, with the owner present; a gate before Task 3).
  - The owner creates the iCloud "OneOS" calendar.
  - Run `python -m tools.calendar_mac selftest` from Terminal, and the owner presses Allow.
  - Run the same selftest from a throwaway LaunchAgent (`local.oneos.calendar-probe`, `RunAtLoad`, same `WorkingDirectory`). It must succeed with no new prompt. Then remove the probe.
  - Record the result in the ledger, including the test event's time zone (it must not be floating).
  - **If the LaunchAgent run cannot get permission, stop and ask the owner. Never work around macOS privacy controls.**
- [ ] **Step 6: Commit** "Calendar: a JXA adapter for the Mac's Calendar app".

### Task 3: Sync engine

**Files:**
- Create: `tools/calendar_sync.py`
- Test: `tests/test_calendar_sync.py`

**Interfaces:**
- Consumes: Task 1 (`snapshot`, `calendar_event_id`) and Task 2 (`CalendarEvent`, `CalendarUnavailable`, `MacCalendar`).
- Produces:
  - `desired_events(reminders, now) -> dict[str, CalendarEvent]`;
  - `plan_sync(desired, existing) -> SyncPlan(create, update, delete)`, which collapses duplicates first and updates only when the title, start, end, notes or alarm differ;
  - `run_sync(state_dir: Path, calendar, *, force: bool | None = None, now=None) -> SyncReport(state, created, updated, removed, code)`. `force=None` means decide from the boot and 24-hour markers.

- [ ] **Step 1: Failing tests,** with a `FakeCalendar` that keeps OneOS events, owner events and a record of calls:
  - A new reminder creates one event with exactly the Global Constraints fields.
  - A subject or time change updates it in place.
  - A second run makes zero calls.
  - A removed row removes its event.
  - A re-key deletes the old id and creates the new one, leaving one event.
  - Owner events are untouched.
  - Two events with one marker collapse to one.
  - `+05:30`, `-08:00` and a DST-date `due_at` all give a UTC start equal to `due_at.astimezone(utc)`.
  - A past `due_at` has `alarm=False`.
  - `snapshot()` is `None` → `skipped`, with zero calls.
  - A `reminders.sqlite3-journal` that clears within the retry gives `ok`; one that stays gives `skipped`/`busy`.
  - An `owner.sqlite3-journal` alone is not busy.
  - An empty table removes all OneOS events.
  - Each `CalendarUnavailable` code gives `needs_setup` (or `failed` for `script`) and no writes, and no `osascript` call follows within 10 minutes for the same fingerprint.
  - The fingerprint skips with zero calls. The boot-id change and the 24-hour age each force.
  - Two `run_sync` calls in parallel threads on one fake (made slow) end with one event per reminder.
  - The status and fingerprint files are 0600 with no subject.
- [ ] **Step 2: Run** `env -u GIT_EDITOR uv run pytest -q tests/test_calendar_sync.py`. Expect failures.
- [ ] **Step 3: Implement** as specified in Global Constraints (lock, retry, cool-down, markers).
- [ ] **Step 4: Run** the tests. Expect them to pass.
- [ ] **Step 5: Commit** "Calendar: reconcile open reminders with the OneOS calendar".

### Task 4: Service commands, LaunchAgent, status and docs

**Files:**
- Modify: `tools/local_service.py`, `docs/LOCAL-OPERATIONS.md`, `docs/ARCHITECTURE.md` (tools list), `CHANGELOG.md`
- Test: `tests/test_local_service.py`

**Interfaces:**
- Consumes: `run_sync` (Task 3) and `MacCalendar` (Task 2).
- Produces:
  - commands `calendar-sync`, `install-calendar` and `remove-calendar`;
  - `install_calendar(config, *, sync=run_sync, calendar_factory=MacCalendar)`;
  - `remove_calendar(config)`;
  - `calendar_diagnostic(config) -> finding`.

- [ ] **Step 1: Failing tests,** following the `test_install_login_*` patterns with fake `launchctl` and `sysctl`:
  - `install-calendar` forces one sync first, and refuses (non-zero, printing that code's action text) on `needs_setup` or `failed`.
  - On success it writes `local.oneos.calendar.plist` with exactly the Global Constraints keys, `WorkingDirectory` included, and bootstraps it.
  - It refuses an existing plist or loaded job, and rolls back on a bootstrap failure.
  - `remove-calendar` boots out and removes only that plist.
  - `calendar-sync` exits 0 for every outcome.
  - `status` reports `calendar` as `off`/`on`/`needs_setup`/`failed` with the per-code action.
  - `doctor` returns 0 even when the calendar is `needs_setup` or `failed`.
  - `install-login` is unchanged.
- [ ] **Step 2: Run** `env -u GIT_EDITOR uv run pytest -q tests/test_local_service.py`. Expect the new tests to fail. The 2 known environment failures stay as they are on `main`.
- [ ] **Step 3: Implement.**
  - `calendar-sync` takes the calendar lock, not the operation lock. `install-calendar` and `remove-calendar` take the operation lock, and `install-calendar` also takes the calendar lock for its sync.
  - Docs: the four owner steps, and how to turn the feature off.
  - `CHANGELOG.md` under Unreleased: "Reminders reach the Mac's Calendar (plan 4 of 4): one event per open reminder in a calendar named OneOS, updated when the reminder changes and removed on Done; set up once with `install-calendar`."
- [ ] **Step 4: Run** the full suite. Expect the same known failures as `main`, and no new ones.
- [ ] **Step 5: Commit** "Calendar: install-calendar, calendar-sync and status".

### Task 5: Home deep link, kept through sign-in

**Files:**
- Modify: `app/main.py` (the `shell` route), `templates/home.html`, `app/auth_web.py` (sign-in redirect and the return after password and passkey sign-in)
- Test: `tests/test_master.py`, `tests/test_auth.py`, `tests/test_passkeys.py`

**Interfaces:**
- Consumes: `calendar_event_id` and `snapshot` (Task 1).
- Produces: `GET /?reminder=<id>` and the `next` handling, per Global Constraints.

- [ ] **Step 1: Failing tests.**
  - With a reminder in entity A, `/?reminder=<id>` renders exactly one load-triggered `hx-get` for A's card popup. It targets `#master-popup` and sits outside `#master`.
  - A reminder whose entity is no longer in the catalog shows "That reminder is no longer in OneOS.". An unknown id shows the same.
  - A malformed id (`../x`, `oneos-ZZ`, 1 000 characters) renders plain Home with no lookup.
  - The store unavailable shows "Reminders are unavailable right now.".
  - The vault HEAD and the store bytes are unchanged after each request.
  - Signed out, `/?reminder=<id>` redirects to the sign-in page carrying it. Password sign-in and passkey sign-in each land on `/?reminder=<id>`.
  - `next` values `//evil.example`, `https://evil.example/`, `/outbox`, `/?reminder=x&a=b` and `/%2F%2Fevil` all land on `/`.
  - The route and middleware invariants still pass.
- [ ] **Step 2: Run** `env -u GIT_EDITOR uv run pytest -q tests/test_master.py tests/test_auth.py tests/test_passkeys.py tests/test_console_invariants.py`. Expect failures.
- [ ] **Step 3: Implement.** Add a single validator, `_safe_next(value) -> str`, used by both sign-in flows. Thread `next` through the sign-in form and the passkey finish request.
- [ ] **Step 4: Run** the tests. Expect them to pass.
- [ ] **Step 5: Commit** "Master view: a calendar event opens its card, through sign-in".

## Before merge

- Run the full suite and `tools.public_repo_audit --history` (CLEAN).
- Run the private gate (`BUILD.md` §3). The vault must be byte-identical before and after.
- Live end-to-end on the owner's Mac, with the owner present:
  1. `install-calendar`, then save a reminder. One event appears in "OneOS" with an alert, at the right time, in the Mac's time zone.
  2. Change the reminder time. The same event moves.
  3. Move the card to Done. The event is gone.
  4. Open the event's link while signed out. After sign-in, the card's popup opens.
  5. An owner-made event in "OneOS" survives all of the above.
  6. Whether to run `remove-calendar` afterwards is the owner's choice.
- Then update the install, and record the update and the live check in a `docs/STATUS.md` PR.

## Rulings in this plan (owner may overturn)

1. The owner creates the "OneOS" calendar once. OneOS never creates calendars, and refuses when there are zero, two or more, or a read-only one.
2. Events last 15 minutes. A reminder already past when first synced gets an event with no alert.
3. The event link carries an opaque id, not the note's path, and opens OneOS only on this Mac. Only the subject the owner typed goes to iCloud.
4. The sync is one-way. Edits in Calendar are overwritten. A deleted OneOS event comes back on the next change, the next boot, or within a day.
5. `remove-calendar` leaves events in place. Deleting the calendar removes them.
6. JXA through the built-in `osascript`, rather than a compiled Swift helper: there is no build step and no new dependency.
7. **Owner decision (#64, 2026-11-04):** keep today's behaviour. A reminder kept on a Done card still shows there. It is marked resolved in the private store so its calendar event is removed, and the mark is cleared if the card leaves Done.
