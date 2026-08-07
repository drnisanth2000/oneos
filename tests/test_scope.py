"""scope.Scope — the tenant boundary. Every path resolution goes through it."""
import pytest

from app.scope import Scope


def test_current_entity_defaults_to_none(tmp_path):
    assert Scope(tmp_path).current_entity() is None


def test_set_and_read_current_entity(tmp_path):
    scope = Scope(tmp_path)
    scope.set_current_entity("acme")
    assert scope.current_entity() == "acme"


def test_resolve_stays_under_root(tmp_path):
    scope = Scope(tmp_path)
    assert scope.resolve("acme", "07-finance") == tmp_path / "acme" / "07-finance"


def test_bundle_path_rejects_traversal(tmp_path):
    scope = Scope(tmp_path)
    for bad in ("..", "a/b", "", ".", "x\\y"):
        with pytest.raises(ValueError):
            scope.bundle_path(bad)


def test_system_path(tmp_path):
    scope = Scope(tmp_path)
    assert scope.system_path("entities.yaml") == tmp_path / "_system" / "entities.yaml"
