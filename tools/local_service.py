"""Explicit, private-configured controls for the dedicated local deployment."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import json
import math
import os
from pathlib import Path
import plistlib
import shutil
import shlex
import signal
import sqlite3
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


def load_config(state: Path, *, validate_runtime_paths=True):
    state = safe_path(state)
    if state.stat().st_uid != os.getuid() or state.stat().st_mode & 0o077:
        raise ValueError('state directory must be private')
    config = json.loads(private_file(state / 'config.json').read_text())
    if Path(config['state_dir']) != state:
        raise ValueError('state identity mismatch')
    if validate_runtime_paths:
        for key in ('vault', 'repo_dir'):
            safe_path(Path(config[key]))
    return config


@contextmanager
def operation_lock(state: Path, *, wait=False):
    safe_path(state)
    fd = os.open(state / 'operation.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
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
                              env=self.env, check=True, capture_output=not interactive, text=True,
                              timeout=15 if args and args[0] in {'version', 'ps'} else None)

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


def write_status(config, status, last_success, *, reason=None):
    write_json(Path(config['state_dir']) / 'status/backup-status.json',
               dict(state=status, last_success=last_success, checked_at=time.time(),
                    message=reason or {'ok': 'Encrypted backup verified.', 'never': 'Backup has not completed.',
                             'missed': 'Backup is overdue.', 'failed': 'Backup failed; inspect local operations.'}[status]))


def safe_failure(error):
    """Allowlisted reasons only: exception strings and command output stay local."""
    known = {
        'copy the private backup key offline before confirming setup': ('offline_key_required', 'Copy the backup key to your offline recovery store, then rerun setup-backup with --offline-key-confirmed.'),
        'macOS ACLs require a metadata-aware backup plan': ('unsupported_acl', 'ACL metadata requires an owner-reviewed backup plan. Do not strip source permissions.'),
        'external or absolute symlink cannot be backed up': ('external_link', 'The source includes an external or absolute link. Review its backup scope before retrying.'),
        'hardlinked files require a separate backup plan': ('hardlinked_source', 'The source includes hardlinked files requiring an owner-reviewed backup plan.'),
        'non-regular filesystem entry cannot be backed up': ('unsupported_entry', 'The source includes an unsupported special filesystem entry. Review its backup scope.'),
        'expected external physical volume is unavailable': ('drive_unavailable', 'Connect the configured external physical drive and verify its identity.'),
        'another service or backup operation is running': ('operation_busy', 'Wait for the active service operation, then retry.'),
        'source changed during snapshot': ('source_changed', 'Pause external editors and workers, then retry the backup.'),
        'source changed during snapshot verification': ('source_changed', 'Pause external editors and workers, then retry the backup.'),
        'insufficient private staging capacity': ('capacity_required', 'Free enough host storage for private snapshot staging, then retry.'),
        'insufficient isolated recovery capacity': ('capacity_required', 'Free enough host storage for isolated recovery, then retry.'),
        'snapshot manifest mismatch': ('verification_failed', 'Recovered data or metadata failed verification. Keep the isolated copy for inspection; do not activate it.'),
        'extended attribute copy verification failed': ('metadata_failed', 'Extended attribute preservation failed. Keep source metadata intact and inspect filesystem support.'),
    }
    if isinstance(error, ValueError) and str(error) in known:
        code, message = known[str(error)]
    elif isinstance(error, OSError) and error.strerror == 'extended attribute operation failed':
        code, message = 'metadata_failed', 'Extended attribute access or preservation was refused. Keep source metadata intact and inspect filesystem support.'
    elif isinstance(error, subprocess.CalledProcessError) and isinstance(error.cmd, (list, tuple)) and error.cmd and Path(error.cmd[0]).name == 'restic' and error.returncode == 3:
        code, message = 'partial_backup', 'Restic reported an incomplete backup. No retention was run; inspect source availability and retry.'
    else:
        code, message = 'operation_failed', 'Operation could not complete. Run doctor for safe diagnostics and retry after resolving unavailable components.'
    return {'code': code, 'message': message}


def start(config):
    subprocess.run(['colima', 'start', '--profile', 'oneos', '--activate=false', '--ssh-config=false', '--mount', config['vault'] + ':w',
                    '--mount', config['state_dir'] + ':w', '--mount', config['repo_dir']], check=True, capture_output=True)
    Runtime(config).compose('up', '-d', '--build', '--wait')
    (Path(config['state_dir']) / 'manual-stop').unlink(missing_ok=True)


def login_start(config, *, requested_at=None):
    if requested_at is not None:
        marker = Path(config['state_dir']) / 'manual-stop'
        if marker.exists():
            stopped = json.loads(private_file(marker).read_text())
            if stopped.get('stopped_at', 0) >= requested_at:
                return
    start(config)


def run_login(config):
    requested_at = time.time()
    # Launchd's one-shot RunAtLoad must queue behind setup, backup and enrollment.
    # Other manual mutations still fail fast instead of silently queuing work.
    with operation_lock(Path(config['state_dir']), wait=True):
        login_start(config, requested_at=requested_at)


def finding(state, action):
    return {'state': state, 'action': action}


def authentication_diagnostic(config):
    path = Path(config['state_dir']) / 'auth/owner.sqlite3'
    try:
        directory = safe_path(path.parent)
        if directory.stat().st_mode & 0o077 or directory.stat().st_uid != os.getuid():
            raise ValueError('unsafe auth directory')
        if not path.exists() and not path.is_symlink():
            return finding('not_enrolled', 'Run enroll from the private launcher in a terminal.')
        private_file(path)
        if any(Path(str(path) + suffix).exists() for suffix in ('-journal', '-wal', '-shm')):
            return finding('busy', 'Retry after the current authentication activity finishes.')
        # Immutable read avoids creating journals, SHM files or a write transaction.
        # This reports enrollment presence only; the app performs full validation.
        with sqlite3.connect(path.as_uri() + '?immutable=1', uri=True) as db:
            healthy = db.execute('pragma quick_check').fetchone() == ('ok',)
            enrolled = db.execute("SELECT EXISTS(SELECT 1 FROM owner WHERE id=1 AND password LIKE '$argon2id$%' AND length(secret)=32 AND typeof(counter)='integer')").fetchone()[0]
        if healthy and enrolled:
            return finding('enrolled', 'Owner enrollment is present; use the login screen.')
    except (OSError, ValueError, sqlite3.Error):
        pass
    return finding('unavailable', 'Inspect private auth permissions; use recover after resolving state errors.')


def diagnostics(config):
    """Read-only aggregate diagnostics; never forward external output or paths."""
    result = {'configuration': finding('valid', 'Private configuration loaded.')}
    missing = [name for name in ('colima', 'docker', 'restic', 'diskutil') if not shutil.which(name)]
    result['tools'] = finding('missing' if missing else 'available',
                              'Install required local tools: ' + ', '.join(missing) if missing else 'Required executables are available.')
    runtime = Runtime(config)
    try:
        runtime.compose('version', '--short')
        result['compose'] = finding('available', 'Compose is available.')
    except (OSError, subprocess.SubprocessError):
        result['compose'] = finding('unavailable', 'Install Docker Compose or repair its plugin discovery.')
    try:
        if 'colima' in missing or 'docker' in missing:
            raise ValueError('runtime tools missing')
        subprocess.run(['colima', 'status', '--profile', 'oneos'], check=True, capture_output=True, timeout=15)
        subprocess.run(['docker', '--context', 'colima-oneos', 'info', '--format', '{{.ServerVersion}}'],
                       env=runtime.env, check=True, capture_output=True, timeout=15)
        result['runtime'] = finding('available', 'The dedicated runtime is available.')
        running = set(runtime.running_services())
        state = 'running' if {'app', 'caddy'} <= running else ('partial' if running else 'stopped')
        result['services'] = finding(state, 'Services are running.' if state == 'running' else 'Run start when service availability is wanted.')
    except (OSError, ValueError, subprocess.SubprocessError):
        result['runtime'] = finding('unavailable', 'Run start; if it fails, inspect the dedicated Colima profile.')
        result['services'] = finding('unknown', 'Restore runtime availability before checking services.')
    try:
        vault = safe_path(Path(config['vault']))
        safe_path(vault / '.git')
        with os.scandir(vault) as entries:
            next(entries, None)
        result['vault'] = finding('accessible', 'Configured vault and Git directory are accessible.')
    except (OSError, ValueError):
        result['vault'] = finding('unavailable', 'Reconnect the configured vault and check private path permissions; do not create an empty replacement.')
    result['authentication'] = authentication_diagnostic(config)
    if 'backup' not in config:
        result['backup'] = finding('not_configured', 'Run setup-backup with the intended external drive and an offline key copy.')
    else:
        from tools.local_backup import validate_volume
        try:
            validate_volume(config['backup'])
        except (OSError, ValueError, KeyError, subprocess.SubprocessError):
            result['backup'] = finding('disconnected', 'Connect the original external physical drive; verify its configured identity.')
        else:
            try:
                status = json.loads(private_file(Path(config['state_dir']) / 'status/backup-status.json').read_text())
                last = status.get('last_success')
                state = status.get('state')
                if state not in {'ok', 'never', 'missed', 'failed'}:
                    raise ValueError('invalid backup status')
                if state == 'ok' and (not isinstance(last, (float, int)) or not math.isfinite(last) or not 0 < last <= time.time() or time.time() - last >= 86400):
                    state = 'missed'
                result['backup'] = finding(state, 'Backup is current.' if state == 'ok' else 'Run backup, then restore-check to verify recovery.')
            except (OSError, ValueError, TypeError):
                result['backup'] = finding('unknown', 'Run backup to establish verified backup status.')
    return result


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
        config = load_config(args.state_dir, validate_runtime_paths=args.command not in ('doctor', 'status'))
        if args.command in ('doctor', 'status'):
            report = diagnostics(config)
            print(json.dumps(report))
            return int(args.command == 'doctor' and any(item['state'] in {'missing', 'unavailable', 'unknown', 'partial', 'failed', 'missed', 'disconnected', 'not_enrolled', 'not_configured'} for item in report.values()))
        if args.command == 'login-start':
            run_login(config)
            return 0
        if args.command == 'stop':
            # Record intent before lock acquisition, so delayed login cannot
            # override an explicit stop requested while it was waiting.
            write_json(args.state_dir / 'manual-stop', {'stopped': True, 'stopped_at': time.time()})
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
            elif args.command == 'stop':
                runtime.compose('stop')
            elif args.command == 'logs': print(runtime.compose('logs', '--tail', '200').stdout)
            elif args.command == 'install-login': install_login(config)
            else:
                from tools.local_backup import backup, restore_check, setup_backup
                if args.command == 'setup-backup':
                    if args.mount is None: parser.error('--mount required')
                    setup_backup(config, args.mount, args.offline_key_confirmed)
                elif args.command == 'restore-check': print(restore_check(config))
                else: backup(config, runtime, due_only=args.command == 'backup-due')
        return 0
    except (ValueError, TypeError, OSError, KeyError, subprocess.SubprocessError) as error:
        if args.command in ('doctor', 'status'):
            print(json.dumps({'configuration': finding('unavailable', 'Check that private state and config.json exist, are owner-only, and use canonical paths. Restore the intended configuration; do not replace the vault.')}))
        else:
            print(json.dumps(safe_failure(error)), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
