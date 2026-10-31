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
from dataclasses import dataclass
from pathlib import Path

import pyotp
from argon2 import PasswordHasher, Type, extract_parameters
from argon2.exceptions import VerificationError, InvalidHashError
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from . import passkeys as webauthn_checks
from .console_routing import structured_reader
from .passkeys import PasskeyRejected

HASHER = PasswordHasher()
IDLE_SECONDS = 1800
ABSOLUTE_SECONDS = 43200
MAX_BLOCK_SECONDS = 900
CHALLENGE_SECONDS = 300
# Pending challenges kept per purpose. Sign-in options need no session, so
# this bounds what an unauthenticated caller can make the store hold.
MAX_CHALLENGES = 16


@dataclass(frozen=True)
class SessionInfo:
    csrf: str
    kept: bool


class AuthUnavailable(Exception):
    """Private state is missing or unsafe; callers must fail closed."""


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _has_table(db, table):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def _ensure_schema(db):
    """Bring an older database up to date. Call only inside a write transaction.

    Only new tables are added; `sessions` keeps exactly its original four
    columns, so the previous release (which inserts sessions positionally)
    still works on an upgraded database if it is rolled back to.
    """
    db.execute("CREATE TABLE IF NOT EXISTS kept_sessions(token TEXT PRIMARY KEY)")
    db.execute("CREATE TABLE IF NOT EXISTS passkeys(id TEXT PRIMARY KEY, public_key BLOB NOT NULL, sign_count INTEGER NOT NULL, created REAL NOT NULL, owner_binding TEXT NOT NULL)")
    db.execute("CREATE TABLE IF NOT EXISTS passkey_challenges(digest TEXT PRIMARY KEY, purpose TEXT NOT NULL, expires REAL NOT NULL)")


def _is_kept(db, token_digest):
    """Whether the session with this token digest is kept: its digest has a marker.

    The caller has already found the session row, so a marker alone (an
    orphan, left when the previous release deleted its session) never makes
    anything kept. An older database without the table has no kept sessions.
    Never writes.
    """
    return _has_table(db, "kept_sessions") and db.execute(
        "SELECT 1 FROM kept_sessions WHERE token=?", (token_digest,)).fetchone() is not None


def _end_sessions(db, token_digest=None):
    """Delete one session (by token digest), or every session when None, with its kept marker.

    Runs inside the caller's write transaction, so a session and its marker
    always go together. Markers are matched by digest (or all removed), so an
    orphaned marker for the same token goes too. An older database without
    the marker table is left without one (no write beyond the delete).
    """
    markers = _has_table(db, "kept_sessions")
    if token_digest is None:
        if markers:
            db.execute("DELETE FROM kept_sessions")
        db.execute("DELETE FROM sessions")
    else:
        if markers:
            db.execute("DELETE FROM kept_sessions WHERE token=?", (token_digest,))
        db.execute("DELETE FROM sessions WHERE token=?", (token_digest,))


PASSKEY_COLUMNS = "id,public_key,sign_count,created,owner_binding"


def _validate_passkey(row):
    key_id, public_key, sign_count, created, owner_binding = row
    if (not isinstance(key_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,1024}", key_id)
            or type(public_key) is not bytes or not public_key
            or type(sign_count) is not int or sign_count < 0
            or type(created) not in (int, float) or not math.isfinite(created) or created < 0
            or not isinstance(owner_binding, str) or not re.fullmatch(r"[0-9a-f]{64}", owner_binding)):
        raise AuthUnavailable()


def _owner_binding(db):
    """SHA-256 (lowercase hex) of the owner's current authenticator secret.

    Every passkey row stores the binding current when it was set up. Every
    recovery (this release's or an older one's, which knows nothing of
    passkeys) writes a new random secret, so it revokes every passkey.
    """
    row = db.execute("SELECT secret FROM owner WHERE id=1").fetchone()
    if row is None or not isinstance(row[0], str):
        raise AuthUnavailable()
    return hashlib.sha256(row[0].encode()).hexdigest()


def _relying_party(origin):
    """(RP ID, expected origin) for the configured origin; a bad one is unsafe state."""
    try:
        return webauthn_checks.rp_from_origin(origin)
    except PasskeyRejected as exc:
        raise AuthUnavailable() from exc


def _well_formed(password, code):
    return isinstance(password, str) and len(password) <= 1024 and isinstance(code, str) and re.fullmatch(r"[0-9]{6}", code) is not None


def _throttle(db):
    row = db.execute("SELECT failures,blocked FROM throttle WHERE id=1").fetchone()
    if row is None:
        raise AuthUnavailable()
    return row


def _record_failure(db, failures, now):
    """Count one failed sign-in (password, setup check or passkey); five or more block."""
    failures += 1
    db.execute("UPDATE throttle SET failures=?,blocked=? WHERE id=1", (failures, now + min(MAX_BLOCK_SECONDS, 30 * 2 ** min(failures - 5, 5)) if failures >= 5 else 0))


def _purge_challenges(db, now):
    """Delete expired challenge rows and any row that is not a well-formed live challenge.

    Rows are untrusted: a wrong type, an unknown purpose, or an expiry further
    away than a challenge ever lives (clock stepped back, or tampering) is
    removed rather than trusted. Call only inside a write transaction, after
    `_ensure_schema`.
    """
    db.execute(
        "DELETE FROM passkey_challenges WHERE NOT (typeof(digest)='text' AND purpose IN ('register','login')"
        " AND typeof(expires) IN ('integer','real') AND expires>? AND expires<=?)",
        (now, now + CHALLENGE_SECONDS))


def _new_challenge(db, purpose, now):
    """A fresh 32-byte challenge, stored only as its SHA-256 digest, purpose and expiry."""
    db.execute(
        "DELETE FROM passkey_challenges WHERE purpose=? AND rowid NOT IN"
        " (SELECT rowid FROM passkey_challenges WHERE purpose=? ORDER BY rowid DESC LIMIT ?)",
        (purpose, purpose, MAX_CHALLENGES - 1))
    challenge = secrets.token_bytes(webauthn_checks.CHALLENGE_BYTES)
    db.execute("INSERT INTO passkey_challenges(digest,purpose,expires) VALUES(?,?,?)",
               (hashlib.sha256(challenge).hexdigest(), purpose, now + CHALLENGE_SECONDS))
    return challenge


def _take_challenge(db, response_json, purpose, now):
    """Consume the live challenge the answer claims to answer; None if there is none.

    The answer's `clientDataJSON.challenge` is hashed and looked up by digest.
    A row that is found is deleted before anything else is checked, so a
    challenge is used at most once whatever the outcome. The returned bytes are
    the stored challenge (its digest matched); `webauthn` then checks that the
    signed client data carries exactly these bytes.
    """
    try:
        challenge = webauthn_checks.response_challenge(response_json)
    except PasskeyRejected:
        return None
    key = hashlib.sha256(challenge).hexdigest()
    row = db.execute("SELECT purpose,expires FROM passkey_challenges WHERE digest=?", (key,)).fetchone()
    if row is None:
        return None
    db.execute("DELETE FROM passkey_challenges WHERE digest=?", (key,))
    stored_purpose, expires = row
    if (stored_purpose != purpose or type(expires) not in (int, float) or not math.isfinite(expires)
            or not now < expires <= now + CHALLENGE_SECONDS):
        return None
    return challenge


def _stored_passkeys(db, *, prune=False):
    """Validated passkey rows bound to the current owner secret; [] without a passkey table.

    Every row is validated first (a malformed one is unsafe state). A row
    bound to another secret was revoked by a recovery: it is never returned.
    Reads leave it in place (they never write); with `prune` (only inside a
    write transaction) it is deleted.
    """
    if not _has_table(db, "passkeys"):
        return []
    binding = _owner_binding(db)
    rows = db.execute(f"SELECT {PASSKEY_COLUMNS} FROM passkeys ORDER BY created,id").fetchall()
    for row in rows:
        _validate_passkey(row)
    if prune:
        db.execute("DELETE FROM passkeys WHERE owner_binding!=?", (binding,))
    return [row for row in rows if row[4] == binding]


def _validate_session(row):
    token, csrf, created, seen = row
    if (not isinstance(token, str) or not re.fullmatch(r"[0-9a-f]{64}", token)
            or not isinstance(csrf, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", csrf)
            or any(type(value) not in (int, float) or not math.isfinite(value) or value < 0
                   for value in (created, seen))
            or seen < created):
        raise AuthUnavailable()


def _validate_kept_marker(row):
    """A kept marker must be a well-formed token digest; anything else is unsafe state.

    A well-formed marker with no session (an orphan) is tolerated: the
    previous release deletes sessions without knowing about markers, and
    failing closed on that would turn a rollback into a lockout. Orphans are
    harmless because a session is kept only when its own row exists and its
    digest has a marker (see `_is_kept`); the next sign-in removes them.
    """
    (token,) = row
    if not isinstance(token, str) or not re.fullmatch(r"[0-9a-f]{64}", token):
        raise AuthUnavailable()


class AuthStore:
    def __init__(self, directory: Path):
        self.directory = directory.absolute()
        self.path = self.directory / "owner.sqlite3"

    def _check(self, *, creating=False):
        try:
            database_identity = None
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
                if path == self.path:
                    database_identity = (info.st_dev, info.st_ino)
            return database_identity
        except OSError as exc:
            raise AuthUnavailable() from exc

    @contextmanager
    @structured_reader(category="admin-db")
    def connect(self):
        expected_identity = self._check()
        try:
            connection = sqlite3.connect(f"{self.path.as_uri()}?mode=rw", uri=True, timeout=5)
            try:
                if self._check() != expected_identity:
                    raise AuthUnavailable()
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
            current_counter = int(time.time() // 30)
            if (not row or not isinstance(row[0], str) or not row[0].startswith("$argon2id$")
                    or not isinstance(row[1], str) or not re.fullmatch(r"[A-Z2-7]{32}", row[1])
                    or type(row[2]) is not int or not 0 <= row[2] <= current_counter):
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
                    or not math.isfinite(throttle[1]) or throttle[1] < 0
                    or throttle[1] > time.time() + MAX_BLOCK_SECONDS):
                raise AuthUnavailable()
            for session in db.execute("SELECT token,csrf,created,seen FROM sessions").fetchall():
                _validate_session(session)
            if _has_table(db, "kept_sessions"):
                for marker in db.execute("SELECT token FROM kept_sessions").fetchall():
                    _validate_kept_marker(marker)
            if _has_table(db, "passkeys"):
                for passkey in db.execute(f"SELECT {PASSKEY_COLUMNS} FROM passkeys").fetchall():
                    _validate_passkey(passkey)
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
            _ensure_schema(db)
            for table in ("sessions", "kept_sessions", "passkeys", "passkey_challenges"):
                db.execute(f"DELETE FROM {table}")
            db.execute("INSERT INTO throttle VALUES(1,0,0) ON CONFLICT(id) DO UPDATE SET failures=0, blocked=0")

    def _create_session(self, db, *, now, keep):
        """The one place a session is made (password and passkey sign-in both call it).

        Runs inside the caller's write transaction. Kept sessions have no idle or
        absolute limit, so the expiry purge never touches them. A kept session
        is a `sessions` row plus a marker in `kept_sessions` with the same
        digest; orphaned markers (whose session is gone) are cleared here.
        """
        _ensure_schema(db)
        # NOT EXISTS, not NOT IN: a NULL in the other table must not stop either sweep.
        db.execute("DELETE FROM sessions WHERE NOT EXISTS (SELECT 1 FROM kept_sessions WHERE kept_sessions.token=sessions.token)"
                   " AND (seen<=? OR created<=?)", (now-IDLE_SECONDS, now-ABSOLUTE_SECONDS))
        db.execute("DELETE FROM kept_sessions WHERE NOT EXISTS (SELECT 1 FROM sessions WHERE sessions.token=kept_sessions.token)")
        token = secrets.token_urlsafe(32)
        db.execute("INSERT INTO sessions(token,csrf,created,seen) VALUES(?,?,?,?)", (digest(token), secrets.token_urlsafe(32), now, now))
        if keep:
            db.execute("INSERT INTO kept_sessions(token) VALUES(?)", (digest(token),))
        return token

    def _check_owner(self, db, password, code, now):
        """The password + code check shared by sign-in and passkey setup.

        Runs inside the caller's write transaction. While the throttle blocks it
        refuses without counting. A wrong password or code, or a code from a
        30-second window already used, counts one failure. On success the code
        window is spent (so the same code cannot be used twice) and the throttle
        is reset.
        """
        failures, blocked = _throttle(db)
        if now < blocked:
            return False
        row = db.execute("SELECT password,secret,counter FROM owner WHERE id=1").fetchone()
        if row is None:
            raise AuthUnavailable()
        try:
            valid = HASHER.verify(row[0], password)
        except (VerificationError, InvalidHashError):
            valid = False
        counter = int(now // 30)
        if not valid or counter <= row[2] or not pyotp.TOTP(row[1]).verify(code, for_time=now):
            _record_failure(db, failures, now)
            return False
        # Password verification and counter advance share the write transaction.
        db.execute("UPDATE owner SET counter=? WHERE id=1", (counter,))
        if HASHER.check_needs_rehash(row[0]):
            db.execute("UPDATE owner SET password=? WHERE id=1", (HASHER.hash(password),))
        db.execute("UPDATE throttle SET failures=0,blocked=0 WHERE id=1")
        return True

    def blocked(self, *, now=None):
        """Whether the sign-in throttle refuses checks right now. Never writes."""
        with self.connect() as db:
            now = time.time() if now is None else now
            return now < _throttle(db)[1]

    def login(self, password, code, *, keep=False, now=None):
        now = time.time() if now is None else now
        if not _well_formed(password, code):
            return None
        with self.connect() as db:
            if not self._check_owner(db, password, code, now):
                return None
            return self._create_session(db, now=now, keep=keep)

    def begin_registration(self, password, code, *, origin, now=None):
        """Options JSON to set up a passkey, after a fresh password + code check; else None.

        The check is exactly sign-in's (throttle, one use per code window) but
        makes no session. Nothing is stored unless it passes.
        """
        if not _well_formed(password, code):
            return None
        rp_id, _ = _relying_party(origin)
        with self.connect() as db:
            now = time.time() if now is None else now
            if not self._check_owner(db, password, code, now):
                return None
            _ensure_schema(db)
            _purge_challenges(db, now)
            exclude = [base64url_to_bytes(row[0]) for row in _stored_passkeys(db, prune=True)]
            return webauthn_checks.registration_options(rp_id, _new_challenge(db, "register", now), exclude)

    def finish_registration(self, response_json, *, origin, now=None):
        """Store the passkey in a verified answer to a live setup challenge. Never raises for bad input.

        A failure here does not count toward the throttle: it needs a challenge
        that only a passed password + code check creates, and the setup
        endpoint is behind a signed-in session, so counting would only let a
        misbehaving browser lock the owner out. The challenge is spent either way.
        """
        rp_id, expected_origin = _relying_party(origin)
        with self.connect() as db:
            now = time.time() if now is None else now
            _ensure_schema(db)
            _purge_challenges(db, now)
            _stored_passkeys(db, prune=True)
            _, blocked = _throttle(db)
            if now < blocked:
                return False
            challenge = _take_challenge(db, response_json, "register", now)
            if challenge is None:
                return False
            try:
                credential_id, public_key, sign_count = webauthn_checks.verify_registration(response_json, challenge, rp_id, expected_origin)
            except PasskeyRejected:
                return False
            row = (bytes_to_base64url(credential_id), public_key, sign_count, now, _owner_binding(db))
            try:
                # Never store a row that available() would reject: that would
                # fail every request closed and lock the owner out.
                _validate_passkey(row)
            except AuthUnavailable:
                return False
            if db.execute("SELECT 1 FROM passkeys WHERE id=?", (row[0],)).fetchone():
                return False
            db.execute(f"INSERT INTO passkeys({PASSKEY_COLUMNS}) VALUES(?,?,?,?,?)", row)
            return True

    def begin_login(self, *, origin, now=None):
        """Options JSON for a passkey sign-in; None if no passkey is stored or the throttle blocks."""
        rp_id, _ = _relying_party(origin)
        with self.connect() as db:
            now = time.time() if now is None else now
            # An older database without passkeys is left untouched. Revoked
            # passkeys are removed before deciding there is one to offer.
            if not _has_table(db, "passkeys") or not _stored_passkeys(db, prune=True):
                return None
            _, blocked = _throttle(db)
            if now < blocked:
                return None
            _ensure_schema(db)
            _purge_challenges(db, now)
            return webauthn_checks.authentication_options(rp_id, _new_challenge(db, "login", now))

    def finish_login(self, response_json, *, origin, keep, now=None):
        """A session token for a verified passkey sign-in; None otherwise.

        Every refusal counts toward the throttle like a failed password; while
        it blocks, refuses without counting (as `login` does). Success resets it.
        """
        rp_id, expected_origin = _relying_party(origin)
        with self.connect() as db:
            now = time.time() if now is None else now
            _ensure_schema(db)
            _purge_challenges(db, now)
            # Revoked passkeys go in this transaction, which commits even on refusal.
            _stored_passkeys(db, prune=True)
            failures, blocked = _throttle(db)
            if now < blocked:
                return None
            token = self._passkey_sign_in(db, response_json, rp_id, expected_origin, keep, now)
            if token is None:
                # Returning (not raising) commits the count with the spent challenge.
                _record_failure(db, failures, now)
            return token

    def _passkey_sign_in(self, db, response_json, rp_id, expected_origin, keep, now):
        challenge = _take_challenge(db, response_json, "login", now)
        if challenge is None:
            return None
        try:
            credential_id = webauthn_checks.response_credential_id(response_json)
        except PasskeyRejected:
            return None
        row = db.execute(f"SELECT {PASSKEY_COLUMNS} FROM passkeys WHERE id=?", (credential_id,)).fetchone()
        if row is None:
            return None
        _validate_passkey(row)
        if row[4] != _owner_binding(db):
            return None  # revoked by a recovery (pruned above; checked again here)
        try:
            sign_count = webauthn_checks.verify_authentication(response_json, challenge, rp_id, expected_origin, row[1], row[2])
        except PasskeyRejected:
            return None
        db.execute("UPDATE passkeys SET sign_count=? WHERE id=?", (sign_count, credential_id))
        db.execute("UPDATE throttle SET failures=0,blocked=0 WHERE id=1")
        return self._create_session(db, now=now, keep=keep)

    def passkeys(self):
        """(id, created) of each passkey bound to the current owner secret, oldest first. Never writes."""
        with self.connect() as db:
            return [(row[0], row[3]) for row in _stored_passkeys(db)]

    def remove_passkey(self, credential_id):
        """Delete one passkey; False if there was none. Open sessions are not ended."""
        if not isinstance(credential_id, str):
            return False
        with self.connect() as db:
            if not _has_table(db, "passkeys"):
                return False
            return db.execute("DELETE FROM passkeys WHERE id=?", (credential_id,)).rowcount > 0

    def session_info(self, token, *, now=None):
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
            kept = _is_kept(db, row[0])
            if not kept and (now-row[2] >= ABSOLUTE_SECONDS or now-row[3] >= IDLE_SECONDS or now < row[3]):
                _end_sessions(db, digest(token))
                return None
            # A kept session has no clock limits. If the clock stepped back, keep
            # `seen` where it was so seen >= created still holds.
            db.execute("UPDATE sessions SET seen=? WHERE token=?", (max(now, row[3]) if kept else now, digest(token)))
            return SessionInfo(row[1], kept)

    def session(self, token, *, now=None):
        info = self.session_info(token, now=now)
        return info.csrf if info else None

    def logout(self, token):
        with self.connect() as db:
            _end_sessions(db, digest(token))

    def logout_everywhere(self):
        with self.connect() as db:
            _end_sessions(db)
