# OneOS — build status and findings

Living status for the code layer. Instance-specific decisions (bank/card
parsers, external-service integrations, entity names) live in the vault's
`decisions.md`, never here. This AGPL-3.0 repository is privately hosted and
intended for eventual public release; the vault is never published.

Build order and rules: see `../BUILD.md` and the spec at
`$ONEOS_VAULT/_system/docs/oneos-spec.md`.

Last reconciled: 2026-08-25, after Safety Foundation S7 final gates.

---

## Phase 1 triage

Original steps 1-10 and Safety Foundation S1-S7 are complete. The separately
sequenced inherited items 2–4 below remain required before live Phase 1 gate
trials. Phase 2 is not authorized.

| Safety step | State | Outcome |
|---|---|---|
| S1 — commit on ingest | **COMPLETE** | Adapter intake creates one redacted receipt-only `ingest:` commit; duplicate intake is a no-op; raw folder archives are contained outside the vault. |
| S2 — request-local scope | **COMPLETE** | Immutable manifest-backed entity scope owns every request and adapter operation; shared mailbox routing uses exactly one configured recipient owner. |
| S3 — server-owned destinations | **COMPLETE** | One canonical resolver validates module/sub/flags/lifecycle paths, derives block, and revalidates stored proposals before reads or writes. |
| S4 — proposal identity and freshness | **COMPLETE** | Collision-safe proposal IDs, exact-byte source SHA-256, no-follow snapshots, and visible stale/missing refusals are merged. |
| S5 — Git transaction and audit | **COMPLETE** | Classification approval and registry deletion use exact-path alternate-index transactions with ownership-aware rollback; Gate 3 validates action-specific messages, paths, and dirty-state fingerprints. |
| S6 — Console failures | **COMPLETE** | Every typed Command Center refusal reaches the operator as a specific, safe, actionable message; no route swallows a failure or returns a raw server fault. Public suite 603 → 832. All public and private gates pass, including the combined repo+vault history audit; Grey Matter fingerprints identical before and after. Per-task review record: `docs/superpowers/plans/2026-08-16-s6-sdd-ledger.md`. |
| S7 — bound review tokens | **COMPLETE** | Exact-byte review fingerprints bind approve, reject, and registry delete; quarantine-last and tracked HEAD receipts protect transactional actions from destructive rollback and repeated ids; reject safely quarantines its reviewed record; receipt-backed cards remain non-actionable. Final evidence: 1,470 public tests, all 48 mutation rows RED then GREEN, 37 private tests, `check_v2` 0/0, clean Gitleaks plus public current-tree/history and combined history audits, byte-identical Grey Matter HEAD/status/worktree/cached proof, and no open Critical or Important scoped-review finding. The macOS no-overwrite path was exercised; Linux `renameat2` is an accepted unexercised user/platform limitation. |

Merged S5 baseline: `0f71cd3`. S6 is complete. S7 began from the fresh
merged-S6 baseline `d7ad86b` with 926 public tests.
Final verification records 1,470 public tests in 141.98s (0:02:21), all 48
mutation rows RED then GREEN, 37 private tests, `check_v2` at 0 errors/0
warnings, clean Gitleaks, public current-tree/history, and combined repo+vault
history audits, and byte-identical Grey Matter HEAD/status/worktree/cached
before/after proof preserving pre-existing edits. The final scoped review found
no open Critical or Important findings. Supported writers cooperate through
OneOS interfaces and the shared action lock; deliberate post-final-check
ancestor relocation is outside that boundary.

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

### Found while designing S6 — historical defect record

These were discovered by review of existing code, not introduced by S6. S6
fixed its in-scope findings; S7 fixed the exact-byte approval boundary. The list
is retained as a historical record, not as a current defect queue.

- **Approval was not bound to reviewed content.** A proposal id named a mutable
  file. S7 fixed this for approve, reject, and registry delete; see `BUILD.md`.
- **A missing `modules:` key blanked every Console page.** `Vault._archetypes`
  raised a bare `ValueError` for a hand-edited `archetypes.yaml`, and
  `bundles()` is called unguarded from every page — so one malformed registry
  produced a 500 on the whole surface. Found and fixed in S6 Task 8.
- **A corrupt `books.db` reported "an unexpected error was not handled."**
  `sqlite3.connect` in read-only URI mode never validates the header, so a
  corrupt file only fails at the first query, escaping the delete-preview route
  untyped. Found and fixed in S6 Task 8.
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

S7 is complete at its gate-certified branch tip. Its merge/PR state is never
recorded here as a point-in-time fact; check it live with `git log
origin/main..<branch>` and the hosting UI. Before live Phase 1 gate trials,
sequence inherited items 2–4 below as their own reviewed tasks. Do not start
Phase 2, deploy, or add deferred UI while those items or the live exit gates
remain open.

---

## Phase 2 — not started

A finance-ingest pipeline is anticipated: deterministic statement/invoice
parsers produce tabular rows in `books.db` under `07-finance`, not Markdown
notes. Any export to an external accounting system or downstream finance
service remains gated through an outbox proposal and explicit approval.

Specific parsers and integration targets are instance data and remain in the
vault's `decisions.md`, not this repository.

## S7 inherits these from S6

S6 is complete and its branch is frozen at a gate-certified tip. Five items pass
to S7 explicitly, so none is rediscovered by accident. Each is stated as a rule
or a measured condition; no instance values appear here or anywhere in this
repository.

**1. Approval bound to reviewed bytes.** A proposal id names a *mutable file*,
not the bytes an operator reviewed. `approve`, `reject` and `execute_delete`
each take an id and re-read the record, comparing it only against another read
made in the same request, so between preview and approval another process may
rewrite a proposal while preserving its id and filename. Every existing check
validates the current record's internal consistency, never its correspondence to
what was reviewed. The fix — hash the validated snapshot, submit
`id + review_sha256`, compare before the first mutation, refuse visibly on
mismatch — **adds a refusal condition**, which is precisely why S6 could not
absorb it. The precedent is exact: S4 bound the source receipt's bytes and
refused stale approvals; this binds the proposal record's bytes one artifact
further out. S6 neither introduced nor widened the exposure.

**2. Prose-leakage enforcement.** This repository has structural invariants for
catch-alls, `hx-vals`, reader categories and route declarations — and **none for
its own documentation**. AGENTS.md's one rule is enforced solely by the combined
repo+vault audit, which needs the private vault and therefore cannot run in CI
or in a cloud task. A private value consequently survived fifty commits and
several careful readings of the exact line that carried it, and was caught only
at the final gate. A check over tracked documentation, seeded from the manifest
at gate time, would have caught it at the first commit.

**3. Declaration-completeness gaps.** The declaration-driven totality sweep
reads each endpoint's own declared catch tuple at test time, so decorator/body
**drift** is caught — widening a decorator alone reds it. It cannot catch
declaration **incompleteness**, because it injects only what a route already
declares. Every gap found in Tasks 13 and 14 was invisible to exactly that shape
and surfaced only by driving the real filesystem. The invariant that would close
this — per route, every type reachable from its own call graph is either
declared or deliberately routed to `E-UNKNOWN` — does not exist. Related: a
broad typed handler can silently *retire* a narrower guard's coverage without
changing behaviour, so whenever one is added above a layer declaring the same
family, check the lower declaration is still pinned.

**4. Remaining filesystem failure shapes.** Two realistic post-startup operator
actions still reach the global fallback as `E-UNKNOWN` on four routes: the
entity manifest with its permissions removed (`PermissionError`), and the vault
root renamed or unmounted (`RuntimeError` from the root resolver). Both raise
inside dependency resolution, so no route-level `except` can answer them; both
are the same design §5 instance — relying on the fallback is a failure, not a
silent default. The manifest reader is a declared `registry`-category structured
reader, so the permission escape is invariant-4 adjacent.

**5. Independent reviewer and mutation-tested verification — retained as
method, not as an option.** Across roughly twenty review rounds on S6, nearly
every finding was a defect in the *tests or the record* rather than in the
feature. Twelve false claims shipped in code comments and in this project's own
ledger; independent review caught all twelve, three of them inside corrections
of earlier ones. The countermeasure that worked is mutation, not inspection:
break the implementation, confirm the specific test fails, restore, confirm
byte-identity. A green suite is evidence only after something was broken and the
suite went red. Two corollaries earned the hard way — a count is meaningless
without the pytest selection that produced it, and a control that does not run
the code under test does not control it.
