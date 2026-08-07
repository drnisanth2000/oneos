import subprocess
from pathlib import Path
import sys

import yaml

from tools.public_repo_audit import audit_repository


def run_git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout


def git_repo(path: Path, files: dict[str, str]) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    run_git(path, "init", "-q")
    run_git(path, "config", "user.name", "test")
    run_git(path, "config", "user.email", "test@example.com")
    commit(path, files, "initial")
    return path


def commit(repo: Path, files: dict[str, str], message: str) -> None:
    for relative, content in files.items():
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-q", "-m", message)


def synthetic_vault(path: Path, entity: str) -> Path:
    system = path / "_system"
    system.mkdir(parents=True)
    (system / "entities.yaml").write_text(
        yaml.safe_dump({"entities": {entity: {"label": "Synthetic"}}}),
        encoding="utf-8",
    )
    (system / "products.yaml").write_text("products: {}\n", encoding="utf-8")
    (system / "members.yaml").write_text("members: {}\n", encoding="utf-8")
    return path


def test_clean_synthetic_repository_passes(tmp_path):
    repo = git_repo(tmp_path, {"app.py": "title = 'OneOS'\n"})
    assert audit_repository(repo, vault=None, include_history=True) == []


def test_absolute_home_path_is_rejected_without_echoing_value(tmp_path):
    private_path = "/" + "Users" + "/example/private-vault"
    repo = git_repo(tmp_path, {"config.py": f"ROOT = {private_path!r}\n"})
    findings = audit_repository(repo, vault=None, include_history=False)
    assert [item.category for item in findings] == ["absolute-private-path"]
    assert private_path not in findings[0].message


def test_relative_private_marker_is_allowed(tmp_path):
    repo = git_repo(tmp_path, {"policy.md": "<entity>/.sensitive/**\n"})
    assert audit_repository(repo, vault=None, include_history=False) == []


def test_absolute_private_marker_is_rejected(tmp_path):
    private_path = "/" + "vault" + "/." + "sensitive/record.md"
    repo = git_repo(tmp_path, {"policy.md": private_path + "\n"})
    findings = audit_repository(repo, vault=None, include_history=False)
    assert [item.category for item in findings] == ["absolute-private-path"]


def test_colon_delimited_absolute_private_marker_is_rejected(tmp_path):
    private_path = "root:/" + "." + "sensitive/record.md"
    repo = git_repo(tmp_path, {"policy.md": private_path + "\n"})
    findings = audit_repository(repo, vault=None, include_history=False)
    assert [item.category for item in findings] == ["absolute-private-path"]


def test_url_private_marker_is_allowed(tmp_path):
    repo = git_repo(
        tmp_path, {"links.md": "https://example.test/.sensitive/record.md\n"}
    )
    assert audit_repository(repo, vault=None, include_history=False) == []


def test_registry_derived_entity_term_is_rejected(tmp_path):
    repo = git_repo(tmp_path / "repo", {"note.md": "customer-zeta\n"})
    vault = synthetic_vault(tmp_path / "vault", entity="customer-zeta")
    findings = audit_repository(repo, vault=vault, include_history=False)
    assert any(item.category == "instance-value" for item in findings)


def test_history_finding_blocks_clean_worktree(tmp_path):
    private_path = "/" + "Users" + "/example/private"
    repo = git_repo(tmp_path, {"note.md": private_path + "\n"})
    commit(repo, {"note.md": "OneOS\n"}, "sanitize")
    assert any(
        item.category == "absolute-private-path"
        for item in audit_repository(repo, vault=None, include_history=True)
    )


def test_credential_value_is_rejected_without_echoing_it(tmp_path):
    assignment = "access_" + "token = 'synthetic-value-123'\n"
    repo = git_repo(tmp_path, {"config.py": assignment})
    findings = audit_repository(repo, vault=None, include_history=False)
    assert [item.category for item in findings] == ["credential"]
    assert "synthetic-value-123" not in findings[0].message


def test_cli_prints_clean_and_exits_zero(tmp_path):
    repo = git_repo(tmp_path, {"app.py": "title = 'OneOS'\n"})
    result = subprocess.run(
        [sys.executable, "-m", "tools.public_repo_audit", "--repo", str(repo)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert result.stdout == "CLEAN\n"


def test_cli_prints_safe_finding_and_exits_one(tmp_path):
    private_path = "/" + "Users" + "/example/private-vault"
    repo = git_repo(tmp_path, {"config.py": private_path + "\n"})
    result = subprocess.run(
        [sys.executable, "-m", "tools.public_repo_audit", "--repo", str(repo)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert result.stdout == (
        "absolute-private-path HEAD:config.py:1 absolute private path detected\n"
    )
    assert private_path not in result.stdout
