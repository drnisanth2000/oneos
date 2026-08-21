import os
import stat as stat_module

import pytest


def test_every_new_subtype_is_caught_as_its_base():
    from app.scope import CrossScopeError, OutOfScopeError, RedirectedPathError
    from app.outbox import ProposalSourceUnavailable, UnreadableProposalRecord, OutboxError
    from app.destinations import (
        UnsafeDestinationPath, RedirectedDestination, MissingDestination,
        InvalidSourceLeaf, RedirectedSourceLeaf, MissingSourceLeaf, NonCanonicalLeaf,
    )
    from app.git_transaction import (
        ReviewedStateConflict, ReviewedPathIntegrityError,
        ReviewedStateChanged, ReviewedPathUnavailable, InvalidTransactionPath,
    )
    pairs = [
        (OutOfScopeError, CrossScopeError),
        (RedirectedPathError, CrossScopeError),
        (ProposalSourceUnavailable, CrossScopeError),
        (UnreadableProposalRecord, OutboxError),
        (RedirectedDestination, UnsafeDestinationPath),
        (MissingDestination, UnsafeDestinationPath),
        (RedirectedSourceLeaf, InvalidSourceLeaf),
        (MissingSourceLeaf, InvalidSourceLeaf),
        (NonCanonicalLeaf, InvalidSourceLeaf),
        (ReviewedPathIntegrityError, ReviewedStateConflict),
        (ReviewedStateChanged, ReviewedStateConflict),
        (ReviewedPathUnavailable, ReviewedStateConflict),
        (InvalidTransactionPath, ReviewedStateConflict),
    ]
    for sub, base in pairs:
        with pytest.raises(base):
            raise sub("x")


# --- Task 3: the safe-read contract on `_read_no_follow_bytes` --------------
#
#   missing leaf                       -> FileNotFoundError (re-raised)
#   ELOOP / O_NOFOLLOW rejection       -> RedirectedPathError
#   fstat says non-regular             -> RedirectedPathError
#   any other OSError (perm, IO, race) -> ProposalSourceUnavailable
#
# Both raised types subclass CrossScopeError, so every existing `except`
# clause is unchanged and no refusal changes.


def test_safe_read_missing_leaf_raises_filenotfound(tmp_path):
    from app.outbox import _read_no_follow_bytes

    with pytest.raises(FileNotFoundError):
        _read_no_follow_bytes(tmp_path / "absent.md")


def test_safe_read_symlink_raises_redirected(tmp_path):
    from app.outbox import _read_no_follow_bytes
    from app.scope import CrossScopeError, RedirectedPathError

    target = tmp_path / "target.md"
    target.write_text("target\n", encoding="utf-8")
    link = tmp_path / "link.md"
    link.symlink_to(target)

    with pytest.raises(RedirectedPathError) as raised:
        _read_no_follow_bytes(link)
    assert isinstance(raised.value, CrossScopeError)


def test_safe_read_nonregular_raises_redirected(tmp_path, monkeypatch):
    # A real file whose descriptor reports a non-regular st_mode. Opening a
    # FIFO read-only blocks forever without a writer, so no FIFO fixture —
    # monkeypatch os.fstat instead.
    import app.outbox as outbox
    from app.scope import CrossScopeError, RedirectedPathError

    regular = tmp_path / "regular.md"
    regular.write_text("regular\n", encoding="utf-8")
    real_fstat = os.fstat

    def nonregular_fstat(descriptor):
        result = real_fstat(descriptor)
        return os.stat_result(
            (stat_module.S_IFIFO | stat_module.S_IMODE(result.st_mode),)
            + tuple(result)[1:]
        )

    monkeypatch.setattr(outbox.os, "fstat", nonregular_fstat)

    with pytest.raises(RedirectedPathError) as raised:
        outbox._read_no_follow_bytes(regular)
    assert isinstance(raised.value, CrossScopeError)


def test_safe_read_permission_error_raises_unavailable(tmp_path):
    from app.outbox import ProposalSourceUnavailable, _read_no_follow_bytes
    from app.scope import CrossScopeError

    unreadable = tmp_path / "unreadable.md"
    unreadable.write_text("unreadable\n", encoding="utf-8")
    unreadable.chmod(0)
    try:
        with pytest.raises(ProposalSourceUnavailable) as raised:
            _read_no_follow_bytes(unreadable)
    finally:
        unreadable.chmod(0o644)
    assert isinstance(raised.value, CrossScopeError)


def test_safe_read_replacement_race_raises_redirected(tmp_path):
    # A directory swapped in where the reviewed file used to be.
    from app.outbox import _read_no_follow_bytes
    from app.scope import CrossScopeError, RedirectedPathError

    swapped = tmp_path / "swapped.md"
    swapped.mkdir()

    with pytest.raises(RedirectedPathError) as raised:
        _read_no_follow_bytes(swapped)
    assert isinstance(raised.value, CrossScopeError)


def test_safe_read_other_oserror_raises_unavailable(tmp_path):
    from app.outbox import ProposalSourceUnavailable, _read_no_follow_bytes
    from app.scope import CrossScopeError

    parent = tmp_path / "sealed"
    parent.mkdir()
    leaf = parent / "receipt.md"
    leaf.write_text("sealed\n", encoding="utf-8")
    parent.chmod(0)
    try:
        with pytest.raises(ProposalSourceUnavailable) as raised:
            _read_no_follow_bytes(leaf)
    finally:
        parent.chmod(0o755)
    assert isinstance(raised.value, CrossScopeError)
