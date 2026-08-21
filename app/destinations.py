"""Canonical, read-only destinations for classification proposals."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from .entities import EntityCatalog
from .scope import Scope
from .vault import Vault


class DestinationError(ValueError):
    pass


class InvalidSourceLeaf(DestinationError):
    pass


class RedirectedSourceLeaf(InvalidSourceLeaf):
    pass


class MissingSourceLeaf(InvalidSourceLeaf):
    pass


class NonCanonicalLeaf(InvalidSourceLeaf):
    pass


class InvalidModule(DestinationError):
    pass


class InvalidSub(DestinationError):
    pass


class BlockMismatch(DestinationError):
    pass


class UnsafeDestinationPath(DestinationError):
    pass


class RedirectedDestination(UnsafeDestinationPath):
    pass


class MissingDestination(UnsafeDestinationPath):
    pass


_REGISTRY_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _is_registry_id(value: object) -> bool:
    return isinstance(value, str) and _REGISTRY_ID.fullmatch(value) is not None


@dataclass(frozen=True)
class ClassificationDestination:
    entity: str
    module: str
    sub: str | None
    block: str
    src: str
    dst: str
    path: Path


def _require_markdown_leaf(leaf: str) -> None:
    if (
        not leaf
        or leaf in {".", ".."}
        or leaf.startswith(".")
        or "/" in leaf
        or "\\" in leaf
        or "\r" in leaf
        or "\n" in leaf
        or leaf != leaf.strip()
        or Path(leaf).suffix != ".md"
    ):
        raise NonCanonicalLeaf("source leaf is non-canonical")


def _require_real_directory(scope: Scope, *parts: str) -> Path:
    lexical = scope.root / scope.current_entity() / Path(*parts)
    resolved = scope.resolve(*parts)
    if lexical.is_symlink() or resolved != lexical:
        raise RedirectedDestination("destination directory is redirected")
    if not lexical.exists():
        # Absence is a routine condition (the standing E4 state) and must
        # never raise a tampering alarm.
        raise MissingDestination("destination directory is missing")
    if not lexical.is_dir():
        raise RedirectedDestination("destination directory is not a directory")
    return resolved


def resolve_classification_destination(
    scope: Scope,
    item_path: Path | str,
    *,
    module: object,
    sub: object,
    claimed_block: object | None = None,
    require_source: bool = True,
) -> ClassificationDestination:
    catalog = EntityCatalog.load(scope.root)
    vault = Vault(catalog)
    entity = scope.current_entity()
    catalog.require(entity)

    source = Path(item_path)
    leaf = source.name
    _require_markdown_leaf(leaf)
    expected_source = scope.root / entity / "00-inbox" / "active" / leaf
    if source != expected_source:
        raise NonCanonicalLeaf("source is not the canonical inbox receipt")
    inbox_dir = _require_real_directory(scope, "00-inbox")
    inbox_active_dir = _require_real_directory(scope, "00-inbox", "active")
    if inbox_active_dir.parent != inbox_dir:
        raise RedirectedDestination("inbox lifecycle directory is redirected")
    if expected_source.is_symlink():
        raise RedirectedSourceLeaf("source receipt is redirected")
    if require_source and not expected_source.is_file():
        if expected_source.exists():
            raise RedirectedSourceLeaf("source receipt is not a regular file")
        raise MissingSourceLeaf("source receipt is missing")

    if not _is_registry_id(module):
        raise InvalidModule("destination module is non-canonical")
    if module not in vault.active_modules_for(scope):
        raise InvalidModule("destination module is not active")

    canonical_sub: str | None
    if sub is None or sub == "":
        canonical_sub = None
    elif not _is_registry_id(sub):
        raise InvalidSub("destination sub is non-canonical")
    elif sub not in vault.active_submodules_for(scope, module):
        raise InvalidSub("destination sub is not active for this module")
    else:
        canonical_sub = sub

    block = vault.require_block(module)
    if claimed_block is not None and claimed_block != block:
        raise BlockMismatch("claimed block does not match destination module")

    module_dir = _require_real_directory(scope, module)
    module_spec = vault.module_spec(module)
    if module_spec.get("lifecycle_pattern", True) is False:
        raise InvalidModule("destination module has no active lifecycle")
    active_dir = _require_real_directory(scope, module, "active")
    if active_dir.parent != module_dir:
        raise RedirectedDestination("active lifecycle directory is redirected")

    destination_lexical = scope.root / entity / module / "active" / leaf
    destination = scope.resolve(module, "active", leaf)
    if (
        destination.parent != active_dir
        or destination_lexical.is_symlink()
        or destination.is_symlink()
    ):
        raise RedirectedDestination("destination is not canonical")

    return ClassificationDestination(
        entity=entity,
        module=module,
        sub=canonical_sub,
        block=block,
        src=scope.vault_relative(expected_source),
        dst=scope.vault_relative(destination),
        path=destination,
    )
