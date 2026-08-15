"""Canonical, read-only classification destination resolution."""
from pathlib import Path

import pytest

from app.destinations import (
    BlockMismatch,
    ClassificationDestination,
    InvalidModule,
    InvalidSourceLeaf,
    InvalidSub,
    UnsafeDestinationPath,
    resolve_classification_destination,
)
from app.scope import CrossScopeError, Scope
from tests.conftest import write_vault


DESTINATION_ARCHETYPES = """
version: "2.0"
flags:
  special: "Enables specialized destinations"
modules:
  00-inbox: {block: system}
  11-library: {block: govern}
  12-frozen: {block: govern, lifecycle_pattern: false}
  zz-extra: {block: growth, requires_flag: special}
submodules:
  00-inbox:
    triage: {name: Triage}
  11-library:
    reference: {name: Reference}
    special-reference: {name: Special reference, flag: special}
  zz-extra:
    specialized: {name: Specialized, flag: special}
"""


def _entity_tree(root: Path, entity: str) -> tuple[tuple[str, str, str], ...]:
    entity_root = root / entity
    entries = []
    for path in entity_root.rglob("*"):
        relative = path.relative_to(entity_root).as_posix()
        if path.is_symlink():
            entries.append((relative, "symlink", path.readlink().as_posix()))
        elif path.is_dir():
            entries.append((relative, "directory", ""))
        else:
            entries.append((relative, "file", ""))
    return tuple(sorted(entries))


def _assert_rejection_is_read_only(root: Path, error: type[Exception], call) -> None:
    before = _entity_tree(root, "alpha")
    with pytest.raises(error):
        call()
    assert _entity_tree(root, "alpha") == before


@pytest.fixture
def destination_vault(tmp_path) -> Path:
    root = write_vault(
        tmp_path,
        """version: "1.0"
entities:
  alpha: {label: Alpha, flags: []}
  enabled: {label: Enabled, flags: [special]}
""",
        DESTINATION_ARCHETYPES,
    )
    for entity, modules in {
        "alpha": ("00-inbox", "11-library", "12-frozen"),
        "enabled": ("00-inbox", "11-library", "12-frozen", "zz-extra"),
    }.items():
        for module in modules:
            (root / entity / module / "active").mkdir(parents=True)
        (root / entity / "00-inbox" / "active" / "receipt.md").write_text(
            "receipt\n", encoding="utf-8"
        )
    return root


def test_resolver_derives_canonical_registered_sub_destination(destination_vault):
    scope = Scope(destination_vault, "alpha")
    item = scope.resolve("00-inbox", "active", "receipt.md")

    result = resolve_classification_destination(
        scope, item, module="11-library", sub="reference", claimed_block="govern"
    )

    assert result == ClassificationDestination(
        entity="alpha",
        module="11-library",
        sub="reference",
        block="govern",
        src="alpha/00-inbox/active/receipt.md",
        dst="alpha/11-library/active/receipt.md",
        path=destination_vault / "alpha/11-library/active/receipt.md",
    )


def test_resolver_allows_module_general_destination(destination_vault):
    scope = Scope(destination_vault, "alpha")
    result = resolve_classification_destination(
        scope,
        scope.resolve("00-inbox", "active", "receipt.md"),
        module="11-library",
        sub="",
    )
    assert result.sub is None
    assert result.block == "govern"


@pytest.mark.parametrize(
    ("module", "sub", "error"),
    [
        ("missing", "reference", InvalidModule),
        ("zz-extra", "specialized", InvalidModule),
        ("11-library", "missing", InvalidSub),
        ("11-library", "triage", InvalidSub),
        ("11-library", " reference", InvalidSub),
        ("11-library", "reference\nstatus: approved", InvalidSub),
    ],
)
def test_resolver_rejects_noncanonical_taxonomy(
    destination_vault, module, sub, error
):
    scope = Scope(destination_vault, "alpha")
    _assert_rejection_is_read_only(
        destination_vault,
        error,
        lambda: resolve_classification_destination(
            scope,
            scope.resolve("00-inbox", "active", "receipt.md"),
            module=module,
            sub=sub,
        ),
    )


def test_resolver_allows_flag_enabled_module_and_sub(destination_vault):
    scope = Scope(destination_vault, "enabled")

    result = resolve_classification_destination(
        scope,
        scope.resolve("00-inbox", "active", "receipt.md"),
        module="zz-extra",
        sub="specialized",
        claimed_block="growth",
    )

    assert result.module == "zz-extra"
    assert result.sub == "specialized"
    assert result.block == "growth"


def test_resolver_rejects_on_disk_module_inactive_for_bound_entity(
    destination_vault,
):
    scope = Scope(destination_vault, "alpha")
    (destination_vault / "alpha" / "zz-extra" / "active").mkdir(parents=True)

    _assert_rejection_is_read_only(
        destination_vault,
        InvalidModule,
        lambda: resolve_classification_destination(
            scope,
            scope.resolve("00-inbox", "active", "receipt.md"),
            module="zz-extra",
            sub=None,
        ),
    )


def test_resolver_rejects_sub_disabled_by_flag_on_active_module(
    destination_vault,
):
    scope = Scope(destination_vault, "alpha")

    _assert_rejection_is_read_only(
        destination_vault,
        InvalidSub,
        lambda: resolve_classification_destination(
            scope,
            scope.resolve("00-inbox", "active", "receipt.md"),
            module="11-library",
            sub="special-reference",
        ),
    )


def test_resolver_allows_sub_enabled_by_flag_on_active_module(destination_vault):
    scope = Scope(destination_vault, "enabled")

    result = resolve_classification_destination(
        scope,
        scope.resolve("00-inbox", "active", "receipt.md"),
        module="11-library",
        sub="special-reference",
        claimed_block="govern",
    )

    assert result.module == "11-library"
    assert result.sub == "special-reference"
    assert result.block == "govern"


@pytest.mark.parametrize(
    "leaf",
    [
        "../receipt.md",
        "nested/receipt.md",
        r"..\receipt.md",
        "/receipt.md",
        ".",
        "..",
        "receipt.txt",
        " receipt.md",
        "receipt.md\n",
    ],
)
def test_resolver_rejects_noncanonical_source_leaf(destination_vault, leaf):
    scope = Scope(destination_vault, "alpha")
    _assert_rejection_is_read_only(
        destination_vault,
        InvalidSourceLeaf,
        lambda: resolve_classification_destination(
            scope,
            scope.resolve("00-inbox", "active") / leaf,
            module="11-library",
            sub="reference",
        ),
    )


def test_resolver_rejects_forged_block(destination_vault):
    scope = Scope(destination_vault, "alpha")
    _assert_rejection_is_read_only(
        destination_vault,
        BlockMismatch,
        lambda: resolve_classification_destination(
            scope,
            scope.resolve("00-inbox", "active", "receipt.md"),
            module="11-library",
            sub="reference",
            claimed_block="growth",
        ),
    )


def test_resolver_rejects_declared_active_module_missing_from_disk(destination_vault):
    scope = Scope(destination_vault, "alpha")
    (destination_vault / "alpha" / "11-library").rename(
        destination_vault / "alpha" / "saved-library"
    )
    _assert_rejection_is_read_only(
        destination_vault,
        UnsafeDestinationPath,
        lambda: resolve_classification_destination(
            scope,
            scope.resolve("00-inbox", "active", "receipt.md"),
            module="11-library",
            sub="reference",
        ),
    )


def test_resolver_rejects_module_symlink(destination_vault):
    scope = Scope(destination_vault, "alpha")
    module = destination_vault / "alpha" / "11-library"
    module.rename(destination_vault / "alpha" / "real-library")
    module.symlink_to("real-library", target_is_directory=True)
    _assert_rejection_is_read_only(
        destination_vault,
        UnsafeDestinationPath,
        lambda: resolve_classification_destination(
            scope,
            scope.resolve("00-inbox", "active", "receipt.md"),
            module="11-library",
            sub="reference",
        ),
    )


@pytest.mark.parametrize("shape", ("absent", "file", "symlink"))
def test_resolver_rejects_non_directory_or_redirected_active(destination_vault, shape):
    scope = Scope(destination_vault, "alpha")
    active = destination_vault / "alpha" / "11-library" / "active"
    active.rmdir()
    if shape == "file":
        active.write_text("not a directory\n", encoding="utf-8")
    elif shape == "symlink":
        replacement = destination_vault / "alpha" / "replacement-active"
        replacement.mkdir()
        active.symlink_to(replacement, target_is_directory=True)

    _assert_rejection_is_read_only(
        destination_vault,
        UnsafeDestinationPath,
        lambda: resolve_classification_destination(
            scope,
            scope.resolve("00-inbox", "active", "receipt.md"),
            module="11-library",
            sub="reference",
        ),
    )


def test_resolver_rejects_existing_destination_leaf_symlink(destination_vault):
    scope = Scope(destination_vault, "alpha")
    destination = destination_vault / "alpha" / "11-library" / "active" / "receipt.md"
    destination.symlink_to("other.md")
    _assert_rejection_is_read_only(
        destination_vault,
        UnsafeDestinationPath,
        lambda: resolve_classification_destination(
            scope,
            scope.resolve("00-inbox", "active", "receipt.md"),
            module="11-library",
            sub="reference",
        ),
    )


def test_resolver_rejects_module_without_active_lifecycle(destination_vault):
    scope = Scope(destination_vault, "alpha")
    _assert_rejection_is_read_only(
        destination_vault,
        InvalidModule,
        lambda: resolve_classification_destination(
            scope,
            scope.resolve("00-inbox", "active", "receipt.md"),
            module="12-frozen",
            sub=None,
        ),
    )


def test_resolver_allows_missing_canonical_source_when_not_required(destination_vault):
    scope = Scope(destination_vault, "alpha")

    result = resolve_classification_destination(
        scope,
        scope.resolve("00-inbox", "active", "missing.md"),
        module="11-library",
        sub=None,
        require_source=False,
    )

    assert result.src == "alpha/00-inbox/active/missing.md"
    assert result.dst == "alpha/11-library/active/missing.md"


@pytest.mark.parametrize("require_source", (True, False), ids=("required", "optional"))
def test_resolver_rejects_redirected_source_leaf(destination_vault, require_source):
    scope = Scope(destination_vault, "alpha")
    source = destination_vault / "alpha" / "00-inbox" / "active" / "receipt.md"
    source.unlink()
    (source.parent / "target.md").write_text("redirect target\n", encoding="utf-8")
    source.symlink_to("target.md")

    _assert_rejection_is_read_only(
        destination_vault,
        InvalidSourceLeaf,
        lambda: resolve_classification_destination(
            scope,
            source,
            module="11-library",
            sub="reference",
            require_source=require_source,
        ),
    )


@pytest.mark.parametrize("part", ("00-inbox", "active"))
def test_resolver_rejects_redirected_inbox_lifecycle(destination_vault, part):
    scope = Scope(destination_vault, "alpha")
    inbox = destination_vault / "alpha" / "00-inbox"
    redirected = inbox if part == "00-inbox" else inbox / "active"
    saved = destination_vault / "alpha" / f"saved-{part}"
    redirected.rename(saved)
    redirected.symlink_to(saved, target_is_directory=True)
    source = destination_vault / "alpha" / "00-inbox" / "active" / "receipt.md"

    _assert_rejection_is_read_only(
        destination_vault,
        UnsafeDestinationPath,
        lambda: resolve_classification_destination(
            scope,
            source,
            module="11-library",
            sub="reference",
            require_source=False,
        ),
    )


def test_resolver_rejects_malformed_source_when_not_required(destination_vault):
    scope = Scope(destination_vault, "alpha")
    _assert_rejection_is_read_only(
        destination_vault,
        InvalidSourceLeaf,
        lambda: resolve_classification_destination(
            scope,
            scope.resolve("00-inbox", "active") / "nested/missing.md",
            module="11-library",
            sub=None,
            require_source=False,
        ),
    )


def test_resolver_rejects_entity_root_symlink(destination_vault):
    scope = Scope(destination_vault, "alpha")
    item = scope.resolve("00-inbox", "active", "receipt.md")
    entity = destination_vault / "alpha"
    entity.rename(destination_vault / "real-alpha")
    entity.symlink_to("real-alpha", target_is_directory=True)

    _assert_rejection_is_read_only(
        destination_vault,
        CrossScopeError,
        lambda: resolve_classification_destination(
            scope, item, module="11-library", sub="reference"
        ),
    )
