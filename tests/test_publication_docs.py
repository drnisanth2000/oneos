from pathlib import Path

import yaml


def test_agent_contract_has_complete_sanitized_task_and_stop_conditions():
    text = Path("AGENTS.md").read_text(encoding="utf-8")
    assert "complete sanitized public task contract" in text
    for phrase in (
        "private authority or material",
        "dependency changes",
        "convention or schema changes",
        "security-boundary changes",
        "destructive actions",
        "deployment",
        "unresolved product decisions",
    ):
        assert phrase in text


def test_status_distinguishes_public_and_private_gates():
    text = Path("docs/STATUS.md").read_text(encoding="utf-8")
    assert "privately hosted" in text
    assert "eventual public release" in text
    assert "synthetic public CI" in text
    assert "local private gate" in text


def test_build_runs_gitleaks_before_oneos_audits():
    text = Path("BUILD.md").read_text(encoding="utf-8")
    commands = [
        "tools/run_gitleaks.sh .",
        "uv run python -m tools.public_repo_audit --repo . --history",
        'uv run python -m tools.public_repo_audit --repo . --vault "$ONEOS_VAULT" --history',
    ]
    assert all(command in text for command in commands)
    assert [text.index(command) for command in commands] == sorted(
        text.index(command) for command in commands
    )


def test_ci_runs_publication_gates_for_tag_pushes():
    workflow = yaml.load(
        Path(".github/workflows/ci.yml").read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )

    assert workflow["on"]["push"]["tags"] == ["**"]


def test_local_install_creates_binary_directory_first():
    text = Path("BUILD.md").read_text(encoding="utf-8")
    create = 'mkdir -p "$HOME/.local/bin"'
    install = 'install -m 0755 /private/tmp/gitleaks "$HOME/.local/bin/gitleaks"'

    assert create in text
    assert text.index(create) < text.index(install)


def test_safety_foundation_status_tracks_merged_s1_through_s5():
    build = Path("BUILD.md").read_text(encoding="utf-8")
    status = Path("docs/STATUS.md").read_text(encoding="utf-8")

    for step in ("S1", "S2", "S3", "S4", "S5", "S6"):
        assert f"| {step} | **COMPLETE** |" in build
    # S6 advanced NEXT -> COMPLETE in the Task 15 documentation commit. This
    # sentinel tracks documented lifecycle state, not an S1-S5 behavioural,
    # isolation or refusal contract, so advancing it spends no regression-table
    # row (human ruling, recorded in the ledger).
    assert "| S7 |" in build
    assert "Merged S5 baseline: `0f71cd3`" in status

    # PR #15 must-fix 8: docs/STATUS.md's own "Next step" section must agree
    # with its "Phase 1 triage" section above (which already marks S6
    # COMPLETE) rather than still directing work on unfinished S6 tasks. This
    # sentinel is authorized to change (human ruling: it tracks documented
    # lifecycle state, not an S1-S5 behavioural, isolation, or refusal
    # contract) and spends no regression-table row.
    assert "S6 is complete" in status

    stale_claims = (
        "real ingest currently creates an uncommitted item",
        "Start with commit-on-ingest",
        "app/ingest/base.write_inbox_item",
        "canonical `f84625b` is the fifth remote commit",
        "**and an empty git status**",
        "S5 is the current step",
        "**OPEN — S5**",
        "S6 is in implementation on",
        "Tasks 1-9 of 14",
        "Remaining: Tasks 10-13",
    )
    combined = build + "\n" + status
    assert all(claim not in combined for claim in stale_claims)


def test_completed_execution_plans_are_marked_historical():
    paths = (
        "docs/superpowers/plans/2026-08-07-oneos-canonical-naming-cutover.md",
        "docs/superpowers/plans/2026-08-07-oneos-private-github-agent-workflow.md",
        "docs/superpowers/plans/2026-08-13-oneos-s2-request-local-scope.md",
        "docs/superpowers/plans/2026-08-15-oneos-s3-server-owned-destinations.md",
        "docs/superpowers/plans/2026-08-15-s4-fresh-collision-safe-proposals.md",
        "docs/superpowers/plans/2026-08-16-s5-isolated-git-transaction-audit.md",
    )

    for path in paths:
        opening = Path(path).read_text(encoding="utf-8")[:600]
        assert "Historical execution plan" in opening

    lessons = Path("docs/SAFETY-FOUNDATION-S1-S4.md").read_text(
        encoding="utf-8"
    )
    assert "This document records the built guarantees" in lessons
