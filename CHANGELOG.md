# Changelog

Merged milestones on `main`, newest first, by pull request. Evidence, test
counts and gate results are in `docs/STATUS.md`; this file only indexes what
landed and when. The published history starts at the 2026-08-07 baseline.

## Unreleased

- Home is the master view (read-only, plan 1 of 4): every entity's notes in
  Draft, Waiting, To review and Done, with entity chips, sort, a read-only card
  popup, a sidebar Home row, and phone and tablet layouts.
- Backups run only when the owner runs `backup`: no scheduled backup job,
  and `doctor` reports backup state without failing on it.
- Documentation reconciled with the code: architecture map, configuration
  reference, README, build-step states, and the Board's place in the record.
- The Board reuses the inbox's no-follow path guards instead of a copy.
- Console visual refresh: one token set (system fonts, 4pt spacing, light and
  dark), an inline icon sprite, a sidebar with per-module links and a
  bottom-left Settings sheet (theme, accent, terminal-only backup and
  recovery), a segmented Board toolbar, and phone and tablet layouts.

## 2026-10-27

- #41 Phase 3 public slices on synthetic fixtures: unlocked PDF archive,
  redacted parser fields, learned rules without Trash authority, encrypted PDF
  unlock, frozen cleanup with recovery, and the mailbox screens. Records the
  owner's 2026-10-26 authorization and the Phase 3 external-action exception.

## 2026-10-26

- #42 Record the Board screen in the agent rules.
- #40 Record the unauthorized Phase 3 email and PDF intake design.
- #39 Board: a read-only, single-entity view of the inbox, proposals and filed
  notes (list, board, gallery, calendar, planner, life map), inbox drops
  through the existing proposal route, and quick capture through ingest.

## 2026-09

- #38 (09-13) Production-shaped local runtime and owner security: password +
  TOTP sign-in, Caddy on loopback HTTPS, Compose, encrypted backups.
- #37 (09-12) Record the Gate 1 live trial.
- #36 (09-10) Fix the Gate 3 lifecycle rejection audit.
- #35 (09-10) Govern lifecycle repair and rollback through reviewed receipts.
- #34 (09-09) Clarify the lifecycle archive exception and propose governed
  repair.
- #33 (09-08) Record the trusted-local Gate 2 pass.
- #32 (09-05) Record the trusted-local Gate 3 pass.
- #31 (09-05) Make classification reviews readable.
- #30 (09-05) Step 6: registry-backed manual triage destinations.

## 2026-08

- #29, #28 (08-30) Harden Gate 3 filesystem evidence and reconcile its status.
- #27 (08-29) Audit sanctioned S7 quarantine outcomes in Gate 3.
- #26 (08-29) Reconcile Phase 1 completion and live-trial next steps.
- #25 (08-29) Inherited item 3: declare route failure contracts.
- #24 (08-28) Inherited item 4: dependency-time vault filesystem failures.
- #23 (08-28) Inherited item 2: short-identifier privacy in Markdown prose.
- #18–#22 (08-27 to 08-28) Short-identifier cutover: design, Stage A,
  structured reference migration, and the post-cutover identifier floor.
- #17 (08-26) Plan inherited safety items 2 to 4.
- #16 (08-25) Safety Foundation S7: bind actions to reviewed proposal bytes.
- #15 (08-23) Safety Foundation S6: visible Console failures.
- #12, #13 (08-16) CI: add the Claude GitHub Actions workflow and
  authenticate it with a subscription OAuth token.
- #11 (08-16) Reconcile Safety Foundation S1–S5 documentation.
- #10 (08-16) Safety Foundation S5: isolated Git approval transactions and
  path-aware Gate 3.
- #9 (08-16) Safety Foundation S4: fresh, collision-safe proposals.
- #8 (08-16) Allow the retained synthetic PII fixture.
- #7 (08-15) S1: contain raw archives on scoped intake.
- #6 (08-15) Safety Foundation S3: server-owned classification destinations.
- #5 (08-12) Reset the publication gate architecture.
- #3, #1 (08-07) Activate the agent pull-request workflow.
- 2026-08-07 Baseline: Phase 1 steps 1–10 (skeleton, sidebar, schema,
  rename, folder intake, triage, outbox, approve/reject, registry delete,
  email intake).
