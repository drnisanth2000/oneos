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
#: hexadecimal. `fullmatch` plus the explicit `\Z` refuses a trailing newline,
#: which `$` would allow.
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class ReviewTokenError(Exception):
    """Base for review-fingerprint outcomes. Never raised directly."""


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


def make_review_snapshot(value: T, contents: bytes) -> ReviewSnapshot[T]:
    """Snapshot `value` against the exact `contents` it was validated from."""
    raw = bytes(contents)          # copy: a caller's buffer must not drift
    return ReviewSnapshot(value, raw, hashlib.sha256(raw).hexdigest())


def require_review_match(contents: bytes, submitted: object) -> str:
    """Return `submitted` only if it is the digest of exactly `contents`.

    Raises `InvalidReviewToken` for anything that is not a well-formed
    lowercase SHA-256, and `ReviewedProposalChanged` when a well-formed
    fingerprint names different bytes. Neither message carries proposal
    bytes, paths, or the digests themselves.
    """
    if not isinstance(submitted, str) or _SHA256.fullmatch(submitted) is None:
        raise InvalidReviewToken("invalid review fingerprint")
    if hashlib.sha256(contents).hexdigest() != submitted:
        raise ReviewedProposalChanged("proposal changed since review")
    return submitted
