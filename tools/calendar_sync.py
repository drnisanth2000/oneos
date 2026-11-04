"""One-way sync of the owner's open reminders into the Mac's "OneOS" calendar.

Reads the private reminder store (never writes it, the vault or Git) and
reconciles the calendar through tools/calendar_mac.MacCalendar. Everything it
keeps lives in <state>/calendar: the lock, the skip fingerprint, the boot and
24-hour markers and the status file, none of which holds a subject.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import time

from app.reminders import DATABASE_NAME, Reminder, ReminderStore, calendar_event_id
from tools.calendar_mac import CalendarEvent, CalendarUnavailable
from tools.local_service import safe_path, write_json

UTC = timezone.utc
LINK = 'https://localhost:8443/?reminder='
DURATION = timedelta(minutes=15)
LOCK_WAIT = 120.0
LOCK_STEP = 0.1
BUSY_WAIT = 5.0
BUSY_STEP = 0.25
COOL_DOWN = timedelta(minutes=10)
FORCE_AGE = timedelta(hours=24)
SYSCTL = '/usr/sbin/sysctl'

_sleep = time.sleep  # the store retry's pause; tests replace it


@dataclass(frozen=True)
class SyncPlan:
    create: list[CalendarEvent] = field(default_factory=list)
    update: list[CalendarEvent] = field(default_factory=list)
    delete: list[str] = field(default_factory=list)  # calendar uids, duplicates first


@dataclass(frozen=True)
class SyncReport:
    state: str  # ok | skipped | needs_setup | failed
    created: int = 0
    updated: int = 0
    removed: int = 0
    code: str | None = None


def _second(value: datetime) -> datetime:
    """Calendar stores whole seconds, so times are compared at that precision."""
    return value.astimezone(UTC).replace(microsecond=0)


def desired_events(reminders: dict[str, Reminder], now: datetime) -> dict[str, CalendarEvent]:
    """The event each open reminder should have, by event id. The alert is set
    only for a start still in the future at `now`."""
    events = {}
    for item in reminders.values():
        event_id = calendar_event_id(item.key)
        start = item.due_at.astimezone(UTC)
        events[event_id] = CalendarEvent(
            id=event_id, title=item.subject, start=start, end=start + DURATION,
            notes=f'Open in OneOS on this Mac: {LINK}{event_id}\nOneOS id: {event_id}',
            alarm=start > now)
    return events


def _differs(current: CalendarEvent, want: CalendarEvent) -> bool:
    return (current.title != want.title or current.notes != want.notes
            or _second(current.start) != _second(want.start) or _second(current.end) != _second(want.end))


def plan_sync(desired: dict[str, CalendarEvent], existing: list[CalendarEvent]) -> SyncPlan:
    """Collapse duplicate markers (keep the first uid, delete the rest before any
    other delete), then create, update and delete. An event is updated only when
    its title, start, end or notes differ; its alarm is decided again only when
    its times change (otherwise the current alert is kept, so a passed start
    whose alert fired is not a change)."""
    groups: dict[str, list[CalendarEvent]] = {}
    for item in existing:
        groups.setdefault(item.id, []).append(item)
    kept, duplicates = {}, []
    for event_id in sorted(groups):
        first, *rest = sorted(groups[event_id], key=lambda item: item.uid)
        kept[event_id] = first
        duplicates.extend(item.uid for item in rest)
    create, update = [], []
    for event_id in sorted(desired):
        want = desired[event_id]
        current = kept.get(event_id)
        if current is None:
            create.append(want)
        elif _differs(current, want):
            moved = (_second(current.start), _second(current.end)) != (_second(want.start), _second(want.end))
            if moved:
                update.append(replace(want, uid=current.uid))
            else:  # same stored times and alert, so the script edits the event in place
                update.append(replace(want, uid=current.uid, start=current.start, end=current.end,
                                      alarm=current.alarm))
    stale = sorted(kept[event_id].uid for event_id in kept if event_id not in desired)
    return SyncPlan(create=create, update=update, delete=duplicates + stale)


def fingerprint(desired: dict[str, CalendarEvent]) -> str:
    """sha256 of the sorted desired events, without the alarm (which changes as time passes)."""
    rows = [[item.id, item.title, _second(item.start).isoformat(), _second(item.end).isoformat(), item.notes]
            for item in sorted(desired.values(), key=lambda item: item.id)]
    return hashlib.sha256(json.dumps(rows, ensure_ascii=True).encode('ascii')).hexdigest()


def boot_id() -> str:
    """`sysctl -n kern.boottime`; '' when it cannot be read (then boot never forces)."""
    try:
        result = subprocess.run([SYSCTL, '-n', 'kern.boottime'], check=False, capture_output=True,
                                encoding='utf-8', errors='replace', timeout=5)
    except (OSError, subprocess.SubprocessError):
        return ''
    return result.stdout.strip() if result.returncode == 0 else ''


def read_store(directory: Path) -> tuple[dict[str, Reminder] | None, str | None]:
    """The open reminders, or (None, code). "Busy" is only reminders.sqlite3-journal
    being present; on busy or a failed read, retry for BUSY_WAIT in BUSY_STEP steps."""
    store = ReminderStore(directory)
    journal = directory / (DATABASE_NAME + '-journal')
    attempts = round(BUSY_WAIT / BUSY_STEP)
    for attempt in range(attempts + 1):
        busy = os.path.lexists(journal)
        if not busy:
            snapshot = store.snapshot()
            if snapshot is not None:
                return snapshot, None
            try:
                if store.path.lstat().st_size == 0:
                    return None, 'no_store'
            except FileNotFoundError:
                return None, 'no_store'
            except OSError:
                pass
        if attempt < attempts:
            _sleep(BUSY_STEP)
    return None, 'busy' if busy else 'no_store'


def _private_folder(path: Path) -> Path:
    path.mkdir(mode=0o700, exist_ok=True)
    safe_path(path)
    info = path.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('calendar state directory must be private')
    return path


@contextmanager
def _locked(path: Path):
    """Yield True while holding an exclusive flock on `path`, or False after LOCK_WAIT."""
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        deadline = time.monotonic() + LOCK_WAIT
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    yield False
                    return
                time.sleep(LOCK_STEP)
        yield True
    finally:
        os.close(fd)


def _read(path: Path):
    """A marker's JSON value, or None when it is absent, unsafe or unreadable."""
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            return None
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def _time(value) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo is not None else None


def _due_for_force(folder: Path, current_boot: str, now: datetime) -> bool:
    if current_boot and _read(folder / 'boot-id') != current_boot:
        return True
    forced = _read(folder / 'forced.json')
    at = _time(forced.get('at')) if isinstance(forced, dict) else None
    return at is None or now - at > FORCE_AGE


def _skip(last, mark: str, now: datetime) -> str | None:
    """The skip code for an unforced run, or None to run."""
    if not isinstance(last, dict):
        return None
    outcome, at = last.get('outcome'), _time(last.get('at'))
    if outcome == 'ok' and last.get('fingerprint') == mark:
        return 'unchanged'
    if outcome in ('needs_setup', 'failed') and at is not None and now - at < COOL_DOWN:
        return 'cool_down'
    return None


def _status(folder: Path, report: SyncReport, now: datetime) -> None:
    write_json(folder / 'status.json', {'state': report.state, 'at': now.isoformat(), 'created': report.created,
                                        'updated': report.updated, 'removed': report.removed, 'code': report.code})


def run_sync(state_dir: Path, calendar, *, force: bool | None = None, now: datetime | None = None) -> SyncReport:
    """Reconcile the open reminders with the calendar once. `force=None` decides
    from the boot and 24-hour markers. The calendar lock is taken here only, so
    callers must not hold it."""
    now = datetime.now(UTC) if now is None else now
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('now must be timezone-aware')
    state = safe_path(Path(state_dir))
    folder = _private_folder(state / 'calendar')
    with _locked(folder / 'lock') as held:
        if not held:
            return SyncReport('skipped', code='locked')
        reminders, code = read_store(state / 'auth')
        if reminders is None:
            report = SyncReport('skipped', code=code)
            _status(folder, report, now)
            return report
        desired = desired_events(reminders, now)
        mark = fingerprint(desired)
        current_boot = boot_id()
        if force is None:
            force = _due_for_force(folder, current_boot, now)
        if not force:
            skipped = _skip(_read(folder / 'last-sync.json'), mark, now)
            if skipped:
                return SyncReport('skipped', code=skipped)
        try:
            plan = plan_sync(desired, calendar.events())
            if plan.create or plan.update or plan.delete:
                calendar.apply(plan.create, plan.update, plan.delete)
            report = SyncReport('ok', len(plan.create), len(plan.update), len(plan.delete))
        except CalendarUnavailable as exc:
            report = SyncReport('failed' if exc.code == 'script' else 'needs_setup', code=exc.code)
        write_json(folder / 'last-sync.json', {'fingerprint': mark, 'outcome': report.state, 'at': now.isoformat()})
        if force:
            write_json(folder / 'boot-id', current_boot)
            write_json(folder / 'forced.json', {'at': now.isoformat()})
        _status(folder, report, now)
        return report
