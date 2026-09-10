"""Read-only, fail-closed lifecycle declaration enumeration.

This is a planner, not a validator certification or a writer. Only missing
immediate child directories are repairable; callers must bind and revalidate
all declarations and filesystem identities at their transaction boundary.
"""
from __future__ import annotations

from .console_routing import structured_reader

from contextlib import contextmanager
from dataclasses import dataclass
import os
from pathlib import Path
import re
import stat
from typing import Literal

import yaml

from .entities import EntityCatalog, EntityDefinition
from .scope import Scope
from .vault import Vault


class LifecycleShapeError(ValueError):
    """A declaration or filesystem boundary prevents safe shape planning."""


@dataclass(frozen=True)
class RequiredEntry:
    path: str
    kind: Literal["directory", "file"]
    declaration: str


class _UniqueLoader(yaml.SafeLoader):
    pass


def _mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            if key in result:
                raise LifecycleShapeError("duplicate registry key")
            result[key] = loader.construct_object(value_node, deep=deep)
        except TypeError as exc:
            raise LifecycleShapeError("registry key must be scalar") from exc
    return result


_UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_EXTENSION = re.compile(r"[a-zA-Z0-9_][a-zA-Z0-9_.-]*\Z")
_REPAIR_DIRECTORY = re.compile(r"[a-z0-9_]+(?:-[a-z0-9_]+)*\Z")


@contextmanager
def _root_descriptor(root: Path):
    """Walk absolute lexical ancestors without following any symlink."""
    descriptor = os.open(root.anchor, _DIR_FLAGS)
    try:
        for component in root.parts[1:]:
            child = os.open(component, _DIR_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        yield descriptor
    finally:
        os.close(descriptor)


@structured_reader(category="registry")
def _registry(root_fd: int, name: str) -> dict:
    system_fd = os.open("_system", _DIR_FLAGS, dir_fd=root_fd)
    try:
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                             dir_fd=system_fd)
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise LifecycleShapeError("registry must be a regular file")
            value = yaml.load(handle.read(), Loader=_UniqueLoader)
    finally:
        os.close(system_fd)
    if not isinstance(value, dict):
        raise LifecycleShapeError("registry must be a mapping")
    return value


def _catalog(root: Path, declaration: dict) -> EntityCatalog:
    """Project no-follow registry bytes into the existing runtime catalog.

    EntityCatalog.load uses path-based reads. Constructing its immutable data
    here avoids reopening a checked path and preserves Vault's flag selection.
    Only entity selection fields are relevant to this read-only planner.
    """
    records = declaration.get("entities")
    if not isinstance(records, dict):
        raise LifecycleShapeError("entities must be a mapping")
    definitions = []
    for name, raw in records.items():
        spec = {} if raw is None else raw
        if not isinstance(spec, dict):
            raise LifecycleShapeError("entity declaration must be a mapping")
        flags = spec.get("flags", [])
        if flags is None:
            flags = []
        label = spec.get("label", name)
        if not isinstance(flags, list) or not all(isinstance(flag, str) for flag in flags):
            raise LifecycleShapeError("entity flags must be strings")
        if label is not None and not isinstance(label, str):
            raise LifecycleShapeError("entity label must be a string")
        definitions.append(EntityDefinition(name, name if label is None else label, tuple(flags)))
    catalog = EntityCatalog(root, tuple(definitions))
    for definition in definitions:
        catalog.require(definition.slug)
    return catalog


def required_shape(scope: Scope) -> tuple[RequiredEntry, ...]:
    """Enumerate roots and required children from explicit runtime flags."""
    entity = scope.current_entity()
    try:
        with _root_descriptor(scope.root) as root_fd:
            catalog = _catalog(scope.root, _registry(root_fd, "entities.yaml"))
            vault = Vault(catalog)
            # A fresh Vault per query: declarations never persist across previews.
            vault.__dict__["_archetypes"] = _registry(root_fd, "archetypes.yaml")
            entries = {entity: RequiredEntry(entity, "directory", "entities.yaml:entities")}
            active_modules = vault.active_modules_for(scope)
            for module in sorted(vault._archetypes["modules"]):
                base = f"{entity}/{module}"
                source = f"archetypes.yaml:modules.{module}"
                spec = vault.module_spec(module)
                children = {}
                if module == "12-archive":
                    children["_templates"] = ("directory", "conventions:v2:archive")
                elif spec.get("lifecycle_pattern", True):
                    children = {name: (kind, "conventions:v2:lifecycle") for name, kind in (
                        ("_templates", "directory"), ("active", "directory"),
                        ("archive", "directory"), ("status.md", "file"))}
                extensions = spec.get("extensions", [])
                if not isinstance(extensions, list):
                    raise LifecycleShapeError("module extensions must be a list")
                seen = set()
                for extension in extensions:
                    if not isinstance(extension, str):
                        raise LifecycleShapeError("extension must be a string")
                    name = extension[:-1] if extension.endswith("/") else extension
                    if not _EXTENSION.fullmatch(name):
                        raise LifecycleShapeError("nested or non-canonical extension is unsupported")
                    if module == "12-archive" and name in {"active", "archive", "status.md"}:
                        raise LifecycleShapeError("archive extension contradicts the lifecycle exception")
                    if extension.endswith("/") and (
                        not _REPAIR_DIRECTORY.fullmatch(name) or name in {"outbox", "staging"}
                    ):
                        raise LifecycleShapeError("extension directory is unsupported by the repair contract")
                    if name in seen or name in children:
                        raise LifecycleShapeError("duplicate or conflicting extension")
                    seen.add(name)
                    children[name] = ("directory" if extension.endswith("/") else "file",
                                      f"{source}.extensions:{extension}")
                if module not in active_modules:
                    continue
                entries[base] = RequiredEntry(base, "directory", source)
                for name, (kind, declaration) in children.items():
                    path = f"{base}/{name}"
                    entries[path] = RequiredEntry(path, kind, declaration)
            return tuple(entries[path] for path in sorted(entries))
    except (OSError, ValueError, yaml.YAMLError, UnicodeError) as exc:
        if isinstance(exc, LifecycleShapeError):
            raise
        raise LifecycleShapeError("lifecycle declarations could not be safely resolved") from exc


def inspect_shape(scope: Scope) -> tuple[RequiredEntry, ...]:
    """Return eligible missing directories, or refuse the entire batch.

    Every path component is checked through held no-follow directory
    descriptors. Required files are checked with no-follow stat, never read.
    """
    scope.current_entity()
    entries = required_shape(scope)
    missing = []
    try:
        with _root_descriptor(scope.root) as root_fd:
            for entry in entries:
                components = entry.path.split("/")
                parent = os.dup(root_fd)
                try:
                    for component in components[:-1]:
                        child = os.open(component, _DIR_FLAGS, dir_fd=parent)
                        os.close(parent)
                        parent = child
                    try:
                        info = os.stat(components[-1], dir_fd=parent, follow_symlinks=False)
                    except FileNotFoundError:
                        if entry.kind != "directory" or len(components) != 3:
                            raise LifecycleShapeError("required root or content file is missing")
                        missing.append(entry)
                        continue
                    expected_kind = stat.S_ISDIR if entry.kind == "directory" else stat.S_ISREG
                    if not expected_kind(info.st_mode):
                        raise LifecycleShapeError("required entry has a wrong kind or redirects")
                finally:
                    os.close(parent)
        return tuple(missing)
    except OSError as exc:
        raise LifecycleShapeError("required ancestor cannot be safely opened") from exc
