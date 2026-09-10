# Lifecycle Repair Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans after the owner
> approves the policy/schema contract. Use independent scoped review.

**Goal:** Implement synthetic, governed missing-directory repair and rollback.

**Architecture:** Extend the existing outbox, receipt store, S5 alternate-index
transaction, and Gate 3. Share declaration enumeration, but independently
verify committed history and exact filesystem transitions.

**Tech Stack:** Existing Python, YAML, FastAPI/Jinja and Git; no new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-09-lifecycle-repair-design.md`.

## Global constraints

- Owner approved the action and receipt contract on 2026-09-10 for synthetic
  implementation only. No live-vault access or validator installation. Gate 1
  remains paused. Private verification is unavailable under this authority.
- Synthetic writes only; no live proposal, repair, rollback or policy change.
- Preserve all existing evidence, worktrees, branches and private candidates.
- Keep ordinary, archive, and disabled non-archive shape fixtures independent.
- Retain both receipts, spent IDs, exact structural restoration and two commits.
- No publication or deployment without separate authorization.

## Task 1: Declaration contract

Files: new `app/lifecycle_shape.py`, new `tests/test_lifecycle_shape.py`.

- [x] Confirm owner policy/schema decision and finalize strict data types before
  code. Use the public approved declarations; private access is prohibited.
- [x] Write independent otherwise-valid fixtures for ordinary missing active,
  archive without active/archive/status, and flag-selected disabled non-archive.
- [x] Observe RED for missing shape support. Cover roots, files, each extension,
  malformed declarations, symlink ancestors, wrong kinds and flag-only selection.
- [x] Implement read-only declaration enumeration and refusal categories from
  the approved design. Run focused tests and `tests/test_vault.py`.

## Task 2: Proposal, policy and receipt contracts

Files: new `app/lifecycle_repair.py`, `app/lifecycle_receipts.py`,
`tests/test_lifecycle_repair.py`, `tests/test_lifecycle_receipts.py`;
modify `app/action_receipts.py`, `app/outbox.py`.

- [x] Add RED tests for unsupported lifecycle actions and receipts, duplicate
  keys, extra fields, invalid hashes, identity collision, unknown policy action,
  absent authorization, wrong scope and spent proposals.
- [x] Implement strict versioned records and exact-byte preview binding, keeping
  legacy receipt behavior unchanged. Embed original proposal bytes in receipts.
- [x] Prove final-store resolution cannot reactivate either proposal and that
  malformed lifecycle receipts fail closed for display and approval.

## Task 3: Directory transaction and recovery

Files: modify `app/git_transaction.py`; new
`tests/test_lifecycle_transaction.py`; extend repair service.

- [x] Add explicit directory manifest tests without generalizing create_parent.
- [x] Capture RED evidence for stale HEAD/declarations/parents, occupied targets,
  changed modes, symlinks, wrong kinds, unrelated staged and unstaged state,
  untracked/ignored content and failures at each existing transaction checkpoint.
- [x] Implement descriptor-held directory operations under the action lock,
  alternate-index initialization from locked HEAD, exact commit verification,
  owned-index synchronization and ownership-aware recovery.
- [x] Compare four index and filesystem checkpoints across repair and rollback;
  assert exact unchanged unrelated projected trees and staged blob/mode entries.
- [x] Verify concurrent replacements survive failed recovery with explicit
  conflict outcomes. Rerun existing transaction and receipt suites.

## Task 4: Historical and filesystem audit

Files: modify `tools/gate3_audit.py`; new
`tests/test_gate3_lifecycle_repair.py`.

- [x] First prove existing unsupported-action handling rejects synthetic commits.
- [x] Implement commit-relative policy, declaration and receipt validation,
  original-repair correlation, and exact filesystem authorization consumption.
- [x] Add tampered-history RED cases for bare revert, missing/modified receipt,
  wrong repair OID/blob hash, extra paths, repeated/partial rollback, stale rules,
  unauthorized directories and ignored writes. Then verify explicit violations.
- [x] Capture intermediate transitions and assert two sanctioned commits, zero
  violating commits/writes, no unused authorization, immutable retained receipts,
  spent IDs and restored structure. Run the entire Gate 3 suite.
- [x] Implement the production external checkpoint journal specified in the
  design, with sequence/digest-chain and commit binding. RED-first tests omit,
  reorder and tamper with intermediate checkpoints despite valid commits;
  complete audit certification must refuse. Test unavailable evidence storage
  before mutation and failed persistence after commit as distinct outcomes.

## Task 5: Outbox integration and closing gates

Files: modify `app/main.py`, existing outbox templates, and focused route tests
only where dispatch/preview requires it; no new screen.

- [x] RED-first preview/approve/reject tests use exact review tokens and visible
  safe failure contracts; implement only approved action dispatch.
- [x] Obtain independent scoped review of policy, schemas, transactions, audit
  and receipt retention; resolve findings and rerun affected checks.
- [x] Run full public tests and source audits. Closing public-history results
  are retained in the external task evidence. Do not run private gates. Report that limitation separately from synthetic
  validation; no private validator installation or access is authorized.
- [ ] Require the corrected full validator to report zero errors and warnings
  on the repaired synthetic fixture. Treat its unavailability as an incomplete
  acceptance dependency, never as a passing focused-test substitute.
- [x] Supply scoped implementation interfaces against the approved schema.
  Public APIs and operational limits are recorded in
  `docs/LIFECYCLE-REPAIR-IMPLEMENTATION.md`; scoped briefs and RED evidence are
  retained in the task ledger.
- [x] Commit authorized local work after required checks. Report exact HEAD,
  evidence and remaining limitations. Do not push or open a PR.
