#!/usr/bin/env python3
"""Gate 3 audit — zero DIRECT vault writes across a session (spec §11.3).

    ONEOS_VAULT=/path/to/vault python -m tools.gate3_audit snapshot
    # ... use the app for a full session ...
    ONEOS_VAULT=/path/to/vault python -m tools.gate3_audit check

A vault change is SANCTIONED iff it is either:
  * a commit whose message starts with an approved prefix
    (outbox: / rename: / registry:), or
  * an uncommitted working-tree entry under <entity>/00-inbox/active/ (ingest)
    or <entity>/outbox/ (a proposal).
Anything else is a DIRECT write — a gate-3 violation.

The snapshot is written OUTSIDE the vault (cwd by default) so the audit tool
never itself writes to the vault it is auditing.
"""
from __future__ import annotations

import fnmatch
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

OK_PREFIXES = ("outbox:", "rename:", "registry:")
OK_GLOBS = ("*/00-inbox/active/*", "*/outbox/*")


@dataclass
class Audit:
    sanctioned_commits: list[str] = field(default_factory=list)
    violating_commits: list[str] = field(default_factory=list)
    sanctioned_writes: list[str] = field(default_factory=list)
    violating_writes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violating_commits and not self.violating_writes


def audit(commit_messages: list[str], dirty_paths: list[str]) -> Audit:
    """Pure classifier — no git, no filesystem. This is the tested core."""
    a = Audit()
    for msg in commit_messages:
        (a.sanctioned_commits if msg.startswith(OK_PREFIXES)
         else a.violating_commits).append(msg)
    for path in dirty_paths:
        (a.sanctioned_writes if any(fnmatch.fnmatch(path, g) for g in OK_GLOBS)
         else a.violating_writes).append(path)
    return a


# --- CLI (git + filesystem glue) -------------------------------------------

def _vault() -> Path:
    return Path(os.environ["ONEOS_VAULT"]).expanduser()


def _snap_path() -> Path:
    return Path(os.environ.get("GATE3_SNAP", "./.gate3-snapshot.json"))


def _git(vault: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=vault, capture_output=True, text=True).stdout


def _porcelain(vault: Path) -> list[str]:
    return sorted(l[3:] for l in _git(vault, "status", "--porcelain").splitlines() if l)


def cmd_snapshot() -> int:
    vault = _vault()
    snap = {"head": _git(vault, "rev-parse", "HEAD").strip(), "dirty": _porcelain(vault)}
    _snap_path().write_text(json.dumps(snap, indent=2))
    print(f"snapshot: HEAD={snap['head'][:8]} dirty={len(snap['dirty'])} -> {_snap_path()}")
    return 0


def cmd_check() -> int:
    vault = _vault()
    snap = json.loads(_snap_path().read_text())
    messages = [l for l in _git(vault, "log", "--pretty=%s", f"{snap['head']}..HEAD").splitlines() if l]
    new_dirty = [p for p in _porcelain(vault) if p not in set(snap["dirty"])]
    a = audit(messages, new_dirty)

    print(f"GATE 3 — {len(messages)} new commit(s), {len(new_dirty)} new working-tree change(s)")
    print(f"  sanctioned commits: {len(a.sanctioned_commits)}")
    print(f"  ingest/proposal writes: {len(a.sanctioned_writes)}")
    for c in a.violating_commits:
        print(f"  VIOLATION commit: {c}")
    for p in a.violating_writes:
        print(f"  VIOLATION direct write: {p}")
    print("GATE 3:", "PASS" if a.ok else "FAIL")
    return 0 if a.ok else 1


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    cmd = argv[0] if argv else ""
    if cmd == "snapshot":
        return cmd_snapshot()
    if cmd == "check":
        return cmd_check()
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
