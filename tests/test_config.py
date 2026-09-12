from pathlib import Path

import pytest

from app.config import ENV_VAULT, VaultRootUnavailable, vault_root


def test_configured_vault_that_disappears_raises_safe_typed_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    missing = tmp_path / "configured-root-that-moved"
    monkeypatch.setenv(ENV_VAULT, str(missing))

    with pytest.raises(VaultRootUnavailable) as raised:
        vault_root()

    assert str(raised.value) == "configured vault root is unavailable"
    assert str(missing) not in str(raised.value)


def test_configured_vault_replaced_at_the_same_path_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    configured = tmp_path / "configured-root"
    configured.mkdir()
    monkeypatch.setenv(ENV_VAULT, str(configured))

    assert vault_root() == configured

    configured.rename(tmp_path / "original-root")
    configured.mkdir()

    with pytest.raises(VaultRootUnavailable, match=r"^configured vault root is unavailable$"):
        vault_root()


def test_unset_vault_has_a_typed_configuration_error_for_readiness(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv(ENV_VAULT, raising=False)

    with pytest.raises(VaultRootUnavailable) as raised:
        vault_root()

    assert ENV_VAULT in str(raised.value)


def _startup_catalog(tmp_path, monkeypatch):
    from app.config import build_catalog
    from tests.conftest import write_vault, entities_yaml

    root = write_vault(tmp_path / "first", entities_yaml("alpha"))
    monkeypatch.setenv(ENV_VAULT, str(root))
    import app.main as main
    catalog = build_catalog()
    monkeypatch.setattr(main, "catalog", catalog)
    return main, catalog


def test_current_catalog_reuses_cache_only_for_the_same_configured_root(tmp_path, monkeypatch):
    from app.config import build_scope
    from tests.conftest import write_vault, entities_yaml

    main, original = _startup_catalog(tmp_path, monkeypatch)
    assert main.current_catalog() is original
    # Same-root manifest caching is an existing independent contract.
    (original.root / "_system/entities.yaml").unlink()
    assert main.current_catalog() is original
    second = write_vault(tmp_path / "second", entities_yaml("bravo"))
    monkeypatch.setenv(ENV_VAULT, str(second))
    scope = build_scope("bravo")
    current = main.current_catalog()
    assert current.root == scope.root == second
    assert [entity.slug for entity in current.entities] == ["bravo"]
    assert current is not original


def test_current_catalog_refuses_replaced_root_despite_populated_cache(tmp_path, monkeypatch):
    main, original = _startup_catalog(tmp_path, monkeypatch)
    original.root.rename(tmp_path / "moved")
    original.root.mkdir()
    with pytest.raises(VaultRootUnavailable):
        main.current_catalog()


def test_current_catalog_refuses_unconfigured_root_despite_populated_cache(tmp_path, monkeypatch):
    main, _ = _startup_catalog(tmp_path, monkeypatch)
    monkeypatch.delenv(ENV_VAULT)
    with pytest.raises(VaultRootUnavailable):
        main.current_catalog()


def test_current_catalog_reload_stays_bound_to_the_root_it_validated(tmp_path, monkeypatch):
    from app import config
    from tests.conftest import write_vault, entities_yaml

    main, original = _startup_catalog(tmp_path, monkeypatch)
    second = write_vault(tmp_path / "second", entities_yaml("bravo"))
    monkeypatch.setenv(ENV_VAULT, str(second))

    def selected_then_changed():
        validated = vault_root()
        monkeypatch.setenv(ENV_VAULT, str(original.root))
        return validated

    monkeypatch.setattr(config, "vault_root", selected_then_changed)
    current = main.current_catalog()
    assert current.root == second
    assert [entity.slug for entity in current.entities] == ["bravo"]
