# S7 Mutation Ledger

Evidence that S7's protections are load-bearing. A green test counts only
after the protection it names has been deliberately broken, the test has gone
red for the intended reason, the exact implementation has been restored
byte-for-byte, and the test has gone green again.

**Baseline:** `d7ad86b651c5f5f7c1adad8af94a0b767fb30a8f` → 926 passed.

The current pass count lives in [The campaign](#the-campaign) below and
nowhere else. It used to be repeated here too, and drifted: this header still
read 1239 while the campaign block said 1258. A count maintained by hand in
two places goes stale by construction, so there is now one copy of it, pasted
from the runner's own output.

Restoration is by `cmp` against a pre-image copy taken before each mutation.
No destructive Git cleanup is used anywhere in this campaign — a `git
checkout` or `git clean` could erase unrelated work in the same tree.

---

## How to reproduce

Every mutation below is a single, exact string substitution. Apply it, run
the named selection, confirm the named test fails, restore the file from a
pre-image copy, and confirm `cmp` reports no difference.

The whole campaign is scripted so a reviewer need not retype anything. Run it
through the project environment:

```bash
uv run python docs/superpowers/plans/s7_mutation_campaign.py --list   # what it will do
uv run python docs/superpowers/plans/s7_mutation_campaign.py          # run it
```

The script itself invokes `uv run python -m pytest`, so it behaves correctly
however it is launched. That matters: a bare interpreter without pytest exits
non-zero having run nothing, which is indistinguishable from a surviving
mutant unless the return code is checked. It is checked.

For each mutation the script:

1. **refuses to start** if any target file has uncommitted changes, so a
   mutation is never applied over unrelated edits and a restore never
   discards them;
2. asserts the `OLD` text appears **exactly once**, so a stale edit cannot
   silently mutate nothing and be recorded as evidence;
3. applies **phase-specific** rules, because the two phases prove different
   things and a shared rule is wrong for both:

   - a **mutated** run is performed **one expected node at a time**, by full
     node id. That node must be the *only* failure, and its failure must
     carry the intended diagnostic. Running the whole selection and checking
     "the node is in some FAILED line" and "the diagnostic is somewhere in
     the output" as separate conditions accepts a crossed result — the
     expected node failing for an unrelated reason while a *different* test
     emits the diagnostic. Running the node alone makes the binding
     structural rather than a matter of parsing. The run must also exit `1`
     and carry no collection `ERROR`s, since an exit of 1 with only `ERROR`
     lines produces no `FAILED` lines at all.
   - a **restored** or **full** run must exit `0`. Nothing else counts as
     green — scanning output for `FAILED` lines is not sufficient, for the
     same reason.

   Runs use **`--tb=line`**, which prints one line per failure: the failing
   assertion and nothing else. A full traceback also prints surrounding
   *source*, so a diagnostic can match a nearby line that **passed** — which
   is exactly how M4b's expected diagnostic came from its own setup
   assertion and was accepted. Every diagnostic in this ledger is a message
   on the assertion the mutant actually breaks, not a string that happens to
   sit near it.

4. restores from an in-memory pre-image, verifies byte-identity, and
   **re-runs the same selection to confirm it is green again**; and
5. runs the **full public suite** after the whole restored group.

Read-only Git (`status --porcelain`) provides the cleanliness check. No
*destructive* Git command appears anywhere in it: a `git checkout` or
`git clean` in a shared tree could erase unrelated work, and a mutation
harness is the last place that should be possible.

---

## The campaign

Last full run, from the script above:

```
M1   RED then GREEN   M3   RED then GREEN   M4b  RED then GREEN
M5   RED then GREEN   M5b  RED then GREEN   M6   RED then GREEN
M7   RED then GREEN   M7b  RED then GREEN   M8   RED then GREEN
M9   RED then GREEN   M10  RED then GREEN   M12  RED then GREEN
M13  RED then GREEN   M14  RED then GREEN   M15  RED then GREEN
M16  RED then GREEN   M11  RED then GREEN

all 17 mutations: red under mutation, green once restored

full public suite after the restored campaign group:
  1258 passed in 91.59s (0:01:31)
```

The runner's own guards were exercised too, since a harness that cannot fail
is not evidence either:

| Guard | Probe | Result |
|---|---|---|
| dirty target refused | append a line to `app/registry.py`, run | refused, naming the file |
| infrastructure ≠ survival | pytest exits 4 / 127, or collects nothing | refused as infrastructure failure, scored as neither |
| **exit 1 with only ERRORs** | `_require_clean_run(1, "ERROR …")` | **refused** — previously reported green |
| mutated run must exit 1 | exit 0, exit 2 | refused as infrastructure failure |
| mutated run must have no ERRORs | exit 1, errors only | refused as infrastructure failure |
| the *exact node* must fail | a different node fails | refused |
| with its *intended diagnostic* | right node, wrong reason | scored ALIVE |
| **diagnostic bound to the node** | expected node fails for another reason while a different test emits the diagnostic | **refused** — previously accepted |
| **diagnostic bound to the failing assertion** | restore M4b's old expectation (a string from its *passing* setup assertion) | **ALIVE** — previously accepted via traceback source context |
| the node id must still select something | a renamed node | refused |
| required field pinned | `Form(...)` → `Form(None)` on all three routes | RED — "outbox_approve does not REQUIRE review_sha256" |
| `.git` exclusion is exact | a directory literally named `ordinary.git/` | its `marker.bin` and bytes are captured |


Each row gives the file, the exact `OLD` text, the exact `NEW` text, and the
pytest selection. `OLD` appears exactly once in the file at the commit named
above; the script asserts that before substituting.

### M1 — approve/reject: the fingerprint comparison

- **file** `app/outbox.py`
- **selection** `tests/test_outbox.py`
- **result** RED — `test_actions_refuse_a_malformed_fingerprint_without_mutation[None-approve]`

```diff
-    require_review_match(proposal_state.contents, review_sha256)
+    pass  # MUTANT M1
```

### M1 requires both nodes

M1 breaks the comparison in `_own_reviewed_proposal`, which approve and
reject share, so the ledger requires **both** `[None-approve]` and
`[None-reject]` to go red. Requiring only one would leave half the shared
protection unproven.

### M2 — reject's bypass is M1

Approve and reject share `_own_reviewed_proposal`, so there is no second
comparison site to break. Recorded rather than fabricated as a distinct
mutant, because a ledger row that mutates nothing is worse than an absent one.

### M3 — registry delete: the fingerprint comparison

- **file** `app/registry.py`
- **selection** `tests/test_registry.py`
- **result** RED — `test_execute_delete_refuses_a_malformed_fingerprint[None]`

```diff
-        require_review_match(proposal_state.contents, review_sha256)
+        pass  # MUTANT M3
```

### M4 — RETIRED (historical)

**Not in the campaign. Do not attempt to reproduce it.** M4 mutated the
strict loader's snapshot:

```diff
-            make_review_snapshot(_require_destination(scope, proposal), contents)
+            make_review_snapshot(_require_destination(scope, proposal), leaf.read_bytes())
```

Independent review established that the loader's digest is read by no
production caller — every action's fingerprint came from a *second*
construction site in `project_outbox`. So M4 proved a protection on a path
no operator ever touched. Both sites are now one function
(`review_snapshot_for`), which means M4 and M4b would mutate the same line;
M4 is retired rather than left as a row that mutates nothing. Kept here only
so the ledger's history is legible.

### M4b — the operator-facing snapshot hashes a second read

- **file** `app/outbox.py` — `review_snapshot_for`
- **selection** `tests/test_console_projection.py tests/test_outbox.py`
- **result** RED — `test_the_projected_value_and_hash_come_from_one_capture`

```diff
-    return make_review_snapshot(_require_destination(scope, proposal), contents)
+    return make_review_snapshot(_require_destination(scope, proposal), leaf.read_bytes())  # MUTANT M4b
```

**Added after independent review, and it replaces M4.** M4 mutated the
strict loader's snapshot — whose digest, it turned out, no production caller
reads. The digest that reaches an operator's button came from a *second*
construction site in `project_outbox`, and mutating that one left the whole
suite green: 1233 passed, while the button carried the fingerprint of bytes
the operator never saw. The two sites are now one function, so M4 and M4b
would mutate the same line; M4 is retired rather than left as a row that
mutates nothing.

### M5 — approve: transaction authority from a later reread

- **file** `app/outbox.py`
- **selection** `tests/test_outbox.py`
- **result** RED — `test_approve_owns_the_reviewed_state_not_whatever_arrives_later`

```diff
-            PathChange(proposal_rel, proposal_state, PathState.absent()),
+            PathChange(proposal_rel, capture_path_state(vault, proposal_rel), PathState.absent()),  # MUTANT M5
```

### M5b — delete: transaction authority from a later reread

- **file** `app/registry.py`
- **selection** `tests/test_registry.py`
- **result** RED — `test_delete_owns_the_reviewed_state_not_whatever_arrives_later`

```diff
-                PathChange(proposal_rel, proposal_state, PathState.absent()),
+                PathChange(proposal_rel, capture_path_state(vault, proposal_rel), PathState.absent()),  # MUTANT M5b
```

### M6 — reject: consuming a recapture instead of the compared state

- **file** `app/outbox.py`
- **selection** `tests/test_outbox.py`
- **result** RED — `test_reject_owns_the_reviewed_state_not_whatever_arrives_later`

```diff
-        consume_reviewed_proposal(scope.root, proposal_rel, proposal_state)
+        consume_reviewed_proposal(scope.root, proposal_rel, capture_path_state(scope.root, proposal_rel))  # MUTANT M6
```

### M7 — classification transport

- **file** `templates/blocks/outbox_card.html`
- **selection** `tests/test_console_routes.py tests/test_console_invariants.py`
- **result** RED — `test_outbox_hx_vals_are_tojson`

```diff
-  {% set outbox_values = {"id": row.proposal.id,
-                          "review_sha256": row.review_sha256} %}
+  {% set outbox_values = {"id": row.proposal.id} %}  {# MUTANT M7 #}
```

### M7b — delete transport

- **file** `templates/blocks/delete_impact.html`
- **selection** `tests/test_console_routes.py tests/test_console_invariants.py`
- **result** RED — `test_delete_preview_hx_vals_survive_hostile_slug`

```diff
-  {% set delete_execute_values = {"id": prop.id,
-                                  "review_sha256": review_sha256} %}
+  {% set delete_execute_values = {"id": prop.id} %}  {# MUTANT M7b #}
```

### M8 — delete success copy from an unbound pre-read

- **file** `app/main.py`
- **selection** `tests/test_console_routes.py tests/test_console_invariants.py`
- **result** RED — `test_alerts_never_contain_paths_slugs_or_echoes`

```diff
-        prop = execute_delete(scope, id, review_sha256)
+        prop = get_delete_review(scope, id).value
+        execute_delete(scope, id, review_sha256)  # MUTANT M8
```

### M9 — surgical: follow the redirect target, refuse identically

- **file** `app/outbox.py`
- **selection** `tests/test_console_routes.py`
- **result** RED — `test_check_again_never_reads_through_a_redirected_leaf[outbox]`

```diff
+    try:
+        _p = open(candidate, "rb")
+    except OSError:
+        pass
+    else:
+        _p.close()
     if candidate.is_symlink():
         raise RedirectedPathError("proposal leaf is redirected")
```

### M10 — a refusal that silently rewrites the delete proposal

- **file** `app/registry.py`
- **selection** `tests/test_registry.py`
- **result** RED — `test_the_delete_no_mutation_matrix[new-live-reference]`

```diff
+        path.write_bytes(path.read_bytes() + b"# MUTANT\n")
         report = reference_count(scope, prop.kind, prop.slug)
         if report.total:
```

Added after review: the delete boundary originally compared Git state and the
registry file only. A delete proposal is untracked, so a refusal that rewrote
it left HEAD, the index and the tracked diff identical and passed. The
boundary now compares the bytes and type of every path in the vault.

### M11 — a route that mentions the fingerprint but never forwards it

- **file** `app/main.py`
- **selection** `tests/test_console_invariants.py`
- **result** RED — `test_every_reviewed_route_requires_and_passes_the_fingerprint`

```diff
-        prop = execute_delete(scope, id, review_sha256)
+        _unused = review_sha256  # MUTANT M11
+        prop = execute_delete(scope, id, "0" * 64)
```

Added after review: the structural test previously asserted only that
`review_sha256` was *used* somewhere in the route, which a log line would
satisfy. It now requires it to be an argument of the call that acts.

### M12 — the live reference gate stops running under the lock

- **file** `app/registry.py`
- **selection** `tests/test_registry.py`
- **result** RED — `test_the_reference_recount_holds_the_approval_lock`

```diff
-            preconditions=(_require_no_live_references,),
+  # MUTANT M12: reference gate no longer runs under the lock
```

Counting references before the approval lock leaves a window another
approval can commit into: a new *reference* leaves this transaction's own
expected states — the registry file and the proposal record — untouched, so
they still match and the deletion proceeds, orphaning it. Acceptance
criterion 5 is about the instant before mutation.

### M13 — a stranded record collapses into a generic recovery outcome

- **file** `app/git_transaction.py`
- **selection** `tests/test_outbox.py`
- **result** RED — `test_a_rollback_that_cannot_restore_the_record_reports_it_as_stranded`

```diff
-        if stranded_records:
+        if False:  # MUTANT M13
```

### M14 — a simultaneous cleanup failure overwrites the stranded outcome

- **file** `app/git_transaction.py`
- **selection** `tests/test_outbox.py`
- **result** RED — `test_a_stranded_record_survives_a_simultaneous_cleanup_failure`

```diff
-            if isinstance(transaction_error, QuarantineRestorationBlocked):
+            if False:  # MUTANT M14
```

A leftover temporary index is a diagnostic detail; a consumed record
stranded in quarantine is an indeterminate state. Reporting the first and
discarding the second says "nothing was changed" while a record sits in
`.consumed/`.

---

### M15 — approve parses a fresh read instead of the compared bytes

- **file** `app/outbox.py`
- **selection** `tests/test_outbox.py`
- **result** RED — `test_approve_parses_the_bytes_it_compared_not_a_fresh_read`

```diff
-    record = _parse_record_bytes(proposal_state.contents)
+    record = _parse_record_bytes((vault / proposal_rel).read_bytes())
```

### M16 — delete parses a fresh read instead of the compared bytes

- **file** `app/registry.py`
- **selection** `tests/test_registry.py`
- **result** RED — `test_execute_delete_parses_the_bytes_it_compared_not_a_fresh_read`

```diff
-            scope, path, _parse_delete_record(proposal_state.contents)
+            scope, path, _parse_delete_record(path.read_bytes())
```

Both of these were **equivalent mutants** until their tests existed, and the
safety review was right to name them: the whole suite passed under either
one. The reason is `PathState`, which compares full contents rather than
metadata. If a replacement is still on disk when the transaction runs,
`_require_expected_states` refuses whichever bytes were parsed, and no
observer outside the function can tell the two implementations apart.

What separates them is a replacement that does not survive to the
transaction — a writer that swaps the record and puts the original back.
Every state check then passes, because by the time anything checks, the
reviewed bytes are what the file holds. Only the parse saw the replacement,
and in both routes the parsed value chooses *what the action does*: the
destination approve moves to, and the registry entry delete removes.

So both tests hold hostile bytes on disk for exactly the window a reread
would use, restore the reviewed bytes immediately afterwards, and take the
action's own choice as the witness. Each asserts the window really opened
and closed, so neither can pass by the race failing to occur. Under M15
approve filed to `demo/11-library/active/note.md`, which nobody reviewed;
under M16 delete removed the sibling product `other` and left `widgetx` in
place. Each mutant fails exactly one node in the full suite — the one added
for it — which is what "no longer equivalent" means here.

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
