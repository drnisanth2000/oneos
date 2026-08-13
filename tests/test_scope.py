"""scope.Scope — the immutable, manifest-backed tenant boundary."""
import dataclasses

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


def test_legacy_reader_rejects_cross_scope_entity_before_disk_access(tmp_path):
    write_vault(tmp_path, entities_yaml("alpha", "beta"))
    beta_inbox = tmp_path / "beta/00-inbox/active"
    beta_inbox.mkdir(parents=True)
    (beta_inbox / "marker.md").write_text(
        "---\ntitle: beta marker\nsub: triage\n---\nbeta body\n",
        encoding="utf-8",
    )

    with pytest.raises(CrossScopeError):
        read_inbox(Scope(tmp_path, "alpha"), "beta")


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
