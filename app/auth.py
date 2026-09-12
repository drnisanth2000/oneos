"""Single-owner authentication state. Never stores bearer tokens in plaintext."""
from __future__ import annotations

import hashlib
import math
import os
import re
import secrets
import sqlite3
import stat
import tempfile
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


def _validate_session(row):
    token, csrf, created, seen = row
    if (not isinstance(token, str) or not re.fullmatch(r"[0-9a-f]{64}", token)
            or not isinstance(csrf, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", csrf)
            or any(type(value) not in (int, float) or not math.isfinite(value) or value < 0
                   for value in (created, seen))
            or seen < created):
        raise AuthUnavailable()


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
                try:
                    info = path.lstat()
                except FileNotFoundError:
                    # SQLite may remove optional sidecars at transaction close.
                    # A single no-follow stat avoids racing a separate exists
                    # check; all other errors and unsafe file types still fail.
                    if path == self.path and not creating:
                        raise AuthUnavailable()
                    continue
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid() or info.st_mode & 0o077:
                    raise AuthUnavailable()
        except OSError as exc:
            raise AuthUnavailable() from exc

    @contextmanager
    @structured_reader(category="admin-db")
    def connect(self):
        self._check()
        try:
            connection = sqlite3.connect(f"{self.path.as_uri()}?mode=rw", uri=True, timeout=5)
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
            if (not throttle or not isinstance(throttle[0], int) or throttle[0] < 0
                    or not isinstance(throttle[1], (int, float))
                    or not math.isfinite(throttle[1]) or throttle[1] < 0):
                raise AuthUnavailable()
            for session in db.execute("SELECT token,csrf,created,seen FROM sessions"):
                _validate_session(session)
            if db.execute("PRAGMA quick_check").fetchone() != ("ok",):
                raise AuthUnavailable()

    def enroll(self, password, secret, code, *, now=None, recover=False):
        now = time.time() if now is None else now
        if not 14 <= len(password) <= 1024 or not re.fullmatch(r"[A-Z2-7]{32}", secret) or not pyotp.TOTP(secret).verify(code, for_time=now):
            raise ValueError("Use a password of 14–1024 characters and a current authenticator code.")
        password_hash = HASHER.hash(password)
        self._check(creating=not recover)
        if recover:
            self._save_owner(password_hash, secret, now, creating=False)
            return
        if self.path.exists():
            raise FileExistsError("owner already enrolled")
        # A crash before publication leaves only private staging, never an
        # incomplete live owner DB. Exclusive publication also arbitrates
        # concurrent enrollments without replacing the winning credentials.
        with tempfile.TemporaryDirectory(prefix=".enroll-", dir=self.directory) as staging:
            staged = AuthStore(Path(staging))
            fd = os.open(staged.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            os.close(fd)
            staged._save_owner(password_hash, secret, now, creating=True)
            from .git_transaction import _move_no_replace
            source_fd = os.open(staging, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                target_fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try:
                    _move_no_replace(source_fd, staged.path.name, target_fd, self.path.name)
                finally:
                    os.close(target_fd)
            finally:
                os.close(source_fd)

    def _save_owner(self, password_hash, secret, now, *, creating):
        with self.connect() as db:
            if creating:
                # executescript implicitly commits a pending transaction.
                # Individual statements keep schema and owner atomic together.
                db.execute("CREATE TABLE owner(id INTEGER PRIMARY KEY CHECK(id=1), password TEXT NOT NULL, secret TEXT NOT NULL, counter INTEGER NOT NULL)")
                db.execute("CREATE TABLE sessions(token TEXT PRIMARY KEY, csrf TEXT NOT NULL, created REAL NOT NULL, seen REAL NOT NULL)")
                db.execute("CREATE TABLE throttle(id INTEGER PRIMARY KEY CHECK(id=1), failures INTEGER NOT NULL, blocked REAL NOT NULL)")
                db.execute("INSERT INTO throttle VALUES(1,0,0)")
            db.execute("INSERT OR REPLACE INTO owner VALUES(1,?,?,?)", (password_hash, secret, int(now // 30)))
            db.execute("DELETE FROM sessions")
            db.execute("INSERT INTO throttle VALUES(1,0,0) ON CONFLICT(id) DO UPDATE SET failures=0, blocked=0")

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
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
            return None
        with self.connect() as db:
            # Order production timestamps by the write transaction, not by
            # concurrent requests reaching this method.
            now = time.time() if now is None else now
            row = db.execute("SELECT token,csrf,created,seen FROM sessions WHERE token=?", (digest(token),)).fetchone()
            if not row:
                return None
            _validate_session(row)
            if now-row[2] >= ABSOLUTE_SECONDS or now-row[3] >= IDLE_SECONDS or now < row[3]:
                db.execute("DELETE FROM sessions WHERE token=?", (digest(token),))
                return None
            db.execute("UPDATE sessions SET seen=? WHERE token=?", (now, digest(token)))
            return row[1]

    def logout(self, token):
        with self.connect() as db:
            db.execute("DELETE FROM sessions WHERE token=?", (digest(token),))
