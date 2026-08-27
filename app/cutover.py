"""cutover.py — promotion under quiesce and the shared action lock, and the CLI.

`git merge --ff-only` advances the ref atomically, but updating the working
tree is many creates, deletes, and renames. A process reading the vault during
that window can observe a half-migrated tree, so every writer must be stopped
first. The shared action lock is taken as well, for the cooperative OneOS
writers it does govern — an addition to the quiesce, never a substitute, since
Hermes, parsers, and adapters need not take it.
"""
from __future__ import annotations

from pathlib import Path
import subprocess

from .cutover_build import (
    CutoverCommittedError,
    CutoverError,
    build_cutover,
    git,
)
from .cutover_inventory import require_clean_entities
from .git_transaction import (
    ActionLockCleanupFailure,
    GitTransactionFailure,
    VaultBusyError,
    action_lock,
)


def _status_bytes(vault: Path) -> bytes:
    return subprocess.run(
        ["git", "status", "--porcelain=v2", "--untracked-files=all"],
        cwd=vault, check=True, capture_output=True,
    ).stdout


def promote(
    vault: Path,
    built_commit: str,
    source_head: str,
    expected_status: bytes,
    affected_entities: list[str],
) -> str:
    """Fast-forward the live vault to the commit built in isolation.

    The caller must already have stopped OneOS, Hermes, and every parser and
    adapter. This function takes the shared action lock, repeats every
    precheck, and only then moves the ref.
    """
    committed = False
    try:
        with action_lock(vault):
            if git(vault, "rev-parse", "HEAD").strip() != source_head:
                raise CutoverError(
                    "live HEAD moved since the build; re-run from inventory"
                )
            if _status_bytes(vault) != expected_status:
                raise CutoverError(
                    "live status changed since the build; re-run from inventory"
                )
            # Repeated here because ignored content can appear at any moment,
            # and a linked worktree could never have carried it.
            require_clean_entities(vault, affected_entities)

            completed = subprocess.run(
                ["git", "merge", "--ff-only", built_commit],
                cwd=vault, check=False, capture_output=True, text=True,
            )
            if completed.returncode != 0:
                raise CutoverError(
                    "fast-forward promotion refused; the vault is unchanged"
                )
            committed = True
            try:
                confirmed = subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=vault, check=True, capture_output=True, text=True,
                ).stdout.strip()
                if confirmed != built_commit:
                    raise CutoverCommittedError(
                        "the cutover command succeeded but confirmed HEAD does not "
                        "equal the built commit; do not retry"
                    )
                return confirmed
            except (OSError, subprocess.CalledProcessError) as exc:
                raise CutoverCommittedError(
                    "the cutover committed but its id could not be read; "
                    "do not retry"
                ) from exc
    except ActionLockCleanupFailure as exc:
        if not committed:
            raise CutoverError(
                "shared action lock cleanup failed before the cutover committed"
            ) from exc
        raise CutoverCommittedError(
            "the cutover committed but the action lock could not be released; "
            "do not retry"
        ) from exc
    except VaultBusyError as exc:
        raise CutoverError(
            "vault is busy; another OneOS action is already running"
        ) from exc
    except GitTransactionFailure as exc:
        if committed:
            raise CutoverCommittedError(
                "the cutover committed but the lock layer failed; do not retry"
            ) from exc
        raise CutoverError(
            "shared action lock is unavailable; the cutover was not started"
        ) from exc
