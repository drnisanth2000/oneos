"""Closed committed receipts for proposal actions, read from Git ``HEAD``.

The working tree is deliberately never authority here.  Request-path readers
look up only the validated ids they were given; the offline validator is the
only operation that enumerates the accumulated store.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import os
from pathlib import Path
import re
import subprocess
from typing import Literal, cast

import yaml

from .console_routing import failure_contract, structured_reader
from .identifiers import meets_floor
from .proposal_identity import (
    ProposalIdentityError,
    require_proposal_id,
    require_proposal_identity,
)
from .review_tokens import (
    InvalidReviewToken,
    ReviewContractViolation,
    require_review_sha256,
)


# Console failure metadata belongs at this route-facing adapter rather than in
# review_tokens.py. That exact-byte domain primitive deliberately has no app
# imports; decorating the shared function object here preserves that boundary
# while exposing the same immutable contract to the Console composition graph.
require_review_sha256 = failure_contract(
    raises=(InvalidReviewToken, ReviewContractViolation)
)(require_review_sha256)


ActionKind = Literal["approval", "registry deletion"]

_ACTION_KINDS = frozenset({"approval", "registry deletion"})
_FIELDS = frozenset({"version", "proposal_id", "review_sha256", "action_kind"})
_ENTITY = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
_OBJECT_ID = re.compile(rb"[0-9a-f]{40}(?:[0-9a-f]{24})?\Z")


class ReceiptError(Exception):
    """Abstract base for receipt outcomes. Never raised directly."""


class InvalidActionReceipt(ReceiptError):
    """A matching receipt exists but does not satisfy the closed schema."""


class ReceiptStoreIntegrityError(ReceiptError):
    """The receipt-store root or accumulated layout is structurally unsafe."""


class ReceiptStoreUnavailable(ReceiptError):
    """Git could not reliably resolve the current receipt authority."""


@dataclass(frozen=True)
class ActionReceipt:
    version: int
    proposal_id: str
    review_sha256: str
    action_kind: ActionKind


@dataclass(frozen=True)
class ReceiptResolution:
    proposal_id: str
    receipt: ActionReceipt | None
    error: InvalidActionReceipt | None


@dataclass(frozen=True)
class SpentAction:
    receipt: ActionReceipt


@dataclass(frozen=True)
class _BatchObject:
    object_type: str
    contents: bytes


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(
    loader: _UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[object, object]:
    mapping: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as exc:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable key",
                key_node.start_mark,
            ) from exc
        if duplicate:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found a duplicate key",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _require_entity(entity: object) -> str:
    if (
        not isinstance(entity, str)
        or _ENTITY.fullmatch(entity) is None
        or not meets_floor(entity)
    ):
        raise ReceiptStoreIntegrityError("receipt entity is not canonical")
    return entity


def receipt_relative_path(entity: str, proposal_id: str) -> str:
    selected = _require_entity(entity)
    canonical_id = require_proposal_id(proposal_id)
    return f"{selected}/outbox/.receipts/{canonical_id}.yaml"


def _validate_fields(
    version: object,
    proposal_id: object,
    review_sha256: object,
    action_kind: object,
) -> ActionReceipt:
    if type(version) is not int or version != 1:
        raise InvalidActionReceipt("action receipt has an unsupported version")
    try:
        canonical_id = require_proposal_id(proposal_id)
        digest = require_review_sha256(review_sha256)
    except (ProposalIdentityError, InvalidReviewToken) as exc:
        raise InvalidActionReceipt("action receipt has an invalid field") from exc
    if not isinstance(action_kind, str) or action_kind not in _ACTION_KINDS:
        raise InvalidActionReceipt("action receipt has an invalid action kind")
    return ActionReceipt(1, canonical_id, digest, cast(ActionKind, action_kind))


def make_action_receipt(
    proposal_id: str, review_sha256: object, action_kind: ActionKind
) -> ActionReceipt:
    return _validate_fields(1, proposal_id, review_sha256, action_kind)


def render_action_receipt(receipt: ActionReceipt) -> bytes:
    if not isinstance(receipt, ActionReceipt):
        raise InvalidActionReceipt("action receipt value is invalid")
    validated = _validate_fields(
        receipt.version,
        receipt.proposal_id,
        receipt.review_sha256,
        receipt.action_kind,
    )
    record = {
        "version": validated.version,
        "proposal_id": validated.proposal_id,
        "review_sha256": validated.review_sha256,
        "action_kind": validated.action_kind,
    }
    return yaml.safe_dump(
        record, sort_keys=False, allow_unicode=False
    ).encode("utf-8")


@structured_reader(category="proposal")
def _load_receipt_mapping(contents: bytes) -> dict[object, object]:
    if type(contents) is not bytes:
        raise InvalidActionReceipt("action receipt must be bytes")
    try:
        text = contents.decode("utf-8", "strict")
        record = yaml.load(text, Loader=_UniqueKeyLoader)
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise InvalidActionReceipt("action receipt is not valid YAML") from exc
    if not isinstance(record, dict):
        raise InvalidActionReceipt("action receipt requires a mapping")
    if set(record) != _FIELDS:
        raise InvalidActionReceipt("action receipt schema is not closed")
    return record


def parse_action_receipt(path: Path, contents: bytes) -> ActionReceipt:
    record = _load_receipt_mapping(contents)
    receipt = _validate_fields(
        record["version"],
        record["proposal_id"],
        record["review_sha256"],
        record["action_kind"],
    )
    try:
        require_proposal_identity(Path(path), receipt.proposal_id)
    except ProposalIdentityError as exc:
        raise InvalidActionReceipt(
            "action receipt id does not match its filename"
        ) from exc
    return receipt


def _git(
    vault: Path,
    *args: str,
    input_bytes: bytes | None = None,
) -> bytes:
    environment = dict(os.environ)
    environment["LC_ALL"] = "C"
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=vault,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            input=input_bytes,
            env=environment,
        )
    except OSError as exc:
        raise ReceiptStoreUnavailable("could not run Git receipt reader") from exc
    if completed.returncode != 0:
        raise ReceiptStoreUnavailable("Git receipt authority is unavailable")
    return completed.stdout


def _receipt_root(entity: str) -> str:
    return f"{_require_entity(entity)}/outbox/.receipts"


def _require_object_id(value: bytes) -> None:
    if _OBJECT_ID.fullmatch(value) is None:
        raise ReceiptStoreUnavailable("Git returned a malformed object id")


def _head_root_exists(vault: Path, entity: str) -> bool:
    root = _receipt_root(entity)
    output = _git(vault, "ls-tree", "-z", "HEAD", "--", root)
    if output == b"":
        return False
    if not output.endswith(b"\0"):
        raise ReceiptStoreUnavailable("Git returned malformed receipt-root state")
    records = output[:-1].split(b"\0")
    if len(records) != 1:
        raise ReceiptStoreUnavailable("Git returned ambiguous receipt-root state")
    try:
        metadata, returned_path = records[0].split(b"\t", 1)
        mode, object_type, object_id = metadata.split(b" ")
    except ValueError as exc:
        raise ReceiptStoreUnavailable("Git returned malformed receipt-root state") from exc
    _require_object_id(object_id)
    if returned_path != os.fsencode(root):
        raise ReceiptStoreUnavailable("Git returned a different receipt-root path")
    if object_type != b"tree":
        raise ReceiptStoreIntegrityError("receipt store root is not a Git tree")
    if mode != b"040000":
        raise ReceiptStoreUnavailable("Git returned a malformed receipt-root mode")
    return True


def _batch_objects(
    vault: Path, expressions: tuple[str, ...]
) -> tuple[_BatchObject | None, ...]:
    if not expressions:
        return ()
    payload = b"".join(expression.encode("ascii") + b"\n" for expression in expressions)
    output = _git(vault, "cat-file", "--batch", input_bytes=payload)
    offset = 0
    resolved: list[_BatchObject | None] = []
    for expression in expressions:
        header_end = output.find(b"\n", offset)
        if header_end < 0:
            raise ReceiptStoreUnavailable("Git returned an incomplete batch header")
        header = output[offset:header_end]
        offset = header_end + 1
        missing_header = expression.encode("ascii") + b" missing"
        if header == missing_header:
            resolved.append(None)
            continue
        try:
            object_id, object_type_bytes, size_bytes = header.split(b" ")
            _require_object_id(object_id)
            object_type = object_type_bytes.decode("ascii", "strict")
            size_text = size_bytes.decode("ascii", "strict")
            if not size_text.isdigit():
                raise ValueError
            size = int(size_text)
        except (UnicodeDecodeError, ValueError) as exc:
            raise ReceiptStoreUnavailable("Git returned a malformed batch header") from exc
        body_end = offset + size
        if body_end >= len(output) or output[body_end:body_end + 1] != b"\n":
            raise ReceiptStoreUnavailable("Git returned a truncated batch object")
        resolved.append(_BatchObject(object_type, output[offset:body_end]))
        offset = body_end + 1
    if offset != len(output):
        raise ReceiptStoreUnavailable("Git returned unexpected batch data")
    return tuple(resolved)


def _canonical_ids(proposal_ids: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(require_proposal_id(value) for value in proposal_ids))


def resolve_head_receipts(
    vault: Path, entity: str, proposal_ids: Iterable[str]
) -> dict[str, ReceiptResolution]:
    ids = _canonical_ids(proposal_ids)
    if not ids:
        return {}
    vault_path = Path(vault)
    if not _head_root_exists(vault_path, entity):
        return {
            proposal_id: ReceiptResolution(proposal_id, None, None)
            for proposal_id in ids
        }
    expressions = tuple(
        f"HEAD:{receipt_relative_path(entity, proposal_id)}" for proposal_id in ids
    )
    objects = _batch_objects(vault_path, expressions)
    resolutions: dict[str, ReceiptResolution] = {}
    for proposal_id, batch_object in zip(ids, objects, strict=True):
        if batch_object is None:
            resolutions[proposal_id] = ReceiptResolution(proposal_id, None, None)
            continue
        if batch_object.object_type != "blob":
            error = InvalidActionReceipt("matching action receipt is not a blob")
            resolutions[proposal_id] = ReceiptResolution(proposal_id, None, error)
            continue
        try:
            receipt = parse_action_receipt(
                Path(f"{proposal_id}.yaml"), batch_object.contents
            )
        except InvalidActionReceipt as exc:
            resolutions[proposal_id] = ReceiptResolution(proposal_id, None, exc)
        else:
            resolutions[proposal_id] = ReceiptResolution(proposal_id, receipt, None)
    return resolutions


@failure_contract(
    raises=(
        InvalidActionReceipt,
        ReceiptStoreIntegrityError,
        ReceiptStoreUnavailable,
    )
)
def resolve_head_receipt(
    vault: Path, entity: str, proposal_id: str
) -> ReceiptResolution:
    canonical_id = require_proposal_id(proposal_id)
    return resolve_head_receipts(vault, entity, (canonical_id,))[canonical_id]


def _head_receipt_entries(vault: Path, entity: str) -> tuple[str, ...]:
    root = _receipt_root(entity)
    # Immediate entries, deliberately not `-r`: recursive enumeration omits
    # an empty child tree and would misread that structurally invalid store as
    # empty. Any tree at this level is a nested receipt path and is refused.
    output = _git(vault, "ls-tree", "-z", "HEAD", "--", f"{root}/")
    if not output:
        return ()
    if not output.endswith(b"\0"):
        raise ReceiptStoreUnavailable("Git returned malformed receipt entries")
    paths: list[str] = []
    seen_paths: set[str] = set()
    prefix = os.fsencode(root + "/")
    for record in output[:-1].split(b"\0"):
        try:
            metadata, returned_path = record.split(b"\t", 1)
            _mode, object_type, object_id = metadata.split(b" ")
        except ValueError as exc:
            raise ReceiptStoreUnavailable("Git returned malformed receipt entries") from exc
        _require_object_id(object_id)
        if object_type != b"blob":
            raise ReceiptStoreIntegrityError("receipt store entry is not a blob")
        if not returned_path.startswith(prefix):
            raise ReceiptStoreUnavailable("Git returned a receipt outside its root")
        leaf = returned_path[len(prefix):]
        if b"/" in leaf or not leaf.endswith(b".yaml"):
            raise ReceiptStoreIntegrityError("receipt store contains a non-receipt path")
        try:
            decoded = returned_path.decode("utf-8", "strict")
        except UnicodeDecodeError as exc:
            raise ReceiptStoreIntegrityError("receipt path is not UTF-8") from exc
        if decoded in seen_paths:
            raise ReceiptStoreUnavailable("Git returned a duplicate receipt path")
        seen_paths.add(decoded)
        paths.append(decoded)
    return tuple(paths)


def validate_head_receipt_store(
    vault: Path, entity: str
) -> tuple[ActionReceipt, ...]:
    vault_path = Path(vault)
    if not _head_root_exists(vault_path, entity):
        return ()
    paths = _head_receipt_entries(vault_path, entity)
    expressions = tuple(f"HEAD:{path}" for path in paths)
    objects = _batch_objects(vault_path, expressions)
    receipts: list[ActionReceipt] = []
    for path, batch_object in zip(paths, objects, strict=True):
        if batch_object is None:
            raise ReceiptStoreUnavailable("listed receipt disappeared from HEAD")
        if batch_object.object_type != "blob":
            raise ReceiptStoreIntegrityError("receipt store entry is not a blob")
        receipts.append(parse_action_receipt(Path(path), batch_object.contents))
    return tuple(sorted(receipts, key=lambda receipt: receipt.proposal_id))


def _head_canonical_roots(vault: Path) -> tuple[str, ...]:
    """Enumerate canonical top-level Git trees without consulting worktree state."""
    output = _git(Path(vault), "ls-tree", "-z", "HEAD")
    if output and not output.endswith(b"\0"):
        raise ReceiptStoreUnavailable("Git returned malformed HEAD roots")
    roots: list[str] = []
    for record in output[:-1].split(b"\0") if output else ():
        try:
            metadata, raw_name = record.split(b"\t", 1)
            mode, object_type, object_id = metadata.split(b" ")
        except ValueError as exc:
            raise ReceiptStoreUnavailable("Git returned malformed HEAD roots") from exc
        _require_object_id(object_id)
        if mode != b"040000" or object_type != b"tree":
            continue
        try:
            name = raw_name.decode("utf-8", "strict")
        except UnicodeDecodeError:
            continue
        if _ENTITY.fullmatch(name) is not None and meets_floor(name):
            roots.append(name)
    return tuple(sorted(set(roots)))


def validate_all_head_receipt_stores(vault: Path) -> tuple[ActionReceipt, ...]:
    """Validate every accumulated canonical receipt store in Git ``HEAD``.

    Entity discovery is itself HEAD-backed.  A working-tree manifest edit
    therefore cannot hide a committed store from either offline audit.
    """
    receipts: list[ActionReceipt] = []
    seen_ids: set[str] = set()
    for entity in _head_canonical_roots(Path(vault)):
        for receipt in validate_head_receipt_store(vault, entity):
            if receipt.proposal_id in seen_ids:
                raise ReceiptStoreIntegrityError(
                    "receipt id appears in more than one entity store"
                )
            seen_ids.add(receipt.proposal_id)
            receipts.append(receipt)
    return tuple(sorted(receipts, key=lambda receipt: receipt.proposal_id))
