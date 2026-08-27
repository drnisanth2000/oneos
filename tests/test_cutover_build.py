import os
from pathlib import Path
import os
import shutil
import stat
import subprocess
import textwrap

import pytest

from app.cutover_locations import advisory_occurrences
from app.cutover_build import (
    CutoverCleanupError,
    CutoverError,
    isolated_worktree,
)
from tests.conftest import git_head, git_is_clean, git_vault


def commit_in(root: Path, message: str) -> str:
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=t@e", "-c", "user.name=t",
         "commit", "-q", "-m", message],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return git_head(root)


def test_isolated_worktree_starts_at_the_requested_head(tmp_path: Path):
    vault = git_vault(tmp_path / "vault", {"a.md": "x\n"})
    head = git_head(vault)

    with isolated_worktree(vault, head) as scratch:
        assert git_head(scratch) == head
        assert scratch != vault


def test_isolated_worktree_is_detached_and_creates_no_branch(tmp_path: Path):
    vault = git_vault(tmp_path / "vault", {"a.md": "x\n"})
    before = subprocess.run(
        ["git", "branch", "--format=%(refname)"],
        cwd=vault, check=True, capture_output=True, text=True,
    ).stdout

    with isolated_worktree(vault, git_head(vault)) as scratch:
        symbolic = subprocess.run(
            ["git", "symbolic-ref", "-q", "HEAD"],
            cwd=scratch, check=False, capture_output=True, text=True,
        )
        assert symbolic.returncode != 0  # detached HEAD

    after = subprocess.run(
        ["git", "branch", "--format=%(refname)"],
        cwd=vault, check=True, capture_output=True, text=True,
    ).stdout
    assert before == after


def test_a_pre_existing_branch_is_never_deleted(tmp_path: Path):
    vault = git_vault(tmp_path / "vault", {"a.md": "x\n"})
    head = git_head(vault)
    subprocess.run(
        ["git", "branch", f"cutover/build-{head[:12]}"],
        cwd=vault, check=True, capture_output=True,
    )

    with isolated_worktree(vault, head):
        pass

    remaining = subprocess.run(
        ["git", "branch", "--format=%(refname:short)"],
        cwd=vault, check=True, capture_output=True, text=True,
    ).stdout
    assert f"cutover/build-{head[:12]}" in remaining


def test_isolated_worktree_writes_do_not_reach_the_live_vault(tmp_path: Path):
    vault = git_vault(tmp_path / "vault", {"a.md": "x\n"})
    head = git_head(vault)

    with isolated_worktree(vault, head) as scratch:
        (scratch / "a.md").write_text("changed\n", encoding="utf-8")

    assert (vault / "a.md").read_text(encoding="utf-8") == "x\n"
    assert git_is_clean(vault)
    assert git_head(vault) == head


def test_isolated_worktree_is_removed_on_success_and_failure(tmp_path: Path):
    vault = git_vault(tmp_path / "vault", {"a.md": "x\n"})
    recorded: list[Path] = []

    with isolated_worktree(vault, git_head(vault)) as scratch:
        recorded.append(scratch)
    assert not recorded[0].exists()

    with pytest.raises(RuntimeError):
        with isolated_worktree(vault, git_head(vault)) as scratch:
            recorded.append(scratch)
            raise RuntimeError("injected")
    assert not recorded[1].exists()
    assert git_is_clean(vault)


def test_a_commit_built_in_isolation_is_visible_from_the_vault(tmp_path: Path):
    vault = git_vault(tmp_path / "vault", {"a.md": "x\n"})
    head = git_head(vault)

    with isolated_worktree(vault, head) as scratch:
        (scratch / "a.md").write_text("changed\n", encoding="utf-8")
        built = commit_in(scratch, "built")

    kind = subprocess.run(
        ["git", "cat-file", "-t", built],
        cwd=vault, check=True, capture_output=True, text=True,
    ).stdout.strip()
    assert kind == "commit"
    assert git_head(vault) == head


def test_a_head_that_is_not_the_requested_source_is_refused(tmp_path: Path):
    vault = git_vault(tmp_path / "vault", {"a.md": "x\n"})

    with pytest.raises(CutoverError):
        with isolated_worktree(vault, "b" * 40):
            pass


import sqlite3

import yaml

import app.cutover_build as cutover_build

from app.cutover_locations import advisory_occurrences
from app.cutover_build import (
    BuildResult,
    build_cutover,
    git,
    require_executor_revision,
    run_vault_validators,
)
from app.cutover_locations import stable_advisory_context
from app.cutover_manifest import (
    ApprovalManifest,
    ApprovalRecord,
    DatabaseTarget,
    Disposition,
    Mapping,
    canonical_bytes,
    load_manifest,
    manifest_digest,
)
from tests.conftest import git_count_commits, git_status_bytes


CUTOVER_MAPPINGS = (
    Mapping(axis="entity", old="ab", new="ab-entity"),
    Mapping(axis="product", old="q7", new="q7-product"),
    Mapping(axis="member", old="m7", new="m7-member"),
    Mapping(axis="workspace", old="w7", new="w7-workspace"),
)
EXECUTOR_COMMIT = "e" * 40


def disposition(
    path: str,
    line: int,
    axis: str,
    old: str,
    text: str,
    *,
    ordinal: int = 1,
    kind: str = "incidental",
    typed_location: str = "",
) -> Disposition:
    return Disposition(
        path=path,
        axis=axis,
        old=old,
        ordinal=ordinal,
        context_sha256=stable_advisory_context(text, CUTOVER_MAPPINGS),
        line=line,
        kind=kind,
        typed_location=typed_location,
    )


def cutover_vault(root: Path) -> Path:
    vault = git_vault(
        root,
        {
            "_system/entities.yaml": "entities:\n  ab:\n    label: A\n",
            "_system/products.yaml": "products:\n  ab:\n    q7:\n      label: Q\n",
            "_system/members.yaml": "members:\n  ab:\n    - {id: m7}\n",
            "_system/workspaces.yaml":
                "workspaces:\n  - {id: w7, entity: ab, product: q7, member: m7, kind: product}\n",
            "_system/scripts/action-policy.yaml":
                'actors:\n  h:\n    allow:\n      - {action: read, paths: ["ab/**"], '
                'except: ["ab/.sensitive/**"]}\n',
            "_system/scripts/check_v2.py":
                'print("0 error(s), 0 warning(s)")\n',
            "_system/scripts/test_cutover_validator.py":
                "import unittest\n\n"
                "class ValidatorSmokeTest(unittest.TestCase):\n"
                "    def test_ok(self):\n"
                "        self.assertTrue(True)\n",
            "ab/00-inbox/note.md":
                "---\nentity: ab\nproduct: q7\nmember: m7\n---\n\nthe ab word\n",
        },
    )
    db = vault / "ab" / "books.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE ledger (product TEXT)")
    conn.execute("INSERT INTO ledger VALUES ('q7')")
    conn.execute("CREATE TABLE roster (member TEXT)")
    conn.execute("INSERT INTO roster VALUES ('m7')")
    conn.commit()
    conn.close()
    commit_in(vault, "add db")
    return vault


def approved(vault: Path, dispositions=None) -> tuple[bytes, ApprovalRecord]:
    manifest = ApprovalManifest(
        source_head=git_head(vault),
        mappings=CUTOVER_MAPPINGS,
        databases=(
            DatabaseTarget(
                path="ab/books.db", table="ledger", column="product", axis="product"
            ),
            DatabaseTarget(
                path="ab/books.db", table="roster", column="member", axis="member"
            ),
        ),
        dispositions=dispositions
        if dispositions is not None
        else (
            disposition(
                "ab/00-inbox/note.md",
                7,
                "entity",
                "ab",
                "the ab word",
            ),
        ),
    )
    raw = canonical_bytes(manifest)
    return raw, ApprovalRecord(
        manifest_sha256=manifest_digest(manifest),
        executor_commit=EXECUTOR_COMMIT,
        approved_by="owner",
    )


def executor_record(repo: Path, *, commit: str | None = None) -> ApprovalRecord:
    return ApprovalRecord(
        manifest_sha256="a" * 64,
        executor_commit=commit or git(repo, "rev-parse", "HEAD").strip(),
        approved_by="owner",
    )


def test_executor_revision_accepts_the_clean_approved_commit(tmp_path: Path):
    repo = cutover_vault(tmp_path / "executor")

    assert require_executor_revision(executor_record(repo), repo) == git(
        repo, "rev-parse", "HEAD"
    ).strip()


def test_executor_revision_refuses_a_different_commit(tmp_path: Path):
    repo = cutover_vault(tmp_path / "executor")

    with pytest.raises(cutover_build.CutoverError, match="executor commit"):
        require_executor_revision(executor_record(repo, commit="f" * 40), repo)


def test_executor_revision_refuses_a_dirty_worktree(tmp_path: Path):
    repo = cutover_vault(tmp_path / "executor")
    (repo / "dirty.py").write_text("changed\n", encoding="utf-8")

    with pytest.raises(cutover_build.CutoverError, match="executor worktree"):
        require_executor_revision(executor_record(repo), repo)


def test_build_leaves_the_live_vault_untouched(tmp_path: Path):
    vault = cutover_vault(tmp_path / "vault")
    head = git_head(vault)
    before = git_count_commits(vault)

    raw, record = approved(vault)
    build_cutover(vault, raw, record)

    assert git_head(vault) == head
    assert git_count_commits(vault) == before
    assert git_is_clean(vault)


def test_build_refuses_a_manifest_that_does_not_match_its_record(tmp_path: Path):
    vault = cutover_vault(tmp_path / "vault")
    raw, _ = approved(vault)

    with pytest.raises(Exception):
        build_cutover(
            vault,
            raw,
            ApprovalRecord(
                manifest_sha256="c" * 64,
                executor_commit=EXECUTOR_COMMIT,
                approved_by="owner",
            ),
        )
    assert git_is_clean(vault)


def test_build_refuses_a_mapping_that_is_not_the_deterministic_result(tmp_path: Path):
    vault = cutover_vault(tmp_path / "vault")
    manifest = ApprovalManifest(
        source_head=git_head(vault),
        mappings=(Mapping(axis="entity", old="ab", new="ab-entity-2"),),
        databases=(),
        dispositions=(),
    )
    raw = canonical_bytes(manifest)
    record = ApprovalRecord(
        manifest_sha256=manifest_digest(manifest),
        executor_commit=EXECUTOR_COMMIT,
        approved_by="owner",
    )

    with pytest.raises(Exception, match="deterministic"):
        build_cutover(vault, raw, record)


def test_build_refuses_an_undispositioned_advisory_occurrence(tmp_path: Path):
    vault = cutover_vault(tmp_path / "vault")
    raw, record = approved(vault, dispositions=())

    with pytest.raises(Exception, match="disposition"):
        build_cutover(vault, raw, record)


def test_build_refuses_a_structural_disposition_naming_no_typed_location(
    tmp_path: Path,
):
    vault = cutover_vault(tmp_path / "vault")
    raw, record = approved(
        vault,
        dispositions=(
            disposition(
                "ab/00-inbox/note.md",
                7,
                "entity",
                "ab",
                "the ab word",
                kind="structural",
                typed_location="entity:nowhere:nothing",
            ),
        ),
    )

    with pytest.raises(Exception, match="typed location"):
        build_cutover(vault, raw, record)


def test_build_refuses_a_colliding_mapping(tmp_path: Path):
    vault = git_vault(
        tmp_path / "vault",
        {"_system/entities.yaml": "entities:\n  ab:\n    label: A\n  ab-entity:\n    label: B\n"},
    )
    manifest = ApprovalManifest(
        source_head=git_head(vault),
        mappings=(Mapping(axis="entity", old="ab", new="ab-entity"),),
        databases=(),
        dispositions=(),
    )
    raw = canonical_bytes(manifest)
    record = ApprovalRecord(
        manifest_sha256=manifest_digest(manifest),
        executor_commit=EXECUTOR_COMMIT,
        approved_by="owner",
    )

    with pytest.raises(Exception, match="collides"):
        build_cutover(vault, raw, record, validator=lambda _root: None)


def test_build_collision_check_uses_the_manifest_source_head(
    tmp_path: Path, monkeypatch
):
    vault = cutover_vault(tmp_path / "vault")
    raw, record = approved(vault)
    registry = vault / "_system" / "entities.yaml"
    original = registry.read_bytes()
    real = cutover_build.require_clean_status
    fired = False

    def live_changes_after_the_initial_status_check(root):
        nonlocal fired
        real(root)
        registry.write_text(
            "entities:\n  ab:\n    label: A\n  ab-entity:\n    label: Later\n",
            encoding="utf-8",
        )
        fired = True

    monkeypatch.setattr(
        cutover_build,
        "require_clean_status",
        live_changes_after_the_initial_status_check,
    )
    try:
        build_cutover(vault, raw, record)
    finally:
        registry.write_bytes(original)

    assert fired, "the live-race probe never ran"


def test_build_refuses_an_entity_with_ignored_content(tmp_path: Path):
    vault = git_vault(
        tmp_path / "vault",
        {
            ".gitignore": ".sensitive/\n",
            "_system/entities.yaml": "entities:\n  ab:\n    label: A\n",
            "ab/00-inbox/n.md": "---\nentity: ab\n---\n",
        },
    )
    (vault / "ab" / ".sensitive").mkdir()
    (vault / "ab" / ".sensitive" / "s.md").write_text("s\n", encoding="utf-8")
    manifest = ApprovalManifest(
        source_head=git_head(vault),
        mappings=(Mapping(axis="entity", old="ab", new="ab-entity"),),
        databases=(),
        dispositions=(),
    )
    raw = canonical_bytes(manifest)
    record = ApprovalRecord(
        manifest_sha256=manifest_digest(manifest),
        executor_commit=EXECUTOR_COMMIT,
        approved_by="owner",
    )

    with pytest.raises(Exception, match="ignored or untracked"):
        build_cutover(vault, raw, record, validator=lambda _root: None)


def test_dispositions_are_checked_before_any_path_move(tmp_path: Path, monkeypatch):
    vault = cutover_vault(tmp_path / "vault")
    raw, record = approved(vault)
    real = cutover_build._require_dispositions

    def assert_source_shape(root, manifest):
        assert (root / "ab").is_dir(), "dispositions were not checked on the source tree"
        assert not (root / "ab-entity").exists(), "an entity moved before disposition checking"
        return real(root, manifest)

    monkeypatch.setattr(cutover_build, "_require_dispositions", assert_source_shape)

    build_cutover(vault, raw, record)


def test_build_gate_refuses_a_writer_that_misses_the_policy_except_half(
    tmp_path: Path, monkeypatch
):
    vault = cutover_vault(tmp_path / "vault")
    raw, record = approved(vault)
    real = cutover_build.rewrite_policy_path_heads

    def paths_only(text, old, new):
        rewritten = real(text, old, new)
        return rewritten.replace(
            f'except: ["{new}/.sensitive/**"]',
            f'except: ["{old}/.sensitive/**"]',
        )

    monkeypatch.setattr(cutover_build, "rewrite_policy_path_heads", paths_only)

    with pytest.raises(CutoverError, match="entity:action-policy:except"):
        build_cutover(vault, raw, record)


def test_build_gate_refuses_a_database_writer_that_leaves_the_old_value(
    tmp_path: Path, monkeypatch
):
    vault = cutover_vault(tmp_path / "vault")
    raw, record = approved(vault)
    monkeypatch.setattr(cutover_build, "apply_database_mappings", lambda *_: ())

    with pytest.raises(CutoverError, match="database residual after update"):
        build_cutover(vault, raw, record)


def test_sequential_application_preserves_every_mapping_touching_one_file(
    tmp_path: Path,
):
    vault = cutover_vault(tmp_path / "vault")
    raw, _record = approved(vault)
    manifest = load_manifest(raw)

    cutover_build._apply_mappings_in_order(vault, manifest)

    workspaces = (vault / "_system" / "workspaces.yaml").read_text(encoding="utf-8")
    assert "id: w7-workspace" in workspaces, "workspace rewrite was lost"
    assert "entity: ab-entity" in workspaces, "entity rewrite was lost"
    assert "product: q7-product" in workspaces, "product rewrite was lost"
    assert "member: m7-member" in workspaces, "member rewrite was lost"


def test_build_refuses_an_extra_or_stale_disposition(tmp_path: Path):
    vault = cutover_vault(tmp_path / "vault")
    raw, record = approved(
        vault,
        dispositions=(
            disposition(
                "ab/00-inbox/note.md", 7, "entity", "ab", "the ab word"
            ),
            disposition("missing.md", 1, "entity", "ab", "missing ab"),
        ),
    )

    with pytest.raises(Exception, match="exactly match"):
        build_cutover(vault, raw, record)


def test_build_regenerates_the_advisory_report_after_rewriting(
    tmp_path: Path, monkeypatch
):
    vault = cutover_vault(tmp_path / "vault")
    raw, record = approved(vault)
    real = cutover_build._apply_entity_mapping

    def introduces_unapproved_occurrence(root, old, new):
        real(root, old, new)
        note = root / new / "00-inbox" / "note.md"
        note.write_text(note.read_text(encoding="utf-8") + "new ab occurrence\n", encoding="utf-8")

    monkeypatch.setattr(cutover_build, "_apply_entity_mapping", introduces_unapproved_occurrence)

    with pytest.raises(Exception, match="advisory report changed"):
        build_cutover(vault, raw, record)


def test_post_advisory_identity_survives_a_display_line_shift(
    tmp_path: Path, monkeypatch
):
    vault = cutover_vault(tmp_path / "vault")
    raw, record = approved(vault)
    real = cutover_build._apply_entity_mapping

    def shifts_only_the_display_line(root, old, new):
        real(root, old, new)
        note = root / new / "00-inbox" / "note.md"
        text = note.read_text(encoding="utf-8")
        note.write_text(
            text.replace("\nthe ab word\n", "\nunrelated line\nthe ab word\n"),
            encoding="utf-8",
        )

    monkeypatch.setattr(
        cutover_build, "_apply_entity_mapping", shifts_only_the_display_line
    )

    build_cutover(vault, raw, record)


def test_post_advisory_identity_refuses_reordering_approved_contexts(
    tmp_path: Path, monkeypatch
):
    vault = cutover_vault(tmp_path / "vault")
    note = vault / "ab" / "00-inbox" / "note.md"
    note.write_text(
        note.read_text(encoding="utf-8").replace(
            "the ab word\n", "first ab context\nsecond ab context\n"
        ),
        encoding="utf-8",
    )
    commit_in(vault, "seed two approved contexts")
    raw, record = approved(
        vault,
        dispositions=(
            disposition(
                "ab/00-inbox/note.md",
                7,
                "entity",
                "ab",
                "first ab context",
                ordinal=1,
            ),
            disposition(
                "ab/00-inbox/note.md",
                8,
                "entity",
                "ab",
                "second ab context",
                ordinal=2,
            ),
        ),
    )
    real = cutover_build._apply_entity_mapping

    def reorders_without_changing_either_occurrence(root, old, new):
        real(root, old, new)
        moved = root / new / "00-inbox" / "note.md"
        text = moved.read_text(encoding="utf-8")
        moved.write_text(
            text.replace(
                "first ab context\nsecond ab context\n",
                "second ab context\nfirst ab context\n",
            ),
            encoding="utf-8",
        )

    monkeypatch.setattr(
        cutover_build,
        "_apply_entity_mapping",
        reorders_without_changing_either_occurrence,
    )

    with pytest.raises(CutoverError, match="advisory report changed"):
        build_cutover(vault, raw, record)


def test_post_advisory_identity_rejects_same_count_with_changed_context(
    tmp_path: Path, monkeypatch
):
    vault = cutover_vault(tmp_path / "vault")
    raw, record = approved(vault)
    real = cutover_build._apply_entity_mapping

    def changes_context_without_changing_the_count(root, old, new):
        real(root, old, new)
        note = root / new / "00-inbox" / "note.md"
        text = note.read_text(encoding="utf-8")
        note.write_text(
            text.replace("the ab word", "changed ab word"), encoding="utf-8"
        )

    monkeypatch.setattr(
        cutover_build,
        "_apply_entity_mapping",
        changes_context_without_changing_the_count,
    )

    with pytest.raises(Exception, match="advisory report changed"):
        build_cutover(vault, raw, record)


def test_validators_run_after_migration_and_before_commit(tmp_path: Path):
    vault = cutover_vault(tmp_path / "vault")
    raw, record = approved(vault)
    observed: list[tuple[bool, str]] = []

    def validator(root: Path) -> None:
        observed.append(((root / "ab-entity").is_dir(), git_head(root)))

    result = build_cutover(vault, raw, record, validator=validator)

    assert observed == [(True, result.source_head)]


def test_validator_failure_discards_the_build_and_preserves_the_live_vault(
    tmp_path: Path,
):
    vault = cutover_vault(tmp_path / "vault")
    raw, record = approved(vault)
    before = (git_head(vault), git_status_bytes(vault), (vault / "ab/books.db").read_bytes())

    def fail(_root: Path) -> None:
        raise CutoverError("validator failed deliberately")

    with pytest.raises(CutoverError, match="validator failed deliberately"):
        build_cutover(vault, raw, record, validator=fail)

    assert (git_head(vault), git_status_bytes(vault), (vault / "ab/books.db").read_bytes()) == before


def test_default_validator_accepts_the_documented_check_v2_summary(tmp_path: Path):
    vault = cutover_vault(tmp_path / "vault")

    run_vault_validators(vault)


def test_default_validator_does_not_read_ten_errors_as_zero(tmp_path: Path):
    vault = cutover_vault(tmp_path / "vault")
    (vault / "_system" / "scripts" / "check_v2.py").write_text(
        'print("10 error(s), 0 warning(s)")\n', encoding="utf-8"
    )

    with pytest.raises(CutoverError, match="0 error"):
        run_vault_validators(vault)


def _opaque_vault_tree(vault: Path) -> dict[str, tuple[str, bytes]]:
    captured = {}
    for path in sorted(vault.rglob("*")):
        relative = path.relative_to(vault)
        if ".git" in relative.parts:
            continue
        if path.is_symlink():
            captured[relative.as_posix()] = ("symlink", os.readlink(path).encode())
        elif path.is_file():
            captured[relative.as_posix()] = ("file", path.read_bytes())
        elif path.is_dir():
            captured[relative.as_posix()] = ("dir", b"")
    return captured


@pytest.mark.parametrize(
    "stage",
    ("mapping-validation", "database", "mapping", "residual", "advisory", "validator", "commit"),
)
def test_every_precommit_stage_failure_preserves_the_complete_live_vault(
    tmp_path: Path, monkeypatch, stage: str
):
    vault = cutover_vault(tmp_path / "vault")
    (vault / ".gitignore").write_text("zz/.sensitive/\n", encoding="utf-8")
    commit_in(vault, "ignore unrelated private state")
    hidden = vault / "zz" / ".sensitive" / "keep.bin"
    hidden.parent.mkdir(parents=True)
    hidden.write_bytes(b"must survive")
    raw, record = approved(vault)
    boundary = (git_head(vault), git_status_bytes(vault), _opaque_vault_tree(vault))

    if stage == "mapping-validation":
        monkeypatch.setattr(cutover_build, "validate_mapping_pair", lambda *_: (_ for _ in ()).throw(CutoverError("stage mapping-validation")))
    elif stage == "database":
        monkeypatch.setattr(cutover_build, "apply_database_mappings", lambda *_: (_ for _ in ()).throw(CutoverError("stage database")))
    elif stage == "mapping":
        monkeypatch.setattr(cutover_build, "_apply_entity_mapping", lambda *_: (_ for _ in ()).throw(CutoverError("stage mapping")))
    elif stage == "residual":
        monkeypatch.setattr(cutover_build, "scoped_residuals", lambda *_: (_ for _ in ()).throw(CutoverError("stage residual")))
    elif stage == "advisory":
        monkeypatch.setattr(cutover_build, "_require_post_advisory", lambda *_: (_ for _ in ()).throw(CutoverError("stage advisory")))
    elif stage == "commit":
        real_git = cutover_build.git

        def fail_commit(root, *args):
            if "commit" in args:
                raise CutoverError("stage commit")
            return real_git(root, *args)

        monkeypatch.setattr(cutover_build, "git", fail_commit)

    def validator(_root: Path) -> None:
        if stage == "validator":
            raise CutoverError("stage validator")

    with pytest.raises(CutoverError, match=f"stage {stage}"):
        build_cutover(vault, raw, record, validator=validator)

    assert (
        git_head(vault),
        git_status_bytes(vault),
        _opaque_vault_tree(vault),
    ) == boundary, f"precommit stage {stage} mutated the live vault"


def test_final_database_gate_reads_the_moved_artifact(tmp_path: Path, monkeypatch):
    """The post-move residual query must inspect `ab-entity/books.db`.

    The approved target names `ab/books.db`, which no longer exists once the
    entity pass has moved the directory. If the final gate still resolved the
    approved path it would either raise for a missing file or silently verify
    nothing; either way an old value reintroduced into the moved database
    would reach the commit.
    """
    import app.cutover_build as cutover_build

    vault = cutover_vault(tmp_path / "vault")
    raw, record = approved(vault)
    seen: dict[str, bool] = {}

    real_apply = cutover_build._apply_mappings_in_order

    def apply_then_reinsert(root: Path, manifest) -> None:
        real_apply(root, manifest)
        moved = root / "ab-entity" / "books.db"
        seen["moved_exists"] = moved.is_file()
        seen["source_gone"] = not (root / "ab" / "books.db").exists()
        conn = sqlite3.connect(moved)
        try:
            conn.execute("INSERT INTO ledger (product) VALUES ('q7')")
            conn.commit()
        finally:
            conn.close()

    monkeypatch.setattr(
        cutover_build, "_apply_mappings_in_order", apply_then_reinsert
    )

    with pytest.raises(CutoverError, match="database residual after migration"):
        build_cutover(vault, raw, record)

    assert seen["moved_exists"] is True
    assert seen["source_gone"] is True
    assert git_is_clean(vault)


import hashlib

from app.action_receipts import make_action_receipt, render_action_receipt, resolve_head_receipt
from app.cutover import promote


def promoted(vault: Path) -> BuildResult:
    head = git_head(vault)
    raw, record = approved(vault)
    result = build_cutover(vault, raw, record)
    promote(vault, result.commit, head, git_status_bytes(vault), ["ab"])
    return result


def test_one_commit_is_produced_and_revert_restores_everything(tmp_path: Path):
    vault = cutover_vault(tmp_path / "vault")
    before = git_count_commits(vault)

    result = promoted(vault)

    assert git_count_commits(vault) == before + 1
    assert (vault / "ab-entity" / "00-inbox" / "note.md").is_file()

    # This is the approved rollback window: promotion has completed but every
    # writer is still quiesced, so no later SQLite write can be overwritten.
    subprocess.run(
        ["git", "-c", "user.email=t@e", "-c", "user.name=t",
         "revert", "--no-edit", result.commit],
        cwd=vault, check=True, capture_output=True,
    )

    assert (vault / "ab" / "00-inbox" / "note.md").is_file()
    conn = sqlite3.connect(vault / "ab" / "books.db")
    try:
        assert conn.execute("SELECT product FROM ledger").fetchall() == [("q7",)]
        assert conn.execute("SELECT member FROM roster").fetchall() == [("m7",)]
    finally:
        conn.close()


def test_ordinary_prose_containing_a_short_identifier_is_untouched(tmp_path: Path):
    vault = cutover_vault(tmp_path / "vault")
    promoted(vault)

    note = (vault / "ab-entity" / "00-inbox" / "note.md").read_text(encoding="utf-8")
    assert "entity: ab-entity" in note
    assert "product: q7-product" in note
    assert "member: m7-member" in note
    assert "the ab word" in note


def test_the_database_is_updated_before_the_entity_directory_moves(tmp_path: Path):
    vault = cutover_vault(tmp_path / "vault")
    promoted(vault)

    moved = vault / "ab-entity" / "books.db"
    assert moved.is_file()
    conn = sqlite3.connect(moved)
    try:
        assert conn.execute("SELECT product FROM ledger").fetchall() == [
            ("q7-product",)
        ]
        assert conn.execute("SELECT member FROM roster").fetchall() == [
            ("m7-member",)
        ]
    finally:
        conn.close()


def test_the_promoted_database_blob_is_the_exact_verified_build_artifact(
    tmp_path: Path,
):
    vault = cutover_vault(tmp_path / "vault")

    result = promoted(vault)

    built_blob = git(
        vault, "rev-parse", f"{result.commit}:ab-entity/books.db"
    ).strip()
    promoted_blob = git(vault, "rev-parse", "HEAD:ab-entity/books.db").strip()
    assert promoted_blob == built_blob
    assert git(vault, "rev-parse", "HEAD^{tree}").strip() == result.tree


def test_a_spent_proposal_id_is_still_refused_after_an_entity_cutover(tmp_path: Path):
    vault = cutover_vault(tmp_path / "vault")
    proposal_id = "20260826T120000-" + "ab" * 16
    receipt = make_action_receipt(proposal_id, "a" * 64, "approval")
    store = vault / "ab" / "outbox" / ".receipts"
    store.mkdir(parents=True)
    (store / f"{proposal_id}.yaml").write_bytes(render_action_receipt(receipt))
    commit_in(vault, "add receipt")

    promoted(vault)

    resolution = resolve_head_receipt(vault, "ab-entity", proposal_id)
    assert resolution.error is None
    assert resolution.receipt == receipt


def test_proposal_prefixes_are_rewritten_and_a_pre_cutover_token_is_refused(
    tmp_path: Path,
):
    vault = cutover_vault(tmp_path / "vault")
    outbox = vault / "ab" / "outbox"
    outbox.mkdir(parents=True)
    proposal_id = "20260826T120000-" + "cd" * 16
    record_path = outbox / f"{proposal_id}.yaml"
    record_path.write_text(
        f"id: {proposal_id}\n"
        "action: classify\n"
        "entity: ab\n"
        "src: ab/00-inbox/active/x.md\n"
        "dst: ab/09-marketing/active/x.md\n"
        'opaque: "keep: [x]"  # exact\n',
        encoding="utf-8",
    )
    before_token = hashlib.sha256(record_path.read_bytes()).hexdigest()
    commit_in(vault, "add proposal")

    promoted(vault)

    moved = vault / "ab-entity" / "outbox" / record_path.name
    text = moved.read_text(encoding="utf-8")
    assert "entity: ab-entity" in text
    assert "src: ab-entity/00-inbox/active/x.md" in text
    assert "dst: ab-entity/09-marketing/active/x.md" in text
    assert 'opaque: "keep: [x]"  # exact' in text, (
        "proposal rewrite altered bytes outside the approved fields"
    )
    # S7 binds an approval to exact proposal bytes, so a token issued before the
    # cutover no longer matches. Failing closed is correct.
    assert hashlib.sha256(moved.read_bytes()).hexdigest() != before_token


import re as _re


def _glob_to_regex(pattern: str) -> _re.Pattern[str]:
    out, index = [], 0
    while index < len(pattern):
        if pattern.startswith("**/", index):
            out.append("(?:.*/)?")
            index += 3
        elif pattern.startswith("**", index):
            out.append(".*")
            index += 2
        elif pattern[index] == "*":
            out.append("[^/]*")
            index += 1
        else:
            out.append(_re.escape(pattern[index]))
            index += 1
    return _re.compile("^" + "".join(out) + "$")


def policy_allows_read(policy_text: str, path: str) -> bool:
    """Minimal allow/except evaluator: default deny; an allow rule grants a
    path only when it matches `paths:` and no `except:` pattern."""
    document = yaml.safe_load(policy_text) or {}
    for actor in (document.get("actors") or {}).values():
        for rule in (actor or {}).get("allow", []) or []:
            if rule.get("action") not in (None, "read"):
                continue
            if not any(
                _glob_to_regex(p).match(path) for p in rule.get("paths", []) or []
            ):
                continue
            if any(
                _glob_to_regex(p).match(path) for p in rule.get("except", []) or []
            ):
                continue
            return True
    return False


def test_sensitive_reads_are_denied_before_and_after_the_cutover(tmp_path: Path):
    vault = cutover_vault(tmp_path / "vault")
    policy_path = vault / "_system" / "scripts" / "action-policy.yaml"

    before = policy_path.read_text(encoding="utf-8")
    assert policy_allows_read(before, "ab/00-inbox/note.md")
    assert not policy_allows_read(before, "ab/.sensitive/secret.md")

    promoted(vault)

    after = policy_path.read_text(encoding="utf-8")
    assert policy_allows_read(after, "ab-entity/00-inbox/note.md")
    assert not policy_allows_read(after, "ab-entity/.sensitive/secret.md")


def test_former_slugs_is_written_only_on_entity_and_product_keys(tmp_path: Path):
    vault = cutover_vault(tmp_path / "vault")
    promoted(vault)

    system = vault / "_system"
    entities = (system / "entities.yaml").read_text(encoding="utf-8")
    products = (system / "products.yaml").read_text(encoding="utf-8")
    members = (system / "members.yaml").read_text(encoding="utf-8")
    workspaces = (system / "workspaces.yaml").read_text(encoding="utf-8")

    assert "former_slugs: [ab]" in entities
    assert "former_slugs: [q7]" in products
    assert "former_slugs" not in members
    assert "former_slugs" not in workspaces
    assert "id: m7-member" in members
    assert "id: w7-workspace" in workspaces


def test_existing_former_slugs_are_preserved_and_no_duplicate_key_is_created(
    tmp_path: Path,
):
    vault = cutover_vault(tmp_path / "vault")
    entities = vault / "_system" / "entities.yaml"
    entities.write_text(
        "entities:\n  ab:\n    former_slugs: [older]\n    label: A\n",
        encoding="utf-8",
    )
    commit_in(vault, "seed provenance")

    promoted(vault)

    text = entities.read_text(encoding="utf-8")
    assert text.count("former_slugs:") == 1
    assert "former_slugs: [older, ab]" in text


def test_a_product_kind_workspace_id_takes_the_workspace_suffix(tmp_path: Path):
    """One literal on two axes: each field takes its own suffix.

    The dangerous shape is a product workspace whose `id` equals the product
    slug. A fixture where the two differ cannot detect a product pass that also
    rewrites `id:`, because a product-axis rewrite of `q7` never matches
    `id: w7`.
    """
    vault = cutover_vault(tmp_path / "vault")
    workspaces_path = vault / "_system" / "workspaces.yaml"
    workspaces_path.write_text(
        "workspaces:\n"
        "  - {id: q7, entity: ab, product: q7, member: m7, kind: product}\n",
        encoding="utf-8",
    )
    commit_in(vault, "workspace id equals the product slug")

    mappings = (
        Mapping(axis="entity", old="ab", new="ab-entity"),
        Mapping(axis="product", old="q7", new="q7-product"),
        Mapping(axis="member", old="m7", new="m7-member"),
        Mapping(axis="workspace", old="q7", new="q7-workspace"),
    )
    # Dispositions come from the actual pre-scan objects, carrying their own
    # context digests, so the approved set is exactly what the scanner saw.
    dispositions = tuple(
        Disposition(
            path=item.path,
            axis=item.axis,
            old=item.old,
            ordinal=item.ordinal,
            context_sha256=item.context_sha256,
            line=item.line,
            kind="incidental",
            typed_location="",
        )
        for item in advisory_occurrences(vault, mappings)
    )
    manifest = ApprovalManifest(
        source_head=git_head(vault),
        mappings=mappings,
        databases=load_manifest(approved(vault)[0]).databases,
        dispositions=dispositions,
    )
    raw = canonical_bytes(manifest)
    record = ApprovalRecord(
        manifest_sha256=manifest_digest(manifest),
        executor_commit=approved(vault)[1].executor_commit,
        approved_by="owner",
    )

    built = build_cutover(vault, raw, record)
    promote(vault, built.commit, manifest.source_head, git_status_bytes(vault), ["ab"])

    workspaces = workspaces_path.read_text(encoding="utf-8")
    assert "id: q7-workspace" in workspaces, "product claimed workspace id"
    assert "product: q7-product" in workspaces
    assert "id: q7-product" not in workspaces, "product claimed workspace id"


def test_run_vault_validators_leaves_no_bytecode(tmp_path: Path):
    """The validators read the tree; they must not add compiled bytecode.

    `git add -A` stages whatever is present, so a `.pyc` written during
    validation would enter the single reviewed cutover commit — and would also
    make two builds of one source HEAD produce different trees.
    """
    from app.cutover_build import run_vault_validators

    vault = cutover_vault(tmp_path / "vault")

    run_vault_validators(vault)

    assert list(vault.rglob("*.pyc")) == []
    assert list(vault.rglob("__pycache__")) == []


def test_a_validator_that_writes_an_ignored_file_refuses_the_build(tmp_path: Path):
    """An ignored file is still detritus. `--ignored` coverage is what makes
    this detectable; without it a private ignore rule would hide it."""
    vault = cutover_vault(tmp_path / "vault")
    (vault / ".gitignore").write_text("scratch.tmp\n", encoding="utf-8")
    commit_in(vault, "ignore scratch")
    raw, record = approved(vault)

    def writes_ignored_file(root: Path) -> None:
        (root / "scratch.tmp").write_text("left behind\n", encoding="utf-8")

    with pytest.raises(CutoverError, match="validator changed the isolated tree"):
        build_cutover(vault, raw, record, validator=writes_ignored_file)

    assert git_is_clean(vault)


import ast


def test_no_destructive_git_command_appears_in_the_cutover_modules():
    modules = [
        "app/cutover.py",
        "app/cutover_build.py",
        "app/cutover_db.py",
        "app/cutover_inventory.py",
        "app/cutover_locations.py",
        "app/identifiers.py",
        "app/cutover_manifest.py",
    ]
    for name in modules:
        source = Path(name).read_text(encoding="utf-8")
        tree = ast.parse(source, filename=name)
        for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
            literals = {
                item.value
                for item in ast.walk(call)
                if isinstance(item, ast.Constant) and isinstance(item.value, str)
            }
            assert not {"reset", "--hard"} <= literals, (
                f"{name} can invoke git reset --hard"
            )
            assert not {"clean", "-fd"} <= literals, (
                f"{name} can invoke git clean -fd"
            )


def test_markdown_under_skip_dirs_is_never_rewritten(tmp_path: Path):
    """The writer must not reach further than the gates can verify.

    `.obsidian/` and `.trash/` are outside the cutover entirely: no gate
    inspects them and no advisory report shows them, so a rewrite there would
    be bytes nobody reviewed and nothing could verify.
    """
    vault = cutover_vault(tmp_path / "vault")
    for skipped in (".obsidian", ".trash"):
        directory = vault / skipped
        directory.mkdir()
        (directory / "template.md").write_text(
            "---\nentity: ab\nproduct: q7\n---\n\nthe ab word\n", encoding="utf-8"
        )
    commit_in(vault, "tracked content inside skip dirs")
    before = {
        skipped: (vault / skipped / "template.md").read_bytes()
        for skipped in (".obsidian", ".trash")
    }

    promoted(vault)

    for skipped, original in before.items():
        assert (vault / skipped / "template.md").read_bytes() == original, (
            f"{skipped} content was rewritten outside every gate"
        )


@pytest.fixture
def stranding_rmtree(monkeypatch):
    """Patch `shutil.rmtree` to fail, recording what it deliberately strands.

    Teardown always runs — including when an assertion in the test body
    fails — so a regression about leaked temporary trees can never leak one
    itself. The closing assertion makes that cleanup provable rather than
    assumed.
    """
    import app.cutover_build as cutover_build

    stranded: list[Path] = []

    def refuse_rmtree(path, *args, **kwargs):
        stranded.append(Path(path))
        raise OSError("device busy")

    real_rmtree = cutover_build.shutil.rmtree
    monkeypatch.setattr(cutover_build.shutil, "rmtree", refuse_rmtree)
    yield stranded
    # Use the captured original rather than relying on teardown ordering to
    # have restored the patch.
    for leaked in stranded:
        real_rmtree(leaked, ignore_errors=True)
    assert not [leaked for leaked in stranded if leaked.exists()], (
        "the stranding regression leaked a temporary tree"
    )


def test_cleanup_failure_after_a_successful_body_is_surfaced(
    tmp_path: Path, stranding_rmtree
):
    """A swallowed cleanup failure can leave a full tracked-vault copy behind.

    On a machine holding Grey Matter that is a silent copy of vault content in
    temporary storage, so it must refuse loudly rather than pass quietly.
    """
    vault = git_vault(tmp_path / "vault", {"a.md": "x\n"})

    with pytest.raises(CutoverError, match="temporary cutover worktree"):
        with isolated_worktree(vault, git_head(vault)):
            pass

    assert stranding_rmtree, "the cleanup path was never reached"


def test_cleanup_failure_preserves_a_failing_body(tmp_path: Path, stranding_rmtree):
    """Both facts survive: the build's own failure and the cleanup failure."""
    vault = git_vault(tmp_path / "vault", {"a.md": "x\n"})

    with pytest.raises(CutoverCleanupError) as caught:
        with isolated_worktree(vault, git_head(vault)):
            raise RuntimeError("body failed")

    # Both facts must appear in the message the CLI actually prints.
    message = str(caught.value)
    assert "build failed" in message and "RuntimeError" in message
    assert "body failed" not in message, "body exception text is not public"
    assert "cleanup also failed" in message
    assert "copy of vault content" in message
    assert isinstance(caught.value.body_error, RuntimeError)
    assert isinstance(caught.value.__cause__, RuntimeError)


def test_cleanup_failure_never_prints_the_absolute_temporary_path(
    tmp_path: Path, stranding_rmtree
):
    vault = git_vault(tmp_path / "vault", {"a.md": "x\n"})

    with pytest.raises(CutoverError) as caught:
        with isolated_worktree(vault, git_head(vault)):
            pass

    message = str(caught.value)
    assert "/var/" not in message and "/private/" not in message
    assert "oneos-cutover-" not in message


def test_cleanup_message_never_carries_body_exception_text(
    tmp_path: Path, stranding_rmtree
):
    """Only the body exception's *type* may reach public CLI output.

    `str(exc)` on an arbitrary failure can carry vault paths — a git error
    naming a file, an OSError with a filename. The CLI prints this message, so
    the text is preserved structurally instead, via `.body_error` and the
    exception chain.
    """
    vault = git_vault(tmp_path / "vault", {"a.md": "x\n"})
    # A real absolute path, built at runtime. Writing a private-looking literal
    # here would itself trip the publication audit this test exists to defend.
    secret = str(tmp_path / "vault" / "entity" / "00-inbox" / "secret.md")

    with pytest.raises(CutoverCleanupError) as caught:
        with isolated_worktree(vault, git_head(vault)):
            raise RuntimeError(f"could not read {secret}")

    message = str(caught.value)
    assert secret not in message, "body exception text reached public output"
    assert str(tmp_path) not in message
    assert "RuntimeError" in message, "the body failure type must still be named"
    # Preserved structurally for diagnosis, not for printing.
    assert secret in str(caught.value.body_error)
    assert secret in str(caught.value.__cause__)


def test_stale_worktree_registration_is_surfaced(tmp_path: Path, monkeypatch):
    """The other cleanup failure mode: the temp tree goes but Git's record stays.

    The vault is then left with a worktree registration pointing at a path that
    no longer exists, and nothing tells the operator to prune it.
    """
    import app.cutover_build as cutover_build

    vault = git_vault(tmp_path / "vault", {"a.md": "x\n"})
    real_run = cutover_build.subprocess.run

    def fail_worktree_remove(args, **kwargs):
        if args[:3] == ["git", "worktree", "remove"]:
            return subprocess.CompletedProcess(args, 1, "", "is locked")
        return real_run(args, **kwargs)

    monkeypatch.setattr(cutover_build.subprocess, "run", fail_worktree_remove)

    with pytest.raises(CutoverError, match="worktree prune"):
        with isolated_worktree(vault, git_head(vault)):
            pass


def test_build_refuses_a_manifest_that_omits_a_sub_floor_identifier(tmp_path: Path):
    """A partial cutover defeats the objective and cannot be undone later.

    Both residual gates search only for the mappings the manifest carries, so
    an omitted identifier passes by construction. Stage B would then refuse to
    read the vault — after the one-commit revert window has closed.
    """
    vault = cutover_vault(tmp_path / "vault")
    manifest = ApprovalManifest(
        source_head=git_head(vault),
        mappings=(Mapping(axis="entity", old="ab", new="ab-entity"),),
        databases=(),
        dispositions=(),
    )
    raw = canonical_bytes(manifest)
    record = ApprovalRecord(
        manifest_sha256=manifest_digest(manifest),
        executor_commit=approved(vault)[1].executor_commit,
        approved_by="owner",
    )

    with pytest.raises(CutoverError, match="does not cover every sub-floor"):
        build_cutover(vault, raw, record)


def test_a_validator_that_edits_a_migrated_file_refuses_the_build(tmp_path: Path):
    """`git status` carries no content hash.

    A file the migration already reports as modified produces an identical
    status line after its bytes change again, so a validator could edit
    migrated content and reach the single reviewed commit ungated.
    """
    vault = cutover_vault(tmp_path / "vault")
    raw, record = approved(vault)

    def edits_a_migrated_file(root: Path) -> None:
        entities = root / "_system" / "entities.yaml"
        entities.write_text(
            entities.read_text(encoding="utf-8") + "injected_by_validator: true\n",
            encoding="utf-8",
        )

    with pytest.raises(CutoverError, match="validator changed the isolated tree"):
        build_cutover(vault, raw, record, validator=edits_a_migrated_file)

    assert git_is_clean(vault)


def test_a_validator_that_changes_a_file_mode_refuses_the_build(tmp_path: Path):
    """Mode is part of what gets committed, so it is part of the snapshot."""
    vault = cutover_vault(tmp_path / "vault")
    raw, record = approved(vault)

    def flips_a_mode(root: Path) -> None:
        target = root / "_system" / "entities.yaml"
        target.chmod(target.stat().st_mode | stat.S_IXUSR)

    with pytest.raises(CutoverError, match="validator changed the isolated tree"):
        build_cutover(vault, raw, record, validator=flips_a_mode)


def test_a_validator_that_adds_a_symlink_refuses_without_following_it(tmp_path: Path):
    """The snapshot records link targets and never follows them."""
    vault = cutover_vault(tmp_path / "vault")
    raw, record = approved(vault)

    def adds_a_symlink(root: Path) -> None:
        (root / "pointer.md").symlink_to("/etc/passwd")

    with pytest.raises(CutoverError, match="validator changed the isolated tree"):
        build_cutover(vault, raw, record, validator=adds_a_symlink)


def test_a_read_only_validator_still_passes(tmp_path: Path):
    """The snapshot must not be so strict that an honest validator trips it."""
    vault = cutover_vault(tmp_path / "vault")
    raw, record = approved(vault)
    seen: list[int] = []

    def reads_only(root: Path) -> None:
        seen.append(len(list(root.rglob("*.md"))))

    result = build_cutover(vault, raw, record, validator=reads_only)

    assert seen and result.commit


def test_a_validator_that_retargets_a_symlink_refuses_the_build(tmp_path: Path):
    """Same path, different target: only recording the target catches this.

    Adding a link changes the path set, so a digest that ignored link targets
    would still catch it. Repointing an existing link is the shape that
    isolates the target itself.
    """
    vault = cutover_vault(tmp_path / "vault")
    (vault / "pointer.md").symlink_to("00-inbox/note.md")
    commit_in(vault, "add a tracked symlink")
    raw, record = approved(vault)

    def repoints_the_symlink(root: Path) -> None:
        link = root / "pointer.md"
        link.unlink()
        link.symlink_to("/etc/passwd")

    with pytest.raises(CutoverError, match="validator changed the isolated tree"):
        build_cutover(vault, raw, record, validator=repoints_the_symlink)


def test_snapshot_does_not_follow_a_symlink_raced_into_place(tmp_path: Path):
    """Deterministic stand-in for the TOCTOU window.

    The swap happens after the entry is stat-ed as a regular file and before
    the read, which is the real window. Reading through the planted link would
    pull content from outside the tree into the digest — and read a file the
    cutover has no business touching. `O_NOFOLLOW` plus a post-open identity
    check closes it.
    """
    import app.cutover_build as cutover_build

    outside = tmp_path / "outside.txt"
    outside.write_text("secret\n", encoding="utf-8")
    tree = tmp_path / "tree"
    tree.mkdir()
    target = tree / "raced.md"
    target.write_text("original\n", encoding="utf-8")

    real_lstat = cutover_build.Path.lstat
    swapped: list[bool] = []

    def lstat_then_swap(self):
        info = real_lstat(self)
        if self.name == "raced.md" and not swapped:
            swapped.append(True)
            self.unlink()
            self.symlink_to(outside)
        return info

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(cutover_build.Path, "lstat", lstat_then_swap)
    try:
        with pytest.raises(CutoverError, match="changed while it was being read"):
            cutover_build._tree_state(tree)
    finally:
        monkeypatch.undo()

    assert swapped, "the race was never triggered"


def test_snapshot_records_a_fifo_without_reading_it(tmp_path: Path):
    """A FIFO would block a reader forever; its type and mode still count."""
    import app.cutover_build as cutover_build

    tree = tmp_path / "tree"
    tree.mkdir()
    os.mkfifo(tree / "pipe")

    before = cutover_build._tree_state(tree)
    (tree / "pipe").chmod(0o600)
    after = cutover_build._tree_state(tree)

    assert before != after, "a special file's mode was not recorded"


def test_snapshot_distinguishes_a_fifo_from_a_regular_file(tmp_path: Path):
    import app.cutover_build as cutover_build

    as_fifo = tmp_path / "a"
    as_fifo.mkdir()
    os.mkfifo(as_fifo / "node")

    as_file = tmp_path / "b"
    as_file.mkdir()
    (as_file / "node").write_bytes(b"")

    assert cutover_build._tree_state(as_fifo) != cutover_build._tree_state(as_file)


def test_snapshot_refuses_a_regular_file_swapped_for_another_regular_file(
    tmp_path: Path,
):
    """Isolates the post-open identity check.

    `O_NOFOLLOW` cannot see this: the replacement is an ordinary file, so the
    open succeeds. Only comparing the inode through the descriptor proves the
    bytes came from the entry that was stat-ed.
    """
    import app.cutover_build as cutover_build

    tree = tmp_path / "tree"
    tree.mkdir()
    target = tree / "swapped.md"
    target.write_text("original\n", encoding="utf-8")
    replacement = tmp_path / "replacement.md"
    replacement.write_text("substituted\n", encoding="utf-8")

    real_lstat = cutover_build.Path.lstat
    swapped: list[bool] = []

    def lstat_then_swap(self):
        info = real_lstat(self)
        if self.name == "swapped.md" and not swapped:
            swapped.append(True)
            self.unlink()
            os.link(replacement, self)
        return info

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(cutover_build.Path, "lstat", lstat_then_swap)
    try:
        with pytest.raises(CutoverError, match="changed while it was being read"):
            cutover_build._tree_state(tree)
    finally:
        monkeypatch.undo()

    assert swapped, "the swap was never triggered"


def test_snapshot_refuses_a_file_replaced_by_a_symlink_to_a_directory(tmp_path: Path):
    """Classification must come from the captured lstat, not a fresh query.

    After the entry is stat-ed as a regular file, the path becomes a symlink
    to an external directory. Re-querying with `is_symlink()`/`is_dir()` would
    describe whatever is there *now* — and `is_dir()` traverses the link to do
    it. Dispatching on the captured mode keeps the entry a regular file, so
    the open runs and `O_NOFOLLOW` refuses without ever touching the target.
    """
    import app.cutover_build as cutover_build

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("secret\n", encoding="utf-8")
    tree = tmp_path / "tree"
    tree.mkdir()
    target = tree / "raced.md"
    target.write_text("original\n", encoding="utf-8")

    real_lstat = cutover_build.Path.lstat
    raced: list[bool] = []

    def lstat_then_swap(self):
        info = real_lstat(self)
        if self.name == "raced.md" and not raced:
            raced.append(True)
            self.unlink()
            self.symlink_to(outside, target_is_directory=True)
        return info

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(cutover_build.Path, "lstat", lstat_then_swap)
    try:
        with pytest.raises(CutoverError, match="changed while it was being read"):
            cutover_build._tree_state(tree)
    finally:
        monkeypatch.undo()

    assert raced, "the race was never triggered"


def test_tree_state_never_queries_path_state_after_lstat():
    """Structural: classification comes from the captured mode alone.

    Any `is_file`, `is_dir`, `is_symlink`, `exists`, `stat` or `resolve` call
    re-reads the path and can describe something the snapshot never inspected
    — and several of them traverse a symlink to answer.
    """
    import ast
    import inspect

    import app.cutover_build as cutover_build

    tree = ast.parse(textwrap.dedent(inspect.getsource(cutover_build._tree_state)))
    following = {"is_file", "is_dir", "is_symlink", "exists", "stat", "resolve"}
    offenders = [
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in following
    ]

    assert offenders == [], f"_tree_state re-queries path state: {offenders}"
