# OneOS Canonical Naming Cutover Implementation Plan

> **Historical execution plan:** the canonical naming cutover is complete.
> Retain this file for migration rationale; do not rerun its private/public
> rename commands or historical test-count instructions.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make OneOS the sole active product name across the application and private system material while preserving Grey Matter, Command Center, and Hermes as component names.

**Architecture:** Perform one atomic private-vault migration followed by one public-application migration. Each repository gets its own naming regression test and commit so tool identifiers, policy identifiers, imports, documentation, and callers cannot drift.

**Tech Stack:** Python 3.12, pytest, unittest, FastAPI, YAML, Markdown, Git, `uv`, POSIX symlink paths.

## Global Constraints

- OneOS is the sole active product and UI name.
- Grey Matter, Command Center, and Hermes remain component names, not alternative product brands.
- The archived adoption guide remains historical research and is not implementation authority.
- No application behavior, schema, roadmap phase, or dependency changes.
- Do not touch unrelated user edits in the private vault.
- Do not put any real entity, product, member, absolute vault path, credential, or GitHub owner in the public repository.
- Use `$ONEOS_VAULT` for the runtime vault location and `~/code/oneos` for the canonical application checkout.
- Internal actor, tool, caller, policy, test, and documentation renames are atomic within the private-vault commit.
- Public and private repository changes are committed separately.

---

## File map

### Private Grey Matter repository

- Rename `_system/scripts/lifeos_wizard.py` to `_system/scripts/oneos_wizard.py`: canonical entity scaffold and flag/module resolver.
- Rename `_system/scripts/lifeos_fs_mcp.py` to `_system/scripts/oneos_fs_mcp.py`: scoped Hermes filesystem MCP service.
- Rename `_system/docs/oneos-web-spec.md` to `_system/docs/oneos-spec.md`: canonical implementation authority.
- Create `_system/scripts/test_oneos_naming.py`: active-system naming regression.
- Modify `_system/scripts/check_v2.py`, `_system/scripts/test_registries.py`, `_system/scripts/test_archetypes.py`: import and documentation reconciliation.
- Modify `_system/scripts/action-policy.yaml`: canonical Hermes actor identifier.
- Modify `_system/scripts/init_books_db.py` and `_system/scripts/policy_enforcer.py`: CLI descriptions and module documentation.
- Modify `_system/hermes-context.md`, `_system/session-findings.md`, `_system/conventions-v2.1-additions.md`: active system language and references.
- Modify `_system/docs/HANDOFF.md`, `_system/docs/GETTING-STARTED.md`, `_system/docs/RECONCILIATION.md`, `_system/docs/hermes-capability-map.md`, `_system/docs/oneos-knowledge-layer.md`: canonical names, paths, and specification references.
- Modify `_system/docs/lifeos-adoption-guide-legacy.md`: update only the live authority link; retain its historical terminology and archive notice.

### Public application repository

- Create `tests/test_naming.py`: public product/package naming regression.
- Modify `pyproject.toml` and `uv.lock`: project name `oneos`.
- Modify `app/main.py`, `app/__init__.py`, `static/app.css`: product title and source labels.
- Modify `app/vault.py`, `app/rename.py`, `tests/test_rename.py`: canonical private tool filename references.
- Modify `AGENTS.md`, `BUILD.md`, `PRODUCT-THESIS.md`, `docs/STATUS.md`: OneOS-only guidance, `$ONEOS_VAULT`, `~/code/oneos`, and `oneos-spec.md`.
- Modify `docs/superpowers/specs/2026-08-07-oneos-canonical-naming-and-github-design.md`: mark the design implemented after verification; its explicit deprecated-name discussion remains a process-record exception.

---

### Task 1: Add the private active-system naming regression

**Files:**
- Create: `$ONEOS_VAULT/_system/scripts/test_oneos_naming.py`

**Interfaces:**
- Consumes: `$ONEOS_VAULT` and the existing unittest discovery command.
- Produces: `OneOSNamingTests.test_active_system_uses_oneos_names()`; later private tasks must make it pass.

- [ ] **Step 1: Confirm and preserve unrelated vault edits**

Run:

```bash
git -C "$ONEOS_VAULT" status --short
```

Expected: the two pre-existing `decisions.md` edits may appear. Record their paths and do not stage or modify them.

- [ ] **Step 2: Write the failing naming test**

Create the test with dynamically assembled deprecated labels so the test source does not itself trigger its scan:

```python
#!/usr/bin/env python3
import unittest
from pathlib import Path

SYSTEM = Path(__file__).resolve().parent.parent
DEPRECATED_TEXT = (
    "Life" + "OS",
    "OneOS " + "Web",
    "OneOS " + "Console",
    "oneos-" + "web",
    "lifeos" + "_",
    "hermes:lifeos" + "-fs-mcp",
)
TEXT_SUFFIXES = {".md", ".py", ".yaml", ".yml"}
EXCLUDED = {SYSTEM / "docs" / ("lifeos" + "-adoption-guide-legacy.md")}


class OneOSNamingTests(unittest.TestCase):
    def test_active_system_uses_oneos_names(self):
        findings = []
        for path in sorted(SYSTEM.rglob("*")):
            if not path.is_file() or path.suffix not in TEXT_SUFFIXES or path in EXCLUDED:
                continue
            text = path.read_text(encoding="utf-8")
            for term in DEPRECATED_TEXT:
                if term.casefold() in text.casefold() or term.casefold() in path.name.casefold():
                    findings.append(f"{path.relative_to(SYSTEM)}: {term}")
        self.assertEqual([], findings, "active deprecated names:\n" + "\n".join(findings))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run the new test and verify it fails**

Run:

```bash
cd "$ONEOS_VAULT/_system/scripts"
python3 -m unittest test_oneos_naming -v
```

Expected: FAIL with findings in active scripts and documents; the archived adoption guide is absent from the findings.

---

### Task 2: Rename private tools, actor identifiers, and imports atomically

**Files:**
- Rename: `$ONEOS_VAULT/_system/scripts/lifeos_wizard.py` → `$ONEOS_VAULT/_system/scripts/oneos_wizard.py`
- Rename: `$ONEOS_VAULT/_system/scripts/lifeos_fs_mcp.py` → `$ONEOS_VAULT/_system/scripts/oneos_fs_mcp.py`
- Modify: `$ONEOS_VAULT/_system/scripts/oneos_fs_mcp.py`
- Modify: `$ONEOS_VAULT/_system/scripts/oneos_wizard.py`
- Modify: `$ONEOS_VAULT/_system/scripts/check_v2.py`
- Modify: `$ONEOS_VAULT/_system/scripts/test_registries.py`
- Modify: `$ONEOS_VAULT/_system/scripts/test_archetypes.py`
- Modify: `$ONEOS_VAULT/_system/scripts/action-policy.yaml`
- Modify: `$ONEOS_VAULT/_system/scripts/init_books_db.py`
- Modify: `$ONEOS_VAULT/_system/scripts/policy_enforcer.py`

**Interfaces:**
- Consumes: existing `resolve_flags()` and `resolve_modules()` signatures without behavior changes.
- Produces: module `oneos_wizard`; actor `hermes:oneos-fs-mcp`; MCP service `oneos-fs`; tools `oneos_read_file`, `oneos_list_dir`, and `oneos_write_file`.

- [ ] **Step 1: Rename the two private script files**

Use filesystem renames without copying content:

```bash
mv "$ONEOS_VAULT/_system/scripts/lifeos_wizard.py" "$ONEOS_VAULT/_system/scripts/oneos_wizard.py"
mv "$ONEOS_VAULT/_system/scripts/lifeos_fs_mcp.py" "$ONEOS_VAULT/_system/scripts/oneos_fs_mcp.py"
```

- [ ] **Step 2: Update the wizard imports and descriptions**

Make these exact import substitutions:

```python
from oneos_wizard import resolve_flags, resolve_modules  # noqa: E402
```

Use `oneos_wizard.py` in its module docstring and usage examples. Change the CLI description to:

```python
parser = argparse.ArgumentParser(description="OneOS entity scaffold wizard (v2)")
```

- [ ] **Step 3: Update the MCP identity without changing safety behavior**

Use these exact public identifiers in `oneos_fs_mcp.py`:

```python
ACTOR_ID = "hermes:oneos-fs-mcp"
mcp = FastMCP("oneos-fs")

@mcp.tool()
def oneos_read_file(path: str) -> str:
    ...

@mcp.tool()
def oneos_list_dir(path: str = ".") -> str:
    ...

@mcp.tool()
def oneos_write_file(path: str, content: str) -> str:
    ...
```

Only rename the module, service, actor, and three tool functions. Preserve path canonicalization, `.sensitive/` denial, policy calls, validation, logging, and return behavior byte-for-byte otherwise.

- [ ] **Step 4: Update the matching policy actor atomically**

Change only the actor key:

```yaml
hermes:oneos-fs-mcp:
  tier_cap: gated
```

Do not alter any `allow`, `except`, or `deny` path. In particular, preserve the paired broad read path and `.sensitive/**` exception unchanged.

- [ ] **Step 5: Update remaining active script descriptions**

Use these exact descriptions:

```python
parser = argparse.ArgumentParser(description="OneOS books.db initializer")
parser = argparse.ArgumentParser(description="OneOS policy enforcer")
```

Change the policy enforcer module docstring to “Deny-by-default enforcement layer for OneOS”.

- [ ] **Step 6: Compile and run focused private tests**

Run:

```bash
python3 -m py_compile \
  "$ONEOS_VAULT/_system/scripts/oneos_wizard.py" \
  "$ONEOS_VAULT/_system/scripts/oneos_fs_mcp.py" \
  "$ONEOS_VAULT/_system/scripts/check_v2.py"
cd "$ONEOS_VAULT/_system/scripts"
python3 -m unittest test_archetypes test_registries test_policy_enforcer test_init_books_db -v
```

Expected: compilation succeeds and all focused tests pass. The naming test still fails because active documentation has not yet been reconciled.

---

### Task 3: Rename the canonical private specification and active guidance

**Files:**
- Rename: `$ONEOS_VAULT/_system/docs/oneos-web-spec.md` → `$ONEOS_VAULT/_system/docs/oneos-spec.md`
- Modify: every active private document listed in the file map.
- Modify: `$ONEOS_VAULT/_system/docs/lifeos-adoption-guide-legacy.md` authority link only.

**Interfaces:**
- Consumes: the naming contract and `type: system-doc` front-matter requirements.
- Produces: `_system/docs/oneos-spec.md` as the only active implementation-spec path.

- [ ] **Step 1: Rename the specification**

Run:

```bash
mv "$ONEOS_VAULT/_system/docs/oneos-web-spec.md" "$ONEOS_VAULT/_system/docs/oneos-spec.md"
```

- [ ] **Step 2: Update specification identity and paths**

Set the front matter and heading to:

```yaml
title: OneOS — implementation spec
updated: 2026-08-07
description: Read-and-approve interface over the vault. Build order, invariants, stack.
```

```markdown
# OneOS — implementation spec
```

Replace the former checkout path with `~/code/oneos/`, the repository tree label with `oneos/`, the application/container label with `oneos`, the product UI label with OneOS, and private script references with `oneos_wizard.py` and `oneos_fs_mcp.py`. Remove the obsolete superseded-filename line rather than preserving a dead reference.

- [ ] **Step 3: Reconcile active system documents**

Across the active documents in the file map:

- use OneOS for the complete product and human interface;
- use `oneos` for repository/container identifiers;
- use `~/code/oneos` for the application checkout;
- use `oneos-spec.md`, `oneos_wizard.py`, and `oneos_fs_mcp.py` for active file references;
- change active Hermes grounding from the deprecated product label to OneOS;
- change each edited system document’s `updated` field to `2026-08-07`;
- add a concise changelog entry where the document already maintains a changelog.

Do not change document status, phase order, conventions, registry values, or private examples unrelated to product naming.

- [ ] **Step 4: Repair the archived guide’s authority link only**

In `lifeos-adoption-guide-legacy.md`, change the current-authority link to `_system/docs/oneos-spec.md`. Preserve `status: archived`, the historical notice, title, filename, market research, examples, and historical terminology.

- [ ] **Step 5: Run the private naming regression**

Run:

```bash
cd "$ONEOS_VAULT/_system/scripts"
python3 -m unittest test_oneos_naming -v
```

Expected: PASS with no active deprecated-name findings.

- [ ] **Step 6: Run all private validation**

Run:

```bash
cd "$ONEOS_VAULT/_system/scripts"
python3 -m unittest discover -q
python3 "$ONEOS_VAULT/_system/scripts/policy_enforcer.py" \
  --policy "$ONEOS_VAULT/_system/scripts/action-policy.yaml" test-suite
python3 "$ONEOS_VAULT/_system/scripts/check_v2.py" "$ONEOS_VAULT"
```

Expected: 35+ tests pass, policy validation succeeds, and `check_v2.py` reports `0 error(s), 0 warning(s)`.

- [ ] **Step 7: Stage only private system changes and inspect them**

Run:

```bash
git -C "$ONEOS_VAULT" add _system
git -C "$ONEOS_VAULT" diff --cached --check
git -C "$ONEOS_VAULT" status --short
```

Expected: staged paths are under `_system/` only. The pre-existing entity `decisions.md` files remain unstaged.

- [ ] **Step 8: Commit the private cutover**

Run:

```bash
git -C "$ONEOS_VAULT" commit -m "refactor: standardize active system naming on OneOS"
```

Expected: one private-vault commit containing the complete tool/policy/import/spec/document cutover, with unrelated user edits still present and unstaged.

---

### Task 4: Add the public naming regression and rename the application

**Files:**
- Create: `tests/test_naming.py`
- Modify: `pyproject.toml`, `uv.lock`, application source, tests, and public guidance listed in the file map.

**Interfaces:**
- Consumes: renamed private paths from Task 3.
- Produces: Python project `oneos`, FastAPI title `OneOS`, and a public naming regression.

- [ ] **Step 1: Write the failing public naming test**

Create:

```python
from pathlib import Path
import tomllib

ROOT = Path(__file__).resolve().parents[1]
DEPRECATED = (
    "Life" + "OS",
    "OneOS " + "Web",
    "OneOS " + "Console",
    "oneos-" + "web",
    "lifeos" + "_",
)
SCANNED_SUFFIXES = {".py", ".md", ".toml", ".css", ".html", ".yaml", ".yml"}
PROCESS_RECORDS = ROOT / "docs" / "superpowers"


def test_python_project_is_oneos():
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert config["project"]["name"] == "oneos"


def test_lockfile_uses_oneos_project_name():
    lock = (ROOT / "uv.lock").read_text(encoding="utf-8")
    assert 'name = "oneos"' in lock
    assert 'name = "' + "oneos-" + 'web"' not in lock


def test_active_public_files_use_oneos_names():
    findings = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file() or path.suffix not in SCANNED_SUFFIXES:
            continue
        if ".git" in path.parts or ".venv" in path.parts or PROCESS_RECORDS in path.parents:
            continue
        text = path.read_text(encoding="utf-8")
        for term in DEPRECATED:
            if term.casefold() in text.casefold() or term.casefold() in path.name.casefold():
                findings.append(f"{path.relative_to(ROOT)}: {term}")
    assert findings == []
```

- [ ] **Step 2: Run the public test and verify it fails**

Run:

```bash
uv run pytest tests/test_naming.py -q
```

Expected: FAIL on the old package and active source/document labels.

- [ ] **Step 3: Rename package and application identity**

Set:

```toml
[project]
name = "oneos"
description = "Read-and-approve interface over a git-backed Markdown vault."
```

Set the FastAPI application title to:

```python
app = FastAPI(title="OneOS")
```

Use “OneOS” in `app/__init__.py` and the CSS header. Update internal comments to reference `oneos_wizard` rather than its former filename.

- [ ] **Step 4: Update the rename safety fixture**

In `tests/test_rename.py`, use:

```yaml
hermes:oneos-fs-mcp:
```

and fixture path:

```python
"_system/scripts/oneos_fs_mcp.py"
```

Preserve all entity-rename, `.sensitive/`, atomicity, and rollback assertions.

- [ ] **Step 5: Reconcile public guidance**

Update the public files in the file map so that:

- headings and product language use OneOS;
- `$ONEOS_VAULT/_system/...` replaces a fixed private vault path;
- `~/code/oneos` replaces the former checkout path;
- `oneos-spec.md`, `oneos_wizard.py`, and `oneos_fs_mcp.py` are the only active private filenames referenced;
- the “one rule” describes prohibited categories without embedding real examples;
- BUILD verification calls use `$ONEOS_VAULT` and no real entity/module path;
- the standing missing-module regression uses a synthetic fixture or documented test helper rather than a live entity path;
- PRODUCT-THESIS describes the human surface simply as OneOS while retaining Command Center, Grey Matter, and Hermes responsibilities.

- [ ] **Step 6: Regenerate the lockfile**

Run:

```bash
uv lock
```

Expected: `uv.lock` records the local project name as `oneos` with no dependency changes.

- [ ] **Step 7: Run focused and full public tests**

Run:

```bash
uv run pytest tests/test_naming.py tests/test_app.py tests/test_rename.py -q
uv run pytest -q
```

Expected: both commands pass; the full suite count remains 83+.

- [ ] **Step 8: Commit the public cutover**

Run:

```bash
git add AGENTS.md BUILD.md PRODUCT-THESIS.md app docs pyproject.toml static tests uv.lock
git diff --cached --check
git commit -m "refactor: standardize product naming on OneOS"
```

Expected: one public-repository commit separate from the private-vault commit.

---

### Task 5: Cross-repository consistency gate

**Files:**
- Verify only; update the naming design status if every check passes.

**Interfaces:**
- Consumes: both naming commits.
- Produces: evidence that active names, paths, policy identifiers, and tests agree.

- [ ] **Step 1: Search both active surfaces**

Run the two naming tests:

```bash
cd ~/code/oneos-web
uv run pytest tests/test_naming.py -q
cd "$ONEOS_VAULT/_system/scripts"
python3 -m unittest test_oneos_naming -v
```

Expected: both pass. The archived adoption guide and public process records are the only deliberate historical records.

- [ ] **Step 2: Run all application and vault gates**

Run:

```bash
cd ~/code/oneos-web
uv run pytest -q
cd "$ONEOS_VAULT/_system/scripts"
python3 -m unittest discover -q
python3 "$ONEOS_VAULT/_system/scripts/policy_enforcer.py" \
  --policy "$ONEOS_VAULT/_system/scripts/action-policy.yaml" test-suite
python3 "$ONEOS_VAULT/_system/scripts/check_v2.py" "$ONEOS_VAULT"
```

Expected: 83+ application tests, 35+ vault tests, successful policy validation, and `0 error(s), 0 warning(s)`.

- [ ] **Step 3: Confirm repository boundaries**

Run:

```bash
git -C ~/code/oneos-web status --short
git -C "$ONEOS_VAULT" status --short
```

Expected: the application repository is clean. Only the user’s pre-existing, unrelated private-vault edits may remain.

- [ ] **Step 4: Mark the design implemented**

Change the design status line to:

```markdown
Status: naming cutover implemented; GitHub bootstrap pending
```

Commit:

```bash
git add docs/superpowers/specs/2026-08-07-oneos-canonical-naming-and-github-design.md
git commit -m "docs: record OneOS naming cutover"
```

Expected: a small documentation-only commit. Proceed to the GitHub bootstrap plan.
