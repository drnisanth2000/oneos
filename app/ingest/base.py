"""base.py — the single ingest write path shared by every adapter.

An adapter's only job is to normalise its source into text + metadata; from
there every source goes through the same code: redact (ADR-008), build the
Envelope (§8.2), write the redacted inbox item. "Same envelope, same PII filter,
no second code path" (spec §10 step 10) is enforced by there being exactly one
of these.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from ..console_routing import structured_reader
from ..scope import Scope
from ..inbox import split_front_matter
from .envelope import Envelope
from .pii import redact

SUMMARY_CHARS = 800


@dataclass(frozen=True)
class IngestResult:
    path: Path
    envelope: Envelope
    created: bool
    commit_oid: str | None


class IngestError(Exception):
    pass


class IngestRepositoryError(IngestError):
    pass


class IngestIdentityConflict(IngestError):
    pass


class IngestPathCollision(IngestError):
    pass


class IngestCommitError(IngestError):
    pass


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "item"


def _scalar(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def render_note(env: Envelope, entity: str) -> str:
    today = date.today().isoformat()
    lines = [
        "---",
        "type: inbox-item",
        f"title: {env.title}",
        f"entity: {entity}",
        "product: null",
        "status: active",
        f"created: {today}",
        f"updated: {today}",
        "sub: triage",
        f"source: {env.source}",
        f"source_id: {env.source_id}",
        f"received_at: {env.received_at}",
        f"sender: {_scalar(env.sender)}",
        f"thread_id: {_scalar(env.thread_id)}",
        f"source_ref: {_scalar(env.source_ref)}",
        f"sha256: {_scalar(env.sha256)}",
        f"mime: {_scalar(env.mime)}",
        f"size: {_scalar(env.size)}",
        f"body_ref: {_scalar(env.body_ref)}",
        f"pii_quarantined: {_scalar(env.pii_quarantined)}",
        f"pii_classes: [{', '.join(env.pii_classes)}]",
        "---",
    ]
    return "\n".join(lines) + "\n" + env.summary + "\n"


def prepare_inbox_item(
    scope: Scope,
    *,
    text: str,
    title: str,
    source: str,
    source_id: str,
    received_at: str,
    sender: str | None = None,
    thread_id: str | None = None,
    source_ref: str | None = None,
    body_ref: str | None = None,
    sha256: str | None = None,
    mime: str | None = None,
    size: int | None = None,
    attachments: list[str] | None = None,
    slug_seed: str | None = None,
) -> tuple[Path, Envelope, str]:
    entity = scope.current_entity()
    if not sha256:
        raise IngestRepositoryError("adapter receipt requires sha256")
    redacted, matches = redact(text)
    env = Envelope(
        source=source,
        source_id=source_id,
        thread_id=thread_id,
        sender=sender,
        received_at=received_at,
        title=title,
        summary=redacted[:SUMMARY_CHARS],
        attachments=attachments or [],
        source_ref=source_ref,
        body_ref=body_ref or source_ref,
        sha256=sha256,
        mime=mime,
        size=size,
        pii_quarantined=bool(matches),
        pii_classes=sorted({m.kind for m in matches}),
    )
    seed = (slug_seed or source_id or "item")[:8]
    note_path = scope.resolve("00-inbox", "active", f"{_slug(title)}-{seed}.md")
    return note_path, env, render_note(env, entity)


def _git(scope: Scope, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=scope.root, check=check,
        capture_output=True, text=True,
    )


def _require_git_head(scope: Scope) -> None:
    probe = _git(scope, "rev-parse", "--is-inside-work-tree", check=False)
    head = _git(scope, "rev-parse", "--verify", "HEAD", check=False)
    if probe.returncode or probe.stdout.strip() != "true" or head.returncode:
        raise IngestRepositoryError("vault must be an initialized Git repository")


def _relative(scope: Scope, path: Path) -> str:
    return path.resolve().relative_to(scope.root.resolve()).as_posix()


def _cleanup_attempt(
    scope: Scope,
    path: Path,
    rendered: str,
    created_dirs: list[Path],
    expected_head: str,
) -> None:
    rel = _relative(scope, path)
    failures: list[str] = []
    current_head = _git(scope, "rev-parse", "HEAD", check=False).stdout.strip()
    if current_head != expected_head:
        raise IngestCommitError("repository HEAD changed during receipt commit")
    reset = _git(scope, "reset", "-q", "HEAD", "--", rel, check=False)
    if reset.returncode:
        failures.append("could not unstage attempted receipt")
    try:
        if path.exists():
            if path.read_text(encoding="utf-8") != rendered:
                failures.append("attempted receipt changed during cleanup")
            else:
                path.unlink()
        for directory in created_dirs:
            if directory.exists():
                directory.rmdir()
    except OSError:
        failures.append("could not remove attempted receipt state")
    staged = _git(scope, "diff", "--cached", "--name-only").stdout.splitlines()
    if path.exists() or rel in staged:
        failures.append("attempted receipt remains in work tree or index")
    if failures:
        raise IngestCommitError("; ".join(failures))


def _tracked_markdown_paths(scope: Scope) -> list[Path]:
    prefix = f"{scope.current_entity()}/"
    output = _git(scope, "ls-files", "--", prefix).stdout
    paths: list[Path] = []
    for rel in output.splitlines():
        candidate = Path(rel)
        parts = candidate.parts
        if candidate.suffix != ".md":
            continue
        if ".sensitive" in parts or "outbox" in parts or "staging" in parts:
            continue
        discovered = scope.root / candidate
        path = scope.resolve_stored(scope.vault_relative(discovered))
        if path.is_file():
            paths.append(path)
    return paths


@structured_reader(category="front-matter")
def find_tracked_receipt(scope: Scope, envelope: Envelope) -> Path | None:
    _require_git_head(scope)
    exact: Path | None = None
    for path in _tracked_markdown_paths(scope):
        fm, _body = split_front_matter(path.read_text(encoding="utf-8"))
        if fm.get("source") != envelope.source or str(fm.get("source_id")) != envelope.source_id:
            continue
        if str(fm.get("sha256")) == envelope.sha256:
            exact = path
        else:
            raise IngestIdentityConflict("source identity already has different content")
    return exact


def commit_inbox_item(scope: Scope, **kwargs) -> IngestResult:
    _require_git_head(scope)
    path, env, rendered = prepare_inbox_item(scope, **kwargs)
    existing = find_tracked_receipt(scope, env)
    if existing is not None:
        return IngestResult(existing, env, False, None)
    rel = _relative(scope, path)
    tracked_destination = (
        _git(scope, "ls-files", "--error-unmatch", "--", rel, check=False).returncode == 0
        or _git(scope, "cat-file", "-e", f"HEAD:{rel}", check=False).returncode == 0
    )
    if path.exists() or tracked_destination:
        raise IngestPathCollision("receipt destination already exists")

    created_dirs: list[Path] = []
    current = path.parent
    while current != scope.root and not current.exists():
        created_dirs.append(current)
        current = current.parent

    expected_head = _git(scope, "rev-parse", "HEAD").stdout.strip()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered, encoding="utf-8")
        _git(scope, "add", "--", rel)
        _git(scope, "commit", "--only", "-m", "ingest: add redacted receipt", "--", rel)
    except (OSError, subprocess.CalledProcessError) as exc:
        try:
            _cleanup_attempt(scope, path, rendered, created_dirs, expected_head)
        except IngestCommitError as cleanup_exc:
            raise IngestCommitError(
                f"receipt commit failed; cleanup failed: {cleanup_exc}"
            ) from exc
        raise IngestCommitError("receipt commit failed") from exc

    oid = _git(scope, "rev-parse", "HEAD").stdout.strip()
    changed = sorted(
        line for line in _git(
            scope, "diff-tree", "--no-commit-id", "--name-only", "-r", oid
        ).stdout.splitlines() if line
    )
    if changed != [rel]:
        raise IngestRepositoryError("ingest commit changed an unexpected path")
    return IngestResult(path, env, True, oid)
