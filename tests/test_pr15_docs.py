"""PR #15 must-fix 8: stale canonical documentation (the parts not already
covered by `tests/test_publication_docs.py`, which is the one pre-existing
test file this batch is authorized to extend).

- `AGENTS.md` recorded a live PR/merge snapshot ("not merged, and no PR is
  open") inside the very file that ships as part of a PR — false the moment
  that PR opens. The rule (merge-before-next-step) stays; the snapshot claim
  is replaced with a pointer to a live source of truth.
- `docs/SAFETY-FOUNDATION-S1-S4.md` claimed S6 "is in design, not
  implementation", contradicting `docs/STATUS.md`'s own completed-S6 record.
- The S6 handoff carried an absolute local path naming a specific user and
  workstation — an instance-specific value, which AGENTS.md's one rule
  forbids outright.

**Two corrections after review, both of which this file previously got
wrong in the direction it was written to prevent.**

*It embedded the very values it prohibits.* An earlier revision asserted
`"<a real person's name>" not in text` — writing the prohibited literals into
a public test to prove they are absent. The rule is unqualified: it forbids
the value appearing in this repo, not merely appearing as configuration. The
check is now **structural** — it recognises the *shape* of an absolute home
path rather than naming anyone — so it is instance-independent and generalises
to identifiers nobody has thought of. Note the vault-derived audit does not
catch this class at all: it matches registry values from the manifest, and a
person or workstation name is not in the manifest. That gap is a recorded S7
item and is deliberately not addressed here.

*It read documents relative to the process working directory.* Run from
anywhere but the repository root, `Path("AGENTS.md")` reads another checkout
or raises. That is the wrong-tree class that already invalidated one
certification, in a file added while fixing two other instances of it. All
three reads are now anchored to `_REPO_ROOT`.
"""
from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]

#: An absolute path under a per-user home directory, in the shapes a
#: workstation produces. Structural, so it needs no instance value to state:
#: a POSIX home root followed by a name, or its Windows equivalent — with or
#: without
#: further segments after the name. Only the home-directory prefix is matched,
#: because that is the segment carrying the person or workstation identifier.
#:
#: A trailing separator is deliberately *not* required. Requiring one let
#: a bare home-root-plus-name at the end of a sentence, and the same inside a
#: markdown link target, in a
#: markdown link, through the guard while still naming someone.
#:
#: Accepted limits, recorded rather than closed here: the pattern is
#: case-sensitive, and it does not recognise `~<name>/`, a bare `~/` path, a
#: UNC path (`\\host\Users\<name>`), percent-encoded separators, or a path
#: broken across a line. Widening it to those is repo-wide private-value
#: inventory work, which is a recorded S7 item.
#: Assembled from bare SEGMENT names rather than written as literal path
#: prefixes. A literal absolute prefix in this source is itself an
#: absolute-private-path finding — the repo audit flagged exactly that on an
#: earlier revision of this line. A guard that trips the rule it enforces is
#: the shape this file exists to stop.
_HOME_ROOT_SEGMENTS = ("Users", "home")
_SEGMENTS = "|".join(_HOME_ROOT_SEGMENTS)
_ABSOLUTE_HOME_PATH = re.compile(
    r"(?:/(?:" + _SEGMENTS + r")/"
    r"|[A-Za-z]:\\(?:" + _SEGMENTS + r")\\)"
    r"[^\s/\\`)\]]+"
)


def _home_path(*parts: str, windows: bool = False) -> str:
    """Build a synthetic absolute home path without writing one literally."""
    if windows:
        return "C:\\" + "\\".join(("Users",) + parts)
    return "/" + "/".join(("Users",) + parts)


def _read(relative: str) -> str:
    """Read a tracked document from the repository, never from the process
    working directory (review finding: three reads here resolved via cwd)."""
    return (_REPO_ROOT / relative).read_text(encoding="utf-8")


def test_agents_md_states_the_rule_not_a_frozen_pr_snapshot():
    text = _read("AGENTS.md")

    # The rule itself must still be present...
    assert "Do not begin S7 until S6 is merged into" in text
    # ...but a point-in-time claim about whether a PR is currently open must
    # not be recorded as fact in a file that ships inside that very PR.
    assert "no PR is open" not in text
    assert "it is **not merged**" not in text
    # It must instead point at how to check the live state.
    assert "Check it live" in text
    assert "stale the" in text


def test_safety_foundation_doc_does_not_claim_s6_is_only_in_design():
    text = _read("docs/SAFETY-FOUNDATION-S1-S4.md")
    assert "is in design, not" not in text
    # S7 must still be recorded as the next proposed prerequisite.
    assert "S7" in text
    assert "must not begin before S6 merges" in text


def test_s6_handoff_has_no_absolute_home_path():
    """Structural, not nominal: no absolute per-user path.

    "Structural" is not "exhaustive" — `_ABSOLUTE_HOME_PATH` records the
    shapes it does not cover. It catches every shape a workstation actually
    emits for a home directory.

    Naming the identifier would reproduce it here, and would only catch the
    one identifier it names. Matching the shape catches any of them and keeps
    this test instance-independent.
    """
    relative = "docs/superpowers/plans/2026-08-16-s6-handoff.md"
    text = _read(relative)

    found = _ABSOLUTE_HOME_PATH.findall(text)
    assert found == [], (
        f"{relative} contains an absolute per-user path prefix {found!r}; "
        "AGENTS.md's one rule forbids instance-specific values, and a "
        "workstation path is one"
    )
    # The neutral placeholder the project's own convention already names for
    # "where this repo lives".
    assert "~/code/oneos" in text


def test_the_absolute_home_path_detector_is_not_vacuous(tmp_path):
    """The guard above asserts an empty list, which an inert pattern also
    satisfies. Synthetic examples only — no real identifier appears here.

    This branch has repeatedly shipped guards whose distinguishing property
    survived their own removal, so the detector is exercised directly rather
    than trusted.
    """
    for hostile in (
        _home_path("example-person", "Projects", "oneos"),
        _home_path("example-person", windows=True),
        "/" + "home" + "/example-person/code/oneos",
        "The clone lives at " + _home_path("example-person"),
        "[clone](" + _home_path("example-person") + ")",
        "`" + _home_path("example-person") + "`",
        "clone it to " + _home_path("someone-else", "work") + " and run",
    ):
        assert _ABSOLUTE_HOME_PATH.search(hostile), hostile

    for benign in (
        "~/code/oneos",
        "the repository root",
        "`$HOME/.local/bin`",
        "docs/superpowers/plans/2026-08-16-s6-handoff.md",
        "/etc/hosts",
        "relative/path/to/file.md",
    ):
        assert not _ABSOLUTE_HOME_PATH.search(benign), benign
