"""cutover_locations.py — the closed list of typed rewrite locations.

A short identifier may also be an ordinary English word, so nothing is
rewritten because it merely looks like the identifier. Only a location on this
table is ever modified, and the table must partition: no `(file_kind, field)`
pair may appear under two axes, or two mappings would contend for one field.
"""
from __future__ import annotations

from dataclasses import dataclass

from .console_routing import structured_reader
from .identifiers import AXES


class LocationError(ValueError):
    pass


@dataclass(frozen=True)
class Location:
    axis: str
    file_kind: str
    field: str
    #: "value" matches an exact whole field value; "key" a mapping key;
    #: "path-head" the first component of a path; "dirname" a directory name.
    match: str

    @property
    def key(self) -> str:
        return f"{self.axis}:{self.file_kind}:{self.field}"


REWRITE_LOCATIONS: tuple[Location, ...] = (
    # --- entity -----------------------------------------------------------
    Location("entity", "entities", "key", "key"),
    Location("entity", "vault-root", "dirname", "dirname"),
    Location("entity", "products", "entity-group", "key"),
    Location("entity", "members", "entity-group", "key"),
    Location("entity", "workspaces", "entity", "value"),
    Location("entity", "workspaces", "primary_entity", "value"),
    Location("entity", "front-matter", "entity", "value"),
    Location("entity", "proposal", "entity", "value"),
    Location("entity", "proposal", "src", "path-head"),
    Location("entity", "proposal", "dst", "path-head"),
    Location("entity", "action-policy", "paths", "path-head"),
    Location("entity", "action-policy", "except", "path-head"),
    # --- product ----------------------------------------------------------
    Location("product", "products", "key", "key"),
    Location("product", "front-matter", "product", "value"),
    Location("product", "workspaces", "product", "value"),
    Location("product", "books-db", "approved-target", "value"),
    # --- member -----------------------------------------------------------
    Location("member", "members", "id", "value"),
    Location("member", "front-matter", "member", "value"),
    Location("member", "workspaces", "member", "value"),
    Location("member", "books-db", "approved-target-member", "value"),
    # --- workspace --------------------------------------------------------
    Location("workspace", "workspaces", "id", "value"),
)


def locations_for_axis(axis: str) -> tuple[Location, ...]:
    if axis not in AXES:
        raise LocationError(f"unknown axis {axis!r}")
    return tuple(item for item in REWRITE_LOCATIONS if item.axis == axis)


def location_keys() -> frozenset[str]:
    """Every valid `axis:file_kind:field` key. A structural disposition must
    name one of these; anything else is an unbuildable promise."""
    return frozenset(item.key for item in REWRITE_LOCATIONS)


def _assert_partition() -> None:
    seen: dict[tuple[str, str], str] = {}
    for location in REWRITE_LOCATIONS:
        key = (location.file_kind, location.field)
        if key in seen and seen[key] != location.axis:
            raise LocationError(
                f"{key} is claimed by both {seen[key]!r} and {location.axis!r}"
            )
        seen[key] = location.axis


_assert_partition()


import re

#: A whole token: not preceded or followed by a word character or a hyphen. A
#: migrated `ab-entity` therefore does not match a scan for `ab`, because the
#: lookahead fails on the hyphen, while a bare `ab` still does.
def boundaried(term: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![\w-]){re.escape(term)}(?![\w-])")


def _split_front_matter(text: str) -> tuple[str, str, str] | None:
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    return text[:3], text[3:end], text[end:]


def rewrite_front_matter_field(text: str, field: str, old: str, new: str) -> str:
    """Replace `field: old` inside the leading front matter only, and only
    when `old` is the entire value."""
    parts = _split_front_matter(text)
    if parts is None:
        return text
    head, block, tail = parts
    rewritten = re.sub(
        rf"(?m)^(\s*{re.escape(field)}:\s*){re.escape(old)}[ \t]*$",
        rf"\g<1>{new}",
        block,
    )
    return head + rewritten + tail


def rewrite_path_head(path: str, old: str, new: str) -> str:
    """Replace the first path component when it is exactly `old`."""
    head, separator, rest = path.partition("/")
    if head != old:
        return path
    return f"{new}{separator}{rest}"


def rewrite_yaml_value_field(text: str, field: str, old: str, new: str) -> str:
    """Replace an exact plain YAML scalar in a block or flow mapping.

    The left boundary is a YAML mapping boundary (line start, `{`, or `,`),
    not an arbitrary occurrence of ``field:`` inside another scalar. The right
    boundary is a complete plain-scalar delimiter. In particular, ``id: ab
    note`` and ``note: "id: ab is prose"`` are not matches.

    Registry identifiers are constrained to lowercase letters, digits and
    hyphens, so the canonical registries write them as plain scalars. A quoted
    or otherwise unsupported representation is left untouched here and then
    refused by Task 11's scoped residual gate rather than guessed at.
    """
    pattern = re.compile(
        rf"(?m)(^|[{{,])"
        rf"([ \t]*(?:-[ \t]*)?{re.escape(field)}:[ \t]*)"
        rf"{re.escape(old)}"
        rf"(?=[ \t]*(?:[,}}#]|$))"
    )
    return pattern.sub(rf"\g<1>\g<2>{new}", text)


def rewrite_yaml_path_head_field(
    text: str, field: str, old: str, new: str
) -> str:
    """Rewrite only the first component of one plain YAML path scalar.

    This is deliberately textual and boundary-scoped. Parsing and dumping the
    complete proposal would rewrite quoting, key order, and other fields that
    the approved location table does not own.
    """
    pattern = re.compile(
        rf"(?m)(^|[{{,])"
        rf"([ \t]*(?:-[ \t]*)?{re.escape(field)}:[ \t]*)"
        rf"{re.escape(old)}/"
    )
    return pattern.sub(rf"\g<1>\g<2>{new}/", text)


def rewrite_mapping_key(text: str, old: str, new: str, indent: int) -> str:
    """Rename a mapping key sitting at exactly `indent` spaces."""
    return re.sub(
        rf"(?m)^(\s{{{indent}}}){re.escape(old)}:",
        rf"\g<1>{new}:",
        text,
    )


_POLICY_LIST = re.compile(
    r"(?m)(^|[,{])([ \t-]*)(paths|except):\s*\[([^\]]*)\]"
)
_QUOTED = re.compile(r"([\"'])([^\"']*)\1")


def rewrite_policy_path_heads(text: str, old: str, new: str) -> str:
    """Rewrite path heads inside `paths:` and `except:` list bodies only.

    Rewriting every quoted string in the file would edit descriptions and
    unrelated values — the blind substitution this design exists to avoid. An
    allow rule's `paths:` and its `except:` for `.sensitive/` are both matched
    here, so they move together; rewriting one without the other is the
    BUILD §4 fail-open.
    """
    def rewrite_body(match: re.Match[str]) -> str:
        boundary, spacing, key, body = match.groups()
        rewritten = _QUOTED.sub(
            lambda item: (
                f"{item.group(1)}"
                f"{rewrite_path_head(item.group(2), old, new)}"
                f"{item.group(1)}"
            ),
            body,
        )
        return f"{boundary}{spacing}{key}: [{rewritten}]"

    return _POLICY_LIST.sub(rewrite_body, text)


from pathlib import Path
import hashlib
import os

from .cutover_manifest import Mapping

SKIP_DIRS = frozenset({".git", ".obsidian", ".trash"})
BINARY_SUFFIXES = frozenset({
    ".pdf", ".docx", ".xlsx", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico",
    ".db", ".sqlite", ".sqlite3", ".zip", ".gz", ".tar", ".woff", ".woff2",
})

#: The only two files where a `former_slugs:` line is legitimate. The rejected
#: plan exempted every line containing the substring, in any file — a blanket
#: exemption that would mask a genuine residual anywhere in the vault.
FORMER_SLUGS_FILES = frozenset({
    "_system/entities.yaml",
    "_system/products.yaml",
})


class UnreadableFile(Exception):
    """A text file the advisory scan could not read.

    Never skipped: an unreadable file could hold a residual, and skipping it
    would let the gate pass on evidence it never saw.
    """


@dataclass(frozen=True, order=True)
class AdvisoryOccurrence:
    path: str
    axis: str
    old: str
    ordinal: int
    context_sha256: str
    line: int


def _yaml_scalar_span(
    line: str, field: str, old: str
) -> tuple[int, int] | None:
    pattern = re.compile(
        rf"(^|[{{,])"
        rf"([ \t]*(?:-[ \t]*)?{re.escape(field)}:[ \t]*)"
        rf"(?P<value>{re.escape(old)})"
        rf"(?=[ \t]*(?:[,}}#]|$))"
    )
    match = pattern.search(line)
    return None if match is None else match.span("value")


def _mapping_key_span(
    line: str, old: str, indent: int
) -> tuple[int, int] | None:
    match = re.match(
        rf"^\s{{{indent}}}(?P<value>{re.escape(old)}):\s*(?:#.*)?$",
        line,
    )
    return None if match is None else match.span("value")


def _typed_token_spans(
    root: Path, mappings: tuple[Mapping, ...]
) -> set[tuple[str, int, str, str, int, int]]:
    """Exact token spans owned by the closed rewrite-location table.

    Advisory reporting is the complement of these spans, not of whole lines.
    A typed scalar and same-axis prose may share a line; only the scalar span
    is excluded.
    """
    by_axis = {
        axis: {item.old for item in mappings if item.axis == axis}
        for axis in AXES
    }
    typed: set[tuple[str, int, str, str, int, int]] = set()

    def record(
        relative: str,
        number: int,
        axis: str,
        old: str,
        span: tuple[int, int] | None,
    ) -> None:
        if span is not None:
            typed.add((relative, number, axis, old, *span))

    for candidate in sorted(root.rglob("*")):
        relative = candidate.relative_to(root).as_posix()
        if any(part in SKIP_DIRS for part in Path(relative).parts):
            continue
        if candidate.is_symlink() or not candidate.is_file():
            continue
        if candidate.suffix.lower() in BINARY_SUFFIXES:
            continue
        try:
            lines = candidate.read_text(encoding="utf-8").splitlines()
        except (UnicodeDecodeError, OSError) as exc:
            raise UnreadableFile(f"{relative} could not be read") from exc

        in_front_matter = False
        for number, line in enumerate(lines, start=1):
            if candidate.suffix.lower() == ".md":
                if number == 1 and line.strip() == "---":
                    in_front_matter = True
                    continue
                if in_front_matter and line.strip() == "---":
                    in_front_matter = False
                    continue
                if in_front_matter:
                    for axis, field in (
                        ("entity", "entity"),
                        ("product", "product"),
                        ("member", "member"),
                    ):
                        for old in by_axis[axis]:
                            record(
                                relative,
                                number,
                                axis,
                                old,
                                _yaml_scalar_span(line, field, old),
                            )

            if relative == "_system/entities.yaml":
                for old in by_axis["entity"]:
                    record(
                        relative,
                        number,
                        "entity",
                        old,
                        _mapping_key_span(line, old, 2),
                    )
            elif relative == "_system/products.yaml":
                for axis, indent in (("entity", 2), ("product", 4)):
                    for old in by_axis[axis]:
                        record(
                            relative,
                            number,
                            axis,
                            old,
                            _mapping_key_span(line, old, indent),
                        )
            elif relative == "_system/members.yaml":
                for old in by_axis["entity"]:
                    record(
                        relative,
                        number,
                        "entity",
                        old,
                        _mapping_key_span(line, old, 2),
                    )
                for old in by_axis["member"]:
                    record(
                        relative,
                        number,
                        "member",
                        old,
                        _yaml_scalar_span(line, "id", old),
                    )
            elif relative == "_system/workspaces.yaml":
                for axis, fields in (
                    ("workspace", ("id",)),
                    ("entity", ("entity", "primary_entity")),
                    ("product", ("product",)),
                    ("member", ("member",)),
                ):
                    for field in fields:
                        for old in by_axis[axis]:
                            record(
                                relative,
                                number,
                                axis,
                                old,
                                _yaml_scalar_span(line, field, old),
                            )
            elif relative == "_system/scripts/action-policy.yaml":
                for match in _POLICY_LIST.finditer(line):
                    for quoted in _QUOTED.finditer(match.group(4)):
                        head = quoted.group(2).partition("/")[0]
                        if head in by_axis["entity"]:
                            start = match.start(4) + quoted.start(2)
                            record(
                                relative,
                                number,
                                "entity",
                                head,
                                (start, start + len(head)),
                            )
            elif candidate.suffix.lower() == ".yaml" and "outbox" in Path(relative).parts:
                for old in by_axis["entity"]:
                    record(
                        relative,
                        number,
                        "entity",
                        old,
                        _yaml_scalar_span(line, "entity", old),
                    )
                    for field in ("src", "dst"):
                        pattern = re.compile(
                            rf"(^|[{{,])([ \t]*{field}:[ \t]*)"
                            rf"(?P<value>{re.escape(old)})/"
                        )
                        match = pattern.search(line)
                        record(
                            relative,
                            number,
                            "entity",
                            old,
                            None if match is None else match.span("value"),
                        )
    return typed


def stable_advisory_context(line: str, mappings: tuple[Mapping, ...]) -> str:
    """Hash context after neutralizing only approved old/new mapping tokens.

    The neutral form is stable when a typed value on the same line is rewritten.
    Every non-mapping byte remains authoritative, and occurrence counts are
    checked separately, so rewriting or adding an incidental old token cannot
    hide behind this normalization.
    """
    normalized = line
    terms = sorted(
        {value for item in mappings for value in (item.old, item.new)},
        key=lambda value: (-len(value), value),
    )
    for term in terms:
        normalized = boundaried(term).sub("<mapped>", normalized)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def advisory_occurrences(
    root: Path, mappings: tuple[Mapping, ...]
) -> list[AdvisoryOccurrence]:
    """Whole-token occurrences of an old identifier, for owner disposition.

    Reported, never rewritten. A short identifier may be an ordinary word, so
    the owner decides which occurrences are structural references and which are
    incidental prose.
    """
    patterns = {
        (item.axis, item.old): boundaried(item.old) for item in mappings
    }
    typed = _typed_token_spans(root, mappings)
    found: list[AdvisoryOccurrence] = []
    for candidate in sorted(root.rglob("*")):
        relative = candidate.relative_to(root).as_posix()
        if any(part in SKIP_DIRS for part in Path(relative).parts):
            continue
        if candidate.is_symlink():
            try:
                text = os.readlink(candidate)
            except OSError as exc:
                raise UnreadableFile(f"{relative} link text could not be read") from exc
        elif candidate.is_file():
            if candidate.suffix.lower() in BINARY_SUFFIXES:
                continue
            try:
                text = candidate.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError) as exc:
                raise UnreadableFile(
                    f"{relative} could not be read; the advisory scan cannot pass "
                    f"on a file it never saw"
                ) from exc
        else:
            continue
        exempt_former_slugs = relative in FORMER_SLUGS_FILES
        ordinals: dict[tuple[str, str], int] = {}
        for number, line in enumerate(text.splitlines(), start=1):
            if exempt_former_slugs and re.match(
                r"^\s*former_slugs:\s*\[", line
            ):
                continue
            context_sha256 = stable_advisory_context(line, mappings)
            for (axis, old), pattern in patterns.items():
                for match in pattern.finditer(line):
                    token_key = (
                        relative,
                        number,
                        axis,
                        old,
                        match.start(),
                        match.end(),
                    )
                    if token_key in typed:
                        continue
                    key = (axis, old)
                    ordinals[key] = ordinals.get(key, 0) + 1
                    found.append(
                        AdvisoryOccurrence(
                            path=relative,
                            axis=axis,
                            old=old,
                            ordinal=ordinals[key],
                            context_sha256=context_sha256,
                            line=number,
                        )
                    )
    return sorted(found)


import yaml


@dataclass(frozen=True, order=True)
class ScopedResidual:
    location: str
    path: str
    old: str


@structured_reader(category="front-matter")
def _front_matter_values(text: str) -> dict[str, str]:
    parts = _split_front_matter(text)
    if parts is None:
        return {}
    try:
        loaded = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        return {}
    if not isinstance(loaded, dict):
        return {}
    return {k: v for k, v in loaded.items() if isinstance(v, str)}


@structured_reader(category="admin-record")
def _load_yaml_file(path: Path) -> object:
    if not path.is_file():
        return None
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, OSError, yaml.YAMLError) as exc:
        raise UnreadableFile(f"{path.name} could not be read for the residual gate") from exc


def scoped_residuals(
    root: Path, mappings: tuple[Mapping, ...]
) -> list[ScopedResidual]:
    """Any enumerated location still holding an old identifier.

    Scoped to the writer's own locations. Prose is never inspected, because a
    retired identifier may legitimately survive there as an ordinary word.
    """
    by_axis: dict[str, set[str]] = {}
    for mapping in mappings:
        by_axis.setdefault(mapping.axis, set()).add(mapping.old)
    entities = by_axis.get("entity", set())
    products = by_axis.get("product", set())
    members = by_axis.get("member", set())
    workspaces = by_axis.get("workspace", set())
    found: list[ScopedResidual] = []

    def report(location: str, path: str, old: str) -> None:
        found.append(ScopedResidual(location, path, old))

    system = root / "_system"

    # entity: bundle directory names
    for old in entities:
        if (root / old).is_dir():
            report("entity:vault-root:dirname", old, old)

    # entity / product: registry mapping keys
    entities_doc = _load_yaml_file(system / "entities.yaml")
    if isinstance(entities_doc, dict):
        for key in (entities_doc.get("entities") or {}):
            if key in entities:
                report("entity:entities:key", "_system/entities.yaml", key)
    products_doc = _load_yaml_file(system / "products.yaml")
    if isinstance(products_doc, dict):
        for group, values in (products_doc.get("products") or {}).items():
            if group in entities:
                report("entity:products:entity-group", "_system/products.yaml", group)
            if isinstance(values, dict):
                for key in values:
                    if key in products:
                        report("product:products:key", "_system/products.yaml", key)
    members_doc = _load_yaml_file(system / "members.yaml")
    if isinstance(members_doc, dict):
        for group, values in (members_doc.get("members") or {}).items():
            if group in entities:
                report("entity:members:entity-group", "_system/members.yaml", group)
            if isinstance(values, list):
                for entry in values:
                    if isinstance(entry, dict) and entry.get("id") in members:
                        report("member:members:id", "_system/members.yaml", entry["id"])

    # workspaces: four typed fields, each owned by exactly one axis
    workspaces_doc = _load_yaml_file(system / "workspaces.yaml")
    if isinstance(workspaces_doc, dict):
        for entry in workspaces_doc.get("workspaces") or []:
            if not isinstance(entry, dict):
                continue
            checks = (
                ("workspace:workspaces:id", "id", workspaces),
                ("entity:workspaces:entity", "entity", entities),
                ("entity:workspaces:primary_entity", "primary_entity", entities),
                ("product:workspaces:product", "product", products),
                ("member:workspaces:member", "member", members),
            )
            for location, field, olds in checks:
                if entry.get(field) in olds:
                    report(location, "_system/workspaces.yaml", entry[field])

    # action-policy: both halves of every rule
    policy = system / "scripts" / "action-policy.yaml"
    if policy.is_file():
        try:
            text = policy.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError) as exc:
            raise UnreadableFile("action-policy.yaml could not be read") from exc
        for match in _POLICY_LIST.finditer(text):
            key, body = match.group(3), match.group(4)
            for quoted in _QUOTED.finditer(body):
                head = quoted.group(2).partition("/")[0]
                if head in entities:
                    report(
                        f"entity:action-policy:{key}",
                        "_system/scripts/action-policy.yaml",
                        head,
                    )

    # front matter and proposals
    for candidate in sorted(root.rglob("*")):
        if not candidate.is_file() or candidate.is_symlink():
            continue
        relative = candidate.relative_to(root).as_posix()
        if any(part in SKIP_DIRS for part in Path(relative).parts):
            continue
        if candidate.suffix.lower() == ".md":
            try:
                text = candidate.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError) as exc:
                raise UnreadableFile(f"{relative} could not be read") from exc
            values = _front_matter_values(text)
            for location, field, olds in (
                ("entity:front-matter:entity", "entity", entities),
                ("product:front-matter:product", "product", products),
                ("member:front-matter:member", "member", members),
            ):
                if values.get(field) in olds:
                    report(location, relative, values[field])
        elif candidate.suffix.lower() == ".yaml" and "outbox" in Path(relative).parts:
            document = _load_yaml_file(candidate)
            if not isinstance(document, dict):
                continue
            if document.get("entity") in entities:
                report("entity:proposal:entity", relative, document["entity"])
            for field in ("src", "dst"):
                value = document.get(field)
                if isinstance(value, str) and value.partition("/")[0] in entities:
                    report(
                        f"entity:proposal:{field}", relative, value.partition("/")[0]
                    )

    return sorted(found)
