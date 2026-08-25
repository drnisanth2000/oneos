import hashlib
import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

from app.action_receipts import (
    make_action_receipt,
    render_action_receipt,
    validate_head_receipt_store,
)
from tools.public_repo_audit import audit_repository


def run_git(
    repo: Path, *args: str, env: dict[str, str] | None = None
) -> str:
    command_env = os.environ.copy()
    if env:
        command_env.update(env)
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        env=command_env,
    ).stdout


def git_repo(path: Path, files: dict[str, str]) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    run_git(path, "init", "-q")
    run_git(path, "config", "user.name", "test")
    run_git(path, "config", "user.email", "test@example.com")
    commit(path, files, "initial")
    return path


def git_repo_with_bytes(path: Path, files: dict[str, bytes]) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    run_git(path, "init", "-q")
    run_git(path, "config", "user.name", "test")
    run_git(path, "config", "user.email", "test@example.com")
    commit_bytes(path, files, "initial")
    return path


def commit(repo: Path, files: dict[str, str], message: str) -> None:
    commit_bytes(
        repo,
        {relative: content.encode("utf-8") for relative, content in files.items()},
        message,
    )


def commit_bytes(repo: Path, files: dict[str, bytes], message: str) -> None:
    for relative, content in files.items():
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-q", "-m", message)


def remove_and_commit(repo: Path, relative: str, message: str) -> None:
    (repo / relative).unlink()
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
    (system / "workspaces.yaml").write_text("workspaces: {}\n", encoding="utf-8")
    run_git(path, "init", "-q")
    run_git(path, "config", "user.name", "test")
    run_git(path, "config", "user.email", "test@example.com")
    run_git(path, "add", "-A")
    run_git(path, "commit", "-q", "-m", "synthetic vault")
    return path


def categories(
    repo: Path, vault: Path | None = None, history: bool = False
) -> list[str]:
    return [
        finding.category
        for finding in audit_repository(repo, vault, include_history=history)
    ]


def add_allowlist(repo: Path, relative: str) -> None:
    digest = hashlib.sha256((repo / relative).read_bytes()).hexdigest()
    commit(
        repo,
        {".oneos-public-binary-allowlist": f"{digest}  {relative}\n"},
        "allow binary",
    )


def test_clean_synthetic_repository_passes(tmp_path: Path):
    repo = git_repo(tmp_path, {"app.py": "title = 'OneOS'\n"})

    assert audit_repository(repo, vault=None, include_history=True) == []


def test_public_audit_pattern_includes_the_accumulated_head_receipt_gate(
    tmp_path: Path,
):
    proposal_id = "20260824T120000-" + "ab" * 16
    relative = f"synthetic/outbox/.receipts/{proposal_id}.yaml"
    receipt = render_action_receipt(
        make_action_receipt(proposal_id, "a" * 64, "approval")
    )
    repo = git_repo_with_bytes(tmp_path, {relative: receipt})

    assert audit_repository(repo, vault=None, include_history=True) == []
    assert validate_head_receipt_store(repo, "synthetic") == (
        make_action_receipt(proposal_id, "a" * 64, "approval"),
    )


def test_public_audit_rejects_a_malformed_accumulated_vault_receipt(tmp_path: Path):
    vault = synthetic_vault(tmp_path / "vault", "synthetic")
    proposal_id = "20260824T120000-" + "ab" * 16
    relative = f"synthetic/outbox/.receipts/{proposal_id}.yaml"
    commit_bytes(vault, {relative: b"not: a closed receipt\n"}, "bad receipt")
    (vault / "_system/entities.yaml").write_text(
        "entities: {}\n", encoding="utf-8"
    )
    repo = git_repo(tmp_path / "repo", {"app.py": "clean = True\n"})

    findings = audit_repository(repo, vault=vault, include_history=False)

    assert [item.category for item in findings] == ["receipt-integrity"]


def test_general_credential_assignment_is_not_python_audit_policy(tmp_path: Path):
    value = "access_" + "token = 'synthetic-value-123'\n"
    repo = git_repo(tmp_path, {"config.py": value})

    assert audit_repository(repo, None, False) == []


@pytest.mark.parametrize(
    "value",
    [
        "/" + "Users" + "/example/private",
        "/" + "home" + "/example/private",
        "C:" + "\\" + "Users" + "\\example\\private",
        "D:" + "\\" + "private\\vault",
    ],
)
def test_cross_platform_absolute_private_home_paths_are_redacted(
    tmp_path: Path, value: str
):
    repo = git_repo(tmp_path, {"note.txt": value + "\n"})

    findings = audit_repository(repo, None, False)

    assert [item.category for item in findings] == ["absolute-private-path"]
    assert value not in repr(findings)


def test_relative_and_url_paths_are_allowed(tmp_path: Path):
    repo = git_repo(
        tmp_path,
        {
            "note.txt": (
                "Users/example/private\n"
                "https://example.test/" + "Users/example/private\n"
                "relative/.sensitive/record.md\n"
            )
        },
    )

    assert audit_repository(repo, None, False) == []


@pytest.mark.parametrize(
    "value",
    [
        "/" + "srv/grey-matter/.sensitive/record.md",
        "root:" + "/." + "sensitive/record.md",
    ],
)
def test_absolute_sensitive_paths_are_redacted(tmp_path: Path, value: str):
    repo = git_repo(tmp_path, {"note.txt": value + "\n"})

    findings = audit_repository(repo, None, False)

    assert [item.category for item in findings] == ["absolute-private-path"]
    assert value not in repr(findings)


@pytest.mark.parametrize(
    "relative", ["books.db", "data/a.db", "a.sqlite", "a.sqlite3"]
)
def test_database_artifacts_are_forbidden_even_if_allowlisted(
    tmp_path: Path, relative: str
):
    repo = git_repo_with_bytes(tmp_path, {relative: b"SQLite format 3\0"})
    add_allowlist(repo, relative)

    assert "forbidden-data-artifact" in categories(repo)


def test_binary_requires_exact_path_and_sha_pair(tmp_path: Path):
    relative = "static/vendor/icon.bin"
    repo = git_repo_with_bytes(tmp_path, {relative: b"\x00\x01"})

    assert categories(repo) == ["unapproved-binary"]
    add_allowlist(repo, relative)
    assert audit_repository(repo, None, False) == []
    assert audit_repository(repo, None, True) == []
    commit_bytes(repo, {relative: b"\x00\x02"}, "change binary")
    assert categories(repo) == ["unapproved-binary"]


def test_history_ignores_unpublished_local_branch(tmp_path: Path):
    repo = git_repo(tmp_path, {"note.md": "clean\n"})
    published_branch = run_git(repo, "branch", "--show-current").strip()
    run_git(repo, "switch", "-q", "-c", "abandoned-local")
    commit(repo, {"books.db": "not a database\n"}, "abandoned artifact")
    run_git(repo, "switch", "-q", published_branch)

    assert audit_repository(repo, None, True) == []


def test_binary_allowlist_rejects_wrong_path_and_malformed_line(tmp_path: Path):
    repo = git_repo_with_bytes(tmp_path, {"asset.bin": b"\x00\x01"})
    digest = hashlib.sha256(b"\x00\x01").hexdigest()
    commit(
        repo,
        {".oneos-public-binary-allowlist": f"{digest}  other.bin\nnot-valid\n"},
        "bad allowlist",
    )

    assert categories(repo) == ["unapproved-binary", "unapproved-binary"]


def test_long_term_matches_text_path_ref_and_commit_metadata(tmp_path: Path):
    vault = synthetic_vault(tmp_path / "vault", entity="customer-zeta")
    text_repo = git_repo(tmp_path / "text", {"note.md": "customer-zeta\n"})
    path_repo = git_repo(
        tmp_path / "path", {"customer-zeta/note.md": "clean\n"}
    )
    ref_repo = git_repo(tmp_path / "ref", {"note.md": "clean\n"})
    run_git(ref_repo, "switch", "-q", "-c", "customer-zeta")
    subject_repo = git_repo(tmp_path / "subject", {"note.md": "clean\n"})
    commit(subject_repo, {"note.md": "still clean\n"}, "customer-zeta")
    author_repo = git_repo(tmp_path / "author", {"note.md": "clean\n"})
    run_git(author_repo, "config", "user.name", "customer-zeta")
    commit(author_repo, {"note.md": "still clean\n"}, "author metadata")
    email_repo = git_repo(tmp_path / "email", {"note.md": "clean\n"})
    run_git(email_repo, "config", "user.email", "customer-zeta@example.test")
    commit(email_repo, {"note.md": "still clean\n"}, "email metadata")

    for repo in (
        text_repo,
        path_repo,
        ref_repo,
        subject_repo,
        author_repo,
        email_repo,
    ):
        findings = audit_repository(repo, vault, True)
        assert {item.category for item in findings} == {"instance-value"}
        assert "customer-zeta" not in repr(findings)


def test_long_term_uses_identifier_boundaries(tmp_path: Path):
    vault = synthetic_vault(tmp_path / "vault", entity="customer-zeta")
    repo = git_repo(tmp_path / "repo", {"note.md": "xcustomer-zetax\n"})

    assert audit_repository(repo, vault, False) == []


def test_short_term_matches_only_component_or_structured_identifier(tmp_path: Path):
    vault = synthetic_vault(tmp_path / "vault", entity="abc")
    component = git_repo(tmp_path / "component", {"abc/note.md": "clean\n"})
    structured = git_repo(
        tmp_path / "structured", {"note.md": "---\nentity: abc\n---\n"}
    )
    yaml_mapping = git_repo(tmp_path / "yaml", {"note.yaml": "member: abc\n"})
    json_object = git_repo(tmp_path / "json", {"note.json": '{"owner": "abc"}\n'})
    prose = git_repo(tmp_path / "prose", {"note.md": "the abc pattern\n"})
    substring = git_repo(tmp_path / "substring", {"xabcx/note.md": "clean\n"})

    assert categories(component, vault) == ["instance-value"]
    assert categories(structured, vault) == ["instance-value"]
    assert categories(yaml_mapping, vault) == ["instance-value"]
    assert categories(json_object, vault) == ["instance-value"]
    assert categories(prose, vault) == []
    assert categories(substring, vault) == []


def test_short_term_is_ignored_in_unapproved_structured_field(tmp_path: Path):
    vault = synthetic_vault(tmp_path / "vault", entity="abc")
    repo = git_repo(tmp_path / "repo", {"note.yaml": "description: abc\n"})

    assert audit_repository(repo, vault, False) == []


def test_registry_terms_include_products_members_and_workspaces(tmp_path: Path):
    vault = synthetic_vault(tmp_path / "vault", entity="entity-safe")
    system = vault / "_system"
    (system / "products.yaml").write_text(
        "products:\n  entity-safe:\n    product-private: {}\n", encoding="utf-8"
    )
    (system / "members.yaml").write_text(
        "members:\n  entity-safe:\n    - id: member-private\n", encoding="utf-8"
    )
    (system / "workspaces.yaml").write_text(
        "workspaces:\n  - {id: workspace-private, label: Private}\n",
        encoding="utf-8",
    )
    repo = git_repo(
        tmp_path / "repo",
        {
            "product.md": "product-private\n",
            "member.md": "member-private\n",
            "workspace.md": "workspace-private\n",
        },
    )

    assert categories(repo, vault) == [
        "instance-value",
        "instance-value",
        "instance-value",
    ]


def test_annotated_tag_metadata_is_scanned(tmp_path: Path):
    vault = synthetic_vault(tmp_path / "vault", entity="customer-zeta")
    repo = git_repo(tmp_path / "repo", {"note.md": "clean\n"})
    run_git(repo, "tag", "-a", "release", "-m", "customer-zeta")

    findings = audit_repository(repo, vault, True)

    assert [item.category for item in findings] == ["instance-value"]
    assert "customer-zeta" not in repr(findings)


def test_history_flag_finds_removed_oneos_violation(tmp_path: Path):
    repo = git_repo(tmp_path, {"books.db": "not a database\n"})
    remove_and_commit(repo, "books.db", "remove artifact")

    assert audit_repository(repo, None, False) == []
    assert categories(repo, history=True) == ["forbidden-data-artifact"]


def test_cli_prints_clean_and_exits_zero(tmp_path: Path):
    repo = git_repo(tmp_path, {"app.py": "title = 'OneOS'\n"})

    result = subprocess.run(
        [sys.executable, "-m", "tools.public_repo_audit", "--repo", str(repo)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert result.stdout == "CLEAN\n"


def test_cli_prints_redacted_finding_and_exits_one(tmp_path: Path):
    private_path = "/" + "Users" + "/example/private-vault"
    repo = git_repo(tmp_path, {"config.py": private_path + "\n"})

    result = subprocess.run(
        [sys.executable, "-m", "tools.public_repo_audit", "--repo", str(repo)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert result.stdout.startswith("absolute-private-path ")
    assert "absolute private path detected" in result.stdout
    assert private_path not in result.stdout
    assert "config.py" not in result.stdout
