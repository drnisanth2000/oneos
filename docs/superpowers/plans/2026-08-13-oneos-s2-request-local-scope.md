# OneOS S2 Request-Local Scope Implementation Plan

> **Historical execution plan:** S2 is implemented and merged in the PR #6
> lineage. Retain this file for design/test rationale; do not run its branch,
> commit, stop, or test-count instructions again. Current state is in
> `BUILD.md`, `docs/STATUS.md`, and `docs/SAFETY-FOUNDATION-S1-S4.md`.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace OneOS's mutable process-wide entity selection with an immutable, manifest-validated request scope and deterministically route shared-mailbox email to exactly one configured entity.

**Architecture:** `_system/entities.yaml` is read through one unscoped catalog boundary; every entity route receives a newly bound immutable `Scope` from a FastAPI dependency. Entity services derive identity and paths from that scope, stored records are rejected when they name another entity, and the email adapter constructs the same scope only after recipient-address routing resolves to exactly one manifest entry.

**Tech Stack:** Python 3.12, FastAPI dependencies, Jinja2, HTMX, PyYAML, standard-library `email`, SQLite, Git, pytest, Starlette `TestClient`, `threading.Barrier`, and `ThreadPoolExecutor`; no new dependency or build step.

## Global Constraints

- Implement Safety Foundation **S2 only** on `codex/s2-request-local-scope`; do not implement S3-S6, push, merge, or open a pull request.
- Preserve S1 exactly: one new intake produces one receipt-only `ingest:` commit, duplicate intake is a no-op, and raw source content never enters the vault.
- The public repository, tests, documentation, commit messages, and logs contain only synthetic slugs and addresses under `example.invalid`.
- Runtime entity identity comes only from `_system/entities.yaml` plus a request path segment or adapter input.
- `Scope` is immutable after construction; there is no module-level mutable scope and no `set_current_entity()` compatibility path at S2 completion.
- All entity document paths, proposal paths, reference scans, `books.db` reads, and entity-sensitive registry operations derive identity from `scope.current_entity()`.
- The unscoped catalog may expose entity labels, flags, module status, and navigation links; it must never read or render entity documents, proposals, database rows, or private registry values from another entity.
- Email routing uses only `Delivered-To`, `X-Original-To`, `Envelope-To`, `To`, and `Cc`; sender, subject, message body, first-entity fallback, and LLM inference are prohibited.
- No new dependency, daemon, queue, scheduler, UI screen, deployment unit, or client-side persistence mechanism.
- S3 retains module/sub/block/destination validation; S4 retains proposal freshness and collision-safe ids; S5 retains general Git isolation; S6 retains polished error rendering.
- Use GPT-5.6 Sol at `high` reasoning for implementation workers when the execution environment permits it.
- Before any private verification command, snapshot Grey Matter `git status --porcelain=v2 --untracked-files=all` and `git diff --binary HEAD` outside both repositories; compare byte-identical snapshots immediately afterward.
- Every task follows red-green-refactor, ends with its focused tests plus `uv run python -m pytest -q` passing, and creates only the commit listed for that task.

## File Structure

- Create `app/entities.py`: parse the entity manifest, expose immutable entity definitions, validate recipient-address ownership, and provide the unscoped catalog used by navigation and scope construction.
- Modify `app/scope.py`: immutable manifest-bound entity scope, entity-contained path resolution, stored vault-relative path validation, and typed selection/containment failures.
- Modify `app/config.py`: construct an unscoped catalog and a new entity scope; remove the process-wide scope factory semantics.
- Modify `app/vault.py`: consume the unscoped entity catalog/root for workspace navigation and registry-derived module metadata only.
- Modify `app/classifier.py`: consume the unscoped catalog boundary for shared classifier/module registries without acquiring entity document authority.
- Modify `app/main.py`: FastAPI entity-scope dependency, request-local route wiring, scope-derived template context, and unscoped shell/default navigation.
- Modify `app/inbox.py`: read only the bound entity inbox.
- Modify `app/outbox.py`: scope-derived proposal creation/loading/preview/approval/rejection with record and stored-path agreement checks.
- Modify `app/registry.py`: bound-entity reference scans and scoped registry proposal/mutation behavior.
- Modify `app/ingest/base.py`: derive receipt identity and tracked-path scans from the bound scope while preserving S1 commits.
- Modify `app/ingest/adapters/email.py`: deterministic recipient parsing/routing and scope-only normalized email ingestion.
- Modify `app/ingest/adapters/folder.py`: validate and bind the configured entity before reading/moving the source; pass one scope through ingestion.
- Modify `tests/conftest.py`: explicit synthetic manifest helpers for temporary vaults; never infer production entities from directory scans.
- Modify `tests/test_scope.py`, `tests/test_vault.py`, `tests/test_triage.py`, `tests/test_app.py`, `tests/test_outbox.py`, `tests/test_registry.py`, `tests/test_ingest_commit.py`, `tests/test_email_adapter.py`, and `tests/test_folder_adapter.py`: S2 behavior, concurrency, mutation, and regression coverage.

---

### Task 1: Manifest Catalog and Immutable Entity Scope

**Files:**
- Create: `app/entities.py`
- Modify: `app/scope.py:1-45`
- Modify: `app/config.py:1-33`
- Modify: `app/vault.py:1-122`
- Modify: `app/classifier.py:18-34`
- Modify: `app/main.py:1-195`
- Modify: `app/inbox.py`
- Modify: `app/outbox.py`
- Modify: `app/registry.py`
- Modify: `app/ingest/base.py`
- Modify: `app/ingest/adapters/email.py`
- Modify: `app/ingest/adapters/folder.py`
- Modify: `tests/conftest.py:1-75`
- Modify: `tests/test_scope.py:1-31`
- Modify: `tests/test_vault.py:1-149`
- Modify: `tests/test_triage.py:1-113`
- Modify: every test that constructs `Scope` or invokes an adapter without a bound scope

**Interfaces:**
- Produces: `EntityCatalog.load(root: Path | str) -> EntityCatalog`
- Produces: `EntityCatalog.require(slug: str) -> EntityDefinition`
- Produces: `EntityCatalog.entities: tuple[EntityDefinition, ...]`
- Produces: `Scope(root: Path | str, entity: str)`
- Produces: `Scope.current_entity() -> str`
- Produces temporarily through Task 5: `Scope.require_entity(entity: str) -> str`, a mismatch guard for legacy service signatures while domains are migrated one at a time.
- Produces: `Scope.resolve(*parts: str | Path) -> Path`
- Produces: `Scope.resolve_stored(relative: str | Path) -> Path`
- Produces: `Scope.vault_relative(path: str | Path) -> str`
- Produces: `Scope.system_path(*parts: str | Path) -> Path`
- Produces: `build_catalog() -> EntityCatalog` and `build_scope(entity: str) -> Scope`
- Consumes: `_system/entities.yaml`; it never scans directories to discover an entity.

- [ ] **Step 1: Add explicit synthetic manifest fixture support**

Add this helper to `tests/conftest.py`; callers must state their entities rather than letting test infrastructure infer them from directory names:

```python
def entities_yaml(*slugs: str, ingest: dict[str, list[str]] | None = None) -> str:
    rows = ['version: "1.0"', "entities:"]
    for slug in slugs:
        rows.extend((f"  {slug}:", f"    label: {slug.title()}", "    flags: []"))
        addresses = (ingest or {}).get(slug, [])
        if addresses:
            rows.append("    ingest:")
            rows.append("      email_addresses:")
            rows.extend(f"        - {address}" for address in addresses)
    return "\n".join(rows) + "\n"


def git_entity_vault(
    root: Path,
    entities: tuple[str, ...],
    files: dict[str, str],
) -> Path:
    tree = dict(files)
    tree.setdefault("_system/entities.yaml", entities_yaml(*entities))
    return git_vault(root, tree)
```

Use `git_entity_vault` only in tests that construct a `Scope`; keep `git_vault` unchanged so an absent manifest remains testable.

- [ ] **Step 2: Write failing catalog and immutable-scope tests**

Replace the mutable tests in `tests/test_scope.py` with manifest-backed cases, including a directory that exists but is absent from the manifest:

```python
def test_scope_accepts_only_registered_entity(tmp_path):
    write_vault(tmp_path, entities_yaml("alpha"))
    scope = Scope(tmp_path, "alpha")
    assert scope.current_entity() == "alpha"
    assert scope.resolve("00-inbox", "active") == tmp_path / "alpha/00-inbox/active"


@pytest.mark.parametrize("slug", ["", ".", "..", "Bad_Slug", "a/b", "a\\b"])
def test_scope_rejects_malformed_entity(tmp_path, slug):
    write_vault(tmp_path, entities_yaml("alpha"))
    with pytest.raises(EntitySelectionError):
        Scope(tmp_path, slug)


def test_scope_rejects_unknown_and_directory_only_entity(tmp_path):
    write_vault(tmp_path, entities_yaml("alpha"))
    (tmp_path / "directory-only").mkdir()
    with pytest.raises(EntitySelectionError):
        Scope(tmp_path, "directory-only")


def test_scope_is_immutable(tmp_path):
    write_vault(tmp_path, entities_yaml("alpha"))
    scope = Scope(tmp_path, "alpha")
    assert not hasattr(scope, "set_current_entity")
    with pytest.raises((AttributeError, dataclasses.FrozenInstanceError)):
        scope._entity = "beta"


def test_stored_path_must_name_bound_entity(tmp_path):
    write_vault(tmp_path, entities_yaml("alpha", "beta"))
    with pytest.raises(CrossScopeError):
        Scope(tmp_path, "alpha").resolve_stored("beta/00-inbox/active/item.md")


def test_system_path_does_not_grant_another_entity_path(tmp_path):
    write_vault(tmp_path, entities_yaml("alpha", "beta"))
    scope = Scope(tmp_path, "alpha")
    assert scope.system_path("entities.yaml") == tmp_path / "_system/entities.yaml"
    with pytest.raises(CrossScopeError):
        scope.resolve("..", "beta", "00-inbox")
```

Also assert `EntityCatalog.entities` preserves manifest order and exposes only `slug`, `label`, and `flags` at this stage.

Add the first request-local regression to `tests/test_app.py` before changing application wiring. Give alpha and beta distinct inbox markers, wrap the existing reader with a `threading.Barrier(2)`, dispatch both real `TestClient` requests through a `ThreadPoolExecutor(max_workers=2)`, and assert each HTML response contains only its own marker. Add `assert not hasattr(app.main, "scope")` so the test detects reintroduction of module-level entity state.

```python
def test_concurrent_triage_requests_keep_entity_rows_isolated(client, monkeypatch):
    barrier = threading.Barrier(2)
    real_read = app.main.read_inbox

    def overlapped(scope, entity):
        barrier.wait(timeout=5)
        return real_read(scope, entity)

    monkeypatch.setattr(app.main, "read_inbox", overlapped)
    with ThreadPoolExecutor(max_workers=2) as pool:
        alpha = pool.submit(client.get, "/triage/alpha")
        beta = pool.submit(client.get, "/triage/beta")
    assert "alpha-marker" in alpha.result().text
    assert "beta-marker" not in alpha.result().text
    assert "beta-marker" in beta.result().text
    assert "alpha-marker" not in beta.result().text
    assert not hasattr(app.main, "scope")
```

- [ ] **Step 3: Run the new tests and verify the mutable implementation fails**

Run:

```bash
uv run python -m pytest tests/test_scope.py -q
```

Expected: failures because `Scope` still accepts an unset entity, exposes `set_current_entity()`, trusts directory-safe but unregistered slugs, and has no stored-path containment API.

- [ ] **Step 4: Implement the unscoped manifest catalog**

Create `app/entities.py` with immutable definitions and typed configuration/selection errors. Error text must identify only the supplied synthetic/runtime slug or registry field, never an absolute vault path or manifest dump:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

import yaml

_ENTITY_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class EntityManifestError(RuntimeError):
    pass


class EntitySelectionError(ValueError):
    pass


@dataclass(frozen=True)
class EntityDefinition:
    slug: str
    label: str
    flags: tuple[str, ...]


@dataclass(frozen=True)
class EntityCatalog:
    root: Path
    entities: tuple[EntityDefinition, ...]

    @classmethod
    def load(cls, root: Path | str) -> "EntityCatalog":
        root_path = Path(root).resolve()
        path = root_path / "_system/entities.yaml"
        if not path.is_file():
            raise EntityManifestError("entities manifest is missing")
        cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        records = cfg.get("entities")
        if not isinstance(records, dict):
            raise EntityManifestError("entities manifest requires an entities mapping")
        parsed: list[EntityDefinition] = []
        for slug, raw in records.items():
            if not isinstance(slug, str) or not _ENTITY_SLUG.fullmatch(slug):
                raise EntityManifestError("entities manifest contains an invalid slug")
            spec = raw or {}
            if not isinstance(spec, dict):
                raise EntityManifestError(f"entity {slug!r} must be a mapping")
            flags = spec.get("flags") or []
            if not isinstance(flags, list) or not all(isinstance(flag, str) for flag in flags):
                raise EntityManifestError(f"entity {slug!r} flags must be a list of strings")
            parsed.append(EntityDefinition(slug, str(spec.get("label", slug)), tuple(flags)))
        return cls(root_path, tuple(parsed))

    def require(self, slug: str) -> EntityDefinition:
        if not isinstance(slug, str) or not _ENTITY_SLUG.fullmatch(slug):
            raise EntitySelectionError("invalid entity selection")
        for entity in self.entities:
            if entity.slug == slug:
                return entity
        raise EntitySelectionError(f"unknown entity {slug!r}")
```

- [ ] **Step 5: Implement immutable entity-contained `Scope`**

Replace `app/scope.py` with a frozen value. Resolve both the entity root and candidate so an existing symlink or `..` cannot cross the bound entity:

```python
from dataclasses import dataclass
from pathlib import Path

from .entities import EntityCatalog, EntitySelectionError


class CrossScopeError(ValueError):
    pass


@dataclass(frozen=True)
class Scope:
    _root: Path
    _entity: str

    def __init__(self, root: Path | str, entity: str) -> None:
        catalog = EntityCatalog.load(root)
        selected = catalog.require(entity)
        object.__setattr__(self, "_root", catalog.root)
        object.__setattr__(self, "_entity", selected.slug)

    @property
    def root(self) -> Path:
        return self._root

    def current_entity(self) -> str:
        return self._entity

    def require_entity(self, entity: str) -> str:
        if entity != self._entity:
            raise CrossScopeError("entity argument disagrees with selected scope")
        return self._entity

    def resolve(self, *parts: str | Path) -> Path:
        base = (self._root / self._entity).resolve()
        candidate = base.joinpath(*map(Path, parts)).resolve()
        if not candidate.is_relative_to(base):
            raise CrossScopeError("entity path leaves the selected scope")
        return candidate

    def resolve_stored(self, relative: str | Path) -> Path:
        stored = Path(relative)
        if stored.is_absolute() or not stored.parts or stored.parts[0] != self._entity:
            raise CrossScopeError("stored path belongs to another entity")
        return self.resolve(*stored.parts[1:])

    def vault_relative(self, path: str | Path) -> str:
        candidate = Path(path).resolve()
        base = self.resolve()
        if not candidate.is_relative_to(base):
            raise CrossScopeError("path belongs to another entity")
        return candidate.relative_to(self._root).as_posix()

    def system_path(self, *parts: str | Path) -> Path:
        base = (self._root / "_system").resolve()
        candidate = base.joinpath(*map(Path, parts)).resolve()
        if not candidate.is_relative_to(base):
            raise CrossScopeError("system path leaves the registry root")
        return candidate
```

- [ ] **Step 6: Separate unscoped navigation from entity scope**

Change `Vault` to accept `EntityCatalog` and read only shared registries plus module-directory status:

```python
class Vault:
    def __init__(self, catalog: EntityCatalog) -> None:
        self._catalog = catalog

    @property
    def root(self) -> Path:
        return self._catalog.root

    def system_path(self, *parts: str) -> Path:
        return self.root.joinpath("_system", *parts)
```

Build `Bundle` rows from `self._catalog.entities`; keep activation flags-only and all E4 behavior unchanged. Update `Classifier` to read `vault.system_path("classifier", "rules.yaml")`. Change config construction to:

```python
def build_catalog() -> EntityCatalog:
    return EntityCatalog.load(vault_root())


def build_scope(entity: str) -> Scope:
    return Scope(vault_root(), entity)
```

Update `tests/test_vault.py` and `tests/test_triage.py` from `Vault(Scope(root))` to `Vault(EntityCatalog.load(root))`; these tests must still prove flags-only activation and E4 visibility.

- [ ] **Step 7: Make the application boot with request-bound scope while legacy service signatures are guarded**

This is a mechanical compatibility migration that keeps the complete suite green while Tasks 2-5 remove each domain's second identity parameter. In `app.main`, remove the module-level `scope`, construct one global `catalog = build_catalog()`, and add this FastAPI dependency:

```python
from typing import Annotated

from fastapi import Depends, HTTPException

from .entities import EntitySelectionError

catalog = build_catalog()


def entity_scope(entity: str) -> Scope:
    try:
        return build_scope(entity)
    except EntitySelectionError as exc:
        raise HTTPException(status_code=404) from exc


EntityScope = Annotated[Scope, Depends(entity_scope)]
```

Every `{entity}` route receives that fresh scope and passes `scope.current_entity()` to any service whose legacy signature still requires an entity.

At the first line of every remaining `(scope, entity, ...)` service, call `entity = scope.require_entity(entity)`. Replace each `scope.resolve(entity, ...)` with `scope.resolve(...)`, and derive record fields from the returned validated entity. Update adapter fallbacks from `Scope(vault)` to `Scope(vault, entity)`. This transitional guard is mandatory: a mismatched service argument must fail rather than resolve below the wrong directory.

Rewrite `_products_for` during this compatibility migration to accept the bound scope and guarded entity, because the module-level scope no longer exists:

```python
def _products_for(scope: Scope, entity: str) -> list[str]:
    entity = scope.require_entity(entity)
    path = scope.system_path("products.yaml")
    if not path.is_file():
        return []
    cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return list(((cfg.get("products") or {}).get(entity) or {}).keys())
```

Update every test-side `Scope(root)` call to `Scope(root, "synthetic-slug")`, using the slug already present in that fixture. Add an explicit synthetic entities manifest to every temporary Git vault that constructs a scope. Do not add a default entity, infer an entity from directories, or keep an unbound constructor.

- [ ] **Step 8: Run focused and complete regressions**

Run:

```bash
uv run python -m pytest tests/test_scope.py tests/test_vault.py tests/test_triage.py -q
uv run python -m pytest -q
```

Expected: the focused tests and request-local concurrency test pass; the complete suite remains at 141 or more passing tests with no warnings. No test or production caller instantiates `Scope` without an explicit registered entity.

- [ ] **Step 9: Commit the scope foundation**

```bash
git add app/entities.py app/scope.py app/config.py app/vault.py app/classifier.py app/main.py app/inbox.py app/outbox.py app/registry.py app/ingest/base.py app/ingest/adapters/email.py app/ingest/adapters/folder.py tests/conftest.py tests/test_scope.py tests/test_vault.py tests/test_triage.py tests/test_app.py tests/test_outbox.py tests/test_registry.py tests/test_ingest_commit.py tests/test_email_adapter.py tests/test_folder_adapter.py
git commit -m "refactor: bind immutable entity scope"
```

---

### Task 2: Scope-Derived Inbox/Outbox Services and Concurrency Proof

**Files:**
- Modify: `app/main.py:1-195`
- Modify: `app/inbox.py:1-61`
- Modify: `app/outbox.py:1-184`
- Modify: `templates/triage.html:20-86`
- Modify: `templates/outbox.html:20-35`
- Modify: `templates/blocks/outbox_list.html:1-25`
- Modify: `templates/blocks/diff.html:1-8`
- Modify: `tests/test_app.py:1-68`
- Modify: `tests/test_outbox.py:1-164`

**Interfaces:**
- Consumes: `Scope(root, entity)`, `Scope.resolve()`, `Scope.resolve_stored()`, `Scope.vault_relative()` from Task 1.
- Consumes: `entity_scope(entity: str) -> Scope` FastAPI dependency from Task 1.
- Produces: `read_inbox(scope: Scope) -> list[InboxItem]`
- Produces: `propose_classification(scope: Scope, item_path: Path, *, module: str, sub: str, block: str, rule_id: str | None = None) -> Proposal`
- Produces: `load_proposals(scope: Scope) -> list[Proposal]`
- Produces: `preview_diff(scope: Scope, proposal: Proposal) -> str`
- Produces: `approve(scope: Scope, proposal_id: str) -> Proposal`
- Produces: `reject(scope: Scope, proposal_id: str) -> Proposal`

- [ ] **Step 1: Write failing outbox request-local concurrency tests**

Extend `tests/test_app.py` with two registered synthetic entities, distinct outbox/source markers, and an overlap barrier around the real proposal loader. Task 1 already proves concurrent triage isolation; this test covers proposal rendering:

```python
def test_concurrent_outbox_requests_keep_entity_diffs_isolated(client, monkeypatch):
    import app.main as main

    barrier = threading.Barrier(2)
    real_load = main.load_proposals

    def overlapped(scope):
        barrier.wait(timeout=5)
        return real_load(scope)

    monkeypatch.setattr(main, "load_proposals", overlapped)
    with ThreadPoolExecutor(max_workers=2) as pool:
        alpha = pool.submit(client.get, "/outbox/alpha")
        beta = pool.submit(client.get, "/outbox/beta")
    alpha_html = alpha.result().text
    beta_html = beta.result().text
    assert "alpha-diff-marker" in alpha_html and "beta-diff-marker" not in alpha_html
    assert "beta-diff-marker" in beta_html and "alpha-diff-marker" not in beta_html


def test_unknown_route_entity_is_404_without_entity_directory_read(client, monkeypatch):
    watched = client.vault / "directory-only"
    watched.mkdir()
    real_is_dir = Path.is_dir

    def guarded(path):
        if path == watched:
            raise AssertionError("unknown entity directory was consulted")
        return real_is_dir(path)

    monkeypatch.setattr(Path, "is_dir", guarded)
    response = client.get("/triage/directory-only")
    assert response.status_code == 404
```

Retain Task 1's `test_no_module_level_scope_exists`, unknown-route test, and unscoped-route checks. In the fixture, attach `tmp_path` to the created client as `test_client.vault` so guarded directory assertions use an explicit temporary root.

- [ ] **Step 2: Write failing scope-derived outbox mutation tests**

Add tests that use the same proposal id in two entities and a forged record in one outbox whose `entity`, `src`, or `dst` names the other:

```python
def test_loading_mismatched_proposal_fails_before_other_entity_source_read(two_entity_vault, monkeypatch):
    scope = Scope(two_entity_vault, "alpha")
    forged = scope.resolve("outbox", "forged.yaml")
    forged.parent.mkdir(parents=True, exist_ok=True)
    forged.write_text(FORGED_BETA_PROPOSAL, encoding="utf-8")
    beta_source = two_entity_vault / "beta/00-inbox/active/beta.md"
    real_read = Path.read_text

    def guarded(path, *args, **kwargs):
        if path.resolve() == beta_source.resolve():
            raise AssertionError("cross-entity source was opened")
        return real_read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded)
    with pytest.raises(OutboxScopeError):
        load_proposals(scope)


def test_outbox_interfaces_have_one_identity_authority():
    for function in (load_proposals, approve, reject):
        assert "entity" not in inspect.signature(function).parameters
```

Also test that `preview_diff`, `approve`, and `reject` reject a `Proposal` or record whose entity differs from the bound scope and leave both entities' files and proposal YAML unchanged.

Add a direct service-boundary test for the source path:

```python
def test_propose_rejects_item_path_from_another_entity(two_entity_vault):
    alpha = Scope(two_entity_vault, "alpha")
    beta_item = two_entity_vault / "beta/00-inbox/active/beta.md"
    with pytest.raises(CrossScopeError):
        propose_classification(
            alpha,
            beta_item,
            module="11-knowledge",
            sub="kb",
            block="govern",
        )
    assert not alpha.resolve("outbox").exists()
```

- [ ] **Step 3: Run the tests and verify independent identity and stored-record trust fail**

Run:

```bash
uv run python -m pytest tests/test_app.py tests/test_outbox.py -q
```

Expected: failures because inbox/outbox functions still accept independent entity arguments and proposal loading still trusts record entity and stored paths. Task 1's triage concurrency, dependency, and unknown-entity tests remain green.

- [ ] **Step 4: Confirm the FastAPI dependency remains the sole route identity source**

Retain the Task 1 dependency exactly; do not introduce a second dependency or session/cookie selection path:

```python
catalog = build_catalog()


def entity_scope(entity: str) -> Scope:
    try:
        return build_scope(entity)
    except EntitySelectionError as exc:
        raise HTTPException(status_code=404) from exc


EntityScope = Annotated[Scope, Depends(entity_scope)]
```

Every route containing `{entity}` receives `scope: EntityScope`; it never calls a setter. Derive the template value once with `selected = scope.current_entity()`. `/`, `/triage`, and `/blocks/pulse` use only `catalog`, static content, or redirects. The implementation change in this step is removing transitional route calls that pass both `scope` and `selected` into inbox/outbox services.

- [ ] **Step 5: Remove the independent entity argument from inbox/outbox services**

Apply these exact signature/body rules:

```python
def read_inbox(scope: Scope) -> list[InboxItem]:
    directory = scope.resolve("00-inbox", "active")


def propose_classification(scope: Scope, item_path: Path, *, module: str, sub: str,
                           block: str, rule_id: str | None = None) -> Proposal:
    entity = scope.current_entity()
    src_rel = scope.vault_relative(item_path)
    dst_rel = (Path(entity) / module / "active" / Path(item_path).name).as_posix()
    outbox = scope.resolve("outbox")


def load_proposals(scope: Scope) -> list[Proposal]:
    outbox = scope.resolve("outbox")


def approve(scope: Scope, proposal_id: str) -> Proposal:
    prop = get_proposal(scope, proposal_id)
    src = scope.resolve_stored(prop.src)
    dst = scope.resolve_stored(prop.dst)


def reject(scope: Scope, proposal_id: str) -> Proposal:
    prop = get_proposal(scope, proposal_id)
```

Introduce a stable typed failure and one validation helper used by load, preview, approve, and reject:

```python
class OutboxScopeError(OutboxError):
    pass


def _require_scope(scope: Scope, proposal: Proposal) -> Proposal:
    if proposal.entity != scope.current_entity():
        raise OutboxScopeError("proposal belongs to another entity")
    scope.resolve_stored(proposal.src)
    scope.resolve_stored(proposal.dst)
    return proposal
```

The helper runs immediately after YAML parsing and before source/destination existence or content reads. Do not add S3 module/sub/block validation or S4 hashes/ids.

- [ ] **Step 6: Bind every render and action URL to the same scope**

Update route calls and contexts so `entity`, inbox rows, proposal list, diff, and action routes all derive from the dependency's `scope`. Keep the existing template URLs, but pass no user-supplied or process-global entity value into service functions. Continue swallowing existing `OutboxError` in approve/reject routes because S6 owns visible error rendering; the cross-scope proposal must remain untouched.

- [ ] **Step 7: Add concurrent outbox and proposal-request coverage**

Use barriers around the real `load_proposals` and `propose_classification` calls. Assert:

```python
assert "alpha-diff-marker" in alpha_outbox.text
assert "beta-diff-marker" not in alpha_outbox.text
assert "beta-diff-marker" in beta_outbox.text
assert "alpha-diff-marker" not in beta_outbox.text
assert list((vault / "alpha/outbox").glob("*.yaml"))
assert list((vault / "beta/outbox").glob("*.yaml"))
assert not list((vault / "alpha/outbox").glob("*beta*.yaml"))
assert not list((vault / "beta/outbox").glob("*alpha*.yaml"))
```

Post alpha's approve/reject route using beta's proposal id and assert beta's proposal/source remain byte-identical and alpha renders only its own outbox.

- [ ] **Step 8: Run focused and complete regressions**

Run:

```bash
uv run python -m pytest tests/test_scope.py tests/test_app.py tests/test_outbox.py -q
uv run python -m pytest -q
```

Expected: all request and outbox concurrency/cross-scope tests pass; all S1 and earlier behavior remains green.

- [ ] **Step 9: Commit request-local web scope**

```bash
git add app/main.py app/inbox.py app/outbox.py templates tests/test_app.py tests/test_outbox.py
git commit -m "fix: isolate entity scope per request"
```

---

### Task 3: Entity-Scoped Registry Reads, Proposals, and Mutations

**Files:**
- Modify: `app/main.py:152-195`
- Modify: `app/registry.py:1-230`
- Modify: `tests/test_registry.py:1-127`
- Modify: `tests/test_app.py`

**Interfaces:**
- Consumes: immutable `Scope` and FastAPI `EntityScope` dependency.
- Produces: `products_for(scope: Scope) -> list[str]`
- Produces: `reference_count(scope: Scope, kind: str, slug: str) -> ReferenceReport`
- Produces: `propose_delete(scope: Scope, kind: str, slug: str) -> DeleteProposal`
- Produces: `get_delete_proposal(scope: Scope, proposal_id: str) -> DeleteProposal`
- Produces: `execute_delete(scope: Scope, proposal_id: str) -> None`

- [ ] **Step 1: Write failing two-entity reference-count tests**

Create two entities that reuse the same synthetic product slug, each with distinct front matter, workspace rows, and `books.db` counts:

```python
def test_reference_count_reads_only_bound_entity(two_entity_registry_vault):
    alpha = reference_count(Scope(two_entity_registry_vault, "alpha"), "product", "shared")
    beta = reference_count(Scope(two_entity_registry_vault, "beta"), "product", "shared")
    assert alpha.sources == {"front-matter": 1, "workspaces": 1, "books.db": 2}
    assert beta.sources == {"front-matter": 2, "workspaces": 0, "books.db": 1}
```

Add a guarded-path test that raises if alpha counting opens a beta Markdown file or beta `books.db`. Add a route test that alpha's delete-impact fragment never contains beta's totals or marker text.

Include `alpha/.sensitive/hidden.md` containing the same product slug and make its `Path.read_text` raise if opened. The report must exclude `.sensitive`, `outbox`, and `staging` entirely.

Wrap `reference_count` with `threading.Barrier(2)` and dispatch concurrent alpha/beta delete-preview requests. Assert each fragment renders only its bound total. This exercises the real FastAPI dependency and registry service together rather than relying only on sequential unit calls.

- [ ] **Step 2: Write failing scoped-record and scoped-mutation tests**

Add a forged delete proposal under alpha whose record says beta and verify `get_delete_proposal` and `execute_delete` fail before registry write. Add identical `shared` product keys under both entities and prove an alpha delete removes only `products.alpha.shared`:

```python
def test_delete_removes_only_bound_registry_key(two_entity_registry_vault):
    scope = Scope(two_entity_registry_vault, "alpha")
    proposal = propose_delete(scope, "product", "unused")
    execute_delete(scope, proposal.id)
    cfg = yaml.safe_load(scope.system_path("products.yaml").read_text())
    assert "unused" not in cfg["products"]["alpha"]
    assert "unused" in cfg["products"]["beta"]


def test_registry_interfaces_have_one_identity_authority():
    for function in (propose_delete, get_delete_proposal, execute_delete):
        assert "entity" not in inspect.signature(function).parameters
```

Add `test_add_workspace_rejects_another_entity_entry`: bind alpha, pass an entry whose `entity` is beta, expect `RegistryError`, and assert `_system/workspaces.yaml` and Git HEAD are byte-identical.

- [ ] **Step 3: Run the registry tests and verify global scans fail**

Run:

```bash
uv run python -m pytest tests/test_registry.py tests/test_app.py -q
```

Expected: failures because front matter and databases are scanned from `scope.root`, workspaces are counted across entities, record entity is not checked, and `_remove_key_block` is not anchored to the selected entity's registry namespace.

- [ ] **Step 4: Scope every registry input**

Change the counting helpers to receive the bound entity root or exact database path:

```python
def _count_front_matter(entity_root: Path, field_name: str, slug: str) -> int:
    count = 0
    for path in entity_root.rglob("*.md"):
        if any(part in {".sensitive", "outbox", "staging"} for part in path.relative_to(entity_root).parts):
            continue
        try:
            front_matter, _body = split_front_matter(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        if str(front_matter.get(field_name)) == slug:
            count += 1
    return count


def _count_books_db(entity_root: Path, kind: str, slug: str) -> int:
    db = entity_root / "books.db"
    if not db.is_file():
        return 0
    columns = _DB_COLUMNS.get(kind, ())
    total = 0
    connection = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        tables = [row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )]
        for table in tables:
            present = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
            for column in columns:
                if column in present:
                    total += connection.execute(
                        f"SELECT COUNT(*) FROM {table} WHERE {column} = ?", (slug,)
                    ).fetchone()[0]
    finally:
        connection.close()
    return total


def _count_workspaces(scope: Scope, kind: str, slug: str) -> int:
    cfg = yaml.safe_load(scope.system_path("workspaces.yaml").read_text()) or {}
    entity = scope.current_entity()
    return sum(
        1
        for entry in cfg.get("workspaces") or []
        if (entry or {}).get("entity", (entry or {}).get("primary_entity")) == entity
        and str((entry or {}).get(kind)) == slug
    )


def reference_count(scope: Scope, kind: str, slug: str) -> ReferenceReport:
    entity_root = scope.resolve()
    return ReferenceReport(kind, slug, {
        "front-matter": _count_front_matter(entity_root, kind, slug),
        "workspaces": _count_workspaces(scope, kind, slug),
        "books.db": _count_books_db(entity_root, kind, slug),
    })
```

Do not scan `scope.root.rglob(...)`. A cross-entity workspace is applicable only through its declared `primary_entity`; viewing inclusion does not authorize reasoning/counting in a secondary entity.

- [ ] **Step 5: Scope product reads and proposal records**

Move `_products_for` out of global state or rewrite it as `products_for(scope)`, reading only `cfg["products"][scope.current_entity()]`. Derive delete record `entity` and outbox path from the scope. After parsing a delete proposal, require `rec["entity"] == scope.current_entity()` before constructing the dataclass or recomputing references.

In `add_workspace`, require the entry's effective entity (`entity`, or `primary_entity` for a cross workspace) to equal `scope.current_entity()` before reading or writing the shared file. A bound entity cannot create a workspace owned by another entity.

- [ ] **Step 6: Remove only the bound registry value**

Replace the global textual slug search with structure-aware removal under the selected namespace:

```python
def _remove_scoped_registry_value(scope: Scope, kind: str, slug: str) -> tuple[Path, str]:
    filename = _REGISTRY_FILE.get(kind)
    if filename is None:
        raise RegistryError(f"delete not supported for kind {kind!r}")
    path = scope.system_path(filename)
    cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    registry = cfg.get(f"{kind}s") or {}
    values = registry.get(scope.current_entity())
    if isinstance(values, dict):
        if slug not in values:
            raise RegistryError(f"unknown {kind} {slug!r} in selected entity")
        del values[slug]
    elif isinstance(values, list):
        kept = [item for item in values if str((item or {}).get("id")) != slug]
        if len(kept) == len(values):
            raise RegistryError(f"unknown {kind} {slug!r} in selected entity")
        registry[scope.current_entity()] = kept
    else:
        raise RegistryError(f"selected entity has no {kind} registry")
    return path, yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True)
```

Write the returned text only after fresh reference count is zero and record scope agrees. Keep the existing single registry commit behavior; general rollback remains S5.

- [ ] **Step 7: Run focused and complete regressions**

Run:

```bash
uv run python -m pytest tests/test_registry.py tests/test_app.py -q
uv run python -m pytest -q
```

Expected: all registry isolation, forged-record, and existing CRUD tests pass.

- [ ] **Step 8: Commit registry isolation**

```bash
git add app/main.py app/registry.py tests/test_registry.py tests/test_app.py
git commit -m "fix: scope registry operations to entity"
```

---

### Task 4: Deterministic Shared-Mailbox Recipient Routing

**Files:**
- Modify: `app/entities.py`
- Modify: `app/ingest/base.py:1-260`
- Modify: `app/ingest/adapters/email.py:1-116`
- Modify: `app/ingest/adapters/folder.py` only for the shared-ingest signature migration
- Modify: `tests/conftest.py`
- Modify: `tests/test_ingest_commit.py:1-232`
- Modify: `tests/test_email_adapter.py:1-116`
- Modify: `tests/test_folder_adapter.py` only for the shared-ingest signature migration
- Modify: `tests/test_outbox.py` only for the shared-ingest signature migration

**Interfaces:**
- Extends: `EntityDefinition.email_addresses: tuple[str, ...]`
- Produces: `EntityCatalog.entity_for_recipient(address: str) -> str | None`
- Produces: `recipient_addresses(message: Message) -> frozenset[str]`
- Produces: `route_email_scope(root: Path | str, message: Message) -> Scope`
- Produces: `process_email(scope: Scope, message: Message, *, now: datetime | None = None) -> IngestResult`
- Produces: `process_shared_email(root: Path | str, message: Message, *, now: datetime | None = None) -> IngestResult`
- Produces: `poll(vault, host, user, password, mailbox="INBOX") -> int`; no entity parameter.
- Changes: `prepare_inbox_item(scope: Scope, **kwargs)`, `find_tracked_receipt(scope: Scope, envelope: Envelope)`, and `commit_inbox_item(scope: Scope, **kwargs)` derive entity from scope.

- [ ] **Step 1: Write failing manifest routing-configuration tests**

Add tests with only synthetic addresses:

```python
def test_manifest_normalizes_recipient_addresses_case_insensitively(tmp_path):
    write_vault(tmp_path, entities_yaml(
        "alpha", ingest={"alpha": [" Intake-Alpha@Example.Invalid "]}
    ))
    catalog = EntityCatalog.load(tmp_path)
    assert catalog.entity_for_recipient("intake-alpha@example.invalid") == "alpha"


@pytest.mark.parametrize("value", ["", "not-an-address", "a@", "@example.invalid"])
def test_manifest_rejects_malformed_recipient_address(tmp_path, value):
    write_vault(tmp_path, entities_yaml("alpha", ingest={"alpha": [value]}))
    with pytest.raises(RecipientConfigurationError):
        EntityCatalog.load(tmp_path)


def test_manifest_rejects_duplicate_normalized_address_ownership(tmp_path):
    write_vault(tmp_path, entities_yaml(
        "alpha", "beta", ingest={
            "alpha": ["shared@example.invalid"],
            "beta": ["SHARED@example.invalid"],
        },
    ))
    with pytest.raises(RecipientConfigurationError):
        EntityCatalog.load(tmp_path)
```

Also assert an entity with no `ingest` key remains valid for web and folder intake.

- [ ] **Step 2: Write failing recipient extraction and routing tests**

Cover all five header sources, display names, repeated fields, case normalization, and de-duplication:

```python
def test_recipient_parser_uses_only_approved_headers():
    message = _msg("body", to="Alpha <INTAKE-ALPHA@example.invalid>")
    message["Delivered-To"] = "intake-alpha@example.invalid"
    message["X-Original-To"] = "alias@example.invalid"
    message["Envelope-To"] = "Alias <alias@example.invalid>"
    message["Cc"] = "cc@example.invalid"
    message["From"] = "intake-beta@example.invalid"
    assert recipient_addresses(message) == frozenset({
        "intake-alpha@example.invalid", "alias@example.invalid", "cc@example.invalid"
    })


def test_shared_email_routes_to_exactly_one_entity(email_vault):
    result = process_shared_email(email_vault, _msg("alpha body", to="intake-alpha@example.invalid"))
    assert result.path.is_relative_to(email_vault / "alpha/00-inbox/active")
    assert not list((email_vault / "beta/00-inbox/active").glob("*.md"))
```

Prove sender and subject do not affect routing by putting beta's configured address in both while `To` names alpha.

- [ ] **Step 3: Write failing no-write/no-commit routing-error tests**

For unmapped recipients and recipients resolving to two entities, capture `HEAD`, tracked paths, and both inbox directory listings before the call:

```python
@pytest.mark.parametrize("recipients,error", [
    (["unknown@example.invalid"], UnmappedRecipientError),
    (["intake-alpha@example.invalid", "intake-beta@example.invalid"], AmbiguousRecipientError),
])
def test_routing_error_creates_no_receipt_or_commit(email_vault, recipients, error):
    before_head = git_head(email_vault)
    before_paths = git_tracked_paths(email_vault)
    message = _msg("must not be written", to=", ".join(recipients))
    with pytest.raises(error):
        process_shared_email(email_vault, message)
    assert git_head(email_vault) == before_head
    assert git_tracked_paths(email_vault) == before_paths
    assert not list(email_vault.glob("*/00-inbox/active/*.md"))
```

Add a poll-level test with a fake IMAP connection showing duplicate ownership is rejected before connecting/fetching. Add an ambiguous-message test showing one receipt per match is never attempted.

- [ ] **Step 4: Run email and ingest tests and verify preselected routing fails**

Run:

```bash
uv run python -m pytest tests/test_scope.py tests/test_ingest_commit.py tests/test_email_adapter.py -q
```

Expected: failures because manifest ingest addresses are ignored, `poll` requires a preselected entity, and `process_email` accepts a caller-selected entity without inspecting recipients.

- [ ] **Step 5: Parse and validate optional manifest email ownership**

Extend `EntityDefinition` and `EntityCatalog` with immutable normalized routes. The new fields have empty-tuple defaults so entities without email intake remain valid:

```python
@dataclass(frozen=True)
class EntityDefinition:
    slug: str
    label: str
    flags: tuple[str, ...]
    email_addresses: tuple[str, ...] = ()


@dataclass(frozen=True)
class EntityCatalog:
    root: Path
    entities: tuple[EntityDefinition, ...]
    recipient_routes: tuple[tuple[str, str], ...] = ()

    def entity_for_recipient(self, address: str) -> str | None:
        normalized = normalize_email_address(address)
        return dict(self.recipient_routes).get(normalized)
```

Use the standard-library address parser plus explicit non-empty local/domain checks:

```python
from email.utils import getaddresses


class RecipientConfigurationError(EntityManifestError):
    pass


def normalize_email_address(value: object) -> str:
    if not isinstance(value, str):
        raise RecipientConfigurationError("email routing address must be a string")
    parsed = getaddresses([value.strip()])
    if len(parsed) != 1:
        raise RecipientConfigurationError("email routing address must contain one address")
    address = parsed[0][1].strip().lower()
    local, separator, domain = address.rpartition("@")
    if separator != "@" or not local or not domain or any(ch.isspace() for ch in address):
        raise RecipientConfigurationError("email routing address is malformed")
    return address
```

While loading every entity, validate `ingest` is a mapping and `email_addresses` is a list. De-duplicate repeated normalized addresses owned by the same entity, then store them on the entity and as a tuple of `(address, slug)` routes on the catalog. If a normalized address appears under a second entity, raise `RecipientConfigurationError("email routing address has duplicate ownership")` without naming live addresses.

- [ ] **Step 6: Make the shared ingest path scope-derived**

Remove the entity argument from `render_note`, `prepare_inbox_item`, `_tracked_markdown_paths`, `find_tracked_receipt`, and `commit_inbox_item`. Keep their existing redaction, envelope, Git, cleanup, and collision bodies unchanged; apply these exact identity/path replacements:

```python
def prepare_inbox_item(
    scope: Scope,
    *,
    text: str,
    title: str,
    source: str,
    source_id: str,
    received_at: str,
    sender: str | None = None,
    thread_id: str | None = None,
    source_ref: str | None = None,
    body_ref: str | None = None,
    sha256: str | None = None,
    mime: str | None = None,
    size: int | None = None,
    attachments: list[str] | None = None,
    slug_seed: str | None = None,
) -> tuple[Path, Envelope, str]:
    entity = scope.current_entity()
    if not sha256:
        raise IngestRepositoryError("adapter receipt requires sha256")
    redacted, matches = redact(text)
    env = Envelope(
        source=source,
        source_id=source_id,
        thread_id=thread_id,
        sender=sender,
        received_at=received_at,
        title=title,
        summary=redacted[:SUMMARY_CHARS],
        attachments=attachments or [],
        source_ref=source_ref,
        body_ref=body_ref or source_ref,
        sha256=sha256,
        mime=mime,
        size=size,
        pii_quarantined=bool(matches),
        pii_classes=sorted({match.kind for match in matches}),
    )
    seed = (slug_seed or source_id or "item")[:8]
    note_path = scope.resolve("00-inbox", "active", f"{_slug(title)}-{seed}.md")
    return note_path, env, render_note(env, entity)


def _tracked_markdown_paths(scope: Scope) -> list[Path]:
    prefix = f"{scope.current_entity()}/"
    output = _git(scope, "ls-files", "--", prefix).stdout
    return [
        scope.root / relative
        for relative in output.splitlines()
        if Path(relative).suffix == ".md"
        and not {".sensitive", "outbox", "staging"}.intersection(Path(relative).parts)
        and (scope.root / relative).is_file()
    ]


def commit_inbox_item(scope: Scope, **kwargs) -> IngestResult:
    _require_git_head(scope)
    path, env, rendered = prepare_inbox_item(scope, **kwargs)
    existing = find_tracked_receipt(scope, env)
    if existing is not None:
        return IngestResult(existing, env, False, None)
```

After those exact identity-derived lines in `commit_inbox_item`, retain the existing statements from `rel = _relative(scope, path)` through the final `IngestResult`. Only remove the entity argument; do not refactor the S1 transaction or cleanup logic.

Update all existing adapter and S1 tests to bind `Scope(vault, "synthetic")`. Preserve commit message, duplicate semantics, exact changed-path assertions, cleanup behavior, and raw-content history checks.

- [ ] **Step 7: Implement deterministic message routing before ingestion**

Use exactly the approved headers:

```python
_RECIPIENT_HEADERS = ("Delivered-To", "X-Original-To", "Envelope-To", "To", "Cc")


class EmailRoutingError(IngestError):
    pass


class UnmappedRecipientError(EmailRoutingError):
    pass


class AmbiguousRecipientError(EmailRoutingError):
    pass


def recipient_addresses(message: Message) -> frozenset[str]:
    values = [value for name in _RECIPIENT_HEADERS for value in message.get_all(name, [])]
    addresses: set[str] = set()
    for _display, raw in getaddresses(values):
        try:
            addresses.add(normalize_email_address(raw))
        except RecipientConfigurationError:
            continue
    return frozenset(addresses)


def route_email_scope(root: Path | str, message: Message) -> Scope:
    catalog = EntityCatalog.load(root)
    matches = {
        entity
        for address in recipient_addresses(message)
        if (entity := catalog.entity_for_recipient(address)) is not None
    }
    if not matches:
        raise UnmappedRecipientError("email has no configured entity recipient")
    if len(matches) != 1:
        raise AmbiguousRecipientError("email recipients map to multiple entities")
    return Scope(catalog.root, matches.pop())


def process_shared_email(root: Path | str, message: Message, *, now=None) -> IngestResult:
    return process_email(route_email_scope(root, message), message, now=now)
```

`process_email(scope, message)` calls `commit_inbox_item(scope, ...)`; it accepts no vault or entity argument. `poll` loads/validates `EntityCatalog` before opening IMAP, removes its entity parameter, and calls `process_shared_email` for every fetched message. It never guesses after a routing error.

- [ ] **Step 8: Run focused and complete regressions**

Run:

```bash
uv run python -m pytest tests/test_scope.py tests/test_ingest_commit.py tests/test_email_adapter.py tests/test_folder_adapter.py tests/test_outbox.py -q
uv run python -m pytest -q
```

Expected: routing, configuration, S1 idempotency/redaction, approval-revert, and full public tests pass.

- [ ] **Step 9: Commit deterministic email routing**

```bash
git add app/entities.py app/ingest/base.py app/ingest/adapters/email.py app/ingest/adapters/folder.py tests/conftest.py tests/test_scope.py tests/test_ingest_commit.py tests/test_email_adapter.py tests/test_folder_adapter.py tests/test_outbox.py
git commit -m "fix: route email by manifest recipient"
```

---

### Task 5: Folder Watcher Validation and Final Interface Mutation Checks

**Files:**
- Modify: `app/ingest/adapters/folder.py:1-160`
- Modify: `tests/test_folder_adapter.py:1-197`
- Modify: `tests/test_ingest_commit.py`
- Modify: `tests/test_email_adapter.py`
- Modify: `tests/test_outbox.py`
- Modify: `tests/test_scope.py`

**Interfaces:**
- Consumes: `Scope(root, entity)` and scope-derived shared ingest from Tasks 1 and 4.
- Produces: `process_drop(scope: Scope, source: Path | str, *, raw_archive: Path | str, now: datetime | None = None) -> IngestResult`
- Produces: `watch(vault: Path | str, entity: str, dropbox: Path | str, raw_archive: Path | str) -> None`; the watcher configuration is validated once before it creates directories or starts observing.

- [ ] **Step 1: Write failing unknown-watcher preservation tests**

Add a registered alpha entity and a real directory-only/unknown beta. Capture source bytes, raw-archive absence, and Git HEAD:

```python
def test_unknown_folder_entity_is_rejected_before_source_move(tmp_path):
    vault = git_entity_vault(
        tmp_path / "vault", ("alpha",), {"alpha/00-inbox/active/.gitkeep": ""}
    )
    (vault / "directory-only").mkdir()
    source = tmp_path / "drop/item.txt"
    source.parent.mkdir()
    source.write_bytes(b"source bytes stay here\n")
    raw = tmp_path / "raw"
    before = git_head(vault)

    with pytest.raises(EntitySelectionError):
        process_drop(Scope(vault, "directory-only"), source, raw_archive=raw)

    assert source.read_bytes() == b"source bytes stay here\n"
    assert not raw.exists()
    assert git_head(vault) == before
```

Because direct `Scope` construction fails before `process_drop` is entered, also test `watch(...)` with a monkeypatched observer and assert neither `dropbox.mkdir` nor observer construction runs for an unknown entity.

- [ ] **Step 2: Write failing adapter interface mutation tests**

Assert entity-sensitive helpers expose one identity authority:

```python
def test_entity_sensitive_interfaces_take_only_bound_scope():
    checks = (
        read_inbox,
        load_proposals,
        approve,
        reject,
        reference_count,
        propose_delete,
        execute_delete,
        prepare_inbox_item,
        find_tracked_receipt,
        commit_inbox_item,
        process_email,
        process_drop,
    )
    for function in checks:
        parameters = inspect.signature(function).parameters
        assert "entity" not in parameters
        assert "scope" in parameters
```

Add source-level mutation checks with `ast` rather than string matching: `app.main` has no assignment named `scope`, `Scope` has no method named `set_current_entity`, and no service call supplies both `scope` and a separate entity identity.

- [ ] **Step 3: Run folder/scope tests and verify the optional scope API fails**

Run:

```bash
uv run python -m pytest tests/test_scope.py tests/test_folder_adapter.py tests/test_ingest_commit.py -q
```

Expected: failures while `process_drop` still accepts `vault`, `entity`, and optional `scope`, and `watch` validates only after a source event.

- [ ] **Step 4: Make folder processing consume only a bound scope**

Change the adapter boundary to:

```python
def process_drop(
    scope: Scope,
    source: Path | str,
    *,
    raw_archive: Path | str,
    now: datetime | None = None,
) -> IngestResult:
    source_path = Path(source)
    archive_root = Path(raw_archive)
    now = now or datetime.now()
    digest = sha256_of(source_path)
    size = source_path.stat().st_size
    mime = mime_of(source_path)
    text = extract_text(source_path)
    archived = archive_root / f"{digest[:16]}-{source_path.name}"
    source_ref = f"raw:{archived.name}"
    kwargs = {
        "text": text,
        "title": source_path.name,
        "source": "folder",
        "source_id": digest[:16],
        "received_at": now.isoformat(timespec="seconds"),
        "source_ref": source_ref,
        "body_ref": source_ref,
        "sha256": digest,
        "mime": mime,
        "size": size,
        "slug_seed": digest,
    }
    _path, envelope, _rendered = prepare_inbox_item(scope, **kwargs)
    existing = find_tracked_receipt(scope, envelope)
    if existing is not None:
        return IngestResult(existing, envelope, False, None)
    if archived.exists():
        raise IngestPathCollision("raw archive destination already exists")
    archive_root.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source_path), str(archived))
    try:
        result = commit_inbox_item(scope, **kwargs)
    except IngestError as exc:
        try:
            _restore_raw(archived, source_path, "receipt commit failed")
        except FolderSourceRestoreError as restore_exc:
            raise restore_exc from exc
        raise
    if not result.created:
        _restore_raw(archived, source_path, "duplicate detected after archive")
    return result
```

Update every caller and test to construct `Scope(vault, synthetic_slug)` first. Remove the optional-scope fallback and all duplicated vault/entity parameters.

After the last domain has dropped its entity parameter, remove the transitional `Scope.require_entity()` method added in Task 1. Extend the AST mutation test with `assert not hasattr(Scope, "require_entity")` so no compatibility identity channel survives S2.

- [ ] **Step 5: Validate watcher configuration before any side effect**

At the top of `watch`, bind once:

```python
def watch(vault, entity, dropbox, raw_archive) -> None:
    scope = Scope(vault, entity)
    dropbox_path = Path(dropbox)
    dropbox_path.mkdir(parents=True, exist_ok=True)

    class _Handler(FileSystemEventHandler):
        def on_created(self, event):
            if not event.is_directory:
                process_drop(scope, event.src_path, raw_archive=raw_archive)

    observer = Observer()
    observer.schedule(_Handler(), str(dropbox_path), recursive=False)
    observer.start()
    try:
        while True:
            observer.join(1)
    finally:
        observer.stop()
        observer.join()
```

The configured entity is an adapter input only at the watcher factory boundary; all source events reuse the immutable validated scope. No source hash/read/archive/move happens before binding succeeds.

- [ ] **Step 6: Re-run S1 and adapter regressions**

Run:

```bash
uv run python -m pytest tests/test_ingest_commit.py tests/test_folder_adapter.py tests/test_email_adapter.py tests/test_outbox.py -q
uv run python -m pytest -q
```

Expected: S1 commit isolation, duplicate no-op, raw restoration, email routing, approval revert, and all interface mutation checks pass.

- [ ] **Step 7: Commit folder validation and final interfaces**

```bash
git add app/scope.py app/ingest/adapters/folder.py tests/test_scope.py tests/test_folder_adapter.py tests/test_ingest_commit.py tests/test_email_adapter.py tests/test_outbox.py
git commit -m "fix: validate folder intake scope"
```

---

### Task 6: Bounded S2 Review and Complete Safety Verification

**Files:**
- Modify only if an actionable S2 correctness/safety finding requires a red-green fix: files already listed in Tasks 1-5.
- Do not modify Grey Matter.

**Interfaces:**
- Consumes: completed S2 implementation and all tests from Tasks 1-5.
- Produces: one bounded review result focused on request concurrency, manifest validation, cross-scope stored records, registry leakage, recipient ambiguity, folder prevalidation, and S1 regressions.

- [ ] **Step 1: Confirm public worktree scope before review**

Run:

```bash
git status --short
git diff --check
git log --oneline --decorate -8
git diff 2358fdf --stat
```

Expected: only S2 public implementation/test/plan files changed since `2358fdf`; no instance values, S3-S6 implementation, generated caches, or private files are present.

- [ ] **Step 2: Perform the bounded S2 correctness and safety review**

Review these mutations explicitly:

```text
1. Replace a request dependency's fresh Scope with one shared object: concurrency tests fail.
2. Let Scope accept a directory-only entity: manifest-selection tests fail.
3. Add an entity argument back to a service and trust it: interface/cross-scope tests fail.
4. Load a proposal whose record or stored path names another entity: outbox tests fail before source read.
5. Scan scope.root for Markdown or books.db: registry isolation tests fail.
6. Route an unmatched message to the first entity: no-write routing test fails.
7. Permit duplicate normalized email ownership: manifest configuration test fails.
8. Move a folder source before binding its configured entity: source-preservation test fails.
```

Classify module/sub/block destination validation as S3, proposal freshness/id collision as S4, general Git rollback/path policy as S5, and user-visible safe errors as S6. Record such findings for later; do not implement them.

- [ ] **Step 3: Apply at most one TDD fix pass for actionable S2 findings**

For each actionable S2 finding, first add the smallest synthetic failing test to the owning test file, run that single node and observe the safety failure, implement the minimal S2-only correction, then re-run its focused file. If the review has no actionable S2 findings, make no source change and create no review-only commit.

- [ ] **Step 4: Run the public S2 verification groups**

Run exactly:

```bash
uv run python -m pytest tests/test_scope.py -q
uv run python -m pytest tests/test_app.py tests/test_outbox.py tests/test_registry.py -q
uv run python -m pytest tests/test_folder_adapter.py tests/test_email_adapter.py -q
uv run python -m pytest -q
tools/run_gitleaks.sh .
uv run python -m tools.public_repo_audit --repo . --history
```

Expected: every pytest group passes, the complete count is greater than 141, and both public leakage/history checks pass.

- [ ] **Step 5: Snapshot Grey Matter immediately before private checks**

Write both snapshots outside the repositories:

```bash
git -C "$ONEOS_VAULT" status --porcelain=v2 --untracked-files=all > /private/tmp/oneos-s2-gm-status.precheck
git -C "$ONEOS_VAULT" diff --binary HEAD --output=/private/tmp/oneos-s2-gm-diff.precheck
git -C "$ONEOS_VAULT" log --oneline -1
```

Expected: HEAD is `2aa8b14` or later. Do not stage, stash, clean, reset, checkout, commit, or edit any Grey Matter file.

- [ ] **Step 6: Run private read-only integration gates**

Run exactly:

```bash
(cd "$ONEOS_VAULT/_system/scripts" && python3 -m unittest discover -q)
python3 "$ONEOS_VAULT/_system/scripts/check_v2.py" "$ONEOS_VAULT"
python3 "$ONEOS_VAULT/_system/scripts/policy_enforcer.py" \
  --policy "$ONEOS_VAULT/_system/scripts/action-policy.yaml" test-suite
uv run python -m tools.public_repo_audit --repo . --vault "$ONEOS_VAULT" --history
```

Expected: 34 or more private script tests pass, `check_v2` reports zero errors and zero warnings, policy enforcement passes, and the combined public/private audit finds no leaked instance values.

- [ ] **Step 7: Compare Grey Matter byte-for-byte immediately after checks**

Run:

```bash
git -C "$ONEOS_VAULT" status --porcelain=v2 --untracked-files=all > /private/tmp/oneos-s2-gm-status.postcheck
git -C "$ONEOS_VAULT" diff --binary HEAD --output=/private/tmp/oneos-s2-gm-diff.postcheck
cmp /private/tmp/oneos-s2-gm-status.precheck /private/tmp/oneos-s2-gm-status.postcheck
cmp /private/tmp/oneos-s2-gm-diff.precheck /private/tmp/oneos-s2-gm-diff.postcheck
```

Expected: both `cmp` commands exit zero. Any mismatch is a hard failure; stop and restore only if the exact S2-created bytes are known. Never overwrite pre-existing Grey Matter edits.

- [ ] **Step 8: Run final public repository checks**

Run:

```bash
git diff --check
git status --short --branch
git log --oneline --decorate -8
git diff 2358fdf --name-only
```

Expected: clean worktree on `codex/s2-request-local-scope`; changes are S2-only. Do not push, merge, or open a pull request.

- [ ] **Step 9: Commit a review fix only when Step 3 changed code**

If Step 3 produced an actionable S2 fix and all complete gates passed:

```bash
git status --short
git add app/entities.py app/scope.py app/config.py app/vault.py app/classifier.py app/main.py app/inbox.py app/outbox.py app/registry.py app/ingest/base.py app/ingest/adapters/email.py app/ingest/adapters/folder.py tests/test_scope.py tests/test_app.py tests/test_outbox.py tests/test_registry.py tests/test_ingest_commit.py tests/test_email_adapter.py tests/test_folder_adapter.py
git commit -m "fix: close S2 scope review findings"
```

If Step 3 changed nothing, skip this commit. Report final public test counts, private test counts, audit results, Grey Matter snapshot equality, commit list, and deferred S3-S6 observations without performing integration actions.
