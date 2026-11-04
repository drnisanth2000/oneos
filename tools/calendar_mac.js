// OneOS adapter for the Mac's Calendar app (JavaScript for Automation).
// Usage: osascript -l JavaScript calendar_mac.js <list|apply> <json>
// Values arrive only as the one JSON argument and are read with JSON.parse.
// Output is JSON on stdout. Failures exit with a code from EXIT and print nothing.
//
// Every write addresses its target by a stable id: the calendar by its calendarIdentifier and
// each event by its uid (both are Calendar's `id` key), never by a position, which Calendar
// re-resolves on every Apple event and which a sync from iCloud can shift.
ObjC.import('stdlib');

// ---- Pure part: no Apple events. tests/test_calendar_mac.py runs these functions directly. ----

// The one place where failure codes map to exit codes (tools/calendar_mac.py reads them back).
var EXIT = {script: 1, no_calendar: 3, permission: 4, duplicate_calendar: 5, read_only: 6};
var PERMISSION_ERROR = -1743;
var MARKER = /^OneOS id: oneos-[0-9a-f]{32}$/;
var ISO_UTC = /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d{1,3})?Z$/;
// Open file alarms are left alone: Calendar refuses to modify them (a save error).
var ALARM_KINDS = ['displayAlarms', 'soundAlarms', 'mailAlarms'];

function Failure(code) { this.code = code; }
function fail(code) { throw new Failure(code); }

function codeFor(error) {
    if (error instanceof Failure && EXIT.hasOwnProperty(error.code)) return error.code;
    if (error && error.errorNumber === PERMISSION_ERROR) return 'permission';
    return 'script';
}

// ASCII-only JSON, so no locale on the reading side can garble it.
function output(value) {
    return JSON.stringify(value).replace(/[\u007f-\uffff]/g, function (ch) {
        return '\\u' + ('0000' + ch.charCodeAt(0).toString(16)).slice(-4);
    });
}

function markerOf(notes) {
    if (typeof notes !== 'string') return null;
    var lines = notes.replace(/(\r\n|\r|\n)$/, '').split(/\r\n|\r|\n/);
    var last = lines[lines.length - 1];
    return MARKER.test(last) ? last.slice('OneOS id: '.length) : null;
}

function instant(value) {
    if (typeof value !== 'string' || !ISO_UTC.test(value)) fail('script');
    var date = new Date(value);  // An ISO UTC string: a zoned instant, never a floating date.
    if (isNaN(date.getTime())) fail('script');
    return date;
}

function iso(date) {
    return (date instanceof Date && !isNaN(date.getTime())) ? date.toISOString() : null;
}

function checked(item, now) {
    if (!item || typeof item.title !== 'string' || typeof item.notes !== 'string' ||
        typeof item.alarm !== 'boolean') fail('script');
    if (!markerOf(item.notes)) fail('script');  // No OneOS event may exist without its marker.
    var start = instant(item.start), end = instant(item.end);
    if (end.getTime() < start.getTime()) fail('script');
    return {title: item.title, notes: item.notes, start: start, end: end,
            alarm: item.alarm && start.getTime() > now};  // No alert for a start already past.
}

// The order to set the times in so Calendar never sees a start after the end:
// moving later past the current end sets the end first; otherwise the start goes first.
function moveOrder(currentEnd, newStart) {
    return newStart.getTime() > currentEnd.getTime() ? ['endDate', 'startDate'] : ['startDate', 'endDate'];
}

function sameList(a, b) {
    if (a.length !== b.length) return false;
    for (var i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
    return true;
}

// ---- Calendar part. ----

function oneCalendar(app, wanted) {
    var calendars = app.calendars;
    var names = calendars.name(), ids = calendars.calendarIdentifier();
    if (!sameList(names, calendars.name()) || ids.length !== names.length) fail('script');
    var matches = [];
    for (var i = 0; i < names.length; i++) {
        if (names[i] === wanted) matches.push(ids[i]);  // Case-sensitive; AppleScript `whose` is not.
    }
    if (matches.length === 0) fail('no_calendar');
    if (matches.length > 1) fail('duplicate_calendar');
    var calendar = app.calendars.byId(matches[0]);
    if (calendar.name() !== wanted) fail('script');
    if (calendar.writable() !== true) fail('read_only');
    return calendar;
}

// Bulk reads, kept only when the uid list is the same before and after them, so no uid is
// ever paired with another event's values.
function snapshot(calendar, fields) {
    for (var attempt = 0; attempt < 3; attempt++) {
        var every = calendar.events;
        var uids = every.uid(), values = {};
        var whole = fields.every(function (field) {
            values[field] = every[field]();  // One bulk read of this property for all events.
            return values[field].length === uids.length;
        });
        if (whole && sameList(uids, every.uid())) return {uids: uids, values: values};
    }
    fail('script');
}

// The event with this uid, only while it still carries a OneOS marker; null when it is gone.
function ours(calendar, uid) {
    var event = calendar.events.byId(uid);
    if (!event.exists()) return null;
    return markerOf(event.description()) ? event : null;
}

function list(app, calendar) {
    var seen = snapshot(calendar, ['description', 'summary', 'startDate', 'endDate']);
    var found = [];
    for (var i = 0; i < seen.uids.length; i++) {
        var id = markerOf(seen.values.description[i]);
        if (!id) continue;
        found.push({
            id: id, uid: seen.uids[i], title: seen.values.summary[i] || '',
            start: iso(seen.values.startDate[i]), end: iso(seen.values.endDate[i]),
            notes: seen.values.description[i],
            alarm: calendar.events.byId(seen.uids[i]).displayAlarms.length > 0,
            created: null  // Calendar's scripting dictionary has no creation date.
        });
    }
    return found;
}

function addAlarm(app, event) {
    event.displayAlarms.push(app.DisplayAlarm({triggerInterval: 0}));  // An alert at the start time.
}

function removeAlarms(app, event) {
    ALARM_KINDS.forEach(function (kind) {
        for (var n = event[kind].length - 1; n >= 0; n--) app.delete(event[kind][n]);
    });
}

function apply(app, calendar, change) {
    if (!change || !Array.isArray(change.create) || !Array.isArray(change.update) ||
        !Array.isArray(change.delete)) fail('script');
    var now = Date.now();
    var creates = change.create.map(function (item) { return checked(item, now); });
    var updates = change.update.map(function (item) {
        if (!item || typeof item.uid !== 'string' || !item.uid) fail('script');
        var value = checked(item, now);
        value.uid = item.uid;
        return value;
    });
    change.delete.forEach(function (uid) { if (typeof uid !== 'string' || !uid) fail('script'); });

    var summary = {created: 0, updated: 0, deleted: 0};
    updates.forEach(function (item) {
        var event = ours(calendar, item.uid);
        if (!event) return;  // Gone, or no longer OneOS's: nothing to do.
        event.summary = item.title;
        moveOrder(event.endDate(), item.start).forEach(function (field) {
            event[field] = field === 'startDate' ? item.start : item.end;
        });
        event.description = item.notes;
        removeAlarms(app, event);
        if (item.alarm) addAlarm(app, event);
        summary.updated++;
    });
    change.delete.forEach(function (uid) {
        var event = ours(calendar, uid);
        if (!event) return;
        app.delete(event);
        summary.deleted++;
    });
    creates.forEach(function (item) {
        // The notes, and so the marker, are part of the creation record.
        var made = app.Event({summary: item.title, startDate: item.start, endDate: item.end,
                              description: item.notes});
        calendar.events.push(made);
        var event = calendar.events.byId(made.uid());
        if (event.description() !== item.notes) fail('script');
        if (item.alarm) addAlarm(app, event);
        summary.created++;
    });
    return summary;
}

var COMMANDS = {list: list, apply: apply};

function run(argv) {
    try {
        // Bad input fails here, before Calendar is touched.
        if (argv.length !== 2 || !COMMANDS.hasOwnProperty(argv[0])) fail('script');
        var input;
        try { input = JSON.parse(argv[1]); } catch (error) { fail('script'); }
        if (!input || typeof input !== 'object' || Array.isArray(input)) fail('script');
        if (typeof input.calendar !== 'string' || !input.calendar) fail('script');
        var app = Application('Calendar');
        var calendar = oneCalendar(app, input.calendar);
        return output(COMMANDS[argv[0]](app, calendar, input));
    } catch (error) {
        $.exit(EXIT[codeFor(error)]);
    }
}
