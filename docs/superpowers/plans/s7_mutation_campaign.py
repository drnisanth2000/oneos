#!/usr/bin/env python3
"""Run S7's mutation campaign, exactly as the ledger records it.

Each mutation is one exact string substitution. For each, this script:

  1. asserts the OLD text appears exactly once (so a stale edit cannot
     silently mutate nothing and be recorded as evidence);
  2. applies it and runs the named pytest selection;
  3. restores the file from an in-memory pre-image; and
  4. verifies with `filecmp` that the file is byte-identical again.

It uses no Git commands. A `git checkout` or `git clean` in a shared tree
could erase unrelated work, and a mutation harness is the last place that
should be possible.

Usage:
    python3 docs/superpowers/plans/s7_mutation_campaign.py --list
    python3 docs/superpowers/plans/s7_mutation_campaign.py [--only M6]
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


def run(selection: list[str]) -> str:
    return subprocess.run(
        [sys.executable, "-m", "pytest", *selection, "-q", "--no-header"],
        cwd=ROOT, capture_output=True, text=True,
    ).stdout


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--only")
    arguments = parser.parse_args()

    selected = [m for m in MUTATIONS if not arguments.only or m[0] == arguments.only]
    if arguments.list:
        for identifier, target, old, _new, selection, expected in selected:
            print(f"{identifier:4} {target:38} {expected}  [{' '.join(selection)}]")
        return 0

    failures = []
    for identifier, target, old, new, selection, expected in selected:
        path = ROOT / target
        pristine = path.read_bytes()
        source = pristine.decode("utf-8")
        if source.count(old) != 1:
            print(f"{identifier:4} SKIPPED — anchor not found exactly once")
            failures.append(identifier)
            continue
        path.write_text(source.replace(old, new), encoding="utf-8")
        try:
            output = run(selection)
        finally:
            path.write_bytes(pristine)
        assert path.read_bytes() == pristine, f"{identifier}: restore failed"

        red = [line for line in output.splitlines() if line.startswith("FAILED")]
        caught = any(expected in line for line in red)
        print(f"{identifier:4} {'RED  ' if caught else 'ALIVE'} {expected}")
        if not caught:
            failures.append(identifier)
            for line in red[:3]:
                print(f"       (also red: {line})")

    print()
    if failures:
        print(f"SURVIVED OR UNANCHORED: {', '.join(failures)}")
        return 1
    print(f"all {len(selected)} mutations detected by their named test")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
