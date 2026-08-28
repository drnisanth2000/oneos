"""cutover_manifest.py — what the owner approved, and the record that binds it.

Two artifacts, deliberately separate. The manifest states what will happen.
The approval record holds the manifest's SHA-256 plus the approval. The
manifest never carries its own digest.

Every database target carries an `axis`. Without it an implementation has
nothing to filter on and applies every product *and* member mapping to every
approved column; where one literal is short on both axes, an approved product
column silently receives a member identifier, invisibly, because the file is
binary.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import PurePosixPath
import re
import unicodedata

from .identifiers import AXES, DATABASE_AXES

_ENTITY_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

_DISPOSITION_KINDS = frozenset({"incidental", "structural"})
_SHA256 = re.compile(r"[0-9a-f]{64}")
_GIT_COMMIT = re.compile(r"[0-9a-f]{40}")


class ManifestError(Exception):
    pass


def _require_text(value, label: str) -> str:
    if type(value) is not str or not value:  # bool/subclasses are not text
        raise ManifestError(f"{label} must be a non-empty string")
    return value


def _require_relative_posix_path(value, label: str) -> str:
    value = _require_text(value, label)
    if (
        "\\" in value
        or "\x00" in value
        or unicodedata.normalize("NFC", value) != value
    ):
        raise ManifestError(f"{label} is not a canonical relative POSIX path")
    parts = value.split("/")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in parts):
        raise ManifestError(f"{label} is not a canonical relative POSIX path")
    if path.as_posix() != value:
        raise ManifestError(f"{label} is not a canonical relative POSIX path")
    return value


def _without_duplicate_keys(pairs):
    document = {}
    for key, value in pairs:
        if key in document:
            raise ManifestError(f"duplicate manifest key {key!r}")
        document[key] = value
    return document


def _require_keys(document: dict, expected: set[str], label: str) -> None:
    if set(document) != expected:
        raise ManifestError(f"{label} must contain exactly {sorted(expected)!r}")


@dataclass(frozen=True, order=True)
class Mapping:
    axis: str
    old: str
    new: str

    def __post_init__(self) -> None:
        _require_text(self.axis, "mapping axis")
        _require_text(self.old, "mapping old")
        _require_text(self.new, "mapping new")
        if self.axis not in AXES:
            raise ManifestError(f"unknown axis {self.axis!r}")


@dataclass(frozen=True, order=True)
class DatabaseTarget:
    """One approved `(source-relative path, table, column, axis)` target.

    All four parts are mandatory. The path is required because the vault holds
    one database per entity root and their schemas are not proven identical.
    The axis is required because a column stores identifiers of exactly one
    axis, and a target must never receive another axis's mapping.
    """

    path: str
    table: str
    column: str
    axis: str

    def __post_init__(self) -> None:
        for field_name in ("path", "table", "column", "axis"):
            _require_text(getattr(self, field_name), f"database target {field_name}")
        _require_relative_posix_path(self.path, "database target path")
        # `books.db` sits at an entity root and serves all that entity's
        # modules. Any other shape names a database no registry describes.
        parts = PurePosixPath(self.path).parts
        if (
            len(parts) != 2
            or parts[1] != "books.db"
            or _ENTITY_SLUG.fullmatch(parts[0]) is None
        ):
            raise ManifestError(
                "database target path must be <entity>/books.db"
            )
        if self.axis not in DATABASE_AXES:
            raise ManifestError(
                f"database target axis must be product or member, not {self.axis!r}"
            )


@dataclass(frozen=True, order=True)
class Disposition:
    path: str
    axis: str
    old: str
    ordinal: int
    context_sha256: str
    line: int
    kind: str
    typed_location: str = ""

    def __post_init__(self) -> None:
        _require_relative_posix_path(self.path, "disposition path")
        if (
            type(self.axis) is not str
            or self.axis not in AXES
            or type(self.old) is not str
            or not self.old
            or type(self.ordinal) is not int
            or self.ordinal < 1
            or type(self.context_sha256) is not str
            or _SHA256.fullmatch(self.context_sha256) is None
            or type(self.line) is not int
            or self.line < 1
        ):
            raise ManifestError("disposition requires a complete stable identity")
        if type(self.kind) is not str or type(self.typed_location) is not str:
            raise ManifestError("disposition fields must use their canonical types")
        if self.kind not in _DISPOSITION_KINDS:
            raise ManifestError(f"unknown disposition kind {self.kind!r}")
        if self.kind == "structural" and not self.typed_location:
            raise ManifestError(
                "a structural disposition must name the typed location that "
                "will rewrite it; there is no hand-fix option"
            )


@dataclass(frozen=True)
class ApprovalManifest:
    source_head: str
    mappings: tuple[Mapping, ...]
    databases: tuple[DatabaseTarget, ...]
    dispositions: tuple[Disposition, ...]

    def __post_init__(self) -> None:
        if type(self.source_head) is not str or _GIT_COMMIT.fullmatch(self.source_head) is None:
            raise ManifestError("source_head must be a full lowercase Git commit")
        if not all(type(items) is tuple for items in (self.mappings, self.databases, self.dispositions)):
            raise ManifestError("manifest collections must be tuples")
        # Canonical in memory as well as on disk. `canonical_bytes` sorts all
        # three collections, so an unsorted in-memory tuple would make
        # `load_manifest(canonical_bytes(m)) == m` false purely on ordering.
        object.__setattr__(self, "mappings", tuple(sorted(self.mappings)))
        object.__setattr__(self, "databases", tuple(sorted(self.databases)))
        object.__setattr__(self, "dispositions", tuple(sorted(self.dispositions)))
        # One column belongs to one axis. Two targets naming the same
        # `(path, table, column)` under different axes would let whichever
        # sorts first win, so a product column could hold a member value —
        # invisibly, because the residual query then finds nothing to report.
        columns: dict[tuple[str, str, str], str] = {}
        for target in self.databases:
            key = (target.path, target.table, target.column)
            if key in columns:
                raise ManifestError(
                    "a database column is claimed by more than one target"
                )
            columns[key] = target.axis


@dataclass(frozen=True)
class ApprovalRecord:
    manifest_sha256: str
    executor_commit: str
    approved_by: str

    def __post_init__(self) -> None:
        if type(self.manifest_sha256) is not str or _SHA256.fullmatch(self.manifest_sha256) is None:
            raise ManifestError("approval record requires a SHA-256 digest")
        if type(self.executor_commit) is not str or _GIT_COMMIT.fullmatch(self.executor_commit) is None:
            raise ManifestError("approval record requires an executor commit")
        _require_text(self.approved_by, "approved_by")


def canonical_bytes(manifest: ApprovalManifest) -> bytes:
    """One compact UTF-8 JSON document followed by exactly one LF."""
    document = {
        "source_head": manifest.source_head,
        "mappings": [
            {"axis": item.axis, "old": item.old, "new": item.new}
            for item in sorted(manifest.mappings)
        ],
        "databases": [
            {
                "path": item.path,
                "table": item.table,
                "column": item.column,
                "axis": item.axis,
            }
            for item in sorted(manifest.databases)
        ],
        "dispositions": [
            {
                "path": item.path,
                "axis": item.axis,
                "old": item.old,
                "ordinal": item.ordinal,
                "context_sha256": item.context_sha256,
                "line": item.line,
                "kind": item.kind,
                "typed_location": item.typed_location,
            }
            for item in sorted(manifest.dispositions)
        ],
    }
    return (
        json.dumps(
            document,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=False,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def manifest_digest(manifest: ApprovalManifest) -> str:
    return hashlib.sha256(canonical_bytes(manifest)).hexdigest()


def load_manifest(raw: bytes) -> ApprovalManifest:
    try:
        document = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_without_duplicate_keys
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestError("approval manifest is unreadable") from exc
    if not isinstance(document, dict):
        raise ManifestError("approval manifest must be a mapping")
    try:
        _require_keys(
            document,
            {"source_head", "mappings", "databases", "dispositions"},
            "approval manifest",
        )
        for item in document["mappings"]:
            _require_keys(item, {"axis", "old", "new"}, "mapping")
        for item in document["databases"]:
            _require_keys(item, {"path", "table", "column", "axis"}, "database target")
        for item in document["dispositions"]:
            _require_keys(
                item,
                {"path", "axis", "old", "ordinal", "context_sha256", "line", "kind", "typed_location"},
                "disposition",
            )
        return ApprovalManifest(
            source_head=document["source_head"],
            mappings=tuple(Mapping(**item) for item in document.get("mappings", [])),
            databases=tuple(
                DatabaseTarget(**item) for item in document.get("databases", [])
            ),
            dispositions=tuple(
                Disposition(**item) for item in document.get("dispositions", [])
            ),
        )
    except (KeyError, TypeError) as exc:
        raise ManifestError("approval manifest is malformed") from exc


def verify_manifest(raw: bytes, record: ApprovalRecord) -> None:
    """Unconditional byte comparison: hash what is there, compare, refuse."""
    if hashlib.sha256(raw).hexdigest() != record.manifest_sha256:
        raise ManifestError("approval manifest does not match its approval record")
    manifest = load_manifest(raw)
    if raw != canonical_bytes(manifest):
        raise ManifestError("approval manifest is not in canonical form")
