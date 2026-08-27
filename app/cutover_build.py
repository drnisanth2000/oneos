"""cutover_build.py — build the single cutover commit in isolation.

Nothing is written to the live vault. Every edit happens in a temporary
detached linked worktree, which shares the vault's object database, so the
resulting commit is already reachable from the vault and promotion is a
fast-forward rather than a file copy.

A failure before promotion discards the worktree. The live vault was never
touched, so there is nothing to roll back, and no `reset --hard` or
`clean -fd` is ever issued against it.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
import shutil
import subprocess
import tempfile


class CutoverError(Exception):
    pass


class CutoverCommittedError(CutoverError):
    """The promotion committed but confirmation or cleanup failed."""


def git(root: Path, *args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args], cwd=root, check=True, capture_output=True, text=True
        ).stdout
    except subprocess.CalledProcessError as exc:
        raise CutoverError(f"git {args[0]} failed") from exc


@contextmanager
def isolated_worktree(vault: Path, source_head: str) -> Iterator[Path]:
    """A throwaway **detached** worktree at `source_head`, removed on exit.

    Detached on purpose. A named branch would have to be cleaned up, and a
    cleanup that deletes a branch it did not create can destroy an unrelated
    one when creation failed precisely because that branch already existed.
    Creating no ref means there is no ref to delete.
    """
    if git(vault, "rev-parse", "HEAD").strip() != source_head:
        raise CutoverError("vault HEAD is not the recorded source HEAD")
    parent = Path(tempfile.mkdtemp(prefix="oneos-cutover-"))
    scratch = parent / "tree"
    try:
        git(vault, "worktree", "add", "--quiet", "--detach", str(scratch), source_head)
        yield scratch
    finally:
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(scratch)],
            cwd=vault, check=False, capture_output=True,
        )
        shutil.rmtree(parent, ignore_errors=True)
