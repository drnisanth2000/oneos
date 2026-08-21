"""Immutable, manifest-backed entity request scope."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .entities import (
    EntityCatalog,
    EntitySelectionError,
    SystemRegistryPathError,
    resolve_system_registry,
)


class CrossScopeError(ValueError):
    pass


class OutOfScopeError(CrossScopeError):
    pass


class RedirectedPathError(CrossScopeError):
    pass


@dataclass(frozen=True)
class Scope:
    _root: Path
    _entity: str

    def __init__(self, root: Path | str, entity: str) -> None:
        catalog = EntityCatalog.load(root)
        selected = catalog.require(entity)
        object.__setattr__(self, "_root", catalog.root)
        object.__setattr__(self, "_entity", selected.slug)

    @property
    def root(self) -> Path:
        return self._root

    def current_entity(self) -> str:
        return self._entity

    def resolve(self, *parts: str | Path) -> Path:
        anchor = self._root / self._entity
        base = anchor.resolve()
        if base != anchor:
            raise CrossScopeError("entity root redirects outside the selected scope")
        candidate = base.joinpath(*map(Path, parts)).resolve()
        if not candidate.is_relative_to(base):
            raise CrossScopeError("entity path leaves the selected scope")
        return candidate

    def resolve_stored(self, relative: str | Path) -> Path:
        stored = Path(relative)
        if stored.is_absolute() or not stored.parts or stored.parts[0] != self._entity:
            raise CrossScopeError("stored path belongs to another entity")
        return self.resolve(*stored.parts[1:])

    def vault_relative(self, path: str | Path) -> str:
        candidate = Path(path).resolve()
        base = self.resolve()
        if not candidate.is_relative_to(base):
            raise CrossScopeError("path belongs to another entity")
        return candidate.relative_to(self._root).as_posix()

    def system_path(self, *parts: str | Path) -> Path:
        try:
            return resolve_system_registry(self._root, *parts)
        except SystemRegistryPathError as exc:
            raise CrossScopeError(str(exc)) from exc
