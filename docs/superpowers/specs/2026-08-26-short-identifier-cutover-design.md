# Short-Identifier Cutover

**Status:** DESIGN — public design task only. No implementation plan exists
yet, no application code or test has been modified, and no migration has been
executed.

**Base:** freshly fetched merged `origin/main` at
`e4478fc1beef985fecc16e485b0974568b4fc004`. Fresh public baseline:
`uv run python -m pytest -q` → 1,476 passed.

**Authority:** `AGENTS.md`, `BUILD.md` Safety Foundation, `docs/STATUS.md`
"S7 inherits these from S6", and
`docs/superpowers/specs/2026-08-26-inherited-safety-items-2-4-design.md`.

**Prerequisite state:** inherited Item 2 is implemented and parked, unpushed,
on branch `codex/inherited-item-2-prose-leakage` at
`cfed17fffd9f00e0036e7877ab6aa8b5342f93bc`. Its public gates passed. Its
trusted-local run reported current-tree and historical findings caused by
private registry identifiers shorter than the audit's long-term threshold.
This cutover exists to remove that class of identifier at the source rather
than to weaken the audit that found it.

## Objective

Raise every Grey Matter registry identifier to a minimum length so that no
registry-derived term can ever again be short enough to escape whole-text
matching, and do it as one reviewable, reversible private commit.

This is a data and convention migration plus the public code that enforces the
new rule. It adds no product surface, no dependency, no schema, and no second
scanner.

## Approved product decisions

These were decided by the product owner and are inputs, not open questions:

1. Registry identifiers must contain at least five characters.
2. Clean cutover: no aliases and no compatibility fallback.
3. Existing short prefixes are preserved by appending a type suffix:
   `<old>-entity`, `<old>-product`, `<old>-member`, `<old>-workspace`.
4. Only the current Grey Matter tree is migrated.
5. Grey Matter Git history is not rewritten.
6. The private cutover must become one controlled, reversible Git commit.
7. Existing identifiers must never be reused for different objects.
8. The final mapping must be proposed to the product owner and explicitly
   approved before it is applied.
9. The parked Item 2 branch resumes only after this cutover is complete.
10. Independent review and mutation-tested verification remain mandatory.

## Identifier format and the five-character minimum

The grammar is unchanged: lowercase ASCII letters and digits in hyphen-joined
tokens, matching the existing expression `^[a-z0-9]+(?:-[a-z0-9]+)*$`. The
cutover adds one rule on top of it: the whole identifier must be at least five
characters long, counting hyphens.

Five is deliberately one character above the publication audit's long-term
threshold of four. The audit classifies a term of four or more characters as a
long term and matches it in every tracked text file; a term of three or fewer
is a short term. A four-character floor would sit exactly on that boundary and
would leave no margin if the audit's own classification is ever retuned. Five
guarantees every registry identifier is matched everywhere by the strongest
rule the audit has, with one character to spare.

The floor applies to all four registry axes this cutover governs: entity,
product, member, and workspace. The `project` axis that `app/rename.py` also
knows about is a directory name inside a bundle pipeline, not a registry
identifier, and is out of scope.

Reserved names remain reserved regardless of length.

## Deterministic type-suffix mapping

### Which identifiers are rewritten

**Only identifiers shorter than five characters are rewritten.** An identifier
that already satisfies the floor keeps its current value.

The alternative — suffixing every identifier uniformly so that shape always
reveals type — was considered and rejected. It would rewrite every identifier
in the vault instead of a small minority, which enlarges a single reversible
commit into a whole-vault rewrite, multiplies the chance of a missed reference,
and makes owner review of the mapping far harder. The suffix exists to add
length, not to encode type, so there is no correctness reason to apply it where
length is already sufficient. If the owner prefers uniform suffixing, that is a
product decision that changes this design and must be settled before any
implementation plan is written.

### The mapping function

For an identifier `old` on axis `axis`, the new value is `f"{old}-{axis}"`,
where `axis` is exactly one of `entity`, `product`, `member`, `workspace`.

The function is total, deterministic, and depends only on the pair
`(axis, old)`. It performs no lookup, consults no counter, and has no
tie-breaking branch. The same inventory always produces the same mapping, which
is what makes the dry-run output a trustworthy preview of the apply.

The suffixes are seven to ten characters long, so the shortest possible output
is a one-character identifier plus the seven-character `-entity` or `-member`
suffix — eight characters, comfortably above the floor. The mapping therefore
never needs a second pass and never produces a value that still violates the
rule.

### Already-suffixed values

No in-scope identifier can already carry an axis suffix, and the arithmetic is
what guarantees it: every suffix is at least seven characters, so any
identifier ending in one is at least eight characters, which is above the floor
and therefore never in scope. An identifier such as `ab-entity` is simply left
alone.

The implementation must still assert this rather than rely on the arithmetic
holding after some future edit. If an in-scope identifier ends in `-entity`,
`-product`, `-member`, or `-workspace`, the tool refuses and escalates instead
of appending a second suffix.

Double-suffixing is never performed. There is no `-entity-entity` outcome in
this design.

## Collisions

Three distinct collision classes exist, and each is a hard refusal rather than
a resolution. The tool never invents a disambiguating suffix, counter, or
alternate spelling: silently choosing a different identifier than the one the
owner approved is exactly the failure this design must not have.

**1. New-value collides with an existing identifier on the same axis.** The
mapping is injective on distinct inputs, so this can only happen when the new
value equals another object's *current* identifier. Example: on the entity
axis, `ab` maps to `ab-entity`, and an entity literally named `ab-entity`
already exists. Applying this would reuse an existing identifier for a
different object, violating approved decision 7. Refuse.

**2. New-value collides with another new value.** Impossible for distinct
inputs on one axis, because appending a constant suffix preserves distinctness.
It is checked anyway, because a mapping table assembled from a faulty inventory
could contain duplicate inputs, and a silent duplicate is worse than a noisy
refusal.

**3. The same literal identifier exists on more than one axis.** This is the
dangerous one, and it is a property of the *source* data rather than of the
mapping. The entity planner in `app/rename.py` performs a boundaried
whole-vault token sweep, because entity slugs are path components and must be
rewritten inside hardcoded script constants and inside the `paths:`/`except:`
pair of a policy rule. The product and member planners are deliberately scoped
to a front-matter field and one registry, precisely because those values can be
short. If one literal is both an entity and a product, the entity sweep will
rewrite the product's occurrences too, and the product will silently acquire the
entity's suffix. Refuse before planning, and escalate to the owner.

All three checks run during inventory, before any mapping is shown to the owner
and long before any write.

## Affected interfaces

The inventory below is the result of reading the public source at the recorded
base. It is what the implementation plan must cover.

### Identifier validation — five copies that must agree

The same grammar is currently restated in five places, none of which enforces
any length:

| Site | Symbol |
|---|---|
| [app/entities.py:12](app/entities.py:12) | `_ENTITY_SLUG` |
| [app/vault.py:31](app/vault.py:31) | `_REGISTRY_ID` |
| [app/destinations.py:57](app/destinations.py:57) | `_REGISTRY_ID` |
| [app/rename.py:46](app/rename.py:46) | `SLUG_RE` |
| [app/action_receipts.py:32](app/action_receipts.py:32) | `_ENTITY` |

A sixth restatement lives in the vault's own wizard, which `app/rename.py`
documents as a mirror it cannot import. That copy is private and is the trusted
local agent's responsibility; this repository must not read it.

AGENTS.md records the exact failure mode that duplicated rules produce: when
the sidebar and the validator disagree about what exists, the disagreement is
invisible until something breaks. Five independent length checks would
reproduce it. The floor must therefore be expressed once and consumed by every
site, and a public structural test must assert that no module defines its own
registry-identifier length rule.

Whether the five grammar copies are also collapsed into one shared definition
is a broader refactor than this cutover needs. The design requires only that
the *length* rule is single-sourced; the grammar copies may stay where they
are, provided the shared validator is what every site calls.

### Values, paths, and records

- **Registries.** `_system/entities.yaml` keys, `_system/products.yaml` keys
  nested per entity, `_system/members.yaml` entry `id:` values,
  `_system/workspaces.yaml` entry `id:` values and their `entity:` /
  `primary_entity:` / `product:` / `member:` references, and
  `_system/scripts/action-policy.yaml` rule paths.
- **Directory names.** An entity slug is a top-level bundle directory. An
  entity rename is a directory move, and every module, `outbox/`, `staging/`,
  `.receipts/`, and `books.db` beneath it moves with it.
- **Front matter.** `entity:`, `product:`, and `member:` field values across
  every tracked Markdown file. `schema.py` lists `entity` and `product` among
  its required fields.
- **Proposals.** Outbox records carry `entity`, and `src`/`dst` stored paths
  whose first component is the entity slug. `Scope.resolve_stored` enforces
  that first component, so a stale prefix becomes a cross-scope refusal rather
  than a silent mis-resolution.
- **Review tokens.** S7 binds an approval to the exact proposal bytes. The
  cutover rewrites pending proposal records, so every review token issued
  before the cutover is invalidated by it. This is correct fail-closed
  behaviour and must be stated to the operator, not engineered around: an
  operator holding a pre-cutover token must review again.
- **Receipts.** `receipt_relative_path` is
  `<entity>/outbox/.receipts/<proposal_id>.yaml`. Receipt *content* carries no
  entity value, so an entity rename moves receipts without rewriting them, and
  spent-id facts survive the cutover because the move and the commit are the
  same commit. A public test must prove a spent id is still refused after the
  cutover.
- **Saved workspaces.** Saved scopes reference entity, product, and member
  values and are rewritten on all three axes.
- **`books.db`.** See the open decision below.

### `books.db` — an unresolved decision, not a design gap

`app/rename.py` deliberately does not modify `books.db`; it counts and reports
matching rows and defers the column update. That deferral is safe for a
one-off rename, where the old value merely becomes historical. It is **not**
safe for a clean cutover with no compatibility fallback: rows still carrying a
retired product or member value would reference a registry value that no longer
exists, and no alias resolves them.

Three options exist — migrate the columns inside the same commit; refuse the
cutover while any row references an in-scope value; or accept the orphans. The
first changes stored data beyond the approved decision list, the second may be
impossible to satisfy without a separate data task, and the third contradicts
approved decision 2.

**This is an unresolved product decision and a hard stop.** The inventory must
report the affected row counts per axis and value, the owner must choose before
approving the mapping, and the tool must refuse to apply until the choice is
recorded. No implementation plan may be written that silently picks one.

## Migration scope: current tree only

Only the working tree at the cutover commit's parent is migrated. Grey Matter
history is not rewritten, so historical commits retain their original
identifiers permanently and by design.

The consequence must be stated plainly because it bounds what this cutover can
claim: a history-mode audit of Grey Matter will still find the old short
identifiers in historical commits. The cutover fixes the current tree and every
future commit. It does not and cannot retroactively clean history without the
rewrite that approved decision 5 forbids.

This is the correct trade. Rewriting the system-of-record's history to satisfy
a scanner would destroy the audit trail that invariant 2 exists to protect.

## Dry-run and explicit apply

The existing rename tool's separation is the model and is kept: dry-run is the
default and `--apply` is explicit.

The cutover adds a stage before both, because a mapping must be approved before
it can be previewed as a diff:

1. **Inventory** — read-only. Enumerate in-scope identifiers per axis, run all
   three collision checks, count `books.db` references, and emit the proposed
   mapping table. Writes nothing, commits nothing, and never requires the
   action lock.
2. **Owner approval** — the owner reviews the mapping table and records an
   explicit approval. The approved mapping becomes a fixed input; the tool does
   not recompute it later from a possibly-changed vault.
3. **Dry run** — default. Plan every mapping against the current tree and
   render the full combined diff and move list. Writes nothing.
4. **Apply** — explicit `--apply`. Requires the approved mapping and a vault
   whose HEAD matches the one the dry run was planned against.

Stages 3 and 4 must produce identical plans for an unchanged tree. Because the
mapping function is deterministic and the mappings are applied in a fixed
order — entity, then product, then member, then workspace, and within each axis
sorted by old identifier — the dry-run output is a faithful preview.

## One reversible commit

### Why the existing tool cannot simply be looped

`apply_rename` commits once per rename. Running it N times produces N commits,
which violates approved decision 6. Squashing afterwards is worse: the
intermediate states are not required to be valid, so a mid-sequence validator
run could fail on a tree that was only ever meant to be transient.

### The architecture

One combined operation, inside one acquisition of the shared action lock:

1. Acquire the shared action lock, so no approval, deletion, or rename can
   interleave.
2. Refuse unless the tree is clean, exactly as `apply_rename` does. A clean
   tree is what makes `git reset --hard` a complete undo.
3. Pin HEAD and refuse if it differs from the HEAD the plan was built against.
4. For each mapping in the fixed order, plan it against the **current tree
   state** and apply its edits and moves immediately, before planning the next.

   Sequential application is what makes plan composition correct. The existing
   planners read from disk; if all plans were built up-front against the
   original tree and merged, two mappings touching the same file would produce
   two different full-file texts and the second would silently discard the
   first. Applying each mapping before planning the next means every planner
   observes its true input.
5. After all mappings are applied, run the residual gate once over the union of
   every old identifier.
6. Run the validators once, on the fully migrated tree.
7. `git add -A` and create exactly one commit.

Failure at any point rolls the tree back with `git reset --hard HEAD` and
`git clean -fd`, the same rollback the rename tool already uses.

The result is one commit that a single `git revert` undoes.

### Exact detection of unresolved old identifiers

The residual gate is the cutover's fail-closed guarantee and runs before the
commit, never after.

It searches every non-binary tracked file outside the skip set for any old
identifier in the mapping, matched as a whole token using the existing
boundaried pattern `(?<![\w-])<old>(?![\w-])`. A non-empty result aborts the
cutover and rolls back.

The boundary rule is what makes the gate usable, and the reason is worth
stating because a naive implementation would break it. A bare escaped search
for `ab` would match inside the tool's own output `ab-entity` and report every
successful rewrite as a residual. The lookahead `(?![\w-])` fails on the hyphen
that begins the suffix, so a correctly migrated occurrence never trips the
gate, while a genuinely missed bare `ab` still does. A public test must pin this
distinction directly, and a mutation that drops the boundary must turn it red.

Binary files are excluded from the gate by construction. `books.db` is
therefore invisible to it, which is a second reason the `books.db` decision
above must be settled before apply rather than after.

`former_slugs:` lines are exempt, for the reason in the next section.

### `former_slugs` is provenance, not an alias

The rename tool records `former_slugs: [old]` on the renamed registry key and
exempts that line from its residual gate. The cutover keeps this.

This is not a compatibility fallback and must never become one. No code path
resolves `former_slugs` today — it is written and gate-exempted, and never
read. It is inert provenance that makes the mapping legible in the vault and
supports manual reconciliation after a revert.

Two constraints follow, and both are testable:

- No reader may ever resolve an identifier through `former_slugs`. A public
  test must assert that no lookup path consults it, so decision 2 cannot be
  eroded later by a well-meaning fallback.
- Retained old values must not re-enter the publication audit's term set. The
  audit seeds terms from entity keys, product keys, member ids, and workspace
  ids. `former_slugs` values are none of those, so they are not seeded — which
  is precisely why Item 2's findings clear once the registry keys themselves
  are long. The implementation must not change how terms are collected.

## Rollback and interruption

**Before the commit.** Any failure — a planner error, a collision discovered
late, a residual, a validator failure, an interrupted process — leaves the tree
dirty and uncommitted. Recovery is `git reset --hard HEAD` followed by
`git clean -fd`. The tool performs this itself on every handled failure. An
operator recovering from a killed process performs it manually, and the
requirement that the tree be clean before starting is what guarantees this
restores the exact pre-cutover state.

**After the commit.** `git revert` of the single cutover commit restores every
identifier. Because history is not rewritten and the commit is one atomic unit,
the revert is complete and needs no manual cleanup — the revert test that
`AGENTS.md` names as one of the two tests that matter more than coverage.

**The window between commit and confirmation.** The rename tool already models
this: if the commit succeeded but reading back the resulting commit id or
releasing the lock failed, that is a distinct outcome from a failed cutover and
must not be retried. The cutover reuses that distinction. Retrying a cutover
that already committed would attempt to rewrite identifiers that no longer
exist and would fail the inventory's own checks, but the operator must be told
"committed, do not retry" rather than left to infer it.

**Interaction with in-flight work.** The action lock and the clean-tree
requirement together mean a cutover cannot interleave with an approval. A
pending proposal is rewritten by the cutover, which invalidates any review
token already issued for it. That is fail-closed and correct.

## Public synthetic tests

All tests use synthetic vaults built in temporary directories, following the
existing rename tests. No test may read a real vault, a real registry, or a
real identifier.

Required coverage:

- **Mapping.** Determinism for a fixed inventory; the suffix per axis;
  identifiers at or above the floor are untouched; every output satisfies the
  floor.
- **Collisions.** Each of the three classes refuses, with its own diagnostic,
  before any write.
- **Already-suffixed.** An in-scope identifier ending in any axis suffix
  refuses rather than double-suffixing.
- **Floor enforcement.** Every validation site rejects a sub-floor identifier,
  and a structural test asserts the length rule is single-sourced.
- **One commit.** A multi-mapping cutover on a synthetic vault produces exactly
  one new commit, and `git revert` of it restores the tree.
- **Residual gate.** A deliberately missed occurrence aborts and rolls back; a
  correctly migrated occurrence does not trip the gate; a bare old token inside
  a longer token is not a residual.
- **Rollback.** An injected failure after partial application leaves the tree
  byte-identical to HEAD.
- **Ordering.** Dry-run and apply produce identical plans for an unchanged
  tree.
- **Receipts.** A spent proposal id is still refused after an entity cutover.
- **Proposals.** Stored `src`/`dst` prefixes are rewritten, and a pre-cutover
  review token is refused afterwards.
- **`former_slugs`.** No resolver consults it, and retained values do not enter
  the audit's term set.
- **Fail-open guard.** A policy rule's `paths:` and its `except:` for
  `.sensitive/` are rewritten in the same pass, and a `.sensitive/` read is
  still denied after the cutover — the rename test that `AGENTS.md` requires.

Mutation evidence is mandatory and must include at least: removing the length
floor from the shared validator; dropping the boundary from the residual gate;
removing one collision check; and replacing sequential application with
up-front plan composition so that two mappings touching one file lose the first
rewrite. Each names the exact test that must go red, and each target file is
restored byte-for-byte before the suite is re-run green.

## Public release sequencing

The public change cannot ship as one release, and the reason is easy to miss.

If read-time floor enforcement and the cutover tool ship together, the tool
cannot read the vault it is meant to migrate: the pre-cutover vault contains
sub-floor identifiers, and `EntityCatalog.load` would reject the manifest
before the tool could inventory it. The app would refuse to start against the
very vault awaiting migration.

Two stages are therefore required:

- **Stage A** — the inventory, mapping, dry-run, apply, and residual machinery,
  plus its synthetic tests. No read-time floor. The tool must be able to read a
  pre-cutover vault.
- **Stage B** — floor enforcement at every validation site, plus the public
  synthetic fixtures that currently use sub-floor slugs.

The private cutover runs between them. Stage B merges only after the cutover
commit exists.

Stage B has bounded, mechanical public churn: the dominant synthetic entity
slug in the existing suite is four characters and appears in the low hundreds
of occurrences, and a small number of other fixture slugs are also sub-floor.
This churn is expected and is not evidence of a problem. It must be a
fixture-only change: if enforcing the floor requires altering application logic
beyond the validation sites, stop and re-open the design.

## Trusted-local sequence

The public agent's role ends at a reviewed public branch. Everything touching
Grey Matter is the trusted local agent's, and no part of it may be delegated to
a cloud task.

1. **Inventory (private, read-only).** Run the Stage A tool against the live
   vault. Produce the mapping table and the `books.db` reference counts.
   Nothing is written.
2. **Owner approval.** Present the mapping and the `books.db` decision. The
   owner approves explicitly. An unapproved or partially approved mapping is
   not executable.
3. **Pre-cutover proof.** Capture opaque `git status --porcelain=v2
   --untracked-files=all`, worktree and cached binary diffs, outside both
   repositories, per `BUILD.md`.
4. **Dry run.** Review the full combined diff.
5. **Apply.** One commit. Record its id.
6. **Private gates.** The vault's own suite, `check_v2` at 0 errors and 0
   warnings, and the combined repo+vault audit in both current-tree and history
   modes. The current-tree audit must now be clean of the short-identifier
   findings; the history audit will still report historical occurrences, and
   that expected residue must be recorded rather than treated as a failure or
   suppressed.
7. **Preservation comparison.** Compare the opaque snapshots. A clean vault
   stays clean apart from the single cutover commit; a vault with approved
   pre-existing edits retains exactly those edits.
8. **Independent review.** A reviewer independently re-derives the mapping from
   the inventory, re-runs the public suite and the mutation campaign, and
   checks every factual claim.

## Sequencing with the inherited items

1. This cutover completes: Stage A merged, private cutover committed and
   verified, Stage B merged.
2. **Item 2 resumes.** The parked branch is rebased onto the resulting
   `origin/main` and its trusted-local audit is re-run. The current-tree
   findings should now be absent. Item 2 merges only on that evidence.
3. **Item 4** — dependency-time filesystem outcomes.
4. **Item 3** — declaration completeness.

The 2 → 4 → 3 order is unchanged from the inherited design. This cutover is
inserted before Item 2's merge, not in place of any item.

Item 2's branch must not be modified while this cutover is in progress.

## Stop conditions

Work halts and returns to the product owner on any of these:

- **The `books.db` decision is unresolved.** No plan, no apply.
- Any collision of the three classes above.
- An in-scope identifier that already carries an axis suffix.
- A request to add an alias, a fallback resolver, a `former_slugs` lookup, or
  any dual-read compatibility path.
- A request to weaken the publication audit, add an exemption or allowlist
  entry, or lower the five-character floor.
- A request to rewrite Grey Matter history.
- Any need to migrate anything beyond the current tree.
- Discovery that enforcing the floor requires application logic changes beyond
  the validation sites.
- Any dependency, schema, convention, or security-boundary change.
- Any destructive action beyond the single reversible commit, or any
  deployment.
- Any need for private material inside a public task, or any instruction to
  place an instance-specific value in this repository.
- A vault that is not clean at apply time, or a HEAD that moved between plan
  and apply.

## Explicitly out of scope

- Rewriting Grey Matter history.
- The `project` axis, module numbers, block values, and `sub:` values.
- Any change to S7 review tokens, receipts, quarantine, or the managed-
  directory boundary.
- Changes to Items 2, 3, or 4 beyond resuming them in order.
- New dependencies, schemas, registry values, conventions, or product surfaces.
- Collapsing the five grammar copies into one shared expression, beyond
  single-sourcing the length rule.

## Completion

The cutover is complete when Stage A is merged, the private cutover exists as
one reverted-testable commit with its private gates recorded, Stage B is
merged, and a fresh `origin/main` baseline passes. Counts without their
commands, and mutations without their exact failing tests, are not completion
evidence.
