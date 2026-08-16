# OneOS — build status and findings

Living status for the code layer. Instance-specific decisions (bank/card
parsers, external-service integrations, entity names) live in the vault's
`decisions.md`, never here. This AGPL-3.0 repository is privately hosted and
intended for eventual public release; the vault is never published.

Build order and rules: see `../BUILD.md` and the spec at
`$ONEOS_VAULT/_system/docs/oneos-spec.md`.

Last reconciled: 2026-08-16, after Safety Foundation S5 merged into `main`.

---

## Phase 1 triage

Original steps 1-10 and Safety Foundation S1-S5 are complete. S6 is the
current step. Phase 2 is not authorized.

| Safety step | State | Merged outcome |
|---|---|---|
| S1 — commit on ingest | **COMPLETE** | Adapter intake creates one redacted receipt-only `ingest:` commit; duplicate intake is a no-op; raw folder archives are contained outside the vault. |
| S2 — request-local scope | **COMPLETE** | Immutable manifest-backed entity scope owns every request and adapter operation; shared mailbox routing uses exactly one configured recipient owner. |
| S3 — server-owned destinations | **COMPLETE** | One canonical resolver validates module/sub/flags/lifecycle paths, derives block, and revalidates stored proposals before reads or writes. |
| S4 — proposal identity and freshness | **COMPLETE** | Collision-safe proposal IDs, exact-byte source SHA-256, no-follow snapshots, and visible stale/missing refusals are merged. |
| S5 — Git transaction and audit | **COMPLETE** | Classification approval and registry deletion use exact-path alternate-index transactions with ownership-aware rollback; Gate 3 validates action-specific messages, paths, and dirty-state fingerprints. |
| S6 — Console failures | **IN DESIGN** | Design at `docs/superpowers/specs/2026-08-16-s6-visible-console-failures-design.md`, Review Pending after seven rounds. No application code written. |
| S7 — bound review tokens | **PROPOSED** | Discovered while designing S6; not designed. See `BUILD.md`. |

Merged S5 baseline: `0f71cd3`. Fresh verification of this reconciled branch
recorded 603 public tests. The most recent private gate recorded 37 private
tests, `check_v2` at 0 errors/0 warnings, policy self-test pass, clean
Gitleaks/public/private audits, and byte-identical Grey Matter before/after
fingerprints.

### Exit gates (spec §11)

| Gate | State |
|---|---|
| 2 — one commit per approval, `git revert`-clean | **AUTOMATED PASS; LIVE TRIAL PENDING** — adapter intake is committed before approval; classification and registry-delete approvals commit exactly reviewed paths; one revert restores the committed action. |
| 4 — front-matter agreement with `policy_enforcer`, 100 files | **PASS** (100/100) |
| 5 — cold start to usable screen < 2s | **PASS** (~0.35s) |
| 3 — zero unsanctioned direct vault writes over a session | **AUTOMATED PASS; LIVE SESSION TRIAL PENDING** — Gate 3 validates action-specific message/path pairs, initially dirty fingerprints, sanctioned proposal writes, and receipt-only `ingest:` commits. |
| 1 — triage 20 items faster than Obsidian | **READY FOR LIVE TRIAL** — requires about 20 real inbox items; stopwatch exists on the triage screen. |

Gates govern expansion, not usage. Live trials do not reopen completed S1-S5
unless they demonstrate a reproducible defect in those guarantees.

---

## Durable findings and decisions

- **Private GitHub agent workflow** — private GitHub CI is active. Codex cloud
  tasks use synthetic fixtures only. The synthetic public CI has no vault
  access; registry-derived validation remains a local private gate before
  merge.
- **One ingest write path** — `app/ingest/base.commit_inbox_item` is shared by
  folder and email adapters. A source adapter normalizes input; it does not own
  another vault-write path.
- **Raw content boundary** — raw input never enters the vault or Git history.
  Folder archive I/O is anchored/no-follow and outside the vault; receipts keep
  redacted provenance (`source_ref` and SHA-256).
- **Entity authority** — `entities.yaml` plus one immutable `Scope` is the only
  entity authority. URL/form/proposal values may be checked, never trusted as a
  second identity channel.
- **Destination authority** — active modules derive from `flags:` only;
  `archetype:` is never merged at read time. Block and final lifecycle path are
  server-derived. Module-general content is represented explicitly as
  `sub: null` in the proposal and no `sub:` field in approved content.
- **Proposal authority** — proposal ID must match its filename; classification
  proposals require lowercase exact-byte SHA-256. Pre-S4 records without the
  hash fail closed and must be recreated.
- **Transaction authority** — classification approval and approved registry
  deletion build immutable exact-state plans, commit through an alternate
  index, synchronize only reviewed real-index entries, and restore owned state
  on failure without overwriting concurrent same-path changes.
- **Gate 3 authority** — a sanctioned prefix alone is never enough. The audit
  validates each new commit's action-specific path set and detects changes to
  staged, unstaged, or untracked state captured at session start.
- **Publication gates** — pinned Gitleaks owns general credential and reachable
  history scanning. `tools/public_repo_audit.py` owns finite OneOS privacy
  rules; trusted local review adds private registry-derived terms.
- **Layer names and ownership** — OneOS is the complete system and human
  surface; Command Center is the deterministic orchestration boundary; Grey
  Matter is the system of record; Hermes is asynchronous and never approval
  authority.
- **Console navigation** — the workspace switcher selects an entity or saved
  scope. `Blocks / Modules` are registry-backed views inside that scope.

### Found while designing S6 — defects that predate it

These were discovered by review of existing code, not introduced by S6. None is
fixed yet; each is either scheduled inside S6 or recorded as its own step.

- **Approval is not bound to reviewed content.** A proposal id names a mutable
  file. See S7 in `BUILD.md`. Confirmed real by three independent reviews.
- **Request rebinding through hand-built `hx-vals`.** Two registry templates
  interpolate a value into hand-written JSON. Jinja escapes the quote, the
  browser decodes it inside the attribute, and duplicate JSON keys resolve
  last-wins — so a crafted slug can append a second `id` and rebind approval to
  a proposal other than the one previewed. `templates/triage.html` already uses
  the correct `| tojson` pattern; the registry templates did not follow it.
- **One malformed outbox record disables the entity.** `load_proposals` raises
  on the first bad file, so `approve`, `reject`, and the outbox screen all fail
  for every proposal, not just the bad one.
- **Ambiguous exception bases.** `CrossScopeError`, `ReviewedStateConflict`,
  `UnsafeDestinationPath`, and `InvalidSourceLeaf` each cover both an integrity
  finding and an ordinary condition, so a redirected path and a merely absent
  one are indistinguishable to any caller.
- **Exception narrowing hides outcomes.** Four service boundaries collapse a
  specific outcome into a generic wrapper. The worst reports a *committed*
  transaction as "rolled back, nothing changed, retry" — a Gate 2 break, since
  retrying produces a second commit for one reviewed action.
- **Registry readers are shape-fragile.** A registry that is valid YAML but
  wrongly shaped parses cleanly and then raises `AttributeError`/`TypeError` on
  access; `yaml.safe_load(...) or {}` guards only the empty case.
- **Unescaped reflection in registry delete.** Both branches of
  `registry_delete_execute` interpolate into an f-string `HTMLResponse`, and the
  success branch reflects the submitted `slug`, which is never compared against
  the proposal's own.
- **The Gate 1 stopwatch infers success from transport.** It counts
  `htmx:afterRequest` successes, so any refusal returned as 200 would be counted
  as a triaged item. S6 moves it to a success-only signal emitted after
  persistence.

The full S1-S5 failure record, fixes, threat boundaries, and workflow lessons
are in `SAFETY-FOUNDATION-S1-S4.md`, including its S5 addendum.

---

## Deferred and intentionally not built

- Frontend drag-drop upload to ingest follows completion of S6 and live
  gates. It must call the existing folder-ingest boundary, not create another
  write path.
- Email adapter credentials/configuration and scheduling remain deployment
  work. Cadence belongs to Hermes scheduling, not the request path.
- ADR-008 escapee lint (re-scan committed content for missed patterns).
- Full Command Center dashboard cards and workspace CRUD beyond current Phase
  1 screens.

---

## Next step

S6 is in design, not implementation. Its design has been through seven review
rounds and remains **Review Pending**; no application code has been written. The
implementation plan is superseded and will be rewritten only after the design
receives fresh whole-document approval, from a base rebased onto current
`origin/main`.

Do not start Phase 2, deploy, or add deferred UI while S6 or the live exit gates
remain open. S7 is proposed but must not begin before S6 merges.

---

## Phase 2 — not started

A finance-ingest pipeline is anticipated: deterministic statement/invoice
parsers produce tabular rows in `books.db` under `07-finance`, not Markdown
notes. Any export to an external accounting system or downstream finance
service remains gated through an outbox proposal and explicit approval.

Specific parsers and integration targets are instance data and remain in the
vault's `decisions.md`, not this repository.
