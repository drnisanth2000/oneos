import subprocess
from pathlib import Path
import sys

import pytest
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


def commit_bytes(repo: Path, files: dict[str, bytes], message: str) -> None:
    for relative, content in files.items():
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-q", "-m", message)


def synthetic_vault(
    path: Path,
    entity: str,
    product: str | None = None,
    member: str | None = None,
    entity_label: str = "Synthetic",
    product_label: str | None = None,
    member_label: str | None = None,
) -> Path:
    system = path / "_system"
    system.mkdir(parents=True)
    (system / "entities.yaml").write_text(
        yaml.safe_dump({"entities": {entity: {"label": entity_label}}}),
        encoding="utf-8",
    )
    products = (
        {entity: {product: {"label": product_label} if product_label else {}}}
        if product
        else {}
    )
    member_record = {"id": member} if member else None
    if member_record is not None and member_label:
        member_record["label"] = member_label
    members = {entity: [member_record]} if member_record else {}
    (system / "products.yaml").write_text(
        yaml.safe_dump({"products": products}), encoding="utf-8"
    )
    (system / "members.yaml").write_text(
        yaml.safe_dump({"members": members}), encoding="utf-8"
    )
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


def test_unapproved_binary_blob_is_rejected_without_echoing_bytes(tmp_path):
    repo = git_repo(tmp_path, {"app.py": "title = 'OneOS'\n"})
    binary = (Path(__file__).parent / "fixtures" / "sample.pdf").read_bytes()
    commit_bytes(repo, {"assets/changed.pdf": binary + b"\0changed"}, "binary")

    findings = audit_repository(repo, vault=None, include_history=False)

    assert [item.category for item in findings] == ["unapproved-binary"]
    assert "changed" not in findings[0].message


def test_known_binary_fixture_is_allowed_only_at_its_exact_path_and_hash(tmp_path):
    repo = git_repo(tmp_path, {"app.py": "title = 'OneOS'\n"})
    binary = (Path(__file__).parent / "fixtures" / "sample.pdf").read_bytes()
    commit_bytes(repo, {"tests/fixtures/sample.pdf": binary}, "fixture")

    assert audit_repository(repo, vault=None, include_history=False) == []


def test_known_binary_hash_at_another_path_is_rejected(tmp_path):
    repo = git_repo(tmp_path, {"app.py": "title = 'OneOS'\n"})
    binary = (Path(__file__).parent / "fixtures" / "sample.pdf").read_bytes()
    commit_bytes(repo, {"assets/sample.pdf": binary}, "fixture")

    findings = audit_repository(repo, vault=None, include_history=False)

    assert [item.category for item in findings] == ["unapproved-binary"]


def test_changed_binary_at_allowlisted_path_is_rejected(tmp_path):
    repo = git_repo(tmp_path, {"app.py": "title = 'OneOS'\n"})
    binary = (Path(__file__).parent / "fixtures" / "sample.pdf").read_bytes()
    commit_bytes(repo, {"tests/fixtures/sample.pdf": binary + b"changed"}, "fixture")

    findings = audit_repository(repo, vault=None, include_history=False)

    assert [item.category for item in findings] == ["unapproved-binary"]


@pytest.mark.parametrize(
    "artifact", ["books.db", "records.db", "records.sqlite", "records.sqlite3"]
)
def test_database_artifact_in_any_selected_tree_is_rejected_and_redacted(
    tmp_path, artifact
):
    repo = git_repo(tmp_path, {f"data/{artifact}": "synthetic data\n"})
    run_git(repo, "rm", "-q", f"data/{artifact}")
    run_git(repo, "commit", "-q", "-m", "remove artifact")

    assert audit_repository(repo, vault=None, include_history=False) == []
    findings = audit_repository(repo, vault=None, include_history=True)

    assert [item.category for item in findings] == ["forbidden-artifact"]
    assert artifact not in findings[0].location
    assert artifact not in findings[0].message


@pytest.mark.parametrize(
    "private_path",
    [
        "/".join(["leak", "Users", "private-person", "note.md"]),
        "/".join(["leak", "home", "private-person", "note.md"]),
        "/".join(["leak", "root", "private-vault", "note.md"]),
        "leak/C:\\" + "Users\\private-person\\note.md",
    ],
)
def test_absolute_home_path_in_tracked_name_is_rejected_and_redacted(
    tmp_path, private_path
):
    repo = git_repo(tmp_path, {private_path: "synthetic\n"})

    findings = audit_repository(repo, vault=None, include_history=False)

    assert [item.category for item in findings] == ["absolute-private-path"]
    assert "private-person" not in findings[0].location
    assert "private-person" not in findings[0].message


@pytest.mark.parametrize(
    "private_path",
    [
        "/" + "/".join(["home", "private-person", "vault"]),
        "/" + "/".join(["root", "private-vault"]),
        "C:\\" + "Users\\private-person\\vault",
    ],
)
def test_linux_and_windows_absolute_home_paths_in_text_are_rejected(
    tmp_path, private_path
):
    repo = git_repo(tmp_path, {"config.txt": f"root = {private_path!r}\n"})

    findings = audit_repository(repo, vault=None, include_history=False)

    assert [item.category for item in findings] == ["absolute-private-path"]
    assert "private-person" not in findings[0].message


def test_root_prefix_without_path_boundary_is_allowed(tmp_path):
    repo = git_repo(
        tmp_path,
        {"routes.txt": "paths = '/rooted/project', '/root-cause/example'\n"},
    )

    assert audit_repository(repo, vault=None, include_history=False) == []


def test_registry_derived_identifier_in_tracked_name_is_rejected_and_redacted(
    tmp_path,
):
    private_identifier = "customer-zeta"
    repo = git_repo(tmp_path / "repo", {f"docs/{private_identifier}/note.md": "ok\n"})
    vault = synthetic_vault(tmp_path / "vault", entity=private_identifier)

    findings = audit_repository(repo, vault=vault, include_history=False)

    assert [item.category for item in findings] == ["instance-value"]
    assert private_identifier not in findings[0].location
    assert private_identifier not in findings[0].message


def test_short_registry_identifier_is_rejected_as_exact_path_component(tmp_path):
    private_identifier = "xy"
    repo = git_repo(tmp_path / "repo", {f"docs/{private_identifier}/note.md": "ok\n"})
    vault = synthetic_vault(tmp_path / "vault", entity=private_identifier)

    findings = audit_repository(repo, vault=vault, include_history=False)

    assert [item.category for item in findings] == ["instance-value"]
    assert private_identifier not in findings[0].location


def test_credential_in_historical_commit_message_is_rejected_and_redacted(tmp_path):
    secret = "synthetic-value-123"
    credential_message = "rotate access_" + f"token={secret}"
    repo = git_repo(tmp_path, {"app.py": "version = 1\n"})
    commit(repo, {"app.py": "version = 2\n"}, credential_message)
    commit(repo, {"app.py": "version = 3\n"}, "sanitize message")

    assert audit_repository(repo, vault=None, include_history=False) == []
    findings = audit_repository(repo, vault=None, include_history=True)

    assert any(item.category == "credential" for item in findings)
    assert all(secret not in item.location for item in findings)
    assert all(secret not in item.message for item in findings)


def test_credential_in_annotated_tag_metadata_is_rejected_and_redacted(tmp_path):
    secret = "synthetic-value-123"
    credential_message = "release access_" + f"token={secret}"
    repo = git_repo(tmp_path, {"app.py": "version = 1\n"})
    run_git(repo, "tag", "-a", "release", "-m", credential_message)

    findings = audit_repository(repo, vault=None, include_history=True)

    assert any(item.category == "credential" for item in findings)
    assert all(secret not in item.location for item in findings)
    assert all(secret not in item.message for item in findings)


def test_absolute_home_path_in_commit_identity_is_rejected_and_redacted(tmp_path):
    private_path = "/" + "/".join(["home", "private-person", "vault"])
    repo = git_repo(tmp_path, {"app.py": "version = 1\n"})
    run_git(repo, "config", "user.name", private_path)
    commit(repo, {"app.py": "version = 2\n"}, "identity metadata")

    findings = audit_repository(repo, vault=None, include_history=False)

    assert any(item.category == "absolute-private-path" for item in findings)
    assert all("private-person" not in item.location for item in findings)
    assert all("private-person" not in item.message for item in findings)


def test_github_generated_owner_metadata_is_the_only_owner_exception(tmp_path):
    owner = "synthetic-owner"
    repo = git_repo(tmp_path / "repo", {"app.py": "version = 1\n"})
    vault = synthetic_vault(tmp_path / "vault", entity=owner)
    run_git(repo, "remote", "add", "origin", f"https://github.com/{owner}/oneos.git")
    base_branch = run_git(repo, "branch", "--show-current").strip()
    run_git(repo, "switch", "-q", "-c", "codex/topic")
    commit(repo, {"topic.py": "ready = True\n"}, "topic")
    run_git(repo, "switch", "-q", base_branch)
    run_git(repo, "config", "user.name", owner)
    run_git(repo, "config", "user.email", f"123+{owner}@users.noreply.github.com")
    run_git(
        repo,
        "merge",
        "-q",
        "--no-ff",
        "codex/topic",
        "-m",
        f"Merge pull request #7 from {owner}/codex/topic",
    )

    assert audit_repository(repo, vault=vault, include_history=False) == []

    commit(repo, {"app.py": "version = 3\n"}, f"document {owner}")
    findings = audit_repository(repo, vault=vault, include_history=False)
    assert any(item.category == "instance-value" for item in findings)


def test_github_noreply_identity_for_non_owner_is_not_exempt(tmp_path):
    owner = "synthetic-owner"
    contributor = "synthetic-contributor"
    repo = git_repo(tmp_path / "repo", {"app.py": "version = 1\n"})
    run_git(repo, "remote", "add", "origin", f"https://github.com/{owner}/oneos.git")
    run_git(repo, "config", "user.name", contributor)
    run_git(
        repo,
        "config",
        "user.email",
        f"123+{contributor}@users.noreply.github.com",
    )
    commit(repo, {"app.py": "version = 2\n"}, "contributor metadata")
    vault = synthetic_vault(tmp_path / "vault", entity=contributor)

    findings = audit_repository(repo, vault=vault, include_history=False)

    assert any(item.category == "instance-value" for item in findings)
    assert all(owner not in item.location for item in findings)
    assert all(owner not in item.message for item in findings)

    commit(
        repo,
        {"app.py": "version = 4\n"},
        f"Merge pull request #8 from {owner}/codex/not-a-merge",
    )
    findings = audit_repository(repo, vault=vault, include_history=False)
    assert any(item.category == "instance-value" for item in findings)


def test_short_product_and_member_ids_are_rejected_in_structured_text(tmp_path):
    entity = "customer-zeta"
    product = "pq"
    member = "mn"
    repo = git_repo(tmp_path / "repo", {"note.md": f"product: {product}\n"})
    commit(repo, {"app.py": "version = 2\n"}, f"member: {member}")
    vault = synthetic_vault(
        tmp_path / "vault", entity=entity, product=product, member=member
    )

    findings = audit_repository(repo, vault=vault, include_history=False)

    assert [item.category for item in findings] == [
        "instance-value",
        "instance-value",
    ]
    assert all(product not in item.message for item in findings)
    assert all(member not in item.message for item in findings)


def test_registry_labels_from_all_private_sources_are_rejected(tmp_path):
    labels = ["Private Entity Name", "Private Product Name", "Private Member Name"]
    repo = git_repo(tmp_path / "repo", {"note.md": "\n".join(labels) + "\n"})
    vault = synthetic_vault(
        tmp_path / "vault",
        entity="customer-zeta",
        product="product-zeta",
        member="member-zeta",
        entity_label=labels[0],
        product_label=labels[1],
        member_label=labels[2],
    )

    findings = audit_repository(repo, vault=vault, include_history=False)

    assert [item.category for item in findings] == [
        "instance-value",
        "instance-value",
        "instance-value",
    ]
    assert all(label not in item.message for label in labels for item in findings)


def test_short_display_vocabulary_is_rejected_in_ordinary_prose(tmp_path):
    private_label = "Amy"
    repo = git_repo(tmp_path / "repo", {"note.md": f"{private_label} joined\n"})
    vault = synthetic_vault(
        tmp_path / "vault",
        entity="customer-zeta",
        entity_label=private_label,
    )

    findings = audit_repository(repo, vault=vault, include_history=False)

    assert [item.category for item in findings] == ["instance-value"]
    assert private_label not in findings[0].message


def test_two_character_display_vocabulary_is_allowed_as_ambiguous_prose(tmp_path):
    private_label = "NN"
    repo = git_repo(
        tmp_path / "repo",
        {"note.md": f"<NN-module>\nlabel: {private_label}\n"},
    )
    vault = synthetic_vault(
        tmp_path / "vault",
        entity="customer-zeta",
        entity_label=private_label,
    )

    assert audit_repository(repo, vault=vault, include_history=False) == []


def test_short_registry_identifier_is_allowed_in_ordinary_prose(tmp_path):
    repo = git_repo(tmp_path / "repo", {"note.md": "it remains generic prose\n"})
    vault = synthetic_vault(tmp_path / "vault", entity="it")

    assert audit_repository(repo, vault=vault, include_history=False) == []


def test_short_registry_identifier_matches_inline_structured_boundaries(tmp_path):
    private_identifier = "xy"
    structured = (
        f'{{"entity": "{private_identifier}"}}\n'
        f"- member_id: {private_identifier}\n"
    )
    repo = git_repo(tmp_path / "repo", {"note.yaml": structured})
    vault = synthetic_vault(tmp_path / "vault", entity=private_identifier)

    findings = audit_repository(repo, vault=vault, include_history=False)

    assert [item.category for item in findings] == [
        "instance-value",
        "instance-value",
    ]


def test_printable_binary_signature_still_fails_closed(tmp_path):
    repo = git_repo(tmp_path, {"app.py": "title = 'OneOS'\n"})
    commit_bytes(
        repo,
        {"assets/printable.pdf": b"%PDF-1.7\nprintable but still binary\n"},
        "binary",
    )

    findings = audit_repository(repo, vault=None, include_history=False)

    assert [item.category for item in findings] == ["unapproved-binary"]


def test_printable_binary_with_unknown_extension_fails_closed(tmp_path):
    repo = git_repo(tmp_path, {"app.py": "title = 'OneOS'\n"})
    commit_bytes(repo, {"assets/image.ppm": b"P6\n1 1\n255\nABC"}, "binary")

    findings = audit_repository(repo, vault=None, include_history=False)

    assert [item.category for item in findings] == ["unapproved-binary"]


def test_credential_shaped_value_in_tracked_name_is_rejected_and_redacted(tmp_path):
    secret = "synthetic-value-123"
    private_path = "leak/access_" + f"token={secret}/note.md"
    repo = git_repo(tmp_path, {private_path: "synthetic\n"})

    findings = audit_repository(repo, vault=None, include_history=False)

    assert [item.category for item in findings] == ["credential"]
    assert secret not in findings[0].location
    assert secret not in findings[0].message


def test_credential_field_type_annotations_are_allowed(tmp_path):
    source = (
        "client_secret: SecretStr\n"
        "password: Optional[str] = None\n"
        "access_token: Annotated[str, 'runtime']\n"
    )
    repo = git_repo(tmp_path, {"settings.py": source})

    assert audit_repository(repo, vault=None, include_history=False) == []


def test_control_characters_in_finding_path_cannot_spoof_output(tmp_path):
    assignment = "access_" + "token='synthetic-value-123'\n"
    repo = git_repo(tmp_path, {"safe\nspoof.py": assignment})

    findings = audit_repository(repo, vault=None, include_history=False)

    assert [item.category for item in findings] == ["credential"]
    assert "\n" not in findings[0].location
    assert findings[0].location.endswith(":<redacted-path>:1")


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
