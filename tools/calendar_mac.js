// OneOS adapter for the Mac's Calendar app (JavaScript for Automation).
// Usage: osascript -l JavaScript calendar_mac.js <list|apply> <json>
// Values arrive only as the one JSON argument and are read with JSON.parse.
// Output is JSON on stdout. Failures exit with a code from EXIT and print nothing.
ObjC.import('stdlib');

// The one place where failure codes map to exit codes (tools/calendar_mac.py reads them back).
var EXIT = {script: 1, no_calendar: 3, permission: 4, duplicate_calendar: 5, read_only: 6};
var PERMISSION_ERROR = -1743;
var MARKER = /^OneOS id: oneos-[0-9a-f]{32}$/;
var ISO_UTC = /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d{1,3})?Z$/;

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

function oneCalendar(app, wanted) {
    if (typeof wanted !== 'string' || !wanted) fail('script');
    var names = app.calendars.name();
    var matches = [];
    for (var i = 0; i < names.length; i++) {
        if (names[i] === wanted) matches.push(i);  // Case-sensitive; AppleScript `whose` is not.
    }
    if (matches.length === 0) fail('no_calendar');
    if (matches.length > 1) fail('duplicate_calendar');
    var calendar = app.calendars[matches[0]];
    if (calendar.writable() !== true) fail('read_only');
    return calendar;
}

function snapshot(calendar) {
    var events = calendar.events;
    return {uids: events.uid(), notes: events.description()};
}

function list(app, calendar) {
    var events = calendar.events;
    var seen = snapshot(calendar);
    var titles = events.summary(), starts = events.startDate(), ends = events.endDate();
    var found = [];
    for (var i = 0; i < seen.uids.length; i++) {
        var id = markerOf(seen.notes[i]);
        if (!id) continue;
        found.push({
            id: id, uid: seen.uids[i], title: titles[i] || '', start: iso(starts[i]), end: iso(ends[i]),
            notes: seen.notes[i], alarm: events[i].displayAlarms.length > 0,
            created: null  // Calendar's scripting dictionary has no creation date.
        });
    }
    return found;
}

function checked(item) {
    if (!item || typeof item.title !== 'string' || typeof item.notes !== 'string' ||
        typeof item.alarm !== 'boolean') fail('script');
    if (!markerOf(item.notes)) fail('script');  // No OneOS event may exist without its marker.
    var start = instant(item.start), end = instant(item.end);
    if (end.getTime() < start.getTime()) fail('script');
    return {title: item.title, notes: item.notes, start: start, end: end,
            alarm: item.alarm && start.getTime() > Date.now()};  // No alert for a start already past.
}

var ALARM_KINDS = ['displayAlarms', 'soundAlarms', 'mailAlarms', 'openFileAlarms'];

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
    var creates = change.create.map(checked);
    var updates = change.update.map(function (item) {
        if (!item || typeof item.uid !== 'string' || !item.uid) fail('script');
        var value = checked(item);
        value.uid = item.uid;
        return value;
    });
    change.delete.forEach(function (uid) { if (typeof uid !== 'string' || !uid) fail('script'); });

    var seen = snapshot(calendar);
    var ours = {};
    for (var i = 0; i < seen.uids.length; i++) {
        if (markerOf(seen.notes[i])) ours[seen.uids[i]] = true;  // Only OneOS's own events are touched.
    }
    // Find an event by its exact uid in a fresh list, so earlier edits cannot shift a position.
    function at(uid) {
        if (!ours.hasOwnProperty(uid)) return null;
        var position = calendar.events.uid().indexOf(uid);
        if (position < 0) return null;  // Gone since the list: nothing to do.
        var event = calendar.events[position];
        if (event.uid() !== uid) fail('script');
        return event;
    }

    var summary = {created: 0, updated: 0, deleted: 0};
    updates.forEach(function (item) {
        var event = at(item.uid);
        if (!event) return;
        event.summary = item.title;
        event.startDate = item.start;
        event.endDate = item.end;
        event.description = item.notes;
        removeAlarms(app, event);
        if (item.alarm) addAlarm(app, event);
        summary.updated++;
    });
    change.delete.forEach(function (uid) {
        var event = at(uid);
        if (!event) return;
        app.delete(event);
        delete ours[uid];
        summary.deleted++;
    });
    creates.forEach(function (item) {
        // The notes, and so the marker, are part of the creation record.
        var event = app.Event({summary: item.title, startDate: item.start, endDate: item.end,
                               description: item.notes});
        calendar.events.push(event);
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
