"""S7 Task 1 — the shared exact-byte review contract.

These tests pin the primitive that every reviewed action depends on: a
snapshot hashes the exact bytes it carries, a submitted fingerprint is
strictly validated before it can ever be compared, and neither failure
discloses proposal bytes or private paths.
"""
import ast
import hashlib
import pathlib

import pytest

from app.review_tokens import (
    InvalidReviewToken,
    ReviewedProposalChanged,
    ReviewSnapshot,
    ReviewTokenError,
    make_review_snapshot,
    require_review_match,
)

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


# --- the snapshot ------------------------------------------------------------


def test_review_snapshot_hashes_the_exact_bytes():
    raw = b"id: same-id\nvalue: first\n"
    snap = make_review_snapshot("validated", raw)
    assert snap.contents == raw
    assert snap.sha256 == hashlib.sha256(raw).hexdigest()


def test_review_snapshot_carries_the_validated_value_unchanged():
    sentinel = object()
    snap = make_review_snapshot(sentinel, b"bytes")
    assert snap.value is sentinel


def test_review_snapshot_is_frozen():
    snap = make_review_snapshot("validated", b"bytes")
    for field in ("value", "contents", "sha256"):
        with pytest.raises(Exception):
            setattr(snap, field, "mutated")


def test_review_snapshot_copies_its_bytes_so_the_caller_cannot_mutate_them():
    # A mutable buffer handed in must not be able to drift out of agreement
    # with the digest that was taken from it.
    buffer = bytearray(b"id: same-id\nvalue: first\n")
    snap = make_review_snapshot("validated", buffer)
    digest_at_capture = snap.sha256
    buffer[0:2] = b"XX"

    assert isinstance(snap.contents, bytes)
    assert snap.contents == b"id: same-id\nvalue: first\n"
    assert snap.sha256 == digest_at_capture
    assert snap.sha256 == hashlib.sha256(snap.contents).hexdigest()


def test_same_id_with_different_bytes_produces_a_different_hash():
    first = make_review_snapshot("v", b"id: same-id\nvalue: first\n")
    second = make_review_snapshot("v", b"id: same-id\nvalue: second\n")
    assert first.sha256 != second.sha256


def test_a_byte_only_difference_still_changes_the_hash():
    # Same meaningful fields, different stored bytes: still a different
    # review. S7 refuses on bytes, not on rendered values.
    first = make_review_snapshot("v", b"id: same-id\nvalue: first\n")
    second = make_review_snapshot("v", b"id:  same-id\nvalue: first\n")
    assert first.sha256 != second.sha256


def test_the_digest_is_lowercase_sha256_hex():
    snap = make_review_snapshot("v", b"bytes")
    assert len(snap.sha256) == 64
    assert snap.sha256 == snap.sha256.lower()
    assert set(snap.sha256) <= set("0123456789abcdef")


# --- the submitted fingerprint ----------------------------------------------


@pytest.mark.parametrize("token", [None, "", "0" * 63, "G" * 64, "g" * 64, 123])
def test_submitted_review_sha256_is_strict(token):
    with pytest.raises(InvalidReviewToken):
        require_review_match(b"proposal", token)


@pytest.mark.parametrize(
    "token",
    [
        "0" * 65,                                    # too long
        "A" * 64,                                    # uppercase hex is not accepted
        " " + "0" * 63,                              # leading space
        "0" * 63 + "\n",                             # trailing newline
        "0" * 64 + "\n",                             # 64 hex then a newline
        b"0" * 64,                                   # bytes, not str
        True,                                        # not a str
    ],
)
def test_submitted_review_sha256_rejects_near_misses(token):
    with pytest.raises(InvalidReviewToken):
        require_review_match(b"proposal", token)


def test_a_malformed_token_is_invalid_even_when_the_bytes_would_match():
    contents = b"proposal"
    correct = hashlib.sha256(contents).hexdigest()
    with pytest.raises(InvalidReviewToken):
        require_review_match(contents, correct.upper())


def test_matching_bytes_return_the_submitted_token():
    contents = b"id: same-id\nvalue: first\n"
    token = hashlib.sha256(contents).hexdigest()
    assert require_review_match(contents, token) == token


def test_changed_bytes_raise_reviewed_proposal_changed():
    reviewed = b"id: same-id\nvalue: first\n"
    token = hashlib.sha256(reviewed).hexdigest()
    with pytest.raises(ReviewedProposalChanged):
        require_review_match(b"id: same-id\nvalue: second\n", token)


def test_a_byte_only_rewrite_is_still_a_changed_review():
    reviewed = b"id: same-id\nvalue: first\n"
    token = hashlib.sha256(reviewed).hexdigest()
    with pytest.raises(ReviewedProposalChanged):
        require_review_match(b"id:  same-id\nvalue: first\n", token)


def test_an_invalid_token_is_never_a_changed_review():
    # The two outcomes are distinct: a malformed request must not be
    # presented to the operator as "someone rewrote your proposal".
    with pytest.raises(InvalidReviewToken) as raised:
        require_review_match(b"proposal", "not-a-hash")
    assert not isinstance(raised.value, ReviewedProposalChanged)


# --- the outcome types -------------------------------------------------------


def test_both_outcomes_share_one_base():
    assert issubclass(InvalidReviewToken, ReviewTokenError)
    assert issubclass(ReviewedProposalChanged, ReviewTokenError)
    assert InvalidReviewToken is not ReviewedProposalChanged


def test_exceptions_reveal_no_proposal_bytes_or_private_paths():
    secret = b"entity: private-person\npath: /vault-root/holder/secret.md\n"
    token = hashlib.sha256(b"reviewed").hexdigest()

    with pytest.raises(ReviewedProposalChanged) as changed:
        require_review_match(secret, token)
    with pytest.raises(InvalidReviewToken) as invalid:
        require_review_match(secret, "nope")

    for raised in (changed.value, invalid.value):
        rendered = f"{raised!r} {raised}"
        assert "private-person" not in rendered
        assert "/vault-root/" not in rendered
        assert "secret.md" not in rendered
        assert token not in rendered


# --- the module's place in the architecture ----------------------------------


def test_the_module_knows_nothing_about_routes_storage_or_presentation():
    """A shared domain primitive: stdlib only, no app imports, no I/O.

    If this module could reach the vault, a route, or the taxonomy, the
    "hash exactly the bytes you were handed" contract would stop being
    checkable in one place.
    """
    source = (_REPO_ROOT / "app" / "review_tokens.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                imported.add("<relative>")
            if node.module:
                imported.add(node.module.split(".")[0])

    assert imported <= {"__future__", "dataclasses", "hashlib", "re", "typing"}, (
        f"unexpected imports: {sorted(imported)}"
    )
