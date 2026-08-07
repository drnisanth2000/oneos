"""outbox.py — the one write path (invariant 1).

The app never mutates curated vault content directly. Confirming a triage
classification writes a *proposal* into `<entity>/outbox/` describing the move;
nothing is moved yet (step 7). Approval performs the real move and commits, as
exactly one revertible commit; reject discards the proposal (step 8).

Proposals are plain YAML under `outbox/`, which is a system area excluded from
block-mapping validation — so it never trips check_v2 or the module lint.
"""
from __future__ import annotations

import difflib
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml

from .inbox import split_front_matter
from .scope import Scope


@dataclass
class Proposal:
    id: str
    path: Path
    action: str
    entity: str
    src: str          # vault-relative source path
    dst: str          # vault-relative destination path
    module: str
    sub: str
    block: str
    rule_id: str | None
    created: str
    status: str = "pending"


def _rel(scope: Scope, path: Path) -> str:
    return str(Path(path).resolve().relative_to(scope.root.resolve()))


def propose_classification(
    scope: Scope,
    entity: str,
    item_path: Path,
    *,
    module: str,
    sub: str,
    block: str,
    rule_id: str | None = None,
) -> Proposal:
    """Write a classify proposal. Moves nothing."""
    item_path = Path(item_path)
    filename = item_path.name
    src_rel = _rel(scope, item_path)
    dst_rel = str(Path(entity) / module / "active" / filename)
    created = datetime.now().isoformat(timespec="seconds")
    pid = f"{datetime.now():%Y%m%dT%H%M%S}-{item_path.stem}"

    record = {
        "id": pid,
        "action": "classify",
        "entity": entity,
        "created": created,
        "status": "pending",
        "src": src_rel,
        "dst": dst_rel,
        "module": module,
        "sub": sub,
        "block": block,
        "rule_id": rule_id,
    }
    outbox = scope.resolve(entity, "outbox")
    outbox.mkdir(parents=True, exist_ok=True)
    path = outbox / f"{pid}.yaml"
    path.write_text(yaml.safe_dump(record, sort_keys=False), encoding="utf-8")
    return _to_proposal(path, record)


def _to_proposal(path: Path, record: dict) -> Proposal:
    return Proposal(
        id=record["id"],
        path=path,
        action=record.get("action", "classify"),
        entity=record["entity"],
        src=record["src"],
        dst=record["dst"],
        module=record["module"],
        sub=record["sub"],
        block=record.get("block", ""),
        rule_id=record.get("rule_id"),
        created=record.get("created", ""),
        status=record.get("status", "pending"),
    )


def load_proposals(scope: Scope, entity: str) -> list[Proposal]:
    outbox = scope.resolve(entity, "outbox")
    if not outbox.is_dir():
        return []
    props = []
    for p in sorted(outbox.glob("*.yaml")):
        record = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        if record.get("action") == "classify":
            props.append(_to_proposal(p, record))
    return props


def _apply_sub(text: str, sub: str) -> str:
    """The move's only content change: the `sub:` front-matter value. `block`
    is derived from the module, never written per file (conventions v2 §1)."""
    if re.search(r"(?m)^sub:\s*.*$", text):
        return re.sub(r"(?m)^sub:\s*.*$", f"sub: {sub}", text, count=1)
    # no sub line yet — insert one just before the closing front-matter fence
    fm_end = text.find("---", 3)
    if fm_end != -1:
        return text[:fm_end] + f"sub: {sub}\n" + text[fm_end:]
    return text


def preview_diff(scope: Scope, proposal: Proposal) -> str:
    """A unified diff previewing what approval would do — the file moving from
    src to dst with `sub:` updated. Reads only; renders, never moves."""
    src_path = scope.root / proposal.src
    old = src_path.read_text(encoding="utf-8") if src_path.exists() else ""
    new = _apply_sub(old, proposal.sub)
    diff = difflib.unified_diff(
        old.splitlines(True), new.splitlines(True),
        fromfile=f"a/{proposal.src}", tofile=f"b/{proposal.dst}",
    )
    header = f"move: {proposal.src} → {proposal.dst}\n"
    return header + "".join(diff)


def _git(vault: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=vault, check=True, capture_output=True, text=True
    ).stdout


class OutboxError(Exception):
    pass


def get_proposal(scope: Scope, entity: str, proposal_id: str) -> Proposal:
    for p in load_proposals(scope, entity):
        if p.id == proposal_id:
            return p
    raise OutboxError(f"no pending proposal {proposal_id!r} for {entity}")


def approve(scope: Scope, entity: str, proposal_id: str) -> Proposal:
    """Perform the proposed move and commit it — exactly one revertible commit.
    The proposal file is untracked, so it never enters git; deleting it leaves a
    clean tree after the commit."""
    prop = get_proposal(scope, entity, proposal_id)
    vault = scope.root
    src = vault / prop.src
    dst = vault / prop.dst
    if not src.exists():
        raise OutboxError(f"source no longer exists: {prop.src}")

    dst.parent.mkdir(parents=True, exist_ok=True)
    _git(vault, "mv", prop.src, prop.dst)          # rename (original content)
    dst.write_text(_apply_sub(dst.read_text(encoding="utf-8"), prop.sub),
                   encoding="utf-8")               # the sub: change
    _git(vault, "add", prop.dst)
    prop.path.unlink(missing_ok=True)              # untracked proposal — just drop it
    _git(vault, "commit", "-q", "-m",
         f"outbox: approve {prop.id} ({prop.src} → {prop.dst})")
    return prop


def reject(scope: Scope, entity: str, proposal_id: str) -> Proposal:
    """Discard the proposal. No move, no commit — the proposal was never
    tracked."""
    prop = get_proposal(scope, entity, proposal_id)
    prop.path.unlink(missing_ok=True)
    return prop
