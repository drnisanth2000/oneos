"""vault.Vault — bundle discovery and flag-driven module activation.

The three rules under test (AGENTS.md, task brief):
  1. No slug in Python — bundles come from entities.yaml, invented per test.
  2. Content paths go through <module>/active/ — not exercised here (no writes
     in step 2); the module tree is read-only structure.
  3. Module activation reads flags: only, never archetype:.
"""
import textwrap

import pytest

from app.entities import EntityCatalog, EntityManifestError
from app.scope import Scope
from app.vault import DestinationRegistryError, Vault
from tests.conftest import scaffold_modules, write_vault

ALL_MODULES = ["00-intake", "01-core", "02-work", "zz-extra"]
BASE_MODULES = ["00-intake", "01-core", "02-work"]  # everything but the gated one

DESTINATION_ARCHETYPES = """
version: "2.0"
flags:
  special: "Enables specialized work"
modules:
  02-work: {block: build}
  zz-extra: {block: self, requires_flag: special}
submodules:
  02-work:
    general: {name: General}
    specialized: {name: Specialized, flag: special}
"""


def counts(bundles):
    return {b.slug: len(b.modules) for b in bundles}


def test_destination_registry_uses_bound_entity_flags_only(tmp_path):
    root = write_vault(
        tmp_path,
        'version: "1.0"\nentities:\n  plain: {label: Plain, flags: []}\n'
        '  enabled: {label: Enabled, flags: [special]}\n',
        DESTINATION_ARCHETYPES,
    )
    vault = Vault(EntityCatalog.load(root))

    plain = Scope(root, "plain")
    enabled = Scope(root, "enabled")

    assert "zz-extra" not in vault.active_modules_for(plain)
    assert "zz-extra" in vault.active_modules_for(enabled)
    assert vault.active_submodules_for(plain, "02-work") == frozenset({"general"})
    assert vault.active_submodules_for(enabled, "02-work") == frozenset(
        {"general", "specialized"}
    )


def test_destination_registry_rejects_wrong_submodule_shape(tmp_path):
    root = write_vault(
        tmp_path,
        'version: "1.0"\nentities:\n  alpha: {label: Alpha, flags: []}\n',
        'version: "2.0"\nflags: {}\nmodules:\n  01-core: {block: govern}\n'
        'submodules:\n  01-core: [not-a-mapping]\n',
    )
    vault = Vault(EntityCatalog.load(root))
    with pytest.raises(DestinationRegistryError):
        vault.active_submodules_for(Scope(root, "alpha"), "01-core")


def test_require_block_rejects_unknown_or_empty_mapping(tmp_path):
    root = write_vault(
        tmp_path,
        'version: "1.0"\nentities:\n  alpha: {label: Alpha, flags: []}\n',
        'version: "2.0"\nflags: {}\nmodules:\n  01-core: {}\n',
    )
    vault = Vault(EntityCatalog.load(root))
    with pytest.raises(DestinationRegistryError):
        vault.require_block("01-core")
    with pytest.raises(DestinationRegistryError):
        vault.require_block("missing")


def test_module_spec_returns_copy_of_strict_mapping(tmp_path):
    root = write_vault(
        tmp_path,
        'version: "1.0"\nentities:\n  alpha: {label: Alpha, flags: []}\n',
        'version: "2.0"\nflags: {}\nmodules:\n'
        '  01-core: {block: govern, lifecycle_pattern: false}\n',
    )
    vault = Vault(EntityCatalog.load(root))
    spec = vault.module_spec("01-core")
    assert spec == {"block": "govern", "lifecycle_pattern": False}
    spec["block"] = "changed"
    assert vault.require_block("01-core") == "govern"


def test_discovers_every_bundle_from_entities_yaml(make_vault):
    root = make_vault(
        textwrap.dedent(
            """
            version: "1.0"
            entities:
              alpha:
                label: Alpha
                flags: [special]
              beta:
                label: Beta
                flags: [other]
            """
        )
    )
    bundles = Vault(EntityCatalog.load(root)).bundles()
    assert [b.slug for b in bundles] == ["alpha", "beta"]
    assert {b.slug: b.label for b in bundles} == {"alpha": "Alpha", "beta": "Beta"}


def test_vault_rejects_registry_leaf_redirected_outside_system(make_vault, tmp_path):
    root = make_vault(
        """
        version: "1.0"
        entities:
          alpha: { label: Alpha, flags: [] }
        """
    )
    vault = Vault(EntityCatalog.load(root))
    registry = root / "_system/archetypes.yaml"
    external = tmp_path / "external-archetypes.yaml"
    external.write_bytes(registry.read_bytes())
    registry.unlink()
    registry.symlink_to(external)

    with pytest.raises(EntityManifestError):
        vault.bundles()


def test_vault_allows_shared_registry_leaf_within_real_system(make_vault):
    root = make_vault(
        """
        version: "1.0"
        entities:
          alpha: { label: Alpha, flags: [] }
        """
    )
    registry = root / "_system/archetypes.yaml"
    shared = root / "_system/shared-archetypes.yaml"
    shared.write_bytes(registry.read_bytes())
    registry.unlink()
    registry.symlink_to(shared)

    assert Vault(EntityCatalog.load(root)).bundles()[0].slug == "alpha"


def test_flag_activates_gated_module(make_vault):
    root = make_vault(
        """
        version: "1.0"
        entities:
          withflag:  { label: With,    flags: [special] }
          without:   { label: Without, flags: [other] }
        """
    )
    scaffold_modules(root, "withflag", ALL_MODULES)
    scaffold_modules(root, "without", BASE_MODULES)
    bundles = {b.slug: b for b in Vault(EntityCatalog.load(root)).bundles()}
    assert counts(bundles.values()) == {"withflag": 4, "without": 3}
    assert "zz-extra" in [m.name for m in bundles["withflag"].modules]
    assert "zz-extra" not in [m.name for m in bundles["without"].modules]


def test_activation_ignores_archetype_uses_flags_only(make_vault):
    """archetype: `special` would activate zz-extra if merged. flags: does not
    include `special`, so the module must NOT appear. (AGENTS.md: never merge
    archetype: into flags: at read time.)"""
    root = make_vault(
        """
        version: "1.0"
        entities:
          tricky:
            label: Tricky
            archetype: special
            flags: [other]
        """
    )
    (bundle,) = Vault(EntityCatalog.load(root)).bundles()
    assert [m.name for m in bundle.modules] == BASE_MODULES
    assert bundle.flags == ("other",)


def test_module_carries_block_from_registry(make_vault):
    root = make_vault(
        """
        version: "1.0"
        entities:
          x: { label: X, flags: [special] }
        """
    )
    (bundle,) = Vault(EntityCatalog.load(root)).bundles()
    blocks = {m.name: m.block for m in bundle.modules}
    assert blocks == {
        "00-intake": "system",
        "01-core": "govern",
        "02-work": "build",
        "zz-extra": "self",
    }


def test_e4_module_required_by_flags_but_missing_on_disk(make_vault):
    """A module the flags activate but the disk lacks is surfaced as an error,
    not silently dropped (check_v2 E4)."""
    root = make_vault(
        """
        version: "1.0"
        entities:
          gap: { label: Gap, flags: [special] }
        """
    )
    # Scaffold everything EXCEPT the gated module -> zz-extra is missing.
    scaffold_modules(root, "gap", BASE_MODULES)
    (bundle,) = Vault(EntityCatalog.load(root)).bundles()
    # Still listed (count unchanged), but flagged missing.
    assert len(bundle.modules) == 4
    missing = [m.name for m in bundle.modules if m.missing]
    assert missing == ["zz-extra"]
    assert [m.name for m in bundle.errors] == ["zz-extra"]


def test_present_and_absent_modules_not_flagged_missing_when_bundle_absent(make_vault):
    """When a whole bundle directory is absent, per-module E4 is not raised
    (check_v2: the registry test owns 'bundle listed but absent')."""
    root = make_vault(
        """
        version: "1.0"
        entities:
          ghost: { label: Ghost, flags: [special] }
        """
    )
    # No directory scaffolded for `ghost` at all.
    (bundle,) = Vault(EntityCatalog.load(root)).bundles()
    assert bundle.on_disk is False
    assert bundle.errors == []


def test_no_missing_errors_when_disk_matches_flags(make_vault):
    root = make_vault(
        """
        version: "1.0"
        entities:
          clean: { label: Clean, flags: [special] }
        """
    )
    scaffold_modules(root, "clean", ALL_MODULES)
    (bundle,) = Vault(EntityCatalog.load(root)).bundles()
    assert bundle.on_disk is True
    assert bundle.errors == []
