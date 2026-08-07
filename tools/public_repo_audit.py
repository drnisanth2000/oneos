"""Audit tracked Git content before publishing a repository."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
import subprocess

import yaml


POSIX_ABSOLUTE_HOME_RE = re.compile(
    r"(?:^|[\s\"'=`(\[,])"
    r"(?:/(?:Users|home)/[^\s\"'`]+|/root(?:/[^\s\"'`]+)?)"
)
WINDOWS_ABSOLUTE_HOME_RE = re.compile(
    r"(?:^|[\s\"'=`(\[,])[A-Za-z]:[\\/]+Users[\\/]+[^\s\"'`]+",
    re.IGNORECASE,
)
CREDENTIAL_RE = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|password|client[_-]?secret)\b"
    r"\s*[:=]\s*(?:[\"'][^\"']{8,}[\"']|[A-Za-z0-9_./+=-]{8,})"
)
URL_CREDENTIAL_RE = re.compile(r"https?://[^/\s:@]+:[^@\s/]+@")
PRIVATE_MARKER_RE = re.compile(
    r"(?:^|[\s\"'=(\[,]|:(?=/[^/]))/(?:[^\s\"']*/)?\.sensitive/"
)
POSIX_HOME_IN_PATH_RE = re.compile(
    r"(?:^|/)(?:(?:Users|home)/[^/]+|root)(?:/|$)"
)
WINDOWS_HOME_IN_PATH_RE = re.compile(
    r"(?:^|/)[A-Za-z]:[\\/]+Users[\\/]+[^\\/]+(?:[\\/]|$)", re.IGNORECASE
)
SAFE_TRACKED_PATH_RE = re.compile(r"\A[A-Za-z0-9._/@+,-]+\Z")
GITHUB_NOREPLY_RE = re.compile(
    r"^(?:[0-9]+\+)?(?P<owner>[A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))"
    r"@users\.noreply\.github\.com$",
    re.IGNORECASE,
)
GITHUB_MERGE_SUBJECT_RE = re.compile(
    r"\AMerge pull request #[0-9]+ from "
    r"(?P<owner>[A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/[^\r\n]+"
)
# This repository needs one generic PDF fixture for ingestion tests. Its digest
# is the SHA-256 of the tracked fixture bytes (`shasum -a 256`); path and digest
# must both match so replacements fail closed.
ALLOWED_BINARY_BLOBS = {
    "tests/fixtures/sample.pdf": (
        "522d88772affe7c5b08039d4cc541c4adeb0c06fee457330852989335718ed2c"
    )
}
BINARY_SIGNATURES = (
    b"%PDF-",
    b"GIF87a",
    b"GIF89a",
    b"PK\x03\x04",
    b"SQLite format 3\x00",
    b"\x7fELF",
    b"\x89PNG\r\n\x1a\n",
    b"\xff\xd8\xff",
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
        findings.extend(scan_commit_metadata(repo, revision, terms))
    return sorted(set(findings), key=lambda item: (item.location, item.category))


def revisions(repo: Path) -> list[str]:
    return git_output(repo, "rev-list", "--all").splitlines()


def scan_commit_metadata(repo: Path, revision: str, terms: set[str]) -> list[Finding]:
    raw_metadata = git_output(
        repo, "show", "-s", "--format=%an%x00%ae%x00%cn%x00%ce%x00%B", revision
    )
    author_name, author_email, committer_name, committer_email, message = (
        raw_metadata.split("\0", maxsplit=4)
    )
    author_name, author_email = redact_github_remote_identity(
        author_name, author_email
    )
    committer_name, committer_email = redact_github_remote_identity(
        committer_name, committer_email
    )
    parent_fields = git_output(repo, "rev-list", "--parents", "-n", "1", revision)
    github_committer = (
        committer_email.casefold() == "noreply@github.com"
        or "<github-owner>@users.noreply.github.com" in committer_email.casefold()
    )
    message = redact_github_merge_owner(
        message,
        github_generated=len(parent_fields.split()) >= 3 and github_committer,
    )
    metadata = "\n".join(
        [author_name, author_email, committer_name, committer_email, message]
    )
    return scan_text(revision, "<commit-metadata>", metadata, terms)


def redact_github_remote_identity(name: str, email: str) -> tuple[str, str]:
    match = GITHUB_NOREPLY_RE.fullmatch(email)
    if not match:
        return name, email
    owner = match.group("owner")
    redacted_name = "<github-owner>" if name.casefold() == owner.casefold() else name
    start, end = match.span("owner")
    redacted_email = email[:start] + "<github-owner>" + email[end:]
    return redacted_name, redacted_email


def redact_github_merge_owner(message: str, *, github_generated: bool) -> str:
    if not github_generated:
        return message
    match = GITHUB_MERGE_SUBJECT_RE.match(message)
    if not match:
        return message
    start, end = match.span("owner")
    return message[:start] + "<github-owner>" + message[end:]


def scan_revision(repo: Path, revision: str, terms: set[str]) -> list[Finding]:
    raw_paths = git_bytes(repo, "ls-tree", "-r", "-z", "--name-only", revision)
    paths = [
        raw_path.decode("utf-8", errors="surrogateescape")
        for raw_path in raw_paths.split(b"\0")
        if raw_path
    ]
    findings: list[Finding] = []
    for relative_path in paths:
        private_home_path = private_home_path_in_tracked_name(relative_path)
        private_instance_path = any(
            instance_term_in_path(term, relative_path) for term in terms
        )
        private_credential_path = credential_in_text(relative_path)
        path_is_sensitive = (
            private_home_path or private_instance_path or private_credential_path
        )
        display_path = safe_display_path(relative_path, path_is_sensitive)
        if private_home_path:
            findings.append(
                Finding(
                    category="absolute-private-path",
                    location=f"{revision}:{display_path}",
                    message="absolute private path detected",
                )
            )
        if private_instance_path:
            findings.append(
                Finding(
                    category="instance-value",
                    location=f"{revision}:{display_path}",
                    message="private registry-derived value detected",
                )
            )
        if private_credential_path:
            findings.append(
                Finding(
                    category="credential",
                    location=f"{revision}:{display_path}",
                    message="credential assignment or URL detected",
                )
            )
        if database_artifact_path(relative_path):
            findings.append(
                Finding(
                    category="forbidden-artifact",
                    location=f"{revision}:<redacted-path>",
                    message="database artifact detected",
                )
            )
            continue
        blob = git_bytes(repo, "show", f"{revision}:{relative_path}")
        if blob_is_binary(blob):
            if binary_is_allowlisted(relative_path, blob):
                continue
            findings.append(
                Finding(
                    category="unapproved-binary",
                    location=f"{revision}:{display_path}",
                    message="binary blob is not explicitly allowlisted",
                )
            )
            continue
        text = blob.decode("utf-8")
        findings.extend(scan_text(revision, display_path, text, terms))
    return findings


def binary_is_allowlisted(relative_path: str, blob: bytes) -> bool:
    expected = ALLOWED_BINARY_BLOBS.get(relative_path)
    return expected is not None and hashlib.sha256(blob).hexdigest() == expected


def blob_is_binary(blob: bytes) -> bool:
    if blob.startswith(BINARY_SIGNATURES):
        return True
    try:
        text = blob.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return any(
        (ord(character) < 32 and character not in "\n\r\t\f")
        or ord(character) == 127
        for character in text
    )


def database_artifact_path(relative_path: str) -> bool:
    name = Path(relative_path).name.lower()
    return name == "books.db" or name.endswith((".db", ".sqlite", ".sqlite3"))


def private_home_path_in_tracked_name(relative_path: str) -> bool:
    return bool(
        POSIX_HOME_IN_PATH_RE.search(relative_path)
        or WINDOWS_HOME_IN_PATH_RE.search(relative_path)
    )


def safe_display_path(relative_path: str, path_is_sensitive: bool) -> str:
    if path_is_sensitive or not SAFE_TRACKED_PATH_RE.fullmatch(relative_path):
        return "<redacted-path>"
    return relative_path


def scan_text(
    revision: str, relative_path: str, text: str, terms: set[str]
) -> list[Finding]:
    findings: list[Finding] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        location = f"{revision}:{relative_path}:{line_number}"
        if (
            POSIX_ABSOLUTE_HOME_RE.search(line)
            or WINDOWS_ABSOLUTE_HOME_RE.search(line)
            or PRIVATE_MARKER_RE.search(line)
        ):
            findings.append(
                Finding(
                    category="absolute-private-path",
                    location=location,
                    message="absolute private path detected",
                )
            )
        if credential_in_text(line):
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


def credential_in_text(text: str) -> bool:
    return bool(CREDENTIAL_RE.search(text) or URL_CREDENTIAL_RE.search(text))


def instance_term_in_line(term: str, line: str) -> bool:
    if len(term) >= 4:
        return bool(
            re.search(rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])", line)
        )
    # Short IDs are matched only as values of explicit registry-identity keys;
    # free prose such as ordinary two-letter words remains usable.
    return bool(
        re.search(
            rf"(?i:^\s*[\"']?(?:entity|product|member)(?:[_-]?id)?[\"']?"
            rf"\s*[:=]\s*[\"']?){re.escape(term)}(?![A-Za-z0-9])",
            line,
        )
    )


def instance_term_in_path(term: str, relative_path: str) -> bool:
    if len(term) >= 4:
        return instance_term_in_line(term, relative_path)
    # Short IDs are unsafe in free prose. A complete path component is a
    # structured identity boundary, so matching it does not make ordinary
    # words unusable.
    return term in re.split(r"[\\/]", relative_path)


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
    return {term for term in terms if isinstance(term, str) and term}


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
