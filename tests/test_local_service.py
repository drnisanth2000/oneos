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
