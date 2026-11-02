# Master view 3: moves, stage buttons, comments and draft editing — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** From a card's popup (four stage buttons, an editable draft, a comment box) or by dragging a card between columns on the home page, the owner moves notes between Draft · Waiting · To review · Done, each vault change being one approved, revertible commit through the existing Outbox protocol.

**Architecture:** A new `stage` proposal kind joins the Outbox: the request writes a `stage` record (bound to the bytes the popup showed) and approves it in the same action through `approve()`'s existing S7 sequence (`_own_reviewed_proposal`), producing one commit and an `"approval"` receipt. A small header-scoped note editor (`app/note_text.py`) computes the new bytes. Moving a filed note *into* To review writes nothing to the vault: a flag in the private reminder store.

**Tech Stack:** Python 3.12, FastAPI, Jinja2, HTMX + `alpine-morph`, Alpine.js (vendored), native HTML drag and drop, Git via `app/git_transaction.py`, SQLite private store (`app/reminders.py`), pytest with synthetic vaults.

**Spec:** `docs/superpowers/specs/2026-10-29-master-view-design.md` — decisions 8 (popup), 9 (your move is your approval), "Moves", "Editing the draft in the popup" (incl. the 2026-11-02 `updated` decision), "Reminders and review flags", anti-goals, "Tests the build must prove". Builds on plans 1–2 (merged): `app/master.py`, `app/reminders.py`, `/master/card`, `/master/reminder`.

**Protocol map (read before Task 2):** S7 is `outbox._own_reviewed_proposal` (capture once → `review_tokens.require_review_match` → parse those bytes) plus the transaction's `before`-state ownership in `git_transaction.execute_transaction`. `approve(scope, proposal_id, review_sha256)` is signature-pinned (`tests/test_outbox.py` ~2109); extra inputs must live in the record bytes. Precedents: `LifecycleProposal` dispatch inside `approve()` (`outbox.py` ~1108) and the in-place edit `registry.execute_delete` (`registry.py` ~691–802).

## Global Constraints

- Every vault change goes through the Outbox protocol unchanged: write a record, approve it by its `review_sha256` through `approve()`, one commit, a receipt with `action_kind: "approval"` (schema version 1, unchanged). Do not edit `app/review_tokens.py`, `app/git_transaction.py`, `app/action_receipts.py`, or the pinned tests for them.
- One user action = one commit containing exactly the note path(s) changed and the receipt; `git revert` of it restores the prior bytes. The `stage` record is consumed (quarantined to `.consumed/`) and never committed.
- Binding: the popup and the cards carry `source_sha256` = SHA-256 of the full note bytes captured in the **same read** that produced what was shown. The `stage` record stores that hash; `approve()` refuses with `StaleProposalSource` if the note changed. A stale refusal re-renders the popup with the newer text and the message "This note changed since you opened it. Here is the newer version."
- Note edits are header-scoped and byte-preserving: only the `status:` and `updated:` lines inside the front-matter block change (inserted before the closing fence if absent); the body is replaced only when an edited body was submitted; nothing else in the file changes. CRLF from browsers is normalised to `\n`. The new bytes must pass `app/schema.validate_file_text`; a file whose header the editor cannot place safely is refused, never rewritten.
- `updated:` = the owner's local date as `YYYY-MM-DD`, sent by the browser (`today` field) and accepted only within ±1 day of the server's UTC date. The comment line is `Comment (YYYY-MM-DD): <text>` (same date), appended to the body as its own line; comment text is single-line, trimmed, 1–500 characters.
- Status values written: `draft`, `waiting`, `done` (spec decision 1; `active` keeps counting as Done on read).
- Stage buttons (exact copy): "Save as Draft", "Waiting", "To review", "Done". Destination picker label "File to". Stale message as above. Bare-inbox drag opens the popup instead of moving.
- Moves:
  - Filed note → Draft / Waiting / Done: `stage` record with `src == dst` (in place).
  - Bare inbox item → any stage: the popup first asks for module/sub (the same choices as Triage's manual destination: active lifecycle modules and their active subs); the `stage` record has `src` = inbox path, `dst` resolved by `destinations.resolve_classification_destination`; `sub` applied as `outbox._apply_sub` does.
  - Inbox item with a pending classify proposal → any stage: `stage` record reuses the proposal's `dst`/`module`/`sub` and names it in `supersedes` with its `review_sha256` (the popup carries it); approval consumes both records in the same transaction (the classify record as an owned change), one commit, one receipt for the stage id.
  - Filed note → To review: a review flag (with optional comment) in the private store; moving it to any stage clears the flag. With no body edit this makes no vault change. With an edited body it is one `stage` commit that saves the body and `updated` and keeps the current `status`, then the flag (Codex review on #59).
  - To review (inbox) → To review: no-op.
  - Done resolves the card's reminder (row deleted); inbox → filed re-keys the reminder (`_reminder_follows_approval`).
- Nothing is written on page load, filter, sort, refresh, or opening a popup. Closing the popup without a button writes nothing. A button press with no change (same stage, unchanged body, no comment) writes nothing and says so.
- A stranded or malformed `stage` record must never block the entity's Outbox: the record is written to a private temporary name outside `*.yaml` and published to `<entity>/outbox/<id>.yaml` with a no-replace move only when complete (so a crash can never leave a truncated record), the loader recognises the kind in the same commit that can write it, and every refusal after publishing quarantines it (`reject`).
- `app/master.py` must not import `app.console_errors`; every new route uses `@console_route` with declared catches and failure contracts (`tests/test_console_invariants.py`, `tests/test_console_routes.py`).
- Private integration gate (`BUILD.md` §3) before merge. Never write "/home/" followed by a path segment. Run tests as `env -u GIT_EDITOR uv run pytest ...`.

## Review Focus

1. **A note edited elsewhere between opening the popup and pressing a button** — refused, nothing written, newer text shown (S7 + source hash). Pinned in Tasks 2 and 4.
2. **A body or front matter containing `status:`, `sub:`, `updated:` or `---` lines, CRLF, or no `status:` line** — only the header lines change. Pinned in Task 1.
3. **A crash or refusal after the record is written** — the entity's Outbox still lists, approves and rejects normally. Pinned in Task 2.
4. **Drag of a bare inbox card, or into Waiting/To review** — opens the popup (destination / reminder) instead of moving. Pinned in Task 5.
5. **Double click / two tabs** — the second press is refused (spent or stale), never a second commit. Pinned in Tasks 2 and 4.

---

### Task 1: Header-scoped note editor

**Files:**
- Create: `app/note_text.py`
- Test: `tests/test_note_text.py`

**Interfaces:**
- Produces:
  - `class NoteTextError(ValueError)`
  - `split_note(text: str) -> tuple[str, str]` — `(header_raw, body_raw)`; `header_raw` runs from the opening `---` line through the closing fence line (a line whose stripped content is `---`), byte-for-byte; refuses (raises) when there is no front matter, no closing fence, or when the first `---` occurrence `schema.validate_file_text` would use differs from the fence line.
  - `edit_note(text: str, *, status: str, updated: str, body: str | None, comment: str | None) -> str` — sets/inserts `status:` and `updated:` inside the header only; replaces the body when `body` is not `None` (preserving the original separator between header and body); appends `\nComment (<updated>): <comment>\n` when given; normalises CRLF; result must pass `schema.validate_file_text` else `NoteTextError`.
  - `normalise_body(text: str) -> str` (CRLF→LF, keeps everything else) and `shown_body(text: str) -> str` (what the popup shows; `edit_note(..., body=shown_body(t))` with no other change returns `t` unchanged apart from status/updated).

- [ ] **Step 1: Failing tests** — status replaced in place; status inserted when missing; `updated` replaced/inserted; a body line `status: x` / `sub: y` / `updated: z` untouched; a title containing `---` refused (fence mismatch) rather than corrupted; CRLF input; no-op body round-trip byte-identical except the two header lines; comment appended with the date; comment with a newline refused; result fails `validate_file_text` → `NoteTextError`.
- [ ] **Step 2: Run** `env -u GIT_EDITOR uv run pytest -q tests/test_note_text.py` — fail.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — pass.
- [ ] **Step 5: Commit** — `Notes: header-scoped status, updated and body edits`.

---

### Task 2: `stage` proposal kind through the Outbox

**Files:**
- Create: `app/stage.py` (record writer + validator; no route code)
- Modify: `app/outbox.py` (`_proposal_from_contents` recognises `action: stage` before the unknown-action refusal; `approve()` dispatches `StageProposal`; `_render_review`/`project_outbox`/`preview_diff` show a stage diff identical to the bytes applied)
- Test: `tests/test_stage.py`

**Interfaces:**
- Consumes: `note_text.edit_note` (Task 1); `destinations.resolve_classification_destination`; `outbox._apply_sub`, `_own_reviewed_proposal`, `make_action_receipt`, `TransactionPlan`, `PathChange`, `PathState`.
- Produces:
  - `@dataclass(frozen=True) class StageProposal: id, entity, created, src, dst, source_sha256, set_status, body, comment, updated, module, sub, supersedes, supersedes_review_sha256`.
  - `propose_stage(scope, *, src, source_sha256, set_status, updated, body=None, comment=None, module=None, sub=None, supersedes=None, supersedes_review_sha256=None) -> tuple[str, str]` — validates (filed `src` must be `<entity>/<lifecycle module>/active/<leaf>.md`, no symlinked components, not under `.sensitive/`; inbox `src` resolves `dst` like classify; `supersedes` must be a pending classify record for the same `src`), pre-computes the new bytes against the current file and refuses `NoteTextError` early, writes `<entity>/outbox/<id>.yaml` with `open("x")`, returns `(id, review_sha256)` where the digest is SHA-256 of the exact bytes written.
  - `approve()` for a `StageProposal`: same S7 order; source capture must hash to `source_sha256` (`StaleProposalSource`); new bytes from `edit_note` (+ `_apply_sub` when moving from inbox); plan changes = in place `PathChange(src, before, regular(new, mode))` or move (`src` delete + `dst` create); receipt `"approval"`; owned changes = the stage record (+ the superseded classify record, whose `review_sha256` must match); preconditions `_require_unspent_id`; commit message `outbox: stage <id> (<src> → <dst>)` or `(<src>)` in place.
  - `propose_and_approve_stage(scope, **kwargs) -> StageProposal | SpentAction` — proposes then approves with the returned digest; on any refusal after writing, quarantines the record via `reject` and re-raises.

- [ ] **Step 1: Failing tests** (synthetic git vault, reuse `tests/conftest.py` git helpers and `tests/test_outbox.py` patterns): in-place stage → exactly one commit with `[note, receipt]`, receipt `action_kind == "approval"`, `git revert` restores bytes; inbox → destination stage (one commit, `[src, dst, receipt]`, revert works); superseding a classify proposal (both records consumed, one commit, one receipt); stale source (note edited after `source_sha256`) → `StaleProposalSource`, nothing committed, record quarantined, Outbox still lists other proposals; a stranded `stage` record (written, never approved) does not set the projection `blocked`; a crash mid-write (simulate by failing between temp write and publish) leaves no `*.yaml` and the Outbox still works; spent replay → `SpentAction`, no second commit; pre-commit hook failure → nothing consumed (follow `tests/test_console_routes.py` ~3379 pattern); `.sensitive/` and non-lifecycle and symlinked sources refused; the Outbox diff for a stage record equals the applied bytes; existing outbox, registry, receipt, review-token and rename tests unchanged and passing.
- [ ] **Step 2: Run** `env -u GIT_EDITOR uv run pytest -q tests/test_stage.py tests/test_outbox.py` — new tests fail.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `tests/test_stage.py tests/test_outbox.py tests/test_action_receipts.py tests/test_review_tokens.py tests/test_git_transaction.py tests/test_registry.py tests/test_rename.py tests/test_console_invariants.py` — pass.
- [ ] **Step 5: Commit** — `Outbox: a stage proposal kind, approved through the same S7 path`.

---

### Task 3: Review flags in the private store

**Files:**
- Modify: `app/reminders.py` (new table `review_flags(key TEXT PRIMARY KEY, entity TEXT NOT NULL, comment TEXT, flagged_at TEXT NOT NULL)` created like the reminders table; `flags() -> dict[str, ReviewFlag]`, `flag(...)`, `unflag(key)`, `rekey`/`rename_entity` also move flags), `app/master.py` (a flagged filed card appears in the To review column, keeps its status, shows its review-by reminder and comment)
- Test: `tests/test_reminders.py`, `tests/test_master.py`

**Interfaces:**
- Produces: `@dataclass(frozen=True) class ReviewFlag: key: str; entity: str; comment: str | None; flagged_at: datetime`; `load_master(..., flags: Mapping[str, ReviewFlag] | None)`.

- [ ] **Step 1: Failing tests** — flag/unflag round trip; reads create nothing; a flagged filed card is in To review (not duplicated in Draft/Waiting/Done) and keeps its `status`; unflag returns it to its stage column; rename and rekey move flags; store unavailable → flags ignored, page renders.
- [ ] **Step 2–4:** Run, implement, run `tests/test_reminders.py tests/test_master.py tests/test_console_invariants.py`.
- [ ] **Step 5: Commit** — `Master view: To review flags for filed notes`.

---

### Task 4: Stage routes and the popup

**Files:**
- Modify: `app/main.py` (`master_card` captures the note once: body shown + `source_sha256`; for a proposed card adds the proposal's `review_sha256` via `outbox.get_proposal_review`; new `POST /master/stage/{entity}`), `templates/blocks/master_popup.html` (textarea, comment box, four buttons, "File to" picker for bare inbox items, existing reminder box), `static/app.css`
- Test: `tests/test_master.py`

**Interfaces:**
- Consumes: Tasks 1–3; `_find_card`, `_popup_context`, `_reminder_follows_approval`, reminder store.
- Produces: `POST /master/stage/{entity}` form `path`, `source_sha256`, `stage` (`draft|waiting|review|done`), `today`, optional `body`, `comment`, `module`, `sub`, `proposal_id`, `proposal_review_sha256`. Behaviour per the Moves constraint; on success re-renders the popup (or closes it) and sends `HX-Trigger: master-refresh`; stale → popup with the newer text and the stale message; other refusals → `master_popup_error.html`; Done deletes the reminder; inbox → filed re-keys reminder and flag; into Waiting/To review with a reminder box keeps the existing reminder save flow.

- [ ] **Step 1: Failing tests** — each row of the Moves table end to end through the route (one commit, receipt, revert restores, reminder handled); filed note → To review with an edited body (one commit, status kept, body and `updated` saved, flag set) and without (no commit, flag set); body edit + comment + stage in one commit (`updated` set to the posted `today`); stale edit refused with newer text shown and nothing written; closed popup / page load / filter / sort / popup GET write nothing (extend `test_master_reads_without_writing`); a forged `path` or `source_sha256` refused; `today` outside ±1 day refused; double submit → one commit; CSRF required (required-auth TestClient pattern); the deliberate updates to the two "popup has no form/textarea" pins in `tests/test_master.py`.
- [ ] **Step 2–4:** Run, implement, run `tests/test_master.py tests/test_stage.py tests/test_console_invariants.py tests/test_console_routes.py tests/test_app.py`.
- [ ] **Step 5: Commit** — `Master view: stage buttons, draft editing and comments in the popup`.

---

### Task 5: Drag and drop on the home page

**Files:**
- Modify: `templates/home.html`, `templates/blocks/master_card.html` (cards carry `source_sha256` from the same read as the card — add `sha256` to `BoardCard` in `app/board.py` `_read_filed`/`_inbox_cards`), `static/app.css`
- Test: `tests/test_master.py`

**Interfaces:**
- Consumes: Task 4 route.
- Produces: native drag and drop between the four columns: a drop into Draft or Done posts straight to `/master/stage/{entity}` (with `today` from the browser); a drop into Waiting or To review, or of a bare inbox card, opens the popup instead (spec decision 9); a drop of a card that has a pending classify proposal also opens the popup (the popup carries that proposal's `review_sha256`; Codex review on #59); drop targets show the existing `drop-over` style; keyboard users use the popup buttons; phone width has no drag (stage switcher + popup buttons).

- [ ] **Step 1: Failing tests** — card markup carries `draggable`, entity, path and `source_sha256`; drop handler targets per column as above (template-level assertions and a node harness for the drop script like `tests/test_passkeys.py`'s); a drop post with a stale hash is refused; a proposed card's drop opens the popup and never posts directly; phone layout renders no drag handles.
- [ ] **Step 2–4:** Run, implement, run `tests/test_master.py tests/test_board.py tests/test_app.py`; browser check at 1280/768/375 px light and dark on a synthetic vault (drag a filed card Draft → Done, see one commit; drag a bare inbox card → popup opens).
- [ ] **Step 5: Commit** — `Master view: drag a card to its next stage`.

---

## Before merge

- Full suite `env -u GIT_EDITOR uv run pytest -q --ignore=tests/test_local_backup.py --ignore=tests/test_local_service.py` and `uv run python -m tools.public_repo_audit --repo . --history`.
- Private integration gate (`BUILD.md` §3): vault unittests, `check_v2.py`, `public_repo_audit --vault … --history`, vault byte-identical — this change writes to the vault, so also run one stage approval against a **throwaway copy** of the vault and confirm the vault's own pre-commit hook (`policy_enforcer.py`) accepts the commit.
- Owner check on the running install after update: move a note Draft → Waiting → Done by button and by drag, edit a draft with a comment, undo one change with `git revert` guidance in STATUS.

## Rulings in this plan (owner may overturn)

1. A new `stage` proposal kind (not an extended classify record), approved through `approve()`'s existing S7 sequence and the `"approval"` receipt — filed-note edits cannot be expressed as classify records, and this keeps review tokens, transactions and receipts untouched.
2. A stage on an inbox item with a pending classify proposal supersedes it in the same transaction, so the owner's one press is one commit.
3. `updated` and the comment date come from the browser's local date, checked within a day of the server's date.
4. A press with no change writes nothing.
5. Drag is desktop and tablet only; phones use the popup buttons.
