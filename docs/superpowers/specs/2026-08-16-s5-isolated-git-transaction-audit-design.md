# S5 — Isolated Git Transaction and Audit

**Status:** Approved

**Base:** `origin/main` at `3c56119` (merged S4)

## Objective

Make each approved action an isolated, exactly-scoped Git transaction. An
approval must commit only its reviewed paths, preserve unrelated staged and
unstaged work, and restore its filesystem, index, proposal, and Git state on
failure. Gate 3 must sanction a commit only when both its message type and its
actual changed paths match an allowed action.

## Approved scope

The new transaction service is used by:

- classification approval; and
- approved registry deletion.

It is internally list-capable, but S5 adds no multi-proposal route or UI. In
S5, an "approved batch" means the exact reviewed path set for one existing
approval action. Intake keeps its S1 path-limited commit and cleanup logic.
Rename and direct registry editing keep their current mutation flows. Gate 3
still audits every sanctioned commit type, including `ingest:`.

## Non-goals

- No multi-proposal route, screen, or workflow.
- No general S6 error presentation.
- No migration of intake, rename, or direct registry-edit mutations into the
  new service.
- No new dependencies, daemon, queue, database, physical subfolder, or private
  vault value.
- No broad reset, stash, clean, or rollback of unrelated vault work.

## Transaction model

### Data-driven plan

Callers submit a complete immutable plan rather than an arbitrary callback.
The plan contains:

- the exact commit message;
- an ordered list of reviewed filesystem changes;
- the exact paths that may enter the commit; and
- owned untracked side effects, such as the proposal file, which must be
  removed on success and restored on failure.

Each filesystem change specifies the expected initial state and desired final
state. State includes absence versus a regular file, exact bytes, and the file
mode needed for faithful restoration. Paths are vault-relative, lexical,
duplicate-free, and validated before use. Symlinks, redirected leaves,
directories, `.git`, and paths outside the vault fail closed.

This shape supports multiple reviewed changes internally without creating a
batch approval interface.

### Callers

Classification approval supplies:

- the verified inbox receipt bytes as the expected source;
- source absence as the final source state;
- destination absence as the expected destination state;
- the already-derived approved bytes as the final destination state;
- the exact stored proposal bytes followed by proposal absence; and
- only source and destination as commit paths.

Registry-delete approval supplies:

- the current registry bytes and newly rendered registry bytes;
- the exact stored proposal bytes followed by proposal absence; and
- only the applicable registry file as a commit path.

S2 request scope, S3 canonical destinations, S4 freshness verification,
proposal identity validation, and registry reference recounting finish before
the transaction begins. Callers translate transaction errors into their
existing domain error families; general route presentation remains deferred to
S6.

## Isolation and commit flow

### One approval per vault

OneOS holds a non-blocking advisory file lock derived from the vault's Git
directory. Only one OneOS approval transaction may run per vault. A second
approval fails before mutation with a typed busy error and may be retried.
There is no request-path waiting queue.

### Hybrid dirty-work policy

Unrelated staged and unstaged work is allowed. Before mutation, every reviewed
commit path must have the expected worktree state and no unexpected staged
change. Owned proposal state must also match exactly. A dirty reviewed path is
refused before mutation; the rest of the vault is not required to be clean.

### Separate Git index

The transaction creates a temporary alternate Git index outside the vault and
initializes it from the starting `HEAD`. After applying only the plan's desired
filesystem states, it stages only the reviewed commit paths in that alternate
index. It verifies that the staged path set exactly equals the plan before
running the repository's existing Git hooks and creating one commit.

The user's real index is never used to construct the commit. After commit
creation, only the reviewed entries in the real index are synchronized to the
new `HEAD`; all unrelated index entries remain byte-for-byte equivalent. The
resulting commit is inspected again and must contain exactly the reviewed path
set. Temporary index state is removed on every exit.

No `git stash`, broad `git add`, `git reset --hard`, or repository-wide clean is
permitted.

## Failure and rollback

Before mutation, the transaction records:

- starting `HEAD`;
- exact owned filesystem and proposal state;
- exact real-index entries for reviewed paths; and
- fingerprints of unrelated staged and unstaged state for proof.

Injected or real failures may occur during filesystem application, alternate
index preparation, staging, hooks, commit, commit verification, or real-index
synchronization. For an ordinary failure, the transaction restores its owned
files, proposal, reviewed real-index entries, and starting `HEAD`, then proves
that unrelated state is unchanged.

Rollback is ownership-aware. Before restoring a path, S5 requires its current
state to equal a state written by this transaction. If another actor changed
the same path meanwhile, S5 does not overwrite the newer state. It raises a
typed recovery error naming the affected runtime paths for manual recovery.
Unrelated work remains untouched in both ordinary and recovery-error cases.

The internal error family distinguishes:

- vault busy;
- reviewed-state conflict;
- Git or hook failure with successful rollback; and
- rollback blocked by a concurrent same-path change.

These errors are tested but do not gain general UI presentation until S6.

## Gate 3 audit

Gate 3 stops treating a prefix alone as sanction. Its session snapshot records
the starting `HEAD` and per-path fingerprints for all initially dirty staged,
unstaged, and untracked entries. The check phase examines every new commit's
message and name-status path set, plus all new or changed dirty state.

Commit rules are action-specific:

- `ingest:` adds exactly one redacted inbox receipt beneath an entity's
  `00-inbox/active/` path;
- `outbox:` contains only the reviewed inbox-source deletion and canonical
  active-destination addition for one entity;
- `registry:` contains only the registry path allowed by the registry action;
  and
- `rename:` is restricted to the exact path envelope produced by the existing
  sanctioned rename operation.

A valid-looking prefix with an unrelated path is a violation. The audit uses
runtime structure and generic conventions, never instance slugs.

Pending canonical proposal YAML files beneath an entity's `outbox/` remain the
only sanctioned new uncommitted writes. An uncommitted inbox receipt is a
violation because S1 requires intake to commit immediately. If a path that was
already dirty at snapshot time changes content or staging state during the
session, Gate 3 reports it rather than hiding it by filename subtraction.

## Required behavioral tests

### Transaction isolation

- Classification approval succeeds with unrelated staged and unstaged edits,
  commits only source and destination, and preserves unrelated bytes and index
  state exactly.
- Registry-delete approval provides the same proof while committing only its
  registry file.
- A reviewed path with unexpected staged or unstaged state is refused before
  mutation while unrelated dirty paths remain allowed.
- Lock contention raises the typed busy error with no mutation.
- The service accepts an internal list of reviewed changes while public routes
  remain single-proposal.

### Rollback

- Failure injection at filesystem application, alternate-index setup, staging,
  hook/commit, commit verification, and real-index synchronization restores
  `HEAD`, owned files, proposal bytes, reviewed index entries, and all unrelated
  state.
- A concurrent same-path change during rollback is preserved and produces the
  typed recovery error.
- Temporary index and lock state do not leak after success or failure.

### Audit and reversibility

- Misleading sanctioned prefixes with unrelated paths fail Gate 3.
- Valid and invalid `ingest:`, `outbox:`, `registry:`, and `rename:` path sets
  are covered.
- New proposal writes are allowed; new uncommitted inbox receipts are refused.
- Changes to an already-dirty baseline path are detected.
- Each classification or registry-delete approval produces exactly one commit,
  and one `git revert` restores every committed path in that approval.
- Existing S1 ingest, S2 scope/concurrency, S3 destination, and S4
  identity/freshness/no-follow regressions remain green.

## Completion gates

- Focused transaction, outbox, registry, ingest, Gate 3, route, scope, and
  adapter tests.
- Full public pytest suite.
- Full private unittest suite.
- `check_v2`: zero errors and zero warnings.
- Policy-enforcer self-test.
- Pinned Gitleaks.
- Public and combined repository audits.
- `git diff --check` and final whole-branch review.
- Private-vault status, worktree diff, and cached-diff fingerprints exactly
  equal before and after all private gates.

The S5 branch remains local until publication is explicitly authorized.
