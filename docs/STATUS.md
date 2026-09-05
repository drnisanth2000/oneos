# OneOS — build status and findings

Living status for the code layer. Instance-specific decisions (bank/card
parsers, external-service integrations, entity names) live in the vault's
`decisions.md`, never here. This AGPL-3.0 repository is privately hosted and
intended for eventual public release; the vault is never published.

Build order and rules: see `../BUILD.md` and the spec at
`$ONEOS_VAULT/_system/docs/oneos-spec.md`.

Last reconciled: 2026-08-30, after integration of the Gate 3 S7-record and
filesystem-evidence corrections.

---

## Phase 1 triage

### Readable classification reviews (2026-09-05)

Outbox classification cards lead with the document title, source folder,
destination folder and category. Empty categories read "No category"; proposal
IDs, full paths and the unchanged Git diff remain in collapsed technical
details. Errors and recovery controls stay visible. The triage preview explains
that nothing has moved and links to Outbox for separate approval.

The title comes from the same no-follow source observation as the diff, with a
filename fallback. It is display-only: proposal bytes, review fingerprints,
issuance IDs, stale-review handling and transaction behavior are unchanged.
The shared stylesheet is content-versioned to avoid stale styling after an
application update. The layout supports narrow screens and keyboard focus.
Regression coverage is in `tests/test_outbox_presentation.py`.

Destination editing remains deferred. In particular, an exact rejected record
created entirely after a Gate 3 snapshot cannot be proven sanctioned by the
existing receipt-free reject evidence. This presentation change neither
relaxes that rule nor introduces replacement proposals, new receipts or a new
snapshot format. It does not establish a live gate result or authorize a live
app update.

### Manual destination selection

The triage screen offers a folder and optional category selector when a rule
cannot provide a usable suggestion, and a correction control beside usable
suggestions. Choices come from the selected entity's active registry entries;
the source inbox and modules without an active lifecycle are not destinations.
Missing configured modules remain visible rather than silently disappearing.

Preview submits to the existing classification-proposal endpoint. That endpoint
revalidates scope, flags and paths, writes only a proposal, and renders its diff.
Nothing moves until separate Outbox review and approval. Manual selection does
not create classifier rules, call a model, or enable Hermes or messaging.
Keyboard shortcuts ignore form controls; the stopwatch retains its existing
persisted-proposal signal. An open correction form owns the accept shortcut;
an incomplete correction never falls back to the original recommendation.
Integrity-error rows expose no manual action or source filename.
Regression coverage is in
`tests/test_triage_manual.py`.

This usability change does not establish a live Gate 1 result or change the
historical gate records below. Model-provider and Hermes integration remain
separate work under the existing asynchronous, propose-only boundary.

### Historical foundation and gate record

Original steps 1-10, Safety Foundation S1-S7, and the separately sequenced
inherited items 2, 4, and 3 below are complete. The first live Phase 1 exit
trial exposed a Gate 3 audit defect, and independent correction review exposed
a second discovery gap. Both corrections are now integrated and synthetically
verified. A fresh trusted-local Gate 3 rerun still requires separate owner
authorization. Phase 2 is not authorized.

| Safety step | State | Outcome |
|---|---|---|
| S1 — commit on ingest | **COMPLETE** | Adapter intake creates one redacted receipt-only `ingest:` commit; duplicate intake is a no-op; raw folder archives are contained outside the vault. |
| S2 — request-local scope | **COMPLETE** | Immutable manifest-backed entity scope owns every request and adapter operation; shared mailbox routing uses exactly one configured recipient owner. |
| S3 — server-owned destinations | **COMPLETE** | One canonical resolver validates module/sub/flags/lifecycle paths, derives block, and revalidates stored proposals before reads or writes. |
| S4 — proposal identity and freshness | **COMPLETE** | Collision-safe proposal IDs, exact-byte source SHA-256, no-follow snapshots, and visible stale/missing refusals are merged. |
| S5 — Git transaction and audit | **COMPLETE** | Classification approval and registry deletion use exact-path alternate-index transactions with ownership-aware rollback; Gate 3 validates action-specific messages, paths, and dirty-state fingerprints. |
| S6 — Console failures | **COMPLETE** | Every typed Command Center refusal reaches the operator as a specific, safe, actionable message; no route swallows a failure or returns a raw server fault. Public suite 603 → 832. All public and private gates pass, including the combined repo+vault history audit; Grey Matter fingerprints identical before and after. Per-task review record: `docs/superpowers/plans/2026-08-16-s6-sdd-ledger.md`. |
| S7 — bound review tokens | **COMPLETE** | Exact-byte review fingerprints bind approve, reject, and registry delete; quarantine-last and tracked HEAD receipts protect transactional actions from destructive rollback and repeated ids; reject safely quarantines its reviewed record; receipt-backed cards remain non-actionable. Published S7 evidence: 1,476 public tests, all 48 campaign rows RED then GREEN, and a 1,476-pass restored closing suite in 107.32s; Gitleaks found no leaks; and public current-tree/history audits were clean. Focused rename proofs refused distinct same-HEAD vaults before lock and kept execution on the reviewed canonical vault if a caller alias was retargeted. Final private gates recorded 37 tests, `check_v2` 0/0, a clean combined history audit, and byte-identical Grey Matter state. Independent scoped review PASS found no findings. The macOS no-overwrite path was exercised; Linux `renameat2` is an accepted unexercised user/platform limitation. |

Merged S5 baseline: `0f71cd3`. S6 is complete. S7 began from the fresh
merged-S6 baseline `d7ad86b` with 926 public tests.
The published S7 verification recorded 1,476 public tests, all 48 campaign rows
RED then GREEN, and a 1,476-pass restored closing suite in 107.32s. Gitleaks
found no leaks and public current-tree/history audits were clean. A separate
focused cross-vault rename-plan mutation went RED then GREEN: two distinct
repositories at the same HEAD refuse before lock, Git, or mutation, while
same-root relative/absolute aliases remain valid. A caller-alias retarget
regression also went RED then GREEN and keeps execution on the canonical vault.
The final private-gate record is 37 tests in 0.174s, `check_v2` at 0 errors/0
warnings, a clean combined repo+vault history audit, and byte-identical Grey
Matter HEAD/status/worktree/cached proof preserving pre-existing edits.
Independent scoped review PASS found no findings. Supported
writers cooperate through OneOS interfaces and the shared action lock;
deliberate post-final-check ancestor relocation is outside that boundary.

### Inherited follow-up completion evidence

These are published completion records, not results from this reconciliation:

| Item | Public implementation | Trusted-local completion evidence |
|---|---|---|
| 2 — prose-leakage enforcement | **COMPLETE** | 39 tests; `check_v2` 0 errors/0 warnings; combined audit CLEAN; byte-identical vault preservation. |
| 4 — remaining filesystem failure shapes | **COMPLETE** | 39 tests; `check_v2` 0 errors/0 warnings; combined audit CLEAN; byte-identical vault preservation. |
| 3 — declaration completeness | **COMPLETE** — final public suite recorded 1,826 passing tests. | 39 tests; `check_v2` 0 errors/0 warnings; Gitleaks clean; combined audit CLEAN; byte-identical vault preservation. CI and CodeRabbit passed. |

### Gate 3 correction integration evidence

The first live Gate 3 session correctly sanctioned both action commits and
reported zero violating commits, but it misclassified the action's exact S7
quarantine record as a direct write. The focused S7 correction now sanctions
only the canonical entity-local record when its proposal identity and bytes,
regular no-follow state, and any receipt/digest evidence supplied by the
transaction contract agree; wrong-location, malformed, mismatched,
non-regular, and unrelated writes remain violations.

Independent review of that correction exposed a separate standing discovery
gap: Git status cannot enumerate empty directories and some non-regular
entries. The integrated filesystem-evidence correction adds a closed snapshot
schema, deterministic no-follow traversal, coherent Git/filesystem endpoint
collection, and fail-closed disposition of directory and special-entry
changes. Filesystem changes inherit sanction only from an independently
verified rename with exact topology, kind, mode, identity, and symlink-target
evidence; the exact canonical S7 quarantine-directory addition remains the
sole standalone directory exception. Endpoint identity is necessary rather
than sufficient: portable metadata cannot distinguish a genuine move from a
delete-and-create that reuses an inode, so every other rename condition must
also agree.

The completed correction was integrated by PR #28 at merge
`cbc15971fecd206bef782b1042fd2ddebe21db3c`. Its reviewed head recorded 306
Gate 3 tests passing with one platform skip and 2,028 public tests passing with
one platform skip. The trusted-local gate recorded 39 private tests,
`check_v2` 0 errors/0 warnings, policy PASS, clean public and combined history
audits, and byte-identical protected HEAD, status, worktree-diff, and
cached-diff evidence preserving approved pre-existing edits. CI, CodeRabbit,
Gitleaks, and independent scoped review passed. Linux CI also exposed tests
that assumed inode numbers could not be immediately reused; the fixtures were
made portable without weakening the product's fail-closed identity checks.
The live Gate 3 rerun was deliberately not performed during correction and is
still required before Gate 3 can be declared passed.

### Exit gates (spec §11)

| Gate | State |
|---|---|
| 2 — one commit per approval, `git revert`-clean | **AUTOMATED PASS; LIVE TRIAL PENDING** — adapter intake is committed before approval; classification and registry-delete approvals commit exactly reviewed paths; one revert restores the committed action. |
| 4 — front-matter agreement with `policy_enforcer`, 100 files | **PASS** (100/100) |
| 5 — cold start to usable screen < 2s | **PASS** (~0.35s) |
| 3 — zero unsanctioned direct vault writes over a session | **AUTOMATED PASS; LIVE SESSION RERUN REQUIRED** — Gate 3 validates action-specific message/path pairs, initial dirty evidence, exact S7 quarantine records, receipt-only `ingest:` commits, and supplemental no-follow filesystem evidence for Git-invisible directories and non-regular entries. The first live run exposed the corrected S7-record defect; later review exposed the filesystem-discovery gap. No live pass has been recorded. |
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
  staged, unstaged, or untracked state captured at session start. Exact S7
  quarantine records require proposal/receipt correlation; deterministic
  no-follow filesystem evidence covers Git-invisible directories and special
  entries. Rename inheritance requires independently sanctioned commit-relative
  topology plus matching endpoint evidence and fails closed on ambiguity.
- **Gate 3 historical-replay correction** — planned-head live lookup initially
  broke historical-tree audit replay. The audit now uses an explicit immutable
  parent OID builder; the correction was developed test-first and independently
  reviewed.
- **Cross-vault rename-plan correction** — two distinct repositories at the
  same HEAD now refuse before lock, Git, or mutation. Same-root relative and
  absolute aliases remain valid. Its focused mutation went RED then GREEN and
  was independently reviewed; it is separate from the 48-row campaign.
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

After separate owner authorization, start a new trusted-local task from a fresh
worktree at the current fetched `origin/main` and rerun the Gate 3 full-session
audit while preserving the existing live evidence. Then complete any
still-pending Gate 2 approval/revert proof and the Gate 1 timed triage of about
20 real inbox items. Gates 4 and 5 already pass. Phase 2 remains unauthorized.
Deployment remains blocked until all Phase 1 gates pass and the owner
separately approves deployment; deferred UI remains out of scope.

---

## Phase 2 — not started

A finance-ingest pipeline is anticipated: deterministic statement/invoice
parsers produce tabular rows in `books.db` under `07-finance`, not Markdown
notes. Any export to an external accounting system or downstream finance
service remains gated through an outbox proposal and explicit approval.

Specific parsers and integration targets are instance data and remain in the
vault's `decisions.md`, not this repository.

## Inherited follow-ups from S6 — completion record

S6 passed five items forward explicitly so none would be rediscovered by
accident. Item 1 completed in S7; items 2, 4, and 3 completed later as separately
sequenced work; item 5 remains the required verification method. The original
problem descriptions are retained below as historical context, followed by the
published completion evidence. No instance values appear here or anywhere in
this repository.

**1. Approval bound to reviewed bytes — COMPLETED IN S7.** A proposal id named a
*mutable file*, not the bytes an operator reviewed. `approve`, `reject` and
`execute_delete` each took an id and re-read the record, comparing it only
against another read made in the same request, so between preview and approval
another process could rewrite a proposal while preserving its id and filename.
Every existing check validated the current record's internal consistency, never
its correspondence to what was reviewed. The fix — hash the validated snapshot,
submit
`id + review_sha256`, compare before the first mutation, refuse visibly on
mismatch — **adds a refusal condition**, which is precisely why S6 could not
absorb it. The precedent is exact: S4 bound the source receipt's bytes and
refused stale approvals; this binds the proposal record's bytes one artifact
further out. S6 neither introduced nor widened the exposure.

**2. Prose-leakage enforcement — COMPLETE.** At inheritance, this repository had
structural invariants for catch-alls, `hx-vals`, reader categories and route
declarations — and **none for its own documentation**. AGENTS.md's one rule was
enforced solely by the combined repo+vault audit, which needs the private vault
and therefore cannot run in CI or in a cloud task. A private value consequently
survived fifty commits and several careful readings of the exact line that
carried it, and was caught only at the final gate. A check over tracked
documentation, seeded from the manifest at gate time, would have caught it at
the first commit.
Exact short registry-derived tokens in tracked Markdown now fail both
current-tree and history audit modes. The published trusted-local completion
record is 39 tests, `check_v2` 0 errors/0 warnings, combined audit CLEAN, and
byte-identical vault preservation.

**3. Declaration completeness — COMPLETE.** Immutable
failure metadata now closes the reviewed inventory of 20 route-facing service
and dependency boundaries. Thirteen registered Console routes declare 35
executable body-service edges; structural traversal proves every known domain
failure exported through those contracts has a route-owned catch, typed
dependency handler, or exact deliberate-`E-UNKNOWN` disposition. Executable
call-edge, FastAPI dependency, lower-ownership, and route/body binding checks
fail independently under seven semantic mutations and every one of the 35
route/service removals, with byte-identical restoration before each GREEN run.
Representative real-filesystem root-loss, manifest-permission, whole-system
redirect, missing-manifest, and leaf-redirect vehicles remain part of the
proof. This is a closed known-domain inventory, not a claim to enumerate every
possible Python exception; unforeseen programmer defects still belong to the
global fallback.

A concurrent entity-registry cutover remains narrow but real: scope binding can
succeed before a service reload observes that the entity disappeared. Triage,
proposal, and outbox contracts now keep that outcome visible as `E-ENTITY`.
`Vault._entity_flags` instead converts the same root cause to `E-CONFIG`; that
pre-existing taxonomy inconsistency is recorded for later work and is not
changed here. The final public suite recorded 1,826 passing tests. The published
trusted-local completion record is 39 tests, `check_v2` 0 errors/0 warnings,
Gitleaks clean, combined audit CLEAN, and byte-identical vault preservation; CI
and CodeRabbit passed.

**4. Remaining filesystem failure shapes — COMPLETE.**
A configured vault root that becomes unavailable now renders `E-TAMPER`, while
an unreadable entity manifest renders `E-CONFIG`, across every entity-scoped
Console endpoint derived from FastAPI's dependency metadata. Neither condition
reaches the global fallback, reflects raw exception or submitted data, exposes
an action control, or mutates the synthetic vault; lower route catches remain
independently pinned. The published trusted-local completion record is 39 tests,
`check_v2` 0 errors/0 warnings, combined audit CLEAN, and byte-identical vault
preservation.

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
