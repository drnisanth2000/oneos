# S7 Stage 2 Action Receipts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make approval and registry deletion commit durable action receipts before consuming proposal records, so a crash or failed quarantine cannot offer the same proposal id for a second action.

**Architecture:** A focused `app/action_receipts.py` module owns the closed receipt schema and read-only Git-`HEAD` authority. Approval and registry deletion add a receipt as an ordinary tracked `PathChange`; the transaction commits action and receipt together, then quarantines the proposal as its final mutation under the same lock. Projection resolves receipts before opening proposal leaves and renders a receipt-backed, controls-withheld card; malformed receipts fail closed per id and store-level corruption fails closed per entity.

**Tech Stack:** Python 3.12, stdlib `subprocess`, PyYAML, FastAPI, Jinja2, HTMX 2.0.4, pytest, `uv`, Git.

**Spec:** `docs/superpowers/specs/2026-08-23-s7-bound-review-tokens-design.md` — Amendment 3 Stage 2 is approved and normative. If this plan and the spec differ, the spec wins.

## Global Constraints

- Work only in the existing linked worktree on `codex/s7-bound-review-tokens`, whose Stage 2 design checkpoint is `519066c` and whose original merged-main baseline is `d7ad86b651c5f5f7c1adad8af94a0b767fb30a8f` (926 public tests at baseline).
- Re-run the current public suite before the first executable edit; the last executable checkpoint reported 1285 passing, but the fresh run is authority.
- Grey Matter remains read-only. Do not access it unless `ONEOS_VAULT` is set by the trusted local environment; never write, normalize, stash, clean, or infer preservation from absence of access.
- No dependency, authentication, authorization, registry schema, curated-vault schema, or request-path LLM change belongs here. Stage 2's only new persisted schema is the approved entity-local receipt at `<entity>/outbox/.receipts/<proposal-id>.yaml`.
- `.receipts/` is tracked; `.consumed/` remains untracked. Both accumulate. Receipt and quarantine cleanup remain separately designed destructive work.
- Receipt authority is the current Git `HEAD`, never the working tree. A valid matching receipt spends an id regardless of action kind or pending-record bytes. The receipt digest is audit-only and never influences eligibility.
- An absent `.receipts/` tree is a valid empty store; an absent matching receipt means unspent. A malformed matching receipt disables only that id. An unreadable `HEAD` or non-tree receipt root blocks the entity.
- Approval and registry deletion put the receipt in `changes` and the exact `commit_paths`, never `owned_changes`; action and receipt must be one commit. Reject creates no receipt but performs the same `HEAD` check under its standalone lock.
- After a successful commit, proposal quarantine is the final mutation under the same approval lock. A consumption failure preserves the commit and receipt, performs no rename-back, and resolves to exact `E-APPLIED` copy. A fully verified consumption followed only by cleanup failure remains `E-COMMITTED`; reject's equivalent remains `E-QUARANTINED`.
- Receipt-backed cards never open or parse the pending proposal. They carry no digest, mutating attribute, reconfirmation, or `Check again`; ids use only a scan-validated proposal id plus a server-minted issuance.
- `E-RETAINED`, `E-STRANDED`, `diagnose_quarantined_record`, their executable tests, and mutation rows M18/M19 retire only in the same task that makes quarantine-last real and proves the old producers unreachable.
- Independent review and mutation-tested verification remain mandatory. An implementer never reviews their own task. No push, PR, merge, branch deletion, or worktree removal is authorized by this plan.
- Linux `renameat2(RENAME_NOREPLACE)` success and occupied-destination behavior, private read-only gates, and the Grey Matter preservation proof remain external completion conditions; Stage 2 cannot make S7 complete without them.

---

## Collaboration and ownership

All implementation work is **sequential in this shared worktree**. Do not let two implementers edit concurrently. Each task starts only after the prior task is committed and independently reviewed.

| Task | Implementer | Reviewer |
|---|---|---|
| 1. Receipt domain and `HEAD` reader | delegated coding agent A | fresh review agent, not A |
| 2. Taxonomy sequencing checkpoint | no code task; receipt mappings closed in Task 1 and `E-APPLIED` lands with its Task 3 producer | primary plan review |
| 3. Quarantine-last transaction core | primary Codex agent | fresh transaction-focused review agent |
| 4. Approval/reject/delete service integration | primary Codex agent | fresh service-boundary review agent |
| 5. Receipt-first projection and UI | delegated coding agent C | fresh presentation/security review agent, not C |
| 6. Offline validator and public audit gate | delegated coding agent D | fresh audit-focused review agent, not D |
| 7. Stage 2 mutation campaign | primary Codex agent | two independent whole-checkpoint reviewers |
| 8. Final public/private/platform verification | primary Codex agent at trusted boundaries | evidence-only review |

The primary agent owns the implementation plan and transaction-sensitive code; delegated agents receive only the bounded task they implement. Agent A or C may review the other's work, but never their own. A review finding returns to that task's implementer before the next task begins.

## File Map

| File | Responsibility |
|---|---|
| `app/action_receipts.py` (new) | Closed receipt schema, deterministic bytes, Git-`HEAD` lookup/batching, per-id/store failures, offline full-store validation, and spent-result type. |
| `app/review_tokens.py` | Export strict lowercase SHA-256 shape validation for receipt parsing without comparing it to pending bytes. |
| `app/git_transaction.py` | Precondition refusal result, receipt-parent creation, quarantine-last ordering, post-commit consumption outcome, and Stage 1 retirement. |
| `app/outbox.py` | Receipt-first projection, approval receipt creation, reject locked check, and spent action result. |
| `app/registry.py` | Receipt-first delete review, registry-delete receipt creation, locked receipt check, and spent action result. |
| `app/console_errors.py` | `E-APPLIED`, `E-RECEIPT`, receipt-store mappings, and retirement of `E-RETAINED`/`E-STRANDED`. |
| `app/main.py` | Tagged spent-result handling, same-screen `E-APPLIED`, receipt-backed fragments, and receipt row composition. |
| `templates/blocks/action_receipt_card.html` (new) | Shared controls-withheld spent/malformed receipt card. |
| `templates/blocks/outbox_list.html` | Render proposal, spent receipt, malformed receipt, or unreadable row without parsing spent records. |
| `templates/_head.html` | No behavior change; tests pin `[45]..` swapping because `E-APPLIED` is HTTP 500. |
| `tests/test_action_receipts.py` (new) | Schema, HEAD authority, absence semantics, batching, root corruption, filename binding, and validator tests. |
| `tests/test_git_transaction.py` | Quarantine-last order, pre/post-commit failures, exact commit, crash boundary, cleanup classification, and retirement evidence. |
| `tests/test_outbox.py`, `tests/test_console_projection.py` | Approval/reject receipt gates and receipt-first no-parse projection. |
| `tests/test_registry.py` | Delete receipt gate, same-commit receipt, live recount ordering, and receipt-first delete review. |
| `tests/test_console_errors.py`, `tests/test_console_invariants.py` | Exact taxonomy, catch/producer completeness, orphan-outcome guard, and structural transport rules. |
| `tests/test_console_routes.py` | Spent cards, malformed cards, same-screen 500 swap, hostile ids, links, and no controls/digests. |
| `tests/test_gate3_audit.py`, `tests/test_public_repo_audit.py` | Read-only accumulated receipt-store gate and no instance-specific leakage. |
| `docs/superpowers/plans/s7_mutation_campaign.py` | Stage 2 mutation rows, deliberate retirement evidence, RED/GREEN restoration, and full-suite close. |
| `docs/superpowers/plans/2026-08-23-s7-mutation-ledger.md` | Exact OLD/NEW substitutions, named nodes/diagnostics, retired rows, and authoritative campaign output. |

## Preconditions

- [ ] **Step 1: Prove the checkout and checkpoint**

```bash
git branch --show-current
git rev-parse HEAD
git merge-base HEAD d7ad86b651c5f5f7c1adad8af94a0b767fb30a8f
git status --porcelain
git diff --check
```

Expected: branch `codex/s7-bound-review-tokens`, HEAD `519066c` plus only this plan commit if it has already been recorded, merge-base `d7ad86b651c5f5f7c1adad8af94a0b767fb30a8f`, clean worktree.

- [ ] **Step 2: Re-read the approved Stage 2 design and mandatory repository instructions**

```bash
sed -n '1,260p' AGENTS.md
sed -n '626,890p' docs/superpowers/specs/2026-08-23-s7-bound-review-tokens-design.md
sed -n '975,1220p' docs/superpowers/specs/2026-08-23-s7-bound-review-tokens-design.md
```

- [ ] **Step 3: Establish the current public baseline**

Run:

```bash
uv run python -m pytest -q
```

Expected: the fresh count is recorded in the task ledger; last known executable count was 1285. Stop on any failure.

- [ ] **Step 4: Record private-gate availability without touching Grey Matter**

```bash
test -n "${ONEOS_VAULT:-}" && printf 'ONEOS_VAULT=set\n' || printf 'ONEOS_VAULT=unset\n'
```

If unset, mark private gates and preservation proof blocked and continue only with public/synthetic work. If set, the trusted local operator—not a delegated agent—records the exact pre-state before any read-only private gate.

---

### Task 1: Add the receipt domain and batched `HEAD` reader

**Owner:** delegated coding agent A.

**Files:**
- Create: `app/action_receipts.py`
- Create: `tests/test_action_receipts.py`
- Modify: `app/review_tokens.py`
- Modify: `tests/test_review_tokens.py`
- Modify: `app/console_errors.py`
- Modify: `tests/test_console_errors.py`
- Modify: `tests/test_console_invariants.py`

**Interfaces:**
- Consumes: `proposal_identity.require_proposal_id`, `proposal_identity.require_proposal_identity`, strict SHA-256 validation from `app.review_tokens`, Git CLI, PyYAML.
- Produces:

```python
ActionKind = Literal["approval", "registry deletion"]

@dataclass(frozen=True)
class ActionReceipt:
    version: int
    proposal_id: str
    review_sha256: str
    action_kind: ActionKind

@dataclass(frozen=True)
class ReceiptResolution:
    proposal_id: str
    receipt: ActionReceipt | None
    error: InvalidActionReceipt | None

@dataclass(frozen=True)
class SpentAction:
    receipt: ActionReceipt
```

The exception hierarchy is `ReceiptError(Exception)` with concrete
`InvalidActionReceipt`, `ReceiptStoreIntegrityError`, and
`ReceiptStoreUnavailable` subclasses. The exact callable signatures are:

- `require_review_sha256(value: object) -> str`
- `receipt_relative_path(entity: str, proposal_id: str) -> str`
- `make_action_receipt(proposal_id: str, review_sha256: object, action_kind: ActionKind) -> ActionReceipt`
- `render_action_receipt(receipt: ActionReceipt) -> bytes`
- `resolve_head_receipts(vault: Path, entity: str, proposal_ids: Iterable[str]) -> dict[str, ReceiptResolution]`
- `resolve_head_receipt(vault: Path, entity: str, proposal_id: str) -> ReceiptResolution`
- `validate_head_receipt_store(vault: Path, entity: str) -> tuple[ActionReceipt, ...]`

`ReceiptError` is an abstract family and is never raised directly. `InvalidActionReceipt` is per-id. `ReceiptStoreIntegrityError` means `.receipts` exists in `HEAD` as a non-tree. `ReceiptStoreUnavailable` means the current `HEAD` or requested objects cannot be read reliably.

- [ ] **Step 1: Write schema and deterministic-byte RED tests**

Add tests that require the exact four-key schema and stable bytes:

```python
def test_action_receipt_round_trips_the_closed_schema():
    receipt = make_action_receipt(PROPOSAL_ID, "a" * 64, "approval")
    raw = render_action_receipt(receipt)
    assert yaml.safe_load(raw) == {
        "version": 1,
        "proposal_id": PROPOSAL_ID,
        "review_sha256": "a" * 64,
        "action_kind": "approval",
    }
    assert render_action_receipt(receipt) == raw


@pytest.mark.parametrize("extra", [
    {"created": "now"}, {"entity": "example"}, {"target": "a/b"}
])
def test_receipt_parser_refuses_fields_outside_the_closed_schema(extra):
    raw = yaml.safe_dump({
        "version": 1, "proposal_id": PROPOSAL_ID,
        "review_sha256": "a" * 64, "action_kind": "approval", **extra,
    }).encode()
    with pytest.raises(InvalidActionReceipt):
        parse_action_receipt(Path(f"{PROPOSAL_ID}.yaml"), raw)
```

Also parameterize invalid version, id, digest, action kind, non-mapping YAML, duplicate/extra/missing fields, non-UTF-8, and filename/content mismatch. Assert receipt parsing never compares `review_sha256` with pending proposal bytes.

- [ ] **Step 2: Run the focused RED selection**

```bash
uv run python -m pytest tests/test_action_receipts.py tests/test_review_tokens.py -q
```

Expected: import failures for `app.action_receipts` and `require_review_sha256`.

- [ ] **Step 3: Export strict digest-shape validation**

Refactor `require_review_match` without changing its outcomes:

```python
def require_review_sha256(value: object) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise InvalidReviewToken("invalid review fingerprint")
    return value


def require_review_match(contents: bytes, submitted: object) -> str:
    raw = _require_bytes(contents)
    token = require_review_sha256(submitted)
    if hashlib.sha256(raw).hexdigest() != token:
        raise ReviewedProposalChanged("proposal changed since review")
    return token
```

In receipt parsing, translate `InvalidReviewToken` into `InvalidActionReceipt`; never let a malformed stored receipt look like a malformed operator form.

- [ ] **Step 4: Implement the closed receipt value and bytes**

Use `yaml.safe_dump(record, sort_keys=False, allow_unicode=False).encode("utf-8")`, inserting fields in the exact order `version`, `proposal_id`, `review_sha256`, `action_kind`. Accept only `version == 1`, exact `set(record)`, canonical id, strict digest, and the two literal action kinds. Call `require_proposal_identity(path, record["proposal_id"])` before returning.

- [ ] **Step 5: Write `HEAD` authority and batching RED tests**

Build synthetic Git repositories and assert:

```python
def test_worktree_deletion_does_not_hide_a_head_receipt(repo):
    committed = commit_receipt(repo, action_kind="approval")
    committed.path.unlink()
    result = resolve_head_receipt(repo, ENTITY, committed.proposal_id)
    assert result.receipt == committed.receipt


def test_absent_head_tree_is_a_valid_empty_store(repo):
    assert resolve_head_receipt(repo, ENTITY, PROPOSAL_ID) == ReceiptResolution(
        PROPOSAL_ID, None, None
    )


def test_non_tree_receipt_root_is_entity_wide_integrity_failure(repo):
    commit_blob_at_receipt_root(repo)
    with pytest.raises(ReceiptStoreIntegrityError):
        resolve_head_receipts(repo, ENTITY, [PROPOSAL_ID])
```

Spy on `subprocess.run`: a lookup of 20 ids must use one root-type command and one `git cat-file --batch` process, never 20 processes. Assert `resolve_head_receipt` delegates to the same batch implementation with one id.

- [ ] **Step 6: Implement exact-tree and batch-object reading**

Run Git with `cwd=vault`, `check=False`, `stdout/stderr=PIPE`, `input=` only for `cat-file --batch`, and `LC_ALL=C`. First resolve `HEAD` and the exact root entry with `git ls-tree -z HEAD -- <entity>/outbox/.receipts`:

- no entry: return absent for every requested id;
- one exact `tree` entry: continue;
- one non-tree entry: raise `ReceiptStoreIntegrityError`;
- malformed output, multiple entries, invalid UTF-8 protocol, or Git failure: raise `ReceiptStoreUnavailable`.

Feed all `HEAD:<receipt-path>` expressions to one `git cat-file --batch`. Parse each header as `<oid> <type> <size>` or `<expression> missing`, require blobs for present entries, read exactly `size` bytes plus the protocol newline, and map each response back to the canonical requested id. A present malformed blob becomes `ReceiptResolution(id, None, InvalidActionReceipt(...))`; one malformed id does not disable its siblings.

- [ ] **Step 7: Implement the read-only full-store validator**

`validate_head_receipt_store` uses `git ls-tree -r -z HEAD -- <root>` once, rejects nested paths/non-blob entries/non-`.yaml` leaves, batches every blob, parses every receipt, and returns receipts sorted by `proposal_id`. It performs no filesystem writes and invokes no request-path code.

- [ ] **Step 8: Run focused and dependency regressions**

```bash
uv run python -m pytest \
  tests/test_action_receipts.py tests/test_review_tokens.py \
  tests/test_outbox.py tests/test_registry.py -q
```

- [ ] **Step 9: Commit Task 1**

```bash
git add app/action_receipts.py app/review_tokens.py \
  app/console_errors.py tests/test_action_receipts.py tests/test_review_tokens.py \
  tests/test_console_errors.py tests/test_console_invariants.py
git commit -m "feat: add committed action receipt reader"
```

If independent review finds a Task 1 defect, correct it in a second focused
commit rather than rewriting the reviewed history. The actual Task 1 review
required `fix: close action receipt review findings` to close concrete
exception mappings, empty nested-tree visibility, and O(store) duplicate
tracking before the checkpoint became green.

- [ ] **Step 10: Independent Task 1 review**

Dispatch a fresh reviewer to check protocol totality, exact filename binding, forced empty-store semantics, per-id versus entity-wide failures, no worktree authority, and absence of request-path enumeration. Resolve every finding before Task 2.

---

### Task 2: Taxonomy sequencing checkpoint — no separate code task

Independent Task 1 review proved that concrete receipt exceptions cannot wait
for a later taxonomy commit: the repository's full-suite invariant requires
every concrete application exception to have its truthful mapping in the same
green checkpoint that introduces it. Task 1 therefore owns exact
`E-RECEIPT`, `InvalidActionReceipt -> E-RECEIPT`,
`ReceiptStoreIntegrityError -> E-TAMPER`, and
`ReceiptStoreUnavailable -> E-UNAVAILABLE`.

`E-APPLIED` must not be added here because it would have no producer. Task 3
adds the code, exact contract tests, `PostCommitConsumptionError`, mapping,
and producer evidence together. This checkpoint has no files, command, or
commit; proceed directly from accepted Task 1 to Task 3.

---

### Task 3: Make quarantine the transaction's final mutation

**Owner:** primary Codex agent.

**Files:**
- Modify: `app/git_transaction.py`
- Modify: `app/console_errors.py`
- Modify: `tests/test_git_transaction.py`
- Modify: `tests/test_console_errors.py`
- Modify: `tests/test_console_invariants.py`
- Modify: `docs/superpowers/plans/s7_mutation_campaign.py`
- Modify: `docs/superpowers/plans/2026-08-23-s7-mutation-ledger.md`

**Interfaces:**
- Consumes: normal receipt `PathChange`; receipt parent path ending in `outbox/.receipts`; existing `QuarantinedRecord` descriptor verification.
- Produces:

```python
@dataclass(frozen=True)
class TransactionPreconditionRefused:
    reason: object

Precondition = Callable[[], object | None]
```

`PostCommitConsumptionError(GitTransactionError)` has constructor
`(result: TransactionResult, cause: BaseException)`, retains both values, and
does not alter `result`. The exact callable signatures are
`execute_transaction(vault: Path, plan: TransactionPlan) -> TransactionResult | TransactionPreconditionRefused`
and `consume_reviewed_proposal(vault: Path, relative_path: str, expected: PathState, *, preconditions: tuple[Precondition, ...] = ()) -> str | TransactionPreconditionRefused`.

The tagged refusal lets a valid receipt stop a direct/forged request under the lock without mislabeling it `E-RECEIPT` or inventing a new operator error. Existing preconditions return `None` or raise; receipt preconditions return an `ActionReceipt` when the id is already spent.

- [ ] **Step 1: Write RED tests for precondition refusal without mutation**

Assert a precondition returning `receipt` yields `TransactionPreconditionRefused(receipt)`, creates no commit, does not call `_apply_state`, leaves HEAD/index/tree byte-identical, and still holds the approval lock while evaluating. Assert raised preconditions retain existing behavior.

- [ ] **Step 2: Write RED tests for receipt parent creation**

Add a receipt `PathChange` with absent `.receipts/`. The transaction must create the worktree directory with mode `0o700`, write the receipt with mode `0o644`, stage exactly the receipt plus action paths, and permit an empty durable directory after a pre-commit refusal. Symlink/non-directory/raced parent shapes refuse without writes outside the entity.

- [ ] **Step 3: Write RED tests for quarantine-last order**

Record checkpoints and require:

```text
expected states -> preconditions -> filesystem action+receipt -> commit verified
-> real index synchronized -> final committed states -> proposal quarantine
-> final HEAD/unrelated-state verification -> return
```

Inject before commit and assert no proposal entered `.consumed/`. Inject after commit but before quarantine and assert commit+receipt remain, proposal remains pending, no rename-back occurs, and `PostCommitConsumptionError.result.commit_oid` is HEAD.

- [ ] **Step 4: Implement tagged precondition refusal**

Change `TransactionPlan.preconditions` to `tuple[Precondition, ...]`. In both `execute_transaction` and standalone `consume_reviewed_proposal`, evaluate under the lock; the first non-`None` value returns `TransactionPreconditionRefused(value)` before any mutation. Preserve lock-cleanup truthfulness: a cleanup failure before mutation remains the existing failure outcome.

- [ ] **Step 5: Add safe receipt-parent creation to `PathChange`**

Extend the dataclass without changing existing callers:

```python
@dataclass(frozen=True)
class PathChange:
    path: str
    before: PathState
    after: PathState
    create_parent: bool = False
```

Only a receipt change sets `create_parent=True`. `_apply_state` may create exactly the missing final parent directory, through its already checked parent descriptor, with `os.mkdir(name, 0o700, dir_fd=...)`; it then opens and identity-verifies that directory. It never recursively creates parents and never adopts a `FileExistsError` race. Add a `TransactionPlan` invariant that `create_parent` is permitted only for a regular-file creation whose path matches `[^/]+/outbox/\.receipts/[^/]+\.yaml`.

- [ ] **Step 6: Reorder `_execute_locked_body`**

Remove the owned-change quarantine loop from the pre-commit section. Complete action/receipt application, alternate-index commit, commit verification, real-index synchronization, committed-path verification, unrelated-state verification, and HEAD verification first. Construct `TransactionResult`; only then quarantine each `owned_change` under the still-held lock and verify `_require_final_states` across `changes + owned_changes`.

Pre-commit exceptions call ordinary rollback with `quarantined=()`. Post-commit quarantine/verification exceptions do **not** call `_rollback_transaction`; they raise `PostCommitConsumptionError(result, exc)` and perform no rename-back. Descriptor release remains exactly once in `finally`.

- [ ] **Step 7: Preserve cleanup distinctions**

Tests pin:

- fully verified quarantine plus later temporary/lock cleanup failure -> `GitTransactionCommittedError` / `E-COMMITTED`;
- incomplete, substituted, disappeared, rewritten, unavailable, or unsupported post-commit consumption -> `PostCommitConsumptionError` / `E-APPLIED`;
- reject's verified consume plus lock cleanup failure -> `QuarantineCleanupError` / `E-QUARANTINED`.

Add `PostCommitConsumptionError: E-APPLIED` to `_EXACT`, the closed Git family test, and the transcribed design map.

- [ ] **Step 8: Prove and retire the now-unreachable Stage 1 machinery**

With quarantine-last present but before deleting the old code, deliberately mutate each former producer in `diagnose_quarantined_record` and run the complete focused behavioral suite. Record intentional `ALIVE`: the mutations change no observed behavior because transaction rollback cannot own a quarantined proposal anymore. A missing anchor or collection error is not evidence.

Then, in this same uncommitted task change, delete `QuarantinedRecordRetained`, `QuarantineRestorationBlocked`, `diagnose_quarantined_record`, `_rank_quarantine_outcomes`, rollback diagnosis parameters/branches, `E-RETAINED`, `E-STRANDED`, exact mappings, and executable tests. Mark M18 and M19 `RETIRED (historical)` in the ledger and remove their live runner entries, preserving the deliberate-ALIVE evidence and reason. `QuarantineEntrySubstituted` / `E-SUBSTITUTED` remain live for reject.

Add the orphan-outcome structural guard now: every remaining `_CODES` member except terminal composition defaults (`E-UNKNOWN` and request validation) must have a named executable producer, and every producer must resolve to the intended code. Plant a temporary code with no producer and require the guard to fail with `orphan operator outcome` before restoring it.

- [ ] **Step 9: Run focused transaction, taxonomy, and retirement tests**

```bash
uv run python -m pytest \
  tests/test_git_transaction.py tests/test_console_errors.py \
  tests/test_console_invariants.py -q
```

- [ ] **Step 10: Commit Task 3**

```bash
git add app/git_transaction.py app/console_errors.py \
  tests/test_git_transaction.py tests/test_console_errors.py \
  tests/test_console_invariants.py \
  docs/superpowers/plans/s7_mutation_campaign.py \
  docs/superpowers/plans/2026-08-23-s7-mutation-ledger.md
git commit -m "feat: quarantine proposals after commit"
```

- [ ] **Step 11: Independent Task 3 review**

Reviewer reproduces pre-commit, commit-created, index-sync, quarantine substitution/disappearance/rewrite, descriptor cleanup, lock cleanup, and crash seams. They must verify no post-commit branch calls rollback or rename-back.

---

### Task 4: Bind all three services to committed receipts

**Owner:** primary Codex agent.

**Files:**
- Modify: `app/outbox.py`
- Modify: `app/registry.py`
- Modify: `tests/test_outbox.py`
- Modify: `tests/test_registry.py`
- Modify: `tests/test_git_transaction.py`

**Interfaces:**
- Consumes: `ActionReceipt`, `SpentAction`, `ReceiptResolution`, `make_action_receipt`, `render_action_receipt`, `receipt_relative_path`, `resolve_head_receipt`, `TransactionPreconditionRefused`.
- Produces:

```python
ClassificationActionResult = Proposal | SpentAction
RegistryDeleteResult = DeleteProposal | SpentAction
```

The exact callable signatures are
`approve(scope: Scope, proposal_id: str, review_sha256: object) -> ClassificationActionResult`,
`reject(scope: Scope, proposal_id: str, review_sha256: object) -> ClassificationActionResult`,
and `execute_delete(scope: Scope, proposal_id: str, review_sha256: object) -> RegistryDeleteResult`.

- [ ] **Step 1: Write approval same-commit receipt RED tests**

Assert success creates exactly one commit whose changed paths are source, destination, and `<entity>/outbox/.receipts/<id>.yaml`; the receipt bytes contain the reviewed proposal digest and `action_kind: approval`; the proposal bytes are absent from the commit and land under `.consumed/` only after commit verification.

Assert omitting the receipt from `changes`, `commit_paths`, staging, or commit verification fails the named test. Assert the receipt is not in `owned_changes`.

- [ ] **Step 2: Write registry-delete same-commit receipt RED tests**

Mirror approval with registry file plus receipt, `action_kind: registry deletion`, while retaining the live-reference recount under the lock. Revert removes the registry diff and receipt; it does not revive a quarantined proposal. If quarantine was injected to fail, revert removes the receipt and the still-unconsumed proposal becomes ordinarily actionable.

- [ ] **Step 3: Write locked existing-receipt RED tests for all actions**

For approve and delete, plant a valid receipt in `HEAD` after proposal review but before lock acquisition. For reject, plant it immediately before `consume_reviewed_proposal` takes the lock. Each returns `SpentAction`, creates no commit/quarantine, and never parses receipt digest against pending bytes. A worktree-only deletion of the committed receipt changes nothing.

Malformed matching receipts raise `InvalidActionReceipt` before mutation. Non-tree roots and unavailable HEAD raise their store-level errors before mutation. A valid receipt wins even when the pending proposal is malformed, replaced, redirected, or contains different bytes.

- [ ] **Step 4: Add receipt construction to approve and delete plans**

Use the submitted fingerprint only after `require_review_match` returned it. Build:

```python
receipt = make_action_receipt(prop.id, review_sha256, "approval")
receipt_change = PathChange(
    receipt_relative_path(prop.entity, prop.id),
    PathState.absent(),
    PathState.regular(render_action_receipt(receipt), 0o644),
    create_parent=True,
)
```

Append it to `changes` and `commit_paths`; never `owned_changes`. Registry delete uses `"registry deletion"`. Add a receipt precondition after existing expected-state checks; for delete keep both receipt lookup and live reference recount in `preconditions` and assert both execute under the same lock before mutation.

- [ ] **Step 5: Translate tagged transaction refusals**

When `execute_transaction` returns `TransactionPreconditionRefused(reason)` require `reason` is an `ActionReceipt` for this canonical id and return `SpentAction(reason)`. Any other reason is an internal contract defect. On ordinary `TransactionResult`, return the original bound `Proposal`/`DeleteProposal` as before.

- [ ] **Step 6: Add reject's standalone locked check**

Pass a receipt lookup precondition to `consume_reviewed_proposal`. A valid receipt returns `SpentAction`; an absent receipt consumes normally; a malformed/root failure raises before quarantine. Keep exact-byte proposal ownership unchanged and do not create a reject receipt or Git commit.

- [ ] **Step 7: Run focused service regressions**

```bash
uv run python -m pytest \
  tests/test_outbox.py tests/test_registry.py tests/test_git_transaction.py -q
```

- [ ] **Step 8: Commit Task 4**

```bash
git add app/outbox.py app/registry.py \
  tests/test_outbox.py tests/test_registry.py tests/test_git_transaction.py
git commit -m "feat: bind reviewed actions to committed receipts"
```

- [ ] **Step 9: Independent Task 4 review**

Reviewer targets late receipt creation, worktree deletion, malformed receipt, valid receipt plus hostile proposal, receipt omission from exact commit, live-reference ordering, reject's separate lock path, and any accidental use of the stored digest for eligibility.

---

### Task 5: Render receipt-backed spent state before proposal parsing

**Owner:** delegated coding agent C.

**Files:**
- Modify: `app/outbox.py`
- Modify: `app/registry.py`
- Modify: `app/main.py`
- Create: `templates/blocks/action_receipt_card.html`
- Modify: `templates/blocks/outbox_list.html`
- Test: `tests/test_console_projection.py`
- Test: `tests/test_console_routes.py`
- Test: `tests/test_console_invariants.py`

**Interfaces:**
- Consumes: `ReceiptResolution`, `ActionReceipt`, `SpentAction`, `resolve_head_receipts`, `resolve_head_receipt`, exact `E-RECEIPT`, `new_issue()`.
- Produces: receipt-bearing outbox rows and a shared spent/malformed card. Extend `OutboxRow` with `receipt: ActionReceipt | None = None` while preserving the rule that only proposal rows can carry `review_sha256` or controls.

- [ ] **Step 1: Write receipt-first no-open RED tests**

For classification projection and registry delete refresh, commit a matching valid receipt and replace the pending leaf with each of: same bytes, different bytes, malformed bytes, symlink to an outside marker, non-file, and recreated canonical-id file. Spy on `os.open`/`builtins.open` by inode. Assert the pending leaf/target is never opened, parsed, hashed, or mentioned; the result is spent and controls-withheld.

Add a positive-control unspent id proving the spy observes its actual proposal inode, so the negative assertion cannot pass vacuously.

- [ ] **Step 2: Write card contract RED tests**

The valid receipt card must contain:

- approval: `An approval has already completed for this proposal ID, so it cannot be used again.`
- registry deletion: `A registry deletion has already completed for this proposal ID, so it cannot be used again.`
- when no-follow metadata proves a real pending-record leaf is present, exact
  persistent copy: `A record with this ID is still present. OneOS will not act
  on it. Do not move or delete it by hand.` Direct spent responses after full
  consumption omit this sentence; this presentation-only check never weakens
  the receipt in `HEAD`.
- approval conditional link: `If the item still needs classifying, start again from triage — that allocates a new proposal.`
- delete conditional link: `If the entry still needs deleting, start a new deletion from the registry.`

Assert no 64-hex run, `hx-post`, fingerprint field, button, reconfirmation, or `Check again`. Element id must be `receipt-card-<validated-id>-<server-issue>`; hostile raw filenames never reach it.

Malformed matching receipts found while projecting a pending row render the
exact `E-RECEIPT` alert, the persistent record copy, no guessed link, no
action, and do not disable a valid sibling id.

- [ ] **Step 3: Implement receipt-first outbox projection**

After validating the outbox directory, gather candidate `*.yaml` leaves by name without opening them. Canonicalize stems with `require_proposal_id`; batch-resolve only canonical ids. For each candidate:

1. valid receipt -> create a non-actionable receipt row without `_require_outbox_path(...require_leaf=True)` or `review_snapshot_for`;
2. per-id invalid receipt -> create a non-actionable receipt-error row;
3. absent receipt -> run the existing strict three-phase proposal projection unchanged;
4. store-level error -> abort/block the entity according to its mapped outcome.

An approval receipt appears on the outbox surface. A valid registry-deletion receipt is skipped by the classification listing and rendered by the registry receipt route. An invalid receipt has no trustworthy kind, so it remains visible as a linkless disabled row wherever its pending id is encountered.

- [ ] **Step 4: Make delete review receipt-first**

Add `get_delete_receipt_or_review(scope, proposal_id) -> ActionReceipt | ReviewSnapshot[DeleteProposal]`. It canonicalizes the id, resolves `HEAD`, returns a valid receipt before `_delete_proposal_path`, raises per-id/store errors as designed, and otherwise delegates to `get_delete_review`. Both preview refresh and post-action rendering use this boundary.

- [ ] **Step 5: Handle `SpentAction` and `E-APPLIED` in routes**

Approve/reject/delete routes branch on the returned value:

- normal domain object -> existing success/list behavior;
- `SpentAction` -> render `action_receipt_card.html` at fragment status 200 without `describe()`;
- `PostCommitConsumptionError` -> render the exact `E-APPLIED` alert **and** re-read the current receipt-backed card from `HEAD`, keeping status 500.

For outbox actions, return the outbox list with `approval_error=E-APPLIED`; projection supplies the spent row. For delete execute, return a wrapper containing the alert and spent card, retargeting the card that actually existed in the submitted issuance. If the receipt-card re-read fails, compose both outcomes under the existing S6 rules rather than losing `E-APPLIED`.

- [ ] **Step 6: Implement the shared card**

`action_receipt_card.html` accepts only `entity`, `proposal_id`, `receipt`, `receipt_error`, and server `issue`. It never accepts a proposal or digest. Valid receipt action kind selects exact copy/link; invalid receipt renders `blocks/alert.html` and no navigation. The persistent lingering-record sentence is unconditional because rendering itself proves a pending filename exists.

- [ ] **Step 7: Pin HTMX 500 swapping and actual selectors**

An end-to-end route test must submit from markup actually rendered by the page, inject post-commit quarantine failure, assert HTTP 500, assert `_head.html` config includes `[45]..` with `swap:true,error:true`, and prove the response's retarget/OOB selectors name the existing acted-on card and the new receipt card. Remove the config in a mutation and require the named test to fail for the intended reason.

- [ ] **Step 8: Run focused presentation regressions**

```bash
uv run python -m pytest \
  tests/test_console_projection.py tests/test_console_routes.py \
  tests/test_console_invariants.py tests/test_outbox.py tests/test_registry.py -q
```

- [ ] **Step 9: Commit Task 5**

```bash
git add app/outbox.py app/registry.py app/main.py \
  templates/blocks/action_receipt_card.html templates/blocks/outbox_list.html \
  tests/test_console_projection.py tests/test_console_routes.py \
  tests/test_console_invariants.py
git commit -m "feat: render receipt-backed spent proposals"
```

- [ ] **Step 10: Independent Task 5 review**

Reviewer verifies no-open instrumentation is non-vacuous, valid/malformed per-id isolation, hostile filename confinement, no digest leak, no live action attribute, actual DOM selector identity, conditional-link truthfulness, and 500 swap dependence.

---

### Task 6: Add the read-only accumulated receipt audit gate

**Owner:** delegated coding agent D.

**Files:**
- Modify: `tests/test_gate3_audit.py`
- Modify: `tests/test_public_repo_audit.py`
- Modify: `tests/test_action_receipts.py`
- Modify: `docs/superpowers/plans/2026-08-24-s7-stage-2-action-receipts.md` only if the final command name differs from this plan's declared interface.

**Interfaces:**
- Consumes: `validate_head_receipt_store(vault, entity) -> tuple[ActionReceipt, ...]`.
- Produces: an offline, read-only O(store) gate that reports schema/version/id/digest/action failures and never repairs.

- [ ] **Step 1: Write RED audit tests**

Create a repository with several historical receipts and no pending proposals. Assert the gate enumerates every `HEAD` receipt and rejects: nested entries, wrong suffix, non-blob, invalid YAML, extra/missing fields, version mismatch, filename/content mismatch, invalid digest, invalid action kind, duplicate ids, and non-tree root.

Monkeypatch filesystem writers (`Path.write_*`, `open` write modes, `os.unlink`, `os.replace`, `os.rename`, `os.mkdir`) to fail if called; the validator must remain green because it reads only Git objects.

- [ ] **Step 2: Run RED**

```bash
uv run python -m pytest \
  tests/test_action_receipts.py tests/test_gate3_audit.py \
  tests/test_public_repo_audit.py -q
```

- [ ] **Step 3: Wire the validator into existing audit patterns**

Use synthetic fixtures only. The request path must not import or call `validate_head_receipt_store`; add an AST call-graph assertion over `app/main.py`, `app/outbox.py`, and `app/registry.py`. The offline gate may enumerate O(store); request handlers may only batch current ids.

- [ ] **Step 4: Run focused audits**

```bash
uv run python -m pytest \
  tests/test_action_receipts.py tests/test_gate3_audit.py \
  tests/test_public_repo_audit.py tests/test_console_invariants.py -q
```

- [ ] **Step 5: Commit Task 6**

```bash
git add tests/test_action_receipts.py tests/test_gate3_audit.py \
  tests/test_public_repo_audit.py tests/test_console_invariants.py
git commit -m "test: audit accumulated action receipts"
```

- [ ] **Step 6: Independent Task 6 review**

Reviewer proves full-store enumeration is offline only, HEAD-only, non-mutating, and complementary to per-id request checks; they also verify the empty-store exception and wrong-object root test are non-vacuous.

---

### Task 7: Extend and run the Stage 2 mutation campaign

**Owner:** primary Codex agent.

**Files:**
- Modify: `tests/test_console_invariants.py`
- Modify: `docs/superpowers/plans/s7_mutation_campaign.py`
- Modify: `docs/superpowers/plans/2026-08-23-s7-mutation-ledger.md`

**Interfaces:**
- Consumes: completed quarantine-last implementation, Task 3's explicit Stage 1 retirement, and all Stage 2 tests.
- Produces: complete reproducible Stage 2 mutation rows and a clean full-suite campaign close.

- [ ] **Step 1: Reconcile the retired rows before adding new live rows**

Assert M18 and M19 are absent from the runner and present exactly once in the ledger as `RETIRED (historical)`, with Task 3's deliberate-ALIVE evidence. Assert no live runner anchor names the removed classes/functions/codes.

- [ ] **Step 2: Re-run the orphan-outcome structural test**

Run the Task 3 guard and confirm every remaining live taxonomy code has a producer. Mutation 17 below will prove the guard itself is load-bearing.

- [ ] **Step 3: Add Stage 2 mutations**

Each exact OLD/NEW mutation gets its own node and unique `--tb=line` diagnostic:

1. omit receipt from `changes`;
2. omit receipt from exact `commit_paths`;
3. mark receipt as `owned_changes`;
4. read receipt authority from worktree instead of HEAD;
5. move receipt check before the lock for approve/delete;
6. omit reject's standalone locked check;
7. compare stored receipt digest with pending bytes;
8. parse/open pending proposal before valid receipt resolution;
9. treat non-tree `.receipts` root as absent;
10. collapse malformed matching receipt into entity-wide blocking;
11. quarantine before commit;
12. roll back action+receipt after post-commit consumption failure;
13. map post-commit consumption failure to `E-COMMITTED`;
14. drop 500-response HTMX swap support;
15. add fingerprint or `hx-post` to spent card;
16. enumerate the full receipt store from a request path;
17. allow an orphan taxonomy code;
18. re-add either retired outcome code without a reachable producer, proving the orphan guard catches attempted taxonomy resurrection.

The runner must refuse dirty targets, require OLD exactly once, run exact nodes alone, bind diagnostics to failing assertions with `--tb=line`, restore from in-memory preimages, verify byte identity, rerun green, and finish with the full public suite.

- [ ] **Step 4: Run the complete campaign from a clean tree**

```bash
git status --porcelain
uv run python docs/superpowers/plans/s7_mutation_campaign.py --list
uv run python docs/superpowers/plans/s7_mutation_campaign.py
git status --porcelain
git diff --check
```

Expected: every live mutation reports RED then GREEN; deliberately retired rows are absent from the live count and documented; final full suite passes; no target file remains dirty.

- [ ] **Step 5: Commit the campaign and evidence**

```bash
git add tests/test_console_invariants.py \
  docs/superpowers/plans/s7_mutation_campaign.py \
  docs/superpowers/plans/2026-08-23-s7-mutation-ledger.md
git commit -m "test: prove committed receipt safety boundary"
```

- [ ] **Step 6: Dispatch two independent whole-checkpoint reviewers**

Reviewer 1 owns transaction/order/crash/revert/taxonomy/retirement. Reviewer 2 owns HEAD reader/projection/UI/audit/mutation reproducibility. Neither may be an implementer of the files they review. Both review the entire checkpoint diff from `519066c`, not only their assigned files, and both independently run the public suite plus relevant mutations.

Resolve every accepted finding with RED -> fix -> focused green -> mutation proof -> full green. Re-dispatch both reviewers against the resulting checkpoint.

---

### Task 8: Final verification and trusted-boundary handoff

**Owner:** primary Codex agent; private evidence only at the trusted local boundary.

**Files:** no product changes. Documentation changes only if verified counts/commands need recording.

- [ ] **Step 1: Run the final public gates**

```bash
uv run python -m pytest -q
uv run python docs/superpowers/plans/s7_mutation_campaign.py
git diff --check
git status --porcelain
```

Read every count from these runs; do not reuse earlier commentary.

- [ ] **Step 2: Verify Linux atomic move on a real Linux host**

Exercise `renameat2(RENAME_NOREPLACE)` for both successful movement and occupied-destination refusal, asserting source/destination bytes and no overwrite. macOS `renameatx_np` evidence does not satisfy this step.

- [ ] **Step 3: Run private gates read-only when authorized**

Only if `ONEOS_VAULT` is set and its HEAD is `2aa8b14` or later:

```bash
git -C "$ONEOS_VAULT" log --oneline -1
python3 "$ONEOS_VAULT/_system/scripts/check_v2.py" "$ONEOS_VAULT"
cd "$ONEOS_VAULT/_system/scripts" && python3 -m unittest discover
```

Expected: `check_v2` reports 0 errors/0 warnings and 37 private tests pass. These commands are reads; do not run formatters, cleaners, or repair tools.

- [ ] **Step 4: Prove Grey Matter preservation**

Compare exact pre/post HEAD, porcelain-v2 NUL output, binary working-tree diff, and binary cached diff captured by the trusted local operator. Absence of vault access is not preservation proof.

- [ ] **Step 5: Prepare the handoff without remote or destructive operations**

Report repository root, original `origin/main`/baseline SHA, branch, worktree, verified HEAD, public suite, mutation campaign, Linux result, private gates, Grey Matter preservation, and remaining inherited items 2–4. Do not push, open a PR, merge, delete branches, or remove worktrees without separate authorization.

## Plan self-review checklist

- [ ] Every Stage 2 design clause maps to a task: schema/HEAD/absence/batching/offline audit (1, 6); taxonomy (2, 3); quarantine-last/crash/revert and retirement/orphan guard (3, 4); three service gates (4); receipt-first presentation (5); live mutation campaign (7); external gates (8).
- [ ] Search this plan for `TBD`, `TODO`, `implement later`, `similar to`, and `appropriate error`; none may remain as an instruction.
- [ ] Verify interface names are consistent: `ActionReceipt`, `ReceiptResolution`, `SpentAction`, `TransactionPreconditionRefused`, `PostCommitConsumptionError`, and the two action result unions.
- [ ] Verify delegated tasks do not edit concurrently and no implementer is their own reviewer.
- [ ] Verify the plan never treats valid receipt presence as `E-RECEIPT`, never compares stored receipt digest with pending bytes, never enumerates historical receipts in a request, and never restores a quarantine record by name.
