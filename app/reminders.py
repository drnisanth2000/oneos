"""Reminders: a private store beside the auth database.

Reminders are owner-private state, not vault content. They live in
`<ONEOS_AUTH_STATE_DIR>/reminders.sqlite3` so the owner-run backup already
copies them, and saving or removing one never touches the vault or Git.

The safety rules mirror `app.auth.AuthStore`: no symlinked component, a
directory owned by this process with no group/other bits, a regular
single-link file with mode 0600. Reading never creates anything; the file
appears on the first save.

Review flags (a filed note put in "To review") live in the same file, in their
own `review_flags` table. That table is created only inside a write
transaction that stores a flag; a file without it simply has no flags, and no
read or no-op write ever creates it.

A reminder kept on a card that landed in Done (an approval that files a note
as `active`) carries a private "resolved" mark, in its own `resolved_reminders`
table. The row stays, so the Done card still shows its reminder, but it is no
longer open: `snapshot()` leaves it out. The table follows the same rule as
`review_flags`: created only inside the write transaction that first marks a
reminder, never by a read or a no-op write. The mark moves with the row on
`rekey` and `rename_entity` and goes with it on `remove`.
"""
from __future__ import annotations

import os
import hashlib
import re
import sqlite3
import stat
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .console_routing import failure_contract, structured_reader

DATABASE_NAME = "reminders.sqlite3"
SUBJECT_MAX = 120
FLAG_COMMENT_MAX = 500
_LINE_BREAKS = ("\r", "\n", "\x0b", "\x0c", "\x85", " ", " ")
_MAX_OFFSET_MINUTES = 14 * 60
_MIN_YEAR = 1970
_MAX_YEAR = 9999


class ReminderStoreUnavailable(Exception):
    """The reminder store is missing a safe home, unreadable, or tampered with."""


class InvalidReminder(ValueError):
    """A reminder value was refused; nothing was stored."""


@dataclass(frozen=True)
class Reminder:
    key: str
    entity: str
    subject: str
    due_at: datetime  # timezone-aware, wall time with the browser's UTC offset


@dataclass(frozen=True)
class ReviewFlag:
    """A filed note the owner put in "To review". Stored beside reminders,
    never in the note. `comment` is optional (None = no comment)."""

    key: str
    entity: str
    comment: str | None
    flagged_at: datetime  # timezone-aware


@failure_contract(raises=())
def calendar_event_id(key: str) -> str:
    """The id a reminder's Calendar event carries, derived from its card key.
    It names no vault path, entity or title: `oneos-` and 32 lowercase hex."""
    return "oneos-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]


@failure_contract(raises=(InvalidReminder,))
def clean_subject(text: str) -> str:
    if not isinstance(text, str):
        raise InvalidReminder("subject must be text")
    if any(mark in text for mark in _LINE_BREAKS):
        raise InvalidReminder("subject must be a single line")
    cleaned = text.strip()
    if not cleaned:
        raise InvalidReminder("subject is required")
    if len(cleaned) > SUBJECT_MAX:
        raise InvalidReminder(f"subject must be at most {SUBJECT_MAX} characters")
    return cleaned


@failure_contract(raises=(InvalidReminder,))
def clean_flag_comment(text: str) -> str:
    """A review-flag comment: one line, trimmed, 1..500 characters."""
    if not isinstance(text, str):
        raise InvalidReminder("comment must be text")
    for char in text:
        if char in _LINE_BREAKS:
            raise InvalidReminder("comment must be a single line")
        if unicodedata.category(char) == "Cc" and char != "\t":
            raise InvalidReminder("comment contains a control character")
    cleaned = text.strip()
    if not cleaned:
        raise InvalidReminder("comment is empty")
    if len(cleaned) > FLAG_COMMENT_MAX:
        raise InvalidReminder(f"comment must be at most {FLAG_COMMENT_MAX} characters")
    return cleaned


@failure_contract(raises=())
def default_subject(title: str) -> str:
    """A card title as a subject `clean_subject` always accepts: line breaks and
    other whitespace runs become single spaces, trimmed, cut to the limit. Used
    to prefill the form, so an unedited submit can never be refused for it. An
    empty title gives an empty subject, which the required field flags."""
    if not isinstance(title, str):
        return ""
    return " ".join(title.split())[:SUBJECT_MAX].rstrip()


@failure_contract(raises=(InvalidReminder,))
def parse_due(date: str, time: str, offset_minutes: str) -> datetime:
    """Build the due instant from the browser's wall date, wall time and
    its UTC offset in minutes east of UTC (`"120"` is `+02:00`)."""
    if not (isinstance(date, str) and isinstance(time, str) and isinstance(offset_minutes, str)):
        raise InvalidReminder("due date is not valid")
    date_match = re.fullmatch(r"([0-9]{4})-([0-9]{2})-([0-9]{2})", date)
    time_match = re.fullmatch(r"([0-9]{2}):([0-9]{2})", time)
    offset_match = re.fullmatch(r"-?[0-9]{1,4}", offset_minutes)
    if not (date_match and time_match and offset_match):
        raise InvalidReminder("due date is not valid")
    offset = int(offset_minutes)
    if abs(offset) > _MAX_OFFSET_MINUTES:
        raise InvalidReminder("due date is not valid")
    # Bounded so ordering aware datetimes can never raise OverflowError.
    if not _MIN_YEAR <= int(date_match[1]) <= _MAX_YEAR:
        raise InvalidReminder("due date is not valid")
    try:
        return datetime(
            int(date_match[1]), int(date_match[2]), int(date_match[3]),
            int(time_match[1]), int(time_match[2]),
            tzinfo=timezone(timedelta(minutes=offset)),
        )
    except ValueError as exc:
        raise InvalidReminder("due date is not valid") from exc


def _validated(reminder: Reminder) -> Reminder:
    if not isinstance(reminder, Reminder):
        raise InvalidReminder("not a reminder")
    if not (isinstance(reminder.key, str) and reminder.key.strip()
            and isinstance(reminder.entity, str) and reminder.entity.strip()):
        raise InvalidReminder("reminder needs a card and an entity")
    clean_subject(reminder.subject)
    if (not isinstance(reminder.due_at, datetime) or reminder.due_at.tzinfo is None
            or reminder.due_at.utcoffset() is None):
        raise InvalidReminder("due time needs a UTC offset")
    return reminder


def _row_to_reminder(row) -> Reminder:
    key, entity, subject, due_at = row
    if not all(isinstance(value, str) for value in row):
        raise ReminderStoreUnavailable()
    try:
        due = datetime.fromisoformat(due_at)
    except ValueError as exc:
        raise ReminderStoreUnavailable() from exc
    if due.tzinfo is None or due.utcoffset() is None:
        raise ReminderStoreUnavailable()
    return Reminder(key=key, entity=entity, subject=subject, due_at=due)


def _validated_flag(flag: ReviewFlag) -> ReviewFlag:
    if not isinstance(flag, ReviewFlag):
        raise InvalidReminder("not a review flag")
    if not (isinstance(flag.key, str) and flag.key.strip()
            and isinstance(flag.entity, str) and flag.entity.strip()):
        raise InvalidReminder("flag needs a card and an entity")
    if flag.comment is not None:
        clean_flag_comment(flag.comment)
    if (not isinstance(flag.flagged_at, datetime) or flag.flagged_at.tzinfo is None
            or flag.flagged_at.utcoffset() is None):
        raise InvalidReminder("flag time needs a UTC offset")
    return flag


def _row_to_flag(row) -> ReviewFlag:
    key, entity, comment, flagged_at = row
    if not (isinstance(key, str) and isinstance(entity, str) and isinstance(flagged_at, str)
            and (comment is None or isinstance(comment, str))):
        raise ReminderStoreUnavailable()
    try:
        when = datetime.fromisoformat(flagged_at)
    except ValueError as exc:
        raise ReminderStoreUnavailable() from exc
    if when.tzinfo is None or when.utcoffset() is None:
        raise ReminderStoreUnavailable()
    return ReviewFlag(key=key, entity=entity, comment=comment, flagged_at=when)


_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS reminders ("
    "key TEXT PRIMARY KEY, entity TEXT NOT NULL, subject TEXT NOT NULL, "
    "due_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
)
_FLAGS_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS review_flags ("
    "key TEXT PRIMARY KEY, entity TEXT NOT NULL, comment TEXT, "
    "flagged_at TEXT NOT NULL)"
)
_RESOLVED_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS resolved_reminders ("
    "key TEXT PRIMARY KEY, resolved_at TEXT NOT NULL)"
)


def _has_table(db, name: str) -> bool:
    return db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _has_flags_table(db) -> bool:
    return _has_table(db, "review_flags")


def _has_resolved_table(db) -> bool:
    return _has_table(db, "resolved_reminders")


class ReminderStore:
    def __init__(self, directory: Path):
        self.directory = Path(directory).absolute()
        self.path = self.directory / DATABASE_NAME

    def _check(self, *, creating=False):
        """Return the database file identity, or None when it is absent and
        `creating` allows that. Anything unsafe raises."""
        try:
            database_identity = None
            # Reject redirected components, including a redirected state root.
            for part in (self.directory, *self.directory.parents):
                if part.is_symlink():
                    raise ReminderStoreUnavailable()
            info = self.directory.stat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise ReminderStoreUnavailable()
            for path in (self.path, Path(str(self.path) + "-journal"), Path(str(self.path) + "-wal"), Path(str(self.path) + "-shm")):
                try:
                    info = path.lstat()
                except FileNotFoundError:
                    # SQLite may remove optional sidecars at transaction close;
                    # a single no-follow stat avoids racing an exists check.
                    if path == self.path and not creating:
                        raise ReminderStoreUnavailable()
                    continue
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid() or info.st_mode & 0o077:
                    raise ReminderStoreUnavailable()
                if path == self.path:
                    database_identity = (info.st_dev, info.st_ino)
            return database_identity
        except OSError as exc:
            raise ReminderStoreUnavailable() from exc

    @structured_reader(category="admin-db")
    def _publish_new(self):
        """Create the database with its schema, then publish it atomically.

        The file is staged in a private temporary directory beside the final
        path and moved into place without replacing anything, so the live
        path never exists without its table. A crash leaves only staging.
        Losing a race to another first save is fine: the winner's file stays.
        """
        import tempfile

        from .git_transaction import AtomicMoveUnavailable, _move_no_replace

        try:
            with tempfile.TemporaryDirectory(prefix=".reminders-", dir=self.directory) as staging:
                staged = Path(staging) / DATABASE_NAME
                fd = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                try:
                    os.fchmod(fd, 0o600)
                finally:
                    os.close(fd)
                connection = sqlite3.connect(f"{staged.as_uri()}?mode=rw", uri=True, timeout=5)
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    connection.execute(_SCHEMA)
                    connection.commit()
                finally:
                    connection.close()
                source_fd = os.open(staging, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try:
                    target_fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                    try:
                        _move_no_replace(source_fd, staged.name, target_fd, self.path.name)
                    finally:
                        os.close(target_fd)
                finally:
                    os.close(source_fd)
        except FileExistsError:
            return
        except (OSError, sqlite3.Error, AtomicMoveUnavailable) as exc:
            raise ReminderStoreUnavailable() from exc

    @contextmanager
    @structured_reader(category="admin-db")
    def connect(self, *, create=False, readonly=False, flags_table=False, existing=False):
        """Yield an open transaction, or None when the file is absent and
        `create` is false (nothing is created by a read or a no-op write).
        `flags_table` makes a write transaction create `review_flags` too; it
        is set only by the call that stores a flag. `existing` is for the
        clean-up writes (remove, unflag, rekey, rename): they yield None, and
        write nothing at all, for a zero-byte file or one without the
        `reminders` table, instead of creating the schema in it."""
        expected_identity = self._check(creating=True)
        if expected_identity is None:
            if not create:
                yield None
                return
            self._publish_new()
            expected_identity = self._check()
            if expected_identity is None:
                raise ReminderStoreUnavailable()
        try:
            size = self.path.lstat().st_size
        except OSError as exc:
            raise ReminderStoreUnavailable() from exc
        if (readonly or existing) and size == 0:
            # Never published with a schema (left by an interrupted older
            # path): there is nothing in it, so there are no reminders.
            yield None
            return
        mode = "ro" if readonly else "rw"
        try:
            connection = sqlite3.connect(f"{self.path.as_uri()}?mode={mode}", uri=True, timeout=5)
            try:
                if self._check() != expected_identity:
                    raise ReminderStoreUnavailable()
                if not readonly:
                    connection.execute("BEGIN IMMEDIATE")
                    if existing and not _has_table(connection, "reminders"):
                        connection.rollback()
                        yield None
                        return
                    if not existing:
                        connection.execute(_SCHEMA)
                    if flags_table:
                        connection.execute(_FLAGS_SCHEMA)
                yield connection
                if not readonly:
                    connection.commit()
            except BaseException:
                connection.rollback()
                raise
            finally:
                connection.close()
        except (sqlite3.Error, ValueError, TypeError, OverflowError) as exc:
            raise ReminderStoreUnavailable() from exc

    @failure_contract(raises=(ReminderStoreUnavailable,))
    def all(self) -> dict[str, Reminder]:
        with self.connect(readonly=True) as db:
            if db is None:
                return {}
            rows = db.execute("SELECT key, entity, subject, due_at FROM reminders").fetchall()
        return {row[0]: _row_to_reminder(row) for row in rows}

    @failure_contract(raises=())
    def snapshot(self) -> dict[str, Reminder] | None:
        """The open reminders by key, for the Calendar sync: resolved rows are
        left out, and a file with no `resolved_reminders` table has none
        resolved. `None` means there is no basis to delete anything: the file
        is missing or 0 bytes, has no `reminders` table, or cannot be read
        safely. `{}` is a real, empty store. Read-only: it never creates the
        file or a table."""
        try:
            with self.connect(readonly=True) as db:
                if db is None or not _has_table(db, "reminders"):
                    return None
                query = "SELECT key, entity, subject, due_at FROM reminders"
                if _has_resolved_table(db):
                    query += " WHERE key NOT IN (SELECT key FROM resolved_reminders)"
                rows = db.execute(query).fetchall()
            return {row[0]: _row_to_reminder(row) for row in rows}
        except ReminderStoreUnavailable:
            return None

    @failure_contract(raises=(ReminderStoreUnavailable, InvalidReminder))
    def save(self, reminder: Reminder) -> None:
        reminder = _validated(reminder)
        subject = clean_subject(reminder.subject)
        updated = datetime.now(timezone.utc).isoformat()
        with self.connect(create=True) as db:
            db.execute(
                "INSERT INTO reminders (key, entity, subject, due_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(key) DO UPDATE SET "
                "entity=excluded.entity, subject=excluded.subject, "
                "due_at=excluded.due_at, updated_at=excluded.updated_at",
                (reminder.key, reminder.entity, subject, reminder.due_at.isoformat(), updated),
            )
            # Saving a reminder is only offered on a card outside Done, so a
            # mark left behind by a missed clean-up must not hide it.
            if _has_resolved_table(db):
                db.execute("DELETE FROM resolved_reminders WHERE key=?", (reminder.key,))

    @failure_contract(raises=(ReminderStoreUnavailable,))
    def remove(self, key: str) -> None:
        with self.connect(existing=True) as db:
            if db is not None:
                db.execute("DELETE FROM reminders WHERE key=?", (key,))
                if _has_resolved_table(db):
                    db.execute("DELETE FROM resolved_reminders WHERE key=?", (key,))

    @failure_contract(raises=(ReminderStoreUnavailable,))
    def resolve(self, key: str) -> None:
        """Mark the reminder under `key` resolved (kept, but no longer open).
        Idempotent. A key with no reminder, and a file with no `reminders`
        table, write nothing; the mark table is created only when a mark is
        stored."""
        with self.connect(existing=True) as db:
            if db is None:
                return
            if db.execute("SELECT 1 FROM reminders WHERE key=?", (key,)).fetchone() is None:
                return
            db.execute(_RESOLVED_SCHEMA)
            db.execute(
                "INSERT OR IGNORE INTO resolved_reminders (key, resolved_at) VALUES (?, ?)",
                (key, datetime.now(timezone.utc).isoformat()),
            )

    @failure_contract(raises=(ReminderStoreUnavailable,))
    def unresolve(self, key: str) -> None:
        """Clear the resolved mark; idempotent, and never creates anything."""
        with self.connect(existing=True) as db:
            if db is not None and _has_resolved_table(db):
                db.execute("DELETE FROM resolved_reminders WHERE key=?", (key,))

    @failure_contract(raises=(ReminderStoreUnavailable,))
    def rekey(self, old: str, new: str) -> None:
        """Move the reminder, its resolved mark and the review flag stored under
        `old` to `new` (a stale row already under `new` is replaced), in one
        transaction."""
        if old == new:
            return
        with self.connect(existing=True) as db:
            if db is None:
                return
            now = datetime.now(timezone.utc).isoformat()
            if db.execute("SELECT 1 FROM reminders WHERE key=?", (old,)).fetchone() is not None:
                db.execute("DELETE FROM reminders WHERE key=?", (new,))
                db.execute(
                    "UPDATE reminders SET key=?, updated_at=? WHERE key=?",
                    (new, now, old),
                )
                if _has_resolved_table(db):
                    db.execute("DELETE FROM resolved_reminders WHERE key=?", (new,))
                    db.execute("UPDATE resolved_reminders SET key=? WHERE key=?", (new, old))
            if _has_flags_table(db) and db.execute(
                "SELECT 1 FROM review_flags WHERE key=?", (old,)
            ).fetchone() is not None:
                db.execute("DELETE FROM review_flags WHERE key=?", (new,))
                db.execute("UPDATE review_flags SET key=? WHERE key=?", (new, old))

    @failure_contract(raises=(ReminderStoreUnavailable,))
    def flags(self) -> dict[str, ReviewFlag]:
        """Every review flag by key. A file without the table has none; a read
        never creates the file or the table."""
        with self.connect(readonly=True) as db:
            if db is None or not _has_flags_table(db):
                return {}
            rows = db.execute(
                "SELECT key, entity, comment, flagged_at FROM review_flags"
            ).fetchall()
        return {row[0]: _row_to_flag(row) for row in rows}

    @failure_contract(raises=(ReminderStoreUnavailable, InvalidReminder))
    def flag(self, review_flag: ReviewFlag) -> None:
        """Store (or replace) the flag for a card."""
        review_flag = _validated_flag(review_flag)
        comment = None if review_flag.comment is None else clean_flag_comment(review_flag.comment)
        with self.connect(create=True, flags_table=True) as db:
            db.execute(
                "INSERT INTO review_flags (key, entity, comment, flagged_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(key) DO UPDATE SET "
                "entity=excluded.entity, comment=excluded.comment, "
                "flagged_at=excluded.flagged_at",
                (review_flag.key, review_flag.entity, comment,
                 review_flag.flagged_at.isoformat()),
            )

    @failure_contract(raises=(ReminderStoreUnavailable,))
    def unflag(self, key: str) -> None:
        """Drop the flag for a card; idempotent, and never creates anything."""
        with self.connect(existing=True) as db:
            if db is not None and _has_flags_table(db):
                db.execute("DELETE FROM review_flags WHERE key=?", (key,))

    @failure_contract(raises=(ReminderStoreUnavailable,))
    def rename_entity(self, old: str, new: str) -> int:
        """Follow an entity rename: rewrite `entity` and the leading `old/`
        path segment of every key stored for `old`, in one transaction.
        Returns the number of rows changed (reminders and flags; resolved marks
        ride along with their reminder and are not counted). Only the exact first segment moves
        (`older/x.md` is another entity's key and stays). A stale row already
        sitting under a rewritten key is replaced, as in `rekey`. Review flags
        follow the same rule in the same transaction; the count includes them."""
        if old == new:
            return 0
        prefix = old + "/"
        changed = 0
        with self.connect(existing=True) as db:
            if db is None:
                return 0
            now = datetime.now(timezone.utc).isoformat()
            rows = db.execute("SELECT key FROM reminders WHERE entity=?", (old,)).fetchall()
            marked = _has_resolved_table(db)
            for (key,) in rows:
                new_key = new + "/" + key[len(prefix):] if key.startswith(prefix) else key
                if new_key != key:
                    db.execute("DELETE FROM reminders WHERE key=?", (new_key,))
                    if marked:
                        db.execute("DELETE FROM resolved_reminders WHERE key=?", (new_key,))
                db.execute(
                    "UPDATE reminders SET key=?, entity=?, updated_at=? WHERE key=?",
                    (new_key, new, now, key),
                )
                if marked and new_key != key:
                    db.execute("UPDATE resolved_reminders SET key=? WHERE key=?", (new_key, key))
                changed += 1
            if _has_flags_table(db):
                flagged = db.execute(
                    "SELECT key FROM review_flags WHERE entity=?", (old,)
                ).fetchall()
                for (key,) in flagged:
                    new_key = new + "/" + key[len(prefix):] if key.startswith(prefix) else key
                    if new_key != key:
                        db.execute("DELETE FROM review_flags WHERE key=?", (new_key,))
                    db.execute(
                        "UPDATE review_flags SET key=?, entity=? WHERE key=?",
                        (new_key, new, key),
                    )
                    changed += 1
        return changed


@failure_contract(raises=())
def reminder_store() -> ReminderStore | None:
    raw = os.getenv("ONEOS_AUTH_STATE_DIR")
    return ReminderStore(Path(raw)) if raw else None
