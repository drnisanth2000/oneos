from __future__ import annotations

from dataclasses import dataclass
from email.utils import getaddresses
from pathlib import Path
import re

import yaml

from .console_routing import structured_reader

_ENTITY_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class EntityManifestError(RuntimeError):
    pass


class SystemRegistryPathError(EntityManifestError):
    pass


class RecipientConfigurationError(EntityManifestError):
    pass


class EntitySelectionError(ValueError):
    pass


def resolve_system_registry(root: Path | str, *parts: str | Path) -> Path:
    """Resolve a registry beneath the vault's lexical ``_system`` boundary."""
    root_path = Path(root).resolve()
    lexical_system = root_path / "_system"
    try:
        resolved_system = lexical_system.resolve()
    except (OSError, RuntimeError) as exc:
        raise SystemRegistryPathError("system registry root is unsafe") from exc
    if resolved_system != lexical_system:
        raise SystemRegistryPathError("system registry root is redirected")
    try:
        candidate = lexical_system.joinpath(*map(Path, parts)).resolve()
    except (OSError, RuntimeError) as exc:
        raise SystemRegistryPathError("system registry path is unsafe") from exc
    if not candidate.is_relative_to(lexical_system):
        raise SystemRegistryPathError("system registry path leaves the registry root")
    return candidate


def normalize_email_address(value: object) -> str:
    if not isinstance(value, str):
        raise RecipientConfigurationError("email routing address must be a string")
    parsed = getaddresses([value.strip()])
    if len(parsed) != 1:
        raise RecipientConfigurationError("email routing address must contain one address")
    address = parsed[0][1].strip().lower()
    local, separator, domain = address.rpartition("@")
    if separator != "@" or not local or not domain or any(ch.isspace() for ch in address):
        raise RecipientConfigurationError("email routing address is malformed")
    return address


@dataclass(frozen=True)
class EntityDefinition:
    slug: str
    label: str
    flags: tuple[str, ...]
    email_addresses: tuple[str, ...] = ()


@dataclass(frozen=True)
class EntityCatalog:
    root: Path
    entities: tuple[EntityDefinition, ...]
    recipient_routes: tuple[tuple[str, str], ...] = ()

    @classmethod
    @structured_reader(category="registry")
    def load(cls, root: Path | str) -> "EntityCatalog":
        root_path = Path(root).resolve()
        path = resolve_system_registry(root_path, "entities.yaml")
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
        recipient_owners: dict[str, str] = {}
        for slug, raw in records.items():
            if not isinstance(slug, str) or not _ENTITY_SLUG.fullmatch(slug):
                raise EntityManifestError("entities manifest contains an invalid slug")
            spec = {} if raw is None else raw
            if not isinstance(spec, dict):
                raise EntityManifestError(f"entity {slug!r} must be a mapping")
            raw_flags = spec.get("flags")
            flags = [] if raw_flags is None else raw_flags
            if not isinstance(flags, list) or not all(isinstance(flag, str) for flag in flags):
                raise EntityManifestError(f"entity {slug!r} flags must be a list of strings")
            raw_label = spec.get("label")
            label = slug if raw_label is None else raw_label
            if not isinstance(label, str):
                raise EntityManifestError(f"entity {slug!r} label must be a string")
            if "ingest" not in spec:
                ingest = {}
            else:
                ingest = spec["ingest"]
                if not isinstance(ingest, dict):
                    raise RecipientConfigurationError("email ingest configuration must be a mapping")
            if "email_addresses" not in ingest:
                raw_addresses = []
            else:
                raw_addresses = ingest["email_addresses"]
                if not isinstance(raw_addresses, list):
                    raise RecipientConfigurationError("email routing addresses must be a list")
            addresses: list[str] = []
            for raw_address in raw_addresses:
                address = normalize_email_address(raw_address)
                owner = recipient_owners.get(address)
                if owner is not None and owner != slug:
                    raise RecipientConfigurationError(
                        "email routing address has duplicate ownership"
                    )
                if owner is None:
                    recipient_owners[address] = slug
                    addresses.append(address)
            parsed.append(EntityDefinition(slug, label, tuple(flags), tuple(addresses)))
        return cls(root_path, tuple(parsed), tuple(recipient_owners.items()))

    def entity_for_recipient(self, address: str) -> str | None:
        normalized = normalize_email_address(address)
        return dict(self.recipient_routes).get(normalized)

    def require(self, slug: str) -> EntityDefinition:
        if not isinstance(slug, str) or not _ENTITY_SLUG.fullmatch(slug):
            raise EntitySelectionError("invalid entity selection")
        for entity in self.entities:
            if entity.slug == slug:
                return entity
        raise EntitySelectionError(f"unknown entity {slug!r}")
