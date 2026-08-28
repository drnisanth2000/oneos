"""PR #15 must-fix 6: external symlinks must classify as E-TAMPER, not
E-SCOPE.

`app/destinations.py::_require_real_directory` (lines 90-100) and
`app/inbox.py::_require_real_directory` (lines 42-48) both called
`Scope.resolve()` (or, for inbox.py, reached it through the same helper)
BEFORE classifying a lexical symlink. `Scope.resolve()` raises
`OutOfScopeError` for a path that resolves outside the bound entity root, so a
symlink at a lifecycle directory pointing outside the entity root raised
`OutOfScopeError` (-> E-SCOPE) instead of the redirection type the design
requires (`RedirectedDestination` / `RedirectedPathError` -> E-TAMPER).

Design §2: "a path is redirected, is not a regular file, or was type-swapped"
is a distinct, more serious finding than "a path resolved outside the bound
entity" — collapsing them into E-SCOPE hides a tampering condition behind an
ordinary scope refusal.
"""
from __future__ import annotations

import pytest

from tests.conftest import write_vault

ENTITIES = 'version: "1.0"\nentities:\n  demo1: {label: Demo, flags: []}\n'
ARCHETYPES = """
version: "2.0"
flags: {}
modules:
  00-inbox: {block: system}
  01-core: {block: govern}
submodules: {}
"""


def _vault(tmp_path):
    write_vault(tmp_path, ENTITIES, ARCHETYPES)
    return tmp_path


# --- app/destinations.py::_require_real_directory -------------------------


def test_destinations_external_symlink_at_module_dir_is_tamper_not_scope(tmp_path):
    from app.destinations import RedirectedDestination, _require_real_directory
    from app.scope import OutOfScopeError, Scope

    vault = _vault(tmp_path)
    outside = tmp_path.parent / "outside_module_target"
    outside.mkdir(exist_ok=True)
    (vault / "demo1").mkdir()
    (vault / "demo1" / "01-core").symlink_to(outside, target_is_directory=True)

    scope = Scope(vault, "demo1")
    with pytest.raises(RedirectedDestination) as raised:
        _require_real_directory(scope, "01-core")
    assert not isinstance(raised.value, OutOfScopeError)


def test_destinations_external_symlink_at_inbox_dir_is_tamper_not_scope(tmp_path):
    from app.destinations import RedirectedDestination, _require_real_directory
    from app.scope import OutOfScopeError, Scope

    vault = _vault(tmp_path)
    outside = tmp_path.parent / "outside_inbox_target"
    outside.mkdir(exist_ok=True)
    (vault / "demo1").mkdir()
    (vault / "demo1" / "00-inbox").symlink_to(outside, target_is_directory=True)

    scope = Scope(vault, "demo1")
    with pytest.raises(RedirectedDestination) as raised:
        _require_real_directory(scope, "00-inbox")
    assert not isinstance(raised.value, OutOfScopeError)


def test_destinations_internal_symlink_still_redirected(tmp_path):
    """Control: a symlink pointing INSIDE the vault (which `scope.resolve()`
    already tolerates without raising `OutOfScopeError`) must still be caught
    as redirected — the reordering must not weaken this existing case."""
    from app.destinations import RedirectedDestination, _require_real_directory
    from app.scope import Scope

    vault = _vault(tmp_path)
    (vault / "demo1").mkdir()
    real = vault / "demo1" / "01-core-real"
    real.mkdir()
    (vault / "demo1" / "01-core").symlink_to(real, target_is_directory=True)

    scope = Scope(vault, "demo1")
    with pytest.raises(RedirectedDestination):
        _require_real_directory(scope, "01-core")


def test_destinations_missing_directory_still_missing_not_tamper(tmp_path):
    """Control: absence is still `MissingDestination`, never a tamper
    alarm — the design's explicit non-goal."""
    from app.destinations import MissingDestination, _require_real_directory
    from app.scope import Scope

    vault = _vault(tmp_path)
    (vault / "demo1").mkdir()

    scope = Scope(vault, "demo1")
    with pytest.raises(MissingDestination):
        _require_real_directory(scope, "01-core")


def test_destinations_ordinary_directory_still_resolves(tmp_path):
    """Control: an ordinary, non-symlinked, present directory is unaffected."""
    from app.destinations import _require_real_directory
    from app.scope import Scope

    vault = _vault(tmp_path)
    (vault / "demo1" / "01-core").mkdir(parents=True)

    scope = Scope(vault, "demo1")
    resolved = _require_real_directory(scope, "01-core")
    assert resolved == (vault / "demo1" / "01-core").resolve()


# --- app/inbox.py::_require_real_directory ---------------------------------


def test_inbox_external_symlink_at_inbox_dir_is_tamper_not_scope(tmp_path):
    from app.inbox import _require_real_directory
    from app.scope import OutOfScopeError, RedirectedPathError, Scope

    vault = _vault(tmp_path)
    outside = tmp_path.parent / "outside_inbox_target_2"
    outside.mkdir(exist_ok=True)
    (vault / "demo1").mkdir()
    (vault / "demo1" / "00-inbox").symlink_to(outside, target_is_directory=True)

    scope = Scope(vault, "demo1")
    with pytest.raises(RedirectedPathError) as raised:
        _require_real_directory(scope, "00-inbox")
    assert not isinstance(raised.value, OutOfScopeError)


def test_inbox_internal_symlink_still_redirected(tmp_path):
    """Control: an internal symlink is still caught, unaffected by the
    reordering."""
    from app.inbox import _require_real_directory
    from app.scope import Scope

    vault = _vault(tmp_path)
    (vault / "demo1").mkdir()
    real = vault / "demo1" / "00-inbox-real"
    real.mkdir()
    (vault / "demo1" / "00-inbox").symlink_to(real, target_is_directory=True)

    scope = Scope(vault, "demo1")
    with pytest.raises(Exception) as raised:
        _require_real_directory(scope, "00-inbox")
    from app.scope import RedirectedPathError

    assert isinstance(raised.value, RedirectedPathError)


def test_inbox_absent_directory_returns_none_not_tamper(tmp_path):
    """Control: an absent, non-symlinked directory returns None — the
    existing tolerant contract `read_inbox` relies on."""
    from app.inbox import _require_real_directory
    from app.scope import Scope

    vault = _vault(tmp_path)
    (vault / "demo1").mkdir()

    scope = Scope(vault, "demo1")
    assert _require_real_directory(scope, "00-inbox") is None


def test_inbox_ordinary_directory_still_resolves(tmp_path):
    from app.inbox import _require_real_directory
    from app.scope import Scope

    vault = _vault(tmp_path)
    (vault / "demo1" / "00-inbox").mkdir(parents=True)

    scope = Scope(vault, "demo1")
    resolved = _require_real_directory(scope, "00-inbox")
    assert resolved == vault / "demo1" / "00-inbox"


# --- end-to-end: resolve_classification_destination / read_inbox ----------


def test_resolve_classification_destination_reports_tamper_for_external_symlink(
    tmp_path,
):
    """End-to-end through the real entry point `triage` calls."""
    from app.destinations import RedirectedDestination, resolve_classification_destination
    from app.console_errors import describe
    from app.scope import Scope

    vault = _vault(tmp_path)
    outside = tmp_path.parent / "outside_module_target_e2e"
    outside.mkdir(exist_ok=True)
    (vault / "demo1" / "00-inbox" / "active").mkdir(parents=True)
    (vault / "demo1" / "00-inbox" / "active" / "note.md").write_text(
        "body\n", encoding="utf-8"
    )
    (vault / "demo1" / "01-core").symlink_to(outside, target_is_directory=True)

    scope = Scope(vault, "demo1")
    item_path = vault / "demo1" / "00-inbox" / "active" / "note.md"

    with pytest.raises(RedirectedDestination) as raised:
        resolve_classification_destination(
            scope, item_path, module="01-core", sub=None
        )
    assert describe(raised.value).code == "E-TAMPER"


# --- C2 (S6 review, must-fix 6's own axis, left open at three sites) ------
#
# The reorder above closed the module- and inbox-directory sites. Review
# found the SAME shape — `scope.resolve(...)` called before the lexical
# `.is_symlink()` check on the corresponding path — still live at three more
# sites: the final destination *leaf* in `resolve_classification_destination`
# itself (distinct from the module-directory check above), the outbox
# directory in `app/outbox.py::_require_outbox_path`, and the outbox
# directory in `app/registry.py::_delete_proposal_path`. Each is reachable
# from a real Console screen: every triage row, the outbox screen and both
# approve/reject POSTs, and every delete-proposal read.


def test_destinations_external_symlink_at_destination_leaf_is_tamper_not_scope(
    tmp_path,
):
    """`app/destinations.py:172` (pre-fix): `scope.resolve(module, "active",
    leaf)` ran before `destination_lexical.is_symlink()` — a symlinked
    destination *file* (module and its active/ directory both ordinary)
    raised OutOfScopeError (-> E-SCOPE) instead of RedirectedDestination
    (-> E-TAMPER)."""
    from app.destinations import RedirectedDestination, resolve_classification_destination
    from app.console_errors import describe
    from app.scope import OutOfScopeError, Scope

    vault = _vault(tmp_path)
    outside = tmp_path.parent / "outside_destination_leaf_target"
    outside.write_text("hostile\n", encoding="utf-8")
    (vault / "demo1" / "00-inbox" / "active").mkdir(parents=True)
    (vault / "demo1" / "00-inbox" / "active" / "note.md").write_text(
        "body\n", encoding="utf-8"
    )
    (vault / "demo1" / "01-core" / "active").mkdir(parents=True)
    (vault / "demo1" / "01-core" / "active" / "note.md").symlink_to(outside)

    scope = Scope(vault, "demo1")
    item_path = vault / "demo1" / "00-inbox" / "active" / "note.md"

    with pytest.raises(RedirectedDestination) as raised:
        resolve_classification_destination(
            scope, item_path, module="01-core", sub=None
        )
    assert not isinstance(raised.value, OutOfScopeError)
    assert describe(raised.value).code == "E-TAMPER"


def test_outbox_external_symlink_at_outbox_dir_is_tamper_not_scope(tmp_path):
    """`app/outbox.py:127-129`: `scope.resolve("outbox")` was assigned before
    `lexical_outbox.is_symlink()` was checked, so a symlinked outbox raised
    OutOfScopeError (-> E-SCOPE) instead of RedirectedPathError
    (-> E-TAMPER). Reached from `load_proposals` -> the outbox screen and
    both approve/reject POSTs."""
    from app.outbox import load_proposals
    from app.scope import OutOfScopeError, RedirectedPathError, Scope

    vault = _vault(tmp_path)
    outside = tmp_path.parent / "outside_outbox_target"
    outside.mkdir(exist_ok=True)
    (vault / "demo1").mkdir()
    (vault / "demo1" / "outbox").symlink_to(outside, target_is_directory=True)

    scope = Scope(vault, "demo1")
    with pytest.raises(RedirectedPathError) as raised:
        load_proposals(scope)
    assert not isinstance(raised.value, OutOfScopeError)


def test_outbox_internal_symlink_at_outbox_dir_still_redirected(tmp_path):
    """Control: an internal symlink is still caught, unaffected by the
    reordering."""
    from app.outbox import load_proposals
    from app.scope import RedirectedPathError, Scope

    vault = _vault(tmp_path)
    (vault / "demo1").mkdir()
    real = vault / "demo1" / "outbox-real"
    real.mkdir()
    (vault / "demo1" / "outbox").symlink_to(real, target_is_directory=True)

    scope = Scope(vault, "demo1")
    with pytest.raises(RedirectedPathError):
        load_proposals(scope)


def test_registry_external_symlink_at_outbox_dir_is_tamper_not_scope(tmp_path):
    """`app/registry.py:289-295`: `_delete_proposal_path` called
    `scope.resolve("outbox")` with no lexical symlink check at all, so a
    symlinked outbox raised OutOfScopeError (-> E-SCOPE) instead of
    RedirectedPathError (-> E-TAMPER). Reached by every delete-proposal
    read."""
    from app.registry import get_delete_proposal
    from app.scope import OutOfScopeError, RedirectedPathError, Scope

    vault = _vault(tmp_path)
    outside = tmp_path.parent / "outside_registry_outbox_target"
    outside.mkdir(exist_ok=True)
    (vault / "demo1").mkdir()
    (vault / "demo1" / "outbox").symlink_to(outside, target_is_directory=True)

    scope = Scope(vault, "demo1")
    proposal_id = "20260815T090703-" + "ab" * 16
    with pytest.raises(RedirectedPathError) as raised:
        get_delete_proposal(scope, proposal_id)
    assert not isinstance(raised.value, OutOfScopeError)


def test_registry_internal_symlink_at_outbox_dir_still_redirected(tmp_path):
    """Control: an internal symlink is still caught, unaffected by the
    reordering."""
    from app.registry import get_delete_proposal
    from app.scope import RedirectedPathError, Scope

    vault = _vault(tmp_path)
    (vault / "demo1").mkdir()
    real = vault / "demo1" / "outbox-real"
    real.mkdir()
    (vault / "demo1" / "outbox").symlink_to(real, target_is_directory=True)

    scope = Scope(vault, "demo1")
    proposal_id = "20260815T090703-" + "cd" * 16
    with pytest.raises(RedirectedPathError):
        get_delete_proposal(scope, proposal_id)
