"""Gate-3 audit classifier (spec §11.3). Tests the pure logic; the CLI is git
glue over it."""
from tools.gate3_audit import audit


def test_flags_rogue_commit_and_direct_write():
    a = audit(
        commit_messages=[
            "outbox: approve X (a -> b)",
            "rename: old -> new",
            "registry: delete product q",
            "hotfix: sneaky direct edit",      # not a sanctioned prefix
        ],
        dirty_paths=[
            "demo/00-inbox/active/n.md",       # ingest — OK
            "demo/outbox/p.yaml",              # proposal — OK
            "demo/07-finance/active/edited.md",  # direct write to curated content — bad
        ],
    )
    assert a.violating_commits == ["hotfix: sneaky direct edit"]
    assert len(a.sanctioned_commits) == 3
    assert a.violating_writes == ["demo/07-finance/active/edited.md"]
    assert len(a.sanctioned_writes) == 2
    assert a.ok is False


def test_clean_session_passes():
    a = audit(
        commit_messages=["outbox: approve X", "registry: add workspace rti"],
        dirty_paths=["demo/00-inbox/active/dropped.md", "demo/outbox/prop.yaml"],
    )
    assert a.ok is True
    assert not a.violating_commits and not a.violating_writes


def test_empty_session_passes():
    a = audit([], [])
    assert a.ok is True
