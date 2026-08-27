"""identifiers.py — the single source of the registry-identifier length rule.

The grammar itself is still restated in several modules; only the *length*
rule lives here, because five independent length checks would reproduce the
sidebar/validator disagreement AGENTS.md warns about.

Five is one character above the publication audit's long-term threshold of
four, so every registry identifier is matched by the audit's strongest rule
with one character to spare.
"""
from __future__ import annotations

#: Minimum identifier length, counting hyphens.
IDENTIFIER_MINIMUM_LENGTH = 5

#: The four registry axes this cutover governs. `project` is a pipeline
#: directory name, not a registry identifier, and is deliberately absent.
AXES = ("entity", "product", "member", "workspace")

#: The only axes whose values are stored in a database column. An `entity` or
#: `workspace` database target is a hard stop.
DATABASE_AXES = frozenset({"product", "member"})

_SUFFIXES = {axis: f"-{axis}" for axis in AXES}


class AxisError(ValueError):
    """An unknown axis, an identifier that must not be mapped, or a mapping
    whose new value is not the deterministic result."""


def meets_floor(value: str) -> bool:
    return len(value) >= IDENTIFIER_MINIMUM_LENGTH


def suffix_for_axis(axis: str) -> str:
    try:
        return _SUFFIXES[axis]
    except KeyError as exc:
        raise AxisError(f"unknown axis {axis!r}") from exc


def map_identifier(axis: str, old: str) -> str:
    """The new identifier for a sub-floor `old` on `axis`.

    Total and deterministic in `(axis, old)`: no lookup, no counter, no
    tie-break. That is what lets a dry-run diff be trusted as a preview.
    """
    suffix = suffix_for_axis(axis)
    if meets_floor(old):
        raise AxisError("identifier already meets the floor and is not rewritten")
    if any(old.endswith(candidate) for candidate in _SUFFIXES.values()):
        # Unreachable by arithmetic — every suffix is >= 7 characters, so an
        # already-suffixed value is >= 8 and cannot be sub-floor. Asserted
        # anyway so a future edit to the floor cannot silently double-suffix.
        raise AxisError("identifier already carries an axis suffix")
    return f"{old}{suffix}"


def validate_mapping_pair(axis: str, old: str, new: str) -> None:
    """Refuse any approved mapping whose new value is not the deterministic
    result. An owner approves a table; this proves the table was produced by
    the rule rather than typed by hand."""
    expected = map_identifier(axis, old)
    if new != expected:
        raise AxisError(
            f"mapping for axis {axis!r} does not match the deterministic rule"
        )
