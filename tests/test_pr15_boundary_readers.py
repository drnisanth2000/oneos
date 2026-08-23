"""PR #15 must-fix 2: UnicodeDecodeError / OSError normalization at required
registry and proposal readers.

Each of these readers checked `is_file()` then called `read_text()`,
converting only `yaml.YAMLError`. Between the existence check and the read,
the file can vanish (`OSError`) or turn out not to be valid UTF-8
(`UnicodeDecodeError`) — both already escape raw today and reach the operator
as `E-UNKNOWN`. This is a pure boundary-conversion fix (design §5): the type
changes, not the fatality — each of these conditions already ends the request
unhandled.

`app/vault.py`'s `_load_yaml` matters most: it backs `bundles()`, which the
full-page error renderer retries for the sidebar, so an unconverted OSError
there can produce an **empty 500** (the error page itself fails to render).
"""
from __future__ import annotations

import contextlib
import os

import pytest

from tests.conftest import write_vault
from app.console_errors import describe

ENTITIES = 'version: "1.0"\nentities:\n  demo: {label: Demo, flags: []}\n'


def _scope(tmp_path):
    from app.scope import Scope

    write_vault(tmp_path, ENTITIES)
    (tmp_path / "demo").mkdir(exist_ok=True)
    return Scope(tmp_path, "demo")


@contextlib.contextmanager
def _deny_read(path):
    """Make `path.read_text()` raise `PermissionError` (an `OSError`
    subclass) while `path.is_file()` keeps returning True, so the reader
    reaches its `read_text()` call rather than its absent-file branch.

    Skipped when running as root: a process with `CAP_DAC_OVERRIDE` ignores
    the permission bit entirely, which would make the assertion depend on the
    environment rather than the code under test (PR #15 must-fix 9's own
    reasoning, applied here for the same reason)."""
    if os.geteuid() == 0:
        pytest.skip("permission bits have no effect for root (CAP_DAC_OVERRIDE)")
    path.chmod(0)
    try:
        yield
    finally:
        path.chmod(0o644)


# --- app/vault.py: _load_yaml -------------------------------------------


def test_vault_load_yaml_oserror_becomes_config(tmp_path):
    from app.entities import EntityCatalog
    from app.vault import DestinationRegistryError, Vault

    write_vault(tmp_path, ENTITIES)
    vault = Vault(EntityCatalog.load(tmp_path))
    with _deny_read(tmp_path / "_system" / "archetypes.yaml"):
        with pytest.raises(DestinationRegistryError) as raised:
            vault._load_yaml("archetypes.yaml")
        assert describe(raised.value).code == "E-CONFIG"


def test_vault_load_yaml_unicode_decode_error_becomes_config(tmp_path):
    from app.entities import EntityCatalog
    from app.vault import DestinationRegistryError, Vault

    write_vault(tmp_path, ENTITIES)
    vault = Vault(EntityCatalog.load(tmp_path))
    (tmp_path / "_system" / "archetypes.yaml").write_bytes(b"\xff\xfe not utf-8")

    with pytest.raises(DestinationRegistryError) as raised:
        vault._load_yaml("archetypes.yaml")
    assert describe(raised.value).code == "E-CONFIG"


# --- app/classifier.py: _load --------------------------------------------


def test_classifier_load_oserror_becomes_config(tmp_path):
    from app.classifier import Classifier
    from app.entities import EntityCatalog
    from app.vault import DestinationRegistryError, Vault

    write_vault(tmp_path, ENTITIES)
    vault = Vault(EntityCatalog.load(tmp_path))
    rules_dir = tmp_path / "_system" / "classifier"
    rules_dir.mkdir(parents=True)
    rules = rules_dir / "rules.yaml"
    rules.write_text("rules: []\n", encoding="utf-8")

    with _deny_read(rules):
        with pytest.raises(DestinationRegistryError) as raised:
            Classifier(vault)
        assert describe(raised.value).code == "E-CONFIG"


def test_classifier_load_unicode_decode_error_becomes_config(tmp_path):
    from app.classifier import Classifier
    from app.entities import EntityCatalog
    from app.vault import DestinationRegistryError, Vault

    write_vault(tmp_path, ENTITIES)
    vault = Vault(EntityCatalog.load(tmp_path))
    rules_dir = tmp_path / "_system" / "classifier"
    rules_dir.mkdir(parents=True)
    (rules_dir / "rules.yaml").write_bytes(b"\xff\xfe not utf-8")

    with pytest.raises(DestinationRegistryError) as raised:
        Classifier(vault)
    assert describe(raised.value).code == "E-CONFIG"


# --- app/registry.py: _count_workspaces and products_for -----------------


def test_count_workspaces_oserror_becomes_config(tmp_path):
    from app.registry import _count_workspaces
    from app.vault import DestinationRegistryError

    scope = _scope(tmp_path)
    ws = tmp_path / "_system" / "workspaces.yaml"
    ws.write_text("workspaces: []\n", encoding="utf-8")

    with _deny_read(ws):
        with pytest.raises(DestinationRegistryError) as raised:
            _count_workspaces(scope, "product", "anything")
        assert describe(raised.value).code == "E-CONFIG"


def test_count_workspaces_unicode_decode_error_becomes_config(tmp_path):
    from app.registry import _count_workspaces
    from app.vault import DestinationRegistryError

    scope = _scope(tmp_path)
    (tmp_path / "_system" / "workspaces.yaml").write_bytes(b"\xff\xfe not utf-8")

    with pytest.raises(DestinationRegistryError) as raised:
        _count_workspaces(scope, "product", "anything")
    assert describe(raised.value).code == "E-CONFIG"


def test_products_for_oserror_becomes_config(tmp_path):
    from app.registry import products_for
    from app.vault import DestinationRegistryError

    scope = _scope(tmp_path)
    products = tmp_path / "_system" / "products.yaml"
    products.write_text("products: {}\n", encoding="utf-8")

    with _deny_read(products):
        with pytest.raises(DestinationRegistryError) as raised:
            products_for(scope)
        assert describe(raised.value).code == "E-CONFIG"


def test_products_for_unicode_decode_error_becomes_config(tmp_path):
    from app.registry import products_for
    from app.vault import DestinationRegistryError

    scope = _scope(tmp_path)
    (tmp_path / "_system" / "products.yaml").write_bytes(b"\xff\xfe not utf-8")

    with pytest.raises(DestinationRegistryError) as raised:
        products_for(scope)
    assert describe(raised.value).code == "E-CONFIG"


# --- app/registry.py: get_delete_proposal --------------------------------


def test_get_delete_proposal_oserror_becomes_unreadable(tmp_path):
    from app.outbox import UnreadableProposalRecord
    from app.registry import get_delete_proposal

    scope = _scope(tmp_path)
    proposal_id = "20260815T090703-" + "ab" * 16
    outbox = tmp_path / "demo" / "outbox"
    outbox.mkdir(parents=True)
    path = outbox / f"{proposal_id}.yaml"
    path.write_text("action: delete\n", encoding="utf-8")

    with _deny_read(path):
        with pytest.raises(UnreadableProposalRecord) as raised:
            get_delete_proposal(scope, proposal_id)
        assert describe(raised.value).code == "E-UNREADABLE"


def test_get_delete_proposal_unicode_decode_error_becomes_unreadable(tmp_path):
    from app.outbox import UnreadableProposalRecord
    from app.registry import get_delete_proposal

    scope = _scope(tmp_path)
    proposal_id = "20260815T090703-" + "cd" * 16
    outbox = tmp_path / "demo" / "outbox"
    outbox.mkdir(parents=True)
    path = outbox / f"{proposal_id}.yaml"
    path.write_bytes(b"\xff\xfe not utf-8")

    with pytest.raises(UnreadableProposalRecord) as raised:
        get_delete_proposal(scope, proposal_id)
    assert describe(raised.value).code == "E-UNREADABLE"
