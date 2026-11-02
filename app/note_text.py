"""note_text.py — header-scoped, byte-preserving edits to one note's text.

The master view moves a note between stages and lets the owner edit a draft.
Both change a few lines of a Markdown file whose YAML front matter must stay
exactly as written. This module computes the new bytes; it never touches disk.

Rules (each one is pinned in tests/test_note_text.py):

* Only the ``status:`` and ``updated:`` lines inside the front-matter block may
  change, and they change only when the value really differs. A missing line
  is inserted just before the closing fence. Nothing is re-dumped, reordered
  or re-quoted. Keys are matched at the start of a header line only, so
  ``x_status:`` and indented (nested) keys are never touched.
* The block ends at the first line whose stripped content is ``---``. If the
  first ``---`` that ``schema.validate_file_text`` would use is somewhere else
  (a header value containing ``---``), the note is refused, never rewritten.
* The body is replaced only when an edited body is submitted, and an edited
  body equal to ``shown_body(text)`` (what the popup showed) leaves the original
  bytes alone, so a no-op save is a no-op even for CRLF files.
* The result must pass ``schema.validate_file_text`` and must parse to the
  same header as before except for the two keys.
* A header this module cannot reason about line by line is refused: anchors
  or aliases, document markers or directives, a quoted scalar or flow
  collection spread over several lines, an indented top-level mapping, a
  ``status:``/``updated:`` value that does not sit on its own key line, or a
  Unicode line separator (U+0085, U+2028, U+2029 and friends) that other
  readers would split on.
* The result must encode as UTF-8.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date

import yaml

from . import schema
from .console_routing import structured_reader

COMMENT_MAX_CHARS = 500

_STATUS_RE = re.compile(r"[a-z][a-z0-9_-]*")
_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_LINE_RE = re.compile(r"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+")
_LINE_BREAKS = frozenset("\r\n\x0b\x0c\x1c\x1d\x1e\x85  ")
_BLANK_LINE_RE = re.compile(r"(?:\r?\n)+")


class NoteTextError(ValueError):
    """The note cannot be edited safely; nothing was changed."""


#: What PyYAML raises for a header it cannot construct. Scalar construction
#: (an impossible date such as ``2026-02-30``, an explicit ``!!timestamp``
#: tag) raises plain ``ValueError``/``AttributeError`` instead of YAMLError.
_YAML_FAILURES = (yaml.YAMLError, ValueError, TypeError, AttributeError, KeyError)


def _lines(text: str) -> list[str]:
    return _LINE_RE.findall(text)


def _eol(line: str) -> str:
    if line.endswith("\r\n"):
        return "\r\n"
    if line.endswith(("\n", "\r")):
        return line[-1]
    return ""


def split_note(text: str) -> tuple[str, str]:
    """Return ``(header_raw, body_raw)`` with ``header_raw + body_raw == text``.

    ``header_raw`` runs from the opening ``---`` line through the closing fence
    line. Raises ``NoteTextError`` without front matter, without a closing fence,
    or when the schema validator would close the block somewhere else.
    """
    if not isinstance(text, str):
        raise NoteTextError("note text must be a string")
    if not text.startswith("---"):
        raise NoteTextError("note has no front matter")
    lines = _lines(text)
    if not lines or lines[0].strip() != "---":
        raise NoteTextError("note has no front matter")
    offset = len(lines[0])
    for line in lines[1:]:
        if line.strip() == "---":
            fence_at = offset + (len(line) - len(line.lstrip()))
            if text.find("---", 3) != fence_at:
                raise NoteTextError(
                    "front matter contains '---' before its closing fence; "
                    "refusing to edit it"
                )
            end = offset + len(line)
            return text[:end], text[end:]
        offset += len(line)
    raise NoteTextError("front matter is not closed")


def normalise_body(text: str) -> str:
    """Browsers post textareas with CRLF; the vault stores LF."""
    return text.replace("\r\n", "\n")


def _split_body(body_raw: str) -> tuple[str, str]:
    """``(separator, content)``: the blank lines after the fence, then the rest."""
    match = _BLANK_LINE_RE.match(body_raw)
    cut = match.end() if match else 0
    return body_raw[:cut], body_raw[cut:]


def shown_body(text: str) -> str:
    """The body as the popup shows it: no leading blank lines, LF endings."""
    _, body_raw = split_note(text)
    return normalise_body(_split_body(body_raw)[1])


def _key_pattern(key: str) -> re.Pattern[str]:
    return re.compile(rf"""(["']?){key}\1[ \t]*:(?=[ \t]|$|\r|\n)""")


@structured_reader(category="front-matter")
def _line_value(line: str):
    """The YAML value one header line parses to, or raise NoteTextError."""
    try:
        loaded = yaml.safe_load(line)
    except _YAML_FAILURES as error:
        raise NoteTextError(f"header line is not valid YAML: {error}") from error
    if not isinstance(loaded, dict) or len(loaded) != 1:
        raise NoteTextError("header line is not a single key")
    return next(iter(loaded.values()))


#: Line breaks other than CR and LF. YAML, ``str.splitlines`` and the policy
#: enforcer do not agree on them, so a header holding one has no single
#: meaning to preserve.
_FOREIGN_LINE_BREAKS = _LINE_BREAKS - {"\r", "\n"}
_REFUSED_TOKENS = (
    yaml.AnchorToken,
    yaml.AliasToken,
    yaml.DirectiveToken,
    yaml.DocumentStartToken,
    yaml.DocumentEndToken,
)
_FLOW_OPEN = (yaml.FlowMappingStartToken, yaml.FlowSequenceStartToken)
_FLOW_CLOSE = (yaml.FlowMappingEndToken, yaml.FlowSequenceEndToken)


@structured_reader(category="front-matter")
def _key_lines(inner_text: str) -> dict[str, tuple[int, int, int]]:
    """Map each top-level key to ``(key line, value start line, value end line)``.

    Refuses every header shape whose lines cannot be edited one at a time.
    """
    if any(char in _FOREIGN_LINE_BREAKS for char in inner_text):
        raise NoteTextError("front matter contains a Unicode line separator")
    try:
        tokens = list(yaml.scan(inner_text, Loader=yaml.SafeLoader))
        node = yaml.compose(inner_text, Loader=yaml.SafeLoader)
    except yaml.YAMLError as error:
        raise NoteTextError(f"front matter is not valid YAML: {error}") from error
    flow_starts: list[int] = []
    for token in tokens:
        if isinstance(token, _REFUSED_TOKENS):
            raise NoteTextError(
                "front matter uses anchors, aliases or document markers; "
                "refusing to edit it"
            )
        if isinstance(token, _FLOW_OPEN):
            flow_starts.append(token.start_mark.line)
        elif isinstance(token, _FLOW_CLOSE):
            if flow_starts and flow_starts.pop() != token.end_mark.line:
                raise NoteTextError("front matter has a multi-line flow collection")
        elif (
            isinstance(token, yaml.ScalarToken)
            and not token.plain
            and token.style in ("'", '"')
            and token.start_mark.line != token.end_mark.line
        ):
            raise NoteTextError("front matter has a multi-line quoted value")
    if node is None:
        return {}
    if not isinstance(node, yaml.MappingNode):
        raise NoteTextError("front matter is not a mapping")
    lines: dict[str, tuple[int, int, int]] = {}
    for key, value in node.value:
        if key.start_mark.column != 0:
            raise NoteTextError("front matter keys are indented; refusing to edit it")
        if isinstance(key.value, str):
            lines[key.value] = (
                key.start_mark.line, value.start_mark.line, value.end_mark.line
            )
    return lines


def _set_key(
    inner: list[str],
    key: str,
    value: str,
    fence: str,
    placed: tuple[int, int, int] | None,
) -> list[str]:
    pattern = _key_pattern(key)
    hits = [i for i, line in enumerate(inner) if pattern.match(line)]
    if len(hits) > 1:
        raise NoteTextError(f"header has more than one '{key}:' line")
    # The line the regex found must be the line YAML says holds the key, and
    # its value must sit on that same line; anything else is a guess.
    if placed is None and hits:
        raise NoteTextError(f"header line '{key}:' is not a top-level key")
    if placed is not None and (
        hits != [placed[0]] or placed[1] != placed[0] or placed[2] != placed[0]
    ):
        raise NoteTextError(f"header '{key}:' value is not on its own line")
    eol = _eol(inner[-1]) if inner else ""
    eol = eol or _eol(fence) or "\n"
    if hits:
        index = hits[0]
        if str(_line_value(inner[index])) == value:
            return inner
        line_eol = _eol(inner[index]) or eol
        inner = list(inner)
        inner[index] = f"{key}: {value}{line_eol}"
        return inner
    return [*inner, f"{key}: {value}{eol}"]


@structured_reader(category="front-matter")
def _header_mapping(header_raw: str) -> dict:
    lines = _lines(header_raw)
    try:
        loaded = yaml.safe_load("".join(lines[1:-1])) or {}
    except _YAML_FAILURES as error:
        raise NoteTextError(f"front matter is not valid YAML: {error}") from error
    if not isinstance(loaded, dict):
        raise NoteTextError("front matter is not a mapping")
    return loaded


def _check_comment(comment: str) -> str:
    if not isinstance(comment, str):
        raise NoteTextError("comment must be text")
    for char in comment:
        if char in _LINE_BREAKS:
            raise NoteTextError("comment must be a single line")
        if unicodedata.category(char) == "Cc" and char != "\t":
            raise NoteTextError("comment contains a control character")
    trimmed = comment.strip()
    if not trimmed:
        raise NoteTextError("comment is empty")
    if len(trimmed) > COMMENT_MAX_CHARS:
        raise NoteTextError(f"comment is longer than {COMMENT_MAX_CHARS} characters")
    return trimmed


def edit_note(
    text: str,
    *,
    status: str | None,
    updated: str,
    body: str | None,
    comment: str | None,
) -> str:
    """Return ``text`` with the header lines, body and comment applied.

    ``status=None`` leaves the ``status:`` line exactly as it is (even absent).
    ``body=None`` keeps the body bytes; a body equal to ``shown_body(text)``
    does too. ``comment`` is appended as ``Comment (<updated>): <comment>``.
    """
    if status is not None and not (
        isinstance(status, str) and _STATUS_RE.fullmatch(status)
    ):
        raise NoteTextError("status is not a valid stage value")
    if not (isinstance(updated, str) and _DATE_RE.fullmatch(updated)):
        raise NoteTextError("updated must be a YYYY-MM-DD date")
    try:
        date.fromisoformat(updated)
    except ValueError as error:
        raise NoteTextError("updated is not a real date") from error
    if body is not None and not isinstance(body, str):
        raise NoteTextError("body must be text")
    comment_text = None if comment is None else _check_comment(comment)

    header_raw, body_raw = split_note(text)
    before = _header_mapping(header_raw)
    lines = _lines(header_raw)
    opening, inner, fence = lines[0], lines[1:-1], lines[-1]
    placed = _key_lines("".join(inner))
    if status is not None:
        inner = _set_key(inner, "status", status, fence, placed.get("status"))
    # Inserting `status:` appends after every existing line, so the line
    # numbers YAML gave for `updated:` are still right.
    inner = _set_key(inner, "updated", updated, fence, placed.get("updated"))
    new_header = "".join([opening, *inner, fence])

    # Whatever the edit did to the lines, the header must still mean the same
    # thing except for the two keys.
    after = _header_mapping(new_header)
    expect = dict(before)
    if status is not None:
        expect["status"] = status
    expect["updated"] = updated
    for key in set(after) | set(expect):
        if key in ("status", "updated"):
            if key in expect and str(after.get(key)) != str(expect[key]):
                raise NoteTextError(f"could not set '{key}:' safely")
        elif after.get(key) != expect.get(key) or (key in after) != (key in expect):
            raise NoteTextError("header edit would change other keys; refusing")

    separator, content = _split_body(body_raw)
    if body is not None:
        submitted = normalise_body(body)
        if submitted != normalise_body(content):
            content = submitted.lstrip("\n")
    if comment_text is not None:
        base = content.rstrip("\n")
        line = f"Comment ({updated}): {comment_text}\n"
        content = f"{base}\n\n{line}" if base else line
    if content and not _eol(new_header) and not separator:
        # Fence was the last bytes of the file: give it a line ending.
        separator = "\n"

    result = new_header + separator + content
    try:
        result.encode("utf-8")
    except UnicodeEncodeError as error:
        raise NoteTextError("edited note is not valid UTF-8 text") from error
    try:
        ok, problems = schema.validate_file_text(result)
    except _YAML_FAILURES as error:
        raise NoteTextError(f"edited note would be invalid: {error}") from error
    if not ok:
        raise NoteTextError("edited note would be invalid: " + "; ".join(problems))
    new_split = split_note(result)
    if new_split[0].rstrip("\r\n") != new_header.rstrip("\r\n"):
        raise NoteTextError("edit would move the front-matter fence; refusing")
    return result
