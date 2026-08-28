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
    rewrite_root_scalar,
    registry_entry_scalar_spans,
    root_scalar_spans,
    rewrite_conventions_member_references,
    rewrite_registry_entry_scalar,
    rewrite_front_matter_field,
    rewrite_mapping_key,
    rewrite_members_comment_references,
    rewrite_path_head,
    rewrite_policy_path_heads,
    rewrite_system_entity_references,
    rewrite_system_product_references,
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


def test_conventions_member_references_rewrite_only_owned_inline_code():
    text = (
        "Known members: `m7`, `x8`; a note tagged `member: m7`.\n"
        "Ordinary m7 prose stays unchanged.\n"
    )

    result = rewrite_conventions_member_references(text, "m7", "m7-member")

    assert result == (
        "Known members: `m7-member`, `x8`; "
        "a note tagged `member: m7-member`.\n"
        "Ordinary m7 prose stays unchanged.\n"
    )


def test_conventions_member_references_rewrite_a_fenced_yaml_scalar():
    text = (
        "```yaml\n"
        "member: m7        # registry reference\n"
        "nested:\n"
        "  member: m7\n"
        "```\n"
        "member: m7 remains ordinary outside the fence.\n"
    )

    result = rewrite_conventions_member_references(text, "m7", "m7-member")

    assert result == (
        "```yaml\n"
        "member: m7-member        # registry reference\n"
        "nested:\n"
        "  member: m7\n"
        "```\n"
        "member: m7 remains ordinary outside the fence.\n"
    )


def test_members_comment_rewrites_only_an_explicit_member_reference():
    text = (
        "members:\n"
        "  ab:\n"
        "    # Example `member: m7`; the code token `m7` and prose m7 stay.\n"
        "    - {id: m7}\n"
    )

    result = rewrite_members_comment_references(text, "m7", "m7-member")

    assert result == (
        "members:\n"
        "  ab:\n"
        "    # Example `member: m7-member`; the code token `m7` and prose m7 stay.\n"
        "    - {id: m7}\n"
    )


def test_members_comment_does_not_rewrite_a_hash_inside_a_yaml_scalar():
    text = (
        "members:\n"
        "  ab:\n"
        "    - {id: x8, label: \"# Example `member: m7`\"}\n"
    )

    assert rewrite_members_comment_references(text, "m7", "m7-member") == text


def test_system_product_references_require_an_inline_qualifier_pair():
    text = (
        "Compact `ab`/q7 and spaced `ab` / q7 references migrate.\n"
        "Documented shorthand `brand`/q7 also migrates.\n"
        "Unquoted brand/q7 and shell cd q7 remain unchanged.\n"
    )

    result = rewrite_system_product_references(text, "q7", "q7-product")

    assert result == (
        "Compact `ab`/q7-product and spaced `ab` / q7-product references migrate.\n"
        "Documented shorthand `brand`/q7-product also migrates.\n"
        "Unquoted brand/q7 and shell cd q7 remain unchanged.\n"
    )


def test_system_entity_references_require_a_registered_product_pair():
    text = (
        "Compact `ab`/q7 and spaced `ab` / q7 references migrate.\n"
        "Unregistered `ab`/x9 and ordinary ab prose remain unchanged.\n"
    )

    result = rewrite_system_entity_references(
        text, frozenset({"q7"}), "ab", "ab-entity"
    )

    assert result == (
        "Compact `ab-entity`/q7 and spaced `ab-entity` / q7 references migrate.\n"
        "Unregistered `ab`/x9 and ordinary ab prose remain unchanged.\n"
    )


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


def test_supported_system_document_references_are_typed_but_prose_is_advisory(
    tmp_path: Path,
):
    system = tmp_path / "_system"
    docs = system / "docs"
    docs.mkdir(parents=True)
    (system / "entities.yaml").write_text(
        "entities:\n  ab-entity:\n    label: A\n", encoding="utf-8"
    )
    (system / "members.yaml").write_text(
        "members:\n"
        "  ab-entity:\n"
        "    # A note tagged `member: m7` is explicit.\n"
        "    - {id: m7-member}\n",
        encoding="utf-8",
    )
    (system / "conventions-v2.1-additions.md").write_text(
        "Known member: `m7`.\nOrdinary m7 prose stays advisory.\n",
        encoding="utf-8",
    )
    (docs / "guide.md").write_text(
        "Product pair: `ab` / q7.\nOrdinary q7 prose stays advisory.\n",
        encoding="utf-8",
    )
    mappings = ADVISORY_MAPPINGS[:3] + (
        Mapping(axis="workspace", old="q7", new="q7-workspace"),
    )

    found = advisory_occurrences(tmp_path, mappings)

    assert {(item.path, item.line, item.axis, item.old) for item in found} == {
        ("_system/conventions-v2.1-additions.md", 2, "member", "m7"),
        ("_system/docs/guide.md", 2, "product", "q7"),
        ("_system/docs/guide.md", 2, "workspace", "q7"),
    }


def test_fenced_yaml_member_and_shorthand_product_are_typed(
    tmp_path: Path,
):
    system = tmp_path / "_system"
    docs = system / "docs"
    docs.mkdir(parents=True)
    (system / "entities.yaml").write_text(
        "entities:\n  ab-entity:\n    label: A\n", encoding="utf-8"
    )
    (system / "products.yaml").write_text(
        "products:\n  ab-entity:\n    q7:\n      label: Q\n", encoding="utf-8"
    )
    (system / "conventions-v2.1-additions.md").write_text(
        "```yaml\nmember: m7  # registry reference\n```\n",
        encoding="utf-8",
    )
    (docs / "guide.md").write_text(
        "Documented pair: `brand` / q7.\n", encoding="utf-8"
    )
    mappings = (
        Mapping(axis="product", old="q7", new="q7-product"),
        Mapping(axis="member", old="m7", new="m7-member"),
        Mapping(axis="workspace", old="q7", new="q7-workspace"),
    )

    found = advisory_occurrences(tmp_path, mappings)

    assert found == []


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


def test_supported_system_document_references_are_scoped_residuals(
    tmp_path: Path,
):
    system = tmp_path / "_system"
    docs = system / "docs"
    docs.mkdir(parents=True)
    (system / "entities.yaml").write_text(
        "entities:\n  ab-entity:\n    label: A\n", encoding="utf-8"
    )
    (system / "members.yaml").write_text(
        "members:\n"
        "  ab-entity:\n"
        "    # Example `member: m7`.\n"
        "    - {id: m7-member}\n",
        encoding="utf-8",
    )
    (system / "conventions-v2.1-additions.md").write_text(
        "Known member: `m7`.\n", encoding="utf-8"
    )
    (docs / "guide.md").write_text(
        "Product pair: `ab`/q7.\n", encoding="utf-8"
    )
    mappings = (
        Mapping(axis="entity", old="ab", new="ab-entity"),
        Mapping(axis="product", old="q7", new="q7-product"),
        Mapping(axis="member", old="m7", new="m7-member"),
    )

    found = scoped_residuals(tmp_path, mappings)

    assert {(item.location, item.path, item.old) for item in found} == {
        (
            "member:system-doc:member-code-reference",
            "_system/conventions-v2.1-additions.md",
            "m7",
        ),
        (
            "member:members:comment-member-reference",
            "_system/members.yaml",
            "m7",
        ),
        (
            "entity:system-doc:entity-product-entity",
            "_system/docs/guide.md",
            "ab",
        ),
        (
            "product:system-doc:entity-product-reference",
            "_system/docs/guide.md",
            "q7",
        ),
    }


def test_fenced_yaml_member_and_shorthand_product_are_scoped_residuals(
    tmp_path: Path,
):
    system = tmp_path / "_system"
    docs = system / "docs"
    docs.mkdir(parents=True)
    (system / "entities.yaml").write_text(
        "entities:\n  ab-entity:\n    label: A\n", encoding="utf-8"
    )
    (system / "products.yaml").write_text(
        "products:\n  ab-entity:\n    q7-product:\n      label: Q\n",
        encoding="utf-8",
    )
    (system / "conventions-v2.1-additions.md").write_text(
        "```yaml\nmember: m7  # registry reference\n```\n",
        encoding="utf-8",
    )
    (docs / "guide.md").write_text(
        "Documented pair: `brand` / q7.\n", encoding="utf-8"
    )
    mappings = (
        Mapping(axis="product", old="q7", new="q7-product"),
        Mapping(axis="member", old="m7", new="m7-member"),
    )

    found = scoped_residuals(tmp_path, mappings)

    assert {(item.location, item.path, item.old) for item in found} == {
        (
            "member:system-doc:member-code-reference",
            "_system/conventions-v2.1-additions.md",
            "m7",
        ),
        (
            "product:system-doc:entity-product-reference",
            "_system/docs/guide.md",
            "q7",
        ),
    }


def test_old_system_document_pair_remains_a_residual_after_registries_migrate(
    tmp_path: Path,
):
    system = tmp_path / "_system"
    docs = system / "docs"
    docs.mkdir(parents=True)
    (system / "entities.yaml").write_text(
        "entities:\n  ab-entity:\n    label: A\n", encoding="utf-8"
    )
    (system / "products.yaml").write_text(
        "products:\n  ab-entity:\n    q7-product:\n      label: Q\n",
        encoding="utf-8",
    )
    (docs / "guide.md").write_text(
        "Product pair: `ab` / q7.\n", encoding="utf-8"
    )
    mappings = (
        Mapping(axis="entity", old="ab", new="ab-entity"),
        Mapping(axis="product", old="q7", new="q7-product"),
    )

    found = scoped_residuals(tmp_path, mappings)

    assert {(item.location, item.path, item.old) for item in found} == {
        (
            "entity:system-doc:entity-product-entity",
            "_system/docs/guide.md",
            "ab",
        ),
        (
            "product:system-doc:entity-product-reference",
            "_system/docs/guide.md",
            "q7",
        ),
    }


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


def test_advisory_scan_itself_refuses_an_unreadable_file(tmp_path: Path, monkeypatch):
    """The advisory scan's own read must fail closed, independently.

    `_typed_token_spans` reads the same file first, so its guard masks this
    one in the end-to-end test. Neutralising that first reader isolates the
    advisory scan's own read: a file it cannot decode must stop the scan, never
    be skipped, because a gate cannot pass on evidence it never saw.
    """
    import app.cutover_locations as locations

    monkeypatch.setattr(locations, "_typed_token_spans", lambda *a, **k: set())
    (tmp_path / "note.md").write_bytes(b"\xff\xfe not utf-8 \xff")

    with pytest.raises(UnreadableFile, match="the advisory scan cannot pass"):
        advisory_occurrences(tmp_path, ADVISORY_MAPPINGS[:1])


def test_a_typed_span_is_typed_for_every_axis_not_only_its_own(tmp_path: Path):
    """One literal on two axes: neither typed span is advisory for the other.

    Typedness belongs to the exact token span, not to the axis that happens to
    be scanning it. Keying the exclusion by axis makes a product-typed `q7`
    advisory for a workspace `q7` occupying the same span — and since a typed
    rewrite later removes it, the post-migration identity check would refuse
    every class-3 cutover the design explicitly permits.
    """
    same_literal = (
        Mapping(axis="product", old="q7", new="q7-product"),
        Mapping(axis="workspace", old="q7", new="q7-workspace"),
    )
    system = tmp_path / "_system"
    system.mkdir()
    (system / "products.yaml").write_text(
        "products:\n  ab:\n    q7:\n      label: Q\n", encoding="utf-8"
    )
    (system / "workspaces.yaml").write_text(
        "workspaces:\n  - {id: q7, product: q7, kind: product}\n", encoding="utf-8"
    )

    found = advisory_occurrences(tmp_path, same_literal)

    assert found == [], "a typed span was misreported on another axis"


def test_untyped_same_literal_prose_remains_advisory_per_axis(tmp_path: Path):
    """The exclusion must narrow to typed spans, never to whole lines."""
    same_literal = (
        Mapping(axis="product", old="q7", new="q7-product"),
        Mapping(axis="workspace", old="q7", new="q7-workspace"),
    )
    (tmp_path / "note.md").write_text("the q7 pattern\n", encoding="utf-8")

    found = advisory_occurrences(tmp_path, same_literal)

    assert {(item.axis, item.old) for item in found} == {
        ("product", "q7"),
        ("workspace", "q7"),
    }


def test_mapping_key_rewrite_respects_exact_indent_across_blank_lines(tmp_path: Path):
    """`\\s{n}` matches newlines; only exact spaces separate nesting depths.

    `indent` is the sole mechanism keeping an entity-group key and a product
    key apart inside `products.yaml` — the one file where two axes share a key
    space — so a depth-4 pass reaching a depth-2 key is a cross-axis rewrite.
    """
    text = "products:\n\n\n  ab:\n    label: A\n"

    result = rewrite_mapping_key(text, "ab", "ab-product", indent=4)

    assert "  ab:" in result, "a depth-4 pass rewrote a depth-2 key"
    assert "ab-product" not in result


def test_mapping_key_span_respects_exact_indent(tmp_path: Path):
    from app.cutover_locations import _mapping_key_span

    assert _mapping_key_span("  ab:", "ab", 4) is None
    assert _mapping_key_span("    ab:", "ab", 4) is not None


def test_malformed_front_matter_is_a_hard_failure(tmp_path: Path):
    """Every sibling reader in this gate raises; this one must not return {}."""
    from app.cutover_locations import _front_matter_values

    with pytest.raises(UnreadableFile):
        _front_matter_values("---\nentity: [unclosed\n---\n\nbody\n")


def test_former_slugs_exemption_is_span_specific_not_whole_line(tmp_path: Path):
    """A whole-line skip hides every other token on the line, including a
    comment the owner must still disposition."""
    system = tmp_path / "_system"
    system.mkdir()
    (system / "entities.yaml").write_text(
        "entities:\n  ab-entity:\n    former_slugs: [ab]  # ab is our ledger code\n",
        encoding="utf-8",
    )

    found = advisory_occurrences(tmp_path, ADVISORY_MAPPINGS[:1])

    assert [(item.path, item.old) for item in found] == [
        ("_system/entities.yaml", "ab")
    ], "the comment token was hidden by a whole-line exemption"


BLOCK_POLICY = """\
version: 1.0
default: deny
description: "ab is mentioned here and must not change"
actors:
  h:
    allow:
      - action: read
        paths: ["ab/**"]
        except:
          - "ab/.sensitive/**"
      - action: write
        paths:
          - "ab/00-inbox/**"
    deny:
      - paths: [".sensitive/**"]
"""


def test_policy_rewrite_handles_block_sequences(tmp_path: Path):
    """A block `except:` is legal YAML and must move with its `paths:`.

    Rewriting one half and not the other converts a deny into an allow — the
    BUILD §4 fail-open.
    """
    result = rewrite_policy_path_heads(BLOCK_POLICY, "ab", "ab-entity")

    assert '"ab-entity/**"' in result
    assert '"ab-entity/.sensitive/**"' in result, "block except was left behind"
    assert '"ab-entity/00-inbox/**"' in result, "block paths was left behind"
    assert '"ab/' not in result


def test_policy_rewrite_leaves_unrelated_quoted_text_and_formatting_alone(
    tmp_path: Path,
):
    result = rewrite_policy_path_heads(BLOCK_POLICY, "ab", "ab-entity")

    assert 'description: "ab is mentioned here and must not change"' in result
    assert '".sensitive/**"' in result
    # No reserialisation: every original line survives except the rewritten
    # path heads.
    assert result.count("\n") == BLOCK_POLICY.count("\n")
    assert "action: read" in result and "actors:" in result


def test_residual_gate_reports_a_block_style_stale_except(tmp_path: Path):
    """The gate must parse the policy itself, not reuse the writer's matcher.

    A gate built from the writer's own regex is blind wherever the writer is
    blind, which is how a half-rewritten rule passed both gates.
    """
    system = tmp_path / "_system" / "scripts"
    system.mkdir(parents=True)
    (system / "action-policy.yaml").write_text(
        "actors:\n  h:\n    allow:\n      - action: read\n"
        '        paths: ["ab-entity/**"]\n'
        "        except:\n"
        '          - "ab/.sensitive/**"\n',
        encoding="utf-8",
    )

    found = scoped_residuals(tmp_path, (Mapping(axis="entity", old="ab", new="ab-entity"),))

    assert any(item.location == "entity:action-policy:except" for item in found), (
        "the gate did not see a stale block-style except"
    )


def test_residual_gate_fails_closed_on_malformed_policy(tmp_path: Path):
    system = tmp_path / "_system" / "scripts"
    system.mkdir(parents=True)
    (system / "action-policy.yaml").write_text(
        "actors:\n  h:\n    allow:\n      - paths: [unclosed\n", encoding="utf-8"
    )

    with pytest.raises(UnreadableFile):
        scoped_residuals(tmp_path, (Mapping(axis="entity", old="ab", new="ab-entity"),))


ALIASED_POLICY = """\
description: &shared "ab/**"
actors:
  h:
    allow:
      - action: read
        paths: [*shared]
        except: ["ab/.sensitive/**"]
"""


def test_policy_writer_refuses_yaml_aliases(tmp_path: Path):
    """An alias resolves to the anchor's node, whose marks point elsewhere.

    Rewriting at those offsets edits the *anchor* — here an unrelated
    `description:` — while `paths:` keeps pointing at the same value. The
    parsed gate then sees a value that looks migrated and reports nothing, so
    the edit is silent and outside `paths`/`except` entirely.
    """
    with pytest.raises(UnreadableFile, match="anchor or alias"):
        rewrite_policy_path_heads(ALIASED_POLICY, "ab", "ab-entity")


def test_policy_writer_never_edits_the_anchored_field(tmp_path: Path):
    try:
        result = rewrite_policy_path_heads(ALIASED_POLICY, "ab", "ab-entity")
    except UnreadableFile:
        result = ALIASED_POLICY
    assert 'description: &shared "ab/**"' in result, (
        "an unrelated anchored field was rewritten"
    )
    assert "ab-entity" not in result


def test_residual_gate_refuses_yaml_aliases(tmp_path: Path):
    """The gate must refuse independently, not inherit the writer's check."""
    system = tmp_path / "_system" / "scripts"
    system.mkdir(parents=True)
    (system / "action-policy.yaml").write_text(ALIASED_POLICY, encoding="utf-8")

    with pytest.raises(UnreadableFile, match="anchor or alias"):
        scoped_residuals(
            tmp_path, (Mapping(axis="entity", old="ab", new="ab-entity"),)
        )


def test_policy_without_anchors_is_still_accepted(tmp_path: Path):
    """The refusal must be specific to anchors, not a blanket rejection."""
    result = rewrite_policy_path_heads(BLOCK_POLICY, "ab", "ab-entity")

    assert '"ab-entity/.sensitive/**"' in result


SCALAR_PATHS_POLICY = """\
actors:
  h:
    allow:
      - action: read
        paths: "ab/**"
        except: ["ab/.sensitive/**"]
"""

SCALAR_EXCEPT_POLICY = """\
actors:
  h:
    allow:
      - action: read
        paths: ["ab/**"]
        except: "ab/.sensitive/**"
"""

NON_STRING_ITEM_POLICY = """\
actors:
  h:
    allow:
      - action: read
        paths:
          - {glob: "ab/**"}
        except: ["ab/.sensitive/**"]
"""


@pytest.mark.parametrize(
    ("policy", "diagnosis"),
    [
        (SCALAR_PATHS_POLICY, "expected a sequence"),
        (SCALAR_EXCEPT_POLICY, "expected a sequence"),
        (NON_STRING_ITEM_POLICY, "expected a scalar"),
    ],
    ids=["scalar-paths", "scalar-except", "non-string-item"],
)
def test_policy_writer_refuses_a_wrongly_shaped_field(policy: str, diagnosis: str):
    """A shape the writer cannot rewrite must refuse, never be skipped.

    Silently ignoring it leaves the rule stale and, because the gate ignores
    the same shape, unreported — a rule still naming the retired entity in a
    committed cutover.
    """
    # The diagnosis matters: a disabled sequence check falls through to the
    # item check, which still raises. Matching only "shape" would accept the
    # wrong guard doing the work.
    with pytest.raises(UnreadableFile, match=diagnosis):
        rewrite_policy_path_heads(policy, "ab", "ab-entity")


@pytest.mark.parametrize(
    ("policy", "diagnosis"),
    [
        (SCALAR_PATHS_POLICY, "expected a sequence"),
        (SCALAR_EXCEPT_POLICY, "expected a sequence"),
        (NON_STRING_ITEM_POLICY, "expected a scalar"),
    ],
    ids=["scalar-paths", "scalar-except", "non-string-item"],
)
def test_residual_gate_refuses_a_wrongly_shaped_field(
    tmp_path: Path, policy: str, diagnosis: str
):
    """The gate refuses independently, not by inheriting the writer's check."""
    system = tmp_path / "_system" / "scripts"
    system.mkdir(parents=True)
    (system / "action-policy.yaml").write_text(policy, encoding="utf-8")

    with pytest.raises(UnreadableFile, match=diagnosis):
        scoped_residuals(
            tmp_path, (Mapping(axis="entity", old="ab", new="ab-entity"),)
        )


EMPTY_PATHS_POLICY = """\
actors:
  h:
    allow:
      - action: read
        paths:
        except: ["ab/.sensitive/**"]
"""


def test_the_policy_sequence_guard_is_what_refuses_an_empty_field():
    """An empty `paths:` isolates the sequence guard from the item guard.

    With a scalar field the item guard also raises, so a message match
    cannot say which one refused. An empty field decodes to `None`: the
    item loop cannot run, so only the sequence guard can refuse — and
    without it the writer returns the file unchanged and the stale rule
    ships.
    """
    with pytest.raises(UnreadableFile, match="expected a sequence"):
        rewrite_policy_path_heads(EMPTY_PATHS_POLICY, "ab", "ab-entity")


def test_the_policy_item_guard_refuses_before_any_span_is_recorded():
    """The locator must refuse, not hand a non-scalar span to the rewriter.

    Exercised through `rewrite_policy_path_heads` the missing guard surfaces
    as an `AttributeError` from the rewriter, which proves only that
    something downstream crashed. Calling the locator alone leaves the item
    guard as the only thing that can refuse.
    """
    from app.cutover_locations import policy_path_scalars

    with pytest.raises(UnreadableFile, match="expected a scalar"):
        policy_path_scalars(NON_STRING_ITEM_POLICY)


def test_the_gate_item_guard_refuses_before_any_value_is_recorded():
    """The gate's own item guard, isolated from the caller that crashes."""
    from app.cutover_locations import _load_policy_document, _policy_path_values

    with pytest.raises(UnreadableFile, match="expected a scalar"):
        _policy_path_values(_load_policy_document(NON_STRING_ITEM_POLICY))


NESTED_FRONT_MATTER = """\
---
entity: ab
source:
  member: m7
related:
  - member: m7
---

the ab word
"""


def test_front_matter_writer_owns_top_level_scalars_only():
    """Nested structures are outside the cutover's front-matter ownership.

    The writer matched any indent and tolerated a `- ` prefix, so a nested
    provenance block was rewritten — bytes the owner never approved, because
    the typed-span suppression removed them from the advisory report too.
    """
    result = rewrite_front_matter_field(NESTED_FRONT_MATTER, "member", "m7", "m7-member")

    assert "  member: m7\n" in result, "a nested mapping value was rewritten"
    assert "  - member: m7\n" in result, "a list entry value was rewritten"
    assert "m7-member" not in result


def test_front_matter_writer_still_rewrites_a_top_level_scalar():
    text = "---\nentity: ab\nmember: m7\n---\n\nbody\n"

    result = rewrite_front_matter_field(text, "member", "m7", "m7-member")

    assert "member: m7-member\n" in result


def test_nested_front_matter_values_remain_advisory(tmp_path: Path):
    """What the writer does not own must stay visible to the owner.

    Suppressing a nested value as "typed" while no writer rewrites it and no
    gate inspects it is the worst of both: unreviewed and unverifiable.
    """
    (tmp_path / "note.md").write_text(NESTED_FRONT_MATTER, encoding="utf-8")

    found = advisory_occurrences(tmp_path, ADVISORY_MAPPINGS[2:3])

    assert [(item.path, item.old, item.line) for item in found] == [
        ("note.md", "m7", 4),
        ("note.md", "m7", 6),
    ], "nested front-matter values were suppressed as typed"


def test_top_level_front_matter_value_is_still_typed(tmp_path: Path):
    """The genuine typed field must not become advisory noise."""
    (tmp_path / "note.md").write_text(
        "---\nentity: ab\n---\n\nbody\n", encoding="utf-8"
    )

    assert advisory_occurrences(tmp_path, ADVISORY_MAPPINGS[:1]) == []


def test_a_hash_without_whitespace_is_part_of_the_value_not_a_comment():
    """YAML starts a comment only after whitespace.

    `entity: ab#suffix` is the single scalar `ab#suffix`, so the old
    identifier is not the whole value and the field must not be rewritten —
    doing so corrupts an unrelated value into `ab-entity#suffix`.
    """
    text = "---\nentity: ab#suffix\n---\n\nbody\n"

    result = rewrite_front_matter_field(text, "entity", "ab", "ab-entity")

    assert result == text, "a `#` inside a scalar was treated as a comment"


def test_a_hash_without_whitespace_stays_advisory(tmp_path: Path):
    """Not owned by the writer, so it must remain visible to the owner."""
    (tmp_path / "note.md").write_text(
        "---\nentity: ab#suffix\n---\n\nbody\n", encoding="utf-8"
    )

    found = advisory_occurrences(tmp_path, ADVISORY_MAPPINGS[:1])

    assert [(item.path, item.old, item.line) for item in found] == [
        ("note.md", "ab", 2)
    ], "a value the writer cannot own was suppressed as typed"


def test_a_hash_after_whitespace_is_still_a_comment():
    """The distinction is whitespace, not the presence of `#`."""
    text = "---\nentity: ab #suffix\n---\n\nbody\n"

    result = rewrite_front_matter_field(text, "entity", "ab", "ab-entity")

    assert "entity: ab-entity #suffix\n" in result


MULTILINE_POLICY = """\
actors:
  h:
    allow:
      - action: read
        paths:
          - "ab/**
             continued"
        except: ["ab/.sensitive/**"]
"""


def test_policy_writer_refuses_a_multiline_scalar():
    """A scalar spanning lines cannot be edited by one line/column span.

    The node marks report a single start line, so an in-place edit at those
    offsets would corrupt the continuation rather than rewrite the value.
    """
    with pytest.raises(UnreadableFile, match="multiline"):
        rewrite_policy_path_heads(MULTILINE_POLICY, "ab", "ab-entity")


def test_residual_gate_refuses_a_multiline_scalar(tmp_path: Path):
    system = tmp_path / "_system" / "scripts"
    system.mkdir(parents=True)
    (system / "action-policy.yaml").write_text(MULTILINE_POLICY, encoding="utf-8")

    with pytest.raises(UnreadableFile, match="multiline"):
        scoped_residuals(
            tmp_path, (Mapping(axis="entity", old="ab", new="ab-entity"),)
        )


def test_block_style_policy_paths_are_typed_not_advisory(tmp_path: Path):
    """A supported, rewritable location must not appear in the advisory report.

    Typedness is decided by the same structural parse the writer uses, so a
    block sequence is typed exactly as a flow sequence is.
    """
    system = tmp_path / "_system" / "scripts"
    system.mkdir(parents=True)
    (system / "action-policy.yaml").write_text(
        "actors:\n  h:\n    allow:\n      - action: read\n"
        "        paths:\n          - \"ab/**\"\n"
        "        except:\n          - \"ab/.sensitive/**\"\n",
        encoding="utf-8",
    )

    found = advisory_occurrences(
        tmp_path, (Mapping(axis="entity", old="ab", new="ab-entity"),)
    )

    assert found == [], "a rewritable policy path was reported as advisory"


def test_member_rewrite_is_confined_to_the_registry_entry_id():
    """`id:` inside nested metadata is not the member registry's `id:`.

    A same-named key one level deeper is unrelated data; rewriting it edits
    content the registry schema never described.
    """
    text = "members:\n  ab:\n    - {id: m7, meta: {id: m7}}\n"

    result = rewrite_registry_entry_scalar(text, "members", "id", "m7", "m7-member")

    assert "meta: {id: m7}" in result, "nested metadata was rewritten"
    assert "{id: m7-member," in result


def test_workspace_rewrite_is_confined_to_the_entry_id():
    text = "workspaces:\n  - {id: w7, extra: {id: w7}}\n"

    result = rewrite_registry_entry_scalar(text, "workspaces", "id", "w7", "w7-workspace")

    assert "extra: {id: w7}" in result, "nested metadata was rewritten"
    assert "{id: w7-workspace," in result


def test_nested_same_named_metadata_is_not_typed(tmp_path: Path):
    """What the writer does not own must remain visible to the owner."""
    system = tmp_path / "_system"
    system.mkdir()
    (system / "members.yaml").write_text(
        "members:\n  ab-entity:\n    - {id: m7-member, meta: {id: m7}}\n",
        encoding="utf-8",
    )

    found = advisory_occurrences(tmp_path, ADVISORY_MAPPINGS[2:3])

    assert [(item.path, item.old) for item in found] == [
        ("_system/members.yaml", "m7")
    ], "nested metadata was suppressed as typed"


def test_workspace_entity_rewrite_is_confined_to_the_entry():
    """`entity`/`primary_entity` are entry fields, not nested metadata."""
    text = (
        "workspaces:\n"
        "  - {id: w7, entity: ab, primary_entity: ab, extra: {entity: ab}}\n"
    )

    result = rewrite_registry_entry_scalar(text, "workspaces", "entity", "ab", "ab-entity")
    result = rewrite_registry_entry_scalar(
        result, "workspaces", "primary_entity", "ab", "ab-entity"
    )

    assert "entity: ab-entity," in result
    assert "primary_entity: ab-entity," in result
    assert "extra: {entity: ab}" in result, "nested metadata was rewritten"


def test_nested_proposal_metadata_remains_advisory(tmp_path: Path):
    """Typed spans must match the structural writer's ownership.

    The writer confines itself to the record's root `entity`/`src`/`dst`, so
    nested `meta:` values are neither rewritten nor typed — they must reach
    the owner as advisory, not vanish from both.
    """
    outbox = tmp_path / "ab" / "outbox"
    outbox.mkdir(parents=True)
    (outbox / "p.yaml").write_text(
        "entity: ab\nsrc: ab/a.md\ndst: ab/b.md\n"
        "meta:\n  entity: ab\n  src: ab/x.md\n  dst: ab/y.md\n",
        encoding="utf-8",
    )

    found = advisory_occurrences(
        tmp_path, (Mapping(axis="entity", old="ab", new="ab-entity"),)
    )

    assert [item.line for item in found] == [5, 6, 7], (
        "nested proposal metadata was suppressed as typed"
    )


def test_root_proposal_fields_stay_typed(tmp_path: Path):
    """The genuine root fields must not become advisory noise."""
    outbox = tmp_path / "ab" / "outbox"
    outbox.mkdir(parents=True)
    (outbox / "p.yaml").write_text(
        "entity: ab\nsrc: ab/a.md\ndst: ab/b.md\n", encoding="utf-8"
    )

    assert advisory_occurrences(
        tmp_path, (Mapping(axis="entity", old="ab", new="ab-entity"),)
    ) == []


ALIASED_PROPOSAL = "meta: &shared ab\nentity: *shared\nsrc: ab/a.md\n"
ALIASED_MEMBERS = "members:\n  ab:\n    - {meta: &s m7, id: *s}\n"
ALIASED_WORKSPACES = "workspaces:\n  - {meta: &s w7, id: *s}\n"


def test_root_scalar_spans_refuse_yaml_aliases():
    """An alias resolves to the anchor's node, whose marks point elsewhere.

    Rewriting at those offsets edits the *anchor* — here an unrelated `meta:`
    — while the aliased field keeps its value. Typed-span detection then hides
    the occurrence, so the edit is silent and outside the closed table.
    """
    with pytest.raises(UnreadableFile, match="anchor or alias"):
        root_scalar_spans(ALIASED_PROPOSAL, ("entity", "src", "dst"))


@pytest.mark.parametrize(
    ("text", "container"),
    [(ALIASED_MEMBERS, "members"), (ALIASED_WORKSPACES, "workspaces")],
    ids=["members", "workspaces"],
)
def test_registry_entry_spans_refuse_yaml_aliases(text: str, container: str):
    with pytest.raises(UnreadableFile, match="anchor or alias"):
        registry_entry_scalar_spans(text, container, ("id",))


def test_an_aliased_proposal_is_never_rewritten():
    with pytest.raises(UnreadableFile):
        rewrite_root_scalar(ALIASED_PROPOSAL, "entity", "ab", "ab-entity")


def test_an_aliased_registry_entry_is_never_rewritten():
    with pytest.raises(UnreadableFile):
        rewrite_registry_entry_scalar(
            ALIASED_MEMBERS, "members", "id", "m7", "m7-member"
        )


def test_ordinary_records_still_work_without_anchors():
    """The refusal is specific to anchors, not a blanket rejection."""
    proposal = "entity: ab\nsrc: ab/a.md\ndst: ab/b.md\n"
    assert rewrite_root_scalar(proposal, "entity", "ab", "ab-entity") == (
        "entity: ab-entity\nsrc: ab/a.md\ndst: ab/b.md\n"
    )

    members = "members:\n  ab:\n    - {id: m7}\n"
    assert rewrite_registry_entry_scalar(
        members, "members", "id", "m7", "m7-member"
    ) == "members:\n  ab:\n    - {id: m7-member}\n"
