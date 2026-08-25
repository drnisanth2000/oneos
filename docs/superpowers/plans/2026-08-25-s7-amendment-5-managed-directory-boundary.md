# S7 Amendment 5 Managed-Directory Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close PR #16 with portable directory checks, truthful moved-folder guidance, an explicit cooperative-writer boundary, and fresh public/private verification without adding configuration CRUD or a recovery subsystem.

**Architecture:** Keep the committed open-before-name-check correction in `app/git_transaction.py`; Amendment 5 states the unavoidable post-check ancestor-relocation limit instead of claiming POSIX can enforce descriptor ancestry. Reuse `E-TAMPER` as the read-only operator surface and strengthen only its copy and route proofs. Supported writers use OneOS interfaces and the shared action lock.

**Tech Stack:** Python 3.12, FastAPI, Jinja2, HTMX, pytest, stdlib filesystem APIs, Git/GitHub CLI.

**Spec:** `docs/superpowers/specs/2026-08-23-s7-bound-review-tokens-design.md` — Amendment 5.

## Global Constraints

- Do not add dependencies, configuration CRUD, folder search, automatic restore, symlink fallback, or a new screen.
- “Read-only” means the affected S7 reviewed-action surface: no fingerprint and no approve, reject, registry-delete, or bulk mutation control. Direct registry add/edit remains outside S7.
- Whole-vault relocation happens only while OneOS is stopped: update `ONEOS_VAULT`, restart, and rerun trusted local verification.
- Every supported OneOS, Hermes, parser, browser-extension, and external-agent writer uses OneOS interfaces and the shared action lock.
- A local actor relocating an ancestor directory after the final identity check bypasses that coordination boundary and is outside S7; no code or test may claim POSIX prevents it.
- Preserve exact-byte review binding, quarantine no-overwrite behavior, receipt-first projection, and every S4-S6 guarantee.
- Grey Matter access is read-only and occurs only inside a fresh pre/post proof.
- Never remove the branch, worktree, recovery bundle, or `refs/original/refs/heads/codex/s7-bound-review-tokens` without separate authorization.

---

### Task 1: Implement truthful moved-folder guidance

**Files:**
- Modify: `app/console_errors.py:149-155`
- Modify: `tests/test_console_errors.py`
- Modify: `tests/test_console_routes.py:4792-4920`
- Modify: `docs/superpowers/specs/2026-08-23-s7-bound-review-tokens-design.md`

**Interfaces:**
- Consumes: `ReviewedPathIntegrityError -> E-TAMPER` and existing page-level rendering.
- Produces: one exact `E-TAMPER` contract and route proofs that redirected reviewed-action surfaces carry no mutation controls.

- [ ] **Step 1: Write the exact-contract RED test**

Add to `tests/test_console_errors.py`:

```python
def test_e_tamper_contract_gives_moved_folder_recovery_guidance():
    from app.console_errors import ConsoleError, describe
    from app.git_transaction import ReviewedPathIntegrityError

    assert describe(ReviewedPathIntegrityError("probe")) == ConsoleError(
        "E-TAMPER",
        "integrity",
        "attention",
        "Refused: a managed file or folder is missing, moved, replaced, or "
        "redirected. Reviewed actions for the affected entity are read-only. "
        "Stop OneOS and every connected writer. Restore the item to its "
        "expected location. If the whole vault intentionally moved, update "
        "ONEOS_VAULT, restart OneOS, and rerun verification. Do not use a "
        "symlink or retry while this warning remains.",
        "stop",
        "no",
        409,
    )
```

- [ ] **Step 2: Strengthen both real redirected-outbox route tests**

In `test_outbox_screen_real_symlinked_outbox_shows_e_tamper` and `test_registry_delete_preview_real_symlinked_outbox_shows_e_tamper`, add:

```python
assert _CODES["E-TAMPER"].message in response.text
assert "ONEOS_VAULT" in response.text
assert "symlink" in response.text
assert "hx-post" not in response.text
assert "review_sha256" not in response.text
```

Keep the existing assertion that the external target marker is absent.

- [ ] **Step 3: Run RED**

```bash
uv run python -m pytest -q \
  tests/test_console_errors.py::test_e_tamper_contract_gives_moved_folder_recovery_guidance \
  tests/test_console_routes.py::test_outbox_screen_real_symlinked_outbox_shows_e_tamper \
  tests/test_console_routes.py::test_registry_delete_preview_real_symlinked_outbox_shows_e_tamper
```

Expected: failures name the old message or missing approved guidance, not an unrelated setup error.

- [ ] **Step 4: Replace only the `E-TAMPER` message**

Retain code, tier, severity, retry, committed, and page status exactly. Replace only the message with the Step 1 string.

- [ ] **Step 5: Advance the amendment state**

Change Amendment 5 to `APPROVED — implementation complete, final verification pending`. Keep the post-check exclusion narrow and retain earlier evidence as historical until Task 3 refreshes it.

- [ ] **Step 6: Run focused GREEN and invariants**

```bash
uv run python -m pytest -q \
  tests/test_console_errors.py \
  tests/test_console_invariants.py \
  tests/test_console_routes.py::test_outbox_screen_real_symlinked_outbox_shows_e_tamper \
  tests/test_console_routes.py::test_registry_delete_preview_real_symlinked_outbox_shows_e_tamper
git diff --check
```

- [ ] **Step 7: Commit Task 1**

```bash
git add app/console_errors.py tests/test_console_errors.py \
  tests/test_console_routes.py \
  docs/superpowers/specs/2026-08-23-s7-bound-review-tokens-design.md
git commit -m "fix: explain the S7 managed-directory boundary"
```

---

### Task 2: Independently review the correction wave

**Files:** no edits unless the reviewer returns a verified finding.

**Interfaces:**
- Consumes: range `a7e52eec0f4fbee3ce7339d50c3108e56b46393a..HEAD`, Amendment 5, CI run 32857993278, and CodeRabbit PR #16 findings.
- Produces: spec-compliance and code-quality verdicts for the Linux correction, CodeRabbit fixes, and Amendment 5.

- [ ] **Step 1: Generate a correction-only review package**

Use `review-package` with this plan, base `a7e52eec0f4fbee3ce7339d50c3108e56b46393a`, and the actual `HEAD`.

- [ ] **Step 2: Dispatch a fresh reviewer**

The reviewer must verify:

1. open-before-name-check detects Linux inode reuse during the supported window;
2. no test claims containment after the final check;
3. `errno.EAGAIN` is portable;
4. `_read_record` removal leaves one structured proposal reader;
5. the registry refusal test is non-vacuous;
6. `E-TAMPER` discloses no path and renders no live action control; and
7. Amendment 5 excludes only deliberate post-check ancestor relocation.

- [ ] **Step 3: Resolve accepted findings test-first**

For each accepted finding: reproduce RED, make the smallest fix, run focused GREEN, and obtain a scoped re-review. Do not weaken the approved boundary or broaden S7.

- [ ] **Step 4: Commit only if a fix round changed files**

```bash
git commit -m "fix: close S7 Amendment 5 review findings"
```

Create no empty commit when review is clean.

---

### Task 3: Reverify, publish, and merge PR #16

**Files:**
- Modify after measured results: `AGENTS.md`
- Modify after measured results: `BUILD.md`
- Modify after measured results: `docs/STATUS.md`
- Modify after measured results: `docs/superpowers/specs/2026-08-23-s7-bound-review-tokens-design.md`
- Modify after measured results: this plan

**Interfaces:**
- Consumes: independently approved final correction tip.
- Produces: final evidence, a green PR, and a merged `main` without branch/ref/worktree cleanup.

- [x] **Step 1: Run public verification from a clean, single-writer tree**

```bash
uv run python -m pytest -q
uv run python docs/superpowers/plans/s7_mutation_campaign.py
tools/run_gitleaks.sh .
uv run python -m tools.public_repo_audit --repo .
uv run python -m tools.public_repo_audit --repo . --history
git diff --check
git status --porcelain
```

Measured public completion: `1476 passed`; all 48 campaign mutations RED then
GREEN; the restored campaign closing suite recorded `1476 passed in 107.32s`;
Gitleaks found no leaks; and public current-tree/history audits were CLEAN. The
restored worktree was clean. The focused cross-vault rename-plan mutation is
separate from the 48-row campaign: two distinct same-HEAD repositories refuse
before lock, Git, or mutation, while same-root relative/absolute aliases still
work; that focused mutation went RED then GREEN. A separate caller-alias
retarget regression also went RED then GREEN and keeps execution on the plan's
canonical vault. Record these final public results only.

- [x] **Step 2: Run private gates inside a new preservation envelope**

Capture HEAD, porcelain-v2 NUL status, binary worktree diff, and binary cached diff to a new mode-0700 directory under `/private/tmp`. Run only:

```bash
python3 "$ONEOS_VAULT/_system/scripts/check_v2.py" "$ONEOS_VAULT"
cd "$ONEOS_VAULT/_system/scripts" && python3 -m unittest discover
uv run python -m tools.public_repo_audit --repo . --vault "$ONEOS_VAULT" --history
```

Final private-gate evidence records `check_v2` at 0 errors/0 warnings;
unittest discovery at 37 tests in 0.174s and OK; a CLEAN combined history
audit; and byte-identical HEAD, porcelain-v2 NUL status, binary worktree diff,
and binary cached diff before and after, preserving pre-existing edits.
Independent scoped review PASS found no findings. The proof is retained outside
the repository; its location is not tracked.

- [x] **Step 3: Record measured completion**

Update only the five declared documents. Mark Amendment 5 and S7 COMPLETE; record the exact Step 1 count, 48/48 mutations, private 37, `check_v2` 0/0, the cooperative-writer boundary, and the accepted Linux limitation. Do not record private paths or point-in-time PR state.

- [x] **Step 4: Commit and recheck documentation**

```bash
git add AGENTS.md BUILD.md docs/STATUS.md \
  docs/superpowers/specs/2026-08-23-s7-bound-review-tokens-design.md \
  docs/superpowers/plans/2026-08-25-s7-amendment-5-managed-directory-boundary.md
git commit -m "docs: complete S7 Amendment 5 verification"
uv run python -m pytest -q tests/test_pr15_docs.py \
  tests/test_publication_docs.py tests/test_public_repo_audit.py
```

- [ ] **Step 5: Push and monitor PR #16**

```bash
git push origin codex/s7-bound-review-tokens
gh pr checks 16 --watch --interval 10
```

CI and CodeRabbit must finish green. Independently verify every new review comment. Accepted findings return to Task 2; branch-wide docstring coverage does not authorize unrelated churn.

- [ ] **Step 6: Merge without cleanup**

Confirm PR #16 is mergeable, every required check is green, CodeRabbit has no unresolved actionable finding, and remote head equals local `HEAD`. Then:

```bash
gh pr merge 16 --merge
```

Verify GitHub `main` contains the merge. Do not delete the branch, worktree, recovery bundle, or original ref.

## Plan self-review checklist

- [x] Every Amendment 5 clause maps to Tasks 1–3.
- [x] No global entity-freeze state, configuration CRUD, folder search, symlink fallback, or automatic restore is added.
- [x] No test claims POSIX prevents post-check ancestor relocation.
- [x] Every production edit has observed RED evidence.
- [x] No private value or proof path enters tracked content.
- [x] Merge does not imply cleanup authorization.
