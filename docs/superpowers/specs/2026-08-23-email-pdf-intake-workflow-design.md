# OneOS Email and PDF Intake Workflow Design

Date: 2026-08-23

Status: public slices implemented with synthetic fixtures. Live mailboxes,
private parsers, and Grey Matter secrets remain outside this repository.

Recorded in this repository as Phase 3. The public slices do not authorize a
live mailbox, a private parser, or a change to the Safety Foundation.

## Purpose

Design a safe, low-effort workflow for receiving emails and PDFs, retaining the
useful document and a short knowledge record, and eventually removing processed
email from the provider without giving automation deletion authority.

This is future architectural work. It is separate from S7, does not widen S7,
and does not authorize a new screen or implementation inside the current Safety
Foundation. The Safety Foundation and the existing Phase 1 gates must pass
before any live mailbox trial.

`AGENTS.md`, `BUILD.md`, the authoritative vault conventions, and the existing
phase sequence continue to govern implementation. Where this design describes
future capability, it does not change those authorities.

## Product outcome

An operator can connect several receiving email addresses. Each address maps to
one OneOS entity and one fixed external archive location. Hermes monitors the
mailboxes and prepares work in the background. OneOS presents only the decisions
that require human judgement.

For a successfully handled PDF, the system permanently retains:

- one verified unlocked PDF in the restricted external archive;
- one small PII-redacted Grey Matter receipt;
- an approved short summary when one is useful;
- essential metadata, the unlocked PDF fingerprint, and a safe archive
  reference; and
- a redacted audit trail of the approved actions and their outcomes.

It does not retain a full email copy or a full extracted PDF transcript. The
provider remains the source of the original email until the provider eventually
removes it from Trash.

## Safety interpretation for external actions

Git remains the reviewed authorization and audit trail. The archive and email
provider remain responsible for physical recovery of their own external state.

A Git revert cannot pretend to undo an archive copy or restore an email from a
provider. Reversal of an external action is therefore a new, explicit recovery
action: restore the provider message, recover a quarantined file, or perform an
authorized archive correction. That recovery receives its own audit record.

Every external action must still have a redacted, exact authorization before it
runs and a verified outcome afterward. The authorization identifies the target
through a safe opaque identifier; protected runtime state resolves that identifier
to the provider message or archive operation.

This external-action interpretation applies only after a separate implementation
authorization. Until then, every current side effect still requires a revertible
Git diff.

## Responsibilities

### OneOS and Command Center

OneOS is the decision-maker. Command Center owns:

- the immutable entity and mailbox scope for each request;
- rule matching and conflict handling;
- operator permissions;
- naming, summary, archive, and cleanup review states;
- rule creation, revision, and pausing;
- exact authorization of archive, Trash, and recovery actions; and
- redacted Git-backed receipts, proposals, approvals, and outcomes.

OneOS never trusts an entity, mailbox, destination, rule, or provider message
identifier supplied by the browser. It resolves them from server-owned scoped
state.

### Hermes

Hermes is the asynchronous worker. It may:

- monitor several IMAP mailboxes independently;
- retrieve a message only when work requires it;
- unlock and fingerprint PDFs;
- run approved extraction and redaction work;
- prepare richer summaries;
- copy and verify approved archive files; and
- carry out the exact provider Trash or recovery action authorized by OneOS.

Hermes cannot approve a summary, change a learned rule, choose between conflicting
rules, or grant itself deletion authority. It reports results; OneOS decides what
those results permit next.

### Information stores

- The email provider retains the original message until the reviewed Trash
  action and controls the provider's recovery window.
- Protected temporary storage holds unlocked PDFs awaiting naming, retry, or
  archive verification. It is outside Git and Grey Matter and is cleared after
  successful reconciliation.
- The restricted external archive holds verified unlocked PDFs. It may be local
  initially and cloud-backed later.
- Protected secret and runtime storage holds mailbox credentials, remembered PDF
  passwords, exact provider identifiers, and instance-specific mailbox settings.
  None of these values appears in the public repository or Grey Matter.
- Grey Matter holds only the redacted receipt and approved short summary, along
  with essential safe metadata, fingerprints, stable source references, and
  redacted rule or action identifiers needed for audit.

Public source code contains only generic schemas and behavior. Mailbox-to-entity
and mailbox-to-archive values are read from protected runtime configuration and
validated against runtime registries; they are never hardcoded.

## Scope and access

Each receiving address has:

- one fixed OneOS entity;
- one fixed external archive location;
- a separate mailbox identity and connection;
- a separately enforceable operator permission; and
- its own tab within the authorized entity scope.

Access to an entity does not by itself reveal every mailbox, PDF, or password
associated with that entity. This boundary exists even when the first deployment
has one trusted operator.

Each Hermes job is bound to one immutable mailbox and entity. It cannot read,
archive, propose, render, trash, or restore work belonging to another scope.

## Email and document identity

Each distinct provider message is one email case. Reprocessing the same provider
message is a no-op.

Each PDF attachment is a separately tracked document within its parent case. A
parent email cannot become ready for cleanup until every PDF and every other
attachment has reached an accepted final state.

The fingerprint is computed from the verified unlocked PDF. When a later email
contains an identical fingerprint, OneOS reuses the existing archive file. The
new email still receives its own redacted receipt and review state, marked as a
duplicate or resend.

Deletion and recovery always target one exact provider message, never an entire
conversation thread or every message that happens to share a subject.

## End-to-end workflow

### 1. Observe and trust-check

Hermes observes each mailbox independently and supplies the exact message to the
scoped intake boundary. OneOS checks the claimed sender and any trust result made
available by the provider.

An unverified or suspicious sender always goes to manual review. OneOS does not
apply a learned rule, try a remembered PDF password, archive an attachment, or
make the message ready for deletion in that state.

### 2. Create the deterministic receipt

OneOS produces a small deterministic PII-redacted receipt immediately. This is
the sole intake exception to the outbox rule:

- it is created in the entity's `00-inbox/active/` location;
- it carries `sub: triage`;
- it is the only file in its own `ingest:` commit; and
- raw email text and attachment contents never enter Grey Matter or Git.

The receipt may show a safe pending or failure state. It must not claim that a
file is archived until the archive copy has been verified.

### 3. Match an approved rule

A rule begins with the receiving mailbox and verified sender. It may add approved
conditions such as subject wording, document type, or expected attachments.

If no rule matches, the email waits for manual review. If more than one rule
matches, automation stops and shows the competing matches. Rule order never
silently resolves a conflict.

### 4. Handle PDFs

For each PDF:

1. A trusted matching rule may ask protected secret storage to try the remembered
   password for that sender and document type.
2. If unlocking fails, the operator is asked whether the password changed.
3. A new password replaces the protected remembered password only after it
   successfully unlocks the PDF. It is never displayed back to an operator or
   written to Git, Grey Matter, a filename, or a log.
4. The unlocked file is fingerprinted and verified in protected temporary
   storage.
5. An exact existing fingerprint reuses the existing archive object.
6. A known approved naming rule may complete the archive action automatically.
   A first-time document waits in protected temporary storage for manual naming
   and naming-rule approval.

The first manual filename becomes the example from which OneOS proposes a
reusable naming pattern. The operator sees example future names before approving
the pattern. A collision between different files receives a unique suffix and
never overwrites an existing file.

### 5. Extract useful text

Extraction is rule-based and tiered:

- an approved document rule chooses an available local parser when that parser
  is known to handle the document reliably;
- documents without a suitable local parser are passed to Hermes for heavier
  asynchronous extraction or text recognition; and
- uncertain or failed extraction returns to operator review.

Full extracted text is temporary. Only the approved short summary and essential
safe metadata are retained. No LLM or other judgement call enters the request
path.

### 6. Handle other attachments and emails without PDFs

A non-PDF attachment must be explicitly dismissed or covered by an approved
rule for that attachment type. An unaccounted attachment blocks cleanup.

An email without a PDF follows its approved sender, mailbox, and optional-detail
rule. That rule may:

- ask Hermes to prepare a short summary;
- state that no richer summary is required; or
- place the email on the cleanup review list after the mandatory receipt exists.

The last action never moves the message to Trash by itself.

### 7. Prepare and review the richer summary

Hermes may combine the PDF's useful content with email text that changes its
meaning, such as a deadline or requested action. Signatures, quoted history,
tracking text, and unrelated conversation are excluded and redacted.

The operator may approve the proposal, edit and approve it, explicitly skip it,
or rely on an already approved rule that skips richer summaries for matching
future messages. The deterministic receipt is never optional.

An approved richer summary changes Grey Matter only through the outbox and an
approved Git transaction.

### 8. Become ready for cleanup

An email becomes ready for cleanup only when:

- every PDF has a verified archive result or approved duplicate reference;
- every other attachment is accounted for;
- the richer summary is approved or explicitly skipped;
- no scope, trust, rule, Git, archive, or provider error remains; and
- the exact provider message is still available.

A learned rule may automate every preparation step but can only place a message
on the review list. It never gains Trash authority.

### 9. Review and move to provider Trash

The operator may select individual rows, select all visible rows, deselect all,
or filter by approved criteria. Any bulk or criteria-based selection freezes the
exact reviewed message list. New arrivals and later state changes are not added.

The same authorized operator may perform the separate confirmation step. After
confirmation, Hermes moves only those exact individual messages to the provider's
recoverable Trash. It records success or failure per message.

### 10. Recover from Trash

While the provider retains a message, the operator may select it and choose
**Recover email**. Hermes returns the exact message to the inbox.

Recovery does not remove the verified archive file or approved Grey Matter
record. It returns the email to manual review and offers three separate choices:
keep the matching rule, revise it, or pause it. Recovery never silently retrains
or disables a rule.

## Operator experience

This future workflow appears only after its build phase is separately authorized.
Within the current entity, each permitted receiving address has its own mailbox
tab.

A mailbox tab separates ordinary intake work from cleanup work. Items needing
attention include unknown or suspicious senders, conflicting rules, changed
passwords, first-time naming, unaccounted attachments, failed archive attempts,
and summaries awaiting review.

Each PDF has a document card with two primary actions:

- **Open file**
- **Open folder**

The actions resolve through a stable, location-independent archive reference.
Local archives open the PDF or file manager. A future cloud archive resolves the
same reference to a secure file view or web archive location. Grey Matter never
contains a vault symlink to the archive.

### Email cleanup tab

One consolidated **Email cleanup** tab has two sections:

- **Ready to delete** contains the frozen pre-Trash review list and its one bulk
  confirmation.
- **Deleted** contains redacted records of messages moved to provider Trash and
  supports multi-select recovery to the inbox.

The tab supports sorting and filtering by date, sender identity, receiving
mailbox, matched rule, number of prior confirmed actions, and last occurrence.
Its default explainable order puts rare, inconsistent, or unusual cases at the
top and repeatedly confirmed routine cases lower down. This ranking changes only
presentation; it never grants deletion authority.

All redacted deletion records remain visible. **Recover email** is enabled only
while the provider still retains the exact message. After provider removal, the
row remains as history and clearly states that recovery is no longer available.

## Learned-rule contract

A learned rule is created or changed only after an operator approves a real
example. It may record:

- receiving mailbox and verified sender identity;
- optional subject or document-type conditions;
- expected PDF and other-attachment handling;
- parser or Hermes extraction choice;
- whether to prepare or skip a richer summary;
- naming pattern and collision behavior;
- redaction behavior; and
- an opaque reference to a protected password.

Rule material written to Git is PII-redacted or safely fingerprinted. Protected
matching values and secrets remain outside Git and Grey Matter; Git records their
opaque rule version and integrity fingerprint so an unreviewed change is visible.

Rules are versioned and auditable. A recovered misclassification prompts rule
review. A changed parser, matcher, password reference, naming pattern, or action
requires explicit approval before becoming the new rule version.

## Failure and reconciliation rules

Failures are visible in OneOS and never silently swallowed.

- If the archive is unavailable, OneOS keeps an **archive pending** state and
  retries safely. The provider email remains untouched and cleanup is blocked.
- A checksum mismatch, conflicting canonical copy, duplicate destination,
  missing file, or unaccounted item is a hard failure. An incomplete copy is
  quarantined rather than treated as archived.
- If an external archive action succeeds but its outcome is not recorded, OneOS
  reconciles the destination and fingerprint before retrying.
- A partial Trash batch records each message independently. Successful messages
  are not repeated; failed messages return to attention outside Trash.
- A missing or changed provider message blocks the action rather than widening
  the selection.
- Unknown outcomes stop later actions until reconciled.

No raw message, PDF content, extracted transcript, password, credential, or
provider secret appears in user-facing errors or logs. The operator receives a
safe explanation and the next permitted action.

## Verification

No live mailbox is used until the Safety Foundation and Phase 1 gates pass.
Initial verification uses disposable mailboxes, a temporary archive, synthetic
entities, and a synthetic vault.

Tests must prove:

- mailbox and entity scope cannot cross through reads, rules, rendering,
  archive paths, approvals, Trash actions, or recovery;
- forged or suspicious senders cannot trigger learned rules or password use;
- conflicting rules always stop automation;
- raw content and secrets never reach Git, Grey Matter, logs, filenames, or
  public configuration;
- duplicate provider messages are no-ops and duplicate PDF fingerprints reuse
  the verified archive object;
- filename collisions never overwrite;
- archive, Git, connection, and partial-provider failures reconcile without
  hidden or repeated side effects;
- frozen cleanup lists never absorb later messages;
- exact individual messages, not conversation threads, move to Trash;
- learned rules cannot acquire Trash authority;
- local and cloud archive implementations satisfy the same stable-reference
  contract; and
- recovery changes the provider message without damaging retained records.

Mutation testing must deliberately weaken or remove the critical checks for
scope, sender trust, fingerprint comparison, rule conflicts, deletion
confirmation, exact-message selection, and rule authority. The suite must fail
for each mutation.

An independent reviewer examines the eventual design-to-code match, privacy
boundaries, failure behavior, provider actions, and mutation-test evidence. A
passing suite written only by the implementer is not sufficient evidence.

## Non-goals

- The public implementation uses synthetic fixtures. A live mailbox
  connection, a private parser, and a Grey Matter secret are not authorized
  by this document.
- The workflow does not belong to S7 and does not widen the current Safety
  Foundation.
- Learned rules never move messages to Trash automatically.
- Permanent provider deletion and provider Trash-emptying are not OneOS actions.
- Full email archiving, full extracted-text retention, email composition,
  replies, and general mailbox administration are excluded.
- Non-PDF formats do not silently become a general file-ingestion system; each
  type requires an explicit handling or ignore rule.
- Cloud archive migration is not part of this design. The stable reference only
  preserves that future option.
- No instance-specific mailbox, entity, archive, sender, credential, or secret
  value belongs in the public repository.

## Phase placement

Phase 2 remains the finance-ingest pipeline and is separately unauthorized.
This design is Phase 3. It is not blocked on building Phase 2. The public
slices are in this repository. The current next expansion decision for
deployment remains the separate Step 11 approval.

The current email adapter commits a redacted summary of the message text and
a body reference. It collects attachment filenames and does not write them
into the receipt. It does not unlock or archive PDFs. The current folder
adapter may retain extracted PDF text on the receipt path. The public Phase 3
archive path keeps a full transcript out of its own receipt and leaves those
adapters unchanged. Official IMAP, monitored by Hermes, remains the connector
for a later live-mailbox slice, which is outside the public slices.

## Completion criteria

The future workflow is complete only when all of the following are true:

- each authorized mailbox remains isolated and maps to its configured entity and
  archive location;
- every intake produces only its redacted deterministic receipt in the isolated
  `ingest:` commit;
- every retained PDF is unlocked, fingerprinted, verified, collision-safe, and
  reachable through its stable archive reference;
- every attachment is accounted for before cleanup;
- every Grey Matter summary is approved through the outbox or explicitly
  skipped;
- cleanup always requires a frozen exact list and separate operator
  confirmation;
- Deleted history and provider recovery behave as designed;
- external failures are visible and reconciled without silent duplication or
  loss;
- required deterministic, failure-injection, isolation, mutation, revert, and
  independent-review gates pass; and
- the existing OneOS and Grey Matter safety suites remain passing.
