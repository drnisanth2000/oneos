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

#: (id, file, OLD, NEW, pytest selection, the test that must go red)
MUTATIONS = [
    (
        "M1", "app/outbox.py",
        "    require_review_match(proposal_state.contents, review_sha256)",
        "    pass  # MUTANT M1",
        ["tests/test_outbox.py"],
        "test_actions_refuse_a_malformed_fingerprint_without_mutation",
    ),
    (
        "M3", "app/registry.py",
        "        require_review_match(proposal_state.contents, review_sha256)",
        "        pass  # MUTANT M3",
        ["tests/test_registry.py"],
        "test_execute_delete_refuses_a_malformed_fingerprint",
    ),
    (
        "M4", "app/outbox.py",
        "            make_review_snapshot(_require_destination(scope, proposal), contents)",
        "            make_review_snapshot(_require_destination(scope, proposal), leaf.read_bytes())  # MUTANT M4",
        ["tests/test_outbox.py", "tests/test_console_projection.py"],
        "test_review_value_and_hash_come_from_one_capture_not_a_second_read",
    ),
    (
        "M5", "app/outbox.py",
        "            PathChange(proposal_rel, proposal_state, PathState.absent()),",
        "            PathChange(proposal_rel, capture_path_state(vault, proposal_rel), PathState.absent()),  # MUTANT M5",
        ["tests/test_outbox.py"],
        "test_approve_owns_the_reviewed_state_not_whatever_arrives_later",
    ),
    (
        "M5b", "app/registry.py",
        "                PathChange(proposal_rel, proposal_state, PathState.absent()),",
        "                PathChange(proposal_rel, capture_path_state(vault, proposal_rel), PathState.absent()),  # MUTANT M5b",
        ["tests/test_registry.py"],
        "test_delete_owns_the_reviewed_state_not_whatever_arrives_later",
    ),
    (
        "M6", "app/outbox.py",
        "        consume_reviewed_proposal(scope.root, proposal_rel, proposal_state)",
        "        consume_reviewed_proposal(scope.root, proposal_rel, capture_path_state(scope.root, proposal_rel))  # MUTANT M6",
        ["tests/test_outbox.py"],
        "test_reject_owns_the_reviewed_state_not_whatever_arrives_later",
    ),
    (
        "M7", "templates/blocks/outbox_card.html",
        '  {% set outbox_values = {"id": row.proposal.id,\n                          "review_sha256": row.review_sha256} %}',
        '  {% set outbox_values = {"id": row.proposal.id} %}  {# MUTANT M7 #}',
        ["tests/test_console_routes.py", "tests/test_console_invariants.py"],
        "test_outbox_hx_vals_are_tojson",
    ),
    (
        "M7b", "templates/blocks/delete_impact.html",
        '  {% set delete_execute_values = {"id": prop.id,\n                                  "review_sha256": review_sha256} %}',
        '  {% set delete_execute_values = {"id": prop.id} %}  {# MUTANT M7b #}',
        ["tests/test_console_routes.py", "tests/test_console_invariants.py"],
        "test_delete_preview_hx_vals_survive_hostile_slug",
    ),
    (
        "M8", "app/main.py",
        "        prop = execute_delete(scope, id, review_sha256)",
        '        prop = get_delete_review(scope, id).value\n        execute_delete(scope, id, review_sha256)  # MUTANT M8',
        ["tests/test_console_routes.py", "tests/test_console_invariants.py"],
        "test_alerts_never_contain_paths_slugs_or_echoes",
    ),
    (
        "M9", "app/outbox.py",
        '    if candidate.is_symlink():\n        raise RedirectedPathError("proposal leaf is redirected")',
        '    try:\n        _p = open(candidate, "rb")\n    except OSError:\n        pass\n    else:\n        _p.close()\n    if candidate.is_symlink():\n        raise RedirectedPathError("proposal leaf is redirected")  # MUTANT M9',
        ["tests/test_console_routes.py"],
        "test_check_again_never_reads_through_a_redirected_leaf",
    ),
    (
        "M10", "app/registry.py",
        "        report = reference_count(scope, prop.kind, prop.slug)\n        if report.total:",
        '        path.write_bytes(path.read_bytes() + b"# MUTANT\\n")\n        report = reference_count(scope, prop.kind, prop.slug)\n        if report.total:',
        ["tests/test_registry.py"],
        "test_the_delete_no_mutation_matrix",
    ),
    (
        "M11", "app/main.py",
        "        prop = execute_delete(scope, id, review_sha256)",
        '        _unused = review_sha256  # MUTANT M11\n        prop = execute_delete(scope, id, "0" * 64)',
        ["tests/test_console_invariants.py"],
        "test_every_reviewed_route_requires_and_passes_the_fingerprint",
    ),
]


class InfrastructureFailure(RuntimeError):
    """The run told us nothing about the mutation, so it is not evidence."""


def _pytest(selection: list[str]) -> tuple[int, str, str]:
    completed = subprocess.run(
        ["uv", "run", "python", "-m", "pytest", *selection, "-q", "--no-header"],
        cwd=ROOT, capture_output=True, text=True,
    )
    return completed.returncode, completed.stdout, completed.stderr


def _require_test_outcome(code: int, out: str, err: str, context: str) -> list[str]:
    """Return the FAILED lines, or refuse to interpret the run at all.

    pytest exits 0 for all-passed and 1 for test failures. Anything else —
    a missing interpreter, a collection error, a usage mistake, no tests
    collected — means the run measured nothing, and scoring it as either
    "detected" or "survived" would be a false entry in the ledger.
    """
    if code not in (0, 1):
        raise InfrastructureFailure(
            f"{context}: pytest exited {code}\n"
            f"--- stdout ---\n{out[-2000:]}\n--- stderr ---\n{err[-2000:]}"
        )
    if "no tests ran" in out or "collected 0 items" in out:
        raise InfrastructureFailure(f"{context}: no tests were collected\n{out[-2000:]}")
    return [line for line in out.splitlines() if line.startswith("FAILED")]


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
        for identifier, target, old, _new, selection, expected in selected:
            print(f"{identifier:4} {target:38} {expected}  [{' '.join(selection)}]")
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
    for identifier, target, old, new, selection, expected in selected:
        path = ROOT / target
        pristine = path.read_bytes()
        source = pristine.decode("utf-8")
        if source.count(old) != 1:
            print(f"{identifier:4} UNANCHORED — the OLD text is not present exactly once")
            failures.append(identifier)
            continue

        path.write_text(source.replace(old, new), encoding="utf-8")
        try:
            code, out, err = _pytest(selection)
            red = _require_test_outcome(code, out, err, f"{identifier} (mutated)")
        except InfrastructureFailure as problem:
            path.write_bytes(pristine)
            print(f"{identifier:4} INFRASTRUCTURE FAILURE — not evidence\n{problem}")
            failures.append(identifier)
            continue
        finally:
            path.write_bytes(pristine)

        if path.read_bytes() != pristine:
            raise SystemExit(f"{identifier}: restore failed — STOPPING")

        caught = [line for line in red if expected in line]
        if not caught:
            print(f"{identifier:4} ALIVE  {expected}")
            for line in red[:3]:
                print(f"       (other failures: {line})")
            failures.append(identifier)
            continue

        # The green rerun: a mutation is only evidence if the same selection
        # passes once the exact implementation is back.
        try:
            code, out, err = _pytest(selection)
            still_red = _require_test_outcome(code, out, err, f"{identifier} (restored)")
        except InfrastructureFailure as problem:
            print(f"{identifier:4} INFRASTRUCTURE FAILURE on rerun\n{problem}")
            failures.append(identifier)
            continue
        if still_red:
            print(f"{identifier:4} RESTORE INCOMPLETE — still failing: {still_red[:3]}")
            failures.append(identifier)
            continue

        print(f"{identifier:4} RED then GREEN  {expected}")

    print()
    if failures:
        print(f"NOT EVIDENCE: {', '.join(failures)}")
        return 1
    print(f"all {len(selected)} mutations: red under mutation, green once restored")

    if arguments.skip_full_suite:
        return 0
    print("\nfull public suite after the restored campaign group:")
    try:
        code, out, err = _pytest([])
        red = _require_test_outcome(code, out, err, "full suite")
    except InfrastructureFailure as problem:
        print(problem)
        return 1
    summary = [line for line in out.splitlines() if " passed" in line or " failed" in line]
    print(f"  {summary[-1] if summary else out[-200:]}")
    if red:
        print(f"  FULL SUITE NOT GREEN: {red[:5]}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
