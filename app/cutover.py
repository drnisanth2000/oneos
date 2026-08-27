"""cutover.py — promotion under quiesce and the shared action lock, and the CLI.

`git merge --ff-only` advances the ref atomically, but updating the working
tree is many creates, deletes, and renames. A process reading the vault during
that window can observe a half-migrated tree, so every writer must be stopped
first. The shared action lock is taken as well, for the cooperative OneOS
writers it does govern — an addition to the quiesce, never a substitute, since
Hermes, parsers, and adapters need not take it.
"""
from __future__ import annotations

from pathlib import Path
import subprocess

from .console_routing import structured_reader
from .cutover_build import (
    CutoverCommittedError,
    CutoverError,
    build_cutover,
    git,
)
from .cutover_inventory import require_clean_entities
from .git_transaction import (
    ActionLockCleanupFailure,
    GitTransactionFailure,
    VaultBusyError,
    action_lock,
)


def _status_bytes(vault: Path) -> bytes:
    return subprocess.run(
        ["git", "status", "--porcelain=v2", "--untracked-files=all"],
        cwd=vault, check=True, capture_output=True,
    ).stdout


def promote(
    vault: Path,
    built_commit: str,
    source_head: str,
    expected_status: bytes,
    affected_entities: list[str],
) -> str:
    """Fast-forward the live vault to the commit built in isolation.

    The caller must already have stopped OneOS, Hermes, and every parser and
    adapter. This function takes the shared action lock, repeats every
    precheck, and only then moves the ref.
    """
    committed = False
    try:
        with action_lock(vault):
            if git(vault, "rev-parse", "HEAD").strip() != source_head:
                raise CutoverError(
                    "live HEAD moved since the build; re-run from inventory"
                )
            if _status_bytes(vault) != expected_status:
                raise CutoverError(
                    "live status changed since the build; re-run from inventory"
                )
            # Repeated here because ignored content can appear at any moment,
            # and a linked worktree could never have carried it.
            require_clean_entities(vault, affected_entities)

            completed = subprocess.run(
                ["git", "merge", "--ff-only", built_commit],
                cwd=vault, check=False, capture_output=True, text=True,
            )
            if completed.returncode != 0:
                raise CutoverError(
                    "fast-forward promotion refused; the vault is unchanged"
                )
            committed = True
            try:
                confirmed = subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=vault, check=True, capture_output=True, text=True,
                ).stdout.strip()
                if confirmed != built_commit:
                    raise CutoverCommittedError(
                        "the cutover command succeeded but confirmed HEAD does not "
                        "equal the built commit; do not retry"
                    )
                return confirmed
            except (OSError, subprocess.CalledProcessError) as exc:
                raise CutoverCommittedError(
                    "the cutover committed but its id could not be read; "
                    "do not retry"
                ) from exc
    except ActionLockCleanupFailure as exc:
        if not committed:
            raise CutoverError(
                "shared action lock cleanup failed before the cutover committed"
            ) from exc
        raise CutoverCommittedError(
            "the cutover committed but the action lock could not be released; "
            "do not retry"
        ) from exc
    except VaultBusyError as exc:
        raise CutoverError(
            "vault is busy; another OneOS action is already running"
        ) from exc
    except GitTransactionFailure as exc:
        if committed:
            raise CutoverCommittedError(
                "the cutover committed but the lock layer failed; do not retry"
            ) from exc
        raise CutoverError(
            "shared action lock is unavailable; the cutover was not started"
        ) from exc


import argparse

import yaml

from .cutover_build import (
    isolated_worktree,
    mappings_in_order,
    require_executor_revision,
)
from .cutover_db import (
    database_reference_inventory,
    database_schema_inventory,
)
from .cutover_inventory import (
    check_collisions,
    existing_identifiers,
    proposed_mappings,
    require_clean_entities,
    require_clean_status,
)
from .cutover_locations import advisory_occurrences
from .cutover_manifest import ApprovalRecord, load_manifest, verify_manifest


@structured_reader(category="admin-record")
def _load_approval(path: Path) -> ApprovalRecord:
    document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(document, dict):
        raise CutoverError("approval record must be a mapping")
    try:
        return ApprovalRecord(
            manifest_sha256=document["manifest_sha256"],
            executor_commit=document["executor_commit"],
            approved_by=document["approved_by"],
        )
    except KeyError as exc:
        raise CutoverError("approval record is missing a required field") from exc


def _run_inventory(vault: Path) -> int:
    require_clean_status(vault)
    source_head = git(vault, "rev-parse", "HEAD").strip()
    with isolated_worktree(vault, source_head) as snapshot:
        mappings = proposed_mappings(snapshot)
        check_collisions(mappings, existing_identifiers(snapshot))
        affected = sorted({m.old for m in mappings if m.axis == "entity"})
        occurrences = advisory_occurrences(snapshot, mappings)
        schemas = database_schema_inventory(snapshot)
        references = database_reference_inventory(snapshot, mappings)

    require_clean_entities(vault, affected)
    if git(vault, "rev-parse", "HEAD").strip() != source_head:
        raise CutoverError("live HEAD changed during inventory; discard the result")
    require_clean_status(vault)
    require_clean_entities(vault, affected)

    print(f"source HEAD: {source_head}")
    for mapping in mappings:
        print(f"{mapping.axis}: {mapping.old} -> {mapping.new}")
    for occurrence in occurrences:
        print(
            f"advisory: {occurrence.path}:{occurrence.line} "
            f"({occurrence.axis}:{occurrence.old} #{occurrence.ordinal} "
            f"context={occurrence.context_sha256}) — disposition required"
        )
    for path, tables in schemas.items():
        for table, columns in tables.items():
            print(f"database {path} {table}: {', '.join(columns)}")
    for reference in references:
        print(
            "database candidate (UNPROVEN — owner approval required): "
            f"path={reference.path} table={reference.table} "
            f"column={reference.column} axis={reference.axis} "
            f"old={reference.old} count={reference.count}"
        )
    print("[INVENTORY] read-only; nothing was written")
    return 0


def _run_dry_run(vault: Path, manifest_bytes: bytes, record: ApprovalRecord) -> int:
    """Build the real result in isolation and render it, then discard.

    A dry run that only printed the mapping table would not be a preview: the
    planners read from disk, so the only faithful preview is the tree the apply
    would actually produce.
    """
    verify_manifest(manifest_bytes, record)
    manifest = load_manifest(manifest_bytes)
    result = build_cutover(vault, manifest_bytes, record)
    print(git(vault, "diff", f"{manifest.source_head}..{result.commit}"))
    for change in result.database_changes:
        print(
            "database rows changed: "
            f"path={change.path} table={change.table} column={change.column} "
            f"axis={change.axis} old={change.old} new={change.new} "
            f"count={change.count}"
        )
    print(
        "database rows changed (total): "
        f"{sum(item.count for item in result.database_changes)}"
    )
    for mapping in mappings_in_order(manifest):
        print(f"{mapping.axis}: {mapping.old} -> {mapping.new}")
    print("\n[DRY RUN] re-run with apply to execute")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="oneos cutover",
        description="Raise registry identifiers to the five-character floor.",
    )
    parser.add_argument("command", choices=("inventory", "dry-run", "apply"))
    parser.add_argument("--vault-root", default=".")
    parser.add_argument("--manifest")
    parser.add_argument("--approval")
    parser.add_argument(
        "--i-have-quiesced-all-writers",
        action="store_true",
        help=(
            "confirm OneOS, Hermes, and every parser and adapter are stopped; "
            "the working-tree update is not atomic and the action lock does "
            "not govern them"
        ),
    )
    args = parser.parse_args(argv)
    vault = Path(args.vault_root).expanduser().resolve()

    try:
        if args.command == "inventory":
            return _run_inventory(vault)

        if not args.manifest or not args.approval:
            print("[ABORTED] --manifest and --approval are required")
            return 1
        manifest_bytes = Path(args.manifest).read_bytes()
        record = _load_approval(Path(args.approval))
        require_executor_revision(record)

        if args.command == "dry-run":
            return _run_dry_run(vault, manifest_bytes, record)

        if not args.i_have_quiesced_all_writers:
            print(
                "[ABORTED] refusing to promote without "
                "--i-have-quiesced-all-writers: stop OneOS, Hermes, and every "
                "parser and adapter first"
            )
            return 1

        manifest = load_manifest(manifest_bytes)
        source_head = git(vault, "rev-parse", "HEAD").strip()
        expected_status = _status_bytes(vault)
        affected = [m.old for m in manifest.mappings if m.axis == "entity"]
        result = build_cutover(vault, manifest_bytes, record)
        promoted_id = promote(
            vault, result.commit, source_head, expected_status, affected
        )
        print(f"[DONE] cutover promoted as {promoted_id}")
        print(
            "[ROLLBACK WINDOW] keep writers stopped while verifying. Plain git "
            "revert is safe only before writers restart; later rollback needs a "
            "separately reviewed database recovery migration."
        )
        return 0
    except CutoverCommittedError as exc:
        print(f"[COMMITTED] {exc}")
        return 2
    except Exception as exc:  # noqa: BLE001 — CLI boundary
        print(f"[ABORTED] {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
