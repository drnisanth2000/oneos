"""cutover_locations.py — the closed list of typed rewrite locations.

A short identifier may also be an ordinary English word, so nothing is
rewritten because it merely looks like the identifier. Only a location on this
table is ever modified, and the table must partition: no `(file_kind, field)`
pair may appear under two axes, or two mappings would contend for one field.
"""
from __future__ import annotations

from dataclasses import dataclass

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
