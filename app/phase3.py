"""Phase 3 slices 2–6.

Parsers, learned rules, encrypted-PDF unlock, frozen cleanup, and the mailbox
screen. Private parsers, passwords, and provider identifiers stay outside Git.
A learned rule can prepare work. It cannot move a message to Trash.
"""
from __future__ import annotations

import hashlib
import os
import re
import stat
import warnings
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Mapping, Protocol

from pypdf import PdfReader, PdfWriter

from .console_routing import failure_contract
from .ingest.adapters.folder import (
    _directory_is_within,
    _identity,
    _open_archive_directory,
    _open_external_archive_anchor,
)
from .ingest.document_archive import ingest_unlocked_bytes
from .ingest.pii import redact
from .scope import Scope

_TOKEN = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,63})")
_FIELD = re.compile(r"[a-z][a-z0-9_]{0,31}")
_HEX = re.compile(r"[0-9a-f]{64}")


class Phase3Error(Exception):
    def __str__(self) -> str:
        return "mailbox action was refused"


class ParserReview(Phase3Error):
    def __str__(self) -> str:
        return "document needs review"


class PasswordUnlockError(Phase3Error):
    def __str__(self) -> str:
        return "document could not be unlocked"


class PasswordStoreCleanupError(Phase3Error):
    def __str__(self) -> str:
        return "password store cleanup failed"


class CleanupError(Phase3Error):
    def __str__(self) -> str:
        return "cleanup action was refused"


class FieldParser(Protocol):
    def parse(self, payload: bytes) -> Mapping[str, str]:
        """Return named fields. The hook redacts values before they are kept."""


@dataclass(frozen=True)
class ParserHook:
    parsers: Mapping[str, FieldParser]

    def extract(self, document_type: str, payload: bytes) -> tuple[tuple[str, str], ...]:
        if not _TOKEN.fullmatch(document_type):
            raise ParserReview()
        parser = self.parsers.get(document_type)
        if parser is None:
            raise ParserReview()
        raw = parser.parse(payload)
        safe: list[tuple[str, str]] = []
        for key, value in raw.items():
            if not _FIELD.fullmatch(str(key)):
                raise ParserReview()
            redacted, _matches = redact(str(value)[:500])
            safe.append((str(key), redacted[:200]))
        return tuple(safe)


def sender_fingerprint(address: str) -> str:
    normalized = address.strip().lower()
    if "@" not in normalized or any(char.isspace() for char in normalized):
        raise Phase3Error()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class LearnedRule:
    rule_id: str
    mailbox_id: str
    sender_fingerprint: str
    document_type: str
    prepare_summary: bool

    def __post_init__(self) -> None:
        for value in (self.rule_id, self.mailbox_id, self.document_type):
            if not _TOKEN.fullmatch(value):
                raise Phase3Error()
        if not _HEX.fullmatch(self.sender_fingerprint):
            raise Phase3Error()


@dataclass(frozen=True)
class RuleMatch:
    status: str
    rule_ids: tuple[str, ...]
    sender_fingerprint: str = ""
    document_type: str = ""


def match_rules(
    rules: tuple[LearnedRule, ...],
    *,
    mailbox_id: str,
    sender: str,
    document_type: str,
    verified: bool,
) -> RuleMatch:
    """Unverified senders and conflicting rules stop automation."""
    if not _TOKEN.fullmatch(document_type):
        raise Phase3Error()
    if not verified:
        return RuleMatch("manual", ())
    fingerprint = sender_fingerprint(sender)
    hits = tuple(
        rule.rule_id
        for rule in rules
        if rule.mailbox_id == mailbox_id
        and rule.sender_fingerprint == fingerprint
        and rule.document_type == document_type
    )
    if not hits:
        return RuleMatch("manual", ())
    if len(hits) > 1:
        return RuleMatch("conflict", hits)
    return RuleMatch("matched", hits, fingerprint, document_type)


def may_use_password(match: RuleMatch, *, verified: bool) -> bool:
    return verified and isinstance(match, RuleMatch) and match.status == "matched"


def preparation_allowed(match: RuleMatch) -> bool:
    return match.status == "matched"


class ProtectedPasswordStore:
    """Password bytes live outside the vault and outside Git."""

    def __init__(self, vault: Path | str, root: Path | str) -> None:
        self.vault = Path(vault)
        self.root = Path(root)
        anchor, archive_fd = self._open_store()
        os.close(archive_fd)
        os.close(anchor.fd)

    def _open_store(self) -> tuple[object, int]:
        anchor = _open_external_archive_anchor(self.vault, self.root)
        try:
            archive_fd = _open_archive_directory(anchor, self.vault)
        except BaseException:
            os.close(anchor.fd)
            raise
        return anchor, archive_fd

    def _name(self, sender_fingerprint: str, document_type: str) -> str:
        if not _HEX.fullmatch(sender_fingerprint) or not _TOKEN.fullmatch(document_type):
            raise PasswordUnlockError()
        return hashlib.sha256(
            f"{sender_fingerprint}:{document_type}".encode("ascii")
        ).hexdigest()

    def password_for(self, sender_fingerprint: str, document_type: str) -> str | None:
        name = self._name(sender_fingerprint, document_type)
        anchor, archive_fd = self._open_store()
        file_fd: int | None = None
        try:
            try:
                file_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=archive_fd)
            except FileNotFoundError:
                return None
            except OSError as exc:
                raise PasswordUnlockError() from exc
            info = os.fstat(file_fd)
            if not stat.S_ISREG(info.st_mode):
                raise PasswordUnlockError()
            chunks: list[bytes] = []
            while True:
                block = os.read(file_fd, 65536)
                if not block:
                    break
                chunks.append(block)
            return b"".join(chunks).decode("utf-8")
        finally:
            if file_fd is not None:
                os.close(file_fd)
            os.close(archive_fd)
            os.close(anchor.fd)

    def remember(self, sender_fingerprint: str, document_type: str, password: str) -> None:
        if not password:
            raise PasswordUnlockError()
        name = self._name(sender_fingerprint, document_type)
        temporary = name + ".new"
        payload = password.encode("utf-8")
        anchor, archive_fd = self._open_store()
        published = False
        try:
            vault_identity = _identity(os.stat(self.vault, follow_symlinks=True))
            self._write_new(archive_fd, temporary, payload)
            if _directory_is_within(archive_fd, vault_identity):
                self._discard(archive_fd, temporary)
                raise PasswordUnlockError()
            os.rename(temporary, name, src_dir_fd=archive_fd, dst_dir_fd=archive_fd)
            published = True
            if _directory_is_within(archive_fd, vault_identity):
                published = False
                self._discard(archive_fd, name)
                raise PasswordUnlockError()
        finally:
            if not published:
                try:
                    os.unlink(temporary, dir_fd=archive_fd)
                except OSError:
                    pass
            os.close(archive_fd)
            os.close(anchor.fd)

    @staticmethod
    def _write_new(directory_fd: int, name: str, payload: bytes) -> None:
        try:
            os.unlink(name, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise PasswordUnlockError() from exc
        previous_mask = os.umask(0o077)
        fd: int | None = None
        try:
            fd = os.open(
                name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=directory_fd,
            )
            os.fchmod(fd, 0o600)
            view = memoryview(payload)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise PasswordUnlockError()
                view = view[written:]
            os.fsync(fd)
        except BaseException:
            if fd is not None:
                os.close(fd)
                fd = None
            try:
                os.unlink(name, dir_fd=directory_fd)
            except OSError:
                pass
            raise
        finally:
            os.umask(previous_mask)
            if fd is not None:
                os.close(fd)

    @staticmethod
    def _discard(directory_fd: int, name: str) -> None:
        """Remove a password copy that landed in the vault, or fail hard."""
        try:
            os.unlink(name, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise PasswordStoreCleanupError() from exc


def _open_pdf(payload: bytes) -> PdfReader:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return PdfReader(BytesIO(payload))
    except Exception as exc:
        raise PasswordUnlockError() from None


def unlocked_pdf_bytes(payload: bytes, password: str | None) -> tuple[bytes, bool]:
    """Return unlocked bytes and whether a password actually decrypted them."""
    reader = _open_pdf(payload)
    if not reader.is_encrypted:
        return payload, False
    if password is None:
        raise PasswordUnlockError()
    try:
        status = reader.decrypt(password)
    except Exception as exc:
        raise PasswordUnlockError() from None
    if status == 0:
        raise PasswordUnlockError()
    try:
        writer = PdfWriter()
        writer.append(reader)
        output = BytesIO()
        writer.write(output)
    except Exception as exc:
        raise PasswordUnlockError() from None
    return output.getvalue(), True


def unlock_for_rule(
    payload: bytes,
    *,
    store: ProtectedPasswordStore,
    sender_fingerprint: str,
    document_type: str,
    match: RuleMatch,
    verified: bool,
    replacement: str | None = None,
) -> bytes:
    """Try a remembered password only after trust and a single rule match."""
    encrypted = _open_pdf(payload).is_encrypted
    # A match authorizes only the sender and document type it was made for.
    if not may_use_password(match, verified=verified) or (
        match.sender_fingerprint != sender_fingerprint
        or match.document_type != document_type
    ):
        if encrypted:
            raise PasswordUnlockError()
        return payload
    if not encrypted:
        if replacement is not None:
            raise PasswordUnlockError()
        return payload
    password = replacement if replacement is not None else store.password_for(
        sender_fingerprint, document_type
    )
    unlocked, decrypted = unlocked_pdf_bytes(payload, password)
    if replacement is not None:
        if not decrypted:
            raise PasswordUnlockError()
        store.remember(sender_fingerprint, document_type, replacement)
    return unlocked


@dataclass
class ProviderMessage:
    message_id: str
    mailbox_id: str
    entity: str
    location: str = "inbox"


@dataclass(frozen=True)
class CleanupOutcome:
    message_id: str
    status: str


@dataclass(frozen=True)
class RecoveryChoice:
    message_id: str
    choices: tuple[str, ...] = ("keep", "revise", "pause")
    rule_changed: bool = False


class SyntheticProvider:
    def __init__(self, messages: tuple[ProviderMessage, ...]) -> None:
        self.messages = {message.message_id: message for message in messages}

    def require_owned(self, entity: str, message_id: str) -> ProviderMessage:
        message = self.messages.get(message_id)
        if message is None or message.entity != entity:
            raise CleanupError()
        return message

    def trash_exact(self, entity: str, message_id: str) -> None:
        message = self.require_owned(entity, message_id)
        if message.location != "inbox":
            raise CleanupError()
        message.location = "trash"

    def recover_exact(self, entity: str, message_id: str) -> None:
        message = self.require_owned(entity, message_id)
        if message.location == "removed":
            raise CleanupError()
        if message.location != "trash":
            raise CleanupError()
        message.location = "inbox"


@dataclass
class MailDocument:
    case_id: str
    mailbox_id: str
    title: str
    reference: str
    attention: str
    ready: bool
    summary_state: str
    prior_confirms: int = 0


@dataclass
class CleanupBook:
    entity: str
    provider: SyntheticProvider
    documents: dict[str, MailDocument] = field(default_factory=dict)
    frozen: tuple[str, ...] | None = None
    deleted: list[str] = field(default_factory=list)

    def freeze(self, message_ids: tuple[str, ...]) -> tuple[str, ...]:
        chosen: list[str] = []
        for message_id in message_ids:
            if not _TOKEN.fullmatch(message_id):
                raise CleanupError()
            document = self.documents.get(message_id)
            self.provider.require_owned(self.entity, message_id)
            if document is None or not document.ready or document.summary_state == "pending":
                raise CleanupError()
            chosen.append(message_id)
        self.frozen = tuple(chosen)
        return self.frozen

    def frozen_digest(self) -> str:
        if self.frozen is None:
            return ""
        return hashlib.sha256("\n".join(self.frozen).encode("ascii")).hexdigest()

    def confirm(self, confirmed: bool, digest: str) -> tuple[CleanupOutcome, ...]:
        """Trash only the frozen list whose digest the owner reviewed."""
        if self.frozen is None or not confirmed or digest != self.frozen_digest():
            raise CleanupError()
        outcomes: list[CleanupOutcome] = []
        for message_id in self.frozen:
            try:
                self.provider.trash_exact(self.entity, message_id)
            except CleanupError:
                outcomes.append(CleanupOutcome(message_id, "attention"))
                continue
            outcomes.append(CleanupOutcome(message_id, "trashed"))
            if message_id not in self.deleted:
                self.deleted.append(message_id)
        self.frozen = None
        return tuple(outcomes)

    def recover(self, message_ids: tuple[str, ...]) -> tuple[RecoveryChoice, ...]:
        choices: list[RecoveryChoice] = []
        for message_id in message_ids:
            if not _TOKEN.fullmatch(message_id):
                raise CleanupError()
            try:
                self.provider.recover_exact(self.entity, message_id)
            except CleanupError:
                choices.append(RecoveryChoice(message_id, ("unavailable",), False))
                continue
            if message_id in self.deleted:
                self.deleted.remove(message_id)
            choices.append(RecoveryChoice(message_id))
        return tuple(choices)

    def ranked_ready(self) -> tuple[MailDocument, ...]:
        ready = [item for item in self.documents.values() if item.ready and item.case_id not in self.deleted]
        ready.sort(key=lambda item: (item.prior_confirms, item.case_id))
        return tuple(ready)


@dataclass(frozen=True)
class MailboxPage:
    entity: str
    mailboxes: tuple[str, ...]
    selected: str
    attention: tuple[tuple[str, str], ...]
    documents: tuple[tuple[str, str, str], ...]


@dataclass(frozen=True)
class CleanupPage:
    entity: str
    ready: tuple[tuple[str, str, int], ...]
    frozen: tuple[str, ...]
    deleted: tuple[str, ...]
    frozen_digest: str = ""
    outcomes: tuple[tuple[str, str], ...] = ()


@dataclass
class MailboxWorkspace:
    mailboxes: tuple[str, ...]
    book: CleanupBook

    def page(self, selected: str) -> MailboxPage:
        mailbox = selected if selected in self.mailboxes else (self.mailboxes[0] if self.mailboxes else "")
        attention = tuple(
            (item.case_id, item.attention)
            for item in self.book.documents.values()
            if item.mailbox_id == mailbox and item.attention
        )
        documents = tuple(
            (item.case_id, item.title, item.reference)
            for item in self.book.documents.values()
            if item.mailbox_id == mailbox
        )
        return MailboxPage(self.book.entity, self.mailboxes, mailbox, attention, documents)

    def cleanup_page(self, outcomes: tuple[tuple[str, str], ...] = ()) -> CleanupPage:
        ready = tuple(
            (item.case_id, item.title, item.prior_confirms)
            for item in self.book.ranked_ready()
        )
        return CleanupPage(
            self.book.entity,
            ready,
            self.book.frozen or (),
            tuple(self.book.deleted),
            self.book.frozen_digest(),
            outcomes,
        )


_WORKSPACES: dict[tuple[str, str], MailboxWorkspace] = {}


def reset_workspaces() -> None:
    _WORKSPACES.clear()


def _key(scope: Scope) -> tuple[str, str]:
    return (str(scope.root.resolve()), scope.current_entity())


def configure_workspace(
    scope: Scope,
    *,
    mailboxes: tuple[str, ...],
    documents: tuple[MailDocument, ...],
    messages: tuple[ProviderMessage, ...],
) -> MailboxWorkspace:
    for mailbox in mailboxes:
        if not _TOKEN.fullmatch(mailbox):
            raise CleanupError()
    provider = SyntheticProvider(messages)
    book = CleanupBook(scope.current_entity(), provider)
    for document in documents:
        if document.mailbox_id not in mailboxes:
            raise CleanupError()
        provider.require_owned(scope.current_entity(), document.case_id)
        book.documents[document.case_id] = document
    workspace = MailboxWorkspace(mailboxes, book)
    _WORKSPACES[_key(scope)] = workspace
    return workspace


def workspace_for(scope: Scope) -> MailboxWorkspace:
    found = _WORKSPACES.get(_key(scope))
    if found is not None:
        return found
    workspace = MailboxWorkspace((), CleanupBook(scope.current_entity(), SyntheticProvider(())))
    _WORKSPACES[_key(scope)] = workspace
    return workspace


@failure_contract(raises=(CleanupError,))
def render_mailbox(scope: Scope, selected: str = "") -> MailboxPage:
    if selected and not _TOKEN.fullmatch(selected):
        raise CleanupError()
    return workspace_for(scope).page(selected)


@failure_contract(raises=(CleanupError,))
def render_cleanup(scope: Scope) -> CleanupPage:
    return workspace_for(scope).cleanup_page()


@failure_contract(raises=(CleanupError,))
def freeze_visible(scope: Scope, message_ids: tuple[str, ...]) -> CleanupPage:
    workspace_for(scope).book.freeze(message_ids)
    return workspace_for(scope).cleanup_page()


@failure_contract(raises=(CleanupError,))
def confirm_frozen(scope: Scope, confirmed: bool, digest: str) -> CleanupPage:
    outcomes = workspace_for(scope).book.confirm(confirmed, digest)
    return workspace_for(scope).cleanup_page(
        tuple((item.message_id, item.status) for item in outcomes)
    )


@failure_contract(raises=(CleanupError,))
def recover_selected(scope: Scope, message_ids: tuple[str, ...]) -> CleanupPage:
    choices = workspace_for(scope).book.recover(message_ids)
    return workspace_for(scope).cleanup_page(
        tuple(
            (item.message_id, "unavailable" if item.choices == ("unavailable",) else "recovered")
            for item in choices
        )
    )


def archive_prepared_pdf(
    scope: Scope,
    payload: bytes,
    filename: str,
    *,
    archive_root: Path | str,
    source_id: str,
    received_at: str,
):
    """Archive bytes that have already been unlocked. Rules do not call Trash."""
    return ingest_unlocked_bytes(
        scope,
        payload,
        filename,
        archive_root=archive_root,
        source_id=source_id,
        received_at=received_at,
    )
