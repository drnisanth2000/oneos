"""outbox.py — the one write path (invariant 1).

The app never mutates curated vault content directly. Confirming a triage
classification writes a *proposal* into `<entity>/outbox/` describing the move;
nothing is moved yet (step 7). Approval performs the real move and commits, as
exactly one revertible commit; reject discards the proposal (step 8).

Proposals are plain YAML under `outbox/`, which is a system area excluded from
block-mapping validation — so it never trips check_v2 or the module lint.
"""
from __future__ import annotations

import difflib
import errno
import hashlib
import os
import re
import stat
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml

from .console_routing import structured_reader
from .git_transaction import (
    GitTransactionError,
    PathChange,
    PathState,
    ReviewedPathIntegrityError,
    ReviewedPathUnavailable,
    TransactionPlan,
    capture_path_state,
    execute_transaction,
    remove_path_if_unchanged,
)
from .inbox import split_front_matter
from .destinations import DestinationError, resolve_classification_destination
from .proposal_identity import (
    ProposalIdentityError,
    proposal_id_candidates,
    require_proposal_identity,
)
from .review_tokens import (
    ReviewSnapshot,
    make_review_snapshot,
    require_review_match,
)
from .scope import CrossScopeError, OutOfScopeError, RedirectedPathError, Scope
from .vault import DestinationRegistryError


class OutboxError(Exception):
    pass


class UnreadableProposalRecord(OutboxError):
    pass


class ProposalSourceUnavailable(CrossScopeError):
    pass


class ProposalFreshnessError(OutboxError):
    pass


class MissingProposalSource(ProposalFreshnessError):
    pass


class StaleProposalSource(ProposalFreshnessError):
    pass


class OutboxScopeError(OutboxError):
    pass


class OutboxDestinationError(OutboxError):
    pass


class OutboxTransactionError(OutboxError):
    pass


@dataclass
class Proposal:
    id: str
    path: Path
    action: str
    entity: str
    src: str          # vault-relative source path
    source_sha256: str
    dst: str          # vault-relative destination path
    module: str
    sub: str | None
    block: str
    rule_id: str | None
    created: str
    status: str = "pending"


@dataclass(frozen=True)
class OutboxRow:
    """One outbox entry as the Console can safely present it. Rows carry
    **capabilities**, not kinds (design §3): a row that cannot be diffed or
    approved is still a row, and it never withholds another row's controls."""
    proposal: Proposal | None
    diff: str | None
    error: BaseException | None
    can_approve: bool
    can_reject: bool
    #: S7. The SHA-256 of the exact stored bytes this row was built from —
    #: the fingerprint its controls must carry and the action boundary will
    #: compare. It is a change detector, not a secret or a capability: it
    #: authorises nothing on its own (threat model, design §Threat model).
    #: `None` means no action is offered.
    review_sha256: str | None = None

    def __post_init__(self) -> None:
        # Controls and fingerprint are issued together or not at all. A
        # button without a fingerprint could not be bound to what was
        # reviewed; a fingerprint on a row with no buttons is an
        # action-binding value for an action this listing has already
        # decided must not be offered — meaningless at best, misleading at
        # worst.
        actionable = self.can_approve or self.can_reject
        if actionable and self.review_sha256 is None:
            raise ValueError("an actionable row must carry its review fingerprint")
        if not actionable and self.review_sha256 is not None:
            raise ValueError("a row without controls must not carry a fingerprint")


@dataclass(frozen=True)
class OutboxListing:
    """`blocked` is reserved for the family that actually poisons
    `load_proposals` — a genuinely unreadable record — because those are the
    conditions under which no action in the entity can succeed."""
    rows: tuple[OutboxRow, ...]
    blocked: bool


def _require_outbox_path(
    scope: Scope,
    proposal_path: Path | None = None,
    *,
    create_directory: bool = False,
    require_leaf: bool = False,
) -> Path:
    """Retain the lexical outbox path and reject every redirected component."""
    lexical_outbox = scope.root / scope.current_entity() / "outbox"
    # C2 (S6 review): classify a lexical symlink BEFORE calling
    # scope.resolve() — matching the correct order already used a few lines
    # below in this same function. Calling scope.resolve() first (as this
    # site previously did, by assigning its result before the is_symlink()
    # check) lets a redirected outbox raise OutOfScopeError (-> E-SCOPE)
    # instead of RedirectedPathError (-> E-TAMPER), the wrong tier for a
    # redirection finding (design §2).
    if lexical_outbox.is_symlink():
        raise RedirectedPathError("outbox directory is redirected")
    resolved_outbox = scope.resolve("outbox")
    if resolved_outbox != lexical_outbox:
        raise RedirectedPathError("outbox directory is redirected")
    if lexical_outbox.exists():
        if not lexical_outbox.is_dir():
            raise RedirectedPathError("outbox path is not a real directory")
    elif create_directory:
        try:
            lexical_outbox.mkdir()
        except FileExistsError as exc:
            raise RedirectedPathError("outbox directory changed during creation") from exc
        if lexical_outbox.is_symlink() or scope.resolve("outbox") != lexical_outbox:
            raise RedirectedPathError("outbox directory is redirected")

    if proposal_path is None:
        return lexical_outbox

    candidate = Path(proposal_path)
    if (
        candidate.parent != lexical_outbox
        or candidate != lexical_outbox / candidate.name
        or candidate.suffix != ".yaml"
    ):
        raise OutOfScopeError("proposal is outside the lexical outbox")
    if candidate.is_symlink():
        raise RedirectedPathError("proposal leaf is redirected")
    if candidate.exists():
        if not candidate.is_file() or candidate.resolve() != candidate:
            raise RedirectedPathError("proposal leaf is not a real file")
    elif require_leaf:
        raise OutboxError("proposal leaf no longer exists")
    return candidate


def _read_no_follow_bytes(path: Path) -> bytes:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        raise
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.EMLINK}:
            # O_NOFOLLOW rejection: ELOOP on Linux/macOS, EMLINK on some BSDs.
            raise RedirectedPathError(
                "source receipt is redirected or unsafe"
            ) from exc
        raise ProposalSourceUnavailable(
            "source receipt could not be read"
        ) from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise RedirectedPathError("source receipt is not a regular file")
        with os.fdopen(descriptor, "rb", closefd=True) as stream:
            descriptor = -1
            return stream.read()
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def propose_classification(
    scope: Scope,
    item_path: Path,
    *,
    module: str,
    sub: str,
    claimed_block: str | None = None,
    rule_id: str | None = None,
) -> Proposal:
    """Write a classify proposal. Moves nothing."""
    destination = resolve_classification_destination(
        scope,
        item_path,
        module=module,
        sub=sub,
        claimed_block=claimed_block,
    )
    created_at = datetime.now()
    try:
        source_bytes = _read_no_follow_bytes(scope.root / destination.src)
    except FileNotFoundError as exc:
        raise OutboxDestinationError("source receipt is missing") from exc
    source_sha256 = hashlib.sha256(source_bytes).hexdigest()

    record = {
        "action": "classify",
        "entity": destination.entity,
        "created": created_at.isoformat(timespec="seconds"),
        "status": "pending",
        "src": destination.src,
        "source_sha256": source_sha256,
        "dst": destination.dst,
        "module": destination.module,
        "sub": destination.sub,
        "block": destination.block,
        "rule_id": rule_id,
    }
    outbox = _require_outbox_path(scope, create_directory=True)
    for pid in proposal_id_candidates(created_at):
        path = _require_outbox_path(scope, outbox / f"{pid}.yaml")
        record["id"] = pid
        try:
            with path.open("x", encoding="utf-8") as stream:
                stream.write(yaml.safe_dump(record, sort_keys=False))
        except FileExistsError:
            continue
        return _to_proposal(path, record)
    raise OutboxError("unable to allocate a unique classification proposal id")


def _required_string(record: dict, key: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value:
        raise OutboxDestinationError("proposal destination record is malformed")
    return value


_SOURCE_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _required_source_hash(record: dict) -> str:
    value = record.get("source_sha256")
    if not isinstance(value, str) or _SOURCE_SHA256.fullmatch(value) is None:
        raise OutboxDestinationError("proposal source hash is malformed")
    return value


def _to_proposal(path: Path, record: dict) -> Proposal:
    if not isinstance(record, dict):
        raise OutboxDestinationError("proposal record must be a mapping")
    try:
        require_proposal_identity(path, record.get("id"))
    except ProposalIdentityError as exc:
        raise OutboxDestinationError("proposal identity is invalid") from exc
    if "sub" not in record:
        raise OutboxDestinationError("proposal destination record is malformed")
    sub = record.get("sub")
    if sub is not None and (not isinstance(sub, str) or not sub):
        raise OutboxDestinationError("proposal sub must be a string or null")
    if record.get("action") != "classify":
        raise OutboxDestinationError("proposal is not a classification")
    return Proposal(
        id=_required_string(record, "id"),
        path=path,
        action=_required_string(record, "action"),
        entity=_required_string(record, "entity"),
        src=_required_string(record, "src"),
        source_sha256=_required_source_hash(record),
        dst=_required_string(record, "dst"),
        module=_required_string(record, "module"),
        sub=sub,
        block=_required_string(record, "block"),
        rule_id=(
            record.get("rule_id")
            if isinstance(record.get("rule_id"), str)
            else None
        ),
        created=(
            record.get("created") if isinstance(record.get("created"), str) else ""
        ),
        status=(
            record.get("status")
            if isinstance(record.get("status"), str)
            else "pending"
        ),
    )


@structured_reader(category="proposal")
def _parse_record_bytes(contents: bytes) -> object:
    """Parse a proposal record from bytes already in hand.

    S7's single-read rule lives on this seam: a caller that has captured
    exact bytes must be able to parse *those* bytes, never re-open the path
    and parse whatever is there now. `_read_record` delegates here so the
    two cannot drift into two different parsers.
    """
    try:
        text = contents.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise UnreadableProposalRecord(
            "proposal record is not valid UTF-8"
        ) from exc
    try:
        return yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise UnreadableProposalRecord(
            "proposal record is invalid YAML"
        ) from exc


@structured_reader(category="proposal")
def _read_record(path: Path) -> object:
    """Read and parse a stored proposal record. A `proposal`-category
    structured read (design §7 invariant 4): every way reading or shaping it
    can fail becomes `UnreadableProposalRecord`, never a raw stdlib type."""
    try:
        contents = path.read_bytes()
    except OSError as exc:
        raise UnreadableProposalRecord(
            "proposal record could not be read"
        ) from exc
    return _parse_record_bytes(contents)


def _validate_record(path: Path, record: object) -> Proposal | None:
    """Schema and identity only (design §3 phase 1) — never touches the
    registries or reads anything beyond the record itself. Returns ``None``
    for a well-formed ``action: delete`` record, which both the strict loader
    and the projection skip identically."""
    if not isinstance(record, dict):
        raise UnreadableProposalRecord("proposal record must be a mapping")
    try:
        require_proposal_identity(path, record.get("id"))
    except ProposalIdentityError as exc:
        raise UnreadableProposalRecord("proposal identity is invalid") from exc
    action = record.get("action")
    if not isinstance(action, str) or not action:
        raise UnreadableProposalRecord("proposal action is malformed")
    if action == "delete":
        return None
    if action != "classify":
        raise UnreadableProposalRecord("proposal action is unknown")
    try:
        return _to_proposal(path, record)
    except OutboxDestinationError as exc:
        raise UnreadableProposalRecord(
            "proposal record is malformed"
        ) from exc


def _load_proposal_reviews(scope: Scope) -> list[ReviewSnapshot[Proposal]]:
    """The strict loader, as review snapshots — one safe scan, one read each.

    Every record is captured through the same no-follow boundary the actions
    use, then parsed, validated and destination-checked *from those captured
    bytes*. So a record is never read twice, a leaf swapped for a symlink
    after its lexical check is refused rather than followed, and the value a
    caller receives is paired with the fingerprint of the bytes it came from.

    The loader's all-or-nothing refusal is unchanged and deliberately
    complete: an unreadable record and a record whose destination no longer
    canonicalises both refuse every proposal in the entity, because both are
    conditions under which no action here can be trusted.
    """
    outbox = _require_outbox_path(scope)
    if not outbox.exists():
        return []
    reviews: list[ReviewSnapshot[Proposal]] = []
    for discovered in sorted(outbox.glob("*.yaml")):
        leaf = _require_outbox_path(scope, discovered, require_leaf=True)
        try:
            contents = _capture_proposal_contents(scope, leaf)
            proposal = _validate_record(leaf, _parse_record_bytes(contents))
        except UnreadableProposalRecord as exc:
            # D1 (ledger): re-narrow to the strict loader's existing escaping
            # type, so every `except OutboxDestinationError` clause and every
            # `_assert_destination_error` call site is unchanged.
            raise OutboxDestinationError(
                "proposal record could not be read"
            ) from exc
        if proposal is None:
            continue
        reviews.append(
            make_review_snapshot(_require_destination(scope, proposal), contents)
        )
    return reviews


def load_proposals(scope: Scope) -> list[Proposal]:
    return [review.value for review in _load_proposal_reviews(scope)]


def _apply_sub(text: str, sub: str | None) -> str:
    """The move's only content change: the `sub:` front-matter value. `block`
    is derived from the module, never written per file (conventions v2 §1)."""
    if sub is None:
        return re.sub(r"(?m)^sub:\s*.*\n?", "", text, count=1)
    if re.search(r"(?m)^sub:\s*.*$", text):
        return re.sub(r"(?m)^sub:\s*.*$", f"sub: {sub}", text, count=1)
    # no sub line yet — insert one just before the closing front-matter fence
    fm_end = text.find("---", 3)
    if fm_end != -1:
        return text[:fm_end] + f"sub: {sub}\n" + text[fm_end:]
    return text


def _diff_text(proposal: Proposal, old: str) -> str:
    """Render the move diff from already-decoded source text.

    Shared by `_render_diff` and `preview_diff`, which differ only in **read
    policy** — the diff production itself is identical, and duplicating it let
    the two drift with no test able to notice.
    """
    new = _apply_sub(old, proposal.sub)
    diff = difflib.unified_diff(
        old.splitlines(True), new.splitlines(True),
        fromfile=f"a/{proposal.src}", tofile=f"b/{proposal.dst}",
    )
    return f"move: {proposal.src} → {proposal.dst}\n" + "".join(diff)


def _render_diff(scope: Scope, proposal: Proposal) -> str:
    """Diff-only work on an **already-validated** record (design §3 phase 3).
    Reads the source receipt through the same safe-read boundary `approve`
    uses, and translates failures into the same domain types `approve` raises
    for the identical physical cause — so a projected row and its approve
    button never describe different conditions for one cause."""
    src = scope.root / proposal.src
    try:
        source_bytes = _read_no_follow_bytes(src)
    except FileNotFoundError as exc:
        raise MissingProposalSource("proposal source is missing") from exc
    # RedirectedPathError and ProposalSourceUnavailable are raised directly by
    # `_read_no_follow_bytes` and pass through unmodified — the same domain
    # types `approve` lets propagate for the same conditions.
    try:
        old = source_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise OutboxDestinationError(
            "proposal source is not UTF-8 markdown"
        ) from exc
    return _diff_text(proposal, old)


def preview_diff(scope: Scope, proposal: Proposal) -> str:
    """A unified diff previewing what approval would do — the file moving from
    src to dst with `sub:` updated. Reads only; renders, never moves.

    Deliberately does **not** delegate to `_render_diff`: `_render_diff`
    raises a described error for a missing/redirected/undecodable receipt
    (the projection's phase-3 contract), while `preview_diff` renders an
    empty-old diff for a missing source without raising.

    Task 12 moved every outbox *route* — `outbox_screen`, `outbox_approve`,
    `outbox_reject` — onto `project_outbox`/`_render_diff`, leaving `propose`
    as the sole remaining caller. The empty-old fallback is kept because
    changing it would alter `propose`'s behaviour, which Task 12 does not own
    — **not** because the source is expected to be absent there:
    `propose_classification` refuses a missing receipt before persisting, so
    on that path the source provably existed moments earlier and only a TOCTOU
    deletion can race it.

    `test_approval_route_visibly_refuses_unfresh_source` now observes
    `approve`'s own refusal rendered through the projection's listing. Its
    missing-source case additionally produces a row-level `E-MISSING`; there
    is no row-level `E-STALE`, because staleness is a revalidation concern
    rather than a read-boundary one, so a stale row keeps `can_approve` and
    renders a normal diff."""
    _require_outbox_path(scope, proposal.path, require_leaf=True)
    reloaded = get_proposal(scope, proposal.id)
    if reloaded.id != proposal.id or reloaded.path != proposal.path:
        raise OutboxDestinationError("proposal changed since it was loaded")
    proposal = reloaded
    src_path = scope.resolve_stored(proposal.src)
    old = src_path.read_text(encoding="utf-8") if src_path.exists() else ""
    return _diff_text(proposal, old)


def project_outbox(scope: Scope) -> OutboxListing:
    """Read-only presentation projection over the outbox (design §3 Rule 3).

    Never calls `get_proposal`, `load_proposals`, or `preview_diff` — it
    revalidates each record itself, through the same three helpers the strict
    loader uses, without the strict loader's all-or-nothing re-entry.

    - Phase 1 (record read/schema): `UnreadableProposalRecord` is the family
      that already poisons `load_proposals`, so it sets `blocked` and
      withholds every row's controls listing-wide — an accurate reflection
      of the untouched strict loader's coupling, not an invented one.
    - Phase 2 (destination/registry/path validation): properties of the
      vault, not of one file. Left uncaught here, so a condition such as a
      broken registry or a redirected outbox propagates out of this function
      entirely, aborting the projection.
    - Phase 3 (diff rendering): row-local and non-poisoning. A row whose
      diff fails keeps `can_reject`, loses `can_approve`, and never sets
      `blocked`.
    """
    outbox = _require_outbox_path(scope)
    if not outbox.exists():
        return OutboxListing(rows=(), blocked=False)

    rows: list[OutboxRow] = []
    blocked = False
    for discovered in sorted(outbox.glob("*.yaml")):
        path = _require_outbox_path(scope, discovered, require_leaf=True)
        try:
            # S7: one capture supplies this row's value AND its fingerprint.
            contents = _capture_proposal_contents(scope, path)
            record = _parse_record_bytes(contents)
            proposal = _validate_record(path, record)
        except UnreadableProposalRecord as exc:
            blocked = True
            rows.append(
                OutboxRow(
                    proposal=None, diff=None, error=exc,
                    can_approve=False, can_reject=False,
                    review_sha256=None,
                )
            )
            continue
        if proposal is None:
            # A well-formed `action: delete` record — skipped exactly as
            # `load_proposals` skips it: renders nothing, blocks nothing.
            continue

        # Phase 2 — propagates. `_require_destination` is the same
        # destination-canonicalization the strict loader applies; left
        # uncaught here, its exception aborts this function entirely.
        proposal = _require_destination(scope, proposal)
        # The value and the digest are paired here, from the one capture
        # above — never from `path` again.
        review = make_review_snapshot(proposal, contents)

        try:
            diff = _render_diff(scope, review.value)
        except (
            MissingProposalSource,
            RedirectedPathError,
            OutboxDestinationError,
            ProposalSourceUnavailable,
        ) as exc:
            # An unavailable *source* says nothing about the proposal
            # record, which is still well-formed and still safely
            # rejectable — so its review fingerprint survives (design
            # §Architecture-1).
            rows.append(
                OutboxRow(
                    proposal=review.value, diff=None, error=exc,
                    can_approve=False, can_reject=True,
                    review_sha256=review.sha256,
                )
            )
            continue

        rows.append(
            OutboxRow(
                proposal=review.value, diff=diff, error=None,
                can_approve=True, can_reject=True,
                review_sha256=review.sha256,
            )
        )

    if blocked:
        # No check is weakened; the strict loader still refuses everything
        # in this entity, exactly as today. Withholding every control here
        # only stops the listing from lying about it.
        rows = [
            OutboxRow(
                row.proposal, row.diff, row.error,
                can_approve=False, can_reject=False,
                # The fingerprint goes with the controls. It grants nothing
                # by itself, but it is the value that binds an action to
                # reviewed bytes, and shipping one for an action this
                # listing has refused to offer would describe a review that
                # is not on offer.
                review_sha256=None,
            )
            for row in rows
        ]

    return OutboxListing(rows=tuple(rows), blocked=blocked)


def _require_scope(scope: Scope, proposal: Proposal) -> Proposal:
    if proposal.entity != scope.current_entity():
        raise OutboxScopeError("proposal belongs to another entity")
    return proposal


def _require_destination(scope: Scope, proposal: Proposal) -> Proposal:
    proposal = _require_scope(scope, proposal)
    _require_outbox_path(scope, proposal.path, require_leaf=True)
    try:
        source = scope.root / proposal.src
        canonical = resolve_classification_destination(
            scope,
            source,
            module=proposal.module,
            sub=proposal.sub,
            claimed_block=proposal.block,
            require_source=False,
        )
    except (DestinationError, CrossScopeError, DestinationRegistryError) as exc:
        raise OutboxDestinationError("proposal destination is invalid") from exc
    if proposal.src != canonical.src or proposal.dst != canonical.dst:
        raise OutboxDestinationError("proposal destination is non-canonical")
    return proposal


def _capture_proposal_state(scope: Scope, relative_path: str) -> PathState:
    """One no-follow capture of a proposal leaf, in the outbox's vocabulary.

    Missing and unreadable states become the outbox's existing safe
    outcomes. Integrity outcomes are deliberately *not* flattened into
    them: a leaf that turned into a symlink or a non-regular file between
    the lexical check and this capture is a tamper finding, and saying
    "could not be read" would tell the operator the wrong thing to do.

    Every reader and every action goes through here, so one translation
    table serves the projection, the strict scan and the mutation boundary
    alike — they cannot describe the same physical condition differently.
    """
    try:
        state = capture_path_state(scope.root, relative_path)
    except ReviewedPathIntegrityError as exc:
        # Re-narrowed to the outbox's own redirection type so route
        # declarations stay truthful; the operator outcome (E-TAMPER) is
        # identical either way, and the redirected target is never read.
        raise RedirectedPathError("proposal leaf is redirected") from exc
    except ReviewedPathUnavailable as exc:
        raise UnreadableProposalRecord(
            "proposal record could not be read"
        ) from exc
    if state.contents is None:
        raise UnreadableProposalRecord("proposal record no longer exists")
    return state


def _capture_proposal_contents(scope: Scope, path: Path) -> bytes:
    """The bytes of one no-follow proposal capture."""
    relative = path.relative_to(scope.root).as_posix()
    return _capture_proposal_state(scope, relative).contents


def get_proposal_review(scope: Scope, proposal_id: str) -> ReviewSnapshot[Proposal]:
    """The reviewable state of one classification proposal.

    The sequence is fixed (design §Architecture-1): capture one byte
    snapshot, parse *those* bytes, validate the value they produced, and
    fingerprint the same bytes. A second read may never supply either the
    value or the digest, so a replacement landing immediately after the
    capture cannot make the two disagree.

    The id is matched against what the strict scan found rather than joined
    to a path, so a caller-supplied id can never become a path fragment, and
    the scan's listing-wide refusals apply here exactly as they do to
    `load_proposals`.
    """
    entity = scope.current_entity()
    for review in _load_proposal_reviews(scope):
        if review.value.id == proposal_id:
            return review
    raise OutboxError(f"no pending proposal {proposal_id!r} for {entity}")


def get_proposal(scope: Scope, proposal_id: str) -> Proposal:
    """The validated value alone, for callers that will not act on it.

    No action may use this: an action needs the fingerprint that came from
    the same bytes, which only `get_proposal_review` can supply.
    """
    return get_proposal_review(scope, proposal_id).value


def _locate_proposal(scope: Scope, proposal_id: str) -> str:
    """The vault-relative leaf of one pending proposal.

    Runs the strict scan, so an action inherits every listing-wide refusal
    the loader applies. Its *value* is deliberately discarded: only the
    state captured afterwards may authorise a mutation.
    """
    review = get_proposal_review(scope, proposal_id)
    return review.value.path.relative_to(scope.root).as_posix()


def _own_reviewed_proposal(
    scope: Scope,
    proposal_id: str,
    review_sha256: object,
) -> tuple[str, PathState, Proposal]:
    """Take ownership of the exact proposal state the operator reviewed.

    This is S7's boundary, and the order is normative (design §3):

    1. locate the leaf under the scan's listing-wide refusals;
    2. capture that leaf's state **once** — this state, and no later read,
       is what the mutation will own;
    3. compare its bytes against the submitted fingerprint; and
    4. parse and validate *those same bytes* into the value the action acts on.

    The captured `PathState` is returned so the caller can hand the very
    same object to the mutation. A reread anywhere after step 2 would
    reopen the window this exists to close.
    """
    vault = scope.root
    proposal_rel = _locate_proposal(scope, proposal_id)

    proposal_state = _capture_proposal_state(scope, proposal_rel)
    require_review_match(proposal_state.contents, review_sha256)
    record = _parse_record_bytes(proposal_state.contents)
    proposal = _require_destination(scope, _to_proposal(vault / proposal_rel, record))
    return proposal_rel, proposal_state, proposal


@structured_reader(category="proposal")
def approve(scope: Scope, proposal_id: str, review_sha256: object) -> Proposal:
    """Perform the proposed move and commit it — exactly one revertible commit.
    The proposal is transaction-owned but never enters the approval commit.

    The move is bound to the reviewed proposal bytes: `review_sha256` is
    required, is compared against the state the transaction will own, and
    has no default or id-only fallback.
    """
    vault = scope.root
    proposal_rel, proposal_state, prop = _own_reviewed_proposal(
        scope, proposal_id, review_sha256
    )
    src = scope.root / prop.src
    try:
        source_bytes = _read_no_follow_bytes(src)
    except FileNotFoundError as exc:
        raise MissingProposalSource("proposal source is missing") from exc
    actual_sha256 = hashlib.sha256(source_bytes).hexdigest()
    if actual_sha256 != prop.source_sha256:
        raise StaleProposalSource("proposal source has changed")
    try:
        approved_bytes = _apply_sub(
            source_bytes.decode("utf-8"), prop.sub
        ).encode("utf-8")
    except UnicodeDecodeError as exc:
        raise OutboxDestinationError(
            "proposal source is not UTF-8 markdown"
        ) from exc

    _require_destination(scope, prop)
    _require_outbox_path(scope, prop.path, require_leaf=True)
    source_state = capture_path_state(vault, prop.src)
    if source_state.contents != source_bytes:
        raise StaleProposalSource("proposal source has changed")

    # No mid-approval re-read of the proposal remains: `proposal_state` is
    # the state whose bytes the fingerprint matched and from which `prop`
    # was parsed, and it is handed to the transaction unchanged. Replacing
    # it here with a fresh capture would mean approving bytes nobody
    # reviewed — the exact defect S7 closes.
    plan = TransactionPlan(
        message=f"outbox: approve {prop.id} ({prop.src} → {prop.dst})",
        changes=(
            PathChange(prop.src, source_state, PathState.absent()),
            PathChange(
                prop.dst,
                PathState.absent(),
                PathState.regular(approved_bytes, source_state.mode),
            ),
        ),
        commit_paths=(prop.src, prop.dst),
        owned_changes=(
            PathChange(proposal_rel, proposal_state, PathState.absent()),
        ),
    )
    try:
        execute_transaction(vault, plan)
    except GitTransactionError as exc:
        raise OutboxTransactionError(
            "classification approval transaction failed"
        ) from exc
    return prop


def reject(scope: Scope, proposal_id: str, review_sha256: object) -> Proposal:
    """Discard the proposal. No move, no commit — the proposal was never
    tracked.

    Bound to the reviewed bytes exactly as approve is. The removal is
    conditional on the captured state: reject unlinks the leaf it reviewed
    or it unlinks nothing. A rewrite, type swap, redirection or
    disappearance before the removal is a refusal, never permission to
    discard whatever took its place.
    """
    proposal_rel, proposal_state, prop = _own_reviewed_proposal(
        scope, proposal_id, review_sha256
    )
    _require_outbox_path(scope, prop.path, require_leaf=True)
    remove_path_if_unchanged(scope.root, proposal_rel, proposal_state)
    return prop
