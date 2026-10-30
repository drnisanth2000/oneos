"""Reminder store tests use only temporary synthetic state."""
import os
import stat
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

from app import reminders
from app.reminders import (
    InvalidReminder,
    Reminder,
    ReminderStore,
    ReminderStoreUnavailable,
    clean_subject,
    parse_due,
    reminder_store,
)

PLUS_TWO = timezone(timedelta(hours=2))


@pytest.fixture
def state(tmp_path):
    directory = tmp_path / "state"
    directory.mkdir()
    directory.chmod(0o700)
    return directory


def make(key="inbox/pay-card.md", entity="synthetic", subject="Pay card", day=4):
    return Reminder(key, entity, subject, datetime(2026, 11, day, 11, 0, tzinfo=PLUS_TWO))


def test_all_on_a_new_directory_is_empty_and_creates_nothing(state):
    assert ReminderStore(state).all() == {}
    assert list(state.iterdir()) == []


def test_save_then_all_round_trips_the_wall_time_and_offset(state):
    store = ReminderStore(state)
    store.save(make())
    loaded = store.all()["inbox/pay-card.md"]
    assert loaded.due_at.isoformat() == "2026-11-04T11:00:00+02:00"
    assert (loaded.entity, loaded.subject) == ("synthetic", "Pay card")


def test_save_creates_the_file_owner_only(state):
    old = os.umask(0)
    try:
        ReminderStore(state).save(make())
    finally:
        os.umask(old)
    assert stat.S_IMODE((state / "reminders.sqlite3").stat().st_mode) == 0o600


def test_save_upserts_by_key(state):
    store = ReminderStore(state)
    store.save(make())
    store.save(make(subject="Pay the card", day=5))
    found = store.all()
    assert list(found) == ["inbox/pay-card.md"]
    assert found["inbox/pay-card.md"].subject == "Pay the card"
    assert found["inbox/pay-card.md"].due_at.day == 5


def test_remove_is_idempotent(state):
    store = ReminderStore(state)
    store.remove("absent")  # no file yet: a no-op that creates nothing
    assert list(state.iterdir()) == []
    store.save(make())
    store.remove("inbox/pay-card.md")
    store.remove("inbox/pay-card.md")
    assert store.all() == {}


def test_rekey_moves_and_replaces(state):
    store = ReminderStore(state)
    store.rekey("a", "b")  # no file yet
    assert list(state.iterdir()) == []
    store.save(make(key="a", subject="From a"))
    store.save(make(key="b", subject="Old b"))
    store.rekey("missing", "b")
    assert set(store.all()) == {"a", "b"}
    store.rekey("a", "b")
    found = store.all()
    assert set(found) == {"b"}
    assert found["b"].subject == "From a"


def _dir_0755(state):
    state.chmod(0o755)


def _db_symlink(state):
    target = state.parent / "elsewhere.sqlite3"
    target.touch()
    (state / "reminders.sqlite3").symlink_to(target)


def _db_0644(state):
    path = state / "reminders.sqlite3"
    ReminderStore(state).save(make())
    path.chmod(0o644)


@pytest.mark.parametrize("break_it", [_dir_0755, _db_symlink, _db_0644])
def test_unsafe_store_is_unavailable(state, break_it):
    break_it(state)
    store = ReminderStore(state)
    with pytest.raises(ReminderStoreUnavailable):
        store.all()
    with pytest.raises(ReminderStoreUnavailable):
        store.save(make(key="other"))


def test_a_redirected_state_directory_is_unavailable(state, tmp_path):
    link = tmp_path / "link"
    link.symlink_to(state)
    with pytest.raises(ReminderStoreUnavailable):
        ReminderStore(link).all()


def test_a_garbled_database_is_unavailable(state):
    path = state / "reminders.sqlite3"
    path.write_bytes(b"not a database" * 100)
    path.chmod(0o600)
    with pytest.raises(ReminderStoreUnavailable):
        ReminderStore(state).all()


def test_save_refuses_a_naive_time_or_bad_subject(state):
    store = ReminderStore(state)
    with pytest.raises(InvalidReminder):
        store.save(Reminder("k", "e", "Pay card", datetime(2026, 11, 4, 11, 0)))
    with pytest.raises(InvalidReminder):
        store.save(make(subject="a\nb"))
    assert list(state.iterdir()) == []


def test_parse_due():
    assert parse_due("2026-11-04", "11:00", "120").isoformat() == "2026-11-04T11:00:00+02:00"
    assert parse_due("2026-11-04", "11:00", "-330").isoformat() == "2026-11-04T11:00:00-05:30"
    for bad in [
        ("2026-02-30", "11:00", "120"),
        ("2026-11-04", "25:00", "0"),
        ("2026-11-04", "11:00", "x"),
        ("2026-11-04", "11:00", "2000"),
        ("11/04/2026", "11:00", "0"),
        ("1969-12-31", "23:59", "0"),
        ("0001-01-01", "00:00", "0"),
    ]:
        with pytest.raises(InvalidReminder):
            parse_due(*bad)


def test_parse_due_extremes_order_without_overflow():
    earliest = parse_due("1970-01-01", "00:00", "840")
    latest = parse_due("9999-12-31", "23:59", "-840")
    assert earliest < latest
    assert parse_due("9999-12-31", "23:59", "840") < latest


def test_clean_subject():
    assert clean_subject("  Pay card  ") == "Pay card"
    assert clean_subject("x" * 120) == "x" * 120
    for bad in ["", "   ", "a\nb", "a\rb", "x" * 121]:
        with pytest.raises(InvalidReminder):
            clean_subject(bad)


def _git(vault, *args):
    return subprocess.run(
        ["git", "-C", str(vault), *args], check=True, capture_output=True, text=True
    ).stdout


def test_store_is_never_written_into_the_vault(state, tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    _git(vault, "init", "-q")
    (vault / "note.md").write_text("synthetic\n")
    _git(vault, "add", "note.md")
    _git(vault, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", "seed")
    head = _git(vault, "rev-parse", "HEAD")
    assert _git(vault, "status", "--porcelain") == ""
    store = ReminderStore(state)
    store.save(make())
    store.rekey("inbox/pay-card.md", "inbox/renamed.md")
    store.remove("inbox/renamed.md")
    assert _git(vault, "status", "--porcelain") == ""
    assert _git(vault, "rev-parse", "HEAD") == head


def test_reminder_store_is_none_without_the_env(monkeypatch):
    monkeypatch.delenv("ONEOS_AUTH_STATE_DIR", raising=False)
    assert reminder_store() is None


def test_reminder_store_uses_the_auth_state_directory(monkeypatch, state):
    monkeypatch.setenv("ONEOS_AUTH_STATE_DIR", str(state))
    store = reminder_store()
    assert store.path == state / "reminders.sqlite3"


def test_public_methods_declare_failure_contracts():
    for fn, expected in [
        (ReminderStore.all, {ReminderStoreUnavailable}),
        (ReminderStore.save, {ReminderStoreUnavailable, InvalidReminder}),
        (ReminderStore.remove, {ReminderStoreUnavailable}),
        (ReminderStore.rekey, {ReminderStoreUnavailable}),
        (parse_due, {InvalidReminder}),
        (clean_subject, {InvalidReminder}),
    ]:
        assert set(fn.__failure_contract__.raises) == expected
    assert reminders.reminder_store.__failure_contract__.raises == ()


def test_a_zero_byte_safe_file_means_no_reminders_and_is_left_alone(state):
    path = state / "reminders.sqlite3"
    path.touch(mode=0o600)
    path.chmod(0o600)
    store = ReminderStore(state)
    assert store.all() == {}
    assert path.stat().st_size == 0
    # A later save still works on top of it.
    store.save(make())
    assert set(store.all()) == {"inbox/pay-card.md"}


def _has_table(path):
    import sqlite3
    from contextlib import closing

    with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)) as db:
        return db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='reminders'"
        ).fetchone() is not None


def test_first_save_publishes_the_file_with_its_schema(state, monkeypatch):
    import app.git_transaction as git_transaction

    real = git_transaction._move_no_replace
    seen = {}

    def spy(from_fd, from_name, to_fd, to_name):
        # At the moment of publication the live path is still absent and the
        # staged file already holds the table.
        assert not (state / "reminders.sqlite3").exists()
        staged = next(state.glob(".reminders-*")) / from_name
        seen["table"] = _has_table(staged)
        seen["mode"] = stat.S_IMODE(staged.stat().st_mode)
        real(from_fd, from_name, to_fd, to_name)

    monkeypatch.setattr(git_transaction, "_move_no_replace", spy)
    ReminderStore(state).save(make())
    assert seen == {"table": True, "mode": 0o600}
    assert _has_table(state / "reminders.sqlite3")
    assert [p.name for p in state.iterdir()] == ["reminders.sqlite3"]


def test_a_failure_before_publication_leaves_nothing_behind(state, monkeypatch):
    import app.git_transaction as git_transaction

    def boom(*args):
        raise OSError("simulated crash before publication")

    monkeypatch.setattr(git_transaction, "_move_no_replace", boom)
    store = ReminderStore(state)
    with pytest.raises(ReminderStoreUnavailable):
        store.save(make())
    assert list(state.iterdir()) == []
    assert store.all() == {}
