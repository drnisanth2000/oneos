"""The outbox presentation projection (S6 Task 9, design §3 "Rule 3 — Read
projection versus mutation authority").

`project_outbox` renders every row it safely can without ever re-entering the
strict loader (`get_proposal`, `load_proposals`, `preview_diff`). Rows carry
**capabilities**, not kinds:

- Phase 1 (record read/schema) failures are the family that poisons the
  strict loader — a row set `blocked` for the whole listing.
- Phase 2 (destination/registry/path) conditions are properties of the vault,
  not of one file — they **propagate**, aborting the projection outright.
- Phase 3 (diff rendering) failures are row-local and never block: the row
  loses `can_approve`, keeps `can_reject`, and `blocked` stays false.

Temp git vaults only; the real vault is never touched.
"""
import os
import stat
import textwrap
from pathlib import Path

import pytest
import yaml

import app.outbox as outbox
from app.console_errors import describe
from app.outbox import (
    approve,
    load_proposals,
    project_outbox,
    propose_classification,
    reject,
)
from app.scope import Scope
from tests.conftest import git_entity_vault


PROJECTION_ARCHETYPES = textwrap.dedent(
    """\
    version: "2.0"
    flags: {}
    modules:
      00-inbox: {block: system}
      11-knowledge: {block: govern}
      11-library: {block: govern}
    submodules:
      00-inbox:
        triage: {name: Triage}
      11-knowledge:
        kb: {name: Knowledge base}
      11-library:
        reference: {name: Reference}
    archetypes:
      plain: {}
    """
)


def _projection_vault(root, entities, files):
    return git_entity_vault(
        root,
        entities,
        {"_system/archetypes.yaml": PROJECTION_ARCHETYPES, **files},
    )


def _note(body: str = "Synthetic receipt body.\n") -> str:
    return textwrap.dedent(
        f"""\
        ---
        type: inbox-item
        title: Synthetic note
        entity: demo
        status: active
        created: 2026-01-01
        updated: 2026-01-01
        sub: triage
        source: folder
        ---
        {body}"""
    )


def _vault(tmp_path) -> Path:
    files = {
        "demo/00-inbox/active/note.md": _note(),
        "demo/11-knowledge/active/.gitkeep": "",
        "demo/11-library/active/.gitkeep": "",
    }
    return _projection_vault(tmp_path, ("demo",), files)


def _two_note_vault(tmp_path) -> Path:
    files = {
        "demo/00-inbox/active/note-a.md": _note("Alpha receipt body.\n"),
        "demo/00-inbox/active/note-b.md": _note("Beta receipt body.\n"),
        "demo/11-knowledge/active/.gitkeep": "",
        "demo/11-library/active/.gitkeep": "",
    }
    return _projection_vault(tmp_path, ("demo",), files)


def _propose(vault: Path):
    scope = Scope(vault, "demo")
    src = scope.resolve("00-inbox", "active", "note.md")
    prop = propose_classification(
        scope, src, module="11-knowledge", sub="kb", claimed_block="govern",
    )
    return scope, prop


def _write_record(scope: Scope, name: str, record: str) -> Path:
    path = scope.resolve("outbox", name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(record, encoding="utf-8")
    return path


def _delete_record(delete_id: str) -> str:
    return yaml.safe_dump(
        {
            "id": delete_id,
            "action": "delete",
            "entity": "demo",
            "kind": "product",
            "slug": "invented-product",
            "status": "pending",
            "total_references": 0,
            "impact": {},
        }
    )


# --- unblocked listing -------------------------------------------------------


def test_unblocked_listing_renders_all_valid_rows_with_controls(tmp_path):
    vault = _two_note_vault(tmp_path)
    scope = Scope(vault, "demo")
    a = propose_classification(
        scope, scope.resolve("00-inbox", "active", "note-a.md"),
        module="11-knowledge", sub="kb", claimed_block="govern",
    )
    b = propose_classification(
        scope, scope.resolve("00-inbox", "active", "note-b.md"),
        module="11-library", sub="reference", claimed_block="govern",
    )

    listing = project_outbox(scope)

    assert listing.blocked is False
    assert {row.proposal.id for row in listing.rows} == {a.id, b.id}
    for row in listing.rows:
        assert row.error is None
        assert row.diff is not None
        assert row.can_approve is True
        assert row.can_reject is True
        # Pin the projected diff CONTENT, not just its presence. `_render_diff`
        # and `preview_diff` keep separate read policies but share `_diff_text`;
        # without this they could drift silently — gutting `_render_diff`
        # passed 13/13.
        assert row.diff == outbox.preview_diff(scope, row.proposal)

    # The row's capability corresponds to a genuinely approvable proposal,
    # through the untouched strict path — not merely a flag on the row.
    approve(scope, a.id)
    assert [p.id for p in load_proposals(scope)] == [b.id]


# --- blocked listing (phase 1) -----------------------------------------------
def test_malformed_record_blocks_listing_and_withholds_all_controls(tmp_path):
    vault = _vault(tmp_path)
    scope, prop = _propose(vault)
    _write_record(scope, "malformed.yaml", "action: classify\nmodule: [unterminated\n")

    listing = project_outbox(scope)

    assert listing.blocked is True
    assert len(listing.rows) == 2
    malformed_rows = [row for row in listing.rows if row.proposal is None]
    valid_rows = [row for row in listing.rows if row.proposal is not None]
    assert len(malformed_rows) == 1
    assert len(valid_rows) == 1

    bad = malformed_rows[0]
    assert bad.diff is None
    assert isinstance(bad.error, outbox.UnreadableProposalRecord)
    assert bad.can_approve is False
    assert bad.can_reject is False
    # Rule 9 — no filename disclosure. `hasattr(row, "path")` would be a
    # tautology on a frozen dataclass with fixed fields; assert instead that
    # the attacker-controlled filename reaches no operator-visible text.
    assert "malformed" not in str(bad.error)
    assert "malformed" not in describe(bad.error).message

    good = valid_rows[0]
    assert good.proposal.id == prop.id
    assert good.diff is not None
    assert good.error is None
    # Every control is withheld listing-wide once anything is blocked, since
    # the untouched strict loader would refuse acting on ANY proposal here.
    assert good.can_approve is False
    assert good.can_reject is False


def test_blocked_state_actions_still_refused_by_strict_loader(tmp_path):
    vault = _vault(tmp_path)
    scope, prop = _propose(vault)
    _write_record(scope, "malformed.yaml", "action: classify\nmodule: [unterminated\n")

    listing = project_outbox(scope)
    assert listing.blocked is True
    good = next(row for row in listing.rows if row.proposal is not None)

    with pytest.raises(outbox.OutboxDestinationError):
        approve(scope, good.proposal.id)
    with pytest.raises(outbox.OutboxDestinationError):
        reject(scope, good.proposal.id)


# --- delete proposals: skipped exactly as today ------------------------------


def test_well_formed_delete_proposal_is_skipped_not_blocking(tmp_path):
    vault = _vault(tmp_path)
    scope, prop = _propose(vault)
    delete_id = "20260815T090703-" + "33" * 16
    _write_record(scope, f"{delete_id}.yaml", _delete_record(delete_id))

    listing = project_outbox(scope)

    assert listing.blocked is False
    assert [row.proposal.id for row in listing.rows] == [prop.id]


def test_outbox_of_only_deletes_renders_empty_not_blocked(tmp_path):
    vault = _vault(tmp_path)
    scope = Scope(vault, "demo")
    delete_id = "20260815T090703-" + "44" * 16
    _write_record(scope, f"{delete_id}.yaml", _delete_record(delete_id))

    listing = project_outbox(scope)

    assert listing.rows == ()
    assert listing.blocked is False


# --- never re-enters the strict loader ---------------------------------------


def test_projection_never_reenters_strict_loading(tmp_path, monkeypatch):
    vault = _vault(tmp_path)
    scope, prop = _propose(vault)

    def boom(*args, **kwargs):
        raise AssertionError("strict loader re-entered")

    monkeypatch.setattr(outbox, "get_proposal", boom)
    monkeypatch.setattr(outbox, "load_proposals", boom)
    monkeypatch.setattr(outbox, "preview_diff", boom)

    listing = project_outbox(scope)

    assert listing.blocked is False
    assert len(listing.rows) == 1
    assert listing.rows[0].proposal.id == prop.id
    assert listing.rows[0].diff is not None


# --- phase 3: diff rendering is row-local -------------------------------------


def test_undiffable_utf8_row_keeps_reject_loses_approve(tmp_path):
    vault = _vault(tmp_path)
    scope = Scope(vault, "demo")
    source = scope.resolve("00-inbox", "active", "note.md")
    # Invalid UTF-8 baked in *before* proposing, so the recorded source_sha256
    # matches these exact bytes and approve() reaches the same decode step
    # rather than refusing earlier on a hash mismatch.
    source.write_bytes(b"---\ntype: inbox-item\nsub: triage\n---\ncorrupt \xff\xfe body\n")
    prop = propose_classification(
        scope, source, module="11-knowledge", sub="kb", claimed_block="govern",
    )

    listing = project_outbox(scope)

    assert listing.blocked is False
    assert len(listing.rows) == 1
    row = listing.rows[0]
    assert row.proposal.id == prop.id
    assert row.diff is None
    assert row.error is not None
    assert row.can_reject is True
    assert row.can_approve is False


@pytest.mark.parametrize("condition", ["missing", "redirected", "non-utf8", "permission"])
def test_undiffable_row_error_matches_approve_outcome(tmp_path, monkeypatch, condition):
    vault = _vault(tmp_path)
    scope = Scope(vault, "demo")
    source = scope.resolve("00-inbox", "active", "note.md")
    if condition == "non-utf8":
        source.write_bytes(
            b"---\ntype: inbox-item\nsub: triage\n---\ncorrupt \xff\xfe body\n"
        )
    prop = propose_classification(
        scope, source, module="11-knowledge", sub="kb", claimed_block="govern",
    )

    if condition == "missing":
        source.unlink()
    elif condition == "redirected":
        # A real, non-symlinked file whose descriptor lies about its mode —
        # matching the safe-read contract's own non-regular-fstat case
        # (S6 Task 3), so the destination-canonicalization pre-check (which
        # only inspects `Path.is_symlink()`) still passes and this failure
        # is genuinely row-local (phase 3), not phase 2.
        real_fstat = outbox.os.fstat

        def nonregular_fstat(descriptor):
            result = real_fstat(descriptor)
            return os.stat_result(
                (stat.S_IFIFO | stat.S_IMODE(result.st_mode),) + tuple(result)[1:]
            )

        monkeypatch.setattr(outbox.os, "fstat", nonregular_fstat)
    elif condition == "permission":
        source.chmod(0)

    try:
        listing = project_outbox(scope)
        assert listing.blocked is False
        assert len(listing.rows) == 1
        row = listing.rows[0]
        assert row.diff is None
        assert row.error is not None
        assert row.can_reject is True
        assert row.can_approve is False

        try:
            approve(scope, prop.id)
            pytest.fail("approve did not raise for the injected condition")
        except Exception as exc:  # noqa: BLE001 - deliberately broad: compare codes
            approve_error = exc

        assert describe(row.error).code == describe(approve_error).code
    finally:
        if condition == "permission":
            source.chmod(0o644)


# --- phase 2: registry/path conditions propagate and abort -------------------


def test_phase2_config_propagates_and_aborts(tmp_path):
    vault = _vault(tmp_path)
    scope, prop = _propose(vault)
    (vault / "_system/archetypes.yaml").write_text(
        PROJECTION_ARCHETYPES.replace("submodules:\n", "submodules: []\ninvalid:\n"),
        encoding="utf-8",
    )

    with pytest.raises(Exception) as raised:
        project_outbox(scope)

    assert describe(raised.value).code == "E-CONFIG"


# --- the displayed diff carries no approval authority ------------------------


def test_approval_after_projection_still_revalidates(tmp_path):
    vault = _vault(tmp_path)
    scope, prop = _propose(vault)

    listing = project_outbox(scope)
    assert listing.blocked is False
    assert listing.rows[0].can_approve is True

    # Tamper the record after projecting, before approving.
    record = yaml.safe_load(prop.path.read_text(encoding="utf-8"))
    record["source_sha256"] = "A" * 64  # malformed: uppercase fails the hash regex
    prop.path.write_text(yaml.safe_dump(record), encoding="utf-8")

    with pytest.raises(outbox.OutboxDestinationError):
        approve(scope, prop.id)


# --- I2: every row of the design's seven-condition phase-1 table ---------------

_PHASE1_RECORDS = {
    "unparseable YAML": "action: classify\nmodule: [unterminated\n",
    "non-mapping record": "- just\n- a\n- list\n",
    "unknown action": "id: {id}\naction: transmute\n",
    "identity failure": "id: not-a-canonical-id\naction: classify\n",
    "malformed required field": (
        "id: {id}\naction: classify\nentity: demo\nsrc: 1\n"
        "source_sha256: 2\ndst: 3\nmodule: 4\nsub: 5\nblock: 6\n"
    ),
}


@pytest.mark.parametrize("condition", sorted(_PHASE1_RECORDS))
def test_every_phase1_condition_blocks_with_e_unreadable(tmp_path, condition):
    """Four of these were implemented but decorative: dropping the handler for
    non-UTF-8 bytes, record-read OSError, the `_to_proposal` wrap, or the
    unknown-action branch each left the whole suite green."""
    vault = _vault(tmp_path)
    scope, _ = _propose(vault)
    name = "20260816T101010-" + "a" * 32 + ".yaml"
    _write_record(scope, name, _PHASE1_RECORDS[condition].format(id=name[:-5]))

    listing = project_outbox(scope)

    assert listing.blocked is True
    bad = [row for row in listing.rows if row.proposal is None]
    assert len(bad) == 1
    assert isinstance(bad[0].error, outbox.UnreadableProposalRecord)
    assert describe(bad[0].error).code == "E-UNREADABLE"


def test_non_utf8_record_bytes_block_with_e_unreadable(tmp_path):
    """Design §3 sets this row in bold: one bad byte must not blank the screen."""
    vault = _vault(tmp_path)
    scope, _ = _propose(vault)
    path = scope.resolve("outbox") / ("20260816T101011-" + "b" * 32 + ".yaml")
    path.write_bytes(b"id: x\naction: classify\n\xff\xfe\n")

    listing = project_outbox(scope)

    assert listing.blocked is True
    bad = [row for row in listing.rows if row.proposal is None]
    assert len(bad) == 1
    assert describe(bad[0].error).code == "E-UNREADABLE"


def test_unreadable_record_bytes_block_with_e_unreadable(tmp_path):
    """Record-read OSError: also bold in the design, also previously unpinned."""
    vault = _vault(tmp_path)
    scope, _ = _propose(vault)
    path = scope.resolve("outbox") / ("20260816T101012-" + "c" * 32 + ".yaml")
    path.write_text("id: x\naction: classify\n", encoding="utf-8")
    path.chmod(0o000)
    try:
        listing = project_outbox(scope)
        assert listing.blocked is True
        bad = [row for row in listing.rows if row.proposal is None]
        assert len(bad) == 1
        assert describe(bad[0].error).code == "E-UNREADABLE"
    finally:
        path.chmod(0o644)


def test_malformed_delete_record_blocks_the_listing(tmp_path):
    """Design §8's matrix row: only a MALFORMED delete record blocks."""
    vault = _vault(tmp_path)
    scope, _ = _propose(vault)
    _write_record(scope, "20260816T101013-" + "d" * 32 + ".yaml",
                  "action: delete\nid: mismatched-id\n")

    listing = project_outbox(scope)

    assert listing.blocked is True


# --- I1: D2 pinned on the STRICT path, which the projection cannot see --------

def test_strict_loader_describes_an_unreadable_record_as_e_unreadable(tmp_path):
    """Ledger D2. `raise ... from None` here silently restores E-INVALID and
    every one of the 716 tests stayed green before this existed."""
    vault = _vault(tmp_path)
    scope, _ = _propose(vault)
    _write_record(scope, "20260816T101014-" + "e" * 32 + ".yaml",
                  "action: classify\nmodule: [unterminated\n")

    with pytest.raises(outbox.OutboxDestinationError) as raised:
        outbox.load_proposals(scope)

    # D1: the escaping type is unchanged, so tests/test_outbox.py stays green.
    assert type(raised.value) is outbox.OutboxDestinationError
    # D2: the cause carries the truthful description through the resolver.
    assert isinstance(raised.value.__cause__, outbox.UnreadableProposalRecord)
    assert describe(raised.value).code == "E-UNREADABLE"
