import pytest

from app.identifiers import (
    AXES,
    DATABASE_AXES,
    IDENTIFIER_MINIMUM_LENGTH,
    AxisError,
    map_identifier,
    meets_floor,
    suffix_for_axis,
    validate_mapping_pair,
)


def test_floor_is_one_above_the_audit_long_term_threshold():
    assert IDENTIFIER_MINIMUM_LENGTH == 5


def test_meets_floor_counts_hyphens():
    assert not meets_floor("ab")
    assert not meets_floor("abcd")
    assert meets_floor("abcde")
    assert meets_floor("a-cde")


def test_axes_are_the_four_registry_axes():
    assert AXES == ("entity", "product", "member", "workspace")


def test_database_axes_exclude_entity_and_workspace():
    assert DATABASE_AXES == frozenset({"product", "member"})


def test_suffix_for_each_axis():
    assert suffix_for_axis("entity") == "-entity"
    assert suffix_for_axis("product") == "-product"
    assert suffix_for_axis("member") == "-member"
    assert suffix_for_axis("workspace") == "-workspace"


def test_unknown_axis_is_refused():
    with pytest.raises(AxisError):
        suffix_for_axis("project")
    with pytest.raises(AxisError):
        map_identifier("project", "ab")


def test_mapping_is_deterministic_and_appends_the_axis_suffix():
    assert map_identifier("entity", "ab") == "ab-entity"
    assert map_identifier("workspace", "q7") == "q7-workspace"
    assert map_identifier("entity", "ab") == map_identifier("entity", "ab")


def test_every_output_satisfies_the_floor():
    for axis in AXES:
        assert meets_floor(map_identifier(axis, "a"))


def test_mapping_refuses_an_identifier_that_already_meets_the_floor():
    with pytest.raises(AxisError):
        map_identifier("entity", "abcde")


def test_mapping_refuses_an_already_suffixed_identifier():
    with pytest.raises(AxisError):
        map_identifier("entity", "a-entity")


def test_validate_mapping_pair_accepts_the_deterministic_result():
    validate_mapping_pair("entity", "ab", "ab-entity")


def test_validate_mapping_pair_refuses_a_hand_edited_new_value():
    with pytest.raises(AxisError):
        validate_mapping_pair("entity", "ab", "ab-entity-2")
    with pytest.raises(AxisError):
        validate_mapping_pair("entity", "ab", "ab-product")
    with pytest.raises(AxisError):
        validate_mapping_pair("entity", "ab", "something-else")
