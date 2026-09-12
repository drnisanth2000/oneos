import json
import sqlite3
import subprocess
from pathlib import Path

import pytest


def test_external_session_closes_original_descriptor_when_volume_open_fails(tmp_path, monkeypatch):
    import errno
    import os
    from tools import local_backup as backup
    config, mount = backup_config(tmp_path, monkeypatch)
    original_open = os.open
    opened = []
    def fail_volume(path, flags, *args, **kwargs):
        if path == mount:
            raise OSError('injected volume open failure')
        fd = original_open(path, flags, *args, **kwargs)
        if path == '.':
            opened.append(fd)
        return fd
    monkeypatch.setattr(os, 'open', fail_volume)
    with pytest.raises(OSError, match='injected volume'):
        with backup.external_session(dict(mount=str(mount), volume_uuid='expected')):
            pytest.fail('volume open must fail')
    assert len(opened) == 1
    try:
        with pytest.raises(OSError) as closed:
            os.fstat(opened[0])
        assert closed.value.errno == errno.EBADF
    finally:
        try:
            os.close(opened[0])
        except OSError:
            pass


@pytest.mark.parametrize('command', ['backup', 'restore'])
def test_restic_timeout_restores_cwd_and_closes_repository(tmp_path, monkeypatch, command):
    import os
    from tools import local_backup as backup
    config, mount = backup_config(tmp_path, monkeypatch)
    key = Path(config['state_dir']) / 'backup-key'
    key.write_text('synthetic-key')
    key.chmod(0o600)
    config['backup'] = dict(mount=str(mount), volume_uuid='expected', password_file=str(key))
    (mount / 'oneos-backup/repository').mkdir(parents=True)
    before = Path.cwd()
    opened = []
    original_open = os.open
    def tracked_open(path, flags, *args, **kwargs):
        fd = original_open(path, flags, *args, **kwargs)
        if path == 'repository':
            opened.append(fd)
        return fd
    def hung_child(args, **kwargs):
        timeout = kwargs.get('timeout')
        assert isinstance(timeout, (int, float)) and 0 < timeout <= 7200
        raise subprocess.TimeoutExpired(args, timeout)
    monkeypatch.setattr(os, 'open', tracked_open)
    monkeypatch.setattr(subprocess, 'run', hung_child)
    with pytest.raises(subprocess.TimeoutExpired):
        backup.restic(config, command)
    assert Path.cwd() == before
    assert len(opened) == 1
    with pytest.raises(OSError):
        os.fstat(opened[0])


def test_snapshot_inspection_subprocesses_have_deadlines(tmp_path, monkeypatch):
    from tools import local_backup as backup
    source = tmp_path / 'source'
    source.mkdir()
    subprocess.run(['git', 'init', '-q', str(source)], check=True)
    original = subprocess.run
    def bounded(command, **kwargs):
        timeout = kwargs.get('timeout')
        assert isinstance(timeout, (int, float)) and 0 < timeout <= 900
        return original(command, **kwargs)
    monkeypatch.setattr(subprocess, 'run', bounded)
    backup.snapshot(source, tmp_path / 'snapshot')
    backup.verify_snapshot(tmp_path / 'snapshot')


def test_disk_identity_timeout_is_propagated(tmp_path, monkeypatch):
    from tools import local_backup as backup
    def hung(command, **kwargs):
        timeout = kwargs.get('timeout')
        assert isinstance(timeout, (int, float)) and 0 < timeout <= 30
        raise subprocess.TimeoutExpired(command, timeout)
    monkeypatch.setattr(subprocess, 'run', hung)
    with pytest.raises(subprocess.TimeoutExpired):
        backup.disk_info(tmp_path)


@pytest.mark.parametrize('private_path', ['vault', 'state_dir'])
@pytest.mark.parametrize('placement', ['root', 'child'])
def test_setup_backup_refuses_volume_containing_private_source(tmp_path, monkeypatch, private_path, placement):
    from tools import local_backup as backup
    config, mount = backup_config(tmp_path, monkeypatch)
    config[private_path] = str(mount if placement == 'root' else mount / 'private-source')
    Path(config[private_path]).mkdir(mode=0o700, exist_ok=True)
    with pytest.raises(ValueError, match='separate'):
        backup.setup_backup(config, mount, True)
    assert not (mount / 'oneos-backup').exists()
    assert not (Path(config['state_dir']) / 'backup-key').exists()


@pytest.mark.parametrize('private_path', ['vault', 'state_dir'])
def test_existing_backup_refuses_relocated_private_source(tmp_path, monkeypatch, private_path):
    from tools import local_backup as backup
    config, mount = backup_config(tmp_path, monkeypatch)
    key = Path(config['state_dir']) / 'backup-key'
    key.write_text('synthetic-key')
    key.chmod(0o600)
    config['backup'] = dict(mount=str(mount), volume_uuid='expected', password_file=str(key))
    config[private_path] = str(mount / 'private-source')
    Path(config[private_path]).mkdir(mode=0o700)
    with pytest.raises(ValueError, match='separate'):
        backup.restic(config, 'init')
    assert not (mount / 'oneos-backup').exists()


@pytest.mark.parametrize('hooks_path', ['absolute', 'relative-escape', 'inherited-relative', 'absolute-internal'])
def test_snapshot_refuses_external_git_hooks(tmp_path, hooks_path):
    from tools.local_backup import snapshot
    source = tmp_path / 'source'
    source.mkdir()
    subprocess.run(['git', 'init', '-q', str(source)], check=True)
    hooks = tmp_path / 'policy-hooks'
    hooks.mkdir()
    (hooks / 'pre-commit').write_text('#!/bin/sh\nexit 1\n')
    configured = str(hooks) if hooks_path == 'absolute' else '../policy-hooks'
    if hooks_path in ('inherited-relative', 'absolute-internal'):
        hooks = source / 'policy-hooks'
        hooks.mkdir()
        (hooks / 'pre-commit').write_text('#!/bin/sh\nexit 1\n')
        configured = str(hooks)
    if hooks_path == 'inherited-relative':
        external_config = tmp_path / 'external-config'
        subprocess.run(['git', 'config', '--file', str(external_config), 'core.hooksPath', 'policy-hooks'], check=True)
        subprocess.run(['git', '-C', str(source), 'config', 'include.path', str(external_config)], check=True)
    else:
        subprocess.run(['git', '-C', str(source), 'config', 'core.hooksPath', configured], check=True)
    with pytest.raises(ValueError, match='hooks'):
        snapshot(source, tmp_path / 'snapshot')
    assert not (tmp_path / 'snapshot').exists()


def test_snapshot_keeps_relative_internal_git_policy_hook(tmp_path):
    from tools.local_backup import snapshot
    source = tmp_path / 'source'
    source.mkdir()
    subprocess.run(['git', 'init', '-q', str(source)], check=True)
    hooks = source / 'policy-hooks'
    hooks.mkdir()
    hook = hooks / 'pre-commit'
    hook.write_text('#!/bin/sh\nexit 42\n')
    hook.chmod(0o700)
    subprocess.run(['git', '-C', str(source), 'config', 'core.hooksPath', 'policy-hooks'], check=True)
    snapshot(source, tmp_path / 'snapshot')
    restored = tmp_path / 'snapshot/vault'
    result = subprocess.run(['git', '-C', str(restored), '-c', 'user.name=Test',
                             '-c', 'user.email=test@example.invalid', 'commit', '--allow-empty', '-m', 'must refuse'],
                            capture_output=True)
    assert result.returncode != 0
    assert not (restored / '.git/refs/heads/main').exists()
    assert (restored / 'policy-hooks/pre-commit').read_bytes() == hook.read_bytes()


@pytest.mark.parametrize('failure', ['config-write', 'after-init'])
def test_setup_backup_retry_after_initialized_repository(tmp_path, monkeypatch, failure):
    import shutil
    from tools import local_backup as backup
    if not shutil.which('restic'):
        pytest.skip('restic executable unavailable')
    config, mount = backup_config(tmp_path, monkeypatch)
    original_write = backup.write_json
    original_restic = backup.restic
    def failed_write(*args, **kwargs):
        raise OSError('injected config persistence failure')
    def interrupted_restic(*args, **kwargs):
        original_restic(*args, **kwargs)
        raise InterruptedError('injected interruption after repository initialization')
    if failure == 'config-write':
        monkeypatch.setattr(backup, 'write_json', failed_write)
    else:
        monkeypatch.setattr(backup, 'restic', interrupted_restic)
    with pytest.raises(OSError):
        backup.setup_backup(config, mount, True)
    assert 'backup' not in config
    state = Path(config['state_dir'])
    assert 'backup' not in json.loads((state / 'config.json').read_text())
    key_before = (state / 'backup-key').read_bytes()
    repository_before = (mount / 'oneos-backup/repository/config').read_bytes()
    monkeypatch.setattr(backup, 'write_json', original_write)
    monkeypatch.setattr(backup, 'restic', original_restic)
    backup.setup_backup(config, mount, True)
    assert json.loads((state / 'config.json').read_text())['backup'] == config['backup']
    assert (state / 'backup-key').read_bytes() == key_before
    assert (mount / 'oneos-backup/repository/config').read_bytes() == repository_before
    assert json.loads(backup.restic(config, 'cat', 'config').stdout)['id']


def test_setup_backup_refuses_existing_repository_with_wrong_key(tmp_path, monkeypatch):
    import shutil
    from tools import local_backup as backup
    if not shutil.which('restic'):
        pytest.skip('restic executable unavailable')
    config, mount = backup_config(tmp_path, monkeypatch)
    backup.setup_backup(config, mount, True)
    state = Path(config['state_dir'])
    del config['backup']
    backup.write_json(state / 'config.json', config)
    (state / 'backup-key').write_text('wrong-synthetic-key')
    before = backup.inventory(mount / 'oneos-backup/repository')
    with pytest.raises(subprocess.CalledProcessError):
        backup.setup_backup(config, mount, True)
    assert backup.inventory(mount / 'oneos-backup/repository') == before
    assert 'backup' not in config
    assert 'backup' not in json.loads((state / 'config.json').read_text())


def test_setup_backup_retries_empty_repository_after_child_interruption(tmp_path, monkeypatch):
    import shutil
    from tools import local_backup as backup
    if not shutil.which('restic'):
        pytest.skip('restic executable unavailable')
    config, mount = backup_config(tmp_path, monkeypatch)
    original = subprocess.run
    def interrupted(args, **kwargs):
        if args[0] == 'restic':
            raise InterruptedError('injected child startup interruption')
        return original(args, **kwargs)
    monkeypatch.setattr(subprocess, 'run', interrupted)
    with pytest.raises(InterruptedError):
        backup.setup_backup(config, mount, True)
    repository = mount / 'oneos-backup/repository'
    assert repository.is_dir()
    assert list(repository.iterdir()) == []
    key_before = (Path(config['state_dir']) / 'backup-key').read_bytes()
    monkeypatch.setattr(subprocess, 'run', original)
    backup.setup_backup(config, mount, True)
    assert (Path(config['state_dir']) / 'backup-key').read_bytes() == key_before
    assert json.loads(backup.restic(config, 'cat', 'config').stdout)['id']


def test_backup_pauses_before_inventory_of_volatile_auth_journal(tmp_path, monkeypatch):
    import shutil
    from tools import local_backup as backup
    if not shutil.which('restic'):
        pytest.skip('restic executable unavailable')
    config, mount = backup_config(tmp_path, monkeypatch)
    backup.setup_backup(config, mount, True)
    state = Path(config['state_dir'])
    database = state / 'auth/owner.sqlite3'
    with sqlite3.connect(database) as db:
        db.execute('create table preserved (value integer)')
        db.execute('insert into preserved values (17)')
    before = database.read_bytes()
    journal = Path(str(database) + '-journal')
    journal.write_bytes(b'synthetic active writer journal')
    class Runtime:
        running = True
        def running_services(self):
            return ['app', 'caddy'] if self.running else []
        def compose(self, *args):
            assert args[1:] == ('app', 'caddy')
            self.running = args[0] == 'start'
            if args[0] == 'stop':
                journal.unlink(missing_ok=True)
    runtime = Runtime()
    original_digest = backup.digest
    def concurrent_writer(path):
        if path == journal and runtime.running:
            # Deterministically finish the transaction after inventory discovers
            # its journal, before the journal's contents can be hashed.
            journal.unlink()
        return original_digest(path)
    monkeypatch.setattr(backup, 'digest', concurrent_writer)
    backup.backup(config, runtime)
    assert runtime.running
    assert database.read_bytes() == before
    assert json.loads((state / 'status/backup-status.json').read_text())['state'] == 'ok'


def test_capacity_failure_restores_services_after_paused_sizing(tmp_path, monkeypatch):
    from tools import local_backup as backup
    config, mount = backup_config(tmp_path, monkeypatch)
    config['backup'] = dict(mount=str(mount), volume_uuid='expected', password_file='unused')
    calls = []
    class Runtime:
        def running_services(self): return ['app']
        def compose(self, *args): calls.append(args)
    monkeypatch.setattr(backup.shutil, 'disk_usage', lambda path: type('Usage', (), {'free': 0})())
    with pytest.raises(ValueError, match='insufficient private staging capacity'):
        backup.backup(config, Runtime())
    assert calls == [('stop', 'app'), ('start', 'app')]
    assert not list(Path(config['state_dir']).glob('snapshot-*'))
    assert json.loads((Path(config['state_dir']) / 'status/backup-status.json').read_text())['state'] == 'failed'


def test_snapshot_preserves_git_dirty_files_and_sqlite(tmp_path):
    from tools.local_backup import snapshot, verify_snapshot
    source = tmp_path / 'source'
    source.mkdir()
    subprocess.run(['git', 'init', '-q', str(source)], check=True)
    (source / 'note').write_text('uncommitted')
    with sqlite3.connect(source / 'books.db') as db:
        db.execute('create table facts (value text)')
        db.execute("insert into facts values ('kept')")
    target = tmp_path / 'snapshot'
    snapshot(source, target)
    verify_snapshot(target)
    assert (target / 'vault/note').read_text() == 'uncommitted'
    with sqlite3.connect(target / 'vault/books.db') as db:
        assert db.execute('select value from facts').fetchone() == ('kept',)
    (target / 'vault/note').write_text('tampered')
    with pytest.raises(ValueError):
        verify_snapshot(target)


@pytest.mark.parametrize('kind', ['external-link', 'linked-git', 'fifo', 'external-worktree'])
def test_snapshot_refuses_incomplete_layouts(tmp_path, kind):
    from tools.local_backup import snapshot
    source = tmp_path / 'source'
    source.mkdir()
    subprocess.run(['git', 'init', '-q', str(source)], check=True)
    if kind == 'external-link':
        (source / 'escape').symlink_to(tmp_path)
    elif kind == 'linked-git':
        (source / '.git/objects/info/alternates').write_text('/elsewhere')
    elif kind == 'external-worktree':
        subprocess.run(['git', '-C', str(source), 'config', 'core.worktree', str(tmp_path)], check=True)
    else:
        import os
        os.mkfifo(source / 'pipe')
    with pytest.raises(ValueError):
        snapshot(source, tmp_path / 'snapshot')


def test_snapshot_detects_source_change(tmp_path, monkeypatch):
    from tools import local_backup as backup
    source = tmp_path / 'source'
    source.mkdir()
    subprocess.run(['git', 'init', '-q', str(source)], check=True)
    (source / 'note').write_text('before')
    original = backup.copy_regular
    def changed(src, dst):
        original(src, dst)
        if src.name == 'note':
            src.write_text('after')
    monkeypatch.setattr(backup, 'copy_regular', changed)
    with pytest.raises(ValueError):
        backup.snapshot(source, tmp_path / 'snapshot')


@pytest.mark.parametrize('change', [{'VolumeUUID': 'other'}, {'Internal': True}, {'VirtualOrPhysical': 'Virtual'}, {'Mounted': False}])
def test_external_identity_rejected_before_write(tmp_path, monkeypatch, change):
    from tools import local_backup as backup
    mount = tmp_path / 'drive'
    mount.mkdir()
    info = dict(VolumeUUID='expected', Internal=False, VirtualOrPhysical='Physical', Mounted=True, MountPoint=str(mount))
    info.update(change)
    monkeypatch.setattr(backup, 'disk_info', lambda path: info)
    with pytest.raises(ValueError):
        backup.validate_volume({'mount': str(mount), 'volume_uuid': 'expected'})
    assert list(mount.iterdir()) == []


def test_backup_lock_refuses_second_job(tmp_path):
    from tools.local_service import operation_lock
    with operation_lock(tmp_path):
        with pytest.raises(ValueError):
            with operation_lock(tmp_path):
                pass


def backup_config(tmp_path, monkeypatch):
    from tools import local_backup as backup
    from tools.local_service import setup
    source = tmp_path / 'source'
    source.mkdir()
    subprocess.run(['git', 'init', '-q', str(source)], check=True)
    (source / 'note').write_text('preserved')
    repo = tmp_path / 'repo'
    repo.mkdir()
    config = setup(tmp_path / 'state', source, repo, 'Test', 'test@example.invalid')
    mount = tmp_path / 'external'
    mount.mkdir()
    monkeypatch.setattr(backup, 'disk_info', lambda path: dict(VolumeUUID='expected', Internal=False,
        VirtualOrPhysical='Physical', Mounted=True, MountPoint=str(mount)))
    return config, mount


def test_real_restic_encrypted_backup_and_isolated_restore(tmp_path, monkeypatch):
    import shutil
    import sys
    from tools import local_backup as backup
    if not shutil.which('restic'):
        pytest.skip('restic executable unavailable')
    config, mount = backup_config(tmp_path, monkeypatch)
    caddy = Path(config['state_dir']) / 'caddy'
    caddy.mkdir(exist_ok=True)
    (caddy / 'authority').write_text('synthetic TLS identity')
    (Path(config['state_dir']) / 'auth/credentials').write_text('synthetic credentials')
    if sys.platform == 'darwin':
        for path in (Path(config['vault']), Path(config['vault']) / 'note', caddy):
            subprocess.run(['/usr/bin/xattr', '-wx', 'com.apple.test-oneos', 'ff000a', str(path)], check=True)
    backup.setup_backup(config, mount, True)
    class StoppedRuntime:
        def running_services(self): return []
        def compose(self, *args): pytest.fail('stopped service must remain stopped')
    backup.backup(config, StoppedRuntime())
    (Path(config['vault']) / 'note').write_text('newest preserved')
    backup.backup(config, StoppedRuntime())
    restored = backup.restore_check(config)
    assert (restored / 'snapshot/vault/note').read_text() == 'newest preserved'
    assert (restored / 'snapshot/deployment/auth/credentials').read_text() == 'synthetic credentials'
    assert (restored / 'snapshot/deployment/caddy/authority').read_text() == 'synthetic TLS identity'
    assert json.loads((restored / 'snapshot/deployment/config.json').read_text())['vault'] == config['vault']
    status = json.loads((Path(config['state_dir']) / 'status/backup-status.json').read_text())
    assert status['state'] == 'ok'
    assert status['last_success'] > 0
    assert not list((mount / 'oneos-backup').glob('snapshot-*'))
    assert restored.is_relative_to(Path(config['state_dir']))
    assert not (restored / 'snapshot/deployment/backup-key').exists()
    if sys.platform == 'darwin':
        for path in (restored / 'snapshot/vault', restored / 'snapshot/vault/note', restored / 'snapshot/deployment/caddy'):
            value = subprocess.run(['/usr/bin/xattr', '-px', 'com.apple.test-oneos', str(path)], check=True, capture_output=True).stdout
            assert bytes.fromhex(value.decode()) == b'\xff\x00\x0a'


def test_setup_backup_rejects_existing_configuration_before_creating_key(tmp_path, monkeypatch):
    from tools import local_backup as backup

    config, mount = backup_config(tmp_path, monkeypatch)
    config['backup'] = {'mount': '/existing', 'volume_uuid': 'existing'}
    key = Path(config['state_dir']) / 'backup-key'
    monkeypatch.setattr(backup, 'disk_info', lambda path: pytest.fail('must reject before drive access'))
    with pytest.raises(ValueError, match='backup already configured'):
        backup.setup_backup(config, mount, False)
    assert not key.exists()


@pytest.mark.parametrize('invalid_last_success',
                         ['not-a-number', True, 0, -1, float('nan'), float('inf'), 1001, 10 ** 400])
def test_due_backup_treats_invalid_or_future_success_as_overdue(tmp_path, monkeypatch,
                                                               invalid_last_success):
    from tools import local_backup as backup
    from tools.local_service import write_status

    config, mount = backup_config(tmp_path, monkeypatch)
    config['backup'] = {'mount': str(mount), 'volume_uuid': 'expected',
                        'password_file': str(Path(config['state_dir']) / 'backup-key')}
    write_status(config, 'ok', invalid_last_success)
    monkeypatch.setattr(backup.time, 'time', lambda: 1000)
    attempted = []

    def stop_after_attempt(backup_config):
        attempted.append(backup_config)
        raise ValueError('synthetic catch-up attempt')

    monkeypatch.setattr(backup, 'validate_volume', stop_after_attempt)
    with pytest.raises(ValueError, match='synthetic catch-up attempt'):
        backup.backup(config, object(), due_only=True)
    assert attempted == [config['backup']]


def test_due_backup_treats_non_mapping_status_as_overdue(tmp_path, monkeypatch):
    from tools import local_backup as backup

    config, mount = backup_config(tmp_path, monkeypatch)
    config['backup'] = {'mount': str(mount), 'volume_uuid': 'expected',
                        'password_file': str(Path(config['state_dir']) / 'backup-key')}
    status = Path(config['state_dir']) / 'status/backup-status.json'
    status.write_text('[]')
    monkeypatch.setattr(backup.time, 'time', lambda: 1000)
    attempted = []

    def stop_after_attempt(backup_config):
        attempted.append(backup_config)
        raise ValueError('synthetic catch-up attempt')

    monkeypatch.setattr(backup, 'validate_volume', stop_after_attempt)
    with pytest.raises(ValueError, match='synthetic catch-up attempt'):
        backup.backup(config, object(), due_only=True)
    assert attempted == [config['backup']]


def test_interrupted_backup_restores_service_and_retains_last_success(tmp_path, monkeypatch):
    from tools import local_backup as backup
    from tools.local_service import write_status
    config, mount = backup_config(tmp_path, monkeypatch)
    config['backup'] = dict(mount=str(mount), volume_uuid='expected', password_file=str(tmp_path / 'unused'))
    write_status(config, 'ok', 123)
    calls = []
    class RunningRuntime:
        def running_services(self): return ['app', 'caddy']
        def compose(self, *args): calls.append(args)
    def interrupted(*args): raise KeyboardInterrupt
    monkeypatch.setattr(backup, 'snapshot', interrupted)
    with pytest.raises(KeyboardInterrupt):
        backup.backup(config, RunningRuntime())
    assert calls == [('stop', 'app', 'caddy'), ('start', 'app', 'caddy')]
    status = json.loads((Path(config['state_dir']) / 'status/backup-status.json').read_text())
    assert status['state'] == 'failed'
    assert status['last_success'] == 123


def test_missing_mount_never_created(tmp_path, monkeypatch):
    from tools import local_backup as backup
    mount = tmp_path / 'absent'
    with pytest.raises(ValueError):
        with backup.external_session(dict(mount=str(mount), volume_uuid='expected')):
            pytest.fail('must refuse')
    assert not mount.exists()


def test_pinned_external_directory_survives_mount_path_replacement(tmp_path, monkeypatch):
    from tools import local_backup as backup
    config, mount = backup_config(tmp_path, monkeypatch)
    with backup.external_session(dict(mount=str(mount), volume_uuid='expected')):
        mount.rename(tmp_path / 'detached')
        mount.mkdir()
        Path('proof').write_text('pinned')
    assert not (mount / 'oneos-backup').exists()
    assert (tmp_path / 'detached/oneos-backup/proof').read_text() == 'pinned'


def test_restic_refuses_repository_on_another_device(tmp_path, monkeypatch):
    import os
    from tools import local_backup as backup

    config, mount = backup_config(tmp_path, monkeypatch)
    repository = mount / 'oneos-backup/repository'
    repository.mkdir(parents=True)
    key = Path(config['state_dir']) / 'backup-key'
    key.write_text('synthetic-key\n')
    key.chmod(0o600)
    config['backup'] = dict(mount=str(mount), volume_uuid='expected', password_file=str(key))
    original_open = os.open
    original_fstat = os.fstat
    repository_fds = set()

    def tracked_open(path, flags, *args, **kwargs):
        descriptor = original_open(path, flags, *args, **kwargs)
        if path == 'repository':
            repository_fds.add(descriptor)
        return descriptor

    def separate_device(descriptor):
        info = original_fstat(descriptor)
        if descriptor not in repository_fds:
            return info
        values = list(info)
        values[2] += 1
        return os.stat_result(values)

    monkeypatch.setattr(backup.os, 'open', tracked_open)
    monkeypatch.setattr(backup.os, 'fstat', separate_device)
    monkeypatch.setattr(backup.subprocess, 'run',
                        lambda *args, **kwargs: pytest.fail('wrong-device repository must be rejected first'))
    with pytest.raises(ValueError, match='repository is on another filesystem'):
        backup.restic(config, 'check')


def test_restic_closes_repository_when_device_validation_fails(tmp_path, monkeypatch):
    import os
    from tools import local_backup as backup

    config, mount = backup_config(tmp_path, monkeypatch)
    (mount / 'oneos-backup/repository').mkdir(parents=True)
    key = Path(config['state_dir']) / 'backup-key'
    key.write_text('synthetic-key\n')
    key.chmod(0o600)
    config['backup'] = dict(mount=str(mount), volume_uuid='expected', password_file=str(key))
    original_open = os.open
    original_fstat = os.fstat
    original_close = os.close
    repository_fds = set()
    closed_fds = set()

    def tracked_open(path, flags, *args, **kwargs):
        descriptor = original_open(path, flags, *args, **kwargs)
        if path == 'repository':
            repository_fds.add(descriptor)
        return descriptor

    def failed_validation(descriptor):
        if descriptor in repository_fds:
            raise OSError('synthetic device lookup failure')
        return original_fstat(descriptor)

    def tracked_close(descriptor):
        if descriptor in repository_fds:
            closed_fds.add(descriptor)
        return original_close(descriptor)

    monkeypatch.setattr(backup.os, 'open', tracked_open)
    monkeypatch.setattr(backup.os, 'fstat', failed_validation)
    monkeypatch.setattr(backup.os, 'close', tracked_close)
    with pytest.raises(OSError, match='synthetic device lookup failure'):
        backup.restic(config, 'check')
    assert closed_fds == repository_fds


def test_copy_regular_preserves_xattrs_on_a_read_only_file(tmp_path):
    import sys
    from tools.local_backup import copy_regular
    from tools.local_metadata import read_xattrs, write_xattrs

    source = tmp_path / 'source'
    source.write_bytes(b'contents')
    attribute = ('com.apple.test-oneos' if sys.platform == 'darwin' else 'user.oneos-test').encode().hex()
    try:
        write_xattrs(source, {attribute: b'preserved'.hex()})
    except OSError:
        pytest.skip('test filesystem does not support user extended attributes')
    source.chmod(0o444)
    destination = tmp_path / 'destination'
    copy_regular(source, destination)
    assert destination.stat().st_mode & 0o777 == 0o444
    assert read_xattrs(destination) == {attribute: b'preserved'.hex()}


def test_partial_restic_backup_never_prunes_or_claims_success(tmp_path, monkeypatch):
    from tools import local_backup as backup
    config, mount = backup_config(tmp_path, monkeypatch)
    config['backup'] = dict(mount=str(mount), volume_uuid='expected', password_file='unused')
    calls = []
    def partial(config, *args, **kwargs):
        calls.append(args[0])
        raise subprocess.CalledProcessError(3, ['restic'])
    monkeypatch.setattr(backup, 'restic', partial)
    class Runtime:
        def running_services(self): return []
    with pytest.raises(subprocess.CalledProcessError):
        backup.backup(config, Runtime())
    assert calls == ['backup']
    status = json.loads((Path(config['state_dir']) / 'status/backup-status.json').read_text())
    assert status['state'] == 'failed'
    assert status['last_success'] is None
    assert list(Path(config['state_dir']).glob('snapshot-*'))
    assert list(mount.iterdir()) == []


def test_unsupported_mac_acl_refused(tmp_path):
    import sys
    from tools.local_backup import inventory
    if sys.platform != 'darwin': pytest.skip('macOS metadata')
    note = tmp_path / 'note'
    note.write_text('keep metadata')
    subprocess.run(['chmod', '+a', 'everyone allow read', str(note)], check=True)
    with pytest.raises(ValueError):
        inventory(tmp_path)


def test_mac_xattrs_preserved_including_root_and_tamper_detected(tmp_path):
    import sys
    from tools.local_backup import snapshot, verify_snapshot
    if sys.platform != 'darwin': pytest.skip('macOS metadata')
    source = tmp_path / 'source'
    source.mkdir()
    subprocess.run(['git', 'init', '-q', str(source)], check=True)
    (source / 'folder').mkdir()
    note = source / 'folder/note'
    note.write_text('keep all metadata')
    for path in (source, source / 'folder', note):
        subprocess.run(['/usr/bin/xattr', '-wx', 'com.apple.test-oneos', '0001ff80', str(path)], check=True)
    target = tmp_path / 'snapshot'
    snapshot(source, target)
    verify_snapshot(target)
    for relative in ('.', 'folder', 'folder/note'):
        value = subprocess.run(['/usr/bin/xattr', '-px', 'com.apple.test-oneos', str(target / 'vault' / relative)], check=True, capture_output=True).stdout
        assert bytes.fromhex(value.decode()) == b'\x00\x01\xff\x80'
    subprocess.run(['/usr/bin/xattr', '-w', 'com.apple.test-oneos', 'changed', str(target / 'vault/folder/note')], check=True)
    with pytest.raises(ValueError):
        verify_snapshot(target)


def test_mac_xattrs_symlink_metadata_does_not_follow_target(tmp_path):
    import sys
    from tools.local_metadata import read_xattrs, write_xattrs
    if sys.platform != 'darwin': pytest.skip('macOS metadata')
    target = tmp_path / 'target'
    target.write_text('keep')
    link = tmp_path / 'link'
    link.symlink_to('target')
    name = b'com.apple.test-oneos'.hex()
    write_xattrs(target, {name: b'target'.hex()})
    write_xattrs(link, {name: b'link'.hex()})
    assert read_xattrs(target) == {name: b'target'.hex()}
    assert read_xattrs(link) == {name: b'link'.hex()}
