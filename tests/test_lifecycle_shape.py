"""Synthetic lifecycle declarations; planning must never scaffold content."""
from pathlib import Path

import pytest
import yaml

from app.scope import Scope


def fixture(tmp_path, modules=None, flags=None):
    root = tmp_path / "vault"
    (root / "_system").mkdir(parents=True)
    (root / "sample").mkdir()
    modules = modules if modules is not None else {"02-work": {"block": "build"}}
    (root / "_system/entities.yaml").write_text(yaml.safe_dump({"entities": {
        "sample": {"flags": flags or [], "archetype": "preset"}}}))
    (root / "_system/archetypes.yaml").write_text(yaml.safe_dump({
        "flags": {"special": "Optional work"},
        "archetypes": {"preset": {"special": True}}, "modules": modules}))
    for name, spec in modules.items():
        if spec.get("requires_flag") and spec["requires_flag"] not in (flags or []):
            continue
        parent = root / "sample" / name
        parent.mkdir()
        if name == "12-archive":
            (parent / "_templates").mkdir()
        elif spec.get("lifecycle_pattern", True) is True:
            for child in ("_templates", "active", "archive"):
                (parent / child).mkdir()
            (parent / "status.md").write_text("status")
        extensions = spec.get("extensions", [])
        if isinstance(extensions, list):
            for ext in extensions:
                if not isinstance(ext, str) or "/" in ext.rstrip("/") or ext in ("", ".", ".."):
                    continue
                target = parent / ext
                if ext.endswith("/"):
                    target.mkdir(exist_ok=True)
                elif not target.exists():
                    target.write_text("extension")
    return Scope(root, "sample")


def shape_api():
    import app.lifecycle_shape as shape
    return shape


def test_only_missing_immediate_directory_is_proposed_without_writes(tmp_path):
    scope = fixture(tmp_path)
    path = scope.root / "sample/02-work/active"
    path.rmdir()
    shape = shape_api()
    entries = shape.inspect_shape(scope)
    assert [(e.path, e.kind) for e in entries] == [("sample/02-work/active", "directory")]
    assert entries[0].declaration
    assert not path.exists()


def test_archive_exception_is_complete_without_lifecycle_children(tmp_path):
    scope = fixture(tmp_path, {"12-archive": {"block": "store", "lifecycle_pattern": False}})
    shape = shape_api()
    assert shape.inspect_shape(scope) == ()
    assert [(e.path, e.kind) for e in shape.required_shape(scope)] == [
        ("sample", "directory"), ("sample/12-archive", "directory"),
        ("sample/12-archive/_templates", "directory")]
    (scope.root / "sample/12-archive/_templates").rmdir()
    assert len(shape.inspect_shape(scope)) == 1


def test_disabled_flag_selected_nonarchive_retains_root_and_extensions(tmp_path):
    scope = fixture(tmp_path, {"18-optional": {"block": "build", "requires_flag": "special",
        "lifecycle_pattern": False, "extensions": ["records/", "summary.md"]}}, ["special"])
    shape = shape_api()
    assert shape.inspect_shape(scope) == ()
    assert [(e.path, e.kind) for e in shape.required_shape(scope)] == [
        ("sample", "directory"), ("sample/18-optional", "directory"),
        ("sample/18-optional/records", "directory"), ("sample/18-optional/summary.md", "file")]
    (scope.root / "sample/18-optional/records").rmdir()
    assert [e.path for e in shape.inspect_shape(scope)] == ["sample/18-optional/records"]


def test_archetype_never_activates_module(tmp_path):
    scope = fixture(tmp_path, {"18-optional": {"block": "build", "requires_flag": "special"}})
    assert [e.path for e in shape_api().required_shape(scope)] == ["sample"]


@pytest.mark.parametrize("missing", ["sample", "sample/02-work", "sample/02-work/status.md"])
def test_missing_root_or_required_file_refuses_entire_batch(tmp_path, missing):
    import shutil
    scope = fixture(tmp_path)
    (scope.root / "sample/02-work/active").rmdir()
    target = scope.root / missing
    shutil.rmtree(target) if target.is_dir() else target.unlink()
    shape = shape_api()
    with pytest.raises(shape.LifecycleShapeError):
        shape.inspect_shape(scope)


@pytest.mark.parametrize("extension", ["snapshots/", "dashboard.md", "kpis.yaml"])
def test_registry_extensions_preserve_directory_and_file_meaning(tmp_path, extension):
    scope = fixture(tmp_path, {"13-insights": {"block": "learn", "extensions": [extension]}})
    target = scope.root / "sample/13-insights" / extension
    target.rmdir() if target.is_dir() else target.unlink()
    shape = shape_api()
    if extension.endswith("/"):
        assert [e.path for e in shape.inspect_shape(scope)] == ["sample/13-insights/snapshots"]
    else:
        with pytest.raises(shape.LifecycleShapeError):
            shape.inspect_shape(scope)


@pytest.mark.parametrize("value", [None, "false", 0, 1, [], {}])
def test_malformed_lifecycle_boolean_refuses(tmp_path, value):
    scope = fixture(tmp_path, {"02-work": {"block": "build", "lifecycle_pattern": value}})
    shape = shape_api()
    with pytest.raises(shape.LifecycleShapeError):
        shape.required_shape(scope)


@pytest.mark.parametrize("extensions", [None, {}, "snapshots/", [1], [""], ["../"],
    ["nested/child/"], ["/absolute/"], ["bad\\name/"], ["snapshots/", "snapshots/"],
    ["status.md/"], ["active"], ["white space/"], [".sensitive/"]])
def test_malformed_or_conflicting_extensions_refuse(tmp_path, extensions):
    scope = fixture(tmp_path, {"02-work": {"block": "build"}})
    path = scope.root / "_system/archetypes.yaml"
    data = yaml.safe_load(path.read_text())
    data["modules"]["02-work"]["extensions"] = extensions
    path.write_text(yaml.safe_dump(data))
    shape = shape_api()
    with pytest.raises(shape.LifecycleShapeError):
        shape.required_shape(scope)


@pytest.mark.parametrize("relative", ["sample", "sample/02-work", "sample/02-work/active",
    "sample/02-work/status.md", "_system", "_system/entities.yaml", "_system/archetypes.yaml"])
def test_symlinks_at_any_required_boundary_refuse(tmp_path, relative):
    scope = fixture(tmp_path)
    target = scope.root / relative
    moved = tmp_path / "redirected"
    target.rename(moved)
    target.symlink_to(moved, target_is_directory=moved.is_dir())
    shape = shape_api()
    with pytest.raises(shape.LifecycleShapeError):
        shape.inspect_shape(scope)


@pytest.mark.parametrize("relative", ["sample/02-work/active", "sample/02-work/status.md"])
def test_wrong_filesystem_kind_refuses(tmp_path, relative):
    scope = fixture(tmp_path)
    target = scope.root / relative
    if target.is_dir():
        target.rmdir()
        target.write_text("wrong kind")
    else:
        target.unlink()
        target.mkdir()
    shape = shape_api()
    with pytest.raises(shape.LifecycleShapeError):
        shape.inspect_shape(scope)


def test_duplicate_yaml_keys_refuse(tmp_path):
    scope = fixture(tmp_path)
    (scope.root / "_system/archetypes.yaml").write_text(
        "modules:\n  02-work: {block: build}\n  02-work: {block: build, lifecycle_pattern: false}\n")
    shape = shape_api()
    with pytest.raises(shape.LifecycleShapeError):
        shape.required_shape(scope)


@pytest.mark.parametrize("value", [True, False])
def test_named_archive_exception_is_independent_of_lifecycle_boolean(tmp_path, value):
    scope = fixture(tmp_path, {"12-archive": {"block": "store", "lifecycle_pattern": value}})
    assert shape_api().inspect_shape(scope) == ()


def test_disabled_module_root_is_still_required(tmp_path):
    scope = fixture(tmp_path, {"18-optional": {"block": "build", "requires_flag": "special",
                                             "lifecycle_pattern": False}}, ["special"])
    (scope.root / "sample/18-optional").rmdir()
    shape = shape_api()
    with pytest.raises(shape.LifecycleShapeError):
        shape.inspect_shape(scope)


def test_special_file_is_not_a_required_content_file(tmp_path):
    import os
    scope = fixture(tmp_path)
    target = scope.root / "sample/02-work/status.md"
    target.unlink()
    os.mkfifo(target)
    shape = shape_api()
    with pytest.raises(shape.LifecycleShapeError):
        shape.inspect_shape(scope)


def test_root_ancestor_redirection_after_scope_creation_refuses(tmp_path):
    scope = fixture(tmp_path)
    root = scope.root
    moved = root.with_name("moved")
    root.rename(moved)
    root.symlink_to(moved, target_is_directory=True)
    shape = shape_api()
    with pytest.raises(shape.LifecycleShapeError):
        shape.inspect_shape(scope)


def test_registry_updates_are_read_afresh(tmp_path):
    scope = fixture(tmp_path)
    shape = shape_api()
    assert shape.inspect_shape(scope) == ()
    path = scope.root / "_system/archetypes.yaml"
    declaration = yaml.safe_load(path.read_text())
    declaration["modules"]["02-work"]["extensions"] = ["records/"]
    path.write_text(yaml.safe_dump(declaration))
    assert [e.path for e in shape.inspect_shape(scope)] == ["sample/02-work/records"]


def test_malformed_extension_on_inactive_module_refuses_declarations(tmp_path):
    scope = fixture(tmp_path, {"18-optional": {"block": "build", "requires_flag": "special",
                                              "extensions": ["nested/child/"]}})
    shape = shape_api()
    with pytest.raises(shape.LifecycleShapeError):
        shape.required_shape(scope)


@pytest.mark.parametrize('extension', ['active/', 'active', 'archive/', 'archive',
                                       'status.md', 'status.md/'])
def test_archive_extensions_cannot_reintroduce_omitted_lifecycle_children(tmp_path, extension):
    scope = fixture(tmp_path, {'12-archive': {'block': 'store', 'extensions': [extension]}})
    shape = shape_api()
    with pytest.raises(shape.LifecycleShapeError):
        shape.required_shape(scope)


@pytest.mark.parametrize('extension', ['Reports/', 'report.data/', 'outbox/', 'staging/', 'double--dash/', 'trailing-/', 'summary_file/'])
def test_directory_extension_eligibility_matches_proposal_path_contract(tmp_path, extension):
    scope = fixture(tmp_path, {'02-work': {'block': 'build', 'extensions': [extension]}})
    shape = shape_api()
    if extension == 'summary_file/':
        assert shape.inspect_shape(scope) == ()
    else:
        with pytest.raises(shape.LifecycleShapeError):
            shape.required_shape(scope)
