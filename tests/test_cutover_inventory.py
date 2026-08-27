from pathlib import Path

import pytest

from app.cutover_inventory import (
    CollisionError,
    UnmigratableContentError,
    check_collisions,
    require_clean_status,
    require_clean_entities,
    untracked_or_ignored_paths,
)
from app.cutover_manifest import Mapping
from tests.conftest import git_vault


def test_a_new_value_colliding_with_an_existing_identifier_is_refused():
    with pytest.raises(CollisionError, match="existing"):
        check_collisions(
            (Mapping(axis="entity", old="ab", new="ab-entity"),),
            {"entity": {"ab", "ab-entity"}},
        )


def test_duplicate_inputs_on_one_axis_are_refused():
    with pytest.raises(CollisionError, match="duplicate"):
        check_collisions(
            (
                Mapping(axis="entity", old="ab", new="ab-entity"),
                Mapping(axis="entity", old="ab", new="ab-entity"),
            ),
            {"entity": {"ab"}},
        )


def test_one_literal_on_two_axes_is_permitted():
    check_collisions(
        (
            Mapping(axis="entity", old="ab", new="ab-entity"),
            Mapping(axis="product", old="ab", new="ab-product"),
        ),
        {"entity": {"ab"}, "product": {"ab"}},
    )


def test_a_clean_mapping_passes():
    check_collisions(
        (Mapping(axis="entity", old="ab", new="ab-entity"),),
        {"entity": {"ab", "zzzzz"}},
    )


def test_an_ignored_path_under_an_affected_entity_is_reported(tmp_path: Path):
    vault = git_vault(
        tmp_path, {".gitignore": ".sensitive/\n", "ab/00-inbox/note.md": "x\n"}
    )
    (vault / "ab" / ".sensitive").mkdir()
    (vault / "ab" / ".sensitive" / "secret.md").write_text("s\n", encoding="utf-8")

    assert any(".sensitive" in item for item in untracked_or_ignored_paths(vault, "ab"))


def test_an_untracked_path_under_an_affected_entity_is_reported(tmp_path: Path):
    vault = git_vault(tmp_path, {"ab/00-inbox/note.md": "x\n"})
    (vault / "ab" / "stray.md").write_text("s\n", encoding="utf-8")

    assert untracked_or_ignored_paths(vault, "ab") == ["ab/stray.md"]


def test_a_clean_entity_reports_nothing(tmp_path: Path):
    vault = git_vault(tmp_path, {"ab/00-inbox/note.md": "x\n"})

    assert untracked_or_ignored_paths(vault, "ab") == []


def test_require_clean_entities_raises_for_an_affected_entity(tmp_path: Path):
    vault = git_vault(tmp_path, {"ab/00-inbox/note.md": "x\n"})
    (vault / "ab" / "stray.md").write_text("s\n", encoding="utf-8")

    with pytest.raises(UnmigratableContentError):
        require_clean_entities(vault, ["ab"])


def test_require_clean_entities_ignores_an_unaffected_entity(tmp_path: Path):
    vault = git_vault(
        tmp_path, {"ab/00-inbox/note.md": "x\n", "zz/00-inbox/note.md": "y\n"}
    )
    (vault / "zz" / "stray.md").write_text("s\n", encoding="utf-8")

    require_clean_entities(vault, ["ab"])


def test_inventory_requires_a_globally_clean_tracked_and_untracked_status(
    tmp_path: Path,
):
    vault = git_vault(tmp_path, {"ab/00-inbox/note.md": "x\n"})
    (vault / "unrelated.txt").write_text("not approved\n", encoding="utf-8")

    with pytest.raises(UnmigratableContentError, match="clean status"):
        require_clean_status(vault)
