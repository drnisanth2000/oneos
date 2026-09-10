# Governed lifecycle repair design

Status: owner approved the action/receipt contract for synthetic implementation
on 2026-09-10. No live-vault access, repair, validator installation or Gate 1
resumption is authorized. Runtime actions remain explicitly policy-gated.

Baseline: `6d0c8e7d7bcb727e9b32190f197e911fa9e7f1bf`, freshly fetched before
branch creation. Governing proposal: `docs/LIFECYCLE-REPAIR-PROPOSAL.md`.

## Owner decision

Approve two explicit owner-approved maintenance actions, `lifecycle_repair`
and `lifecycle_rollback`, using the existing outbox review boundary. Both deny
by default and require explicit runtime policy authorization. Approve closed
version-2 receipts in the existing `<entity>/outbox/.receipts/<proposal-id>.yaml`
store. Version-1 receipts retain their current parsing and action semantics.
Frozen live policy changes require a separate owner-authored decision; this
design neither supplies that decision nor changes live policy.

The recommended implementation extends the existing transaction engine with
explicit reviewed directory changes. A separate transaction engine would
duplicate S5 recovery and index isolation. A script using mkdir and git revert
would bypass proposal authority and erase evidence. Neither alternative meets
the approved proposal's contract.

## Shape and planning

Add `app/lifecycle_shape.py` to enumerate required entries from explicit entity
flags, registry module declarations, and the conventions shape contract.
Every query starts with `scope.current_entity()`. Never merge archetype flags.
Lifecycle defaults to true; malformed booleans refuse. Enabled ordinary
modules require `_templates/`, `active/`, `archive/`, and `status.md`.
The named archive exception retains `_templates/` and omits the other three.
A separately flag-selected non-archive module with lifecycle disabled skips
lifecycle children, while retaining root and applicable extension validation.
Extensions preserve their trailing-slash directory versus file meaning.
Analytics additions remain registry-derived. Nested extension declarations
are reported as unsupported by this initial repair, never scaffolded.

Planning is read-only and checks every ancestor with no-follow descriptors.
Missing entity or module roots, missing required content files, wrong kinds,
redirections, and malformed declarations refuse the entire batch. Only absent
required immediate child directories beneath valid existing module roots are
eligible. No status, dashboard, KPI, curated content, or sub-module folders are
generated. A repair contains a nonempty, duplicate-free sorted manifest of
those directories and zero-byte mode-0644 `.gitkeep` files.

## Proposal and review

Add `app/lifecycle_repair.py` for strict proposal parsing, planning, preview,
and execution. Proposals bind version, action, proposal ID, creation time,
canonical vault identity digest, entity, baseline HEAD, policy digest,
registry digests, shape-contract digest, and exact ordered directory manifest.
Each manifest entry binds its relative path, declaration source, absent
preimage, parent device/inode identity and mode, expected directory mode,
placeholder path, file mode, and SHA-256. Identity evidence stays local and
must not expose absolute paths. Approval uses the existing exact-byte review
token; the digest is supplied separately to avoid self-reference.

Extend the existing outbox dispatch and preview to expose these two actions
with exact diffs and per-directory provenance. No standalone writer endpoint
or direct admin shortcut is introduced. Unknown actions, extra fields,
duplicate YAML keys, cross-scope paths, and spent identities fail closed.
Receipt-first projection remains authoritative before parsing a proposal.

## Receipts and historical authority

Add `app/lifecycle_receipts.py` for the closed version-2 schema and validation;
integrate it with `app/action_receipts.py` store validation and spent-ID lookup.
Both receipts contain version, action kind, proposal ID, review SHA-256,
entity, vault identity digest, starting HEAD, declaration/policy/shape digests,
exact proposal bytes, and the complete before/after structural manifest.
The embedded proposal is a canonical byte encoding whose decoded digest must
equal the review hash. This permits audit after proposal quarantine without
depending on a mutable working-tree record. No receipt contains its own
commit OID. Its identity is the containing commit OID plus receipt path and
Git blob OID, resolved externally.

A rollback receipt additionally names the repair commit OID, repair receipt
path and blob OID, and exact inverse manifest. Approval verifies the original
repair was sanctioned, both receipts are immutable, no rollback already
references that repair, and its own proposal is fresh. Both receipt IDs remain
spent after rollback. Neither action may delete or modify any existing receipt.
The final receipt store retains both new receipts and all prior evidence.

## Transaction and failure behavior

The current `TransactionPlan.create_parent` is limited to receipt creation;
do not relax it into arbitrary parent creation. Introduce a distinct explicit
directory-change contract in `app/git_transaction.py`. Creation/removal has
reviewed absence or owned-directory preconditions, held parent descriptors,
no-follow checks, modes, and recorded identities for recovery. Refuse aliases,
symlinks, wrong kinds, occupancy, stale HEAD, changed policy/declarations,
changed parent identity, and transaction-owned staged differences before writes.

Under the shared action lock, revalidate all evidence and capture real-index
and filesystem state. Initialize a temporary alternate index using `read-tree`
from locked HEAD; stage only manifest placeholder changes and the new receipt.
Keep proposal consumption as the existing exact-byte, quarantine-last owned
transition, not an incidental committed path. Verify parent, resulting blobs,
modes and exact changed-path set before synchronizing owned real-index entries.
No normal commit using the real index is allowed.

Rollback removes exactly the reviewed unchanged placeholders, then removes
only their proven-empty owned directories, and commits the new rollback receipt.
Any extra file, ignored entry, special entry, or changed directory identity
refuses the whole batch before mutation. Injected failure restores owned state
and index only while ownership remains provable. A concurrent replacement is
preserved and reported as a recovery conflict. Post-commit consumption failure
retains committed authority and reports the existing committed outcome class.

## Gate 3

Extend `_commit_is_sanctioned`, historical rules, receipt authorization
extraction, proposal/quarantine recognition, and filesystem transition checks.
Resolve policy and registry rules from each relevant commit, not current files.
Validate embedded proposal bytes, review digest, receipt schema, baseline
parent, and exact placeholder/receipt blob changes together. Resolve rollback's
original repair from retained history, prove it sanctioned, and reject repeat,
partial, expanded, unrelated or forged inverses. Commit messages select a
candidate action; they never grant authority.

Audit both commits and the retained final receipt store. Account separately
for directory creation/removal, placeholder writes, new receipt-store parent
creation where needed, proposal creation, and quarantine transitions. An extra
Git-invisible write must violate the audit. A bare revert deleting the repair
receipt is a violation. A no-op post-rollback structural delta does not excuse
missing intermediate evidence: fixtures capture every transition separately.

The production audit must consume an ordered checkpoint journal, not only a
baseline/final pair. Extend Gate 3 evidence serialization with a versioned
journal containing session ID, sequence number, previous-checkpoint digest,
HEAD, action/proposal ID, phase (before or after), and complete no-follow
filesystem/index evidence. Record before/after repair and before/after rollback
under the shared action lock; bind each after checkpoint to its verified commit.
Persist evidence to an explicitly configured trusted-local external store,
outside both vault and repository, with exclusive creation and no overwrite.
The audited session supplies its initial checkpoint digest independently of
the journal. Verify the digest chain, commit order, exact action coverage and
all adjacent transitions. Missing, truncated, reordered, mismatched or absent
intermediate evidence prevents a complete audit PASS even if both commits are
valid. Refuse before mutation if the evidence store cannot be initialized;
failure to persist evidence after a commit is a committed-but-audit-incomplete
outcome and must not trigger an unapproved inverse. The existing snapshot-only
path must refuse complete lifecycle-session certification. This documents
observed transitions within the cooperative-writer boundary, not continuous
detection of arbitrary transient writes between observations.

## Acceptance and boundaries

Four index checkpoints are mandatory: before/after repair and before/after
rollback. Compare exact unrelated index entries and their projected tree,
staged blobs/modes, unstaged bytes/kinds/modes, untracked and ignored state,
and protected evidence. Full index-tree differences may include only owned
paths. Injected failures restore the full original index tree and owned state,
except preserved concurrent replacements explicitly reported as conflicts.

The end-to-end synthetic proof requires two sanctioned commits, zero violating
commits or writes, no unclaimed authorizations, both immutable receipts in the
final store and history, both IDs spent, and exact structural restoration.
The final Git tree differs by retained evidence, as required. Ordinary missing
active, archive exception, and disabled non-archive fixtures are independent.

Run RED-first negative cases and focused regressions, then the public suite,
policy and publication audits, authorized read-only private gates with fresh
opaque preimages, and independent scoped review. Do not install or modify the
preserved private validator candidate. Its corrected full-validator proof is
a separate dependency; do not substitute the legacy validator's success for it.
Closing repair acceptance requires the corrected full validator to report zero
errors and zero warnings on the repaired synthetic fixture. If that dependency
is unavailable, report acceptance incomplete; focused shape tests are not a
substitute. Develop a fresh external copy only under its applicable authority;
never install or change the preserved candidate.

No live repair, proposal, rollback, Gate 1 trial, phase advance, deployment,
push, PR, merge, evidence deletion, or standing review waiver is authorized.
