"""The exact-byte review contract shared by every reviewed action.

A proposal id names a mutable file. It is not evidence that the file still
holds the bytes an operator reviewed. This module owns the one primitive that
closes that gap: a review is captured as an immutable snapshot of exact bytes
plus their SHA-256, and an action may proceed only while the current bytes
still hash to the fingerprint the operator was shown.

The digest is a change detector, not a secret. It is not a password, a
capability, or proof of attention, so there is no signing, storage, session,
or dependency here — and there must never be. Keeping this module free of
route, storage, and presentation knowledge is what makes "parse and hash the
same single read" checkable in one place.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")

#: A submitted fingerprint is accepted only as lowercase 64-character
#: hexadecimal. `fullmatch` is what refuses a trailing newline: it must consume
#: the whole string, and a zero-width `$` cannot absorb the `\n`. The explicit
#: `\Z` is belt and braces, and keeps the refusal if `fullmatch` is ever
#: weakened to `match`.
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class ReviewTokenError(Exception):
    """Base for review-fingerprint outcomes. Never raised directly."""


class ReviewContractViolation(ReviewTokenError):
    """A caller broke this module's contract: an internal defect.

    Distinct from the two operator-facing outcomes on purpose. It is not a
    changed review (nobody rewrote anything) and not a bad request (the
    operator submitted nothing wrong) — it means OneOS handed this module
    something it must never hand it.
    """


class InvalidReviewToken(ReviewTokenError):
    """The submitted fingerprint is missing or malformed: an invalid request.

    This is never a legacy fallback. An action that cannot present a
    well-formed fingerprint has not been reviewed.
    """


class ReviewedProposalChanged(ReviewTokenError):
    """The stored bytes no longer match the reviewed fingerprint."""


@dataclass(frozen=True)
class ReviewSnapshot(Generic[T]):
    """One validated value, the exact bytes it was parsed from, their digest.

    The three fields travel together so no caller can pair a value parsed
    from one read with a digest taken from another.
    """

    value: T
    contents: bytes
    sha256: str

    def __post_init__(self) -> None:
        # Tasks 2-4 build these inside the outbox and registry readers, which
        # is where "parse one read, hash another" can be reintroduced. Making
        # the inconsistent value unconstructible keeps spec §Architecture-1
        # checkable here rather than at every call site.
        if type(self.contents) is not bytes:
            raise ReviewContractViolation("snapshot contents must be immutable bytes")
        if hashlib.sha256(self.contents).hexdigest() != self.sha256:
            raise ReviewContractViolation("snapshot digest is not of its own bytes")


def _require_bytes(contents: object) -> bytes:
    """Copy `contents` to immutable bytes, refusing anything not byte-like.

    `bytes(5)` is five NUL bytes with a perfectly valid, self-consistent
    digest. Coercing would fingerprint data nobody ever read, stably and
    forever, instead of failing — so a wrong variable at a call site would
    produce a confident answer about the wrong thing.
    """
    if not isinstance(contents, (bytes, bytearray, memoryview)):
        raise ReviewContractViolation("review contents must be bytes")
    return bytes(contents)         # copy: a caller's buffer must not drift


def make_review_snapshot(value: T, contents: bytes) -> ReviewSnapshot[T]:
    """Snapshot `value` against the exact `contents` it was validated from."""
    raw = _require_bytes(contents)
    return ReviewSnapshot(value, raw, hashlib.sha256(raw).hexdigest())


def require_review_match(contents: bytes, submitted: object) -> str:
    """Return `submitted` only if it is the digest of exactly `contents`.

    Raises `InvalidReviewToken` for anything that is not a well-formed
    lowercase SHA-256, and `ReviewedProposalChanged` when a well-formed
    fingerprint names different bytes. Neither message carries proposal
    bytes, paths, or the digests themselves.
    """
    raw = _require_bytes(contents)  # the caller's own contract, checked first
    if not isinstance(submitted, str) or _SHA256.fullmatch(submitted) is None:
        raise InvalidReviewToken("invalid review fingerprint")
    if hashlib.sha256(raw).hexdigest() != submitted:
        raise ReviewedProposalChanged("proposal changed since review")
    return submitted
