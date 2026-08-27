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


from pathlib import Path

import pytest

from app.cutover_locations import (
    AdvisoryOccurrence,
    UnreadableFile,
    advisory_occurrences,
    stable_advisory_context,
)
from app.cutover_manifest import Mapping


ADVISORY_MAPPINGS = (
    Mapping(axis="entity", old="ab", new="ab-entity"),
    Mapping(axis="product", old="q7", new="q7-product"),
    Mapping(axis="member", old="m7", new="m7-member"),
    Mapping(axis="workspace", old="w7", new="w7-workspace"),
)


def occurrence(
    path: str,
    line: int,
    axis: str,
    old: str,
    text: str,
    ordinal: int = 1,
) -> AdvisoryOccurrence:
    return AdvisoryOccurrence(
        path=path,
        axis=axis,
        old=old,
        ordinal=ordinal,
        context_sha256=stable_advisory_context(text, ADVISORY_MAPPINGS),
        line=line,
    )


def test_advisory_reports_a_bare_token_outside_the_enumerated_locations(tmp_path: Path):
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "one.md").write_text("the ab pattern\n", encoding="utf-8")

    assert advisory_occurrences(tmp_path, ADVISORY_MAPPINGS[:1]) == [
        occurrence("notes/one.md", 1, "entity", "ab", "the ab pattern")
    ]


def test_advisory_identity_distinguishes_two_tokens_on_one_line(tmp_path: Path):
    (tmp_path / "note.md").write_text("ab and ab\n", encoding="utf-8")

    assert advisory_occurrences(tmp_path, ADVISORY_MAPPINGS[:1]) == [
        occurrence("note.md", 1, "entity", "ab", "ab and ab", ordinal=1),
        occurrence("note.md", 1, "entity", "ab", "ab and ab", ordinal=2),
    ]


def test_advisory_does_not_report_a_migrated_token(tmp_path: Path):
    (tmp_path / "note.md").write_text("entity: ab-entity\n", encoding="utf-8")

    assert advisory_occurrences(tmp_path, ADVISORY_MAPPINGS[:1]) == []


def test_stable_context_ignores_an_approved_typed_value_rewrite():
    before = "entity: ab # q7 remains incidental"
    after = "entity: ab-entity # q7 remains incidental"

    assert stable_advisory_context(before, ADVISORY_MAPPINGS) == (
        stable_advisory_context(after, ADVISORY_MAPPINGS)
    )


def test_advisory_does_not_report_a_longer_token(tmp_path: Path):
    (tmp_path / "note.md").write_text("xabx and cab and abx\n", encoding="utf-8")

    assert advisory_occurrences(tmp_path, ADVISORY_MAPPINGS[:1]) == []


def test_advisory_skips_git_and_binaries(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "note.md").write_text("ab\n", encoding="utf-8")
    (tmp_path / "books.db").write_bytes(b"\x00ab\x00")

    assert advisory_occurrences(tmp_path, ADVISORY_MAPPINGS[:1]) == []


def test_former_slugs_is_exempt_only_in_the_entity_and_product_registries(
    tmp_path: Path,
):
    system = tmp_path / "_system"
    system.mkdir()
    (system / "entities.yaml").write_text(
        "entities:\n  ab-entity:\n    former_slugs: [ab]\n", encoding="utf-8"
    )
    (system / "products.yaml").write_text(
        "products:\n  ab-entity:\n    q7-product:\n      former_slugs: [q7]\n",
        encoding="utf-8",
    )
    (tmp_path / "note.md").write_text("former_slugs: [ab]\n", encoding="utf-8")

    found = advisory_occurrences(tmp_path, ADVISORY_MAPPINGS[:2])

    assert found == [
        occurrence("note.md", 1, "entity", "ab", "former_slugs: [ab]")
    ]


def test_former_slugs_is_not_exempt_in_the_member_registry(tmp_path: Path):
    system = tmp_path / "_system"
    system.mkdir()
    (system / "members.yaml").write_text(
        "members:\n  ab-entity:\n    - {id: m7-member, former_slugs: [m7]}\n",
        encoding="utf-8",
    )

    assert advisory_occurrences(tmp_path, ADVISORY_MAPPINGS[2:3]) == [
        occurrence(
            "_system/members.yaml",
            3,
            "member",
            "m7",
            "    - {id: m7-member, former_slugs: [m7]}",
        )
    ]


def test_an_unreadable_text_file_is_a_hard_failure(tmp_path: Path):
    (tmp_path / "note.md").write_bytes(b"\xff\xfe not utf-8 \xff")

    with pytest.raises(UnreadableFile):
        advisory_occurrences(tmp_path, ADVISORY_MAPPINGS[:1])


def test_typed_registry_front_matter_workspace_policy_and_proposal_lines_are_not_advisory(
    tmp_path: Path,
):
    system = tmp_path / "_system"
    (system / "scripts").mkdir(parents=True)
    (system / "entities.yaml").write_text(
        "entities:\n  ab:\n    label: A\n", encoding="utf-8"
    )
    (system / "products.yaml").write_text(
        "products:\n  ab:\n    q7:\n      label: Q\n", encoding="utf-8"
    )
    (system / "members.yaml").write_text(
        "members:\n  ab:\n    - {id: m7}\n", encoding="utf-8"
    )
    (system / "workspaces.yaml").write_text(
        "workspaces:\n  - {id: w7, entity: ab, product: q7, member: m7}\n",
        encoding="utf-8",
    )
    (system / "scripts" / "action-policy.yaml").write_text(
        'allow:\n  - {paths: ["ab/**"], except: ["ab/.sensitive/**"]}\n',
        encoding="utf-8",
    )
    inbox = tmp_path / "ab" / "00-inbox"
    inbox.mkdir(parents=True)
    (inbox / "note.md").write_text(
        "---\nentity: ab\nproduct: q7\nmember: m7\n---\n\nordinary ab prose\n",
        encoding="utf-8",
    )
    outbox = tmp_path / "ab" / "outbox"
    outbox.mkdir()
    (outbox / "p.yaml").write_text(
        "entity: ab\nsrc: ab/00-inbox/a.md\ndst: ab/09-marketing/a.md\n",
        encoding="utf-8",
    )

    assert advisory_occurrences(tmp_path, ADVISORY_MAPPINGS) == [
        occurrence(
            "ab/00-inbox/note.md", 7, "entity", "ab", "ordinary ab prose"
        )
    ]


def test_a_typed_scalar_does_not_hide_same_axis_prose_on_its_line(tmp_path: Path):
    note = tmp_path / "note.md"
    note.write_text(
        "---\nentity: ab # ab remains ordinary prose\n---\n",
        encoding="utf-8",
    )

    assert advisory_occurrences(tmp_path, ADVISORY_MAPPINGS[:1]) == [
        occurrence(
            "note.md",
            2,
            "entity",
            "ab",
            "entity: ab # ab remains ordinary prose",
        )
    ]


def test_a_symlink_is_scanned_as_link_text_without_following_its_target(tmp_path: Path):
    link = tmp_path / "ab-link"
    link.symlink_to("ab/target")

    assert advisory_occurrences(tmp_path, ADVISORY_MAPPINGS[:1]) == [
        occurrence("ab-link", 1, "entity", "ab", "ab/target")
    ]


from app.cutover_locations import ScopedResidual, scoped_residuals
from app.cutover_manifest import Mapping


def migrated_tree(root: Path) -> None:
    system = root / "_system"
    (system / "scripts").mkdir(parents=True)
    (system / "entities.yaml").write_text(
        "entities:\n  ab-entity:\n    label: A\n", encoding="utf-8"
    )
    (system / "products.yaml").write_text(
        "products:\n  ab-entity:\n    q7-product:\n      label: Q\n", encoding="utf-8"
    )
    (system / "members.yaml").write_text(
        "members:\n  ab-entity:\n    - {id: m7-member}\n", encoding="utf-8"
    )
    (system / "workspaces.yaml").write_text(
        "workspaces:\n  - {id: w7-workspace, entity: ab-entity, product: q7-product}\n",
        encoding="utf-8",
    )
    (system / "scripts" / "action-policy.yaml").write_text(
        'actors:\n  h:\n    allow:\n      - {paths: ["ab-entity/**"], '
        'except: ["ab-entity/.sensitive/**"]}\n',
        encoding="utf-8",
    )
    inbox = root / "ab-entity" / "00-inbox"
    inbox.mkdir(parents=True)
    (inbox / "n.md").write_text(
        "---\nentity: ab-entity\nproduct: q7-product\nmember: m7-member\n---\n\n"
        "the ab word is ordinary prose\n",
        encoding="utf-8",
    )


MAPPINGS = (
    Mapping(axis="entity", old="ab", new="ab-entity"),
    Mapping(axis="product", old="q7", new="q7-product"),
    Mapping(axis="member", old="m7", new="m7-member"),
    Mapping(axis="workspace", old="w7", new="w7-workspace"),
)


def test_a_fully_migrated_tree_has_no_scoped_residual(tmp_path: Path):
    migrated_tree(tmp_path)

    assert scoped_residuals(tmp_path, MAPPINGS) == []


def test_ordinary_prose_containing_an_old_identifier_is_not_a_residual(tmp_path: Path):
    migrated_tree(tmp_path)
    (tmp_path / "ab-entity" / "00-inbox" / "prose.md").write_text(
        "ab ab ab everywhere in the body\n", encoding="utf-8"
    )

    assert scoped_residuals(tmp_path, MAPPINGS) == []


def test_a_missed_front_matter_field_is_a_residual(tmp_path: Path):
    migrated_tree(tmp_path)
    (tmp_path / "ab-entity" / "00-inbox" / "n.md").write_text(
        "---\nentity: ab\n---\n", encoding="utf-8"
    )

    assert ScopedResidual(
        location="entity:front-matter:entity",
        path="ab-entity/00-inbox/n.md",
        old="ab",
    ) in scoped_residuals(tmp_path, MAPPINGS)


def test_a_missed_registry_key_is_a_residual(tmp_path: Path):
    migrated_tree(tmp_path)
    (tmp_path / "_system" / "entities.yaml").write_text(
        "entities:\n  ab:\n    label: A\n", encoding="utf-8"
    )

    assert any(
        item.location == "entity:entities:key" for item in scoped_residuals(tmp_path, MAPPINGS)
    )


def test_a_missed_policy_except_half_is_a_residual(tmp_path: Path):
    migrated_tree(tmp_path)
    (tmp_path / "_system" / "scripts" / "action-policy.yaml").write_text(
        'actors:\n  h:\n    allow:\n      - {paths: ["ab-entity/**"], '
        'except: ["ab/.sensitive/**"]}\n',
        encoding="utf-8",
    )

    assert any(
        item.location == "entity:action-policy:except"
        for item in scoped_residuals(tmp_path, MAPPINGS)
    )


def test_a_surviving_entity_directory_is_a_residual(tmp_path: Path):
    migrated_tree(tmp_path)
    (tmp_path / "ab").mkdir()

    assert any(
        item.location == "entity:vault-root:dirname"
        for item in scoped_residuals(tmp_path, MAPPINGS)
    )


def test_a_missed_workspace_id_is_a_residual(tmp_path: Path):
    migrated_tree(tmp_path)
    (tmp_path / "_system" / "workspaces.yaml").write_text(
        "workspaces:\n  - {id: w7, entity: ab-entity}\n", encoding="utf-8"
    )

    assert any(
        item.location == "workspace:workspaces:id"
        for item in scoped_residuals(tmp_path, MAPPINGS)
    )
