"""scope.Scope — the immutable, manifest-backed tenant boundary."""
import dataclasses
import inspect

import pytest

from app.entities import (
    EntityCatalog,
    EntityDefinition,
    EntityManifestError,
    EntitySelectionError,
)
from app.inbox import read_inbox
from app.scope import CrossScopeError, Scope
from tests.conftest import entities_yaml, write_vault


def test_catalog_preserves_manifest_order_and_public_entity_fields(tmp_path):
    write_vault(tmp_path, entities_yaml("beta", "alpha"))

    catalog = EntityCatalog.load(tmp_path)

    assert catalog.entities == (
        EntityDefinition(slug="beta", label="Beta", flags=()),
        EntityDefinition(slug="alpha", label="Alpha", flags=()),
    )
    assert tuple(field.name for field in dataclasses.fields(EntityDefinition)) == (
        "slug",
        "label",
        "flags",
    )


def test_catalog_rejects_missing_manifest_without_exposing_vault_path(tmp_path):
    with pytest.raises(EntityManifestError) as caught:
        EntityCatalog.load(tmp_path)
    assert str(caught.value) == "entities manifest is missing"


@pytest.mark.parametrize("entity_spec", ['""', "[]", "0", "false"])
def test_catalog_rejects_falsey_non_mapping_entity_specs(tmp_path, entity_spec):
    manifest = f'version: "1.0"\nentities:\n  alpha: {entity_spec}\n'
    write_vault(tmp_path, manifest)
    with pytest.raises(EntityManifestError, match="must be a mapping"):
        EntityCatalog.load(tmp_path)


@pytest.mark.parametrize("flags", ['""', "{}", "0", "false"])
def test_catalog_rejects_falsey_non_list_flags(tmp_path, flags):
    manifest = f'version: "1.0"\nentities:\n  alpha:\n    flags: {flags}\n'
    write_vault(tmp_path, manifest)
    with pytest.raises(EntityManifestError, match="flags must be a list of strings"):
        EntityCatalog.load(tmp_path)


@pytest.mark.parametrize("label", ["[]", "{}", "0", "false"])
def test_catalog_rejects_non_string_labels(tmp_path, label):
    manifest = f'version: "1.0"\nentities:\n  alpha:\n    label: {label}\n    flags: []\n'
    write_vault(tmp_path, manifest)
    with pytest.raises(EntityManifestError, match="label must be a string"):
        EntityCatalog.load(tmp_path)


def test_catalog_defaults_null_entity_fields(tmp_path):
    write_vault(tmp_path, 'version: "1.0"\nentities:\n  alpha: null\n')
    assert EntityCatalog.load(tmp_path).entities == (
        EntityDefinition(slug="alpha", label="alpha", flags=()),
    )


def test_scope_accepts_only_registered_entity(tmp_path):
    write_vault(tmp_path, entities_yaml("alpha"))
    scope = Scope(tmp_path, "alpha")
    assert scope.current_entity() == "alpha"
    assert scope.resolve("00-inbox", "active") == tmp_path / "alpha/00-inbox/active"


@pytest.mark.parametrize("slug", ["", ".", "..", "Bad_Slug", "a/b", "a\\b"])
def test_scope_rejects_malformed_entity(tmp_path, slug):
    write_vault(tmp_path, entities_yaml("alpha"))
    with pytest.raises(EntitySelectionError):
        Scope(tmp_path, slug)


def test_scope_rejects_unknown_and_directory_only_entity(tmp_path):
    write_vault(tmp_path, entities_yaml("alpha"))
    (tmp_path / "directory-only").mkdir()
    with pytest.raises(EntitySelectionError):
        Scope(tmp_path, "directory-only")


def test_scope_is_immutable(tmp_path):
    write_vault(tmp_path, entities_yaml("alpha"))
    scope = Scope(tmp_path, "alpha")
    with pytest.raises((AttributeError, dataclasses.FrozenInstanceError)):
        scope._entity = "beta"


def test_legacy_entity_guard_rejects_cross_scope_argument(tmp_path):
    write_vault(tmp_path, entities_yaml("alpha", "beta"))
    scope = Scope(tmp_path, "alpha")
    assert scope.require_entity("alpha") == "alpha"
    with pytest.raises(CrossScopeError):
        scope.require_entity("beta")


def test_inbox_interface_has_one_identity_authority(tmp_path):
    write_vault(tmp_path, entities_yaml("alpha", "beta"))
    assert tuple(inspect.signature(read_inbox).parameters) == ("scope",)


def test_stored_path_must_name_bound_entity(tmp_path):
    write_vault(tmp_path, entities_yaml("alpha", "beta"))
    with pytest.raises(CrossScopeError):
        Scope(tmp_path, "alpha").resolve_stored("beta/00-inbox/active/item.md")


def test_vault_relative_rejects_another_entity_path(tmp_path):
    write_vault(tmp_path, entities_yaml("alpha", "beta"))
    with pytest.raises(CrossScopeError):
        Scope(tmp_path, "alpha").vault_relative(tmp_path / "beta/00-inbox/item.md")


def test_system_path_does_not_grant_another_entity_path(tmp_path):
    write_vault(tmp_path, entities_yaml("alpha", "beta"))
    scope = Scope(tmp_path, "alpha")
    assert scope.system_path("entities.yaml") == tmp_path / "_system/entities.yaml"
    with pytest.raises(CrossScopeError):
        scope.resolve("..", "beta", "00-inbox")


def test_entity_root_symlink_cannot_redirect_bound_scope(tmp_path):
    write_vault(tmp_path, entities_yaml("alpha", "beta"))
    beta = tmp_path / "beta"
    beta.mkdir()
    (beta / "private.md").write_text("beta-private\n", encoding="utf-8")
    (tmp_path / "alpha").symlink_to(beta, target_is_directory=True)

    scope = Scope(tmp_path, "alpha")
    assert scope.current_entity() == "alpha"
    with pytest.raises(CrossScopeError):
        scope.resolve("private.md")
