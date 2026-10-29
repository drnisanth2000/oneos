# Master view design

Date: 2026-10-29

Status: owner-approved on 2026-10-29. Built in four plans, in order:
(1) read-only master view, (2) reminders and Needs attention, (3) moves
and edits, (4) Calendar sync. Each plan ships on its own. Owner answers folded in on 2026-10-29:
Calendar permission on the Mac is OK; the popup edits the draft text.

## Why

Today OneOS opens on a "Wiring check" clock. To see anything real the
owner picks one entity in the sidebar, opens its Board, and switches the
grouping to find filed notes. With five entities (Neshlin Medtech,
Nambisons Softronix, Personal, Personal · Professor, Personal · Council)
nothing shows the whole picture, and nothing warns that a payment or a
filing is due tomorrow.

The owner wants one page that opens by default and answers, for every
entity at once:

- what needs my attention right now (deadlines, colour-coded);
- what is still a draft, what is waiting on someone else, what needs my
  review, and what is done;
- and lets them move a card to its next stage with one drag or one click.

A reminder must reach the owner's calendar at the right time with its
subject ("Pay HDFC credit card", Tue 4 Nov 11:00). Everything else about
a reminder (amount, account, how to pay) the owner handles by hand for
now.

## Decisions the owner made

These came from the 2026-10-28/29 conversation and the frozen mockup
(version 4).

1. **Stages.** Every note is in one of three stages: **Draft**,
   **Waiting**, or **Done**. Archive stays a folder move (`active/` →
   `archive/`), not a stage.
2. **Existing notes.** The 25 notes whose status is `active` start as
   **Done**. The 5 `draft` notes stay Draft.
3. **No new official fields.** Status, deadline and amount do not become
   new convention fields. A reminder is a simple snippet in the note.
   The existing free-text `status` front-matter carries `draft`,
   `waiting` or `done`.
4. **Home page.** OneOS opens on the master view, across all entities.
5. **Greeting.** The top of the page shows a greeting ("Good morning")
   and the current date and time.
6. **Needs attention strip.** Below the greeting, a row of compact cards,
   one per reminder that is due. Each card shows the subject on top and
   the entity chip and deadline underneath; cards are compact, not
   full-width.
   - Overdue: red, alarm-clock icon, sorted first.
   - Due within the next day: amber, bell icon, sorted next.
   - Later: grey, calendar icon.
   - Clicking a card opens that note's popup (below).
7. **Board below.** Four columns in this order:
   **Draft · Waiting · To review · Done**.
   - Every card shows its title, an entity chip, and a date (deadline or
     last change).
   - Cards are draggable between columns.
   - Entity chips above the board filter it to one or more entities.
   - A sort control sits next to the filters, and each column also has
     its own sort (Draft: updated; Waiting: deadline; To review: review-by;
     Done: recent).
   - To review cards carry a "Review by…" deadline.
   - Done shows recent items with a "Show all" link.
8. **Popup.** Clicking a card opens a centred popup (modal) over the
   dimmed page. It shows:
   - the title, entity chip, current stage, and where it is filed or
     proposed to go;
   - the draft itself, **editable**: either the proposal an agent
     (Hermes, or the existing classifier) suggested, or the note the
     owner drafted (owner decision, 2026-10-29);
   - a comment box;
   - four compact buttons: **Save as Draft · Waiting · To review · Done**;
   - for **Waiting** and **To review**, a reminder box at the bottom with
     an editable date, time, and subject, and a **Save** button.
   Clicking a stage button puts the card in that column.
9. **Your move is your approval.** A drag, or a stage button in the
   popup, is the owner's approval of that change, recorded as one change.
   There is no second approval step for the owner's own moves. Dragging a
   card out of To review files it, as approving a proposal does today.
   Dragging into Waiting or To review opens the reminder box first.
10. **Board stays on Review.** The single-entity Board keeps opening on
    its Review grouping (in-tray with pending counts). This design does
    not change that default.

## Goals

- One default page with a bird's-eye view of all entities.
- Deadlines are impossible to miss: colour, icon, and position.
- Moving a card to its next stage takes one drag or one click.
- A Waiting or To review reminder appears in the owner's calendar at the
  chosen time with its subject.

## Non-goals

- Paying anything, reading card or bank statements, or tracking amounts.
  Finance ingest (Phase 2) stays unauthorized; an amount typed into a
  comment is just text.
- Live mailboxes, Hermes deployment, WhatsApp/Telegram/Notion. A
  separate morning-inbox design (drafted on 2026-10-28, not yet in this
  repository) owns those; when its list produces proposals they appear in
  To review here.
- New convention fields, a new status vocabulary in `conventions.md`, or
  schema changes.
- Push notifications, email, or SMS reminders.
- Multi-user anything.

## Anti-goals (would make this a failure even if it works)

- A card moving, a stage changing, or a reminder being created without
  the owner's click or drag.
- A note silently disappearing because its status is not one of the three
  stages.
- Credentials, a calendar password, or an account token stored in OneOS
  or the vault to make reminders work.
- The page making a network call outside this Mac on page load.
- Breaking the existing single-entity Board, Triage, Outbox, or their
  approval protocol.

## Constraints

- Stack is fixed (`AGENTS.md`): FastAPI, Jinja, HTMX, Alpine,
  `alpine-morph`, vendored JS only, no build step, no new runtime
  dependency unless the owner approves one. Drag and drop uses the
  browser's native drag events, as the Board already does.
- Every vault write goes through the Command Center: the shared action
  lock, one commit per action, a receipt, and `git revert` undoes it
  (revert test). The S7 rule that an approval binds to the exact bytes
  the owner reviewed applies to every move made from the popup or by
  drag.
- The master view reads every entity's registry and lifecycle modules, so
  it "reads or interprets vault structure": the local private
  integration gate in `BUILD.md` runs before merge.
- Sensitive content rules are unchanged: `.sensitive/` stays denied, and
  the page shows only what the Board already may show.
- Values come from runtime registries and vault state, never copied from
  the mockup.
- The existing visual system (PR #45) applies: tokens, icons, light and
  dark, phone and tablet layouts.

## How it works (decided parts)

### Data on the page

- **Draft, Waiting, Done columns:** notes in each lifecycle module's
  `active/` folder, across all entities in `entities.yaml`, by their
  `status` value (`draft`, `waiting`, `done`).
- **To review column:** what the Board's Review grouping already shows
  for each entity: inbox items and pending proposals in the outbox.
- **Other statuses:** a note whose status is anything else is not hidden.
  The page shows a small "N notes with another status" link that lists
  them, so the owner can move them. *(My call, from decision 2 and the
  anti-goal; the owner did not choose the exact UI.)*

### `active` counts as Done

Ingest writes `status: active` on every receipt, and the existing Triage,
Outbox and Board approvals do not change `status`. So the master view
treats `active` as **Done**, now and for notes filed later through those
flows. The owner's 25 `active` notes therefore show as Done with no
migration commit, and no rewrite of ingest or the approval diff is
needed. A note moved to Done from the master view is written as `done`.
*(My call: it gives the owner's decision 2 without touching the protected
approval protocol, and stops the "another status" list from growing.)*

### Reminders and review flags

A reminder is a date, a time (local, Asia/Kolkata on this Mac) and a
subject, attached to one card. Reminders, and the "To review" flag on an
already-filed note, are kept in a small OneOS store in private state
(SQLite beside the authentication database), **not** written into the
note. Each entry is keyed by the card's identity (vault-relative path for
a note; proposal id or inbox path for an item not yet filed).

Why not a line in the note: a pending proposal binds the SHA-256 of its
source (S4/S7), so writing a reminder into an inbox note would make its
own proposal stale; and a reminder edit is scheduling, not curated
content, so it should not cost a vault commit. *(My call; the owner asked
only for "a simple snippet" and said what matters is the reminder at the
right time with its subject.)*

Consequences the builder must keep:

- Saving or editing a reminder writes nothing to the vault.
- When a card is filed or moved, its reminder follows it (the key is
  updated in the same request that performs the vault change).
- Moving a card to Done resolves its reminder; it leaves Needs attention.
- The store is included in the owner-run `backup` with the rest of
  private state.

### Getting reminders into the calendar

OneOS adds each reminder as an event in the Mac's own Calendar app, in a
dedicated calendar named "OneOS": title = subject, start = reminder time,
an alert at that time, and the note's OneOS link in the notes field. If
that calendar sits in the owner's iCloud account, the Mac's Calendar
syncs it to their iPhone. OneOS stores no calendar or iCloud credential.
The owner grants OneOS Calendar permission once on the Mac (owner
decision, 2026-10-29).

- Saving, editing, or moving a reminder updates its one event; moving
  the card to Done, or deleting the reminder, removes the event.
- The app runs in a container and cannot reach Calendar itself, so a
  small host-side step on the Mac (beside `tools/local_service`) does the
  Calendar writes. It reacts to changes in the private reminders store
  rather than polling on a timer, and only while the owner is logged in.
- Events are found again by a stable OneOS id stored in the event, so a
  resync never duplicates them, and the step only ever touches the
  "OneOS" calendar.
- Nothing about a reminder needs a route without sign-in.

### Moves

| From → To | What one click or drag does |
|---|---|
| To review (has a proposal) → Draft / Waiting / Done | Approves that proposal (files the item exactly as today) and sets `status`. One commit. |
| To review (bare inbox item, no proposal) → any stage | The popup first asks for the destination, using the same module/sub choices as Triage's manual destination; then propose-and-approve happens as one action. A drag of such a card opens the popup instead of moving. |
| Draft ↔ Waiting ↔ Done | Sets `status`. One commit. |
| Filed note → To review | The note stays where it is filed and keeps its status; OneOS adds a "To review" flag with its review-by reminder in the private store. No vault commit. Moving it on to a stage clears the flag. |

Every vault change above goes through the existing protocol unchanged:
the request creates the proposal record bound to the bytes the popup
showed, and approves it in the same action, leaving the usual receipt and
one commit. No new approval path and no change to S7. *(My call.)*

### Editing the draft in the popup

The popup shows the note's body in a plain-text editor (Markdown as
stored). Front-matter is not in the editor; the stage buttons own
`status`, and the other required fields stay untouched. *(My call, so an
edit cannot break the policy enforcer's required fields.)*

- Any stage button, including Save as Draft, saves the edited body
  together with the stage change and comment, as one action and one
  commit, bound to the bytes the popup opened with. If the note changed
  meanwhile, the save is refused and the popup shows the newer text.
- For a To review item with a pending proposal, an edit changes the
  proposal's source bytes, so the edit, a re-issued proposal, and its
  approval happen in the same action.
- Closing the popup without a button discards the edit; nothing is
  written.

The comment, if any, is added to the note as one dated line in the same
commit, e.g. `Comment (2026-10-29): Pay ₹12,400 from savings`. A comment
on a move that makes no vault commit (into To review) is kept with the
flag in the private store.

### Layout

As the frozen mockup v4 shows (kept in the brainstorm session, recreated
in implementation, not committed as a file). Phone width: the Needs
attention cards scroll sideways, and the four columns become one column
at a time with a stage switcher. Tablet: two columns visible.

## Left to the builder

- Module layout in `app/` (a new reader beside `app/board.py` is
  expected; reuse its guards and card model rather than copying them).
- The action kind name(s) and receipt shape for stage changes and
  reminder saves, inside the existing S7 protocol.
- How "Show all" in Done paginates.
- Where the old wiring-check pulse block lives (it may remain at
  `/blocks/pulse` for its existing tests).
- Test layout, following `tests/test_board.py` with synthetic vaults only.

## Tests the build must prove

- The home page lists cards from every entity, in the right column, with
  entity chips; a note with another status is reachable, not dropped.
- Needs attention orders overdue, then due within a day, then later, with
  the right colour and icon.
- Each move in the table produces exactly one commit, a receipt, and
  `git revert` restores the prior bytes.
- A move bound to stale bytes (note changed since the popup opened) is
  refused, as S7 refuses today.
- Saving a reminder writes nothing to the vault; filing or moving a card
  keeps its reminder; Done resolves it.
- `active` and `done` both land in Done; any other status is listed, not
  dropped.
- Nothing is written on page load, filter, sort, or opening a popup.
- A body edit saved with a stage button produces one commit containing
  the edit, the status, and the comment; a stale edit is refused; a
  closed popup writes nothing.
- The Calendar step creates one event per reminder, updates it in place,
  removes it on Done, and never touches another calendar (tested against
  a fake Calendar adapter, not the real app).
- Revert test and rename test still pass; `.sensitive/` is still denied.

## Approval

Implementation waits for the owner to approve this document. The Board
fixes already in progress (module icons, no This week card, List view
honouring Group) are separate and ship on their own branch.
