"""Exclusive external evidence for lifecycle actions; no approval authority.

Call creation and checkpoints under the shared action lock. Keep ``anchor``
independently. Historical action classification belongs to the Gate 3 caller.
A pending final ``before`` is readable evidence, never a complete action proof.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import uuid


class JournalError(OSError):
    """Evidence could not be captured or its integrity could not be proved."""


_SHA = re.compile(r'[0-9a-f]{64}\Z')
_OID = re.compile(r'(?:[0-9a-f]{40}|[0-9a-f]{64})\Z')
_ID = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}\Z')
_FIELDS = {'version', 'session_id', 'sequence', 'previous_digest', 'phase',
           'proposal_id', 'head', 'commit_oid', 'snapshot', 'index_entries',
           'index_tree'}
_REPO = Path(__file__).resolve().parents[1]


def _canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True) + '\n').encode()


def _digest(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _absolute(path):
    path = Path(os.path.abspath(os.fspath(path)))
    return path


@contextmanager
def _directory(path):
    """Walk every component without following a symlink, retaining the leaf."""
    descriptor = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in _absolute(path).parts[1:]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        yield descriptor
    finally:
        os.close(descriptor)


def _git(vault, *args):
    env = os.environ.copy()
    for key in ('GIT_INDEX_FILE', 'GIT_DIR', 'GIT_WORK_TREE', 'GIT_COMMON_DIR'):
        env.pop(key, None)
    env['GIT_OPTIONAL_LOCKS'] = '0'
    result = subprocess.run(['git', '-C', os.fspath(vault), *args], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        raise JournalError('Git evidence capture failed')
    return result.stdout


def _tree_hash(entries, oid_length=40):
    """Compute Git's staged tree OID without writing the index or object store."""
    tree = {}
    algorithm = "sha1" if oid_length == 40 else "sha256"
    for item in entries.split(b'\0'):
        if not item:
            continue
        metadata, path = item.split(b'\t', 1)
        mode, oid, stage = metadata.split(b' ')
        if stage != b'0':
            return None
        if len(oid) != oid_length:
            raise JournalError("index object format mismatch")
        node = tree
        parts = path.split(b'/')
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = (mode, oid)
    def encode(node):
        contents = b''
        for name, value in sorted(node.items(), key=lambda pair: pair[0] + (b'/' if isinstance(pair[1], dict) else b'')):
            if isinstance(value, dict):
                mode, oid = b'40000', encode(value)
            else:
                mode, oid = value
            contents += mode.lstrip(b'0') + b' ' + name + b'\0' + bytes.fromhex(oid.decode())
        return hashlib.new(algorithm, b'tree ' + str(len(contents)).encode() + b'\0' + contents).hexdigest().encode()
    return encode(tree).decode()


def _capture(vault):
    from tools.gate3_audit import _snapshot_payload
    head = _git(vault, 'rev-parse', 'HEAD').decode().strip()
    entries = _git(vault, 'ls-files', '--stage', '-z')
    snapshot = json.loads(_canonical(_snapshot_payload(vault)))
    if snapshot['head'] != head or _git(vault, 'rev-parse', 'HEAD').decode().strip() != head:
        raise JournalError('HEAD changed during evidence capture')
    if entries != _git(vault, 'ls-files', '--stage', '-z'):
        raise JournalError('index changed during evidence capture')
    return head, snapshot, base64.b64encode(entries).decode(), _tree_hash(entries, len(head))


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise JournalError('duplicate journal key')
        result[key] = value
    return result


def _validate_snapshot(raw, head):
    from tools.gate3_audit import SNAPSHOT_VERSION, _load_filesystem_map
    if not isinstance(raw, dict) or set(raw) != {'version', 'head', 'dirty', 'filesystem'}:
        raise JournalError('invalid journal snapshot')
    if type(raw['version']) is not int or raw['version'] != SNAPSHOT_VERSION or raw['head'] != head:
        raise JournalError('invalid journal snapshot identity')
    _load_filesystem_map(raw['filesystem'])
    if not isinstance(raw['dirty'], dict):
        raise JournalError('invalid dirty evidence')
    for path, value in raw['dirty'].items():
        if not isinstance(path, str) or path.startswith('/') or any(p in {'', '.', '..'} for p in path.split('/')):
            raise JournalError('invalid dirty path')
        if not isinstance(value, dict) or set(value) != {'status', 'index_entries', 'kind', 'mode', 'digest'}:
            raise JournalError('invalid dirty fingerprint')
        if not isinstance(value['status'], str) or len(value['status']) != 2 or value['kind'] not in ('file', 'symlink', 'directory', 'other', 'absence', 'redirected'):
            raise JournalError('invalid dirty fingerprint types')
        if not isinstance(value['index_entries'], list) or not all(isinstance(v, str) for v in value['index_entries']):
            raise JournalError('invalid dirty index entries')
        if value['mode'] is not None and (type(value['mode']) is not int or not 0 <= value['mode'] <= 0o7777):
            raise JournalError('invalid dirty mode')
        if value['digest'] is not None and (not isinstance(value['digest'], str) or not _SHA.fullmatch(value['digest'])):
            raise JournalError('invalid dirty digest')


def _validate(record, sequence, session, previous):
    if not isinstance(record, dict) or set(record) != _FIELDS:
        raise JournalError('invalid checkpoint schema')
    if type(record['version']) is not int or record['version'] != 1 or type(record['sequence']) is not int or record['sequence'] != sequence:
        raise JournalError('invalid checkpoint version or sequence')
    if record['session_id'] != session or record['previous_digest'] != (None if previous is None else _digest(previous)):
        raise JournalError('checkpoint chain mismatch')
    head = record['head']
    if not isinstance(head, str) or not _OID.fullmatch(head):
        raise JournalError('invalid checkpoint HEAD')
    _validate_snapshot(record['snapshot'], head)
    entries = base64.b64decode(record['index_entries'], validate=True)
    if base64.b64encode(entries).decode() != record['index_entries']:
        raise JournalError('noncanonical index encoding')
    if entries and not entries.endswith(b'\0'):
        raise JournalError('invalid index termination')
    seen = set()
    for item in entries.split(b'\0'):
        if not item:
            continue
        metadata, path = item.split(b'\t', 1)
        mode, oid, stage = metadata.split(b' ')
        if mode not in {b'100644', b'100755', b'120000', b'160000', b'040000'} or not _OID.fullmatch(oid.decode()) or stage not in {b'0', b'1', b'2', b'3'}:
            raise JournalError('invalid index entry')
        if path.startswith(b'/') or any(p in {b'', b'.', b'..'} for p in path.split(b'/')) or (path, stage) in seen:
            raise JournalError('invalid index path')
        seen.add((path, stage))
    if record['index_tree'] != _tree_hash(entries, len(head)):
        raise JournalError('index tree mismatch')
    phase, proposal, commit = record['phase'], record['proposal_id'], record['commit_oid']
    if sequence == 0:
        if (phase, proposal, commit) != ('initial', None, None):
            raise JournalError('invalid initial checkpoint')
    elif not isinstance(proposal, str) or not _ID.fullmatch(proposal):
        raise JournalError('invalid checkpoint proposal')
    elif phase == 'before':
        if previous['phase'] not in {'initial', 'after'} or commit is not None:
            raise JournalError('pending checkpoint cannot be bypassed')
    elif phase == 'after':
        if previous['phase'] != 'before' or proposal != previous['proposal_id'] or commit != head:
            raise JournalError('after checkpoint does not match pending action')
    else:
        raise JournalError('invalid checkpoint phase')


def load_journal(session_path: Path, anchor: str) -> tuple[dict, ...]:
    """Validate ordered evidence; a trailing before remains explicitly pending."""
    try:
        session_path = _absolute(session_path)
        if not isinstance(anchor, str) or not _SHA.fullmatch(anchor):
            raise JournalError('invalid independent anchor')
        session = session_path.name
        if not re.fullmatch(r'[0-9a-f]{32}', session):
            raise JournalError('invalid session identity')
        records = []
        with _directory(session_path) as directory:
            names = sorted(os.listdir(directory))
            if not names or names != [f'{i:08d}.json' for i in range(len(names))]:
                raise JournalError('missing or unexpected checkpoint files')
            for sequence, name in enumerate(names):
                descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
                with os.fdopen(descriptor, 'rb') as stream:
                    info = os.fstat(stream.fileno())
                    if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1:
                        raise JournalError('unsafe checkpoint file')
                    data = stream.read()
                record = json.loads(data, object_pairs_hook=_unique)
                if data != _canonical(record):
                    raise JournalError('noncanonical checkpoint bytes')
                _validate(record, sequence, session, records[-1] if records else None)
                if sequence == 0 and _digest(record) != anchor:
                    raise JournalError('independent checkpoint anchor mismatch')
                records.append(record)
            if sorted(os.listdir(directory)) != names:
                raise JournalError('journal changed during validation')
        return tuple(records)
    except JournalError:
        raise
    except (OSError, ValueError, TypeError, KeyError, UnicodeError) as exc:
        raise JournalError('journal cannot be validated') from exc


class LifecycleJournal:
    @classmethod
    def create(cls, store: Path, vault: Path) -> LifecycleJournal:
        try:
            store, vault = _absolute(store), _absolute(vault)
            with _directory(vault):
                pass
            if store.is_relative_to(vault) or store.is_relative_to(_REPO):
                raise JournalError('journal store must be external')
            journal = cls()
            journal._vault = vault
            journal._session = uuid.uuid4().hex
            journal.session_path = store / journal._session
            journal._count = 0
            journal._last = None
            with _directory(store) as directory:
                os.mkdir(journal._session, mode=0o700, dir_fd=directory)
                os.fsync(directory)
            with _directory(journal.session_path) as directory:
                info = os.fstat(directory)
                journal._identity = (info.st_dev, info.st_ino)
            journal._append(vault, None, 'initial', None)
            journal.anchor = _digest(journal._last)
            return journal
        except JournalError:
            raise
        except (OSError, ValueError, TypeError) as exc:
            raise JournalError('journal cannot be initialized') from exc

    def _append(self, vault, proposal_id, phase, commit_oid):
        head, snapshot, entries, tree = _capture(vault)
        record = dict(version=1, session_id=self._session, sequence=self._count,
                      previous_digest=None if self._last is None else _digest(self._last),
                      phase=phase, proposal_id=proposal_id, head=head, commit_oid=commit_oid,
                      snapshot=snapshot, index_entries=entries, index_tree=tree)
        _validate(record, self._count, self._session, self._last)
        with _directory(self.session_path) as directory:
            info = os.fstat(directory)
            if (info.st_dev, info.st_ino) != self._identity:
                raise JournalError('journal directory was replaced')
            descriptor = os.open(f'{self._count:08d}.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 0o600, dir_fd=directory)
            with os.fdopen(descriptor, 'wb') as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(_canonical(record))
                stream.flush()
                os.fsync(stream.fileno())
            os.fsync(directory)
        self._count += 1
        self._last = record

    def checkpoint(self, vault: Path, proposal_id: str, phase: str,
                   commit_oid: str | None = None) -> None:
        try:
            if _absolute(vault) != self._vault:
                raise JournalError('journal belongs to another vault')
            records = load_journal(self.session_path, self.anchor)
            if len(records) != self._count or records[-1] != self._last:
                raise JournalError('journal was truncated or externally extended')
            if phase not in {'before', 'after'}:
                raise JournalError('invalid action checkpoint phase')
            self._append(self._vault, proposal_id, phase, commit_oid)
        except JournalError:
            raise
        except (OSError, ValueError, TypeError) as exc:
            raise JournalError('checkpoint could not be persisted') from exc
