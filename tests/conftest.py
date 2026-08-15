"""Synthetic vault fixtures.

Committed tests must stay instance-agnostic: no real slug, path, or module
count from the actual vault ever appears in this repo (AGENTS.md, "the one
rule"). Every test builds its own throwaway vault with invented slugs, so the
same code that drives the real system drives these.
"""
from pathlib import Path
import json
import subprocess
import textwrap

import pytest

# A minimal but faithful archetypes.yaml: one module gated behind a flag
# (`zz-extra` needs `special`), the rest unconditional — mirroring how the real
# registry gates only `15-self` behind `personal`.
ARCHETYPES = textwrap.dedent(
    """
    version: "2.0"
    flags:
      special: "Activates the extra module"
      other:   "Some other capability"
    modules:
      00-intake:  { block: system, core: true }
      01-core:    { block: govern, core: true }
      02-work:    { block: build }
      zz-extra:   { block: self, core: true, requires_flag: special }
    submodules:
      00-intake:
        triage: { name: "Triage" }
    archetypes:
      plain:   { }
      special: { special: true }
    """
).strip()


def write_vault(root: Path, entities_yaml: str, archetypes_yaml: str = ARCHETYPES) -> Path:
    system = root / "_system"
    system.mkdir(parents=True, exist_ok=True)
    (system / "archetypes.yaml").write_text(archetypes_yaml, encoding="utf-8")
    (system / "entities.yaml").write_text(entities_yaml, encoding="utf-8")
    return root


def entities_yaml(*slugs: str, ingest: dict[str, list[str]] | None = None) -> str:
    rows = ['version: "1.0"', "entities:"]
    for slug in slugs:
        rows.extend((f"  {slug}:", f"    label: {slug.title()}", "    flags: []"))
        addresses = (ingest or {}).get(slug, [])
        if addresses:
            rows.append("    ingest:")
            rows.append("      email_addresses:")
            rows.extend(f"        - {json.dumps(address)}" for address in addresses)
    return "\n".join(rows) + "\n"


def scaffold_modules(root: Path, slug: str, modules: list[str]) -> None:
    """Create the given module directories on disk under a bundle."""
    for m in modules:
        (root / slug / m).mkdir(parents=True, exist_ok=True)


@pytest.fixture
def make_vault(tmp_path):
    def _make(entities_yaml: str, archetypes_yaml: str = ARCHETYPES) -> Path:
        return write_vault(tmp_path, entities_yaml, archetypes_yaml)
    return _make


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout


def write_tree(root: Path, files: dict[str, str]) -> None:
    """Write a {relative_path: content} tree, creating parents."""
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")


def git_vault(root: Path, files: dict[str, str]) -> Path:
    """Create a committed git repo at `root` from a file tree — a throwaway
    vault for rename tests. Never the real vault."""
    write_tree(root, files)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "user.name", "test")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    return root


def git_entity_vault(
    root: Path,
    entities: tuple[str, ...],
    files: dict[str, str],
    *,
    ingest: dict[str, list[str]] | None = None,
) -> Path:
    tree = dict(files)
    tree.setdefault("_system/entities.yaml", entities_yaml(*entities, ingest=ingest))
    return git_vault(root, tree)


def git_head_message(root: Path) -> str:
    return _git(root, "log", "-1", "--pretty=%s").strip()


def git_is_clean(root: Path) -> bool:
    return _git(root, "status", "--short").strip() == ""


def git_count_commits(root: Path) -> int:
    return int(_git(root, "rev-list", "--count", "HEAD").strip())


def git_head(root: Path) -> str:
    return _git(root, "rev-parse", "HEAD").strip()


def git_changed_paths(root: Path, revision: str = "HEAD") -> list[str]:
    output = _git(root, "diff-tree", "--no-commit-id", "--name-only", "-r", revision)
    return sorted(line for line in output.splitlines() if line)


def git_tracked_paths(root: Path) -> list[str]:
    return sorted(line for line in _git(root, "ls-files").splitlines() if line)


def git_index_paths(root: Path) -> list[str]:
    return sorted(line for line in _git(root, "diff", "--cached", "--name-only").splitlines() if line)


def git_history_contains(root: Path, needle: str) -> bool:
    objects = _git(root, "rev-list", "--objects", "--all")
    for line in objects.splitlines():
        oid = line.split(" ", 1)[0]
        proc = subprocess.run(
            ["git", "cat-file", "-p", oid], cwd=root,
            check=True, capture_output=True,
        )
        if needle.encode("utf-8") in proc.stdout:
            return True
    return False
