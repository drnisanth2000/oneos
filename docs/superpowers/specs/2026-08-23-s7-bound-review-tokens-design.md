# S7 — Bound Review Tokens

**Status:** S7 COMPLETE — Amendment 5 verified. Final verification recorded
1,470 public tests passed in 141.98s (0:02:21); all 48 mutation rows RED then
GREEN; Gitleaks and public current-tree/history audits were clean; private
gates recorded `check_v2` 0/0 and 37 tests; the combined repo-plus-vault
history audit was clean; Grey Matter's HEAD, status, worktree diff, and cached
diff were byte-identical before and after, preserving pre-existing edits; and
the final scoped review found no open Critical or Important findings. Amendment
3 Stage 2 provides action receipts, quarantine-last,
`E-APPLIED`, `E-RECEIPT`, the offline receipt validator, and the
orphan-outcome guard. `E-RETAINED`, `E-STRANDED`, and
`diagnose_quarantined_record` are retired. Final evidence is recorded in the
implementation plans and mutation ledger. Inherited items 2–4 remain separate
pre-live-gate work and are not silently claimed complete here.

**Amendment 5 (COMPLETE, 2026-08-25):**
managed ancestor directories are stable for the duration of a reviewed action.
Every supported OneOS, Hermes, parser, browser-extension, and external-agent
writer must use OneOS interfaces and the shared action lock; none may rename an
entity root, `outbox/`, or `.consumed/` while an action holds that lock. A local
actor with filesystem authority that relocates one of those directories after
OneOS's final identity check deliberately bypasses that coordination boundary
and is outside S7's threat model. Detectable moves still fail closed as
`E-TAMPER`; symlinks remain refused; recovery guidance never moves or deletes
content automatically. Rationale and the exact operator behavior appear in
“Managed-directory stability boundary” and “Moved-folder recovery guidance”.

**Amendment 4 (product-owner decision, 2026-08-25):** Linux
`renameat2(RENAME_NOREPLACE)` verification is **withdrawn as a completion
condition** and recorded instead as a known limitation for Linux users. No
Linux host is available; only macOS `renameatx_np(RENAME_EXCL)` has been
exercised. The Linux implementation is unchanged. Rationale and the exact
exposure in “Known Linux limitation” below; acceptance criterion 16 is struck
accordingly.

**Amendment 1 (APPROVED at e0316cc):** consumed proposals are
quarantined rather than deleted. Amended clauses are marked
*(Amendment 1)*. Rationale in “Why deletion cannot satisfy criterion 4”
below.

**Amendment 2 (APPROVED at 0492d94):** restoration is abandoned, not
attempted, once the quarantine name is known to hold a different object.
Amended clauses are marked *(Amendment 2)*. Rationale in “When restoration
must not be attempted” below.

**Amendment 3 (APPROVED — architecture, Stage 1, and Stage 2 design):** no
quarantined record is ever renamed back on the strength of its name. Amendment
2 closed one of the three places that did so; Stage 1 closed the class
immediately, and implemented Stage 2 makes quarantine the last mutation and
uses committed action receipts to prevent a second action.
Amended clauses are marked *(Amendment 3)*. Rationale in “Why a name is never
enough” below.

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
11. **A committed action leaves durable, revertible memory.** *(Amendment 3,
    Stage 2)* Approval and registry deletion commit a minimal action receipt
    in the same commit as the action. Receipt presence spends the proposal id:
    OneOS does not compare, parse, or discuss whatever record later occupies
    that id. Reject creates no commit and therefore creates no receipt, but it
    checks for an existing receipt under its own lock before consuming.

## Scope boundaries

### Included

- one exact-byte review snapshot and SHA-256 fingerprint for each displayed
  classification or delete proposal;
- propagation of `proposal id + review_sha256` through route, service, and
  mutation boundaries;
- exact-state refusal before approve, reject, or delete mutates anything;
- same-screen changed-since-review presentation in which the old review's
  controls are removed and replaced by a "Previously reviewed — no longer
  actionable" label, and only the current review carries controls;
- a read-only `Check again` path and guidance back to existing triage when a
  missing proposal can be recreated safely;
- truthful integration with S6's existing visible Console outcomes;
- a quarantine area for consumed proposal records, and the replacement of
  every destructive removal *of a proposal record* in a reviewed action with
  an atomic no-overwrite move into it *(Amendment 1)*; and
- an entity-local tracked action-receipt store, receipt-bound projection and
  service checks, and a complete read-only offline validator for the
  accumulated receipt store *(Amendment 3, Stage 2)*; and
- synthetic public tests plus the existing private read-only integration gates.

### Explicitly excluded

- a review gate for direct registry add/edit;
- quarantine retention, expiry, reclaim, or any operator surface over it
  *(Amendment 1)* — S7 only stops destroying, and the lifecycle is sequenced
  separately;
- receipt retention, expiry, or reclaim *(Amendment 3, Stage 2)*. Receipt and
  quarantine cleanup both delete evidence, so both require their own later
  design and review. S7 builds every read-only mechanism it depends on and
  defers these destructive ones;
- automatic repair, reconstruction, or relocation of damaged proposals;
- email/PDF intake, archive, summary, and mailbox-deletion workflows;
- dashboards, cards, or general workflow screens unrelated to S7;
- new dependencies, daemons, secrets, schemas, conventions, registry values,
  or instance-specific configuration — with two explicit, bounded exceptions:
  **Amendment 1** adds the quarantine directory for consumed proposal records,
  and **Amendment 3 Stage 2** adds the sibling tracked receipt directory and
  fixed receipt schema. Both live inside the entity's existing `outbox/` and
  exist only to satisfy the no-destruction and no-double-action guarantees.
  They add no dependency (`ctypes` is stdlib), registry value, daemon, secret,
  or instance-specific configuration. Nothing else may use either exception
  to widen S7's scope; and
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

### Managed-directory stability boundary *(Amendment 5)*

S7 continues to protect proposal-leaf replacement, in-place rewriting, type
changes, symlink substitution, and a managed-directory replacement detected
while that directory is being opened and verified. Amendment 5 does not narrow
those guarantees.

It does state one filesystem limit explicitly. A directory descriptor binds an
inode, not that inode's continuing ancestry beneath the vault root. Linux
`renameat2` and macOS `renameatx_np` can act relative to that descriptor, but
neither can atomically require that another local process has not renamed its
directory outside the vault after the final name-and-identity check. Rechecking
the name narrows the interval but cannot close it; checking after the move is
too late to support a “never moved” claim.

Therefore all supported writers cooperate with the action lock and never
rename an entity root, `outbox/`, or `.consumed/` while an action is active.
An authorized local process that deliberately violates this rule is bypassing
OneOS in the same sense as the already excluded operator who deliberately
bypasses the UI. S7 makes no claim that application code can contain that
actor. This exclusion is limited to **ancestor-directory relocation after the
final check**; it is not a general exclusion for concurrent proposal edits or
directory substitutions that OneOS can detect before acting.

The review fingerprint is not a password, capability, or proof of attention.
It does not prove that the operator read the screen, and it does not protect a
compromised browser or an authorized operator deliberately bypassing the UI.
Authentication, authorization, CSRF, and hostile-client attestation are not
added or weakened by S7.

Stage 2 trusts the committed receipt tree in the current Git `HEAD`, on the
same boundary as committed registries and policy files. This does not create a
new exclusion: the approved threat model already excludes an authorized
operator deliberately bypassing the UI. The distinction between that
exclusion and the first paragraph above is the **medium, not the actor**. S7
defends working-tree proposal bytes because a review fingerprint can bind
those mutable bytes. Someone authorized to commit can also rewrite a
destination or `_system/products.yaml` directly; defending committed state
against that capability would require signed records, history interpretation,
or a controlled revert system and is outside S7.

A deliberately committed forged or deleted receipt can defeat prevention for
one proposal id, but it cannot make the result silent: using the id again
creates another visible, individually revertible commit with its own action
message. OneOS loses prevention in that out-of-model case, not the Git audit
record. That bounded and auditable harm is why trusting current `HEAD` is an
acceptable boundary rather than merely a convenience.

The browser-retained old review is presentation evidence, never mutation
authority. Current server-validated bytes alone determine the new review and
the action's eligibility.

The issuance nonce carried on review element ids is likewise untrusted
presentation data. It is syntax-checked and nothing more: decision 6 rules out
review sessions and temporary review storage, so no server-side record of
issued nonces exists and none can be checked against. Replaying a syntactically
valid nonce may select a different same-version DOM anchor than the operator's
own, or name no element at all. It reaches only element ids and swap selectors
— never the service action, the proposal or bytes the server reads, the
fingerprint, or the comparison that authorizes a mutation. The realistic worst
case is misplaced markup on one screen, not an unauthorized action.

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
**non-destructive**: losing the race cannot delete content. The guarantee
stops being a property of timing and becomes a property of the construction.

*(Amendment 3 replaces what this clause originally claimed.)* It first read
"if the wrong file is moved, it can be moved back, and nothing is gone" —
resting the safety of quarantine on automatic restoration. Amendment 3
establishes that automatic name-based restoration is never safe, so that
justification cannot stand and is not merely qualified here. What survives,
and is the stronger claim, is that nothing is destroyed: whatever the race
does, the result is **retained or diagnosed, never automatically restored**.
Stage 1 accepts that a lost race strands the record; Stage 2 eliminates
transaction-rollback stranding by moving quarantine after the commit.

The rule: **no reviewed action may unlink a proposal record.** Approve, reject
and registry delete each

1. capture, compare and validate the reviewed state as above;
2. move that leaf into the quarantine area with a **single atomic
   no-overwrite move**;
3. verify the quarantined file through an `O_NOFOLLOW` descriptor — identity
   and contents, never a fresh name lookup; and
4. verify **both** identity and contents through the descriptor held across
   the move, and on any mismatch — a different inode, no entry at all, or
   the same inode with different bytes — perform no further mutation and
   refuse *(Amendment 2, extended by Amendment 3)*. No mismatch of any kind
   renames the record back: see “Why a name is never enough”.

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

#### Known Linux limitation *(product-owner decision, 2026-08-25)*

Only the macOS path has ever been executed. No real Linux host is available to
this project, so `renameat2(RENAME_NOREPLACE)` has never run — not its success
path and not its occupied-destination refusal. The product owner has accepted
this as a **known limitation for Linux users** rather than a blocking
completion condition. It is recorded here as *unverified*; nothing in this
repository should be read as claiming otherwise.

The Linux implementation is deliberately left unchanged. Editing code that
cannot be exercised would replace untested code with untested edits, which is
not an improvement.

**What a Linux user carries.** `_atomic_mover()` resolves the Linux path two
ways — the libc `renameat2` symbol, or a raw `syscall()` with a
per-architecture number from `_SYS_RENAMEAT2`. Neither has run, and the two
ways it can be wrong differ in kind:

- **Fail-closed, safe but unusable.** If neither path resolves — an unlisted
  `platform.machine()`, or a kernel without `renameat2` — `_MOVE_NO_REPLACE`
  is `None` and every reviewed action refuses with `E-UNSUPPORTED`. Nothing is
  destroyed; approve, reject and registry delete simply do not work there.
- **Fail-open, destructive.** If `_RENAME_NOREPLACE` or a syscall number were
  wrong, the call could degrade to an *ordinary* rename, which silently
  overwrites its destination. That is exactly the harm Amendment 1 exists to
  prevent, reached through the one path no test has entered.

No evidence in this repository distinguishes those outcomes on Linux. Before
S7 is relied upon on a Linux host, both paths must be exercised there. That
work is out of S7's scope and is not a precondition of S7's completion.

Both are reachable through the standard library's `ctypes`, so this adds no
dependency. **Ordinary `rename` is never an acceptable fallback**, because it
silently overwrites.

Availability is a property of the running kernel *and* of the filesystem, so
it is established by attempting the operation, not by inspecting versions.
When the primitive is unavailable — unimplemented, or refused by this
filesystem — the reviewed action **fails closed**: it changes nothing and
reports that this vault cannot be operated on safely. OneOS refuses the action
rather than falling back to a destructive one.

#### What each Stage 1 outcome must truthfully say *(Amendments 1–3)*

This subsection records the implemented Stage 1 contract until Stage 2 lands.
Stage 2 then retires `E-RETAINED` and `E-STRANDED` in the same change that
makes them unreachable, while retaining the reject and substitution outcomes
identified below.

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
- **substituted** — OneOS cannot verify that the quarantine location still
  holds the exact reviewed proposal *(Amendment 2, generalised by Amendment
  3)*. One outcome covers every way that can be true, because the operator's
  position is identical in each and splitting them invites the "one branch
  over" gap this amendment exists to close:

  - the name resolves to a **different inode** (replacement);
  - the name resolves to **nothing** (disappearance);
  - the name resolves to the **same inode with different bytes** (an in-place
    rewrite, which passes every identity check); or
  - the location **cannot be inspected at all** — the record's directory or
    the quarantine cannot be opened, or a name cannot be `stat`ed.

  The fourth is why the wording is "cannot verify" rather than "no longer
  holds". An access failure is not evidence that the proposal is gone; it is
  evidence that OneOS is not entitled to say it is there. Both warrant the
  same `committed=unknown`, `retry=stop` response, and claiming the stronger
  fact would be untrue.

  A distinct outcome from restoration blocked, and never folded into it: that
  outcome can promise both files survive, and this one cannot. Nothing further
  is changed — whatever is at the quarantine location is left exactly where it
  is and is never moved under the proposal's name.

  The contract is closed here, so that no implementation has to invent it:

  | field | value |
  | --- | --- |
  | code | `E-SUBSTITUTED` |
  | tier | `recovery` |
  | severity | `attention` |
  | committed | `unknown` |
  | retry | `stop` |
  | page status | `500` |

  Message, verbatim: "The reviewed proposal was moved, but OneOS cannot
  verify that its quarantine location still holds it unchanged. The reviewed
  record may no longer exist. Do not retry or move files by hand. No
  automated recovery is available. Inspect vault state with git status and
  escalate for verified recovery."

  The wording deliberately says neither *replaced* nor *no longer holds*.
  "Replaced" was true of only one condition. "No longer holds" is true of
  three but false of the fourth: when the location cannot be inspected,
  nothing is known about what it holds.

  The observed `st_nlink` is carried on the typed exception as diagnostic
  evidence. It must not soften this message, must not vary it, and must not
  be presented as a route to automatic recovery — a positive link count says
  only that some link existed at detection time, not where, and not that
  following it would be safe.
- **unsupported** — the atomic no-overwrite move is unavailable here. Nothing
  was changed, and no action is possible on this vault until it is resolved.

A refusal before quarantine leaves the proposal record exactly as it found it.
After quarantine, Amendment 3 permits no name-based restoration: OneOS reports
only what it can verify about the retained record and never claims that it put
the record back. A substitution can promise neither preservation nor
restoration, and says that *(Amendments 2–3)*.

The quarantine directory itself is the one exception, and deliberately so:
it is durable infrastructure, created on first use and never removed. A
refusal may therefore leave an empty `.consumed/` behind. Removing it again
would add a cleanup step whose failure the operator could not see — the
refusal they are shown is about the proposal, not about a directory — and a
silently failing cleanup is a worse thing to own than an empty directory.

#### When restoration must not be attempted *(Amendment 2)*

Step 3 verifies identity because the quarantine name can be rebound between
the move and the verification. Step 4 originally restored on *any* mismatch,
which is correct for a contents mismatch — the object under the name is still
the object that was moved — and actively harmful for an identity mismatch,
where it is known not to be.

Restoring by name after an identity mismatch moves the **substitute** under
the reviewed record's name. OneOS thereby installs an object nobody reviewed
where the reviewed record used to be, and does it as a deliberate step of a
refusal. Meanwhile the fate of the reviewed inode depends on whether any name still
refers to it. If whatever rebound the quarantine name unlinked it, and no other
link remains, it survives only as long as the descriptor OneOS holds and is
gone once that descriptor closes. If some link does remain, it survives
somewhere OneOS cannot identify. Both are possible, and which one obtains is
not knowable from the mismatch alone.

Measured on the pre-amendment implementation, in the first of those cases: the
reviewed inode had no remaining link anywhere in the vault, the proposal's own
name held the substitute's bytes, and the outcome reported was `E-CONFLICT`
with `committed=no` — "nothing was changed" — which was false in both
directions at once.

The reviewed object cannot be *preserved* portably once another writer has
unlinked it. Re-linking an open descriptor into a directory requires
`linkat(AT_EMPTY_PATH)`, which is Linux-only and privileged, and has no macOS
equivalent.

Its bytes, by contrast, are perfectly readable — OneOS still holds the
descriptor and has already verified them. Rebuilding the record from them is
rejected for two other reasons. It would put a *different object* under the
reviewed record's name, which is the same substitution this amendment exists
to stop OneOS from performing, differing only in who supplied the bytes. And
it would be a further mutation after the mismatch was detected, which is
precisely the rule being established. Neither reason is that the record could
not be read; it can.

So S7 stops trying. **After detecting an identity mismatch, no further
mutation is performed.** The quarantine move has already happened and is not
undone; that is precisely what the outcome must say, rather than claiming
either that nothing changed or that both files survive.

`st_nlink` on the held descriptor is recorded as diagnostic evidence at the
moment of detection, and is not a recovery signal: zero means no filesystem
link currently preserves the held inode; greater than zero means some link
existed then, and OneOS must not claim to know where it is or that recovering
through it would be safe. The Console message is the conservative one either
way.

#### Why a name is never enough *(Amendment 3)*

Amendment 2 stopped restoration after an *identity* mismatch. That was one
instance of a general defect, not the defect. The general form: **check the
parcel, then collect it by the shelf label.** Anything that renames a
quarantined record back on the strength of its name can move an object that
was substituted after the check, because POSIX has no rename that is bound to
an inode. Three paths did this; Amendment 2 fixed one.

Measured on the Amendment 2 implementation, substituting *after* the identity
check passed and before the contents-mismatch restore: the proposal's own name
held `DECOY-AFTER-IDENTITY`, the reviewed inode was not there, and the outcome
was `E-CONFLICT` with `committed=no`. Identical to the defect Amendment 2 was
written to remove, one branch further along. A second path,
`restore_quarantined_leaf`, is used by transaction rollback and carries no
identity binding at all. A third condition — the quarantine name *removed*
rather than replaced — was not handled: `lstat` raised a bare
`FileNotFoundError`, which resolved to `E-UNKNOWN`.

So the rule is stated once, positively, and admits no exceptions:

**No reviewed action may rename a quarantined record back under its own name
on the strength of that name.** There is no primitive that fuses the identity
check to the rename, so there is no safe way to do it, and narrowing the
window is not a fix.

This has a consequence for descriptor lifetime, which is normative rather
than an implementation detail. The descriptor opened on the record *before*
it was quarantined **stays open for the whole transaction, including rollback
diagnosis.** Every exact-byte verification — at the move, and again when
deciding which outcome a failed transaction gets — reads through that
descriptor. The quarantine name is never reopened to check bytes: reopening
it is the parcel-label operation in read form, and would verify whatever the
name resolves to at that moment rather than the object that was moved. The
name may be `stat`ed as a diagnostic, to learn whether it still resolves to
the descriptor's inode, but the bytes always come from the descriptor.

Three consequences follow.

1. **The post-move contents check stays; only its response changes.** An
   earlier draft of this amendment proposed removing it, on the reasoning
   that identity after the move already proves the object consumed is the
   object reviewed. That reasoning was wrong, and the error is worth
   recording: identity and contents are independent. A writer can rewrite a
   file *in place* — same device, same inode, same name — and every identity
   check still passes while the bytes are no longer the reviewed bytes.
   Removing the check would let OneOS act on the reviewed version while
   consuming a changed one, which is the exact substitution S7 exists to
   prevent, achieved without any rename at all.

   So the check remains. What changes is what happens when it fails: stop,
   leave the record in quarantine, and report — never rename it back.

2. **Disappearance is the same conservative outcome as substitution.** A
   quarantine name that no longer resolves is no more recoverable than one
   that resolves to something else, and must not escape as `E-UNKNOWN`.

3. **Rollback stops restoring, in two stages.**

   *Stage 1 (now).* A transaction that fails after a record was quarantined
   leaves it quarantined and reports a new outcome. `E-STRANDED` does **not**
   fit and must not be reused: its message says another file now holds the
   proposal's name and that both files are preserved. After a plain
   transaction failure the original name is simply *empty* — there is no
   second file — so that message would be false in both of its claims.
   `E-STRANDED` keeps its existing meaning in Stage 1, for the case where
   something does occupy the original name. Approved Stage 2 retires it only
   when transaction rollback can no longer receive a quarantined record.

   The stage-1 contract is closed here:

   | field | value |
   | --- | --- |
   | code | `E-RETAINED` |
   | tier | `integrity` |
   | severity | `attention` |
   | committed | `no` |
   | retry | `stop` |
   | page status | `500` |

   Message, verbatim: "The action did not complete. The reviewed proposal is
   retained unchanged in the quarantine area, and its original name is empty.
   Do not retry or move files by hand. No automated recovery is available.
   Inspect vault state with git
   status and escalate for verified recovery."

   **Tier.** An earlier draft put this in `recovery` with `committed=no`,
   which is not merely inconsistent — `ConsoleError.__post_init__` enforces
   that a recovery outcome reports `unknown`, so that entry could not be
   constructed at all. Rather than weaken an invariant that earns its keep,
   `E-RETAINED` sits in `integrity`, which already carries determinate
   `committed=no` outcomes such as `E-UNSUPPORTED`. The recovery tier keeps
   its meaning: indeterminate state.

   **`committed=no` is a claim, and it has preconditions.** It is truthful
   only when all three hold at rollback time:

   1. the quarantine location holds the exact reviewed bytes — verified, not
      assumed, through the descriptor still open on the record;
   2. the original name is observed empty; and
   3. every other change in the transaction rolled back successfully.

   If any of them fails, `E-RETAINED` must not be reported. A quarantine
   location that no longer holds the reviewed proposal is `E-SUBSTITUTED`;
   an occupied original name is `E-STRANDED`.

   A simultaneous rollback failure outranks **`E-RETAINED` only**, because
   the thing it invalidates is precondition 3, and therefore the
   `committed=no` claim. It must not replace either of the others: both
   already report `committed=unknown`, and both say something more specific
   than "a rollback failed" — that the exact reviewed bytes could not be
   verified, or that two files were observed. Replacing them would trade a
   precise description for a vaguer one and hide what the operator most
   needs. Where a rollback failure accompanies them it is **composed** onto
   the primary outcome, never substituted for it.

   Which outcome applies is decided by diagnostic *reads* — never by
   attempting the rename — and, as with `st_nlink`, each reports what was
   observed at that instant and claims nothing beyond it.

   **No raw-move guidance.** The message must not tell the operator to move
   the record back. A manual `mv` is the same parcel-label operation this
   amendment forbids OneOS from performing: it resolves a name at the instant
   it runs, can move something other than the reviewed record, and can
   overwrite whatever holds the destination. Recovery has to be a verified
   procedure that binds identity and contents before it moves anything.

   S7 does **not** build one. Adding a restore command would turn a safety
   fix into a new destructive surface needing its own design and review. So
   the messages say plainly that no automated recovery is available and that
   the operator should escalate, rather than naming a procedure that does not
   exist or implying one is available here.

   This closes the race immediately, at the cost of turning an ordinary
   transient Git failure into manual recovery. Stage 2 exists to make that
   rare.

   *Stage 2 (implemented).* Quarantining becomes the
   **last** mutation, after the commit succeeds. No proposal has been
   quarantined when rollback runs, so no rollback of quarantine is attempted.
   Stage 1 remained the safety boundary until Stage 2 landed; its unreachable
   protections were retired in the same change, never in advance.

#### Stage 2: committed action receipts *(Amendment 3)*

Quarantine-last removes the unsafe restoration problem but creates a new
double-action boundary. If the commit succeeds and quarantine then fails, the
proposal remains in the live outbox even though its action is already in Git
history. A lock closes this window only while the process is running. OneOS
needs durable memory that survives the request, a process crash, and restart.

Deriving that memory from commit messages is rejected. Approval messages name
the proposal id, while registry-delete messages do not, and prose is not a
uniform authority. Stage 2 instead commits a small action receipt in the
**same commit** as every approval and registry deletion. This is the entire
source of its revert property: a receipt created before or after that commit
could disagree with the action and is not acceptable.

Receipts live beside quarantine bookkeeping, outside curated knowledge, at:

```text
<entity>/outbox/.receipts/<proposal-id>.yaml
```

`outbox/` is already excluded from curated reference counting and block
mapping. Entity-local storage also confines an unreadable store's availability
impact to that entity, though a single-entity vault still loses every action
when its store root cannot be resolved safely. `.receipts/` is tracked;
`.consumed/` remains untracked. Their opposite semantics are deliberate and
must be asserted so that an `outbox/.*` ignore rule cannot silently omit a
receipt from its commit.

The receipt schema is closed and minimal:

```text
version
proposal_id
review_sha256
action kind: approval or registry deletion
```

Entity and time come from the path and commit metadata. A commit oid cannot be
stored inside the commit that creates it. Paths, slugs, targets, and summaries
are deliberately absent: the Git diff remains the detailed action audit and a
second description could drift.

Two fields are load-bearing. The proposal itself is an untracked owned change
and its bytes never enter the approval or registry-delete commit, so
`review_sha256` is the only tracked evidence of which reviewed proposal state
was consumed. It is **audit-only and must never be read for an eligibility
decision or compared with a later pending record**. `action kind` is the only
safe source for the card's conditional triage or registry guidance, because a
receipt-backed pending record is deliberately never parsed.

Receipt identity is content-bound, not name-bound. The reader reuses
`require_proposal_identity(path, record_id)` so the scan-validated filename
stem and the parsed `proposal_id` must agree. A file merely named for an id is
the parcel label again; a filename/content mismatch is refused, never trusted.

##### HEAD authority, absence, and lookup cost

The current Git `HEAD` tree is receipt authority, never the working-tree copy.
Deleting a tracked receipt only from the working tree therefore cannot
re-enable an id. Approve and registry delete perform an O(1) lookup for their
validated id in a `TransactionPlan` precondition under the approval lock,
after expected-state validation and before any mutation. Reject has no
`TransactionPlan`, so it performs the same `HEAD` lookup explicitly inside
`consume_reviewed_proposal`'s own locked section. Receipt presence stops all
three actions regardless of action kind or whatever bytes occupy the proposal
name.

Projection cost is tied to current work, not the vault's lifetime. It gathers
the currently pending, scan-validated ids and resolves them from `HEAD` in one
batched object read, such as `git cat-file --batch`; it does not spawn one Git
process per id and does not enumerate the accumulated store in the request
path. For each current id:

1. a valid matching receipt produces the spent, controls-withheld state
   without opening, parsing, hashing, comparing, or describing the pending
   proposal;
2. an absent matching receipt means the id is unspent and the ordinary strict
   proposal reader applies; and
3. a malformed matching receipt disables that id with `E-RECEIPT`, while
   independently resolved ids remain available.

Historical receipts with no pending proposal are inert by construction. Every
decision is keyed on a pending or acting id; proposal ids are freshly generated
and never reissued. If an old receipt ever became matching, the per-id rule
would catch it at exactly the moment it became relevant. Request-path omission
is therefore safe, not an assumption that old data remains well formed.

The complete accumulated store is nevertheless a durable audit record. Stage
2 adds a read-only offline validator, following the existing public and Gate 3
audit pattern. It enumerates `.receipts/` in `HEAD` and validates every entry's
schema version, filename-to-id binding, digest shape, and action kind. O(store)
work is correct there and **only** there. It reports failures and never removes,
rewrites, or repairs a receipt. The request path protects current decisions;
the offline validator protects accumulated audit integrity. Neither subsumes
the other.

Git cannot represent an empty directory, so receipt-store absence has one
forced exception to the otherwise fail-closed design:

- no `.receipts/` tree in `HEAD` is a valid empty store;
- no matching receipt in a valid store means the id is unspent;
- a matching malformed receipt disables that id; and
- a `.receipts` root that exists as anything other than a Git tree, or a
  `HEAD` tree that cannot be read, blocks every action for the entity.

The first rule is the design's **only fail-open**. It is forced by Git's data
model rather than chosen: treating an absent empty tree as corruption would
make every fresh vault unable to act. A committed deletion of the tree is the
authorized-bypass case already outside the threat model. The wrong-object-type
check is essential: without it, a `.receipts` blob would make every child
lookup look absent and silently turn the forced exception into an entity-wide
fail-open.

##### Transaction order and crash boundary

Approval and registry deletion follow this sequence under the existing
approval lock:

1. validate the reviewed proposal, its expected state, and all existing
   action-specific preconditions;
2. look up the validated proposal id in the verified current `HEAD` receipt
   tree and refuse before mutation if any matching receipt exists or cannot be
   resolved safely;
3. add the receipt as a normal filesystem `change` and exact `commit_path`;
4. commit the action paths and receipt together, verifying that the receipt
   entered the exact staged and committed path set; and
5. quarantine the proposal as the final mutation while the lock is still
   held.

A receipt is **not** an `owned_change`. That term has an existing, enforced
meaning: owned paths must be untracked, while a receipt is intentionally
tracked. Declaring it owned would make the transaction refuse itself.

Any pre-commit failure follows the ordinary transaction rollback and creates
no committed receipt. Once step 4 succeeds, the action and receipt remain
committed. Stage 2 never rolls them back merely because step 5 cannot complete
or cannot verify the exact reviewed record at its quarantine location. It
performs no rename-back. The receipt is the durable interlock that prevents a
second action.

A process crash after the commit but before quarantine is safe on restart:
the receipt already exists in `HEAD`, so projection withholds controls and
every action's locked service check refuses the id. A crash after quarantine
likewise leaves either an absent projected proposal or a receipt-blocked id.
This restart property is the headline guarantee that justifies quarantine-last
rather than an implication left to timing.

##### Stage 2 outcomes

Incomplete or unverifiable **consumption** after a successful commit has one
outcome because every member supports the same facts and operator action. This
is different from the Stage 1 decision not to collapse `E-RETAINED` into
`E-STRANDED`: those outcomes asserted different files and locations. Collapse
is correct only when every claim is true for every member and the operator's
next step is identical.

The Stage 2 contract is:

| field | value |
| --- | --- |
| code | `E-APPLIED` |
| tier | `committed` |
| severity | `attention` |
| committed | `yes` |
| retry | `stop` |
| page status | `500` |

Message, verbatim: "The action completed, but OneOS could not verify that its
proposal was safely consumed. Its receipt prevents this proposal ID from being
used again. Do not retry or move files by hand. Inspect vault state with git
status."

`E-APPLIED` always means **committed**. This needs an explicit maintainer
warning because `applied_changes`, `on_applied`, `filesystem-applied`, and
`filesystem-path-applied` use *applied* for a pre-commit phase in the same
transaction module. The operator taxonomy uses the ordinary meaning: the
action took durable effect.

This outcome covers failures of consumption itself. If consumption was fully
verified and only a later cleanup failed, existing `E-COMMITTED` remains
truthful: "only the cleanup afterwards failed." `E-QUARANTINED` also remains
live for reject, which consumes without a Git commit and can still finish
consumption before its lock cleanup fails.

A malformed matching receipt has a distinct contract because `E-INVALID`,
`E-UNREADABLE`, and `E-TAMPER` each give false attribution or unsafe guidance:

| field | value |
| --- | --- |
| code | `E-RECEIPT` |
| tier | `integrity` |
| severity | `attention` |
| committed | `no` |
| retry | `stop` |
| page status | `500` |

Message, verbatim: "OneOS found an invalid action receipt for this proposal
ID. It cannot safely tell what completed action the receipt represents, so the
ID is disabled. Do not retry, and do not move or delete files by hand. No
automated recovery is available. Inspect vault state with git status and
escalate for verified recovery."

`committed` describes the **current request**, not all of history. At render
time no action was attempted; at the locked service check the current action
was refused before mutation. Both truthfully report `committed=no` even though
the message discusses a receipt that may represent an earlier committed
action. Stating that scope is necessary because this is the first outcome
where the field and message visibly describe different actions.

##### Revert, consumption, and accumulation

A revert undoes the action and removes its receipt in the same revertible diff.
It never restores a record from quarantine. Consumption, not action, makes a
proposal spent:

- after reverting a fully successful action, the receipt is gone but the
  consumed proposal remains in `.consumed/` and does not project;
- if the original commit succeeded but quarantine never did, reverting removes
  the receipt while the exact unconsumed record remains in the outbox, so it
  becomes actionable again under the ordinary content-bound rules; and
- no mechanism revives a quarantined record by name. Repeating consumed work
  requires a newly generated id and fresh review.

This codifies existing revert behavior: revert tests require restoration of
the committed source/destination or registry state, not resurrection of the
untracked proposal record.

Both tracked receipts and untracked quarantined records accumulate. This is an
accepted operational cost, not hidden cleanup debt. Pruning either store is a
destructive operation on audit evidence and is outside S7 until separately
designed and reviewed.

##### Stage 1 retirement

When quarantine-last is implemented, no proposal has been quarantined when
transaction rollback runs. `diagnose_quarantined_record` and its only two
specific products, `E-RETAINED` and `E-STRANDED`, then become unreachable.
They, their domain types, exact taxonomy mappings, and executable tests are
removed **only in the same change** that makes quarantine-last real. The
mutation-ledger rows M18 and M19 are marked **RETIRED** with the reason, rather
than silently deleted. `E-SUBSTITUTED` remains reachable through reject.

Retirement is proved, not inferred from a call graph. After quarantine-last
lands, deliberate mutations of each former producer must leave the behavioral
suite green — the campaign runner's `ALIVE` result used intentionally as
evidence that the old path no longer participates. A new structural guard
also requires every live taxonomy outcome to have a reachable producer, the
outcome analogue of the duplicate-definition and inert-control sweeps. This
closes the gap between a runner that detects unanchored mutations and a
taxonomy that previously could retain unproducible states.

#### What quarantine costs the working tree *(Amendment 1)*

A consumed record is retained, so it remains an untracked file in the vault.
`git status` is therefore no longer empty after an approve, reject or
registry delete, where a deletion left it empty. This is a real, permanent
operational consequence of Amendment 1, accepted alongside accumulation, and
it is recorded here rather than absorbed silently into test helpers.

What it does **not** weaken is the S5 guarantee itself, which is preserved
exactly and must stay asserted in that exact form:

- approve still produces **exactly one commit**, and reverting it still
  restores both paths;
- the Git **index**, **HEAD**, and every **tracked** path are untouched
  except by that one commit; and
- unrelated staged, unstaged and untracked state is byte-identical
  afterwards.

The only permitted difference is the presence of records at
`<entity>/outbox/.consumed/*.yaml`. Verification may exclude exactly that
path shape and nothing broader: a filter that hides any path merely
containing `.consumed` would conceal real regressions elsewhere in the vault.

Quarantined records are invisible to every listing, and **no reviewed action
can reach them**: they are never approved, rejected, deleted, re-proposed, or
re-fingerprinted. They are evidence, not pending work.

This is not a claim that they are untouchable forever. The separately
sequenced reclaim is a different, explicit operation with its own review — it
acts on quarantine deliberately, which is precisely why it is out of S7's
scope rather than a hidden part of it. What S7 guarantees is narrower and
checkable: nothing in the approve, reject or registry-delete path can consume
a record twice.

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
3. no old action control remains — the container is emptied and labelled,
   not repopulated with disabled stand-ins;
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
- Within the supported managed-directory boundary, a proposal that resolves
  cross-scope is never moved. OneOS may direct the operator to a safely
  identified workspace, but must not disclose or guess a private path. S7 does
  not claim containment against the deliberate post-check ancestor relocation
  excluded by Amendment 5.

#### Moved-folder recovery guidance *(Amendment 5)*

When OneOS detects that a managed file or directory is missing, moved,
replaced, or redirected, the affected entity remains visible but read-only.
The response carries no review fingerprint and offers no approve, reject,
registry-delete, bulk, or other mutation control. It uses the existing
`E-TAMPER` surface and tells the operator to:

1. stop OneOS and every connected writer;
2. restore an internal managed file or directory to its canonical location;
3. if the **whole vault** intentionally moved, update `ONEOS_VAULT`, restart
   OneOS, and rerun the trusted local verification gates;
4. never substitute a symlink for a managed directory; and
5. not retry while the warning remains.

OneOS does not search the disk for a guessed replacement, follow a newly known
external location, create a symlink, move content back automatically, or delete
anything. “Remove from OneOS” means configuration-only removal, but building a
configuration-removal control would add workspace CRUD and is explicitly not
part of this amendment; S7 provides guidance only.

If a directory relocation is detected only after an action may have begun,
OneOS reports the existing conservative committed/unknown outcome appropriate
to that action and tells the operator not to retry until vault state is
inspected. It never reports a definite rollback merely because the canonical
name is missing.

### Receipt-backed spent state *(Amendment 3, Stage 2)*

A valid receipt short-circuits proposal handling. The card is rendered from
the scan-validated proposal id, receipt action kind, and a newly server-minted
issuance only. It carries no proposal digest and OneOS does not open, parse,
compare, summarize, or mention the pending record's contents.

The card has no action button, fingerprint, reconfirmation flow, or `Check
again`. Its element id is keyed by `(scan-validated proposal id,
server-minted issuance)`, never a raw filename fragment or receipt digest. The
normal copy names the action without attributing it to whatever currently
occupies the id:

- approval: "An approval has already completed for this proposal ID, so it
  cannot be used again."
- registry deletion: "A registry deletion has already completed for this
  proposal ID, so it cannot be used again."

When a real pending-record leaf is established without opening or parsing it,
the spent card also says, verbatim: "A record with this ID is still present.
OneOS will not act on it. Do not move or delete it by hand." Projection of a
pending row establishes that fact by construction. Direct action and refresh
responses may also render the card after the record was fully consumed; those
responses omit the sentence when a no-follow metadata check cannot prove a
real leaf is present. That optional presentation evidence never overrides the
receipt in `HEAD` and never authorizes an action. OneOS cannot distinguish a
failed consumption from a later recreated id without reading the record,
which this design forbids, so whenever the sentence is shown the single
conservative instruction is the only honest one.

The affirmative path is conditional and read-only, reusing existing Console
wording rather than implying that completed work must be repeated:

- "If the item still needs classifying, start again from triage — that
  allocates a new proposal."
- "If the entry still needs deleting, start a new deletion from the
  registry."

The receipt action kind selects the applicable destination. If the matching
receipt is malformed, `E-RECEIPT` replaces the normal spent card: the id is
disabled, but no link is guessed because the action kind cannot be trusted.

When `E-APPLIED` is returned, the operator stays on the same screen: the alert
is shown and the acted-on card is replaced with this receipt-backed state.
Because `E-APPLIED` has HTTP status 500, this behavior depends on the HTMX
configuration in `templates/_head.html` that enables swapping for `[45]..`
responses. Unlike the 200-status `E-REVIEW` flow, it would silently stop
swapping if that configuration were removed, so the configuration and the
actual rendered retarget/OOB ids are acceptance-tested together. Later page
loads render the same spent state for as long as both the receipt and a record
with that id remain.

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
- no old control survives a conflict, and none is invented in its place;
- current values and meaningful differences render on the same screen;
- `Check again` is read-only; and
- missing-proposal recreation uses the existing triage action and a new id.

Stage 2 additionally proves:

- approval and registry deletion commit the receipt and action in exactly one
  commit, with the receipt in both `changes` and the exact `commit_paths` and
  never in `owned_changes`;
- omitting, ignoring, failing to stage, or failing to commit the receipt makes
  the action fail rather than silently weakening revert behavior;
- approve and registry delete check the matching `HEAD` receipt in a locked
  transaction precondition, while reject performs the same check inside its
  own locked consumer;
- a worktree-only receipt deletion cannot re-enable an id because `HEAD`, not
  the working tree, is authority;
- valid receipt presence short-circuits proposal opening and parsing for the
  same id, including same bytes, changed bytes, malformed bytes, redirected
  leaves, and recreated ids;
- an absent receipt tree is a valid empty store, an absent matching receipt is
  unspent, a malformed matching receipt yields `E-RECEIPT` for that id, and a
  non-tree receipt root blocks the entity;
- a crash boundary after the commit and before quarantine leaves a `HEAD`
  receipt that withholds every control on restart;
- every incomplete or unverifiable post-commit consumption path yields
  `E-APPLIED`, preserves the commit and receipt, performs no rename-back, and
  exposes no second action;
- a verified consumption followed only by cleanup failure remains
  `E-COMMITTED`, and reject's completed consumption followed by cleanup failure
  remains `E-QUARANTINED`;
- reverting a fully successful action removes the action and receipt but does
  not restore the quarantined proposal, while reverting an action whose
  quarantine never happened re-enables its still-unconsumed record;
- the spent card contains no digest, proposal values, mutating attribute,
  reconfirmation, or `Check again`; its ids and HTMX selectors name actual
  issuance-keyed markup, and its conditional link follows only the verified
  receipt action kind;
- malformed matching receipts render the link-less `E-RECEIPT` state without
  disabling independently resolvable ids; and
- the offline validator enumerates the entire `HEAD` receipt store read-only,
  catches every schema, binding, digest, action-kind, and wrong-object-type
  defect, and performs no repair.

### Mutation-tested evidence

For each action family, verification deliberately disables or bypasses the
fingerprint comparison and runs the smallest test selection that must detect
the change. The test must fail for the expected reason. The exact implementation
is restored, its bytes are verified against the saved before-state, and the
same selection must pass.

Mutation checks also cover a transport break: remove the fingerprint from one
button or service call and prove the structural/route test fails. A test that
does not execute the changed path is not accepted as a control.

Stage 2 mutation evidence separately breaks: receipt inclusion in the exact
commit, `HEAD` authority, the under-lock precondition for each action family,
reject's standalone check, receipt-first no-parse projection, the non-tree
store-root refusal, `E-APPLIED` preservation, the 500-response HTMX swap, and
the offline validator's read-only full-store checks. Each row binds its own
exact node and diagnostic under the campaign runner's existing RED-then-GREEN
rules.

The former Stage 1 producers for `E-RETAINED` and `E-STRANDED` receive the
opposite reachability proof only after quarantine-last is implemented: their
mutations are deliberately `ALIVE`, the types and mappings are removed, and
M18/M19 remain explicitly retired historical ledger rows. A structural
producer guard prevents any later taxonomy code from surviving without a
reachable domain producer.

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

Stage 2 review also checks:

7. the receipt is in the same exact commit as the action and nowhere in the
   untracked-owned path family;
8. no receipt digest influences eligibility and no receipt-backed pending
   record is parsed;
9. all three services check current `HEAD` under their actual lock shape,
   including reject's non-transaction path;
10. every post-commit/pre-quarantine failure remains double-action-safe after
    restart; and
11. retired outcomes and ledger rows are demonstrably unreachable rather than
    merely unreferenced by inspection.

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
   OneOS never deletes or overwrites proposal content to win or clean up a
   race, and an unavailable primitive fails closed instead of degrading to a
   destructive one. Where another writer makes preservation unknowable, the
   outcome says so and no unsafe restoration is attempted.
5. Registry delete still refuses newly introduced references.
6. `Check again` is read-only and no recovery path reuses a stale id.
7. Existing S4 source freshness, S5 isolated transaction/audit, and S6 visible
   outcome guarantees remain intact.
8. Focused mutation controls go red, restored controls go green, the full
   public suite passes, and Grey Matter remains byte-identical.
9. Independent review closes every finding.
10. Approval and registry deletion commit a valid receipt in the same exact
    commit as the action; reverting that commit's effect removes both without
    reviving a quarantined proposal.
11. A valid matching receipt prevents projection and all three locked service
    paths from reading or acting on any record carrying that id, regardless of
    its bytes.
12. A post-commit crash or consumption failure cannot expose a second action:
    `HEAD` retains the receipt, the same-screen response renders
    `E-APPLIED`, and later page loads show the controls-withheld spent state.
13. Receipt absence, per-id corruption, store-root corruption, and worktree
    deletion follow the exact authority and fail-closed rules above, including
    the forced empty-store exception.
14. The complete `HEAD` receipt store passes the read-only offline validator;
    no request-path operation enumerates the accumulated store.
15. `E-RETAINED`, `E-STRANDED`, `diagnose_quarantined_record`, their executable
    tests, and their live campaign claims are retired only after evidence
    proves quarantine-last made them unreachable; every remaining taxonomy
    code has a reachable producer.
16. ~~Linux `renameat2(RENAME_NOREPLACE)` is exercised on a real Linux host.~~
    **Withdrawn as a completion condition** by product-owner decision on
    2026-08-25; recorded instead as a known Linux limitation (see "Known
    Linux limitation" above). macOS `renameatx_np(RENAME_EXCL)` has been
    exercised; the Linux path has not, and is documented as unverified rather
    than claimed.
17. *(Amendment 5)* A detectable moved, replaced, missing, or redirected
    managed path leaves the affected entity visible and read-only, with no
    fingerprint or mutation control. `E-TAMPER` gives the approved restore or
    whole-vault reconfiguration guidance, symlinks remain refused, and no
    configuration-removal control or automatic recovery is added. The spec
    makes no containment claim against deliberate ancestor-directory
    relocation after the final check by an actor that bypasses the required
    action-lock coordination boundary.

Only after these criteria pass may S7 be called complete. S7 does not itself
authorize live gate trials until the separately sequenced inherited items 2–4
are also resolved.
