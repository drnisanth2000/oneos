"""schema.FrontMatter — the shared Pydantic front-matter model (spec §10 step 3).

This is a *refactor* of policy_enforcer.validate_front_matter, not a rewrite:
the verdict must be identical, which means presence-only checking of the
required fields, dispatched by `type`. Stricter value validation would reject
files the enforcer accepts and break spec §11 gate 4 (both validators agree on
100 real files). These tests pin that behaviour.

Instance-agnostic: synthetic front-matter only, no vault slug or path.
"""
import textwrap

from app.schema import validate_file_text, validate_front_matter

STD = textwrap.dedent(
    """\
    ---
    type: note
    title: Example
    entity: demo
    product: null
    status: active
    created: 2026-01-01
    updated: 2026-01-02
    ---
    body text
    """
)

SYSTEM_DOC = textwrap.dedent(
    """\
    ---
    type: system-doc
    title: A convention
    version: 2.0.0
    status: frozen
    created: 2026-01-01
    updated: 2026-01-01
    ---
    body
    """
)


def test_valid_standard_doc():
    ok, problems = validate_file_text(STD)
    assert ok, problems


def test_valid_system_doc_needs_no_entity_or_product():
    ok, problems = validate_file_text(SYSTEM_DOC)
    assert ok, problems


def test_missing_required_field_is_invalid():
    text = STD.replace("entity: demo\n", "")
    ok, problems = validate_file_text(text)
    assert not ok
    assert any("entity" in p for p in problems)


def test_system_doc_missing_version_is_invalid():
    text = SYSTEM_DOC.replace("version: 2.0.0\n", "")
    ok, problems = validate_file_text(text)
    assert not ok
    assert any("version" in p for p in problems)


def test_no_front_matter_block():
    ok, problems = validate_file_text("# just a heading\n")
    assert not ok
    assert any("front-matter" in p for p in problems)


def test_unclosed_front_matter():
    ok, problems = validate_file_text("---\ntype: note\n")
    assert not ok
    assert any("not closed" in p or "closed" in p for p in problems)


def test_invalid_yaml_front_matter():
    ok, problems = validate_file_text("---\ntype: [unclosed\n---\n")
    assert not ok
    assert any("YAML" in p or "yaml" in p for p in problems)


def test_extra_fields_allowed():
    text = STD.replace(
        "updated: 2026-01-02\n",
        "updated: 2026-01-02\nblock: govern\nsub: matters\n"
        "orchestration: human\ncadence: weekly\nmember: nn\n",
    )
    ok, problems = validate_file_text(text)
    assert ok, problems


def test_date_typed_values_pass():
    # YAML parses unquoted dates to date objects; presence-only must accept them.
    ok, problems = validate_file_text(STD)
    assert ok, problems


def test_validate_front_matter_on_mapping_directly():
    ok, problems = validate_front_matter(
        {
            "type": "note",
            "title": "x",
            "entity": "demo",
            "product": None,
            "status": "active",
            "created": "2026-01-01",
            "updated": "2026-01-01",
        }
    )
    assert ok, problems
    ok2, problems2 = validate_front_matter({"type": "note", "title": "x"})
    assert not ok2
    assert any("entity" in p for p in problems2)
