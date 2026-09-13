# Local deployment implementation contract

Approved scope: one owner on this Mac, localhost HTTPS, Colima/Docker Compose,
automatic login startup, encrypted external-drive backups and isolated restore
verification. VPS, remote access, multi-user accounts and new worker behavior
are deferred. Existing curated-content/outbox and Git invariants remain binding.

## Shared interfaces

- `ONEOS_DEPLOYMENT=local` selects the production runtime. Authentication defaults
  to required; explicit `ONEOS_AUTH_MODE=disabled` is allowed only outside that
  runtime for existing synthetic tests and loopback development.
- `ONEOS_ORIGIN=https://localhost:8443` is the sole browser origin. Caddy terminates
  TLS and forwards to app:8000 internally. Only Caddy publishes loopback 8443.
- `ONEOS_VAULT` supplies the existing vault; the container sees `/vault`.
- `ONEOS_STATE_DIR` is the host deployment state root. Its `auth` child is mounted
  into the app as `/state/auth` and selected using `ONEOS_AUTH_STATE_DIR`.
  `ONEOS_BACKUP_STATUS_FILE` selects a read-only status JSON for readiness UI.
- Host private configuration is `config.json` in the state root. Runtime scripts
  consume it without shell evaluation. No private literal enters tracked files.
- `GET /healthz` returns a minimal process health response without vault data.
  `GET /readyz` is owner-authenticated and reports safe configuration/vault/backup
  diagnostics. Owner login/logout are `/login` and `/logout`.
- `python -m app.auth_admin enroll|recover` is the interactive owner administration
  entrypoint; no secrets in argv or logs. `python -m tools.local_service` provides
  setup/start/stop/status/logs/doctor/backup/restore-check/install-login/login-start.
- Compose inputs: `ONEOS_VAULT`, `ONEOS_STATE_DIR`, `ONEOS_UID`, `ONEOS_GID`,
  `ONEOS_GIT_NAME`, `ONEOS_GIT_EMAIL`. Git identity is configured locally, never
  inherited accidentally from the image. Image runs as selected non-root uid/gid.

## Task 1: Owner security

Own app/auth*.py, auth templates, template CSRF wiring, static auth integration,
app/main.py authentication wiring, tests/test_auth*.py, minimal tests/conftest.py
explicit development-mode setup, pyproject.toml and uv.lock.
Implement Argon2id password hashing with argon2-cffi and PyOTP. Maintain opaque
server-side sessions in private SQLite, store only hashes of session tokens,
HttpOnly/Secure/SameSite=Strict cookie, bounded idle and absolute expiry, revoke
sessions on recovery/logout. Enforce exact Host/Origin, protect every mutation
including login/logout with CSRF, support normal forms and HTMX. TOTP counters
must advance atomically to reject replay across concurrent requests/restarts.
Persist throttling; cap unauthenticated input sizes/work. Fail closed for missing,
corrupt or unsafe auth state; health alone remains available. Local interactive
enrollment verifies a code before activating the owner; recovery rotates owner
credentials/TOTP and revokes sessions. No default credentials or web enrollment.
Tests must exercise real hashing, login, CSRF, expiry, replay, concurrency,
logout/recovery, HTMX and all-route protection. Record RED then GREEN evidence.

## Task 2: Runtime packaging

Own Dockerfile, compose.yaml, .dockerignore, deploy/Caddyfile, deploy runtime
documentation, and tests/test_local_runtime.py. Do not edit app/main.py or auth.
Use Python 3.12, locked uv dependencies, non-root selected uid/gid, writable only
explicit mounts/temp locations, no Docker socket, no reload. Include Git and
Python needed by existing vault hooks. Do not disable policy hooks. Bind source
vault only at runtime, never COPY it or secrets into build contexts. Caddy local
TLS state persists. App healthcheck uses /healthz; logs rotate; restart policy
survives crashes but explicit stop remains stopped. Startup must diagnose config.
Do synthetic validation; do not install host services or touch live vault.

## Task 3: Operations and recovery

Own tools/local_service.py, tools/local_backup.py (and focused helper modules),
launcher, login templates, docs/LOCAL-OPERATIONS.md, tests/test_local_service.py
and tests/test_local_backup.py. Implement dedicated Colima profile oneos, Docker
context colima-oneos, explicit Compose project oneos. Use subprocess argument
arrays and private JSON configuration, never shell-source secrets. Validate
paths, prevent symlink redirection and never silently create a missing vault.
Install login/periodic jobs only via explicit command; respect manual stop.
External backup setup binds volume UUID and external physical status using
diskutil plist; revalidate before every write/init/retention/restore. Never write
into an absent mountpoint. Keep encryption key private with offline-owner copy.
Daily scheduling with catch-up and bounded logs/status, 7 daily/4 weekly/6 monthly
retention. Snapshot consistency: coordinate app stop and existing action_lock;
copy to private host staging with no-follow inventory and SHA verification, SQLite
online backup for databases, detect source changes and fail rather than claim a
consistent snapshot. Preserve originals. Refuse unexpected external links and
Git directories outside vault (linked worktree backups would be incomplete).
External editors must be quiet for snapshot; stop only services owned by this
deployment, restore previous run state afterward, do not stop unrelated servers.
Restore to a new isolated private host directory, verify hashes/Git/SQLite, never
overwrite live paths or restore active sessions into production. Use real restic
integration if available plus tests for missing/wrong/replaced drive, interruptions,
manual stop and recovery. No host installation or live actions in this workstream.

## Task 4: Integration and acceptance

Lead owns health/readiness, visible status and shared conflicts. Verify fresh
public baseline, scoped workstreams, independent review, combined suite and
publication scans. Then synthetic container startup/TLS/restart/login/transaction
checks and external recovery drill. Trusted-local private gate requires opaque
before/after state and 39+ private tests plus validator 0/0. Owner credentials and
external-drive identity are entered interactively before live activation; never
invent them. Preserve previous development server until controlled switch.

## Execution rulings

- Parallel independent worktrees are explicitly approved by the owner; this
  overrides the skill's default sequential implementer rule. Lead integrations
  remain sequential and reviewers are independently dispatched.
- Dependency additions for password/TOTP are explicitly approved; use upstream
  maintained APIs and locked resolutions. No registry or convention changes.
  The locked additions use the upstream high-level
  [Argon2 PasswordHasher API](https://argon2-cffi.readthedocs.io/en/stable/howto.html)
  and [PyOTP TOTP API](https://pyauth.github.io/pyotp/). The application, not the
  TOTP library alone, enforces persistent replay protection and throttling.
- Colima is the approved free runtime; startup is after login, not before disk
  unlock. This does not promise availability while the Mac is asleep.
- Hardware/owner secrets may block final live setup, never public implementation.
  Report any unexercised acceptance conditions explicitly.
- Private host staging and restore directories keep plaintext off an unencrypted
  external drive. Only the encrypted Restic repository is stored externally.
  Authentication state is backed up, but never restored into a running service.
