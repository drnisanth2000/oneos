import textwrap

import pytest
from app.console_errors import ConsoleError


def test_refusal_cannot_report_a_commit():
    with pytest.raises(ValueError):
        ConsoleError("E-X", "refusal", "refusal", "m", "retry", "yes", 422)


def test_committed_tier_must_stop_and_report_yes():
    with pytest.raises(ValueError):
        ConsoleError("E-X", "committed", "attention", "m", "retry", "yes", 500)
    with pytest.raises(ValueError):
        ConsoleError("E-X", "committed", "attention", "m", "stop", "no", 500)


def test_recovery_tier_must_stop_and_report_unknown():
    with pytest.raises(ValueError):
        ConsoleError("E-X", "recovery", "attention", "m", "stop", "no", 500)


def test_page_status_must_be_a_known_http_status():
    with pytest.raises(ValueError):
        ConsoleError("E-X", "refusal", "refusal", "m", "none", "no", 299)


def test_console_error_is_frozen():
    e = ConsoleError("E-X", "refusal", "refusal", "m", "none", "no", 422)
    with pytest.raises(Exception):
        e.code = "E-Y"


# --- Task 5: the class map and describe() -----------------------------------


def _code_of(exc) -> str:
    from app.console_errors import describe

    return describe(exc).code


def _committed_error():
    from app.git_transaction import GitTransactionCommittedError, TransactionResult

    return GitTransactionCommittedError(
        TransactionResult("a" * 64, ("probe/path.md",)), OSError("probe")
    )


def _post_commit_consumption_error():
    from app.git_transaction import PostCommitConsumptionError, TransactionResult

    return PostCommitConsumptionError(
        TransactionResult("a" * 64, ("probe/path.md",)), OSError("probe")
    )


# One test per row of the design's normative class map.

def test_map_GitTransactionCommittedError():
    assert _code_of(_committed_error()) == "E-COMMITTED"


def test_map_PostCommitConsumptionError():
    assert _code_of(_post_commit_consumption_error()) == "E-APPLIED"


def test_e_applied_contract_is_exact_and_committed():
    from app.console_errors import describe

    outcome = describe(_post_commit_consumption_error())
    assert outcome == ConsoleError(
        "E-APPLIED",
        "committed",
        "attention",
        "The action completed, but OneOS could not verify that its proposal "
        "was safely consumed. Its receipt prevents this proposal ID from "
        "being used again. Do not retry or move files by hand. Inspect vault "
        "state with git status.",
        "stop",
        "yes",
        500,
    )


def test_map_GitTransactionRecoveryError():
    from app.git_transaction import GitTransactionRecoveryError

    assert _code_of(GitTransactionRecoveryError(("probe/path.md",))) == "E-RECOVER"


def test_map_ReviewedPathIntegrityError():
    from app.git_transaction import ReviewedPathIntegrityError

    assert _code_of(ReviewedPathIntegrityError("probe")) == "E-TAMPER"


def test_map_ReviewedPathUnavailable():
    from app.git_transaction import ReviewedPathUnavailable

    assert _code_of(ReviewedPathUnavailable("probe")) == "E-UNAVAILABLE"


def test_map_ReviewedStateChanged():
    from app.git_transaction import ReviewedStateChanged

    assert _code_of(ReviewedStateChanged("probe")) == "E-CONFLICT"


def test_map_InvalidTransactionPath():
    from app.git_transaction import InvalidTransactionPath

    assert _code_of(InvalidTransactionPath("probe")) == "E-INTERNAL"


def test_map_VaultBusyError():
    from app.git_transaction import VaultBusyError

    assert _code_of(VaultBusyError("probe")) == "E-BUSY"


def test_map_GitTransactionFailure():
    from app.git_transaction import GitTransactionFailure

    assert _code_of(GitTransactionFailure("probe")) == "E-GIT"


def test_map_GitTransactionError():
    from app.git_transaction import GitTransactionError

    assert _code_of(GitTransactionError("probe")) == "E-GIT"


def test_map_ApprovalLockCleanupFailure():
    from app.git_transaction import _ApprovalLockCleanupFailure

    assert _code_of(_ApprovalLockCleanupFailure(OSError("probe"))) == "E-GIT"


def test_map_ReviewedIndexOwnershipConflict():
    from app.git_transaction import _ReviewedIndexOwnershipConflict

    assert _code_of(_ReviewedIndexOwnershipConflict(("probe/path.md",))) == "E-CONFLICT"


def test_map_RedirectedPathError():
    from app.scope import RedirectedPathError

    assert _code_of(RedirectedPathError("probe")) == "E-TAMPER"


def test_map_ProposalSourceUnavailable():
    from app.outbox import ProposalSourceUnavailable

    assert _code_of(ProposalSourceUnavailable("probe")) == "E-UNAVAILABLE"


def test_map_OutOfScopeError():
    from app.scope import OutOfScopeError

    assert _code_of(OutOfScopeError("probe")) == "E-SCOPE"


def test_map_OutboxScopeError():
    from app.outbox import OutboxScopeError

    assert _code_of(OutboxScopeError("probe")) == "E-SCOPE"


def test_map_UnreadableProposalRecord():
    from app.outbox import UnreadableProposalRecord

    assert _code_of(UnreadableProposalRecord("probe")) == "E-UNREADABLE"


def test_map_StaleProposalSource():
    from app.outbox import StaleProposalSource

    assert _code_of(StaleProposalSource("probe")) == "E-STALE"


def test_map_MissingProposalSource():
    from app.outbox import MissingProposalSource

    assert _code_of(MissingProposalSource("probe")) == "E-MISSING"


def test_map_ProposalFreshnessError():
    from app.outbox import ProposalFreshnessError

    assert _code_of(ProposalFreshnessError("probe")) == "E-STALE"


def test_map_OutboxTransactionError():
    from app.outbox import OutboxTransactionError

    assert _code_of(OutboxTransactionError("probe")) == "E-GIT"


def test_map_OutboxDestinationError():
    from app.outbox import OutboxDestinationError

    assert _code_of(OutboxDestinationError("probe")) == "E-INVALID"


def test_map_OutboxError():
    from app.outbox import OutboxError

    assert _code_of(OutboxError("probe")) == "E-INVALID"


def test_map_ProposalIdentityError():
    from app.proposal_identity import ProposalIdentityError

    assert _code_of(ProposalIdentityError("probe")) == "E-INVALID"


def test_map_RedirectedDestination():
    from app.destinations import RedirectedDestination

    assert _code_of(RedirectedDestination("probe")) == "E-TAMPER"


def test_map_RedirectedSourceLeaf():
    from app.destinations import RedirectedSourceLeaf

    assert _code_of(RedirectedSourceLeaf("probe")) == "E-TAMPER"


def test_map_NonCanonicalLeaf():
    from app.destinations import NonCanonicalLeaf

    assert _code_of(NonCanonicalLeaf("probe")) == "E-DEST"


def test_map_MissingSourceLeaf():
    from app.destinations import MissingSourceLeaf

    assert _code_of(MissingSourceLeaf("probe")) == "E-DEST"


def test_map_MissingDestination():
    from app.destinations import MissingDestination

    assert _code_of(MissingDestination("probe")) == "E-DEST"


def test_map_DestinationError():
    from app.destinations import DestinationError

    assert _code_of(DestinationError("probe")) == "E-DEST"


def test_map_DestinationRegistryError():
    from app.vault import DestinationRegistryError

    assert _code_of(DestinationRegistryError("probe")) == "E-CONFIG"


def test_map_SystemRegistryPathError():
    from app.entities import SystemRegistryPathError

    assert _code_of(SystemRegistryPathError("probe")) == "E-TAMPER"


def test_map_RecipientConfigurationError():
    from app.entities import RecipientConfigurationError

    assert _code_of(RecipientConfigurationError("probe")) == "E-CONFIG"


def test_map_EntityManifestError():
    from app.entities import EntityManifestError

    assert _code_of(EntityManifestError("probe")) == "E-CONFIG"


def test_map_EntitySelectionError():
    from app.entities import EntitySelectionError

    assert _code_of(EntitySelectionError("probe")) == "E-ENTITY"


def test_map_RegistryTransactionError():
    from app.registry import RegistryTransactionError

    assert _code_of(RegistryTransactionError("probe")) == "E-GIT"


def test_map_RegistryError():
    from app.registry import RegistryError

    assert _code_of(RegistryError("probe")) == "E-REGISTRY"


def test_map_IngestError():
    from app.ingest.base import IngestError

    assert _code_of(IngestError("probe")) == "E-INGEST"


def test_map_RenameError():
    from app.rename import RenameError

    assert _code_of(RenameError("probe")) == "E-ADMIN"


def test_map_RequestValidationError():
    from fastapi.exceptions import RequestValidationError

    assert _code_of(RequestValidationError([])) == "E-REQUEST"


# --- resolver behavior -------------------------------------------------------


OUTBOX_ARCHETYPES = textwrap.dedent(
    """\
    version: "2.0"
    flags: {}
    modules:
      00-inbox: {block: system}
      11-knowledge: {block: govern}
    submodules:
      00-inbox:
        triage: {name: Triage}
      11-knowledge:
        kb: {name: Knowledge base}
    archetypes:
      plain: {}
    """
)

RECEIPT = textwrap.dedent(
    """\
    ---
    type: inbox-item
    title: Synthetic receipt
    entity: demo
    product: null
    status: active
    created: 2026-01-01
    updated: 2026-01-01
    sub: triage
    source: folder
    ---
    Synthetic receipt body.
    """
)


def _fp(scope, proposal_id: str) -> str:
    """The fingerprint of the proposal exactly as it now stands (S7)."""
    from app.outbox import get_proposal_review

    return get_proposal_review(scope, proposal_id).sha256


def _outbox_fixture(tmp_path):
    from app.outbox import propose_classification
    from app.scope import Scope
    from tests.conftest import git_entity_vault

    vault = git_entity_vault(
        tmp_path,
        ("demo",),
        {
            "_system/archetypes.yaml": OUTBOX_ARCHETYPES,
            "demo/00-inbox/active/note.md": RECEIPT,
            "demo/11-knowledge/active/.gitkeep": "",
        },
    )
    scope = Scope(vault, "demo")
    proposal = propose_classification(
        scope,
        scope.resolve("00-inbox", "active", "note.md"),
        module="11-knowledge",
        sub="kb",
    )
    return vault, scope, proposal


def _fp_delete(scope, proposal_id: str) -> str:
    """The fingerprint of the delete proposal as it now stands (S7)."""
    from app.registry import get_delete_review

    return get_delete_review(scope, proposal_id).sha256


def _registry_fixture(tmp_path):
    from app.registry import propose_delete
    from app.scope import Scope
    from tests.conftest import git_entity_vault

    vault = git_entity_vault(
        tmp_path,
        ("demo",),
        {
            "_system/products.yaml": (
                'version: "1.0"\nproducts:\n  demo:\n    widgetx:\n'
                "      label: Widgetx\n"
            ),
        },
    )
    scope = Scope(vault, "demo")
    proposal = propose_delete(scope, "product", "widgetx")
    return vault, scope, proposal


_S5_OUTCOMES = (
    ("committed", "E-COMMITTED"),
    ("recovery", "E-RECOVER"),
    ("busy", "E-BUSY"),
    ("conflict", "E-CONFLICT"),
    ("rolled-back", "E-GIT"),
)


def _s5_exception(kind):
    from app.git_transaction import (
        GitTransactionFailure,
        GitTransactionRecoveryError,
        ReviewedStateChanged,
        VaultBusyError,
    )

    if kind == "committed":
        return _committed_error()
    if kind == "recovery":
        return GitTransactionRecoveryError(("probe/path.md",))
    if kind == "busy":
        return VaultBusyError("probe")
    if kind == "conflict":
        return ReviewedStateChanged("probe")
    return GitTransactionFailure("probe")


def test_committed_outcome_survives_the_domain_wrapper(tmp_path, monkeypatch):
    # The failure is injected into the transaction layer and propagates
    # through the actual `approve` wrapper and its `from exc` chain.
    import app.outbox as outbox

    _vault, scope, proposal = _outbox_fixture(tmp_path)

    def fail_transaction(*_args, **_kwargs):
        raise _committed_error()

    monkeypatch.setattr(outbox, "execute_transaction", fail_transaction)

    with pytest.raises(outbox.OutboxTransactionError) as raised:
        outbox.approve(scope, proposal.id, _fp(scope, proposal.id))

    assert _code_of(raised.value) == "E-COMMITTED"


def test_recovery_outcome_survives_both_wrappers(tmp_path, monkeypatch):
    import app.outbox as outbox
    import app.registry as registry
    from app.git_transaction import GitTransactionRecoveryError

    def fail_transaction(*_args, **_kwargs):
        raise GitTransactionRecoveryError(("probe/path.md",))

    _vault, scope, proposal = _outbox_fixture(tmp_path / "outbox-vault")
    monkeypatch.setattr(outbox, "execute_transaction", fail_transaction)
    with pytest.raises(outbox.OutboxTransactionError) as via_outbox:
        outbox.approve(scope, proposal.id, _fp(scope, proposal.id))
    assert _code_of(via_outbox.value) == "E-RECOVER"

    _vault, scope, proposal = _registry_fixture(tmp_path / "registry-vault")
    monkeypatch.setattr(registry, "execute_transaction", fail_transaction)
    with pytest.raises(registry.RegistryTransactionError) as via_registry:
        registry.execute_delete(scope, proposal.id, _fp_delete(scope, proposal.id))
    assert _code_of(via_registry.value) == "E-RECOVER"


@pytest.mark.parametrize(("kind", "expected"), _S5_OUTCOMES)
def test_all_five_s5_outcomes_via_registry_wrapper(
    tmp_path, monkeypatch, kind, expected
):
    import app.registry as registry

    _vault, scope, proposal = _registry_fixture(tmp_path)

    def fail_transaction(*_args, **_kwargs):
        raise _s5_exception(kind)

    monkeypatch.setattr(registry, "execute_transaction", fail_transaction)

    with pytest.raises(registry.RegistryTransactionError) as raised:
        registry.execute_delete(scope, proposal.id, _fp_delete(scope, proposal.id))

    assert _code_of(raised.value) == expected


@pytest.mark.parametrize(("kind", "expected"), _S5_OUTCOMES)
def test_all_five_s5_outcomes_via_outbox_wrapper(
    tmp_path, monkeypatch, kind, expected
):
    import app.outbox as outbox

    _vault, scope, proposal = _outbox_fixture(tmp_path)

    def fail_transaction(*_args, **_kwargs):
        raise _s5_exception(kind)

    monkeypatch.setattr(outbox, "execute_transaction", fail_transaction)

    with pytest.raises(outbox.OutboxTransactionError) as raised:
        outbox.approve(scope, proposal.id, _fp(scope, proposal.id))

    assert _code_of(raised.value) == expected


def test_config_survives_outbox_destination_wrapper(tmp_path):
    # A broken registry raised through the real loading path arrives as
    # OutboxDestinationError with a DestinationRegistryError cause and must
    # resolve to E-CONFIG, not the wrapper's E-INVALID.
    import app.outbox as outbox

    vault, scope, _proposal = _outbox_fixture(tmp_path)
    (vault / "_system/archetypes.yaml").write_text(
        'version: "2.0"\nflags: {}\nmodules: [unterminated\n', encoding="utf-8"
    )

    with pytest.raises(outbox.OutboxDestinationError) as raised:
        outbox.load_proposals(scope)

    assert _code_of(raised.value) == "E-CONFIG"


def test_context_is_never_traversed():
    from app.outbox import OutboxTransactionError

    try:
        try:
            raise _committed_error()
        except Exception:
            raise OutboxTransactionError("probe")  # implicit __context__ only
    except OutboxTransactionError as raised:
        assert raised.__cause__ is None
        assert raised.__context__ is not None
        assert _code_of(raised) == "E-GIT"


def test_depth_overflow_fails_closed_to_unknown():
    from app.outbox import OutboxTransactionError

    chain = None
    for _ in range(5):
        link = OutboxTransactionError("probe")
        link.__cause__ = chain
        chain = link

    assert _code_of(chain) == "E-UNKNOWN"


def test_allowlist_membership_is_exact_class_identity():
    from app.outbox import OutboxTransactionError

    class SyntheticWrapper(OutboxTransactionError):
        pass

    wrapper = SyntheticWrapper("probe")
    wrapper.__cause__ = _committed_error()

    # The subclass is not a member, so the walk stops and the cause is never
    # read: the committed outcome must NOT surface.
    assert _code_of(wrapper) != "E-COMMITTED"


def test_exact_mapping_does_not_inherit():
    from app.outbox import StaleProposalSource
    from app.registry import RegistryTransactionError

    class SyntheticStale(StaleProposalSource):
        pass

    # An exact-mapped non-Git class does not pass its code to subclasses;
    # the subclass resolves via MRO rules instead (OutboxError -> E-INVALID).
    assert _code_of(SyntheticStale("probe")) != "E-STALE"
    assert _code_of(SyntheticStale("probe")) == "E-INVALID"

    class SyntheticRegistryTransaction(RegistryTransactionError):
        pass

    # Same rule for the RegistryError-parented exact class: the subclass
    # resolves via MRO to E-REGISTRY, never inheriting the exact E-GIT.
    assert _code_of(SyntheticRegistryTransaction("probe")) != "E-GIT"
    assert _code_of(SyntheticRegistryTransaction("probe")) == "E-REGISTRY"


def test_closed_family_synthetic_subclass_is_unknown():
    from app.git_transaction import GitTransactionError

    class SyntheticGitError(GitTransactionError):
        pass

    assert _code_of(SyntheticGitError("probe")) == "E-UNKNOWN"


def test_abstract_bases_resolve_nowhere_and_are_never_raised():
    from app.console_errors import _EXACT, _MRO
    from app.action_receipts import ReceiptError
    from app.destinations import InvalidSourceLeaf, UnsafeDestinationPath
    from app.git_transaction import ReviewedStateConflict
    from app.scope import CrossScopeError

    for base in (
        CrossScopeError,
        ReviewedStateConflict,
        UnsafeDestinationPath,
        InvalidSourceLeaf,
        ReceiptError,
    ):
        assert base not in _EXACT
        assert base not in _MRO
    # invariant 3 (tests/test_console_invariants.py) proves they are never
    # raised directly anywhere under app/.


def test_committed_message_affirms_success_and_forbids_retry():
    from app.console_errors import _CODES

    message = _CODES["E-COMMITTED"].message
    assert "commit succeeded" in message
    assert "Do not retry" in message


def test_no_attention_message_invites_a_retry():
    from app.console_errors import _CODES

    for error in _CODES.values():
        if error.severity == "attention":
            assert error.retry in {"stop", "none"}
            assert "Try again" not in error.message


# --- S7 Task 1: the bound-review outcomes ------------------------------------


def test_map_ReviewedProposalChanged():
    from app.review_tokens import ReviewedProposalChanged

    assert _code_of(ReviewedProposalChanged("probe")) == "E-REVIEW"


def test_map_InvalidReviewToken():
    from app.review_tokens import InvalidReviewToken

    assert _code_of(InvalidReviewToken("probe")) == "E-REQUEST"


def test_map_ReviewTokenError_base_is_an_internal_defect():
    # The base is never raised deliberately; if it ever surfaces it is a
    # defect in OneOS, not a problem with the operator's data.
    from app.review_tokens import ReviewTokenError

    assert _code_of(ReviewTokenError("probe")) == "E-INTERNAL"


def test_map_ReviewContractViolation():
    from app.review_tokens import ReviewContractViolation

    assert _code_of(ReviewContractViolation("probe")) == "E-INTERNAL"


def test_review_outcomes_do_not_inherit_each_others_codes():
    from app.review_tokens import InvalidReviewToken, ReviewedProposalChanged

    class SyntheticChanged(ReviewedProposalChanged):
        pass

    class SyntheticInvalid(InvalidReviewToken):
        pass

    # Exact entries never pass through MRO. A subclass degrading to the
    # base's internal-defect code is the safe runtime backstop, not the
    # intended end state: the closed-family walk in
    # tests/test_console_invariants.py fails until any new application
    # subclass declares its own exact outcome.
    assert _code_of(SyntheticChanged("probe")) == "E-INTERNAL"
    assert _code_of(SyntheticInvalid("probe")) == "E-INTERNAL"


def test_changed_review_message_is_the_approved_wording_verbatim():
    from app.console_errors import _CODES

    error = _CODES["E-REVIEW"]
    assert error.message == (
        "Proposal changed since your review. Nothing was changed."
    )
    assert error.severity == "refusal"
    assert error.committed == "no"
    assert error.page_status == 409
    assert error.retry == "reload"


def test_changed_review_is_a_distinct_outcome_from_reviewed_state_changed():
    # S5's E-CONFLICT describes reviewed *files* changing under a
    # transaction. S7's E-REVIEW describes the proposal record itself
    # changing since the operator reviewed it. Collapsing them would make
    # the Console tell the operator the wrong thing to do.
    from app.console_errors import _CODES

    assert _CODES["E-REVIEW"].message != _CODES["E-CONFLICT"].message
    assert _CODES["E-REVIEW"].code != _CODES["E-CONFLICT"].code


def test_changed_review_message_names_no_bytes_and_promises_no_change():
    from app.console_errors import _CODES

    message = _CODES["E-REVIEW"].message
    assert "Nothing was changed." in message
    assert "sha" not in message.lower()
    assert "/" not in message


# --- S7 Amendment 3 Stage 2: committed action receipt outcomes --------------


def test_map_InvalidActionReceipt():
    from app.action_receipts import InvalidActionReceipt

    assert _code_of(InvalidActionReceipt("probe")) == "E-RECEIPT"


def test_map_ReceiptStoreIntegrityError():
    from app.action_receipts import ReceiptStoreIntegrityError

    assert _code_of(ReceiptStoreIntegrityError("probe")) == "E-TAMPER"


def test_map_ReceiptStoreUnavailable():
    from app.action_receipts import ReceiptStoreUnavailable

    assert _code_of(ReceiptStoreUnavailable("probe")) == "E-UNAVAILABLE"


def test_invalid_receipt_message_is_the_approved_wording_verbatim():
    from app.console_errors import _CODES

    error = _CODES["E-RECEIPT"]
    assert error.message == (
        "OneOS found an invalid action receipt for this proposal ID. It "
        "cannot safely tell what completed action the receipt represents, "
        "so the ID is disabled. Do not retry, and do not move or delete "
        "files by hand. No automated recovery is available. Inspect vault "
        "state with git status and escalate for verified recovery."
    )
    assert error.tier == "integrity"
    assert error.severity == "attention"
    assert error.committed == "no"
    assert error.retry == "stop"
    assert error.page_status == 500
