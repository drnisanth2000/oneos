import json
import pytest


def test_missing_vault_is_never_created(tmp_path):
    from tools.local_service import setup
    with pytest.raises((ValueError, FileNotFoundError)):
        setup(tmp_path / 'state', tmp_path / 'missing', tmp_path, 'Owner', 'owner@example.invalid')
    assert not (tmp_path / 'missing').exists()


def test_private_config_rejects_symlink_and_public_permissions(tmp_path):
    from tools.local_service import load_config
    state = tmp_path / 'state'
    state.mkdir(mode=0o700)
    config = state / 'config.json'
    config.write_text('{}')
    config.chmod(0o644)
    with pytest.raises(ValueError):
        load_config(state)
    config.unlink()
    config.symlink_to(tmp_path / 'elsewhere')
    with pytest.raises(ValueError):
        load_config(state)


def test_login_starts_after_a_previous_manual_stop(tmp_path, monkeypatch):
    from tools import local_service as service
    (tmp_path / 'manual-stop').write_text('')
    started = []
    monkeypatch.setattr(service, 'start', lambda *args: started.append(True))
    service.login_start({'state_dir': str(tmp_path)})
    assert started == [True]


@pytest.mark.parametrize('running', [True, False])
def test_backup_pause_restores_previous_state_even_on_interrupt(running):
    from tools.local_service import paused
    calls = []
    class Runtime:
        def running_services(self): return ['app'] if running else []
        def compose(self, *args): calls.append(args)
    with pytest.raises(KeyboardInterrupt):
        with paused(Runtime()):
            raise KeyboardInterrupt
    assert calls == ([('stop', 'app'), ('start', 'app')] if running else [])


def test_setup_refuses_private_state_inside_public_repository(tmp_path):
    import subprocess
    from tools.local_service import setup
    vault = tmp_path / 'vault'
    vault.mkdir()
    subprocess.run(['git', 'init', '-q', str(vault)], check=True)
    repo = tmp_path / 'repo'
    repo.mkdir()
    with pytest.raises(ValueError):
        setup(repo / 'private-state', vault, repo, 'Test', 'test@example.invalid')
    assert not (repo / 'private-state').exists()


def test_compose_uses_explicit_context_and_ignores_ambient_overrides(monkeypatch):
    from tools import local_service as service
    calls = []
    monkeypatch.setenv('DOCKER_HOST', 'unrelated-daemon')
    monkeypatch.setenv('COMPOSE_PROJECT_NAME', 'unrelated-project')
    monkeypatch.setattr(service.shutil, 'which', lambda name: '/synthetic/docker-compose')
    monkeypatch.setattr(service.subprocess, 'run', lambda args, **kwargs: calls.append((args, kwargs)))
    service.Runtime(dict(vault='/synthetic/vault', state_dir='/synthetic/state', repo_dir='/synthetic/repo',
                         uid=501, gid=20, git_name='Test', git_email='test@example.invalid')).compose('stop')
    args, options = calls[0]
    assert args[:3] == ['docker-compose', '--project-name', 'oneos']
    assert args[3:5] == ['--env-file', '/dev/null']
    assert args[-1] == 'stop'
    assert options['env']['DOCKER_CONTEXT'] == 'colima-oneos'
    assert 'DOCKER_HOST' not in options['env']
    assert 'COMPOSE_PROJECT_NAME' not in options['env']
    assert not options.get('shell', False)


def test_login_waits_for_backup_lock(tmp_path, monkeypatch):
    import threading
    from tools import local_service as service
    started = threading.Event()
    entered = threading.Event()
    monkeypatch.setattr(service, 'start', lambda config: started.set())
    def login():
        entered.set()
        service.run_login({'state_dir': str(tmp_path)})
    with service.operation_lock(tmp_path):
        worker = threading.Thread(target=login)
        worker.start()
        assert entered.wait(1)
        assert not started.wait(0.05)
    worker.join(2)
    assert not worker.is_alive()
    assert started.is_set()


def test_delayed_login_respects_new_stop_request(tmp_path, monkeypatch):
    from tools import local_service as service
    service.write_json(tmp_path / 'manual-stop', {'stopped_at': 200})
    monkeypatch.setattr(service, 'start', lambda config: pytest.fail('new stop wins'))
    service.login_start({'state_dir': str(tmp_path)}, requested_at=100)


def diagnostic_config(tmp_path):
    state = tmp_path / 'state'
    state.mkdir(mode=0o700)
    for child in ('auth', 'status'):
        (state / child).mkdir(mode=0o700)
    vault = tmp_path / 'vault'
    vault.mkdir()
    (vault / '.git').mkdir()
    repo = tmp_path / 'repo'
    repo.mkdir()
    return dict(state_dir=str(state), vault=str(vault), repo_dir=str(repo), uid=501, gid=20,
                git_name='Test', git_email='test@example.invalid')


def test_diagnostics_report_disconnected_backup_and_missing_enrollment_without_paths(tmp_path, monkeypatch):
    from tools import local_service as service
    from tools import local_backup
    config = diagnostic_config(tmp_path)
    config['backup'] = {'mount': '/private/sensitive-volume', 'volume_uuid': 'private-uuid'}
    monkeypatch.setattr(service.shutil, 'which', lambda name: '/synthetic/tool')
    monkeypatch.setattr(service.subprocess, 'run', lambda *args, **kwargs: type('Result', (), {'stdout': '', 'returncode': 0})())
    monkeypatch.setattr(service.Runtime, 'running_services', lambda self: ['app', 'caddy'])
    monkeypatch.setattr(local_backup, 'validate_volume', lambda config: (_ for _ in ()).throw(ValueError('private-uuid')))
    result = service.diagnostics(config)
    assert result['backup']['state'] == 'disconnected'
    assert result['authentication']['state'] == 'not_enrolled'
    assert result['services']['state'] == 'running'
    output = json.dumps(result)
    assert str(tmp_path) not in output
    assert 'private-uuid' not in output
    assert '/private/sensitive-volume' not in output


def test_diagnostics_read_enrollment_without_mutating_database(tmp_path, monkeypatch):
    import sqlite3
    from tools import local_service as service
    config = diagnostic_config(tmp_path)
    path = tmp_path / 'state/auth/owner.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('create table owner(id integer, password text, secret text, counter integer)')
        db.execute('insert into owner values(1,?,?,1)', ('$argon2id$synthetic', 'A' * 32))
    path.chmod(0o600)
    before = path.read_bytes()
    result = service.authentication_diagnostic(config)
    assert result['state'] == 'enrolled'
    assert path.read_bytes() == before
    assert list(path.parent.iterdir()) == [path]


def test_diagnostics_missing_vault_is_actionable(tmp_path, monkeypatch):
    from tools import local_service as service
    config = diagnostic_config(tmp_path)
    config['vault'] = str(tmp_path / 'missing-sensitive-vault')
    monkeypatch.setattr(service.shutil, 'which', lambda name: None)
    result = service.diagnostics(config)
    assert result['vault']['state'] == 'unavailable'
    assert result['runtime']['state'] == 'unavailable'
    assert result['backup']['state'] == 'not_configured'
    assert 'missing-sensitive-vault' not in json.dumps(result)


@pytest.mark.parametrize(('state', 'last', 'expected'), [('ok', 1, 'missed'), ('failed', 1, 'failed'), ('never', None, 'never')])
def test_diagnostics_report_backup_freshness(tmp_path, monkeypatch, state, last, expected):
    from tools import local_service as service
    from tools import local_backup
    config = diagnostic_config(tmp_path)
    config['backup'] = {}
    service.write_status(config, state, last)
    monkeypatch.setattr(service.shutil, 'which', lambda name: None)
    monkeypatch.setattr(service.Runtime, 'compose', lambda *args: (_ for _ in ()).throw(OSError()))
    monkeypatch.setattr(local_backup, 'validate_volume', lambda config: None)
    result = service.diagnostics(config)
    assert result['backup']['state'] == expected
    assert 'Run backup' in result['backup']['action']


def test_safe_error_reasons_never_echo_unrecognized_private_text():
    import subprocess
    from tools.local_service import safe_failure
    assert safe_failure(ValueError('copy the private backup key offline before confirming setup'))['code'] == 'offline_key_required'
    assert safe_failure(ValueError('macOS ACLs require a metadata-aware backup plan'))['code'] == 'unsupported_acl'
    partial = subprocess.CalledProcessError(3, ['restic', '/private/sensitive-path'], stderr='private secret')
    assert safe_failure(partial)['code'] == 'partial_backup'
    assert 'private' not in json.dumps(safe_failure(ValueError('private secret path')))
    assert 'sensitive-path' not in json.dumps(safe_failure(partial))
