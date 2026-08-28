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


def test_unset_vault_remains_a_startup_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv(ENV_VAULT, raising=False)

    with pytest.raises(RuntimeError) as raised:
        vault_root()

    assert not isinstance(raised.value, VaultRootUnavailable)
