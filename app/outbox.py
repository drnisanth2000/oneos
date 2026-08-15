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
import hashlib
import os
import re
import stat
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml

from .inbox import split_front_matter
from .destinations import DestinationError, resolve_classification_destination
from .proposal_identity import (
    ProposalIdentityError,
    proposal_id_candidates,
    require_proposal_identity,
)
from .scope import CrossScopeError, Scope
from .vault import DestinationRegistryError


class OutboxError(Exception):
    pass


class OutboxScopeError(OutboxError):
    pass


class OutboxDestinationError(OutboxError):
    pass


@dataclass
class Proposal:
    id: str
    path: Path
    action: str
    entity: str
    src: str          # vault-relative source path
    source_sha256: str
    dst: str          # vault-relative destination path
    module: str
    sub: str | None
    block: str
    rule_id: str | None
    created: str
    status: str = "pending"


def _require_outbox_path(
    scope: Scope,
    proposal_path: Path | None = None,
    *,
    create_directory: bool = False,
    require_leaf: bool = False,
) -> Path:
    """Retain the lexical outbox path and reject every redirected component."""
    lexical_outbox = scope.root / scope.current_entity() / "outbox"
    resolved_outbox = scope.resolve("outbox")
    if lexical_outbox.is_symlink() or resolved_outbox != lexical_outbox:
        raise CrossScopeError("outbox directory is redirected")
    if lexical_outbox.exists():
        if not lexical_outbox.is_dir():
            raise CrossScopeError("outbox path is not a real directory")
    elif create_directory:
        try:
            lexical_outbox.mkdir()
        except FileExistsError as exc:
            raise CrossScopeError("outbox directory changed during creation") from exc
        if lexical_outbox.is_symlink() or scope.resolve("outbox") != lexical_outbox:
            raise CrossScopeError("outbox directory is redirected")

    if proposal_path is None:
        return lexical_outbox

    candidate = Path(proposal_path)
    if (
        candidate.parent != lexical_outbox
        or candidate != lexical_outbox / candidate.name
        or candidate.suffix != ".yaml"
    ):
        raise CrossScopeError("proposal is outside the lexical outbox")
    if candidate.is_symlink():
        raise CrossScopeError("proposal leaf is redirected")
    if candidate.exists():
        if not candidate.is_file() or candidate.resolve() != candidate:
            raise CrossScopeError("proposal leaf is not a real file")
    elif require_leaf:
        raise OutboxError("proposal leaf no longer exists")
    return candidate


def _read_no_follow_bytes(path: Path) -> bytes:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise CrossScopeError("source receipt is redirected or unsafe") from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise CrossScopeError("source receipt is not a regular file")
        with os.fdopen(descriptor, "rb", closefd=True) as stream:
            descriptor = -1
            return stream.read()
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def propose_classification(
    scope: Scope,
    item_path: Path,
    *,
    module: str,
    sub: str,
    claimed_block: str | None = None,
    rule_id: str | None = None,
) -> Proposal:
    """Write a classify proposal. Moves nothing."""
    destination = resolve_classification_destination(
        scope,
        item_path,
        module=module,
        sub=sub,
        claimed_block=claimed_block,
    )
    created_at = datetime.now()
    try:
        source_bytes = _read_no_follow_bytes(scope.resolve_stored(destination.src))
    except FileNotFoundError as exc:
        raise OutboxDestinationError("source receipt is missing") from exc
    source_sha256 = hashlib.sha256(source_bytes).hexdigest()

    record = {
        "action": "classify",
        "entity": destination.entity,
        "created": created_at.isoformat(timespec="seconds"),
        "status": "pending",
        "src": destination.src,
        "source_sha256": source_sha256,
        "dst": destination.dst,
        "module": destination.module,
        "sub": destination.sub,
        "block": destination.block,
        "rule_id": rule_id,
    }
    outbox = _require_outbox_path(scope, create_directory=True)
    for pid in proposal_id_candidates(created_at):
        path = _require_outbox_path(scope, outbox / f"{pid}.yaml")
        record["id"] = pid
        try:
            with path.open("x", encoding="utf-8") as stream:
                stream.write(yaml.safe_dump(record, sort_keys=False))
        except FileExistsError:
            continue
        return _to_proposal(path, record)
    raise OutboxError("unable to allocate a unique classification proposal id")


def _required_string(record: dict, key: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value:
        raise OutboxDestinationError("proposal destination record is malformed")
    return value


_SOURCE_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _required_source_hash(record: dict) -> str:
    value = record.get("source_sha256")
    if not isinstance(value, str) or _SOURCE_SHA256.fullmatch(value) is None:
        raise OutboxDestinationError("proposal source hash is malformed")
    return value


def _to_proposal(path: Path, record: dict) -> Proposal:
    if not isinstance(record, dict):
        raise OutboxDestinationError("proposal record must be a mapping")
    try:
        require_proposal_identity(path, record.get("id"))
    except ProposalIdentityError as exc:
        raise OutboxDestinationError("proposal identity is invalid") from exc
    if "sub" not in record:
        raise OutboxDestinationError("proposal destination record is malformed")
    sub = record.get("sub")
    if sub is not None and (not isinstance(sub, str) or not sub):
        raise OutboxDestinationError("proposal sub must be a string or null")
    if record.get("action") != "classify":
        raise OutboxDestinationError("proposal is not a classification")
    return Proposal(
        id=_required_string(record, "id"),
        path=path,
        action=_required_string(record, "action"),
        entity=_required_string(record, "entity"),
        src=_required_string(record, "src"),
        source_sha256=_required_source_hash(record),
        dst=_required_string(record, "dst"),
        module=_required_string(record, "module"),
        sub=sub,
        block=_required_string(record, "block"),
        rule_id=(
            record.get("rule_id")
            if isinstance(record.get("rule_id"), str)
            else None
        ),
        created=(
            record.get("created") if isinstance(record.get("created"), str) else ""
        ),
        status=(
            record.get("status")
            if isinstance(record.get("status"), str)
            else "pending"
        ),
    )


def load_proposals(scope: Scope) -> list[Proposal]:
    outbox = _require_outbox_path(scope)
    if not outbox.exists():
        return []
    props = []
    for discovered in sorted(outbox.glob("*.yaml")):
        p = _require_outbox_path(scope, discovered, require_leaf=True)
        try:
            record = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise OutboxDestinationError("proposal record is invalid YAML") from exc
        if not isinstance(record, dict):
            raise OutboxDestinationError("proposal record must be a mapping")
        try:
            require_proposal_identity(p, record.get("id"))
        except ProposalIdentityError as exc:
            raise OutboxDestinationError("proposal identity is invalid") from exc
        action = record.get("action")
        if not isinstance(action, str) or not action:
            raise OutboxDestinationError("proposal action is malformed")
        if action == "delete":
            continue
        if action != "classify":
            raise OutboxDestinationError("proposal action is unknown")
        proposal = _to_proposal(p, record)
        props.append(_require_destination(scope, proposal))
    return props


def _apply_sub(text: str, sub: str | None) -> str:
    """The move's only content change: the `sub:` front-matter value. `block`
    is derived from the module, never written per file (conventions v2 §1)."""
    if sub is None:
        return re.sub(r"(?m)^sub:\s*.*\n?", "", text, count=1)
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
    proposal = _require_destination(scope, proposal)
    src_path = scope.resolve_stored(proposal.src)
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


def _require_scope(scope: Scope, proposal: Proposal) -> Proposal:
    if proposal.entity != scope.current_entity():
        raise OutboxScopeError("proposal belongs to another entity")
    return proposal


def _require_destination(scope: Scope, proposal: Proposal) -> Proposal:
    proposal = _require_scope(scope, proposal)
    _require_outbox_path(scope, proposal.path, require_leaf=True)
    try:
        source = scope.resolve_stored(proposal.src)
        canonical = resolve_classification_destination(
            scope,
            source,
            module=proposal.module,
            sub=proposal.sub,
            claimed_block=proposal.block,
            require_source=False,
        )
    except (DestinationError, CrossScopeError, DestinationRegistryError) as exc:
        raise OutboxDestinationError("proposal destination is invalid") from exc
    if proposal.src != canonical.src or proposal.dst != canonical.dst:
        raise OutboxDestinationError("proposal destination is non-canonical")
    return proposal


def get_proposal(scope: Scope, proposal_id: str) -> Proposal:
    entity = scope.current_entity()
    for p in load_proposals(scope):
        if p.id == proposal_id:
            return p
    raise OutboxError(f"no pending proposal {proposal_id!r} for {entity}")


def approve(scope: Scope, proposal_id: str) -> Proposal:
    """Perform the proposed move and commit it — exactly one revertible commit.
    The proposal file is untracked, so it never enters git; deleting it leaves a
    clean tree after the commit."""
    prop = _require_destination(scope, get_proposal(scope, proposal_id))
    vault = scope.root
    src = scope.resolve_stored(prop.src)
    dst = scope.resolve_stored(prop.dst)
    if not src.exists():
        raise OutboxError(f"source no longer exists: {prop.src}")

    _git(vault, "mv", prop.src, prop.dst)          # rename (original content)
    dst.write_text(_apply_sub(dst.read_text(encoding="utf-8"), prop.sub),
                   encoding="utf-8")               # the sub: change
    _git(vault, "add", prop.dst)
    _require_outbox_path(scope, prop.path, require_leaf=True).unlink()
    _git(vault, "commit", "-q", "-m",
         f"outbox: approve {prop.id} ({prop.src} → {prop.dst})")
    return prop


def reject(scope: Scope, proposal_id: str) -> Proposal:
    """Discard the proposal. No move, no commit — the proposal was never
    tracked."""
    prop = get_proposal(scope, proposal_id)
    _require_outbox_path(scope, prop.path, require_leaf=True).unlink()
    return prop
