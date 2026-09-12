"""Single-owner authentication state. Never stores bearer tokens in plaintext."""
from __future__ import annotations

import hashlib
import os
import re
import secrets
import sqlite3
import stat
import time
from contextlib import contextmanager
from pathlib import Path

import pyotp
from argon2 import PasswordHasher, Type, extract_parameters
from argon2.exceptions import VerificationError, InvalidHashError
from .console_routing import structured_reader

HASHER = PasswordHasher()
IDLE_SECONDS = 1800
ABSOLUTE_SECONDS = 43200


class AuthUnavailable(Exception):
    """Private state is missing or unsafe; callers must fail closed."""


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class AuthStore:
    def __init__(self, directory: Path):
        self.directory = directory.absolute()
        self.path = self.directory / "owner.sqlite3"

    def _check(self, *, creating=False):
        try:
            # Reject redirected components, including a redirected state root.
            for part in (self.directory, *self.directory.parents):
                if part.is_symlink():
                    raise AuthUnavailable()
            info = self.directory.stat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise AuthUnavailable()
            for path in (self.path, Path(str(self.path) + "-journal"), Path(str(self.path) + "-wal"), Path(str(self.path) + "-shm")):
                if not path.exists() and not path.is_symlink():
                    if path == self.path and not creating:
                        raise AuthUnavailable()
                    continue
                info = path.lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid() or info.st_mode & 0o077:
                    raise AuthUnavailable()
        except OSError as exc:
            raise AuthUnavailable() from exc

    @contextmanager
    @structured_reader(category="admin-db")
    def connect(self):
        self._check()
        try:
            connection = sqlite3.connect(f"file:{self.path}?mode=rw", uri=True, timeout=5)
            try:
                connection.execute("BEGIN IMMEDIATE")
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
            finally:
                connection.close()
        except (sqlite3.Error, ValueError, TypeError, OverflowError) as exc:
            raise AuthUnavailable() from exc

    def available(self):
        with self.connect() as db:
            row = db.execute("SELECT password, secret, counter FROM owner WHERE id=1").fetchone()
            if not row or not isinstance(row[0], str) or not row[0].startswith("$argon2id$") or not isinstance(row[1], str) or not re.fullmatch(r"[A-Z2-7]{32}", row[1]) or not isinstance(row[2], int):
                raise AuthUnavailable()
            try:
                parameters = extract_parameters(row[0])
            except InvalidHashError as exc:
                raise AuthUnavailable() from exc
            if parameters.type != Type.ID or not 1 <= parameters.time_cost <= 10 or not 8 <= parameters.memory_cost <= 262144 or not 1 <= parameters.parallelism <= 16 or not 16 <= parameters.hash_len <= 128 or not 16 <= parameters.salt_len <= 128:
                raise AuthUnavailable()
            throttle = db.execute("SELECT failures, blocked FROM throttle WHERE id=1").fetchone()
            if not throttle or not isinstance(throttle[0], int) or throttle[0] < 0 or not isinstance(throttle[1], (int, float)):
                raise AuthUnavailable()
            db.execute("SELECT token,csrf,created,seen FROM sessions LIMIT 1")
            if db.execute("PRAGMA quick_check").fetchone() != ("ok",):
                raise AuthUnavailable()

    def enroll(self, password, secret, code, *, now=None, recover=False):
        now = time.time() if now is None else now
        if not 14 <= len(password) <= 1024 or not re.fullmatch(r"[A-Z2-7]{32}", secret) or not pyotp.TOTP(secret).verify(code, for_time=now):
            raise ValueError("Use a password of 14–1024 characters and a current authenticator code.")
        password_hash = HASHER.hash(password)
        self._check(creating=not recover)
        if not recover:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            os.close(fd)
        with self.connect() as db:
            if not recover:
                db.executescript("""
                    CREATE TABLE owner(id INTEGER PRIMARY KEY CHECK(id=1), password TEXT NOT NULL, secret TEXT NOT NULL, counter INTEGER NOT NULL);
                    CREATE TABLE sessions(token TEXT PRIMARY KEY, csrf TEXT NOT NULL, created REAL NOT NULL, seen REAL NOT NULL);
                    CREATE TABLE throttle(id INTEGER PRIMARY KEY CHECK(id=1), failures INTEGER NOT NULL, blocked REAL NOT NULL);
                    INSERT INTO throttle VALUES(1,0,0);
                """)
            db.execute("INSERT OR REPLACE INTO owner VALUES(1,?,?,?)", (password_hash, secret, int(now // 30)))
            db.execute("DELETE FROM sessions")
            db.execute("UPDATE throttle SET failures=0, blocked=0 WHERE id=1")

    def login(self, password, code, *, now=None):
        now = time.time() if now is None else now
        if not isinstance(password, str) or len(password) > 1024 or not re.fullmatch(r"[0-9]{6}", code):
            return None
        with self.connect() as db:
            failures, blocked = db.execute("SELECT failures,blocked FROM throttle WHERE id=1").fetchone()
            if now < blocked:
                return None
            row = db.execute("SELECT password,secret,counter FROM owner WHERE id=1").fetchone()
            if row is None:
                raise AuthUnavailable()
            try:
                valid = HASHER.verify(row[0], password)
            except (VerificationError, InvalidHashError):
                valid = False
            counter = int(now // 30)
            if not valid or counter <= row[2] or not pyotp.TOTP(row[1]).verify(code, for_time=now):
                failures += 1
                db.execute("UPDATE throttle SET failures=?,blocked=? WHERE id=1", (failures, now + min(900, 30 * 2 ** min(failures - 5, 5)) if failures >= 5 else 0))
                return None
            # Password verification and counter advance share the write transaction.
            db.execute("UPDATE owner SET counter=? WHERE id=1", (counter,))
            if HASHER.check_needs_rehash(row[0]):
                db.execute("UPDATE owner SET password=? WHERE id=1", (HASHER.hash(password),))
            db.execute("UPDATE throttle SET failures=0,blocked=0 WHERE id=1")
            db.execute("DELETE FROM sessions WHERE seen<=? OR created<=?", (now-IDLE_SECONDS, now-ABSOLUTE_SECONDS))
            token = secrets.token_urlsafe(32)
            db.execute("INSERT INTO sessions VALUES(?,?,?,?)", (digest(token), secrets.token_urlsafe(32), now, now))
            return token

    def session(self, token, *, now=None):
        now = time.time() if now is None else now
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
            return None
        with self.connect() as db:
            row = db.execute("SELECT csrf,created,seen FROM sessions WHERE token=?", (digest(token),)).fetchone()
            if not row:
                return None
            if now-row[1] >= ABSOLUTE_SECONDS or now-row[2] >= IDLE_SECONDS or now < row[2]:
                db.execute("DELETE FROM sessions WHERE token=?", (digest(token),))
                return None
            db.execute("UPDATE sessions SET seen=? WHERE token=?", (now, digest(token)))
            return row[0]

    def logout(self, token):
        with self.connect() as db:
            db.execute("DELETE FROM sessions WHERE token=?", (digest(token),))
