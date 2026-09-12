# Production-shaped local deployment acceptance

This record distinguishes public implementation and synthetic verification from
owner-specific live rollout. It does not authorize remote access or another
product phase. The operational entrypoint is [the local operations guide](LOCAL-OPERATIONS.md).

## Scope and evidence

The implementation started from freshly fetched `origin/main` at
`c60fc802d056f144cd442c570c6109aecbe1435e`. Security, runtime and operations used
separate branches/worktrees and synthetic data. Integration was sequential.
Owner passwords, TOTP secrets, backup keys and vault paths are not repository
configuration. No public agent received private-vault contents.

| Acceptance area | Evidence and remaining boundary |
|---|---|
| Authentication | Real Argon2 hashing; password/TOTP login; atomic replay rejection; persistent throttling; expiry, logout/recovery and CSRF tests; route-wide protection including unknown/static/framework routes |
| Container security | Non-root selected UID/GID, read-only root, dropped capabilities, no privilege escalation, no app host port; Caddy publishes only loopback HTTPS |
| Mounted Git behavior | Synthetic vault on the actual macOS-mounted filesystem: isolated approval commit, exact tree revert, stale-source refusal, rejecting-hook rollback and retained UID |
| Backup/recovery | Real Restic synthetic backups and isolated latest-snapshot recovery; hash manifest, Git fsck and SQLite integrity checks; physical-drive trial still required |
| Login startup | Synthetic startup-lock contention and newer manual-stop precedence verified; actual logout/login trial still required |
| Existing private contract | Trusted-local unit/structural checks with private preservation evidence; no live repair, proposal or curated-content mutation by deployment work |

Container integration found and corrected issues that configuration-only tests
missed: the inherited Caddy low-port file capability conflicted with dropped
capabilities; the older distribution Git rejected the existing full-hash commit
option; and a single-file Caddy configuration bind did not provide a reliable
update boundary. Caddy now strips the unnecessary capability, the Python 3.12
image includes a compatible distribution Git, and public Caddy configuration
is embedded in the image. Transaction safety code and policy hooks are retained.

Independent review also corrected hidden-form attribute escaping and a login
startup race with scheduled backups. Pull-request review then tightened session
transaction ordering, setup/login concurrency, bounded startup waits, pinned
external-repository validation and read-only extended-attribute recovery.
Expanded regression tests cover these boundaries, including failure-path file
descriptor cleanup. Internal app traffic remains on the unpublished dedicated
Compose network; HTTPS terminates at Caddy as approved for this localhost-only
deployment. Exact-head re-review additionally closed build-context vault
containment, partial setup and LaunchAgent rollback, repeat backup-key setup,
and malformed scheduled-backup timestamp cases. LaunchAgent rollback preserves
pre-existing loaded jobs while cleaning jobs installed before an interrupted
bootstrap result. Final review also made owner enrollment atomically retryable,
aligned authenticated form parsing with the maximum lifecycle proposal shape,
kept backup media separate from plaintext state and vault data, retained only
portable captured Git hooks, made backup setup resumable after repository
initialization, and closed setup, explicit-stop and review-card identity races.
Direct Compose validation rebuilds both application and proxy images.

## Closing implementation verification

- Locked Python 3.12 public suite: **2,356 passed, 4 skipped**.
- Trusted-local private suite: **39 passed**; structural validation: **0 errors,
  0 warnings**. Opaque before/after Git evidence preserved pre-existing state.
- Actual synthetic HTTPS verified password/TOTP login, secure cookies, protected
  pages, CSRF/Origin rejection, logout and persisted sessions after an application
  process crash. Recovery was confirmed by an increased container restart count.
  Certificate trust was confined to the test client, not the system keychain.
- The launcher started the synthetic service independently of its invoking
  Terminal process. Explicit stop survived an unsuccessful scheduled backup
  check; the next-login entrypoint started it again. This is not a substitute
  for the pending real logout/login trial.
- Real Restic synthetic recovery tests preserve macOS extended attributes,
  including binary resource forks, as well as file contents and required state.
  Unsupported filesystem layouts fail closed rather than silently losing data.
- Independent security, runtime, operations and shared-integration reviews
  completed without unresolved findings. Synthetic services were stopped after
  acceptance; their state and the previous development service were preserved.

## Live rollout checklist — not yet completed

1. Use a stable, reviewed checkout and Python environment, retaining the prior
   application release. Do not install a LaunchAgent pointing at a temporary
   worktree. Use the setup command to select private state and the existing vault.
2. Configure the intended external drive locally, verify identity, retain the
   encryption key off the Mac, and explicitly confirm that offline copy. Test
   disconnection, reconnection, wrong-drive rejection and an interrupted backup.
3. Enroll the owner interactively; verify the authenticator and local recovery
   procedure. No default account or generated owner credential is installed by
   the test suite. Review and trust only the actual deployment's local CA.
4. Back up and restore-check the actual data into an isolated directory. Keep
   other vault writers quiet. Inspect the restored Git history, databases,
   authentication recovery state and metadata. Never activate restored sessions.
5. Install login jobs, close Terminal, test a real logout/login, and confirm
   explicit stop and crash-recovery behavior. Availability requires an awake,
   logged-in Mac; closing the lid can stop availability.
6. Perform the controlled development-server switch only after these checks.
   Retain the old release and a verified pre-switch backup. A code rollback does
   not authorize blindly replacing authentication state or reverting user data.

Do not describe the installation as production-ready until the owner-specific
checks above have evidence. Synthetic success does not prove physical drive
behavior, private filesystem compatibility or an actual macOS login event.
