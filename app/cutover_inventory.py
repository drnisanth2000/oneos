"""cutover_inventory.py — read-only enumeration, collisions, and hard stops.

Nothing here writes. The inventory runs against the live vault, produces the
material the owner approves, and refuses conditions that must never reach a
build.
"""
from __future__ import annotations

from pathlib import Path
import subprocess

from .cutover_manifest import Mapping


class CollisionError(Exception):
    pass


class UnmigratableContentError(Exception):
    """An affected entity holds content a linked worktree cannot carry."""


def check_collisions(
    mappings: tuple[Mapping, ...], existing: dict[str, set[str]]
) -> None:
    """Refuse class 1 and class 2.

    Class 3 — one literal on two axes — is permitted: scoped replacement gives
    each axis its own typed locations, so an entity and a product sharing a
    literal migrate independently and correctly.
    """
    seen: dict[str, set[str]] = {}
    for mapping in mappings:
        axis_seen = seen.setdefault(mapping.axis, set())
        if mapping.old in axis_seen:
            raise CollisionError(f"duplicate mapping input on axis {mapping.axis!r}")
        axis_seen.add(mapping.old)

    produced: dict[str, set[str]] = {}
    for mapping in mappings:
        axis_produced = produced.setdefault(mapping.axis, set())
        if mapping.new in axis_produced:
            raise CollisionError(f"duplicate mapping output on axis {mapping.axis!r}")
        axis_produced.add(mapping.new)
        if mapping.new in existing.get(mapping.axis, set()):
            raise CollisionError(
                f"new value collides with an existing identifier on axis "
                f"{mapping.axis!r}"
            )


def untracked_or_ignored_paths(vault: Path, entity: str) -> list[str]:
    """Ignored or untracked paths beneath one entity directory.

    A linked worktree materialises tracked content only. If an affected entity
    holds anything else, promoting a renamed tree would strand it at the old
    path — outside the new entity and outside every scope check that assumes it
    lives beneath its entity root.
    """
    completed = subprocess.run(
        [
            "git", "status", "--porcelain", "--untracked-files=all",
            "--ignored", "--", entity,
        ],
        cwd=vault,
        check=True,
        capture_output=True,
        text=True,
    )
    found: list[str] = []
    for line in completed.stdout.splitlines():
        if not line:
            continue
        status, _, path = line.partition(" ")
        if status in {"??", "!!"}:
            found.append(path.strip())
    return sorted(found)


def require_clean_entities(vault: Path, entities: list[str]) -> None:
    for entity in sorted(entities):
        found = untracked_or_ignored_paths(vault, entity)
        if found:
            raise UnmigratableContentError(
                f"entity {entity!r} holds ignored or untracked content; relocate "
                f"or retire it and re-run from inventory ({len(found)} path(s))"
            )


def require_clean_status(vault: Path) -> None:
    """Planning begins only from HEAD with no tracked or untracked overlay."""
    completed = subprocess.run(
        ["git", "status", "--porcelain=v2", "--untracked-files=all"],
        cwd=vault,
        check=True,
        capture_output=True,
    )
    if completed.stdout:
        raise UnmigratableContentError(
            "inventory requires a clean status; preserve or commit current "
            "work and re-run from a newly recorded source HEAD"
        )


import yaml

from .identifiers import AXES, map_identifier, meets_floor


def _load(path: Path) -> object:
    if not path.is_file():
        return None
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, OSError, yaml.YAMLError) as exc:
        raise UnmigratableContentError(f"{path.name} could not be read") from exc


def existing_identifiers(vault: Path) -> dict[str, set[str]]:
    """Every current identifier, per axis, read from the registries."""
    system = vault / "_system"
    found: dict[str, set[str]] = {axis: set() for axis in AXES}

    entities = _load(system / "entities.yaml")
    if isinstance(entities, dict):
        found["entity"].update(
            key for key in (entities.get("entities") or {}) if isinstance(key, str)
        )

    products = _load(system / "products.yaml")
    if isinstance(products, dict):
        for values in (products.get("products") or {}).values():
            if isinstance(values, dict):
                found["product"].update(k for k in values if isinstance(k, str))

    members = _load(system / "members.yaml")
    if isinstance(members, dict):
        for values in (members.get("members") or {}).values():
            if isinstance(values, list):
                found["member"].update(
                    entry["id"]
                    for entry in values
                    if isinstance(entry, dict) and isinstance(entry.get("id"), str)
                )

    workspaces = _load(system / "workspaces.yaml")
    if isinstance(workspaces, dict):
        for entry in workspaces.get("workspaces") or []:
            if isinstance(entry, dict) and isinstance(entry.get("id"), str):
                found["workspace"].add(entry["id"])

    return found


def proposed_mappings(vault: Path) -> tuple[Mapping, ...]:
    """The deterministic mapping for every sub-floor identifier."""
    existing = existing_identifiers(vault)
    mappings: list[Mapping] = []
    for axis in AXES:
        for old in sorted(existing[axis]):
            if meets_floor(old):
                continue
            mappings.append(Mapping(axis=axis, old=old, new=map_identifier(axis, old)))
    return tuple(mappings)
