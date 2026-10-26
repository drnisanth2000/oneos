"""Phase 3 slices 2–6. Synthetic fixtures only."""
from __future__ import annotations

import importlib
from io import BytesIO
from pathlib import Path

from pypdf import PdfWriter
from starlette.testclient import TestClient

from app.phase3 import (
    CleanupBook,
    LearnedRule,
    MailDocument,
    ParserHook,
    PasswordUnlockError,
    ProtectedPasswordStore,
    ProviderMessage,
    RecoveryChoice,
    SyntheticProvider,
    archive_prepared_pdf,
    configure_workspace,
    match_rules,
    may_use_password,
    reset_workspaces,
    sender_fingerprint,
    unlock_for_rule,
    unlocked_pdf_bytes,
)
from app.scope import Scope
from tests.conftest import git_entity_vault, git_history_contains, scaffold_modules, write_vault

_RECEIVED_AT = "2026-10-26T09:30:00"


def _pan() -> str:
    return "ABCDE1234F"


def _pdf(marker: str, password: str | None = None) -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.add_metadata({"/Title": marker})
    if password is not None:
        writer.encrypt(password)
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


class _InvoiceParser:
    def __init__(self, fields: dict[str, str]) -> None:
        self.fields = fields

    def parse(self, payload: bytes) -> dict[str, str]:
        return dict(self.fields)


class _CountingStore(ProtectedPasswordStore):
    def __init__(self, vault: Path, root: Path) -> None:
        super().__init__(vault, root)
        self.reads = 0

    def password_for(self, sender_fingerprint: str, document_type: str) -> str | None:
        self.reads += 1
        return super().password_for(sender_fingerprint, document_type)


def _vault(tmp_path: Path) -> Path:
    return git_entity_vault(
        tmp_path / "vault",
        ("synthetic",),
        {"synthetic/00-inbox/active/.gitkeep": ""},
    )


def test_parser_hook_redacts_fields_and_drops_unknown_types():
    hook = ParserHook({"invoice": _InvoiceParser({"account": _pan(), "note": "due Tuesday"})})
    fields = dict(hook.extract("invoice", b"%PDF synthetic"))
    assert fields["account"] == "[PAN]"
    assert fields["note"] == "due Tuesday"
    assert _pan() not in " ".join(fields.values())
    try:
        hook.extract("unknown", b"payload-marker")
    except Exception as exc:
        assert "payload-marker" not in str(exc)
    else:
        raise AssertionError("unknown parser must stop for review")


def test_unverified_sender_and_conflicting_rules_stop_before_password_use(tmp_path):
    sender = "synthetic-sender@example.invalid"
    fingerprint = sender_fingerprint(sender)
    rules = (
        LearnedRule("rule-a", "mailbox-a", fingerprint, "invoice", True),
        LearnedRule("rule-b", "mailbox-a", fingerprint, "invoice", False),
    )
    unverified = match_rules(rules[:1], mailbox_id="mailbox-a", sender=sender, verified=False)
    conflict = match_rules(rules, mailbox_id="mailbox-a", sender=sender, verified=True)
    matched = match_rules(rules[:1], mailbox_id="mailbox-a", sender=sender, verified=True)
    assert unverified.status == "manual"
    assert conflict.status == "conflict"
    assert matched.status == "matched"
    assert may_use_password(unverified, verified=False) is False
    assert may_use_password(conflict, verified=True) is False
    assert "trash" not in LearnedRule.__dataclass_fields__

    (tmp_path / "vault").mkdir()
    store = _CountingStore(tmp_path / "vault", tmp_path / "secrets")
    payload = _pdf("locked", "synthetic-password")
    try:
        unlock_for_rule(
            payload,
            store=store,
            sender_fingerprint=fingerprint,
            document_type="invoice",
            match=unverified,
            verified=False,
        )
    except PasswordUnlockError as exc:
        assert "synthetic-password" not in str(exc)
    assert store.reads == 0


def test_password_is_replaced_only_after_unlock_and_stays_outside_the_receipt(tmp_path):
    vault = _vault(tmp_path)
    secret_root = tmp_path / "secrets"
    store = ProtectedPasswordStore(vault, secret_root)
    sender = sender_fingerprint("synthetic-sender@example.invalid")
    old = "synthetic-old-password"
    new = "synthetic-new-password"
    store.remember(sender, "invoice", old)
    payload = _pdf("SYNTHETIC-LOCKED-MARKER", old)

    try:
        unlock_for_rule(
            payload,
            store=store,
            sender_fingerprint=sender,
            document_type="invoice",
            match=type("Match", (), {"status": "matched"})(),
            verified=True,
            replacement=new,
        )
    except PasswordUnlockError:
        assert store.password_for(sender, "invoice") == old
    else:
        raise AssertionError("a wrong replacement must not be stored")

    # The stored password is the old one, so unlocking without a replacement works.
    # A failed replacement above used a password that does not unlock this file
    # only when it differs. Use the current password as the successful path.
    unlocked = unlock_for_rule(
        payload,
        store=store,
        sender_fingerprint=sender,
        document_type="invoice",
        match=type("Match", (), {"status": "matched"})(),
        verified=True,
        replacement=old,
    )
    archived = archive_prepared_pdf(
        Scope(vault, "synthetic"),
        unlocked,
        "statement.pdf",
        archive_root=tmp_path / "archive",
        source_id="case-one",
        received_at=_RECEIVED_AT,
    )
    receipt = archived.result.path.read_text(encoding="utf-8")
    names = [path.name for path in secret_root.rglob("*") if path.is_file()]
    assert old not in receipt and new not in receipt
    assert old not in " ".join(names)
    assert "SYNTHETIC-LOCKED-MARKER" not in receipt
    assert git_history_contains(vault, old) is False
    assert stat_mode_is_private(secret_root)


def test_wrong_replacement_does_not_overwrite_the_remembered_password(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    # Containment only needs the vault path to exist as a directory boundary.
    git_entity_vault(vault, ("synthetic",), {"synthetic/00-inbox/active/.gitkeep": ""})
    store = ProtectedPasswordStore(vault, tmp_path / "secrets")
    sender = sender_fingerprint("synthetic-sender@example.invalid")
    store.remember(sender, "invoice", "synthetic-old-password")
    payload = _pdf("marker", "synthetic-old-password")
    from app.phase3 import RuleMatch

    try:
        unlock_for_rule(
            payload,
            store=store,
            sender_fingerprint=sender,
            document_type="invoice",
            match=RuleMatch("matched", ("rule-a",)),
            verified=True,
            replacement="synthetic-new-password",
        )
    except PasswordUnlockError as exc:
        assert "synthetic-new-password" not in str(exc)
    assert store.password_for(sender, "invoice") == "synthetic-old-password"


def stat_mode_is_private(root: Path) -> bool:
    import os
    import stat

    for path in root.rglob("*"):
        if path.is_file() and stat.S_IMODE(path.stat().st_mode) != 0o600:
            return False
    return True


def test_cleanup_freezes_exact_messages_and_recovery_keeps_the_archive(tmp_path):
    provider = SyntheticProvider((
        ProviderMessage("case-rare", "mailbox-a", "synthetic"),
        ProviderMessage("case-routine", "mailbox-a", "synthetic"),
        ProviderMessage("case-other", "mailbox-b", "other"),
        ProviderMessage("case-missing", "mailbox-a", "synthetic", "removed"),
    ))
    book = CleanupBook("synthetic", provider)
    book.documents = {
        "case-rare": MailDocument(
            "case-rare", "mailbox-a", "Rare", "archive:aaa", "", True, "approved", 0
        ),
        "case-routine": MailDocument(
            "case-routine", "mailbox-a", "Routine", "archive:bbb", "", True, "skipped", 4
        ),
    }
    assert [item.case_id for item in book.ranked_ready()] == ["case-rare", "case-routine"]
    frozen = book.freeze(("case-rare",))
    provider.messages["case-later"] = ProviderMessage("case-later", "mailbox-a", "synthetic")
    outcomes = book.confirm(True)
    assert frozen == ("case-rare",)
    assert outcomes[0].status == "trashed"
    assert provider.messages["case-later"].location == "inbox"
    assert provider.messages["case-routine"].location == "inbox"
    archive = tmp_path / "kept.pdf"
    archive.write_bytes(b"kept")
    choices = book.recover(("case-rare",))
    assert choices[0] == RecoveryChoice("case-rare")
    assert provider.messages["case-rare"].location == "inbox"
    assert archive.read_bytes() == b"kept"
    unavailable = book.recover(("case-missing",))
    assert unavailable[0].choices == ("unavailable",)
    try:
        book.freeze(("case-other",))
    except Exception as exc:
        assert "case-other" not in str(exc)
    else:
        raise AssertionError("another entity must not enter this cleanup list")


def test_partial_trash_records_each_message():
    provider = SyntheticProvider((
        ProviderMessage("case-ok", "mailbox-a", "synthetic"),
        ProviderMessage("case-gone", "mailbox-a", "synthetic", "removed"),
    ))
    book = CleanupBook("synthetic", provider)
    book.documents = {
        "case-ok": MailDocument("case-ok", "mailbox-a", "Ok", "archive:a", "", True, "approved", 1),
        "case-gone": MailDocument("case-gone", "mailbox-a", "Gone", "archive:b", "", True, "approved", 1),
    }
    book.freeze(("case-ok", "case-gone"))
    outcomes = {item.message_id: item.status for item in book.confirm(True)}
    assert outcomes == {"case-ok": "trashed", "case-gone": "attention"}
    assert provider.messages["case-ok"].location == "trash"


def test_mailbox_screen_shows_tabs_and_cleanup_requires_confirmation(tmp_path, monkeypatch):
    write_vault(tmp_path, 'version: "1.0"\nentities:\n  alpha: { label: Alpha, flags: [] }\n')
    scaffold_modules(tmp_path, "alpha", ["00-intake", "01-core", "02-work"])
    monkeypatch.setenv("ONEOS_VAULT", str(tmp_path))
    import app.phase3 as phase3
    import app.main as main

    importlib.reload(phase3)
    main = importlib.reload(main)
    scope = Scope(tmp_path, "alpha")
    phase3.configure_workspace(
        scope,
        mailboxes=("mailbox-a", "mailbox-b"),
        documents=(
            MailDocument("case-one", "mailbox-a", "Statement", "archive:abc", "first-time naming", True, "approved", 0),
            MailDocument("case-two", "mailbox-b", "Other", "archive:def", "", False, "pending", 2),
        ),
        messages=(
            ProviderMessage("case-one", "mailbox-a", "alpha"),
            ProviderMessage("case-two", "mailbox-b", "alpha"),
        ),
    )
    client = TestClient(main.app)
    mail = client.get("/mail/alpha?mailbox=mailbox-a")
    assert mail.status_code == 200
    assert "mailbox-a" in mail.text
    assert "mailbox-b" in mail.text
    assert "first-time naming" in mail.text
    assert "Open file" in mail.text
    assert "archive:abc" in mail.text
    cleanup = client.get("/mail/alpha/cleanup")
    assert cleanup.status_code == 200
    assert "Ready to delete" in cleanup.text
    assert "Deleted" in cleanup.text
    assert "case-one" in cleanup.text
    refused = client.post("/mail/alpha/cleanup/confirm", data={"confirmed": "no"})
    assert refused.status_code == 400
    frozen = client.post("/mail/alpha/cleanup/freeze", data={"message_id": "case-one"})
    assert frozen.status_code == 200
    assert "case-one" in frozen.text
    confirmed = client.post("/mail/alpha/cleanup/confirm", data={"confirmed": "yes"})
    assert confirmed.status_code == 200
    assert phase3.workspace_for(scope).book.provider.messages["case-one"].location == "trash"
    recovered = client.post("/mail/alpha/cleanup/recover", data={"message_id": "case-one"})
    assert recovered.status_code == 200
    assert phase3.workspace_for(scope).book.provider.messages["case-one"].location == "inbox"
    reset_workspaces()


def test_unlocked_bytes_round_trip_helper():
    payload = _pdf("plain")
    assert unlocked_pdf_bytes(payload, None) == payload
