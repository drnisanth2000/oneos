# Governed lifecycle maintenance implementation

The owner approved synthetic implementation on 2026-09-10. This is Phase 1
maintenance development, not authorization to access or repair a live vault,
install a validator, resume Gate 1, change phases or publish this branch.

## Explicit authority

The implementation reads the existing policy file at
`_system/scripts/action-policy.yaml`. Its optional `lifecycle_actions` mapping
contains only `lifecycle_repair` and `lifecycle_rollback`. Each enabled action
has exactly `actor: owner` and an `entities` list of explicitly authorized
runtime entity identifiers. There is no wildcard, inherited permission or
worker permission. Missing or malformed authorization refuses the action.
Other existing policy sections retain their existing meanings. This code does
not modify that file or provide a live policy installation command.

The human request surface supplies the owner actor; no form field grants this
role. This retains the existing trusted-local human-surface boundary. Service
callers must supply the actor explicitly. Git environment overrides are refused
for lifecycle authority, to prevent selecting a different repository or index.

## Interfaces

- `required_shape(scope)` enumerates declarations without writes.
  `inspect_shape(scope)` returns only missing eligible directories, or refuses
  when required content/roots lack both filesystem presence and committed
  authority, or any applicable path is unsafe. Both supported regular Git file
  modes remain accepted for existing declarations and required content.
- `plan_repair(scope, actor=...)` is read-only. `propose_repair` creates an exact,
  exclusively allocated outbox proposal and returns its review snapshot.
- `LifecycleJournal.create(store, vault)` creates a fresh journal in an existing
  external directory. Keep its `anchor` independently of its `session_path`.
  Evidence creation and subsequent checkpoints belong under the shared action
  lock. The application never automatically enables a journal or policy.
- `approve_lifecycle(scope, id, review_sha256, actor=..., journal=...)` executes
  the reviewed transaction. Existing outbox approval dispatch uses the journal
  configured on the application state and restores request-local context on exit.
- `propose_rollback(scope, repair_commit, actor=..., journal=...)` binds the exact
  historical inverse and observed original directory identity. Its approval uses
  the same service and a new exact-byte review token.
- `audit_lifecycle_session(vault, session_path, anchor)` checks every recorded
  adjacent transition, committed authority and retained receipts. The existing
  Gate 3 check requires `ONEOS_LIFECYCLE_JOURNAL` and
  `ONEOS_LIFECYCLE_ANCHOR` for a session containing lifecycle commits, and binds
  the journal to its complete saved baseline snapshot.

The initial audit supports an uninterrupted lifecycle session: repair and
rollback checkpoints share one journal, and intervening activity may only be
creation of the next exact proposal. Unrelated pre-existing dirty state is
preserved. Unrecorded intervening activity, unrelated commits or incomplete
checkpoints prevent certification. The journal is observed evidence within the
cooperative-writer boundary, not continuous monitoring of transient writes.

## Durable evidence

Version-1 action receipts remain supported. Version-2 lifecycle receipts live
in the same scoped `.receipts` store and contain exact original proposal bytes
plus matching closed bound fields. The proposal binds the starting commit,
canonical local identity digest, declaration and policy hashes, shape hash,
ordered manifest, and directory/parent identities. Rollback also binds the
original repair commit, receipt path and blob OID. A receipt never contains its
own commit OID.

Repair creates only immediate required child directories and empty mode-0644
`.gitkeep` files. Both transactions use an alternate index initialized from
locked HEAD. They synchronize only owned real-index paths after verifying the
commit. Rollback restores structural shape while retaining both receipts and
spent identities. A bare revert deleting either receipt is an audit violation.
Failure recovery must retain concurrent replacements; cleanup failures after a
verified commit are reported as committed outcomes, never silently inverted.

## Verification boundary

Synthetic tests separately cover ordinary missing `active`, the named archive
exception, and independently flag-selected lifecycle-disabled non-archive
modules. They cover declared analytics additions and file/directory extension
kinds without inventing content. Full synthetic action tests compare unrelated
staged entries and dirty fingerprints before/after each action, check the final
receipt store, and require two sanctioned commits with no audit violations.

Private gates and the corrected full private validator are not run: the latest
owner instruction prohibits live-vault access and validator installation.
Public shape tests do not substitute for that full-validator 0/0 acceptance
dependency. No live readiness or live repair completion is claimed.
