"""Host-side adapter for the Mac's Calendar app, through tools/calendar_mac.js (JXA)."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys

SCRIPT = Path(__file__).resolve().parent / 'calendar_mac.js'
OSASCRIPT = '/usr/bin/osascript'
TIMEOUT = 60
EXIT_CODES = {3: 'no_calendar', 4: 'permission', 5: 'duplicate_calendar', 6: 'read_only'}
CODES = frozenset({*EXIT_CODES.values(), 'script'})


@dataclass(frozen=True)
class CalendarEvent:
    id: str
    title: str
    start: datetime
    end: datetime
    notes: str
    alarm: bool
    uid: str = ''
    created: datetime | None = None


class CalendarUnavailable(Exception):
    """Calendar cannot be used; carries a code only, never script output."""

    def __init__(self, code: str):
        if code not in CODES:
            code = 'script'
        super().__init__(code)
        self.code = code


def iso_utc(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('calendar times must be timezone-aware')
    return value.astimezone(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('calendar times must be timezone-aware')
    return parsed.astimezone(timezone.utc)


def event_json(event: CalendarEvent) -> dict:
    return {'id': event.id, 'uid': event.uid, 'title': event.title, 'start': iso_utc(event.start),
            'end': iso_utc(event.end), 'notes': event.notes, 'alarm': bool(event.alarm)}


def parse_event(item) -> CalendarEvent:
    if not isinstance(item, dict):
        raise ValueError('event must be an object')
    for key in ('id', 'uid', 'title', 'start', 'end', 'notes'):
        if not isinstance(item.get(key), str):
            raise ValueError('event field has the wrong type')
    if not isinstance(item.get('alarm'), bool):
        raise ValueError('event alarm must be a boolean')
    created = item.get('created')
    if created is not None and not isinstance(created, str):
        raise ValueError('event created has the wrong type')
    return CalendarEvent(id=item['id'], title=item['title'], start=parse_utc(item['start']),
                         end=parse_utc(item['end']), notes=item['notes'], alarm=item['alarm'],
                         uid=item['uid'], created=parse_utc(created) if created else None)


class MacCalendar:
    def __init__(self, *, run=subprocess.run, name='OneOS'):
        self.run = run
        self.name = name

    def call(self, command: str, payload: dict, *, retry: bool = False):
        """Run one script command. Only reads are retried: a timed-out apply may have written."""
        argv = [OSASCRIPT, '-l', 'JavaScript', str(SCRIPT), command,
                json.dumps({'calendar': self.name, **payload})]
        attempts = 2 if retry else 1
        for attempt in range(attempts):
            try:
                result = self.run(argv, check=False, capture_output=True, encoding='utf-8',
                                  errors='replace', timeout=TIMEOUT)
                break
            except subprocess.TimeoutExpired:
                if attempt == attempts - 1:
                    raise CalendarUnavailable('script') from None
            except (OSError, subprocess.SubprocessError):
                raise CalendarUnavailable('script') from None
        if result.returncode:
            raise CalendarUnavailable(EXIT_CODES.get(result.returncode, 'script'))
        try:
            return json.loads(result.stdout)
        except (TypeError, ValueError):
            raise CalendarUnavailable('script') from None

    def events(self) -> list[CalendarEvent]:
        value = self.call('list', {}, retry=True)
        try:
            if not isinstance(value, list):
                raise ValueError('list output must be an array')
            return [parse_event(item) for item in value]
        except (TypeError, ValueError):
            raise CalendarUnavailable('script') from None

    def apply(self, create: list[CalendarEvent], update: list[CalendarEvent], delete: list[str]) -> None:
        payload = {'create': [event_json(item) for item in create],
                   'update': [event_json(item) for item in update],
                   'delete': [str(uid) for uid in delete]}
        if any(not item['uid'] for item in payload['update']) or not all(payload['delete']):
            raise ValueError('updates and deletes need a calendar uid')
        value = self.call('apply', payload)
        if not isinstance(value, dict) or not all(isinstance(value.get(key), int)
                                                  for key in ('created', 'updated', 'deleted')):
            raise CalendarUnavailable('script')


def only_match(found: list[CalendarEvent], want: CalendarEvent) -> CalendarEvent | None:
    """The one listed event with want's marker, when it also has want's title, times and alert."""
    mine = [item for item in found if item.id == want.id]
    if len(mine) != 1:
        return None
    item = mine[0]
    same = (item.title, item.start, item.end, item.alarm) == (want.title, want.start, want.end, want.alarm)
    return item if same else None


def clean_up(calendar: MacCalendar, marker: str) -> str:
    try:
        uids = [item.uid for item in calendar.events() if item.id == marker]
        if uids:
            calendar.apply([], [], uids)
    except CalendarUnavailable as exc:
        return exc.code
    return 'ok'


def selftest(calendar: MacCalendar) -> str:
    """Create "OneOS test" one hour ahead, list it, move it a day later under a new title (which
    replaces the event), list it, delete it.

    Returns 'ok' or the failure code. The test event (and any an interrupted run left) is
    deleted by its marker whatever happens in between.
    """
    marker = 'oneos-' + '0' * 32
    start = (datetime.now(timezone.utc) + timedelta(hours=1)).replace(second=0, microsecond=0)
    test = CalendarEvent(id=marker, title='OneOS test', start=start, end=start + timedelta(minutes=15),
                         notes=f'OneOS selftest\nOneOS id: {marker}', alarm=True)
    outcome = 'ok'
    try:
        calendar.apply([test], [], [])
        made = only_match(calendar.events(), test)
        if made is None:
            outcome = 'script'
        else:
            moved = replace(test, uid=made.uid, title='OneOS test (moved)', start=test.start + timedelta(days=1),
                            end=test.end + timedelta(days=1))
            calendar.apply([], [moved], [])
            if only_match(calendar.events(), moved) is None:
                outcome = 'script'
    except CalendarUnavailable as exc:
        outcome = exc.code
    finally:
        cleaned = clean_up(calendar, marker)
    return outcome if outcome != 'ok' else cleaned


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv != ['selftest']:
        print('usage: python -m tools.calendar_mac selftest', file=sys.stderr)
        return 2
    outcome = selftest(MacCalendar(run=subprocess.run))
    print(outcome)
    return 0 if outcome == 'ok' else 1


if __name__ == '__main__':
    sys.exit(main())
