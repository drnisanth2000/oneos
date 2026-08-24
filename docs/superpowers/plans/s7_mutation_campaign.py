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
        "    require_review_match(proposal_state.contents, review_sha256)",
        "    pass  # MUTANT M1",
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
        "        require_review_match(proposal_state.contents, review_sha256)",
        "        pass  # MUTANT M3",
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
        "        consume_reviewed_proposal(scope.root, proposal_rel, proposal_state)",
        "        consume_reviewed_proposal(scope.root, proposal_rel, capture_path_state(scope.root, proposal_rel))  # MUTANT M6",
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
        "            preconditions=(_require_no_live_references,),\n",
        "  # MUTANT M12: reference gate no longer runs under the lock\n",
        ["tests/test_registry.py"],
        [("tests/test_registry.py::test_the_reference_recount_holds_the_approval_lock",
          "the reference count never ran")],
    ),
    (
        "M13", "app/git_transaction.py",
        "        if stranded_records:",
        "        if False:  # MUTANT M13: stranded record collapses into E-RECOVER",
        ["tests/test_outbox.py"],
        [("tests/test_outbox.py::test_a_rollback_that_cannot_restore_the_record_reports_it_as_stranded",
          "E-STRANDED")],
    ),
    (
        "M14", "app/git_transaction.py",
        "            if isinstance(transaction_error, QuarantineRestorationBlocked):",
        "            if False:  # MUTANT M14: cleanup overwrites the stranded outcome",
        ["tests/test_outbox.py"],
        [("tests/test_outbox.py::test_a_stranded_record_survives_a_simultaneous_cleanup_failure",
          "the stranded outcome was overwritten")],
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
        "M17", "app/git_transaction.py",
        "            raise QuarantineEntrySubstituted(path, link_count)",
        "            _restore()  # MUTANT M17\n            raise QuarantineEntrySubstituted(path, link_count)",
        ["tests/test_git_transaction.py"],
        [("tests/test_git_transaction.py::test_a_substituted_quarantine_entry_is_refused_and_nothing_further_moves",
          "assert [] == ['outbox-record.yaml']")],
    ),
    (
        "M11", "app/main.py",
        "        prop = execute_delete(scope, id, review_sha256)",
        '        _unused = review_sha256  # MUTANT M11\n        prop = execute_delete(scope, id, "0" * 64)',
        ["tests/test_console_invariants.py"],
        [("tests/test_console_invariants.py::test_every_reviewed_route_requires_and_passes_the_fingerprint",
          "never passes review_sha256 to execute_delete")],
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
