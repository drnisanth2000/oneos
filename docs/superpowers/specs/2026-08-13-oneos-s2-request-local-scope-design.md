# OneOS S2 Request-Local Entity Scope Design

**Date:** 2026-08-13

**Status:** Approved for implementation planning

**Authority:** `BUILD.md` Safety Foundation, the private OneOS specification,
and the approved recipient-address routing decision

**Scope:** Safety Foundation S2 only

## Purpose

The current web process owns one mutable `Scope`. Each route changes its
current entity before reading or writing. Two overlapping requests can
therefore replace each other's entity selection and cross the future tenant
boundary.

S2 replaces that mutable process state with an immutable entity scope created
for each request. It validates every selected entity against
`_system/entities.yaml`, makes entity-scoped services derive identity from the
scope, and proves concurrent requests cannot cross entity boundaries.

The approved shared-mailbox requirement is part of the same boundary. Each
entity may declare receiving email addresses in `entities.yaml`. Email intake
matches the message recipient to exactly one registered entity before it may
create a receipt.

S2 builds on S1 commit-on-ingest. It does not authorize S3-S6.

## Approved decisions

- Scoped web routes select their entity from the `{entity}` URL segment.
- No cookie, login session, saved-scope selection, or new workspace UI is
  introduced.
- An unknown route entity returns a plain HTTP 404. S6 owns polished, specific
  Console error rendering.
- The selected scope is immutable for the lifetime of the request.
- Entity-sensitive functions derive the entity from the scope instead of
  accepting a second independent entity value wherever practical.
- A shared mailbox routes email by configured recipient address, never by
  sender, subject, content inference, the first registered entity, or a
  process-global default.
- Recipient-address routing configuration lives under the applicable entity in
  the private `_system/entities.yaml` manifest.
- Learned document type, tag, module, and sub-module classification is deferred.
  It may later use deterministic rules learned from approved corrections, but
  it is not S2 and no LLM enters the request or intake path.

## Fixed constraints

- S1 remains intact: a new intake produces one receipt-only `ingest:` commit;
  duplicate intake is a no-op; raw source content never enters the vault.
- No instance-specific entity, address, path, credential, mailbox, source
  identity, or registry value enters the public repository, tests, commit
  messages, or documentation.
- Runtime identity comes only from private registries and request or adapter
  inputs.
- All entity paths and entity-sensitive queries are bound through `Scope`.
- Global registry data required for the workspace switcher may be read without
  exposing another entity's documents, proposals, database rows, or paths.
- No new dependency, daemon, queue, scheduler, UI screen, or deployment unit is
  introduced.
- S3 continues to own active-module and `sub:` validation, server-derived
  `block`, and complete destination validation.
- S4 continues to own proposal freshness and collision-safe proposal IDs.
- S5 continues to own general Git transaction isolation and Gate 3 changed-path
  policy.
- S6 continues to own safe, specific HTMX/Console error presentation.

## Architecture

### Immutable entity scope

`Scope` becomes an immutable value containing the vault root and one registered
entity slug. `set_current_entity()` is removed. `current_entity()` returns the
bound slug and cannot change during the request.

Construction validates the slug by loading `_system/entities.yaml`. A safe path
segment that is absent from the manifest is still invalid. Validation uses the
manifest, never a directory scan or `index.md`.

Entity path methods no longer trust a separate slug supplied by callers. A
normal entity path resolves below the bound entity directory. A stored
vault-relative path, such as a proposal source, must name the bound entity and
must not resolve outside it. This is the minimum entity-containment rule needed
by S2. It does not validate destination modules, sub-modules, or blocks; S3 adds
those rules before proposal creation.

`system_path()` remains available for shared registries. Code that builds the
workspace switcher uses an unscoped registry/catalog reader rather than an
entity data scope. This preserves the single-user switcher while preventing it
from becoming a way to read another entity's content.

### FastAPI request binding

Every route containing `{entity}` receives `Scope` through a FastAPI
dependency. The dependency:

1. reads the entity slug from the route;
2. loads the runtime manifest;
3. rejects an unregistered slug with HTTP 404; and
4. returns a new immutable scope for that request.

The module-level mutable scope is removed. Unscoped routes such as the shell,
the default triage redirect, and the pulse fragment may read shared catalog
data or render static content, but they may not read entity documents.

### Entity-scoped service interfaces

Entity-sensitive functions consume the bound scope as their single identity
authority. Their target interfaces are:

```python
read_inbox(scope: Scope) -> list[InboxItem]
propose_classification(scope: Scope, item_path: Path, *, ...) -> Proposal
load_proposals(scope: Scope) -> list[Proposal]
preview_diff(scope: Scope, proposal: Proposal) -> str
approve(scope: Scope, proposal_id: str) -> Proposal
reject(scope: Scope, proposal_id: str) -> Proposal
reference_count(scope: Scope, kind: str, slug: str) -> ReferenceReport
propose_delete(scope: Scope, kind: str, slug: str) -> DeleteProposal
execute_delete(scope: Scope, proposal_id: str) -> None
```

Proposal and delete-proposal records must agree with
`scope.current_entity()`. Loading, previewing, approving, rejecting, or
executing a mismatched record fails before another entity path is opened.

Registry reference counting is also entity-scoped. Front matter and `books.db`
queries start at the bound entity root. Shared workspace registry reads count
only records applicable to that entity, so one entity's delete preview does not
reveal another entity's reference totals.

### Rendering boundary

Templates receive the current entity from the bound scope. Entity document
rows, proposal diffs, delete-impact reports, and action URLs all come from that
same request scope.

The workspace switcher may list registered entity labels and links because it
is intentional global navigation in this single-user product. It must not place
another entity's inbox rows, proposal paths, registry values, or database facts
in a scoped response.

## Shared-mailbox recipient routing

### Manifest shape

The public implementation supports this optional private manifest structure:

```yaml
entities:
  synthetic-a:
    label: Synthetic A
    flags: []
    ingest:
      email_addresses:
        - intake-a@example.invalid
```

The example is synthetic. Live addresses remain only in the private manifest.
Entities without configured addresses remain valid for web and folder intake
but cannot receive shared-mailbox email.

The loader validates that each configured address is a non-empty email address
and that no normalized address belongs to more than one entity. Address
comparison is case-insensitive after parsing and trimming. Duplicate ownership
is a configuration error, not a first-match rule.

### Routing sequence

The shared mailbox poll no longer receives a preselected entity. For each
message it:

1. extracts recipient addresses from `Delivered-To`, `X-Original-To`,
   `Envelope-To`, `To`, and `Cc` headers using the standard-library email
   address parser;
2. normalizes and de-duplicates them;
3. matches them against `entities.yaml`;
4. requires the matches to resolve to exactly one entity;
5. creates an immutable validated scope for that entity; and
6. calls the same S1 email receipt commit path with that scope.

If no entity matches, or recipients match more than one entity, the adapter
raises a typed routing error before redacted Markdown is written or committed.
It never guesses, falls back to the first entity, or creates one receipt per
match.

Recipient routing determines only the entity inbox. It does not choose a
module, sub-module, block, tag, document type, or final destination.

Folder intake continues to receive an entity from its configured watcher. It
must validate and bind that entity through the same manifest-backed scope
factory before archiving the raw source or creating a receipt.

## Error model

S2 introduces typed failures for:

- an unknown or malformed entity selection;
- use of a stored record or relative path belonging to another entity;
- missing, malformed, or duplicate email-address routing configuration;
- an email with no configured recipient match; and
- an email whose recipients map to multiple entities.

The HTTP dependency converts only unknown URL entities to a plain 404. Other
typed failures propagate to existing callers. S6 will later map the stable
error taxonomy to safe, specific Command Center responses.

Errors and logs must not include live message bodies, credentials, private
absolute paths, or a dump of the entity manifest.

## Concurrency and test design

All committed tests use synthetic entities, addresses under `example.invalid`,
and temporary Git vaults.

### Scope tests

- construction accepts a registered entity and rejects unknown, malformed, and
  directory-only slugs;
- the bound entity cannot be mutated;
- entity resolution derives from `current_entity()`;
- a stored relative path naming another entity is rejected; and
- shared system registry paths remain available without granting entity data
  access.

### Request tests

Real concurrent requests use two synthetic entities with distinct marker
content. Barriers force their execution to overlap. Tests prove:

- each triage response renders only its own inbox rows;
- each outbox response renders only its own proposals and diffs;
- concurrent proposal requests write into only their own outboxes;
- an approval or rejection request cannot address another entity's proposal;
- registry reference reports contain only the bound entity's counts; and
- an unknown route entity returns 404 without reading an entity directory.

The test must fail against the old mutable process scope by arranging for one
request to switch the shared entity while the other is paused.

### Adapter tests

- a message sent to a configured synthetic address creates one tracked receipt
  under that entity and nowhere else;
- recipient parsing is independent of sender and subject;
- an unmapped recipient creates no receipt and no commit;
- recipients mapping to two entities create no receipt and no commit;
- duplicate address ownership is rejected before polling;
- folder intake rejects an unknown configured entity before moving its source;
  and
- all S1 redaction, idempotency, raw-source restoration, and receipt-only commit
  tests remain green.

### Mutation checks

The tests must detect at least these regressions:

- reintroducing one mutable module-level scope;
- trusting a service-level entity argument instead of the bound scope;
- skipping manifest validation because a directory exists;
- loading a proposal whose record names another entity;
- routing unmatched email to the first entity; and
- allowing one email address to belong to two entities.

## Verification gates

Before S2 is ready for review, run:

```bash
uv run python -m pytest tests/test_scope.py -q
uv run python -m pytest tests/test_app.py tests/test_outbox.py tests/test_registry.py -q
uv run python -m pytest tests/test_folder_adapter.py tests/test_email_adapter.py -q
uv run python -m pytest -q
(cd "$ONEOS_VAULT/_system/scripts" && python3 -m unittest discover -q)
python3 "$ONEOS_VAULT/_system/scripts/check_v2.py" "$ONEOS_VAULT"
python3 "$ONEOS_VAULT/_system/scripts/policy_enforcer.py" \
  --policy "$ONEOS_VAULT/_system/scripts/action-policy.yaml" test-suite
tools/run_gitleaks.sh .
uv run python -m tools.public_repo_audit --repo . --history
uv run python -m tools.public_repo_audit --repo . --vault "$ONEOS_VAULT" --history
```

Private integration commands remain read-only. Capture the live vault status
and binary diff outside both repositories before work and compare identical
snapshots after every private check.

## Review boundary

S2 receives one bounded correctness and safety review after its tests first
pass. Review focuses on request concurrency, entity validation, cross-scope
stored records, shared registry leakage, recipient routing ambiguity, and S1
regressions. Actionable S2 findings receive one TDD fix pass followed by the
complete verification gates.

A finding that requires module/sub/block validation, proposal freshness,
general Git rollback, or Console error presentation is recorded as S3-S6 work
and is not implemented in S2.

## Explicit deferrals

- Cookies, login sessions, saved-scope persistence, and workspace-switcher UI
  changes.
- Learned entity routing from sender, subject, body, attachments, or an LLM.
- Document-type, tag, module, sub-module, and block automation.
- Server-owned destination validation and final proposal policy: S3.
- Proposal freshness and collision-safe identifiers: S4.
- General Git transaction isolation and Gate 3 path policy: S5.
- Polished safe Console/HTMX failure rendering: S6.
