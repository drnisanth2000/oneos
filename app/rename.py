"""rename.py — renaming as a first-class, tested, atomic operation.

Spec §2.2c / conventions-v2.1 §7. A slug appears in folder names, registries,
front-matter, wikilinks, action-policy rules and hardcoded script constants;
doing that by hand is how a vault gets quietly corrupted, so it is a command
with a test instead.

Two properties make it safe:

  * **Atomic.** Every edit is applied to the working tree, then a grep-gate and
    the validators run, then exactly one commit. Any failure rolls the tree
    back to HEAD — the vault is required to be clean before we start, so
    `git reset --hard` is a complete undo.

  * **No fail-open.** The BUILD §4 danger is renaming an allow `paths:` and
    missing its `except:` for `.sensitive/`. The entity path replacement is a
    boundaried whole-token rewrite that rewrites both halves in the same pass,
    and the grep-gate then refuses to commit if a single old-slug token
    survives anywhere a validator would not have caught it.

Dry-run is the default; `--apply` is explicit. This module writes to the vault
directly and commits — the sanctioned direct admin op (spec §2.2b: add/edit/
rename direct, delete via the outbox), never the request path.
"""
from __future__ import annotations

import argparse
import difflib
import re
import subprocess
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from .console_routing import structured_reader
from .git_transaction import (
    ActionLockCleanupFailure,
    GitTransactionFailure,
    VaultBusyError,
    action_lock,
)

# Mirror of oneos_wizard.SLUG_RE / RESERVED — the app cannot import from the
# vault (it is public and vault-path-free), so the grammar is restated and the
# rename test is what keeps the two honest.
SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
RESERVED = {
    "_system", "_commons", "_archive", "_dropbox", "_meta", "_scratch",
    "outbox", "staging", "raw", "active", "archive",
}
AXES = {"entity", "product", "member", "project", "workspace"}

BINARY_EXT = {
    ".pdf", ".docx", ".xlsx", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico",
    ".db", ".sqlite", ".sqlite3", ".zip", ".gz", ".tar", ".woff", ".woff2",
}
SKIP_DIRS = {".git", ".obsidian", ".trash"}


class RenameError(Exception):
    pass


class RenameCommittedError(Exception):
    """The rename committed, but post-commit confirmation or cleanup failed."""

    def __init__(
        self, commit_oid: str | None, cleanup_error: OSError | None = None
    ) -> None:
        self.commit_oid = commit_oid
        self.cleanup_error = cleanup_error
        commit_id = commit_oid if commit_oid is not None else "unknown/unavailable"
        detail = (
            "; action-lock cleanup failed" if cleanup_error is not None else ""
        )
        super().__init__(
            f"rename committed (commit id: {commit_id}){detail}; do not retry"
        )


@dataclass
class RenamePlan:
    axis: str
    old: str
    new: str
    vault: Path
    moves: list[tuple[Path, Path]] = field(default_factory=list)
    edits: dict[Path, str] = field(default_factory=dict)  # current path -> new text
    reports: list[str] = field(default_factory=list)


# --- helpers ---------------------------------------------------------------

def _git(vault: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=vault, check=True, capture_output=True, text=True
    ).stdout


def _git_clean(vault: Path) -> bool:
    return _git(vault, "status", "--short").strip() == ""


def _boundaried(slug: str) -> re.Pattern:
    """`slug` as a whole token — not preceded or followed by a word char or a
    hyphen, so a short slug like `cd` never matches inside `record`, and a
    short entity slug never matches inside a longer hyphenated one."""
    return re.compile(rf"(?<![\w-]){re.escape(slug)}(?![\w-])")


def _text_files(vault: Path):
    for p in vault.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(vault)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if p.suffix.lower() in BINARY_EXT:
            continue
        yield p


def _read(p: Path) -> str | None:
    try:
        return p.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None


def grep_gate(vault: Path, old: str) -> list[tuple[str, int, str]]:
    """Every surviving occurrence of the old slug as a whole token, except on a
    `former_slugs:` line (the one place it is meant to remain). A non-empty
    result must abort the rename — a missed reference is a loud failure, never a
    silent fail-open."""
    pat = _boundaried(old)
    residuals = []
    for p in _text_files(vault):
        text = _read(p)
        if text is None:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if pat.search(line) and "former_slugs" not in line:
                residuals.append((str(p.relative_to(vault)), i, line.strip()))
    return residuals


def _validate_new_slug(new: str) -> None:
    if not SLUG_RE.match(new):
        raise RenameError(f"invalid slug {new!r} — lowercase, digits, hyphens")
    if new in RESERVED:
        raise RenameError(f"{new!r} is a reserved name")


def _insert_former_slug(text: str, key: str, old: str, indent_hint: int = 2) -> str:
    """Insert `former_slugs: [old]` as a child of the (first) `key:` line."""
    key_re = re.compile(rf"^(\s*){re.escape(key)}:\s*$")
    out, done = [], False
    for line in text.splitlines(keepends=True):
        out.append(line)
        m = key_re.match(line)
        if m and not done:
            child = m.group(1) + " " * indent_hint
            if not line.endswith("\n"):
                out[-1] = line + "\n"
            out.append(f"{child}former_slugs: [{old}]\n")
            done = True
    return "".join(out)


def _quote_identifier(name: str) -> str:
    """Quote a SQLite identifier (table or column name) discovered from a
    file's own schema, never trusted as SQL syntax. Doubling an embedded `"`
    is SQLite's own escaping rule for a quoted identifier. This also fixes
    legitimate names containing spaces, hyphens, or reserved words, which
    previously broke the generated SQL outright."""
    return '"' + name.replace('"', '""') + '"'


@structured_reader(category="admin-db")
def _books_db_refs(vault: Path, column_values: dict[str, str]) -> list[str]:
    """Count (never modify) books.db rows referencing `old` in the given
    {table_or_any: column} sense. Reported for the human; the actual column
    update is deferred (spec §2.2c defers it, and fund_holdings.member_id is
    opaque, not the registry id)."""
    reports = []
    for db in vault.rglob("books.db"):
        try:
            conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        except sqlite3.Error:
            continue
        try:
            try:
                tables = [
                    r[0] for r in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                ]
                for table in tables:
                    qtable = _quote_identifier(table)
                    cols = {
                        r[1] for r in conn.execute(f"PRAGMA table_info({qtable})")
                    }
                    for col, val in column_values.items():
                        if col in cols:
                            qcol = _quote_identifier(col)
                            n = conn.execute(
                                f"SELECT COUNT(*) FROM {qtable} WHERE {qcol} = ?",
                                (val,),
                            ).fetchone()[0]
                            if n:
                                rel = db.relative_to(vault)
                                reports.append(
                                    f"{rel}: {n} row(s) in {table}.{col} = "
                                    f"'{val}' (not modified — see spec §2.2c)"
                                )
            except sqlite3.DatabaseError as exc:
                # connect() with mode=ro never validates the header, so a
                # corrupt file only fails at the first query. Normalize the
                # type; the existing skip-on-open tolerance is untouched.
                raise RenameError("books.db could not be read") from exc
        finally:
            conn.close()
    return reports


# --- per-axis planners -----------------------------------------------------

def _plan_entity(plan: RenamePlan) -> None:
    vault, old, new = plan.vault, plan.old, plan.new
    old_dir = vault / old
    ent_path = vault / "_system" / "entities.yaml"
    ent_text = _read(ent_path) or ""
    key_re = lambda slug: re.compile(rf"(?m)^\s{{2}}{re.escape(slug)}:")

    if not old_dir.is_dir() and not key_re(old).search(ent_text):
        raise RenameError(f"entity {old!r} not found on disk or in entities.yaml")
    if (vault / new).exists() or key_re(new).search(ent_text):
        raise RenameError(f"target {new!r} already exists")

    if old_dir.is_dir():
        plan.moves.append((old_dir, vault / new))

    # Entity slugs are distinctive multi-token paths, so a boundaried whole-vault
    # rewrite is safe and is what reaches the hardcoded script constants. This
    # rewrites the fail-open rule's paths: and except: in the same pass.
    pat = _boundaried(old)
    for p in _text_files(vault):
        text = _read(p)
        if text is not None and pat.search(text):
            plan.edits[p] = pat.sub(new, text)

    # former_slugs on the (now-renamed) entities.yaml key.
    base = plan.edits.get(ent_path, ent_text)
    if base:
        plan.edits[ent_path] = _insert_former_slug(base, new, old)

    if (old_dir / "books.db").exists():
        plan.reports.append(
            "books.db moves with the bundle; no column change on an entity rename"
        )
    plan.reports.append(
        f"entity {old} → {new}: 1 dir move, {len(plan.edits)} file(s) rewritten"
    )


def _replace_fm_field(text: str, field_name: str, old: str, new: str) -> str:
    """Within the leading front-matter block only, replace `field: old` (exact
    value) with `field: new`."""
    if not text.startswith("---"):
        return text
    end = text.find("---", 3)
    if end == -1:
        return text
    head, fm, tail = text[:3], text[3:end], text[end:]
    fm = re.sub(
        rf"(?m)^(\s*{re.escape(field_name)}:\s*){re.escape(old)}\s*$",
        rf"\g<1>{new}",
        fm,
    )
    return head + fm + tail


def _plan_value_axis(plan: RenamePlan, fm_field: str, registry: str,
                     key_indent: int | None, db_columns: dict[str, str]) -> None:
    """Shared planner for product / member: a value that lives in front-matter
    and in one registry, scoped (never a blanket token sweep — these slugs can
    be short)."""
    vault, old, new = plan.vault, plan.old, plan.new

    # front-matter field values across the vault
    for p in vault.rglob("*.md"):
        if any(part in SKIP_DIRS for part in p.relative_to(vault).parts):
            continue
        text = _read(p)
        if text is None:
            continue
        new_text = _replace_fm_field(text, fm_field, old, new)
        if new_text != text:
            plan.edits[p] = new_text

    # registry: rename the key / id
    reg = vault / "_system" / registry
    reg_text = _read(reg)
    if reg_text is not None:
        if key_indent is not None:
            # nested mapping key: `<indent><old>:`
            m = re.search(rf"(?m)^\s{{{key_indent}}}{re.escape(old)}:", reg_text)
            if not m:
                raise RenameError(f"{old!r} not found in {registry}")
            new_reg = re.sub(
                rf"(?m)^(\s{{{key_indent}}}){re.escape(old)}:",
                rf"\g<1>{new}:", reg_text,
            )
            new_reg = _insert_former_slug(new_reg, new, old, indent_hint=key_indent + 2)
        else:
            # list item `id: <old>`
            if not re.search(rf"(?m)\bid:\s*{re.escape(old)}\b", reg_text):
                raise RenameError(f"{old!r} not found in {registry}")
            new_reg = re.sub(
                rf"(?m)(\bid:\s*){re.escape(old)}\b", rf"\g<1>{new}", reg_text
            )
        plan.edits[reg] = new_reg

    plan.reports += _books_db_refs(vault, db_columns)


def _plan_product(plan: RenamePlan) -> None:
    vault, old, new = plan.vault, plan.old, plan.new
    _plan_value_axis(plan, "product", "products.yaml", key_indent=4,
                     db_columns={"product": old, "tag": old})
    # workspaces referencing this product
    ws = vault / "_system" / "workspaces.yaml"
    ws_text = _read(ws)
    if ws_text is not None:
        new_ws = re.sub(rf"(?m)(\bproduct:\s*){re.escape(old)}\b", rf"\g<1>{new}", ws_text)
        # a product workspace whose id equals the product slug
        new_ws = re.sub(
            rf"(?m)(\bid:\s*){re.escape(old)}\b(?=.*\bkind:\s*product)",
            rf"\g<1>{new}", new_ws,
        )
        if new_ws != ws_text:
            plan.edits[ws] = new_ws
    plan.reports.append(f"product {old} → {new}")


def _plan_member(plan: RenamePlan) -> None:
    _plan_value_axis(plan, "member", "members.yaml", key_indent=None,
                     db_columns={"member": plan.old, "member_id": plan.old})
    plan.reports.append(f"member {plan.old} → {plan.new}")


def _plan_workspace(plan: RenamePlan) -> None:
    vault, old, new = plan.vault, plan.old, plan.new
    ws = vault / "_system" / "workspaces.yaml"
    ws_text = _read(ws)
    if ws_text is None or not re.search(rf"(?m)\bid:\s*{re.escape(old)}\b", ws_text):
        raise RenameError(f"workspace {old!r} not found")
    plan.edits[ws] = re.sub(
        rf"(?m)(\bid:\s*){re.escape(old)}\b", rf"\g<1>{new}", ws_text
    )
    plan.reports.append(f"workspace {old} → {new} (UI identifier only)")


def _plan_project(plan: RenamePlan) -> None:
    vault, old, new = plan.vault, plan.old, plan.new
    # project directories live inside a bundle's pipeline; move each match.
    dirs = [
        d for d in vault.rglob(old)
        if d.is_dir()
        and "02-pipeline" in d.relative_to(vault).parts
        and not any(part in SKIP_DIRS for part in d.relative_to(vault).parts)
    ]
    if not dirs:
        raise RenameError(f"project {old!r} not found under any */02-pipeline/")
    for d in dirs:
        plan.moves.append((d, d.parent / new))

    # references: wikilinks, repo: paths, and pipeline path segments only.
    pat = _boundaried(old)
    for p in vault.rglob("*.md"):
        if any(part in SKIP_DIRS for part in p.relative_to(vault).parts):
            continue
        text = _read(p)
        if text is None:
            continue
        lines = text.splitlines(keepends=True)
        changed = False
        for i, line in enumerate(lines):
            if ("[[" in line or "repo:" in line or "02-pipeline" in line) and pat.search(line):
                lines[i] = pat.sub(new, line)
                changed = True
        if changed:
            plan.edits[p] = "".join(lines)
    plan.reports.append(f"project {old} → {new}: {len(dirs)} dir move(s)")


_PLANNERS = {
    "entity": _plan_entity,
    "product": _plan_product,
    "member": _plan_member,
    "workspace": _plan_workspace,
    "project": _plan_project,
}


# --- public API ------------------------------------------------------------

def plan_rename(vault: Path | str, axis: str, old: str, new: str) -> RenamePlan:
    vault = Path(vault)
    if axis not in AXES:
        raise RenameError(f"unknown axis {axis!r} — one of {sorted(AXES)}")
    if old == new:
        raise RenameError("old and new slug are identical")
    _validate_new_slug(new)
    plan = RenamePlan(axis=axis, old=old, new=new, vault=vault)
    _PLANNERS[axis](plan)
    if not plan.moves and not plan.edits:
        raise RenameError(f"{axis} {old!r} not found — nothing to rename")
    return plan


def render_diff(plan: RenamePlan) -> str:
    """Dry-run output — computed from disk, writes nothing."""
    out = []
    for src, dst in plan.moves:
        out.append(f"move: {src.relative_to(plan.vault)}/ → {dst.relative_to(plan.vault)}/")
    for p in sorted(plan.edits, key=lambda x: str(x)):
        old_text = _read(p) or ""
        rel = str(p.relative_to(plan.vault))
        out.append(
            "".join(
                difflib.unified_diff(
                    old_text.splitlines(True),
                    plan.edits[p].splitlines(True),
                    fromfile=rel, tofile=rel,
                )
            )
        )
    for r in plan.reports:
        out.append(f"# {r}")
    return "\n".join(out)


def _validate_check_v2(vault: Path, plan: RenamePlan) -> None:
    script = vault / "_system" / "scripts" / "check_v2.py"
    if not script.is_file():
        return
    r = subprocess.run(
        ["python3", str(script), str(vault)], capture_output=True, text=True
    )
    if r.returncode != 0:
        raise RenameError(f"check_v2 failed after rename:\n{r.stdout}\n{r.stderr}")


DEFAULT_VALIDATORS = [_validate_check_v2]


def apply_rename(vault: Path | str, plan: RenamePlan, validators=None) -> str:
    vault = Path(vault)
    commit_completed = False
    commit_oid: str | None = None
    lock_body_entered = False
    try:
        with action_lock(vault):
            lock_body_entered = True
            if not _git_clean(vault):
                raise RenameError(
                    "vault has uncommitted changes; commit or stash first"
                )
            validators = DEFAULT_VALIDATORS if validators is None else validators
            try:
                for p, new_text in plan.edits.items():
                    p.write_text(new_text, encoding="utf-8")
                for src, dst in plan.moves:
                    _git(
                        vault,
                        "mv",
                        str(src.relative_to(vault)),
                        str(dst.relative_to(vault)),
                    )

                residual = grep_gate(vault, plan.old)
                if residual:
                    raise RenameError(
                        f"residual old slug after rename: {residual[:5]}"
                    )

                for v in validators:
                    v(vault, plan)

                _git(vault, "add", "-A")
                _git(
                    vault,
                    "commit",
                    "-q",
                    "-m",
                    f"rename: {plan.old} → {plan.new}",
                )
                commit_completed = True
            except Exception:
                _git(vault, "reset", "-q", "--hard", "HEAD")
                _git(vault, "clean", "-qfd")
                raise
            try:
                commit_oid = _git(vault, "rev-parse", "HEAD").strip()
            except Exception as exc:
                raise RenameCommittedError(None) from exc
            return f"rename: {plan.old} → {plan.new}"
    except ActionLockCleanupFailure as exc:
        if not commit_completed:
            raise RenameError(
                "shared action lock cleanup failed before the rename committed"
            ) from exc
        raise RenameCommittedError(commit_oid, exc.cleanup_error) from exc
    except VaultBusyError as exc:
        raise RenameError(
            "vault is busy; another OneOS action is already running"
        ) from exc
    except GitTransactionFailure as exc:
        if not lock_body_entered:
            raise RenameError(
                "shared action lock is unavailable; the rename was not started"
            ) from exc
        raise


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="oneos rename", description="Rename a slug across the vault.")
    parser.add_argument("axis", choices=sorted(AXES))
    parser.add_argument("old")
    parser.add_argument("new")
    parser.add_argument("--vault-root", default=".")
    parser.add_argument("--apply", action="store_true", help="execute (default is dry-run)")
    args = parser.parse_args(argv)

    vault = Path(args.vault_root).expanduser().resolve()
    try:
        plan = plan_rename(vault, args.axis, args.old, args.new)
        if not args.apply:
            print(render_diff(plan))
            print(f"\n[DRY RUN] re-run with --apply to execute: rename {args.axis} {args.old} → {args.new}")
            return 0
        msg = apply_rename(vault, plan)
        print(f"[DONE] {msg}")
        return 0
    except RenameCommittedError as e:
        commit_id = e.commit_oid if e.commit_oid is not None else "unknown/unavailable"
        cleanup_detail = (
            ", and shared action-lock cleanup failed"
            if e.cleanup_error is not None
            else ""
        )
        print(
            f"[COMMITTED] Rename committed (commit id: {commit_id})"
            f"{cleanup_detail}. Do not retry this rename."
        )
        return 2
    except RenameError as e:
        print(f"[ABORTED] {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
