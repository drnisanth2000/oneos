from contextlib import contextmanager
from pathlib import Path

import pytest
import yaml

import app.cutover as cutover
from app.cutover import main
from tests.conftest import git_head, git_is_clean, git_vault
from tests.test_cutover_build import approved, commit_in, cutover_vault


@pytest.fixture(autouse=True)
def approved_synthetic_executor(monkeypatch):
    monkeypatch.setattr(cutover, "require_executor_revision", lambda _record: None)


def write_artifacts(tmp_path: Path, raw: bytes, record) -> tuple[Path, Path]:
    manifest_path = tmp_path / "manifest.json"
    record_path = tmp_path / "record.yaml"
    manifest_path.write_bytes(raw)
    record_path.write_text(
        yaml.safe_dump(
            {
                "manifest_sha256": record.manifest_sha256,
                "executor_commit": record.executor_commit,
                "approved_by": "owner",
            }
        ),
        encoding="utf-8",
    )
    return manifest_path, record_path


def test_inventory_reports_identifiers_advisory_and_schema(tmp_path: Path, capsys):
    vault = cutover_vault(tmp_path / "vault")

    code = main(["inventory", "--vault-root", str(vault)])
    out = capsys.readouterr().out

    assert code == 0
    assert f"source HEAD: {git_head(vault)}" in out
    assert "entity: ab -> ab-entity" in out
    assert "workspace: w7 -> w7-workspace" in out
    assert "advisory" in out
    assert "ab/books.db ledger" in out
    assert "path=ab/books.db table=ledger column=product axis=product" in out
    assert "count=1" in out
    assert "UNPROVEN" in out
    assert git_is_clean(vault)


def test_inventory_writes_nothing(tmp_path: Path):
    vault = cutover_vault(tmp_path / "vault")
    head = git_head(vault)

    main(["inventory", "--vault-root", str(vault)])

    assert git_head(vault) == head
    assert git_is_clean(vault)


def test_inventory_reads_tracked_evidence_from_the_captured_head(
    tmp_path: Path, monkeypatch, capsys
):
    vault = cutover_vault(tmp_path / "vault")
    registry = vault / "_system" / "entities.yaml"
    original = registry.read_bytes()
    real = cutover.isolated_worktree

    @contextmanager
    def live_differs_while_snapshot_is_read(root, source_head):
        with real(root, source_head) as snapshot:
            registry.write_text(
                "entities:\n  zz:\n    label: Later\n", encoding="utf-8"
            )
            try:
                yield snapshot
            finally:
                registry.write_bytes(original)

    monkeypatch.setattr(
        cutover, "isolated_worktree", live_differs_while_snapshot_is_read
    )

    assert main(["inventory", "--vault-root", str(vault)]) == 0
    out = capsys.readouterr().out
    assert "entity: ab -> ab-entity" in out
    assert "entity: zz" not in out


def test_inventory_discards_results_when_live_status_changes_before_return(
    tmp_path: Path, monkeypatch, capsys
):
    vault = cutover_vault(tmp_path / "vault")
    real = cutover.isolated_worktree

    @contextmanager
    def live_changes_before_return(root, source_head):
        with real(root, source_head) as snapshot:
            yield snapshot
        (vault / "changed-after-snapshot.txt").write_text(
            "late\n", encoding="utf-8"
        )

    monkeypatch.setattr(cutover, "isolated_worktree", live_changes_before_return)

    assert main(["inventory", "--vault-root", str(vault)]) == 1
    assert "clean status" in capsys.readouterr().out


def test_inventory_refuses_a_dirty_live_vault(tmp_path: Path, capsys):
    vault = cutover_vault(tmp_path / "vault")
    (vault / "untracked.txt").write_text("not approved\n", encoding="utf-8")

    code = main(["inventory", "--vault-root", str(vault)])

    assert code == 1
    assert "clean status" in capsys.readouterr().out


def test_dry_run_builds_and_shows_the_diff_without_touching_the_vault(
    tmp_path: Path, capsys
):
    vault = cutover_vault(tmp_path / "vault")
    head = git_head(vault)
    manifest_path, record_path = write_artifacts(tmp_path, *approved(vault))

    code = main(
        ["dry-run", "--vault-root", str(vault),
         "--manifest", str(manifest_path), "--approval", str(record_path)]
    )
    out = capsys.readouterr().out

    assert code == 0
    assert "diff --git" in out
    assert "path=ab/books.db table=ledger column=product axis=product" in out
    assert "old=q7 new=q7-product count=1" in out
    assert "DRY RUN" in out
    assert git_head(vault) == head
    assert git_is_clean(vault)


def test_dry_run_and_apply_build_identical_trees_for_one_source_head(
    tmp_path: Path, monkeypatch
):
    vault = cutover_vault(tmp_path / "vault")
    manifest_path, record_path = write_artifacts(tmp_path, *approved(vault))
    results = []
    real = cutover.build_cutover

    def recording(*args, **kwargs):
        result = real(*args, **kwargs)
        results.append(result)
        return result

    monkeypatch.setattr(cutover, "build_cutover", recording)

    assert main(
        ["dry-run", "--vault-root", str(vault),
         "--manifest", str(manifest_path), "--approval", str(record_path)]
    ) == 0
    assert main(
        ["apply", "--vault-root", str(vault),
         "--manifest", str(manifest_path), "--approval", str(record_path),
         "--i-have-quiesced-all-writers"]
    ) == 0

    assert len(results) == 2
    assert results[0].source_head == results[1].source_head
    assert results[0].tree == results[1].tree


def test_dry_run_reports_a_refusal_instead_of_a_diff(tmp_path: Path, capsys):
    vault = cutover_vault(tmp_path / "vault")
    raw, record = approved(vault, dispositions=())
    manifest_path, record_path = write_artifacts(tmp_path, raw, record)

    code = main(
        ["dry-run", "--vault-root", str(vault),
         "--manifest", str(manifest_path), "--approval", str(record_path)]
    )

    assert code == 1
    assert "ABORTED" in capsys.readouterr().out
    assert git_is_clean(vault)


def test_apply_requires_the_quiesce_acknowledgement(tmp_path: Path, capsys):
    vault = cutover_vault(tmp_path / "vault")
    head = git_head(vault)
    manifest_path, record_path = write_artifacts(tmp_path, *approved(vault))

    code = main(
        ["apply", "--vault-root", str(vault),
         "--manifest", str(manifest_path), "--approval", str(record_path)]
    )

    assert code == 1
    assert "quiesced" in capsys.readouterr().out
    assert git_head(vault) == head


@pytest.mark.parametrize("command", ("dry-run", "apply"))
def test_action_commands_refuse_a_different_executor(
    tmp_path: Path, monkeypatch, capsys, command: str
):
    vault = cutover_vault(tmp_path / "vault")
    manifest_path, record_path = write_artifacts(tmp_path, *approved(vault))

    def refuses(_record):
        raise cutover.CutoverError("executor commit does not match")

    monkeypatch.setattr(cutover, "require_executor_revision", refuses)
    argv = [
        command,
        "--vault-root",
        str(vault),
        "--manifest",
        str(manifest_path),
        "--approval",
        str(record_path),
    ]
    if command == "apply":
        argv.append("--i-have-quiesced-all-writers")

    assert main(argv) == 1
    assert "executor commit" in capsys.readouterr().out


def test_apply_promotes_when_acknowledged(tmp_path: Path, capsys):
    vault = cutover_vault(tmp_path / "vault")
    head = git_head(vault)
    manifest_path, record_path = write_artifacts(tmp_path, *approved(vault))

    code = main(
        ["apply", "--vault-root", str(vault),
         "--manifest", str(manifest_path), "--approval", str(record_path),
         "--i-have-quiesced-all-writers"]
    )

    assert code == 0
    out = capsys.readouterr().out
    assert "DONE" in out
    assert "before writers restart" in out
    assert git_head(vault) != head
    assert (vault / "ab-entity").is_dir()


def test_output_failure_after_promotion_keeps_the_committed_classification(
    tmp_path: Path, monkeypatch
):
    """Once `promote()` returns, the ref has moved.

    Reporting must never downgrade that to a refusal: an operator who sees the
    aborted code retries a cutover that already committed, which is exactly the
    confusion the distinct committed outcome exists to prevent.
    """
    import builtins

    import app.cutover as cutover

    vault = cutover_vault(tmp_path / "vault")
    head = git_head(vault)
    manifest_path, record_path = write_artifacts(tmp_path, *approved(vault))

    real_print = builtins.print

    def print_or_break(*args, **kwargs):
        if args and str(args[0]).startswith("[DONE]"):
            raise BrokenPipeError("stdout closed")
        return real_print(*args, **kwargs)

    monkeypatch.setattr(builtins, "print", print_or_break)

    code = main(
        ["apply", "--vault-root", str(vault),
         "--manifest", str(manifest_path), "--approval", str(record_path),
         "--i-have-quiesced-all-writers"]
    )

    monkeypatch.undo()
    assert git_head(vault) != head, "the cutover did commit"
    assert (vault / "ab-entity").is_dir()
    assert code != 1, "a committed cutover was reported with the aborted code"
    assert code == 2


def test_committed_outcome_survives_broken_output(tmp_path: Path, monkeypatch):
    """`CutoverCommittedError` must still return the committed code.

    Its own handler prints. If that print fails, an unguarded handler raises
    out of `main()` and the operator gets a traceback instead of "committed,
    do not retry" — losing the classification at the one moment it matters.
    """
    import builtins

    import app.cutover as cutover

    vault = cutover_vault(tmp_path / "vault")
    manifest_path, record_path = write_artifacts(tmp_path, *approved(vault))

    def committed(*args, **kwargs):
        raise cutover.CutoverCommittedError("committed but unconfirmed")

    monkeypatch.setattr(cutover, "promote", committed)

    real_print = builtins.print

    def print_or_break(*args, **kwargs):
        if args and str(args[0]).startswith("[COMMITTED]"):
            raise BrokenPipeError("stdout closed")
        return real_print(*args, **kwargs)

    monkeypatch.setattr(builtins, "print", print_or_break)

    code = main(
        ["apply", "--vault-root", str(vault),
         "--manifest", str(manifest_path), "--approval", str(record_path),
         "--i-have-quiesced-all-writers"]
    )

    monkeypatch.undo()
    assert code == 2, "a committed outcome was lost when its report failed"
