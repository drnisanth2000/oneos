"""scope.py — the tenant boundary.

`current_entity()` wraps every query and path resolution, from the first commit
(spec §7, invariant 4). It is the future tenant boundary; retrofitting it is
miserable, so it exists before there is more than one entity to scope to.

No slug is baked in here. The current entity is set at runtime (later: from the
session); path resolution takes the slug as data and refuses anything that could
escape the vault root.
"""
from __future__ import annotations

from pathlib import Path


class Scope:
    def __init__(self, root: Path | str, current: str | None = None) -> None:
        self._root = Path(root)
        self._current = current

    @property
    def root(self) -> Path:
        return self._root

    def current_entity(self) -> str | None:
        return self._current

    def set_current_entity(self, slug: str | None) -> None:
        self._current = slug

    def bundle_path(self, slug: str) -> Path:
        """Resolve a bundle directory. `slug` is a single path segment; reject
        anything that is empty, a dot-name, or contains a separator — a bundle
        slug must never be able to climb out of the vault."""
        if slug in ("", ".", "..") or "/" in slug or "\\" in slug:
            raise ValueError(f"invalid entity slug: {slug!r}")
        return self._root / slug

    def resolve(self, slug: str, *parts: str) -> Path:
        """Path to something inside a bundle. All disk access goes through here
        so the tenant boundary is honoured in one place."""
        return self.bundle_path(slug).joinpath(*parts)

    def system_path(self, *parts: str) -> Path:
        """Path inside `_system/` (the registries). Not entity-scoped."""
        return self._root.joinpath("_system", *parts)
