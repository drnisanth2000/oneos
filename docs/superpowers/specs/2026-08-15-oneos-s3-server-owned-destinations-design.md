# OneOS Safety Foundation S3 — Server-Owned Destinations Design

**Date:** 2026-08-15

**Status:** Implemented and merged; historical design record

**Implementation:** PR #6 merged at `21da6fe`. Do not use this file as a
current execution plan.

**Authority:** `BUILD.md` Safety Foundation, the private OneOS specification,
the authoritative vault conventions, and the approved module-general decision

**Scope:** Safety Foundation S3 only

## Purpose

S1 made intake a tracked, revertible receipt. S2 bound every request and
adapter to one immutable, manifest-backed entity scope. S3 makes the final
classification destination equally authoritative.

Today the browser can submit `module`, `sub`, and `block`, and the outbox
writer assembles a destination path from those values. Scope containment keeps
that path within one entity, but it does not prove that the module is active,
the sub-module belongs to that module, the block matches the registry, or the
destination is the module's canonical lifecycle directory.

S3 introduces one server-side destination authority. It validates runtime
registries, derives the block, constructs the only legal path, and revalidates
stored proposals before they can be previewed or approved. Invalid input fails
before a proposal file is written.

S3 does not authorize S4 proposal freshness or identifier changes, S5 Git
transaction isolation, or S6 user-visible error presentation.

## Approved decisions

- The server owns entity, module, sub-module, block, and destination path.
- Entity continues to come only from the request-local `Scope`; no service
  accepts a second entity authority.
- Module activation reads the selected entity's `flags:` only. `archetype:` is
  never merged at read time.
- A module must be declared, active for the selected entity, present on disk,
  and have a safe `active/` lifecycle directory to receive a classification.
- A lifecycle-incompatible module cannot receive a classification through the
  ordinary `<module>/active/` path.
- An empty sub-module is valid and means module-general content.
- A non-empty `sub:` must be a canonical registry id under the selected module
  and must satisfy its optional activating flag.
- `block` is derived from the selected module's registry record. A client value
  is never an authority.
- The filename is one Markdown leaf, not a path. Traversal is rejected rather
  than silently reduced to a basename.
- Proposal creation and every later proposal load repeat destination
  validation. Editing proposal YAML cannot create a new authority.
- No new dependency, daemon, queue, scheduler, screen, or vault taxonomy is
  introduced.

## Fixed constraints

- No instance-specific entity, person, product, credential, or vault-path
  value may enter the public repository.
- All fixtures use synthetic entities and registry values.
- Entity discovery comes from `_system/entities.yaml`.
- Modules, blocks, and sub-module ids come from
  `_system/archetypes.yaml` at runtime.
- Flags are the only read-time module and sub-module activation authority.
- Blocks remain lowercase and derived; they are not written into curated file
  front-matter.
- Sub-modules remain front-matter values. S3 never creates physical sub-folders.
- Curated changes still go through the outbox. S3 adds validation; it does not
  create another write path.
- The request path remains deterministic and contains no LLM call.
- Grey Matter is read-only during public implementation and verification.

## Threat model

S3 must fail closed against these inputs:

- an unknown or inactive module;
- a registry module missing from disk;
- a module whose `active/` path is absent, redirected, or not a directory;
- a sub-module copied from another module;
- a sub-module whose activating flag is absent;
- a path-like, absolute, multiline, or otherwise non-canonical module/sub value;
- a client-supplied block that differs from the registry-derived block;
- a filename containing POSIX or Windows separators, `.`/`..`, or a non-Markdown
  destination;
- a destination that resolves outside the selected entity or through a
  redirected module/lifecycle directory;
- an edited proposal whose entity, module, sub, block, or destination no longer
  equals the canonical server result; and
- a direct service caller attempting to bypass the HTTP route's checks.

The trusted registries may themselves be malformed. Missing mappings, wrong
YAML shapes, or missing block values are configuration errors and must deny the
destination rather than create a best-effort path.

## Architecture

### Runtime registry queries

`app/vault.py` remains the registry owner. It exposes strict read helpers for:

- the active module set for a bound scope;
- the active sub-module set for one active module; and
- the block derived for a declared module.

These helpers use a fresh manifest/catalog rooted at the same vault as the
request scope. They reuse the existing flag and module activation logic rather
than creating a second activation algorithm.

Sub-module activation follows the same rule as module activation: an entry
without `flag:` is active; an entry with `flag:` is active only when that exact
flag appears in the selected entity's `flags:` list.

### Destination resolver

A focused `app/destinations.py` service owns classification destination
validation. Its public result is an immutable canonical value containing:

```text
entity
module
sub: str | None
block
source-relative path
destination-relative path
destination filesystem path
```

The resolver accepts a bound `Scope`, the inbox item path or filename, the
submitted module, the submitted sub value, and an optional claimed block used
only for tamper detection. It returns the canonical value or raises a typed
destination error. It performs no writes.

The resolver is the only component allowed to assemble a classification
destination. Routes, templates, the classifier, and outbox code may display or
transport its result, but may not concatenate their own destination paths.

### Outbox boundary

`propose_classification()` continues to accept the bound `Scope` and the
classification choice, but it calls the resolver before creating `outbox/` or
writing YAML. It stores only the resolver's canonical entity, module, sub,
block, source, and destination.

The service does not accept a trusted block or entity argument. An optional
client block claim can be checked for backward compatibility and tamper tests,
but the stored block always comes from the registry.

`load_proposals()` parses each classification proposal, performs the existing
S2 entity/scope checks, and then re-runs S3 destination resolution. The record's
module, canonical sub, block, and destination must exactly equal the resolved
values. A mismatch makes the proposal invalid before preview or approval reads
the source body.

## Canonical resolution algorithm

The resolver applies this order:

1. Confirm the scope still selects an entity in the current manifest.
2. Validate the source leaf. It must be one non-empty `.md` filename with no
   POSIX or Windows separator and must identify the selected entity's
   `00-inbox/active/` receipt at proposal creation.
3. Require `module` to be a string that exactly matches a declared module id.
   No trimming, case folding, basename conversion, or path normalization makes
   an invalid value valid.
4. Derive the selected entity's active modules from `flags:` only and require
   the module to be active.
5. Require the lexical module directory and its lexical `active/` child to
   exist as real directories and resolve to themselves beneath the selected
   entity. A symlink or redirected lifecycle path is invalid, even when its
   target remains inside the same entity.
6. Interpret an exactly empty submitted sub value as module-general `None`.
   Any non-empty value must exactly match an active sub id under the selected
   module. Whitespace, separators, newlines, and ids owned by other modules are
   invalid.
7. Derive a non-empty block from the module registry. If a client block claim
   is present, require exact equality with the derived value.
8. Resolve `<module>/active/<filename>` through `Scope`, require the result to
   remain under the bound entity, and require its parent to be the validated
   canonical lifecycle directory.
9. Return the immutable canonical destination.

Validation of an already stored proposal uses the same algorithm and exact
record comparisons. It validates the source path's shape and scope but leaves
source existence/freshness semantics to S4, which will add the stored source
hash and stale-source refusal.

## Module-general content

The approved empty-sub decision follows the authoritative convention that
files without `sub:` are module-general.

- Browser input `sub=""` becomes canonical `None`.
- Proposal YAML stores `sub: null`.
- Preview shows removal of the triage receipt's `sub: triage` line.
- Approval removes the `sub:` field instead of writing an empty `sub:` value.
- A non-empty sub always writes the exact canonical registry id.

This does not create a folder or a second taxonomy.

## Request and rendering flow

### Triage render

1. The request dependency creates the immutable entity scope.
2. The deterministic classifier produces a recommendation.
3. The destination resolver validates that recommendation against the bound
   entity's current registries and disk structure.
4. The template renders the canonical module/sub/block and an Accept action
   only for a valid destination.

An invalid or stale classifier route is not silently repaired and does not get
an actionable button. S6 will later own a user-friendly explanation.

### Proposal POST

1. The route receives the filename, module, and sub selection. Entity remains
   the route-bound scope.
2. Any form-provided `entity` field is rejected as an unexpected second
   identity claim; it is never compared, normalized, or passed to a service.
3. The server rejects path-like filenames instead of calling `Path.name` to
   transform them.
4. `propose_classification()` resolves the canonical destination again because
   the browser is untrusted.
5. Only after successful resolution may it create the outbox directory and
   write the proposal.
6. Diff preview reloads and revalidates the proposal before reading the receipt.

The template stops treating block as an authority. The route may consume an
optional legacy `block` form value solely to reject a mismatch; omission is
valid because the server derives it.

### Proposal load and approval

1. Proposal discovery remains inside the bound entity's outbox.
2. YAML is parsed into typed scalar values; wrong shapes fail closed.
3. S2 verifies entity and stored-path scope.
4. S3 re-derives the destination and compares every canonical field.
5. Preview or approval proceeds only after both boundaries pass.

Approval still performs the existing move, front-matter update, and commit.
S3 does not change identifier generation, stale-source behavior, staged-file
handling, rollback, or commit path isolation.

## Error model and no-mutation guarantee

Destination failures use typed exceptions that distinguish invalid source
leaf, inactive/missing module, invalid sub, block mismatch, unsafe lifecycle
path, malformed record, and non-canonical destination. Outbox-facing failures
are represented as `OutboxError` subclasses so existing approve/reject routes
continue to fail closed.

For every destination failure:

- no outbox directory or proposal file is created;
- no source content is read when untrusted destination fields are already
  invalid;
- no module or lifecycle directory is created;
- no source or destination file is moved or rewritten;
- no Git index or commit is changed; and
- no other entity path is opened.

The current Console may still render a generic server failure or preserve its
existing silent approve/reject behavior. Specific safe UI errors are S6.

## Test strategy

All implementation follows strict RED-GREEN-REFACTOR TDD. Tests use synthetic
registries, entities, modules, sub ids, paths, and Git repositories.

### Registry and resolver tests

- declared, active, present module with a registered active sub resolves;
- the same module with empty sub resolves to module-general `None`;
- block is derived from the module registry;
- unknown and inactive modules fail;
- a declared module missing from disk fails;
- a missing, non-directory, or redirected `active/` path fails;
- a lifecycle-incompatible module fails;
- unknown, wrong-module, and flag-disabled subs fail;
- malformed registry shapes and missing block mappings fail closed;
- POSIX traversal, Windows separators, absolute values, dot segments,
  whitespace variants, multiline values, and non-Markdown filenames fail;
- entity-root, module, active-directory, and destination-leaf symlink escapes
  fail; and
- a same-entity redirect to a different module also fails as non-canonical.

### Outbox service tests

- a valid proposal stores the derived block and canonical destination;
- callers cannot provide entity or trusted block authorities;
- module-general proposals store `sub: null` and preview removal of `sub:`;
- invalid input creates no outbox directory or proposal;
- forged entity/module/sub/block/destination records fail on load;
- tampered records fail before source-body reads;
- direct service calls cannot bypass the resolver;
- valid preview and approval behavior remains unchanged; and
- S1's adapter-created receipt remains approval-revertible.

### Route and rendering tests

- a valid recommendation displays canonical values and can write a proposal;
- an invalid or inactive classifier recommendation has no Accept action;
- form-provided entity claims and tampered module, sub, block claim, filename,
  and traversal requests write no proposal;
- request-local entity scope remains the only identity authority; and
- concurrent requests cannot share destination state.

### Mutation checks

The tests must fail if an implementation:

- trusts the submitted block;
- merges `archetype:` into `flags:`;
- accepts a module merely because a directory exists;
- accepts a sub owned by another module;
- ignores a sub's activating flag;
- normalizes traversal into a valid basename;
- constructs a path outside the destination resolver;
- validates only at proposal creation and trusts edited YAML later;
- creates missing lifecycle directories; or
- writes an empty `sub:` instead of removing it for module-general content.

## Expected public file surface

Implementation is expected to remain within:

- `app/destinations.py` — canonical destination value, resolver, and typed
  validation errors;
- `app/vault.py` — strict active-module/sub registry queries;
- `app/outbox.py` — resolve before proposal write and revalidate on load;
- `app/main.py` — reject path normalization and pass no destination authority;
- `templates/triage.html` — render canonical values, not a trusted block field;
- `tests/test_destinations.py` — focused resolver behavior;
- `tests/test_outbox.py` — proposal and tampered-record integration;
- `tests/test_app.py` — HTTP tampering and rendering; and
- existing scope/triage tests only where an interface must change.

The implementation plan may narrow this set after exact dependency mapping,
but it may not expand into S4-S6 behavior.

## Verification gates

Before S3 is complete:

1. Run focused destination, vault, outbox, triage, and route tests.
2. Run the complete public test suite.
3. Run the public history audit and secret scan.
4. Snapshot Grey Matter's complete Git status and binary diff.
5. Run the private test suite, structural validator, policy self-test, and
   combined public/private audit read-only.
6. Snapshot Grey Matter again and require byte-identical status and binary
   diff files.
7. Run `git diff --check` and require a clean public worktree.

## Deferred work

S3 deliberately does not implement:

- source SHA-256 storage, missing/stale-source refusal, or collision-safe
  proposal ids — S4;
- isolated staging, exact reviewed-path commits, failure rollback, broader
  changed-path policy, or batch transaction guarantees — S5;
- specific user-visible HTMX/Console errors — S6;
- physical sub-folder promotion;
- classifier learning beyond the existing deterministic rule mechanism;
- dashboard cards, drag-and-drop, saved workspace behavior, deployment, or new
  agent skills.

## Completion criteria

S3 is complete when a canonical server resolver is the only classification
destination authority; valid module-general and registered-sub destinations
work; tampered entity/module/sub/block/path values and edited proposal records
fail before unsafe reads or proposal writes; all S1/S2 behavior remains green;
and the read-only private gates prove Grey Matter unchanged.
