"""config.py — runtime configuration.

The vault path is instance-specific and therefore never hardcoded (AGENTS.md,
"the one rule"). It comes from the environment at runtime. Swap the env var and
the same code drives a different vault.
"""
from __future__ import annotations

import os
import stat
from functools import cache
from pathlib import Path

from .console_routing import failure_contract
from .entities import (
    EntityCatalog,
    EntityManifestError,
    EntitySelectionError,
    SystemRegistryPathError,
)
from .scope import Scope

ENV_VAULT = "ONEOS_VAULT"


class VaultRootUnavailable(RuntimeError):
    """A configured vault root is no longer available at request time."""


def _vault_root_identity(root: Path) -> tuple[Path, int, int]:
    try:
        resolved = root.resolve(strict=True)
        info = root.stat()
    except (OSError, RuntimeError) as exc:
        raise VaultRootUnavailable("configured vault root is unavailable") from exc
    if not stat.S_ISDIR(info.st_mode):
        raise VaultRootUnavailable("configured vault root is unavailable")
    return resolved, info.st_dev, info.st_ino


@cache
def _pinned_vault_root_identity(root: Path) -> tuple[Path, int, int]:
    """Remember the root first observed for this configured lexical path."""
    return _vault_root_identity(root)


@failure_contract(raises=(VaultRootUnavailable,))
def vault_root() -> Path:
    raw = os.environ.get(ENV_VAULT)
    if not raw:
        raise RuntimeError(
            f"{ENV_VAULT} is not set — point it at the vault root before starting."
        )
    root = Path(raw).expanduser().absolute()
    if _vault_root_identity(root) != _pinned_vault_root_identity(root):
        raise VaultRootUnavailable("configured vault root is unavailable")
    return root


def build_catalog() -> EntityCatalog:
    return EntityCatalog.load(vault_root())


@failure_contract(
    raises=(EntityManifestError, SystemRegistryPathError, EntitySelectionError),
    calls=(vault_root,),
)
def build_scope(entity: str) -> Scope:
    return Scope(vault_root(), entity)
