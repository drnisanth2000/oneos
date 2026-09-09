# Proposed governed lifecycle repair

Status: design proposal only. No repair capability is implemented or authorized
by this document. No outbox record is created by documenting this proposal.

The lifecycle validator must refuse incomplete required structure. Conventions
v2 §3 governs: lifecycle-enabled ordinary modules require `_templates/`, `active/`, `archive/`,
and `status.md`; `12-archive` omits `active/`, `archive/`, and `status.md`.
The analytics module additionally requires its registry-declared `snapshots/`,
`dashboard.md`, and `kpis.yaml`. Module activation comes from explicit entity
flags. Lifecycle-shape requirements apply only when `lifecycle_pattern` is
enabled (default `true`); explicitly disabled modules are excluded from those
checks, independently of the named archive exception. Their manifest-required
roots and applicable non-lifecycle/extension checks still apply. Extension
declarations retain their directory/file distinction.

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
6. Both repair and governed rollback must use the established S5 transaction
   contract in `app/git_transaction.py`: a temporary alternate index selected
   by `GIT_INDEX_FILE`, initialized with `read-tree` from the locked starting
   HEAD. Stage only exact reviewed paths in that index and verify the resulting
   commit against its parent and reviewed before/after states. A normal commit
   using the real index is forbidden. Synchronize only transaction-owned real
   index entries after verified success; preserve every unrelated entry.
   Failure restores owned filesystem, index, and proposal state when ownership
   is unchanged; a concurrent replacement must be preserved and reported.
7. One approved repair creates exactly one commit containing the reviewed
   placeholders, its immutable receipt, and only any proposal transition
   explicitly included in the reviewed transaction. The receipt binds proposal
   ID and exact-byte review hash, scope, baseline HEAD, registry/shape hashes,
   and each path's before/after kind and content hash. The receipt's committed
   blob is identified externally by its containing commit OID and path; it
   must not require a self-referential commit hash in its own bytes.
8. A governed rollback is a separately approved inverse of the repair-owned
   structural changes. It creates exactly one new commit through the same
   alternate-index contract, retains the original receipt unchanged, and adds
   an immutable rollback receipt. The rollback receipt binds its own proposal
   and review hash, original repair commit OID, original receipt path/blob hash,
   exact inverse path manifest, and fresh starting HEAD. Both receipts remain
   protected historical evidence and remain in the final receipt store. Spent
   proposal IDs stay spent; rollback must not resurrect an executable proposal.
   Remove only owned directories proven empty after removing their reviewed
   placeholders; occupied or changed paths refuse the rollback without cleanup
   of other content. A bare `git revert` deleting the repair receipt is not this
   sanctioned operation. Any supported revert commit must satisfy this same
   governed rollback contract; a `Revert` message is never authority.

## Gate 3 recognition and final audit state

Current Gate 3 does not sanction `lifecycle_repair` or its rollback. Future
implementation must add narrow policy, receipt schema, commit recognition
(including `_commit_is_sanctioned`), receipt authorization extraction, and
filesystem-transition rules for both actions before either can run. This
proposal does not change those rules today.

Gate 3 must validate the repair's approved exact-byte proposal, commit-relative
registry rules, immutable receipt, and exact changed-path set together. For a
rollback/revert, it must additionally resolve the original repair and receipt
from retained Git history, prove the original was sanctioned, and verify that
the structural delta exactly inverts that repair with only the permitted new
rollback receipt and reviewed proposal transition. It must reject forged,
unrelated, repeated, partial, or expanded inverses, missing/mismatched receipts,
and unknown actions even when their messages resemble sanctioned commits.
Historical correlation uses commit OID, receipt path/blob hash, proposal ID,
and review hash, not a current entity label or commit message alone.

Gate 3 audits both commits in the post-snapshot history; rollback never erases
or exempts the original repair from audit. It validates both receipt blobs
against their historical versions and the retained final store, and accounts
for all directory and Git-invisible transitions. A complete synthetic
repair/rollback sequence must yield two sanctioned action commits, zero
violating commits/writes, no unclaimed authorizations, both receipts present
and immutable, and the pre-repair structural shape restored. Existing protected
receipts, raw originals, quarantined proposals, and comparison evidence remain
unchanged. The final whole Git tree intentionally retains the two audit
receipts; structural inversion does not mean deleting historical evidence.

## Required evidence before any live batch

- RED-first failures for tampered proposals, stale parents/registries/HEAD,
  occupied targets, symlinks and wrong kinds, cross-scope paths, and injected
  transaction failures.
- RED-first transaction tests seed unrelated staged paths, unstaged edits,
  untracked/ignored entries, and protected evidence. Capture the real-index
  tree and entries plus dirty-state fingerprints before repair, after repair,
  before rollback, and after rollback. Compare exact unrelated index entries
  and their projected tree across all four checkpoints; full index-tree deltas
  may contain only reviewed transaction-owned paths. Verify unrelated staged
  blobs/modes and unstaged bytes/kinds/modes remain identical, with no unrelated
  content in either commit. Fingerprints for all unrelated dirty state must
  match; whole-state differences must be exactly the reviewed owned changes.
  On injected failures, require the full original index tree and owned state
  restored, preserving concurrent replacements under the existing S5 rule.
- RED-first receipt/audit tests first fail against the current unsupported
  action handling, then verify one repair commit followed by one governed
  rollback/revert commit. Assert both correlated receipts remain immutable in
  the final store and resolvable from history, both proposal IDs remain spent,
  the pre-repair structure is restored without manual cleanup, and Gate 3
  reports exactly two sanctioned action commits, zero violations, and no
  unclaimed authorization. Assert unrelated evidence is byte-identical.
- RED-first negative audit tests cover a bare revert that deletes evidence,
  forged/missing/changed receipts, wrong repair OID or receipt hash, unrelated
  or extra changed paths, repeat/partial rollback, stale HEAD/registries,
  occupied directories, and unauthorized Git-invisible writes. Each must fail
  closed with no mutation or, for synthetic tampered-history audit fixtures,
  an explicit audit violation. No generic revert exemption is permitted.
- The full validator reaches zero errors and warnings on the repaired
  synthetic fixture. Private integration tests, policy checks, and combined
  repository-plus-vault history audit pass.
- Fresh private preimages and per-item inventories account for every protected
  receipt, raw original, outbox record, and comparison copy. Evidence remains
  private and is never deleted as part of repair.

Passing these checks would not authorize a Gate 1 rerun, deployment, or Phase 2.
Each remains a separate owner decision.
