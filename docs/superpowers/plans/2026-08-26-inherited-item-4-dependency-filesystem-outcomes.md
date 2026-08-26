# Inherited Item 4 — Dependency Filesystem Outcomes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render a lost configured vault root as `E-TAMPER` and an unreadable entity manifest as `E-CONFIG` across every entity-scoped Console endpoint without reaching the global fallback or mutating the vault.

**Architecture:** Normalize both filesystem failures at their domain boundaries, map only the new root exception to the existing taxonomy, and answer dependency-time failures with typed FastAPI handlers. Route-body catches remain independently pinned even where an application handler would render the same outcome.

**Tech Stack:** Python 3.12, FastAPI/Starlette exception handlers, pathlib, Jinja/HTMX, pytest.

**Spec:** `docs/superpowers/specs/2026-08-26-inherited-safety-items-2-4-design.md`

## Global Constraints

- Start only after Item 2 is merged and a freshly fetched merged `origin/main` full suite passes.
- Use a fresh task, worktree, and `codex/` branch for Item 4 only.
- Public repository and synthetic fixtures only; no live vault or private values.
- Reuse existing `E-TAMPER` and `E-CONFIG` copy verbatim. Add no taxonomy code, dependency, repair action, symlink, or automatic relocation.
- Exception messages are constant and must not contain `ONEOS_VAULT`, its value, a manifest path, an OS error, or submitted data.
- Preserve every lower route catch already required for body-time failures.
- Do not push, open a pull request, merge, delete a branch, remove a worktree, or run private gates without separate authorization.
- Stop on dependency, convention, schema, security-boundary, destructive-action, deployment, private-material, or unresolved-product changes.
- Independent review and mutation RED→GREEN evidence are mandatory.

## Execution Preconditions

```bash
git fetch origin
BASE_SHA="$(git rev-parse origin/main)"
WORKTREE="$(dirname "$(git rev-parse --show-toplevel)")/oneos-inherited-item-4"
git worktree add "$WORKTREE" -b codex/inherited-item-4-filesystem-outcomes "$BASE_SHA"
cd "$WORKTREE"
test "$(git rev-parse HEAD)" = "$BASE_SHA"
test -z "$(git status --porcelain)"
uv run python -m pytest -q
```

The baseline must be green and include Item 2. Otherwise stop.

### Task 1: Type the configured-root loss

**Files:**
- Create: `tests/test_config.py`
- Modify: `app/config.py:13-36`

**Interfaces:**
- Produces: `class VaultRootUnavailable(RuntimeError)` and unchanged `vault_root() -> Path`.
- Consumes later: exact taxonomy mapping and FastAPI handler.

- [ ] **Step 1: Write RED unit tests**

```python
from pathlib import Path

import pytest

from app.config import ENV_VAULT, VaultRootUnavailable, vault_root


def test_configured_vault_that_disappears_raises_safe_typed_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    missing = tmp_path / "configured-root-that-moved"
    monkeypatch.setenv(ENV_VAULT, str(missing))

    with pytest.raises(VaultRootUnavailable) as raised:
        vault_root()

    assert str(raised.value) == "configured vault root is unavailable"
    assert str(missing) not in str(raised.value)


def test_unset_vault_remains_a_startup_configuration_error(monkeypatch):
    monkeypatch.delenv(ENV_VAULT, raising=False)

    with pytest.raises(RuntimeError) as raised:
        vault_root()

    assert not isinstance(raised.value, VaultRootUnavailable)
```

- [ ] **Step 2: Confirm RED**

```bash
uv run python -m pytest -q tests/test_config.py
```

Expected: collection fails because `VaultRootUnavailable` does not exist.

- [ ] **Step 3: Implement the narrow exception**

```python
class VaultRootUnavailable(RuntimeError):
    """A configured vault root is no longer available at request time."""


def vault_root() -> Path:
    raw = os.environ.get(ENV_VAULT)
    if not raw:
        raise RuntimeError(
            f"{ENV_VAULT} is not set — point it at the vault root before starting."
        )
    root = Path(raw).expanduser()
    if not root.is_dir():
        raise VaultRootUnavailable("configured vault root is unavailable")
    return root
```

- [ ] **Step 4: Run GREEN and commit**

```bash
uv run python -m pytest -q tests/test_config.py
git add app/config.py tests/test_config.py
git commit -m "feat: type unavailable configured vault roots"
```

### Task 2: Normalize manifest read failures

**Files:**
- Create: `tests/test_entities.py`
- Modify: `app/entities.py:78-88`

**Interfaces:**
- Preserves: `EntityCatalog.load(root) -> EntityCatalog`.
- Produces: every `OSError` from `entities.yaml` reading becomes `EntityManifestError("entities manifest could not be read")`.

- [ ] **Step 1: Write deterministic RED coverage**

Create a minimal synthetic `_system/entities.yaml`, patch `Path.read_text` only for that exact path to raise `PermissionError("private marker")`, and assert:

```python
with pytest.raises(EntityManifestError) as raised:
    EntityCatalog.load(vault)

assert type(raised.value) is EntityManifestError
assert str(raised.value) == "entities manifest could not be read"
assert "private marker" not in str(raised.value)
assert str(manifest) not in str(raised.value)
```

Add a positive control proving the patched reader fired.

- [ ] **Step 2: Confirm RED**

```bash
uv run python -m pytest -q tests/test_entities.py
```

Expected: bare `PermissionError` escapes.

- [ ] **Step 3: Separate I/O and YAML conversion**

```python
try:
    text = path.read_text(encoding="utf-8")
except OSError as exc:
    raise EntityManifestError("entities manifest could not be read") from exc
try:
    cfg = yaml.safe_load(text) or {}
except yaml.YAMLError as exc:
    raise EntityManifestError("entities manifest is invalid YAML") from exc
```

- [ ] **Step 4: Run GREEN and commit**

```bash
uv run python -m pytest -q tests/test_entities.py tests/test_app.py tests/test_email_adapter.py
git add app/entities.py tests/test_entities.py
git commit -m "fix: normalize unreadable entity manifests"
```

### Task 3: Map and render the root exception at dependency time

**Files:**
- Modify: `app/console_errors.py:285-355`
- Modify: `app/main.py:20-135,285-335`
- Modify: `tests/test_console_errors.py:275-305`
- Modify: `tests/test_console_invariants.py:150-215`
- Modify: `tests/test_console_routes.py:4070-4565`

**Interfaces:**
- Consumes: `VaultRootUnavailable`.
- Produces: exact `VaultRootUnavailable -> E-TAMPER` mapping and typed FastAPI handler.

- [ ] **Step 1: Add RED mapping and design-map assertions**

```python
def test_map_VaultRootUnavailable():
    from app.config import VaultRootUnavailable

    assert _code_of(VaultRootUnavailable("probe")) == "E-TAMPER"
```

Add `config.VaultRootUnavailable: "E-TAMPER"` to the transcribed class map in `tests/test_console_invariants.py`.

- [ ] **Step 2: Add RED dependency-route matrix**

Derive the affected endpoints from FastAPI's registered routes and the actual
`EntityScope`/`entity_scope` dependency; do not maintain a second list of
names. Reuse the request callables in `_route_totality_plan(main)` for those
endpoints. For every endpoint accepting form or query input, add a unique
synthetic sentinel to that request. Start from a valid synthetic vault, import
the app, rename the whole root to a sibling path, then request each affected
endpoint with `raise_server_exceptions=False`.

For every response assert:

```python
assert response.status_code == _CODES["E-TAMPER"].page_status
assert "E-TAMPER" in response.text
assert str(vault) not in response.text
assert "configured vault root is unavailable" not in response.text
assert "hx-post" not in response.text
assert "review_sha256" not in response.text
if submitted_sentinel is not None:
    assert submitted_sentinel not in response.text
assert reached_global_fallback == []
```

Restore the root in `finally` and compare the moved tree's pre/post `_fs_snapshot` exactly.

- [ ] **Step 3: Confirm RED**

Run the new mapping and root-loss nodes. Expected: `E-UNKNOWN` and global-fallback spy activity.

- [ ] **Step 4: Add the exact map and handler**

In `app/console_errors.py`, import config as `_config` and add:

```python
_config.VaultRootUnavailable: _CODES["E-TAMPER"],
```

to `_EXACT`.

In `app/main.py`, import `VaultRootUnavailable` and register:

```python
@app.exception_handler(VaultRootUnavailable)
async def _vault_root_unavailable_handler(
    request: Request, exc: VaultRootUnavailable
) -> HTMLResponse:
    return _render_console_error(request, describe(exc))
```

Do not reuse the global handler and do not render `str(exc)`.

- [ ] **Step 5: Add the post-startup manifest-permission route matrix**

Use the same derived endpoint set and the same per-input-endpoint unique form or
query sentinels. After app import, remove read permission from
`_system/entities.yaml`; if the executing account can still read it, mark only
the real-permission variant skipped with an explicit privilege reason. Always
run the deterministic `Path.read_text` denial variant. Assert `E-CONFIG`, status
500, no global fallback, no raw marker/path, no submitted sentinel, no
`hx-post`, and unchanged `_fs_snapshot`. Restore the original mode in
`finally`.

- [ ] **Step 6: Run focused GREEN and commit**

```bash
uv run python -m pytest -q \
  tests/test_config.py tests/test_entities.py tests/test_console_errors.py \
  tests/test_console_invariants.py tests/test_console_routes.py
git add app/config.py app/entities.py app/console_errors.py app/main.py \
  tests/test_config.py tests/test_entities.py tests/test_console_errors.py \
  tests/test_console_invariants.py tests/test_console_routes.py
git commit -m "fix: render dependency filesystem failures safely"
```

### Task 4: Mutation proof and public handoff

**Files:**
- Modify: `docs/STATUS.md:234-244`
- Verify: all Item 4 product and test files.

**Interfaces:**
- Produces: five named mutation results and public completion wording that reserves private gates.

- [ ] **Step 1: Run five independent mutations**

For each mutation, save a pre-image outside the repo, alter one protection, run the named node, restore, `cmp` the file, and rerun GREEN:

1. `VaultRootUnavailable` → `RuntimeError` at the missing-root raise; root matrix must fail on E-TAMPER/global-fallback assertions.
2. Delete the `VaultRootUnavailable` handler; root matrix must fail because the global fallback spy fires.
3. Delete the existing `EntityManifestError` application handler; the manifest
   route matrix must fail because the global fallback spy fires.
4. Remove the `except OSError` conversion in `EntityCatalog.load`; deterministic manifest test must fail with bare `PermissionError`.
5. Remove `SystemRegistryPathError` from one lower body catch such as `_OUTBOX_CATCHES`; existing `test_route_tuples_still_answer_the_leaf_redirect_without_the_dependency_handler` must fail.

- [ ] **Step 2: Update status without claiming local gates**

Change the Item 4 heading to:

```markdown
**4. Remaining filesystem failure shapes — PUBLIC IMPLEMENTATION COMPLETE.**
```

State the two exact outcome mappings and that trusted-local private gates remain outstanding.

- [ ] **Step 3: Run the full public suite**

```bash
uv run python -m pytest -q
```

Expected: PASS.

- [ ] **Step 4: Commit the status record**

```bash
git add docs/STATUS.md
git commit -m "docs: record public item 4 filesystem outcomes"
```

- [ ] **Step 5: Run publication gates on the committed tree**

```bash
uv run python -m tools.public_repo_audit --repo . --history
tools/run_gitleaks.sh .
git diff --check
git status --porcelain
```

Expected: both audits clean; diff check clean; final status empty.

## External-Agent Handoff

Return base SHA, branch/worktree, commits, the derived affected-endpoint names, focused RED and GREEN commands, all four mutation results, full public count, audits, diff check, and clean status. Explicitly state that the live vault and private gates were not accessed.

The trusted local reviewer independently reruns every claim, performs the private 37 tests, `check_v2`, combined vault-seeded audit, and opaque preservation comparison, and decides whether the branch is eligible for push/PR/merge authorization.
