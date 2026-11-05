import json
import os
import re
import subprocess
import sys
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


def test_timed_out_apply_is_not_retried():
    run = FakeRun(subprocess.TimeoutExpired("osascript", 60), (0, json.dumps({"created": 1, "updated": 0, "deleted": 0}), ""))
    with pytest.raises(CalendarUnavailable) as info:
        MacCalendar(run=run).apply([event()], [], [])
    assert info.value.code == "script"
    assert len(run.calls) == 1


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


class FakeCalendar:
    """A stand-in for the script that keeps events in memory, for the selftest."""

    def __init__(self, fail_lists=0, keep_replaced=False):
        self.events, self.calls, self.fail_lists, self.next = {}, [], fail_lists, 0
        self.keep_replaced = keep_replaced

    def add(self, item):
        self.next += 1
        self.events[f"UID-{self.next}"] = dict(item, uid=f"UID-{self.next}", created=None)

    def __call__(self, args, **kwargs):
        command, payload = args[4], json.loads(args[5])
        self.calls.append((command, payload))
        if command == "list":
            if self.fail_lists:
                self.fail_lists -= 1
                return subprocess.CompletedProcess(args, 1, "", "")
            return subprocess.CompletedProcess(args, 0, json.dumps(list(self.events.values())), "")
        for item in payload["create"]:
            self.add(item)
        for item in payload["update"]:
            old = self.events.get(item["uid"])
            if old is None:
                continue
            if any(old[key] != item[key] for key in ("start", "end", "alarm")):  # Like the script: replace.
                self.add(item)
                if not self.keep_replaced:
                    del self.events[item["uid"]]
            else:
                self.events[item["uid"]] = dict(item, created=None)
        for uid in payload["delete"]:
            self.events.pop(uid, None)
        counts = {"created": len(payload["create"]), "updated": len(payload["update"]),
                  "deleted": len(payload["delete"])}
        return subprocess.CompletedProcess(args, 0, json.dumps(counts), "")


def test_selftest_creates_lists_moves_and_deletes(capsys, monkeypatch):
    fake = FakeCalendar()
    monkeypatch.setattr(calendar_mac.subprocess, "run", fake)
    assert calendar_mac.main(["selftest"]) == 0
    assert capsys.readouterr().out.strip() == "ok"
    assert [command for command, _ in fake.calls] == ["apply", "list", "apply", "list", "list", "apply"]
    created = fake.calls[0][1]["create"][0]
    assert created["title"] == "OneOS test" and created["alarm"] is True
    start = datetime.fromisoformat(created["start"])
    assert timedelta(minutes=58) < start - datetime.now(timezone.utc) <= timedelta(hours=1)
    moved = fake.calls[2][1]["update"][0]
    assert moved["uid"] == "UID-1" and moved["title"] != "OneOS test" and moved["alarm"] is True
    assert datetime.fromisoformat(moved["start"]) == start + timedelta(days=1)
    assert datetime.fromisoformat(moved["end"]) == start + timedelta(days=1, minutes=15)
    assert fake.calls[-1][1]["delete"] == ["UID-2"]  # The replacement, under its new uid.
    assert fake.events == {}


def test_selftest_reports_script_when_the_move_leaves_two_events(capsys, monkeypatch):
    fake = FakeCalendar(keep_replaced=True)
    monkeypatch.setattr(calendar_mac.subprocess, "run", fake)
    assert calendar_mac.main(["selftest"]) == 1
    assert capsys.readouterr().out.strip() == "script"
    assert fake.events == {}  # Both are cleaned up by the marker.


def test_selftest_cleans_up_even_on_an_unexpected_error(monkeypatch):
    fake = FakeCalendar()
    calls = {"n": 0}
    real_events = MacCalendar.events

    def events(self):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("unexpected")
        return real_events(self)

    monkeypatch.setattr(MacCalendar, "events", events)
    with pytest.raises(RuntimeError):
        calendar_mac.selftest(MacCalendar(run=fake))
    assert fake.events == {}


def test_selftest_reports_script_when_the_move_does_not_show(capsys, monkeypatch):
    fake = FakeCalendar()
    original = fake.__call__

    def ignore_updates(args, **kwargs):
        if args[4] == "apply" and json.loads(args[5])["update"]:
            return subprocess.CompletedProcess(args, 0, json.dumps({"created": 0, "updated": 1, "deleted": 0}), "")
        return original(args, **kwargs)

    monkeypatch.setattr(calendar_mac.subprocess, "run", ignore_updates)
    assert calendar_mac.main(["selftest"]) == 1
    assert capsys.readouterr().out.strip() == "script"
    assert fake.events == {}


def test_selftest_cleans_up_when_the_first_list_fails(capsys, monkeypatch):
    fake = FakeCalendar(fail_lists=1)
    monkeypatch.setattr(calendar_mac.subprocess, "run", fake)
    assert calendar_mac.main(["selftest"]) == 1
    assert capsys.readouterr().out.strip() == "script"
    assert fake.events == {}


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
    assert "calendars.byName(wanted)" in source and "events.byId(" in source
    # The live check: calendar identifiers and calendar byId fail, and deleting an alarm fails.
    assert "calendarIdentifier" not in source and "calendars.byId(" not in source
    assert not re.search(r"[A-Za-z]*Alarms\[", source) and "removeAlarms" not in source
    assert not re.search(r"(calendars|events)\[", source)  # Never addressed by position.


def test_script_checks_input_before_touching_calendar():
    source = calendar_mac.SCRIPT.read_text()
    body = source[source.index("function run(argv)"):]
    assert body.index("COMMANDS.hasOwnProperty(argv[0])") < body.index("Application('Calendar')")
    assert body.index("JSON.parse(argv[1])") < body.index("Application('Calendar')")


MAC = sys.platform == "darwin" and os.path.exists("/usr/bin/osascript")
# Replaces the script's run handler (the later declaration wins), so nothing below makes an Apple event.
HARNESS = """
function run(argv) {
    var cases = JSON.parse(argv[0]), now = Date.now();
    function code(f) { try { f(); return 'none'; } catch (e) { return codeFor(e); } }
    function at(hours) { return new Date(now + hours * 3600e3).toISOString(); }
    return output({
        exit: EXIT,
        markers: cases.markers.map(markerOf),
        instants: cases.instants.map(function (v) {
            return code(function () { instant(v); }) === 'none' ? instant(v).toISOString() : 'refused'; }),
        alarms: [1, -1].map(function (h) {
            return checked({title: 'x', notes: cases.marker, alarm: true, start: at(h), end: at(h)}, now).alarm; }),
        alarmOff: checked({title: 'x', notes: cases.marker, alarm: false, start: at(1), end: at(1)}, now).alarm,
        unmarked: code(function () { checked({title: 'x', notes: 'x', alarm: false, start: at(1), end: at(1)}, now); }),
        backwards: code(function () { checked({title: 'x', notes: cases.marker, alarm: false, start: at(2), end: at(1)}, now); }),
        echo: JSON.parse(output({text: cases.text})).text,
        ascii: /^[\\x00-\\x7e]*$/.test(output({text: cases.text})),
        codes: [code(function () { fail('read_only'); }), code(function () { fail('nonsense'); }),
                codeFor({errorNumber: -1743}), codeFor({errorNumber: -1728}), codeFor(new Error('x'))],
        calendars: cases.calendars.map(function (names) { return code(function () { pickCalendar(names, 'OneOS'); }); }),
        kinds: cases.kinds.map(function (k) {
            var current = {start: new Date(at(k[0])), end: new Date(at(k[1])), alarm: k[2]};
            var item = {start: new Date(at(k[3])), end: new Date(at(k[4])), alarm: k[5]};
            return updateKind(current, item, now); })
    });
}
"""


def osascript(*args):
    return subprocess.run(["/usr/bin/osascript", "-l", "JavaScript", *args], capture_output=True, text=True,
                          timeout=60, check=False)


@pytest.mark.skipif(not MAC, reason="needs macOS osascript")
def test_script_pure_functions_under_osascript(tmp_path):
    source = calendar_mac.SCRIPT.read_text()
    assert "Application(" not in HARNESS
    harness = tmp_path / "harness.js"
    harness.write_text(source + HARNESS)
    hex32 = "0123456789abcdef" * 2
    marker = f"OneOS id: oneos-{hex32}"
    text = 'Say "hi" it\'s a \\ back\nslash \U0001F382 é'
    cases = {"marker": marker, "text": text,
             "markers": [f"Open\n{marker}", f"Open\r\n{marker}\n", marker, f"{marker}\nmore", f"{marker} ",
                         f"OneOS id: oneos-{hex32.upper()}", f"OneOS id: oneos-{hex32[:-1]}", "", None],
             "instants": ["2026-11-05T09:30:00.000Z", "2026-11-05T09:30:00Z", "2026-11-05T09:30:00",
                          "2026-11-05T09:30:00+05:30", "2026-11-05", "nonsense"],
             "calendars": [["OneOS"], ["Work", "OneOS", "Home"], [], ["Oneos"], ["OneOS", "OneOS"],
                           ["OneOS", "oneos"], ["ONEOS", "OneOS"], ["ONEOS", "Oneos"]],
             # [current start, end (hours from now), has alarm, new start, end, wants alarm]
             "kinds": [[1, 2, True, 1, 2, True],      # same times and alarm: title/notes in place
                       [1, 2, True, 24, 25, True],    # moved later
                       [1, 2, True, 1, 3, True],      # new end only
                       [1, 2, False, 1, 2, True],     # alarm wanted, none there
                       [1, 2, True, 1, 2, False],     # alarm no longer wanted, start still ahead
                       [-2, -1, True, -2, -1, False], # start passed: the fired alarm is no reason to replace
                       [1, 2, False, 1, 2, False]]}
    result = osascript(str(harness), json.dumps(cases))
    assert result.returncode == 0, "harness failed"
    out = json.loads(result.stdout)
    assert {number: code for code, number in out["exit"].items() if code != "script"} == calendar_mac.EXIT_CODES
    assert out["exit"]["script"] not in calendar_mac.EXIT_CODES
    assert out["markers"] == [f"oneos-{hex32}"] * 3 + [None] * 6
    assert out["instants"] == ["2026-11-05T09:30:00.000Z"] * 2 + ["refused"] * 4
    assert out["alarms"] == [True, False] and out["alarmOff"] is False
    assert out["unmarked"] == "script" and out["backwards"] == "script"
    assert out["echo"] == text and out["ascii"] is True
    assert out["codes"] == ["read_only", "script", "permission", "script", "script"]
    assert out["calendars"] == ["none", "none", "no_calendar", "no_calendar", "duplicate_calendar",
                                "duplicate_calendar", "duplicate_calendar", "no_calendar"]
    assert out["kinds"] == ["inPlace", "replace", "replace", "replace", "replace", "inPlace", "inPlace"]


@pytest.mark.skipif(not MAC, reason="needs macOS osascript")
@pytest.mark.parametrize("args", [["bogus", "{}"], ["list"], ["list", "not json"], ["list", "[]"],
                                  ["apply", '{"calendar": ""}']])
def test_script_refuses_bad_input_before_calendar(args):
    # Each of these fails before the script's Application('Calendar') line.
    result = osascript(str(calendar_mac.SCRIPT), *args)
    assert result.returncode == 1 and result.stdout == ""
