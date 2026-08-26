# Short-Identifier Cutover

**Status:** DESIGN — public design task only, revision 2. No implementation
plan exists yet, no application code or test has been modified, and no
migration has been executed.

**Base:** freshly fetched merged `origin/main` at
`e4478fc1beef985fecc16e485b0974568b4fc004`. Fresh public baseline:
`uv run python -m pytest -q` → 1,476 passed.

**Authority:** `AGENTS.md`, `BUILD.md` Safety Foundation, `docs/STATUS.md`
"S7 inherits these from S6", and
`docs/superpowers/specs/2026-08-26-inherited-safety-items-2-4-design.md`.

**Prerequisite state:** inherited Item 2 is implemented and parked, unpushed,
on branch `codex/inherited-item-2-prose-leakage` at
`cfed17fffd9f00e0036e7877ab6aa8b5342f93bc`. Its public gates passed. Its
trusted-local run reported publication-audit findings caused by private
registry identifiers shorter than the audit's long-term threshold. This cutover
removes that class of identifier at the source rather than weakening the audit
that found it.

**Revision 2 incorporates four owner corrections** — no vault-wide word
replacement, a corrected history-audit expectation, an isolated mutable copy
for dry-run, and removal of live-vault destructive rollback — **and the
approved `books.db` decision.**

## Objective

Raise every Grey Matter registry identifier to a minimum length so that no
registry-derived term can again be short enough to escape whole-text matching,
and do it as one reviewable, reversible private commit.

This is a data and convention migration plus the public code that enforces the
new rule. It adds no product surface, no dependency, no schema, and no second
scanner.

## Approved product decisions

Decided by the product owner. These are inputs, not open questions.

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
11. Only identifiers shorter than five characters are rewritten; identifiers
    already at or above the floor keep their current value.
12. `former_slugs` is retained as unread provenance only, never as an alias.
13. Matching product and member values inside `books.db` are migrated in the
    same reversible cutover commit, under the narrow allowlist rules below.

## Identifier format and the five-character minimum

The grammar is unchanged: lowercase ASCII letters and digits in hyphen-joined
tokens, matching the existing expression `^[a-z0-9]+(?:-[a-z0-9]+)*$`. The
cutover adds one rule: the whole identifier must be at least five characters
long, counting hyphens.

Five is deliberately one character above the publication audit's long-term
threshold of four. The audit classifies a term of four or more characters as a
long term and matches it in every tracked text file; three or fewer makes it a
short term. A four-character floor would sit exactly on that boundary with no
margin if the classification is ever retuned. Five guarantees every registry
identifier is matched by the strongest rule the audit has, with one character
to spare.

The floor applies to the four registry axes this cutover governs: entity,
product, member, and workspace. The `project` axis that `app/rename.py` also
knows about is a directory name inside a bundle pipeline, not a registry
identifier, and is out of scope.

Reserved names remain reserved regardless of length.

## Deterministic type-suffix mapping

### Which identifiers are rewritten

Only identifiers shorter than five characters are rewritten. An identifier that
already satisfies the floor keeps its current value.

Uniform suffixing of every identifier was considered and rejected: it would
turn a small, reviewable commit into a whole-vault rewrite, multiply the chance
of a missed reference, and make owner review far harder. The suffix exists to
add length, not to encode type, so there is no correctness reason to apply it
where length already suffices.

### The mapping function

For an identifier `old` on axis `axis`, the new value is `f"{old}-{axis}"`,
where `axis` is exactly one of `entity`, `product`, `member`, `workspace`.

The function is total, deterministic, and depends only on `(axis, old)`. It
performs no lookup, consults no counter, and has no tie-breaking branch. The
same inventory always produces the same mapping, which is what lets the
dry-run diff be trusted as a preview of the apply.

The suffixes are seven to ten characters, so the shortest possible output is a
one-character identifier plus the seven-character `-entity` or `-member`
suffix — eight characters, comfortably above the floor. The mapping never needs
a second pass and never produces a value that still violates the rule.

### Already-suffixed values

No in-scope identifier can already carry an axis suffix, and arithmetic
guarantees it: every suffix is at least seven characters, so any identifier
ending in one is at least eight characters, above the floor and therefore never
in scope. An identifier such as `ab-entity` is left alone.

The implementation must still assert this rather than rely on the arithmetic
surviving a future edit. If an in-scope identifier ends in `-entity`,
`-product`, `-member`, or `-workspace`, the tool refuses and escalates instead
of appending a second suffix. Double-suffixing is never performed.

## Scoped replacement — no vault-wide word substitution

**A short identifier may also be an ordinary English word.** Blind
whole-vault token replacement would corrupt unrelated notes, and the corruption
would be invisible: a note whose prose contained the word would be silently
edited, and no gate keyed to that same token could tell the difference.

The cutover therefore rewrites an identifier **only** where a registry
identifier is structurally required. Nothing is rewritten because it merely
looks like the identifier.

This is a deliberate departure from `app/rename.py`, whose entity planner
performs a boundaried whole-vault token sweep. That sweep is acceptable for a
one-off rename of a distinctive multi-token slug chosen by an operator; it is
not acceptable for a bulk cutover of identifiers selected precisely because
they are short.

### The enumerated rewrite locations

Only these locations are rewritten. The list is closed: a location not on it is
never modified automatically.

**Entity axis**

- `_system/entities.yaml` — the top-level key under `entities:`.
- The bundle directory name at the vault root, and therefore every path
  beneath it, including `outbox/`, `staging/`, `.receipts/`, and `books.db`.
- `_system/products.yaml` and `_system/members.yaml` — the per-entity grouping
  key.
- `_system/workspaces.yaml` — `entity:` and `primary_entity:` values.
- Markdown front matter — the `entity:` field value.
- Outbox proposal records — the `entity:` field value, and the first path
  component of `src:` and `dst:`.
- `_system/scripts/action-policy.yaml` — the first path component of each
  pattern in `paths:` and in `except:`. Both are rewritten in the same pass;
  see the fail-open rule below.

**Product axis**

- `_system/products.yaml` — the product key within its entity's mapping.
- Markdown front matter — the `product:` field value.
- `_system/workspaces.yaml` — `product:` values, and an `id:` whose entry is a
  product-kind workspace.
- Approved `books.db` `(table, column)` pairs only.

**Member axis**

- `_system/members.yaml` — the entry `id:` value within its entity's list.
- Markdown front matter — the `member:` field value.
- `_system/workspaces.yaml` — `member:` values.
- Approved `books.db` `(table, column)` pairs only.

**Workspace axis**

- `_system/workspaces.yaml` — the entry `id:` value.

Values are matched as exact whole field values, not as substrings of them. A
front-matter `entity:` whose value merely contains the identifier is not a
match and is not rewritten.

### The fail-open rule

`BUILD.md` §4 names the danger precisely: renaming an allow rule's `paths:`
while missing its `except:` for `.sensitive/` converts a deny into an allow.
Both keys are rewritten in the same pass over the same rule, and a public test
must assert that a `.sensitive/` read is still denied after the cutover. This
is the rename test `AGENTS.md` requires, and it is mandatory here.

### The advisory report

Scoped replacement means the cutover no longer reaches an identifier hardcoded
somewhere unstructured — a vault script constant, for example, which the old
whole-vault sweep did reach. Silently losing that coverage would be worse than
the sweep it replaces.

The inventory and dry-run therefore produce an **advisory report**: every
occurrence of an in-scope identifier, matched as a whole token with the
existing boundary pattern, that lies **outside** the enumerated locations,
grouped by file and line.

The report is never acted on automatically. The owner dispositions each
occurrence as either:

- **structural** — a genuine reference. The owner either extends the approved
  location list to cover that exact file and field, or fixes it by hand in a
  separate commit before the cutover; or
- **incidental** — an ordinary word that must be left exactly as it is.

Every occurrence must carry a disposition before apply. The dispositions are
part of the approval manifest and are bound by its digest, so the tree that is
migrated is the tree whose incidental words the owner actually reviewed.

## Collisions

Three collision classes are checked during inventory, before any mapping is
shown and long before any write. The tool never invents a disambiguating
suffix, counter, or alternate spelling.

**1. A new value collides with an existing identifier on the same axis.** The
mapping is injective on distinct inputs, so this arises only when the new value
equals another object's current identifier — for example, entity `ab` maps to
`ab-entity` while an entity named `ab-entity` already exists. Applying it would
reuse an existing identifier for a different object, violating decision 7.
Refuse.

**2. A new value collides with another new value.** Impossible for distinct
inputs on one axis, since appending a constant suffix preserves distinctness.
Checked anyway: a mapping assembled from a faulty inventory could contain
duplicate inputs, and a silent duplicate is worse than a noisy refusal.

**3. The same literal exists on more than one axis.** In revision 1 this was a
hard refusal, because the whole-vault entity sweep would have rewritten a
same-named product's occurrences and silently given it the entity's suffix.
**Scoped replacement removes that hazard.** Each axis now touches only its own
structurally-typed locations: `entity:` and `product:` are distinct front-matter
fields, and their registry homes are distinct files or distinct nesting levels.
An entity `ab` and a product `ab` migrate independently and correctly.

It is therefore no longer a refusal. It remains a reportable condition: the
inventory must surface it so the owner sees that one literal carries two
meanings, and the residual gate attributes findings per axis and per location
rather than per token.

## Affected interfaces

The inventory below is the result of reading the public source at the recorded
base.

### Identifier validation — five copies that must agree

The same grammar is restated in five places, none enforcing any length:

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

`AGENTS.md` records the failure mode duplicated rules produce: when the sidebar
and the validator disagree about what exists, the disagreement stays invisible
until something breaks. Five independent length checks would reproduce it. The
floor must be expressed once and consumed by every site, and a public
structural test must assert no module defines its own registry-identifier
length rule.

Collapsing the five grammar copies is a broader refactor than this cutover
needs. Only the length rule must be single-sourced.

### Records and paths

- **Review tokens.** S7 binds an approval to exact proposal bytes. The cutover
  rewrites pending proposal records, invalidating every review token issued
  before it. This is correct fail-closed behaviour and must be stated to the
  operator, never engineered around.
- **Receipts.** `receipt_relative_path` is
  `<entity>/outbox/.receipts/<proposal_id>.yaml`. Receipt content carries no
  entity value, so an entity migration moves receipts without rewriting them,
  and spent-id facts survive because the move and the commit are one commit. A
  public test must prove a spent id is still refused afterwards.
- **Stored paths.** `Scope.resolve_stored` requires a stored path's first
  component to equal the current entity, so a stale prefix becomes a
  cross-scope refusal rather than a silent mis-resolution.

### `books.db` — approved, under a narrow writer allowlist

Leaving `books.db` untouched would leave rows referencing registry values that
no longer exist, with no alias to resolve them — incompatible with decision 2.
Migrating it is approved. It is `UPDATE`-only: no `CREATE`, `ALTER`, or `DROP`,
and therefore no schema change.

The danger is that migrating it naively reproduces exactly the error correction
1 forbids, in a binary file where no text gate can see it. Two public-source
facts make this concrete:

- [app/registry.py:56](app/registry.py:56) counts references over
  `product: ("product", "tag")` and `member: ("member", "member_id")`.
- [app/rename.py:182](app/rename.py:182) says of that same set that the column
  update is deferred "and `fund_holdings.member_id` is opaque, not the registry
  id".

A column named `member_id` is therefore already documented in-tree as **not**
necessarily a registry identifier, and a column named `tag` may hold free text
that merely coincides with a product id. Updating either by name would corrupt
rows silently.

The rules are binding:

1. Migrate only explicitly approved `(table, column)` pairs proven to store
   OneOS registry identifiers.
2. Neither `registry.py::_DB_COLUMNS` nor `rename.py`'s broad column-name
   counter may be reused as the writer allowlist.
3. `fund_holdings.member_id` is excluded.
4. `tag` columns are excluded by default. One may be included only if the
   trusted-local schema inventory proves that exact table and column stores
   product registry identifiers and the owner approves it.
5. A column name is never evidence. The allowlist identifies exact
   `(table, column)` pairs.
6. `books.db` must be Git-tracked. Ignored, untracked, unreadable, or
   concurrently changed databases are hard stops.
7. After updating, every approved pair is re-queried and must return zero
   remaining old registry values.
8. Reference counting may stay deliberately broad. Over-counting only causes a
   refusal, which is safe; writing must stay narrowly allowlisted.

Rule 6 is partly self-enforcing under the isolated-build architecture below: an
untracked `books.db` never appears in the isolated worktree at all, which makes
its absence immediately detectable rather than silently skipped.

Values are updated with parameter-bound `UPDATE` statements matching the exact
old value. Table and column identifiers are quoted using the existing
`_quote_identifier` rule, because an identifier cannot be parameter-bound.

## Migration scope: current tree only

Only the working tree at the cutover commit's parent is migrated. Grey Matter
history is not rewritten, so historical commits retain their original
identifiers permanently and by design. Rewriting the system of record's history
to satisfy a scanner would destroy the audit trail invariant 2 exists to
protect.

### The combined history audit must be clean — no expected residue

Revision 1 claimed the combined history audit would still report historical
occurrences and that this residue should be recorded as expected. **That was
wrong, and the corrected expectation is stricter: the combined public history
audit must be clean.**

The mechanism is the audit's term seeding. `load_instance_terms` builds its
term set from the **current** registries at gate time — entity keys, product
keys, member ids, workspace ids. After the cutover those registries contain
only the new identifiers. The retired short identifiers are no longer registry
values, so they are never seeded as terms, and the history scan never looks for
them. Old identifiers remain in Grey Matter's history, but they are not terms,
so they produce no findings.

The audit is therefore clean, not clean-with-exceptions. Any finding after the
cutover is a real finding and must be investigated. It must never be recorded
as expected residue, suppressed, exempted, or added to `.gitleaksignore`.

Two consequences are load-bearing and must be pinned by tests:

- **Term collection must not change.** If `former_slugs` values — or any other
  retained provenance — were ever added to term collection, the retired
  identifiers would be seeded again and the audit would go red. A public test
  must assert that term collection reads only entity keys, product keys, member
  ids, and workspace ids.
- **New identifiers are long terms.** Every post-cutover identifier is at least
  five characters, so the audit matches it in all tracked text, not only
  Markdown. The public repository must contain none of them.

## Dry-run, approval, and explicit apply

Revision 1 described a dry-run that "writes nothing" while also requiring each
mapping to be applied before the next is planned. Those cannot both be true:
the planners read from disk. The contradiction is resolved by giving the
dry-run its own mutable copy.

### Isolated mutable copy

Every planning and building step operates on a **temporary isolated worktree**
created from the recorded source HEAD, never on the live vault. It is
materialised with Git's own worktree mechanism so it shares the vault's object
database — which is what later makes promotion a pure ref update rather than a
file copy — while having a completely separate working tree and index.

The live vault is not written, not locked, and not read for mutation during
planning. It is read once, at the start, to record HEAD and confirm a clean
status.

The temporary worktree is removed when the operation ends, in success or
failure.

### The four stages

1. **Inventory** — read-only against the live vault. Enumerate in-scope
   identifiers per axis, run the three collision checks, produce the advisory
   report, and produce the `books.db` schema inventory and reference counts.
   Emits the proposed mapping and the proposed `(table, column)` allowlist.
   Writes nothing, commits nothing, takes no lock.
2. **Owner approval** — the owner reviews and explicitly approves a single
   canonical **approval manifest**. See its binding below.
3. **Dry run** — default. In a temporary isolated worktree at the manifest's
   source HEAD, apply every mapping in the fixed order and render the complete
   combined diff, move list, and `books.db` row-change summary. Discard the
   worktree.
4. **Apply** — explicit `--apply`. Build and verify in isolation, then promote.

Stages 3 and 4 build identically, because the mapping function is
deterministic and the order is fixed: entity, then product, then member, then
workspace, and within each axis sorted by old identifier.

### What owner approval binds

Approval is not a verbal yes to a table on screen. It binds one canonical
manifest whose SHA-256 is recorded, and the tool refuses to apply anything
whose manifest digest, or whose live HEAD, does not match. The manifest binds:

- the **source HEAD** the mapping was derived from;
- the exact **old → new mappings**, per axis;
- the exact approved **`books.db` `(table, column)` pairs**;
- the **disposition of every advisory-report occurrence**; and
- the **SHA-256 of the canonical manifest** itself.

The manifest is serialised canonically — fixed key order, fixed encoding — so
its digest is reproducible. The tool never recomputes the mapping from a
possibly-changed vault at apply time; it uses the approved manifest and refuses
if the vault has moved beneath it.

The advisory dispositions are an addition to the four bindings the owner
specified. They are included because the enumerated-location rule makes the
set of deliberately-untouched occurrences part of what is being approved; if
that set is unbound, a note could change between review and apply and the
cutover would not notice.

## One reversible commit: build in isolation, then promote

### Why the existing tool cannot be looped

`apply_rename` commits once per rename. Running it N times produces N commits,
violating decision 6. Squashing afterwards is worse: intermediate states are
not required to be valid, so a mid-sequence validator run could fail on a tree
that was only ever meant to be transient.

### Build

In the temporary isolated worktree at the manifest's source HEAD:

1. Verify the manifest digest and that the worktree HEAD equals the manifest's
   source HEAD.
2. For each mapping in the fixed order, plan it against the **current state of
   the isolated worktree** and apply its edits and moves immediately, before
   planning the next.

   Sequential application is what makes composition correct. The planners read
   from disk; building all plans up front against the original tree and merging
   them would mean two mappings touching one file produce two different
   full-file texts, and the second would silently discard the first.
3. Apply the approved `books.db` updates, once, in this worktree. The resulting
   SQLite artifact is the one that will be committed and promoted. It is never
   regenerated.
4. Run the scoped residual gate and the in-database residual query.
5. Run the validators on the fully migrated tree.
6. Commit exactly once in the isolated worktree.

Because the isolated worktree shares the vault's object database, this commit
already exists as an object in the vault repository. Nothing has been written
to the live working tree.

### Promote

Under the live shared action lock, and only then:

1. Re-read live HEAD and live status.
2. Refuse unless HEAD is byte-identical to the manifest's source HEAD and the
   status is unchanged from the pre-build capture.
3. Fast-forward the live branch to the built commit with `git merge --ff-only`.

`git merge --ff-only` is the right primitive because it enforces the same
guarantees independently: it refuses if the target is not a fast-forward, and
Git's own checkout safety refuses to overwrite a modified or untracked file. So
the precheck and the primitive must both agree before anything moves.

The SQLite artifact is promoted as the exact bytes that were verified. The
transformation is never re-run against the live database. This also disposes of
SQLite's page-level non-determinism: two runs of the same `UPDATE` need not
produce identical bytes, so verifying one artifact and then rebuilding another
would verify something that was never shipped.

### No destructive rollback on the live vault

Revision 1 rolled back with `git reset --hard HEAD` and `git clean -fd` on the
live vault. **That is removed.** `clean -fd` deletes untracked files, and a
concurrent process or an operator could have created one; a safety mechanism
must not be able to destroy data the cutover never owned.

It is not needed. Every failure before promotion happens inside the temporary
worktree, which is simply discarded. The live vault was never written, so there
is nothing to undo. The built commit, if one exists, remains an unreferenced
object and is collected by Git's ordinary maintenance.

The one destructive operation that remains permitted is the removal of the
temporary worktree itself.

### Exact detection of unresolved old identifiers

Two gates run in the isolated worktree before the commit, never after.

**1. The scoped residual gate.** For every enumerated rewrite location, assert
that no old identifier remains. The gate is scoped to the same locations as the
writer, and this symmetry is essential: a whole-vault text gate would refuse
forever on any short identifier that is also an ordinary word appearing in
prose — precisely the values this cutover exists to retire. A gate that cannot
pass is not a safety mechanism.

Within those locations, matching is by exact whole field value, or by exact
path component for paths.

The advisory report is regenerated and compared against the manifest's
dispositions. Any occurrence outside the enumerated locations that was not
dispositioned in the approved manifest aborts the build.

The boundary pattern used for the advisory scan is the existing
`(?<![\w-])<old>(?![\w-])`. Its behaviour matters and must be pinned: a
migrated `ab-entity` does not match a scan for `ab`, because the lookahead
fails on the hyphen, while a bare `ab` still does. A naive escaped search would
report every successful rewrite as a residual.

**2. The in-database residual query.** For every approved `(table, column)`
pair, query for any remaining old registry value and require zero rows. The
text gate skips binaries and can never see inside `books.db`, so without this
query the database half of the migration would have no fail-closed check at
all.

Either gate failing aborts the build and discards the temporary worktree.

### `former_slugs` is provenance, not an alias

The cutover records `former_slugs: [old]` on the migrated registry key and
exempts that line from the residual gate.

This is not a compatibility fallback and must never become one. No code path
resolves `former_slugs` today: it is written and gate-exempted, never read. It
is inert provenance that makes the mapping legible in the vault and supports
reconciliation after a revert.

Two testable constraints follow:

- No reader may ever resolve an identifier through `former_slugs`, so decision
  2 cannot be eroded later by a well-meaning fallback.
- Retained values must not enter the publication audit's term set, as the
  history-audit section above requires.

## Interruption behaviour

**During inventory, dry run, or build.** The live vault was never written.
Recovery is to delete the temporary worktree; the vault needs no action. A
partially built commit, if any, is unreferenced.

**Before promotion.** Identical: nothing to undo.

**During promotion.** The window is one fast-forward. Git's checkout is
crash-safe at the ref level: either the ref moved or it did not. An operator
who finds an interrupted promotion inspects `git status` and `git log` and
resolves it with ordinary Git commands. They must **not** run `reset --hard` or
`clean -fd` as a recovery step; that is the same destructive action this design
removed, and it is no safer when performed by hand.

**After promotion.** `git revert` of the single cutover commit restores every
identifier, including the `books.db` blob, because the database is tracked and
the commit is one atomic unit. This is the revert test `AGENTS.md` names as one
of the two tests that matter more than coverage.

**A committed-but-unconfirmed promotion** — the ref moved but reading back the
commit id or releasing the lock failed — is a distinct outcome from a failed
cutover and must be reported as "committed, do not retry". Retrying would
attempt to migrate identifiers that no longer exist and would fail inventory,
but the operator must be told rather than left to infer it.

## Public synthetic tests

All tests use synthetic vaults in temporary directories, following the existing
rename tests. No test may read a real vault, registry, or identifier.

Required coverage:

- **Mapping.** Determinism; the suffix per axis; identifiers at or above the
  floor untouched; every output satisfies the floor.
- **Collisions.** Classes 1 and 2 refuse with distinct diagnostics before any
  write. Class 3 does **not** refuse: an entity and a product sharing one
  literal migrate independently and correctly, and this test is what pins
  scoped replacement.
- **Scoped replacement.** A note whose prose contains a short identifier as an
  ordinary word is byte-identical after the cutover. A front-matter field whose
  value merely contains the identifier is unchanged. Only enumerated locations
  change.
- **Advisory report.** An occurrence outside the enumerated locations is
  reported, not rewritten; an undispositioned occurrence aborts the build.
- **Already-suffixed.** An in-scope identifier ending in any axis suffix
  refuses rather than double-suffixing.
- **Floor enforcement.** Every validation site rejects a sub-floor identifier,
  and a structural test asserts the length rule is single-sourced.
- **One commit.** A multi-mapping cutover produces exactly one new commit, and
  `git revert` restores the tree including `books.db`.
- **Isolation.** A failure injected at every stage before promotion leaves the
  live vault byte-identical, with untracked files intact. A test must assert
  the cutover never invokes `reset --hard` or `clean -fd` against the live
  vault.
- **Promotion refusal.** A live HEAD that moved, or a live status that changed,
  refuses promotion and leaves the vault untouched.
- **Approval binding.** A manifest whose digest, source HEAD, mappings,
  `(table, column)` pairs, or dispositions differ from the approved one is
  refused before any build.
- **Residual gates.** A deliberately missed enumerated location aborts; a
  correctly migrated occurrence does not trip the gate; a bare old token inside
  a longer token is not a residual; an in-database old value aborts.
- **`books.db`.** Approved pairs are updated; a non-allowlisted column with a
  matching name is **not** updated; an untracked or unreadable database is a
  hard stop; the promoted artifact is byte-identical to the verified one.
- **Ordering.** Dry-run and apply build identical trees for an unchanged
  source HEAD.
- **Receipts.** A spent proposal id is still refused after an entity cutover.
- **Proposals.** Stored `src`/`dst` prefixes are rewritten and a pre-cutover
  review token is refused afterwards.
- **`former_slugs`.** No resolver consults it, and term collection reads only
  entity keys, product keys, member ids, and workspace ids.
- **Fail-open guard.** A policy rule's `paths:` and its `except:` for
  `.sensitive/` are rewritten in the same pass, and a `.sensitive/` read is
  still denied afterwards.

Mutation evidence is mandatory and must include at least: removing the length
floor from the shared validator; widening the writer allowlist to a
column-name match so a non-allowlisted column is updated; skipping the
in-database residual query; dropping the boundary from the advisory scan;
removing a collision check; replacing sequential application with up-front plan
composition so two mappings touching one file lose the first rewrite; and
removing the promotion precheck so a moved HEAD is accepted. Each names the
exact test that must go red, and each target file is restored byte-for-byte
before the suite is re-run green.

## Public release sequencing

The public change cannot ship as one release. If read-time floor enforcement
and the cutover tool ship together, the tool cannot read the vault it must
migrate: the pre-cutover vault contains sub-floor identifiers, and
`EntityCatalog.load` would reject the manifest before inventory could run. The
app would refuse to start against the very vault awaiting migration.

- **Stage A** — inventory, manifest, dry-run, build, promote, both residual
  gates, and the synthetic tests. No read-time floor; the tool must be able to
  read a pre-cutover vault.
- **Private cutover** runs between the stages.
- **Stage B** — floor enforcement at every validation site, plus the public
  synthetic fixtures that currently use sub-floor slugs.

Stage B's public churn is bounded and mechanical: the dominant synthetic entity
slug in the existing suite is four characters and appears in the low hundreds
of occurrences, with a small number of other sub-floor fixture slugs. This
churn is expected. It must remain fixture-only: if enforcing the floor requires
application logic changes beyond the validation sites, stop and re-open the
design.

## Trusted-local sequence

The public agent's role ends at a reviewed public branch. Everything touching
Grey Matter belongs to the trusted local agent and may never be delegated to a
cloud task.

1. **Inventory (private, read-only).** Run the Stage A tool against the live
   vault. Produce the mapping, the advisory report, the `books.db` schema
   inventory with reference counts, and the proposed `(table, column)`
   allowlist. Nothing is written.
2. **`books.db` proof.** Confirm the database is Git-tracked and readable, and
   prove for each proposed pair that it stores OneOS registry identifiers.
   `fund_holdings.member_id` stays excluded; a `tag` column is included only
   with proof and explicit approval.
3. **Owner approval.** Present the mapping, the dispositions, and the
   allowlist. The owner approves the canonical manifest; record its SHA-256.
4. **Pre-cutover proof.** Capture opaque `git status --porcelain=v2
   --untracked-files=all`, worktree and cached binary diffs, outside both
   repositories, per `BUILD.md`.
5. **Dry run.** Review the full combined diff and the row-change summary.
6. **Build and promote.** One commit. Record its id.
7. **Private gates.** The vault's own suite, `check_v2` at 0 errors and 0
   warnings, and the combined repo+vault audit in **both** current-tree and
   history modes. Both must be clean. A finding is a real finding.
8. **Preservation comparison.** Compare the opaque snapshots. A clean vault
   stays clean apart from the single cutover commit; a vault with approved
   pre-existing edits retains exactly those edits.
9. **Independent review.** A reviewer independently re-derives the mapping from
   the inventory, re-runs the public suite and the mutation campaign, and
   checks every factual claim.

## Sequencing with the inherited items

1. This cutover completes: Stage A merged, private cutover promoted and
   verified, Stage B merged.
2. **Item 2 resumes.** The parked branch is rebased onto the resulting
   `origin/main` and its trusted-local audit is re-run. Both audit modes must
   now be clean. Item 2 merges only on that evidence.
3. **Item 4** — dependency-time filesystem outcomes.
4. **Item 3** — declaration completeness.

The 2 → 4 → 3 order is unchanged from the inherited design. This cutover is
inserted before Item 2's merge, not in place of any item. Item 2's branch must
not be modified while this cutover is in progress.

## Stop conditions

Work halts and returns to the product owner on any of these:

- A `books.db` pair proposed for the writer allowlist without proof that the
  exact table and column store registry identifiers.
- Any attempt to derive the writer allowlist from `registry.py::_DB_COLUMNS`,
  from `rename.py`'s column-name counter, or from column names alone.
- A proposal to include `fund_holdings.member_id`, or a `tag` column without
  proof and explicit approval.
- `books.db` ignored, untracked, unreadable, or changed concurrently.
- A non-zero in-database residual after the update.
- Any collision of class 1 or class 2.
- An in-scope identifier that already carries an axis suffix.
- An undispositioned advisory occurrence, or any proposal to rewrite outside
  the enumerated locations.
- A request to add an alias, a fallback resolver, a `former_slugs` lookup, or
  any dual-read compatibility path.
- A request to weaken the publication audit, add an exemption or
  `.gitleaksignore` entry, lower the five-character floor, or record a
  post-cutover audit finding as expected residue.
- A request to rewrite Grey Matter history, or to migrate beyond the current
  tree.
- Any proposal to run `reset --hard`, `clean -fd`, or any destructive recovery
  against the live vault.
- A live HEAD or status that changed between build and promotion.
- Discovery that enforcing the floor requires application logic changes beyond
  the validation sites.
- Any dependency, schema, convention, or security-boundary change.
- Any destructive action beyond the single reversible commit, or any
  deployment.
- Any need for private material inside a public task, or any instruction to
  place an instance-specific value in this repository.

## Explicitly out of scope

- Rewriting Grey Matter history.
- The `project` axis, module numbers, block values, and `sub:` values.
- Any `books.db` schema change: `UPDATE` only, no DDL.
- Any change to S7 review tokens, receipts, quarantine, or the managed-
  directory boundary.
- Changes to Items 2, 3, or 4 beyond resuming them in order.
- New dependencies, schemas, registry values, conventions, or product surfaces.
- Collapsing the five grammar copies beyond single-sourcing the length rule.

## Completion

The cutover is complete when Stage A is merged, the private cutover exists as
one revert-testable commit with its private gates recorded and both audit modes
clean, Stage B is merged, and a fresh `origin/main` baseline passes. Counts
without their commands, and mutations without their exact failing tests, are
not completion evidence.
