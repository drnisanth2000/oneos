"""PR #15 must-fix 1: nested classifier registry validation.

`app/classifier.py`'s `_load()` parsed `rules.yaml` and checked only that the
top-level document is a mapping. A syntactically valid but wrongly shaped
nested value — `rules: [bad]`, a `match:`/`route:`/`default:` that is not a
mapping, or an `any:` keyword list containing a non-string — passed `_load()`
silently and only crashed later, inside `Classifier.classify()`, with a raw
`AttributeError` (`"bad".get(...)`) that reached the operator as `E-UNKNOWN`
instead of `E-CONFIG`.

This is a boundary-conversion fix in the same sense as design §5 "Boundary
conversions": the crash already happens on the very next triage request for
any inbox item, because `classify()` unconditionally loops over every rule.
Moving the check to the reader only retypes an already-guaranteed failure; it
does not invent a new refusal. See `app/classifier.py::_validate_rules_shape`
for the exact fatality analysis of which nested shapes are validated (because
they already crash) and which are deliberately left alone (because they
never do, and validating them would invent a new refusal instead of retyping
an existing one) — pinned by `test_shapes_that_never_crash_stay_tolerated`
below.
"""
from __future__ import annotations

import textwrap

import pytest

from tests.conftest import write_vault
from app.console_errors import describe
from app.entities import EntityCatalog
from app.vault import DestinationRegistryError, Vault

ENTITIES = 'version: "1.0"\nentities:\n  demo: {label: Demo, flags: []}\n'


def _vault(tmp_path):
    write_vault(tmp_path, ENTITIES)
    return Vault(EntityCatalog.load(tmp_path))


def _write_rules(tmp_path, text: str) -> None:
    d = tmp_path / "_system" / "classifier"
    d.mkdir(parents=True, exist_ok=True)
    (d / "rules.yaml").write_text(textwrap.dedent(text), encoding="utf-8")


@pytest.mark.parametrize(
    "label, rules_yaml",
    [
        ("rules not a list", "rules: not-a-list\n"),
        ("rule item not a mapping", "rules: [bad]\n"),
        (
            "match not a mapping",
            "rules:\n  - match: not-a-mapping\n    route: {module: 00-intake}\n",
        ),
        (
            "match.any not a list of strings",
            "rules:\n  - match: {any: [1, 2]}\n    route: {module: 00-intake}\n",
        ),
        ("default not a mapping", "default: not-a-mapping\n"),
        # "route not a mapping" is deliberately NOT a row here: `route:` is
        # validated only once its rule has matched (C1, S6 review — the
        # eager, always-checked version of this row is exactly the invented
        # refusal that turned a non-matching rule's malformed `route:` into
        # a fatal E-CONFIG on every triage page regardless of whether that
        # rule ever fires). See
        # test_route_shape_validated_only_when_the_rule_matches below, which
        # asserts this shape becomes E-CONFIG only on the matching path.
    ],
)
def test_malformed_nested_classifier_shape_becomes_config(tmp_path, label, rules_yaml):
    from app.classifier import Classifier

    vault = _vault(tmp_path)
    _write_rules(tmp_path, rules_yaml)

    with pytest.raises(DestinationRegistryError) as raised:
        Classifier(vault)
    assert describe(raised.value).code == "E-CONFIG", label


@pytest.mark.parametrize(
    "label, rules_yaml",
    [
        # These shapes never actually crash (see the module-level fatality
        # analysis in app/classifier.py's `_validate_rules_shape` docstring),
        # so validating them would invent a NEW refusal rather than retype an
        # existing crash. Pinned here as controls: must NOT raise.
        (
            "match.source not a string is harmless (only ever compared with !=)",
            "rules:\n  - match: {any: [x], source: 5}\n"
            "    route: {module: 00-intake}\n",
        ),
        (
            "route.module as a number is safely refused downstream, not here",
            "rules:\n  - match: {any: [x]}\n    route: {module: 5}\n",
        ),
        (
            "route.sub as a number is safely refused downstream, not here",
            "rules:\n  - match: {any: [x]}\n    route: {module: m, sub: 5}\n",
        ),
        (
            "default.module as a number is safely refused downstream, not here",
            "default: {module: 5}\n",
        ),
        (
            "default.sub as a number is safely refused downstream, not here",
            "default: {module: m, sub: 5}\n",
        ),
        # --- C1b (S6 review): the falsy axis, not just the type axis. ------
        # `classify()` reads every one of these through `x or <fallback>`,
        # which absorbs every falsy value regardless of its type. A
        # validator that checks type alone (`isinstance`) and skips
        # truthiness, as an earlier revision of `_require_mapping` did,
        # turns every one of these into an invented `E-CONFIG` refusal.
        ("rules: null is falsy and tolerated", "rules:\n"),
        ("rules: {} is falsy and tolerated", "rules: {}\n"),
        ("rules: 0 is falsy and tolerated", "rules: 0\n"),
        ("rules: '' is falsy and tolerated", "rules: ''\n"),
        ("rules: false is falsy and tolerated", "rules: false\n"),
        (
            "match: [] is falsy and tolerated",
            "rules:\n  - match: []\n    route: {module: m}\n",
        ),
        (
            "match: 0 is falsy and tolerated",
            "rules:\n  - match: 0\n    route: {module: m}\n",
        ),
        (
            "match: '' is falsy and tolerated",
            "rules:\n  - match: ''\n    route: {module: m}\n",
        ),
        (
            "match.any: {} is falsy and tolerated",
            "rules:\n  - match: {any: {}}\n    route: {module: m}\n",
        ),
        (
            "match.any: '' is falsy and tolerated",
            "rules:\n  - match: {any: ''}\n    route: {module: m}\n",
        ),
        (
            "match.any: 0 is falsy and tolerated",
            "rules:\n  - match: {any: 0}\n    route: {module: m}\n",
        ),
        (
            "match.any: invoice (bare scalar) never reaches a mapping method",
            "rules:\n  - match: {any: invoice}\n    route: {module: m}\n",
        ),
        (
            "match.any: {invoice: 1} (mapping) never reaches a mapping method",
            "rules:\n  - match: {any: {invoice: 1}}\n    route: {module: m}\n",
        ),
        ("default: [] is falsy and tolerated", "default: []\n"),
        ("default: 0 is falsy and tolerated", "default: 0\n"),
        ("default: '' is falsy and tolerated", "default: ''\n"),
    ],
)
def test_shapes_that_never_crash_stay_tolerated(tmp_path, label, rules_yaml):
    from app.classifier import Classifier

    vault = _vault(tmp_path)
    _write_rules(tmp_path, rules_yaml)

    # Must not raise: construction succeeds exactly as it did before must-fix 1.
    Classifier(vault)


def test_route_shape_validated_only_when_the_rule_matches(tmp_path):
    """C1 (S6 review): `route:` crashes only once its rule has matched —
    `route.get(...)` never runs for a rule that never fires. The axis is
    match-status, not type: the SAME malformed `route:` must stay tolerated
    on a non-matching rule and become `E-CONFIG` on a matching one."""
    from app.classifier import Classifier

    # (a) Never matches -> classify() must not raise, and falls through to
    # the default classification exactly as if `route:` were well-formed.
    vault = _vault(tmp_path)
    _write_rules(
        tmp_path,
        "rules:\n"
        "  - match: {any: [nevermatches]}\n"
        "    route: not-a-mapping\n"
        "default:\n"
        "  module: 00-inbox\n"
        "  sub: triage\n",
    )
    clf = Classifier(vault)
    result = clf.classify("Invoice due", "please pay", None)
    assert result.confident is False
    assert result.module == "00-inbox"

    # (b) Matches -> the same malformed `route:` now raises E-CONFIG at the
    # access site.
    vault2 = _vault(tmp_path)
    _write_rules(
        tmp_path,
        "rules:\n"
        "  - match: {any: [invoice]}\n"
        "    route: not-a-mapping\n",
    )
    clf2 = Classifier(vault2)
    with pytest.raises(DestinationRegistryError) as raised:
        clf2.classify("Invoice due", "please pay", None)
    assert describe(raised.value).code == "E-CONFIG"


def test_malformed_rule_no_longer_reaches_classify_as_attributeerror(tmp_path):
    """The exact reproduction from the CodeRabbit finding: a valid-YAML,
    malformed-shape `rules.yaml` must not let construction succeed only to
    crash `classify()` with a raw AttributeError on the next inbox item."""
    from app.classifier import Classifier

    vault = _vault(tmp_path)
    _write_rules(tmp_path, "rules: [bad]\n")

    with pytest.raises(DestinationRegistryError):
        Classifier(vault)


def test_well_formed_rules_still_classify(tmp_path):
    """Control: a well-formed registry is untouched by the new validation."""
    from app.classifier import Classifier

    vault = _vault(tmp_path)
    _write_rules(
        tmp_path,
        """
        rules:
          - id: rule-1
            match: {any: ["invoice"]}
            route: {module: "07-finance", sub: "billing"}
        default:
          module: "00-inbox"
          sub: "triage"
        """,
    )
    clf = Classifier(vault)
    result = clf.classify("Invoice due", "please pay", None)
    assert result.module == "07-finance"
    assert result.confident is True


def test_absent_rules_file_still_unclassified(tmp_path):
    """Control: the deliberate absent-file tolerance is untouched."""
    from app.classifier import Classifier

    vault = _vault(tmp_path)
    clf = Classifier(vault)
    result = clf.classify("anything", "anything", None)
    assert result.confident is False
    assert result.module == "00-inbox"
