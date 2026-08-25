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
M15  RED then GREEN   M16  RED then GREEN   M17  RED then GREEN
M20  RED then GREEN   M21  RED then GREEN   M22  RED then GREEN
M23  RED then GREEN   M11  RED then GREEN   M24  RED then GREEN
M24b RED then GREEN   M25  RED then GREEN   M25b RED then GREEN
M26  RED then GREEN   M26b RED then GREEN   M27  RED then GREEN
M28  RED then GREEN   M28b RED then GREEN   M29  RED then GREEN
M30  RED then GREEN   M31  RED then GREEN   M31b RED then GREEN
M32  RED then GREEN   M33  RED then GREEN   M34  RED then GREEN
M35  RED then GREEN   M36  RED then GREEN   M37  RED then GREEN
M38  RED then GREEN   M39  RED then GREEN   M40  RED then GREEN
M41  RED then GREEN   M42  RED then GREEN   M43  RED then GREEN
M43b RED then GREEN   M44  RED then GREEN   M45  RED then GREEN

all 48 mutations: red under mutation, green once restored

full public suite after the restored campaign group:
  1473 passed in 110.35s
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
- **result** RED — both nodes, as the committed runner requires:
  - `test_actions_refuse_a_malformed_fingerprint_without_mutation[None-approve]`
    — `DID NOT RAISE InvalidReviewToken`
  - `test_actions_refuse_a_malformed_fingerprint_without_mutation[None-reject]`
    — `DID NOT RAISE InvalidReviewToken`

```diff
-    review_digest = require_review_match(proposal_state.contents, review_sha256)
+    review_digest = hashlib.sha256(proposal_state.contents).hexdigest()  # MUTANT M1
```

M1 breaks the comparison in `_own_reviewed_proposal`, which approve and
reject share, so **both** nodes must go red. Requiring only one would leave
half the shared protection unproven. This was previously a separate `### M1
requires both nodes` heading, which made the document appear to hold a second
M1 mutation and threw off the count of mutation headings; the row itself
listed only the approve node while the runner required both.

### M2 — reject's bypass is M1

Approve and reject share `_own_reviewed_proposal`, so there is no second
comparison site to break. Recorded rather than fabricated as a distinct
mutant, because a ledger row that mutates nothing is worse than an absent one.

### M3 — registry delete: the fingerprint comparison

- **file** `app/registry.py`
- **selection** `tests/test_registry.py`
- **result** RED — `test_execute_delete_refuses_a_malformed_fingerprint[None]`

```diff
-        review_digest = require_review_match(
-            proposal_state.contents, review_sha256
-        )
+        review_digest = "0" * 64  # MUTANT M3
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
-        result = consume_reviewed_proposal(
-            scope.root,
-            proposal_rel,
-            proposal_state,
-            preconditions=(_require_unspent_id,),
-        )
+        result = consume_reviewed_proposal(
+            scope.root,
+            proposal_rel,
+            capture_path_state(scope.root, proposal_rel),  # MUTANT M6
+            preconditions=(_require_unspent_id,),
+        )
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
- **result** RED — `test_delete_success_copy_comes_from_the_bound_execution`
  (this row named `test_alerts_never_contain_paths_slugs_or_echoes`, which is
  not the node the runner binds M8 to and not the node that goes red)

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
-            preconditions=(_require_unspent_id, _require_no_live_references),
+            preconditions=(_require_unspent_id,),  # MUTANT M12
```

Counting references before the approval lock leaves a window another
approval can commit into: a new *reference* leaves this transaction's own
expected states — the registry file and the proposal record — untouched, so
they still match and the deletion proceeds, orphaning it. Acceptance
criterion 5 is about the instant before mutation.

### M13 — RETIRED (historical): stranded record collapses into recovery

- **file** `app/git_transaction.py`
- **selection** `tests/test_outbox.py`
- **result** RED — `test_a_rollback_that_cannot_restore_the_record_reports_it_as_stranded`

Not in the campaign after S7 Stage 2. Quarantine now happens only after the
commit succeeds, so rollback cannot own a quarantined proposal and cannot
produce the stranded state this row guarded.

```diff
-        if stranded_records:
+        if False:  # MUTANT M13
```

### M14 — RETIRED (historical): cleanup overwrites stranded outcome

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

Not in the campaign after S7 Stage 2, for the same reachability reason as
M13: the transaction cannot strand a proposal during rollback.

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

### M17 — restoring by name after an identity mismatch

- **file** `app/git_transaction.py`
- **selection** `tests/test_git_transaction.py`
- **result** RED — `test_a_substituted_quarantine_entry_is_refused_and_nothing_further_moves`

```diff
             raise QuarantineEntrySubstituted(path, link_count)
+            _restore()  # MUTANT M17
```

Amendment 2. Identity verification catches a rebound quarantine name, but
*restoring* by that name afterwards is the harm, not the remedy: the name is
known to hold the substitute, so the restore moves an object nobody reviewed
under the reviewed record's name — OneOS performing the substitution itself,
as a deliberate step of a refusal.

Measured on the implementation that had the identity check but still
restored: no link to the reviewed inode anywhere in the vault, the proposal's
own name holding the decoy's bytes, and an outcome of `E-CONFLICT` with
`committed=no`. Both halves false at once.

The test this mutation turns red is the rewritten one. Its predecessor ended
in `assert leaf.exists() or list(...)`, which the decoy satisfied — it could
not distinguish the reviewed record surviving from a substitute standing in
its place, which is the only question the scenario asks. The replacement
names every inode and asserts which object is where.

### M18 — RETIRED (historical): rollback diagnosis ignores identity

- **file** `app/git_transaction.py`
- **selection** `tests/test_outbox.py tests/test_registry.py`
- **result** RED — both transactional actions:
  - `test_approve_reports_a_substitution_that_lands_before_rollback`
    — `assert 'E-RETAINED' == 'E-SUBSTITUTED'`
  - `test_delete_reports_a_substitution_that_lands_before_rollback`
    — `assert 'E-RETAINED' == 'E-SUBSTITUTED'`

```diff
-            if (landed.st_dev, landed.st_ino) != (identity.st_dev, identity.st_ino):
+            if False:  # MUTANT M18: rollback ignores identity
```

The seam only a transaction has: consumption succeeds, the transaction then
fails, and a writer replaces the quarantine entry before rollback diagnoses
it. Without the identity check the diagnosis reports the record safely
retained and claims `committed=no` — a claim resting on the reviewed bytes
being verifiably in quarantine, which they are not. Both nodes are required,
because the row asserts the guarantee for approve *and* registry delete.

Not in the campaign after S7 Stage 2. The diagnosis function and both
transactional rollback seams were removed when quarantine became the final
post-commit mutation.

### M19 — RETIRED (historical): rollback failure outranks retained record

- **file** `app/git_transaction.py`
- **selection** `tests/test_outbox.py`
- **result** RED — `test_a_rollback_failure_composes_onto_the_retained_record`
  — `QuarantinedRecordRetained`

```diff
-            if isinstance(primary, QuarantinedRecordRetained) and blocked_paths:
+            if False:  # MUTANT M19: retained outranks a rollback failure
```

`E-RETAINED`'s third precondition is that every other change rolled back.
When one did not, `committed=no` is unavailable and the indeterminate outcome
must take over, with the retained record composed onto it rather than dropped.

Not in the campaign after S7 Stage 2. `E-RETAINED` and its only producer were
removed with the rollback diagnosis path.

Retirement was measured before deletion, not inferred from the call graph.
Replacing the `QuarantinedRecordRetained` producer, then independently the
`QuarantineRestorationBlocked` producer, left the complete action-level
transaction selection green both times: `108 passed, 7 deselected`. The seven
deselected nodes called the historical diagnosis function directly. This
deliberate ALIVE result is why M13, M14, M18, and M19 are historical evidence
rather than live campaign rows.

### M20 — transaction-owned descriptors are never released

- **file** `app/git_transaction.py`
- **selection** `tests/test_git_transaction.py`
- **result** RED — both paths:
  - `test_a_transaction_closes_every_descriptor_it_took_ownership_of`
    — `a descriptor was leaked on success`
  - `test_a_failed_transaction_closes_the_descriptor_it_owns`
    — `a descriptor was leaked on failure`

```diff
-        for _change, _record in quarantined:
+        for _change, _record in []:  # MUTANT M20: descriptors leak
             # Not guarded.
```

Amendment 3 makes the transaction the owner of each record's descriptor,
held through commit and rollback diagnosis. Both nodes are required: the
release must happen on success and on failure, and the failure path is the
one where an exception could otherwise skip it.

### M21 — `st_nlink` evidence is assumed rather than observed

- **file** `app/git_transaction.py`
- **selection** `tests/test_git_transaction.py`
- **result** RED —
  `test_link_count_evidence_reports_both_outcomes_without_changing_the_message[True]`
  — `assert 0 > 0`

```diff
-                link_count = os.fstat(descriptor).st_nlink
+                link_count = 0  # MUTANT M21: evidence hardcoded
```

The design specifies both readings. Only the zero case was covered, so a
mutant behaving differently above zero survived. The `[True]` node drives the
positive reading via a second hard link to the reviewed inode, and asserts the
operator message is identical either way — the evidence may not vary it.

### M22 — a disappeared quarantine entry escapes unclassified

- **file** `app/git_transaction.py`
- **selection** `tests/test_outbox.py`
- **result** RED —
  `test_reject_refuses_every_post_move_quarantine_condition[absent]`
  — `assert 'E-UNKNOWN' == 'E-SUBSTITUTED'`

```diff
-            raise _substituted("absent") from exc
+            raise  # MUTANT M22: disappearance escapes unclassified
```

Reject runs no transaction and has no rollback, so the consumption primitive
is its only exposure. A removed entry previously escaped as a bare
`FileNotFoundError`, resolving to E-UNKNOWN — "an unexpected error was not
handled" for a condition the taxonomy describes exactly.

### M23 — an in-place rewrite goes unnoticed

- **file** `app/git_transaction.py`
- **selection** `tests/test_outbox.py`
- **result** RED —
  `test_reject_refuses_every_post_move_quarantine_condition[rewritten]`
  — `DID NOT RAISE`

```diff
-        if _held_state(descriptor) != expected:
+        if False:  # MUTANT M23: in-place rewrite goes unnoticed
             raise _substituted("rewritten")
```

The post-move contents check is the only thing that can see this: an in-place
rewrite keeps the same device, inode and name, so every identity check passes
while the bytes are no longer the reviewed bytes. Bypassing it lets OneOS
consume bytes nobody reviewed with no rename involved at all. This is why the
check survived Amendment 3 even though its old remedy did not.

### M24 — approval receipt omitted from filesystem changes

- **file** `app/outbox.py`
- **selection** `tests/test_console_invariants.py`
- **result** RED — `test_stage2_receipt_has_exact_transaction_roles[approve]`
  — `approval receipt is not a filesystem change`

```diff
-            PathChange(
-                receipt_rel,
-                PathState.absent(),
-                PathState.regular(render_action_receipt(receipt), 0o644),
-                create_parent=True,
-            ),
+            # MUTANT M24: receipt omitted from filesystem changes
```

### M24b — registry-delete receipt omitted from filesystem changes

- **file** `app/registry.py`
- **selection** `tests/test_console_invariants.py`
- **result** RED — `test_stage2_receipt_has_exact_transaction_roles[registry-delete]`
  — `registry deletion receipt is not a filesystem change`

```diff
-                PathChange(
-                    receipt_rel,
-                    PathState.absent(),
-                    PathState.regular(render_action_receipt(receipt), 0o644),
-                    create_parent=True,
-                ),
+                # MUTANT M24b: receipt omitted from filesystem changes
```

### M25 — approval receipt omitted from exact commit paths

- **file** `app/outbox.py`
- **selection** `tests/test_console_invariants.py`
- **result** RED — `test_stage2_receipt_has_exact_transaction_roles[approve]`
  — `approval receipt is not an exact commit path`

```diff
-        commit_paths=(prop.src, prop.dst, receipt_rel),
+        commit_paths=(prop.src, prop.dst),  # MUTANT M25
```

### M25b — registry-delete receipt omitted from exact commit paths

- **file** `app/registry.py`
- **selection** `tests/test_console_invariants.py`
- **result** RED — `test_stage2_receipt_has_exact_transaction_roles[registry-delete]`
  — `registry deletion receipt is not an exact commit path`

```diff
-            commit_paths=(registry_rel, receipt_rel),
+            commit_paths=(registry_rel,),  # MUTANT M25b
```

### M26 — approval receipt misclassified as an owned change

- **file** `app/outbox.py`
- **selection** `tests/test_console_invariants.py`
- **result** RED — `test_stage2_receipt_has_exact_transaction_roles[approve]`
  — `tracked receipt was misclassified as an untracked owned change`

```diff
-            PathChange(proposal_rel, proposal_state, PathState.absent()),
+            PathChange(receipt_rel, proposal_state, PathState.absent()),  # MUTANT M26
```

### M26b — registry-delete receipt misclassified as an owned change

- **file** `app/registry.py`
- **selection** `tests/test_console_invariants.py`
- **result** RED — `test_stage2_receipt_has_exact_transaction_roles[registry-delete]`
  — `tracked receipt was misclassified as an untracked owned change`

```diff
-                PathChange(proposal_rel, proposal_state, PathState.absent()),
+                PathChange(receipt_rel, proposal_state, PathState.absent()),  # MUTANT M26b
```

M24–M26 each have an independent registry-delete row because approval and
registry deletion construct their transaction plans at different sites. A
single approval mutation would not prove the duplicated registry path.

### M27 — receipt authority read from the working tree

- **file** `app/action_receipts.py`
- **selection** `tests/test_console_invariants.py`
- **result** RED — `test_stage2_receipt_authority_comes_only_from_git_head`
  — `receipt authority no longer comes from Git HEAD`

```diff
     expressions = tuple(
         f"HEAD:{receipt_relative_path(entity, proposal_id)}" for proposal_id in ids
     )
-    objects = _batch_objects(vault_path, expressions)
+    objects = tuple(
+        _BatchObject("blob", (vault_path / receipt_relative_path(entity, proposal_id)).read_bytes())
+        if (vault_path / receipt_relative_path(entity, proposal_id)).exists() else None
+        for proposal_id in ids
+    )  # MUTANT M27: working-tree authority
```

### M28 — approval checks the receipt before the lock

- **file** `app/outbox.py`
- **selection** `tests/test_console_invariants.py`
- **result** RED — `test_stage2_spent_id_checks_are_locked_preconditions[approve]`
  — `approve receipt check no longer runs only under the lock`

```diff
-        preconditions=(_require_unspent_id,),
+        preconditions=(_require_unspent_id,) if _require_unspent_id() is None else (),  # MUTANT M28
```

### M28b — registry deletion checks the receipt before the lock

- **file** `app/registry.py`
- **selection** `tests/test_console_invariants.py`
- **result** RED — `test_stage2_spent_id_checks_are_locked_preconditions[registry-delete]`
  — `registry delete receipt check no longer runs only under the lock`

```diff
-            preconditions=(_require_unspent_id, _require_no_live_references),
+            preconditions=(
+                _require_unspent_id, _require_no_live_references
+            ) if _require_unspent_id() is None else (),  # MUTANT M28b
```

### M29 — reject omits its standalone locked receipt check

- **file** `app/outbox.py`
- **selection** `tests/test_console_invariants.py`
- **result** RED — `test_stage2_spent_id_checks_are_locked_preconditions[reject]`
  — `reject omitted its locked spent-id check`

```diff
-            preconditions=(_require_unspent_id,),
+            preconditions=(),  # MUTANT M29
```

### M30 — stored audit digest compared with pending bytes

- **file** `app/outbox.py`
- **selection** `tests/test_console_projection.py`
- **result** RED — `test_receipt_first_projection_never_opens_any_spent_leaf_shape[different]`
  — `the different spent leaf or its target was opened`

```diff
-            if resolution.receipt is not None:
+            if (
+                resolution.receipt is not None
+                and resolution.receipt.review_sha256
+                == hashlib.sha256(discovered.read_bytes()).hexdigest()
+            ):  # MUTANT M30: audit digest compared with pending bytes
```

The first draft merely tested whether the digest string was truthy. That was
a semantic no-op because the closed schema always supplies 64 hex characters.
This row performs the forbidden comparison and is killed by the spent-leaf
no-open proof itself.

### M31 — classification opens the pending record before receipt resolution

- **file** `app/outbox.py`
- **selection** `tests/test_console_projection.py`
- **result** RED — `test_matching_receipt_projects_spent_without_opening_pending_record`
  — `a spent proposal record was opened`

```diff
+            review_snapshot_for(scope, discovered)  # MUTANT M31
             if resolution.receipt is not None:
```

### M31b — registry refresh opens the pending record before receipt resolution

- **file** `app/registry.py`
- **selection** `tests/test_console_routes.py`
- **result** RED — `test_delete_refresh_never_opens_any_spent_leaf_shape[different]`
  — `the different spent delete leaf or its target was opened`

```diff
+    get_delete_review(scope, canonical_id)  # MUTANT M31b: opened before receipt
     if resolution.receipt is not None:
         return resolution.receipt
```

M31 and M31b are separate because classification projection and registry
refresh have independent receipt-first implementations.

### M32 — non-tree receipt root treated as an empty store

- **file** `app/action_receipts.py`
- **selection** `tests/test_console_invariants.py`
- **result** RED — `test_stage2_non_tree_receipt_root_fails_closed`
  — `non-tree receipt root was treated as an empty store`

```diff
-        raise ReceiptStoreIntegrityError("receipt store root is not a Git tree")
+        return False  # MUTANT M32: non-tree store treated as absent
```

This is the load-bearing distinction between the forced empty-store rule and
tampering: an absent tree is a fresh vault; a blob committed at `.receipts`
must block rather than make every lookup appear absent.

### M33 — one malformed receipt blocks the entire entity

- **file** `app/outbox.py`
- **selection** `tests/test_console_invariants.py`
- **result** RED — `test_stage2_malformed_receipt_projection_stays_per_id`
  — `malformed matching receipt became entity-wide blocking`

```diff
             if resolution.error is not None:
+                blocked = True  # MUTANT M33
                 rows.append(
```

### M34 — proposal quarantine moves before the action commit

- **file** `app/git_transaction.py`
- **selection** `tests/test_console_invariants.py`
- **result** RED — `test_stage2_proposal_quarantine_stays_after_the_action_commit`
  — `a proposal was quarantined before its action committed`

```diff
+        for change in plan.owned_changes:
+            quarantined.append((
+                change, quarantine_path_if_unchanged(vault, change.path, change.before)
+            ))  # MUTANT M34
         _checkpoint("filesystem-applied")
```

The first probe used the existing pre-commit failure test. It went red only
when that test attempted to read the now-missing proposal, before reaching
its intended quarantine assertion. The permanent row instead binds to an
executable-order invariant and its unique diagnostic.

### M35 — post-commit consumption failure rolls back action and receipt

- **file** `app/git_transaction.py`
- **selection** `tests/test_console_invariants.py`
- **result** RED — `test_stage2_postcommit_consumption_failure_has_no_rollback_path`
  — `post-commit consumption failure can roll back action and receipt`

```diff
+        _git(vault, "reset", "--hard", start_head)  # MUTANT M35
         raise applied from exc
```

### M36 — unresolved consumption mapped as ordinary committed cleanup

- **file** `app/console_errors.py`
- **selection** `tests/test_console_invariants.py`
- **result** RED — `test_every_application_exception_resolves_to_its_designed_code`
  — `PostCommitConsumptionError`

```diff
-    _git_transaction.PostCommitConsumptionError: _CODES["E-APPLIED"],
+    _git_transaction.PostCommitConsumptionError: _CODES["E-COMMITTED"],  # MUTANT M36
```

### M37 — HTMX stops swapping 500 attention fragments

- **file** `templates/_head.html`
- **selection** `tests/test_console_invariants.py`
- **result** RED — `test_receipt_attention_fragments_remain_swappable_at_500`
  — `E-APPLIED 500 fragment no longer swaps`

```diff
-{"code":"[45]..","swap":true,"error":true}
+{"code":"[45]..","swap":false,"error":true}
```

### M38 — spent card regains a live action

- **file** `templates/blocks/action_receipt_card.html`
- **selection** `tests/test_console_invariants.py`
- **result** RED — `test_action_receipt_card_has_no_review_or_mutation_transport`
  — `receipt card regained action/review transport: hx-post`

```diff
 <div class="proposal action-receipt"
-     id="receipt-card-{{ proposal_id }}-{{ issue }}">
+     id="receipt-card-{{ proposal_id }}-{{ issue }}"
+     hx-post="/outbox/{{ entity }}/approve">  {# MUTANT M38 #}
```

### M39 — request path enumerates every accumulated receipt

- **file** `app/outbox.py`
- **selection** `tests/test_console_invariants.py`
- **result** RED — `test_full_receipt_store_enumeration_is_offline_audit_only`
  — `request path enumerates the accumulated receipt store: app/outbox.py`

```diff
+    from .action_receipts import validate_head_receipt_store
+    validate_head_receipt_store(
+        scope.root, scope.current_entity()
+    )  # MUTANT M39: enumerate accumulated receipts in request
     receipts = resolve_head_receipts(
         scope.root, scope.current_entity(), canonical_ids.values()
     )
```

An earlier mutant aliased the two-argument validator to the three-argument
batch resolver. It failed on a `TypeError` before enumeration ran, while the
import-only structural check still went red. This row executes a well-typed
full-store validation before retaining the normal batched lookup.

### M40 — orphan guard allows a dead outcome

- **file** `tests/test_console_invariants.py`
- **selection** `tests/test_console_invariants.py`
- **result** RED — `test_a_dead_mapping_cannot_impersonate_an_executable_producer`
  — `orphan guard allowed E-APPLIED to impersonate a live outcome`

```diff
-        - {"E-UNKNOWN", "E-REQUEST"}
+        - {"E-UNKNOWN", "E-REQUEST", "E-APPLIED"}  # MUTANT M40
```

### M41 — retired outcome returns without an executable producer

- **file** `app/console_errors.py`
- **selection** `tests/test_console_invariants.py`
- **result** RED — `test_every_operator_outcome_has_an_executable_producer`
  — `orphan operator outcome: E-RETAINED`

```diff
+_CODES["E-RETAINED"] = ConsoleError(
+    "E-RETAINED", "integrity", "attention", "retired outcome",
+    "stop", "no", 500,
+)  # MUTANT M41
+
 UNKNOWN = _CODES["E-UNKNOWN"]
```

M40 proves the producer guard itself cannot be weakened into an allowlist;
M41 proves an outcome retired by quarantine-last cannot silently reappear as
a live taxonomy row. Retired M18/M19 remain historical-only evidence and are
absent from the runner.

### M42 — acted-on outbox receipt card is lost after consumption

- **file** `app/main.py`
- **selection** `tests/test_console_routes.py`
- **result** RED — both replay nodes report
  `a spent replay lost its receipt-backed card`; the post-move failure node
  reports `E-APPLIED lost the acted-on receipt card after consumption`

```diff
-    rows = _with_action_receipt_row(rows, scope, action_receipt)
+    rows = rows  # MUTANT M42: absent acted-on receipt card
```

Required exact nodes:

- `test_outbox_spent_replay_keeps_the_card_after_the_record_is_consumed[approve]`
- `test_outbox_spent_replay_keeps_the_card_after_the_record_is_consumed[reject]`
- `test_approve_post_move_failure_keeps_applied_alert_and_spent_card`

This proves that a response does not fall back to “No pending proposals” merely
because quarantine removed the acted-on leaf. Both replay and the
committed-but-unconsumed `E-APPLIED` response retain the receipt-backed card.

### M43 — spent-card copy treats any name as a pending record

- **file** `templates/blocks/outbox_list.html`
- **selection** `tests/test_console_routes.py`
- **result** RED — `test_outbox_receipt_card_does_not_call_a_directory_a_pending_record`
  — `a non-regular outbox entry was presented as a real pending record`

```diff
-          record_present=row.record_present %}
+          record_present=true %}  {# MUTANT M43 #}
```

The lingering-record sentence is presentation evidence, not receipt authority.
It appears only when checked metadata confirms a real regular pending leaf.

### M43b — projection infers record presence without checked metadata

- **file** `app/outbox.py`
- **selection** `tests/test_console_projection.py`
- **result** RED —
  `test_receipt_first_projection_never_opens_any_spent_leaf_shape[non-file]`
  — `the non-file spent row misreported whether a real pending record exists`

```diff
-                record_present = pending_proposal_entry_exists(
-                    scope, canonical_id
-                )
+                record_present = True  # MUTANT M43b
```

M43 guards the rendered consequence; M43b separately binds the row flag to the
existing no-follow metadata boundary so template correctness cannot hide a
false service value.

### M44 — retirement reconciliation returns to a hand-maintained subset

- **file** `docs/superpowers/plans/s7_mutation_campaign.py`
- **selection** `tests/test_console_invariants.py`
- **result** RED — `test_stage2_retired_outcomes_are_historical_evidence_only`
  — `M13 returned to the live campaign`

```diff
-        "M1", "app/outbox.py",
+        "M13", "app/outbox.py",  # MUTANT M44: retired id returned
```

The invariant derives every exact `RETIRED (historical)` heading from this
ledger, checks each heading is unique, and refuses every such id in the live
runner. It therefore cannot drift back to an M18/M19-only list while older
retirements silently return.

### M45 — blocked-list rebuilding drops pending-record evidence

- **file** `app/outbox.py`
- **selection** `tests/test_console_projection.py`
- **result** RED —
  `test_blocked_listing_preserves_spent_rows_pending_record_evidence`
  — `blocked-row reconstruction lost the pending-record evidence`

```diff
-                record_present=row.record_present,
+                record_present=False,  # MUTANT M45
```

Entity-wide blocking withholds controls and review fingerprints, but it must
not erase the independent checked-metadata fact used by the persistent receipt
card. Otherwise one malformed sibling makes the real lingering proposal file
disappear from the operator's do-not-touch explanation.

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

- **Linux `renameat2(RENAME_NOREPLACE)`** — unexercised, and now permanently
  so within S7. This session is macOS, where `renameatx_np(RENAME_EXCL)` was
  measured directly. Amendment 1 originally made the Linux success path and its
  occupied-destination refusal a completion condition; that requirement was
  **withdrawn on 2026-08-25** by product-owner decision, because no Linux host
  is available, and is recorded as a known Linux limitation in the design. The
  campaign therefore certifies nothing about Linux, and no row should be read
  as doing so.
- **Private boundaries are outside the mutation campaign.** They were verified
  separately at the trusted local boundary: 37 private tests passed,
  `check_v2` reported 0 errors/0 warnings, the combined repo+vault history
  audit was clean, and the captured HEAD, porcelain-v2 status, binary worktree
  diff, and binary cached diff were byte-identical before and after. The
  campaign itself makes no claim about those private checks.
