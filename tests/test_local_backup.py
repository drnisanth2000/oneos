import json
import sqlite3
import subprocess
from pathlib import Path

import pytest


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
    from tools import local_backup as backup
    if not shutil.which('restic'):
        pytest.skip('restic executable unavailable')
    config, mount = backup_config(tmp_path, monkeypatch)
    caddy = Path(config['state_dir']) / 'caddy'
    caddy.mkdir(exist_ok=True)
    (caddy / 'authority').write_text('synthetic TLS identity')
    (Path(config['state_dir']) / 'auth/credentials').write_text('synthetic credentials')
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


@pytest.mark.parametrize('metadata', ['xattr', 'acl'])
def test_unsupported_mac_metadata_refused(tmp_path, metadata):
    import os
    import sys
    from tools.local_backup import inventory
    if sys.platform != 'darwin': pytest.skip('macOS metadata')
    note = tmp_path / 'note'
    note.write_text('keep metadata')
    if metadata == 'xattr':
        subprocess.run(['/usr/bin/xattr', '-w', 'com.apple.test-oneos', 'keep', str(note)], check=True)
    else:
        subprocess.run(['chmod', '+a', 'everyone allow read', str(note)], check=True)
    with pytest.raises(ValueError):
        inventory(tmp_path)
