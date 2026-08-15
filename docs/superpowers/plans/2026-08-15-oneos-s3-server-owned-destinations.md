# OneOS Safety Foundation S3 — Server-Owned Destinations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make one server-side resolver the only authority for classification module, sub-module, block, and destination path, rejecting tampered or non-canonical values before proposal writes or unsafe reads.

**Architecture:** `Vault` remains the runtime registry owner and exposes scope-bound active module/sub queries. A new pure `destinations.py` service validates the receipt leaf, registries, lifecycle directories, derived block, and final scoped path, returning an immutable canonical destination. Outbox creation and stored-proposal loading both invoke that resolver; the HTTP/UI layer displays canonical results but never becomes an authority.

**Tech Stack:** Python 3.12+, FastAPI forms, Jinja2, Pydantic-era typed Python, PyYAML, pathlib, pytest, Git CLI through existing helpers; no new dependency or build step.

**Spec:** `docs/superpowers/specs/2026-08-15-oneos-s3-server-owned-destinations-design.md`

## Global Constraints

- Implement Safety Foundation S3 only. Do not implement S4 proposal hashes/ids, S5 Git transaction isolation, or S6 user-visible error presentation.
- No instance-specific entity, person, product, credential, or vault-path value may enter this public repository; fixtures use invented values only.
- Entity identity comes only from immutable request-local `Scope`; service interfaces accept no separate entity slug.
- Entity discovery is `_system/entities.yaml`; module, block, and sub ids are `_system/archetypes.yaml`; flags alone activate modules/subs at read time.
- Empty submitted sub is canonical module-general `None`; proposal YAML stores `sub: null`; approval removes `sub:` instead of writing an empty value.
- Non-empty subs must belong to the selected module and satisfy their optional flag.
- Block is derived server-side. A client or stored block may only be checked against the derived value.
- Never scaffold modules, lifecycle directories, or sub-folders. Missing/redirected paths are errors.
- Curated content changes remain outbox-only; no LLM enters the request path.
- Every production change follows strict RED-GREEN-REFACTOR TDD. Record the expected RED failure before implementation.
- Preserve Grey Matter byte-for-byte. Private gates are read-only and run only after before/after Git status and binary-diff snapshots are established.
- Baseline at S3 branch point: 233 public tests passing at S2 commit `5990f6f`.
- Do not push, merge, open a PR, or implement beyond this plan.

---

## File Map

- Create `app/destinations.py`: immutable canonical destination, typed destination errors, and the only classification destination resolver.
- Create `tests/test_destinations.py`: focused registry/path/leaf/flag/symlink resolver behavior.
- Modify `app/vault.py`: strict scope-bound active module/sub queries and strict block lookup; no path assembly.
- Modify `app/outbox.py`: resolve before proposal write, represent optional sub, remove `sub:` for module-general content, and revalidate stored proposals.
- Modify `app/main.py`: validate classifier recommendations for display, reject an entity form claim, reject path normalization, and pass only raw choices into the trusted service.
- Modify `templates/triage.html`: render canonical destination values and stop submitting block as an authority.
- Modify `tests/test_vault.py`: registry query contract and malformed-registry denial.
- Modify `tests/test_outbox.py`: registry-faithful fixtures, creation behavior, module-general behavior, and tampered stored records.
- Modify `tests/test_app.py`: registry-faithful route fixtures, invalid recommendation rendering, and tampered form no-write behavior.
- Preserve `tests/test_triage.py` unchanged as a focused classifier/read-inbox regression; destination policy stays outside the classifier.

---

### Task 1: Scope-Bound Runtime Destination Registry Queries

**Files:**
- Modify: `app/vault.py`
- Modify: `tests/test_vault.py`

**Interfaces:**
- Consumes: `Scope.root`, `Scope.current_entity()`, `EntityCatalog.require()`, `Vault.resolve_flags(None, flags)`, and `Vault.active_modules(active_flags)`.
- Produces:
  - `class DestinationRegistryError(ValueError)`
  - `Vault.active_modules_for(scope: Scope) -> frozenset[str]`
  - `Vault.active_submodules_for(scope: Scope, module: str) -> frozenset[str]`
  - `Vault.require_block(module: str) -> str`
  - `Vault.module_spec(module: str) -> dict`
- Later tasks must use these methods; they must not inspect `Vault._archetypes` directly.

- [ ] **Step 1: Add failing tests for flag-only module activation**

Add a synthetic archetypes registry to `tests/test_vault.py` with one conditional module and one sub requiring the same invented flag. Add:

```python
def test_destination_registry_uses_bound_entity_flags_only(tmp_path):
    root = write_vault(
        tmp_path,
        'version: "1.0"\nentities:\n  plain: {label: Plain, flags: []}\n'
        '  enabled: {label: Enabled, flags: [special]}\n',
        DESTINATION_ARCHETYPES,
    )
    vault = Vault(EntityCatalog.load(root))

    plain = Scope(root, "plain")
    enabled = Scope(root, "enabled")

    assert "zz-extra" not in vault.active_modules_for(plain)
    assert "zz-extra" in vault.active_modules_for(enabled)
    assert vault.active_submodules_for(plain, "02-work") == frozenset({"general"})
    assert vault.active_submodules_for(enabled, "02-work") == frozenset(
        {"general", "specialized"}
    )
```

The entity records may carry an `archetype:` value in a second regression, but it must not activate anything unless the corresponding flag is explicitly in `flags:`.

- [ ] **Step 2: Run the exact tests and verify RED**

Run:

```bash
UV_CACHE_DIR=/private/tmp/oneos-s3-uv-cache uv run python -m pytest \
  tests/test_vault.py::test_destination_registry_uses_bound_entity_flags_only -q
```

Expected: FAIL because `active_modules_for` is absent.

- [ ] **Step 3: Add failing malformed-registry and strict block tests**

Add tests with literal malformed YAML structures:

```python
def test_destination_registry_rejects_wrong_submodule_shape(tmp_path):
    root = write_vault(
        tmp_path,
        'version: "1.0"\nentities:\n  alpha: {label: Alpha, flags: []}\n',
        'version: "2.0"\nflags: {}\nmodules:\n  01-core: {block: govern}\n'
        'submodules:\n  01-core: [not-a-mapping]\n',
    )
    vault = Vault(EntityCatalog.load(root))
    with pytest.raises(DestinationRegistryError):
        vault.active_submodules_for(Scope(root, "alpha"), "01-core")


def test_require_block_rejects_unknown_or_empty_mapping(tmp_path):
    root = write_vault(
        tmp_path,
        'version: "1.0"\nentities:\n  alpha: {label: Alpha, flags: []}\n',
        'version: "2.0"\nflags: {}\nmodules:\n  01-core: {}\n',
    )
    vault = Vault(EntityCatalog.load(root))
    with pytest.raises(DestinationRegistryError):
        vault.require_block("01-core")
    with pytest.raises(DestinationRegistryError):
        vault.require_block("missing")


def test_module_spec_returns_copy_of_strict_mapping(tmp_path):
    root = write_vault(
        tmp_path,
        'version: "1.0"\nentities:\n  alpha: {label: Alpha, flags: []}\n',
        'version: "2.0"\nflags: {}\nmodules:\n'
        '  01-core: {block: govern, lifecycle_pattern: false}\n',
    )
    vault = Vault(EntityCatalog.load(root))
    spec = vault.module_spec("01-core")
    assert spec == {"block": "govern", "lifecycle_pattern": False}
    spec["block"] = "changed"
    assert vault.require_block("01-core") == "govern"
```

- [ ] **Step 4: Implement the minimal strict registry API**

In `app/vault.py`, import `Scope` and add:

```python
class DestinationRegistryError(ValueError):
    pass


def _entity_flags(self, scope: Scope) -> set[str]:
    if scope.root != self.root:
        raise DestinationRegistryError("scope and registry roots differ")
    entity = self._catalog.require(scope.current_entity())
    return self.resolve_flags(None, list(entity.flags))


def active_modules_for(self, scope: Scope) -> frozenset[str]:
    return frozenset(self.active_modules(self._entity_flags(scope)))


def active_submodules_for(self, scope: Scope, module: str) -> frozenset[str]:
    groups = self._archetypes.get("submodules") or {}
    if not isinstance(groups, dict):
        raise DestinationRegistryError("submodules registry must be a mapping")
    entries = groups.get(module) or {}
    if not isinstance(entries, dict):
        raise DestinationRegistryError("module submodules must be a mapping")
    flags = self._entity_flags(scope)
    active: set[str] = set()
    for sub, raw in entries.items():
        if not isinstance(sub, str) or not isinstance(raw, dict):
            raise DestinationRegistryError("submodule entry is malformed")
        required = raw.get("flag")
        if required is not None and not isinstance(required, str):
            raise DestinationRegistryError("submodule flag must be a string")
        if required is None or required in flags:
            active.add(sub)
    return frozenset(active)


def require_block(self, module: str) -> str:
    spec = self.module_spec(module)
    block = spec.get("block")
    if not isinstance(block, str) or not block:
        raise DestinationRegistryError("destination module has no block")
    return block


def module_spec(self, module: str) -> dict:
    modules = self._archetypes.get("modules")
    if not isinstance(modules, dict) or module not in modules:
        raise DestinationRegistryError("destination module is not declared")
    spec = modules[module]
    if not isinstance(spec, dict):
        raise DestinationRegistryError("module registry entry is malformed")
    return dict(spec)
```

Place `_entity_flags`, `active_modules_for`, `active_submodules_for`, and `require_block` as `Vault` methods. Do not change the legacy `block_of()` behavior yet; the resolver will use only `require_block()`.

- [ ] **Step 5: Run focused and vault regression tests GREEN**

Run:

```bash
UV_CACHE_DIR=/private/tmp/oneos-s3-uv-cache uv run python -m pytest tests/test_vault.py -q
UV_CACHE_DIR=/private/tmp/oneos-s3-uv-cache uv run python -m pytest tests/test_scope.py tests/test_triage.py -q
git diff --check
```

Expected: all pass; no whitespace errors.

- [ ] **Step 6: Commit Task 1**

```bash
git add app/vault.py tests/test_vault.py
git commit -m "feat: expose scoped destination registries"
```

---

### Task 2: Canonical Classification Destination Resolver

**Files:**
- Create: `app/destinations.py`
- Create: `tests/test_destinations.py`

**Interfaces:**
- Consumes: Task 1's `Vault.active_modules_for`, `active_submodules_for`, and `require_block`; `Scope.resolve`, `resolve_stored`, and `vault_relative`.
- Produces:
  - `class DestinationError(ValueError)` and focused subclasses `InvalidSourceLeaf`, `InvalidModule`, `InvalidSub`, `BlockMismatch`, `UnsafeDestinationPath`.
  - Immutable `ClassificationDestination(entity: str, module: str, sub: str | None, block: str, src: str, dst: str, path: Path)`.
  - `resolve_classification_destination(scope: Scope, item_path: Path | str, *, module: object, sub: object, claimed_block: object | None = None, require_source: bool = True) -> ClassificationDestination`.
- No function in this file writes to disk.

- [ ] **Step 1: Create complete synthetic resolver fixtures and valid-path tests**

Create `tests/test_destinations.py` with a literal registry containing `00-inbox`, unconditional `11-library`, conditional `zz-extra`, registered subs, and invented flags. Build entity folders explicitly; never read Grey Matter.

```python
def test_resolver_derives_canonical_registered_sub_destination(destination_vault):
    scope = Scope(destination_vault, "alpha")
    item = scope.resolve("00-inbox", "active", "receipt.md")

    result = resolve_classification_destination(
        scope, item, module="11-library", sub="reference", claimed_block="govern"
    )

    assert result == ClassificationDestination(
        entity="alpha",
        module="11-library",
        sub="reference",
        block="govern",
        src="alpha/00-inbox/active/receipt.md",
        dst="alpha/11-library/active/receipt.md",
        path=destination_vault / "alpha/11-library/active/receipt.md",
    )


def test_resolver_allows_module_general_destination(destination_vault):
    scope = Scope(destination_vault, "alpha")
    result = resolve_classification_destination(
        scope,
        scope.resolve("00-inbox", "active", "receipt.md"),
        module="11-library",
        sub="",
    )
    assert result.sub is None
    assert result.block == "govern"
```

- [ ] **Step 2: Run valid resolver tests and verify RED**

Run:

```bash
UV_CACHE_DIR=/private/tmp/oneos-s3-uv-cache uv run python -m pytest \
  tests/test_destinations.py::test_resolver_derives_canonical_registered_sub_destination \
  tests/test_destinations.py::test_resolver_allows_module_general_destination -q
```

Expected: collection FAIL because `app.destinations` does not exist.

- [ ] **Step 3: Add table-driven invalid taxonomy tests**

Use literal inputs and expected exception types:

```python
@pytest.mark.parametrize(
    ("module", "sub", "error"),
    [
        ("missing", "reference", InvalidModule),
        ("zz-extra", "specialized", InvalidModule),
        ("11-library", "missing", InvalidSub),
        ("11-library", "from-other-module", InvalidSub),
        ("11-library", " reference", InvalidSub),
        ("11-library", "reference\nstatus: approved", InvalidSub),
    ],
)
def test_resolver_rejects_noncanonical_taxonomy(
    destination_vault, module, sub, error
):
    scope = Scope(destination_vault, "alpha")
    with pytest.raises(error):
        resolve_classification_destination(
            scope,
            scope.resolve("00-inbox", "active", "receipt.md"),
            module=module,
            sub=sub,
        )
```

Add a separate enabled-entity assertion proving the conditional module/sub succeeds only when `special` is explicitly in `flags:`.

- [ ] **Step 4: Add leaf, block, lifecycle, and symlink safety tests**

Cover these literal cases:

```python
@pytest.mark.parametrize(
    "leaf",
    ["../receipt.md", "nested/receipt.md", r"..\receipt.md", "/receipt.md",
     ".", "..", "receipt.txt", " receipt.md", "receipt.md\n"],
)
def test_resolver_rejects_noncanonical_source_leaf(destination_vault, leaf):
    scope = Scope(destination_vault, "alpha")
    with pytest.raises(InvalidSourceLeaf):
        resolve_classification_destination(
            scope,
            scope.resolve("00-inbox", "active") / leaf,
            module="11-library",
            sub="reference",
        )


def test_resolver_rejects_forged_block(destination_vault):
    scope = Scope(destination_vault, "alpha")
    with pytest.raises(BlockMismatch):
        resolve_classification_destination(
            scope,
            scope.resolve("00-inbox", "active", "receipt.md"),
            module="11-library",
            sub="reference",
            claimed_block="growth",
        )
```

Also add independent tests for:

- declared active module absent from disk;
- module path as a symlink;
- `active/` absent, a file, or a symlink;
- an existing destination leaf symlink;
- a module with `lifecycle_pattern: false`; and
- `require_source=False` accepting a missing canonical source path while still rejecting a malformed source path.

Each failure test snapshots the entity tree before the call and asserts no directory or file was created.

- [ ] **Step 5: Implement the minimal resolver**

Create `app/destinations.py` with the exact result and error classes. The function must:

```python
def resolve_classification_destination(
    scope: Scope,
    item_path: Path | str,
    *,
    module: object,
    sub: object,
    claimed_block: object | None = None,
    require_source: bool = True,
) -> ClassificationDestination:
    catalog = EntityCatalog.load(scope.root)
    vault = Vault(catalog)
    entity = scope.current_entity()
    catalog.require(entity)

    source = Path(item_path)
    leaf = source.name
    _require_markdown_leaf(leaf)
    expected_source = scope.resolve("00-inbox", "active", leaf)
    if source.resolve() != expected_source:
        raise InvalidSourceLeaf("source is not the canonical inbox receipt")
    if require_source and (not expected_source.is_file() or expected_source.is_symlink()):
        raise InvalidSourceLeaf("source receipt is missing or redirected")

    if not isinstance(module, str) or module != module.strip():
        raise InvalidModule("destination module is non-canonical")
    if module not in vault.active_modules_for(scope):
        raise InvalidModule("destination module is not active")

    canonical_sub: str | None
    if sub is None or sub == "":
        canonical_sub = None
    elif not isinstance(sub, str) or sub != sub.strip():
        raise InvalidSub("destination sub is non-canonical")
    elif sub not in vault.active_submodules_for(scope, module):
        raise InvalidSub("destination sub is not active for this module")
    else:
        canonical_sub = sub

    block = vault.require_block(module)
    if claimed_block is not None and claimed_block != block:
        raise BlockMismatch("claimed block does not match destination module")

    module_dir = _require_real_directory(scope, module)
    module_spec = vault.module_spec(module)
    if module_spec.get("lifecycle_pattern", True) is False:
        raise InvalidModule("destination module has no active lifecycle")
    active_dir = _require_real_directory(scope, module, "active")
    if active_dir.parent != module_dir:
        raise UnsafeDestinationPath("active lifecycle directory is redirected")

    destination = scope.resolve(module, "active", leaf)
    if destination.parent != active_dir or destination.is_symlink():
        raise UnsafeDestinationPath("destination is not canonical")

    return ClassificationDestination(
        entity=entity,
        module=module,
        sub=canonical_sub,
        block=block,
        src=scope.vault_relative(expected_source),
        dst=scope.vault_relative(destination),
        path=destination,
    )
```

Use Task 1's strict copy-returning `Vault.module_spec()` for the lifecycle flag. `_require_markdown_leaf` explicitly rejects `/`, `\\`, CR/LF, whitespace changes, dot names, and suffixes other than `.md`. `_require_real_directory` compares the resolved path with the lexical `scope.root / entity / parts` path and requires `is_dir()`.

- [ ] **Step 6: Run resolver and neighboring tests GREEN**

Run:

```bash
UV_CACHE_DIR=/private/tmp/oneos-s3-uv-cache uv run python -m pytest tests/test_destinations.py -q
UV_CACHE_DIR=/private/tmp/oneos-s3-uv-cache uv run python -m pytest tests/test_vault.py tests/test_scope.py -q
git diff --check
```

Expected: all pass.

- [ ] **Step 7: Commit Task 2**

```bash
git add app/destinations.py app/vault.py tests/test_destinations.py tests/test_vault.py
git commit -m "feat: resolve canonical classification destinations"
```

---

### Task 3: Resolve Before Proposal Write and Support Module-General Content

**Files:**
- Modify: `app/outbox.py`
- Modify: `tests/test_outbox.py`

**Interfaces:**
- Consumes: Task 2 `resolve_classification_destination`.
- Produces:
  - `Proposal.sub: str | None`.
  - `propose_classification(scope, item_path, *, module, sub, claimed_block=None, rule_id=None) -> Proposal`; there is no `entity` or trusted `block` parameter.
  - `_apply_sub(text: str, sub: str | None) -> str`, removing the field when `sub is None`.
- Proposal ids and created timestamps remain unchanged for S4.

- [ ] **Step 1: Make outbox fixtures registry-faithful**

Add a complete invented `_system/archetypes.yaml` to every classification outbox Git fixture. The declared destination modules/subs/blocks must match the fixture's module directories. Do not weaken the resolver or introduce production fallbacks for old tests.

- [ ] **Step 2: Add failing canonical creation and interface tests**

```python
def test_proposal_derives_block_and_canonical_destination(tmp_path):
    vault = _vault(tmp_path)
    scope = Scope(vault, "demo")
    source = scope.resolve("00-inbox", "active", "note.md")

    prop = propose_classification(
        scope, source, module="11-library", sub="reference", claimed_block="govern"
    )

    record = yaml.safe_load(prop.path.read_text(encoding="utf-8"))
    assert record["entity"] == "demo"
    assert record["module"] == "11-library"
    assert record["sub"] == "reference"
    assert record["block"] == "govern"
    assert record["dst"] == "demo/11-library/active/note.md"


def test_proposal_interface_has_no_entity_or_trusted_block_authority():
    params = inspect.signature(propose_classification).parameters
    assert "entity" not in params
    assert "block" not in params
    assert "claimed_block" in params
```

Run both tests and record RED from the legacy trusted `block` signature.

- [ ] **Step 3: Add failing no-write tamper tests**

Parameterize invalid module, invalid sub, forged claimed block, and foreign/noncanonical source. Before each call snapshot:

```python
before_head = git_head(vault)
before_paths = git_tracked_paths(vault)
assert not scope.resolve("outbox").exists()
```

After the expected `DestinationError`, assert the outbox still does not exist, HEAD/tracked paths are unchanged, and neither source nor destination bytes changed.

- [ ] **Step 4: Add failing module-general diff/application tests**

```python
def test_module_general_proposal_stores_null_and_removes_sub(tmp_path):
    vault = _vault(tmp_path)
    scope = Scope(vault, "demo")
    prop = propose_classification(
        scope,
        scope.resolve("00-inbox", "active", "note.md"),
        module="11-library",
        sub="",
    )
    record = yaml.safe_load(prop.path.read_text(encoding="utf-8"))
    assert record["sub"] is None
    diff = preview_diff(scope, prop)
    assert "-sub: triage" in diff
    assert "+sub:" not in diff
```

The approval assertion after Task 4 revalidation will confirm the destination file has no `sub:` line.

- [ ] **Step 5: Implement resolver-first proposal creation**

Change the signature and begin the function with:

```python
destination = resolve_classification_destination(
    scope,
    item_path,
    module=module,
    sub=sub,
    claimed_block=claimed_block,
)
```

Only after that returns may `scope.resolve("outbox")`, `mkdir`, proposal id generation, or YAML writing occur. Build the record exclusively from `destination.entity`, `.src`, `.dst`, `.module`, `.sub`, and `.block`.

Change `_apply_sub`:

```python
def _apply_sub(text: str, sub: str | None) -> str:
    if sub is None:
        return re.sub(r"(?m)^sub:\s*.*\n?", "", text, count=1)
    if re.search(r"(?m)^sub:\s*.*$", text):
        return re.sub(r"(?m)^sub:\s*.*$", f"sub: {sub}", text, count=1)
    fm_end = text.find("---", 3)
    if fm_end != -1:
        return text[:fm_end] + f"sub: {sub}\n" + text[fm_end:]
    return text
```

- [ ] **Step 6: Update all direct callers and run GREEN**

Replace direct test/service calls using `block=` with either no block or `claimed_block=`. Do not touch the HTTP route yet beyond what is necessary for collection; Task 5 owns browser behavior.

Run:

```bash
UV_CACHE_DIR=/private/tmp/oneos-s3-uv-cache uv run python -m pytest tests/test_outbox.py -q
UV_CACHE_DIR=/private/tmp/oneos-s3-uv-cache uv run python -m pytest tests/test_ingest_commit.py tests/test_folder_adapter.py tests/test_email_adapter.py -q
git diff --check
```

- [ ] **Step 7: Commit Task 3**

```bash
git add app/outbox.py tests/test_outbox.py
git commit -m "feat: canonicalize classification proposals"
```

---

### Task 4: Revalidate Stored Classification Proposals

**Files:**
- Modify: `app/outbox.py`
- Modify: `tests/test_outbox.py`

**Interfaces:**
- Consumes: Task 2 resolver with `require_source=False` and Task 3 canonical proposal schema.
- Produces: `class OutboxDestinationError(OutboxError)` and one `_require_destination(scope, proposal) -> Proposal` gate used by discovery, preview, and approval.
- S4 continues to own source hash, collision-safe ids, and stale/missing refusal.

- [ ] **Step 1: Add failing typed-record tests**

Write proposal YAML records where `module`, `sub`, `block`, `src`, or `dst` is a list/int/missing value. `load_proposals(scope)` must raise `OutboxDestinationError`, not `KeyError`, `TypeError`, or an unsafe fallback.

```python
@pytest.mark.parametrize("field,value", [
    ("module", ["11-library"]),
    ("sub", {"id": "reference"}),
    ("block", 7),
    ("dst", ["alpha/11-library/active/note.md"]),
])
def test_loading_rejects_malformed_destination_scalars(
    two_entity_vault, field, value
):
    scope = Scope(two_entity_vault, "alpha")
    record = canonical_alpha_record()
    record[field] = value
    _write_record(scope, "malformed.yaml", yaml.safe_dump(record))
    with pytest.raises(OutboxDestinationError):
        load_proposals(scope)
```

- [ ] **Step 2: Add failing exact canonical comparison tests**

Parameterize individually forged but same-scope fields:

- active but different module;
- registered sub from another module;
- incorrect block;
- destination filename mismatch;
- destination module mismatch;
- destination with an extra path segment; and
- `sub: null` paired with a destination/preview that tries to keep `sub: triage`.

All must fail on `load_proposals`, `preview_diff`, and `approve` before mutation.

- [ ] **Step 3: Prove invalid records fail before source-body reads**

Patch `Path.read_text` only to raise if the canonical receipt path is opened. Allow reading the proposal and registries. Load a record with a forged block or dst and assert `OutboxDestinationError`; the guarded source read must not fire.

- [ ] **Step 4: Implement strict parsing and destination revalidation**

Make `_to_proposal` validate required scalar types and convert only `sub is None` or `str`. Do not stringify YAML values. Use a required-string helper and wrap malformed records:

```python
def _required_string(record: dict, key: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value:
        raise OutboxDestinationError("proposal destination record is malformed")
    return value


def _to_proposal(path: Path, record: dict) -> Proposal:
    if not isinstance(record, dict):
        raise OutboxDestinationError("proposal record must be a mapping")
    sub = record.get("sub")
    if sub is not None and not isinstance(sub, str):
        raise OutboxDestinationError("proposal sub must be a string or null")
    return Proposal(
        id=_required_string(record, "id"),
        path=path,
        action=_required_string(record, "action"),
        entity=_required_string(record, "entity"),
        src=_required_string(record, "src"),
        dst=_required_string(record, "dst"),
        module=_required_string(record, "module"),
        sub=sub,
        block=_required_string(record, "block"),
        rule_id=record.get("rule_id") if isinstance(record.get("rule_id"), str) else None,
        created=record.get("created") if isinstance(record.get("created"), str) else "",
        status=record.get("status") if isinstance(record.get("status"), str) else "pending",
    )
```

Implement:

```python
def _require_destination(scope: Scope, proposal: Proposal) -> Proposal:
    proposal = _require_scope(scope, proposal)
    try:
        source = scope.resolve_stored(proposal.src)
        canonical = resolve_classification_destination(
            scope,
            source,
            module=proposal.module,
            sub=proposal.sub,
            claimed_block=proposal.block,
            require_source=False,
        )
    except (DestinationError, CrossScopeError, DestinationRegistryError) as exc:
        raise OutboxDestinationError("proposal destination is invalid") from exc
    if proposal.src != canonical.src or proposal.dst != canonical.dst:
        raise OutboxDestinationError("proposal destination is non-canonical")
    return proposal
```

Call `_require_destination` from `load_proposals`, `preview_diff`, and any path that receives a constructed `Proposal`. `get_proposal` remains based on validated `load_proposals`.

- [ ] **Step 5: Complete module-general approval behavior**

Approve a canonical `sub: null` proposal in a real temporary Git vault. Assert the destination contains no line matching `^sub:`, the move is one commit, and `git revert` restores the triage receipt. This is a behavior test, not a mock assertion.

- [ ] **Step 6: Run outbox and S1/S2 regressions GREEN**

```bash
UV_CACHE_DIR=/private/tmp/oneos-s3-uv-cache uv run python -m pytest tests/test_outbox.py -q
UV_CACHE_DIR=/private/tmp/oneos-s3-uv-cache uv run python -m pytest tests/test_scope.py tests/test_ingest_commit.py -q
git diff --check
```

- [ ] **Step 7: Commit Task 4**

```bash
git add app/outbox.py tests/test_outbox.py
git commit -m "fix: revalidate stored classification destinations"
```

---

### Task 5: Bind Triage Rendering and POSTs to Canonical Destinations

**Files:**
- Modify: `app/main.py`
- Modify: `templates/triage.html`
- Modify: `tests/test_app.py`
- Test unchanged: `tests/test_triage.py`

**Interfaces:**
- Consumes: Task 2 resolver and Task 3 `propose_classification(..., claimed_block=...)`.
- Produces: triage rows `(item, classification, canonical_destination_or_none)`; no actionable button for an invalid recommendation.
- The POST consumes optional legacy `block` only as a mismatch claim and rejects any form-provided `entity` field before service invocation.

- [ ] **Step 1: Make HTTP fixtures destination-registry faithful**

Update the synthetic app vault so every confident classifier route and pre-existing proposal points to a module/sub declared in its synthetic archetypes registry, active for that entity, and present with a real `active/` directory. Preserve the existing two-entity concurrency markers and negative cross-entity assertions.

- [ ] **Step 2: Add failing rendering tests**

Add one valid rule and one invalid/inactive rule. Assert:

```python
def test_triage_renders_accept_only_for_canonical_destination(client):
    html = client.get("/triage/alpha").text
    assert "valid-destination-marker" in html
    assert "invalid-destination-marker" in html
    assert html.count('class="accept"') == 1
    assert '"block":' not in html
```

The marker strings are real item titles/bodies from the synthetic fixture, not test-only DOM attributes. The invalid item remains visible but has no Accept action.

- [ ] **Step 3: Add failing tampered POST no-write tests**

Parameterize:

```python
@pytest.mark.parametrize("data", [
    {"filename": "../marker.md", "module": "02-work", "sub": "general"},
    {"filename": r"..\\marker.md", "module": "02-work", "sub": "general"},
    {"filename": "marker.md", "module": "missing", "sub": "general"},
    {"filename": "marker.md", "module": "02-work", "sub": "wrong-module"},
    {"filename": "marker.md", "module": "02-work", "sub": "general", "block": "growth"},
    {"filename": "marker.md", "module": "02-work", "sub": "general", "entity": "beta"},
])
def test_tampered_proposal_form_writes_nothing(client, data):
    before_head = git_head(client.vault)
    before = snapshot_entity_bytes(client.vault, ("alpha", "beta"))
    response = client.post("/triage/alpha/propose", data=data)
    assert response.status_code >= 400
    assert git_head(client.vault) == before_head
    assert snapshot_entity_bytes(client.vault, ("alpha", "beta")) == before
    assert not list((client.vault / "alpha/outbox").glob("*.yaml"))
```

For this test, construct `TestClient(client.app, raise_server_exceptions=False)` after the fixture has loaded the app and copy `client.vault` only for snapshot access. Do not change the main fixture to suppress exceptions globally, and do not add S6 error HTML.

- [ ] **Step 4: Implement canonical triage rendering**

For every `(item, classification)` result, call the resolver with the bound scope and classification values. Catch only `DestinationError`/registry validation errors and store `None`; do not repair the rule. Pass triples to the template.

In `templates/triage.html`, render module/sub/block from the canonical destination. Render the button only when `classification.confident` and the canonical destination is non-`None`. Submit filename, module, and `sub or ''`; omit block and entity.

- [ ] **Step 5: Implement fail-closed POST handling**

Replace basename normalization. The route signature accepts:

```python
filename: str = Form(...)
module: str = Form(...)
sub: str = Form("")
block: str | None = Form(None)
entity_claim: str | None = Form(None, alias="entity")
```

If `entity_claim is not None`, raise `OutboxDestinationError("entity is owned by request scope")` before resolving any path or calling the proposal service. Pass `filename` unchanged to `scope.resolve("00-inbox", "active") / filename`; the resolver rejects separators before any write. Call `propose_classification(..., claimed_block=block)`.

Do not convert these failures to friendly HTML; S6 owns presentation.

- [ ] **Step 6: Preserve concurrent request isolation**

Extend the existing overlapped alpha/beta proposal route test so both use the same filename and destination choice. Assert each proposal's canonical entity/src/dst remains in its own scope and no shared destination object is reused.

- [ ] **Step 7: Run route, triage, outbox, and full public tests GREEN**

```bash
UV_CACHE_DIR=/private/tmp/oneos-s3-uv-cache uv run python -m pytest tests/test_app.py tests/test_triage.py -q
UV_CACHE_DIR=/private/tmp/oneos-s3-uv-cache uv run python -m pytest tests/test_destinations.py tests/test_outbox.py -q
UV_CACHE_DIR=/private/tmp/oneos-s3-uv-cache uv run python -m pytest -q
git diff --check
```

Expected: all pass; complete count is at least the 233-test baseline.

- [ ] **Step 8: Commit Task 5**

```bash
git add app/main.py templates/triage.html tests/test_app.py
git commit -m "feat: bind triage to server destinations"
```

---

### Task 6: Bounded S3 Review and Complete Safety Verification

**Files:**
- Review: all S3 commits since `d034623`
- Modify only if a bounded S3 defect first receives a failing regression test
- Evidence: git-ignored SDD report files only

**Interfaces:**
- Consumes: Tasks 1-5 and the approved S3 design.
- Produces: evidence that S3 is complete, S1/S2 remain green, S4-S6 remain deferred, and Grey Matter is byte-identical.

- [ ] **Step 1: Confirm public worktree scope**

```bash
git status --short --branch
git diff --check
git log --oneline --decorate -12
git diff d034623 --stat
git diff d034623 --name-only
```

Expected: only S3 plan/implementation/test files; no private values, generated caches, dependency changes, or S4-S6 implementation.

- [ ] **Step 2: Perform the bounded S3 mutation review**

Explicitly verify that tests kill each mutation:

1. Trust submitted block instead of registry block.
2. Merge `archetype:` into flags.
3. Accept an on-disk but inactive module.
4. Accept an active registry module missing from disk.
5. Accept a sub belonging to another module or disabled by a flag.
6. Turn traversal into a basename instead of rejecting it.
7. Follow module/active/destination symlinks.
8. Write proposal YAML before validation completes.
9. Validate at creation but trust edited YAML at load/preview/approval.
10. Write `sub:` empty instead of removing the field.

If a real S3 defect is found, write the smallest behavioral failing test, run it RED, implement the smallest fix, rerun focused tests GREEN, and commit `fix: close S3 destination review findings`. Do not implement deferred observations.

- [ ] **Step 3: Run complete public gates**

```bash
UV_CACHE_DIR=/private/tmp/oneos-s3-uv-cache uv run python -m pytest tests/test_destinations.py tests/test_vault.py -q
UV_CACHE_DIR=/private/tmp/oneos-s3-uv-cache uv run python -m pytest tests/test_outbox.py tests/test_app.py tests/test_triage.py -q
UV_CACHE_DIR=/private/tmp/oneos-s3-uv-cache uv run python -m pytest -q
tools/run_gitleaks.sh .
UV_CACHE_DIR=/private/tmp/oneos-s3-uv-cache uv run python -m tools.public_repo_audit --repo . --history
```

Expected: all tests pass, no leaks, audit `CLEAN`.

- [ ] **Step 4: Establish Grey Matter precheck snapshots**

Require `ONEOS_VAULT` to name the private vault root. Verify its HEAD is at or after the minimum required by `AGENTS.md`, then run:

```bash
git -C "$ONEOS_VAULT" status --porcelain=v2 --untracked-files=all \
  > /private/tmp/oneos-s3-gm-status.precheck
git -C "$ONEOS_VAULT" diff --binary HEAD \
  --output=/private/tmp/oneos-s3-gm-diff.precheck
git -C "$ONEOS_VAULT" log --oneline -1
```

Do not stage, stash, reset, clean, checkout, edit, or commit anything in Grey Matter.

- [ ] **Step 5: Run private gates read-only**

```bash
cd "$ONEOS_VAULT/_system/scripts"
python3 -m unittest discover -q
python3 "$ONEOS_VAULT/_system/scripts/check_v2.py" "$ONEOS_VAULT"
python3 "$ONEOS_VAULT/_system/scripts/policy_enforcer.py" \
  --policy "$ONEOS_VAULT/_system/scripts/action-policy.yaml" test-suite
cd /private/tmp/oneos-s3-server-owned-destinations
UV_CACHE_DIR=/private/tmp/oneos-s3-uv-cache uv run python -m tools.public_repo_audit \
  --repo . --vault "$ONEOS_VAULT" --history
```

Expected: private tests pass, structural validator reports `0 error(s), 0 warning(s)`, policy exit gate passes, combined audit is `CLEAN`.

- [ ] **Step 6: Prove Grey Matter byte-identical**

```bash
git -C "$ONEOS_VAULT" status --porcelain=v2 --untracked-files=all \
  > /private/tmp/oneos-s3-gm-status.postcheck
git -C "$ONEOS_VAULT" diff --binary HEAD \
  --output=/private/tmp/oneos-s3-gm-diff.postcheck
cmp /private/tmp/oneos-s3-gm-status.precheck \
  /private/tmp/oneos-s3-gm-status.postcheck
cmp /private/tmp/oneos-s3-gm-diff.precheck \
  /private/tmp/oneos-s3-gm-diff.postcheck
```

Expected: both `cmp` commands exit 0.

- [ ] **Step 7: Record final evidence and stop before integration**

```bash
git diff --check
git status --short --branch
git log --oneline --decorate -12
```

Report exact public/private test counts, audits, Grey Matter comparison results, S3 commit list, and deferred S4-S6 observations. Do not push, merge, open a PR, or remove the worktree.
