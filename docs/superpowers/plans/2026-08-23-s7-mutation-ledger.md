# S7 Mutation Ledger

Evidence that S7's protections are load-bearing. A green test counts only
after the protection it names has been deliberately broken, the test has gone
red for the intended reason, the exact implementation has been restored
byte-for-byte, and the test has gone green again.

**Baseline:** `d7ad86b651c5f5f7c1adad8af94a0b767fb30a8f` → 926 passed.
**At the time of writing:** 1233 passed.

Restoration is by `cmp` against a pre-image copy taken before each mutation.
No destructive Git cleanup is used anywhere in this campaign — a `git
checkout` or `git clean` could erase unrelated work in the same tree.

---

## How to reproduce

Each row names the exact edit and the exact selection. Apply the edit, run
the selection, confirm the named test fails, restore the file from a
pre-image copy, confirm `cmp` reports no difference, and re-run.

---

## The campaign

| # | Protection broken | Edit | Selection | Result |
|---|---|---|---|---|
| M1 | approve/reject: the fingerprint comparison | `require_review_match(...)` → `pass` in `_own_reviewed_proposal` | `tests/test_outbox.py` | **RED** — `test_actions_refuse_a_malformed_fingerprint_without_mutation[None-approve]` |
| M2 | *(not a separate site)* | approve and reject share `_own_reviewed_proposal`, so M1 is the reject bypass too. Recorded rather than fabricated as a second mutant. | — | — |
| M3 | registry delete: the fingerprint comparison | `require_review_match(...)` → `pass` in `execute_delete` | `tests/test_registry.py` | **RED** — `test_execute_delete_refuses_a_malformed_fingerprint[None]` |
| M4 | hashing the parsed bytes | snapshot built from `leaf.read_bytes()` instead of the captured `contents` | `tests/test_outbox.py tests/test_console_projection.py` | **RED** — `test_review_value_and_hash_come_from_one_capture_not_a_second_read` |
| M5 | approve: transaction authority | owned change's `before` → a fresh `capture_path_state` | `tests/test_outbox.py` | **RED** — `test_approve_owns_the_reviewed_state_not_whatever_arrives_later` |
| M5b | delete: transaction authority | same substitution in `execute_delete`'s plan | `tests/test_registry.py` | **RED** — `test_delete_owns_the_reviewed_state_not_whatever_arrives_later` |
| M6 | reject: consuming the compared state | `consume_reviewed_proposal(..., proposal_state)` → `..., capture_path_state(...)` | `tests/test_outbox.py` | **RED** — `test_reject_owns_the_reviewed_state_not_whatever_arrives_later` |
| M7 | classification transport | `review_sha256` removed from `outbox_card.html`'s `hx-vals` | `tests/test_console_routes.py tests/test_console_invariants.py` | **RED** — `test_outbox_hx_vals_are_tojson` |
| M7b | delete transport | `review_sha256` removed from `delete_impact.html`'s `hx-vals` | same | **RED** — `test_delete_preview_hx_vals_survive_hostile_slug` |
| M8 | delete success copy | route pre-reads via `get_delete_review(...).value` for display | same | **RED** — `test_alerts_never_contain_paths_slugs_or_echoes` |
| M9 | never following a redirected leaf | *surgical*: open the leaf once, then refuse **identically** | `tests/test_console_routes.py` | **RED** — `test_check_again_never_reads_through_a_redirected_leaf[outbox]` |

## M6 — a survivor, and what it cost

M6 **survived** its first run: `195 passed`. Reject had no test for a change
landing between the fingerprint comparison and the consumption, because a
contents comparison cannot distinguish a fresh capture from the compared one
while nothing changes in between.

`test_reject_owns_the_reviewed_state_not_whatever_arrives_later` closes it.
Anchoring the probe took three attempts, and the failures are instructive:

1. patching `_require_outbox_path` unconditionally fired during the strict
   scan, *before* the comparison — the refusal was `E-REVIEW`, not the
   post-comparison `E-CONFLICT`;
2. counting leaf checks was brittle: `_require_destination` checks the same
   leaf again, so the "second" call was still inside the scan;
3. anchoring on the boundary itself works — once `_own_reviewed_proposal`
   has returned, the next leaf check is reject's own.

Wrapping `consume_reviewed_proposal` instead would *not* have worked: Python
evaluates the call's arguments before the wrapper body runs, so M6's
substituted capture would see the same bytes the real code does. Recorded
because the near-miss is the interesting part.

## M9 — the surgical follow-then-refuse mutant

Requested by review, and the sharpest evidence in this ledger. It opens the
redirect target once and then refuses through every existing guard, so every
operator-visible outcome is byte-identical to the correct implementation.

Against the whole suite it produces **exactly one failure in 1233 tests**:

```
E   AssertionError: the redirected target was opened
E   assert (16777234, 51228699) not in [(16777234, 51228699), ...]
```

That is the target-inode assertion itself, and nothing else — proving the
assertion is what detects a follow, not some neighbouring guard. It also
retires the earlier, weaker claim in this campaign: removing individual
no-follow layers left the test green because a deeper layer still refused,
and only stripping all four made it red — on the *positive control* rather
than the negative assertion. M9 replaces that inference with a direct
measurement.

## Earlier survivors, recorded

Mutants that survived when first run, each closed by strengthening the test
rather than by adjusting the mutation:

| Survivor | Why it survived | Closed by |
|---|---|---|
| Approve transaction authority (Task 3) | contents identical when nothing changes in between | `test_approve_owns_the_reviewed_state_not_whatever_arrives_later` |
| Delete transaction authority (Task 4) | same shape, delete path | `test_delete_owns_the_reviewed_state_not_whatever_arrives_later` |
| Destructive fallback on an unsupported errno (Task 3c) | no test drove the real errno branch | `test_an_unsupported_errno_fails_closed_and_never_falls_back` |
| Adopting a raced quarantine directory | the refusal happened for a different reason | assert `E-TAMPER` specifically |
| Accepting non-string reported evidence | nothing was echoed either way | assert badly-typed fields land in *uncompared* |
| `_discard_private_name` deleting content | the guard was unreachable from any test | a direct unit test of the guard |

## What this campaign does not cover

- **Linux `renameat2(RENAME_NOREPLACE)`** — unexercised. This session is
  macOS, where `renameatx_np(RENAME_EXCL)` was measured directly. Amendment 1
  makes the Linux success path *and* its occupied-destination refusal a
  completion condition, not a pre-live-gate item.
- **The private read-only gates** and the **Grey Matter preservation proof** —
  both require `ONEOS_VAULT`, which was deliberately never set in this
  session. Grey Matter was not accessed at any point; that is an absence of
  access, not a proof of preservation, and the proof remains outstanding.
