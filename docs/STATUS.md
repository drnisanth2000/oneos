# OneOS — build status & findings

Living status for the code layer. Instance-specific decisions (bank/card
parsers, external-service integrations, entity names) live in the vault's
`decisions.md`, never here. This AGPL-3.0 repository is privately hosted and
intended for eventual public release; the vault is never published.

Build order and rules: see `../BUILD.md` and the spec at
`$ONEOS_VAULT/_system/docs/oneos-spec.md`.

---

## Phase 1 (triage) — steps 1–10 complete

All in git history, one commit per step. Plus two gate instruments:
gate-1 stopwatch on the triage screen, gate-3 audit at `tools/gate3_audit.py`.

### Exit gates (spec §11)

| Gate | State |
|---|---|
| 2 — one commit per approval, `git revert`-clean | **REOPENED** — existing test starts from a committed synthetic item; real ingest currently creates an uncommitted item |
| 4 — front-matter agreement with `policy_enforcer`, 100 files | **PASS** (100/100) |
| 5 — cold start to usable screen < 2s | **PASS** (~0.35s) |
| 3 — zero direct vault writes over a session | **REOPENED** — the auditor is prefix-only and does not yet validate changed paths or sanctioned `ingest:` commits |
| 1 — triage 20 items faster than Obsidian | **ready** — needs ~20 real inbox items; stopwatch on the triage screen |

Gates govern expansion, not usage. Phase 2 is not scoped until all five pass.

---

## Findings / decisions this session

- **Private GitHub agent workflow** — private GitHub CI is active. Codex cloud
  tasks use synthetic fixtures only. The synthetic public CI has no vault access;
  registry-derived validation remains a local private gate before merge.
- **Ingest write target** — redacted items are written directly to
  `<entity>/00-inbox/active/` with `sub: triage` (the sanctioned intake feed).
  The raw original never enters git; it is referenced by `source_ref` + `sha256`
  and kept outside the vault.
- **Real-ingest revert gap** — adapter-created receipts are currently
  uncommitted, while the gate-2 revert test begins with a committed fixture.
  Approval therefore has not proved that `git revert` restores a real ingested
  item to triage. The approved correction is one isolated `ingest:` commit for
  the redacted receipt before classification.
- **One ingest write path** — `app/ingest/base.write_inbox_item` is shared by
  the folder-drop and email adapters. Adding a source is a normaliser, not a new
  write path (spec §8.2).
- **PII filter (ADR-008)** — 11 deterministic classes, no LLM. The Verhoeff
  (Aadhaar) table had a typo that only a transposition-detection test exposed;
  now cross-checked against `python-stdnum` (0 disagreements over 50k numbers).
- **Rename** — all five axes; the fail-open `.sensitive` allow/except pair is
  rewritten atomically and a grep-gate refuses to commit on any residual slug.
  `books.db` references are *reported, not rewritten* (deferred; the opaque
  member id is not the registry id).
- **Registry delete** — routed through the outbox with a reference count
  (front-matter + workspaces + `books.db`); refused while any reference remains.
- **Front-matter schema** — presence-only, matching `policy_enforcer` exactly.
  Wiring the enforcer to *import* the shared model is deferred: the enforcer is
  stdlib-only by design, and editing it is a vault-side change.
- **Publication gates** — pinned Gitleaks owns general credential and reachable
  history scanning. `tools/public_repo_audit.py` owns only finite OneOS privacy
  rules; trusted local review adds private registry-derived terms before merge.
- **Remote-history evidence** — canonical `f84625b` is the fifth remote commit.
  Any earlier three-commit description applies only to the snapshot before PR
  #3, not to the current canonical remote history.
- **Layer names and ownership** — OneOS is the complete system and the human surface;
  Command Center is the deterministic orchestration boundary;
  Grey Matter is the system of record; Hermes is an asynchronous worker, never
  the orchestrator or approval authority.
- **Console navigation** — the workspace switcher selects an entity or saved
  scope. `Blocks / Modules` are two registry-backed views inside that scope.
- **Gate-3 audit gap** — commit-message prefixes alone cannot prove that a
  sanctioned commit touched only permitted paths. Safety Foundation S5 updates
  the auditor to inspect each commit's path set and recognize `ingest:` only for
  redacted receipts under `00-inbox/active/`.

---

## Deferred (not yet built)

- **Frontend drag-drop upload → ingest** — follows Safety Foundation hardening.
  A browser
  upload route saves the file and calls the existing `process_drop()`; same one
  write path, no new code.
- Email adapter is built and tested (`app/ingest/adapters/email.py`), but not
  yet wired to credentials/config or a schedule. Cadence is Hermes cron's job
  (spec §5), not this app's.
- ADR-008 escapee lint (re-scan committed content for missed patterns).
- Full Command Center dashboard cards and workspace CRUD beyond what the current
  Phase 1 screens require.

---

## Next step (Phase 1)

Complete `BUILD.md`'s Safety Foundation S1–S6. Start with commit-on-ingest,
then request-local scope, server-owned destinations, stale proposal detection,
isolated Git transactions, and visible Console errors. Drag-drop follows only
after these guarantees pass against real adapter output.

---

## Phase 2 (not started — only after the gates pass)

A **finance-ingest pipeline** is anticipated: deterministic statement/invoice
parsers produce *tabular* rows that land in `books.db` under `07-finance` — not
markdown notes (invariant: markdown for narrative, SQLite for tabular). Any
export to an external accounting system or a downstream finance service is a
**gated** action: the parser writes a proposal to `<entity>/outbox/`, a human
approves, and **Hermes** performs the push. The web app never calls external
services directly (spec §5.1).

The specific parsers and integration targets are instance data and live in the
vault's `decisions.md`, not in this repo.
