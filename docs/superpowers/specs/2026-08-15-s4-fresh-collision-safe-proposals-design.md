# S4 Fresh, Collision-Safe Proposals Design

**Date:** 2026-08-15

**Status:** Approved

**Base:** `origin/main` at `90b753b` (S3 plus merged S1 containment), plus the rebased local Gitleaks baseline hotfix (`24da1f6`)

## Objective

Safety Foundation S4 makes classification proposals collision-safe and binds
each proposal to the exact source receipt bytes reviewed when it was created.
Approval must refuse a changed or missing source before any filesystem or Git
mutation, while leaving the proposal and all unrelated state untouched.

S4 hardens the existing S1-S3 flow. It does not implement S5 transaction
isolation or general S6 error presentation.

## Proposal Identity

Every new proposal uses this identifier format:

```text
YYYYMMDDTHHMMSS-<32 lowercase hexadecimal characters>
```

The timestamp is generated from the proposal creation time. The suffix comes
from `secrets.token_hex(16)`, providing 128 bits of cryptographic randomness.
The timestamp is readable metadata, not the collision boundary.

The proposal filename is exactly `<id>.yaml`. A stored proposal is valid only
when all of these conditions hold:

- its filename ends in `.yaml`;
- its filename stem matches the identifier grammar;
- its stored `id` is a string matching the same grammar; and
- its stored `id` exactly equals the filename stem.

Identity validation happens before mixed-action dispatch. Classification and
registry-delete proposals therefore share one identity contract, while their
action-specific schemas remain separate.

Creation remains exclusive. The implementation opens the candidate path in
exclusive-create mode, never overwrite mode. If a generated ID already exists,
it generates a new random suffix and retries a small bounded number of times.
Exhaustion raises a typed creation error without modifying an existing record.

## Classification Source Integrity

Every classification proposal requires:

```yaml
source_sha256: <64 lowercase hexadecimal characters>
```

The value is SHA-256 over the exact source receipt bytes. It is computed before
YAML serialization and is not based on decoded text, normalized newlines, Git
blob framing, or front-matter fields.

`source_sha256` is valid only when it is a string matching
`^[0-9a-f]{64}$`. Missing, uppercase, shortened, extended, or non-string values
fail closed. Pre-S4 classification proposals without `source_sha256` are not
migrated or grandfathered; they must be rejected and recreated.

Registry-delete proposals do not gain `source_sha256`, because they have no
single source receipt. They do use the shared collision-safe identity rules.

## Safe Source Snapshot

Source bytes are read through a focused no-follow helper. The helper opens the
already-canonical S3 source path with `O_NOFOLLOW` where supported, verifies via
`fstat` that the opened object is a regular file, and reads from that file
descriptor. A missing leaf and an unsafe or redirected leaf remain distinct
fail-closed cases.

Proposal creation resolves the S3 canonical destination first, then reads one
source snapshot and stores its SHA-256.

Approval performs these steps in order:

1. Validate request-local scope and the lexical outbox boundary.
2. Validate proposal filename, stored ID, record schema, action, required hash,
   and the S3 canonical source and destination.
3. Revalidate the proposal leaf.
4. Open and read the source once through the no-follow snapshot helper.
5. Raise `MissingProposalSource` when the canonical source is absent.
6. Hash the snapshot and raise `StaleProposalSource` when it differs from the
   proposal's `source_sha256`.
7. Decode and apply the `sub:` transformation to that same verified snapshot.
8. Revalidate S3 path boundaries, then perform the existing move and write the
   already-derived bytes before staging and committing.

No filesystem or Git mutation occurs before step 8. Approval never hashes one
read and then consumes a second source read, eliminating the source-content
check/use race. Rollback after a mutation or injected Git failure remains S5.

## Typed Refusals

The outbox defines this exception hierarchy:

```text
OutboxError
└── ProposalFreshnessError
    ├── MissingProposalSource
    └── StaleProposalSource
```

Identity, schema, hash-format, scope, destination, and no-follow violations
remain invalid-record or boundary errors. They fail closed and preserve the
proposal. They are not converted into freshness errors.

For freshness refusals, the approval route returns the existing outbox partial
with a visible `role="alert"` message and HTTP 200 so HTMX swaps it into the
current screen:

- Missing: `Approval refused: source is missing. Restore it or reject the proposal.`
- Stale: `Approval refused: source changed since this proposal was created. Create a fresh proposal.`

The proposal remains listed. S4 adds no general exception handler and does not
change presentation for unrelated invalid-record or Git failures; those belong
to S6.

## Components

### `app/proposal_identity.py`

Owns the shared identifier grammar, secure generation, bounded collision retry
support, and record-ID/filename equality validation. It has no outbox or
registry dependency, preventing circular imports.

### `app/outbox.py`

Adds `source_sha256` to `Proposal`, validates classification integrity, reads
exact source snapshots, raises typed freshness refusals, and makes approval
derive output from the verified snapshot.

### `app/registry.py`

Uses the shared identity generator and validator for registry-delete proposals,
retains exclusive creation, and validates stored ID against the filename before
executing a delete.

### `app/main.py` and `templates/blocks/outbox_list.html`

Catch only the two freshness exceptions and render the freshness-specific
alert. Existing broad error behavior is unchanged.

## No-Mutation Contract

For changed, missing, malformed-hash, and invalid-identity cases, tests snapshot
state immediately before approval and assert that the attempted approval adds
no mutation:

- Git `HEAD` is unchanged;
- the Git index is unchanged;
- tracked and untracked status is unchanged;
- the proposal exists with identical bytes;
- no destination exists;
- source and unrelated file bytes are unchanged relative to the pre-attempt
  state.

A test that intentionally changes or removes the source takes its baseline
after that setup, so it distinguishes the precondition from approval side
effects.

## Test Strategy

Strict TDD adds focused tests for:

- two proposals for one receipt under a frozen clock producing unique IDs and
  paths while preserving both records;
- exact-byte SHA-256 persistence;
- exclusive creation preserving an existing collision target;
- malformed IDs, ID/filename mismatches, and malformed or missing hashes;
- typed stale-source and missing-source refusals with full no-mutation proof;
- freshness-specific route alerts with the proposal preserved;
- successful approval and Git revert using the verified snapshot;
- mixed classification/delete outbox dispatch under the shared identity rules;
  and
- same-second registry-delete proposal preservation.

The full regression suite must retain S1 ingest and revert behavior, S2
request-local concurrency, S3 canonical destinations and lexical no-follow
boundaries, traversal and symlink refusals, cross-scope isolation, and registry
delete behavior.

## Completion Gates

Before the final S4 commit is reported:

- focused S4, outbox, route, and registry tests pass;
- the full public pytest suite passes;
- the private unittest suite passes;
- `check_v2` reports 0 errors and 0 warnings;
- the policy-enforcer self-test passes;
- pinned Gitleaks passes;
- public and combined repository audits pass;
- `git diff --check` passes;
- a whole-branch review finds no S5, S6, instance-specific, private-vault, or
  unrelated changes; and
- private-vault Git status, working diff, and cached diff are byte-identical to
  their pre-S4 fingerprints.

The branch is committed locally but is not merged, pushed, submitted as a PR,
or removed until explicitly authorized.
