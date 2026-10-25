# Later connector and communications-router notes

Date: 2026-10-25

Status: product notes recorded; not part of the approved Phase 3 design; implementation not authorized

These notes do not revise
`docs/superpowers/specs/2026-08-23-email-pdf-intake-workflow-design.md`.
They do not authorize a dependency, a screen, a browser extension, a hosted
service, a live mailbox, or any other build. Official IMAP, monitored by
Hermes, remains the connector in that Phase 3 design.

## Local-first, hosted service later

The durable path is local-first. A later optional hosted service may replace a
local companion. SaaS packaging stays deferred in `PRODUCT-THESIS.md` until the
intake loop proves the need. The current repository stack still excludes an
npm build step.

A future local companion, if separately authorized, would hold credentials,
PDF processing, archives, and a user-supplied model key. It would not become
approval authority.

## Three nested scopes

1. **Core document engine.** Extracts, unlocks, parses, names, redacts, and
   archives files. It can be built independently and exposed inside OneOS
   through Hermes.
2. **OneOS email workflow.** This is the approved Phase 3 design: Hermes
   connects mailboxes; OneOS handles review, rules, summaries, and Trash
   approval.
3. **Future communications router.** Gmail pages, SMS, WhatsApp, Instagram,
   and other channels routing work into external apps. This is a later product
   phase. It is not part of the approved email and PDF design or S7.

## Browser extension as a later connector

A browser extension can act as a human-in-the-loop connector:

1. Read permitted information from a web app the operator has allowed.
2. Push a redacted task or message into OneOS.
3. Receive an approved action from OneOS.
4. Open the target app, show exactly what will happen, and let the operator
   confirm.
5. Perform the action and return evidence and status to OneOS.

Chrome content scripts and Native Messaging are the relevant platform
mechanisms. Permissions would be requested per website. Website interfaces
change, sessions expire, and pages can interfere with extension scripts, so an
extension is less reliable than an official API. It must not silently perform
a high-risk action.

If a later phase adopts an extension, its first use is capture into OneOS and
then a reviewed browser action. Official APIs remain the optional reliable
connectors. An extension does not replace Hermes and does not gain Trash
authority.

## Product distinctions worth preserving

These describe why the Phase 3 workflow exists. They do not add scope.

- Inbox cleanup that shows unusual messages first, freezes the deletion list,
  requires confirmation, and can recover from provider Trash.
- PDF handling that unlocks password-protected files, remembers passwords only
  in protected storage, renames, deduplicates, and archives.
- A repeated sender or document action becomes a reusable rule only after the
  operator approves a real example.
- A model may summarize and suggest. It cannot delete.
- Several receiving addresses can have their own tabs and one cleanup view.
- History can explain why a message was classified and how often that action
  was previously approved.

Existing products cover pieces of this. Shortwave offers summaries, filtering,
and bulk triage. Clean Email focuses on automated inbox cleanup. Gmail
One-Click Cleaner offers bulk cleanup, restore logs, suggestions, and multiple
accounts. Save Emails and Attachments archives Gmail attachments and can unlock
encrypted PDFs. Parsio extracts information from emails and PDF attachments
with rules or a model. The OneOS distinction is the combination of local
privacy, learned cleanup, secure PDF processing, and reversible deletion under
human approval. That market observation is a hypothesis, not a design
invariant.

## Boundaries

- No instance-specific mailbox, entity, archive, sender, credential, or secret
  belongs in this repository.
- No model call enters the request path.
- Learned rules never acquire Trash authority.
- Permanent provider deletion and emptying Trash are not OneOS actions.
