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
    ReviewFlag,
    ReminderStoreUnavailable,
    calendar_event_id,
    clean_flag_comment,
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


def test_rename_entity_rewrites_only_that_entitys_exact_first_segment(state):
    store = ReminderStore(state)
    assert store.rename_entity("old", "new") == 0  # no file yet
    assert list(state.iterdir()) == []
    store.save(make(key="old/02-work/a.md", entity="old", subject="A"))
    store.save(make(key="old/00-inbox/old/b.md", entity="old", subject="B"))
    store.save(make(key="older/02-work/c.md", entity="older", subject="C"))
    store.save(make(key="other/old/d.md", entity="other", subject="D"))
    # A leftover row already sitting under a rewritten key is replaced.
    store.save(make(key="new/02-work/a.md", entity="new", subject="Stale"))
    assert store.rename_entity("old", "new") == 2
    found = store.all()
    assert set(found) == {
        "new/02-work/a.md", "new/00-inbox/old/b.md", "older/02-work/c.md", "other/old/d.md",
    }
    assert found["new/02-work/a.md"].subject == "A" and found["new/02-work/a.md"].entity == "new"
    assert found["new/00-inbox/old/b.md"].entity == "new"
    assert (found["older/02-work/c.md"].entity, found["other/old/d.md"].entity) == ("older", "other")
    assert store.rename_entity("old", "new") == 0


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


def test_default_subject_is_always_accepted_by_clean_subject():
    for title in ("x" * 130, "Re: a\nb\r\nc", "  spaced   out  ", "tab\tand\u2028sep\u0085x",
                  "y" * 119 + " " + "z" * 20, "é" * 300, "\n\n"):
        subject = reminders.default_subject(title)
        assert len(subject) <= 120
        if subject:
            assert clean_subject(subject) == subject
    assert reminders.default_subject("x" * 130) == "x" * 120
    assert reminders.default_subject("a\nb  c") == "a b c"
    assert reminders.default_subject("   ") == ""


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


# --- review flags ----------------------------------------------------------

WHEN = datetime(2026, 11, 3, 9, 30, tzinfo=PLUS_TWO)


def flag_of(key="synthetic/02-work/active/a.md", entity="synthetic", comment="Check the totals"):
    return ReviewFlag(key, entity, comment, WHEN)


def _tables(path):
    import sqlite3
    from contextlib import closing

    with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)) as db:
        return {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_flag_then_flags_round_trips_and_unflag_removes(state):
    store = ReminderStore(state)
    store.flag(flag_of())
    store.flag(flag_of(key="synthetic/02-work/active/b.md", comment=None))
    found = store.flags()
    assert set(found) == {"synthetic/02-work/active/a.md", "synthetic/02-work/active/b.md"}
    first = found["synthetic/02-work/active/a.md"]
    assert (first.entity, first.comment) == ("synthetic", "Check the totals")
    assert first.flagged_at.isoformat() == "2026-11-03T09:30:00+02:00"
    assert found["synthetic/02-work/active/b.md"].comment is None
    store.unflag("synthetic/02-work/active/a.md")
    store.unflag("synthetic/02-work/active/a.md")  # idempotent
    assert set(store.flags()) == {"synthetic/02-work/active/b.md"}


def test_flag_upserts_and_trims_the_comment(state):
    store = ReminderStore(state)
    store.flag(flag_of(comment="  first  "))
    store.flag(flag_of(comment="second"))
    found = store.flags()
    assert list(found) == ["synthetic/02-work/active/a.md"]
    assert found["synthetic/02-work/active/a.md"].comment == "second"
    store.flag(flag_of(comment="  padded  "))
    assert store.flags()["synthetic/02-work/active/a.md"].comment == "padded"


def test_flag_reads_create_nothing(state):
    store = ReminderStore(state)
    assert store.flags() == {}
    store.unflag("anything")
    assert list(state.iterdir()) == []


def test_a_reminders_only_file_has_no_flags_table_and_reads_never_add_one(state):
    store = ReminderStore(state)
    store.save(make())
    path = state / "reminders.sqlite3"
    assert "review_flags" not in _tables(path)
    assert store.flags() == {}
    store.unflag("nothing")
    store.rekey("a", "b")
    store.rename_entity("old", "new")
    store.remove("inbox/pay-card.md")
    assert "review_flags" not in _tables(path)
    store.flag(flag_of())
    assert "review_flags" in _tables(path)
    assert set(store.all()) == set()  # the reminder table is untouched by flags


def test_first_flag_publishes_the_file_and_creates_the_table(state):
    ReminderStore(state).flag(flag_of())
    assert [p.name for p in state.iterdir()] == ["reminders.sqlite3"]
    assert {"reminders", "review_flags"} <= _tables(state / "reminders.sqlite3")


def test_a_zero_byte_file_has_no_flags_and_is_left_alone(state):
    path = state / "reminders.sqlite3"
    path.touch(mode=0o600)
    path.chmod(0o600)
    store = ReminderStore(state)
    assert store.flags() == {}
    assert path.stat().st_size == 0
    store.flag(flag_of())
    assert set(store.flags()) == {"synthetic/02-work/active/a.md"}


def test_flag_refuses_bad_values_and_writes_nothing(state):
    store = ReminderStore(state)
    for bad in (
        ReviewFlag("k", "e", "a\nb", WHEN),
        ReviewFlag("k", "e", "", WHEN),
        ReviewFlag("k", "e", "   ", WHEN),
        ReviewFlag("k", "e", "x" * 501, WHEN),
        ReviewFlag("k", "e", "bell\x07", WHEN),
        ReviewFlag("k", "e", "ok", datetime(2026, 11, 3, 9, 30)),
        ReviewFlag("", "e", "ok", WHEN),
        ReviewFlag("k", " ", "ok", WHEN),
    ):
        with pytest.raises(InvalidReminder):
            store.flag(bad)
    with pytest.raises(InvalidReminder):
        store.flag("not a flag")
    assert list(state.iterdir()) == []


def test_clean_flag_comment():
    assert clean_flag_comment("  Look again  ") == "Look again"
    assert clean_flag_comment("x" * 500) == "x" * 500
    for bad in ["", "   ", "a\nb", "a\rb", "a\u2028b", "x" * 501, "a\x00b", None, 3]:
        with pytest.raises(InvalidReminder):
            clean_flag_comment(bad)


def test_rekey_moves_a_flag_with_its_reminder_in_one_go(state):
    store = ReminderStore(state)
    store.save(make(key="a", subject="From a"))
    store.flag(flag_of(key="a", comment="from a"))
    store.flag(flag_of(key="b", comment="stale b"))
    store.rekey("a", "b")
    assert set(store.all()) == {"b"}
    found = store.flags()
    assert set(found) == {"b"} and found["b"].comment == "from a"
    # A flag alone (no reminder) moves too.
    store.flag(flag_of(key="c", comment="alone"))
    store.rekey("c", "d")
    assert set(store.flags()) == {"b", "d"}
    store.rekey("missing", "b")
    assert store.flags()["b"].comment == "from a"


def test_rename_entity_moves_flags_key_prefix_and_entity(state):
    store = ReminderStore(state)
    store.flag(flag_of(key="old/02-work/a.md", entity="old", comment="A"))
    store.flag(flag_of(key="older/02-work/c.md", entity="older", comment="C"))
    store.flag(flag_of(key="other/old/d.md", entity="other", comment="D"))
    store.flag(flag_of(key="new/02-work/a.md", entity="new", comment="Stale"))
    store.save(make(key="old/02-work/a.md", entity="old"))
    assert store.rename_entity("old", "new") == 2  # one reminder, one flag
    found = store.flags()
    assert set(found) == {"new/02-work/a.md", "older/02-work/c.md", "other/old/d.md"}
    assert (found["new/02-work/a.md"].entity, found["new/02-work/a.md"].comment) == ("new", "A")
    assert (found["older/02-work/c.md"].entity, found["other/old/d.md"].entity) == ("older", "other")
    assert set(store.all()) == {"new/02-work/a.md"}
    assert store.rename_entity("old", "new") == 0


def test_rekey_and_rename_are_all_or_nothing_across_both_tables(state, monkeypatch):
    store = ReminderStore(state)
    store.save(make(key="old/x.md", entity="old"))
    store.flag(flag_of(key="old/x.md", entity="old"))
    import app.reminders as module

    real = module._has_flags_table

    def boom(db):
        raise __import__("sqlite3").OperationalError("simulated")

    monkeypatch.setattr(module, "_has_flags_table", boom)
    with pytest.raises(ReminderStoreUnavailable):
        store.rename_entity("old", "new")
    with pytest.raises(ReminderStoreUnavailable):
        store.rekey("old/x.md", "new/x.md")
    monkeypatch.setattr(module, "_has_flags_table", real)
    assert set(store.all()) == {"old/x.md"} and set(store.flags()) == {"old/x.md"}


def test_a_malformed_flag_row_is_unavailable(state):
    import sqlite3
    from contextlib import closing

    store = ReminderStore(state)
    store.flag(flag_of())
    with closing(sqlite3.connect(state / "reminders.sqlite3")) as db:
        db.execute("UPDATE review_flags SET flagged_at='not a time'")
        db.commit()
    with pytest.raises(ReminderStoreUnavailable):
        store.flags()
    with closing(sqlite3.connect(state / "reminders.sqlite3")) as db:
        db.execute("UPDATE review_flags SET flagged_at='2026-11-03T09:30:00'")  # naive
        db.commit()
    with pytest.raises(ReminderStoreUnavailable):
        store.flags()
    with closing(sqlite3.connect(state / "reminders.sqlite3")) as db:
        db.execute("UPDATE review_flags SET flagged_at=5")
        db.commit()
    with pytest.raises(ReminderStoreUnavailable):
        store.flags()


def test_unsafe_store_refuses_flags_too(state):
    store = ReminderStore(state)
    store.flag(flag_of())
    (state / "reminders.sqlite3").chmod(0o644)
    with pytest.raises(ReminderStoreUnavailable):
        store.flags()
    with pytest.raises(ReminderStoreUnavailable):
        store.flag(flag_of(key="other"))
    with pytest.raises(ReminderStoreUnavailable):
        store.unflag("x")


def test_flags_are_never_written_into_the_vault(state, tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    _git(vault, "init", "-q")
    (vault / "note.md").write_text("synthetic\n")
    _git(vault, "add", "note.md")
    _git(vault, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", "seed")
    head = _git(vault, "rev-parse", "HEAD")
    store = ReminderStore(state)
    store.flag(flag_of())
    store.rekey("synthetic/02-work/active/a.md", "synthetic/02-work/active/b.md")
    store.unflag("synthetic/02-work/active/b.md")
    assert _git(vault, "status", "--porcelain") == ""
    assert _git(vault, "rev-parse", "HEAD") == head


def test_flag_methods_declare_failure_contracts():
    for fn, expected in [
        (ReminderStore.flags, {ReminderStoreUnavailable}),
        (ReminderStore.flag, {ReminderStoreUnavailable, InvalidReminder}),
        (ReminderStore.unflag, {ReminderStoreUnavailable}),
        (ReminderStore.rename_entity, {ReminderStoreUnavailable}),
        (clean_flag_comment, {InvalidReminder}),
    ]:
        assert set(fn.__failure_contract__.raises) == expected


def _file_state(directory):
    return {
        p.name: (p.read_bytes(), p.stat().st_mtime_ns, p.stat().st_mode)
        for p in sorted(directory.iterdir())
    }


@pytest.mark.parametrize("call", [
    lambda store: store.unflag("synthetic/02-work/active/a.md"),
    lambda store: store.rekey("synthetic/00-inbox/active/a.md", "synthetic/02-work/active/a.md"),
    lambda store: store.rename_entity("synthetic", "renamed"),
    lambda store: store.remove("synthetic/02-work/active/a.md"),
    lambda store: store.resolve("synthetic/02-work/active/a.md"),
    lambda store: store.unresolve("synthetic/02-work/active/a.md"),
])
def test_cleanup_writes_never_touch_a_zero_byte_store(state, call):
    """The master view calls `unflag` on every move: a store that was never
    published with a schema is left exactly as it is, with no sidecar files."""
    path = state / "reminders.sqlite3"
    path.touch(mode=0o600)
    path.chmod(0o600)
    before = _file_state(state)
    call(ReminderStore(state))
    assert _file_state(state) == before
    assert path.stat().st_size == 0


@pytest.mark.parametrize("call", [
    lambda store: store.unflag("synthetic/02-work/active/a.md"),
    lambda store: store.rekey("synthetic/00-inbox/active/a.md", "synthetic/02-work/active/a.md"),
    lambda store: store.rename_entity("synthetic", "renamed"),
    lambda store: store.remove("synthetic/02-work/active/a.md"),
    lambda store: store.resolve("synthetic/02-work/active/a.md"),
    lambda store: store.unresolve("synthetic/02-work/active/a.md"),
])
def test_cleanup_writes_never_add_a_reminders_table(state, call):
    import sqlite3
    from contextlib import closing

    path = state / "reminders.sqlite3"
    with closing(sqlite3.connect(path)) as db:
        db.execute("CREATE TABLE unrelated (x TEXT)")
        db.commit()
    path.chmod(0o600)
    before = _file_state(state)
    call(ReminderStore(state))
    assert _file_state(state) == before
    assert _tables(path) == {"unrelated"}


# --- resolved marks, event id, snapshot ------------------------------------


def test_calendar_event_id_is_stable_and_shaped():
    import hashlib
    import re

    key = "synthetic/02-work/active/a.md"
    assert calendar_event_id(key) == calendar_event_id(key)
    assert re.fullmatch(r"oneos-[0-9a-f]{32}", calendar_event_id(key))
    assert calendar_event_id(key) == "oneos-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
    assert calendar_event_id(key) != calendar_event_id(key + "x")


def _bytes(directory):
    return {p.name: p.read_bytes() for p in sorted(directory.iterdir())}


def test_snapshot_is_none_for_a_missing_file_and_creates_nothing(state):
    assert ReminderStore(state).snapshot() is None
    assert list(state.iterdir()) == []


def test_snapshot_is_none_for_a_zero_byte_file_and_leaves_it_alone(state):
    path = state / "reminders.sqlite3"
    path.touch(mode=0o600)
    path.chmod(0o600)
    before = _file_state(state)
    assert ReminderStore(state).snapshot() is None
    assert _file_state(state) == before


def test_snapshot_is_none_for_a_file_without_a_reminders_table(state):
    import sqlite3
    from contextlib import closing

    path = state / "reminders.sqlite3"
    with closing(sqlite3.connect(path)) as db:
        db.execute("CREATE TABLE unrelated (x TEXT)")
        db.commit()
    path.chmod(0o600)
    before = _file_state(state)
    assert ReminderStore(state).snapshot() is None
    assert _file_state(state) == before


def test_snapshot_is_none_when_the_store_cannot_be_read(state):
    ReminderStore(state).save(make())
    state.chmod(0o755)
    assert ReminderStore(state).snapshot() is None


def test_snapshot_of_an_empty_table_is_an_empty_dict(state):
    store = ReminderStore(state)
    store.save(make())
    store.remove("inbox/pay-card.md")
    before = _file_state(state)
    assert store.snapshot() == {}
    assert _file_state(state) == before


def test_snapshot_holds_the_open_rows_only(state):
    store = ReminderStore(state)
    store.save(make(key="a", subject="Open"))
    store.save(make(key="b", subject="Done one"))
    store.resolve("b")
    before = _file_state(state)
    found = store.snapshot()
    assert set(found) == {"a"} and found["a"] == store.all()["a"]
    assert set(store.all()) == {"a", "b"}  # the row is kept, only marked
    assert _file_state(state) == before
    store.unresolve("b")
    assert set(store.snapshot()) == {"a", "b"}


def test_resolve_creates_its_table_only_when_it_writes(state):
    store = ReminderStore(state)
    path = state / "reminders.sqlite3"
    store.save(make(key="a"))
    assert _tables(path) == {"reminders"}
    store.resolve("no-such-row")  # nothing to mark: a no-op
    store.unresolve("a")  # no table: a no-op
    store.snapshot(), store.all(), store.flags()
    assert _tables(path) == {"reminders"}
    store.resolve("a")
    assert _tables(path) == {"reminders", "resolved_reminders"}
    store.resolve("a")  # idempotent
    store.unresolve("a")
    store.unresolve("a")
    assert set(store.snapshot()) == {"a"}


def test_resolve_leaves_a_flags_table_out_of_it(state):
    store = ReminderStore(state)
    store.save(make(key="a"))
    store.resolve("a")
    assert "review_flags" not in _tables(state / "reminders.sqlite3")


def test_rekey_carries_the_resolved_mark_with_the_row(state):
    store = ReminderStore(state)
    store.save(make(key="a"))
    store.resolve("a")
    store.save(make(key="b", subject="Stale"))
    store.rekey("a", "b")
    assert set(store.all()) == {"b"} and store.snapshot() == {}
    # An open row moved onto a stale mark is open again.
    store.save(make(key="c"))
    store.resolve("b")
    store.rekey("c", "b")
    assert set(store.snapshot()) == {"b"}


def test_rename_entity_carries_the_resolved_mark(state):
    store = ReminderStore(state)
    store.save(make(key="old/02-work/a.md", entity="old", subject="A"))
    store.save(make(key="old/02-work/b.md", entity="old", subject="B"))
    store.save(make(key="older/02-work/c.md", entity="older", subject="C"))
    store.resolve("old/02-work/a.md")
    store.resolve("older/02-work/c.md")
    assert store.rename_entity("old", "new") == 2
    assert set(store.snapshot()) == {"new/02-work/b.md"}
    assert set(store.all()) == {"new/02-work/a.md", "new/02-work/b.md", "older/02-work/c.md"}
    store.unresolve("new/02-work/a.md")
    assert set(store.snapshot()) == {"new/02-work/a.md", "new/02-work/b.md"}


def test_remove_drops_the_mark_and_a_new_row_under_that_key_is_open(state):
    store = ReminderStore(state)
    store.save(make(key="a"))
    store.resolve("a")
    store.remove("a")
    store.save(make(key="a", subject="Again"))
    assert set(store.snapshot()) == {"a"}


def test_saving_a_reminder_clears_a_stale_mark(state):
    store = ReminderStore(state)
    store.save(make(key="a"))
    store.resolve("a")
    store.save(make(key="a", subject="Edited", day=6))
    assert set(store.snapshot()) == {"a"}


def test_a_save_never_creates_the_resolved_table(state):
    store = ReminderStore(state)
    store.save(make(key="a"))
    store.save(make(key="a", subject="Edited"))
    assert _tables(state / "reminders.sqlite3") == {"reminders"}


def test_resolved_marks_are_all_or_nothing_with_rekey(state, monkeypatch):
    import app.reminders as module

    store = ReminderStore(state)
    store.save(make(key="a"))
    store.resolve("a")
    real = module._has_table

    def boom(db, name):
        if name == "resolved_reminders":
            raise __import__("sqlite3").OperationalError("simulated")
        return real(db, name)

    monkeypatch.setattr(module, "_has_table", boom)
    with pytest.raises(ReminderStoreUnavailable):
        store.rekey("a", "b")
    monkeypatch.setattr(module, "_has_table", real)
    assert set(store.all()) == {"a"} and store.snapshot() == {}


def test_resolved_methods_declare_failure_contracts():
    for fn, expected in [
        (ReminderStore.resolve, {ReminderStoreUnavailable}),
        (ReminderStore.unresolve, {ReminderStoreUnavailable}),
        (ReminderStore.snapshot, set()),
        (calendar_event_id, set()),
    ]:
        assert set(fn.__failure_contract__.raises) == expected
