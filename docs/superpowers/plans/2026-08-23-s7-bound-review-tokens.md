# S7 Bound Review Tokens Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure approve, reject, and registry delete can act only on the exact
proposal bytes the operator reviewed, while keeping a changed review on the
same screen and requiring a fresh confirmation.

**Architecture:** Treat S7 as an architectural safety boundary. A small shared
review-token module owns exact-byte SHA-256 snapshots and submitted-token
validation. The outbox and registry services build their validated domain
objects from the same bytes they fingerprint, and each action compares the
submitted fingerprint at its final pre-mutation boundary. FastAPI/Jinja/HTMX
carry the server-rendered token and preserve the old browser review on a
conflict while rendering the current review beside it. Services, not routes,
remain the enforcement boundary.

**Tech Stack:** Python 3.12, FastAPI, Jinja2, HTMX 2.0.4, Alpine with
alpine-morph, Pydantic v2, GitPython, pytest, `uv`.

**Spec:** `docs/superpowers/specs/2026-08-23-s7-bound-review-tokens-design.md`
— approved and normative. If this plan and the spec differ, the spec wins.

## Global Constraints

- Work only on `codex/s7-bound-review-tokens`, based on exact merged-main SHA
  `d7ad86b651c5f5f7c1adad8af94a0b767fb30a8f`.
- The established public baseline is `uv run python -m pytest -q` → 926 passed.
- Grey Matter is read-only. Record and compare its exact pre/post state; never
  clean, stash, normalize, or overwrite pre-existing private edits.
- No dependency, schema, convention, registry-value, authentication, or
  authorization change belongs in S7, with one explicit exception granted by
  **Amendment 1**: the quarantine area for consumed proposal records. That is
  a deliberate, narrowly scoped convention change — a single directory inside
  the entity's existing `outbox/`, which is already a system area excluded
  from block-mapping validation — and it is admitted because acceptance
  criterion 4 cannot be met by any deletion-based construction. It carries no
  dependency (`ctypes` is stdlib), no schema change, no registry value, and
  no authentication or authorization change. Nothing else may use this
  exception to widen S7's scope.
- Direct registry add/edit remains direct and reversible. Only registry delete
  joins the bound-review contract.
- The separately sequenced inherited work—public-document leakage checking,
  route-declaration completeness, and the named specific configuration
  outcomes—remains mandatory before live gates but is not implemented here.
- A changed proposal always refuses with exactly:
  `Proposal changed since your review. Nothing was changed.`
- Approve, reject, and registry delete use the same required-token semantics.
  Missing or malformed tokens are invalid requests, never legacy fallbacks.
- Independent review and mutation-tested verification are release gates. A
  test counts as mutation evidence only after the protection is deliberately
  broken, the test fails for the intended reason, the exact protection is
  restored, and the test passes again.
- Each task follows red → green → focused regression → commit. Do not combine
  task commits or push/open a PR without separate authorization.

---

## Preconditions

- [ ] Confirm branch, exact base ancestry, clean worktree, and approved spec:

```bash
git branch --show-current
git merge-base HEAD d7ad86b651c5f5f7c1adad8af94a0b767fb30a8f
git status --short
sed -n '1,380p' docs/superpowers/specs/2026-08-23-s7-bound-review-tokens-design.md
```

Expected branch: `codex/s7-bound-review-tokens`. Expected merge-base: the full
S7 baseline SHA above. Before product implementation, only the approved S7
design and this plan may be ahead of it.

- [ ] Re-run the public baseline before the first product-code edit:

```bash
uv run python -m pytest -q
```

Expected: 926 passed.

- [ ] Record Grey Matter's exact read-only pre-state in a unique proof
  directory. Preserve the vault path configured by the trusted local
  environment; never print it into tracked files:

```bash
export ONEOS_VAULT="${ONEOS_VAULT:?set ONEOS_VAULT to the private vault root}"
S7_PROOF="$(mktemp -d /private/tmp/oneos-s7-proof.XXXXXX)"
git -C "$ONEOS_VAULT" rev-parse HEAD > "$S7_PROOF/head.before"
git -C "$ONEOS_VAULT" status --porcelain=v2 -z --untracked-files=all > "$S7_PROOF/status.before"
git -C "$ONEOS_VAULT" diff --binary > "$S7_PROOF/worktree.before"
git -C "$ONEOS_VAULT" diff --cached --binary > "$S7_PROOF/cached.before"
```

Keep `$S7_PROOF` in the executing shell or private handoff only. Never add it
to this repository.

---

## File Map

| File | Responsibility |
|---|---|
| `app/review_tokens.py` (new) | Immutable snapshots, lowercase SHA-256, strict submitted-token validation, and changed-review exception. |
| `app/outbox.py` | Exact-byte classification reviews, projected hashes, and bound approve/reject. |
| `app/registry.py` | Exact-byte delete reviews, bound delete execution, returned success value, and fresh reference refusal. |
| `app/git_transaction.py` | One narrow conditional-removal primitive for reject using existing state ownership. |
| `app/console_errors.py` | Safe changed-review and invalid-token outcomes. |
| `app/main.py` | Required token fields, bound service calls, and read-only fresh-review fragments. |
| `templates/blocks/outbox_list.html` | Outbox token transport and card delegation. |
| `templates/blocks/outbox_card.html` (new) | One classification review and its controls. |
| `templates/blocks/delete_impact.html` | Delete impact from the fingerprinted snapshot and its token. |
| `templates/blocks/review_changed.html` (new) | Same-screen refusal, removal of the old controls, and current review. |
| `templates/blocks/review_unavailable.html` (new) | Safe no-action state, read-only check, and triage guidance. |
| `tests/test_review_tokens.py` (new) | Shared primitive tests. |
| `docs/superpowers/plans/s7_mutation_campaign.py` (new) | Runnable mutation campaign: applies each mutation, requires the named node to fail for its own reason, restores byte-for-byte, requires green. |
| `tests/test_outbox.py`, `tests/test_console_projection.py` | Classification service and projection safety. |
| `tests/test_registry.py` | Delete binding, reference recount, return value, and races. |
| `tests/test_console_routes.py`, `tests/test_console_invariants.py` | Transport, same-screen behavior, read-only refresh, declarations, and structural proof. |
| `tests/test_console_errors.py` | Exact outcome mapping and taxonomy invariants. |
| `tests/test_git_transaction.py` | Conditional reject-removal tests only if its service changes. |
| `docs/superpowers/plans/2026-08-23-s7-mutation-ledger.md` (new during verification) | Break/red/restore/green evidence and independent-review resolutions. |

---

## Task 1: Add the Shared Exact-Byte Review Contract

**Files:** create `app/review_tokens.py`, `tests/test_review_tokens.py`; modify
`app/console_errors.py`, `tests/test_console_errors.py`

- [ ] **RED:** Add tests that pin exact-byte hashing, frozen snapshots, strict
  lowercase 64-character hexadecimal tokens, different bytes under the same id,
  and exception messages that reveal no proposal bytes or private paths.

```python
def test_review_snapshot_hashes_the_exact_bytes():
    raw = b"id: same-id\nvalue: first\n"
    snap = make_review_snapshot("validated", raw)
    assert snap.contents == raw
    assert snap.sha256 == hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize("token", [None, "", "0" * 63, "G" * 64, "g" * 64, 123])
def test_submitted_review_sha256_is_strict(token):
    with pytest.raises(InvalidReviewToken):
        require_review_match(b"proposal", token)
```

- [ ] Run focused tests and observe the import/behavior failures:

```bash
uv run pytest tests/test_review_tokens.py tests/test_console_errors.py -q
```

- [ ] **GREEN:** Implement the small domain module without route or storage
knowledge:

```python
T = TypeVar("T")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class ReviewTokenError(Exception):
    pass


class InvalidReviewToken(ReviewTokenError):
    pass


class ReviewedProposalChanged(ReviewTokenError):
    pass


@dataclass(frozen=True)
class ReviewSnapshot(Generic[T]):
    value: T
    contents: bytes
    sha256: str


def make_review_snapshot(value: T, contents: bytes) -> ReviewSnapshot[T]:
    raw = bytes(contents)
    return ReviewSnapshot(value, raw, hashlib.sha256(raw).hexdigest())


def require_review_match(contents: bytes, submitted: object) -> str:
    if not isinstance(submitted, str) or _SHA256.fullmatch(submitted) is None:
        raise InvalidReviewToken("invalid review fingerprint")
    if hashlib.sha256(contents).hexdigest() != submitted:
        raise ReviewedProposalChanged("proposal changed since review")
    return submitted
```

The hash is a change detector, not a secret. Do not add signing, storage,
sessions, or a dependency.

- [ ] Map `ReviewedProposalChanged` exactly to the approved message, refusal
severity, `committed=no`, HTTP 409, and reload/review-again guidance. Map
`InvalidReviewToken` to the existing invalid-request outcome. Extend existing
map-completeness tests.

- [ ] Run focused and full public tests, then commit:

```bash
uv run pytest tests/test_review_tokens.py tests/test_console_errors.py -q
uv run python -m pytest -q
git add app/review_tokens.py app/console_errors.py tests/test_review_tokens.py tests/test_console_errors.py
git commit -m "feat: add exact-byte review token contract"
```

---

## Task 2: Produce Classification Reviews From One Byte Snapshot

**Files:** modify `app/outbox.py`, `tests/test_outbox.py`,
`tests/test_console_projection.py`

- [ ] **RED:** Add tests proving:

  - `get_proposal_review(scope, id)` returns the validated `Proposal`, exact raw
    bytes, and their SHA-256;
  - parsing and hashing use one captured byte value, even if the file is
    replaced immediately after capture;
  - `project_outbox()` puts `review_sha256` on every well-formed row whose
    proposal can safely be rejected;
  - a missing/unreadable source can disable approve while preserving reject
    and its proposal hash;
  - malformed, missing, redirected, non-regular, or cross-scope proposal
    records expose no review token and no action buttons; and
  - the same proposal id with different bytes produces a different hash.

- [ ] Run focused tests and observe failures:

```bash
uv run pytest tests/test_outbox.py tests/test_console_projection.py -q
```

- [ ] **GREEN:** Add a typed exact-byte reader around the existing S5/S6 safe
path primitives. Its fixed sequence is capture → parse those bytes → validate
that value → hash those bytes. Never parse one read and hash another.

```python
def get_proposal_review(
    scope: Scope,
    proposal_id: str,
) -> ReviewSnapshot[Proposal]:
    path = _require_outbox_path(scope, proposal_id)
    relative = path.relative_to(scope.root).as_posix()
    state = capture_path_state(scope.root, relative)
    contents = _require_regular_proposal_contents(state)
    proposal = _validate_proposal_bytes(scope, path, contents)
    return make_review_snapshot(proposal, contents)
```

Names may follow local conventions, but the single-state flow is mandatory.
Translate missing/unreadable proposal states into existing safe outcomes and
preserve integrity/tamper outcomes rather than flattening them.

- [ ] Extend the frozen projection row without breaking blocked-row creation:

```python
@dataclass(frozen=True)
class OutboxRow:
    proposal: Proposal | None
    diff: str | None
    error: Exception | None
    can_approve: bool
    can_reject: bool
    review_sha256: str | None = None
```

Build `proposal` and `review_sha256` from the same review snapshot. Preserve
the hash when source-diff construction fails but rejection remains safe.

- [ ] Keep `get_proposal(scope, id)` only for legitimate non-action callers,
implemented as `get_proposal_review(scope, proposal_id).value`. No action may
use it.

- [ ] Run focused and full tests, then commit:

```bash
uv run pytest tests/test_outbox.py tests/test_console_projection.py -q
uv run python -m pytest -q
git add app/outbox.py tests/test_outbox.py tests/test_console_projection.py
git commit -m "feat: project exact classification review snapshots"
```

---

## Task 3: Bind Classification Approve and Reject at Mutation Time

**Files:** modify `app/outbox.py`, `app/git_transaction.py`,
`tests/test_git_transaction.py`, `tests/test_outbox.py`, and
`tests/test_console_errors.py`

- [ ] **RED:** Change direct service tests first so the contract is explicit:

```python
review = get_proposal_review(scope, proposal.id)
approved = approve(scope, proposal.id, review.sha256)

review = get_proposal_review(scope, proposal.id)
rejected = reject(scope, proposal.id, review.sha256)
```

Add failures for absent and malformed hashes. There must be no default,
optional argument, or id-only compatibility path.

- [ ] Add same-id replacement tests for approve and reject. Repeat with an
action-relevant change and a byte-only change such as whitespace/key order.
Both must raise `ReviewedProposalChanged` and preserve complete vault state.

```python
review = get_proposal_review(scope, proposal.id)
rewrite_proposal_at_same_path(proposal.id)
with pytest.raises(ReviewedProposalChanged):
    approve(scope, proposal.id, review.sha256)
assert_entire_vault_state_unchanged()
```

- [ ] Add final-boundary race tests. Inject a replacement after the initial
review comparison but before the first mutation. Approve must commit nothing;
reject must not unlink the replacement. Cover disappearance, symlink,
directory, and safe non-file substitutions without blocking.

- [ ] Run focused tests and confirm the missing enforcement fails:

```bash
uv run pytest tests/test_outbox.py -q
```

- [ ] **GREEN — approve:** Change the exact public signature to
`approve(scope: Scope, proposal_id: str, review_sha256: object) -> Proposal`.
Capture proposal state once, compare its bytes, validate those bytes, retain
the existing source/policy checks, and give that same captured state to the
transaction as proposal authority.

Never call the value-only reader before capturing transaction authority, and
never replace `proposal_state` with a later reread.

- [ ] **GREEN — reject:** Use the identical capture/compare/validate sequence,
then conditionally remove only that captured regular leaf.

Add exactly one narrow public API to the transaction module:

```python
def remove_path_if_unchanged(
    vault: Path,
    relative_path: str,
    expected: PathState,
) -> None:
    """Remove one untracked regular leaf only while it matches expected."""
    try:
        _validate_transaction_path(relative_path)
    except ValueError as exc:
        raise InvalidTransactionPath("reviewed path is unsafe") from exc
    if expected.contents is None:
        raise ValueError("conditional removal requires a regular expected state")
    root = Path(os.path.abspath(os.fspath(vault)))
    change = PathChange(relative_path, expected, PathState.absent())
    with _approval_lock(root):
        _apply_state(root, change)
```

It must reuse existing lexical validation, no-follow capture, approval lock,
and state comparison. It must not create a Git commit, accept absent expected
state, or become an arbitrary-delete utility. Have the safety reviewer assess
the compare/remove timing boundary before adoption.

- [ ] Preserve existing guarantees: source fingerprint, destination policy,
one approval commit, exact revertibility, identity, scope confinement, and safe
visible errors.

- [ ] Run focused and full tests, then commit only needed files:

```bash
uv run pytest tests/test_git_transaction.py tests/test_outbox.py tests/test_console_errors.py -q
uv run python -m pytest -q
git add app/outbox.py tests/test_outbox.py tests/test_console_errors.py
git add app/git_transaction.py tests/test_git_transaction.py
git commit -m "feat: bind approve and reject to reviewed proposal bytes"
```

---

## Task 3c: Consume Proposals by Quarantine, Never by Deletion

> Added by **Amendment 1** to the approved spec, after independent review
> established that no deletion-based construction can satisfy acceptance
> criterion 4. Do not start until the amendment is approved.

**Files:** modify `app/git_transaction.py`, `app/outbox.py`,
`app/console_errors.py`, `tests/test_git_transaction.py`,
`tests/test_outbox.py`, `tests/test_console_errors.py`,
`tests/test_console_invariants.py`

`console_errors.py` and its two test files are in scope because this task
retires `E-DISCARDED` and `ConditionalRemovalCleanupError` and adds the three
replacement outcomes: the codes table, the exact class map, the chain
allowlist, the closed-family subclass walk, and the transcribed design map all
change together or the taxonomy tests fail.

- [ ] **RED:** Add tests proving that **no reviewed action unlinks a proposal**:

  - after a successful approve and a successful reject, the proposal is gone
    from the outbox listing *and* present in quarantine with its exact
    reviewed bytes;
  - an AST test that neither `approve`, `reject`, nor the proposal-consumption
    primitive reaches `os.unlink`/`Path.unlink` on a proposal path;
  - a replacement swapped in at every injectable seam — after the fingerprint
    comparison, after the internal capture, and after the final descriptor
    gate — leaves **both** files intact and refuses;
  - a quarantine destination that is already occupied is refused, never
    overwritten, with the occupant's bytes intact; and
  - with the atomic no-overwrite move made to report itself unavailable, every
    reviewed action refuses and changes nothing — no fallback path exists.

- [ ] **GREEN — the atomic move.** Add the one primitive everything else
rests on. It must be a **single kernel operation** that moves the file and
fails if the destination exists:

```python
def _move_no_replace(from_fd: int, from_name: str, to_fd: int, to_name: str) -> None:
    """Atomically move a leaf, refusing if the destination exists.

    Linux: renameat2(RENAME_NOREPLACE). macOS: renameatx_np(RENAME_EXCL).
    Both via ctypes, so no dependency is added. Ordinary `rename` is NEVER
    an acceptable fallback: it silently overwrites.
    """
```

Reserving a name with `O_EXCL` and renaming onto it later is **two**
operations and does not compose into one guarantee — another writer can take
the reservation in between, and the rename destroys what took it. Do not
reintroduce that shape.

Availability depends on the kernel *and* the filesystem, so learn it by
attempting the real operation and translating `ENOSYS`/`EINVAL`/`EOPNOTSUPP`
into the unsupported outcome. **Fail closed**: refuse the action, change
nothing, never degrade to a destructive path.

`EPERM` is not in that set, because it is ambiguous: a seccomp filter that
blocks the syscall answers `EPERM`, and so does a refusal about these
particular files. It is disambiguated by calling the mover with invalid
descriptors and empty names — a blocked syscall still answers `EPERM`, while
a reachable one gets as far as argument validation and answers a non-`EPERM`
argument error, such as `EBADF` or `ENOENT`. Which one is not fixed: the same
call returns `EBADF` on some platforms and `ENOENT` on macOS. The
classification turns on the errno *not* being `EPERM`, never on a particular
alternative.

No classification may write anywhere in the vault. A refusal path that
created even a temporary file would be vault state changed outside the Git
transaction, which is the invariant S5 and S7 exist to hold; an in-vault
probe was written and withdrawn for exactly this reason, and the regression
that caught it guards any create, unlink or rename under the vault during
classification.

- [ ] **GREEN — the consumption primitive.** Add one narrow public API beside
`remove_path_if_unchanged`, which it replaces for proposal consumption:

```python
def quarantine_path_if_unchanged(
    vault: Path, relative_path: str, expected: PathState, quarantine: str
) -> str:
    """Move one reviewed regular leaf into quarantine while it matches."""
```

Its sequence is: atomic no-overwrite move of the leaf into quarantine; open
the moved file `O_NOFOLLOW` and verify identity and contents through the
**descriptor**; on mismatch, atomic no-overwrite move back under its own name
and refuse. No step may unlink a proposal record.

- [ ] **Quarantine location:** a `.consumed/` directory inside the entity's
existing `outbox/`. Same filesystem, so the move is atomic; inside the area
already excluded from block-mapping validation, so convention impact is
minimal; and outside the `*.yaml` top-level glob, so quarantined records never
appear in a listing and no reviewed action can reach them.

- [ ] **The quarantine directory is resolved as safely as the outbox itself.**
It is a path the action writes into, so it gets the same treatment
`_require_outbox_path` already gives the outbox, and for the same reasons:

  - the lexical `<entity>/outbox/.consumed` is checked with `is_symlink()`
    **before** any `resolve()`, so a redirected quarantine raises the
    redirection outcome (E-TAMPER) rather than a scope outcome — the C2
    ordering rule this repository already enforces;
  - the resolved path must equal the lexical path, and must be a real
    directory, never a symlink, never a non-directory;
  - creation is `mkdir` with an explicit mode, and a `FileExistsError` on
    creation is a redirection refusal, not a silent success; and
  - it is confined to the bound entity, so no action can quarantine across
    scopes.

  Add tests for each condition, mirroring the existing outbox-redirect tests.

- [ ] **The move uses checked directory descriptors, never a resolved
`Path`.** `_require_outbox_path` validates *lexical* paths; it hands back no
descriptor, and `_move_no_replace` needs two. Re-opening a validated path by
name would reintroduce exactly the lookup-after-check gap this amendment
removes — one directory level up, where a swapped directory redirects every
move inside it.

  So, after the lexical checks pass:

  - open `<entity>/outbox` and `<entity>/outbox/.consumed` through
    `_open_checked_directory`, which already `lstat`s, refuses a symlink or
    non-directory, opens with `O_DIRECTORY | O_NOFOLLOW`, and re-verifies the
    descriptor by `fstat` — the same primitive `capture_path_state` and
    `_apply_state` already walk with;
  - open `.consumed` **relative to the outbox descriptor** (`dir_fd=`), so
    the parent cannot be swapped between the two opens; and
  - pass those descriptors to every `_move_no_replace` call — quarantine and
    restore alike. No step may re-derive a directory from a `Path` after
    validation.

- [ ] **RED for the directory swap:** replace `<entity>/outbox/.consumed`
with a symlink to a directory outside the entity in the window between its
lexical validation and its descriptor being opened. Assert the action refuses
with the redirection outcome, that the proposal is untouched, and — the point
of the test — that **nothing is written or moved outside the entity**. Repeat
with the `outbox` directory itself swapped, since a redirect there
redirects `.consumed` with it.

- [ ] **Rollback: a quarantined record must come back if the action does
not complete.** Approve consumes the proposal *and* commits a move; if the
transaction fails or is rolled back after the record was quarantined, leaving
it in quarantine would discard a proposal for an approval that never
happened.

  Specify and test:

  - the quarantine move happens inside the transaction's owned-change
    handling, so the existing rollback path restores it under its original
    name with the atomic no-overwrite move;
  - if restoration is blocked because the original name is now occupied, the
    outcome is the indeterminate recovery one — both files preserved, nothing
    deleted to tidy up;
  - a rolled-back approval leaves the proposal pending and actionable again,
    with a fingerprint that still matches its unchanged bytes; and
  - reject has no commit to roll back, so its only rollback is the mismatch
    restoration already specified.

- [ ] **Scope the change precisely.** Quarantine replaces *proposal
consumption* only. `_apply_state`'s removal branch still serves approve's
source→destination move, whose removal is the intended, committed, Git-revertible
effect. Do not quarantine the source receipt.

- [ ] Approve's `owned_changes` proposal consumption and reject both route
through the new primitive. Preserve one approval commit, exact revertibility,
identity, scope confinement, and safe visible errors.

- [ ] **Replace `E-DISCARDED`.** It says the proposal "is already gone",
which quarantine makes false. Retire it and its `ConditionalRemovalCleanupError`
and add three truthful outcomes, each with its own regression:

| Condition | Must say | Shape |
|---|---|---|
| consumed, then cleanup failed | the action took effect; the record is quarantined and recoverable; do not retry | committed tier, `committed=yes`, `retry=stop` |
| mismatch, restoration blocked | both files preserved, original name occupied, state indeterminate — never "nothing was changed" | recovery tier, `committed=unknown`, `retry=stop` |
| atomic move unavailable here | nothing was changed and no action is possible on this vault until resolved | refusal, `committed=no` |

- [ ] Add a regression for the restoration-blocked path specifically: occupy
the original name while the record is in quarantine, and assert that **both**
files survive, that nothing is deleted to tidy up, and that the outcome is the
indeterminate one rather than a refusal claiming no change.

- [ ] Retire `remove_path_if_unchanged` if nothing else uses it, rather than
leaving a destructive primitive available to a future caller.

- [ ] Run focused and full tests, repeat the Task 3 mutation set against the
new construction, then commit:

```bash
uv run pytest tests/test_git_transaction.py tests/test_outbox.py \
  tests/test_console_routes.py tests/test_console_errors.py \
  tests/test_console_invariants.py -q
uv run python -m pytest -q
git add app/git_transaction.py app/outbox.py app/console_errors.py
git add tests/test_git_transaction.py tests/test_outbox.py
git add tests/test_console_errors.py tests/test_console_invariants.py
git commit -m "feat: consume reviewed proposals by quarantine"
```

**Known consequences, deliberately accepted:**

1. Quarantined records accumulate until the separately sequenced reclaim
   exists. S7 stops destroying; it does not own the lifecycle.
2. On a kernel or filesystem without an atomic no-overwrite move, approve,
   reject and registry delete refuse outright. That is the intended trade:
   refusing to act is strictly better than acting destructively.

**Blocking verification, not a deferred one:** the Linux
`renameat2(RENAME_NOREPLACE)` **success** path must be exercised on real
Linux — both that it moves the file and that it refuses an occupied
destination — before S7 can be declared complete. It is unverified in the
session that wrote this task, which is macOS, where only
`renameatx_np(RENAME_EXCL)` was confirmed. This is a completion condition, not
a pre-live-gate item: an untested success path on a supported platform could
mean reviewed actions refuse everywhere, or worse, do not.

---

## Task 4: Bind Registry Delete and Keep the Live Reference Gate

**Files:** modify `app/registry.py`, `tests/test_registry.py`,
`tests/test_console_errors.py`

- [ ] **RED:** Add `get_delete_review(scope, id)` tests equivalent to the
classification snapshot tests. Displayed `kind`, `value`, and saved impact must
come from `review.value`, never a second live report.

- [ ] Require a hash in direct execution tests and add:

  - same-id proposal replacement → changed-review refusal, registry unchanged,
    replacement preserved;
  - missing/malformed hash → invalid request, no mutation;
  - replacement after comparison but before transaction ownership → refusal,
    no commit;
  - matching proposal plus a new live reference → existing reference refusal;
  - matching proposal without references → one revertible commit; and
  - return value equals the validated proposal whose bytes matched, so the
    route needs no earlier unbound read for success copy.

- [ ] Observe focused failures:

```bash
uv run pytest tests/test_registry.py tests/test_console_errors.py -q
```

- [ ] **GREEN:** Change the exact public signature to
`execute_delete(scope: Scope, proposal_id: str, review_sha256: object) ->
DeleteProposal`. Capture the delete-proposal state once; compare, parse, and
validate its bytes; repeat the live reference count; build the existing
transaction with that same state as proposal-consumption authority; execute;
then return the bound `DeleteProposal`.

Registry delete adopts Task 3c's quarantine from the start: the delete
proposal is consumed by the same non-destructive move, never unlinked. The
registry *value* removal remains a normal committed change.

The hash does not authorize deletion by itself. Scope, kind/value existence,
current registry state, fresh references, and transaction-owned state remain
independent gates.

- [ ] Implement `get_delete_review()` through the same byte parser used by
execution. Any retained value-only reader delegates to it and is never used by
an action.

- [ ] Run focused and full tests, then commit:

```bash
uv run pytest tests/test_registry.py tests/test_console_errors.py -q
uv run python -m pytest -q
git add app/registry.py tests/test_registry.py tests/test_console_errors.py
git commit -m "feat: bind registry delete to reviewed proposal bytes"
```

---

## Task 5: Carry Tokens Through HTMX and Reconfirm on the Same Screen

**Files:** modify `app/main.py`, `templates/blocks/outbox_list.html`,
`templates/blocks/delete_impact.html`, `tests/test_console_routes.py`, and
`tests/test_console_invariants.py`; create `templates/blocks/outbox_card.html`,
`templates/blocks/review_changed.html`, and
`templates/blocks/review_unavailable.html`

- [ ] **RED — transport:** Require both server-rendered values on every action:

```html
hx-vals='{{ {"id": row.proposal.id,
             "review_sha256": row.review_sha256}|tojson }}'
```

Apply the same `tojson` rule to approve, reject, and registry delete. Tests
must fail hand-built JSON, missing hashes, hashes from another row, and action
buttons on rows without hashes.

- [ ] **RED — service plumbing:** Prove each route passes the submitted hash
unchanged. Delete success copy must use the `DeleteProposal` returned by
`execute_delete`; fail if the route calls `get_delete_proposal()` first.

- [ ] **RED — same-screen change:** For approve, reject, and delete, render a
review, replace its stored proposal under the same id, and submit the old
controls. Assert the response:

  - carries the exact approved message. The **status is 200**, not
    E-REVIEW's 409: S6 states normatively that a fragment refusal renders at
    200 (`status_for`), and the S7 spec preserves S6's outcome behaviour
    (acceptance criterion 7). A full-page render still uses 409. This is not
    a client workaround — `templates/_head.html` configures HTMX `[45]..`
    with `swap:true`, so a 4xx would swap perfectly well; the reason is the
    taxonomy's rule;
  - leaves the old review visible;
  - empties its old controls through a hash-specific HTMX out-of-band
    target, leaving a "no longer actionable" label and inventing no
    replacement buttons — which controls the stale card offered is a property
    of bytes the server never saw;
  - appends a separately identified current review built from current
    server-validated bytes;
  - gives only the current review a fresh hash and any controls at all;
  - changes no proposal, destination, registry, or Git state; and
  - repeats correctly if the current proposal changes again.

- [ ] **RED — refresh/unavailable:** Add read-only fresh-review routes. A normal
check returns the current review and token. Missing, malformed, redirected,
cross-scope, or non-file proposals render the existing safe outcome and no
controls. `Check again` performs only a read. A missing classification proposal
may link safely to the existing triage screen; it never reconstructs, moves, or
guesses a proposal/source path.

- [ ] Run focused tests and observe failures:

```bash
uv run pytest tests/test_console_routes.py tests/test_console_invariants.py -q
```

- [ ] **GREEN — routes:** Require
`review_sha256: Annotated[str, Form()]` with no default on each action route and
pass it unchanged to the corresponding three-argument service.

Mirror this contract for reject and registry delete. Add decorated, declared
GET fragment routes for read-only current review. Name all reachable typed
failures in their S6 route declarations; never broaden catches to `Exception`.

- [ ] **GREEN — HTMX behavior:** Keep the existing list target for success. On
`ReviewedProposalChanged`, use `HX-Retarget` and `HX-Reswap` to append beside
the hash-specific old card; include an out-of-band replacement that disables
the old controls. A practical DOM contract is:

```text
review-card-<proposal-id>-<review-sha256>
review-controls-<proposal-id>-<review-sha256>
```

Use the same pattern for classification and delete. Escape attributes normally
and serialize HTMX values only with `tojson`.

- [ ] Do not imply the old review came back from the server. It remains browser
presentation evidence. The appended card is the server-validated current state
and the only source of active controls.

- [ ] Run focused tests, inspect synthetic rendered fragments, then run the
full public suite:

```bash
uv run pytest tests/test_console_routes.py tests/test_console_invariants.py -q
uv run python -m pytest -q
```

- [ ] Commit:

```bash
git add app/main.py templates/blocks/outbox_list.html templates/blocks/outbox_card.html
git add templates/blocks/delete_impact.html templates/blocks/review_changed.html
git add templates/blocks/review_unavailable.html
git add tests/test_console_routes.py tests/test_console_invariants.py
git commit -m "feat: require same-screen reconfirmation after review changes"
```

---

## Task 6: Prove Complete No-Mutation Outcomes and Mutation Resistance

**Files:** modify existing S7 tests only; create
`docs/superpowers/plans/2026-08-23-s7-mutation-ledger.md`

- [ ] Add or confirm a parameterized matrix for all three actions:

| State at action boundary | Required result |
|---|---|
| exact reviewed bytes | existing successful behavior |
| same id, meaningful byte change | changed-review refusal |
| same id, byte-only difference | changed-review refusal |
| missing or malformed token | invalid request |
| missing proposal | existing safe missing outcome |
| malformed proposal | existing safe unreadable outcome |
| symlink, redirect, or cross-scope | existing integrity/refusal outcome |
| regular file replaced before mutation | changed/state-conflict refusal |
| non-regular replacement | integrity/refusal outcome |

For every refusal, compare proposal bytes/type, source/destination, registry
bytes, references, Git HEAD, index, tracked diff, untracked paths, and commit
count. “No mutation” must not mean only “no new commit.”

- [ ] Add AST/template tests proving:

  - all three service and route signatures require `review_sha256`;
  - all three routes pass it;
  - all three action templates carry it through `tojson`;
  - action routes never call value-only proposal readers;
  - delete success uses the service return;
  - no error row has active controls; and
  - every new route has a truthful `@console_route` declaration.

These checks cover only S7's changed surface. Do not expand them into the
separately sequenced global route-declaration audit.

- [ ] Run all S7-focused tests together:

```bash
uv run pytest tests/test_review_tokens.py tests/test_outbox.py \
  tests/test_console_projection.py tests/test_registry.py \
  tests/test_console_errors.py tests/test_console_routes.py \
  tests/test_console_invariants.py tests/test_git_transaction.py -q
```

- [ ] Deliberately make each mutation below, one at a time, without committing
the broken state:

  1. bypass `require_review_match()` in approve;
  2. bypass it in reject;
  3. bypass it in registry delete;
  4. hash a second read instead of the parsed/rendered bytes;
  5. replace approve/delete transaction authority with a later reread;
  6. make reject unlink by id without comparing captured state;
  7. omit `review_sha256` from one HTMX action; and
  8. make delete success pre-read an unbound proposal.

For each, record the exact temporary diff, named failing test, intended
assertion, exact restoration, and green rerun in the mutation ledger. Restore
the specific lines with `apply_patch`; never use destructive Git cleanup that
could erase unrelated work.

- [ ] Run the full public suite after every restored mutation group:

```bash
uv run python -m pytest -q
```

- [ ] Commit verification additions and the ledger:

```bash
git add tests docs/superpowers/plans/2026-08-23-s7-mutation-ledger.md
git commit -m "test: prove S7 review binding resists mutation"
```

---

## Task 7: Independent Review, Private Read-Only Gates, and Handoff

**Files:** modify S7 files only for accepted findings; update the mutation
ledger with review evidence

- [ ] Invoke `superpowers:requesting-code-review` and obtain at least two
independent reviews with different assignments:

  1. **Safety/transaction reviewer:** single-read byte lineage, service
     enforcement, compare-to-mutation timing, conditional reject removal,
     live-reference recount, and no-mutation proof.
  2. **Route/operator/scope reviewer:** same-screen old/current behavior,
     removal of stale controls, HTMX token transport, safe errors, read-only
     checking, declarations, and exclusion of inherited/non-S7 work.

Neither reviewer may be the implementer. Each reads the approved spec, this
plan, and the complete diff from the exact baseline.

- [ ] Resolve every finding with evidence. For any code change, write or name
the failing regression first, make the smallest correction, run focused tests,
and repeat the affected mutation proof. Record accepted and rejected findings
with concrete reasons.

- [ ] Invoke `superpowers:verification-before-completion` and run fresh public
verification from the final tree:

```bash
git status --short
git diff --check d7ad86b651c5f5f7c1adad8af94a0b767fb30a8f..HEAD
uv run python -m pytest -q
```

- [ ] Run existing private gates read-only:

```bash
cd "$ONEOS_VAULT/_system/scripts"
python3 -m unittest discover
cd "$ONEOS_VAULT"
python3 _system/scripts/check_v2.py .
```

Expected inherited private baseline: 37 tests pass; `check_v2` reports zero
errors and zero warnings. If it differs, investigate rather than changing Grey
Matter.

- [ ] Prove Grey Matter remained byte-for-byte untouched:

```bash
git -C "$ONEOS_VAULT" rev-parse HEAD > "$S7_PROOF/head.after"
git -C "$ONEOS_VAULT" status --porcelain=v2 -z --untracked-files=all > "$S7_PROOF/status.after"
git -C "$ONEOS_VAULT" diff --binary > "$S7_PROOF/worktree.after"
git -C "$ONEOS_VAULT" diff --cached --binary > "$S7_PROOF/cached.after"
cmp "$S7_PROOF/head.before" "$S7_PROOF/head.after"
cmp "$S7_PROOF/status.before" "$S7_PROOF/status.after"
cmp "$S7_PROOF/worktree.before" "$S7_PROOF/worktree.after"
cmp "$S7_PROOF/cached.before" "$S7_PROOF/cached.after"
```

- [ ] Confirm S7-only scope and no instance-specific values:

```bash
git diff --stat d7ad86b651c5f5f7c1adad8af94a0b767fb30a8f..HEAD
git log --oneline d7ad86b651c5f5f7c1adad8af94a0b767fb30a8f..HEAD
git status --short --branch
```

- [ ] Prepare a handoff stating repository root, baseline, branch, worktree,
HEAD, live PR/merge state, public/private gates, mutation evidence, reviewer
outcomes, and Grey Matter preservation. Do not push, open a PR, merge, delete
branches, or remove worktrees without separate authorization.

---

## Completion Conditions

S7 is complete only when:

- approve, reject, and registry delete refuse an id-identical byte replacement
  at both service and final pre-mutation boundaries;
- every actionable review is parsed, validated, and hashed from one exact byte
  snapshot;
- the stale review stays visible with its controls removed and labelled
  rather than replaced by invented disabled ones, the current review appears
  on the same screen, and only its fresh controls can act;
- registry delete independently repeats the live-reference check;
- all refusals prove complete state non-mutation;
- deliberate mutations produce the intended red tests and exact restoration
  returns them to green;
- two independent reviews are resolved;
- final public and private gates pass;
- Grey Matter's exact pre-existing state is preserved; and
- the inherited pre-live-gate items remain separately sequenced rather than
  silently claimed complete; and
- the atomic no-overwrite move has been exercised on real Linux
  (`renameat2(RENAME_NOREPLACE)`) and on macOS
  (`renameatx_np(RENAME_EXCL)`), covering both the success path and the
  occupied-destination refusal *(Amendment 1)*.
