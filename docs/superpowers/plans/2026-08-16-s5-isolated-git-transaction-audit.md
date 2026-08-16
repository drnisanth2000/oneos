# S5 Isolated Git Transaction and Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make classification approval and approved registry deletion isolated, exactly-scoped, revertible Git transactions, and make Gate 3 sanction commits only when both their action message and changed paths are valid.

**Architecture:** A new dependency-free transaction service accepts an immutable, list-capable data plan describing exact before/after file states, reviewed commit paths, and owned untracked side effects. It serializes OneOS approvals with a non-blocking per-vault advisory lock, constructs commits through a temporary alternate Git index, preserves unrelated real-index/worktree state, and performs ownership-aware rollback on every failure. Classification approval and registry deletion translate its typed failures into their existing domain error families; Gate 3 separately records dirty-state fingerprints and validates every new commit with action-specific, runtime-registry-backed path rules.

**Tech Stack:** Python 3.12+, FastAPI, PyYAML, pytest, standard-library `dataclasses`, `fcntl`, `hashlib`, `os`, `pathlib`, `stat`, `subprocess`, and `tempfile`; Git CLI through argument arrays; no new dependency or build step.

**Spec:** `docs/superpowers/specs/2026-08-16-s5-isolated-git-transaction-audit-design.md`

## Global Constraints

- Implement Safety Foundation **S5 only** on `codex/s5-isolated-git-transaction-audit`, based on merged `origin/main` `3c56119` plus approved design commit `38c12f7`.
- The new service is used only by classification approval and approved registry deletion.
- The transaction plan is internally list-capable, but S5 adds no multi-proposal route, UI, screen, or workflow.
- Intake keeps its S1 path-limited commit and cleanup logic. Rename and direct registry add/edit keep their existing mutation flows.
- Only one OneOS approval transaction may run per vault; lock acquisition is non-blocking and a busy vault fails before mutation.
- Unrelated staged, unstaged, and untracked work is allowed and must remain exactly unchanged. A dirty reviewed path or mismatched owned proposal is refused before mutation.
- Construct the commit from a temporary alternate Git index initialized from starting `HEAD`; never construct it from the user's real index.
- Stage exactly the reviewed commit paths, run existing Git hooks, verify the staged set before commit, and verify the committed set and parent after commit.
- Synchronize only reviewed entries in the real index to the new `HEAD`; preserve all unrelated index entries exactly.
- On failure, restore starting `HEAD`, owned filesystem/proposal state, and reviewed real-index entries. Never use stash, broad add, broad reset, hard reset, clean, or repository-wide rollback.
- Rollback is ownership-aware: restore a path only while its current state equals a state written by this transaction. Preserve concurrent same-path changes and raise a typed recovery error naming only runtime paths.
- Gate 3 validates `ingest:`, `outbox:`, `registry:`, and `rename:` messages together with their actual name-status path sets. A valid prefix alone never sanctions a commit.
- `ingest:` is sanctioned only for one added redacted Markdown receipt under a manifest entity's `00-inbox/active/`; an uncommitted inbox receipt is a violation.
- Only canonical pending proposal YAML files under a manifest entity's `outbox/` are sanctioned new dirty writes. A change to any path already dirty at snapshot time is reported as a violation instead of being hidden by filename subtraction.
- Runtime entity/module/registry structure comes from `entities.yaml` and `archetypes.yaml`; never hardcode an instance slug, active module list, block map, vault path, or authenticated GitHub owner.
- Preserve S1 ingest, S2 request-local scope/concurrency, S3 canonical destinations/containment, S4 collision-safe identity/freshness/no-follow behavior, and the current FastAPI routes.
- Do not implement S6 general error presentation. Transaction errors are typed and domain-wrapped, but classification routes keep their existing presentation behavior.
- Add no dependency, daemon, queue, database, physical subfolder, deployment change, or private-vault value.
- Grey Matter is read-only. Before private gates, record `HEAD`, status, worktree-diff, and cached-diff fingerprints; prove exact equality afterwards.
- Follow strict red-green-refactor TDD. Each task gets a fresh implementer, then a requirements reviewer, then a code-quality reviewer; fix loops return to the same implementer before the task commit is accepted.
- Do not push, merge, open a pull request, remove the worktree, or publish the branch without explicit authorization.

## File Map

- Create `app/git_transaction.py`: immutable transaction model, safe state capture, path validation, per-vault lock, alternate-index commit, real-index synchronization, rollback, and typed errors.
- Create `tests/test_git_transaction.py`: synthetic-repository contract, isolation, failure-injection, recovery, lock, and cleanup tests.
- Modify `app/outbox.py`: build and execute a classification transaction after S2-S4 validation; domain-wrap transaction failures.
- Modify `tests/test_outbox.py`: prove dirty-work isolation, exact paths, rollback, one-commit approval, and one-revert restoration.
- Modify `app/registry.py`: render approved deletion from an exact registry snapshot, execute it through the service, and domain-wrap failures.
- Modify `tests/test_registry.py`: prove the registry-only commit, unrelated-state preservation, rollback, proposal restoration, and revert behavior.
- Rewrite `tools/gate3_audit.py`: versioned session snapshots, per-path dirty fingerprints, commit records, action/path validators, runtime rules, and CLI reporting.
- Rewrite `tests/test_gate3_audit.py`: pure and Git-backed valid/invalid coverage for every action and dirty-state rule.
- Modify `tests/conftest.py`: add narrowly reusable binary Git/index/state helpers used by S5 tests.
- Modify `tests/test_app.py` only where route-level regression proof is needed; do not add a route or general S6 alert.

## Design Coverage

| Approved requirement | Owning task |
|---|---|
| Immutable list-capable plan, exact states, lexical/no-follow validation | Task 1 |
| One non-blocking approval lock per vault | Task 1 |
| Hybrid dirty-work policy and separate alternate index | Task 2 |
| Hook execution, exact staged/committed paths, reviewed-only real-index sync | Task 2 |
| All-phase rollback, proposal restoration, compare-and-swap `HEAD`, concurrent same-path preservation | Task 3 |
| Classification approval only; no new route/UI | Task 4 |
| Approved registry deletion only; direct add/edit unchanged | Task 5 |
| Action/path-aware `ingest:`, `outbox:`, `registry:`, and existing-planner `rename:` audit | Task 6 |
| Dirty baseline fingerprints, canonical proposal-only pending writes, uncommitted receipt refusal | Task 6 |
| One-revert proof, S1-S4 regressions, private fingerprints, policy/check/audit gates, whole-branch review | Task 7 |

---

### Task 1: Immutable transaction contract, safe path state, and non-blocking vault lock

**Files:**
- Create: `app/git_transaction.py`
- Create: `tests/test_git_transaction.py`
- Modify: `tests/conftest.py`

**Interfaces:**
- Produces: `PathState(contents: bytes | None, mode: int | None)` with `PathState.absent()` and `PathState.regular(contents: bytes, mode: int)` constructors.
- Produces: `PathChange(path: str, before: PathState, after: PathState)`.
- Produces: `TransactionPlan(message: str, changes: tuple[PathChange, ...], commit_paths: tuple[str, ...], owned_changes: tuple[PathChange, ...] = ())`.
- Produces: `TransactionResult(commit_oid: str, changed_paths: tuple[str, ...])`.
- Produces error family: `GitTransactionError`, `VaultBusyError`, `ReviewedStateConflict`, `GitTransactionFailure`, and `GitTransactionRecoveryError(paths: tuple[str, ...])`.
- Produces: `capture_path_state(vault: Path, relative_path: str) -> PathState` using no-follow lexical validation.
- Produces internally: `_approval_lock(vault: Path)` as a non-blocking context manager over `absolute_git_dir/oneos-approval.lock`.
- Consumed later: Tasks 2-5 import the model, `capture_path_state`, `execute_transaction`, and typed errors.

- [ ] **Step 1: Add exact binary Git-state helpers for the S5 tests**

Add these helpers to `tests/conftest.py`; they deliberately return bytes so tests compare index and status without lossy decoding:

```python
def git_bytes(root: Path, *args: str, env: dict[str, str] | None = None) -> bytes:
    return subprocess.run(
        ["git", *args], cwd=root, env=env, check=True, capture_output=True
    ).stdout


def git_status_bytes(root: Path) -> bytes:
    return git_bytes(root, "status", "--porcelain=v1", "-z", "--untracked-files=all")


def git_index_entries(root: Path) -> bytes:
    return git_bytes(root, "ls-files", "--stage", "-z")


def git_worktree_diff(root: Path) -> bytes:
    return git_bytes(root, "diff", "--binary")


def git_cached_diff(root: Path) -> bytes:
    return git_bytes(root, "diff", "--cached", "--binary")
```

- [ ] **Step 2: Write failing model, path, state, and lock tests**

Create `tests/test_git_transaction.py` with synthetic repositories only. Start with these contracts:

```python
import fcntl
import os
import stat
from pathlib import Path

import pytest

import app.git_transaction as transaction
from app.git_transaction import (
    PathChange,
    PathState,
    ReviewedStateConflict,
    TransactionPlan,
    VaultBusyError,
    capture_path_state,
)
from tests.conftest import git_entity_vault


def _vault(tmp_path: Path) -> Path:
    return git_entity_vault(
        tmp_path,
        ("synthetic",),
        {
            "synthetic/00-inbox/active/item.md": "reviewed\n",
            "synthetic/11-library/active/.gitkeep": "",
        },
    )


def test_path_state_requires_absence_or_exact_regular_bytes_and_mode():
    assert PathState.absent() == PathState(None, None)
    assert PathState.regular(b"body\n", 0o644) == PathState(b"body\n", 0o644)
    with pytest.raises(ValueError):
        PathState(b"body\n", None)
    with pytest.raises(ValueError):
        PathState(None, 0o644)


@pytest.mark.parametrize(
    "path",
    ["", ".", "../escape", "/absolute", "synthetic/../escape", ".git/config",
     "synthetic/.git/config", "synthetic\\item.md"],
)
def test_plan_rejects_nonlexical_or_git_internal_paths(path):
    change = PathChange(path, PathState.absent(), PathState.regular(b"x", 0o644))
    with pytest.raises(ValueError):
        TransactionPlan("outbox: synthetic", (change,), (path,))


def test_plan_rejects_duplicate_or_mismatched_commit_paths():
    change = PathChange(
        "synthetic/00-inbox/active/item.md",
        PathState.regular(b"reviewed\n", 0o644),
        PathState.absent(),
    )
    with pytest.raises(ValueError):
        TransactionPlan("outbox: synthetic", (change, change), (change.path,))
    with pytest.raises(ValueError):
        TransactionPlan("outbox: synthetic", (change,), ("synthetic/other.md",))


def test_capture_rejects_symlink_directory_and_redirected_parent(tmp_path):
    vault = _vault(tmp_path)
    target = vault / "target.md"
    target.write_bytes(b"target\n")
    leaf = vault / "synthetic/leaf.md"
    leaf.symlink_to(target)
    redirected = vault / "synthetic/redirected"
    redirected.symlink_to(vault / "synthetic/00-inbox", target_is_directory=True)

    with pytest.raises(ReviewedStateConflict):
        capture_path_state(vault, "synthetic/leaf.md")
    with pytest.raises(ReviewedStateConflict):
        capture_path_state(vault, "synthetic/redirected/active/item.md")


def test_second_approval_lock_fails_without_waiting_or_mutating(tmp_path):
    vault = _vault(tmp_path)
    with transaction._approval_lock(vault):
        with pytest.raises(VaultBusyError):
            with transaction._approval_lock(vault):
                pytest.fail("contended lock body must not run")
```

- [ ] **Step 3: Run the focused contract tests and confirm RED**

```bash
uv run pytest tests/test_git_transaction.py -q
```

Expected: collection fails because `app.git_transaction` does not exist.

- [ ] **Step 4: Implement the immutable model and exact validation**

Create `app/git_transaction.py` with frozen dataclasses and these exact public signatures:

```python
@dataclass(frozen=True)
class PathState:
    contents: bytes | None
    mode: int | None

    def __post_init__(self) -> None:
        if (self.contents is None) != (self.mode is None):
            raise ValueError("path state must be absent or a regular file")
        if self.mode is not None and (self.mode < 0 or self.mode > 0o7777):
            raise ValueError("file mode is invalid")

    @classmethod
    def absent(cls) -> "PathState":
        return cls(None, None)

    @classmethod
    def regular(cls, contents: bytes, mode: int) -> "PathState":
        return cls(bytes(contents), mode)


@dataclass(frozen=True)
class PathChange:
    path: str
    before: PathState
    after: PathState


@dataclass(frozen=True)
class TransactionPlan:
    message: str
    changes: tuple[PathChange, ...]
    commit_paths: tuple[str, ...]
    owned_changes: tuple[PathChange, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.message, str) or not self.message or "\n" in self.message:
            raise ValueError("commit message must be one non-empty line")
        if not isinstance(self.changes, tuple) or not self.changes:
            raise ValueError("transaction requires reviewed changes")
        if not isinstance(self.commit_paths, tuple) or not self.commit_paths:
            raise ValueError("transaction requires commit paths")
        if not isinstance(self.owned_changes, tuple):
            raise ValueError("owned changes must be a tuple")

        all_paths = tuple(change.path for change in self.changes + self.owned_changes)
        for value in all_paths + self.commit_paths:
            if not isinstance(value, str) or not value or "\\" in value:
                raise ValueError("transaction path is not lexical POSIX")
            path = PurePosixPath(value)
            if path.is_absolute() or any(
                part in {"", ".", "..", ".git"} for part in path.parts
            ):
                raise ValueError("transaction path is unsafe")
            if path.as_posix() != value:
                raise ValueError("transaction path is not canonical")

        if len(set(all_paths)) != len(all_paths):
            raise ValueError("transaction paths must be duplicate-free")
        if len(set(self.commit_paths)) != len(self.commit_paths):
            raise ValueError("commit paths must be duplicate-free")
        if set(self.commit_paths) != {change.path for change in self.changes}:
            raise ValueError("commit paths must equal reviewed change paths")
```

Import `PurePosixPath` for this validation. `capture_path_state()` must walk each lexical parent with `os.lstat`, reject every symlink/non-directory, reject a symlink/directory/non-regular leaf, open regular files with `O_NOFOLLOW` where available, compare `fstat` identity to the opened leaf, and return exact bytes plus `stat.S_IMODE(st_mode)`. Absence is allowed only for the final leaf; a missing parent is a conflict because S3 owns destination scaffolding.

- [ ] **Step 5: Implement the typed errors and advisory lock**

Use `git rev-parse --path-format=absolute --git-dir` to derive the lock path. Open the lock file with mode `0o600`, acquire `fcntl.LOCK_EX | fcntl.LOCK_NB`, translate only `EACCES`/`EAGAIN` contention to `VaultBusyError`, and always unlock/close in `finally`. The lock file may persist inside Git metadata; the testable no-leak guarantee is that no OS lock remains and immediate reacquisition succeeds.

```python
class GitTransactionError(Exception):
    pass


class VaultBusyError(GitTransactionError):
    pass


class ReviewedStateConflict(GitTransactionError):
    pass


class GitTransactionFailure(GitTransactionError):
    pass


class GitTransactionRecoveryError(GitTransactionError):
    def __init__(self, paths: tuple[str, ...]) -> None:
        self.paths = tuple(sorted(paths))
        super().__init__("transaction recovery blocked: " + ", ".join(self.paths))
```

- [ ] **Step 6: Run focused tests and confirm GREEN**

```bash
uv run pytest tests/test_git_transaction.py -q
uv run pytest tests/test_scope.py tests/test_destinations.py -q
```

Expected: transaction contract tests pass and S2/S3 path behavior remains green.

- [ ] **Step 7: Commit Task 1**

```bash
git add app/git_transaction.py tests/test_git_transaction.py tests/conftest.py
git commit -m "feat: define isolated approval transaction contract"
```

---

### Task 2: Alternate-index success path and unrelated-state isolation

**Files:**
- Modify: `app/git_transaction.py`
- Modify: `tests/test_git_transaction.py`

**Interfaces:**
- Produces: `execute_transaction(vault: Path, plan: TransactionPlan) -> TransactionResult`.
- Produces internally: `_IndexEntry`, `_capture_unrelated_state`, `_apply_state`, `_stage_in_alternate_index`, `_verify_commit`, and `_sync_reviewed_index`.
- Consumes: Task 1's immutable plan, state capture, path validation, lock, and error family.
- Consumed later: Tasks 3-5 rely on `execute_transaction` without changing its signature.

- [ ] **Step 1: Write failing happy-path isolation and internal-list tests**

Add tests that create two reviewed file changes plus unrelated staged, unstaged, and untracked files. Record exact unrelated bytes, `git ls-files --stage -z`, status, worktree diff, and cached diff before execution. Assert:

```python
result = execute_transaction(vault, plan)

assert result.changed_paths == (
    "synthetic/00-inbox/active/item.md",
    "synthetic/11-library/active/item.md",
)
assert git_changed_paths(vault, result.commit_oid) == list(result.changed_paths)
assert git_head_message(vault) == plan.message
assert source.exists() is False
assert destination.read_bytes() == b"approved\n"
assert proposal.exists() is False
assert unrelated_staged.read_bytes() == staged_bytes
assert unrelated_unstaged.read_bytes() == unstaged_bytes
assert unrelated_untracked.read_bytes() == untracked_bytes
assert unrelated_index_entries_after == unrelated_index_entries_before
```

Include these separate tests:

- `test_list_capable_plan_commits_two_reviewed_changes_once`
- `test_unrelated_staged_unstaged_and_untracked_state_is_preserved_exactly`
- `test_existing_pre_commit_hook_runs_against_only_alternate_index_paths`
- `test_reviewed_staged_change_is_refused_before_filesystem_mutation`
- `test_reviewed_unstaged_change_is_refused_before_filesystem_mutation`
- `test_owned_proposal_mismatch_is_refused_before_filesystem_mutation`
- `test_temporary_alternate_index_is_removed_after_success`

The hook test writes a synthetic executable `.git/hooks/pre-commit` that records `git diff --cached --name-only` outside the repository, then assert it saw exactly `plan.commit_paths` and no unrelated staged path.

- [ ] **Step 2: Run the new success-path tests and confirm RED**

```bash
uv run pytest tests/test_git_transaction.py \
  -k 'list_capable or unrelated or pre_commit or reviewed or owned or alternate_index' -q
```

Expected: failures because `execute_transaction` and alternate-index helpers do not exist.

- [ ] **Step 3: Implement preflight snapshots and alternate-index construction**

The implementation order is fixed:

```python
def execute_transaction(vault: Path, plan: TransactionPlan) -> TransactionResult:
    vault = Path(vault).resolve()
    with _approval_lock(vault):
        start_head = _git_text(vault, "rev-parse", "HEAD").strip()
        reviewed_index = _capture_reviewed_index(vault, plan.commit_paths)
        unrelated = _capture_unrelated_state(vault, plan)
        _require_expected_states(vault, plan)
        _require_reviewed_index_matches_head(vault, start_head, plan.commit_paths)
        return _execute_locked(vault, start_head, reviewed_index, unrelated, plan)
```

`_capture_unrelated_state` stores exact stage-0 index tuples for every non-reviewed path and fingerprints every initially dirty non-owned path as `(porcelain XY, path kind, mode, SHA-256/target/absence)`. It must use `git status --porcelain=v1 -z --untracked-files=all --no-renames` so one record maps to one path.

Create the alternate index with `tempfile.mkstemp(prefix="oneos-index-")`, close and unlink the seed, then run all alternate-index Git commands with a copied environment containing `GIT_INDEX_FILE=temporary_index_path`. Initialize it using `git read-tree start_head`. Do not set or mutate the process environment globally.

- [ ] **Step 4: Implement exact state application, staging, hooks, commit, and real-index sync**

For a regular final state, write a same-directory exclusive temporary file, `fsync`, `chmod`, and `os.replace` it onto the validated lexical leaf; for absence, unlink only a regular non-symlink leaf. Track every successfully written final state for Task 3 rollback.

Then:

```python
_git(vault, "add", "--all", "--", *plan.commit_paths, env=alternate_env)
staged = _name_only(vault, "diff", "--cached", "--name-only", "-z", env=alternate_env)
if tuple(sorted(staged)) != tuple(sorted(plan.commit_paths)):
    raise GitTransactionFailure("alternate index staged an unexpected path")

_git(vault, "commit", "-q", "-m", plan.message, env=alternate_env)
commit_oid = _git_text(vault, "rev-parse", "HEAD").strip()
_verify_commit(vault, commit_oid, start_head, plan.commit_paths)
_sync_reviewed_index(vault, commit_oid, plan.commit_paths)
_verify_reviewed_index_matches_head(vault, commit_oid, plan.commit_paths)
_require_unrelated_state_unchanged(vault, unrelated, plan)
return TransactionResult(commit_oid, tuple(sorted(plan.commit_paths)))
```

`_verify_commit` must assert one parent equal to `start_head`, a subject exactly equal to `plan.message`, and `git diff-tree --no-commit-id --no-renames --name-only -r -z commit_oid` exactly equal to the reviewed set. `_sync_reviewed_index` uses path-limited `git reset -q commit_oid -- path_one path_two` with the real environment; it must never call a broad reset.

- [ ] **Step 5: Run focused and full public tests**

```bash
uv run pytest tests/test_git_transaction.py -q
uv run python -m pytest -q
```

Expected: transaction success/isolation tests pass and the public count remains at least 436.

- [ ] **Step 6: Commit Task 2**

```bash
git add app/git_transaction.py tests/test_git_transaction.py
git commit -m "feat: commit reviewed paths through alternate index"
```

---

### Task 3: Failure injection, exact rollback, and ownership-aware recovery

**Files:**
- Modify: `app/git_transaction.py`
- Modify: `tests/test_git_transaction.py`

**Interfaces:**
- Keeps: `execute_transaction(vault, plan)` unchanged.
- Produces internally: `_checkpoint(name: str) -> None`, used only as a deterministic monkeypatch seam.
- Guarantees: ordinary failures become `GitTransactionFailure` after complete rollback; blocked rollback becomes `GitTransactionRecoveryError` with sorted runtime-relative paths.

- [ ] **Step 1: Write parameterized failure-restoration tests**

Add a fixture that records starting `HEAD`, every owned file/proposal state, reviewed real-index entries, unrelated index entries, status, binary diffs, and temporary-index directory contents. Parameterize these exact checkpoints:

```python
@pytest.mark.parametrize(
    "checkpoint",
    (
        "filesystem-applied",
        "alternate-index-ready",
        "reviewed-paths-staged",
        "commit-created",
        "commit-verified",
        "real-index-synchronized",
    ),
)
def test_failure_at_every_phase_restores_owned_and_unrelated_state(
    tmp_path, monkeypatch, checkpoint
):
    vault, plan, before = prepared_transaction(tmp_path)

    def fail_here(name: str) -> None:
        if name == checkpoint:
            raise OSError(f"injected {name}")

    monkeypatch.setattr(transaction, "_checkpoint", fail_here)
    with pytest.raises(transaction.GitTransactionFailure):
        transaction.execute_transaction(vault, plan)

    assert complete_state(vault) == before
    assert no_oneos_temporary_index_remains()
    assert approval_lock_can_be_reacquired(vault)
```

Add a rejecting-hook case separately so the real `subprocess.CalledProcessError` path is covered, not only checkpoint exceptions.

- [ ] **Step 2: Write failing concurrent same-path recovery tests**

Inject a failure after commit creation, then replace one transaction-owned destination with `b"concurrent replacement\n"` before rollback. Assert the replacement survives, unrelated state remains exact, the exception is `GitTransactionRecoveryError`, and `error.paths` contains only the destination runtime-relative path. Add the equivalent `HEAD` ownership test: change `HEAD` after the transaction's commit and prove rollback does not overwrite the newer ref.

- [ ] **Step 3: Run rollback tests and confirm RED**

```bash
uv run pytest tests/test_git_transaction.py \
  -k 'failure_at_every_phase or rejecting_hook or concurrent_same_path or head_ownership' -q
```

Expected: failures because failure checkpoints and rollback orchestration are incomplete.

- [ ] **Step 4: Implement ownership-aware rollback in reverse write order**

Before mutation, retain `start_head`, reviewed index entries, expected owned states, and unrelated fingerprints. After each successful `_apply_state`, record `(path, state_written)`. Rollback must:

1. For each written path in reverse order, recapture current state.
2. Restore its `before` state only when current equals `state_written`.
3. Otherwise preserve current state and append the path to `blocked_paths`.
4. Restore reviewed real-index entries individually with `git update-index --force-remove -- relative_path` for an originally absent entry or `git update-index --add --cacheinfo original_mode original_oid relative_path` for an original stage-0 entry.
5. Move `HEAD` back only with compare-and-swap semantics: `git update-ref HEAD start_head transaction_commit_oid`. If `HEAD` is no longer owned, record `HEAD` as blocked and preserve it.
6. Recheck unrelated index/worktree fingerprints and never attempt to repair unrelated paths.
7. Remove the alternate index and same-directory temporary files in `finally`, then release the advisory lock.

Raise `GitTransactionRecoveryError(tuple(blocked_paths))` if anything is not transaction-owned at recovery time. Otherwise raise `GitTransactionFailure("approval transaction failed and was rolled back")` from the original exception.

- [ ] **Step 5: Run transaction and relevant Git regressions**

```bash
uv run pytest tests/test_git_transaction.py -q
uv run pytest tests/test_ingest_commit.py tests/test_rename.py -q
uv run python -m pytest -q
```

Expected: all failure points restore exact starting state, concurrent replacements survive, S1 intake and existing rename remain unchanged, and the complete suite passes.

- [ ] **Step 6: Commit Task 3**

```bash
git add app/git_transaction.py tests/test_git_transaction.py
git commit -m "feat: roll back failed approval transactions safely"
```

---

### Task 4: Route classification approval through the isolated transaction

**Files:**
- Modify: `app/outbox.py`
- Modify: `tests/test_outbox.py`
- Modify: `tests/test_app.py`

**Interfaces:**
- Produces: `OutboxTransactionError(OutboxError)` wrapping any `GitTransactionError` after S2-S4 checks.
- Keeps: `approve(scope: Scope, proposal_id: str) -> Proposal` and every public route signature unchanged.
- Consumes: `PathState`, `PathChange`, `TransactionPlan`, `capture_path_state`, and `execute_transaction` from Task 3.

- [ ] **Step 1: Write failing classification isolation and rollback tests**

Add these tests to `tests/test_outbox.py`:

- `test_approval_with_unrelated_staged_unstaged_and_untracked_work_commits_only_source_and_destination`
- `test_reviewed_source_or_destination_index_dirt_is_refused_before_mutation`
- `test_approval_busy_error_preserves_source_destination_proposal_and_git_state`
- `test_injected_transaction_failure_restores_source_destination_and_exact_proposal_bytes`
- `test_approval_transaction_error_is_an_outbox_error`
- `test_classification_approval_still_creates_exactly_one_commit_and_one_revert_restores_both_paths`
- `test_public_routes_remain_single_proposal_actions`

The isolation test must compare unrelated file bytes and full stage entries before/after, and assert `git_changed_paths(vault) == sorted([prop.src, prop.dst])`. The revert test starts from the real folder-adapter receipt as the existing S1 regression does, adds unrelated dirt, approves, reverts only the approval OID, and proves the tracked receipt returns to triage while unrelated dirt is byte-identical.

Add one route test to `tests/test_app.py` that monkeypatches `app.outbox.execute_transaction` to raise `GitTransactionFailure`, posts the existing approval route, and proves there is no 500, no move, no commit, and no general new S6 error panel.

- [ ] **Step 2: Run focused tests and confirm RED**

```bash
uv run pytest tests/test_outbox.py tests/test_app.py \
  -k 'unrelated_staged or index_dirt or busy_error or injected_transaction or transaction_error or single_proposal' -q
```

Expected: failures because approval still uses the real index and direct `git mv/add/commit`.

- [ ] **Step 3: Build the classification transaction only after S2-S4 validation**

Keep the existing order: load canonical proposal, validate request scope/destination, take the no-follow source snapshot, verify `source_sha256`, derive approved UTF-8 bytes, and revalidate the persisted proposal. Then build:

```python
source_state = capture_path_state(vault, prop.src)
if source_state.contents != source_bytes:
    raise StaleProposalSource("proposal source has changed")

proposal_rel = prop.path.relative_to(vault).as_posix()
proposal_state = capture_path_state(vault, proposal_rel)
persisted = _to_proposal(prop.path, yaml.safe_load(proposal_state.contents))
persisted = _require_destination(scope, persisted)
if persisted != prop:
    raise OutboxDestinationError("proposal changed since it was loaded")

plan = TransactionPlan(
    message=f"outbox: approve {prop.id} ({prop.src} → {prop.dst})",
    changes=(
        PathChange(prop.src, source_state, PathState.absent()),
        PathChange(
            prop.dst,
            PathState.absent(),
            PathState.regular(approved_bytes, source_state.mode),
        ),
    ),
    commit_paths=(prop.src, prop.dst),
    owned_changes=(
        PathChange(proposal_rel, proposal_state, PathState.absent()),
    ),
)
```

Catch `GitTransactionError` only around `execute_transaction(vault, plan)` and raise `OutboxTransactionError("classification approval transaction failed") from exc`. Remove the approval-only `_git`, `git mv`, real-index add, unlink, and direct commit. Do not change proposal creation, preview, reject, intake, routes, or S4 error types.

- [ ] **Step 4: Run focused S1-S4 and route regressions**

```bash
uv run pytest tests/test_outbox.py tests/test_app.py -q
uv run pytest tests/test_ingest_commit.py tests/test_scope.py tests/test_destinations.py tests/test_proposal_identity.py -q
uv run python -m pytest -q
```

Expected: classification approval uses one isolated commit; all S1-S4 and route behavior remains green.

- [ ] **Step 5: Commit Task 4**

```bash
git add app/outbox.py tests/test_outbox.py tests/test_app.py
git commit -m "feat: isolate classification approval commits"
```

---

### Task 5: Route approved registry deletion through the isolated transaction

**Files:**
- Modify: `app/registry.py`
- Modify: `tests/test_registry.py`
- Modify: `tests/test_app.py`

**Interfaces:**
- Produces: `RegistryTransactionError(RegistryError)` wrapping transaction failures.
- Keeps: `execute_delete(scope: Scope, proposal_id: str) -> None`, direct `add_workspace`, and every registry route unchanged.
- Refines internally: `_remove_scoped_registry_value` becomes a pure renderer over exact input bytes rather than writing or rereading the file.

- [ ] **Step 1: Write failing registry transaction tests**

Add these cases:

- `test_delete_with_unrelated_staged_unstaged_and_untracked_work_commits_only_registry_file`
- `test_dirty_reviewed_registry_is_refused_before_proposal_or_registry_mutation`
- `test_registry_delete_busy_error_preserves_exact_state`
- `test_registry_delete_commit_failure_restores_registry_and_proposal_bytes`
- `test_registry_transaction_error_is_a_registry_error`
- `test_registry_delete_is_one_commit_and_one_revert_restores_every_registry_key`
- `test_direct_registry_add_still_uses_existing_direct_flow`

The one-revert test deletes an unreferenced synthetic product, records `approval_oid`, runs `git revert --no-edit approval_oid`, and compares the whole registry bytes to the pre-approval bytes. The exact changed-path assertion is `['_system/products.yaml']`; the proposal remains untracked and never enters the commit.

- [ ] **Step 2: Run focused tests and confirm RED**

```bash
uv run pytest tests/test_registry.py tests/test_app.py \
  -k 'unrelated_staged or dirty_reviewed_registry or busy_error or commit_failure or transaction_error or one_revert or direct_registry_add' -q
```

Expected: failures because `execute_delete` writes, unlinks, stages, and commits directly.

- [ ] **Step 3: Render from exact snapshots and execute one registry transaction**

After proposal identity/scope validation and the fresh reference recount, capture the registry and proposal states with `capture_path_state`. Parse the exact registry bytes, remove only the selected entity's selected kind/slug, and render UTF-8 bytes without touching disk. Build:

```python
plan = TransactionPlan(
    message=f"registry: delete {prop.kind} {prop.slug}",
    changes=(
        PathChange(
            registry_rel,
            registry_state,
            PathState.regular(rendered_registry_bytes, registry_state.mode),
        ),
    ),
    commit_paths=(registry_rel,),
    owned_changes=(
        PathChange(proposal_rel, proposal_state, PathState.absent()),
    ),
)
```

Reparse the captured proposal bytes and require its canonical filename/id, entity, action, kind, and slug to equal `prop` before transaction start. Translate `GitTransactionError` to `RegistryTransactionError`. Remove the delete-only direct write, proposal unlink, real-index add, and direct commit. Do not migrate `add_workspace` or another direct registry mutation.

- [ ] **Step 4: Run registry, outbox, route, and full regressions**

```bash
uv run pytest tests/test_registry.py tests/test_outbox.py tests/test_app.py -q
uv run pytest tests/test_scope.py tests/test_proposal_identity.py -q
uv run python -m pytest -q
```

Expected: both approved action families use the service, direct registry add remains direct, and the public suite passes.

- [ ] **Step 5: Commit Task 5**

```bash
git add app/registry.py tests/test_registry.py tests/test_app.py
git commit -m "feat: isolate approved registry deletion"
```

---

### Task 6: Make Gate 3 action/path-aware and fingerprint dirty session state

**Files:**
- Rewrite: `tools/gate3_audit.py`
- Rewrite: `tests/test_gate3_audit.py`

**Interfaces:**
- Produces: `PathChangeRecord(status: str, path: str)` and `CommitRecord(oid: str, message: str, parents: tuple[str, ...], changes: tuple[PathChangeRecord, ...])`.
- Produces: `DirtyFingerprint(status: str, index_entry: str | None, kind: str, mode: int | None, digest: str | None)`.
- Produces: `Audit(sanctioned_commits: list[str], violating_commits: list[str], sanctioned_writes: list[str], violating_writes: list[str])` with `ok` true only when both violation lists are empty.
- Produces: version-2 snapshot JSON with `head` and a mapping of every initially dirty path to its fingerprint.
- Produces: `AuditRules.load(vault: Path) -> AuditRules`, using `EntityCatalog` and `Vault` to map manifest entities to active runtime modules.
- Produces: `audit_commits(records: tuple[CommitRecord, ...], rules: AuditRules, vault: Path) -> Audit` and `audit_dirty(before: dict[str, DirtyFingerprint], after: dict[str, DirtyFingerprint], rules: AuditRules, vault: Path) -> Audit`.
- Keeps: `python -m tools.gate3_audit snapshot|check` and external `ONEOS_VAULT`/`GATE3_SNAP` configuration.

- [ ] **Step 1: Write failing action-specific commit tests**

Replace the prefix-only tests with Git-backed synthetic cases covering:

```python
@pytest.mark.parametrize(
    ("message", "changes", "valid"),
    [
        ("ingest: add redacted receipt", (("A", "synthetic/00-inbox/active/r.md"),), True),
        ("ingest: misleading", (("A", "synthetic/11-library/active/r.md"),), False),
        ("ingest: two", (("A", "synthetic/00-inbox/active/a.md"),
                         ("A", "synthetic/00-inbox/active/b.md")), False),
        ("outbox: approve p", (("D", "synthetic/00-inbox/active/r.md"),
                                ("A", "synthetic/11-library/active/r.md")), True),
        ("outbox: misleading", (("M", "_system/entities.yaml"),), False),
        ("registry: delete product x", (("M", "_system/products.yaml"),), True),
        ("registry: delete product x", (("M", "_system/members.yaml"),), False),
        ("registry: add workspace x", (("M", "_system/workspaces.yaml"),), True),
        ("unknown: edit", (("M", "_system/products.yaml"),), False),
    ],
)
def test_message_and_changed_paths_must_both_be_sanctioned(
    tmp_path, message, changes, valid
):
    vault = _audit_vault(tmp_path)
    rules = AuditRules.load(vault)
    record = CommitRecord(
        oid="f" * 40,
        message=message,
        parents=("e" * 40,),
        changes=tuple(
            PathChangeRecord(status=status, path=path)
            for status, path in changes
        ),
    )

    result = audit_commits((record,), rules, vault)

    assert result.ok is valid
    assert bool(result.violating_commits) is (not valid)
```

Define `_audit_vault()` with the shared synthetic `entities.yaml` and `archetypes.yaml` fixture shape. Add cross-entity outbox paths, mismatched leaf names, inactive/unknown destination modules, non-Markdown ingest leaves, merge commits, and status types other than the required A/D/M cases.

- [ ] **Step 2: Write valid/invalid rename-envelope tests without changing rename flow**

Create a parent synthetic vault, call existing `plan_rename`/`apply_rename` for each supported axis, and assert Gate 3 accepts its resulting commit. Create a sibling malicious commit with the same `rename:` prefix plus one unrelated path and assert rejection.

For audit reconstruction, keep the existing message shape `rename: old_slug → new_slug`. Materialize the commit parent into a temporary directory using a temporary alternate index plus `git read-tree parent_oid` and `git checkout-index --all --prefix=temp_tree/`. Try each existing `AXES` planner against that parent. For each successful plan, derive its exact allowed path envelope from `plan.edits` and tracked files beneath every `plan.moves` source/destination; accept only if one candidate envelope exactly equals the commit's no-renames path set. This audits the existing sanctioned rename planner without moving rename into the new service or changing its commit format.

- [ ] **Step 3: Write failing snapshot/fingerprint and dirty-write tests**

Cover:

- a new canonical pending classification proposal is allowed;
- a new canonical pending registry-delete proposal is allowed;
- malformed YAML, mismatched proposal id/filename, wrong entity, non-pending status, or unknown action under `outbox/` is refused;
- a new uncommitted inbox receipt is refused;
- a new curated/module write is refused;
- a path already dirty at snapshot that changes bytes is refused;
- a path already staged at snapshot whose index OID/stage changes is refused;
- disappearance of an initially dirty path is refused;
- an unchanged initially dirty path remains baseline state and is not misclassified as a new write;
- status parsing uses `--no-renames` and handles spaces without slicing path bytes incorrectly.

- [ ] **Step 4: Run Gate 3 tests and confirm RED**

```bash
uv run pytest tests/test_gate3_audit.py -q
```

Expected: current prefix/glob classifier fails action-path and fingerprint requirements.

- [ ] **Step 5: Implement versioned snapshot and exact commit collection**

Snapshot JSON shape:

```json
{
  "version": 2,
  "head": "ffffffffffffffffffffffffffffffffffffffff",
  "dirty": {
    "relative/path": {
      "status": " M",
      "index_entry": "100644:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee:0",
      "kind": "file",
      "mode": 420,
      "digest": "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"
    }
  }
}
```

Use SHA-256 for regular-file bytes, SHA-256 of the link target bytes for unrelated symlinks, explicit `absence` for deletions, and exact stage-0 index metadata from `git ls-files --stage -z`. Collect commit OIDs with `git rev-list --reverse snapshot_head..HEAD`; for each `commit_oid`, collect full subject, parents, and `git diff-tree --no-commit-id --no-renames --name-status -r -z commit_oid`.

- [ ] **Step 6: Implement runtime action validators and dirty comparison**

Load `EntityCatalog` from the vault and `Vault(catalog).active_modules_for(Scope(vault, entity))` for each manifest entity. Validators must enforce:

- `ingest:`: one parent, exactly one `A`, manifest entity, `00-inbox/active`, direct `.md` leaf.
- `outbox:`: one parent, exactly one `D` inbox source and one `A` active destination, same manifest entity, same direct filename, destination module active at runtime, no `outbox`, `staging`, or `_system` destination.
- `registry:`: one parent and exactly one A/M path; parse `add|edit|delete` plus `workspace|product|member`, then require its conventional `_system/{workspaces|products|members}.yaml` path.
- `rename:`: one parent and exact equality to one reconstructed existing-planner envelope.
- anything else: violation, even when its subject starts with a sanctioned-looking word.

For dirty state, compare the union of baseline and current paths. Any baseline fingerprint difference or disappearance is a violation. A genuinely new dirty path is allowed only when it is `??`, a direct `.yaml` leaf under a manifest entity's lexical real `outbox/`, and its no-follow bytes parse to a canonical pending `classify` or `delete` proposal whose stored entity/id/action agrees with the runtime path. All uncommitted inbox receipts are violations.

- [ ] **Step 7: Run Gate 3, Git-flow, and full tests**

```bash
uv run pytest tests/test_gate3_audit.py tests/test_ingest_commit.py \
  tests/test_outbox.py tests/test_registry.py tests/test_rename.py -q
uv run python -m pytest -q
```

Expected: every sanctioned action/path pair passes, misleading prefixes fail, dirty-state changes are visible, and all existing flows remain green.

- [ ] **Step 8: Commit Task 6**

```bash
git add tools/gate3_audit.py tests/test_gate3_audit.py
git commit -m "feat: audit sanctioned commit paths and dirty state"
```

---

### Task 7: Whole-branch verification and read-only private integration gates

**Files:**
- Modify only if a discovered regression requires an in-scope S5 fix: files already named in Tasks 1-6 and their tests.
- Do not modify Grey Matter.

**Interfaces:**
- Verifies all S5 behavioral contracts, S1-S4 regressions, public/privacy gates, and private-vault immutability.
- Produces no route, UI, schema, dependency, deployment, or S6 behavior.

- [ ] **Step 1: Apply `superpowers:verification-before-completion`**

Read that skill before making any completion claim. Run fresh commands; do not rely on Task 1-6 outputs.

- [ ] **Step 2: Run focused and full public verification**

```bash
uv run pytest tests/test_git_transaction.py -q
uv run pytest tests/test_outbox.py tests/test_registry.py tests/test_gate3_audit.py -q
uv run pytest tests/test_ingest_commit.py tests/test_scope.py tests/test_destinations.py \
  tests/test_proposal_identity.py tests/test_app.py tests/test_folder_adapter.py \
  tests/test_email_adapter.py tests/test_rename.py -q
uv run python -m pytest -q
```

Expected: all focused groups and the complete public suite pass; the count is greater than 436 because S5 adds tests.

- [ ] **Step 3: Capture private-vault pre-gate fingerprints without printing private content**

Require `ONEOS_VAULT` to be set and verify its revision is `2aa8b14` or newer. Then:

```bash
S5_PRIVATE_PROOF=$(mktemp -d /private/tmp/oneos-s5-private.XXXXXX)
git -C "$ONEOS_VAULT" rev-parse HEAD > "$S5_PRIVATE_PROOF/head.before"
git -C "$ONEOS_VAULT" status --porcelain=v1 -z > "$S5_PRIVATE_PROOF/status.before"
git -C "$ONEOS_VAULT" diff --binary > "$S5_PRIVATE_PROOF/worktree.before"
git -C "$ONEOS_VAULT" diff --cached --binary > "$S5_PRIVATE_PROOF/cached.before"
shasum -a 256 "$S5_PRIVATE_PROOF"/*.before
```

Keep the proof directory outside both repositories. Do not clean or normalize a pre-existing private state; the post-gate proof must equal whatever was present before.

- [ ] **Step 4: Run the full private gate read-only**

```bash
(cd "$ONEOS_VAULT/_system/scripts" && python3 -m unittest discover -q)
python3 "$ONEOS_VAULT/_system/scripts/check_v2.py" "$ONEOS_VAULT"
python3 "$ONEOS_VAULT/_system/scripts/policy_enforcer.py" \
  --policy "$ONEOS_VAULT/_system/scripts/action-policy.yaml" test-suite
```

Expected: 34 or more private tests pass, `check_v2` ends with `0 error(s), 0 warning(s)`, and the policy self-test passes.

- [ ] **Step 5: Run pinned secret and repository audits**

```bash
tools/run_gitleaks.sh .
uv run python -m tools.public_repo_audit --repo . --history
uv run python -m tools.public_repo_audit \
  --repo . --vault "$ONEOS_VAULT" --history
```

Expected: pinned Gitleaks 8.30.1, public audit, and combined registry-derived audit all exit 0.

- [ ] **Step 6: Prove the private vault is exactly unchanged**

```bash
git -C "$ONEOS_VAULT" rev-parse HEAD > "$S5_PRIVATE_PROOF/head.after"
git -C "$ONEOS_VAULT" status --porcelain=v1 -z > "$S5_PRIVATE_PROOF/status.after"
git -C "$ONEOS_VAULT" diff --binary > "$S5_PRIVATE_PROOF/worktree.after"
git -C "$ONEOS_VAULT" diff --cached --binary > "$S5_PRIVATE_PROOF/cached.after"
cmp "$S5_PRIVATE_PROOF/head.before" "$S5_PRIVATE_PROOF/head.after"
cmp "$S5_PRIVATE_PROOF/status.before" "$S5_PRIVATE_PROOF/status.after"
cmp "$S5_PRIVATE_PROOF/worktree.before" "$S5_PRIVATE_PROOF/worktree.after"
cmp "$S5_PRIVATE_PROOF/cached.before" "$S5_PRIVATE_PROOF/cached.after"
shasum -a 256 "$S5_PRIVATE_PROOF"/*.after
```

Expected: all four `cmp` commands exit 0. If any differs, stop and report; do not alter Grey Matter to make the proof pass.

- [ ] **Step 7: Run diff hygiene and inspect exact branch scope**

```bash
git diff --check 3c56119..HEAD
git status --short --branch
git log --oneline --decorate 3c56119..HEAD
git diff --stat 3c56119..HEAD
git diff --name-only 3c56119..HEAD
```

Expected: only the approved design, this plan, S5 transaction/audit implementation, and S5 tests appear; no private value, dependency file change, route/UI expansion, or S6 behavior appears.

- [ ] **Step 8: Run final whole-branch review**

Dispatch one fresh reviewer over `3c56119..HEAD` with the approved design and this plan. Require explicit findings-first review for transaction correctness, Git/index isolation, rollback ownership, action/path audit completeness, S1-S4 preservation, instance leakage, and non-goal drift. Return every valid finding to the relevant task's original implementer, rerun its focused tests, then repeat Steps 2-7.

- [ ] **Step 9: Commit only an in-scope verification fix if one was required**

If review or fresh verification required code changes, commit them with an exact message after their focused red-green cycle. If no files changed, do not create an empty verification commit.

- [ ] **Step 10: Stop for publication/integration authorization**

Report fresh test counts, private-gate outputs, fingerprint equality, branch commits, remaining known limitations, and final-review disposition. Do not push, open a pull request, merge, or remove the worktree.

---

## Execution Handoff

After human approval, commit this plan alone first:

```bash
git add docs/superpowers/plans/2026-08-16-s5-isolated-git-transaction-audit.md
git commit -m "docs: plan S5 isolated transaction implementation"
```

Then execute with `superpowers:subagent-driven-development` as already selected by the delegation. For each task, use one fresh implementer and serial requirements/code-quality reviewer loops; do not parallelize implementers against shared Git state. Apply `superpowers:test-driven-development` inside every task and `superpowers:verification-before-completion` before Task 7 completion claims.
