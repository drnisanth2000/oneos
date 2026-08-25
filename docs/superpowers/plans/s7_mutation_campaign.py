#!/usr/bin/env python3
"""Run S7's mutation campaign, exactly as the ledger records it.

Each mutation is one exact string substitution. For each, this script:

  1. asserts the target file is clean, so a mutation is never applied on top
     of unrelated edits and a "restore" never discards them;
  2. asserts the OLD text appears exactly once, so a stale edit cannot
     silently mutate nothing and be recorded as evidence;
  3. applies it and runs the named pytest selection **in the project
     environment** (`uv run`), because a bare `python3` without pytest exits
     non-zero having run nothing — which looks exactly like a surviving
     mutant unless the return code is checked;
  4. distinguishes a *test failure* (pytest exit 1) from *infrastructure
     failure* (any other non-zero exit, or an empty collection), and refuses
     to score the latter as evidence either way;
  5. requires the named test to be among the failures — not merely that
     something failed;
  6. restores the file from an in-memory pre-image, verifies byte-identity,
     and **re-runs the same selection to confirm it is green again**; and
  7. after the whole group, runs the full public suite.

Read-only Git (`status --porcelain`) is used for the cleanliness check.
Nothing here ever runs a *destructive* Git command: a `git checkout` or
`git clean` in a shared tree could erase unrelated work, and a mutation
harness is the last place that should be possible.

Usage:
    uv run python docs/superpowers/plans/s7_mutation_campaign.py --list
    uv run python docs/superpowers/plans/s7_mutation_campaign.py [--only M6]
"""
from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[3]

#: (id, file, OLD, NEW, selection, [(exact node id, its intended diagnostic)])
#:
#: Each expected node is a FULL node id and is run **on its own**. Checking
#: "the node appears in some FAILED line" and "the diagnostic appears
#: somewhere in the output" independently would accept a run where the
#: expected node failed for an unrelated reason while a *different* test
#: emitted the diagnostic. Running one node at a time makes the binding
#: structural: the only failure the output can contain is that node's.
MUTATIONS = [
    (
        "M1", "app/outbox.py",
        "    review_digest = require_review_match(proposal_state.contents, review_sha256)",
        '    review_digest = hashlib.sha256(proposal_state.contents).hexdigest()  # MUTANT M1',
        ["tests/test_outbox.py"],
        [
            ("tests/test_outbox.py::test_actions_refuse_a_malformed_fingerprint_without_mutation[None-approve]",
             "DID NOT RAISE InvalidReviewToken"),
            ("tests/test_outbox.py::test_actions_refuse_a_malformed_fingerprint_without_mutation[None-reject]",
             "DID NOT RAISE InvalidReviewToken"),
        ],
    ),
    (
        "M3", "app/registry.py",
        "        review_digest = require_review_match(\n"
        "            proposal_state.contents, review_sha256\n"
        "        )",
        '        review_digest = "0" * 64  # MUTANT M3',
        ["tests/test_registry.py"],
        [("tests/test_registry.py::test_execute_delete_refuses_a_malformed_fingerprint[None]",
          "DID NOT RAISE InvalidReviewToken")],
    ),
    (
        "M4b", "app/outbox.py",
        "    return make_review_snapshot(_require_destination(scope, proposal), contents)",
        "    return make_review_snapshot(_require_destination(scope, proposal), leaf.read_bytes())  # MUTANT M4b",
        ["tests/test_console_projection.py", "tests/test_outbox.py"],
        [("tests/test_console_projection.py::test_the_projected_value_and_hash_come_from_one_capture",
          "the operator's fingerprint is not of the captured bytes")],
    ),
    (
        "M5", "app/outbox.py",
        "            PathChange(proposal_rel, proposal_state, PathState.absent()),",
        "            PathChange(proposal_rel, capture_path_state(vault, proposal_rel), PathState.absent()),  # MUTANT M5",
        ["tests/test_outbox.py"],
        [("tests/test_outbox.py::test_approve_owns_the_reviewed_state_not_whatever_arrives_later",
          "DID NOT RAISE Exception")],
    ),
    (
        "M5b", "app/registry.py",
        "                PathChange(proposal_rel, proposal_state, PathState.absent()),",
        "                PathChange(proposal_rel, capture_path_state(vault, proposal_rel), PathState.absent()),  # MUTANT M5b",
        ["tests/test_registry.py"],
        [("tests/test_registry.py::test_delete_owns_the_reviewed_state_not_whatever_arrives_later",
          "DID NOT RAISE Exception")],
    ),
    (
        "M6", "app/outbox.py",
        "        result = consume_reviewed_proposal(\n"
        "            scope.root,\n"
        "            proposal_rel,\n"
        "            proposal_state,\n"
        "            preconditions=(_require_unspent_id,),\n"
        "        )",
        "        result = consume_reviewed_proposal(\n"
        "            scope.root,\n"
        "            proposal_rel,\n"
        "            capture_path_state(scope.root, proposal_rel),  # MUTANT M6\n"
        "            preconditions=(_require_unspent_id,),\n"
        "        )",
        ["tests/test_outbox.py"],
        [("tests/test_outbox.py::test_reject_owns_the_reviewed_state_not_whatever_arrives_later",
          "DID NOT RAISE Exception")],
    ),
    (
        "M7", "templates/blocks/outbox_card.html",
        '  {% set outbox_values = {"id": row.proposal.id,\n                          "review_sha256": row.review_sha256} %}',
        '  {% set outbox_values = {"id": row.proposal.id} %}  {# MUTANT M7 #}',
        ["tests/test_console_routes.py", "tests/test_console_invariants.py"],
        [("tests/test_console_routes.py::test_outbox_hx_vals_are_tojson",
          "does not transport exactly id + review_sha256")],
    ),
    (
        "M7b", "templates/blocks/delete_impact.html",
        '  {% set delete_execute_values = {"id": prop.id,\n                                  "review_sha256": review_sha256} %}',
        '  {% set delete_execute_values = {"id": prop.id} %}  {# MUTANT M7b #}',
        ["tests/test_console_routes.py", "tests/test_console_invariants.py"],
        [("tests/test_console_routes.py::test_delete_preview_hx_vals_survive_hostile_slug",
          "does not transport exactly id + review_sha256")],
    ),
    (
        "M8", "app/main.py",
        "        prop = execute_delete(scope, id, review_sha256)",
        '        prop = get_delete_review(scope, id).value\n        execute_delete(scope, id, review_sha256)  # MUTANT M8',
        ["tests/test_console_routes.py", "tests/test_console_invariants.py"],
        [("tests/test_console_invariants.py::test_delete_success_copy_comes_from_the_bound_execution",
          "reads a review for display instead of using the")],
    ),
    (
        "M9", "app/outbox.py",
        '    if candidate.is_symlink():\n        raise RedirectedPathError("proposal leaf is redirected")',
        '    try:\n        _p = open(candidate, "rb")\n    except OSError:\n        pass\n    else:\n        _p.close()\n    if candidate.is_symlink():\n        raise RedirectedPathError("proposal leaf is redirected")  # MUTANT M9',
        ["tests/test_console_routes.py"],
        [("tests/test_console_routes.py::test_check_again_never_reads_through_a_redirected_leaf[outbox]",
          "the redirected target was opened")],
    ),
    (
        "M10", "app/registry.py",
        "            report = reference_count(scope, prop.kind, prop.slug)\n            if report.total:",
        '            path.write_bytes(path.read_bytes() + b"# MUTANT\\n")\n            report = reference_count(scope, prop.kind, prop.slug)\n            if report.total:',
        ["tests/test_registry.py"],
        [("tests/test_registry.py::test_the_delete_no_mutation_matrix[new-live-reference]",
          "delete refusal mutated vault state")],
    ),
    (
        "M12", "app/registry.py",
        "            preconditions=(_require_unspent_id, _require_no_live_references),",
        "            preconditions=(_require_unspent_id,),  # MUTANT M12",
        ["tests/test_registry.py"],
        [("tests/test_registry.py::test_the_reference_recount_holds_the_approval_lock",
          "the reference count never ran")],
    ),
    (
        # The two action-boundary parses. Both were equivalent mutants until
        # M15/M16's tests existed: `PathState` compares full contents, so a
        # replacement still on disk at transaction time is refused either
        # way. Only a replacement restored *before* the transaction tells
        # the two implementations apart.
        "M15", "app/outbox.py",
        "    record = _parse_record_bytes(proposal_state.contents)",
        "    record = _parse_record_bytes((vault / proposal_rel).read_bytes())  # MUTANT M15",
        ["tests/test_outbox.py"],
        [("tests/test_outbox.py::test_approve_parses_the_bytes_it_compared_not_a_fresh_read",
          "approve chose a destination from a reread")],
    ),
    (
        "M16", "app/registry.py",
        "            scope, path, _parse_delete_record(proposal_state.contents)",
        "            scope, path, _parse_delete_record(path.read_bytes())  # MUTANT M16",
        ["tests/test_registry.py"],
        [("tests/test_registry.py::test_execute_delete_parses_the_bytes_it_compared_not_a_fresh_read",
          "delete chose a target from a reread")],
    ),
    (
        # Amendment 2. Restoring by name after an identity mismatch moves
        # the *substitute* under the reviewed record's name — OneOS
        # installing an object nobody reviewed, as a step of a refusal.
        # Re-expressed for Amendment 3. M17 used to reinstate a `_restore()`
        # call; that helper no longer exists, because no rename-back
        # survives anywhere in the consumption path. The defect it guards
        # against is now reachable only by inlining the move — which is
        # exactly the shape a future edit would take if someone decided a
        # mismatch should "just put it back".
        "M17", "app/git_transaction.py",
        '            raise _substituted("replaced")',
        '            _move_no_replace(\n'
        '                quarantine_descriptor, leaf, parent_descriptor, leaf\n'
        '            )  # MUTANT M17: rename back after an identity mismatch\n'
        '            raise _substituted("replaced")',
        ["tests/test_git_transaction.py", "tests/test_outbox.py"],
        [
            ("tests/test_git_transaction.py::test_a_substituted_quarantine_entry_is_refused_and_nothing_further_moves",
             "assert [] == ['outbox-record.yaml']"),
            ("tests/test_outbox.py::test_reject_refuses_every_post_move_quarantine_condition[replaced]",
             "a substitute was renamed back under the record's name"),
        ],
    ),
    (
        # Amendment 3. The transaction owns each quarantined record's
        # descriptor and must release it on every path.
        "M20", "app/git_transaction.py",
        "        for _change, _record in quarantined:\n            # Not guarded.",
        "        for _change, _record in []:  # MUTANT M20: descriptors leak\n            # Not guarded.",
        ["tests/test_git_transaction.py"],
        [
            ("tests/test_git_transaction.py::test_a_transaction_closes_every_descriptor_it_took_ownership_of",
             "a descriptor was leaked on success"),
            ("tests/test_git_transaction.py::test_a_failed_transaction_closes_the_descriptor_it_owns",
             "a descriptor was leaked on failure"),
        ],
    ),
    (
        # Amendment 2/3. `st_nlink` is diagnostic evidence and must be
        # observed, not assumed.
        "M21", "app/git_transaction.py",
        "                link_count = os.fstat(descriptor).st_nlink",
        "                link_count = 0  # MUTANT M21: evidence hardcoded",
        ["tests/test_git_transaction.py"],
        [("tests/test_git_transaction.py::test_link_count_evidence_reports_both_outcomes_without_changing_the_message[True]",
          "assert 0 > 0")],
    ),
    (
        # Amendment 3. Reject has no transaction and no rollback, so the
        # consumption primitive is its only exposure. A disappeared
        # quarantine entry must not escape as a bare FileNotFoundError.
        "M22", "app/git_transaction.py",
        '            raise _substituted("absent") from exc',
        "            raise  # MUTANT M22: disappearance escapes unclassified",
        ["tests/test_outbox.py"],
        [("tests/test_outbox.py::test_reject_refuses_every_post_move_quarantine_condition[absent]",
          "assert 'E-UNKNOWN' == 'E-SUBSTITUTED'")],
    ),
    (
        # Amendment 3. The post-move contents check is the only thing that
        # can see an in-place rewrite: identity is unchanged, so every
        # other check passes. Bypassing it lets OneOS consume bytes nobody
        # reviewed without any rename being involved.
        "M23", "app/git_transaction.py",
        "        if _held_state(descriptor) != expected:\n            raise _substituted(\"rewritten\")",
        "        if False:  # MUTANT M23: in-place rewrite goes unnoticed\n            raise _substituted(\"rewritten\")",
        ["tests/test_outbox.py"],
        [("tests/test_outbox.py::test_reject_refuses_every_post_move_quarantine_condition[rewritten]",
          "DID NOT RAISE")],
    ),
    (
        "M11", "app/main.py",
        "        prop = execute_delete(scope, id, review_sha256)",
        '        _unused = review_sha256  # MUTANT M11\n        prop = execute_delete(scope, id, "0" * 64)',
        ["tests/test_console_invariants.py"],
        [("tests/test_console_invariants.py::test_every_reviewed_route_requires_and_passes_the_fingerprint",
          "never passes review_sha256 to execute_delete")],
    ),
    (
        # Stage 2. A receipt omitted from `changes` can never be written by
        # the transaction, even if somebody leaves it in `commit_paths`.
        "M24", "app/outbox.py",
        "            PathChange(\n"
        "                receipt_rel,\n"
        "                PathState.absent(),\n"
        "                PathState.regular(render_action_receipt(receipt), 0o644),\n"
        "                create_parent=True,\n"
        "            ),",
        "            # MUTANT M24: receipt omitted from filesystem changes",
        ["tests/test_console_invariants.py"],
        [("tests/test_console_invariants.py::test_stage2_receipt_has_exact_transaction_roles[approve]",
          "approval receipt is not a filesystem change")],
    ),
    (
        "M24b", "app/registry.py",
        "                PathChange(\n"
        "                    receipt_rel,\n"
        "                    PathState.absent(),\n"
        "                    PathState.regular(render_action_receipt(receipt), 0o644),\n"
        "                    create_parent=True,\n"
        "                ),",
        "                # MUTANT M24b: receipt omitted from filesystem changes",
        ["tests/test_console_invariants.py"],
        [("tests/test_console_invariants.py::test_stage2_receipt_has_exact_transaction_roles[registry-delete]",
          "registry deletion receipt is not a filesystem change")],
    ),
    (
        # Stage 2. The receipt must be inside the exact commit, not merely
        # left as an untracked working-tree side effect.
        "M25", "app/outbox.py",
        "        commit_paths=(prop.src, prop.dst, receipt_rel),",
        "        commit_paths=(prop.src, prop.dst),  # MUTANT M25",
        ["tests/test_console_invariants.py"],
        [("tests/test_console_invariants.py::test_stage2_receipt_has_exact_transaction_roles[approve]",
          "approval receipt is not an exact commit path")],
    ),
    (
        "M25b", "app/registry.py",
        "            commit_paths=(registry_rel, receipt_rel),",
        "            commit_paths=(registry_rel,),  # MUTANT M25b",
        ["tests/test_console_invariants.py"],
        [("tests/test_console_invariants.py::test_stage2_receipt_has_exact_transaction_roles[registry-delete]",
          "registry deletion receipt is not an exact commit path")],
    ),
    (
        # Stage 2. `owned_changes` are required to be untracked and are
        # consumed after commit. A tracked receipt belongs to `changes`.
        "M26", "app/outbox.py",
        "            PathChange(proposal_rel, proposal_state, PathState.absent()),",
        "            PathChange(receipt_rel, proposal_state, PathState.absent()),  # MUTANT M26",
        ["tests/test_console_invariants.py"],
        [("tests/test_console_invariants.py::test_stage2_receipt_has_exact_transaction_roles[approve]",
          "tracked receipt was misclassified as an untracked owned change")],
    ),
    (
        "M26b", "app/registry.py",
        "                PathChange(proposal_rel, proposal_state, PathState.absent()),",
        "                PathChange(receipt_rel, proposal_state, PathState.absent()),  # MUTANT M26b",
        ["tests/test_console_invariants.py"],
        [("tests/test_console_invariants.py::test_stage2_receipt_has_exact_transaction_roles[registry-delete]",
          "tracked receipt was misclassified as an untracked owned change")],
    ),
    (
        # Stage 2. Deleting or replacing a working-tree receipt must not
        # re-enable an id whose receipt remains committed in HEAD.
        "M27", "app/action_receipts.py",
        "    expressions = tuple(\n"
        "        f\"HEAD:{receipt_relative_path(entity, proposal_id)}\" for proposal_id in ids\n"
        "    )\n"
        "    objects = _batch_objects(vault_path, expressions)",
        "    expressions = tuple(\n"
        "        f\"HEAD:{receipt_relative_path(entity, proposal_id)}\" for proposal_id in ids\n"
        "    )\n"
        "    objects = tuple(\n"
        "        _BatchObject(\"blob\", (vault_path / receipt_relative_path(entity, proposal_id)).read_bytes())\n"
        "        if (vault_path / receipt_relative_path(entity, proposal_id)).exists() else None\n"
        "        for proposal_id in ids\n"
        "    )  # MUTANT M27: working-tree authority",
        ["tests/test_console_invariants.py"],
        [("tests/test_console_invariants.py::test_stage2_receipt_authority_comes_only_from_git_head",
          "receipt authority no longer comes from Git HEAD")],
    ),
    (
        # Stage 2. Evaluating the precondition while constructing the plan
        # recreates the check-before-lock race even if the tuple remains.
        "M28", "app/outbox.py",
        "        owned_changes=(\n"
        "            PathChange(proposal_rel, proposal_state, PathState.absent()),\n"
        "        ),\n"
        "        preconditions=(_require_unspent_id,),",
        "        owned_changes=(\n"
        "            PathChange(proposal_rel, proposal_state, PathState.absent()),\n"
        "        ),\n"
        "        preconditions=(_require_unspent_id,) if _require_unspent_id() is None else (),  # MUTANT M28",
        ["tests/test_console_invariants.py"],
        [("tests/test_console_invariants.py::test_stage2_spent_id_checks_are_locked_preconditions[approve]",
          "approve receipt check no longer runs only under the lock")],
    ),
    (
        "M28b", "app/registry.py",
        "            preconditions=(_require_unspent_id, _require_no_live_references),",
        "            preconditions=(\n"
        "                _require_unspent_id, _require_no_live_references\n"
        "            ) if _require_unspent_id() is None else (),  # MUTANT M28b",
        ["tests/test_console_invariants.py"],
        [("tests/test_console_invariants.py::test_stage2_spent_id_checks_are_locked_preconditions[registry-delete]",
          "registry delete receipt check no longer runs only under the lock")],
    ),
    (
        # Stage 2. Reject has no TransactionPlan; its locked consume helper
        # is the only place its spent-id precondition can run safely.
        "M29", "app/outbox.py",
        "            preconditions=(_require_unspent_id,),",
        "            preconditions=(),  # MUTANT M29",
        ["tests/test_console_invariants.py"],
        [("tests/test_console_invariants.py::test_stage2_spent_id_checks_are_locked_preconditions[reject]",
          "reject omitted its locked spent-id check")],
    ),
    (
        # Stage 2. A committed receipt digest is audit evidence only. It must
        # never be compared with or used to classify the pending record.
        "M30", "app/outbox.py",
        "            if resolution.receipt is not None:",
        "            if (\n"
        "                resolution.receipt is not None\n"
        "                and resolution.receipt.review_sha256\n"
        "                == hashlib.sha256(discovered.read_bytes()).hexdigest()\n"
        "            ):  # MUTANT M30: audit digest compared with pending bytes",
        ["tests/test_console_projection.py"],
        [("tests/test_console_projection.py::test_receipt_first_projection_never_opens_any_spent_leaf_shape[different]",
          "the different spent leaf or its target was opened")],
    ),
    (
        # Stage 2. Receipt-first projection must stop before opening or
        # parsing whatever currently occupies the pending-record name.
        "M31", "app/outbox.py",
        "            if resolution.receipt is not None:",
        "            review_snapshot_for(scope, discovered)  # MUTANT M31\n"
        "            if resolution.receipt is not None:",
        ["tests/test_console_projection.py"],
        [("tests/test_console_projection.py::test_matching_receipt_projects_spent_without_opening_pending_record",
          "a spent proposal record was opened")],
    ),
    (
        "M31b", "app/registry.py",
        "    if resolution.receipt is not None:\n"
        "        return resolution.receipt",
        "    get_delete_review(scope, canonical_id)  # MUTANT M31b: opened before receipt\n"
        "    if resolution.receipt is not None:\n"
        "        return resolution.receipt",
        ["tests/test_console_routes.py"],
        [("tests/test_console_routes.py::test_delete_refresh_never_opens_any_spent_leaf_shape[different]",
          "the different spent delete leaf or its target was opened")],
    ),
    (
        # Stage 2. A committed blob at `.receipts` is not an empty store; it
        # is an integrity failure that must block the entity.
        "M32", "app/action_receipts.py",
        '        raise ReceiptStoreIntegrityError("receipt store root is not a Git tree")',
        "        return False  # MUTANT M32: non-tree store treated as absent",
        ["tests/test_console_invariants.py"],
        [("tests/test_console_invariants.py::test_stage2_non_tree_receipt_root_fails_closed",
          "non-tree receipt root was treated as an empty store")],
    ),
    (
        # Stage 2. A malformed matching receipt withholds only its own card;
        # unrelated pending ids remain independently actionable.
        "M33", "app/outbox.py",
        "            if resolution.error is not None:\n"
        "                rows.append(",
        "            if resolution.error is not None:\n"
        "                blocked = True  # MUTANT M33\n"
        "                rows.append(",
        ["tests/test_console_invariants.py"],
        [("tests/test_console_invariants.py::test_stage2_malformed_receipt_projection_stays_per_id",
          "malformed matching receipt became entity-wide blocking")],
    ),
    (
        # Stage 2. Quarantine-last is the crash-safety boundary: no proposal
        # may be consumed before its action and receipt commit exists.
        "M34", "app/git_transaction.py",
        '        _checkpoint("filesystem-applied")',
        "        for change in plan.owned_changes:\n"
        "            quarantined.append((\n"
        "                change, quarantine_path_if_unchanged(vault, change.path, change.before)\n"
        "            ))  # MUTANT M34\n"
        '        _checkpoint("filesystem-applied")',
        ["tests/test_console_invariants.py"],
        [("tests/test_console_invariants.py::test_stage2_proposal_quarantine_stays_after_the_action_commit",
          "a proposal was quarantined before its action committed")],
    ),
    (
        # Stage 2. Once commit+receipt are durable, a consumption failure is
        # reported without rolling either of them back.
        "M35", "app/git_transaction.py",
        "        raise applied from exc",
        '        _git(vault, "reset", "--hard", start_head)  # MUTANT M35\n'
        "        raise applied from exc",
        ["tests/test_console_invariants.py"],
        [("tests/test_console_invariants.py::test_stage2_postcommit_consumption_failure_has_no_rollback_path",
          "post-commit consumption failure can roll back action and receipt")],
    ),
    (
        # Stage 2. Consumption unresolved is not cleanup-after-success;
        # E-APPLIED and E-COMMITTED make different factual claims.
        "M36", "app/console_errors.py",
        '    _git_transaction.PostCommitConsumptionError: _CODES["E-APPLIED"],',
        '    _git_transaction.PostCommitConsumptionError: _CODES["E-COMMITTED"],  # MUTANT M36',
        ["tests/test_console_invariants.py"],
        [("tests/test_console_invariants.py::test_every_application_exception_resolves_to_its_designed_code",
          "PostCommitConsumptionError")],
    ),
    (
        # Stage 2. The persistent applied/spent fragment is HTTP 500, so the
        # app-level HTMX override is what keeps it visible on the same screen.
        "M37", "templates/_head.html",
        '{"code":"[45]..","swap":true,"error":true}',
        '{"code":"[45]..","swap":false,"error":true}',
        ["tests/test_console_invariants.py"],
        [("tests/test_console_invariants.py::test_receipt_attention_fragments_remain_swappable_at_500",
          "E-APPLIED 500 fragment no longer swaps")],
    ),
    (
        # Stage 2. A spent-id card is navigation-only and must never regain
        # a fingerprint or mutation transport.
        "M38", "templates/blocks/action_receipt_card.html",
        '<div class="proposal action-receipt"\n'
        '     id="receipt-card-{{ proposal_id }}-{{ issue }}">',
        '<div class="proposal action-receipt"\n'
        '     id="receipt-card-{{ proposal_id }}-{{ issue }}"\n'
        '     hx-post="/outbox/{{ entity }}/approve">  {# MUTANT M38 #}',
        ["tests/test_console_invariants.py"],
        [("tests/test_console_invariants.py::test_action_receipt_card_has_no_review_or_mutation_transport",
          "receipt card regained action/review transport: hx-post")],
    ),
    (
        # Stage 2. Full-store enumeration is an offline audit responsibility,
        # never work performed in a request path whose cost grows forever.
        "M39", "app/outbox.py",
        "    receipts = resolve_head_receipts(\n"
        "        scope.root, scope.current_entity(), canonical_ids.values()\n"
        "    )",
        "    from .action_receipts import validate_head_receipt_store\n"
        "    validate_head_receipt_store(\n"
        "        scope.root, scope.current_entity()\n"
        "    )  # MUTANT M39: enumerate accumulated receipts in request\n"
        "    receipts = resolve_head_receipts(\n"
        "        scope.root, scope.current_entity(), canonical_ids.values()\n"
        "    )",
        ["tests/test_console_invariants.py"],
        [("tests/test_console_invariants.py::test_full_receipt_store_enumeration_is_offline_audit_only",
          "request path enumerates the accumulated receipt store: app/outbox.py")],
    ),
    (
        # Stage 2. Removing the last executable producer must make a code an
        # orphan; an allowlist cannot make dead taxonomy look live.
        "M40", "tests/test_console_invariants.py",
        '        - {"E-UNKNOWN", "E-REQUEST"}',
        '        - {"E-UNKNOWN", "E-REQUEST", "E-APPLIED"}  # MUTANT M40',
        ["tests/test_console_invariants.py"],
        [("tests/test_console_invariants.py::test_a_dead_mapping_cannot_impersonate_an_executable_producer",
          "orphan guard allowed E-APPLIED to impersonate a live outcome")],
    ),
    (
        # Stage 2. Retired outcomes may remain in historical prose, never in
        # the live taxonomy without an executable producer.
        "M41", "app/console_errors.py",
        'UNKNOWN = _CODES["E-UNKNOWN"]',
        '_CODES["E-RETAINED"] = ConsoleError(\n'
        '    "E-RETAINED", "integrity", "attention", "retired outcome",\n'
        '    "stop", "no", 500,\n'
        ')  # MUTANT M41\n\n'
        'UNKNOWN = _CODES["E-UNKNOWN"]',
        ["tests/test_console_invariants.py"],
        [("tests/test_console_invariants.py::test_every_operator_outcome_has_an_executable_producer",
          "orphan operator outcome: E-RETAINED")],
    ),
]


class InfrastructureFailure(RuntimeError):
    """The run told us nothing about the mutation, so it is not evidence."""


class MutantSurvived(RuntimeError):
    """The run was sound, and the protection did not hold."""


def _pytest(selection: list[str]) -> tuple[int, str, str]:
    completed = subprocess.run(
        # `--tb=line` prints one line per failure — the failing assertion and
        # nothing else. A full traceback includes surrounding *source*, so a
        # diagnostic could match a nearby line that passed; that is exactly
        # how M4b's expected diagnostic came from its setup assertion.
        ["uv", "run", "python", "-m", "pytest", *selection, "-q", "--no-header",
         "--tb=line"],
        cwd=ROOT, capture_output=True, text=True,
    )
    return completed.returncode, completed.stdout, completed.stderr


def _require_node_failed_for_its_reason(
    code: int, out: str, err: str, node: str, diagnostic: str, context: str
) -> None:
    """One node, run alone, must fail carrying its intended diagnostic.

    The node is run **by itself**, so the only failure the output can hold is
    that node's — which is what binds the diagnostic to it. Checking "the
    node appears in some FAILED line" and "the diagnostic appears somewhere
    in the output" as separate conditions over a multi-test run would accept
    the expected node failing for an unrelated reason while a *different*
    test emitted the diagnostic.
    """
    if code != 1:
        raise InfrastructureFailure(
            f"{context}: pytest exited {code}, expected 1 (this test failed)\n"
            f"--- stdout ---\n{out[-2000:]}\n--- stderr ---\n{err[-2000:]}"
        )
    errors = [line for line in out.splitlines() if line.startswith("ERROR")]
    if errors:
        raise InfrastructureFailure(
            f"{context}: collection errors, so the run measured nothing:\n"
            + "\n".join(errors[:5])
        )
    if "no tests ran" in out or "collected 0 items" in out:
        raise InfrastructureFailure(
            f"{context}: the node id selected nothing — it may have been "
            f"renamed:\n{out[-2000:]}"
        )
    failed = [line for line in out.splitlines() if line.startswith("FAILED")]
    if len(failed) != 1:
        raise InfrastructureFailure(
            f"{context}: expected exactly one failing test, saw {len(failed)}:\n"
            + "\n".join(failed[:5])
        )
    if node.split("::", 1)[1] not in failed[0]:
        raise InfrastructureFailure(
            f"{context}: the failure is not this node: {failed[0]}"
        )
    if diagnostic not in out:
        raise MutantSurvived(
            f"{context}: {node} failed, but not for its intended reason\n"
            f"  expected the failure to contain: {diagnostic!r}\n"
            f"--- output ---\n{out[-2000:]}"
        )


def _require_clean_run(code: int, out: str, err: str, context: str) -> None:
    """A restored or full run is evidence only if it is *completely* green.

    Exit code 0 and nothing else. Scanning the output for `FAILED` lines is
    not enough: an exit of 1 carrying only `ERROR` lines produces no `FAILED`
    lines at all and would be read as success.
    """
    if code != 0:
        raise InfrastructureFailure(
            f"{context}: pytest exited {code}, expected 0\n"
            f"--- stdout ---\n{out[-3000:]}\n--- stderr ---\n{err[-2000:]}"
        )


def _dirty(paths: list[str]) -> list[str]:
    completed = subprocess.run(
        ["git", "status", "--porcelain", "--", *paths],
        cwd=ROOT, capture_output=True, text=True,
    )
    if completed.returncode != 0:
        raise InfrastructureFailure(
            f"could not check worktree cleanliness: {completed.stderr.strip()}"
        )
    return [line for line in completed.stdout.splitlines() if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--only")
    parser.add_argument(
        "--skip-full-suite", action="store_true",
        help="skip the closing full-suite run (for iterating on one mutation)",
    )
    arguments = parser.parse_args()

    selected = [m for m in MUTATIONS if not arguments.only or m[0] == arguments.only]
    if not selected:
        print(f"no mutation named {arguments.only!r}")
        return 1
    if arguments.list:
        for identifier, target, old, _new, selection, expectations in selected:
            for node, diagnostic in expectations:
                print(f"{identifier:4} {target:38} {node}")
                print(f"{'':4} {'':38}   └─ {diagnostic}")
        return 0

    targets = sorted({target for _i, target, *_rest in selected})
    dirty = _dirty(targets)
    if dirty:
        print("refusing to run: these targets have uncommitted changes, and a")
        print("restore would discard them:")
        for line in dirty:
            print(f"  {line}")
        return 1

    failures = []
    for identifier, target, old, new, selection, expectations in selected:
        path = ROOT / target
        pristine = path.read_bytes()
        source = pristine.decode("utf-8")
        if source.count(old) != 1:
            print(f"{identifier:4} UNANCHORED — the OLD text is not present exactly once")
            failures.append(identifier)
            continue

        path.write_text(source.replace(old, new), encoding="utf-8")
        problems = []
        try:
            for node, diagnostic in expectations:
                code, out, err = _pytest([node])
                try:
                    _require_node_failed_for_its_reason(
                        code, out, err, node, diagnostic, f"{identifier} mutated"
                    )
                except (InfrastructureFailure, MutantSurvived) as problem:
                    problems.append(problem)
        finally:
            path.write_bytes(pristine)

        if path.read_bytes() != pristine:
            raise SystemExit(f"{identifier}: restore failed — STOPPING")

        if problems:
            alive = any(isinstance(p, MutantSurvived) for p in problems)
            print(f"{identifier:4} {'ALIVE' if alive else 'NOT EVIDENCE'}")
            for problem in problems:
                print(f"       {problem}")
            failures.append(identifier)
            continue

        try:
            code, out, err = _pytest(selection)
            _require_clean_run(code, out, err, f"{identifier} restored")
        except InfrastructureFailure as problem:
            print(f"{identifier:4} RESTORE INCOMPLETE\n{problem}")
            failures.append(identifier)
            continue

        print(f"{identifier:4} RED then GREEN")
        for node, diagnostic in expectations:
            print(f"       {node.split('::', 1)[1]}")
            print(f"         └─ {diagnostic}")

    print()
    if failures:
        print(f"NOT EVIDENCE: {', '.join(failures)}")
        return 1
    print(f"all {len(selected)} mutations: red under mutation, green once restored")

    if arguments.skip_full_suite:
        return 0
    print("\nfull public suite after the restored campaign group:")
    code, out, err = _pytest([])
    try:
        _require_clean_run(code, out, err, "full suite")
    except InfrastructureFailure as problem:
        print(problem)
        return 1
    summary = [line for line in out.splitlines() if " passed" in line or " failed" in line]
    print(f"  {summary[-1] if summary else out[-200:]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
