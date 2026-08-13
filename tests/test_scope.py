"""scope.Scope — the immutable, manifest-backed tenant boundary."""
import dataclasses
import inspect

import pytest

from app.entities import (
    EntityCatalog,
    EntityDefinition,
    EntityManifestError,
    EntitySelectionError,
    RecipientConfigurationError,
)
from app.inbox import read_inbox
from app.ingest.adapters.email import process_email
from app.ingest.adapters.folder import process_drop
from app.ingest.base import commit_inbox_item, find_tracked_receipt, prepare_inbox_item
from app.outbox import approve, load_proposals, reject
from app.registry import execute_delete, propose_delete, reference_count
from app.scope import CrossScopeError, Scope
from tests.conftest import entities_yaml, write_vault


def test_catalog_preserves_manifest_order_and_public_entity_fields(tmp_path):
    write_vault(tmp_path, entities_yaml("beta", "alpha"))

    catalog = EntityCatalog.load(tmp_path)

    assert catalog.entities == (
        EntityDefinition(slug="beta", label="Beta", flags=(), email_addresses=()),
        EntityDefinition(slug="alpha", label="Alpha", flags=(), email_addresses=()),
    )
    assert tuple(field.name for field in dataclasses.fields(EntityDefinition)) == (
        "slug",
        "label",
        "flags",
        "email_addresses",
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
        EntityDefinition(slug="alpha", label="alpha", flags=(), email_addresses=()),
    )


def test_manifest_normalizes_recipient_addresses_case_insensitively(tmp_path):
    write_vault(tmp_path, entities_yaml(
        "alpha", ingest={"alpha": [" Intake-Alpha@Example.Invalid "]}
    ))
    catalog = EntityCatalog.load(tmp_path)
    assert catalog.entity_for_recipient("intake-alpha@example.invalid") == "alpha"
    assert catalog.entities[0].email_addresses == ("intake-alpha@example.invalid",)


@pytest.mark.parametrize("value", ["", "not-an-address", "a@", "@example.invalid"])
def test_manifest_rejects_malformed_recipient_address(tmp_path, value):
    write_vault(tmp_path, entities_yaml("alpha", ingest={"alpha": [value]}))
    with pytest.raises(RecipientConfigurationError):
        EntityCatalog.load(tmp_path)


def test_manifest_rejects_duplicate_normalized_address_ownership(tmp_path):
    write_vault(tmp_path, entities_yaml(
        "alpha", "beta", ingest={
            "alpha": ["shared@example.invalid"],
            "beta": ["SHARED@example.invalid"],
        },
    ))
    with pytest.raises(RecipientConfigurationError, match="duplicate ownership"):
        EntityCatalog.load(tmp_path)


def test_manifest_deduplicates_repeated_addresses_owned_by_one_entity(tmp_path):
    write_vault(tmp_path, entities_yaml(
        "alpha", ingest={
            "alpha": ["shared@example.invalid", "SHARED@example.invalid"]
        },
    ))
    catalog = EntityCatalog.load(tmp_path)
    assert catalog.entities[0].email_addresses == ("shared@example.invalid",)
    assert catalog.entity_for_recipient("shared@example.invalid") == "alpha"


@pytest.mark.parametrize("ingest", ["[]", "false", '"email"'])
def test_manifest_rejects_non_mapping_ingest_configuration(tmp_path, ingest):
    manifest = f'version: "1.0"\nentities:\n  alpha:\n    flags: []\n    ingest: {ingest}\n'
    write_vault(tmp_path, manifest)
    with pytest.raises(RecipientConfigurationError):
        EntityCatalog.load(tmp_path)


@pytest.mark.parametrize("addresses", ["{}", "false", '"address@example.invalid"'])
def test_manifest_rejects_non_list_email_addresses(tmp_path, addresses):
    manifest = (
        'version: "1.0"\nentities:\n  alpha:\n    flags: []\n'
        f'    ingest:\n      email_addresses: {addresses}\n'
    )
    write_vault(tmp_path, manifest)
    with pytest.raises(RecipientConfigurationError):
        EntityCatalog.load(tmp_path)


def test_entity_without_ingest_configuration_remains_selectable(tmp_path):
    write_vault(tmp_path, entities_yaml("alpha"))
    catalog = EntityCatalog.load(tmp_path)
    assert catalog.entities[0].email_addresses == ()
    assert catalog.entity_for_recipient("unknown@example.invalid") is None
    assert Scope(tmp_path, "alpha").current_entity() == "alpha"


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


def test_inbox_interface_has_one_identity_authority(tmp_path):
    write_vault(tmp_path, entities_yaml("alpha", "beta"))
    assert tuple(inspect.signature(read_inbox).parameters) == ("scope",)


def test_entity_sensitive_interfaces_take_only_bound_scope():
    checks = (
        read_inbox,
        load_proposals,
        approve,
        reject,
        reference_count,
        propose_delete,
        execute_delete,
        prepare_inbox_item,
        find_tracked_receipt,
        commit_inbox_item,
        process_email,
        process_drop,
    )

    for function in checks:
        parameters = inspect.signature(function).parameters
        assert "entity" not in parameters
        assert "scope" in parameters


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
