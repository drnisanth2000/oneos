from pathlib import Path


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
