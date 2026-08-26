# Inherited Item 3 — Route Failure Contracts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove that every known domain failure exported by a Console route's body services or FastAPI dependencies has an explicit route, typed-handler, or deliberate-`E-UNKNOWN` disposition.

**Architecture:** Add immutable, runtime-neutral failure metadata in `app/console_routing.py`, attach it to the closed inventory of route-facing service/dependency boundaries, and extend `ConsoleRoute` with the body services each endpoint uses. Structural tests traverse the contracts and actual FastAPI dependency graph; real filesystem tests remain the independent check that metadata matches behavior.

**Tech Stack:** Python 3.12 dataclasses and typing, FastAPI `APIRoute.dependant` metadata, Python AST/`inspect`, pytest.

**Spec:** `docs/superpowers/specs/2026-08-26-inherited-safety-items-2-4-design.md`

## Global Constraints

- Start only after Item 4 is merged and a freshly fetched `origin/main` full suite passes.
- Use a fresh task, worktree, and `codex/` branch for Item 3 only.
- Public repository and synthetic fixtures only; no live vault or private values.
- Metadata must not catch, translate, render, suppress, or otherwise alter exceptions at runtime.
- The global `Exception` handler never satisfies a known domain contract.
- A typed application handler cannot satisfy a body-service contract; body failures remain route-owned.
- Add no dependency, taxonomy code, operator copy, schema, registry value, or feature surface.
- Do not push, open a pull request, merge, delete a branch, remove a worktree, or run private gates without separate authorization.
- Stop if the inventory below differs from the freshly merged code, if a new product outcome is required, or on any dependency, convention, schema, security-boundary, destructive, deployment, or private-material change.
- Independent review and mutation RED→GREEN evidence are mandatory.

## Execution Preconditions

```bash
git fetch origin
BASE_SHA="$(git rev-parse origin/main)"
WORKTREE="$(dirname "$(git rev-parse --show-toplevel)")/oneos-inherited-item-3"
git worktree add "$WORKTREE" -b codex/inherited-item-3-failure-contracts "$BASE_SHA"
cd "$WORKTREE"
test "$(git rev-parse HEAD)" = "$BASE_SHA"
test -z "$(git status --porcelain)"
uv run python -m pytest -q
```

The baseline must be green and contain the merged Item 2 and Item 4 status records.

## Closed Initial Inventory

Before editing, verify these route-facing boundaries still exist. If any is absent, renamed, or has materially different callers, stop and report the mismatch rather than silently changing the plan:

```text
app.config.vault_root
app.config.build_scope
app.main.entity_scope
app.entities.EntityCatalog.load
app.vault.Vault.bundles
app.inbox.read_inbox
app.destinations.resolve_classification_destination
app.outbox.propose_classification
app.outbox.preview_diff
app.outbox.project_outbox
app.outbox.approve
app.outbox.reject
app.outbox.pending_proposal_entry_exists
app.registry.products_for
app.registry.propose_delete
app.registry.get_delete_receipt_or_review
app.registry.execute_delete
app.action_receipts.resolve_head_receipt
app.proposal_identity.require_proposal_id
app.review_tokens.require_review_sha256
```

The inventory is intentionally limited to route-facing boundaries that export known domain outcomes. Pure formatters, template calls, `datetime`, `secrets`, and unforeseen programmer defects remain outside the finite contract.

### Task 1: Build and validate the metadata primitives

**Files:**
- Modify: `app/console_routing.py:1-50`
- Modify: `tests/test_console_render.py`

**Interfaces:**
- Produces: `DeliberateUnknown`, `FailureContract`, `failure_contract`, and `ConsoleRoute.services`.
- Consumes later: service decorators and structural completeness traversal.

- [ ] **Step 1: Write RED declaration tests**

Cover immutable construction, valid decoration, and rejection of `Exception`, `BaseException`, non-exception values, duplicate classes, the same class in `raises` and `deliberate_unknown`, an empty reason, an uncontracted `calls` target, and a non-callable route service.

Use these frozen data shapes in the tests:

```python
@dataclass(frozen=True)
class DeliberateUnknown:
    exception: type[BaseException]
    reason: str


@dataclass(frozen=True)
class FailureContract:
    raises: tuple[type[BaseException], ...]
    calls: tuple[Callable[..., object], ...]
    deliberate_unknown: tuple[DeliberateUnknown, ...]
```

The decorator's exact signature is
`failure_contract(*, raises=(), calls=(), deliberate_unknown=()) -> Callable[[_Decorated], _Decorated]`, with the tuple element types shown by `FailureContract` above.

Extend `ConsoleRoute` and `console_route` with:

```python
services: tuple[Callable[..., object], ...] = ()
```

Every service passed to `console_route` must already carry a genuine `FailureContract` in `__failure_contract__`.

- [ ] **Step 2: Confirm RED**

```bash
uv run python -m pytest -q tests/test_console_render.py
```

Expected: imports or new assertions fail because the metadata does not exist.

- [ ] **Step 3: Implement pure validation**

Keep `app/console_routing.py` free of taxonomy, route-list, FastAPI, and domain imports. The decorators attach frozen metadata only. They do not wrap the callable.

- [ ] **Step 4: Run GREEN and commit**

```bash
uv run python -m pytest -q tests/test_console_render.py tests/test_console_invariants.py
git add app/console_routing.py tests/test_console_render.py
git commit -m "feat: add pure failure contract metadata"
```

### Task 2: Attach contracts to the closed service/dependency inventory

**Files:**
- Modify: `app/config.py`
- Modify: `app/entities.py`
- Modify: `app/vault.py`
- Modify: `app/inbox.py`
- Modify: `app/destinations.py`
- Modify: `app/outbox.py`
- Modify: `app/registry.py`
- Modify: `app/action_receipts.py`
- Modify: `app/proposal_identity.py`
- Modify: `app/review_tokens.py`
- Modify: `app/main.py`
- Modify: `tests/test_console_invariants.py`

**Interfaces:**
- Consumes: `@failure_contract` from Task 1 and the Item 4 `VaultRootUnavailable`/`EntityManifestError` behavior.
- Produces: one exact metadata object on every inventoried boundary.

- [ ] **Step 1: Add a transcribed inventory test before decorators**

Create `EXPECTED_CONTRACTED_BOUNDARIES` in `tests/test_console_invariants.py` as module/qualified-name pairs matching the closed inventory above. Resolve each object and assert it carries `FailureContract`. Add a floor equal to the inventory length and assert no duplicate qualified names.

- [ ] **Step 2: Confirm RED**

Run the named inventory test. Expected: every boundary is reported missing metadata.

- [ ] **Step 3: Attach exact exported-family contracts**

Use the narrowest existing public domain classes. The initial families are:

```text
vault_root:
  raises = VaultRootUnavailable
build_scope:
  raises = EntityManifestError, SystemRegistryPathError, EntitySelectionError
  calls = vault_root
EntityCatalog.load:
  raises = EntityManifestError, SystemRegistryPathError, RecipientConfigurationError
Vault.bundles:
  raises = DestinationRegistryError, EntityManifestError, SystemRegistryPathError
read_inbox:
  raises = RedirectedPathError
resolve_classification_destination:
  raises = DestinationError, CrossScopeError, DestinationRegistryError,
           EntityManifestError, SystemRegistryPathError
propose_classification:
  raises = OutboxError
  calls = resolve_classification_destination
preview_diff:
  raises = OutboxError, CrossScopeError, DestinationRegistryError,
           EntityManifestError, SystemRegistryPathError
project_outbox:
  raises = OutboxError, CrossScopeError, DestinationRegistryError,
           EntityManifestError, SystemRegistryPathError,
           InvalidActionReceipt, ReceiptStoreIntegrityError,
           ReceiptStoreUnavailable
approve / reject:
  raises = OutboxError, CrossScopeError, DestinationRegistryError,
           EntityManifestError, SystemRegistryPathError, ReviewTokenError,
           InvalidActionReceipt, ReceiptStoreIntegrityError,
           ReceiptStoreUnavailable
pending_proposal_entry_exists:
  raises = CrossScopeError
products_for / propose_delete:
  raises = RegistryError, CrossScopeError, DestinationRegistryError
get_delete_receipt_or_review:
  raises = RegistryError, CrossScopeError, DestinationRegistryError,
           UnreadableProposalRecord, InvalidActionReceipt,
           ReceiptStoreIntegrityError, ReceiptStoreUnavailable
execute_delete:
  raises = RegistryError, CrossScopeError, DestinationRegistryError,
           UnreadableProposalRecord, ReviewTokenError,
           InvalidActionReceipt, ReceiptStoreIntegrityError,
           ReceiptStoreUnavailable
resolve_head_receipt:
  raises = InvalidActionReceipt, ReceiptStoreIntegrityError,
           ReceiptStoreUnavailable
require_proposal_id:
  raises = ProposalIdentityError
require_review_sha256:
  raises = InvalidReviewToken, ReviewContractViolation
```

If executable characterization proves one listed family cannot cross that boundary or reveals another known domain family, stop and return the exact test/probe to the trusted reviewer. Do not silently broaden or narrow this approved inventory.

- [ ] **Step 4: Bind contract edges to executable calls**

Decorate `app.main.entity_scope` in this task with `calls=(build_scope,)`. Keep the genuine `build_scope -> vault_root` and `propose_classification -> resolve_classification_destination` edges too.

In `tests/test_console_invariants.py`, strip docstrings and inspect each
inventoried boundary's AST. Resolve calls through the relevant executable
context rather than accepting only `ast.Name`: direct module-global calls,
attribute calls such as `EntityCatalog.load`, and bound calls such as
`vault.bundles()` where the receiver's type can be established from the
function body or its enclosing module. This is a closed-inventory resolver,
not a claim to infer arbitrary dynamic Python dispatch. Assert both directions:

1. every contracted executable call is named in `FailureContract.calls`; and
2. every named call edge appears in executable code.

Reject a planted cycle in a synthetic contract graph. Comments or docstrings mentioning a function must not satisfy the executable-call check.

- [ ] **Step 5: Run the inventory and domain-focused suites**

```bash
uv run python -m pytest -q \
  tests/test_console_invariants.py tests/test_app.py tests/test_vault.py \
  tests/test_triage.py tests/test_outbox.py tests/test_registry.py
```

- [ ] **Step 6: Commit the service contracts**

```bash
git add app/config.py app/entities.py app/vault.py app/inbox.py \
  app/destinations.py app/outbox.py app/registry.py app/action_receipts.py \
  app/proposal_identity.py app/review_tokens.py app/main.py \
  tests/test_console_invariants.py
git commit -m "feat: declare route-facing service failures"
```

### Task 3: Bind registered routes and actual dependencies to contracts

**Files:**
- Modify: `app/main.py`
- Modify: `tests/test_console_invariants.py`
- Modify: `tests/test_console_routes.py`

**Interfaces:**
- Consumes: contracted service functions and `APIRoute.dependant`.
- Produces: transitive completeness invariant for every registered Console endpoint.

- [ ] **Step 1: Add RED route-service inventory**

Extend the registered-endpoint sweep so every route in `_route_totality_plan(main)` has an exact `services` tuple. Use this map as the starting point:

```text
shell: Vault.bundles
pulse: none
triage_default: Vault.bundles
triage: read_inbox, resolve_classification_destination, Vault.bundles
propose: propose_classification, preview_diff
outbox_screen: project_outbox, Vault.bundles
outbox_approve: approve, project_outbox, Vault.bundles,
                resolve_head_receipt, pending_proposal_entry_exists
outbox_reject: reject, project_outbox, Vault.bundles,
               resolve_head_receipt, pending_proposal_entry_exists
outbox_review_fragment: project_outbox, Vault.bundles
registry_products: products_for, Vault.bundles
registry_delete_preview: propose_delete, get_delete_receipt_or_review
registry_delete_execute: execute_delete, get_delete_receipt_or_review,
                         resolve_head_receipt
registry_delete_review_fragment: get_delete_receipt_or_review
```

The test enumerates registered routes first and fails on a missing or extra
endpoint; it never trusts only the map. Apply the same executable-call resolver
used for contract edges to each route body. Every service call resolved from
the executable body must appear in that route's `services` tuple, and every
declared service must be called by that body. Comments, docstrings, logging, or
a reference outside the acting call cannot satisfy the check.

- [ ] **Step 2: Add RED dependency introspection**

For every registered `APIRoute`, walk `route.dependant.dependencies` recursively. Any application dependency must carry `FailureContract`. Assert all `EntityScope` routes discover `main.entity_scope`; do not transcribe those route names separately.

- [ ] **Step 3: Add the completeness algorithm**

For each route:

1. Traverse `ConsoleRoute.services` contracts and their `calls` graph.
2. Traverse actual FastAPI dependency contracts separately.
3. For each body exception, require `issubclass(exported, caught)` for at least one route catch, or an exact `DeliberateUnknown` entry.
4. For each dependency exception, require a registered typed handler whose class catches it, excluding `Exception`, or an exact `DeliberateUnknown` entry.
5. Fail if one exception is both handled and deliberate-unknown.
6. Include route name, boundary name, and exception class—but no exception text—in every diagnostic.

- [ ] **Step 4: Confirm RED on current missing coverage**

Run the new structural nodes. Any real missing route catch revealed by the approved contracts must fail by name. Do not weaken a contract to make the test pass.

- [ ] **Step 5: Add only the missing lower catches**

Update the applicable existing named catch tuple (`_TRIAGE_CATCHES`, `_PROPOSE_CATCHES`, `_OUTBOX_CATCHES`, `_REGISTRY_PRODUCTS_CATCHES`, or `_REGISTRY_DELETE_CATCHES`) used by both decorator and body `except`. Do not add a new `except`, broad base, or catch-all. Route-visible behavior should remain the same because Item 4's typed handler already rendered these outcomes; this task restores truthful lower ownership.

- [ ] **Step 6: Run GREEN and the real-filesystem routes**

```bash
uv run python -m pytest -q \
  tests/test_console_invariants.py \
  tests/test_console_routes.py::test_route_totality_from_declared_catches \
  tests/test_console_routes.py::test_route_tuples_still_answer_the_leaf_redirect_without_the_dependency_handler
```

- [ ] **Step 7: Commit route completeness**

```bash
git add app/main.py tests/test_console_invariants.py tests/test_console_routes.py
git commit -m "test: enforce complete route failure contracts"
```

### Task 4: Mutation campaign and truthful status

**Files:**
- Modify: `docs/STATUS.md:217-233`
- Verify: all Item 3 files.

**Interfaces:**
- Produces: seven independently reproducible mutation results and public-complete status.

- [ ] **Step 1: Run the seven approved mutations independently**

For each, save a pre-image outside the repo, modify one target, run one exact node with a unique diagnostic, restore, verify `cmp`, and rerun GREEN:

1. Remove a known exported class from a route catch; completeness must name the route/service/class.
2. Remove `@failure_contract` from `entity_scope`; dependency inventory must name it.
3. Remove `build_scope` from `entity_scope`'s `calls`; the call-edge invariant must fail.
4. Add `PermissionError` to `EntityCatalog.load`'s exported contract without a disposition; completeness must fail.
5. Keep the Item 4 `EntityManifestError` application handler but remove the narrower body catch; lower-ownership test must fail.
6. Replace an exact deliberate-unknown entry in a synthetic declaration test with `Exception` or an empty reason; declaration-time validation must fail.
7. For every route/service entry in the approved inventory, independently
   remove that service from the route's `services` declaration without changing
   the executable body. The route/body binding invariant must fail with the
   exact route and missing service name. The sweep must exercise direct,
   attribute, and bound-call shapes, so no one resolver class passes vacuously.

- [ ] **Step 2: Retain real-filesystem evidence**

Run the Item 4 root-loss, manifest-permission, whole-system redirect, missing-manifest, and leaf-redirect tests. The metadata suite does not replace them.

- [ ] **Step 3: Update Item 3 status**

Change its heading to:

```markdown
**3. Declaration completeness — PUBLIC IMPLEMENTATION COMPLETE.**
```

State that the proof covers the closed known-domain service/dependency inventory, not every possible Python exception, and that trusted-local gates remain outstanding.

- [ ] **Step 4: Run the full public suite**

```bash
uv run python -m pytest -q
```

Expected: PASS.

- [ ] **Step 5: Commit the status record**

```bash
git add docs/STATUS.md
git commit -m "docs: record public route failure contracts"
```

- [ ] **Step 6: Run publication gates on the committed tree**

```bash
uv run python -m tools.public_repo_audit --repo . --history
tools/run_gitleaks.sh .
git diff --check
git status --porcelain
```

Expected: both audits clean; diff check clean; final status empty.

## External-Agent Handoff

Return the base SHA, branch/worktree, verified closed inventory, any approved contract mismatch ruling, commits, focused RED/GREEN output, six mutation results, real-filesystem selection, full public count, audits, diff check, and clean status. State explicitly that the live vault and private gates were not accessed.

The trusted local reviewer independently checks every contract against executable callers, reruns all public and mutation evidence, performs the 37 private tests, `check_v2`, combined vault-seeded history audit, and opaque preservation comparison, and only then advises the product owner on push/PR/merge.
