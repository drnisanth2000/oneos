"""Reminders: a private store beside the auth database.

Reminders are owner-private state, not vault content. They live in
`<ONEOS_AUTH_STATE_DIR>/reminders.sqlite3` so the owner-run backup already
copies them, and saving or removing one never touches the vault or Git.

The safety rules mirror `app.auth.AuthStore`: no symlinked component, a
directory owned by this process with no group/other bits, a regular
single-link file with mode 0600. Reading never creates anything; the file
appears on the first save.
"""
from __future__ import annotations

import os
import re
import sqlite3
import stat
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .console_routing import failure_contract, structured_reader

DATABASE_NAME = "reminders.sqlite3"
SUBJECT_MAX = 120
_LINE_BREAKS = ("\r", "\n", "\x0b", "\x0c", "\x85", " ", " ")
_MAX_OFFSET_MINUTES = 14 * 60


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


_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS reminders ("
    "key TEXT PRIMARY KEY, entity TEXT NOT NULL, subject TEXT NOT NULL, "
    "due_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
)


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

    def _create_file(self):
        """Create the database file owner-only, never following a link."""
        try:
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            return
        except OSError as exc:
            raise ReminderStoreUnavailable() from exc
        try:
            os.fchmod(fd, 0o600)
        except OSError as exc:
            raise ReminderStoreUnavailable() from exc
        finally:
            os.close(fd)

    @contextmanager
    @structured_reader(category="admin-db")
    def connect(self, *, create=False, readonly=False):
        """Yield an open transaction, or None when the file is absent and
        `create` is false (nothing is created by a read or a no-op write)."""
        expected_identity = self._check(creating=True)
        if expected_identity is None:
            if not create:
                yield None
                return
            self._create_file()
            expected_identity = self._check()
        mode = "ro" if readonly else "rw"
        try:
            connection = sqlite3.connect(f"{self.path.as_uri()}?mode={mode}", uri=True, timeout=5)
            try:
                if self._check() != expected_identity:
                    raise ReminderStoreUnavailable()
                if not readonly:
                    connection.execute("BEGIN IMMEDIATE")
                    connection.execute(_SCHEMA)
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

    @failure_contract(raises=(ReminderStoreUnavailable,))
    def remove(self, key: str) -> None:
        with self.connect() as db:
            if db is not None:
                db.execute("DELETE FROM reminders WHERE key=?", (key,))

    @failure_contract(raises=(ReminderStoreUnavailable,))
    def rekey(self, old: str, new: str) -> None:
        if old == new:
            return
        with self.connect() as db:
            if db is None:
                return
            if db.execute("SELECT 1 FROM reminders WHERE key=?", (old,)).fetchone() is None:
                return
            db.execute("DELETE FROM reminders WHERE key=?", (new,))
            db.execute(
                "UPDATE reminders SET key=?, updated_at=? WHERE key=?",
                (new, datetime.now(timezone.utc).isoformat(), old),
            )


@failure_contract(raises=())
def reminder_store() -> ReminderStore | None:
    raw = os.getenv("ONEOS_AUTH_STATE_DIR")
    return ReminderStore(Path(raw)) if raw else None
