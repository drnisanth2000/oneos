# Safety Foundation S1-S4 — as built, with S5 addendum

**Status:** Implemented and merged

**Reconciled:** 2026-08-16

**Authority:** `AGENTS.md`, `BUILD.md`, the private OneOS specification, and
the merged implementation. When an old execution plan disagrees with current
code or this record, current code and the higher authorities win.

## Why this document exists

S1-S5 hardened an already working Phase 1 surface. The work found real defects
that ordinary happy-path tests had missed, but it also exposed avoidable
process problems: dependent branches started before predecessors were merged,
two clones carried different histories, step boundaries blurred across tasks,
and a custom publication scanner grew beyond its proper responsibility.

This document records the built guarantees, the defects that changed the final
design, and the rules future work must retain. It is not an execution plan.

## Completion record

| Step | Built guarantee | Mainline evidence |
|---|---|---|
| S1 | New redacted intake is one tracked receipt-only `ingest:` commit; duplicate intake is a no-op; folder raw archives stay outside the vault and restore safely on failure. | Core S1 landed in the S2/S3 lineage; containment completed through PR #7 and main `90b753b`. |
| S2 | Every request and adapter operation owns one immutable, manifest-validated entity scope; shared mail routes by exactly one configured recipient owner. | S2 commits are in the PR #6 lineage merged at `21da6fe`. |
| S3 | One canonical server resolver owns module, sub, block, source leaf, and destination lifecycle path; stored proposals are revalidated before use. | PR #6 merged at `21da6fe`. |
| S4 | Proposal identity is collision-safe, exact source bytes are hashed, approval consumes one verified no-follow snapshot, and stale/missing sources are visibly refused before mutation. | PR #9 merged at `3c56119`. |
| S5 | Approved actions use exact-path isolated Git transactions with ownership-aware rollback; Gate 3 validates messages, paths, and dirty-state fingerprints together. | PR #10 merged at `0f71cd3`. |

After rebasing this reconciliation onto the S5 merge baseline, the public suite
contained 603 passing tests. The most recent private integration gate contained
37 passing tests; structural validation, policy, Gitleaks, and public/private
audits passed with Grey Matter fingerprints unchanged.

## S1 — ingest and raw-source containment

### Final boundary

- `commit_inbox_item(scope, ...)` is the only receipt write/commit boundary.
- A successful new intake creates one fixed-message commit changing exactly one
  redacted receipt path.
- Duplicate identity returns the tracked receipt without changing `HEAD` or
  moving the raw source.
- Folder input archives raw bytes outside the vault before receipt commit and
  restores the source on commit failure without overwriting another file.
- Source and archive I/O are anchored with no-follow file descriptors; regular
  file identity, lexical containment, physical containment, and cleanup are
  checked before destructive source removal.

### Defects that changed the implementation

- Checking only a resolved archive path allowed a vault path, case alias, or
  symlinked path to cross the raw-content boundary.
- Reusing pathnames after validation allowed archive or source symlink
  rebinding between check and use.
- Reopening the dropped source by pathname allowed a source-to-symlink swap
  after validation.
- Rollback originally did not preserve all source mode/timestamp metadata and
  could under-report cleanup failure.
- A scope rooted at another repository could be paired with the requested
  vault unless repository identity was checked.

### Intentional threat boundary

OneOS prevents in-operation path substitution and fails closed on detected
reparenting. It cannot stop another equally privileged process from moving an
archive after ingestion has returned. Stronger protection would require a new
storage/permission policy, such as a separately controlled filesystem; S1 did
not invent that policy.

## S2 — immutable entity authority

### Final boundary

- `Scope` is immutable and constructed from a registered entity.
- Services derive entity identity from the scope; they do not accept another
  independent entity slug.
- Stored proposal paths, registry queries, `books.db` reads, inbox/outbox views,
  and mutations remain inside the bound entity.
- Shared-mailbox routing considers only approved recipient headers and requires
  exactly one manifest owner. Unknown or ambiguous recipients create nothing.
- Email is acknowledged as seen only after the receipt commit succeeds and the
  IMAP server confirms the acknowledgement.

### Defects that changed the implementation

- Entity-root and discovered-leaf symlinks could escape a naive scoped scan.
- Falsy malformed manifest structures could be mistaken for an absent optional
  value instead of failing closed.
- Delete-proposal filenames and system registry paths needed the same anchored
  traversal/symlink denial as content paths.
- Ordinary message fetch could mark rejected email seen before routing or
  commit. Switching to peek avoided that, but successful mail then needed a
  deliberate post-commit acknowledgement and non-OK response handling.
- Concurrency tests were more valuable than source-text assertions: overlapping
  real requests proved that data and rendering did not cross entities.

## S3 — canonical destinations and stored-record trust

### Final boundary

- A pure destination service reads fresh registries and returns one canonical
  destination for the bound scope.
- Module activation uses entity `flags:` only. Registry declaration, lifecycle
  directory presence, sub ownership, and optional sub flag are all required.
- Block is derived from the module registry; client and stored values are only
  tamper claims to compare.
- A path-like filename is rejected, never silently reduced to a basename.
- Proposal discovery, preview, approval, and rejection validate stored shape,
  entity, source, destination, block, module, and sub before unsafe reads.
- Module-general classification uses explicit `sub: null`; approval removes the
  triage `sub:` field rather than writing an empty value.

### Defects that changed the implementation

- Resolving a receipt leaf before checking it dereferenced a redirected source
  too early. Lexical validation must precede no-follow open.
- Malformed YAML and wrong scalar types needed typed failure, not implicit
  stringification or `KeyError`/`TypeError` leakage.
- Test fixtures that omitted active modules or registered subs could make the
  intended safety test fail for the wrong reason.
- Hand-built JSON inside `hx-vals` was injectable by a resolver-valid quoted
  filename. The complete mapping must pass through Jinja's JSON encoder.
- A validator that iterates only over existing paths cannot report a required
  missing module. Synthetic absence tests remain mandatory.

## S4 — proposal identity and freshness

### Final boundary

- Proposal IDs are `YYYYMMDDTHHMMSS-<32 lowercase hex>` with 128 random bits.
- Proposal creation is exclusive and retries a bounded number of collisions;
  an existing record is never overwritten.
- Stored ID must match the filename stem before mixed-action dispatch.
- Every classification proposal stores lowercase SHA-256 of exact receipt
  bytes. Missing or malformed hashes fail closed; old proposals must be
  recreated.
- Approval validates the record and canonical paths, opens the lexical receipt
  no-follow once, verifies its digest, applies the content transformation to
  that same byte snapshot, and only then begins mutation.
- Stale and missing sources preserve proposal, source precondition, destination
  absence, `HEAD`, index, worktree state, and unrelated bytes while returning a
  focused visible alert.

### Defects that changed the implementation

- Creating `outbox/` before validating a redirected boundary violated the
  no-mutation contract even when proposal creation later failed.
- Collision preservation, retry, and exhaustion needed deterministic tests;
  uniqueness-only happy paths were insufficient.
- A second `resolve_stored()` before no-follow open reintroduced a TOCTOU
  window. The S3-validated lexical leaf must be opened directly.
- Preview needed the same stored-record revalidation as approval.
- Registry-delete proposal leaves needed the same redirected-leaf rejection as
  classification proposals.
- Freshness tests must take their no-mutation baseline after intentionally
  changing/removing the source, so setup is not confused with approval effects.

## S5 — isolated Git transactions and path-aware audit

### Final boundary

- Classification approval and approved registry deletion submit immutable
  plans containing exact before/after states, reviewed commit paths, and owned
  proposal side effects.
- A non-blocking per-vault lock serializes OneOS approval transactions without
  introducing a request-path queue.
- Commits are constructed through a temporary alternate index initialized from
  the starting `HEAD`; unrelated real-index, staged, unstaged, and untracked
  state is preserved.
- Every failure path restores owned filesystem, proposal, reviewed-index, and
  Git state when ownership is unchanged. Concurrent same-path replacements are
  preserved and reported for manual recovery.
- Gate 3 validates the action message and actual changed paths together for
  `ingest:`, `outbox:`, `registry:`, and `rename:` commits. It also fingerprints
  initially dirty state and permits only canonical pending proposal writes.

### Defects that changed the implementation

- Lock cleanup and safe path-state capture needed explicit failure coverage;
  successful-path tests did not exercise leaked transaction infrastructure.
- Git index encodings and replacement operations exposed restoration cases
  that were invisible with only ordinary text fixtures.
- Rollback needed compare-and-swap ownership checks for both paths and `HEAD`
  so recovery could not overwrite a concurrent actor's newer work.
- Gate 3 needed runtime-aware path envelopes and dirty-state fingerprints;
  commit prefixes and filename subtraction alone were not evidence of a
  sanctioned session.
- Proposal timestamps had to be bound to collision-safe IDs, and real-index
  restoration had to run even when a replace operation raised after partially
  changing state.

## Cross-cutting engineering lessons

### Paths and untrusted data

Validate lexical ownership before resolution, use no-follow file descriptors
for the object actually consumed, and avoid a second path lookup between check
and use. Apply the same rule to content files, proposal files, registry files,
archive directories, form values, YAML records, and template-embedded JSON.

### Failure ordering

"Rejected" is insufficient if validation created a directory, touched the
index, acknowledged a message, or read outside scope first. Tests must snapshot
all relevant state and prove failure occurs before the first mutation.

### Review and TDD

Independent task reviews found load-bearing defects in every step. Keep one
planned review/fix wave, but a regression introduced by that fix may receive a
focused corrective TDD pass without redefining scope. Never manufacture past
RED evidence or rewrite honest history; add mutation evidence and record the
process gap.

### Repository and session coordination

- Start dependent work only from the merged predecessor on fetched
  `origin/main`.
- A local `main` name is not proof of currency. Record the exact remote SHA.
- One step belongs to one task, branch, worktree, and pull request.
- Do not use the prior step's task as the discussion/implementation home for
  the next step.
- Handoffs must distinguish committed, pushed, PR-open, merged, and merely
  planned work. "Draft PR" means published commits, not unfinished code.
- Preserve pre-existing Grey Matter edits with before/after status and binary
  diff equality; do not require or manufacture a clean private worktree.

### Publication tooling

The first publication attempt over-expanded a custom Python scanner into
general credential/history detection. The successful reset assigned general
secret scanning to pinned Gitleaks and kept Python limited to finite OneOS
privacy rules. Gitleaks Git mode scans all local refs, so clone-specific stale
history must be diagnosed at the retaining ref before an ignore is added.

## Remaining attention

S5 closes the isolated-transaction and path-aware Gate 3 gaps for classification
approval and approved registry deletion. It intentionally does not migrate
intake, rename, or direct registry add/edit into the new transaction service.
S6 owns the remaining safe Console error taxonomy. Live Phase 1 timing and
session trials still remain after S6.

## Historical documents

The S2-S5 designs and plans under `docs/superpowers/` are retained because they
record decisions and test intent. They are marked implemented/historical and
must not be rerun. S1's durable as-built record is this document; its older
unmerged execution-plan branch is not current authority.
