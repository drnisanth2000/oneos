# Short-Identifier Cutover Stage B Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enforce the approved five-character minimum at every entity,
product, member, and workspace boundary after the accepted private cutover,
without changing module, block, flag, submodule, or project identifiers.

**Architecture:** `app.identifiers.meets_floor` remains the only public length
rule. Existing grammar checks stay local. Entity and receipt boundaries consume
the floor directly; `app.registry` validates the three registry axes it reads
or writes; rename validation is axis-aware so `project` remains grammar-only.
The private wizard mirrors the public constant because the public repository
cannot import from the vault.

**Tech Stack:** Python 3.13 test host / Python 3.12 supported runtime, pytest,
PyYAML, unittest, Git.

**Spec:**
`docs/superpowers/specs/2026-08-26-short-identifier-cutover-design.md`
revision 16.

## Global Constraints

- `IDENTIFIER_MINIMUM_LENGTH` is exactly `5`, counting hyphens.
- The floor applies only to entity, product, member, and workspace ids.
- Module, block, flag, submodule, and `project` identifiers keep their current
  grammar and acceptance behavior.
- No dependency, schema, alias, fallback, or product-surface change.
- Stage A migration fixtures retain short source identifiers because they test
  the cutover itself; ordinary application fixtures must satisfy the floor.
- Public work uses synthetic values only. Private identifiers never enter this
  repository, logs, plans, or mutation evidence.
- Every behavior change follows RED → GREEN and gets a mutation proof.
- The two restored pre-cutover vault edits remain byte-identical and outside
  every Stage B commit.

---

### Task 1: Pin the four-axis boundary in public tests

**Files:**
- Create: `tests/test_stage_b_identifier_floor.py`
- Read: `tests/conftest.py`
- Read: `app/entities.py`
- Read: `app/registry.py`
- Read: `app/rename.py`
- Read: `app/action_receipts.py`

**Interfaces:**
- Consumes: `app.identifiers.meets_floor(value: str) -> bool`.
- Produces: behavioral coverage for every public floor consumer and explicit
  non-regression coverage for generic registry vocabulary and `project`.

- [ ] **Step 1: Add entity and receipt RED tests**

  Add tests proving:
  - `EntityCatalog.load` refuses entity key `abcd` and accepts `abcde`;
  - `EntityCatalog.require` refuses selection `abcd` even when a catalog is
    constructed directly;
  - `receipt_relative_path` refuses entity `abcd` and accepts `abcde`;
  - offline receipt-root discovery does not classify a four-character root as
    a canonical entity root.

- [ ] **Step 2: Add product/member/workspace RED tests**

  Use real synthetic registry files and public functions. Prove:
  - `products_for` refuses a product key `abcd`;
  - scoped product/member deletion refuses a sub-floor registry value before
    serializing a changed registry;
  - workspace counting refuses a sub-floor workspace `id` or sub-floor
    entity/product/member reference;
  - `add_workspace` refuses a sub-floor `id` or axis reference before writing.

- [ ] **Step 3: Add rename RED tests and exclusions**

  Prove `plan_rename` and `build_rename_plan` refuse a four-character `new`
  value for entity/product/member/workspace, while a four-character `project`
  value still reaches normal project planning. Prove existing short block,
  flag, and submodule fixtures remain valid.

- [ ] **Step 4: Add the single-source guard**

  Scan `app/**/*.py` with the AST and assert that
  `IDENTIFIER_MINIMUM_LENGTH` is assigned only in `app/identifiers.py`.
  Monkeypatch that module constant to `6` and run representative real boundary
  calls, proving consumers follow the shared value rather than a local `5`.

- [ ] **Step 5: Run the new test file and observe RED**

  Run:

  ```bash
  uv run python -m pytest -q tests/test_stage_b_identifier_floor.py
  ```

  Required: failures must name missing floor refusals. Syntax, fixture, or
  import errors are not evidence.

---

### Task 2: Consume the shared floor at public boundaries

**Files:**
- Modify: `app/entities.py`
- Modify: `app/registry.py`
- Modify: `app/rename.py`
- Modify: `app/action_receipts.py`
- Do not modify: `app/vault.py`
- Do not modify: `app/destinations.py`

**Interfaces:**
- Consumes: `meets_floor` and local grammar checks.
- Produces: fail-closed, domain-typed refusals with no reflected identifier.

- [ ] **Step 1: Enforce entity ids**

  Import `meets_floor` in `app/entities.py`. In both `EntityCatalog.load` and
  `EntityCatalog.require`, require the local `_ENTITY_SLUG` grammar and
  `meets_floor(slug)`. Preserve the existing exception types and generic copy.

- [ ] **Step 2: Enforce receipt entity ids**

  Import `meets_floor` in `app/action_receipts.py`. `_require_entity` requires
  both the local `_ENTITY` grammar and the floor. `_head_canonical_roots`
  includes only roots satisfying both; it does not apply the floor to `_system`
  or arbitrary non-entity roots.

- [ ] **Step 3: Add registry-axis floor helpers**

  In `app/registry.py`, add private helpers that validate only fields typed as
  entity/product/member/workspace. They call `meets_floor` and raise the
  existing `DestinationRegistryError` for malformed stored registries or
  `RegistryError` for an invalid direct request. Messages remain generic and
  never echo the submitted value.

  Apply them to:
  - product keys returned by `products_for`;
  - product/member values read by `_remove_scoped_registry_value`;
  - workspace `id`, `entity`/`primary_entity`, `entities[]`, `product`, and
    `member` fields read by `_count_workspaces`;
  - the same workspace fields before `add_workspace` writes anything.

- [ ] **Step 4: Make rename validation axis-aware**

  Change `_validate_new_slug(new)` to `_validate_new_slug(axis, new)`. Preserve
  grammar and reserved-name checks. Require `meets_floor(new)` only when
  `axis in {"entity", "product", "member", "workspace"}`. Both
  `plan_rename` and `build_rename_plan` pass the axis. `project` remains
  grammar-only.

- [ ] **Step 5: Run focused GREEN tests**

  ```bash
  uv run python -m pytest -q \
    tests/test_stage_b_identifier_floor.py \
    tests/test_scope.py tests/test_registry.py tests/test_rename.py \
    tests/test_action_receipts.py tests/test_vault.py tests/test_destinations.py
  ```

  At this checkpoint, only ordinary synthetic fixtures that still use
  sub-floor ids may fail. Any application-logic failure is a stop.

---

### Task 3: Migrate ordinary public fixtures mechanically

**Files:**
- Modify only the ordinary application tests among:
  `tests/test_app.py`, `tests/test_console_errors.py`,
  `tests/test_console_invariants.py`, `tests/test_console_projection.py`,
  `tests/test_console_readers.py`, `tests/test_console_routes.py`,
  `tests/test_email_adapter.py`, `tests/test_folder_adapter.py`,
  `tests/test_outbox.py`, `tests/test_pr15_boundary_readers.py`,
  `tests/test_pr15_classifier_shapes.py`,
  `tests/test_pr15_delete_proposal_types.py`,
  `tests/test_pr15_sqlite_safety.py`,
  `tests/test_pr15_tamper_not_scope.py`,
  `tests/test_proposal_identity.py`, `tests/test_registry.py`,
  `tests/test_rename.py`, `tests/test_schema.py`, `tests/test_scope.py`,
  `tests/test_triage.py`, and `tests/test_vault.py`.
- Do not mechanically rewrite `tests/test_cutover_*.py`; their short values are
  the source state Stage A exists to migrate.

**Interfaces:**
- Consumes: the validation behavior from Task 2.
- Produces: semantically identical fixtures whose in-scope ids meet the floor.

- [ ] **Step 1: Apply the canonical fixture mapping**

  Apply token-boundary replacements only in the listed ordinary tests:

  ```text
  demo -> demo1
  beta -> beta1
  acme -> acme1
  x (entity key only) -> xxxxx
  gap (entity key/path only) -> gapxx
  oldm -> oldmember
  newm -> newmember
  nn (member id only) -> member-two
  main (workspace id only) -> main1
  ```

  Do not change ordinary English, test names, malformed-input cases, proposal
  ids, former-slug audit fixtures, or Stage A source identifiers.

- [ ] **Step 2: Run the full public suite**

  ```bash
  PYTHONPYCACHEPREFIX="$(mktemp -d)" \
    uv run python -m pytest -q -p no:cacheprovider
  ```

  Classify every remaining failure. A fixture-only sub-floor identifier gets a
  local, typed update. Any required production change outside Task 2 is a hard
  stop under design revision 16.

- [ ] **Step 3: Review fixture churn**

  Confirm `git diff -- app/` names only the four Task 2 modules and
  `app/identifiers.py` remains unchanged. Confirm every test edit is a fixture
  value or the new Stage B regression file.

---

### Task 4: Enforce the private mirror without disturbing preserved edits

**Files (private vault only):**
- Modify: `_system/scripts/oneos_wizard.py`
- Create: `_system/scripts/test_identifier_floor.py`
- Preserve byte-for-byte: the two restored pre-cutover files outside
  `_system/scripts/`.

**Interfaces:**
- Consumes: the approved literal floor `5`; the vault cannot import public app
  code.
- Produces: wizard refusal and a private all-four-registry gate.

- [ ] **Step 1: Capture preservation state**

  Outside both repositories, capture opaque HEAD, porcelain-v2 status,
  worktree diff, cached diff, and SHA-256 for the two restored files. Confirm
  the working state matches the accepted post-cutover state plus exactly those
  edits.

- [ ] **Step 2: Write private RED tests**

  `test_identifier_floor.py` imports `oneos_wizard`, proves `validate_slug`
  rejects `abcd` and accepts `abcde`, and loads the four live registries to
  assert every entity/product/member/workspace id has length at least five.
  It prints no ids or paths.

- [ ] **Step 3: Implement the private wizard mirror**

  Add `IDENTIFIER_MINIMUM_LENGTH = 5` beside `SLUG_RE`. `validate_slug`
  rejects a shorter entity name with generic, non-reflective copy. Do not
  change its grammar or reserved names.

- [ ] **Step 4: Run private GREEN tests and validators**

  ```bash
  cd "$ONEOS_VAULT/_system/scripts"
  python3 -m unittest discover
  cd "$ONEOS_VAULT"
  python3 _system/scripts/check_v2.py .
  ```

  Require the suite to pass and exactly `0 error(s), 0 warning(s)`.

- [ ] **Step 5: Commit only the private mirror files**

  Use an isolated index or explicit path-only commit so the two restored edits
  remain unstaged and uncommitted. Recompare their opaque state byte-for-byte
  after the commit.

---

### Task 5: Mutation, publication, and completion gates

**Files:**
- Create: `docs/superpowers/plans/2026-08-28-short-identifier-stage-b-ledger.md`
- Modify: no production file after evidence capture begins.

**Interfaces:**
- Consumes: committed public implementation and tests.
- Produces: reproducible RED → byte-identical restore → GREEN evidence.

- [ ] **Step 1: Prove the shared-floor control**

  Mutate `meets_floor` to accept every value. Run the exact Stage B node set;
  require RED at entity, product/member/workspace, rename, and receipt nodes.
  Restore `app/identifiers.py` byte-identically and rerun GREEN with a fresh
  `PYTHONPYCACHEPREFIX`.

- [ ] **Step 2: Prove both exclusions**

  Mutate rename so `project` receives the floor and require the project
  non-regression node to RED. Mutate either generic `_REGISTRY_ID` helper to
  consume `meets_floor` and require the generic-vocabulary node to RED. Restore
  each preimage byte-identically.

- [ ] **Step 3: Run public gates**

  ```bash
  uv run python -m pytest -q
  uv run python tools/public_repo_audit.py --repo .
  uv run python tools/public_repo_audit.py --repo . --history
  bash tools/run_gitleaks.sh
  git diff --check
  git status --porcelain --untracked-files=all
  ```

- [ ] **Step 4: Run trusted-local gates**

  With `ONEOS_VAULT` set only at the trusted boundary, run the combined
  repo-plus-vault current/history audits and the private suite/checker. Compare
  the restored pre-cutover edits byte-for-byte before and after.

- [ ] **Step 5: Request independent review before integration**

  Push only after every gate is green, open a pull request, wait for CI and
  CodeRabbit, resolve attributable findings with new RED tests, and merge only
  after owner approval. A fresh merged-`origin/main` baseline is the completion
  proof.
