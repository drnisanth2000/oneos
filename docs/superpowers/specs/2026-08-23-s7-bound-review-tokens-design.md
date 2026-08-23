# S7 — Bound Review Tokens

**Status:** APPROVED — conversational and written design approved

**Amendment 1 (proposed, pending approval):** consumed proposals are
quarantined rather than deleted. Amended clauses are marked
*(Amendment 1)*. Rationale in “Why deletion cannot satisfy criterion 4”
below.

**Base:** merged `origin/main` at
`d7ad86b651c5f5f7c1adad8af94a0b767fb30a8f`. Fresh public baseline:
`uv run python -m pytest -q` → 926 passed.

**Authority:** `AGENTS.md`, `BUILD.md` Safety Foundation S7, and
`docs/STATUS.md` “S7 inherits these from S6”.

**Scope:** Safety Foundation S7 only.

## Objective

Bind approve, reject, and registry-delete execution to the exact stored
proposal bytes used to build the operator's review. A proposal id names a
mutable file. It is not evidence that the file still contains what the
operator reviewed.

S7 adds a review fingerprint to every actionable review. The action must carry
both the proposal id and that fingerprint. OneOS compares the submitted
fingerprint with the exact proposal state at the final pre-mutation boundary.
Any mismatch is a visible refusal and changes nothing.

The precedent is S4: S4 binds approval to the source receipt bytes. S7 applies
the same rule one artifact later, to the proposal record itself.

## Approved product decisions

1. **One rule for all reviewed actions.** Approve, reject, and registry delete
   use the same review-fingerprint contract.
2. **Reconfirm rather than discard.** A changed proposal stays on the same
   screen. OneOS shows the previously reviewed version and the current version,
   disables the old controls, and requires a deliberate new action.
3. **The final review is the boundary.** The fingerprint is made from the exact
   validated proposal bytes used to render the final review, not from an
   earlier list entry or a later reread.
4. **Repeated change means repeated review.** Every subsequent rewrite
   invalidates the controls issued for the previous version.
5. **Refresh starts a new review.** Reloading presents the current proposal as
   a new reviewed version with a new fingerprint.
6. **The browser holds the old review.** OneOS does not add server-side review
   sessions or temporary review storage. The already-rendered browser card is
   retained for comparison; the server supplies the newly validated current
   card.
7. **Pending edits invalidate; direct registry edits do not join S7.** Any edit
   to a pending proposal changes its fingerprint. Registry add/edit remains the
   existing direct, one-commit, reversible operation. Registry delete remains
   reviewed.
8. **Recovery does not guess.** `Check again` is read-only. A missing proposal
   may link the operator back to the existing triage flow, where any recreated
   proposal receives a new id. Damaged or cross-scope records are never
   reconstructed or moved automatically.
9. **Independent review and mutation testing are mandatory.** A green test is
   evidence only after the relevant protection has been deliberately broken,
   the test has gone red, and the exact implementation has been restored.
10. **A reviewed action never deletes a proposal file.** *(Amendment 1)*
    Approve, reject, and registry delete consume a *proposal record* by moving
    it into a recoverable quarantine, never by unlinking it. Losing a race
    must not be able to destroy a proposal. This rule is about proposal
    records only: approve's source-to-destination move of the curated file is
    an intended, committed, Git-revertible effect and is unchanged.
    Reclaiming quarantined records is a separate, later operation and is
    never part of a reviewed action.

## Scope boundaries

### Included

- one exact-byte review snapshot and SHA-256 fingerprint for each displayed
  classification or delete proposal;
- propagation of `proposal id + review_sha256` through route, service, and
  mutation boundaries;
- exact-state refusal before approve, reject, or delete mutates anything;
- same-screen changed-since-review presentation with disabled old controls and
  fresh current controls;
- a read-only `Check again` path and guidance back to existing triage when a
  missing proposal can be recreated safely;
- truthful integration with S6's existing visible Console outcomes;
- a quarantine area for consumed proposal records, and the replacement of
  every destructive removal *of a proposal record* in a reviewed action with
  an atomic no-overwrite move into it *(Amendment 1)*; and
- synthetic public tests plus the existing private read-only integration gates.

### Explicitly excluded

- a review gate for direct registry add/edit;
- quarantine retention, expiry, reclaim, or any operator surface over it
  *(Amendment 1)* — S7 only stops destroying, and the lifecycle is sequenced
  separately;
- automatic repair, reconstruction, or relocation of damaged proposals;
- email/PDF intake, archive, summary, and mailbox-deletion workflows;
- dashboards, cards, or general workflow screens unrelated to S7;
- new dependencies, daemons, secrets, schemas, conventions, registry values,
  or instance-specific configuration; and
- any write to Grey Matter during design or public verification.

The excluded product work is sequenced separately. The three inherited S6
items below remain mandatory before live gate trials, but are not S7 changes:

1. private-value leakage checks over tracked public documentation;
2. declaration-completeness proof for every route's reachable failures; and
3. specific handling for an unreadable entity manifest and an unavailable
   vault root.

## Threat model

S7 protects against another request, process, worker, or manual edit replacing
a proposal after it was displayed while preserving its id and filename. It
also protects the smaller race between the action's first read and its first
mutation: the state compared must be the same state conditionally removed or
transaction-owned.

The review fingerprint is not a password, capability, or proof of attention.
It does not prove that the operator read the screen, and it does not protect a
compromised browser or an authorized operator deliberately bypassing the UI.
Authentication, authorization, CSRF, and hostile-client attestation are not
added or weakened by S7.

The browser-retained old review is presentation evidence, never mutation
authority. Current server-validated bytes alone determine the new review and
the action's eligibility.

## Architecture

### 1. Exact-byte review snapshot

The proposal reader gains one immutable review result concept:

```text
ReviewSnapshot
  validated proposal value
  exact raw proposal bytes
  sha256(raw bytes)
```

The sequence is fixed:

1. resolve the bound entity and lexical proposal leaf safely;
2. read one no-follow byte snapshot;
3. parse and validate that same byte snapshot;
4. derive scope, destination, identity, and action checks from the parsed value;
5. hash the exact bytes; and
6. render the review from the validated value paired with that hash.

No render path may parse one read and hash another. The digest is lowercase
SHA-256 and is validated as exactly 64 hexadecimal characters when submitted.
A malformed review hash is an invalid request and never reaches a mutation.

Classification projection rows carry `review_sha256` whenever reject is safe.
An unavailable source can still leave a well-formed proposal rejectable, so
source-diff failure does not erase the proposal review hash. An unreadable or
invalid proposal has no review hash and no controls.

Registry delete preview reads the just-written delete proposal into the same
snapshot contract. Its displayed kind, value, and recorded impact come from
that snapshot. The route must not display a second live report that is not part
of the fingerprinted proposal.

### 2. Action contract

The three service boundaries become conceptually:

```text
approve(scope, proposal_id, review_sha256) -> Proposal
reject(scope, proposal_id, review_sha256) -> Proposal
execute_delete(scope, proposal_id, review_sha256) -> DeleteProposal
```

The HTTP forms and HTMX values carry only server-rendered values using the
existing `tojson` rule. A direct service caller is subject to the same required
fingerprint; the route is not the safety boundary.

`execute_delete` returns the bound proposal it actually executed. The route
uses that returned value for success copy instead of performing an earlier,
unbound read for display.

### 3. Final pre-mutation comparison

Every action follows this order:

1. validate the request-local scope, id, and submitted review hash;
2. capture the exact current proposal state safely;
3. compare the captured bytes' SHA-256 with `review_sha256`;
4. parse and validate those same captured bytes;
5. perform existing action-specific freshness and policy checks; and
6. mutate only while still owning that exact captured state.

A new `ReviewedProposalChanged`-style domain outcome represents a digest
mismatch. It is described through S6's conflict/refusal presentation with the
approved message:

> Proposal changed since your review. Nothing was changed.

The name of the implementation type is not normative; the distinct outcome
and its mapping are.

Approve and registry delete already use S5 transaction-owned proposal state.
Their plans must use the same proposal state whose bytes passed the fingerprint
comparison. A reread may not replace it as authority.

Reject remains an uncommitted consumption of an untracked proposal, but it must
be conditional on the reviewed state. It captures, compares, and consumes only
the same regular non-symlink leaf. A rewrite, identity change, type swap,
redirect, or disappearance before consumption is a refusal, not permission to
consume the replacement.

#### Why deletion cannot satisfy criterion 4 *(Amendment 1)*

Criterion 4 requires the compared state to be the exact state consumed. A
deletion cannot provide that. POSIX offers no way to unlink an inode: `unlink`
resolves a name at the instant it runs, so between the last verification and
the removal the name can be rebound, and the removal destroys whatever now
holds it. Every mitigation — capturing under a directory descriptor, reserving
a private name with `O_EXCL`, re-verifying identity and contents through the
open descriptor — narrows that window without closing it, because no primitive
fuses the check to the removal.

So S7 stops relying on winning the race and changes what losing it costs.
Consumption becomes an atomic rename into a quarantine area, which is
**non-destructive**: if the wrong file is moved, it can be moved back, and
nothing is gone. The guarantee stops being a property of timing and becomes a
property of the construction.

The rule: **no reviewed action may unlink a proposal record.** Approve, reject
and registry delete each

1. capture, compare and validate the reviewed state as above;
2. move that leaf into the quarantine area with a **single atomic
   no-overwrite move**;
3. verify the quarantined file through an `O_NOFOLLOW` descriptor — identity
   and contents, never a fresh name lookup; and
4. on any mismatch, move it back under its own name — again with the atomic
   no-overwrite move — and refuse.

#### The move must be one syscall *(Amendment 1)*

Reserving a destination name and then renaming onto it is **two** operations,
not one guarantee: `O_EXCL` proves the reservation was unoccupied when it was
made, and nothing holds it until the rename. Another writer can take the name
in between, and an ordinary `rename` then destroys what took it. A two-step
construction reintroduces exactly the destructive race this amendment exists
to remove.

So step 2 and step 4 must each be a single kernel operation that moves the
file *and* fails if the destination exists:

- Linux: `renameat2(..., RENAME_NOREPLACE)`
- macOS: `renameatx_np(..., RENAME_EXCL)`

Both are reachable through the standard library's `ctypes`, so this adds no
dependency. **Ordinary `rename` is never an acceptable fallback**, because it
silently overwrites.

Availability is a property of the running kernel *and* of the filesystem, so
it is established by attempting the operation, not by inspecting versions.
When the primitive is unavailable — unimplemented, or refused by this
filesystem — the reviewed action **fails closed**: it changes nothing and
reports that this vault cannot be operated on safely. OneOS refuses the action
rather than falling back to a destructive one.

#### What each outcome must truthfully say *(Amendment 1)*

Quarantine changes what is true after a consumption, so the outcomes must
change with it. `E-DISCARDED` — "the proposal is already gone" — becomes
false the moment consumption is recoverable, and must be replaced. Three
distinct outcomes are required:

- **consumed** — the reviewed record was quarantined and the action
  completed. If only the cleanup afterwards failed, the operator is told the
  action took effect, that the record is retained and recoverable, and not to
  retry.
- **restoration blocked** — a mismatch was found, but the record could not be
  put back because its own name is now occupied. Both files are preserved:
  the record stays in quarantine and whatever holds the original name is left
  untouched. This is an indeterminate recovery state and must be reported as
  one. It must never be described as "nothing was changed", and nothing is
  ever deleted to tidy it up.
- **unsupported** — the atomic no-overwrite move is unavailable here. Nothing
  was changed, and no action is possible on this vault until it is resolved.

A refusal that completes restoration leaves the outbox exactly as it found
it. A refusal that cannot complete restoration does not, and says so.

Quarantined records are invisible to every listing and can never be acted on
again: they are evidence, not pending work.

### 4. Registry-delete freshness

The fingerprint binds the delete proposal and its reviewed impact snapshot.
It does not freeze the registries or their references. Immediately before
deletion, OneOS repeats the existing live reference count. New references
refuse deletion even when the proposal fingerprint still matches.

This produces two independent protections:

- changed proposal bytes require a new review; and
- changed live references require clearing the references and reviewing a
  currently valid deletion again.

Neither protection weakens the other.

## Presentation and operator flow

### Normal review

Every actionable proposal card is rendered from a validated review snapshot.
Its approve/reject/delete controls carry its id and review fingerprint. The
fingerprint may remain hidden; it is not useful operator content.

The visible review includes every action-relevant validated proposal field.
Raw YAML syntax is not exposed as an error message. If the stored bytes change
without changing a meaningful field, OneOS still refuses and states that the
stored record changed even though the action-relevant values appear identical.

### Changed-since-review response

On mismatch:

1. no source, destination, registry, proposal, Git index, or Git HEAD state is
   changed;
2. the browser retains and labels the old card “Previously reviewed”;
3. every old action control is disabled;
4. the server returns a newly validated current card and fingerprint;
5. the page presents the meaningful field differences between old and current;
   and
6. only the current card offers fresh controls.

The browser may carry its already-rendered review data solely to explain the
comparison. The server verifies and renders the current version independently;
old browser data never authorizes a mutation.

If another rewrite occurs before reconfirmation, the same flow repeats. The UI
must not accumulate live controls for more than the newest reviewed version.

### Check again and recovery guidance

`Check again` performs only the read/validate/render sequence. It writes no
proposal, curated content, registry, Git, or recovery state.

- If the condition clears, OneOS returns a fresh review and fresh controls.
- If a proposal is missing and the original triage receipt is still available,
  the response links to the existing triage flow. Re-proposal is the existing
  explicit action and allocates a new id.
- A damaged proposal remains preserved for diagnosis; S7 does not infer bytes
  from a browser copy.
- A cross-scope proposal is never moved. OneOS may direct the operator to a
  safely identified workspace, but must not disclose or guess a private path.

### Other failures

Missing, unreadable, cross-scope, source-freshness, registry-reference, and Git
outcomes keep their S6 descriptions and truthful committed/retry semantics.
An action refusal and a subsequent list-rendering failure retain both outcomes
under the S6 composition rules. S7 never reports “nothing changed” when S5
proves a commit succeeded or state is indeterminate.

## Verification

### Required behavioral matrix

Approve, reject, and registry delete each receive tests for:

- unchanged reviewed bytes: the existing success behavior remains;
- same id and filename, rewritten bytes after render: visible refusal and no
  mutation;
- rewrite at the last pre-mutation checkpoint: visible refusal and no mutation;
- malformed or omitted review hash: invalid request and no mutation;
- repeated rewrite/reconfirm cycles: only the newest controls are live;
- refresh: the current version becomes a new review;
- missing, unreadable, redirected, type-swapped, and cross-scope proposal
  states; and
- direct service calls, proving the HTTP route is not the only enforcement.

The no-mutation proof captures and compares all relevant state: proposal bytes
and identity, source and destination bytes, registry bytes, Git HEAD, Git index,
and unrelated staged/unstaged/untracked state. Each test asserts only the state
its action can own, while the higher-level proof asserts the full boundary.

Registry delete additionally proves that a new live reference refuses an
otherwise matching review. Success copy must come from the proposal returned by
the bound execution, not an earlier route read.

Presentation tests prove:

- every actionable control transports exactly one id and one
  `review_sha256` using `tojson`;
- old controls are disabled on conflict;
- current values and meaningful differences render on the same screen;
- `Check again` is read-only; and
- missing-proposal recreation uses the existing triage action and a new id.

### Mutation-tested evidence

For each action family, verification deliberately disables or bypasses the
fingerprint comparison and runs the smallest test selection that must detect
the change. The test must fail for the expected reason. The exact implementation
is restored, its bytes are verified against the saved before-state, and the
same selection must pass.

Mutation checks also cover a transport break: remove the fingerprint from one
button or service call and prove the structural/route test fails. A test that
does not execute the changed path is not accepted as a control.

Every recorded count includes the exact pytest selection that produced it.
The full public suite remains required after focused checks. Private validation
uses synthetic or read-only integration paths and must leave Grey Matter
byte-identical, including its pre-existing staged, unstaged, and untracked
state.

### Independent review

Implementation is not complete on the implementer's green suite. An
independent reviewer checks:

1. the digest is made from the exact bytes used to render;
2. the compared bytes are the state conditionally removed or transaction-owned;
3. every approve, reject, and registry-delete entry point requires the hash;
4. no mismatch path mutates any owned or unrelated state;
5. the same-screen comparison cannot leave stale live controls; and
6. each claimed regression test has a demonstrated red mutation.

Findings are corrected and independently re-reviewed until closure.

## Acceptance criteria

S7 is complete only when all of the following are true:

1. A proposal rewritten after review while preserving id and filename is
   visibly refused before approve, reject, or registry delete changes anything.
2. The operator sees the previously reviewed and current meaningful values on
   the same screen and must act again on the current version.
3. Every action service and transport requires the reviewed SHA-256.
4. The final comparison owns the same exact proposal state the mutation
   consumes, and no reviewed action can destroy a proposal record at all
   *(Amendment 1)*: every consumption is a single atomic no-overwrite move,
   so a lost race leaves recoverable state rather than none, and an
   unavailable primitive fails closed instead of degrading to a destructive
   one.
5. Registry delete still refuses newly introduced references.
6. `Check again` is read-only and no recovery path reuses a stale id.
7. Existing S4 source freshness, S5 isolated transaction/audit, and S6 visible
   outcome guarantees remain intact.
8. Focused mutation controls go red, restored controls go green, the full
   public suite passes, and Grey Matter remains byte-identical.
9. Independent review closes every finding.

Only after these criteria pass may S7 be called complete. S7 does not itself
authorize live gate trials until the separately sequenced inherited items 2–4
are also resolved.
