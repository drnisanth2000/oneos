# Proposed governed lifecycle repair

Status: design proposal only. No repair capability is implemented or authorized
by this document. No outbox record is created by documenting this proposal.

The lifecycle validator must refuse incomplete required structure. Conventions
v2 §3 governs: ordinary modules require `_templates/`, `active/`, `archive/`,
and `status.md`; `12-archive` omits `active/`, `archive/`, and `status.md`.
The analytics module additionally requires its registry-declared `snapshots/`,
`dashboard.md`, and `kpis.yaml`. Module activation comes from explicit entity
flags. Extension declarations retain their directory/file distinction.

## Authority required before implementation

The current documented direct writers cover intake, registry add/edit, and
the tested rename operation. They do not authorize general structural repair.
The generic transaction helper and cutover commands are not substitutes for
that authority. Gate 3 must recognize the precise repair action and changed
paths before the operation can be considered sanctioned.

The owner would need to approve a narrowly scoped `lifecycle_repair` action,
its policy rule, review interface, receipt schema, and audit recognition as
Phase 1 maintenance. Any frozen policy or registry change needs its own
owner-authored decision. That approval would authorize development of the
operation, not a live repair batch. A live batch would require separate review
and approval through the established outbox boundary.

## Proposed operation

1. A read-only planner enumerates manifest-required modules and required
   children from the authoritative lifecycle contract and registry extensions.
   It reports missing entries, wrong kinds, and redirections separately.
   Wrong-kind or redirected entries are hard refusals, not repair candidates.
2. A proposal binds the canonical vault identity, entity scope, baseline HEAD,
   relevant registry hashes, exact required-shape version, and every proposed
   path. Each item includes the expected absent preimage, parent-directory
   identity, content hash where applicable, and provenance to its declaration.
3. The initial supported repair is limited to missing required directories
   beneath existing valid module roots. Each directory receives a tracked
   `.gitkeep`, making the approved structure visible to Git and reversible.
   Missing module roots or required content files are refused by this initial
   action. A separate reviewed content proposal must supply real content;
   the planner must never fabricate status, dashboard, or KPI documents.
4. Preview displays the complete diff and per-item provenance. Approval binds
   the exact proposal bytes, including the directory manifest. No incidental
   directory or content creation is permitted.
5. The writer acquires the existing action lock, revalidates all bound evidence,
   and resolves scope and parents without following symlinks. A changed HEAD,
   changed declaration, changed parent, or newly occupied target refuses the
   entire batch before mutation. Creation uses descriptor-relative no-follow
   checks and no-overwrite semantics.
6. One isolated Git transaction commits only the reviewed placeholder files
   and the corresponding action receipt. Its commit type must be explicitly
   added to policy and Gate 3; a suggestive commit message alone grants no
   authority. Unrelated staged, unstaged, untracked, and ignored entries remain
   unchanged. Failure restores owned filesystem, index, and proposal state.
7. The receipt correlates every placeholder with its directory and proposal.
   Gate 3 validates both the committed paths and filesystem transitions,
   including empty and Git-invisible entries. Rejecting a proposal uses the
   existing reviewed-record semantics and creates no structure.

## Required evidence before any live batch

- RED-first failures for tampered proposals, stale parents/registries/HEAD,
  occupied targets, symlinks and wrong kinds, cross-scope paths, and injected
  transaction failures.
- One approved batch produces exactly one commit; `git revert` restores the
  pre-repair filesystem shape, including directory removal, with no manual
  cleanup. If plain Git revert cannot satisfy that proof, the mechanism must
  be redesigned before authorization; a receipt alone is not sufficient.
- A rollback test preserves unrelated staged and unstaged changes and all
  pre-existing evidence byte-for-byte.
- The full validator reaches zero errors and warnings on the repaired
  synthetic fixture. Private integration tests, policy checks, and combined
  repository-plus-vault history audit pass.
- Fresh private preimages and per-item inventories account for every protected
  receipt, raw original, outbox record, and comparison copy. Evidence remains
  private and is never deleted as part of repair.

Passing these checks would not authorize a Gate 1 rerun, deployment, or Phase 2.
Each remains a separate owner decision.
