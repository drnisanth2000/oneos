"""Stage B: the five-character floor applies only to four registry axes."""

from __future__ import annotations

import ast
from pathlib import Path
import textwrap

import pytest
import yaml

import app.identifiers as identifiers
from app.action_receipts import (
    ReceiptStoreIntegrityError,
    _head_canonical_roots,
    receipt_relative_path,
)
from app.entities import (
    EntityCatalog,
    EntityDefinition,
    EntityManifestError,
    EntitySelectionError,
)
from app.registry import (
    DestinationRegistryError,
    RegistryError,
    _count_workspaces,
    _remove_scoped_registry_value,
    add_workspace,
    propose_delete,
    products_for,
)
from app.rename import RenameError, plan_rename
from app.scope import Scope
from app.vault import Vault
from tests.conftest import git_entity_vault, git_vault, write_vault


def test_entity_manifest_rejects_a_sub_floor_identifier(tmp_path: Path) -> None:
    write_vault(
        tmp_path,
        'version: "1.0"\nentities:\n  abcd: {label: Short, flags: []}\n',
    )

    with pytest.raises(EntityManifestError, match="invalid slug"):
        EntityCatalog.load(tmp_path)


def test_entity_selection_rechecks_the_floor() -> None:
    catalog = EntityCatalog(
        Path("/synthetic"),
        (EntityDefinition("abcd", "Short", ()),),
    )

    with pytest.raises(EntitySelectionError, match="invalid entity selection"):
        catalog.require("abcd")


def test_receipt_paths_reject_a_sub_floor_entity() -> None:
    proposal_id = "20260828T120000-" + "ab" * 16

    with pytest.raises(ReceiptStoreIntegrityError, match="not canonical"):
        receipt_relative_path("abcd", proposal_id)


def test_offline_receipt_discovery_excludes_a_sub_floor_root(tmp_path: Path) -> None:
    git_vault(tmp_path, {"abcd/outbox/.receipts/.gitkeep": ""})

    assert "abcd" not in _head_canonical_roots(tmp_path)


def test_product_reader_rejects_a_sub_floor_product(tmp_path: Path) -> None:
    vault = git_entity_vault(
        tmp_path,
        ("alpha",),
        {
            "_system/products.yaml": (
                'version: "1.0"\nproducts:\n  alpha:\n'
                "    abcd: {label: Short}\n"
            )
        },
    )

    with pytest.raises(DestinationRegistryError, match="product identifier"):
        products_for(Scope(vault, "alpha"))


@pytest.mark.parametrize(
    ("kind", "registry_bytes"),
    (
        ("product", b"products:\n  alpha:\n    abcd: {label: Short}\n"),
        ("member", b"members:\n  alpha:\n    - {id: abcd, label: Short}\n"),
    ),
)
def test_scoped_registry_delete_rejects_a_sub_floor_value(
    tmp_path: Path, kind: str, registry_bytes: bytes
) -> None:
    vault = git_entity_vault(tmp_path, ("alpha",), {})
    scope = Scope(vault, "alpha")

    with pytest.raises(DestinationRegistryError, match=f"{kind} identifier"):
        _remove_scoped_registry_value(scope, kind, "abcd", registry_bytes)


def test_delete_proposal_refuses_a_sub_floor_registry_value_before_writing(
    tmp_path: Path,
) -> None:
    vault = git_entity_vault(tmp_path, ("alpha",), {})

    with pytest.raises(RegistryError, match="product identifier"):
        propose_delete(Scope(vault, "alpha"), "product", "abcd")

    assert not (vault / "alpha/outbox").exists()


@pytest.mark.parametrize(
    ("field_name", "value", "axis"),
    (
        ("id", "abcd", "workspace"),
        ("entity", "abcd", "entity"),
        ("primary_entity", "abcd", "entity"),
        ("entities", ["abcd"], "entity"),
        ("product", "abcd", "product"),
        ("member", "abcd", "member"),
    ),
)
def test_workspace_reader_rejects_sub_floor_axis_fields(
    tmp_path: Path, field_name: str, value: object, axis: str
) -> None:
    entry = {"id": "space1", "entity": "alpha", "product": "widgetx"}
    entry[field_name] = value
    vault = git_entity_vault(
        tmp_path,
        ("alpha",),
        {
            "_system/workspaces.yaml": yaml.safe_dump(
                {"version": "1.0", "workspaces": [entry]}, sort_keys=False
            )
        },
    )

    with pytest.raises(DestinationRegistryError, match=f"{axis} identifier"):
        _count_workspaces(Scope(vault, "alpha"), "product", "widgetx")


@pytest.mark.parametrize(
    ("field_name", "value", "axis"),
    (
        ("id", "abcd", "workspace"),
        ("entity", "abcd", "entity"),
        ("primary_entity", "abcd", "entity"),
        ("entities", ["abcd"], "entity"),
        ("product", "abcd", "product"),
        ("member", "abcd", "member"),
    ),
)
def test_workspace_writer_refuses_before_writing_sub_floor_axis_fields(
    tmp_path: Path, field_name: str, value: object, axis: str
) -> None:
    vault = git_entity_vault(
        tmp_path,
        ("alpha",),
        {"_system/workspaces.yaml": 'version: "1.0"\nworkspaces:\n'},
    )
    path = vault / "_system/workspaces.yaml"
    before = path.read_bytes()

    entry = {"id": "space1", "entity": "alpha", "kind": "entity"}
    entry[field_name] = value
    with pytest.raises(RegistryError, match=f"{axis} identifier"):
        add_workspace(Scope(vault, "alpha"), entry)

    assert path.read_bytes() == before


@pytest.mark.parametrize("axis", ("entity", "product", "member", "workspace"))
def test_rename_refuses_a_sub_floor_new_registry_identifier(
    tmp_path: Path, axis: str
) -> None:
    trees = {
        "entity": (
            ("sourcevalue",),
            {"sourcevalue/note.md": "---\nentity: sourcevalue\n---\n"},
        ),
        "product": (
            ("alpha",),
            {
                "_system/products.yaml": (
                    "products:\n  alpha:\n    sourcevalue: {label: Source}\n"
                )
            },
        ),
        "member": (
            ("alpha",),
            {
                "_system/members.yaml": (
                    "members:\n  alpha:\n    - {id: sourcevalue, label: Source}\n"
                )
            },
        ),
        "workspace": (
            ("alpha",),
            {
                "_system/workspaces.yaml": (
                    "workspaces:\n  - {id: sourcevalue, entity: alpha}\n"
                )
            },
        ),
    }
    entities, files = trees[axis]
    vault = git_entity_vault(tmp_path, entities, files)

    with pytest.raises(RenameError, match="shorter than five"):
        plan_rename(vault, axis, "sourcevalue", "abcd")


def test_project_rename_keeps_its_existing_grammar_only_boundary(
    tmp_path: Path,
) -> None:
    vault = git_vault(
        tmp_path,
        {
            "alpha/02-pipeline/active/sourceproject/index.md": (
                "---\ntype: project\ntitle: Source\nentity: alpha\n---\n"
            )
        },
    )

    plan = plan_rename(vault, "project", "sourceproject", "abcd")

    assert plan.new == "abcd"


def test_generic_registry_vocabulary_keeps_its_existing_grammar(
    tmp_path: Path,
) -> None:
    archetypes = textwrap.dedent(
        """
        version: "2.0"
        flags: {x: Short flag}
        modules:
          01-core: {block: grow, requires_flag: x}
        submodules:
          01-core:
            ar: {name: Short submodule}
        archetypes: {}
        """
    ).strip()
    write_vault(
        tmp_path,
        'version: "1.0"\nentities:\n  alpha: {label: Alpha, flags: [x]}\n',
        archetypes,
    )

    (bundle,) = Vault(EntityCatalog.load(tmp_path)).bundles()

    assert bundle.flags == ("x",)
    assert bundle.modules[0].block == "grow"


def test_the_public_minimum_length_constant_has_one_definition() -> None:
    definitions: list[tuple[Path, int]] = []
    app_root = Path(__file__).parents[1] / "app"
    for path in sorted(app_root.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if any(
                    isinstance(target, ast.Name)
                    and target.id == "IDENTIFIER_MINIMUM_LENGTH"
                    for target in targets
                ):
                    definitions.append((path.relative_to(app_root), node.lineno))

    assert definitions == [(Path("identifiers.py"), 14)]


def test_floor_consumers_follow_the_shared_constant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_vault(
        tmp_path,
        'version: "1.0"\nentities:\n  abcde: {label: Five, flags: []}\n',
    )
    assert EntityCatalog.load(tmp_path).require("abcde").slug == "abcde"

    monkeypatch.setattr(identifiers, "IDENTIFIER_MINIMUM_LENGTH", 6)

    with pytest.raises(EntityManifestError, match="invalid slug"):
        EntityCatalog.load(tmp_path)
