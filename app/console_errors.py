"""The Console's operator-facing error vocabulary. One table, one resolver.

This module imports domain exceptions; only the presentation composition root
(`app/main.py`) imports this module. The boundary is one-way and asserted by
test — no domain or service module may import `console_errors`.
"""
from __future__ import annotations

from dataclasses import dataclass

TIERS = ("committed", "recovery", "integrity", "refusal", "unknown")
SEVERITIES = frozenset({"refusal", "attention"})
RETRIES = frozenset({"retry", "reload", "recreate", "stop", "none"})
COMMITTED = frozenset({"no", "yes", "unknown"})
PAGE_STATUSES = frozenset({404, 409, 422, 500})


@dataclass(frozen=True)
class ConsoleError:
    code: str
    tier: str
    severity: str
    message: str
    retry: str
    committed: str
    page_status: int

    def __post_init__(self) -> None:
        if self.tier not in TIERS:
            raise ValueError("tier is not a permitted value")
        if self.severity not in SEVERITIES:
            raise ValueError("severity is not a permitted value")
        if self.retry not in RETRIES:
            raise ValueError("retry is not a permitted value")
        if self.committed not in COMMITTED:
            raise ValueError("committed is not a permitted value")
        if self.page_status not in PAGE_STATUSES:
            raise ValueError("page status is not a permitted value")
        if self.severity == "refusal" and self.committed != "no":
            raise ValueError("a refusal cannot report a commit")
        if self.tier == "committed" and (self.committed != "yes" or self.retry != "stop"):
            raise ValueError("a committed outcome must stop and report yes")
        if self.tier == "recovery" and (self.committed != "unknown" or self.retry != "stop"):
            raise ValueError("a recovery outcome must stop and report unknown")


# --- the codes table (design §2, verbatim) ----------------------------------

_CODES: dict[str, ConsoleError] = {
    error.code: error
    for error in (
        ConsoleError(
            "E-COMMITTED", "committed", "attention",
            "The commit succeeded; only the cleanup afterwards failed. "
            "Do not retry — retrying would commit this action twice. "
            "Inspect vault state with git status.",
            "stop", "yes", 500,
        ),
        ConsoleError(
            # Stage 2. `applied` means committed here, despite the
            # pre-commit `applied_changes` vocabulary in git_transaction.
            # The action and receipt are durable; proposal consumption is
            # the unresolved post-commit step, so retrying could act twice.
            "E-APPLIED", "committed", "attention",
            "The action completed, but OneOS could not verify that its "
            "proposal was safely consumed. Its receipt prevents this "
            "proposal ID from being used again. Do not retry or move files "
            "by hand. Inspect vault state with git status.",
            "stop", "yes", 500,
        ),
        ConsoleError(
            # S7 Amendment 1. Reject makes no Git commit, so E-COMMITTED's
            # "the commit succeeded" would be untrue — but the consumption
            # itself is done, which is the fact the operator has to act on.
            # That is what the `committed` tier encodes here: the effect took
            # hold, so do not retry. It does NOT say the record is gone;
            # under quarantine it is retained and recoverable.
            "E-QUARANTINED", "committed", "attention",
            "The proposal was consumed and set aside; only the cleanup "
            "afterwards failed. Do not retry — the action already took "
            "effect, and the record is retained. Inspect vault state with "
            "git status.",
            "stop", "yes", 500,
        ),
        ConsoleError(
            # S7 Amendment 2, generalised by Amendment 3. One outcome for
            # every way OneOS can fail to verify that the quarantine
            # location still holds the exact reviewed proposal: a different
            # inode, no entry at all, the same inode whose bytes were
            # rewritten in place, or a location that cannot be inspected —
            # hence "cannot verify" rather than "no longer holds". An access
            # failure is not evidence the proposal is gone, only that OneOS
            # may not claim it is there. The operator's
            # position is identical in each — the reviewed record cannot be
            # shown to be there, no rename-back is safe, do not retry — and
            # separate codes would invite the "one branch over" gap that
            # Amendment 2 itself left. Deliberately does not say "replaced",
            # which was true of only one of the three.
            #
            "E-SUBSTITUTED", "recovery", "attention",
            "The reviewed proposal was moved, but OneOS cannot verify that "
            "its quarantine location still holds it unchanged. The reviewed "
            "record may no longer exist. Do not retry or move files by hand. "
            "No automated recovery is available. Inspect vault state with "
            "git status and escalate for verified recovery.",
            "stop", "unknown", 500,
        ),
        ConsoleError(
            # S7 Amendment 1. Fails closed: OneOS refuses rather than
            # degrading to an ordinary rename, which overwrites silently.
            "E-UNSUPPORTED", "integrity", "attention",
            "This vault's filesystem cannot move files safely, so proposals "
            "cannot be approved, rejected or deleted here. Nothing was "
            "changed.",
            "none", "no", 500,
        ),
        ConsoleError(
            "E-RECOVER", "recovery", "attention",
            "Rollback was blocked by a change made at the same time. "
            "Do not retry. Inspect vault state with git status and resolve "
            "it before continuing.",
            "stop", "unknown", 500,
        ),
        ConsoleError(
            "E-CONFIG", "integrity", "attention",
            "The vault registries could not be read. The Console cannot "
            "operate here until they are valid.",
            "none", "no", 500,
        ),
        ConsoleError(
            # S7 Amendment 3 Stage 2. A matching receipt disables only its
            # proposal id, but malformed content cannot truthfully select an
            # action-specific recovery link. `committed` describes this
            # request, which refused before mutation, not the earlier action
            # the invalid receipt may represent.
            "E-RECEIPT", "integrity", "attention",
            "OneOS found an invalid action receipt for this proposal ID. It "
            "cannot safely tell what completed action the receipt represents, "
            "so the ID is disabled. Do not retry, and do not move or delete "
            "files by hand. No automated recovery is available. Inspect vault "
            "state with git status and escalate for verified recovery.",
            "stop", "no", 500,
        ),
        ConsoleError(
            "E-SCOPE", "integrity", "refusal",
            "Refused: the request resolved outside the selected entity.",
            "none", "no", 404,
        ),
        ConsoleError(
            "E-TAMPER", "integrity", "attention",
            "Refused: a managed file or folder is missing, moved, replaced, or "
            "redirected. Reviewed actions for the affected entity are read-only. "
            "Stop OneOS and every connected writer. Restore the item to its "
            "expected location. If the whole vault intentionally moved, update "
            "ONEOS_VAULT, restart OneOS, and rerun verification. Do not use a "
            "symlink or retry while this warning remains.",
            "stop", "no", 409,
        ),
        ConsoleError(
            "E-STALE", "refusal", "refusal",
            "Approval refused: source changed since this proposal was "
            "created. Create a fresh proposal.",
            "recreate", "no", 409,
        ),
        ConsoleError(
            "E-MISSING", "refusal", "refusal",
            "Approval refused: source is missing. Restore it or reject the "
            "proposal.",
            "recreate", "no", 409,
        ),
        ConsoleError(
            "E-INVALID", "refusal", "refusal",
            "This proposal record is not valid and cannot be approved. "
            "Create a new proposal.",
            "recreate", "no", 422,
        ),
        ConsoleError(
            "E-UNREADABLE", "refusal", "attention",
            "A file in the outbox could not be read as a proposal. Creating "
            "another proposal will not clear it — repair or remove it "
            "outside the Console.",
            "stop", "no", 422,
        ),
        ConsoleError(
            "E-UNAVAILABLE", "refusal", "refusal",
            "A file involved in this action could not be read. Nothing was "
            "changed. Try again; if it persists, check that the file is "
            "readable.",
            "retry", "no", 500,
        ),
        ConsoleError(
            "E-INTERNAL", "refusal", "attention",
            "The action was refused by an internal safety check. Nothing "
            "was changed. This indicates a defect rather than a problem "
            "with your data.",
            "stop", "no", 500,
        ),
        ConsoleError(
            "E-DEST", "refusal", "refusal",
            "The destination could not be resolved from the registries. "
            "Re-classify this item.",
            "recreate", "no", 422,
        ),
        ConsoleError(
            "E-BUSY", "refusal", "refusal",
            "Another approval is in progress. Nothing was changed. Try "
            "again in a moment.",
            "retry", "no", 409,
        ),
        ConsoleError(
            "E-CONFLICT", "refusal", "refusal",
            "The reviewed files changed since this proposal was previewed. "
            "Reload and review again.",
            "reload", "no", 409,
        ),
        ConsoleError(
            # S7. Distinct from E-CONFLICT: that one describes reviewed
            # *files* moving under a transaction, this one describes the
            # proposal record itself being rewritten since the operator
            # reviewed it. The wording is normative — the approved design
            # fixes this sentence verbatim.
            "E-REVIEW", "refusal", "refusal",
            "Proposal changed since your review. Nothing was changed.",
            "reload", "no", 409,
        ),
        ConsoleError(
            "E-GIT", "refusal", "refusal",
            "The commit failed and was rolled back. Nothing was changed.",
            "retry", "no", 500,
        ),
        ConsoleError(
            "E-REGISTRY", "refusal", "refusal",
            "The registry operation was refused. Review the impact report "
            "and try again.",
            "reload", "no", 422,
        ),
        ConsoleError(
            "E-REQUEST", "refusal", "refusal",
            "The form could not be read. Reload the screen and try again.",
            "recreate", "no", 422,
        ),
        ConsoleError(
            "E-ENTITY", "refusal", "refusal",
            "That entity is not in the manifest.",
            "none", "no", 404,
        ),
        ConsoleError(
            "E-INGEST", "refusal", "refusal",
            "Intake failed. Nothing was written to the vault.",
            "none", "no", 500,
        ),
        ConsoleError(
            "E-ADMIN", "refusal", "refusal",
            "The administrative operation was refused.",
            "none", "no", 500,
        ),
        ConsoleError(
            "E-UNKNOWN", "unknown", "attention",
            "An unexpected error was not handled. Inspect vault state with "
            "git status before continuing.",
            "stop", "unknown", 500,
        ),
    )
}

UNKNOWN = _CODES["E-UNKNOWN"]

_TIER_RANK = {tier: rank for rank, tier in enumerate(TIERS)}
MAX_DEPTH = 4


# --- the class map (design §2, "Class mapping — normative") -----------------
#
# The table keys on imported exception classes, not dotted strings: strings
# are not refactor-safe and a renamed class would degrade silently to
# E-UNKNOWN. Where the design's §2 subtype summary and this normative table
# disagree (`InvalidTransactionPath`, `ReviewedPathUnavailable`), the
# normative table wins, per its own rationale paragraph.

from fastapi.exceptions import RequestValidationError  # noqa: E402

from . import action_receipts as _action_receipts  # noqa: E402
from . import destinations as _destinations  # noqa: E402
from . import entities as _entities  # noqa: E402
from . import git_transaction as _git_transaction  # noqa: E402
from . import outbox as _outbox  # noqa: E402
from . import proposal_identity as _proposal_identity  # noqa: E402
from . import registry as _registry  # noqa: E402
from . import review_tokens as _review_tokens  # noqa: E402
from . import rename as _rename  # noqa: E402
from . import scope as _scope  # noqa: E402
from . import vault as _vault  # noqa: E402
from .ingest import base as _ingest_base  # noqa: E402

#: Rule 2 — `GitTransactionError` is a closed family: within it, MRO
#: inheritance does not apply, and a subclass without its own exact entry
#: resolves to E-UNKNOWN.
CLOSED_FAMILY = _git_transaction.GitTransactionError

#: `exact` — the entry applies to that class only, never through MRO.
_EXACT: dict[type[BaseException], ConsoleError] = {
    _git_transaction.GitTransactionCommittedError: _CODES["E-COMMITTED"],
    _git_transaction.PostCommitConsumptionError: _CODES["E-APPLIED"],
    _git_transaction.GitTransactionRecoveryError: _CODES["E-RECOVER"],
    _git_transaction.ReviewedPathIntegrityError: _CODES["E-TAMPER"],
    _git_transaction.ReviewedPathUnavailable: _CODES["E-UNAVAILABLE"],
    _git_transaction.ReviewedStateChanged: _CODES["E-CONFLICT"],
    _git_transaction.InvalidTransactionPath: _CODES["E-INTERNAL"],
    _git_transaction.VaultBusyError: _CODES["E-BUSY"],
    _git_transaction.GitTransactionFailure: _CODES["E-GIT"],
    _git_transaction.GitTransactionError: _CODES["E-GIT"],
    _git_transaction._ApprovalLockCleanupFailure: _CODES["E-GIT"],
    _git_transaction.QuarantineCleanupError: _CODES["E-QUARANTINED"],
    _git_transaction.QuarantineEntrySubstituted: _CODES["E-SUBSTITUTED"],
    _git_transaction.AtomicMoveUnavailable: _CODES["E-UNSUPPORTED"],
    _git_transaction._ReviewedIndexOwnershipConflict: _CODES["E-CONFLICT"],
    _action_receipts.InvalidActionReceipt: _CODES["E-RECEIPT"],
    _action_receipts.ReceiptStoreIntegrityError: _CODES["E-TAMPER"],
    _action_receipts.ReceiptStoreUnavailable: _CODES["E-UNAVAILABLE"],
    _outbox.ProposalSourceUnavailable: _CODES["E-UNAVAILABLE"],
    _outbox.StaleProposalSource: _CODES["E-STALE"],
    _outbox.MissingProposalSource: _CODES["E-MISSING"],
    _outbox.ProposalFreshnessError: _CODES["E-STALE"],
    _outbox.OutboxTransactionError: _CODES["E-GIT"],
    _destinations.RedirectedDestination: _CODES["E-TAMPER"],
    _destinations.RedirectedSourceLeaf: _CODES["E-TAMPER"],
    _destinations.NonCanonicalLeaf: _CODES["E-DEST"],
    _destinations.MissingSourceLeaf: _CODES["E-DEST"],
    _destinations.MissingDestination: _CODES["E-DEST"],
    _entities.SystemRegistryPathError: _CODES["E-TAMPER"],
    _entities.RecipientConfigurationError: _CODES["E-CONFIG"],
    _registry.RegistryTransactionError: _CODES["E-GIT"],
    _review_tokens.ReviewedProposalChanged: _CODES["E-REVIEW"],
    _review_tokens.InvalidReviewToken: _CODES["E-REQUEST"],
    _review_tokens.ReviewContractViolation: _CODES["E-INTERNAL"],
    RequestValidationError: _CODES["E-REQUEST"],
}

#: `mro` — subclasses without their own entry inherit it.
_MRO: dict[type[BaseException], ConsoleError] = {
    _scope.RedirectedPathError: _CODES["E-TAMPER"],
    _scope.OutOfScopeError: _CODES["E-SCOPE"],
    _outbox.OutboxScopeError: _CODES["E-SCOPE"],
    _outbox.UnreadableProposalRecord: _CODES["E-UNREADABLE"],
    _outbox.OutboxDestinationError: _CODES["E-INVALID"],
    _outbox.OutboxError: _CODES["E-INVALID"],
    _proposal_identity.ProposalIdentityError: _CODES["E-INVALID"],
    _destinations.DestinationError: _CODES["E-DEST"],
    _vault.DestinationRegistryError: _CODES["E-CONFIG"],
    _entities.EntityManifestError: _CODES["E-CONFIG"],
    _entities.EntitySelectionError: _CODES["E-ENTITY"],
    _registry.RegistryError: _CODES["E-REGISTRY"],
    # The S7 base is never raised deliberately. If it surfaces — or if a
    # future subclass forgets its own exact entry — that is a defect in
    # OneOS, not a problem with the operator's data.
    _review_tokens.ReviewTokenError: _CODES["E-INTERNAL"],
    _ingest_base.IngestError: _CODES["E-INGEST"],
    _rename.RenameError: _CODES["E-ADMIN"],
}

#: Rule 1 — only these classes are chain-bearing, by exact class identity.
#: The private `GitTransactionFailure` subclasses are the design's one stated
#: exception; invariant 2 walks `__subclasses__()` so a new one fails a test
#: until it is listed here.
ALLOWLIST: frozenset[type[BaseException]] = frozenset(
    {
        _outbox.OutboxTransactionError,
        _registry.RegistryTransactionError,
        _outbox.OutboxDestinationError,
        _git_transaction.GitTransactionFailure,
        _git_transaction._ApprovalLockCleanupFailure,
        _git_transaction.QuarantineCleanupError,
        _git_transaction._ReviewedIndexOwnershipConflict,
    }
)


def _lookup(exc: BaseException) -> ConsoleError:
    cls = type(exc)
    if cls in _EXACT:                      # exact: this class only, never MRO
        return _EXACT[cls]
    for ancestor in cls.__mro__:
        if (
            isinstance(ancestor, type)
            and issubclass(ancestor, CLOSED_FAMILY)
            and ancestor is not Exception
        ):
            return UNKNOWN                 # closed family: no inheritance
        if ancestor in _MRO:
            return _MRO[ancestor]
    return UNKNOWN


def describe(exc: BaseException) -> ConsoleError:
    """Resolve an exception's outcome across its allowlisted cause chain.

    The resolver never reads exception text, ``args``, or attributes — only
    the class identity of each link selects a curated description. Only
    ``__cause__`` is traversed, never ``__context__``. Ties resolve to the
    innermost candidate; overflow fails closed to E-UNKNOWN.
    """
    best, current, depth = None, exc, 0
    while True:
        candidate = _lookup(current)
        if best is None or _TIER_RANK[candidate.tier] <= _TIER_RANK[best.tier]:
            best = candidate               # <= : innermost wins a tie
        if type(current) not in ALLOWLIST:  # exact identity, per the design
            return best
        nxt = current.__cause__
        if nxt is None:
            return best
        depth += 1
        if depth >= MAX_DEPTH:
            return UNKNOWN                 # overflow fails closed
        current = nxt
