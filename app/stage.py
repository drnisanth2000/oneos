"""stage.py — the `stage` proposal kind (master view 3, Task 2).

A stage move (Draft / Waiting / Done, an edited draft, a dated comment) is an
ordinary Outbox proposal. `propose_stage` writes one record bound to the note
bytes the owner was shown; `outbox.approve()` approves it through the same
S7 sequence as every other record (`_own_reviewed_proposal`: capture once,
compare the digest, parse those bytes) and `approve_stage` builds the one
transaction: the note change(s), an `"approval"` action receipt, and the
record consumed into `.consumed/` after the commit. Nothing here changes
review tokens, receipts or the transaction engine.

Two kinds of move:

* a filed note `<entity>/<lifecycle module>/active/<leaf>.md` is edited in
  place (`src == dst`): only its `status:`/`updated:` header lines, and the
  body when an edited body is submitted (`app.note_text.edit_note`);
* an inbox item `<entity>/00-inbox/active/<leaf>.md` moves to the
  destination a classification would use, with its `sub:` set exactly as
  classification approval sets it, then the same header edit. A pending
  classification of that item is named in `supersedes` (with its review
  digest) and consumed in the same transaction.

A record never blocks the entity's Outbox. It is written to a private
staging directory the loader never reads, then published to
`<entity>/outbox/<id>.yaml` by one no-replace move only once complete. The
loader recognises the kind, validates it from the record bytes alone (no
registry or disk reads, so a valid record stays valid), and checks paths
row-locally. `propose_and_approve_stage` rejects (quarantines) its own
record after any refusal.
"""
from __future__ import annotations

import contextlib
import dataclasses
import difflib
import hashlib
import os
import re
import secrets
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path

import yaml

from . import outbox as _outbox
from .action_receipts import (
    ActionReceipt,
    SpentAction,
    make_action_receipt,
    receipt_relative_path,
    render_action_receipt,
)
from .console_routing import structured_reader
from .destinations import (
    DestinationError,
    InvalidModule,
    RedirectedSourceLeaf,
    _is_registry_id,
    _require_markdown_leaf,
    _require_real_directory,
    resolve_classification_destination,
)
from .entities import EntityCatalog
from .git_transaction import (
    GitTransactionError,
    PathChange,
    PathState,
    ReviewedPathIntegrityError,
    TransactionPlan,
    TransactionPreconditionRefused,
    _move_no_replace,
    _open_checked_directory,
    execute_transaction,
)
from .inbox import split_front_matter
from .note_text import (
    NoteTextError,
    _header_mapping,
    _key_lines,
    _lines,
    edit_note,
    split_note,
)
from .outbox import (
    MissingProposalSource,
    OutboxDestinationError,
    OutboxError,
    OutboxTransactionError,
    Proposal,
    StageProposal,
    StaleProposalSource,
    UnreadableProposalRecord,
    _apply_sub,
    _capture_source_state,
    _head_action_receipt,
    _own_reviewed_proposal,
    _parse_record_bytes,
    _read_no_follow_bytes,
    _require_outbox_path,
    _spent_action_before_review,
    _spent_action_from_refusal,
    load_proposals,
)
from .proposal_identity import (
    ProposalIdentityError,
    proposal_id_candidates,
    require_proposal_id,
    require_proposal_identity,
)
from .scope import CrossScopeError, RedirectedPathError, Scope
from .vault import DestinationRegistryError, Vault

__all__ = [
    "STAGE_STATUSES",
    "StageProposal",
    "StageUnchanged",
    "approve_stage",
    "propose_and_approve_stage",
    "propose_stage",
    "render_stage_review",
    "stage_from_record",
    "stage_new_bytes",
]

#: The status values a stage move writes (spec decision 1). `None` on a
#: record means "keep the current status line", used only for a filed note.
STAGE_STATUSES = frozenset({"draft", "waiting", "done"})
INBOX_MODULE = "00-inbox"

#: The record's closed schema, in the order it is written.
_RECORD_KEYS = (
    "action", "id", "entity", "created", "status", "src", "dst",
    "source_sha256", "module", "sub", "block", "set_status", "updated",
    "body", "comment", "supersedes", "supersedes_review_sha256",
)
_SHA256 = re.compile(r"[0-9a-f]{64}")
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_STAGING_PREFIX = ".staging-"
#: Not `*.yaml`, inside a dot-directory, so no Outbox or vault scanner ever
#: mistakes an unpublished record for a proposal.
_STAGED_LEAF = "record.part"


class StageUnchanged(OutboxError):
    """The requested stage move would leave the note byte-for-byte as it is."""


# --- record validation (bytes only; never the registries or the disk) -----


def _split_stage_path(value: object, entity: str) -> tuple[str, str]:
    """`(module, leaf)` of `<entity>/<module>/active/<leaf>.md`, lexically."""
    if (
        not isinstance(value, str)
        or not value
        or any(char in value for char in ("\\", "\0", "\r", "\n"))
    ):
        raise OutboxDestinationError("stage path is not a vault-relative path")
    parts = value.split("/")
    if len(parts) != 4 or any(
        part in {"", ".", ".."} or part.startswith(".") for part in parts
    ):
        # Every component is a plain name: no `.sensitive`, `.git`, `..`.
        raise OutboxDestinationError("stage path is non-canonical")
    owner, module, lifecycle, leaf = parts
    if owner != entity or lifecycle != "active" or not _is_registry_id(module):
        raise OutboxDestinationError("stage path is not an active lifecycle note")
    try:
        _require_markdown_leaf(leaf)
    except DestinationError as exc:
        raise OutboxDestinationError("stage path leaf is non-canonical") from exc
    return module, leaf


def _require_date(value: object) -> str:
    if not isinstance(value, str) or _DATE.fullmatch(value) is None:
        raise OutboxDestinationError("stage date is malformed")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise OutboxDestinationError("stage date is not a real date") from exc
    return value


def _optional_text(record: dict, key: str) -> str | None:
    value = record[key]
    if value is not None and not isinstance(value, str):
        raise OutboxDestinationError(f"stage {key} must be text or null")
    return value


def _validated(path: Path, record: dict) -> StageProposal:
    """The record's own consistency; raises `OutboxDestinationError`."""
    # A set comparison, not `sorted`: a non-string key is simply unequal.
    if set(record) != set(_RECORD_KEYS):
        raise OutboxDestinationError("stage record fields are not the stage schema")
    try:
        proposal_id = require_proposal_identity(path, record["id"])
    except ProposalIdentityError as exc:
        raise OutboxDestinationError("stage identity is invalid") from exc
    if record["action"] != "stage" or record["status"] != "pending":
        raise OutboxDestinationError("stage record is not a pending stage")
    entity = record["entity"]
    if not _is_registry_id(entity):
        raise OutboxDestinationError("stage entity is non-canonical")
    if not isinstance(record["created"], str):
        raise OutboxDestinationError("stage created is malformed")
    src, dst = record["src"], record["dst"]
    src_module, src_leaf = _split_stage_path(src, entity)
    dst_module, dst_leaf = _split_stage_path(dst, entity)
    module, sub, block = record["module"], record["sub"], record["block"]
    if not _is_registry_id(module) or not _is_registry_id(block):
        raise OutboxDestinationError("stage module or block is non-canonical")
    if sub is not None and not _is_registry_id(sub):
        raise OutboxDestinationError("stage sub is non-canonical")
    source_sha256 = record["source_sha256"]
    if not isinstance(source_sha256, str) or _SHA256.fullmatch(source_sha256) is None:
        raise OutboxDestinationError("stage source hash is malformed")
    set_status = record["set_status"]
    if set_status is not None and (
        not isinstance(set_status, str) or set_status not in STAGE_STATUSES
    ):
        raise OutboxDestinationError("stage status is not a stage value")
    supersedes = record["supersedes"]
    supersedes_sha = record["supersedes_review_sha256"]
    if (supersedes is None) != (supersedes_sha is None):
        raise OutboxDestinationError("supersedes needs exactly its review digest")
    if supersedes is not None:
        try:
            require_proposal_id(supersedes)
        except ProposalIdentityError as exc:
            raise OutboxDestinationError("superseded id is non-canonical") from exc
        if (
            supersedes == proposal_id
            or not isinstance(supersedes_sha, str)
            or _SHA256.fullmatch(supersedes_sha) is None
        ):
            raise OutboxDestinationError("superseded review is malformed")
    if src_module == INBOX_MODULE:
        if (
            dst_module == INBOX_MODULE
            or dst_module != module
            or dst_leaf != src_leaf
            or set_status is None
        ):
            raise OutboxDestinationError("inbox stage destination is inconsistent")
    elif (
        dst != src
        or module != src_module
        or sub is not None
        or supersedes is not None
    ):
        raise OutboxDestinationError("filed stage must edit the note in place")
    return StageProposal(
        id=proposal_id,
        path=path,
        entity=entity,
        created=record["created"],
        src=src,
        dst=dst,
        source_sha256=source_sha256,
        set_status=set_status,
        updated=_require_date(record["updated"]),
        body=_optional_text(record, "body"),
        comment=_optional_text(record, "comment"),
        module=module,
        sub=sub,
        block=block,
        supersedes=supersedes,
        supersedes_review_sha256=supersedes_sha,
    )


def stage_from_record(path: Path, record: object) -> StageProposal:
    """Validate a parsed `action: stage` record (loader phase 1)."""
    if not isinstance(record, dict):
        raise UnreadableProposalRecord("proposal record must be a mapping")
    try:
        return _validated(path, record)
    except (OutboxDestinationError, TypeError, ValueError) as exc:
        # TypeError/ValueError: a value of a type no check above anticipated
        # (an unhashable list, say). Any tampered shape is malformed, never a
        # crash of the loader.
        raise UnreadableProposalRecord("stage proposal record is malformed") from exc


# --- the new bytes ------------------------------------------------------


def _set_sub(text: str, sub: str | None) -> str:
    """Classification's `sub:` change, scoped to the header.

    `outbox._apply_sub` matches the first `^sub:` line anywhere in the file,
    so on a note whose header has no `sub:` it would rewrite or delete a body
    line. Here it only ever sees the header, the body bytes are appended
    unchanged, and the header must mean what it meant except for `sub`.
    """
    header_raw, body_raw = split_note(text)
    inner = _lines(header_raw)[1:-1]
    placed = _key_lines("".join(inner))
    # `_apply_sub` only sees a key written exactly `sub:` at the start of a
    # line. A `"sub":` or `sub :` spelling would get a second `sub:` line
    # (PyYAML keeps the last), so such a header is refused, never rewritten.
    if "sub" in placed and not inner[placed["sub"][0]].startswith("sub:"):
        raise NoteTextError("header 'sub' key is not written as 'sub:'; refusing to edit it")
    new_header = _apply_sub(header_raw, sub)
    expected = {key: value for key, value in _header_mapping(header_raw).items() if key != "sub"}
    if sub is not None:
        expected["sub"] = sub
    if _header_mapping(new_header) != expected:
        raise NoteTextError("could not set 'sub:' safely")
    result = new_header + body_raw
    # The only guard against `_apply_sub`'s `\s*` eating the closing fence
    # (an empty `sub:` value): the mapping check can pass when a comment
    # line is lost instead, and the body would then leak into the header.
    if split_note(result) != (new_header, body_raw):
        raise NoteTextError("setting 'sub:' would move the front-matter fence")
    return result


def stage_new_bytes(prop: StageProposal, source_bytes: bytes) -> bytes:
    """The exact bytes approval writes. The review diff uses this too."""
    try:
        text = source_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise NoteTextError("note is not UTF-8 text") from exc
    if prop.src != prop.dst:
        # Classification approval's one content change, applied to the
        # header only, so a stage move from the inbox files the item as
        # approving its proposal would without ever touching the body.
        text = _set_sub(text, prop.sub)
    new = edit_note(
        text,
        status=prop.set_status,
        updated=prop.updated,
        body=prop.body,
        comment=prop.comment,
    )
    return new.encode("utf-8")


# --- paths against the vault (render and approval) -------------------------


def _require_filed_note(scope: Scope, src: str) -> tuple[str, str]:
    """`(module, block)` for a filed note in an active lifecycle module."""
    module, leaf = _split_stage_path(src, scope.current_entity())
    if module == INBOX_MODULE:
        raise OutboxDestinationError("an inbox item is not a filed note")
    catalog = EntityCatalog.load(scope.root)
    vault = Vault(catalog)
    catalog.require(scope.current_entity())
    if module not in vault.active_modules_for(scope):
        raise InvalidModule("note module is not active")
    if vault.module_spec(module).get("lifecycle_pattern", True) is False:
        raise InvalidModule("note module has no active lifecycle")
    block = vault.require_block(module)
    module_dir = _require_real_directory(scope, module)
    active_dir = _require_real_directory(scope, module, "active")
    if active_dir.parent != module_dir:
        raise RedirectedSourceLeaf("active lifecycle directory is redirected")
    lexical = scope.root / scope.current_entity() / module / "active" / leaf
    # Lexical symlink first, then resolve (the C2 order).
    if lexical.is_symlink():
        raise RedirectedSourceLeaf("note is redirected")
    if scope.resolve(module, "active", leaf) != lexical:
        raise RedirectedSourceLeaf("note is redirected")
    return module, block


def _require_stage_paths(scope: Scope, prop: StageProposal) -> None:
    """Re-derive both paths from the registries and the disk."""
    try:
        if prop.src == prop.dst:
            module, block = _require_filed_note(scope, prop.src)
            if (module, block) != (prop.module, prop.block):
                raise OutboxDestinationError("stage module is non-canonical")
            return
        canonical = resolve_classification_destination(
            scope,
            scope.root / prop.src,
            module=prop.module,
            sub=prop.sub,
            claimed_block=prop.block,
            require_source=False,
        )
    except (DestinationError, CrossScopeError, DestinationRegistryError) as exc:
        raise OutboxDestinationError("stage destination is invalid") from exc
    if (canonical.src, canonical.dst, canonical.sub) != (prop.src, prop.dst, prop.sub):
        raise OutboxDestinationError("stage destination is non-canonical")


def _require_source(prop: StageProposal, source_bytes: bytes) -> None:
    if hashlib.sha256(source_bytes).hexdigest() != prop.source_sha256:
        raise StaleProposalSource("note changed since it was shown")


@structured_reader(category="front-matter")
def render_stage_review(scope: Scope, prop: StageProposal) -> tuple[str, str]:
    """Diff and title for one stage row; every failure is row-local."""
    _require_stage_paths(scope, prop)
    try:
        source_bytes = _read_no_follow_bytes(scope.root, prop.src)
    except FileNotFoundError as exc:
        raise MissingProposalSource("note is missing") from exc
    _require_source(prop, source_bytes)
    try:
        new = stage_new_bytes(prop, source_bytes).decode("utf-8")
    except NoteTextError as exc:
        raise OutboxDestinationError("note cannot be edited safely") from exc
    old = source_bytes.decode("utf-8")
    diff = difflib.unified_diff(
        old.splitlines(True), new.splitlines(True),
        fromfile=f"a/{prop.src}", tofile=f"b/{prop.dst}",
    )
    route = prop.src if prop.src == prop.dst else f"{prop.src} → {prop.dst}"
    try:
        front_matter, _ = split_front_matter(old)
    except (ValueError, AttributeError, KeyError):
        front_matter = {}
    title = front_matter.get("title") if isinstance(front_matter, dict) else None
    if not isinstance(title, str) or not title.strip():
        title = Path(prop.src).name
    return f"stage: {route}\n" + "".join(diff), title.strip()


# --- approval -------------------------------------------------------------


def _require_supersedable(prop: StageProposal, superseded: object) -> None:
    if (
        not isinstance(superseded, Proposal)
        or superseded.action != "classify"
        or superseded.entity != prop.entity
        or superseded.src != prop.src
        or superseded.dst != prop.dst
        or superseded.module != prop.module
        or superseded.sub != prop.sub
    ):
        raise OutboxDestinationError(
            "superseded proposal is not a classification of this item"
        )


def approve_stage(
    scope: Scope,
    proposal_rel: str,
    proposal_state: PathState,
    prop: StageProposal,
    review_digest: str,
) -> StageProposal | SpentAction:
    """The transaction for one owned, reviewed stage record.

    Called only by `outbox.approve()` after `_own_reviewed_proposal`, with
    the captured record state whose bytes matched the review digest.
    """
    vault = scope.root
    _require_stage_paths(scope, prop)
    owned = [PathChange(proposal_rel, proposal_state, PathState.absent())]
    if prop.supersedes is not None:
        if _spent_action_before_review(scope, prop.supersedes) is not None:
            raise OutboxDestinationError("the superseded proposal was already acted on")
        # The superseded record is owned on the same S7 terms as this one:
        # one capture, its digest compared, those bytes parsed.
        superseded_rel, superseded_state, superseded, _ = _own_reviewed_proposal(
            scope, prop.supersedes, prop.supersedes_review_sha256
        )
        _require_supersedable(prop, superseded)
        owned.append(PathChange(superseded_rel, superseded_state, PathState.absent()))

    source_state = _capture_source_state(vault, prop.src)
    if source_state.contents is None:
        raise MissingProposalSource("note is missing")
    _require_source(prop, source_state.contents)
    try:
        new_bytes = stage_new_bytes(prop, source_state.contents)
    except NoteTextError as exc:
        raise OutboxDestinationError("note cannot be edited safely") from exc
    if prop.src == prop.dst and new_bytes == source_state.contents:
        raise StageUnchanged("this stage move changes nothing")

    receipt = make_action_receipt(prop.id, review_digest, "approval")
    receipt_rel = receipt_relative_path(prop.entity, prop.id)
    receipt_change = PathChange(
        receipt_rel,
        PathState.absent(),
        PathState.regular(render_action_receipt(receipt), 0o644),
        create_parent=True,
    )
    after = PathState.regular(new_bytes, source_state.mode)
    if prop.src == prop.dst:
        message = f"outbox: stage {prop.id} ({prop.src})"
        changes = (PathChange(prop.src, source_state, after), receipt_change)
    else:
        message = f"outbox: stage {prop.id} ({prop.src} → {prop.dst})"
        changes = (
            PathChange(prop.src, source_state, PathState.absent()),
            PathChange(prop.dst, PathState.absent(), after),
            receipt_change,
        )

    def _require_unspent_id() -> ActionReceipt | None:
        return _head_action_receipt(scope, prop.id)

    def _require_superseded_unspent() -> None:
        if prop.supersedes is not None and _head_action_receipt(scope, prop.supersedes):
            raise OutboxDestinationError("the superseded proposal was already acted on")

    plan = TransactionPlan(
        message=message,
        changes=changes,
        commit_paths=tuple(change.path for change in changes),
        owned_changes=tuple(owned),
        preconditions=(_require_unspent_id, _require_superseded_unspent),
    )
    try:
        result = execute_transaction(vault, plan)
    except GitTransactionError as exc:
        raise OutboxTransactionError("stage approval transaction failed") from exc
    if isinstance(result, TransactionPreconditionRefused):
        return _spent_action_from_refusal(result, prop.id)
    return prop


# --- proposing ------------------------------------------------------------


def _render_record(record: dict) -> bytes:
    return yaml.safe_dump(record, sort_keys=False, allow_unicode=False).encode("utf-8")


def _write_all(descriptor: int, contents: bytes) -> None:
    view = memoryview(contents)
    while view:
        written = os.write(descriptor, view)
        view = view[written:]


def _publish_record(
    scope: Scope, created_at: datetime, record_for: Callable[[str], bytes]
) -> tuple[str, bytes]:
    """Stage the record privately, then publish it with a no-replace move.

    The staged file lives in `outbox/.staging-<random>/record.part`, which no
    loader reads. Only a complete, fsynced record is ever moved to
    `outbox/<id>.yaml`; a crash before that leaves only staging. Any error
    removes the staging directory.
    """
    _require_outbox_path(scope, create_directory=True)
    descriptors: list[int] = []
    staging_name = f"{_STAGING_PREFIX}{secrets.token_hex(8)}"
    try:
        descriptor = _open_checked_directory(scope.root, "vault root")
        descriptors.append(descriptor)
        for part, description in (
            (scope.current_entity(), "entity root"), ("outbox", "outbox directory")
        ):
            descriptor = _open_checked_directory(part, description, dir_fd=descriptor)
            descriptors.append(descriptor)
        outbox_fd = descriptors[-1]
        os.mkdir(staging_name, 0o700, dir_fd=outbox_fd)
        try:
            staging_fd = _open_checked_directory(
                staging_name, "staging directory", dir_fd=outbox_fd
            )
            try:
                for proposal_id in proposal_id_candidates(created_at):
                    contents = record_for(proposal_id)
                    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
                    file_fd = os.open(_STAGED_LEAF, flags, 0o644, dir_fd=staging_fd)
                    try:
                        _write_all(file_fd, contents)
                        os.fsync(file_fd)
                    finally:
                        os.close(file_fd)
                    try:
                        _move_no_replace(
                            staging_fd, _STAGED_LEAF, outbox_fd, f"{proposal_id}.yaml"
                        )
                    except FileExistsError:
                        os.unlink(_STAGED_LEAF, dir_fd=staging_fd)
                        continue
                    with contextlib.suppress(OSError):
                        os.fsync(outbox_fd)
                    return proposal_id, contents
                raise OutboxError("unable to allocate a unique stage proposal id")
            finally:
                with contextlib.suppress(FileNotFoundError):
                    os.unlink(_STAGED_LEAF, dir_fd=staging_fd)
                os.close(staging_fd)
        finally:
            # Debris here would be invisible to every loader, so a failed
            # cleanup must not turn a published record into a refusal.
            with contextlib.suppress(OSError):
                os.rmdir(staging_name, dir_fd=outbox_fd)
    except ReviewedPathIntegrityError as exc:
        raise RedirectedPathError("outbox directory is redirected") from exc
    except (OSError, GitTransactionError) as exc:
        raise OutboxError("stage proposal could not be written") from exc
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _require_no_pending_classification(scope: Scope, src: str) -> None:
    for pending in load_proposals(scope):
        if isinstance(pending, Proposal) and pending.src == src:
            raise OutboxDestinationError(
                "this item has a pending proposal; the move must supersede it"
            )


def propose_stage(
    scope: Scope,
    *,
    src: str,
    source_sha256: str,
    set_status: str | None,
    updated: str,
    body: str | None = None,
    comment: str | None = None,
    module: str | None = None,
    sub: str | None = None,
    supersedes: str | None = None,
    supersedes_review_sha256: str | None = None,
) -> tuple[str, str]:
    """Write one `stage` record; move and commit nothing.

    Returns `(proposal id, review digest)`, the digest being the SHA-256 of
    the exact bytes published. Refuses before writing anything when the
    note changed since `source_sha256` was taken, when the editor refuses
    the note, or when the move would change nothing.
    """
    entity = scope.current_entity()
    if set_status is not None and set_status not in STAGE_STATUSES:
        raise OutboxDestinationError("stage status is not a stage value")
    if not isinstance(source_sha256, str) or _SHA256.fullmatch(source_sha256) is None:
        raise OutboxDestinationError("stage source hash is malformed")
    src_module, _ = _split_stage_path(src, entity)

    if src_module == INBOX_MODULE:
        if set_status is None:
            raise OutboxDestinationError("an inbox item needs a stage status")
        if supersedes is not None:
            if _spent_action_before_review(scope, supersedes) is not None:
                raise OutboxDestinationError("the superseded proposal was already acted on")
            _, _, superseded, _ = _own_reviewed_proposal(
                scope, supersedes, supersedes_review_sha256
            )
            if not isinstance(superseded, Proposal) or superseded.src != src:
                raise OutboxDestinationError(
                    "superseded proposal is not a classification of this item"
                )
            if (module is not None and module != superseded.module) or (
                sub is not None and sub != superseded.sub
            ):
                raise OutboxDestinationError("a superseding move keeps the proposal's destination")
            module, sub = superseded.module, superseded.sub
        else:
            if supersedes_review_sha256 is not None:
                raise OutboxDestinationError("a review digest needs its superseded id")
            _require_no_pending_classification(scope, src)
        destination = resolve_classification_destination(
            scope, scope.root / src, module=module, sub=sub
        )
        dst, module, sub, block = (
            destination.dst, destination.module, destination.sub, destination.block
        )
        if destination.src != src:
            raise OutboxDestinationError("stage source is non-canonical")
    else:
        if supersedes is not None or supersedes_review_sha256 is not None:
            raise OutboxDestinationError("only an inbox item can supersede a proposal")
        if sub is not None:
            raise OutboxDestinationError("a filed note keeps its sub")
        filed_module, block = _require_filed_note(scope, src)
        if module is not None and module != filed_module:
            raise OutboxDestinationError("a filed note stays in its module")
        dst, module = src, filed_module

    source_state = _capture_source_state(scope.root, src)
    if source_state.contents is None:
        raise MissingProposalSource("note is missing")
    created_at = datetime.now()
    fields = {
        "action": "stage",
        "id": None,
        "entity": entity,
        "created": created_at.isoformat(timespec="seconds"),
        "status": "pending",
        "src": src,
        "dst": dst,
        "source_sha256": source_sha256,
        "module": module,
        "sub": sub,
        "block": block,
        "set_status": set_status,
        "updated": updated,
        "body": body,
        "comment": comment,
        "supersedes": supersedes,
        "supersedes_review_sha256": supersedes_review_sha256 if supersedes else None,
    }
    request = StageProposal(
        id="", path=Path(), entity=entity, created=fields["created"], src=src,
        dst=dst, source_sha256=source_sha256, set_status=set_status,
        updated=updated, body=body, comment=comment, module=module, sub=sub,
        block=block, supersedes=supersedes,
        supersedes_review_sha256=fields["supersedes_review_sha256"],
    )
    # Pre-compute the new bytes against the current file, so every refusal
    # (stale bytes, a note or value the editor refuses, no change) happens
    # before anything is written.
    _require_source(request, source_state.contents)
    new_bytes = stage_new_bytes(request, source_state.contents)
    if src == dst and new_bytes == source_state.contents:
        raise StageUnchanged("this stage move changes nothing")

    def record_for(proposal_id: str) -> bytes:
        contents = _render_record(dict(fields, id=proposal_id))
        # The published bytes must parse back to exactly this request, or
        # the review would bind something other than what was asked.
        path = _require_outbox_path(scope) / f"{proposal_id}.yaml"
        parsed = stage_from_record(path, _parse_record_bytes(contents))
        if parsed != dataclasses.replace(request, id=proposal_id, path=path):
            raise OutboxError("stage record does not round-trip")
        return contents

    proposal_id, contents = _publish_record(scope, created_at, record_for)
    return proposal_id, hashlib.sha256(contents).hexdigest()


def propose_and_approve_stage(scope: Scope, **kwargs) -> StageProposal | SpentAction:
    """Propose, then approve with the digest of the exact bytes published.

    The owner's press is the review (spec decision 9). On any refusal after
    publishing, the record is rejected — quarantined, never deleted — with
    that same digest, and the original refusal is raised.
    """
    proposal_id, review_sha256 = propose_stage(scope, **kwargs)
    try:
        return _outbox.approve(scope, proposal_id, review_sha256)
    except Exception as refusal:
        try:
            _outbox.reject(scope, proposal_id, review_sha256)
        except Exception as cleanup:  # the refusal is what the caller needs
            refusal.add_note(f"the stage record was left pending: {cleanup!r}")
        raise
