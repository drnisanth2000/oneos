# Local operations and recovery

This owner-approved deployment uses the dedicated `oneos` Colima profile,
`colima-oneos` Docker context, and `oneos` Compose project. Commands do not
change the global Docker context. Install Colima, Docker CLI, Docker Compose,
and restic locally before setup. Both the standalone Compose executable and
the Docker Compose plugin are supported. No host installation occurs merely
by importing these tools.

Run commands from the installed repository with its Python environment:

```sh
python -m tools.local_service --state-dir "$ONEOS_STATE_DIR" setup --vault "$ONEOS_VAULT"
python -m tools.local_service --state-dir "$ONEOS_STATE_DIR" doctor
python -m tools.local_service --state-dir "$ONEOS_STATE_DIR" start
python -m tools.local_service --state-dir "$ONEOS_STATE_DIR" status
python -m tools.local_service --state-dir "$ONEOS_STATE_DIR" logs
python -m tools.local_service --state-dir "$ONEOS_STATE_DIR" stop
```

The state parent must already exist. Setup refuses a missing vault and prompts
for the Git identity; it creates owner-only state and `config.json`. The JSON
contains local paths and must never be committed. It is parsed as data, never
sourced as shell code. Paths containing symbolic-link components are refused;
use their canonical paths, including `/private/tmp` on macOS. Keep state
outside the vault and public repository. The source vault must be a standalone
Git checkout. Colima explicitly shares only vault, state, and repository.
Changing an already-running Colima profile's mounts may require stopping that
profile and starting again after confirming no owned service is running.

Setup also creates a private executable `OneOS.command` in the state directory.
Double-click it to start and open the browser, or run it from Terminal with
`stop`, `status`, `backup`, `restore-check`, `enroll`, or `recover`. The enrollment
wrappers invoke owner administration in the configured app container and require
a real terminal; no credential travels in arguments or captured logs. Install
from a stable checkout and stable Python environment before creating this
launcher or login jobs. A temporary worktree is not a deployment installation.

Owner enrollment and recovery use `python -m app.auth_admin enroll|recover`
within the configured app environment. Never place passwords or TOTP secrets
in command arguments. Open `https://localhost:8443` after successful enrollment
and startup. Local TLS trust is an explicit host setup step described in the
runtime deployment guide.

After checking that this is the intended deployment's CA certificate, explicitly
trust it in your login keychain (never trust a synthetic test CA):

```sh
security add-trusted-cert -r trustRoot -k "$HOME/Library/Keychains/login.keychain-db" "$ONEOS_STATE_DIR/caddy/data/caddy/pki/authorities/local/root.crt"
```

`stop` stops this Compose project. It remains stopped until `start` or the next
login. `install-login` explicitly installs two user LaunchAgents: startup at
login and an hourly due-backup check. The latter catches up a missed daily
backup when the Mac is awake and the drive is available. It never starts a
previously stopped app. Jobs run only after login, and cannot promise service
or backup availability during sleep. Reinstallation refuses existing job files
so an operator can inspect them. To disable a job, unload its exact
`local.oneos.login` or `local.oneos.backup` plist with `launchctl bootout`.

The login job waits for any active setup, backup, or enrollment operation before
starting. A new explicit stop request received while login is waiting cancels
that delayed startup. Other mutations still report contention immediately; retry
them after the active operation finishes. This also handles both RunAtLoad jobs
starting while installation still holds the service-operation lock.

`doctor` and `status` return safe aggregate JSON with an action for each check:
configuration, required tools, Compose, the dedicated runtime, service state,
vault accessibility, owner enrollment presence, and backup configuration/drive/
freshness. They do not acquire the mutation lock or reveal subprocess output,
paths, credentials, or volume identities. Authentication inspection does not
write to its database; active journal sidecars report busy. The app still
performs full credential-state validation. `doctor` exits nonzero for unavailable,
failed, or incomplete enrollment/backup setup; `status` succeeds when it can
provide the report. CLI failures include an allowlisted reason code and next
action; failed-backup status retains the corresponding safe message. Raw command
errors, paths, and credential data are never forwarded.

```sh
python -m tools.local_service --state-dir "$ONEOS_STATE_DIR" install-login
```

## External encrypted backup

Use an external physical drive mounted in macOS. Only the encrypted restic
repository is stored there. Staging and recovery copies stay in owner-only
directories under private host state, with available capacity checked first.
Protect the Mac with disk encryption. Setup records the
volume UUID and canonical mountpoint. Every external operation checks UUID,
mounted state, physical status, and that the drive is external using diskutil.
A missing, wrong, internal, virtual, or replaced drive fails closed. Open
directory descriptors pin external writes so a disappearing mountpoint cannot
fall back onto the Mac's internal disk.

First request setup without acknowledgement:

```sh
python -m tools.local_service --state-dir "$ONEOS_STATE_DIR" setup-backup --mount "$ONEOS_BACKUP_MOUNT"
```

This creates an owner-only `backup-key` in the private state directory and
refuses repository initialization until its offline copy is confirmed. Copy
that key into your offline password/recovery store using local trusted tools;
never paste it into a task, command line, tracked file, or log. Then initialize:

```sh
python -m tools.local_service --state-dir "$ONEOS_STATE_DIR" setup-backup --mount "$ONEOS_BACKUP_MOUNT" --offline-key-confirmed
python -m tools.local_service --state-dir "$ONEOS_STATE_DIR" backup
python -m tools.local_service --state-dir "$ONEOS_STATE_DIR" restore-check
```

Backups preserve the vault's Git history, index, dirty and untracked content,
directories, file modes, relative internal links, exact extended attributes
(including source root and link metadata), and complete raw SQLite
files including sidecars. The recovery payload also includes Caddy's private
TLS authority/state, owner authentication state, and the private deployment
configuration under `deployment/`; the offline backup key is excluded.
They also create integrity-checked SQLite online
backup copies from the private staging copy, so no SQLite connection modifies
the source vault or live authentication database. Authentication/session state
is restored only for isolated inspection. Recover credentials and revoke all
sessions before any separately reviewed activation; never copy restored active
sessions into production.

Before copying, the tool stops only previously running services in this
deployment and acquires the existing action lock. A host flock alone does not
coordinate reliably with a guest VM; stopping the app is essential. Keep other
vault editors and workers quiet during the snapshot. A no-follow inventory,
SHA-256 copy verification, and repeated source inventory comparison refuse
changes during copying. The app's previous running state is restored even if
copying fails or is interrupted. One service-operation lock excludes concurrent
start, stop, scheduled backup, and recovery commands.

Unsupported layouts fail rather than lose data: external/absolute symlinks,
linked Git worktrees, alternate Git object stores, explicit Git worktree
redirection, nested filesystems, hardlinks, special files, and macOS ACLs.
Extended attributes are captured through descriptor-based native APIs, stored
losslessly in the encrypted version-2 manifest, copied to staging, and verified
again after restore. A filesystem refusing an attribute write is a hard failure,
never permission to strip the source attribute. Inventory refusal requires an owner-reviewed metadata-aware backup
plan; do not delete source attributes or links merely to make backup succeed.
Git environment overrides must be unset. A process kill or power loss cannot
execute restoration cleanup; inspect status and start the service explicitly.

After restic backup and repository checks succeed, retention keeps 7 daily,
4 weekly, and 6 monthly snapshots. Pruning occurs only on the bound external
drive. A nonzero restic exit, including a partial backup, never records success.
`status/backup-status.json` contains only aggregate state, last successful UTC
epoch, checked-at UTC epoch, and a safe message. Docker logs are bounded by the
Compose logging policy; `logs` prints at most 200 recent lines. Restic output
is captured and not published into readiness diagnostics.

Successful staging directories are removed. Interrupted or failed runs retain
their exact `snapshot-*` directory for inspection in private host state; these
contain plaintext. Inspect and remove only a confirmed obsolete staging
directory after a verified backup. A stale restic repository lock is never
automatically unlocked: confirm that no restic process still owns it before
using restic's explicit unlock command.

## Recovery drill

`restore-check` selects the newest `oneos` snapshot, restores into a fresh
`recovery-*` directory under private host state, verifies the complete hash
manifest, runs Git fsck and SQLite integrity checks, and prints its path. It
never overwrites the live vault. Inspect the restored content there. Recovery
promotion is a separate owner-reviewed action after a full drill; this tool
does not reconfigure production or restore active sessions.

Keep the offline encryption key and a copy of the nonsecret recovery
instructions separate from this Mac. Losing both key copies makes the encrypted
repository unrecoverable. A replacement Mac needs restic, the key file with
owner-only permissions, and the actual external repository. Use restic restore
to a new isolated directory and verify with
`tools.local_backup.verify_snapshot` before any promotion. The physical-drive
trial remains an explicit deployment acceptance check; synthetic tests cannot
prove USB removal behavior or the owner's actual disk identity.
