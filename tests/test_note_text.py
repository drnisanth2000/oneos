"""Header-scoped note editor: only status/updated header lines (and the body
when an edited body is submitted) ever change."""
from __future__ import annotations

import pytest

from app import schema
from app.note_text import (
    NoteTextError,
    edit_note,
    normalise_body,
    shown_body,
    split_note,
)

HEADER = (
    "---\n"
    "type: note\n"
    "title: Plan the thing\n"
    "entity: acme\n"
    "product: null\n"
    "status: active\n"
    "created: 2026-10-01\n"
    "updated: 2026-10-02\n"
    "---\n"
)
NOTE = HEADER + "\nBody line one.\n\nBody line two.\n"
TODAY = "2026-11-02"


def edit(text, **kw):
    kw.setdefault("status", "waiting")
    kw.setdefault("updated", TODAY)
    kw.setdefault("body", None)
    kw.setdefault("comment", None)
    return edit_note(text, **kw)


def test_status_and_updated_replaced_in_place():
    out = edit(NOTE)
    assert out == NOTE.replace("status: active", "status: waiting").replace(
        "updated: 2026-10-02", f"updated: {TODAY}"
    )
    assert schema.validate_file_text(out)[0]


def test_status_inserted_before_closing_fence_when_missing():
    text = NOTE.replace("status: active\n", "")
    out = edit(text)
    assert out == text.replace(
        "updated: 2026-10-02\n---\n", f"updated: {TODAY}\nstatus: waiting\n---\n"
    )
    header, body = split_note(out)
    assert "status: waiting\n" in header
    assert body == "\nBody line one.\n\nBody line two.\n"


def test_updated_inserted_when_missing():
    text = NOTE.replace("updated: 2026-10-02\n", "")
    out = edit(text)
    header, body = split_note(out)
    assert header.endswith(f"updated: {TODAY}\n---\n")
    assert body == split_note(text)[1]
    assert schema.validate_file_text(out)[0]


def test_status_none_leaves_status_line_exactly():
    text = NOTE.replace("status: active", "status:   'Filed'   # keep me")
    out = edit(text, status=None)
    assert "status:   'Filed'   # keep me\n" in out
    assert f"updated: {TODAY}" in out


def test_status_none_with_no_status_line_inserts_nothing_and_is_refused_by_schema():
    text = NOTE.replace("status: active\n", "")
    with pytest.raises(NoteTextError):
        edit(text, status=None)


def test_unchanged_values_keep_their_bytes_even_when_quoted_or_commented():
    text = NOTE.replace("status: active", 'status: "waiting"  # why').replace(
        "updated: 2026-10-02", f"updated: '{TODAY}'"
    )
    assert edit(text) == text


def test_body_lines_that_look_like_header_keys_are_untouched():
    body = "\nstatus: x\nsub: y\nupdated: z\n---\nstatus: still body\n"
    text = HEADER + body
    out = edit(text)
    assert out.endswith(body)
    assert out.count("status: waiting") == 1


def test_similar_and_indented_keys_are_not_matched():
    text = HEADER.replace(
        "status: active\n", "x_status: keep\nstatus: active\nmeta:\n  status: nested\n"
    ) + "\nbody\n"
    out = edit(text)
    assert "x_status: keep\n" in out
    assert "  status: nested\n" in out
    assert "\nstatus: waiting\n" in out


def test_key_written_with_space_before_colon_is_replaced_not_duplicated():
    text = NOTE.replace("status: active", "status : active")
    out = edit(text)
    assert out.count("status") == 1
    assert "status: waiting\n" in out
    assert schema.validate_file_text(out)[0]


@pytest.mark.parametrize("key", ["status", "updated"])
def test_duplicate_keys_are_refused(key):
    text = NOTE.replace("---\n\nBody", f"{key}: again\n---\n\nBody")
    with pytest.raises(NoteTextError):
        edit(text)


def test_title_containing_triple_dash_is_refused_not_corrupted():
    text = NOTE.replace("title: Plan the thing", "title: Plan --- the thing")
    with pytest.raises(NoteTextError):
        edit(text)
    with pytest.raises(NoteTextError):
        split_note(text)


def test_fence_variants_are_accepted_when_consistent():
    text = NOTE.replace("\n---\n\nBody", "\n---   \n\nBody")
    header, body = split_note(text)
    assert header + body == text
    assert header.endswith("---   \n")


@pytest.mark.parametrize(
    "text",
    ["no front matter\n", "---\ntype: note\n", "", "----\na: b\n---\nx\n"],
)
def test_missing_or_unclosed_front_matter_refused(text):
    with pytest.raises(NoteTextError):
        split_note(text)
    with pytest.raises(NoteTextError):
        edit(text)


def test_split_note_is_byte_exact():
    header, body = split_note(NOTE)
    assert header == HEADER
    assert header + body == NOTE


def test_crlf_header_is_preserved_and_new_lines_match():
    text = NOTE.replace("\n", "\r\n").replace("status: active\r\n", "")
    out = edit(text)
    assert "\r\n" in out
    assert "status: waiting\r\n" in out
    assert "\n" not in out.replace("\r\n", "")
    assert out.endswith("\r\nBody line one.\r\n\r\nBody line two.\r\n")


def test_noop_body_roundtrip_is_byte_identical_lf():
    out = edit_note(
        NOTE, status="active", updated="2026-10-02", body=shown_body(NOTE), comment=None
    )
    assert out == NOTE


def test_noop_body_roundtrip_with_browser_crlf_is_byte_identical():
    posted = shown_body(NOTE).replace("\n", "\r\n")
    out = edit_note(
        NOTE, status="active", updated="2026-10-02", body=posted, comment=None
    )
    assert out == NOTE


def test_noop_roundtrip_on_crlf_file_is_byte_identical():
    text = NOTE.replace("\n", "\r\n")
    out = edit_note(
        text, status="active", updated="2026-10-02", body=shown_body(text), comment=None
    )
    assert out == text


@pytest.mark.parametrize(
    "tail",
    ["", "\n", "\n\n\n", "text", "text\n", "\n\n  indented\n\n", "a\n\n\nb\n\n"],
)
@pytest.mark.parametrize("gap", ["", "\n", "\n\n"])
def test_noop_roundtrip_for_every_separator_and_tail(gap, tail):
    text = HEADER + gap + tail
    out = edit_note(
        text,
        status="active",
        updated="2026-10-02",
        body=shown_body(text).replace("\n", "\r\n"),
        comment=None,
    )
    assert out == text


def test_shown_body_has_no_leading_blank_lines_and_lf():
    text = (HEADER + "\n\r\nhello\r\nworld\r\n").replace("\n\r\n", "\n\n", 1)
    assert shown_body(text) == "hello\nworld\n"
    assert normalise_body("a\r\nb\r\n") == "a\nb\n"
    assert normalise_body("a\rb") == "a\rb"


def test_edited_body_replaces_body_keeps_separator_and_header_lines():
    out = edit(NOTE, body="New text\r\nmore\r\n")
    assert out == HEADER.replace("status: active", "status: waiting").replace(
        "updated: 2026-10-02", f"updated: {TODAY}"
    ) + "\nNew text\nmore\n"


def test_edited_body_keeps_a_missing_separator_missing():
    text = HEADER + "old\n"
    out = edit(text, status=None, body="new\n")
    assert out == HEADER.replace("updated: 2026-10-02", f"updated: {TODAY}") + "new\n"


def test_edited_body_with_leading_newlines_is_not_doubled():
    out = edit(NOTE, body="\n\nNew\n")
    assert split_note(out)[1] == "\nNew\n"


def test_empty_body_replacement():
    out = edit(NOTE, body="")
    assert split_note(out)[1] == "\n"


def test_comment_appended_as_own_line_with_date():
    out = edit(NOTE, comment="  Looks good  ")
    assert out.endswith("Body line two.\n\nComment (2026-11-02): Looks good\n")


def test_comment_after_edited_body_and_normalised_newlines():
    out = edit(NOTE, body="Edited\r\n\r\n\r\n", comment="hi")
    assert split_note(out)[1] == "\nEdited\n\nComment (2026-11-02): hi\n"


def test_comment_on_body_without_trailing_newline_and_on_empty_body():
    assert edit(HEADER + "\ntext", comment="c").endswith("text\n\nComment (2026-11-02): c\n")
    out = edit(HEADER, comment="c")
    assert out == HEADER.replace("status: active", "status: waiting").replace(
        "updated: 2026-10-02", f"updated: {TODAY}"
    ) + "Comment (2026-11-02): c\n"


def test_second_comment_is_appended_after_first():
    once = edit(NOTE, comment="one")
    twice = edit(once, comment="two")
    assert twice.endswith("Comment (2026-11-02): one\n\nComment (2026-11-02): two\n")


def test_comment_on_note_whose_fence_ends_the_file():
    text = HEADER[:-1]
    out = edit(text, comment="c")
    assert out.endswith("---\nComment (2026-11-02): c\n")
    assert schema.validate_file_text(out)[0]


@pytest.mark.parametrize(
    "comment",
    ["a\nb", "a\r\nb", "a\rb", "trailing\n", "a b", "a\x85b", "a\x00b", "", "   ", "x" * 501],
)
def test_bad_comments_refused(comment):
    with pytest.raises(NoteTextError):
        edit(NOTE, comment=comment)


def test_comment_length_boundaries():
    assert "x" * 500 in edit(NOTE, comment="x" * 500)
    assert "y" in edit(NOTE, comment="y")


@pytest.mark.parametrize("bad", ["2026-1-2", "2026-13-01", "2026-02-30", "20261102", "2026-11-02\n", "", "٢٠٢٦-١١-٠٢"])
def test_bad_updated_refused(bad):
    with pytest.raises(NoteTextError):
        edit(NOTE, updated=bad)


@pytest.mark.parametrize("bad", ["", "wait ing", "a\nb: c", "x: y", "Waiting!", "# c", "'q'"])
def test_bad_status_refused(bad):
    with pytest.raises(NoteTextError):
        edit(NOTE, status=bad)


def test_result_failing_schema_is_refused():
    text = NOTE.replace("entity: acme\n", "")
    with pytest.raises(NoteTextError):
        edit(text)


def test_header_with_multiline_status_value_is_refused_not_guessed():
    text = NOTE.replace("status: active\n", "status:\n  active\n")
    with pytest.raises(NoteTextError):
        edit(text)


def test_other_header_content_is_preserved_byte_for_byte():
    header = (
        "---\n"
        "# a comment\n"
        "type: note\n"
        'title: "Quoted: title"\n'
        "entity: acme\n"
        "product: null\n"
        "tags: [b,   a]\n"
        "status: active   # trailing\n"
        "created: 2026-10-01\n"
        "updated: 2026-10-02\n"
        "sub: Alpha\n"
        "---\n"
    )
    out = edit(header + "\nbody\n")
    assert out == (
        header.replace("status: active   # trailing", "status: waiting").replace(
            "updated: 2026-10-02", f"updated: {TODAY}"
        )
        + "\nbody\n"
    )


def _adversarial(old: str, new: str) -> str:
    assert old in NOTE
    return NOTE.replace(old, new, 1)


ADVERSARIAL_HEADERS = {
    # A quoted scalar whose continuation line looks like a header key.
    "double-quoted-multiline-with-status": _adversarial(
        "title: Plan the thing\n", 'title: "Plan\nstatus: x"\n'
    ),
    "single-quoted-multiline-with-status": _adversarial(
        "title: Plan the thing\n", "title: 'Plan\nstatus: x'\n"
    ),
    "quoted-multiline-is-the-only-status": _adversarial(
        "status: active\n", 'note: "a\nstatus: x"\n'
    ),
    "double-quoted-multiline": _adversarial(
        "title: Plan the thing\n", 'title: "Plan\n  the thing"\n'
    ),
    "flow-mapping-multiline-with-status": _adversarial(
        "product: null\n", "product: null\nmeta: {a: 1,\nstatus: x}\n"
    ),
    "flow-mapping-multiline": _adversarial(
        "product: null\n", "product: null\nmeta: {a: 1,\n  b: 2}\n"
    ),
    "flow-sequence-multiline": _adversarial(
        "product: null\n", "product: null\ntags: [a,\n  b]\n"
    ),
    "anchor-and-alias": _adversarial(
        "product: null\n", "product: &p null\nmirror: *p\n"
    ),
    "anchor-on-status": _adversarial("status: active", "status: &s active"),
    "alias-as-status": _adversarial(
        "product: null\n", "product: null\nother: &s waiting\n"
    ).replace("status: active", "status: *s"),
    "tab-after-status-colon": _adversarial("status: active", "status:\tactive"),
    "tab-after-updated-colon": _adversarial(
        "updated: 2026-10-02", "updated:\t2026-10-02"
    ),
    "document-end-marker": _adversarial("product: null\n", "product: null\n...\n"),
    "indented-header": "---\n" + "".join(
        f"  {line}\n" for line in HEADER.split("\n")[1:-2]
    ) + "---\n\nbody\n",
    "indented-status-only": _adversarial("status: active", " status: active"),
    "byte-order-mark": "\ufeff" + NOTE,
    "next-line-in-header": _adversarial("title: Plan the thing", "title: Plan\x85thing"),
    "line-separator-in-header": _adversarial(
        "title: Plan the thing", "title: Plan\u2028thing"
    ),
    "paragraph-separator-in-header": _adversarial(
        "title: Plan the thing", "title: Plan\u2029thing"
    ),
    "quoted-line-separator-in-header": _adversarial(
        "title: Plan the thing", 'title: "Plan\u2028thing"'
    ),
}


@pytest.mark.parametrize("text", ADVERSARIAL_HEADERS.values(), ids=ADVERSARIAL_HEADERS.keys())
@pytest.mark.parametrize("status", ["waiting", None])
def test_adversarial_headers_are_refused_never_rewritten(text, status):
    with pytest.raises(NoteTextError):
        edit(text, status=status)


def test_plain_block_lists_and_scalars_in_the_header_are_still_edited():
    text = NOTE.replace(
        "product: null\n",
        "product: null\ntags:\n  - a\n  - b\nsummary: >\n  folded\n  status: not a key\n",
    )
    out = edit(text)
    assert out == text.replace("status: active", "status: waiting").replace(
        "updated: 2026-10-02", f"updated: {TODAY}"
    )


@pytest.mark.parametrize("field", ["body", "comment"])
def test_result_that_cannot_be_utf8_is_refused(field):
    with pytest.raises(NoteTextError):
        edit(NOTE, **{field: "lone \ud800 surrogate"})


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("created: 2026-10-01", "created: 2026-02-30"),
        ("updated: 2026-10-02", "updated: 2026-13-01"),
        ("product: null", "product: !!timestamp not-a-date"),
    ],
)
def test_impossible_header_values_raise_note_text_error_not_value_error(old, new):
    with pytest.raises(NoteTextError):
        edit(NOTE.replace(old, new))
