"""vault.py — read the registries, resolve the module tree.

Bundle discovery is `_system/entities.yaml` — never `index.md` (stale) and
never a directory scan (a scan cannot tell a bundle from a stray folder, nor
supply labels or flags). Module activation reads `flags:` only; `archetype:` is
a creation-time preset and is never merged at read time (decisions.md
2026-08-05). This mirrors `check_v2.load_expected_modules` exactly, so the
sidebar and the validator agree on which modules exist.

Nothing instance-specific lives here: swap in a different entities.yaml and you
get a different system with no code change.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
import re

import yaml

from .console_routing import structured_reader
from .entities import EntityCatalog, resolve_system_registry
from .scope import Scope


class DestinationRegistryError(ValueError):
    pass


_REGISTRY_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _is_registry_id(value: object) -> bool:
    return isinstance(value, str) and _REGISTRY_ID.fullmatch(value) is not None


def _boundary(read, message: str):
    """Task 8 corrective (design §5 "Boundary conversions"): perform a
    registry access whose only failure mode is the wrong SHAPE — an
    `AttributeError` from calling a mapping method (`.items()`, `.get()`) on
    something that is not a mapping, or a `TypeError` from an
    unhashable/non-iterable value reaching `set(...)` or `in` — and convert
    that escape into `DestinationRegistryError`.

    `_destination_registry` already converts these shapes, but with FULLER,
    STRICTER semantic validation (canonical ids, `requires_flag` present in
    a declared `flags:`, `lifecycle_pattern` typing, ...) than
    `resolve_flags` / `active_modules` / `block_of` have ever enforced —
    those three serve `bundles()`, called unguarded from every Console page,
    and S6 has no authority to fatal-ize a shape it currently tolerates (a
    `flags:` written as a plain list of names, e.g., works today via
    `set([...])` and must keep working). So this is deliberately NOT an
    `isinstance` pre-check shared with `_destination_registry` — that was
    tried (see the task's saved, reverted patch) and it measurably changes
    which shapes are fatal. Instead, `read` performs the UNMODIFIED original
    access: any shape that already succeeds keeps succeeding with the
    identical result, and only a shape that already raises one of these two
    specific errors is retyped. Never a fatality change, in either
    direction.

    M6 (S6 corrective review): this is NOT the identical shape as
    `_destination_registry`'s own precedent, and the claim is corrected
    here rather than repeated. `_destination_registry` wraps its
    `self._archetypes` access in a single `try`/`except` around the WHOLE
    property read, catching six types (`OSError`, `TypeError`,
    `AttributeError`, `KeyError`, `ValueError`, `yaml.YAMLError`) — a
    single-statement guard that nonetheless catches six types. `_boundary`
    is narrower on the type axis only: one
    access at a time (never a whole function body — Rule 5 forbids that
    breadth), and only the two types a wrong-shape access can actually
    raise. What the two share is the destination type and the principle
    (retype an escape, change nothing else) — not the shape of the guard.
    """
    try:
        return read()
    except (AttributeError, TypeError) as exc:
        raise DestinationRegistryError(message) from exc


@dataclass(frozen=True)
class Module:
    name: str
    block: str
    #: E4 — activated by the bundle's flags but absent from disk. Surfaced,
    #: never silently dropped.
    missing: bool = False


@dataclass(frozen=True)
class Bundle:
    slug: str
    label: str
    flags: tuple[str, ...]
    modules: tuple[Module, ...]
    #: Whether the bundle directory exists. When False, per-module E4 is not
    #: raised — the registry test owns "listed but absent" (check_v2).
    on_disk: bool = True

    @property
    def errors(self) -> list[Module]:
        return [m for m in self.modules if m.missing]


class Vault:
    def __init__(self, catalog: EntityCatalog) -> None:
        self._catalog = catalog

    @property
    def root(self) -> Path:
        return self._catalog.root

    def system_path(self, *parts: str) -> Path:
        return resolve_system_registry(self.root, *parts)

    # --- registry loading ---------------------------------------------------

    @structured_reader(category="registry")
    def _load_yaml(self, *system_parts: str) -> dict:
        path = self.system_path(*system_parts)
        if not path.is_file():
            # Absence of a required system registry is already fatal on every
            # caller path; only the type is normalized (S6 design §5).
            raise DestinationRegistryError("registry not found")
        try:
            loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise DestinationRegistryError("registry is invalid YAML") from exc
        if not isinstance(loaded, dict):
            raise DestinationRegistryError("registry must be a mapping")
        return loaded

    @cached_property
    def _archetypes(self) -> dict:
        cfg = self._load_yaml("archetypes.yaml")
        if "modules" not in cfg:
            # Sibling of the _load_yaml conversion: a hand-edited archetypes.yaml
            # missing `modules:` is a registry-validity condition, and bundles()
            # is called unguarded from every Console page.
            raise DestinationRegistryError(
                "archetypes.yaml has no `modules:` — v2 schema required"
            )
        return cfg

    # --- flag / module resolution (faithful to oneos_wizard) ---------------

    def resolve_flags(self, archetype: str | None, flags: list[str] | None) -> set[str]:
        """Union an archetype's bundle with explicit flags — but callers for the
        sidebar pass archetype=None, so only `flags:` count. Kept archetype-aware
        for parity with the wizard, never called with an archetype at read time.
        """
        cfg = self._archetypes
        declared = _boundary(
            lambda: set(cfg.get("flags") or {}),
            "archetypes.yaml `flags:` must be a mapping",
        )
        active: set[str] = set()

        if archetype:
            # M1 (S6 corrective review): `bundles()` never passes an
            # `archetype` — this branch is console-unreachable today — but
            # design §5 binds the READER, not the caller, so the same two
            # conversions apply here as everywhere else in this function.
            bundle = _boundary(
                lambda: (cfg.get("archetypes") or {}).get(archetype),
                "archetypes.yaml `archetypes:` must be a mapping",
            )
            if bundle is None:
                raise DestinationRegistryError(f"unknown archetype {archetype!r}")
            active |= _boundary(
                lambda: {f for f, on in (bundle or {}).items() if on},
                f"archetypes.yaml archetype {archetype!r} must be a mapping",
            )

        for f in flags or []:
            if f not in declared:
                # C2 (S6 review, Task 8 corrective): an unknown flag in
                # entities.yaml is a registry-validity condition, not a
                # programmer error, and this is called unguarded from every
                # Console page (design §5) — already fatal; only the type
                # changes, so every route's own DestinationRegistryError
                # catch answers it instead of the untyped ValueError
                # reaching the global fallback as E-UNKNOWN.
                raise DestinationRegistryError(f"unknown flag {f!r}")
            active.add(f)

        return active

    def active_modules(self, active_flags: set[str]) -> list[str]:
        """A module is active unless it declares `requires_flag:` and that flag
        is absent. Sorted, so zero-padded names order 00→15."""
        modules = self._archetypes["modules"]
        items = _boundary(
            lambda: list(modules.items()),
            "archetypes.yaml `modules:` must be a mapping",
        )
        out = []
        for name, spec in items:
            required = _boundary(
                lambda: (spec or {}).get("requires_flag"),
                f"archetypes.yaml module {name!r} spec must be a mapping",
            )
            activated = required is None or _boundary(
                lambda: required in active_flags,
                f"archetypes.yaml module {name!r} requires_flag must be a string",
            )
            if activated:
                out.append(name)
        return _boundary(
            lambda: sorted(out),
            "archetypes.yaml `modules:` keys must be comparable strings",
        )

    @cached_property
    def _destination_registry(self) -> tuple[dict, dict, dict]:
        try:
            cfg = self._archetypes
        except (OSError, TypeError, AttributeError, KeyError, ValueError, yaml.YAMLError) as exc:
            raise DestinationRegistryError("destination registry cannot be loaded") from exc
        if not isinstance(cfg, dict):
            raise DestinationRegistryError("destination registry must be a mapping")

        flags = cfg.get("flags", {})
        if not isinstance(flags, dict):
            raise DestinationRegistryError("flags registry must be a mapping")
        for flag, description in flags.items():
            if not _is_registry_id(flag) or not isinstance(description, str):
                raise DestinationRegistryError("flag registry entry is malformed")

        modules = cfg.get("modules")
        if not isinstance(modules, dict):
            raise DestinationRegistryError("modules registry must be a mapping")
        for module, spec in modules.items():
            if not _is_registry_id(module):
                raise DestinationRegistryError("module id is non-canonical")
            if not isinstance(spec, dict):
                raise DestinationRegistryError("module registry entry is malformed")
            if not _is_registry_id(spec.get("block")):
                raise DestinationRegistryError("module block is non-canonical")
            required = spec.get("requires_flag")
            if required is not None and (
                not _is_registry_id(required) or required not in flags
            ):
                raise DestinationRegistryError("module requires_flag is malformed")
            lifecycle = spec.get("lifecycle_pattern", True)
            if not isinstance(lifecycle, bool):
                raise DestinationRegistryError("module lifecycle_pattern must be boolean")

        submodules = cfg.get("submodules", {})
        if submodules is None:
            submodules = {}
        if not isinstance(submodules, dict):
            raise DestinationRegistryError("submodules registry must be a mapping")
        for module, entries in submodules.items():
            if not _is_registry_id(module) or module not in modules:
                raise DestinationRegistryError("submodule group is malformed")
            if not isinstance(entries, dict):
                raise DestinationRegistryError("module submodules must be a mapping")
            for sub, spec in entries.items():
                if not _is_registry_id(sub) or not isinstance(spec, dict):
                    raise DestinationRegistryError("submodule entry is malformed")
                required = spec.get("flag")
                if required is not None and (
                    not _is_registry_id(required) or required not in flags
                ):
                    raise DestinationRegistryError("submodule flag is malformed")
        return flags, modules, submodules

    def _entity_flags(self, scope: Scope) -> set[str]:
        if scope.root != self.root:
            raise DestinationRegistryError("scope and registry roots differ")
        flags, _, _ = self._destination_registry
        try:
            entity = self._catalog.require(scope.current_entity())
        except ValueError as exc:
            raise DestinationRegistryError("scope entity is not in the registry") from exc
        active = set(entity.flags)
        if not active.issubset(flags):
            raise DestinationRegistryError("entity references an unknown flag")
        return active

    def active_modules_for(self, scope: Scope) -> frozenset[str]:
        active_flags = self._entity_flags(scope)
        _, modules, _ = self._destination_registry
        return frozenset(
            module
            for module, spec in modules.items()
            if spec.get("requires_flag") is None
            or spec.get("requires_flag") in active_flags
        )

    def active_submodules_for(self, scope: Scope, module: str) -> frozenset[str]:
        _, modules, groups = self._destination_registry
        if not _is_registry_id(module) or module not in modules:
            raise DestinationRegistryError("destination module is not declared")
        entries = groups.get(module, {})
        flags = self._entity_flags(scope)
        return frozenset(
            sub
            for sub, spec in entries.items()
            if spec.get("flag") is None or spec.get("flag") in flags
        )

    def require_block(self, module: str) -> str:
        spec = self.module_spec(module)
        block = spec.get("block")
        if not isinstance(block, str) or not block:
            raise DestinationRegistryError("destination module has no block")
        return block

    def module_spec(self, module: str) -> dict:
        _, modules, _ = self._destination_registry
        if not _is_registry_id(module) or module not in modules:
            raise DestinationRegistryError("destination module is not declared")
        return dict(modules[module])

    def _block_of(self, module: str) -> str:
        return self.block_of(module)

    def block_of(self, module: str) -> str:
        """Block for a module, derived from the registry — never hardcoded,
        never stored per file.

        Task 8 corrective: `Classifier.classify` (app/classifier.py) calls
        this directly with a module name read from `classifier/rules.yaml`,
        independently of `bundles()` / `active_modules` — so the identical
        unguarded access here is reachable on its own, not merely "already
        validated by the time bundles() gets here" as it is on that path.
        """
        modules = self._archetypes["modules"]
        spec = _boundary(
            lambda: modules.get(module),
            "archetypes.yaml `modules:` must be a mapping",
        )
        return _boundary(
            lambda: (spec or {}).get("block", ""),
            f"archetypes.yaml module {module!r} spec must be a mapping",
        )

    # --- sidebar model ------------------------------------------------------

    def bundles(self) -> list[Bundle]:
        """Every bundle in entities.yaml, each with the modules its flags
        activate. Order follows the registry."""
        result: list[Bundle] = []
        for entity in self._catalog.entities:
            slug = entity.slug
            flags = list(entity.flags)
            # archetype is deliberately NOT passed — flags only.
            active = self.resolve_flags(None, flags)
            names = self.active_modules(active)

            bundle_dir = self.root / slug
            on_disk = bundle_dir.is_dir()

            modules = tuple(
                Module(
                    name=name,
                    block=self._block_of(name),
                    # E4 only when the bundle exists but this module does not.
                    missing=on_disk and not _boundary(
                        lambda: (bundle_dir / name).is_dir(),
                        "archetypes.yaml module name must be a string",
                    ),
                )
                for name in names
            )
            result.append(
                Bundle(
                    slug=slug,
                    label=entity.label,
                    flags=tuple(flags),
                    modules=modules,
                    on_disk=on_disk,
                )
            )
        return result
