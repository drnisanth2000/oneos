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

import yaml

from .entities import EntityCatalog


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
        return self.root.joinpath("_system", *parts)

    # --- registry loading ---------------------------------------------------

    def _load_yaml(self, *system_parts: str) -> dict:
        path = self.system_path(*system_parts)
        if not path.is_file():
            raise FileNotFoundError(f"registry not found: {path}")
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    @cached_property
    def _archetypes(self) -> dict:
        cfg = self._load_yaml("archetypes.yaml")
        if "modules" not in cfg:
            raise ValueError("archetypes.yaml has no `modules:` — v2 schema required")
        return cfg

    # --- flag / module resolution (faithful to oneos_wizard) ---------------

    def resolve_flags(self, archetype: str | None, flags: list[str] | None) -> set[str]:
        """Union an archetype's bundle with explicit flags — but callers for the
        sidebar pass archetype=None, so only `flags:` count. Kept archetype-aware
        for parity with the wizard, never called with an archetype at read time.
        """
        cfg = self._archetypes
        declared = set(cfg.get("flags") or {})
        active: set[str] = set()

        if archetype:
            bundle = (cfg.get("archetypes") or {}).get(archetype)
            if bundle is None:
                raise ValueError(f"unknown archetype {archetype!r}")
            active |= {f for f, on in (bundle or {}).items() if on}

        for f in flags or []:
            if f not in declared:
                raise ValueError(f"unknown flag {f!r}")
            active.add(f)

        return active

    def active_modules(self, active_flags: set[str]) -> list[str]:
        """A module is active unless it declares `requires_flag:` and that flag
        is absent. Sorted, so zero-padded names order 00→15."""
        out = []
        for name, spec in self._archetypes["modules"].items():
            required = (spec or {}).get("requires_flag")
            if required is None or required in active_flags:
                out.append(name)
        return sorted(out)

    def _block_of(self, module: str) -> str:
        return self.block_of(module)

    def block_of(self, module: str) -> str:
        """Block for a module, derived from the registry — never hardcoded,
        never stored per file."""
        return ((self._archetypes["modules"].get(module) or {}).get("block", ""))

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
                    missing=on_disk and not (bundle_dir / name).is_dir(),
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
