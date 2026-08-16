#!/usr/bin/env python3
"""Gate 3: audit sanctioned Git transactions and exact dirty session state.

    ONEOS_VAULT=/path/to/vault python -m tools.gate3_audit snapshot
    # ... exercise a complete session ...
    ONEOS_VAULT=/path/to/vault python -m tools.gate3_audit check

The snapshot is external to the vault.  A check accepts only the commit
envelopes produced by the existing ingest, outbox, registry, and rename flows,
plus genuinely new canonical pending proposal YAML.  Every initially dirty
path must retain its exact index/worktree fingerprint for the whole session.
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

import yaml

from app.entities import EntityCatalog
from app.outbox import OutboxError, _require_destination, _to_proposal
from app.proposal_identity import ProposalIdentityError, require_proposal_identity
from app.registry import RegistryError, get_delete_proposal
from app.rename import AXES, RenameError, plan_rename
from app.scope import CrossScopeError, Scope
from app.vault import DestinationRegistryError, Vault


SNAPSHOT_VERSION = 2
_OID = re.compile(r"^[0-9a-f]{40,64}$")
_REGISTRY_MESSAGE = re.compile(
    r"^registry: (add|edit|delete) (workspace|product|member)(?: .*)?$"
)
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
    index_entry: str | None
    kind: str
    mode: int | None
    digest: str | None


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

    @classmethod
    def load(cls, vault: Path) -> "AuditRules":
        catalog = EntityCatalog.load(vault)
        runtime = Vault(catalog)
        active = {
            entity.slug: runtime.active_modules_for(Scope(catalog.root, entity.slug))
            for entity in catalog.entities
        }
        return cls(root=catalog.root, active_modules=active)

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


def collect_commit_records(vault: Path, snapshot_head: str) -> tuple[CommitRecord, ...]:
    """Collect every post-snapshot commit in chronological order."""
    vault = Path(vault).resolve()
    if _OID.fullmatch(snapshot_head) is None:
        raise ValueError("Gate 3 snapshot HEAD is malformed")
    oids = tuple(
        line
        for line in _git_text(
            vault, "rev-list", "--reverse", f"{snapshot_head}..HEAD"
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


def _parse_index_entries(output: bytes) -> dict[str, str]:
    entries: dict[str, str] = {}
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
        if stage_text == "0":
            entries[_decode_path(path_bytes)] = (
                f"{mode_text}:{oid_text}:{stage_text}"
            )
    return entries


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


def _fingerprint_path(
    vault: Path,
    relative: str,
    status_value: str,
    index_entry: str | None,
) -> DirtyFingerprint:
    path = vault / relative
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return DirtyFingerprint(status_value, index_entry, "absence", None, None)
    mode = stat.S_IMODE(metadata.st_mode)
    if stat.S_ISREG(metadata.st_mode):
        contents, opened_mode = _read_regular_no_follow(path)
        return DirtyFingerprint(
            status_value,
            index_entry,
            "file",
            opened_mode,
            hashlib.sha256(contents).hexdigest(),
        )
    if stat.S_ISLNK(metadata.st_mode):
        target = os.fsencode(os.readlink(path))
        return DirtyFingerprint(
            status_value,
            index_entry,
            "symlink",
            mode,
            hashlib.sha256(target).hexdigest(),
        )
    kind = "directory" if stat.S_ISDIR(metadata.st_mode) else "other"
    return DirtyFingerprint(status_value, index_entry, kind, mode, None)


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
    return {
        relative: _fingerprint_path(
            vault, relative, statuses[relative], index_entries.get(relative)
        )
        for relative in sorted(statuses)
    }


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
        or module not in rules.active_modules[entity]
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


def _sanctioned_outbox(record: CommitRecord, rules: AuditRules) -> bool:
    if len(record.parents) != 1 or len(record.changes) != 2:
        return False
    deleted = [change for change in record.changes if change.status == "D"]
    added = [change for change in record.changes if change.status == "A"]
    if len(deleted) != 1 or len(added) != 1:
        return False
    source = _inbox_path(deleted[0].path, rules)
    destination = _active_destination(added[0].path, rules)
    return (
        source is not None
        and destination is not None
        and source == destination
    )


def _sanctioned_registry(record: CommitRecord) -> bool:
    match = _REGISTRY_MESSAGE.fullmatch(record.message)
    if match is None or len(record.parents) != 1 or len(record.changes) != 1:
        return False
    change = record.changes[0]
    kind = match.group(2)
    return change.status in {"A", "M"} and change.path == _REGISTRY_PATH[kind]


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
) -> frozenset[tuple[str, str]]:
    plan = plan_rename(tree, axis, old, new)
    moves = tuple(
        (source.relative_to(tree), destination.relative_to(tree))
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
        relative = edited.relative_to(tree)
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
                expected = _rename_envelope(tree, tracked, axis, old, new)
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
        return _sanctioned_outbox(record, rules)
    if record.message.startswith("registry:"):
        return _sanctioned_registry(record)
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


def _canonical_delete_record(record: dict) -> bool:
    kind = record.get("kind")
    slug = record.get("slug")
    created = record.get("created")
    total = record.get("total_references")
    impact = record.get("impact")
    if (
        kind not in _DELETE_KINDS
        or not isinstance(slug, str)
        or _CANONICAL_SLUG.fullmatch(slug) is None
        or not isinstance(created, str)
        or not created
        or not isinstance(total, int)
        or isinstance(total, bool)
        or total < 0
        or not isinstance(impact, dict)
    ):
        return False
    try:
        datetime.fromisoformat(created)
    except ValueError:
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
            proposal = _to_proposal(path, loaded)
            return _require_destination(scope, proposal).status == "pending"
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


def audit_dirty(
    before: dict[str, DirtyFingerprint],
    after: dict[str, DirtyFingerprint],
    rules: AuditRules,
    vault: Path,
) -> Audit:
    result = Audit()
    for relative in sorted(set(before) | set(after)):
        if relative in before:
            if after.get(relative) != before[relative]:
                result.violating_writes.append(relative)
            continue
        fingerprint = after[relative]
        destination = (
            result.sanctioned_writes
            if _new_proposal_is_sanctioned(
                relative, fingerprint, rules, Path(vault).resolve()
            )
            else result.violating_writes
        )
        destination.append(relative)
    return result


def _vault() -> Path:
    return Path(os.environ["ONEOS_VAULT"]).expanduser().resolve()


def _snapshot_path(vault: Path) -> Path:
    path = Path(
        os.environ.get("GATE3_SNAP", "./.gate3-snapshot.json")
    ).expanduser().resolve()
    if path == vault or path.is_relative_to(vault):
        raise ValueError("Gate 3 snapshot must stay outside the vault")
    return path


def _snapshot_payload(vault: Path) -> dict:
    dirty = collect_dirty_fingerprints(vault)
    return {
        "version": SNAPSHOT_VERSION,
        "head": _git_text(vault, "rev-parse", "HEAD").strip(),
        "dirty": {path: asdict(fingerprint) for path, fingerprint in dirty.items()},
    }


def _load_snapshot(path: Path) -> tuple[str, dict[str, DirtyFingerprint]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("version") != SNAPSHOT_VERSION:
        raise ValueError("Gate 3 snapshot version is unsupported")
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
    expected_fields = {"status", "index_entry", "kind", "mode", "digest"}
    for relative, value in dirty.items():
        if set(value) != expected_fields:
            raise ValueError("Gate 3 snapshot fingerprint is malformed")
        fingerprint = DirtyFingerprint(**value)
        if (
            not isinstance(fingerprint.status, str)
            or len(fingerprint.status) != 2
            or fingerprint.index_entry is not None
            and not isinstance(fingerprint.index_entry, str)
            or not isinstance(fingerprint.kind, str)
            or fingerprint.mode is not None
            and not isinstance(fingerprint.mode, int)
            or fingerprint.digest is not None
            and not isinstance(fingerprint.digest, str)
        ):
            raise ValueError("Gate 3 snapshot fingerprint types are malformed")
        fingerprints[relative] = fingerprint
    return head, fingerprints


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
    head, before = _load_snapshot(snapshot_path)
    rules = AuditRules.load(vault)
    records = collect_commit_records(vault, head)
    after = collect_dirty_fingerprints(vault)
    commit_audit = audit_commits(records, rules, vault)
    dirty_audit = audit_dirty(before, after, rules, vault)
    result = Audit(
        sanctioned_commits=commit_audit.sanctioned_commits,
        violating_commits=commit_audit.violating_commits,
        sanctioned_writes=dirty_audit.sanctioned_writes,
        violating_writes=dirty_audit.violating_writes,
    )

    print(
        f"GATE 3 — {len(records)} new commit(s), "
        f"{len(after)} current dirty path(s)"
    )
    print(f"  sanctioned commits: {len(result.sanctioned_commits)}")
    print(f"  pending proposal writes: {len(result.sanctioned_writes)}")
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
        json.JSONDecodeError,
        KeyError,
        OSError,
        subprocess.CalledProcessError,
        TypeError,
        ValueError,
        yaml.YAMLError,
    ) as exc:
        print(f"GATE 3 ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
