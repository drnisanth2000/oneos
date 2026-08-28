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
from pathlib import Path, PurePosixPath
import hashlib
import os
import shutil
import stat
import subprocess
import tempfile


class CutoverError(Exception):
    pass


class CutoverCleanupError(CutoverError):
    """Cleanup of the isolated build failed, possibly alongside the build.

    Composed rather than chained: the CLI prints `str(exc)`, so a fact that
    lives only in `__notes__` or `__cause__` never reaches the operator. A
    stranded temporary tree is a copy of tracked vault content, so it has to
    appear in the message the operator actually sees.

    Changing the propagating type is safe here — every isolated-build failure
    is a pre-promotion administrative refusal, and `CutoverError` is what
    upstream dispatches on.
    """

    def __init__(self, detail: str, body_error: BaseException | None = None):
        self.body_error = body_error
        self.detail = detail
        if body_error is None:
            super().__init__(f"temporary cutover cleanup failed: {detail}")
        else:
            # Only the type name is public. `str(body_error)` is arbitrary
            # text that can carry vault paths — a git error naming a file, an
            # OSError with a filename — and the CLI prints this message. The
            # text is preserved on `.body_error` and through the chain.
            super().__init__(
                f"build failed ({type(body_error).__name__}); "
                f"temporary cutover cleanup also failed: {detail}"
            )


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
        # Cleanup failure is never swallowed. A leaked temporary tree is a full
        # copy of tracked vault content sitting in temporary storage on a
        # machine that holds Grey Matter, and a stale worktree registration
        # leaves the vault's Git metadata pointing at a path that is gone.
        # The message stays generic: the temporary path is not printed.
        failures: list[str] = []
        removal = subprocess.run(
            ["git", "worktree", "remove", "--force", str(scratch)],
            cwd=vault, check=False, capture_output=True, text=True,
        )
        if removal.returncode != 0:
            failures.append(
                "a stale worktree registration remains in the vault; "
                "run `git worktree prune`"
            )
        try:
            shutil.rmtree(parent)
        except OSError:
            failures.append(
                "the temporary cutover worktree could not be removed and may "
                "still hold a copy of vault content; remove it manually"
            )
        if failures:
            # Compose both facts into one message. The operator sees `str(exc)`
            # and nothing else, so the stranded copy must be stated there.
            raise CutoverCleanupError(
                "; ".join(failures), sys.exc_info()[1]
            ) from sys.exc_info()[1]


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
    proposed_mappings,
    require_clean_entities,
    require_clean_status,
)
from .cutover_locations import (
    SKIP_DIRS,
    advisory_occurrences,
    location_keys,
    rewrite_front_matter_field,
    rewrite_conventions_member_references,
    rewrite_mapping_key,
    rewrite_members_comment_references,
    rewrite_registry_entry_scalar,
    rewrite_root_scalar,
    rewrite_path_head,
    rewrite_policy_path_heads,
    rewrite_system_entity_references,
    rewrite_system_product_references,
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
def _existing_former_slugs(line: str) -> list:
    """Read provenance already recorded on a registry key."""
    return (yaml.safe_load(line) or {})["former_slugs"]


def _render_former_slugs(values: list[str], indent: int) -> str:
    """Serialise provenance so every value stays a string.

    Interpolating raw text writes `former_slugs: [no]`, which parses back as
    the boolean `False` rather than the identifier it records.
    """
    rendered = yaml.safe_dump(
        values, default_flow_style=True, width=10**6
    ).strip()
    return " " * indent + f"former_slugs: {rendered}\n"


def _record_former_slug(text: str, key: str, old: str, indent: int) -> str:
    """Append inert provenance beneath every matching registry key.

    A key may already carry provenance from a previous sanctioned rename. A
    second `former_slugs` key would be duplicate YAML and could change meaning
    by parser, so this updates an existing list or inserts exactly one child.

    `products.yaml` nests product keys per entity, so one slug can occur under
    several entities and each occurrence needs its own provenance. Member and
    workspace entries never call this.
    """
    import re

    lines = text.splitlines(keepends=True)
    # The registry key sits at a known depth: `former_slugs` is written at
    # `indent`, so its parent is at `indent - 2`. Matching at any depth found
    # a same-named key deeper in the tree and inserted provenance under the
    # wrong parent, producing a document that no longer parses.
    parent_indent_required = max(indent - 2, 0)
    key_re = re.compile(
        rf"^( {{{parent_indent_required}}})(?! ){re.escape(key)}:\s*(?:#.*)?$"
    )
    parents = [
        index for index, line in enumerate(lines)
        if key_re.match(line.rstrip("\n"))
    ]
    if not parents:
        raise CutoverError(f"renamed registry key {key!r} is absent")

    # Right-to-left, so an insertion never shifts an earlier parent's index.
    for parent_index in sorted(parents, reverse=True):
        parent_indent = len(key_re.match(lines[parent_index].rstrip("\n")).group(1))
        existing: list[int] = []
        for index in range(parent_index + 1, len(lines)):
            stripped = lines[index].strip()
            if stripped:
                current_indent = len(lines[index]) - len(lines[index].lstrip(" "))
                if current_indent <= parent_indent:
                    break
            if re.match(
                rf"^ {{{indent}}}(?! )former_slugs:\s*", lines[index]
            ):
                existing.append(index)
        if len(existing) > 1:
            raise CutoverError(f"registry key {key!r} has duplicate former_slugs")
        if existing:
            index = existing[0]
            try:
                values = _existing_former_slugs(lines[index].strip())
            except (KeyError, TypeError, yaml.YAMLError) as exc:
                raise CutoverError(f"former_slugs for {key!r} is malformed") from exc
            if not isinstance(values, list) or not all(
                isinstance(value, str) for value in values
            ):
                raise CutoverError(f"former_slugs for {key!r} must be a string list")
            if old not in values:
                values.append(old)
            prefix_length = len(lines[index]) - len(lines[index].lstrip(" "))
            lines[index] = _render_former_slugs(values, prefix_length)
        else:
            lines.insert(parent_index + 1, _render_former_slugs([old], indent))
    return "".join(lines)


def _rewrite_proposal(path: Path, old: str, new: str) -> None:
    """Rewrite only the three entity-owned proposal fields.

    Do not parse and re-serialize the record. That would alter quoting, key
    order, comments, or unrelated values outside the closed location table.
    The scoped residual gate subsequently parses the result and refuses any
    unsupported representation that these exact textual writers left behind.
    """
    text = path.read_text(encoding="utf-8")
    rewritten = rewrite_root_scalar(text, "entity", old, new)
    for field in ("src", "dst"):
        rewritten = rewrite_root_scalar(rewritten, field, old, new, path_head=True)
    if rewritten != text:
        path.write_text(rewritten, encoding="utf-8")


def _markdown_files(root: Path):
    """Exactly the scope the advisory scan and the residual gate inspect.

    A writer that reaches further than either gate edits bytes nobody
    reviewed and nothing can verify. `SKIP_DIRS` content is outside the
    cutover entirely — not rewritten, and deliberately not advisory either.
    """
    for candidate in sorted(root.rglob("*.md")):
        relative = candidate.relative_to(root)
        if any(part in SKIP_DIRS for part in relative.parts) or candidate.is_symlink():
            continue
        yield candidate


def _apply_entity_mapping(root: Path, old: str, new: str) -> None:
    system = root / "_system"
    registered_products = frozenset(existing_identifiers(root)["product"])
    docs = system / "docs"
    if docs.is_dir() and not docs.is_symlink():
        for document in sorted(docs.rglob("*.md")):
            if document.is_symlink() or not document.is_file():
                continue
            text = document.read_text(encoding="utf-8")
            rewritten = rewrite_system_entity_references(
                text, registered_products, old, new
            )
            if rewritten != text:
                document.write_text(rewritten, encoding="utf-8")
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
            text = rewrite_registry_entry_scalar(
                text, "workspaces", field, old, new
            )
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
            text = rewrite_registry_entry_scalar(
                registry.read_text(encoding="utf-8"), "members", "id", old, new
            )
            registry.write_text(
                rewrite_members_comment_references(text, old, new),
                encoding="utf-8",
            )
        for conventions in sorted(system.glob("conventions*.md")):
            if conventions.is_symlink() or not conventions.is_file():
                continue
            text = conventions.read_text(encoding="utf-8")
            rewritten = rewrite_conventions_member_references(text, old, new)
            if rewritten != text:
                conventions.write_text(rewritten, encoding="utf-8")
    if axis == "product":
        registered_entities = frozenset(existing_identifiers(root)["entity"])
        docs = system / "docs"
        if docs.is_dir() and not docs.is_symlink():
            for document in sorted(docs.rglob("*.md")):
                if document.is_symlink() or not document.is_file():
                    continue
                text = document.read_text(encoding="utf-8")
                rewritten = rewrite_system_product_references(
                    text, registered_entities, old, new
                )
                if rewritten != text:
                    document.write_text(rewritten, encoding="utf-8")
    for markdown in _markdown_files(root):
        text = markdown.read_text(encoding="utf-8")
        rewritten = rewrite_front_matter_field(text, axis, old, new)
        if rewritten != text:
            markdown.write_text(rewritten, encoding="utf-8")
    workspaces = system / "workspaces.yaml"
    if workspaces.is_file():
        workspaces.write_text(
            rewrite_registry_entry_scalar(
                workspaces.read_text(encoding="utf-8"), "workspaces", axis, old, new
            ),
            encoding="utf-8",
        )


def _apply_workspace_mapping(root: Path, old: str, new: str) -> None:
    workspaces = root / "_system" / "workspaces.yaml"
    if workspaces.is_file():
        workspaces.write_text(
            rewrite_registry_entry_scalar(
                workspaces.read_text(encoding="utf-8"), "workspaces", "id", old, new
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


def _tree_state(root: Path) -> str:
    """An opaque digest of the tree's own contents.

    `git status` carries no content hash, so a file the migration already
    reports as modified yields an identical status line after its bytes change
    again — a validator could edit migrated content and reach the single
    reviewed commit ungated.

    Every entry contributes its path, type, mode and content: a file's bytes,
    a symlink's target text. Symlinks are never followed, so a link planted
    during validation is recorded as a link rather than read through. Only the
    repository's own `.git` metadata is excluded, because Git rewrites it as a
    matter of course; nothing else is trusted to be uninteresting.

    The digest is opaque by construction — no path or content ever reaches a
    diagnostic, only the fact that something changed.
    """
    digest = hashlib.sha256()
    for entry in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative = entry.relative_to(root)
        if relative.parts and relative.parts[0] == ".git":
            continue
        digest.update(relative.as_posix().encode("utf-8", "surrogateescape"))
        # Classification comes from this one captured `lstat` and nothing
        # else. `is_dir()` traverses a symlink to answer, and every such call
        # re-reads the path, so it can describe something that arrived after
        # the entry was inspected.
        info = entry.lstat()
        if stat.S_ISLNK(info.st_mode):
            digest.update(b"\x00link\x00")
            digest.update(os.readlink(entry).encode("utf-8", "surrogateescape"))
            digest.update(f"\x00{info.st_mode:o}\x00".encode("ascii"))
        elif stat.S_ISDIR(info.st_mode):
            digest.update(b"\x00dir\x00")
            digest.update(f"\x00{info.st_mode:o}\x00".encode("ascii"))
        elif stat.S_ISREG(info.st_mode):
            digest.update(b"\x00file\x00")
            digest.update(_regular_file_contents(entry, info))
            digest.update(f"\x00{info.st_mode:o}\x00".encode("ascii"))
        else:
            # A FIFO, socket or device is never read — opening one can block
            # forever. Its type and mode still change the artifact, so they
            # are recorded instead.
            digest.update(f"\x00special:{stat.S_IFMT(info.st_mode):o}\x00".encode("ascii"))
            digest.update(f"\x00{info.st_mode:o}\x00".encode("ascii"))
    return digest.hexdigest()


def _regular_file_contents(entry: Path, expected: os.stat_result) -> bytes:
    """Read a regular file through a descriptor that cannot be redirected.

    Between deciding a path is a regular file and reading it, the path can
    become a symlink. `O_NOFOLLOW` refuses to open one, and re-checking the
    type and inode *through the descriptor* proves the bytes came from the
    file that was inspected rather than something swapped in behind it.
    """
    try:
        handle = os.open(entry, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise CutoverError(
            "a tree entry changed while it was being read; the snapshot "
            "cannot describe a tree that moved beneath it"
        ) from exc
    try:
        actual = os.fstat(handle)
        if not stat.S_ISREG(actual.st_mode) or (
            actual.st_ino,
            actual.st_dev,
        ) != (expected.st_ino, expected.st_dev):
            raise CutoverError(
                "a tree entry changed while it was being read; the snapshot "
                "cannot describe a tree that moved beneath it"
            )
        chunks: list[bytes] = []
        while True:
            block = os.read(handle, 1 << 20)
            if not block:
                return b"".join(chunks)
            chunks.append(block)
    finally:
        os.close(handle)


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
        # The isolated worktree *is* the manifest's source HEAD, so this is a
        # closed comparison rather than a recomputation the design forbids.
        # Without it both residual gates pass by construction on a partial
        # manifest — they only search for the mappings it carries — and the
        # omission is discoverable only after the revert window has closed.
        expected = set(proposed_mappings(scratch))
        if set(manifest.mappings) != expected:
            raise CutoverError(
                "the approved mapping does not cover every sub-floor identifier "
                "at the source HEAD; re-run from inventory"
            )
        registered = existing_identifiers(scratch)["entity"]
        for entity in sorted(registered):
            if (scratch / entity).is_symlink():
                raise CutoverError(
                    "a registered entity root is a symlink; the cutover will "
                    "not migrate through a redirected location"
                )
        for target in manifest.databases:
            owner = PurePosixPath(target.path).parts[0]
            if owner not in registered:
                raise CutoverError(
                    "an approved database is not under a registered entity; "
                    "re-run from inventory"
                )
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
