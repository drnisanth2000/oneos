"""Audit tracked Git content for finite OneOS publication rules."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
from typing import Any

import yaml

from app.action_receipts import ReceiptError, validate_all_head_receipt_stores


#: A term of this length or more is matched in every tracked text file; a
#: shorter one is only matched as a path component, a structured value, or an
#: exact Markdown token. The registry identifier floor is deliberately one
#: character above this, so retuning it must be a visible change.
LONG_TERM_MINIMUM_LENGTH = 4

ALLOWLIST_PATH = ".oneos-public-binary-allowlist"
TEXT_FIELDS = frozenset(
    {"entity", "product", "member", "workspace", "owner", "id", "slug"}
)
DATABASE_SUFFIXES = frozenset({".db", ".sqlite", ".sqlite3"})
MARKDOWN_SUFFIXES = frozenset({".md", ".markdown"})
PRIVATE_PATH_PATTERNS = (
    re.compile(r"(?<![A-Za-z0-9:/])/(?:Users|home)/[^/\s]+/"),
    # macOS per-user temporary namespace, with or without the `/private`
    # prefix. Captured test output embeds these routinely, and the segment
    # often carries a username (`pytest-of-<user>`). Matching only `/Users`
    # and `/home` let this form pass a CLEAN audit.
    # Written with alternations so the pattern's own source does not contain
    # the literal it matches — the same reason the rule above spells
    # `/(?:Users|home)/` rather than the bare path.
    re.compile(r"(?<![A-Za-z0-9:/])(?:/private)?/(?:var)/(?:folders)/[^\s\"']+"),
    re.compile(r"(?i)(?<![A-Za-z0-9:/\\])[A-Z]:\\[^\\\s]+\\"),
    re.compile(
        r"(?:^|[\s\"'=(\[,]|:(?=/[^/]))/(?:[^\s\"']*/)?\.sensitive(?:/|$)"
    ),
)
ALLOWLIST_RE = re.compile(r"^([0-9a-f]{64})  (\S(?:.*\S)?)$")


@dataclass(frozen=True)
class Finding:
    category: str
    location: str
    message: str


MESSAGES = {
    "instance-value": "private registry-derived value detected",
    "absolute-private-path": "absolute private path detected",
    "forbidden-data-artifact": "forbidden data artifact detected",
    "unapproved-binary": "unapproved non-text blob or allowlist entry detected",
    "receipt-integrity": "accumulated action receipt audit failed",
}


def audit_repository(
    repo: Path,
    vault: Path | None = None,
    include_history: bool = False,
) -> list[Finding]:
    long_terms, short_terms = load_instance_terms(vault) if vault else (set(), set())
    head = git_output(repo, "rev-parse", "HEAD").strip()
    selected = revisions(repo) if include_history else [head]
    allowlist_text = binary_allowlist_text(repo, head)
    allowlist = parse_binary_allowlist(allowlist_text)
    findings = scan_refs(repo, long_terms, short_terms)
    findings.extend(scan_annotated_tags(repo, long_terms))
    findings.extend(invalid_allowlist_findings(head, allowlist_text))
    for revision in selected:
        findings.extend(
            scan_revision(repo, revision, long_terms, short_terms, allowlist)
        )
    if vault is not None:
        try:
            validate_all_head_receipt_stores(vault)
        except ReceiptError:
            findings.append(
                finding("receipt-integrity", "vault:HEAD:receipt-store")
            )
    return sorted(set(findings), key=lambda item: (item.location, item.category))


def revisions(repo: Path) -> list[str]:
    return git_output(repo, "rev-list", "HEAD", "--remotes", "--tags").splitlines()


def scan_refs(repo: Path, long_terms: set[str], short_terms: set[str]) -> list[Finding]:
    findings: list[Finding] = []
    refs = set(
        git_output(
            repo,
            "for-each-ref",
            "--format=%(refname)",
            "refs/remotes",
            "refs/tags",
        ).splitlines()
    )
    current_ref = subprocess.run(
        ["git", "symbolic-ref", "-q", "HEAD"],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if current_ref:
        refs.add(current_ref)
    for ref in refs:
        if contains_long_term(ref, long_terms) or path_has_short_term(ref, short_terms):
            findings.append(finding("instance-value", ref_location(ref)))
    return findings


def scan_annotated_tags(repo: Path, long_terms: set[str]) -> list[Finding]:
    if not long_terms:
        return []
    findings: list[Finding] = []
    records = git_output(
        repo,
        "for-each-ref",
        "--format=%(objecttype) %(objectname)",
        "refs/tags",
    ).splitlines()
    for record in records:
        object_type, object_id = record.split(" ", 1)
        if object_type != "tag":
            continue
        metadata = git_bytes(repo, "cat-file", "tag", object_id).decode(
            "utf-8", "replace"
        )
        if contains_long_term(metadata, long_terms):
            findings.append(
                finding("instance-value", f"{object_id[:12]}:tag-metadata")
            )
    return findings


def scan_revision(
    repo: Path,
    revision: str,
    long_terms: set[str],
    short_terms: set[str],
    allowlist: set[tuple[str, str]],
) -> list[Finding]:
    entries = tree_entries(repo, revision)
    findings: list[Finding] = []

    for relative_path, object_id in entries:
        location = safe_location(revision, relative_path)
        if path_contains_term(relative_path, long_terms, short_terms):
            findings.append(finding("instance-value", location))
        if is_forbidden_data_path(relative_path):
            findings.append(finding("forbidden-data-artifact", location))
            continue

        blob = git_bytes(repo, "cat-file", "blob", object_id)
        try:
            text = blob.decode("utf-8", "strict")
            is_text = b"\0" not in blob
        except UnicodeDecodeError:
            text = ""
            is_text = False

        if not is_text:
            digest = hashlib.sha256(blob).hexdigest()
            if (relative_path, digest) not in allowlist:
                findings.append(finding("unapproved-binary", location))
            continue

        findings.extend(
            scan_text(revision, relative_path, text, long_terms, short_terms)
        )

    if long_terms:
        metadata = git_bytes(repo, "cat-file", "commit", revision).decode(
            "utf-8", "replace"
        )
        if contains_long_term(metadata, long_terms):
            findings.append(
                finding("instance-value", metadata_location(revision))
            )
    return findings


def tree_entries(repo: Path, revision: str) -> list[tuple[str, str]]:
    raw = git_bytes(repo, "ls-tree", "-rz", "--full-tree", revision)
    entries: list[tuple[str, str]] = []
    for record in raw.split(b"\0"):
        if not record:
            continue
        metadata, encoded_path = record.split(b"\t", 1)
        _mode, object_type, object_id = metadata.decode("ascii").split()
        if object_type != "blob":
            continue
        entries.append(
            (encoded_path.decode("utf-8", "surrogateescape"), object_id)
        )
    return entries


def scan_text(
    revision: str,
    relative_path: str,
    text: str,
    long_terms: set[str],
    short_terms: set[str],
) -> list[Finding]:
    findings: list[Finding] = []
    is_markdown = PurePosixPath(relative_path).suffix.lower() in MARKDOWN_SUFFIXES
    for line_number, line in enumerate(text.splitlines(), start=1):
        location = safe_location(revision, relative_path, line_number)
        if any(pattern.search(line) for pattern in PRIVATE_PATH_PATTERNS):
            findings.append(finding("absolute-private-path", location))
        if contains_long_term(line, long_terms) or (
            is_markdown and contains_exact_term(line, short_terms)
        ):
            findings.append(finding("instance-value", location))
    if not is_markdown and structured_values(relative_path, text) & short_terms:
        findings.append(finding("instance-value", safe_location(revision, relative_path)))
    return findings


def structured_values(relative_path: str, text: str) -> set[str]:
    suffix = PurePosixPath(relative_path).suffix.lower()
    loaded: Any = None
    try:
        if suffix == ".json":
            loaded = json.loads(text)
        elif suffix in {".yaml", ".yml"}:
            loaded = yaml.safe_load(text)
        elif suffix in {".md", ".markdown"}:
            lines = text.splitlines()
            if not lines or lines[0].strip() != "---":
                return set()
            closing = next(
                (index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---"),
                None,
            )
            if closing is None:
                return set()
            loaded = yaml.safe_load("\n".join(lines[1:closing]))
    except (json.JSONDecodeError, yaml.YAMLError):
        return set()
    return collect_structured_values(loaded)


def collect_structured_values(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, nested in value.items():
            if key in TEXT_FIELDS and isinstance(nested, str):
                found.add(nested)
            found.update(collect_structured_values(nested))
    elif isinstance(value, list):
        for nested in value:
            found.update(collect_structured_values(nested))
    return found


def contains_exact_term(text: str, terms: set[str]) -> bool:
    return any(
        re.search(rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])", text)
        for term in terms
    )


def contains_long_term(text: str, terms: set[str]) -> bool:
    return contains_exact_term(text, terms)


def path_contains_term(path: str, long_terms: set[str], short_terms: set[str]) -> bool:
    return contains_long_term(path, long_terms) or path_has_short_term(path, short_terms)


def path_has_short_term(path: str, short_terms: set[str]) -> bool:
    return any(component in short_terms for component in PurePosixPath(path).parts)


def is_forbidden_data_path(path: str) -> bool:
    pure = PurePosixPath(path)
    return pure.name == "books.db" or pure.suffix.lower() in DATABASE_SUFFIXES


def parse_binary_allowlist(text: str) -> set[tuple[str, str]]:
    allowed: set[tuple[str, str]] = set()
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = ALLOWLIST_RE.fullmatch(line)
        if match:
            digest, path = match.groups()
            allowed.add((path, digest))
    return allowed


def load_binary_allowlist(repo: Path, revision: str) -> set[tuple[str, str]]:
    return parse_binary_allowlist(binary_allowlist_text(repo, revision))


def binary_allowlist_text(repo: Path, revision: str) -> str:
    entries = dict(tree_entries(repo, revision))
    object_id = entries.get(ALLOWLIST_PATH)
    if object_id is None:
        return ""
    return git_bytes(repo, "cat-file", "blob", object_id).decode("utf-8", "strict")


def invalid_allowlist_findings(revision: str, text: str) -> list[Finding]:
    findings: list[Finding] = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if line and not line.startswith("#") and not ALLOWLIST_RE.fullmatch(line):
            findings.append(
                finding(
                    "unapproved-binary",
                    safe_location(revision, ALLOWLIST_PATH, line_number),
                )
            )
    return findings


def load_instance_terms(vault: Path) -> tuple[set[str], set[str]]:
    system = vault / "_system"
    terms: set[str] = set()

    entities = load_yaml(system / "entities.yaml").get("entities", {})
    if isinstance(entities, dict):
        terms.update(key for key in entities if isinstance(key, str))

    products = load_yaml(system / "products.yaml").get("products", {})
    if isinstance(products, dict):
        for entity_products in products.values():
            if isinstance(entity_products, dict):
                terms.update(
                    key for key in entity_products if isinstance(key, str)
                )

    members = load_yaml(system / "members.yaml").get("members", {})
    if isinstance(members, dict):
        for entity_members in members.values():
            if isinstance(entity_members, list):
                terms.update(
                    member["id"]
                    for member in entity_members
                    if isinstance(member, dict) and isinstance(member.get("id"), str)
                )

    workspaces = load_yaml(system / "workspaces.yaml").get("workspaces", {})
    if isinstance(workspaces, dict):
        terms.update(key for key in workspaces if isinstance(key, str))
    elif isinstance(workspaces, list):
        terms.update(
            workspace["id"]
            for workspace in workspaces
            if isinstance(workspace, dict) and isinstance(workspace.get("id"), str)
        )

    normalized = {term for term in terms if term}
    return (
        {term for term in normalized if len(term) >= LONG_TERM_MINIMUM_LENGTH},
        {term for term in normalized if len(term) < LONG_TERM_MINIMUM_LENGTH},
    )


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return loaded if isinstance(loaded, dict) else {}


def finding(category: str, location: str) -> Finding:
    return Finding(category=category, location=location, message=MESSAGES[category])


def safe_location(revision: str, path: str, line: int | None = None) -> str:
    digest = hashlib.sha256(path.encode("utf-8", "surrogateescape")).hexdigest()[:12]
    suffix = f":{line}" if line is not None else ""
    return f"{revision[:12]}:path-{digest}{suffix}"


def ref_location(ref: str) -> str:
    digest = hashlib.sha256(ref.encode("utf-8")).hexdigest()[:12]
    return f"ref-{digest}"


def metadata_location(revision: str) -> str:
    return f"{revision[:12]}:commit-metadata"


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
    for item in findings:
        print(f"{item.category} {item.location} {item.message}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
