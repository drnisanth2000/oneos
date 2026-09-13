"""Encrypted external snapshots. Nothing here restores into a live vault."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import plistlib
import secrets
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime

from tools.local_service import private_file, safe_path, write_json, write_status, paused, safe_failure
from tools.local_metadata import read_xattrs, write_xattrs


def disk_info(path):
    result = subprocess.run(['diskutil', 'info', '-plist', str(path)], check=True, capture_output=True, timeout=30)
    return plistlib.loads(result.stdout)


def validate_volume(config):
    mount = safe_path(Path(config['mount']))
    info = disk_info(mount)
    if (not isinstance(config['volume_uuid'], str) or not config['volume_uuid']
            or info.get('VolumeUUID') != config['volume_uuid'] or info.get('Internal') is not False
            or info.get('VirtualOrPhysical') != 'Physical' or info.get('Mounted') is not True
            or info.get('MountPoint') != str(mount)):
        raise ValueError('expected external physical volume is unavailable')
    return mount


def validate_backup_location(config, mount):
    """Keep encrypted storage separate from both plaintext sources and the key."""
    mount = safe_path(mount)
    for name in ('vault', 'state_dir'):
        private = safe_path(Path(config[name]))
        if private.is_relative_to(mount) or mount.is_relative_to(private):
            raise ValueError('backup volume must be separate from vault and private state')


@contextmanager
def external_session(config):
    """Pin writes to an open external directory, even if its mount disappears.

    This CLI is single-threaded. Relative paths and inherited cwd deliberately
    retain the directory vnode; they cannot fall back to the host mountpoint.
    """
    mount = validate_volume(config)
    original = os.open('.', os.O_RDONLY)
    volume = root = None
    try:
        volume = os.open(mount, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        validate_volume(config)
        if (os.fstat(volume).st_dev, os.fstat(volume).st_ino) != (mount.stat().st_dev, mount.stat().st_ino):
            raise ValueError('volume was replaced')
        try:
            os.mkdir('oneos-backup', mode=0o700, dir_fd=volume)
        except FileExistsError:
            pass
        root = os.open('oneos-backup', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=volume)
        if os.fstat(root).st_dev != os.fstat(volume).st_dev:
            raise ValueError('backup directory is on another filesystem')
        os.fchdir(root)
        yield root
    finally:
        try:
            os.fchdir(original)
        finally:
            for fd in (root, volume, original):
                if fd is not None: os.close(fd)


def digest(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as source:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            raise ValueError('unsupported file')
        return hashlib.file_digest(source, 'sha256').hexdigest()


def inventory(root):
    root = safe_path(root)
    result = {'.': {'kind': 'directory', 'mode': stat.S_IMODE(root.stat().st_mode), 'xattrs': read_xattrs(root)}}
    def walk(directory):
        for item in sorted(directory.iterdir()):
            relative = str(item.relative_to(root))
            info = item.lstat()
            if stat.S_ISLNK(info.st_mode):
                target = os.readlink(item)
                try:
                    resolved = item.resolve()
                except RuntimeError as exc:
                    raise ValueError('cyclic symlink cannot be backed up') from exc
                if os.path.isabs(target) or not resolved.is_relative_to(root):
                    raise ValueError('external or absolute symlink cannot be backed up')
                result[relative] = {'kind': 'link', 'target': target}
            elif stat.S_ISDIR(info.st_mode):
                if info.st_dev != root.stat().st_dev:
                    raise ValueError('nested filesystem cannot be backed up')
                result[relative] = {'kind': 'directory', 'mode': stat.S_IMODE(info.st_mode)}
                walk(item)
            elif stat.S_ISREG(info.st_mode):
                if info.st_nlink != 1:
                    raise ValueError('hardlinked files require a separate backup plan')
                result[relative] = {'kind': 'file', 'sha256': digest(item), 'mode': stat.S_IMODE(info.st_mode),
                                    'size': info.st_size}
            else:
                raise ValueError('non-regular filesystem entry cannot be backed up')
            result[relative]['xattrs'] = read_xattrs(item)
    walk(root)
    if sys.platform == 'darwin':
        # Never invoke recursive native utilities: xattr recursion can follow
        # directory links. Check only the already inventoried entries in batches.
        paths = [str(root / relative) for relative in result]
        for offset in range(0, len(paths), 100):
            batch = paths[offset:offset + 100]
            listing = subprocess.run(['/bin/ls', '-lde', *batch], capture_output=True, check=True, timeout=30)
            if re.search(rb'^\s*\d+:\s', listing.stdout, re.MULTILINE):
                raise ValueError('macOS ACLs require a metadata-aware backup plan')
    return result


def copy_regular(source, destination):
    attributes = read_xattrs(source)
    fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as incoming, destination.open('xb') as outgoing:
        shutil.copyfileobj(incoming, outgoing)
    write_xattrs(destination, attributes)
    shutil.copystat(source, destination, follow_symlinks=False)
    if read_xattrs(source) != attributes:
        raise ValueError('extended attributes changed during copy')
    if digest(source) != digest(destination):
        raise ValueError('copy hash mismatch')


def copy_tree(source, target):
    before = inventory(source)
    target.mkdir(mode=0o700)
    for relative, entry in before.items():
        if relative == '.':
            continue
        destination = target / relative
        if entry['kind'] == 'directory':
            destination.mkdir(mode=0o700)
        elif entry['kind'] == 'link':
            destination.symlink_to(entry['target'])
        else:
            copy_regular(source / relative, destination)
    for relative, entry in reversed(list(before.items())):
        destination = target / relative
        write_xattrs(destination, entry['xattrs'])
        if entry['kind'] == 'directory':
            destination.chmod(entry['mode'])
    if inventory(source) != before or inventory(target) != before:
        raise ValueError('source changed during snapshot')


def check_git_layout(source):
    git_dir = source / '.git'
    if not git_dir.is_dir() or git_dir.is_symlink():
        raise ValueError('standalone Git directory required')
    for path in ('objects/info/alternates', 'objects/info/http-alternates', 'commondir', 'worktrees'):
        if (git_dir / path).exists():
            raise ValueError('external Git storage or linked worktrees unsupported')
    result = subprocess.run(['git', '-C', str(source), 'config', '--get', 'core.worktree'],
                            capture_output=True, text=True, timeout=30)
    if result.returncode != 1:
        raise ValueError('explicit Git worktree redirection is unsupported')
    hooks = subprocess.run(['git', '-C', str(source), 'config', '--path', '--get', 'core.hooksPath'],
                           capture_output=True, text=True, timeout=30)
    if hooks.returncode == 0:
        configured = Path(hooks.stdout.rstrip('\n'))
        captured = subprocess.run(['git', '-C', str(source), 'config', '--local', '--no-includes',
                                   '--path', '--get', 'core.hooksPath'], capture_output=True, text=True, timeout=30)
        if captured.returncode != 0 or captured.stdout != hooks.stdout:
            raise ValueError('Git hooks configuration must be captured in the local repository')
        # Absolute paths still point at the original checkout after recovery.
        try:
            resolved_hooks = (source / configured).resolve()
        except RuntimeError as exc:
            raise ValueError('cyclic Git hooks path is unsupported') from exc
        if configured.is_absolute() or not resolved_hooks.is_relative_to(source):
            raise ValueError('external or absolute Git hooks path is unsupported')
    elif hooks.returncode != 1:
        raise ValueError('Git hooks configuration is unavailable')


def sqlite_files(root):
    for relative, entry in inventory(root).items():
        if entry['kind'] == 'file':
            path = root / relative
            with path.open('rb') as source:
                if source.read(16) == b'SQLite format 3\x00':
                    yield path


def sqlite_copy(source, destination, workspace):
    """Online-backup a private COPY, never open the original vault database."""
    workspace.mkdir(mode=0o700)
    local = workspace / 'database'
    copy_regular(source, local)
    for suffix in ('-wal', '-shm', '-journal'):
        sidecar = Path(str(source) + suffix)
        if sidecar.exists():
            copy_regular(sidecar, Path(str(local) + suffix))
    with sqlite3.connect(local) as incoming, sqlite3.connect(destination) as output:
        if incoming.execute('pragma integrity_check').fetchall() != [('ok',)]:
            raise ValueError('SQLite integrity check failed')
        incoming.backup(output)
        if output.execute('pragma integrity_check').fetchall() != [('ok',)]:
            raise ValueError('SQLite backup verification failed')


def snapshot(source, target):
    if any(key.startswith('GIT_') and key not in {'GIT_PAGER', 'GIT_TERMINAL_PROMPT'} for key in os.environ):
        raise ValueError('unset Git environment overrides before backup')
    source = safe_path(source)
    check_git_layout(source)
    before = inventory(source)
    for relative, entry in before.items():
        if Path(relative).name == '.git' and relative != '.git':
            if entry['kind'] != 'directory':
                raise ValueError('nested external Git directory unsupported')
            check_git_layout((source / relative).parent)
    target.mkdir(mode=0o700)
    vault = target / 'vault'
    copy_tree(source, vault)
    if inventory(source) != before or inventory(vault) != before:
        raise ValueError('source changed during snapshot')
    normalized = target / 'sqlite'
    normalized.mkdir(mode=0o700)
    for index, database in enumerate(sqlite_files(vault)):
        with tempfile.TemporaryDirectory(dir=target, prefix='sqlite-work-') as working:
            sqlite_copy(database, normalized / (str(index) + '.db'), Path(working) / 'copy')
    if inventory(source) != before:
        raise ValueError('source changed during snapshot verification')
    manifest = inventory(target)
    write_json(target / 'manifest.json', {'version': 2, 'files': manifest})
    verify_snapshot(target)


def verify_snapshot(target):
    target = safe_path(target)
    manifest = json.loads((target / 'manifest.json').read_text())
    actual = inventory(target)
    actual.pop('manifest.json', None)
    if manifest.get('version') != 2 or actual != manifest['files']:
        raise ValueError('snapshot manifest mismatch')
    check_git_layout(target / 'vault')
    subprocess.run(['git', '-C', str(target / 'vault'), 'fsck', '--full'],
                   check=True, capture_output=True, timeout=900)
    for database in sqlite_files(target / 'sqlite'):
        with sqlite3.connect(database.as_uri() + '?immutable=1', uri=True) as db:
            if db.execute('pragma integrity_check').fetchall() != [('ok',)]:
                raise ValueError('restored SQLite integrity failure')


def restic(config, *args, cwd=None):
    backup_config = config['backup']
    validate_backup_location(config, Path(backup_config['mount']))
    key = private_file(Path(backup_config['password_file']))
    env = {k: v for k, v in os.environ.items() if not k.startswith('RESTIC_')}
    env['RESTIC_PASSWORD_FILE'] = str(key)
    # Any temporary files remain inside private host state; only encrypted
    # repository contents are written to the external drive.
    env['TMPDIR'] = config['state_dir']
    with external_session(backup_config) as external:
        try:
            repository = os.open('repository', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                 dir_fd=external)
        except FileNotFoundError:
            if not args or args[0] != 'init':
                raise ValueError('backup repository is unavailable')
            os.mkdir('repository', mode=0o700, dir_fd=external)
            repository = os.open('repository', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                 dir_fd=external)
        try:
            if os.fstat(repository).st_dev != os.fstat(external).st_dev:
                raise ValueError('repository is on another filesystem')
            if cwd is not None:
                raise ValueError('restic must retain the pinned external working directory')
            # The child inherits this already-open directory as its working
            # directory, so replacing the visible mount path cannot redirect it.
            os.fchdir(repository)
            return subprocess.run(['restic', '--no-cache', '--repo', '.', *args],
                                  env=env, check=True, capture_output=True, text=True, timeout=7200)
        finally:
            os.close(repository)


def setup_backup(config, mount, offline_confirmed):
    if 'backup' in config:
        raise ValueError('backup already configured')
    mount = safe_path(mount)
    info = disk_info(mount)
    backup_config = dict(mount=str(mount), volume_uuid=info.get('VolumeUUID'),
                         password_file=str(Path(config['state_dir']) / 'backup-key'))
    validate_volume(backup_config)
    validate_backup_location(config, mount)
    key = Path(backup_config['password_file'])
    if not key.exists():
        fd = os.open(key, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as output:
            output.write(secrets.token_urlsafe(48) + '\n')
    private_file(key)
    if not offline_confirmed:
        raise ValueError('copy the private backup key offline before confirming setup')
    proposed = {**config, 'backup': backup_config}
    # A crash can leave an initialized repository without the host config update.
    # Reuse it only after authenticating with the retained offline-backed key;
    # never remove or reinitialize existing encrypted data to recover setup.
    initialized = False
    with external_session(backup_config) as external:
        try:
            repository = os.open('repository', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                 dir_fd=external)
        except FileNotFoundError:
            pass
        else:
            try:
                if os.fstat(repository).st_dev != os.fstat(external).st_dev:
                    raise ValueError('repository is on another filesystem')
                try:
                    existing = os.stat('config', dir_fd=repository, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    if not stat.S_ISREG(existing.st_mode):
                        raise ValueError('unsafe repository configuration')
                    initialized = True
            finally:
                os.close(repository)
    if initialized:
        restic(proposed, 'cat', 'config')
    else:
        restic(proposed, 'init')
    write_json(Path(config['state_dir']) / 'config.json', proposed)
    config.update(proposed)


def previous_success(config):
    try:
        path = safe_path(Path(config['state_dir']) / 'status/backup-status.json', directory=False)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 4096:
                return None
            raw = stream.read(4097)
            if len(raw) > 4096:
                return None
            status = json.loads(raw)
        if not isinstance(status, dict):
            return None
        last = status.get('last_success')
        if isinstance(last, bool) or not isinstance(last, (int, float)):
            return None
        if isinstance(last, float) and not math.isfinite(last):
            return None
        if not 0 < last <= time.time():
            return None
        return last
    except (OSError, ValueError, TypeError):
        return None


def backup(config, runtime, due_only=False):
    last = previous_success(config)
    if due_only and last and time.time() - last < 86400:
        return
    if due_only:
        write_status(config, 'missed', last)
    try:
        from app.git_transaction import action_lock
        validate_volume(config['backup'])
        validate_backup_location(config, Path(config['backup']['mount']))
        state = safe_path(Path(config['state_dir']))
        with paused(runtime):
            with action_lock(Path(config['vault'])):
                # Sizing inventories hash files too: pause the app and acquire
                # the writer lock before traversing volatile SQLite sidecars.
                needed = sum(entry.get('size', 0) + sum(len(value) // 2 for value in entry.get('xattrs', {}).values())
                             for root in (Path(config['vault']), state / 'caddy', state / 'auth')
                             for entry in inventory(root).values())
                if shutil.disk_usage(state).free < needed * 3 + 64 * 1024 * 1024:
                    raise ValueError('insufficient private staging capacity')
                stage = Path(tempfile.mkdtemp(prefix='snapshot-', dir=state))
                payload = stage / 'snapshot'
                snapshot(Path(config['vault']), payload)
                deployment = payload / 'deployment'
                deployment.mkdir(mode=0o700)
                copy_tree(state / 'caddy', deployment / 'caddy')
                copy_tree(state / 'auth', deployment / 'auth')
                for index, database in enumerate(sqlite_files(deployment / 'auth')):
                    with tempfile.TemporaryDirectory(dir=stage, prefix='sqlite-work-') as working:
                        sqlite_copy(database, payload / 'sqlite' / ('auth-' + str(index) + '.db'), Path(working) / 'copy')
                copy_regular(private_file(state / 'config.json'), deployment / 'config.json')
                manifest = inventory(payload)
                manifest.pop('manifest.json')
                write_json(payload / 'manifest.json', {'version': 2, 'files': manifest})
                verify_snapshot(payload)
        # Never claim success on partial-backup exit codes or interruption.
        restic(config, 'backup', '--tag', 'oneos', '--', str(payload))
        restic(config, 'check')
        restic(config, 'forget', '--tag', 'oneos', '--group-by', 'tags', '--keep-daily', '7',
               '--keep-weekly', '4', '--keep-monthly', '6', '--prune')
        shutil.rmtree(stage)  # Exact generated private staging directory, after success.
        write_status(config, 'ok', time.time())
    except BaseException as error:
        write_status(config, 'failed', last, reason=safe_failure(error)['message'])
        raise


def restore_check(config):
    state = safe_path(Path(config['state_dir']))
    snapshots = json.loads(restic(config, 'snapshots', '--tag', 'oneos', '--json').stdout)
    if not snapshots:
        raise ValueError('expected one complete snapshot')
    selected = max(snapshots, key=lambda item: datetime.fromisoformat(item['time'].replace('Z', '+00:00')))
    if len(selected.get('paths', [])) != 1:
        raise ValueError('expected one complete snapshot path')
    snapshot_id = selected['id']
    if len(snapshot_id) != 64 or any(char not in '0123456789abcdef' for char in snapshot_id):
        raise ValueError('invalid snapshot identity')
    size = json.loads(restic(config, 'stats', snapshot_id, '--mode', 'restore-size', '--json').stdout)['total_size']
    if shutil.disk_usage(state).free < size * 2 + 64 * 1024 * 1024:
        raise ValueError('insufficient isolated recovery capacity')
    target = Path(tempfile.mkdtemp(prefix='recovery-', dir=state))
    restic(config, 'restore', snapshot_id + ':' + selected['paths'][0], '--target', str(target / 'snapshot'))
    verify_snapshot(target / 'snapshot')
    # Includes encrypted owner state for disaster recovery, but NEVER activates it.
    return target
