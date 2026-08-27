"""cutover_build.py — build the single cutover commit in isolation.

Nothing is written to the live vault. Every edit happens in a temporary
detached linked worktree, which shares the vault's object database, so the
resulting commit is already reachable from the vault and promotion is a
fast-forward rather than a file copy.

A failure before promotion discards the worktree. The live vault was never
touched, so there is nothing to roll back, and no `reset --hard` or
`clean -fd` is ever issued against it.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
import shutil
import subprocess
import tempfile


class CutoverError(Exception):
    pass


class CutoverCommittedError(CutoverError):
    """The promotion committed but confirmation or cleanup failed."""


def git(root: Path, *args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args], cwd=root, check=True, capture_output=True, text=True
        ).stdout
    except subprocess.CalledProcessError as exc:
        raise CutoverError(f"git {args[0]} failed") from exc


@contextmanager
def isolated_worktree(vault: Path, source_head: str) -> Iterator[Path]:
    """A throwaway **detached** worktree at `source_head`, removed on exit.

    Detached on purpose. A named branch would have to be cleaned up, and a
    cleanup that deletes a branch it did not create can destroy an unrelated
    one when creation failed precisely because that branch already existed.
    Creating no ref means there is no ref to delete.
    """
    if git(vault, "rev-parse", "HEAD").strip() != source_head:
        raise CutoverError("vault HEAD is not the recorded source HEAD")
    parent = Path(tempfile.mkdtemp(prefix="oneos-cutover-"))
    scratch = parent / "tree"
    try:
        git(vault, "worktree", "add", "--quiet", "--detach", str(scratch), source_head)
        yield scratch
    finally:
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(scratch)],
            cwd=vault, check=False, capture_output=True,
        )
        shutil.rmtree(parent, ignore_errors=True)


from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
import re
import subprocess
import sys

import yaml

from .console_routing import structured_reader
from .cutover_db import DatabaseChange, apply_database_mappings, database_residuals
from .cutover_inventory import (
    check_collisions,
    existing_identifiers,
    require_clean_entities,
    require_clean_status,
)
from .cutover_locations import (
    advisory_occurrences,
    location_keys,
    rewrite_front_matter_field,
    rewrite_mapping_key,
    rewrite_path_head,
    rewrite_policy_path_heads,
    rewrite_yaml_path_head_field,
    rewrite_yaml_value_field,
    scoped_residuals,
)
from .cutover_manifest import (
    ApprovalManifest,
    ApprovalRecord,
    DatabaseTarget,
    load_manifest,
    verify_manifest,
)
from .identifiers import validate_mapping_pair

#: Entity first: its directory move relocates everything beneath it. Then the
#: value axes, then workspaces. Within an axis, sorted by old identifier.
_AXIS_ORDER = ("entity", "product", "member", "workspace")
_CHECK_V2_ZERO = re.compile(r"(?m)^0 error\(s\), 0 warning\(s\)\s*$")


@dataclass(frozen=True)
class BuildResult:
    source_head: str
    commit: str
    tree: str
    database_changes: tuple[DatabaseChange, ...]


def mappings_in_order(manifest: ApprovalManifest) -> list:
    return sorted(
        manifest.mappings, key=lambda item: (_AXIS_ORDER.index(item.axis), item.old)
    )


@structured_reader(category="admin-record")
def _record_former_slug(text: str, key: str, old: str, indent: int) -> str:
    """Append inert provenance beneath one entity/product mapping key.

    A key may already carry provenance from a previous sanctioned rename. A
    second `former_slugs` key would be duplicate YAML and could change meaning
    by parser, so this function updates the existing list or inserts exactly
    one new child. Member and workspace entries never call it.
    """
    import re

    lines = text.splitlines(keepends=True)
    key_re = re.compile(rf"^(\s*){re.escape(key)}:\s*(?:#.*)?$")
    parent_index = next(
        (index for index, line in enumerate(lines) if key_re.match(line.rstrip("\n"))),
        None,
    )
    if parent_index is None:
        raise CutoverError(f"renamed registry key {key!r} is absent")
    parent_indent = len(key_re.match(lines[parent_index].rstrip("\n")).group(1))
    existing: list[int] = []
    end = len(lines)
    for index in range(parent_index + 1, len(lines)):
        stripped = lines[index].strip()
        if stripped:
            current_indent = len(lines[index]) - len(lines[index].lstrip(" "))
            if current_indent <= parent_indent:
                end = index
                break
        if re.match(r"^\s*former_slugs:\s*", lines[index]):
            existing.append(index)
    if len(existing) > 1:
        raise CutoverError(f"registry key {key!r} has duplicate former_slugs")
    if existing:
        index = existing[0]
        try:
            document = yaml.safe_load(lines[index].strip()) or {}
            values = document["former_slugs"]
        except (KeyError, TypeError, yaml.YAMLError) as exc:
            raise CutoverError(f"former_slugs for {key!r} is malformed") from exc
        if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
            raise CutoverError(f"former_slugs for {key!r} must be a string list")
        if old not in values:
            values.append(old)
        prefix = lines[index][: len(lines[index]) - len(lines[index].lstrip(" "))]
        lines[index] = prefix + f"former_slugs: [{', '.join(values)}]\n"
        return "".join(lines)
    lines.insert(parent_index + 1, " " * indent + f"former_slugs: [{old}]\n")
    return "".join(lines)


def _rewrite_proposal(path: Path, old: str, new: str) -> None:
    """Rewrite only the three entity-owned proposal fields.

    Do not parse and re-serialize the record. That would alter quoting, key
    order, comments, or unrelated values outside the closed location table.
    The scoped residual gate subsequently parses the result and refuses any
    unsupported representation that these exact textual writers left behind.
    """
    text = path.read_text(encoding="utf-8")
    rewritten = rewrite_yaml_value_field(text, "entity", old, new)
    for field in ("src", "dst"):
        rewritten = rewrite_yaml_path_head_field(rewritten, field, old, new)
    if rewritten != text:
        path.write_text(rewritten, encoding="utf-8")


def _markdown_files(root: Path):
    for candidate in sorted(root.rglob("*.md")):
        if ".git" in candidate.relative_to(root).parts or candidate.is_symlink():
            continue
        yield candidate


def _apply_entity_mapping(root: Path, old: str, new: str) -> None:
    system = root / "_system"
    for name in ("products.yaml", "members.yaml"):
        path = system / name
        if path.is_file():
            path.write_text(
                rewrite_mapping_key(path.read_text(encoding="utf-8"), old, new, 2),
                encoding="utf-8",
            )
    entities = system / "entities.yaml"
    if entities.is_file():
        text = rewrite_mapping_key(entities.read_text(encoding="utf-8"), old, new, 2)
        entities.write_text(_record_former_slug(text, new, old, 4), encoding="utf-8")
    workspaces = system / "workspaces.yaml"
    if workspaces.is_file():
        text = workspaces.read_text(encoding="utf-8")
        for field in ("entity", "primary_entity"):
            text = rewrite_yaml_value_field(text, field, old, new)
        workspaces.write_text(text, encoding="utf-8")
    policy = system / "scripts" / "action-policy.yaml"
    if policy.is_file():
        policy.write_text(
            rewrite_policy_path_heads(policy.read_text(encoding="utf-8"), old, new),
            encoding="utf-8",
        )
    for markdown in _markdown_files(root):
        text = markdown.read_text(encoding="utf-8")
        rewritten = rewrite_front_matter_field(text, "entity", old, new)
        if rewritten != text:
            markdown.write_text(rewritten, encoding="utf-8")
    for record in sorted(root.rglob("outbox/*.yaml")):
        _rewrite_proposal(record, old, new)
    if (root / old).is_dir():
        git(root, "mv", old, new)


def _apply_value_mapping(root: Path, axis: str, old: str, new: str) -> None:
    system = root / "_system"
    if axis == "product":
        registry = system / "products.yaml"
        if registry.is_file():
            text = rewrite_mapping_key(registry.read_text(encoding="utf-8"), old, new, 4)
            registry.write_text(_record_former_slug(text, new, old, 6), encoding="utf-8")
    else:
        registry = system / "members.yaml"
        if registry.is_file():
            registry.write_text(
                rewrite_yaml_value_field(
                    registry.read_text(encoding="utf-8"), "id", old, new
                ),
                encoding="utf-8",
            )
    for markdown in _markdown_files(root):
        text = markdown.read_text(encoding="utf-8")
        rewritten = rewrite_front_matter_field(text, axis, old, new)
        if rewritten != text:
            markdown.write_text(rewritten, encoding="utf-8")
    workspaces = system / "workspaces.yaml"
    if workspaces.is_file():
        workspaces.write_text(
            rewrite_yaml_value_field(
                workspaces.read_text(encoding="utf-8"), axis, old, new
            ),
            encoding="utf-8",
        )


def _apply_workspace_mapping(root: Path, old: str, new: str) -> None:
    workspaces = root / "_system" / "workspaces.yaml"
    if workspaces.is_file():
        workspaces.write_text(
            rewrite_yaml_value_field(
                workspaces.read_text(encoding="utf-8"), "id", old, new
            ),
            encoding="utf-8",
        )


def _post_move_database_targets(
    manifest: ApprovalManifest,
) -> tuple[DatabaseTarget, ...]:
    """Approved targets relocated by the entity moves, for the final gate only.

    Mutation and the immediate residual query use the approved source-relative
    path verbatim, exactly as the owner read it. Only this last verification
    needs the post-move location, because `_apply_mappings_in_order` has
    renamed the entity directory by then and the approved path no longer
    resolves.

    Derivation is deterministic and closed over the approved entity mappings:
    the first path component is replaced only when it matches an approved
    `old` exactly. Unmatched paths are returned unchanged, never guessed at.
    Each result is reconstructed as a `DatabaseTarget`, so path confinement,
    canonical-form, and axis validation all run again on the derived value.
    """
    entity_mappings = [item for item in manifest.mappings if item.axis == "entity"]
    derived: list[DatabaseTarget] = []
    for target in manifest.databases:
        path = target.path
        for mapping in entity_mappings:
            moved = rewrite_path_head(path, mapping.old, mapping.new)
            if moved != path:
                path = moved
                break
        derived.append(
            DatabaseTarget(
                path=path,
                table=target.table,
                column=target.column,
                axis=target.axis,
            )
        )
    return tuple(derived)


def _apply_mappings_in_order(root: Path, manifest: ApprovalManifest) -> None:
    """Plan each mapping from the tree produced by its predecessor."""
    for mapping in mappings_in_order(manifest):
        if mapping.axis == "entity":
            _apply_entity_mapping(root, mapping.old, mapping.new)
        elif mapping.axis == "workspace":
            _apply_workspace_mapping(root, mapping.old, mapping.new)
        else:
            _apply_value_mapping(root, mapping.axis, mapping.old, mapping.new)


def _occurrence_key(
    item,
    *,
    path: str | None = None,
    include_ordinal: bool = True,
    include_line: bool = False,
):
    key = (
        item.path if path is None else path,
        item.axis,
        item.old,
        item.context_sha256,
    )
    if include_ordinal:
        key += (item.ordinal,)
    return key + ((item.line,) if include_line else ())


def _post_occurrence_key(item, *, path: str | None = None):
    """Carried source identity after path translation, including ordinal."""
    return _occurrence_key(item, path=path, include_ordinal=True)


def _require_dispositions(root: Path, manifest: ApprovalManifest) -> None:
    """Bind the source advisory report exactly before any path moves."""
    valid = location_keys()
    for disposition in manifest.dispositions:
        if disposition.kind == "structural":
            if disposition.typed_location not in valid:
                raise CutoverError(
                    "structural disposition names an unknown typed location"
                )
            raise CutoverError(
                "structural advisory occurrence requires a Stage A location-table "
                "change, fresh inventory, and fresh approval"
            )
    expected = {
        _occurrence_key(item, include_line=True)
        for item in manifest.dispositions
    }
    actual = {
        _occurrence_key(item, include_line=True)
        for item in advisory_occurrences(root, manifest.mappings)
    }
    if actual != expected:
        raise CutoverError(
            "approved dispositions do not exactly match the source advisory report; "
            "re-run from inventory"
        )


def _translated_advisory_path(path: str, manifest: ApprovalManifest) -> str:
    translated = path
    for mapping in mappings_in_order(manifest):
        if mapping.axis == "entity":
            translated = rewrite_path_head(translated, mapping.old, mapping.new)
    return translated


def _require_post_advisory(root: Path, manifest: ApprovalManifest) -> None:
    """Regenerate the report after migration and compare approved evidence.

    Entity moves translate source-relative path heads and provenance insertion
    can shift display line numbers. The post-build identity retains axis, old
    value, source ordinal, and canonical-context digest while translating only
    the path. The span-confined writers do not add, remove, or reorder advisory
    tokens, so a changed ordinal is a refusal rather than a reason to rebind.
    """
    expected = Counter(
        _post_occurrence_key(
            item, path=_translated_advisory_path(item.path, manifest)
        )
        for item in manifest.dispositions
        if item.kind == "incidental"
    )
    actual = Counter(
        _post_occurrence_key(item)
        for item in advisory_occurrences(root, manifest.mappings)
    )
    if actual != expected:
        raise CutoverError(
            "post-migration advisory report changed from the approved incidental set"
        )


def require_executor_revision(
    record: ApprovalRecord, repo_root: Path | None = None
) -> str:
    """Require the exact clean public executor the owner approved."""
    repo = repo_root or Path(__file__).resolve().parents[1]
    status = git(repo, "status", "--porcelain=v2", "--untracked-files=all")
    if status:
        raise CutoverError("executor worktree is dirty; approval binds clean bytes")
    head = git(repo, "rev-parse", "HEAD").strip()
    if head != record.executor_commit:
        raise CutoverError("executor commit does not match the approval record")
    return head


def run_vault_validators(root: Path) -> None:
    """Run both existing read-only vault validators on the migrated tree."""
    scripts = root / "_system" / "scripts"
    check_v2 = scripts / "check_v2.py"
    if not check_v2.is_file():
        raise CutoverError("check_v2.py is absent from the isolated tree")
    # `-B` stops bytecode at its source. Relying on a private `.gitignore` to
    # hide validator detritus would make commit contents depend on a rule this
    # repository cannot see.
    structural = subprocess.run(
        [sys.executable, "-B", str(check_v2), "."],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    combined = structural.stdout + structural.stderr
    if structural.returncode != 0 or not _CHECK_V2_ZERO.search(combined):
        raise CutoverError("check_v2 did not report 0 error(s), 0 warning(s)")
    private_suite = subprocess.run(
        [sys.executable, "-B", "-m", "unittest", "discover"],
        cwd=scripts,
        check=False,
        capture_output=True,
        text=True,
    )
    if private_suite.returncode != 0:
        raise CutoverError("vault script tests failed on the isolated tree")


def _tree_state(root: Path) -> bytes:
    """Opaque worktree state, including ignored paths.

    `--ignored` is mandatory: without it a private ignore rule could hide
    validator detritus, and the comparison would pass while the artifact
    silently gained a file nobody reviewed.
    """
    return subprocess.run(
        ["git", "status", "--porcelain=v2", "--untracked-files=all", "--ignored"],
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout


def build_cutover(
    vault: Path,
    manifest_bytes: bytes,
    record: ApprovalRecord,
    *,
    validator: Callable[[Path], None] = run_vault_validators,
) -> BuildResult:
    """Build and verify the one exact artifact later consumed by promotion."""
    verify_manifest(manifest_bytes, record)
    manifest = load_manifest(manifest_bytes)

    require_clean_status(vault)
    for mapping in manifest.mappings:
        validate_mapping_pair(mapping.axis, mapping.old, mapping.new)
    affected = [item.old for item in manifest.mappings if item.axis == "entity"]
    require_clean_entities(vault, affected)

    with isolated_worktree(vault, manifest.source_head) as scratch:
        check_collisions(manifest.mappings, existing_identifiers(scratch))
        _require_dispositions(scratch, manifest)

        database_changes = apply_database_mappings(
            scratch, manifest.databases, manifest.mappings
        )
        residual = database_residuals(scratch, manifest.databases, manifest.mappings)
        if residual:
            raise CutoverError(
                f"database residual after update: {len(residual)} row set(s)"
            )

        _apply_mappings_in_order(scratch, manifest)

        remaining = scoped_residuals(scratch, manifest.mappings)
        if remaining:
            raise CutoverError(
                f"scoped residual after migration: {remaining[0].location}"
            )
        remaining_rows = database_residuals(
            scratch, _post_move_database_targets(manifest), manifest.mappings
        )
        if remaining_rows:
            raise CutoverError("database residual after migration")

        _require_post_advisory(scratch, manifest)

        # The validators read the tree; they must not add to it. `git add -A`
        # stages everything present, so anything a validator leaves behind
        # would enter the one reviewed cutover commit.
        before_validation = _tree_state(scratch)
        validator(scratch)
        if _tree_state(scratch) != before_validation:
            raise CutoverError(
                "validator changed the isolated tree; the cutover commit must "
                "contain only the reviewed migration"
            )

        git(scratch, "add", "-A")
        git(
            scratch,
            "-c", "user.email=cutover@invalid", "-c", "user.name=cutover",
            "commit", "-q", "-m",
            "cutover: raise registry identifiers to the floor",
        )
        commit = git(scratch, "rev-parse", "HEAD").strip()
        tree = git(scratch, "rev-parse", "HEAD^{tree}").strip()
        return BuildResult(manifest.source_head, commit, tree, database_changes)
