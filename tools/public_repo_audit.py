"""Audit tracked Git content before publishing a repository."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import re
import subprocess

import yaml


HOME_PREFIX = "/" + "Users" + "/"
CREDENTIAL_RE = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|password|client[_-]?secret)\b"
    r"\s*[:=]\s*[\"'][^\"']{8,}[\"']"
)
URL_CREDENTIAL_RE = re.compile(r"https?://[^/\s:@]+:[^@\s/]+@")
PRIVATE_MARKER_RE = re.compile(
    r"(?:^|[\s\"'=(\[,]|:(?=/[^/]))/(?:[^\s\"']*/)?\.sensitive/"
)


@dataclass(frozen=True)
class Finding:
    category: str
    location: str
    message: str


def audit_repository(
    repo: Path,
    vault: Path | None = None,
    include_history: bool = False,
) -> list[Finding]:
    terms = load_instance_terms(vault) if vault else set()
    selected = revisions(repo) if include_history else ["HEAD"]
    findings = []
    for revision in selected:
        findings.extend(scan_revision(repo, revision, terms))
    return sorted(set(findings), key=lambda item: (item.location, item.category))


def revisions(repo: Path) -> list[str]:
    return git_output(repo, "rev-list", "--all").splitlines()


def scan_revision(repo: Path, revision: str, terms: set[str]) -> list[Finding]:
    paths = git_output(repo, "ls-tree", "-r", "--name-only", revision).splitlines()
    findings: list[Finding] = []
    for relative_path in paths:
        blob = git_bytes(repo, "show", f"{revision}:{relative_path}")
        if b"\0" in blob:
            continue
        try:
            text = blob.decode("utf-8")
        except UnicodeDecodeError:
            continue
        findings.extend(scan_text(revision, relative_path, text, terms))
    return findings


def scan_text(
    revision: str, relative_path: str, text: str, terms: set[str]
) -> list[Finding]:
    findings: list[Finding] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        location = f"{revision}:{relative_path}:{line_number}"
        if HOME_PREFIX in line or PRIVATE_MARKER_RE.search(line):
            findings.append(
                Finding(
                    category="absolute-private-path",
                    location=location,
                    message="absolute private path detected",
                )
            )
        if CREDENTIAL_RE.search(line) or URL_CREDENTIAL_RE.search(line):
            findings.append(
                Finding(
                    category="credential",
                    location=location,
                    message="credential assignment or URL detected",
                )
            )
        if any(instance_term_in_line(term, line) for term in terms):
            findings.append(
                Finding(
                    category="instance-value",
                    location=location,
                    message="private registry-derived value detected",
                )
            )
    return findings


def instance_term_in_line(term: str, line: str) -> bool:
    return bool(
        re.search(rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])", line)
    )


def load_instance_terms(vault: Path) -> set[str]:
    system = vault / "_system"
    entities = load_yaml(system / "entities.yaml").get("entities", {})
    products = load_yaml(system / "products.yaml").get("products", {})
    members = load_yaml(system / "members.yaml").get("members", {})
    terms = set(entities)
    for entity_products in products.values():
        if isinstance(entity_products, dict):
            terms.update(entity_products)
    for entity_members in members.values():
        if isinstance(entity_members, list):
            terms.update(
                member["id"]
                for member in entity_members
                if isinstance(member, dict) and isinstance(member.get("id"), str)
            )
    return {term for term in terms if isinstance(term, str) and len(term) >= 4}


def load_yaml(path: Path) -> dict:
    if not path.is_file():
        return {}
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return loaded if isinstance(loaded, dict) else {}


def git_output(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def git_bytes(repo: Path, *args: str) -> bytes:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True
    ).stdout


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--vault", type=Path)
    parser.add_argument("--history", action="store_true")
    args = parser.parse_args()

    findings = audit_repository(args.repo, args.vault, args.history)
    if not findings:
        print("CLEAN")
        return 0
    for finding in findings:
        print(f"{finding.category} {finding.location} {finding.message}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
