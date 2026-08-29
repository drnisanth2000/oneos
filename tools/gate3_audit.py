#!/usr/bin/env python3
"""Gate 3: audit sanctioned Git transactions and exact dirty session state.

    ONEOS_VAULT=/path/to/vault python -m tools.gate3_audit snapshot
    # ... exercise a complete session ...
    ONEOS_VAULT=/path/to/vault python -m tools.gate3_audit check

The snapshot is external to the vault.  A check accepts only the commit
envelopes produced by the existing ingest, outbox, registry, and rename flows,
genuinely new canonical pending proposal YAML, and the exact S7 quarantine
outcome for a reviewed proposal.  Every other initially dirty path must retain
its exact index/worktree fingerprint for the whole session.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import sys
import tempfile
from typing import Literal, TypeAlias

import yaml

from app.action_receipts import (
    ActionReceipt,
    ReceiptError,
    parse_action_receipt,
    validate_all_head_receipt_stores,
)
from app.destinations import DestinationError, resolve_classification_destination
from app.entities import EntityCatalog, EntityManifestError
from app.outbox import OutboxError, _require_destination, _to_proposal
from app.proposal_identity import (
    ProposalIdentityError,
    require_proposal_id,
    require_proposal_identity,
)
from app.registry import RegistryError, _validate_delete_record, get_delete_proposal
from app.rename import AXES, RenameError, build_rename_plan
from app.scope import CrossScopeError, Scope
from app.vault import DestinationRegistryError, Vault


SNAPSHOT_VERSION = 4
_OID = re.compile(r"^[0-9a-f]{40,64}$")
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
_FILESYSTEM_KINDS = frozenset(
    {
        "directory",
        "symlink",
        "fifo",
        "socket",
        "char-device",
        "block-device",
        "other",
    }
)

FilesystemKind: TypeAlias = Literal[
    "directory",
    "symlink",
    "fifo",
    "socket",
    "char-device",
    "block-device",
    "other",
]
ChangeKind: TypeAlias = Literal["added", "removed", "changed"]
Disposition: TypeAlias = Literal["sanctioned", "violating"]
_REGISTRY_MESSAGE = re.compile(
    r"^registry: (add|edit|delete) (workspace|product|member)(?: .*)?$"
)
_OUTBOX_APPROVAL_MESSAGE = re.compile(r"^outbox: approve ([^ ]+) \(.*\)$")
_RENAME_MESSAGE = re.compile(
    r"^rename: ([a-z0-9]+(?:-[a-z0-9]+)*) → "
    r"([a-z0-9]+(?:-[a-z0-9]+)*)$"
)
_REGISTRY_PATH = {
    "workspace": "_system/workspaces.yaml",
    "product": "_system/products.yaml",
    "member": "_system/members.yaml",
}
_DELETE_KINDS = frozenset({"product", "member"})
_CANONICAL_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_CLASSIFY_RECORD_FIELDS = frozenset(
    {
        "id",
        "action",
        "entity",
        "created",
        "status",
        "src",
        "source_sha256",
        "dst",
        "module",
        "sub",
        "block",
        "rule_id",
    }
)
_DELETE_RECORD_FIELDS = frozenset(
    {
        "id",
        "action",
        "entity",
        "kind",
        "slug",
        "created",
        "status",
        "total_references",
        "impact",
    }
)
_DELETE_IMPACT_FIELDS = frozenset({"front-matter", "workspaces", "books.db"})


@dataclass(frozen=True)
class PathChangeRecord:
    status: str
    path: str


@dataclass(frozen=True)
class CommitRecord:
    oid: str
    message: str
    parents: tuple[str, ...]
    changes: tuple[PathChangeRecord, ...]


@dataclass(frozen=True)
class DirtyFingerprint:
    status: str
    index_entries: tuple[str, ...]
    kind: str
    mode: int | None
    digest: str | None


@dataclass(frozen=True)
class GitDirtyInputs:
    """The two raw Git reads every dirty fingerprint derives from."""

    statuses: dict[str, str]
    index_entries: dict[str, tuple[str, ...]]

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, GitDirtyInputs):
            return NotImplemented
        return (
            self.statuses == other.statuses
            and self.index_entries == other.index_entries
        )


@dataclass(frozen=True)
class FilesystemFingerprint:
    """Metadata-only evidence for one non-regular entry or real directory.

    Content never appears here. `identity_digest` detects replacement by a
    different object of the same apparent kind, and `target_digest` records a
    symlink's raw text without ever resolving it.
    """

    kind: FilesystemKind
    mode: int
    identity_digest: str
    target_digest: str | None


@dataclass(frozen=True)
class Gate3Evidence:
    dirty: dict[str, DirtyFingerprint]
    filesystem: dict[str, FilesystemFingerprint]


@dataclass(frozen=True)
class Gate3Snapshot:
    head: str
    evidence: Gate3Evidence


@dataclass(frozen=True)
class FilesystemChange:
    path: str
    kind: ChangeKind
    before: FilesystemFingerprint | None
    after: FilesystemFingerprint | None


@dataclass(frozen=True)
class ClassifiedPathChange:
    path: str
    kind: ChangeKind
    disposition: Disposition


class FilesystemEvidenceError(ValueError):
    """Gate 3 could not obtain one coherent filesystem observation."""


@dataclass(frozen=True)
class _ConsumedRecord:
    relative: str
    pending_relative: str
    entity: str
    proposal_id: str
    action: str
    digest: str
    source: str | None = None
    destination: str | None = None
    registry_kind: str | None = None


@dataclass(frozen=True)
class _ReceiptAuthorization:
    key: tuple[str, str]
    entity: str
    proposal_id: str
    action_kind: str
    review_sha256: str
    source: str | None = None
    destination: str | None = None
    registry_kind: str | None = None


@dataclass
class Audit:
    sanctioned_commits: list[str] = field(default_factory=list)
    violating_commits: list[str] = field(default_factory=list)
    sanctioned_writes: list[str] = field(default_factory=list)
    violating_writes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violating_commits and not self.violating_writes


@dataclass(frozen=True)
class AuditRules:
    root: Path
    active_modules: dict[str, frozenset[str]]
    active_lifecycle_modules: dict[str, frozenset[str]]

    @classmethod
    def load(cls, vault: Path) -> "AuditRules":
        catalog = EntityCatalog.load(vault)
        runtime = Vault(catalog)
        active: dict[str, frozenset[str]] = {}
        lifecycle: dict[str, frozenset[str]] = {}
        for entity in catalog.entities:
            scope = Scope(catalog.root, entity.slug)
            entity_active = runtime.active_modules_for(scope)
            active[entity.slug] = entity_active
            lifecycle[entity.slug] = frozenset(
                module
                for module in entity_active
                if runtime.module_spec(module).get("lifecycle_pattern", True)
            )
        return cls(
            root=catalog.root,
            active_modules=active,
            active_lifecycle_modules=lifecycle,
        )

    @property
    def entities(self) -> frozenset[str]:
        return frozenset(self.active_modules)


def _git_bytes(
    vault: Path,
    *args: str,
    env: dict[str, str] | None = None,
) -> bytes:
    return subprocess.run(
        ["git", *args],
        cwd=vault,
        env=env,
        check=True,
        capture_output=True,
    ).stdout


def _git_text(vault: Path, *args: str) -> str:
    return _git_bytes(vault, *args).decode("utf-8", "surrogateescape")


def _decode_path(value: bytes) -> str:
    return os.fsdecode(value)


def _parse_name_status(output: bytes) -> tuple[PathChangeRecord, ...]:
    tokens = output.split(b"\0")
    if tokens and tokens[-1] == b"":
        tokens.pop()
    records: list[PathChangeRecord] = []
    cursor = 0
    while cursor < len(tokens):
        status_token = tokens[cursor]
        cursor += 1
        if b"\t" in status_token:
            status_bytes, path_bytes = status_token.split(b"\t", 1)
        else:
            if cursor >= len(tokens):
                raise ValueError("Git name-status output ended before its path")
            status_bytes = status_token
            path_bytes = tokens[cursor]
            cursor += 1
        try:
            status_value = status_bytes.decode("ascii")
        except UnicodeDecodeError as exc:
            raise ValueError("Git emitted a non-ASCII change status") from exc
        if not status_value or path_bytes == b"":
            raise ValueError("Git emitted an empty change record")
        records.append(PathChangeRecord(status_value, _decode_path(path_bytes)))
    return tuple(records)


def _head_oid(vault: Path) -> str:
    oid = _git_text(vault, "rev-parse", "HEAD").strip()
    if _OID.fullmatch(oid) is None:
        raise ValueError("Gate 3 current HEAD is malformed")
    return oid


def collect_commit_records(
    vault: Path, snapshot_head: str, audit_head: str | None = None
) -> tuple[CommitRecord, ...]:
    """Collect every post-snapshot commit in chronological order."""
    vault = Path(vault).resolve()
    if _OID.fullmatch(snapshot_head) is None:
        raise ValueError("Gate 3 snapshot HEAD is malformed")
    audit_head = _head_oid(vault) if audit_head is None else audit_head
    if _OID.fullmatch(audit_head) is None:
        raise ValueError("Gate 3 audit HEAD is malformed")
    try:
        _git_bytes(
            vault, "merge-base", "--is-ancestor", snapshot_head, audit_head
        )
    except subprocess.CalledProcessError as exc:
        raise ValueError(
            "Gate 3 snapshot HEAD is not an ancestor of audit HEAD"
        ) from exc
    oids = tuple(
        line
        for line in _git_text(
            vault, "rev-list", "--reverse", f"{snapshot_head}..{audit_head}"
        ).splitlines()
        if line
    )
    records: list[CommitRecord] = []
    for oid in oids:
        message = _git_text(vault, "show", "-s", "--format=%s", oid).rstrip("\n")
        parents_text = _git_text(vault, "show", "-s", "--format=%P", oid).strip()
        changes = _parse_name_status(
            _git_bytes(
                vault,
                "diff-tree",
                "--no-commit-id",
                "--no-renames",
                "--name-status",
                "-r",
                "-z",
                oid,
            )
        )
        records.append(
            CommitRecord(
                oid=oid,
                message=message,
                parents=tuple(parents_text.split()) if parents_text else (),
                changes=changes,
            )
        )
    return tuple(records)


def _parse_index_entries(output: bytes) -> dict[str, tuple[str, ...]]:
    entries: dict[str, list[tuple[int, str]]] = {}
    for record in output.split(b"\0"):
        if not record:
            continue
        header, separator, path_bytes = record.partition(b"\t")
        fields = header.split(b" ")
        if separator != b"\t" or len(fields) != 3:
            raise ValueError("Git emitted a malformed index entry")
        mode, oid, stage = fields
        try:
            mode_text = mode.decode("ascii")
            oid_text = oid.decode("ascii")
            stage_text = stage.decode("ascii")
        except UnicodeDecodeError as exc:
            raise ValueError("Git emitted non-ASCII index metadata") from exc
        try:
            stage_number = int(stage_text)
        except ValueError as exc:
            raise ValueError("Git emitted a malformed index stage") from exc
        path = _decode_path(path_bytes)
        path_entries = entries.setdefault(path, [])
        if any(existing_stage == stage_number for existing_stage, _ in path_entries):
            raise ValueError("Git emitted a duplicate index stage")
        path_entries.append(
            (stage_number, f"{mode_text}:{oid_text}:{stage_text}")
        )
    return {
        path: tuple(value for _, value in sorted(path_entries))
        for path, path_entries in entries.items()
    }


def _parse_porcelain(output: bytes) -> dict[str, str]:
    statuses: dict[str, str] = {}
    for record in output.split(b"\0"):
        if not record:
            continue
        if len(record) < 4 or record[2:3] != b" ":
            raise ValueError("Git emitted a malformed porcelain record")
        try:
            status_value = record[:2].decode("ascii")
        except UnicodeDecodeError as exc:
            raise ValueError("Git emitted a non-ASCII porcelain status") from exc
        path = _decode_path(record[3:])
        if not path or path in statuses:
            raise ValueError("Git emitted an ambiguous porcelain path")
        statuses[path] = status_value
    return statuses


def _read_regular_no_follow(path: Path) -> tuple[bytes, int]:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise OSError("path changed type while Gate 3 read it")
        with os.fdopen(descriptor, "rb", closefd=True) as stream:
            descriptor = -1
            contents = stream.read()
        return contents, stat.S_IMODE(opened.st_mode)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _read_relative_regular_no_follow(
    vault: Path, relative: str
) -> tuple[bytes, int]:
    """Read one vault-relative regular leaf without following any component."""
    parts = _path_parts(relative)
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if parts is None or no_follow is None:
        raise OSError("safe no-follow reads are unavailable")
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | no_follow
    parent_descriptor = os.open(vault, directory_flags)
    try:
        for component in parts[:-1]:
            next_descriptor = os.open(
                component, directory_flags, dir_fd=parent_descriptor
            )
            os.close(parent_descriptor)
            parent_descriptor = next_descriptor
        descriptor = os.open(
            parts[-1], os.O_RDONLY | no_follow, dir_fd=parent_descriptor
        )
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode):
                raise OSError("path changed type while Gate 3 read it")
            with os.fdopen(descriptor, "rb", closefd=True) as stream:
                descriptor = -1
                contents = stream.read()
            return contents, stat.S_IMODE(opened.st_mode)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
    finally:
        os.close(parent_descriptor)


_IDENTITY_DOMAIN = b"oneos-gate3-identity-v1\0"
_TARGET_DOMAIN = b"oneos-gate3-target-v1\0"
_UNCLASSIFIABLE = "Gate 3 filesystem entry is unclassifiable"
_TRAVERSAL_FAILED = "Gate 3 filesystem traversal failed"


def _filesystem_kind(mode: int) -> FilesystemKind:
    """Map one no-follow mode to exactly one supplemental kind.

    Regular files are deliberately not supplemental evidence — they stay
    under Git's status, index, content and mode rules — so a regular mode
    reaching here is internal misuse, not an unknown type.
    """
    if stat.S_ISREG(mode):
        raise FilesystemEvidenceError(_UNCLASSIFIABLE)
    if stat.S_ISDIR(mode):
        return "directory"
    if stat.S_ISLNK(mode):
        return "symlink"
    if stat.S_ISFIFO(mode):
        return "fifo"
    if stat.S_ISSOCK(mode):
        return "socket"
    if stat.S_ISCHR(mode):
        return "char-device"
    if stat.S_ISBLK(mode):
        return "block-device"
    return "other"


def _length_delimited(*fields: bytes) -> bytes:
    """Join fields so no concatenation can imitate a different tuple."""
    joined = b""
    for value in fields:
        joined += str(len(value)).encode("ascii") + b":" + value
    return joined


def _filesystem_identity_digest(
    kind: FilesystemKind, metadata: os.stat_result
) -> str:
    """Stable identity over device and inode, never content or path text.

    This is what distinguishes a directory that was removed and recreated
    from one that never changed; both present the same kind and mode.
    """
    fields = [
        kind.encode("ascii"),
        str(metadata.st_dev).encode("ascii"),
        str(metadata.st_ino).encode("ascii"),
    ]
    if kind in {"char-device", "block-device"}:
        fields.append(str(metadata.st_rdev).encode("ascii"))
    return hashlib.sha256(
        _IDENTITY_DOMAIN + _length_delimited(*fields)
    ).hexdigest()


def _filesystem_fingerprint(
    parent_descriptor: int,
    name: str,
    metadata: os.stat_result,
) -> FilesystemFingerprint:
    """Fingerprint one entry from metadata already captured no-follow."""
    kind = _filesystem_kind(metadata.st_mode)
    target_digest: str | None = None
    if kind == "symlink":
        try:
            raw_target = os.readlink(name, dir_fd=parent_descriptor)
        except OSError as exc:
            raise FilesystemEvidenceError(_TRAVERSAL_FAILED) from exc
        # The link text is the evidence. Resolving or opening the target
        # would leave the boundary and could read outside the vault.
        target_digest = hashlib.sha256(
            _TARGET_DOMAIN + os.fsencode(raw_target)
        ).hexdigest()
    return FilesystemFingerprint(
        kind=kind,
        mode=stat.S_IMODE(metadata.st_mode),
        identity_digest=_filesystem_identity_digest(kind, metadata),
        target_digest=target_digest,
    )


def _fingerprint_path(
    vault: Path,
    relative: str,
    status_value: str,
    index_entries: tuple[str, ...],
) -> DirtyFingerprint:
    parts = _path_parts(relative)
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if parts is None or no_follow is None:
        return DirtyFingerprint(status_value, index_entries, "redirected", None, None)
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | no_follow
    try:
        parent_descriptor = os.open(vault, directory_flags)
    except FileNotFoundError:
        return DirtyFingerprint(status_value, index_entries, "absence", None, None)
    except OSError:
        return DirtyFingerprint(status_value, index_entries, "redirected", None, None)
    try:
        for component in parts[:-1]:
            try:
                next_descriptor = os.open(
                    component, directory_flags, dir_fd=parent_descriptor
                )
            except FileNotFoundError:
                return DirtyFingerprint(
                    status_value, index_entries, "absence", None, None
                )
            except OSError:
                return DirtyFingerprint(
                    status_value, index_entries, "redirected", None, None
                )
            os.close(parent_descriptor)
            parent_descriptor = next_descriptor

        leaf = parts[-1]
        try:
            metadata = os.stat(leaf, dir_fd=parent_descriptor, follow_symlinks=False)
        except FileNotFoundError:
            return DirtyFingerprint(status_value, index_entries, "absence", None, None)
        except OSError:
            return DirtyFingerprint(
                status_value, index_entries, "redirected", None, None
            )
        mode = stat.S_IMODE(metadata.st_mode)
        if stat.S_ISREG(metadata.st_mode):
            descriptor = -1
            try:
                descriptor = os.open(
                    leaf, os.O_RDONLY | no_follow, dir_fd=parent_descriptor
                )
                opened = os.fstat(descriptor)
                if not stat.S_ISREG(opened.st_mode):
                    return DirtyFingerprint(
                        status_value, index_entries, "redirected", None, None
                    )
                with os.fdopen(descriptor, "rb", closefd=True) as stream:
                    descriptor = -1
                    contents = stream.read()
            except OSError:
                return DirtyFingerprint(
                    status_value, index_entries, "redirected", None, None
                )
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
            return DirtyFingerprint(
                status_value,
                index_entries,
                "file",
                stat.S_IMODE(opened.st_mode),
                hashlib.sha256(contents).hexdigest(),
            )
        if stat.S_ISLNK(metadata.st_mode):
            try:
                target = os.fsencode(os.readlink(leaf, dir_fd=parent_descriptor))
            except OSError:
                return DirtyFingerprint(
                    status_value, index_entries, "redirected", None, None
                )
            return DirtyFingerprint(
                status_value,
                index_entries,
                "symlink",
                mode,
                hashlib.sha256(target).hexdigest(),
            )
        kind = "directory" if stat.S_ISDIR(metadata.st_mode) else "other"
        return DirtyFingerprint(status_value, index_entries, kind, mode, None)
    finally:
        os.close(parent_descriptor)


def collect_dirty_fingerprints(vault: Path) -> dict[str, DirtyFingerprint]:
    """Fingerprint every Git-dirty path without rename pairing or path quoting."""
    vault = Path(vault).resolve()
    statuses = _parse_porcelain(
        _git_bytes(
            vault,
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
            "--no-renames",
        )
    )
    index_entries = _parse_index_entries(
        _git_bytes(vault, "ls-files", "--stage", "-z")
    )
    _supplement_untracked_consumed_entries(vault, statuses, index_entries)
    return {
        relative: _fingerprint_path(
            vault, relative, statuses[relative], index_entries.get(relative, ())
        )
        for relative in sorted(statuses)
    }


def _supplement_untracked_consumed_entries(
    vault: Path,
    statuses: dict[str, str],
    index_entries: dict[str, tuple[str, ...]],
) -> None:
    """Make Git-invisible special leaves in exact S7 stores auditable."""
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if no_follow is None:
        return
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | no_follow
    catalog = EntityCatalog.load(vault)

    def record_untracked(relative: str) -> None:
        if relative not in index_entries:
            statuses.setdefault(relative, "??")

    for entity in catalog.entities:
        descriptors: list[int] = []
        try:
            descriptor = os.open(vault, directory_flags)
            descriptors.append(descriptor)
            traversed: list[str] = []
            for component in (entity.slug, "outbox", ".consumed"):
                traversed.append(component)
                try:
                    descriptor = os.open(
                        component, directory_flags, dir_fd=descriptor
                    )
                except FileNotFoundError:
                    break
                except OSError:
                    record_untracked("/".join(traversed))
                    break
                descriptors.append(descriptor)
            else:
                try:
                    leaves = os.listdir(descriptors[-1])
                except OSError:
                    record_untracked(f"{entity.slug}/outbox/.consumed")
                else:
                    for leaf in leaves:
                        record_untracked(
                            f"{entity.slug}/outbox/.consumed/{leaf}"
                        )
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)


def _path_parts(path: str) -> tuple[str, ...] | None:
    if path.startswith("/") or "\0" in path:
        return None
    parts = tuple(path.split("/"))
    if not parts or any(part in {"", ".", ".."} for part in parts):
        return None
    return parts


def _canonical_markdown_leaf(leaf: str) -> bool:
    return (
        bool(leaf)
        and leaf not in {".", ".."}
        and not leaf.startswith(".")
        and leaf == leaf.strip()
        and "\\" not in leaf
        and "\r" not in leaf
        and "\n" not in leaf
        and Path(leaf).suffix == ".md"
    )


def _inbox_path(path: str, rules: AuditRules) -> tuple[str, str] | None:
    parts = _path_parts(path)
    if (
        parts is None
        or len(parts) != 4
        or parts[0] not in rules.entities
        or parts[1:3] != ("00-inbox", "active")
        or not _canonical_markdown_leaf(parts[3])
    ):
        return None
    return parts[0], parts[3]


def _active_destination(path: str, rules: AuditRules) -> tuple[str, str] | None:
    parts = _path_parts(path)
    if parts is None or len(parts) != 4:
        return None
    entity, module, lifecycle, leaf = parts
    if (
        entity not in rules.entities
        or lifecycle != "active"
        or module in {"outbox", "staging", "_system"}
        or module not in rules.active_lifecycle_modules[entity]
        or not _canonical_markdown_leaf(leaf)
    ):
        return None
    return entity, leaf


def _sanctioned_ingest(record: CommitRecord, rules: AuditRules) -> bool:
    return (
        len(record.parents) == 1
        and len(record.changes) == 1
        and record.changes[0].status == "A"
        and _inbox_path(record.changes[0].path, rules) is not None
    )


def _receipt_path(path: str, rules: AuditRules) -> tuple[str, str] | None:
    parts = _path_parts(path)
    if parts is None or len(parts) != 4:
        return None
    entity, outbox, store, leaf = parts
    if (
        entity not in rules.entities
        or outbox != "outbox"
        or store != ".receipts"
        or not leaf.endswith(".yaml")
    ):
        return None
    proposal_id = leaf[:-5]
    try:
        canonical_id = require_proposal_id(proposal_id)
    except ProposalIdentityError:
        return None
    return entity, canonical_id


def _receipt_from_commit(
    vault: Path, record: CommitRecord, path: str
) -> ActionReceipt | None:
    """Read and validate the receipt blob from the commit being audited."""
    try:
        contents = _git_bytes(vault, "show", f"{record.oid}:{path}")
        return parse_action_receipt(Path(path), contents)
    except (OSError, ReceiptError, subprocess.CalledProcessError):
        return None


def _sanctioned_outbox(
    record: CommitRecord, rules: AuditRules, vault: Path
) -> bool:
    message = _OUTBOX_APPROVAL_MESSAGE.fullmatch(record.message)
    if message is None or len(record.parents) != 1 or len(record.changes) != 3:
        return False
    deleted = [change for change in record.changes if change.status == "D"]
    added = [change for change in record.changes if change.status == "A"]
    if len(deleted) != 1 or len(added) != 2:
        return False
    source = _inbox_path(deleted[0].path, rules)
    destinations = [
        value
        for change in added
        if (value := _active_destination(change.path, rules)) is not None
    ]
    receipts = [
        (change, value)
        for change in added
        if (value := _receipt_path(change.path, rules)) is not None
    ]
    receipt = (
        _receipt_from_commit(vault, record, receipts[0][0].path)
        if len(receipts) == 1
        else None
    )
    return (
        source is not None
        and len(destinations) == 1
        and len(receipts) == 1
        and source == destinations[0]
        and source[0] == receipts[0][1][0]
        and receipts[0][1][1] == message.group(1)
        and receipt is not None
        and receipt.proposal_id == message.group(1)
        and receipt.action_kind == "approval"
    )


def _sanctioned_registry(
    record: CommitRecord, rules: AuditRules, vault: Path
) -> bool:
    match = _REGISTRY_MESSAGE.fullmatch(record.message)
    if match is None or len(record.parents) != 1:
        return False
    action = match.group(1)
    kind = match.group(2)
    registry = [
        change
        for change in record.changes
        if change.status in {"A", "M"} and change.path == _REGISTRY_PATH[kind]
    ]
    if len(registry) != 1:
        return False
    if action != "delete":
        return len(record.changes) == 1
    receipts = [
        (change, value)
        for change in record.changes
        if change.status == "A"
        and (value := _receipt_path(change.path, rules)) is not None
    ]
    receipt = (
        _receipt_from_commit(vault, record, receipts[0][0].path)
        if len(receipts) == 1
        else None
    )
    return (
        len(record.changes) == 2
        and len(receipts) == 1
        and receipt is not None
        and receipt.proposal_id == receipts[0][1][1]
        and receipt.action_kind == "registry deletion"
    )


def _parent_tree(
    vault: Path, parent_oid: str
) -> tuple[tempfile.TemporaryDirectory[str], Path, tuple[str, ...]]:
    temporary = tempfile.TemporaryDirectory(prefix="oneos-gate3-rename-")
    temporary_root = Path(temporary.name)
    tree = temporary_root / "tree"
    tree.mkdir()
    index = temporary_root / "index"
    alternate_env = os.environ.copy()
    alternate_env["GIT_INDEX_FILE"] = os.fspath(index)
    try:
        _git_bytes(vault, "read-tree", parent_oid, env=alternate_env)
        _git_bytes(
            vault,
            "checkout-index",
            "--all",
            f"--prefix={tree}{os.sep}",
            env=alternate_env,
        )
        tracked = tuple(
            sorted(
                _parse_index_entries(
                    _git_bytes(vault, "ls-files", "--stage", "-z", env=alternate_env)
                )
            )
        )
    except Exception:
        temporary.cleanup()
        raise
    return temporary, tree, tracked


def _audit_commit_history(
    records: tuple[CommitRecord, ...], vault: Path
) -> Audit:
    result = Audit()
    for record in records:
        temporary: tempfile.TemporaryDirectory[str] | None = None
        try:
            temporary, commit_tree, _ = _parent_tree(vault, record.oid)
            commit_rules = AuditRules.load(commit_tree)
            audited = audit_commits((record,), commit_rules, vault)
            result.sanctioned_commits.extend(audited.sanctioned_commits)
            result.violating_commits.extend(audited.violating_commits)
        finally:
            if temporary is not None:
                temporary.cleanup()
    return result


def _relative_to(path: Path, parent: Path) -> Path | None:
    try:
        return path.relative_to(parent)
    except ValueError:
        return None


def _rename_envelope(
    tree: Path,
    tracked_paths: tuple[str, ...],
    axis: str,
    old: str,
    new: str,
    *,
    parent_oid: str,
) -> frozenset[tuple[str, str]]:
    plan = build_rename_plan(
        tree,
        axis,
        old,
        new,
        planned_head=parent_oid,
    )
    planned_root = plan.vault
    moves = tuple(
        (source.relative_to(planned_root), destination.relative_to(planned_root))
        for source, destination in plan.moves
    )
    envelope: set[tuple[str, str]] = set()
    for tracked in tracked_paths:
        tracked_path = Path(tracked)
        for source, destination in moves:
            tail = _relative_to(tracked_path, source)
            if tail is not None:
                envelope.add(("D", tracked_path.as_posix()))
                envelope.add(("A", (destination / tail).as_posix()))
                break
    for edited in plan.edits:
        relative = edited.relative_to(planned_root)
        if any(_relative_to(relative, source) is not None for source, _ in moves):
            continue
        envelope.add(("M", relative.as_posix()))
    return frozenset(envelope)


def _sanctioned_rename(record: CommitRecord, vault: Path) -> bool:
    match = _RENAME_MESSAGE.fullmatch(record.message)
    if match is None or len(record.parents) != 1:
        return False
    actual = frozenset((change.status, change.path) for change in record.changes)
    if len(actual) != len(record.changes) or not actual:
        return False
    old, new = match.groups()
    temporary: tempfile.TemporaryDirectory[str] | None = None
    try:
        temporary, tree, tracked = _parent_tree(vault, record.parents[0])
        for axis in sorted(AXES):
            try:
                expected = _rename_envelope(
                    tree,
                    tracked,
                    axis,
                    old,
                    new,
                    parent_oid=record.parents[0],
                )
            except (OSError, RenameError, UnicodeError, sqlite3.Error):
                continue
            if expected and actual == expected:
                return True
    except (OSError, subprocess.CalledProcessError, ValueError):
        return False
    finally:
        if temporary is not None:
            temporary.cleanup()
    return False


def _commit_is_sanctioned(
    record: CommitRecord, rules: AuditRules, vault: Path
) -> bool:
    if record.message.startswith("ingest:"):
        return _sanctioned_ingest(record, rules)
    if record.message.startswith("outbox:"):
        return _sanctioned_outbox(record, rules, vault)
    if record.message.startswith("registry:"):
        return _sanctioned_registry(record, rules, vault)
    if record.message.startswith("rename:"):
        return _sanctioned_rename(record, vault)
    return False


def audit_commits(
    records: tuple[CommitRecord, ...], rules: AuditRules, vault: Path
) -> Audit:
    result = Audit()
    for record in records:
        destination = (
            result.sanctioned_commits
            if _commit_is_sanctioned(record, rules, vault)
            else result.violating_commits
        )
        destination.append(record.message)
    return result


def _canonical_created(proposal_id: object, value: object) -> bool:
    if not isinstance(proposal_id, str) or not isinstance(value, str):
        return False
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return False
    return (
        parsed.isoformat(timespec="seconds") == value
        and parsed.strftime("%Y%m%dT%H%M%S") == proposal_id.partition("-")[0]
    )


def _canonical_classification_record(record: dict) -> bool:
    return (
        set(record) == _CLASSIFY_RECORD_FIELDS
        and _canonical_created(record.get("id"), record.get("created"))
        and (
            record.get("rule_id") is None
            or isinstance(record.get("rule_id"), str)
        )
    )


def _canonical_delete_record(record: dict) -> bool:
    kind = record.get("kind")
    slug = record.get("slug")
    total = record.get("total_references")
    impact = record.get("impact")
    if (
        set(record) != _DELETE_RECORD_FIELDS
        or kind not in _DELETE_KINDS
        or not isinstance(slug, str)
        or _CANONICAL_SLUG.fullmatch(slug) is None
        or not _canonical_created(record.get("id"), record.get("created"))
        or not isinstance(total, int)
        or isinstance(total, bool)
        or total < 0
        or not isinstance(impact, dict)
        or set(impact) != _DELETE_IMPACT_FIELDS
    ):
        return False
    if not all(
        isinstance(key, str)
        and isinstance(value, int)
        and not isinstance(value, bool)
        and value >= 0
        for key, value in impact.items()
    ):
        return False
    return total == sum(impact.values())


def _new_proposal_is_sanctioned(
    relative: str,
    fingerprint: DirtyFingerprint,
    rules: AuditRules,
    vault: Path,
) -> bool:
    if fingerprint.status != "??" or fingerprint.kind != "file":
        return False
    parts = _path_parts(relative)
    if (
        parts is None
        or len(parts) != 3
        or parts[0] not in rules.entities
        or parts[1] != "outbox"
        or not parts[2].endswith(".yaml")
    ):
        return False
    entity, _, leaf = parts
    scope = Scope(vault, entity)
    lexical_outbox = rules.root / entity / "outbox"
    path = lexical_outbox / leaf
    try:
        if (
            lexical_outbox.is_symlink()
            or not lexical_outbox.is_dir()
            or scope.resolve("outbox") != lexical_outbox
            or path.parent != lexical_outbox
            or path.is_symlink()
        ):
            return False
        contents, _ = _read_regular_no_follow(path)
        loaded = yaml.safe_load(contents.decode("utf-8"))
        if not isinstance(loaded, dict):
            return False
        proposal_id = require_proposal_identity(path, loaded.get("id"))
        action = loaded.get("action")
        if (
            loaded.get("entity") != entity
            or loaded.get("status") != "pending"
            or action not in {"classify", "delete"}
        ):
            return False
        if action == "classify":
            if not _canonical_classification_record(loaded):
                return False
            proposal = _to_proposal(path, loaded)
            proposal = _require_destination(scope, proposal)
            source = _fingerprint_path(
                rules.root, proposal.src, "  ", index_entries=()
            )
            return (
                proposal.status == "pending"
                and source.kind == "file"
                and source.digest == proposal.source_sha256
            )
        if not _canonical_delete_record(loaded):
            return False
        proposal = get_delete_proposal(scope, proposal_id)
        return (
            proposal.path == path
            and proposal.entity == entity
            and proposal.kind == loaded["kind"]
            and proposal.slug == loaded["slug"]
        )
    except (
        CrossScopeError,
        DestinationRegistryError,
        OSError,
        OutboxError,
        ProposalIdentityError,
        RegistryError,
        UnicodeError,
        ValueError,
        KeyError,
        TypeError,
        yaml.YAMLError,
    ):
        return False


def _load_consumed_record(
    relative: str,
    fingerprint: DirtyFingerprint,
    rules: AuditRules,
    vault: Path,
) -> _ConsumedRecord | None:
    """Validate the exact S7 entity-local quarantine leaf and its bytes."""
    parts = _path_parts(relative)
    if (
        fingerprint.status != "??"
        or fingerprint.index_entries
        or fingerprint.kind != "file"
        or fingerprint.mode is None
        or fingerprint.digest is None
        or parts is None
        or len(parts) != 4
        or parts[0] not in rules.entities
        or parts[1:3] != ("outbox", ".consumed")
        or not parts[3].endswith(".yaml")
        or rules.root != vault
    ):
        return None
    entity, _, _, leaf = parts
    proposal_id = leaf[:-5]
    pending_relative = f"{entity}/outbox/{leaf}"
    pending_path = vault / pending_relative
    try:
        canonical_id = require_proposal_id(proposal_id)
        contents, mode = _read_relative_regular_no_follow(vault, relative)
        digest = hashlib.sha256(contents).hexdigest()
        if mode != fingerprint.mode or digest != fingerprint.digest:
            return None
        loaded = yaml.safe_load(contents.decode("utf-8"))
        if not isinstance(loaded, dict):
            return None
        require_proposal_identity(pending_path, loaded.get("id"))
        if (
            loaded.get("id") != canonical_id
            or loaded.get("entity") != entity
            or loaded.get("status") != "pending"
        ):
            return None
        action = loaded.get("action")
        scope = Scope(vault, entity)
        if action == "classify":
            if not _canonical_classification_record(loaded):
                return None
            proposal = _to_proposal(pending_path, loaded)
            canonical = resolve_classification_destination(
                scope,
                vault / proposal.src,
                module=proposal.module,
                sub=proposal.sub,
                claimed_block=proposal.block,
                require_source=False,
            )
            if (
                proposal.entity != entity
                or proposal.src != canonical.src
                or proposal.dst != canonical.dst
            ):
                return None
            return _ConsumedRecord(
                relative=relative,
                pending_relative=pending_relative,
                entity=entity,
                proposal_id=canonical_id,
                action=action,
                digest=digest,
                source=proposal.src,
                destination=proposal.dst,
            )
        if action == "delete" and _canonical_delete_record(loaded):
            proposal = _validate_delete_record(scope, pending_path, loaded)
            return _ConsumedRecord(
                relative=relative,
                pending_relative=pending_relative,
                entity=entity,
                proposal_id=canonical_id,
                action=action,
                digest=digest,
                registry_kind=proposal.kind,
            )
    except (
        CrossScopeError,
        DestinationRegistryError,
        DestinationError,
        OSError,
        OutboxError,
        ProposalIdentityError,
        RegistryError,
        UnicodeError,
        ValueError,
        KeyError,
        TypeError,
        yaml.YAMLError,
    ):
        return None
    return None


def _receipt_bearing(record: CommitRecord) -> bool:
    """Whether this commit's envelope can carry a receipt authorization."""
    if _OUTBOX_APPROVAL_MESSAGE.fullmatch(record.message) is not None:
        return True
    registry = _REGISTRY_MESSAGE.fullmatch(record.message)
    return registry is not None and registry.group(1) == "delete"


def _commit_relative_rules(
    vault: Path, records: tuple[CommitRecord, ...]
) -> dict[str, AuditRules]:
    """Registry rules as of each receipt-bearing commit.

    `_audit_commit_history` already judges every commit against its own
    tree, so an approval made before a rename stays sanctioned. Reading
    authorization from the *final* rules instead left the two auditors
    disagreeing: the old slug is gone from the final manifest, so every path
    helper refused, no authorization was emitted, and a missing quarantine
    record never reached `unclaimed`. A failure to build a commit's rules
    propagates rather than dropping its authorization — a dropped
    authorization is a silent PASS.
    """
    resolved: dict[str, AuditRules] = {}
    for record in records:
        if not _receipt_bearing(record) or record.oid in resolved:
            continue
        temporary: tempfile.TemporaryDirectory[str] | None = None
        try:
            temporary, commit_tree, _ = _parent_tree(vault, record.oid)
            resolved[record.oid] = AuditRules.load(commit_tree)
        finally:
            if temporary is not None:
                temporary.cleanup()
    return resolved


def _receipt_authorizations(
    records: tuple[CommitRecord, ...],
    rules: AuditRules,
    vault: Path,
    commit_rules: dict[str, AuditRules],
) -> tuple[_ReceiptAuthorization, ...]:
    """Extract exact-byte authorities only from sanctioned session commits."""
    authorizations: list[_ReceiptAuthorization] = []
    for record in records:
        # The commit's own rules, never the final vault's, and never carried
        # into the next iteration: a later rename must not erase an
        # authorization this session actually issued.
        record_rules = commit_rules.get(record.oid, rules)
        approval = _OUTBOX_APPROVAL_MESSAGE.fullmatch(record.message)
        if approval is not None and _sanctioned_outbox(record, record_rules, vault):
            deleted = [change.path for change in record.changes if change.status == "D"]
            destinations = [
                change.path
                for change in record.changes
                if change.status == "A"
                and _active_destination(change.path, record_rules) is not None
            ]
            receipt_changes = [
                change.path
                for change in record.changes
                if change.status == "A"
                and _receipt_path(change.path, record_rules) is not None
            ]
            if len(deleted) != 1 or len(destinations) != 1 or len(receipt_changes) != 1:
                continue
            receipt_path = receipt_changes[0]
            receipt_identity = _receipt_path(receipt_path, record_rules)
            receipt = _receipt_from_commit(vault, record, receipt_path)
            if receipt_identity is None or receipt is None:
                continue
            authorizations.append(
                _ReceiptAuthorization(
                    key=(record.oid, receipt_path),
                    entity=receipt_identity[0],
                    proposal_id=receipt.proposal_id,
                    action_kind=receipt.action_kind,
                    review_sha256=receipt.review_sha256,
                    source=deleted[0],
                    destination=destinations[0],
                )
            )
            continue

        registry = _REGISTRY_MESSAGE.fullmatch(record.message)
        if (
            registry is None
            or registry.group(1) != "delete"
            or not _sanctioned_registry(record, record_rules, vault)
        ):
            continue
        receipt_changes = [
            change.path
            for change in record.changes
            if change.status == "A"
            and _receipt_path(change.path, record_rules) is not None
        ]
        if len(receipt_changes) != 1:
            continue
        receipt_path = receipt_changes[0]
        receipt_identity = _receipt_path(receipt_path, record_rules)
        receipt = _receipt_from_commit(vault, record, receipt_path)
        if receipt_identity is None or receipt is None:
            continue
        authorizations.append(
            _ReceiptAuthorization(
                key=(record.oid, receipt_path),
                entity=receipt_identity[0],
                proposal_id=receipt.proposal_id,
                action_kind=receipt.action_kind,
                review_sha256=receipt.review_sha256,
                registry_kind=registry.group(2),
            )
        )
    return tuple(authorizations)


def _authorization_matches(
    consumed: _ConsumedRecord, authorization: _ReceiptAuthorization
) -> bool:
    common = (
        consumed.entity == authorization.entity
        and consumed.proposal_id == authorization.proposal_id
        and consumed.digest == authorization.review_sha256
    )
    if consumed.action == "classify":
        return (
            common
            and authorization.action_kind == "approval"
            and consumed.source == authorization.source
            and consumed.destination == authorization.destination
        )
    return (
        common
        and consumed.action == "delete"
        and authorization.action_kind == "registry deletion"
        and consumed.registry_kind == authorization.registry_kind
    )


def _record_mentions_proposal(
    record: CommitRecord,
    entity: str,
    proposal_id: str,
    rules: AuditRules,
    commit_rules: dict[str, AuditRules],
) -> bool:
    """Conservatively keep a failed transaction from masquerading as reject.

    Read with the commit's own rules for the same reason authorization is:
    a receipt written under a slug a later rename retired still mentions its
    proposal, and missing that would let the reject branch adopt it.
    """
    approval = _OUTBOX_APPROVAL_MESSAGE.fullmatch(record.message)
    if approval is not None and approval.group(1) == proposal_id:
        return True
    record_rules = commit_rules.get(record.oid, rules)
    return any(
        _receipt_path(change.path, record_rules) == (entity, proposal_id)
        for change in record.changes
    )


def _sanctioned_consumed_paths(
    before: dict[str, DirtyFingerprint],
    after: dict[str, DirtyFingerprint],
    rules: AuditRules,
    vault: Path,
    records: tuple[CommitRecord, ...],
    commit_rules: dict[str, AuditRules],
) -> tuple[set[str], set[str], set[str]]:
    authorizations = _receipt_authorizations(records, rules, vault, commit_rules)
    claimed_authorizations: set[tuple[str, str]] = set()
    sanctioned: set[str] = set()
    blocked_pending: set[str] = set()
    for relative in sorted(set(after) - set(before)):
        consumed = _load_consumed_record(relative, after[relative], rules, vault)
        if consumed is None:
            continue
        pending_absent = consumed.pending_relative not in after
        if not pending_absent:
            blocked_pending.add(consumed.pending_relative)
        pending_before = before.get(consumed.pending_relative)
        snapshot_pair = (
            pending_before is not None
            and pending_absent
            and pending_before == after[relative]
        )
        matching = [
            authorization
            for authorization in authorizations
            if _authorization_matches(consumed, authorization)
            and authorization.key not in claimed_authorizations
        ]
        if (
            len(matching) == 1
            and pending_absent
            and (pending_before is None or snapshot_pair)
        ):
            claimed_authorizations.add(matching[0].key)
            sanctioned.add(relative)
            if pending_before is not None:
                sanctioned.add(consumed.pending_relative)
            continue
        mentioned = any(
            _record_mentions_proposal(
                record, consumed.entity, consumed.proposal_id, rules, commit_rules
            )
            for record in records
        )
        if (
            consumed.action == "classify"
            and not matching
            and snapshot_pair
            and not mentioned
        ):
            sanctioned.update({relative, consumed.pending_relative})
    unclaimed = {
        f"{authorization.entity}/outbox/.consumed/"
        f"{authorization.proposal_id}.yaml"
        for authorization in authorizations
        if authorization.key not in claimed_authorizations
    }
    return sanctioned, blocked_pending, unclaimed


def audit_dirty(
    before: dict[str, DirtyFingerprint],
    after: dict[str, DirtyFingerprint],
    rules: AuditRules,
    vault: Path,
    *,
    records: tuple[CommitRecord, ...] = (),
) -> Audit:
    vault = Path(vault).resolve()
    result = Audit()
    commit_rules = _commit_relative_rules(vault, records)
    sanctioned, blocked_pending, unclaimed = _sanctioned_consumed_paths(
        before, after, rules, vault, records, commit_rules
    )
    for relative in sorted(set(before) | set(after)):
        if relative in sanctioned:
            result.sanctioned_writes.append(relative)
            continue
        if relative in before:
            if after.get(relative) != before[relative]:
                result.violating_writes.append(relative)
            continue
        fingerprint = after[relative]
        destination = (
            result.sanctioned_writes
            if relative not in blocked_pending
            and _new_proposal_is_sanctioned(
                relative, fingerprint, rules, vault
            )
            else result.violating_writes
        )
        destination.append(relative)
    for relative in sorted(unclaimed - set(result.violating_writes)):
        result.violating_writes.append(relative)
    return result


def _validate_receipt_stores(vault: Path, rules: AuditRules) -> None:
    """Run the complete O(store) receipt audit only at Gate 3."""
    del rules  # Scope comes from HEAD itself, never a mutable manifest.
    validate_all_head_receipt_stores(vault)


def _vault() -> Path:
    return Path(os.environ["ONEOS_VAULT"]).expanduser().resolve()


def _snapshot_path(vault: Path) -> Path:
    path = Path(
        os.environ.get("GATE3_SNAP", "./.gate3-snapshot.json")
    ).expanduser().resolve()
    if path == vault or path.is_relative_to(vault):
        raise ValueError("Gate 3 snapshot must stay outside the vault")
    return path


def _snapshot_payload(vault: Path) -> dict[str, object]:
    dirty = collect_dirty_fingerprints(vault)
    return {
        "version": SNAPSHOT_VERSION,
        "head": _git_text(vault, "rev-parse", "HEAD").strip(),
        "dirty": {
            path: asdict(fingerprint)
            for path, fingerprint in sorted(dirty.items())
        },
        "filesystem": {},
    }


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    """`json.loads` keeps the last repeated key and drops the rest silently.

    A second value for one snapshot path would overwrite the evidence the
    snapshot recorded, so the repetition is refused before it can collapse.
    """
    seen: dict[str, object] = {}
    for key, value in pairs:
        if key in seen:
            raise ValueError("Gate 3 snapshot has a duplicate key")
        seen[key] = value
    return seen


def _load_filesystem_fingerprint(value: object) -> FilesystemFingerprint:
    """Parse one closed supplemental entry, rejecting every other shape."""
    expected = {"kind", "mode", "identity_digest", "target_digest"}
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError("Gate 3 snapshot filesystem fingerprint is malformed")
    kind = value["kind"]
    mode = value["mode"]
    identity_digest = value["identity_digest"]
    target_digest = value["target_digest"]
    if kind not in _FILESYSTEM_KINDS:
        raise ValueError("Gate 3 snapshot filesystem kind is malformed")
    # `bool` is an `int` subclass, so `mode: true` would otherwise pass.
    if isinstance(mode, bool) or not isinstance(mode, int) or mode < 0:
        raise ValueError("Gate 3 snapshot filesystem mode is malformed")
    if (
        not isinstance(identity_digest, str)
        or _SHA256_HEX.fullmatch(identity_digest) is None
    ):
        raise ValueError("Gate 3 snapshot filesystem digest is malformed")
    if kind == "symlink":
        if (
            not isinstance(target_digest, str)
            or _SHA256_HEX.fullmatch(target_digest) is None
        ):
            raise ValueError("Gate 3 snapshot filesystem digest is malformed")
    elif target_digest is not None:
        raise ValueError("Gate 3 snapshot filesystem digest is malformed")
    return FilesystemFingerprint(
        kind=kind,
        mode=mode,
        identity_digest=identity_digest,
        target_digest=target_digest,
    )


def _load_filesystem_map(raw: object) -> dict[str, FilesystemFingerprint]:
    if not isinstance(raw, dict):
        raise ValueError("Gate 3 snapshot filesystem map is malformed")
    fingerprints: dict[str, FilesystemFingerprint] = {}
    for relative, value in raw.items():
        if not isinstance(relative, str) or _path_parts(relative) is None:
            raise ValueError("Gate 3 snapshot filesystem path is malformed")
        try:
            relative.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError(
                "Gate 3 snapshot filesystem path is malformed"
            ) from exc
        fingerprints[relative] = _load_filesystem_fingerprint(value)
    return fingerprints


def _load_snapshot(path: Path) -> Gate3Snapshot:
    raw = json.loads(
        path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys
    )
    if not isinstance(raw, dict):
        raise ValueError("Gate 3 snapshot is malformed")
    # Version first: an operator holding an older snapshot needs the
    # actionable reason, not a generic shape complaint about the map that
    # version never had.
    if raw.get("version") != SNAPSHOT_VERSION:
        raise ValueError("Gate 3 snapshot version is unsupported")
    if set(raw) != {"version", "head", "dirty", "filesystem"}:
        raise ValueError("Gate 3 snapshot is malformed")
    head = raw.get("head")
    dirty = raw.get("dirty")
    if not isinstance(head, str) or _OID.fullmatch(head) is None:
        raise ValueError("Gate 3 snapshot HEAD is malformed")
    if not isinstance(dirty, dict) or not all(
        isinstance(path_value, str) and isinstance(value, dict)
        for path_value, value in dirty.items()
    ):
        raise ValueError("Gate 3 snapshot dirty map is malformed")
    fingerprints: dict[str, DirtyFingerprint] = {}
    expected_fields = {"status", "index_entries", "kind", "mode", "digest"}
    for relative, value in dirty.items():
        if set(value) != expected_fields:
            raise ValueError("Gate 3 snapshot fingerprint is malformed")
        raw_index_entries = value.get("index_entries")
        if not isinstance(raw_index_entries, list) or not all(
            isinstance(entry, str) for entry in raw_index_entries
        ):
            raise ValueError("Gate 3 snapshot index entries are malformed")
        fingerprint = DirtyFingerprint(
            status=value.get("status"),
            index_entries=tuple(raw_index_entries),
            kind=value.get("kind"),
            mode=value.get("mode"),
            digest=value.get("digest"),
        )
        if (
            not isinstance(fingerprint.status, str)
            or len(fingerprint.status) != 2
            or not isinstance(fingerprint.index_entries, tuple)
            or not isinstance(fingerprint.kind, str)
            or fingerprint.mode is not None
            and not isinstance(fingerprint.mode, int)
            or fingerprint.digest is not None
            and not isinstance(fingerprint.digest, str)
        ):
            raise ValueError("Gate 3 snapshot fingerprint types are malformed")
        fingerprints[relative] = fingerprint
    return Gate3Snapshot(
        head=head,
        evidence=Gate3Evidence(
            dirty=fingerprints,
            filesystem=_load_filesystem_map(raw.get("filesystem")),
        ),
    )


def cmd_snapshot() -> int:
    vault = _vault()
    snapshot_path = _snapshot_path(vault)
    snapshot = _snapshot_payload(vault)
    snapshot_path.write_text(
        json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        f"snapshot: HEAD={snapshot['head'][:8]} "
        f"dirty={len(snapshot['dirty'])} -> {snapshot_path}"
    )
    return 0


def cmd_check() -> int:
    vault = _vault()
    snapshot_path = _snapshot_path(vault)
    snapshot = _load_snapshot(snapshot_path)
    head = snapshot.head
    before = snapshot.evidence.dirty
    audit_head = _head_oid(vault)
    rules = AuditRules.load(vault)
    _validate_receipt_stores(vault, rules)
    records = collect_commit_records(vault, head, audit_head)
    after = collect_dirty_fingerprints(vault)
    commit_audit = _audit_commit_history(records, vault)
    dirty_audit = audit_dirty(before, after, rules, vault, records=records)
    result = Audit(
        sanctioned_commits=commit_audit.sanctioned_commits,
        violating_commits=commit_audit.violating_commits,
        sanctioned_writes=dirty_audit.sanctioned_writes,
        violating_writes=dirty_audit.violating_writes,
    )
    if _head_oid(vault) != audit_head:
        raise ValueError("Gate 3 HEAD changed during the audit")

    print(
        f"GATE 3 — {len(records)} new commit(s), "
        f"{len(after)} current dirty path(s)"
    )
    print(f"  sanctioned commits: {len(result.sanctioned_commits)}")
    print(f"  sanctioned dirty writes: {len(result.sanctioned_writes)}")
    for message in result.violating_commits:
        print(f"  VIOLATION commit: {message}")
    for relative in result.violating_writes:
        print(f"  VIOLATION direct write: {relative}")
    print("GATE 3:", "PASS" if result.ok else "FAIL")
    return 0 if result.ok else 1


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    command = argv[0] if argv else ""
    try:
        if command == "snapshot":
            return cmd_snapshot()
        if command == "check":
            return cmd_check()
        print(__doc__)
        return 2
    except (
        # An unreadable entity manifest is a `RuntimeError`, so naming it is
        # the only way this boundary reports it. The store sweep reaches
        # `EntityCatalog.load` from `snapshot` as well as `check`.
        EntityManifestError,
        json.JSONDecodeError,
        KeyError,
        OSError,
        ReceiptError,
        subprocess.CalledProcessError,
        TypeError,
        ValueError,
        yaml.YAMLError,
    ) as exc:
        print(f"GATE 3 ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
