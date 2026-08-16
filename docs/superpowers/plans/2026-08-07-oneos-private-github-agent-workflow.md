# OneOS Private GitHub and Agent Workflow Implementation Plan

> **Historical execution plan:** the private GitHub/agent bootstrap is complete.
> Retain this file for publication-boundary rationale; do not recreate the
> repository or rerun its bootstrap/history-cutover commands.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish a clean, private OneOS source repository to GitHub and establish CI, pull-request review, Codex cloud, and local private-vault integration gates without uploading old contaminated history or Grey Matter.

**Architecture:** Add a repeatable public-repository audit and GitHub CI to the sanitized tree, export that tree into a new canonical Git repository with one clean baseline commit, then push only that repository. GitHub and Codex cloud operate on synthetic fixtures; a local gate alone can read the private Grey Matter vault.

**Tech Stack:** Git, GitHub CLI, GitHub Actions, Python 3.12, `uv`, pytest, YAML, Codex cloud.

## Global Constraints

- Run this plan only after the canonical naming cutover plan passes.
- The remote repository is private and named `oneos`.
- Never upload the old application Git history, Grey Matter, live registries, database files, private paths, credentials, or instance values.
- The GitHub owner is supplied interactively and never written into the repository.
- The existing checkout remains the local pre-public history archive.
- The new canonical checkout is `~/code/oneos`.
- CI uses only synthetic fixtures and no `ONEOS_VAULT` secret.
- No direct push to the new `main` after bootstrap; changes use `codex/` branches and pull requests.
- Cloud agents stop for dependencies, convention changes, security-boundary changes, destructive actions, deployment, or unresolved product decisions.

---

## File map

- Create `tools/public_repo_audit.py`: deterministic tracked-tree/history audit with optional private registry-derived terms.
- Create `tests/test_public_repo_audit.py`: synthetic tests for findings, redaction, history scanning, and clean results.
- Modify `tests/test_pii.py`: keep its synthetic credential case without embedding a credential-shaped literal that the audit must reject.
- Create `.github/workflows/ci.yml`: read-only CI running the public audit and test suite.
- Create `.github/pull_request_template.md`: verification and private-gate checklist.
- Create `.github/ISSUE_TEMPLATE/agent-task.yml`: bounded agent task contract.
- Modify `AGENTS.md`: cloud/local execution boundary and PR rules.
- Modify `BUILD.md`: public CI command and private integration command.
- Create the new Git repository at `~/code/oneos` from a tracked-file export of the sanitized source tree.

---

### Task 1: Build the repeatable public-repository audit

**Files:**
- Create: `tools/public_repo_audit.py`
- Create: `tests/test_public_repo_audit.py`
- Modify: `tests/test_pii.py`

**Interfaces:**
- Produces: `audit_repository(repo: Path, vault: Path | None, include_history: bool) -> list[Finding]`.
- Produces CLI: `python -m tools.public_repo_audit --repo PATH [--vault PATH] [--history]`.
- `Finding` fields: `category: str`, `location: str`, `message: str`; messages identify a category without printing matched secret values.

- [ ] **Step 1: Write failing tests for clean and contaminated tracked trees**

Define these complete local helpers at the top of the test module:

```python
import subprocess
from pathlib import Path

import yaml


def run_git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout


def git_repo(path: Path, files: dict[str, str]) -> Path:
    path.mkdir(parents=True)
    run_git(path, "init", "-q")
    run_git(path, "config", "user.name", "test")
    run_git(path, "config", "user.email", "test@example.com")
    commit(path, files, "initial")
    return path


def commit(repo: Path, files: dict[str, str], message: str) -> None:
    for relative, content in files.items():
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-q", "-m", message)


def synthetic_vault(path: Path, entity: str) -> Path:
    system = path / "_system"
    system.mkdir(parents=True)
    (system / "entities.yaml").write_text(
        yaml.safe_dump({"entities": {entity: {"label": "Synthetic"}}}),
        encoding="utf-8",
    )
    (system / "products.yaml").write_text("products: {}\n", encoding="utf-8")
    (system / "members.yaml").write_text("members: {}\n", encoding="utf-8")
    return path
```

Then add these assertions:

```python
def test_clean_synthetic_repository_passes(tmp_path):
    repo = git_repo(tmp_path, {"app.py": "title = 'OneOS'\n"})
    assert audit_repository(repo, vault=None, include_history=True) == []


def test_absolute_home_path_is_rejected_without_echoing_value(tmp_path):
    private_path = "/" + "Users" + "/example/private-vault"
    repo = git_repo(tmp_path, {"config.py": f"ROOT = {private_path!r}\n"})
    findings = audit_repository(repo, vault=None, include_history=False)
    assert [item.category for item in findings] == ["absolute-private-path"]
    assert private_path not in findings[0].message


def test_registry_derived_entity_term_is_rejected(tmp_path):
    repo = git_repo(tmp_path / "repo", {"note.md": "customer-zeta\n"})
    vault = synthetic_vault(tmp_path / "vault", entity="customer-zeta")
    findings = audit_repository(repo, vault=vault, include_history=False)
    assert any(item.category == "instance-value" for item in findings)


def test_history_finding_blocks_clean_worktree(tmp_path):
    private_path = "/" + "Users" + "/example/private"
    repo = git_repo(tmp_path, {"note.md": private_path + "\n"})
    commit(repo, {"note.md": "OneOS\n"}, "sanitize")
    assert any(
        item.category == "absolute-private-path"
        for item in audit_repository(repo, vault=None, include_history=True)
    )


def test_credential_value_is_rejected_without_echoing_it(tmp_path):
    assignment = "access_" + "token = 'synthetic-value-123'\n"
    repo = git_repo(tmp_path, {"config.py": assignment})
    findings = audit_repository(repo, vault=None, include_history=False)
    assert [item.category for item in findings] == ["credential"]
    assert "synthetic-value-123" not in findings[0].message
```

In the existing PII test, assemble its fake credential label as
`"pass" + "word: hunter2sekret"`; its behavior and expected redaction remain
unchanged while the source tree no longer contains a credential-shaped literal.

- [ ] **Step 2: Run tests and verify the module is missing**

Run:

```bash
uv run pytest tests/test_public_repo_audit.py -q
```

Expected: FAIL because `tools.public_repo_audit` does not exist.

- [ ] **Step 3: Implement the audit data model and tracked-file scan**

Implement:

```python
@dataclass(frozen=True)
class Finding:
    category: str
    location: str
    message: str


def audit_repository(
    repo: Path,
    vault: Path | None = None,
    include_history: bool = False,
) -> list[Finding]:
    terms = load_instance_terms(vault) if vault else set()
    selected = revisions(repo) if include_history else ["HEAD"]
    findings = []
    for revision in selected:
        findings.extend(scan_revision(repo, revision, terms))
    return sorted(set(findings), key=lambda item: (item.location, item.category))
```

Implement `revisions(repo: Path) -> list[str]` with `git rev-list --all`, and
`scan_revision(repo: Path, revision: str, terms: set[str]) -> list[Finding]`
with `git ls-tree -r --name-only REVISION` and `git show REVISION:PATH`, so scans
cover tracked content only. Skip blobs that contain NUL bytes or cannot be
decoded as UTF-8. Scan the remaining text files for:

- absolute macOS home-directory paths;
- private-vault directory markers such as `.sensitive/` only when combined with an absolute path;
- credential assignments or URLs containing tokens/passwords;
- private instance terms loaded from registries.

Do not print matched credential or instance values. Report only category, revision/path/line, and a safe explanation.

Use these concrete built-in patterns, assembled so the scanner does not flag
its own source:

```python
HOME_PREFIX = "/" + "Users" + "/"
CREDENTIAL_RE = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|password|client[_-]?secret)\b"
    r"\s*[:=]\s*[\"'][^\"']{8,}[\"']"
)
URL_CREDENTIAL_RE = re.compile(r"https?://[^/\s:@]+:[^@\s/]+@")
```

In test fixtures, assemble the absolute sample path with
`"/" + "Users" + "/example/private-vault"` so the repository containing the
test remains clean.

- [ ] **Step 4: Implement private term loading**

Implement:

```python
def load_instance_terms(vault: Path) -> set[str]:
    system = vault / "_system"
    entities = load_yaml(system / "entities.yaml").get("entities", {})
    products = load_yaml(system / "products.yaml").get("products", {})
    members = load_yaml(system / "members.yaml").get("members", {})
    terms = set(entities)
    for entity_products in products.values():
        if isinstance(entity_products, dict):
            terms.update(entity_products)
    for entity_members in members.values():
        if isinstance(entity_members, list):
            terms.update(
                member["id"]
                for member in entity_members
                if isinstance(member, dict) and isinstance(member.get("id"), str)
            )
    return {term for term in terms if isinstance(term, str) and len(term) >= 4}


def load_yaml(path: Path) -> dict:
    if not path.is_file():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
```

Registry parsing is read-only. Do not add or edit any private registry.

- [ ] **Step 5: Implement the CLI**

Use:

```python
parser.add_argument("--repo", type=Path, default=Path("."))
parser.add_argument("--vault", type=Path)
parser.add_argument("--history", action="store_true")
```

Exit `0` with `CLEAN` when no findings exist. Exit `1` and print one safe `category location message` line per finding otherwise.

- [ ] **Step 6: Run focused and full tests**

Run:

```bash
uv run pytest tests/test_public_repo_audit.py -q
uv run pytest -q
```

Expected: focused tests and all 87+ tests pass.

- [ ] **Step 7: Prove the current old history is blocked**

Run:

```bash
uv run python -m tools.public_repo_audit --repo . --vault "$ONEOS_VAULT" --history
```

Expected: non-zero exit with safe findings from old reachable history. This is expected evidence that the clean-baseline route is mandatory; do not weaken the audit to make the command pass.

- [ ] **Step 8: Commit the audit tool**

Run:

```bash
git add tools/public_repo_audit.py tests/test_public_repo_audit.py tests/test_pii.py
git commit -m "feat: add public repository safety audit"
```

---

### Task 2: Add GitHub CI and bounded-agent templates

**Files:**
- Create: `.github/workflows/ci.yml`
- Create: `.github/pull_request_template.md`
- Create: `.github/ISSUE_TEMPLATE/agent-task.yml`
- Modify: `AGENTS.md`
- Modify: `BUILD.md`

**Interfaces:**
- Consumes: `python -m tools.public_repo_audit` and `uv run pytest`.
- Produces: required CI job `test`; reusable issue fields for scope and acceptance criteria.

- [ ] **Step 1: Create the read-only CI workflow**

Create exactly:

```yaml
name: CI

on:
  pull_request:
  push:
    branches: [main]

permissions:
  contents: read

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
        with:
          fetch-depth: 0
      - name: Install uv
        uses: astral-sh/setup-uv@08807647e7069bb48b6ef5acd8ec9567f424441b # v8.1.0
        with:
          version: "0.11.32"
          enable-cache: true
      - name: Install Python
        run: uv python install 3.12
      - name: Sync dependencies
        run: uv sync --locked --dev
      - name: Audit public repository
        run: uv run python -m tools.public_repo_audit --repo . --history
      - name: Run tests
        run: uv run pytest -q
```

- [ ] **Step 2: Create the pull-request template**

Require these checkboxes:

```markdown
## Outcome

<!-- What changed, in user terms? -->

## Verification

- [ ] Targeted tests pass
- [ ] Full `uv run pytest -q` passes
- [ ] `python -m tools.public_repo_audit --repo . --history` passes
- [ ] No live Grey Matter data was used or uploaded
- [ ] Private local integration gate passes, or this change is marked “public-only” with a reason
- [ ] Diff contains no unrelated changes

## Risk and rollback

<!-- Name the failure mode and how the commit/PR is reverted. -->
```

- [ ] **Step 3: Create the agent-task issue form**

The YAML form must require: outcome, in-scope files, out-of-scope changes, acceptance tests, private-gate requirement, dependencies, and stop conditions. Default stop conditions include new dependencies, convention/schema changes, security-boundary changes, destructive actions, deployment, and unresolved product choices.

- [ ] **Step 4: Add cloud/local boundaries to repository guidance**

Add to `AGENTS.md`:

```markdown
## Cloud and pull-request boundary

Cloud agents use this repository and synthetic fixtures only. They never receive
the live Grey Matter vault, its registries, databases, paths, or Git history.
Every write task uses a `codex/` branch and a pull request. CI is necessary but
not sufficient: changes that read or interpret vault structure also require the
local private integration gate in `BUILD.md` before merge.
```

Replace the old hardcoded instance scan in `BUILD.md` with:

```bash
uv run python -m tools.public_repo_audit --repo . --history
uv run python -m tools.public_repo_audit --repo . --vault "$ONEOS_VAULT" --history
```

Explain that CI runs the first command and the trusted local integration agent runs the second.

- [ ] **Step 5: Verify workflow syntax and tests**

Run:

```bash
python3 -c 'import yaml; yaml.safe_load(open(".github/workflows/ci.yml"))'
uv run pytest -q
git diff --check
```

Expected: workflow parses, all tests pass, and no whitespace errors exist.

- [ ] **Step 6: Commit CI and workflow contracts**

Run:

```bash
git add .github AGENTS.md BUILD.md
git commit -m "ci: add private-first agent development gates"
```

---

### Task 3: Create the clean canonical local repository

**Files:**
- Source: the reviewed isolated worktree containing Tasks 1–2.
- Create: canonical checkout at `~/code/oneos`.
- Preserve: current checkout and its `.git` directory as the local pre-public history archive.

**Interfaces:**
- Consumes: the sanitized tracked tree after Tasks 1–2.
- Produces: a new Git repository whose `main` contains exactly one clean baseline commit.

- [ ] **Step 1: Verify the source checkout and private gates**

Run:

```bash
SOURCE_WORKTREE=$(git rev-parse --show-toplevel)
cd "$SOURCE_WORKTREE"
git status --short
uv run pytest -q
uv run python -m tools.public_repo_audit --repo . --vault "$ONEOS_VAULT"
```

Expected: source checkout clean, tests pass, and the current tree audit passes without `--history`. If the tree audit fails, fix the tree before continuing.

- [ ] **Step 2: Validate an existing canonical path**

Run:

```bash
if [ -e ~/code/oneos ] || [ -L ~/code/oneos ]; then
  if [ ! -L ~/code/oneos ] || [ "$(realpath ~/code/oneos)" != "$(realpath ~/code/oneos-web)" ]; then
    printf '%s\n' '~/code/oneos exists and is not the expected compatibility symlink' >&2
    exit 1
  fi
fi
```

Expected: exit `0` when the path is absent or is exactly the compatibility symlink from `~/code/oneos` to `~/code/oneos-web`. Any regular file, directory, dangling symlink, or symlink to another target is a hard stop.

- [ ] **Step 3: Export only tracked files**

Run:

```bash
set -e
SOURCE_WORKTREE=$(git rev-parse --show-toplevel)
git -C "$SOURCE_WORKTREE" archive --format=tar --output=/private/tmp/oneos-clean-tree.tar HEAD
if [ -L ~/code/oneos ] && [ "$(realpath ~/code/oneos)" = "$(realpath ~/code/oneos-web)" ]; then
  unlink ~/code/oneos
elif [ -e ~/code/oneos ] || [ -L ~/code/oneos ]; then
  printf '%s\n' '~/code/oneos changed after validation; refusing to overwrite it' >&2
  exit 1
fi
mkdir ~/code/oneos
tar -xf /private/tmp/oneos-clean-tree.tar -C ~/code/oneos
```

The compatibility symlink is revalidated and unlinked immediately before the clean directory is created. No other existing path is removed. The tracked-file export intentionally excludes `.git`, untracked files, local environments, caches, and unrelated private material.

- [ ] **Step 4: Initialize the new clean history**

Run:

```bash
git -C ~/code/oneos init -b main
git -C ~/code/oneos add -A
git -C ~/code/oneos diff --cached --check
git -C ~/code/oneos commit -m "feat: establish OneOS baseline"
```

Expected: exactly one root commit.

- [ ] **Step 5: Verify the clean repository independently**

Run:

```bash
cd ~/code/oneos
uv run pytest -q
uv run python -m tools.public_repo_audit --repo . --vault "$ONEOS_VAULT" --history
git rev-list --count HEAD
git status --short
```

Expected: tests and audit pass, commit count is `1`, and status is empty.

- [ ] **Step 6: Confirm the old repository remains recoverable**

Run:

```bash
git -C ~/code/oneos-web fsck --full
git -C ~/code/oneos-web log --oneline -3
```

Expected: old local history is intact and has no remote configured. Do not delete or push it.

---

### Task 4: Authenticate GitHub, create the private remote, and push

**Files:**
- Modify Git configuration in `~/code/oneos/.git` only.
- Create private remote repository `oneos` under the interactively authenticated owner.

**Interfaces:**
- Consumes: clean canonical repository from Task 3.
- Produces: `origin` and upstream `origin/main`.

- [ ] **Step 1: Reauthenticate the GitHub CLI**

Run:

```bash
gh auth login -h github.com -p https -w
```

Human action: approve the browser/device confirmation. Do not paste a token into chat or commit it anywhere.

- [ ] **Step 2: Verify the authenticated owner without persisting it**

Run:

```bash
gh auth status
gh api user --jq .login
```

Expected: authentication succeeds. Treat the printed owner as runtime setup data, not repository content.

- [ ] **Step 3: Create the private repository without pushing**

Run from `~/code/oneos`:

```bash
gh repo create oneos --private --source=. --remote=origin --description "Local-first, git-backed operating system"
```

Expected: `origin` points to a private repository named `oneos`.

- [ ] **Step 4: Re-run the final pre-push gate**

Run:

```bash
uv run python -m tools.public_repo_audit --repo . --vault "$ONEOS_VAULT" --history
uv run pytest -q
git remote -v
```

Expected: audit and tests pass. Remote output contains only the intended OneOS repository.

- [ ] **Step 5: Push only clean main**

Run:

```bash
git push --set-upstream origin main
```

Do not use `--all`, `--mirror`, or push from the former checkout.

- [ ] **Step 6: Configure conservative repository settings**

Run:

```bash
gh repo edit --enable-issues --disable-wiki --delete-branch-on-merge
```

In GitHub settings, require the `test` status check and pull requests before merging when the account plan exposes those controls. If the controls are unavailable, preserve the same rule operationally: no direct development on `main`.

---

### Task 5: Connect Codex cloud and prove the review loop

**Files:**
- Create on branch: `.github/ISSUE_TEMPLATE/config.yml`
- Modify on branch: `docs/STATUS.md`

**Interfaces:**
- Consumes: connected private GitHub repository and CI.
- Produces: one `codex/workflow-pilot` pull request reviewed by CI and Codex.

- [ ] **Step 1: Connect the private repository to Codex cloud**

Human action in Codex settings:

1. Connect the authenticated GitHub account.
2. Grant Codex access only to the private `oneos` repository.
3. Create a repository environment with setup command `uv sync --locked --dev`.
4. Add no vault path, vault archive, database, registry, or production secret.
5. Enable Codex code review for the repository.

- [ ] **Step 2: Create the pilot branch**

Run:

```bash
cd ~/code/oneos
git switch -c codex/workflow-pilot
```

- [ ] **Step 3: Add the issue-template chooser and status record**

Create:

```yaml
blank_issues_enabled: false
contact_links: []
```

Add a concise `docs/STATUS.md` entry stating that private GitHub CI is active, cloud work uses synthetic fixtures only, and private-vault integration remains a local merge gate.

- [ ] **Step 4: Verify, commit, and push the pilot**

Run:

```bash
uv run python -m tools.public_repo_audit --repo . --vault "$ONEOS_VAULT" --history
uv run pytest -q
git add .github/ISSUE_TEMPLATE/config.yml docs/STATUS.md
git commit -m "docs: activate agent pull request workflow"
git push --set-upstream origin codex/workflow-pilot
```

- [ ] **Step 5: Open the pilot pull request**

Run:

```bash
gh pr create --base main --head codex/workflow-pilot --title "Activate agent pull request workflow" --body-file .github/pull_request_template.md
```

Edit the generated body so every checkbox reflects actual evidence; do not mark the private gate complete until it has run.

- [ ] **Step 6: Run the local private integration gate**

Run:

```bash
uv run python -m tools.public_repo_audit --repo . --vault "$ONEOS_VAULT" --history
uv run pytest -q
cd "$ONEOS_VAULT/_system/scripts"
python3 -m unittest discover -q
python3 "$ONEOS_VAULT/_system/scripts/policy_enforcer.py" \
  --policy "$ONEOS_VAULT/_system/scripts/action-policy.yaml" test-suite
python3 "$ONEOS_VAULT/_system/scripts/check_v2.py" "$ONEOS_VAULT"
```

Expected: public audit and tests pass; private tests and validators pass; no private files are staged or pushed.

- [ ] **Step 7: Request Codex review and merge only when green**

Add `@codex review` to the pull request. Resolve any consequential findings, wait for the `test` job, inspect the final diff, then merge through GitHub. Delete the remote branch after merge.

- [ ] **Step 8: Hand the first real task to Codex cloud**

Create a bounded issue for Safety Foundation S1 using the agent-task form. Start one Codex cloud task from that issue. Its stop conditions remain those in `AGENTS.md`; S2–S6 are not dispatched concurrently until their shared-file and dependency boundaries are reviewed.

---

### Task 6: Final operating-model verification

**Files:**
- Verify only; update the design status after success.

**Interfaces:**
- Consumes: merged pilot PR and Codex cloud connection.
- Produces: a ready private-first agent development system.

- [ ] **Step 1: Verify repository identity and visibility**

Run:

```bash
cd ~/code/oneos
gh repo view --json name,visibility,defaultBranchRef
```

Expected: name `oneos`, visibility `PRIVATE`, default branch `main`.

- [ ] **Step 2: Verify no old history reached the remote**

Run:

```bash
git fetch origin
git rev-list --count origin/main
uv run python -m tools.public_repo_audit --repo . --vault "$ONEOS_VAULT" --history
```

Expected: remote history begins with the clean baseline and the small pilot PR; the audit passes.

- [ ] **Step 3: Verify the working boundary**

Confirm:

- GitHub contains the OneOS application repository only.
- Grey Matter has no remote added by this work.
- Codex cloud has no vault secret or uploaded vault data.
- CI passes on pull requests.
- the local private gate passes from `~/code/oneos`.
- the former checkout remains local and has not been pushed.

- [ ] **Step 4: Mark the design complete**

Set the design status to:

```markdown
Status: implemented
```

Commit and merge this status through a final `codex/` documentation pull request, proving the operating model is used for its own completion.
