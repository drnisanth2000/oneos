from collections import Counter

from app.cutover_locations import REWRITE_LOCATIONS, location_keys, locations_for_axis
from app.identifiers import AXES


def test_every_axis_has_at_least_one_location():
    for axis in AXES:
        assert locations_for_axis(axis)


def test_no_file_kind_and_field_pair_appears_under_two_axes():
    pairs = Counter(
        (location.file_kind, location.field) for location in REWRITE_LOCATIONS
    )
    assert [pair for pair, count in pairs.items() if count > 1] == []


def test_product_axis_never_claims_a_workspace_id():
    assert ("workspaces", "id") not in {
        (item.file_kind, item.field) for item in locations_for_axis("product")
    }


def test_workspace_axis_owns_the_workspace_id():
    assert ("workspaces", "id") in {
        (item.file_kind, item.field) for item in locations_for_axis("workspace")
    }


def test_members_id_and_workspaces_id_are_distinct_pairs():
    member_pairs = {
        (item.file_kind, item.field) for item in locations_for_axis("member")
    }
    assert ("members", "id") in member_pairs
    assert ("workspaces", "id") not in member_pairs


def test_action_policy_rewrites_both_halves_of_the_fail_open_rule():
    assert {
        item.field
        for item in locations_for_axis("entity")
        if item.file_kind == "action-policy"
    } == {"paths", "except"}


def test_location_keys_are_stable_identifiers_for_dispositions():
    assert "entity:front-matter:entity" in location_keys()
    assert "workspace:workspaces:id" in location_keys()
    assert "product:workspaces:id" not in location_keys()
