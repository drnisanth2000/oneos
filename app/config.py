"""config.py — runtime configuration.

The vault path is instance-specific and therefore never hardcoded (AGENTS.md,
"the one rule"). It comes from the environment at runtime. Swap the env var and
the same code drives a different vault.
"""
from __future__ import annotations

import os
from pathlib import Path

from .scope import Scope

ENV_VAULT = "ONEOS_VAULT"


def vault_root() -> Path:
    raw = os.environ.get(ENV_VAULT)
    if not raw:
        raise RuntimeError(
            f"{ENV_VAULT} is not set — point it at the vault root before starting."
        )
    root = Path(raw).expanduser()
    if not root.is_dir():
        raise RuntimeError(f"{ENV_VAULT}={raw!r} is not a directory")
    return root


def build_scope() -> Scope:
    """The process-wide scope. current_entity() starts unset; a request selects
    one later. Path resolution always goes through this object."""
    return Scope(vault_root())
