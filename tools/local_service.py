"""Explicit, private-configured controls for the dedicated local deployment."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import plistlib
import shutil
import shlex
import signal
import stat
import subprocess
import sys
import tempfile
import time


def safe_path(path: Path, *, directory=True) -> Path:
    path = Path(os.path.abspath(path))
    for item in [*reversed(path.parents), path]:
        if item.is_symlink():
            raise ValueError('symlink paths are not supported')
    if not path.exists() or (directory and not path.is_dir()):
        raise ValueError('required path is unavailable')
    return path


def private_file(path: Path) -> Path:
    safe_path(path, directory=False)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('private file must be owner-only and regular')
    return path


def write_json(path: Path, value):
    safe_path(path.parent)
    if path.is_symlink():
        raise ValueError('unsafe destination')
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as output:
            json.dump(value, output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_config(state: Path):
    state = safe_path(state)
    if state.stat().st_uid != os.getuid() or state.stat().st_mode & 0o077:
        raise ValueError('state directory must be private')
    config = json.loads(private_file(state / 'config.json').read_text())
    if Path(config['state_dir']) != state:
        raise ValueError('state identity mismatch')
    for key in ('vault', 'repo_dir'):
        safe_path(Path(config[key]))
    return config


@contextmanager
def operation_lock(state: Path):
    safe_path(state)
    fd = os.open(state / 'operation.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError('another service or backup operation is running') from exc
        yield
    finally:
        os.close(fd)


class Runtime:
    def __init__(self, config):
        self.config = config
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(('ONEOS_', 'COMPOSE_', 'DOCKER_'))}
        self.env.update({
            'ONEOS_VAULT': config['vault'], 'ONEOS_STATE_DIR': config['state_dir'],
            'ONEOS_UID': str(config['uid']), 'ONEOS_GID': str(config['gid']),
            'ONEOS_GIT_NAME': config['git_name'], 'ONEOS_GIT_EMAIL': config['git_email'],
            'ONEOS_BACKUP_STATUS_FILE': str(Path(config['state_dir']) / 'status/backup-status.json'),
        })

    def compose(self, *args, interactive=False):
        self.env['DOCKER_CONTEXT'] = 'colima-oneos'
        command = ['docker-compose'] if shutil.which('docker-compose') else ['docker', '--context', 'colima-oneos', 'compose']
        return subprocess.run([*command, '--project-name', 'oneos', '--env-file', '/dev/null',
                               '--file', str(Path(self.config['repo_dir']) / 'compose.yaml'), *args],
                              env=self.env, check=True, capture_output=not interactive, text=True)

    def running_services(self):
        return self.compose('ps', '--services', '--status', 'running').stdout.split()


@contextmanager
def paused(runtime):
    running = runtime.running_services()
    if running:
        # Set the restoration obligation before stop: a partial stop also needs recovery.
        try:
            runtime.compose('stop', *running)
            yield
        finally:
            runtime.compose('start', *running)
    else:
        yield


def setup(state, vault, repo, name, email):
    vault, repo = safe_path(vault), safe_path(repo)
    if not (vault / '.git').is_dir() or (vault / '.git').is_symlink():
        raise ValueError('vault must be a standalone Git checkout')
    if not name.strip() or not email.strip() or '\n' in name + email:
        raise ValueError('explicit Git identity required')
    state = Path(os.path.abspath(state))
    if state.is_relative_to(vault) or state.is_relative_to(repo) or vault.is_relative_to(state):
        raise ValueError('private state must be separate from vault and repository')
    safe_path(state.parent)
    state.mkdir(mode=0o700, exist_ok=True)
    safe_path(state)
    if state.stat().st_mode & 0o077:
        raise ValueError('state directory must be private')
    if (state / 'config.json').exists():
        raise ValueError('configuration already exists')
    for child in ('auth', 'status', 'logs', 'caddy', 'caddy/data', 'caddy/config'):
        (state / child).mkdir(mode=0o700, exist_ok=True)
        safe_path(state / child)
    config = dict(state_dir=str(state), vault=str(vault), repo_dir=str(repo), git_name=name,
                  git_email=email, uid=os.getuid(), gid=os.getgid())
    write_json(state / 'config.json', config)
    launcher = state / 'OneOS.command'
    fd = os.open(launcher, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o700)
    with os.fdopen(fd, 'w') as output:
        output.write('#!/bin/sh\nset -eu\ncd ' + shlex.quote(str(repo)) + '\n'
                     'if [ "$#" -eq 0 ]; then set -- open; fi\nexec ' + shlex.quote(sys.executable)
                     + ' -m tools.local_service --state-dir ' + shlex.quote(str(state)) + ' "$@"\n')
    write_status(config, 'never', None)
    return config


def write_status(config, status, last_success):
    write_json(Path(config['state_dir']) / 'status/backup-status.json',
               dict(state=status, last_success=last_success, checked_at=time.time(),
                    message={'ok': 'Encrypted backup verified.', 'never': 'Backup has not completed.',
                             'missed': 'Backup is overdue.', 'failed': 'Backup failed; inspect local operations.'}[status]))


def start(config):
    subprocess.run(['colima', 'start', '--profile', 'oneos', '--activate=false', '--ssh-config=false', '--mount', config['vault'] + ':w',
                    '--mount', config['state_dir'] + ':w', '--mount', config['repo_dir']], check=True, capture_output=True)
    Runtime(config).compose('up', '-d', '--build', '--wait')
    (Path(config['state_dir']) / 'manual-stop').unlink(missing_ok=True)


def login_start(config):
    start(config)


def install_login(config):
    """Install only on explicit invocation; launchd retries daily backup catch-up hourly."""
    library = safe_path(Path.home() / 'Library')
    (library / 'LaunchAgents').mkdir(mode=0o700, exist_ok=True)
    destination = safe_path(library / 'LaunchAgents')
    python = sys.executable  # Preserve the venv entrypoint, not its base interpreter.
    for suffix, command in [('login', 'login-start'), ('backup', 'backup-due')]:
        value = {'Label': 'local.oneos.' + suffix,
                 'ProgramArguments': [python, '-m', 'tools.local_service', '--state-dir', config['state_dir'], command],
                 'WorkingDirectory': config['repo_dir'], 'RunAtLoad': True,
                 'EnvironmentVariables': {'PATH': os.environ.get('PATH', '')}}
        if suffix == 'backup':
            value['StartInterval'] = 3600
        path = destination / ('local.oneos.' + suffix + '.plist')
        if path.exists() or path.is_symlink():
            raise ValueError('login job already exists; inspect before replacing')
        with path.open('xb') as output:
            output.write(plistlib.dumps(value))
        subprocess.run(['launchctl', 'bootstrap', 'gui/' + str(os.getuid()), str(path)], check=True, capture_output=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', type=Path, required=True)
    parser.add_argument('command', choices=['setup', 'start', 'open', 'enroll', 'recover', 'stop', 'status', 'logs', 'doctor', 'backup',
                                         'restore-check', 'install-login', 'login-start', 'backup-due', 'setup-backup'])
    parser.add_argument('--vault', type=Path)
    parser.add_argument('--mount', type=Path)
    parser.add_argument('--offline-key-confirmed', action='store_true')
    args = parser.parse_args(argv)
    def terminated(*_):
        raise InterruptedError('service operation interrupted')
    signal.signal(signal.SIGTERM, terminated)
    try:
        if args.command == 'setup':
            if args.vault is None:
                parser.error('--vault required for setup')
            setup(args.state_dir, args.vault, Path(__file__).resolve().parents[1], input('Git name: '), input('Git email: '))
            return 0
        config = load_config(args.state_dir)
        with operation_lock(args.state_dir):
            runtime = Runtime(config)
            if args.command == 'start': start(config)
            elif args.command == 'open':
                start(config)
                subprocess.run(['open', 'https://localhost:8443'], check=True, capture_output=True)
            elif args.command in ('enroll', 'recover'):
                if not sys.stdin.isatty() or not sys.stdout.isatty():
                    raise ValueError('owner administration requires an interactive terminal')
                runtime.compose('exec', 'app', 'python', '-m', 'app.auth_admin', args.command, interactive=True)
            elif args.command == 'login-start': login_start(config)
            elif args.command == 'stop':
                write_json(args.state_dir / 'manual-stop', {'stopped': True})
                runtime.compose('stop')
            elif args.command == 'status': print(runtime.compose('ps').stdout)
            elif args.command == 'logs': print(runtime.compose('logs', '--tail', '200').stdout)
            elif args.command == 'doctor':
                missing = [name for name in ('colima', 'docker', 'restic', 'diskutil') if not shutil.which(name)]
                print(json.dumps({'missing_tools': missing, 'config': 'valid'}))
                return bool(missing)
            elif args.command == 'install-login': install_login(config)
            else:
                from tools.local_backup import backup, restore_check, setup_backup
                if args.command == 'setup-backup':
                    if args.mount is None: parser.error('--mount required')
                    setup_backup(config, args.mount, args.offline_key_confirmed)
                elif args.command == 'restore-check': print(restore_check(config))
                else: backup(config, runtime, due_only=args.command == 'backup-due')
        return 0
    except (ValueError, OSError, KeyError, subprocess.SubprocessError):
        print('Operation failed. Check private configuration, service state, and external drive.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
