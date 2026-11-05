"""Calendar sync engine tests: synthetic state and a FakeCalendar only (no osascript)."""
import fcntl
import itertools
import json
import os
import re
import stat
import threading
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.reminders import Reminder, ReminderStore, calendar_event_id
from tools import calendar_sync
from tools.calendar_mac import CODES, CalendarEvent, CalendarUnavailable
from tools.calendar_sync import SyncPlan, desired_events, plan_sync, run_sync

UTC = timezone.utc
NOW = datetime(2026, 11, 4, 9, 0, tzinfo=UTC)
PLUS_TWO = timezone(timedelta(hours=2))
MARKER = re.compile(r"(?:^|\n)OneOS id: oneos-[0-9a-f]{32}\n?$")


class FakeCalendar:
    """Keeps OneOS events and owner events by uid and records every call."""

    def __init__(self, *, fail=None, fail_on="events", delay=0.0):
        self.store = {}
        self.calls = []
        self.fail = fail
        self.fail_on = fail_on
        self.delay = delay
        self._uids = itertools.count(1)

    def new_uid(self):
        return f"uid-{next(self._uids):04d}"

    def add(self, event):
        uid = event.uid or self.new_uid()
        self.store[uid] = replace(event, uid=uid)
        return uid

    def owner(self, title="Dentist"):
        start = NOW + timedelta(days=1)
        return self.add(CalendarEvent(id="", title=title, start=start, end=start + timedelta(hours=1),
                                      notes="bring the card", alarm=True))

    def ours(self):
        return [item for item in self.store.values() if MARKER.search(item.notes)]

    def events(self):
        self.calls.append(("events",))
        if self.fail and self.fail_on == "events":
            raise CalendarUnavailable(self.fail)
        time.sleep(self.delay)
        return [replace(item) for item in self.ours()]

    def apply(self, create, update, delete):
        self.calls.append(("apply", list(create), list(update), list(delete)))
        if self.fail and self.fail_on == "apply":
            raise CalendarUnavailable(self.fail)
        time.sleep(self.delay)
        for item in create:
            self.add(replace(item, uid=""))
        for item in update:
            current = self.store[item.uid]
            if (current.start, current.end, current.alarm) != (item.start, item.end, item.alarm):
                del self.store[item.uid]  # the script replaces the event on a time change
                self.add(replace(item, uid=""))
            else:
                self.store[item.uid] = replace(current, title=item.title, notes=item.notes)
        for uid in delete:
            if uid in self.store and MARKER.search(self.store[uid].notes):
                del self.store[uid]


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setattr(calendar_sync, "boot_id", lambda: "boot-1")
    monkeypatch.setattr(calendar_sync, "_sleep", lambda seconds: None)


@pytest.fixture
def state(tmp_path):
    directory = tmp_path / "state"
    directory.mkdir(mode=0o700)
    (directory / "auth").mkdir(mode=0o700)
    return directory


def store(state):
    return ReminderStore(state / "auth")


def reminder(key="inbox/pay-card.md", subject="Pay card", due=None):
    return Reminder(key, "synthetic", subject, due or datetime(2026, 11, 5, 11, 0, tzinfo=PLUS_TWO))


def notes_for(event_id):
    return f"Open in OneOS on this Mac: https://localhost:8443/?reminder={event_id}\nOneOS id: {event_id}"


def run(state, calendar, **kwargs):
    kwargs.setdefault("now", NOW)
    return run_sync(state, calendar, **kwargs)


# ---- desired_events ----

def test_desired_event_has_exactly_the_global_constraints_fields():
    item = reminder()
    events = desired_events({item.key: item}, NOW)
    event_id = calendar_event_id(item.key)
    assert list(events) == [event_id]
    event = events[event_id]
    assert event == CalendarEvent(id=event_id, title="Pay card", start=datetime(2026, 11, 5, 9, 0, tzinfo=UTC),
                                  end=datetime(2026, 11, 5, 9, 15, tzinfo=UTC), notes=notes_for(event_id),
                                  alarm=True)
    assert event.start.utcoffset() == timedelta(0)


@pytest.mark.parametrize("due", [
    datetime(2026, 11, 6, 8, 30, tzinfo=timezone(timedelta(hours=5, minutes=30))),
    datetime(2026, 11, 6, 8, 30, tzinfo=timezone(timedelta(hours=-8))),
    datetime(2027, 3, 14, 3, 30, tzinfo=ZoneInfo("America/New_York")),  # the morning after spring forward
    datetime(2026, 10, 25, 2, 30, fold=1, tzinfo=ZoneInfo("Europe/Berlin")),  # the repeated hour
])
def test_start_is_the_same_instant_in_utc(due):
    item = reminder(due=due)
    event = desired_events({item.key: item}, NOW - timedelta(days=400))[calendar_event_id(item.key)]
    assert event.start == due.astimezone(UTC)
    assert event.start.tzinfo == UTC
    assert event.end - event.start == timedelta(minutes=15)


def test_past_due_at_has_no_alarm_and_future_has_one():
    past = reminder(key="a.md", due=NOW - timedelta(minutes=1))
    future = reminder(key="b.md", due=NOW + timedelta(minutes=1))
    events = desired_events({past.key: past, future.key: future}, NOW)
    assert events[calendar_event_id("a.md")].alarm is False
    assert events[calendar_event_id("b.md")].alarm is True


# ---- plan_sync ----

def listed(event, uid):
    return replace(event, uid=uid)


def one_desired(**changes):
    item = reminder()
    event = desired_events({item.key: item}, NOW)[calendar_event_id(item.key)]
    return replace(event, **changes)


def test_plan_creates_missing_and_deletes_stale():
    want = one_desired()
    stale = replace(one_desired(), id="oneos-" + "b" * 32, notes=notes_for("oneos-" + "b" * 32), uid="u-stale")
    plan = plan_sync({want.id: want}, [stale])
    assert plan == SyncPlan(create=[want], update=[], delete=["u-stale"])


def test_plan_is_empty_when_nothing_differs_even_at_millisecond_noise():
    want = one_desired(start=one_desired().start + timedelta(milliseconds=400),
                       end=one_desired().end + timedelta(milliseconds=400))
    current = listed(one_desired(), "u1")
    assert plan_sync({want.id: want}, [current]) == SyncPlan([], [], [])


def test_plan_ignores_an_alarm_difference_alone():
    want = one_desired(alarm=False)  # the start has passed; the alert already fired
    current = listed(one_desired(alarm=True), "u1")
    assert plan_sync({want.id: want}, [current]) == SyncPlan([], [], [])
    assert plan_sync({want.id: replace(want, alarm=True)}, [listed(want, "u1")]) == SyncPlan([], [], [])


def test_plan_title_change_keeps_the_current_times_and_alarm():
    current = listed(one_desired(alarm=False), "u1")
    want = one_desired(title="Pay the card", alarm=True,
                       start=current.start + timedelta(milliseconds=200))
    plan = plan_sync({want.id: want}, [current])
    assert plan.create == [] and plan.delete == []
    assert plan.update == [replace(current, title="Pay the card")]


def test_plan_start_change_takes_the_desired_alarm():
    current = listed(one_desired(alarm=False), "u1")
    want = one_desired(start=current.start + timedelta(hours=1), end=current.end + timedelta(hours=1), alarm=True)
    plan = plan_sync({want.id: want}, [current])
    assert plan.update == [replace(want, uid="u1")]


def test_plan_collapses_duplicates_keeping_the_first_uid_and_deleting_them_first():
    want = one_desired()
    stale = replace(one_desired(), id="oneos-" + "b" * 32, notes=notes_for("oneos-" + "b" * 32), uid="u-a")
    twins = [listed(want, "u-c"), listed(want, "u-b"), listed(replace(want, title="old"), "u-d")]
    plan = plan_sync({want.id: want}, [stale, *twins])
    assert plan.create == [] and plan.update == []
    assert plan.delete == ["u-c", "u-d", "u-a"]


# ---- run_sync against a FakeCalendar ----

def test_new_reminder_creates_one_event_and_owner_events_are_untouched(state):
    store(state).save(reminder())
    calendar = FakeCalendar()
    owner_uid = calendar.owner()
    owner_event = calendar.store[owner_uid]
    report = run(state, calendar)
    assert (report.state, report.created, report.updated, report.removed, report.code) == ("ok", 1, 0, 0, None)
    [event] = calendar.ours()
    assert replace(event, uid="") == one_desired()
    assert calendar.store[owner_uid] == owner_event
    assert calendar.calls[0] == ("events",) and calendar.calls[1][0] == "apply"


def test_second_run_makes_zero_calls(state):
    store(state).save(reminder())
    calendar = FakeCalendar()
    run(state, calendar)
    calendar.calls.clear()
    report = run(state, calendar, now=NOW + timedelta(hours=1))
    assert report.state == "skipped" and report.code == "unchanged"
    assert calendar.calls == []


def test_a_forced_run_with_nothing_to_change_only_lists(state):
    store(state).save(reminder())
    calendar = FakeCalendar()
    run(state, calendar)
    calendar.calls.clear()
    report = run(state, calendar, force=True, now=NOW + timedelta(days=2))  # past the start: alarm differs
    assert (report.state, report.created, report.updated, report.removed) == ("ok", 0, 0, 0)
    assert calendar.calls == [("events",)]


def test_subject_change_updates_in_place(state):
    store(state).save(reminder())
    calendar = FakeCalendar()
    run(state, calendar)
    [before] = calendar.ours()
    store(state).save(reminder(subject="Pay the card"))
    report = run(state, calendar)
    assert (report.state, report.created, report.updated, report.removed) == ("ok", 0, 1, 0)
    [after] = calendar.ours()
    assert after.uid == before.uid and after.title == "Pay the card"
    assert calendar.calls[-1] == ("apply", [], [replace(before, title="Pay the card")], [])


def test_time_change_updates_the_event(state):
    store(state).save(reminder())
    calendar = FakeCalendar()
    run(state, calendar)
    later = datetime(2026, 11, 5, 15, 0, tzinfo=PLUS_TWO)
    store(state).save(reminder(due=later))
    report = run(state, calendar)
    assert (report.state, report.created, report.updated, report.removed) == ("ok", 0, 1, 0)
    [event] = calendar.ours()
    assert event.start == later.astimezone(UTC) and event.end == event.start + timedelta(minutes=15)
    assert event.alarm is True


def test_removed_row_removes_its_event(state):
    store(state).save(reminder())
    store(state).save(reminder(key="b.md", subject="Second"))
    calendar = FakeCalendar()
    calendar.owner()
    run(state, calendar)
    store(state).remove("b.md")
    report = run(state, calendar)
    assert (report.state, report.removed) == ("ok", 1)
    assert [item.id for item in calendar.ours()] == [calendar_event_id("inbox/pay-card.md")]
    assert len(calendar.store) == 2


def test_rekey_deletes_the_old_id_and_creates_the_new_one(state):
    store(state).save(reminder())
    calendar = FakeCalendar()
    run(state, calendar)
    store(state).rekey("inbox/pay-card.md", "done/pay-card.md")
    report = run(state, calendar)
    assert (report.state, report.created, report.removed) == ("ok", 1, 1)
    assert [item.id for item in calendar.ours()] == [calendar_event_id("done/pay-card.md")]


def test_two_events_with_one_marker_collapse_to_one(state):
    store(state).save(reminder())
    calendar = FakeCalendar()
    want = one_desired()
    calendar.add(replace(want, uid="uid-b"))
    calendar.add(replace(want, uid="uid-a"))
    report = run(state, calendar)
    assert (report.state, report.created, report.removed) == ("ok", 0, 1)
    assert [item.uid for item in calendar.ours()] == ["uid-a"]


def test_empty_table_removes_all_oneos_events_only(state):
    store(state).save(reminder())
    calendar = FakeCalendar()
    owner_uid = calendar.owner()
    run(state, calendar)
    store(state).remove("inbox/pay-card.md")
    assert store(state).snapshot() == {}
    report = run(state, calendar)
    assert (report.state, report.removed) == ("ok", 1)
    assert calendar.ours() == [] and list(calendar.store) == [owner_uid]


def test_missing_store_is_skipped_with_zero_calls(state):
    calendar = FakeCalendar()
    calendar.add(one_desired())
    report = run(state, calendar)
    assert (report.state, report.code) == ("skipped", "no_store")
    assert calendar.calls == [] and len(calendar.ours()) == 1


def test_zero_byte_store_is_skipped_with_zero_calls(state):
    path = state / "auth" / "reminders.sqlite3"
    path.touch(mode=0o600)
    calendar = FakeCalendar()
    report = run(state, calendar)
    assert (report.state, report.code) == ("skipped", "no_store")
    assert calendar.calls == []


def journal(state, name="reminders.sqlite3-journal"):
    path = state / "auth" / name
    path.touch(mode=0o600)
    return path


def test_journal_that_clears_within_the_retry_gives_ok(state, monkeypatch):
    store(state).save(reminder())
    path = journal(state)
    waits = []

    def sleep(seconds):
        waits.append(seconds)
        if len(waits) == 3:
            path.unlink()
    monkeypatch.setattr(calendar_sync, "_sleep", sleep)
    calendar = FakeCalendar()
    report = run(state, calendar)
    assert report.state == "ok" and report.created == 1
    assert waits == [0.25, 0.25, 0.25]


def test_journal_that_stays_is_skipped_busy(state, monkeypatch):
    store(state).save(reminder())
    journal(state)
    waits = []
    monkeypatch.setattr(calendar_sync, "_sleep", waits.append)
    calendar = FakeCalendar()
    report = run(state, calendar)
    assert (report.state, report.code) == ("skipped", "busy")
    assert calendar.calls == []
    assert sum(waits) == pytest.approx(5.0) and set(waits) == {0.25}


def test_owner_journal_alone_is_not_busy(state, monkeypatch):
    store(state).save(reminder())
    journal(state, "owner.sqlite3-journal")
    waits = []
    monkeypatch.setattr(calendar_sync, "_sleep", waits.append)
    report = run(state, FakeCalendar())
    assert report.state == "ok" and waits == []


@pytest.mark.parametrize("code", sorted(CODES))
@pytest.mark.parametrize("fail_on", ["events", "apply"])
def test_calendar_unavailable_gives_needs_setup_and_cools_down(state, code, fail_on):
    store(state).save(reminder())
    calendar = FakeCalendar(fail=code, fail_on=fail_on)
    report = run(state, calendar)
    expected = "failed" if code == "script" else "needs_setup"
    assert (report.state, report.code, report.created, report.updated, report.removed) == (expected, code, 0, 0, 0)
    assert calendar.ours() == []
    status = json.loads((state / "calendar" / "status.json").read_text())
    assert (status["state"], status["code"]) == (expected, code)
    calendar.calls.clear()
    assert run(state, calendar, now=NOW + timedelta(minutes=9, seconds=59)).state == "skipped"
    assert calendar.calls == []
    calendar.fail = None
    report = run(state, calendar, now=NOW + timedelta(minutes=10, seconds=1))
    assert (report.state, report.created) == ("ok", 1)


def test_cool_down_does_not_block_a_forced_run(state):
    store(state).save(reminder())
    calendar = FakeCalendar(fail="no_calendar")
    run(state, calendar)
    calendar.fail = None
    assert run(state, calendar, force=True, now=NOW + timedelta(minutes=1)).state == "ok"


def test_changed_fingerprint_after_ok_runs(state):
    store(state).save(reminder())
    calendar = FakeCalendar()
    run(state, calendar)
    store(state).save(reminder(key="b.md", subject="Second"))
    report = run(state, calendar, now=NOW + timedelta(seconds=5))
    assert (report.state, report.created) == ("ok", 1)


def test_boot_id_change_forces_a_run(state, monkeypatch):
    store(state).save(reminder())
    calendar = FakeCalendar()
    run(state, calendar)
    calendar.calls.clear()
    monkeypatch.setattr(calendar_sync, "boot_id", lambda: "boot-2")
    report = run(state, calendar, now=NOW + timedelta(minutes=1))
    assert report.state == "ok" and calendar.calls == [("events",)]
    calendar.calls.clear()
    assert run(state, calendar, now=NOW + timedelta(minutes=2)).code == "unchanged"
    assert calendar.calls == []
    assert json.loads((state / "calendar" / "boot-id").read_text()) == "boot-2"


def test_twenty_four_hour_age_forces_a_run(state):
    store(state).save(reminder())
    calendar = FakeCalendar()
    run(state, calendar)
    calendar.calls.clear()
    assert run(state, calendar, now=NOW + timedelta(hours=23, minutes=59)).code == "unchanged"
    assert calendar.calls == []
    report = run(state, calendar, now=NOW + timedelta(hours=24, minutes=1))
    assert report.state == "ok" and calendar.calls == [("events",)]
    calendar.calls.clear()
    assert run(state, calendar, now=NOW + timedelta(hours=24, minutes=2)).code == "unchanged"


def test_deleted_event_comes_back_on_a_forced_run(state):
    store(state).save(reminder())
    calendar = FakeCalendar()
    run(state, calendar)
    calendar.store.clear()
    assert run(state, calendar, now=NOW + timedelta(minutes=1)).code == "unchanged"
    report = run(state, calendar, force=True, now=NOW + timedelta(minutes=2))
    assert (report.state, report.created) == ("ok", 1)


def test_force_false_never_forces(state, monkeypatch):
    store(state).save(reminder())
    calendar = FakeCalendar()
    run(state, calendar)
    monkeypatch.setattr(calendar_sync, "boot_id", lambda: "boot-2")
    calendar.calls.clear()
    assert run(state, calendar, force=False, now=NOW + timedelta(days=3)).code == "unchanged"
    assert calendar.calls == []


def test_parallel_runs_end_with_one_event_per_reminder(state):
    store(state).save(reminder())
    store(state).save(reminder(key="b.md", subject="Second"))
    calendar = FakeCalendar(delay=0.2)
    reports = []
    threads = [threading.Thread(target=lambda: reports.append(run(state, calendar, force=True)))
               for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(item.id for item in calendar.ours()) == sorted(
        [calendar_event_id("inbox/pay-card.md"), calendar_event_id("b.md")])
    assert sorted(report.created for report in reports) == [0, 2]


def test_a_held_lock_skips_as_locked(state, monkeypatch):
    store(state).save(reminder())
    monkeypatch.setattr(calendar_sync, "LOCK_WAIT", 0.2)
    (state / "calendar").mkdir(mode=0o700)
    fd = os.open(state / "calendar" / "lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        calendar = FakeCalendar()
        report = run(state, calendar)
        assert (report.state, report.code) == ("skipped", "locked")
        assert calendar.calls == []
    finally:
        os.close(fd)
    assert run(state, FakeCalendar()).state == "ok"


def test_status_and_fingerprint_files_are_private_and_hold_no_subject(state):
    store(state).save(reminder(subject="Secret subject"))
    run(state, FakeCalendar())
    folder = state / "calendar"
    assert stat.S_IMODE(folder.stat().st_mode) == 0o700
    for name in ("status.json", "last-sync.json", "boot-id", "forced.json", "lock"):
        path = folder / name
        assert stat.S_IMODE(path.stat().st_mode) == 0o600, name
        assert "Secret" not in path.read_text(), name
    status = json.loads((folder / "status.json").read_text())
    assert set(status) == {"state", "at", "created", "updated", "removed", "code"}
    assert (status["state"], status["created"], status["code"]) == ("ok", 1, None)
    last = json.loads((folder / "last-sync.json").read_text())
    assert set(last) == {"fingerprint", "outcome", "at"}
    assert re.fullmatch(r"[0-9a-f]{64}", last["fingerprint"])


def test_fingerprint_ignores_the_alarm_and_time_passing(state):
    item = reminder(due=NOW + timedelta(hours=1))
    before = calendar_sync.fingerprint(desired_events({item.key: item}, NOW))
    after = calendar_sync.fingerprint(desired_events({item.key: item}, NOW + timedelta(hours=2)))
    assert before == after
    changed = calendar_sync.fingerprint(desired_events({item.key: replace(item, subject="Other")}, NOW))
    assert changed != before


# ---- fix round 1 ----

def test_changed_fingerprint_during_the_cool_down_runs(state):
    store(state).save(reminder())
    calendar = FakeCalendar(fail="no_calendar")
    run(state, calendar)
    calendar.fail = None
    calendar.calls.clear()
    store(state).save(reminder(key="b.md", subject="Second"))
    report = run(state, calendar, now=NOW + timedelta(minutes=2))
    assert (report.state, report.created) == ("ok", 2)
    assert calendar.calls[0] == ("events",)


class Exploding(FakeCalendar):
    def apply(self, create, update, delete):
        self.calls.append(("apply",))
        raise RuntimeError("synthetic")


def test_any_exception_records_failed_and_cools_down(state):
    store(state).save(reminder())
    calendar = Exploding()
    report = run(state, calendar)
    assert (report.state, report.code) == ("failed", "script")
    folder = state / "calendar"
    status = json.loads((folder / "status.json").read_text())
    assert (status["state"], status["code"]) == ("failed", "script")
    assert json.loads((folder / "last-sync.json").read_text())["outcome"] == "failed"
    calendar.calls.clear()
    report = run(state, calendar, now=NOW + timedelta(minutes=5))
    assert (report.state, report.code) == ("skipped", "cool_down")
    assert calendar.calls == []


def test_an_overflow_date_reminder_does_not_poison_the_sync(state):
    store(state).save(reminder())
    store(state).save(reminder(key="far.md", subject="Far", due=datetime(9999, 12, 31, 23, 50, tzinfo=UTC)))
    store(state).save(reminder(key="over.md", subject="Over",
                               due=datetime(9999, 12, 31, 22, 0, tzinfo=timezone(timedelta(hours=-5)))))
    calendar = FakeCalendar()
    report = run(state, calendar)
    assert (report.state, report.created) == ("ok", 1)
    assert [item.id for item in calendar.ours()] == [calendar_event_id("inbox/pay-card.md")]


def test_a_future_cool_down_stamp_counts_as_expired(state):
    store(state).save(reminder())
    calendar = FakeCalendar(fail="no_calendar")
    run(state, calendar, now=NOW + timedelta(hours=1))  # the clock was ahead, then stepped back
    calendar.fail = None
    calendar.calls.clear()
    report = run(state, calendar, force=False, now=NOW)
    assert report.state == "ok" and calendar.calls[0] == ("events",)


def test_a_future_force_stamp_forces(state):
    store(state).save(reminder())
    calendar = FakeCalendar()
    run(state, calendar, now=NOW + timedelta(days=3))
    calendar.calls.clear()
    report = run(state, calendar, now=NOW)
    assert report.state == "ok" and calendar.calls == [("events",)]


def test_duplicate_collapse_never_deletes_the_kept_uid():
    want = one_desired()
    plan = plan_sync({want.id: want}, [listed(want, "u1"), listed(want, "u1"), listed(want, "u2"),
                                       listed(want, "u2")])
    assert plan == SyncPlan([], [], ["u2"])


def test_sub_second_due_at_is_truncated_and_never_replaced(state):
    due = datetime(2026, 11, 5, 9, 0, 0, 600000, tzinfo=UTC)
    item = reminder(due=due)
    event = desired_events({item.key: item}, NOW)[calendar_event_id(item.key)]
    assert event.start == datetime(2026, 11, 5, 9, 0, tzinfo=UTC)
    assert event.end == datetime(2026, 11, 5, 9, 15, tzinfo=UTC)
    store(state).save(item)
    calendar = FakeCalendar()
    run(state, calendar)
    calendar.calls.clear()
    assert run(state, calendar, force=True, now=NOW + timedelta(minutes=1)).updated == 0
    assert calendar.calls == [("events",)]


def test_store_without_a_reminders_table_is_skipped(state):
    import sqlite3
    path = state / "auth" / "reminders.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE other (x)")
    connection.commit()
    connection.close()
    path.chmod(0o600)
    calendar = FakeCalendar()
    assert (run(state, calendar).state, run(state, calendar).code) == ("skipped", "no_store")
    assert calendar.calls == []


def test_unsafe_store_is_skipped(state):
    store(state).save(reminder())
    (state / "auth" / "reminders.sqlite3").chmod(0o644)
    calendar = FakeCalendar()
    report = run(state, calendar)
    assert (report.state, report.code) == ("skipped", "no_store")
    assert calendar.calls == []


def test_bad_row_is_skipped(state):
    import sqlite3
    store(state).save(reminder())
    connection = sqlite3.connect(state / "auth" / "reminders.sqlite3")
    connection.execute("UPDATE reminders SET due_at='not a time'")
    connection.commit()
    connection.close()
    calendar = FakeCalendar()
    report = run(state, calendar)
    assert (report.state, report.code) == ("skipped", "no_store")
    assert calendar.calls == []
