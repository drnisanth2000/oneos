"""vault.Vault — bundle discovery and flag-driven module activation.

The three rules under test (AGENTS.md, task brief):
  1. No slug in Python — bundles come from entities.yaml, invented per test.
  2. Content paths go through <module>/active/ — not exercised here (no writes
     in step 2); the module tree is read-only structure.
  3. Module activation reads flags: only, never archetype:.
"""
import textwrap

from app.scope import Scope
from app.vault import Vault
from tests.conftest import scaffold_modules

ALL_MODULES = ["00-intake", "01-core", "02-work", "zz-extra"]
BASE_MODULES = ["00-intake", "01-core", "02-work"]  # everything but the gated one


def counts(bundles):
    return {b.slug: len(b.modules) for b in bundles}


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
    bundles = Vault(Scope(root)).bundles()
    assert [b.slug for b in bundles] == ["alpha", "beta"]
    assert {b.slug: b.label for b in bundles} == {"alpha": "Alpha", "beta": "Beta"}


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
    bundles = {b.slug: b for b in Vault(Scope(root)).bundles()}
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
    (bundle,) = Vault(Scope(root)).bundles()
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
    (bundle,) = Vault(Scope(root)).bundles()
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
    (bundle,) = Vault(Scope(root)).bundles()
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
    (bundle,) = Vault(Scope(root)).bundles()
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
    (bundle,) = Vault(Scope(root)).bundles()
    assert bundle.on_disk is True
    assert bundle.errors == []
