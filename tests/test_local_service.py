import json
import pytest


@pytest.mark.parametrize('command', ['up', 'stop', 'start', 'logs'])
@pytest.mark.parametrize('interactive', [False, True])
def test_compose_lifecycle_has_deadline(tmp_path, monkeypatch, command, interactive):
    import subprocess
    from tools import local_service as service
    config = diagnostic_config(tmp_path)
    def hung_child(args, **kwargs):
        timeout = kwargs.get('timeout')
        assert isinstance(timeout, (int, float)) and 0 < timeout <= 900
        raise subprocess.TimeoutExpired(args, timeout)
    monkeypatch.setattr(service.subprocess, 'run', hung_child)
    with pytest.raises(subprocess.TimeoutExpired):
        service.Runtime(config).compose(command, interactive=interactive)


def test_interactive_enrollment_has_no_deadline(tmp_path, monkeypatch):
    import subprocess
    from tools import local_service as service
    def interactive_child(args, **kwargs):
        assert kwargs.get('timeout') is None
        assert kwargs['capture_output'] is False
        return subprocess.CompletedProcess(args, 0)
    monkeypatch.setattr(service.subprocess, 'run', interactive_child)
    assert service.Runtime(diagnostic_config(tmp_path)).compose('exec', 'app', 'python', '-m',
                                                               'app.auth_admin', 'enroll', interactive=True).returncode == 0


def test_colima_start_deadline_keeps_manual_stop_marker(tmp_path, monkeypatch):
    import subprocess
    from tools import local_service as service
    config = diagnostic_config(tmp_path)
    marker = tmp_path / 'state/manual-stop'
    marker.write_text('preserved')
    def hung_child(args, **kwargs):
        timeout = kwargs.get('timeout')
        assert isinstance(timeout, (int, float)) and 0 < timeout <= 900
        raise subprocess.TimeoutExpired(args, timeout)
    monkeypatch.setattr(service.subprocess, 'run', hung_child)
    with pytest.raises(subprocess.TimeoutExpired):
        service.start(config)
    assert marker.read_text() == 'preserved'


def test_timeout_restores_paused_services_and_releases_operation_lock(tmp_path, monkeypatch, capsys):
    import subprocess
    from tools import local_service as service
    config = diagnostic_config(tmp_path)
    state = tmp_path / 'state'
    service.write_json(state / 'config.json', config)
    calls = []
    def child(command, **kwargs):
        timeout = kwargs.get('timeout')
        assert isinstance(timeout, (int, float)) and 0 < timeout <= 900
        if 'ps' in command:
            return subprocess.CompletedProcess(command, 0, stdout='app\ncaddy\n')
        calls.append(command[-3:])
        if 'stop' in command:
            raise subprocess.TimeoutExpired(['synthetic-private-command'], timeout, stderr='synthetic-secret')
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(service.subprocess, 'run', child)
    with pytest.raises(subprocess.TimeoutExpired):
        with service.operation_lock(state), service.paused(service.Runtime(config)):
            pytest.fail('stop timed out')
    assert calls == [['stop', 'app', 'caddy'], ['start', 'app', 'caddy']]
    with service.operation_lock(state):
        pass
    assert service.main(['--state-dir', str(state), 'stop']) == 1
    output = capsys.readouterr()
    assert json.loads(output.err)['code'] == 'operation_failed'
    assert 'synthetic-secret' not in output.err
    assert 'synthetic-private-command' not in output.err


def test_backup_git_lock_timeout_is_sanitized_and_releases_operation_lock(
    tmp_path, monkeypatch, capsys
):
    import subprocess
    from app.git_transaction import GitTransactionFailure
    from tools import local_backup, local_service as service

    config = diagnostic_config(tmp_path)
    state = tmp_path / 'state'
    service.write_json(state / 'config.json', config)
    timeout = subprocess.TimeoutExpired(
        ['git', 'rev-parse', '/private/sensitive-path'], 30,
        stderr='private secret',
    )

    def failed_backup(*_args, **_kwargs):
        raise GitTransactionFailure('private vault lock failure') from timeout

    monkeypatch.setattr(local_backup, 'backup', failed_backup)

    assert service.main(['--state-dir', str(state), 'backup']) == 1
    output = capsys.readouterr()
    assert json.loads(output.err)['code'] == 'operation_failed'
    assert 'private' not in output.err
    assert 'sensitive-path' not in output.err
    with service.operation_lock(state):
        pass


def test_direct_compose_validation_rebuilds_both_images():
    from pathlib import Path

    readme = (Path(__file__).resolve().parents[1] / 'deploy/README.md').read_text()
    assert 'docker compose build --pull app caddy' in readme


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
    service.write_json(tmp_path / 'manual-stop', {'stopped_at': 100})
    started = []
    monkeypatch.setattr(service, 'start', lambda *args: started.append(True))
    service.login_start({'state_dir': str(tmp_path)}, requested_at=200)
    assert started == [True]


def test_start_bounds_compose_health_wait(tmp_path, monkeypatch):
    from tools import local_service as service
    calls = []
    config = dict(state_dir=str(tmp_path), vault='/synthetic/vault', repo_dir='/synthetic/repo',
                  uid=501, gid=20, git_name='Test', git_email='test@example.invalid')
    monkeypatch.setattr(service.subprocess, 'run', lambda *args, **kwargs: None)
    monkeypatch.setattr(service.Runtime, 'compose', lambda self, *args: calls.append(args))
    service.start(config)
    assert calls == [('up', '-d', '--build', '--wait', '--wait-timeout', '120')]


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


def test_setup_refuses_vault_inside_container_build_context(tmp_path):
    import subprocess
    from tools.local_service import setup

    repo = tmp_path / 'repo'
    repo.mkdir()
    vault = repo / 'private-vault'
    vault.mkdir()
    subprocess.run(['git', 'init', '-q', str(vault)], check=True)
    state = tmp_path / 'state'
    with pytest.raises(ValueError, match='vault must be separate'):
        setup(state, vault, repo, 'Test', 'test@example.invalid')
    assert not state.exists()


def test_setup_preflights_existing_launcher_before_configuration(tmp_path):
    import subprocess
    from tools.local_service import setup

    vault = tmp_path / 'vault'
    vault.mkdir()
    subprocess.run(['git', 'init', '-q', str(vault)], check=True)
    repo = tmp_path / 'repo'
    repo.mkdir()
    state = tmp_path / 'state'
    state.mkdir(mode=0o700)
    launcher = state / 'OneOS.command'
    launcher.write_text('preserve existing launcher')
    with pytest.raises((ValueError, FileExistsError)):
        setup(state, vault, repo, 'Test', 'test@example.invalid')
    assert launcher.read_text() == 'preserve existing launcher'
    assert not (state / 'config.json').exists()
    assert not (state / 'auth').exists()


def test_setup_refuses_existing_nonempty_auth_or_status_directory(tmp_path):
    import subprocess
    from tools.local_service import setup

    vault = tmp_path / 'vault'
    vault.mkdir()
    subprocess.run(['git', 'init', '-q', str(vault)], check=True)
    repo = tmp_path / 'repo'
    repo.mkdir()
    state = tmp_path / 'state'
    state.mkdir(mode=0o700)
    for path in (state / 'auth', state / 'status'):
        path.mkdir(mode=0o700)
        (path / 'stale.txt').write_text('retain private content')

    with pytest.raises(ValueError, match='refuse stale private state'):
        setup(state, vault, repo, 'Test', 'test@example.invalid')


def test_main_returns_sanitized_interrupt_json(tmp_path, monkeypatch, capsys):
    from tools import local_service as service

    state = tmp_path / 'state'
    state.mkdir(mode=0o700)
    service.write_json(state / 'config.json', dict(
        state_dir=str(state), vault='/vault', repo_dir='/repo', uid=501, gid=20, git_name='Owner',
        git_email='owner@example.invalid',
    ))
    monkeypatch.setattr(service.subprocess, 'run', lambda *args, **kwargs: (_ for _ in ()).throw(KeyboardInterrupt))
    assert service.main(['--state-dir', str(state), 'status']) == 1
    output = capsys.readouterr()
    assert json.loads(output.err) == {'code': 'operation_cancelled', 'message': 'Operation was interrupted by user input.'}


def test_setup_preserves_preexisting_dangling_config_symlink(tmp_path):
    import subprocess
    from tools.local_service import setup

    vault = tmp_path / 'vault'
    vault.mkdir()
    subprocess.run(['git', 'init', '-q', str(vault)], check=True)
    repo = tmp_path / 'repo'
    repo.mkdir()
    state = tmp_path / 'state'
    state.mkdir(mode=0o700)
    config = state / 'config.json'
    config.symlink_to(tmp_path / 'missing-config-target')

    with pytest.raises(ValueError, match='configuration already exists'):
        setup(state, vault, repo, 'Test', 'test@example.invalid')

    assert config.is_symlink()
    assert config.readlink() == tmp_path / 'missing-config-target'


def test_setup_removes_only_its_artifacts_after_late_write_failure_and_retries(tmp_path, monkeypatch):
    import subprocess
    from tools import local_service as service

    vault = tmp_path / 'vault'
    vault.mkdir()
    subprocess.run(['git', 'init', '-q', str(vault)], check=True)
    repo = tmp_path / 'repo'
    repo.mkdir()
    state = tmp_path / 'state'
    state.mkdir(mode=0o700)
    preserved = state / 'preserved.txt'
    preserved.write_text('keep me')
    real_write_status = service.write_status
    fail_once = True

    def interrupted_status(config, status, last_success, **kwargs):
        nonlocal fail_once
        real_write_status(config, status, last_success, **kwargs)
        if fail_once:
            fail_once = False
            raise InterruptedError('synthetic setup interruption')

    monkeypatch.setattr(service, 'write_status', interrupted_status)
    with pytest.raises(InterruptedError, match='setup interruption'):
        service.setup(state, vault, repo, 'Test', 'test@example.invalid')

    assert preserved.read_text() == 'keep me'
    assert not (state / 'config.json').exists()
    assert not (state / 'OneOS.command').exists()
    assert not (state / 'status/backup-status.json').exists()

    config = service.setup(state, vault, repo, 'Test', 'test@example.invalid')
    assert config['vault'] == str(vault)
    assert (state / 'config.json').is_file()
    assert (state / 'OneOS.command').is_file()
    assert (state / 'status/backup-status.json').is_file()


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


def test_setup_holds_operation_lock_until_all_private_state_exists(tmp_path, monkeypatch):
    import subprocess
    import threading
    from tools import local_service as service

    vault = tmp_path / 'vault'
    vault.mkdir()
    subprocess.run(['git', 'init', '-q', str(vault)], check=True)
    repo = tmp_path / 'repo'
    repo.mkdir()
    state = tmp_path / 'state'
    config_written = threading.Event()
    release_setup = threading.Event()
    login_started = threading.Event()
    original_write_json = service.write_json

    def delayed_config_write(path, value):
        original_write_json(path, value)
        if path.name == 'config.json':
            config_written.set()
            assert release_setup.wait(2)

    monkeypatch.setattr(service, 'write_json', delayed_config_write)
    monkeypatch.setattr(service, 'start', lambda config: login_started.set())
    worker = threading.Thread(target=service.setup,
                              args=(state, vault, repo, 'Test', 'test@example.invalid'))
    worker.start()
    assert config_written.wait(1)
    login = threading.Thread(target=service.run_login, args=({'state_dir': str(state)},))
    login.start()
    assert not login_started.wait(0.05)
    release_setup.set()
    worker.join(2)
    login.join(2)
    assert not worker.is_alive()
    assert not login.is_alive()
    assert login_started.is_set()


def test_install_login_preflights_all_destinations_before_writing(tmp_path, monkeypatch):
    from tools import local_service as service

    launch_agents = tmp_path / 'Library/LaunchAgents'
    launch_agents.mkdir(parents=True)
    (launch_agents / 'local.oneos.backup.plist').write_bytes(b'preserve')
    monkeypatch.setattr(service.Path, 'home', classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(service.subprocess, 'run', lambda *args, **kwargs: pytest.fail('must preflight before bootstrap'))
    config = {'state_dir': '/synthetic/state', 'repo_dir': '/synthetic/repo'}
    with pytest.raises(ValueError, match='already exists'):
        service.install_login(config)
    assert not (launch_agents / 'local.oneos.login.plist').exists()
    assert (launch_agents / 'local.oneos.backup.plist').read_bytes() == b'preserve'


def test_install_login_rolls_back_jobs_and_plists_after_bootstrap_failure(tmp_path, monkeypatch):
    import subprocess
    from tools import local_service as service

    launch_agents = tmp_path / 'Library/LaunchAgents'
    launch_agents.mkdir(parents=True)
    monkeypatch.setattr(service.Path, 'home', classmethod(lambda cls: tmp_path))
    calls = []

    def launchctl(command, **kwargs):
        timeout = kwargs.get('timeout')
        assert isinstance(timeout, (int, float)) and 0 < timeout <= 30
        calls.append(command)
        if command[1] == 'print':
            return subprocess.CompletedProcess(command, 113)
        if command[1] == 'bootstrap' and command[-1].endswith('backup.plist'):
            raise subprocess.CalledProcessError(5, command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(service.subprocess, 'run', launchctl)
    config = {'state_dir': '/synthetic/state', 'repo_dir': '/synthetic/repo'}
    with pytest.raises(subprocess.CalledProcessError):
        service.install_login(config)
    assert not list(launch_agents.glob('local.oneos.*.plist'))
    bootouts = [command[-1] for command in calls if command[1] == 'bootout']
    assert bootouts == ['gui/' + str(service.os.getuid()) + '/local.oneos.backup',
                        'gui/' + str(service.os.getuid()) + '/local.oneos.login']


def test_login_rollback_timeout_still_removes_created_plists(tmp_path, monkeypatch):
    import subprocess
    from tools import local_service as service
    launch_agents = tmp_path / 'Library/LaunchAgents'
    launch_agents.mkdir(parents=True)
    monkeypatch.setattr(service.Path, 'home', classmethod(lambda cls: tmp_path))
    def launchctl(command, **kwargs):
        if command[1] == 'print':
            return subprocess.CompletedProcess(command, 113)
        raise subprocess.TimeoutExpired(command, kwargs.get('timeout', 30))
    monkeypatch.setattr(service.subprocess, 'run', launchctl)
    with pytest.raises(OSError, match='rollback incomplete'):
        service.install_login({'state_dir': '/synthetic/state', 'repo_dir': '/synthetic/repo'})
    assert not list(launch_agents.glob('local.oneos.*.plist'))


def test_browser_open_timeout_releases_operation_lock(tmp_path, monkeypatch, capsys):
    import subprocess
    from tools import local_service as service
    config = diagnostic_config(tmp_path)
    state = tmp_path / 'state'
    service.write_json(state / 'config.json', config)
    monkeypatch.setattr(service, 'start', lambda config: None)
    def hung(command, **kwargs):
        timeout = kwargs.get('timeout')
        assert isinstance(timeout, (int, float)) and 0 < timeout <= 30
        raise subprocess.TimeoutExpired(command, timeout)
    monkeypatch.setattr(service.subprocess, 'run', hung)
    assert service.main(['--state-dir', str(state), 'open']) == 1
    assert json.loads(capsys.readouterr().err)['code'] == 'operation_failed'
    with service.operation_lock(state):
        pass


def test_install_login_removes_partial_plist_after_write_failure(tmp_path, monkeypatch):
    from tools import local_service as service

    launch_agents = tmp_path / 'Library/LaunchAgents'
    launch_agents.mkdir(parents=True)
    monkeypatch.setattr(service.Path, 'home', classmethod(lambda cls: tmp_path))
    original_open = service.Path.open

    class FailingWrite:
        def __init__(self, output):
            self.output = output

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.output.close()

        def write(self, value):
            self.output.write(value[:8])
            self.output.flush()
            raise OSError('synthetic disk full')

    def failing_open(path, *args, **kwargs):
        output = original_open(path, *args, **kwargs)
        if path.name == 'local.oneos.login.plist':
            return FailingWrite(output)
        return output

    monkeypatch.setattr(service.Path, 'open', failing_open)

    def launchctl(command, **kwargs):
        if command[1] == 'print':
            return type('Result', (), {'returncode': 113})()
        pytest.fail('write must fail before launchctl bootstrap')

    monkeypatch.setattr(service.subprocess, 'run', launchctl)
    with pytest.raises(OSError, match='synthetic disk full'):
        service.install_login({'state_dir': '/synthetic/state', 'repo_dir': '/synthetic/repo'})
    assert not list(launch_agents.glob('local.oneos.*.plist'))


def test_install_login_preserves_preexisting_loaded_job(tmp_path, monkeypatch):
    import subprocess
    from tools import local_service as service

    launch_agents = tmp_path / 'Library/LaunchAgents'
    launch_agents.mkdir(parents=True)
    monkeypatch.setattr(service.Path, 'home', classmethod(lambda cls: tmp_path))
    calls = []

    def launchctl(command, **kwargs):
        calls.append(command)
        if command[1] == 'print':
            return subprocess.CompletedProcess(command, 0)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(service.subprocess, 'run', launchctl)
    with pytest.raises(ValueError, match='already loaded'):
        service.install_login({'state_dir': '/synthetic/state', 'repo_dir': '/synthetic/repo'})
    assert not list(launch_agents.glob('local.oneos.*.plist'))
    assert [command[1] for command in calls] == ['print']


def test_install_login_removes_job_installed_before_bootstrap_interrupt(tmp_path, monkeypatch):
    import subprocess
    from pathlib import Path
    from tools import local_service as service

    launch_agents = tmp_path / 'Library/LaunchAgents'
    launch_agents.mkdir(parents=True)
    monkeypatch.setattr(service.Path, 'home', classmethod(lambda cls: tmp_path))
    loaded = set()

    def launchctl(command, **kwargs):
        if command[1] == 'print':
            return subprocess.CompletedProcess(command, 0 if command[-1] in loaded else 113)
        if command[1] == 'bootstrap':
            label = 'local.oneos.' + Path(command[-1]).stem.rsplit('.', 1)[-1]
            loaded.add('gui/' + str(service.os.getuid()) + '/' + label)
            raise InterruptedError('synthetic post-install interruption')
        if command[1] == 'bootout':
            loaded.discard(command[-1])
            return subprocess.CompletedProcess(command, 0)
        pytest.fail('unexpected launchctl command')

    monkeypatch.setattr(service.subprocess, 'run', launchctl)
    with pytest.raises(InterruptedError, match='post-install interruption'):
        service.install_login({'state_dir': '/synthetic/state', 'repo_dir': '/synthetic/repo'})
    assert loaded == set()
    assert not list(launch_agents.glob('local.oneos.*.plist'))


def test_delayed_login_respects_new_stop_request(tmp_path, monkeypatch):
    from tools import local_service as service
    service.write_json(tmp_path / 'manual-stop', {'stopped_at': 200})
    monkeypatch.setattr(service, 'start', lambda config: pytest.fail('new stop wins'))
    service.login_start({'state_dir': str(tmp_path)}, requested_at=100)


def test_stop_reasserts_marker_after_acquiring_operation_lock(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from tools import local_service as service

    config = {'state_dir': str(tmp_path)}
    monkeypatch.setattr(service, 'load_config', lambda *args, **kwargs: config)

    @contextmanager
    def start_finishes_before_stop_gets_lock(state, **kwargs):
        (state / 'manual-stop').unlink()
        yield

    monkeypatch.setattr(service, 'operation_lock', start_finishes_before_stop_gets_lock)

    class Runtime:
        def __init__(self, _config): pass
        def compose(self, *args):
            assert args == ('stop',)
            marker = json.loads((tmp_path / 'manual-stop').read_text())
            assert marker['stopped'] is True
            assert isinstance(marker['stopped_at'], float)

    monkeypatch.setattr(service, 'Runtime', Runtime)
    assert service.main(['--state-dir', str(tmp_path), 'stop']) == 0
    assert (tmp_path / 'manual-stop').is_file()


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


@pytest.mark.parametrize(('last', 'expected'), [
    pytest.param(10 ** 400, 'missed', id='oversized-integer'),
    pytest.param(True, 'missed', id='boolean'),
    pytest.param(float('inf'), 'missed', id='infinity'),
    pytest.param(float('nan'), 'missed', id='nan'),
    pytest.param(1001, 'missed', id='future'),
    pytest.param(-1, 'missed', id='negative'),
    pytest.param(999, 'ok', id='valid-integer'),
    pytest.param(999.5, 'ok', id='valid-float'),
])
def test_diagnostics_handles_backup_timestamp_types_and_bounds(tmp_path, monkeypatch, last, expected):
    from tools import local_service as service
    from tools import local_backup
    config = diagnostic_config(tmp_path)
    config['backup'] = {}
    service.write_status(config, 'ok', last)
    monkeypatch.setattr(service.time, 'time', lambda: 1000)
    monkeypatch.setattr(service.shutil, 'which', lambda name: None)
    monkeypatch.setattr(service.Runtime, 'compose', lambda *args: (_ for _ in ()).throw(OSError()))
    monkeypatch.setattr(local_backup, 'validate_volume', lambda config: None)
    assert service.diagnostics(config)['backup']['state'] == expected


def test_doctor_fails_when_backup_has_never_succeeded(tmp_path, monkeypatch, capsys):
    import sqlite3
    import subprocess
    from tools import local_service as service
    from tools import local_backup
    config = diagnostic_config(tmp_path)
    config['backup'] = {}
    state = tmp_path / 'state'
    service.write_json(state / 'config.json', config)
    service.write_status(config, 'never', None)
    owner = state / 'auth/owner.sqlite3'
    with sqlite3.connect(owner) as db:
        db.execute('create table owner(id integer, password text, secret text, counter integer)')
        db.execute('insert into owner values(1,?,?,1)', ('$argon2id$synthetic', 'A' * 32))
    owner.chmod(0o600)
    monkeypatch.setattr(service.shutil, 'which', lambda name: '/synthetic/tool')
    monkeypatch.setattr(service.subprocess, 'run', lambda command, **kwargs: subprocess.CompletedProcess(command, 0, stdout=''))
    monkeypatch.setattr(service.Runtime, 'running_services', lambda self: ['app', 'caddy'])
    monkeypatch.setattr(local_backup, 'validate_volume', lambda config: None)
    assert service.main(['--state-dir', str(state), 'doctor']) == 1
    output = capsys.readouterr()
    report = json.loads(output.out)
    assert report['backup']['state'] == 'never'
    assert all(item['state'] in {'valid', 'available', 'running', 'accessible', 'enrolled'}
               for name, item in report.items() if name != 'backup')
    assert output.err == ''


def test_safe_error_reasons_never_echo_unrecognized_private_text():
    import subprocess
    from tools.local_service import safe_failure
    assert safe_failure(ValueError('copy the private backup key offline before confirming setup'))['code'] == 'offline_key_required'
    assert safe_failure(ValueError('macOS ACLs require a metadata-aware backup plan'))['code'] == 'unsupported_acl'
    partial = subprocess.CalledProcessError(3, ['restic', '/private/sensitive-path'], stderr='private secret')
    assert safe_failure(partial)['code'] == 'partial_backup'
    assert 'private' not in json.dumps(safe_failure(ValueError('private secret path')))
    assert 'sensitive-path' not in json.dumps(safe_failure(partial))


@pytest.mark.parametrize('payload', [[], None, 'private-status-value', 7, True])
@pytest.mark.parametrize('command', ['doctor', 'status'])
def test_diagnostics_non_mapping_backup_status_is_safe_unknown(tmp_path, monkeypatch, capsys, payload, command):
    from tools import local_service as service
    from tools import local_backup
    config = diagnostic_config(tmp_path)
    config['backup'] = {}
    state = tmp_path / 'state'
    service.write_json(state / 'config.json', config)
    service.write_json(state / 'status/backup-status.json', payload)
    monkeypatch.setattr(service.shutil, 'which', lambda name: None)
    monkeypatch.setattr(service.Runtime, 'compose', lambda *args: (_ for _ in ()).throw(OSError()))
    monkeypatch.setattr(local_backup, 'validate_volume', lambda config: None)
    assert service.main(['--state-dir', str(state), command]) == (1 if command == 'doctor' else 0)
    output = capsys.readouterr()
    assert json.loads(output.out)['backup']['state'] == 'unknown'
    assert output.err == ''
    assert 'private-status-value' not in output.out
    assert str(state) not in output.out
