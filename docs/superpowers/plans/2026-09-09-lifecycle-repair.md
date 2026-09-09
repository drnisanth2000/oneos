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

- Owner-review draft; dependent implementation remains blocked on the explicit
  policy/schema decision. This is a sequenced work breakdown, not an approved
  executable implementation contract.
- Synthetic writes only; no live proposal, repair, rollback or policy change.
- Preserve all existing evidence, worktrees, branches and private candidates.
- Keep ordinary, archive, and disabled non-archive shape fixtures independent.
- Retain both receipts, spent IDs, exact structural restoration and two commits.
- No publication or deployment without separate authorization.

## Task 1: Declaration contract

Files: new `app/lifecycle_shape.py`, new `tests/test_lifecycle_shape.py`.

- [ ] Confirm owner policy/schema decision and finalize strict data types before
  code. Read authoritative private declarations through sanitized local access.
- [ ] Write independent otherwise-valid fixtures for ordinary missing active,
  archive without active/archive/status, and flag-selected disabled non-archive.
- [ ] Observe RED for missing shape support. Cover roots, files, each extension,
  malformed declarations, symlink ancestors, wrong kinds and flag-only selection.
- [ ] Implement read-only declaration enumeration and refusal categories from
  the approved design. Run focused tests and `tests/test_vault.py`.

## Task 2: Proposal, policy and receipt contracts

Files: new `app/lifecycle_repair.py`, `app/lifecycle_receipts.py`,
`tests/test_lifecycle_repair.py`, `tests/test_lifecycle_receipts.py`;
modify `app/action_receipts.py`, `app/outbox.py`.

- [ ] Add RED tests for unsupported lifecycle actions and receipts, duplicate
  keys, extra fields, invalid hashes, identity collision, unknown policy action,
  absent authorization, wrong scope and spent proposals.
- [ ] Implement strict versioned records and exact-byte preview binding, keeping
  legacy receipt behavior unchanged. Embed original proposal bytes in receipts.
- [ ] Prove final-store resolution cannot reactivate either proposal and that
  malformed lifecycle receipts fail closed for display and approval.

## Task 3: Directory transaction and recovery

Files: modify `app/git_transaction.py`; new
`tests/test_lifecycle_transaction.py`; extend repair service.

- [ ] Add explicit directory manifest tests without generalizing create_parent.
- [ ] Capture RED evidence for stale HEAD/declarations/parents, occupied targets,
  changed modes, symlinks, wrong kinds, unrelated staged and unstaged state,
  untracked/ignored content and failures at each existing transaction checkpoint.
- [ ] Implement descriptor-held directory operations under the action lock,
  alternate-index initialization from locked HEAD, exact commit verification,
  owned-index synchronization and ownership-aware recovery.
- [ ] Compare four index and filesystem checkpoints across repair and rollback;
  assert exact unchanged unrelated projected trees and staged blob/mode entries.
- [ ] Verify concurrent replacements survive failed recovery with explicit
  conflict outcomes. Rerun existing transaction and receipt suites.

## Task 4: Historical and filesystem audit

Files: modify `tools/gate3_audit.py`; new
`tests/test_gate3_lifecycle_repair.py`.

- [ ] First prove existing unsupported-action handling rejects synthetic commits.
- [ ] Implement commit-relative policy, declaration and receipt validation,
  original-repair correlation, and exact filesystem authorization consumption.
- [ ] Add tampered-history RED cases for bare revert, missing/modified receipt,
  wrong repair OID/blob hash, extra paths, repeated/partial rollback, stale rules,
  unauthorized directories and ignored writes. Then verify explicit violations.
- [ ] Capture intermediate transitions and assert two sanctioned commits, zero
  violating commits/writes, no unused authorization, immutable retained receipts,
  spent IDs and restored structure. Run the entire Gate 3 suite.
- [ ] Implement the production external checkpoint journal specified in the
  design, with sequence/digest-chain and commit binding. RED-first tests omit,
  reorder and tamper with intermediate checkpoints despite valid commits;
  complete audit certification must refuse. Test unavailable evidence storage
  before mutation and failed persistence after commit as distinct outcomes.

## Task 5: Outbox integration and closing gates

Files: modify `app/main.py`, existing outbox templates, and focused route tests
only where dispatch/preview requires it; no new screen.

- [ ] RED-first preview/approve/reject tests use exact review tokens and visible
  safe failure contracts; implement only approved action dispatch.
- [ ] Obtain independent scoped review of policy, schemas, transactions, audit
  and receipt retention; resolve findings and rerun affected checks.
- [ ] Run full public tests and audits. Run authorized trusted-local read-only
  gates with opaque before/after preservation proofs; report legacy-validator
  limitations separately from corrected synthetic validation.
- [ ] Require the corrected full validator to report zero errors and warnings
  on the repaired synthetic fixture. Treat its unavailability as an incomplete
  acceptance dependency, never as a passing focused-test substitute.
- [ ] Complete executable task details and interface signatures against the
  approved schema before execution; do not infer approval from this draft.
- [ ] Commit authorized local work after required checks. Report exact HEAD,
  evidence and remaining limitations. Do not push or open a PR.
