import json
import os
import re
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

from tools import calendar_mac
from tools.calendar_mac import CalendarEvent, CalendarUnavailable, MacCalendar

MARKER_ID = "oneos-" + "a" * 32
START = datetime(2026, 11, 5, 9, 30, tzinfo=timezone.utc)


class FakeRun:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def __call__(self, args, **kwargs):
        self.calls.append((args, kwargs))
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        returncode, stdout, stderr = result
        return subprocess.CompletedProcess(args, returncode, stdout, stderr)


def event(title="Call the bank", alarm=True, uid=""):
    notes = f"Open in OneOS on this Mac: https://localhost:8443/?reminder=1\nOneOS id: {MARKER_ID}"
    return CalendarEvent(id=MARKER_ID, title=title, start=START, end=START + timedelta(minutes=15),
                         notes=notes, alarm=alarm, uid=uid)


def listed(uid, created):
    return {"id": MARKER_ID, "uid": uid, "title": "Call the bank", "start": "2026-11-05T09:30:00.000Z",
            "end": "2026-11-05T09:45:00.000Z",
            "notes": f"Open in OneOS on this Mac: https://localhost:8443/?reminder=1\nOneOS id: {MARKER_ID}",
            "alarm": True, "created": created}


def test_argv_is_exact_with_timeout():
    run = FakeRun((0, "[]", ""))
    assert MacCalendar(run=run).events() == []
    args, kwargs = run.calls[0]
    script = args[3]
    assert args[:3] == ["/usr/bin/osascript", "-l", "JavaScript"]
    assert os.path.isabs(script) and script.endswith(os.path.join("tools", "calendar_mac.js"))
    assert os.path.exists(script)
    assert args[4] == "list"
    assert json.loads(args[5]) == {"calendar": "OneOS"}
    assert len(args) == 6
    assert kwargs["timeout"] == 60
    assert kwargs.get("shell") in (None, False)


def test_timeout_is_retried_once_then_script_error():
    run = FakeRun(subprocess.TimeoutExpired("osascript", 60), (0, "[]", ""))
    assert MacCalendar(run=run).events() == []
    assert len(run.calls) == 2

    run = FakeRun(subprocess.TimeoutExpired("osascript", 60), subprocess.TimeoutExpired("osascript", 60))
    with pytest.raises(CalendarUnavailable) as info:
        MacCalendar(run=run).events()
    assert info.value.code == "script"
    assert len(run.calls) == 2


def test_awkward_subject_travels_only_in_the_json_argument():
    subject = 'Say "hi" it\'s a \\ back\nslash \U0001F382'
    run = FakeRun((0, json.dumps({"created": 1, "updated": 0, "deleted": 0}), ""))
    MacCalendar(run=run).apply([event(title=subject)], [], [])
    args, kwargs = run.calls[0]
    assert args[5].isascii()
    assert args[4] == "apply"
    assert all(subject not in part for part in args[:5])
    assert "input" not in kwargs or kwargs["input"] is None
    payload = json.loads(args[5])
    assert payload["calendar"] == "OneOS"
    created = payload["create"][0]
    assert created["title"] == subject
    assert created["start"] == "2026-11-05T09:30:00.000Z"
    assert created["end"] == "2026-11-05T09:45:00.000Z"
    assert created["notes"].splitlines()[-1] == f"OneOS id: {MARKER_ID}"
    assert created["alarm"] is True
    assert payload["update"] == [] and payload["delete"] == []


def test_apply_addresses_updates_and_deletes_by_uid():
    run = FakeRun((0, json.dumps({"created": 0, "updated": 1, "deleted": 1}), ""))
    MacCalendar(run=run).apply([], [event(uid="UID-1", alarm=False)], ["UID-2"])
    payload = json.loads(run.calls[0][0][5])
    assert payload["update"][0]["uid"] == "UID-1"
    assert payload["update"][0]["alarm"] is False
    assert payload["delete"] == ["UID-2"]


def test_update_without_uid_is_refused_before_running():
    run = FakeRun()
    with pytest.raises(ValueError):
        MacCalendar(run=run).apply([], [event(uid="")], [])
    with pytest.raises(ValueError):
        MacCalendar(run=run).apply([], [], [""])
    assert run.calls == []


def test_times_sent_are_the_utc_form_the_script_accepts():
    run = FakeRun((0, json.dumps({"created": 1, "updated": 0, "deleted": 0}), ""))
    zoned = START.astimezone(timezone(timedelta(hours=5, minutes=30)))
    MacCalendar(run=run).apply([CalendarEvent(id=MARKER_ID, title="x", start=zoned, end=zoned, notes=event().notes,
                                              alarm=False)], [], [])
    sent = json.loads(run.calls[0][0][5])["create"][0]["start"]
    assert sent == "2026-11-05T09:30:00.000Z"
    accepted = re.search(r"var ISO_UTC = /(.*)/;", calendar_mac.SCRIPT.read_text()).group(1)
    assert re.fullmatch(accepted, sent)


def test_missing_osascript_is_a_script_error():
    run = FakeRun(FileNotFoundError("/usr/bin/osascript"))
    with pytest.raises(CalendarUnavailable) as info:
        MacCalendar(run=run).events()
    assert info.value.code == "script"
    assert len(run.calls) == 1


def test_naive_datetime_is_refused_before_running():
    run = FakeRun()
    naive = CalendarEvent(id=MARKER_ID, title="x", start=datetime(2026, 11, 5, 9, 30),
                          end=datetime(2026, 11, 5, 9, 45), notes="", alarm=False)
    with pytest.raises(ValueError):
        MacCalendar(run=run).apply([naive], [], [])
    assert run.calls == []


@pytest.mark.parametrize("returncode, code", [(3, "no_calendar"), (4, "permission"), (5, "duplicate_calendar"),
                                              (6, "read_only"), (1, "script"), (7, "script")])
def test_exit_codes_map_to_their_codes(returncode, code):
    run = FakeRun((returncode, "", "boom"))
    with pytest.raises(CalendarUnavailable) as info:
        MacCalendar(run=run).events()
    assert info.value.code == code


def test_stderr_never_reaches_the_exception():
    secret = "Pick up the biopsy result"
    for returncode in (1, 3, 4):
        run = FakeRun((returncode, f"partial {secret}", f"execution error: {secret} (-1743)"))
        with pytest.raises(CalendarUnavailable) as info:
            MacCalendar(run=run).events()
        assert secret not in str(info.value)
        assert secret not in repr(info.value)
        assert info.value.__cause__ is None or secret not in str(info.value.__cause__)


@pytest.mark.parametrize("stdout", ["not json", "{\"a\": 1}", "[{\"id\": 1}]"])
def test_unparsable_output_is_a_script_error(stdout):
    run = FakeRun((0, stdout, ""))
    with pytest.raises(CalendarUnavailable) as info:
        MacCalendar(run=run).events()
    assert info.value.code == "script"


def test_list_parses_two_events_sharing_a_marker():
    run = FakeRun((0, json.dumps([listed("UID-1", "2026-11-01T08:00:00.000Z"), listed("UID-2", None)]), ""))
    events = MacCalendar(run=run).events()
    assert [item.uid for item in events] == ["UID-1", "UID-2"]
    assert all(item.id == MARKER_ID for item in events)
    first = events[0]
    assert first.title == "Call the bank"
    assert first.start == START and first.start.tzinfo is not None
    assert first.end == START + timedelta(minutes=15)
    assert first.alarm is True
    assert first.created == datetime(2026, 11, 1, 8, 0, tzinfo=timezone.utc)
    assert events[1].created is None


def test_calendar_name_is_passed_through():
    run = FakeRun((0, "[]", ""))
    MacCalendar(run=run, name="Other").events()
    assert json.loads(run.calls[0][0][5]) == {"calendar": "Other"}


def test_selftest_creates_lists_and_deletes(capsys, monkeypatch):
    seen = {}

    def fake(args, **kwargs):
        payload = json.loads(args[5])
        if args[4] == "apply" and payload["create"]:
            seen["created"] = payload["create"][0]
            return subprocess.CompletedProcess(args, 0, json.dumps({"created": 1, "updated": 0, "deleted": 0}), "")
        if args[4] == "list":
            item = dict(seen["created"], uid="UID-T", created=None)
            return subprocess.CompletedProcess(args, 0, json.dumps([item]), "")
        assert payload["delete"] == ["UID-T"]
        seen["deleted"] = True
        return subprocess.CompletedProcess(args, 0, json.dumps({"created": 0, "updated": 0, "deleted": 1}), "")

    monkeypatch.setattr(calendar_mac.subprocess, "run", fake)
    assert calendar_mac.main(["selftest"]) == 0
    assert capsys.readouterr().out.strip() == "ok"
    assert seen["created"]["title"] == "OneOS test"
    assert seen["created"]["alarm"] is True
    start = datetime.fromisoformat(seen["created"]["start"])
    assert timedelta(minutes=58) < start - datetime.now(timezone.utc) <= timedelta(hours=1)
    assert seen["deleted"]


def test_selftest_reports_script_when_the_event_is_not_listed(capsys, monkeypatch):
    def fake(args, **kwargs):
        if args[4] == "list":
            return subprocess.CompletedProcess(args, 0, "[]", "")
        return subprocess.CompletedProcess(args, 0, json.dumps({"created": 1, "updated": 0, "deleted": 0}), "")

    monkeypatch.setattr(calendar_mac.subprocess, "run", fake)
    assert calendar_mac.main(["selftest"]) == 1
    assert capsys.readouterr().out.strip() == "script"


def test_selftest_needs_an_argument(capsys):
    assert calendar_mac.main([]) == 2
    assert capsys.readouterr().out == ""


def test_selftest_prints_the_code(capsys, monkeypatch):
    monkeypatch.setattr(calendar_mac.subprocess, "run",
                        lambda args, **kwargs: subprocess.CompletedProcess(args, 3, "", "secret"))
    assert calendar_mac.main(["selftest"]) != 0
    assert capsys.readouterr().out.strip() == "no_calendar"


def test_script_has_one_error_code_table_and_exact_match():
    source = (calendar_mac.SCRIPT).read_text()
    assert "names[i] === wanted" in source
    assert source.count("var EXIT = {") == 1
    assert "whose(" not in source
    assert "-1743" in source
    assert "writable()" in source


def test_script_checks_input_before_touching_calendar():
    source = calendar_mac.SCRIPT.read_text()
    body = source[source.index("function run(argv)"):]
    assert body.index("COMMANDS.hasOwnProperty(argv[0])") < body.index("Application('Calendar')")
    assert body.index("JSON.parse(argv[1])") < body.index("Application('Calendar')")
