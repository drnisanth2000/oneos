from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

import yaml

_ENTITY_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class EntityManifestError(RuntimeError):
    pass


class EntitySelectionError(ValueError):
    pass


@dataclass(frozen=True)
class EntityDefinition:
    slug: str
    label: str
    flags: tuple[str, ...]


@dataclass(frozen=True)
class EntityCatalog:
    root: Path
    entities: tuple[EntityDefinition, ...]

    @classmethod
    def load(cls, root: Path | str) -> "EntityCatalog":
        root_path = Path(root).resolve()
        path = root_path / "_system/entities.yaml"
        if not path.is_file():
            raise EntityManifestError("entities manifest is missing")
        try:
            cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise EntityManifestError("entities manifest is invalid YAML") from exc
        if not isinstance(cfg, dict):
            raise EntityManifestError("entities manifest requires a mapping")
        records = cfg.get("entities")
        if not isinstance(records, dict):
            raise EntityManifestError("entities manifest requires an entities mapping")
        parsed: list[EntityDefinition] = []
        for slug, raw in records.items():
            if not isinstance(slug, str) or not _ENTITY_SLUG.fullmatch(slug):
                raise EntityManifestError("entities manifest contains an invalid slug")
            spec = raw or {}
            if not isinstance(spec, dict):
                raise EntityManifestError(f"entity {slug!r} must be a mapping")
            flags = spec.get("flags") or []
            if not isinstance(flags, list) or not all(isinstance(flag, str) for flag in flags):
                raise EntityManifestError(f"entity {slug!r} flags must be a list of strings")
            parsed.append(EntityDefinition(slug, str(spec.get("label", slug)), tuple(flags)))
        return cls(root_path, tuple(parsed))

    def require(self, slug: str) -> EntityDefinition:
        if not isinstance(slug, str) or not _ENTITY_SLUG.fullmatch(slug):
            raise EntitySelectionError("invalid entity selection")
        for entity in self.entities:
            if entity.slug == slug:
                return entity
        raise EntitySelectionError(f"unknown entity {slug!r}")
