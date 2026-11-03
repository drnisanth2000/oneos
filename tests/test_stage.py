"""The `stage` proposal kind (master view 3, Task 2).

A stage move is an ordinary Outbox proposal: the request writes a `stage`
record bound to the note bytes the owner saw, then approves it through
`outbox.approve()`'s existing S7 sequence. These tests pin the protocol:
one revertible commit with an `"approval"` receipt, refusal of stale bytes and
spent ids, a hook failure that consumes nothing, and a record that can never
leave the entity's Outbox blocked.

Temp git vaults only; the real vault is never touched.
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import textwrap
from pathlib import Path

import pytest
import yaml

import app.outbox as outbox
import app.stage as stage
from app.action_receipts import (
    SpentAction,
    parse_action_receipt,
    receipt_relative_path,
)
from app.destinations import (
    InvalidModule,
    RedirectedDestination,
    RedirectedSourceLeaf,
)
from app.note_text import NoteTextError, edit_note
from app.outbox import (
    MissingProposalSource,
    OutboxDestinationError,
    OutboxError,
    OutboxTransactionError,
    Proposal,
    StageProposal,
    StaleProposalSource,
    UnreadableProposalRecord,
    approve,
    load_proposals,
    preview_diff,
    project_outbox,
    propose_classification,
    reject,
)
from app.review_tokens import ReviewedProposalChanged
from app.scope import Scope
from app.stage import StageUnchanged, propose_and_approve_stage, propose_stage
from tests.conftest import (
    git_bytes,
    git_changed_paths,
    git_count_commits,
    git_entity_vault,
    git_head,
    git_head_message,
    git_index_entries,
    git_is_clean_apart_from_quarantine,
    git_status_bytes,
)

TODAY = "2026-11-02"

ARCHETYPES = textwrap.dedent(
    """\
    version: "2.0"
    flags: {}
    modules:
      00-inbox: {block: system}
      02-work: {block: build}
      03-alias: {block: build}
      11-knowledge: {block: govern}
      12-flat: {block: govern, lifecycle_pattern: false}
    submodules:
      00-inbox:
        triage: {name: Triage}
      11-knowledge:
        kb: {name: Knowledge base}
    archetypes:
      plain: {}
    """
)


def _note(title: str, status: str, *, sub: str | None = None, body: str = "Body.\n") -> str:
    sub_line = f"sub: {sub}\n" if sub else ""
    return (
        "---\n"
        "type: note\n"
        f"title: {title}\n"
        "entity: demo1\n"
        "product: null\n"
        f"status: {status}\n"
        "created: 2026-10-01\n"
        "updated: 2026-10-02\n"
        f"{sub_line}"
        "---\n"
        f"\n{body}"
    )


FILED = "demo1/02-work/active/plan.md"
INBOX = "demo1/00-inbox/active/note.md"
OTHER_INBOX = "demo1/00-inbox/active/other.md"
INBOX_DST = "demo1/11-knowledge/active/note.md"


def _vault(tmp_path: Path) -> Path:
    files = {
        "_system/archetypes.yaml": ARCHETYPES,
        FILED: _note("Plan the thing", "draft", body="First line.\n\nSecond line.\n"),
        INBOX: _note("Inbox note", "active", sub="triage", body="Inbox body.\n"),
        OTHER_INBOX: _note("Other note", "active", sub="triage", body="Other body.\n"),
        "demo1/02-work/archive/old.md": _note("Old", "done"),
        "demo1/02-work/status.md": _note("Status", "active"),
        "demo1/02-work/active/.sensitive/hidden.md": _note("Hidden", "draft"),
        "demo1/.sensitive/secret.md": _note("Secret", "draft"),
        "demo1/11-knowledge/active/.gitkeep": "",
        "demo1/12-flat/active/flat.md": _note("Flat", "draft"),
    }
    vault = git_entity_vault(tmp_path / "vault", ("demo1",), files)
    (vault / "demo1/02-work/active/link.md").symlink_to("plan.md")
    (vault / "demo1/03-alias").symlink_to("02-work")
    git_bytes(vault, "add", "-A")
    git_bytes(vault, "commit", "-q", "-m", "symlinks")
    return vault


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _source_sha(vault: Path, rel: str) -> str:
    return _sha((vault / rel).read_bytes())


def _records(vault: Path) -> list[Path]:
    return sorted((vault / "demo1/outbox").glob("*.yaml"))


def _quarantined(vault: Path) -> list[str]:
    return sorted(p.name for p in vault.rglob(".consumed/*.yaml"))


def _staging_debris(vault: Path) -> list[Path]:
    outbox_dir = vault / "demo1/outbox"
    if not outbox_dir.exists():
        return []
    return sorted(p for p in outbox_dir.iterdir() if p.name.startswith(".staging"))


def _receipt(vault: Path, proposal_id: str):
    rel = receipt_relative_path("demo1", proposal_id)
    return parse_action_receipt(Path(rel), git_bytes(vault, "show", f"HEAD:{rel}"))


def _revert_head(vault: Path) -> None:
    subprocess.run(
        ["git", "revert", "--no-edit", "HEAD"], cwd=vault, check=True, capture_output=True
    )


def _stage_filed(scope: Scope, **kwargs):
    kwargs.setdefault("src", FILED)
    if "source_sha256" not in kwargs:
        kwargs["source_sha256"] = _source_sha(scope.root, kwargs["src"])
    kwargs.setdefault("set_status", "waiting")
    kwargs.setdefault("updated", TODAY)
    return propose_stage(scope, **kwargs)


def _classify(scope: Scope, rel: str = INBOX) -> tuple[Proposal, str]:
    prop = propose_classification(
        scope, scope.root / rel, module="11-knowledge", sub="kb", claimed_block="govern"
    )
    return prop, outbox.get_proposal_review(scope, prop.id).sha256


def _apply_unified(old: str, diff: str) -> str:
    """Apply a unified diff produced by difflib to `old` (test oracle)."""
    lines = diff.splitlines(True)
    index = next(i for i, line in enumerate(lines) if line.startswith("--- ")) + 2
    old_lines = old.splitlines(True)
    out: list[str] = []
    position = 0
    while index < len(lines):
        match = re.match(r"@@ -(\d+)(?:,(\d+))? \+\d+(?:,\d+)? @@", lines[index])
        assert match, lines[index]
        index += 1
        start = int(match.group(1)) - (0 if match.group(2) == "0" else 1)
        out.extend(old_lines[position:start])
        position = start
        while index < len(lines) and not lines[index].startswith("@@"):
            tag, text = lines[index][0], lines[index][1:]
            index += 1
            if tag in " -":
                assert old_lines[position] == text
                position += 1
            if tag in " +":
                out.append(text)
    out.extend(old_lines[position:])
    return "".join(out)


# --- one commit, one receipt, revertible -----------------------------------


def test_in_place_stage_is_one_commit_with_the_note_and_receipt_and_reverts(tmp_path):
    vault = _vault(tmp_path)
    scope = Scope(vault, "demo1")
    before = (vault / FILED).read_bytes()
    commits = git_count_commits(vault)

    proposal_id, digest = _stage_filed(scope)
    record = vault / "demo1/outbox" / f"{proposal_id}.yaml"
    assert _sha(record.read_bytes()) == digest
    assert git_count_commits(vault) == commits

    result = approve(scope, proposal_id, digest)

    assert isinstance(result, StageProposal)
    assert git_count_commits(vault) == commits + 1
    receipt_rel = receipt_relative_path("demo1", proposal_id)
    assert git_changed_paths(vault) == sorted([FILED, receipt_rel])
    assert git_head_message(vault) == f"outbox: stage {proposal_id} ({FILED})"
    receipt = _receipt(vault, proposal_id)
    assert receipt.action_kind == "approval"
    assert receipt.proposal_id == proposal_id
    assert receipt.review_sha256 == digest
    expected = edit_note(
        before.decode(), status="waiting", updated=TODAY, body=None, comment=None
    ).encode()
    assert (vault / FILED).read_bytes() == expected
    assert not record.exists()
    assert _quarantined(vault) == [f"{proposal_id}.yaml"]
    assert git_is_clean_apart_from_quarantine(vault)

    _revert_head(vault)
    assert (vault / FILED).read_bytes() == before
    assert not (vault / receipt_rel).exists()


def test_body_comment_and_status_land_in_one_commit(tmp_path):
    vault = _vault(tmp_path)
    scope = Scope(vault, "demo1")
    before = (vault / FILED).read_bytes()
    commits = git_count_commits(vault)

    result = propose_and_approve_stage(
        scope,
        src=FILED,
        source_sha256=_sha(before),
        set_status="done",
        updated=TODAY,
        body="Rewritten\r\nbody\r\n",
        comment="Pay from savings",
    )

    assert isinstance(result, StageProposal)
    assert git_count_commits(vault) == commits + 1
    after = (vault / FILED).read_text()
    assert "status: done\n" in after
    assert f"updated: {TODAY}\n" in after
    assert after.endswith(f"\nRewritten\nbody\n\nComment ({TODAY}): Pay from savings\n")
    _revert_head(vault)
    assert (vault / FILED).read_bytes() == before


def test_keep_status_with_an_edited_body(tmp_path):
    vault = _vault(tmp_path)
    scope = Scope(vault, "demo1")
    result = propose_and_approve_stage(
        scope, src=FILED, source_sha256=_source_sha(vault, FILED),
        set_status=None, updated=TODAY, body="Only the body\n",
    )
    assert isinstance(result, StageProposal)
    after = (vault / FILED).read_text()
    assert "status: draft\n" in after
    assert after.endswith("\nOnly the body\n")


def test_inbox_stage_moves_to_its_destination_in_one_revertible_commit(tmp_path):
    vault = _vault(tmp_path)
    scope = Scope(vault, "demo1")
    before = (vault / INBOX).read_bytes()
    commits = git_count_commits(vault)

    proposal_id, digest = propose_stage(
        scope, src=INBOX, source_sha256=_sha(before), set_status="draft",
        updated=TODAY, module="11-knowledge", sub="kb",
    )
    approve(scope, proposal_id, digest)

    assert git_count_commits(vault) == commits + 1
    receipt_rel = receipt_relative_path("demo1", proposal_id)
    assert git_changed_paths(vault) == sorted([INBOX, INBOX_DST, receipt_rel])
    assert git_head_message(vault) == f"outbox: stage {proposal_id} ({INBOX} → {INBOX_DST})"
    assert _receipt(vault, proposal_id).action_kind == "approval"
    assert not (vault / INBOX).exists()
    moved = (vault / INBOX_DST).read_text()
    assert "sub: kb\n" in moved and "sub: triage" not in moved
    assert "status: draft\n" in moved and f"updated: {TODAY}\n" in moved
    assert moved.endswith("\nInbox body.\n")
    assert git_is_clean_apart_from_quarantine(vault)

    _revert_head(vault)
    assert (vault / INBOX).read_bytes() == before
    assert not (vault / INBOX_DST).exists()


# --- superseding a pending classification ---------------------------------


def test_stage_supersedes_a_pending_classification_in_the_same_commit(tmp_path):
    vault = _vault(tmp_path)
    scope = Scope(vault, "demo1")
    classify, classify_digest = _classify(scope)
    commits = git_count_commits(vault)

    result = propose_and_approve_stage(
        scope, src=INBOX, source_sha256=_source_sha(vault, INBOX),
        set_status="waiting", updated=TODAY,
        supersedes=classify.id, supersedes_review_sha256=classify_digest,
    )

    assert isinstance(result, StageProposal)
    assert (result.module, result.sub, result.dst) == ("11-knowledge", "kb", INBOX_DST)
    assert git_count_commits(vault) == commits + 1
    stage_receipt = receipt_relative_path("demo1", result.id)
    assert git_changed_paths(vault) == sorted([INBOX, INBOX_DST, stage_receipt])
    assert not (vault / receipt_relative_path("demo1", classify.id)).exists()
    assert _records(vault) == []
    assert _quarantined(vault) == sorted([f"{classify.id}.yaml", f"{result.id}.yaml"])
    assert git_is_clean_apart_from_quarantine(vault)
    listing = project_outbox(scope)
    assert not listing.blocked and listing.rows == ()


def test_supersede_with_a_wrong_digest_writes_nothing(tmp_path):
    vault = _vault(tmp_path)
    scope = Scope(vault, "demo1")
    classify, _ = _classify(scope)
    with pytest.raises(ReviewedProposalChanged):
        propose_stage(
            scope, src=INBOX, source_sha256=_source_sha(vault, INBOX),
            set_status="waiting", updated=TODAY,
            supersedes=classify.id, supersedes_review_sha256="0" * 64,
        )
    assert [p.stem for p in _records(vault)] == [classify.id]


def test_superseded_record_rewritten_after_proposal_refuses_approval(tmp_path):
    vault = _vault(tmp_path)
    scope = Scope(vault, "demo1")
    classify, classify_digest = _classify(scope)
    proposal_id, digest = propose_stage(
        scope, src=INBOX, source_sha256=_source_sha(vault, INBOX),
        set_status="waiting", updated=TODAY,
        supersedes=classify.id, supersedes_review_sha256=classify_digest,
    )
    classify.path.write_bytes(classify.path.read_bytes() + b"# rewritten\n")
    head = git_head(vault)

    with pytest.raises(ReviewedProposalChanged):
        approve(scope, proposal_id, digest)

    assert git_head(vault) == head
    assert (vault / INBOX).exists() and not (vault / INBOX_DST).exists()
    assert len(_records(vault)) == 2


def test_supersede_must_name_a_classification_of_the_same_item(tmp_path):
    vault = _vault(tmp_path)
    scope = Scope(vault, "demo1")
    other, other_digest = _classify(scope, OTHER_INBOX)
    with pytest.raises(OutboxDestinationError):
        propose_stage(
            scope, src=INBOX, source_sha256=_source_sha(vault, INBOX),
            set_status="waiting", updated=TODAY,
            supersedes=other.id, supersedes_review_sha256=other_digest,
        )
    assert [p.stem for p in _records(vault)] == [other.id]


def test_inbox_item_with_a_pending_classification_must_supersede_it(tmp_path):
    vault = _vault(tmp_path)
    scope = Scope(vault, "demo1")
    classify, _ = _classify(scope)
    with pytest.raises(OutboxDestinationError):
        propose_stage(
            scope, src=INBOX, source_sha256=_source_sha(vault, INBOX),
            set_status="waiting", updated=TODAY, module="11-knowledge", sub="kb",
        )
    assert [p.stem for p in _records(vault)] == [classify.id]


# --- stale, spent, hook failure --------------------------------------------


def test_stale_source_at_proposal_writes_nothing(tmp_path):
    vault = _vault(tmp_path)
    scope = Scope(vault, "demo1")
    shown = _source_sha(vault, FILED)
    (vault / FILED).write_bytes((vault / FILED).read_bytes() + b"edited elsewhere\n")
    head = git_head(vault)

    with pytest.raises(StaleProposalSource):
        propose_and_approve_stage(
            scope, src=FILED, source_sha256=shown, set_status="done", updated=TODAY
        )

    assert git_head(vault) == head
    assert _records(vault) == [] and _quarantined(vault) == []
    assert _staging_debris(vault) == []


def test_stale_source_after_publishing_is_refused_and_quarantined(tmp_path, monkeypatch):
    vault = _vault(tmp_path)
    scope = Scope(vault, "demo1")
    unrelated, unrelated_digest = _classify(scope, OTHER_INBOX)
    real_approve = outbox.approve
    edited = (vault / FILED).read_bytes() + b"edited elsewhere\n"

    def edit_then_approve(scope_, proposal_id, review_sha256):
        (vault / FILED).write_bytes(edited)
        return real_approve(scope_, proposal_id, review_sha256)

    monkeypatch.setattr(outbox, "approve", edit_then_approve)
    head = git_head(vault)

    with pytest.raises(StaleProposalSource):
        propose_and_approve_stage(
            scope, src=FILED, source_sha256=_source_sha(vault, FILED),
            set_status="done", updated=TODAY,
        )
    monkeypatch.setattr(outbox, "approve", real_approve)

    assert git_head(vault) == head
    assert (vault / FILED).read_bytes() == edited
    assert [p.stem for p in _records(vault)] == [unrelated.id]
    assert len(_quarantined(vault)) == 1
    listing = project_outbox(scope)
    assert not listing.blocked
    assert [(r.proposal.id, r.can_approve) for r in listing.rows] == [(unrelated.id, True)]
    approve(scope, unrelated.id, unrelated_digest)
    assert git_head_message(vault).startswith("outbox: approve")


def test_stale_stage_row_is_rejectable_but_not_approvable(tmp_path):
    vault = _vault(tmp_path)
    scope = Scope(vault, "demo1")
    proposal_id, digest = _stage_filed(scope)
    (vault / FILED).write_bytes((vault / FILED).read_bytes() + b"later\n")

    listing = project_outbox(scope)
    assert not listing.blocked
    (row,) = listing.rows
    assert isinstance(row.error, StaleProposalSource)
    assert (row.can_approve, row.can_reject, row.review_sha256) == (False, True, digest)
    with pytest.raises(StaleProposalSource):
        approve(scope, proposal_id, digest)
    reject(scope, proposal_id, digest)
    assert _records(vault) == []


def test_spent_replay_returns_spent_and_never_commits_twice(tmp_path):
    vault = _vault(tmp_path)
    scope = Scope(vault, "demo1")
    proposal_id, digest = _stage_filed(scope)
    approve(scope, proposal_id, digest)
    commits = git_count_commits(vault)

    replay = approve(scope, proposal_id, digest)

    assert isinstance(replay, SpentAction)
    assert replay.receipt.proposal_id == proposal_id
    assert git_count_commits(vault) == commits


def test_second_press_with_the_same_shown_bytes_is_refused(tmp_path):
    vault = _vault(tmp_path)
    scope = Scope(vault, "demo1")
    shown = _source_sha(vault, FILED)
    kwargs = dict(src=FILED, source_sha256=shown, set_status="waiting", updated=TODAY)
    propose_and_approve_stage(scope, **kwargs)
    commits = git_count_commits(vault)

    with pytest.raises(StaleProposalSource):
        propose_and_approve_stage(scope, **kwargs)

    assert git_count_commits(vault) == commits
    assert _records(vault) == []


def _failing_hook(vault: Path) -> None:
    hook = vault / ".git/hooks/pre-commit"
    hook.write_text("#!/bin/sh\necho refused by policy >&2\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)


def test_pre_commit_hook_failure_consumes_nothing(tmp_path):
    vault = _vault(tmp_path)
    scope = Scope(vault, "demo1")
    classify, classify_digest = _classify(scope)
    before = (vault / INBOX).read_bytes()
    proposal_id, digest = propose_stage(
        scope, src=INBOX, source_sha256=_sha(before), set_status="waiting",
        updated=TODAY, supersedes=classify.id, supersedes_review_sha256=classify_digest,
    )
    records = {p.name: p.read_bytes() for p in _records(vault)}
    head = git_head(vault)
    _failing_hook(vault)
    status = git_status_bytes(vault)
    index = git_index_entries(vault)

    with pytest.raises(OutboxTransactionError):
        approve(scope, proposal_id, digest)

    assert git_head(vault) == head
    assert (vault / INBOX).read_bytes() == before
    assert not (vault / INBOX_DST).exists()
    assert {p.name: p.read_bytes() for p in _records(vault)} == records
    assert _quarantined(vault) == []
    assert git_status_bytes(vault) == status
    assert git_index_entries(vault) == index


def test_hook_failure_through_propose_and_approve_quarantines_only_the_stage_record(
    tmp_path,
):
    vault = _vault(tmp_path)
    scope = Scope(vault, "demo1")
    classify, classify_digest = _classify(scope)
    head = git_head(vault)
    _failing_hook(vault)

    with pytest.raises(OutboxTransactionError):
        propose_and_approve_stage(
            scope, src=INBOX, source_sha256=_source_sha(vault, INBOX),
            set_status="waiting", updated=TODAY,
            supersedes=classify.id, supersedes_review_sha256=classify_digest,
        )

    assert git_head(vault) == head
    assert [p.stem for p in _records(vault)] == [classify.id]
    assert len(_quarantined(vault)) == 1
    (vault / ".git/hooks/pre-commit").unlink()
    approve(scope, classify.id, classify_digest)
    assert (vault / INBOX_DST).exists()


# --- a record can never block the Outbox -----------------------------------


def test_a_stranded_stage_record_is_listed_not_blocking(tmp_path):
    vault = _vault(tmp_path)
    scope = Scope(vault, "demo1")
    classify, classify_digest = _classify(scope, OTHER_INBOX)
    proposal_id, digest = _stage_filed(scope)

    listing = project_outbox(scope)
    assert not listing.blocked
    rows = {row.proposal.id: row for row in listing.rows}
    assert rows[proposal_id].can_approve and rows[proposal_id].can_reject
    assert rows[proposal_id].review_sha256 == digest
    assert rows[proposal_id].title == "Plan the thing"
    assert {type(p) for p in load_proposals(scope)} == {Proposal, StageProposal}

    approve(scope, classify.id, classify_digest)
    reject(scope, proposal_id, digest)
    assert _records(vault) == []
    assert f"{proposal_id}.yaml" in _quarantined(vault)


def test_a_crash_between_write_and_publish_leaves_no_record(tmp_path, monkeypatch):
    vault = _vault(tmp_path)
    scope = Scope(vault, "demo1")
    classify, classify_digest = _classify(scope, OTHER_INBOX)
    seen: list[list[str]] = []

    def crash(from_fd, from_name, to_fd, to_name):
        seen.append(sorted(os.listdir(from_fd)))
        raise OSError(5, "simulated crash before publish")

    monkeypatch.setattr(stage, "_move_no_replace", crash)

    with pytest.raises(OutboxError):
        _stage_filed(scope)

    assert seen and seen[0] == ["record.part"]
    assert [p.stem for p in _records(vault)] == [classify.id]
    assert _staging_debris(vault) == []
    assert not project_outbox(scope).blocked
    approve(scope, classify.id, classify_digest)


def test_staging_debris_from_a_real_crash_is_invisible_to_the_outbox(tmp_path):
    vault = _vault(tmp_path)
    scope = Scope(vault, "demo1")
    classify, classify_digest = _classify(scope, OTHER_INBOX)
    debris = vault / "demo1/outbox/.staging-deadbeef"
    debris.mkdir()
    (debris / "record.part").write_bytes(b"action: stage\nid: trunc")

    listing = project_outbox(scope)
    assert not listing.blocked
    assert [row.proposal.id for row in listing.rows] == [classify.id]
    approve(scope, classify.id, classify_digest)
    assert git_head_message(vault).startswith("outbox: approve")


def test_the_stage_validator_refuses_every_malformed_shape(tmp_path):
    vault = _vault(tmp_path)
    scope = Scope(vault, "demo1")
    proposal_id, _ = _stage_filed(scope)
    path = vault / "demo1/outbox" / f"{proposal_id}.yaml"
    record = yaml.safe_load(path.read_bytes())
    for broken in _MALFORMED(record):
        with pytest.raises(UnreadableProposalRecord):
            stage.stage_from_record(path, broken)


def _MALFORMED(record: dict) -> list[dict]:
    """Every way a tampered or version-skewed stage record can be wrong."""
    missing = dict(record)
    del missing["comment"]
    return [
        dict(record, extra="x"),
        missing,
        dict(record, set_status="active"),
        dict(record, set_status=["draft"]),
        dict(record, set_status={"a": 1}),
        dict(record, src="demo1/.sensitive/secret.md"),
        dict(record, dst="demo1/02-work/active/other.md"),
        dict(record, updated="2026-11-31"),
        dict(record, body=["not", "text"]),
        {**record, 1: "non-string key"},
        dict(record, id="20260101T000000-" + "b" * 32),
    ]


@pytest.mark.parametrize("index", range(11))
def test_a_malformed_stage_record_is_row_local_and_only_rejectable(tmp_path, index):
    vault = _vault(tmp_path)
    scope = Scope(vault, "demo1")
    classify, classify_digest = _classify(scope, OTHER_INBOX)
    proposal_id, _ = _stage_filed(scope)
    path = vault / "demo1/outbox" / f"{proposal_id}.yaml"
    broken = _MALFORMED(yaml.safe_load(path.read_bytes()))[index]
    path.write_bytes(yaml.safe_dump(broken, sort_keys=False).encode())
    digest = _sha(path.read_bytes())
    head = git_head(vault)

    listing = project_outbox(scope)
    assert not listing.blocked
    rows = {row.proposal.id: row for row in listing.rows}
    bad = rows[proposal_id]
    assert bad.error is not None and bad.diff is None
    assert (bad.can_approve, bad.can_reject, bad.review_sha256) == (False, True, digest)
    assert rows[classify.id].can_approve

    with pytest.raises(UnreadableProposalRecord):
        approve(scope, proposal_id, digest)
    assert git_head(vault) == head
    with pytest.raises(ReviewedProposalChanged):
        reject(scope, proposal_id, "0" * 64)
    assert path.exists()

    reject(scope, proposal_id, digest)
    assert not path.exists()
    assert f"{proposal_id}.yaml" in _quarantined(vault)
    approve(scope, classify.id, classify_digest)
    assert git_head_message(vault).startswith("outbox: approve")


def test_an_unknown_non_stage_action_still_blocks(tmp_path):
    vault = _vault(tmp_path)
    scope = Scope(vault, "demo1")
    proposal_id, _ = _stage_filed(scope)
    path = vault / "demo1/outbox" / f"{proposal_id}.yaml"
    record = yaml.safe_load(path.read_bytes())
    path.write_bytes(yaml.safe_dump(dict(record, action="restage")).encode())
    assert project_outbox(scope).blocked


# --- refusals -------------------------------------------------------------


@pytest.mark.parametrize(
    ("src", "refusal"),
    [
        ("demo1/.sensitive/secret.md", OutboxDestinationError),
        ("demo1/02-work/active/.sensitive/hidden.md", OutboxDestinationError),
        ("demo1/12-flat/active/flat.md", InvalidModule),
        ("demo1/99-missing/active/plan.md", InvalidModule),
        ("demo1/02-work/archive/old.md", OutboxDestinationError),
        ("demo1/02-work/status.md", OutboxDestinationError),
        ("demo1/02-work/active/link.md", RedirectedSourceLeaf),
        ("demo1/03-alias/active/plan.md", RedirectedDestination),
        ("demo1/02-work/active/../active/plan.md", OutboxDestinationError),
        ("demo1/02-work/active/./plan.md", OutboxDestinationError),
        ("/demo1/02-work/active/plan.md", OutboxDestinationError),
        ("demo2/02-work/active/plan.md", OutboxDestinationError),
        ("demo1/02-work/active/plan.txt", OutboxDestinationError),
    ],
)
def test_unsafe_or_non_lifecycle_sources_are_refused_without_writing(tmp_path, src, refusal):
    vault = _vault(tmp_path)
    scope = Scope(vault, "demo1")
    head = git_head(vault)
    target = vault / src.lstrip("/")
    # The real hash wherever the bytes exist, so a stale-source refusal can
    # never stand in for the path refusal under test.
    sha = _sha(target.read_bytes()) if target.is_file() else "a" * 64
    with pytest.raises(refusal):
        propose_stage(
            scope, src=src, source_sha256=sha, set_status="waiting", updated=TODAY
        )
    assert git_head(vault) == head
    assert _records(vault) == [] and _staging_debris(vault) == []


def test_a_record_whose_module_stops_qualifying_stays_rejectable(tmp_path):
    vault = _vault(tmp_path)
    scope = Scope(vault, "demo1")
    proposal_id, digest = _stage_filed(scope)
    archetypes = vault / "_system/archetypes.yaml"
    archetypes.write_text(
        archetypes.read_text().replace(
            "02-work: {block: build}", "02-work: {block: build, lifecycle_pattern: false}"
        )
    )
    head = git_head(vault)

    listing = project_outbox(scope)
    assert not listing.blocked
    (row,) = listing.rows
    assert isinstance(row.error, OutboxDestinationError)
    assert (row.can_approve, row.can_reject) == (False, True)
    with pytest.raises(OutboxDestinationError):
        approve(scope, proposal_id, digest)
    assert git_head(vault) == head
    reject(scope, proposal_id, digest)
    assert _records(vault) == []


@pytest.mark.parametrize("status", ["active", "Draft", "review", "", "pending"])
def test_only_stage_statuses_are_written(tmp_path, status):
    vault = _vault(tmp_path)
    scope = Scope(vault, "demo1")
    with pytest.raises(OutboxDestinationError):
        _stage_filed(scope, set_status=status)
    assert _records(vault) == []


def test_keep_status_is_only_for_filed_notes(tmp_path):
    vault = _vault(tmp_path)
    scope = Scope(vault, "demo1")
    with pytest.raises(OutboxDestinationError):
        propose_stage(
            scope, src=INBOX, source_sha256=_source_sha(vault, INBOX), set_status=None,
            updated=TODAY, module="11-knowledge", sub="kb",
        )
    assert _records(vault) == []


def test_a_note_the_editor_refuses_is_refused_before_writing(tmp_path):
    vault = _vault(tmp_path)
    scope = Scope(vault, "demo1")
    with pytest.raises(NoteTextError):
        _stage_filed(scope, comment="two\nlines")
    with pytest.raises(NoteTextError):
        _stage_filed(scope, updated="2026-13-01")
    assert _records(vault) == []


def test_a_press_that_changes_nothing_writes_nothing(tmp_path):
    vault = _vault(tmp_path)
    scope = Scope(vault, "demo1")
    with pytest.raises(StageUnchanged):
        _stage_filed(scope, set_status="draft", updated="2026-10-02")
    assert _records(vault) == []


def test_missing_source_is_refused(tmp_path):
    vault = _vault(tmp_path)
    scope = Scope(vault, "demo1")
    sha = _source_sha(vault, FILED)
    (vault / FILED).unlink()
    with pytest.raises(MissingProposalSource):
        _stage_filed(scope, source_sha256=sha)
    assert _records(vault) == []


# --- the review shows exactly what is applied ------------------------------


@pytest.mark.parametrize("kind", ["filed", "inbox"])
def test_outbox_diff_equals_the_applied_bytes(tmp_path, kind):
    vault = _vault(tmp_path)
    scope = Scope(vault, "demo1")
    src = FILED if kind == "filed" else INBOX
    before = (vault / src).read_text()
    extra = {} if kind == "filed" else {"module": "11-knowledge", "sub": "kb"}
    proposal_id, digest = propose_stage(
        scope, src=src, source_sha256=_sha(before.encode()), set_status="done",
        updated=TODAY, body="New body\nwith: colon\n---\n", comment="Done now", **extra,
    )
    (row,) = project_outbox(scope).rows
    prop = row.proposal
    assert row.diff == preview_diff(scope, prop)
    dst = FILED if kind == "filed" else INBOX_DST
    arrow = f"{src}" if kind == "filed" else f"{src} → {dst}"
    assert row.diff.startswith(f"stage: {arrow}\n")

    approve(scope, proposal_id, digest)

    assert _apply_unified(before, row.diff) == (vault / dst).read_text()


def test_record_round_trips_awkward_text_exactly(tmp_path):
    vault = _vault(tmp_path)
    scope = Scope(vault, "demo1")
    body = "  lead\ttab\nstatus: x\n---\n'quote' \"dq\" é ✓ \x85 trailing  \n\n"
    proposal_id, _ = _stage_filed(scope, body=body, comment="  ünïcode: yes  ")
    (prop,) = load_proposals(scope)
    assert prop.id == proposal_id
    assert prop.body == body
    assert prop.comment == "  ünïcode: yes  "
    assert (prop.src, prop.dst, prop.module, prop.set_status) == (
        FILED, FILED, "02-work", "waiting"
    )


# --- the existing Outbox screen copes with a stranded stage record ---------


def test_the_outbox_screen_lists_and_approves_a_stranded_stage_record(
    tmp_path, monkeypatch
):
    from tests.test_console_routes import _action_data, _outbox_proposal_client

    _main, client, classify_id = _outbox_proposal_client(tmp_path, monkeypatch)
    note = "alpha/02-work/active/plan.md"
    (tmp_path / note).write_text(
        _note("Plan", "draft").replace("entity: demo1", "entity: alpha"), encoding="utf-8"
    )
    git_bytes(tmp_path, "add", note)
    git_bytes(tmp_path, "commit", "-q", "-m", "note")
    scope = Scope(tmp_path, "alpha")
    proposal_id, _ = _stage_filed(scope, src=note)

    page = client.get("/outbox/alpha")
    assert page.status_code == 200
    assert proposal_id in page.text and classify_id in page.text
    assert f"stage: {note}" in page.text
    assert "E-UNREADABLE" not in page.text

    response = client.post("/outbox/alpha/approve", data=_action_data(tmp_path, proposal_id))
    assert response.status_code == 200
    assert git_head_message(tmp_path) == f"outbox: stage {proposal_id} ({note})"
    assert "status: waiting\n" in (tmp_path / note).read_text()


def _card_html(page: str, proposal_id: str) -> str:
    start = page.index(f'id="review-card-{proposal_id}-')
    end = page.find('id="review-card-', start + 1)
    return page[start:] if end == -1 else page[start:end]


def test_a_stranded_stage_row_says_what_approval_does_in_plain_words(
    tmp_path, monkeypatch
):
    from tests.test_console_routes import _outbox_proposal_client

    _main, client, _classify_id = _outbox_proposal_client(tmp_path, monkeypatch)
    plan, notes = "alpha/02-work/active/plan.md", "alpha/02-work/active/notes.md"
    mail = "alpha/00-inbox/active/mail.md"
    for rel, title, status in ((plan, "Plan", "draft"), (notes, "Notes", "draft"), (mail, "Mail", "active")):
        (tmp_path / rel).write_text(
            _note(title, status).replace("entity: demo1", "entity: alpha"), encoding="utf-8"
        )
        git_bytes(tmp_path, "add", rel)
    git_bytes(tmp_path, "commit", "-q", "-m", "notes")
    scope = Scope(tmp_path, "alpha")
    status_id, _ = _stage_filed(scope, src=plan, set_status="waiting")
    body_id, _ = _stage_filed(scope, src=notes, set_status=None, body="New body.\n")
    inbox_id, _ = _stage_filed(scope, src=mail, set_status="draft", module="02-work")

    page = client.get("/outbox/alpha")
    assert page.status_code == 200

    status_card = _card_html(page.text, status_id)
    assert "Approval changes this note's status to Waiting" in status_card
    assert "moves this document" not in status_card
    assert "<dt>From</dt>" not in status_card
    assert f"stage: {plan}" in status_card  # the technical diff is unchanged

    body_card = _card_html(page.text, body_id)
    assert "Approval updates this note" in body_card
    assert "moves this document" not in body_card and "status to" not in body_card

    inbox_card = _card_html(page.text, inbox_id)
    assert "Approval files this note into 02-work" in inbox_card
    assert "moves this document" not in inbox_card
    assert f"stage: {mail} → " in inbox_card


# --- review fixes: header-scoped sub, approval-time supersede checks -------


def _inbox_note(vault: Path, header_sub: str | None, body: str) -> bytes:
    text = _note("Inbox note", "active", sub=header_sub, body=body)
    (vault / INBOX).write_text(text, encoding="utf-8")
    git_bytes(vault, "add", INBOX)
    git_bytes(vault, "commit", "-q", "-m", "inbox shape")
    return text.encode()


@pytest.mark.parametrize("header_sub", [None, "triage"])
def test_moving_without_a_sub_never_touches_a_body_sub_line(tmp_path, header_sub):
    vault = _vault(tmp_path)
    scope = Scope(vault, "demo1")
    body = "First.\nsub: keep me\nLast.\n"
    before = _inbox_note(vault, header_sub, body)

    result = propose_and_approve_stage(
        scope, src=INBOX, source_sha256=_sha(before), set_status="draft",
        updated=TODAY, module="02-work", sub=None,
    )

    moved = (vault / result.dst).read_text()
    assert moved.endswith("\n" + body)
    header = moved[: moved.index("---\n", 4)]
    assert "sub:" not in header


def test_setting_a_sub_on_a_header_without_one_never_touches_the_body(tmp_path):
    vault = _vault(tmp_path)
    scope = Scope(vault, "demo1")
    body = "First.\nsub: keep me\nLast.\n"
    before = _inbox_note(vault, None, body)

    result = propose_and_approve_stage(
        scope, src=INBOX, source_sha256=_sha(before), set_status="draft",
        updated=TODAY, module="11-knowledge", sub="kb",
    )

    moved = (vault / result.dst).read_text()
    assert moved.endswith("\n" + body)
    header = moved[: moved.index("---\n", 4)]
    assert header.count("sub: kb\n") == 1


def test_an_empty_header_sub_that_would_swallow_a_line_is_refused(tmp_path):
    vault = _vault(tmp_path)
    scope = Scope(vault, "demo1")
    text = _note("Inbox note", "active", body="Body.\n").replace(
        "updated: 2026-10-02\n", "updated: 2026-10-02\nsub:\nsource: folder\n"
    )
    (vault / INBOX).write_text(text, encoding="utf-8")
    with pytest.raises(NoteTextError):
        propose_stage(
            scope, src=INBOX, source_sha256=_sha(text.encode()), set_status="draft",
            updated=TODAY, module="11-knowledge", sub="kb",
        )
    assert _records(vault) == []


@pytest.mark.parametrize("src", [FILED, INBOX])
def test_an_impossible_header_date_is_a_note_text_error(tmp_path, src):
    vault = _vault(tmp_path)
    scope = Scope(vault, "demo1")
    text = (vault / src).read_text().replace("created: 2026-10-01", "created: 2026-02-30")
    (vault / src).write_text(text, encoding="utf-8")
    extra = {} if src == FILED else {"module": "11-knowledge", "sub": "kb"}
    with pytest.raises(NoteTextError):
        propose_stage(
            scope, src=src, source_sha256=_sha(text.encode()), set_status="done",
            updated=TODAY, **extra,
        )
    assert _records(vault) == []


def _rewrite(path: Path, **changes) -> str:
    record = yaml.safe_load(path.read_bytes())
    record.update(changes)
    path.write_bytes(yaml.safe_dump(record, sort_keys=False).encode())
    return _sha(path.read_bytes())


def test_a_superseded_record_that_no_longer_matches_is_refused_at_approval(tmp_path):
    vault = _vault(tmp_path)
    scope = Scope(vault, "demo1")
    classify, classify_digest = _classify(scope)
    proposal_id, _ = propose_stage(
        scope, src=INBOX, source_sha256=_source_sha(vault, INBOX),
        set_status="waiting", updated=TODAY,
        supersedes=classify.id, supersedes_review_sha256=classify_digest,
    )
    # The classification is rewritten (still a valid, canonical record) and
    # the stage record re-reviewed against it: both digests now match, so
    # only the approval-time identity check can refuse.
    new_classify_digest = _rewrite(classify.path, sub=None)
    stage_path = vault / "demo1/outbox" / f"{proposal_id}.yaml"
    digest = _rewrite(stage_path, supersedes_review_sha256=new_classify_digest)
    head = git_head(vault)

    with pytest.raises(OutboxDestinationError):
        approve(scope, proposal_id, digest)

    assert git_head(vault) == head
    assert (vault / INBOX).exists() and not (vault / INBOX_DST).exists()
    assert len(_records(vault)) == 2


def test_a_superseded_id_spent_before_the_lock_is_refused_under_it(tmp_path, monkeypatch):
    from app.action_receipts import make_action_receipt, render_action_receipt

    vault = _vault(tmp_path)
    scope = Scope(vault, "demo1")
    classify, classify_digest = _classify(scope)
    proposal_id, digest = propose_stage(
        scope, src=INBOX, source_sha256=_source_sha(vault, INBOX),
        set_status="waiting", updated=TODAY,
        supersedes=classify.id, supersedes_review_sha256=classify_digest,
    )
    # Another action spends the classification id after the unlocked check.
    receipt_rel = receipt_relative_path("demo1", classify.id)
    (vault / receipt_rel).parent.mkdir(parents=True, exist_ok=True)
    (vault / receipt_rel).write_bytes(
        render_action_receipt(make_action_receipt(classify.id, classify_digest, "approval"))
    )
    git_bytes(vault, "add", receipt_rel)
    git_bytes(vault, "commit", "-q", "-m", "concurrent approval")
    monkeypatch.setattr(stage, "_spent_action_before_review", lambda scope_, id_: None)
    head = git_head(vault)

    with pytest.raises(OutboxDestinationError):
        approve(scope, proposal_id, digest)

    assert git_head(vault) == head
    assert (vault / INBOX).exists()
    assert len(_records(vault)) == 2


def test_a_record_that_does_not_round_trip_is_never_published(tmp_path, monkeypatch):
    vault = _vault(tmp_path)
    scope = Scope(vault, "demo1")
    real = stage._render_record
    monkeypatch.setattr(
        stage, "_render_record", lambda record: real(dict(record, comment="altered"))
    )
    with pytest.raises(OutboxError):
        _stage_filed(scope)
    assert _records(vault) == [] and _staging_debris(vault) == []


def test_the_outbox_screen_offers_only_reject_for_a_malformed_stage_record(
    tmp_path, monkeypatch
):
    from tests.test_console_routes import _action_data, _outbox_proposal_client

    _main, client, classify_id = _outbox_proposal_client(tmp_path, monkeypatch)
    note = "alpha/02-work/active/plan.md"
    (tmp_path / note).write_text(
        _note("Plan", "draft").replace("entity: demo1", "entity: alpha"), encoding="utf-8"
    )
    git_bytes(tmp_path, "add", note)
    git_bytes(tmp_path, "commit", "-q", "-m", "note")
    proposal_id, _ = _stage_filed(Scope(tmp_path, "alpha"), src=note)
    path = tmp_path / "alpha/outbox" / f"{proposal_id}.yaml"
    _rewrite(path, set_status=["draft"])

    page = client.get("/outbox/alpha")
    assert page.status_code == 200
    assert "E-UNREADABLE" not in page.text
    assert "E-INVALID" in page.text
    assert page.text.count('class="approve"') == 1  # the classification's only
    assert page.text.count('class="reject"') == 2

    response = client.post("/outbox/alpha/reject", data=_action_data(tmp_path, proposal_id))
    assert response.status_code == 200
    assert not path.exists()
    assert (tmp_path / "alpha/outbox" / f"{classify_id}.yaml").exists()


# --- review round 2: the fence guard, odd sub spellings, parse-safe records --


def test_a_sub_change_that_would_swallow_the_closing_fence_is_refused(tmp_path):
    vault = _vault(tmp_path)
    scope = Scope(vault, "demo1")
    # `_apply_sub`'s `\s*` eats the empty `sub:` value's newline and the
    # closing fence; the comment line hides it from the mapping check, so
    # only the fence/body check stops `zz: 1` moving into the header.
    text = _note("Inbox note", "active", body="zz: 1\n---\ntail\n").replace(
        "updated: 2026-10-02\n---\n", "updated: 2026-10-02\n# c\nsub:\n---\n"
    )
    (vault / INBOX).write_text(text, encoding="utf-8")
    with pytest.raises(NoteTextError):
        propose_stage(
            scope, src=INBOX, source_sha256=_sha(text.encode()), set_status="draft",
            updated=TODAY, module="02-work", sub=None,
        )
    assert _records(vault) == []


@pytest.mark.parametrize("spelling", ['"sub": triage', "sub : triage", "'sub': triage"])
@pytest.mark.parametrize("sub", ["kb", None])
def test_a_sub_key_not_written_plainly_is_refused(tmp_path, spelling, sub):
    vault = _vault(tmp_path)
    scope = Scope(vault, "demo1")
    text = _note("Inbox note", "active", sub="triage").replace("sub: triage", spelling)
    (vault / INBOX).write_text(text, encoding="utf-8")
    module = "11-knowledge" if sub else "02-work"
    with pytest.raises(NoteTextError):
        propose_stage(
            scope, src=INBOX, source_sha256=_sha(text.encode()), set_status="draft",
            updated=TODAY, module=module, sub=sub,
        )
    assert _records(vault) == []


@pytest.mark.parametrize("value", ["2026-02-30", "!!timestamp nope"])
def test_an_unconstructible_stage_record_is_row_local_and_only_rejectable(tmp_path, value):
    vault = _vault(tmp_path)
    scope = Scope(vault, "demo1")
    classify, classify_digest = _classify(scope, OTHER_INBOX)
    proposal_id, _ = _stage_filed(scope)
    path = vault / "demo1/outbox" / f"{proposal_id}.yaml"
    raw = path.read_text()
    assert f"updated: '{TODAY}'" in raw
    path.write_text(raw.replace(f"updated: '{TODAY}'", f"updated: {value}"))
    digest = _sha(path.read_bytes())

    listing = project_outbox(scope)
    assert not listing.blocked
    rows = {row.proposal.id: row for row in listing.rows}
    assert (rows[proposal_id].can_approve, rows[proposal_id].can_reject) == (False, True)
    assert rows[proposal_id].review_sha256 == digest
    with pytest.raises(UnreadableProposalRecord):
        approve(scope, proposal_id, digest)
    reject(scope, proposal_id, digest)
    assert not path.exists()
    approve(scope, classify.id, classify_digest)


def test_an_unconstructible_classify_record_blocks_instead_of_crashing(tmp_path):
    vault = _vault(tmp_path)
    scope = Scope(vault, "demo1")
    classify, _ = _classify(scope)
    raw = classify.path.read_text()
    created = yaml.safe_load(raw)["created"]
    classify.path.write_text(raw.replace(f"created: '{created}'", "created: 2026-02-30"))
    assert "created: 2026-02-30" in classify.path.read_text()

    listing = project_outbox(scope)
    assert listing.blocked
    with pytest.raises(OutboxDestinationError):
        load_proposals(scope)
