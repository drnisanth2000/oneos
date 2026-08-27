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


import textwrap

from app.cutover_locations import (
    rewrite_front_matter_field,
    rewrite_mapping_key,
    rewrite_path_head,
    rewrite_policy_path_heads,
    rewrite_yaml_path_head_field,
    rewrite_yaml_value_field,
)


def test_front_matter_rewrite_matches_only_the_exact_whole_value():
    text = textwrap.dedent(
        """\
        ---
        entity: ab
        title: ab is a common word
        ---

        The ab pattern is discussed here, and abx is not ab.
        """
    )
    result = rewrite_front_matter_field(text, "entity", "ab", "ab-entity")
    assert "entity: ab-entity\n" in result
    assert "title: ab is a common word" in result
    assert "The ab pattern is discussed here, and abx is not ab." in result


def test_front_matter_rewrite_ignores_a_value_that_merely_contains_the_term():
    text = "---\nentity: abx\n---\n"
    assert rewrite_front_matter_field(text, "entity", "ab", "ab-entity") == text


def test_front_matter_rewrite_ignores_body_occurrences_of_the_field_name():
    text = "---\nentity: zz\n---\n\nentity: ab\n"
    assert rewrite_front_matter_field(text, "entity", "ab", "ab-entity") == text


def test_path_head_rewrite_replaces_only_the_first_component():
    assert rewrite_path_head("ab/00-inbox/ab.md", "ab", "ab-entity") == (
        "ab-entity/00-inbox/ab.md"
    )
    assert rewrite_path_head("zz/ab/note.md", "ab", "ab-entity") == "zz/ab/note.md"
    assert rewrite_path_head("abx/note.md", "ab", "ab-entity") == "abx/note.md"
    assert rewrite_path_head("ab", "ab", "ab-entity") == "ab-entity"


def test_yaml_path_head_field_rewrites_only_the_named_plain_scalar():
    text = (
        "src: ab/00-inbox/active/x.md\n"
        "dst: zz/ab/active/x.md\n"
        'note: "src: ab/this-is-prose"\n'
    )

    result = rewrite_yaml_path_head_field(text, "src", "ab", "ab-entity")

    assert "src: ab-entity/00-inbox/active/x.md" in result
    assert "dst: zz/ab/active/x.md" in result
    assert 'note: "src: ab/this-is-prose"' in result


def test_yaml_path_head_field_preserves_every_unrelated_byte():
    text = 'src: ab/x.md\nopaque: "keep: [x]"  # exact\n'

    result = rewrite_yaml_path_head_field(text, "src", "ab", "ab-entity")

    assert result == 'src: ab-entity/x.md\nopaque: "keep: [x]"  # exact\n'


def test_yaml_value_field_rewrite_matches_the_exact_value():
    text = "workspaces:\n  - {id: ab, product: ab, kind: product}\n"
    result = rewrite_yaml_value_field(text, "product", "ab", "ab-product")
    assert "product: ab-product" in result
    assert "id: ab," in result


def test_yaml_value_field_rewrite_does_not_match_a_scalar_prefix():
    text = "members:\n  ab:\n    - {id: ab note, label: Keep}\n"

    assert rewrite_yaml_value_field(text, "id", "ab", "ab-member") == text


def test_yaml_value_field_rewrite_does_not_match_text_inside_another_scalar():
    text = 'note: "the text id: ab is explanatory"\n'

    assert rewrite_yaml_value_field(text, "id", "ab", "ab-member") == text


def test_yaml_value_field_rewrite_accepts_block_and_flow_entries():
    text = "members:\n  ab:\n    - id: ab\n    - {id: ab, label: A}\n"

    result = rewrite_yaml_value_field(text, "id", "ab", "ab-member")

    assert result.count("id: ab-member") == 2


def test_mapping_key_rewrite_matches_at_the_given_indent():
    text = "products:\n  zz:\n    ab:\n      label: A\n"
    result = rewrite_mapping_key(text, "ab", "ab-product", indent=4)
    assert "    ab-product:" in result
    assert "  zz:" in result


POLICY = textwrap.dedent(
    """\
    version: 1.0
    default: deny
    description: "ab is mentioned here and must not change"
    actors:
      hermes:
        allow:
          - {action: read, paths: ["ab/**"], except: ["ab/.sensitive/**"]}
          - {action: write, paths: ["ab/00-inbox/**"]}
        deny:
          - {paths: [".sensitive/**"]}
    """
)


def test_policy_rewrite_touches_paths_and_except_only():
    result = rewrite_policy_path_heads(POLICY, "ab", "ab-entity")

    assert '"ab-entity/**"' in result
    assert '"ab-entity/.sensitive/**"' in result
    assert '"ab-entity/00-inbox/**"' in result
    assert '"ab/**"' not in result
    assert '"ab/.sensitive/**"' not in result


def test_policy_rewrite_leaves_other_quoted_strings_alone():
    result = rewrite_policy_path_heads(POLICY, "ab", "ab-entity")

    assert 'description: "ab is mentioned here and must not change"' in result


def test_policy_rewrite_does_not_treat_paths_text_inside_a_description_as_a_key():
    text = 'description: \'example paths: ["ab/**"] is prose\'\n' + POLICY

    result = rewrite_policy_path_heads(text, "ab", "ab-entity")

    assert 'example paths: ["ab/**"] is prose' in result


def test_policy_rewrite_leaves_a_non_matching_path_head_alone():
    result = rewrite_policy_path_heads(POLICY, "ab", "ab-entity")

    assert '".sensitive/**"' in result
