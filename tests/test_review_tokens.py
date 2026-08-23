"""S7 Task 1 — the shared exact-byte review contract.

These tests pin the primitive that every reviewed action depends on: a
snapshot hashes the exact bytes it carries, a submitted fingerprint is
strictly validated before it can ever be compared, and neither failure
discloses proposal bytes or private paths.
"""
import ast
import dataclasses
import hashlib
import pathlib

import pytest

from app.review_tokens import (
    InvalidReviewToken,
    ReviewContractViolation,
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
        with pytest.raises(dataclasses.FrozenInstanceError):
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


# --- review findings S1, S4, F6, F8, F9 --------------------------------------


def test_a_snapshot_cannot_exist_whose_digest_is_not_of_its_own_bytes():
    """S1. The bytes-to-digest pairing holds for the type, not just the
    factory — so a direct construction cannot smuggle in a mismatch.

    This is the whole of what Task 1 proves. See
    `test_a_snapshot_cannot_prove_where_its_value_came_from` for the half
    that is deliberately out of reach here.
    """
    contents = b"id: same-id\nvalue: first\n"
    other = hashlib.sha256(b"different bytes entirely").hexdigest()

    with pytest.raises(ReviewContractViolation):
        ReviewSnapshot("validated", contents, other)

    # A well-formed but wrong-cased digest is equally not this snapshot's.
    with pytest.raises(ReviewContractViolation):
        ReviewSnapshot("validated", contents, hashlib.sha256(contents).hexdigest().upper())

    # The honest pairing still constructs.
    snap = ReviewSnapshot("validated", contents, hashlib.sha256(contents).hexdigest())
    assert snap.contents == contents


def test_a_snapshot_cannot_hold_mutable_contents():
    """S1. `bytearray` would defeat the frozen guarantee from the inside."""
    contents = bytearray(b"id: same-id\n")
    with pytest.raises(ReviewContractViolation):
        ReviewSnapshot("validated", contents, hashlib.sha256(contents).hexdigest())


@pytest.mark.parametrize("contents", [5, "a str", None, ["bytes"], object()])
def test_non_bytes_contents_are_refused_rather_than_coerced(contents):
    """S4. `bytes(5)` is five NUL bytes with a valid, self-consistent digest.

    Coercion would fingerprint the wrong data stably and forever instead of
    failing, so a wrong variable at a call site in Tasks 2-4 would produce a
    confident answer about bytes nobody ever read.
    """
    with pytest.raises(ReviewContractViolation):
        make_review_snapshot("validated", contents)
    with pytest.raises(ReviewContractViolation):
        require_review_match(contents, "0" * 64)


def test_a_contract_violation_is_neither_a_changed_review_nor_a_bad_request():
    with pytest.raises(ReviewContractViolation) as raised:
        make_review_snapshot("validated", "a str")
    assert not isinstance(raised.value, ReviewedProposalChanged)
    assert not isinstance(raised.value, InvalidReviewToken)
    assert isinstance(raised.value, ReviewTokenError)


def test_a_snapshot_cannot_prove_where_its_value_came_from():
    """The boundary of Task 1's guarantee, pinned so it is not overclaimed.

    `value` is opaque to this module, so a value parsed from one read can
    legally be carried alongside bytes and a digest from another. Nothing
    here can detect it. Proving `value` was parsed from `contents` is a
    property of the readers' capture-parse-hash sequence (spec
    §Architecture-1) and is established in Task 2 and Task 4.
    """
    bytes_a = b"id: same-id\nvalue: first\n"
    bytes_b = b"id: same-id\nvalue: second\n"
    value_from_a = make_review_snapshot("parsed-from-A", bytes_a).value

    mismatched = ReviewSnapshot(
        value_from_a, bytes_b, hashlib.sha256(bytes_b).hexdigest()
    )

    # Self-consistent bytes and digest; the value's lineage is simply not
    # visible from here. If this ever raises, the guarantee has grown and
    # the module and its docstrings must say so.
    assert mismatched.value == "parsed-from-A"
    assert mismatched.contents == bytes_b


def test_a_snapshot_verifies_against_its_own_fingerprint():
    """F9. The production seam Tasks 2-4 depend on: produce, then verify."""
    snap = make_review_snapshot("validated", b"id: same-id\nvalue: first\n")
    assert require_review_match(snap.contents, snap.sha256) == snap.sha256


def test_a_changed_review_does_not_disclose_the_current_bytes_digest():
    """F6. The reviewed digest is the operator's; the current one is not."""
    secret = b"entity: private-person\npath: /vault-root/holder/secret.md\n"
    token = hashlib.sha256(b"reviewed").hexdigest()

    with pytest.raises(ReviewedProposalChanged) as raised:
        require_review_match(secret, token)

    rendered = f"{raised.value!r} {raised.value}"
    assert hashlib.sha256(secret).hexdigest() not in rendered
    assert token not in rendered


def test_the_module_performs_no_file_access():
    """F8. `open` is a builtin and needs no import, so the import scan
    above cannot see it. The module's contract is: it hashes bytes it was
    handed, and never goes and gets bytes itself."""
    tree = ast.parse((_REPO_ROOT / "app" / "review_tokens.py").read_text(encoding="utf-8"))
    forbidden = {"open", "eval", "exec", "compile", "__import__"}
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert not (called & forbidden), f"module reaches for I/O: {sorted(called & forbidden)}"
