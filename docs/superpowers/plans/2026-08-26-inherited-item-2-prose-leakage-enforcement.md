# Inherited Item 2 — Prose-Leakage Enforcement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make exact short registry-derived identifiers in tracked Markdown fail the existing current-tree and history publication audit without exposing private terms.

**Architecture:** Extend `tools/public_repo_audit.py`; do not create another scanner. The vault-supplied trusted-local mode adds exact short-token matching for Markdown lines while preserving the existing long-term, path-component, structured-value, binary, receipt, and history rules.

**Tech Stack:** Python 3.12, stdlib `re`/`pathlib`, PyYAML, pytest, Git CLI through the audit's existing helpers.

**Spec:** `docs/superpowers/specs/2026-08-26-inherited-safety-items-2-4-design.md`

## Global Constraints

- Start only after this planning-only change is merged into freshly fetched `origin/main`; record that SHA before branching.
- Use a fresh task, worktree, and `codex/` branch for Item 2 only.
- Use only the public repository and synthetic fixtures. Never request or inspect a live vault, live registry value, database, history snapshot, or private proof artifact.
- Add no dependency, allowlist, exemption, configuration switch, schema, registry value, or second scanner.
- Findings expose only category and safe location; never matched text, matched term, registry path, or surrounding prose.
- Do not push, open a pull request, merge, delete a branch, remove a worktree, or run private gates without separate product-owner authorization.
- Stop on any dependency, convention, schema, security-boundary, destructive-action, deployment, private-material, or unresolved-product decision.
- Independent review and mutation RED→GREEN evidence are mandatory before trusted-local handoff.

## Execution Preconditions

```bash
git fetch origin
BASE_SHA="$(git rev-parse origin/main)"
WORKTREE="$(dirname "$(git rev-parse --show-toplevel)")/oneos-inherited-item-2"
git worktree add "$WORKTREE" -b codex/inherited-item-2-prose-leakage "$BASE_SHA"
cd "$WORKTREE"
test "$(git rev-parse HEAD)" = "$BASE_SHA"
test -z "$(git status --porcelain)"
uv run python -m pytest -q
```

The full baseline must report **1,476 or more passing tests** and zero failures. If it does not, stop and return the exact SHA and failure output to the trusted local reviewer.

### Task 1: Turn the short-Markdown gap into RED tests

**Files:**
- Modify: `tests/test_public_repo_audit.py:289-306`

**Interfaces:**
- Consumes: `synthetic_vault(path: Path, entity: str) -> Path` and the existing `git_repo`, `commit`, and `categories` test helpers.
- Produces: named regressions for exact Markdown tokens, substrings, history, and non-disclosure.

- [ ] **Step 1: Replace the prose-permitted characterization**

Rename `test_short_term_matches_only_component_or_structured_identifier` to `test_short_term_matches_components_structured_values_and_markdown_tokens`. Keep the existing component, YAML, JSON, and substring cases, but change the prose assertion to:

```python
assert categories(prose, vault) == ["instance-value"]
assert categories(substring, vault) == []
```

- [ ] **Step 2: Add a history regression**

```python
def test_short_markdown_term_is_found_after_removal_from_head(tmp_path: Path):
    vault = synthetic_vault(tmp_path / "vault", entity="q7x")
    repo = git_repo(tmp_path / "repo", {"note.md": "public words only\n"})
    commit(repo, {"note.md": "the q7x pattern\n"}, "add exact synthetic term")
    commit(repo, {"note.md": "public words only\n"}, "remove exact synthetic term")

    assert categories(repo, vault, history=False) == []
    assert categories(repo, vault, history=True) == ["instance-value"]
```

- [ ] **Step 3: Add a non-disclosure regression**

```python
def test_short_markdown_finding_never_echoes_term_or_line(tmp_path: Path):
    vault = synthetic_vault(tmp_path / "vault", entity="q7x")
    repo = git_repo(tmp_path / "repo", {"docs/note.md": "before q7x after\n"})
    source_registry = vault / "_system" / "entities.yaml"

    findings = audit_repository(repo, vault=vault, include_history=False)

    assert len(findings) == 1
    assert findings[0].category == "instance-value"
    assert findings[0].location.endswith(":docs/note.md:1")
    assert "q7x" not in repr(findings)
    assert "before" not in repr(findings)
    assert "after" not in repr(findings)
    assert str(source_registry) not in repr(findings)
    assert str(vault) not in repr(findings)
```

The two path assertions are mandatory: findings may name the safe repository
location being audited, but must never disclose either the registry path that
supplied a private term or the configured vault path.

- [ ] **Step 4: Run the focused tests and confirm RED**

Run:

```bash
uv run python -m pytest -q \
  tests/test_public_repo_audit.py::test_short_term_matches_components_structured_values_and_markdown_tokens \
  tests/test_public_repo_audit.py::test_short_markdown_term_is_found_after_removal_from_head \
  tests/test_public_repo_audit.py::test_short_markdown_finding_never_echoes_term_or_line
```

Expected: all three tests fail because Markdown prose does not yet inspect `short_terms`.

- [ ] **Step 5: Commit the RED tests**

```bash
git add tests/test_public_repo_audit.py
git commit -m "test: expose exact short ids in markdown prose"
```

### Task 2: Extend the existing scanner minimally

**Files:**
- Modify: `tools/public_repo_audit.py:160-230`
- Test: `tests/test_public_repo_audit.py`

**Interfaces:**
- Consumes: `scan_text(revision, relative_path, text, long_terms, short_terms)` and the existing safe `finding` constructor.
- Produces: exact-token Markdown line findings using the existing `instance-value` category.

- [ ] **Step 1: Add one exact-token helper shared by long and short terms**

Refactor the current boundary expression without changing its semantics:

```python
MARKDOWN_SUFFIXES = frozenset({".md", ".markdown"})


def contains_exact_term(text: str, terms: set[str]) -> bool:
    return any(
        re.search(rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])", text)
        for term in terms
    )


def contains_long_term(text: str, terms: set[str]) -> bool:
    return contains_exact_term(text, terms)
```

- [ ] **Step 2: Scan short terms only on Markdown lines**

Inside `scan_text`, compute the suffix once and add the short-term condition to the existing per-line finding:

```python
is_markdown = PurePosixPath(relative_path).suffix.lower() in MARKDOWN_SUFFIXES
for line_number, line in enumerate(text.splitlines(), start=1):
    location = safe_location(revision, relative_path, line_number)
    if any(pattern.search(line) for pattern in PRIVATE_PATH_PATTERNS):
        findings.append(finding("absolute-private-path", location))
    if contains_long_term(line, long_terms) or (
        is_markdown and contains_exact_term(line, short_terms)
    ):
        findings.append(finding("instance-value", location))
```

Do not remove the existing `structured_values(relative_path, text) & short_terms` file-level check.

- [ ] **Step 3: Run focused and audit tests**

```bash
uv run python -m pytest -q tests/test_public_repo_audit.py tests/test_publication_docs.py
```

Expected: PASS.

- [ ] **Step 4: Commit the implementation**

```bash
git add tools/public_repo_audit.py tests/test_public_repo_audit.py
git commit -m "feat: reject exact short ids in markdown prose"
```

### Task 3: Prove the protection by mutation and close public evidence

**Files:**
- Modify: `docs/STATUS.md:209-216`
- Verify: `tools/public_repo_audit.py`, `tests/test_public_repo_audit.py`

**Interfaces:**
- Consumes: the exact `is_markdown and contains_exact_term(line, short_terms)` guard.
- Produces: reproducible RED→GREEN evidence and a truthful Item 2 completion note.

- [ ] **Step 1: Save an exact pre-image and remove only the new guard**

Copy `tools/public_repo_audit.py` to a temporary file outside the repository. Change the condition temporarily to `if contains_long_term(line, long_terms):`.

- [ ] **Step 2: Run the exact mutation node**

```bash
uv run python -m pytest -q \
  tests/test_public_repo_audit.py::test_short_term_matches_components_structured_values_and_markdown_tokens \
  tests/test_public_repo_audit.py::test_short_markdown_term_is_found_after_removal_from_head
```

Expected: RED on the Markdown assertions, not on setup or collection.

- [ ] **Step 3: Restore byte-for-byte and rerun GREEN**

Restore the saved pre-image, verify `cmp` succeeds, then rerun the same nodes. Expected: PASS.

- [ ] **Step 4: Update the inherited status paragraph without claiming private completion**

Change the Item 2 heading in `docs/STATUS.md` to:

```markdown
**2. Prose-leakage enforcement — PUBLIC IMPLEMENTATION COMPLETE.**
```

Append one sentence stating that exact short registry-derived tokens in tracked Markdown now fail current-tree and history audit modes, while final live-vault audit and preservation proof remain trusted-local gates.

- [ ] **Step 5: Run the full suite before recording completion**

```bash
uv run python -m pytest -q
```

Expected: PASS.

- [ ] **Step 6: Commit the status evidence**

```bash
git add docs/STATUS.md
git commit -m "docs: record public item 2 audit enforcement"
```

- [ ] **Step 7: Run publication gates on the committed tree**

```bash
uv run python -m tools.public_repo_audit --repo . --history
tools/run_gitleaks.sh .
git diff --check
git status --porcelain
```

Expected: both audits clean; diff check clean; final status empty.

## External-Agent Handoff

Return the recorded base SHA, branch, worktree, commit list, focused RED output, mutation RED→GREEN output, full public count and command, public audit results, Gitleaks result, `git diff --check`, and final `git status --porcelain`. State explicitly that no live vault was accessed and that private gates were not run.

The trusted local reviewer—not the external agent—runs the combined vault-seeded history audit, 37 private tests, `check_v2`, and opaque pre/post byte-preservation comparison before any push or merge decision.
