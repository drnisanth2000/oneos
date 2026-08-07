"""pii.py — deterministic deny-list PII filter (ADR-008).

Runs inside every adapter, before the normalised envelope is written. No LLM
(deterministic before LLM; a later model pass may only quarantine *more*, never
release). Structured classes are checksum/format validated to cut false
positives; contextual classes match on a nearby keyword. Any match redacts the
span to a class token and is reported so the adapter can quarantine.

The known gap (ADR-008) is unstructured PII — a patient named in prose has no
pattern. That is handled structurally elsewhere: the vault stores a summary,
not the body.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# --- checksums -------------------------------------------------------------

# Verhoeff tables (dihedral group D5) — used to validate Aadhaar numbers.
_VERHOEFF_D = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    [1, 2, 3, 4, 0, 6, 7, 8, 9, 5],
    [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
    [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
    [4, 0, 1, 2, 3, 9, 5, 6, 7, 8],
    [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
    [6, 5, 9, 8, 7, 1, 0, 4, 3, 2],
    [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
    [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
    [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
]
# The standard 8-row permutation table (index by position mod 8).
_VERHOEFF_P = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    [1, 5, 7, 6, 2, 8, 3, 0, 9, 4],
    [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
    [8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
    [9, 4, 5, 3, 1, 2, 6, 8, 7, 0],
    [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
    [2, 7, 9, 3, 8, 0, 6, 4, 1, 5],
    [7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
]
_VERHOEFF_INV = [0, 4, 3, 2, 1, 5, 6, 7, 8, 9]


def verhoeff_valid(number: str) -> bool:
    """True if the digit string satisfies the Verhoeff checksum."""
    if not number.isdigit():
        return False
    c = 0
    for i, ch in enumerate(reversed(number)):
        c = _VERHOEFF_D[c][_VERHOEFF_P[i % 8][int(ch)]]
    return c == 0


def verhoeff_check_digit(number: str) -> int:
    """The check digit that makes `number + digit` Verhoeff-valid."""
    c = 0
    for i, ch in enumerate(reversed(number)):
        c = _VERHOEFF_D[c][_VERHOEFF_P[(i + 1) % 8][int(ch)]]
    return _VERHOEFF_INV[c]


def luhn_valid(number: str) -> bool:
    digits = [int(d) for d in number if d.isdigit()]
    if len(digits) < 12:
        return False
    total, alt = 0, False
    for d in reversed(digits):
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


# --- matches ---------------------------------------------------------------

@dataclass(frozen=True)
class PIIMatch:
    kind: str
    start: int
    end: int
    text: str


# --- detectors -------------------------------------------------------------
#
# Each detector yields (start, end) spans over `text`. Order matters only for
# overlapping redaction; we resolve overlaps by earliest-start, longest-span.

_PAN_RE = re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b")
_IFSC_RE = re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b")
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PHONE_RE = re.compile(r"(?<![\d])(?:\+91[\s-]?)?[6-9]\d{9}(?![\d])")
_AADHAAR_RE = re.compile(r"(?<![\d])\d{4}\s?\d{4}\s?\d{4}(?![\d])")
_CARD_RE = re.compile(r"(?<![\d])(?:\d[ -]?){13,19}(?![\d])")
_ACCOUNT_RE = re.compile(r"(?i)\b(?:a/c|acct?|account)\b[^\d]{0,12}(\d{9,18})\b")
_MRN_RE = re.compile(r"(?i)\b(?:MRN|UHID|patient\s*id)\b\s*[:#]?\s*([A-Z0-9][A-Z0-9-]{3,})")
_PASSWORD_RE = re.compile(r"(?i)\b(?:password|passwd|pwd)\b\s*[:=]\s*(\S+)")
_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
    re.DOTALL,
)
_TOKEN_PREFIX_RE = re.compile(
    r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}"
    r"|sk-[A-Za-z0-9]{16,}"
    r"|xox[baprs]-[A-Za-z0-9-]{10,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|AIza[0-9A-Za-z_\-]{35})\b"
)


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def scan(text: str) -> list[PIIMatch]:
    """All PII spans in `text`, resolved so redaction never double-counts."""
    raw: list[PIIMatch] = []

    def add(kind, m, group=0):
        raw.append(PIIMatch(kind, m.start(group), m.end(group), m.group(group)))

    for m in _PRIVATE_KEY_RE.finditer(text):
        add("private_key", m)
    for m in _TOKEN_PREFIX_RE.finditer(text):
        add("token", m)
    for m in _EMAIL_RE.finditer(text):
        add("email", m)
    for m in _PAN_RE.finditer(text):
        add("pan", m)
    for m in _IFSC_RE.finditer(text):
        add("ifsc", m)
    for m in _AADHAAR_RE.finditer(text):
        if verhoeff_valid(_digits(m.group(0))):
            add("aadhaar", m)
    for m in _CARD_RE.finditer(text):
        if luhn_valid(m.group(0)):
            add("card", m)
    for m in _PHONE_RE.finditer(text):
        add("phone", m)
    for m in _ACCOUNT_RE.finditer(text):
        add("account", m, 1)
    for m in _MRN_RE.finditer(text):
        add("mrn", m, 1)
    for m in _PASSWORD_RE.finditer(text):
        add("password", m, 1)

    # Resolve overlaps: keep earliest start, then longest span. This stops a
    # card run from also being redacted as a phone, etc.
    raw.sort(key=lambda x: (x.start, -(x.end - x.start)))
    resolved: list[PIIMatch] = []
    cursor = -1
    for match in raw:
        if match.start >= cursor:
            resolved.append(match)
            cursor = match.end
    return resolved


_TOKENS = {
    "aadhaar": "[AADHAAR]", "pan": "[PAN]", "card": "[CARD]", "ifsc": "[IFSC]",
    "account": "[ACCOUNT]", "phone": "[PHONE]", "email": "[EMAIL]",
    "token": "[TOKEN]", "password": "[PASSWORD]", "private_key": "[PRIVATE_KEY]",
    "mrn": "[MRN]",
}


def redact(text: str) -> tuple[str, list[PIIMatch]]:
    """Return (redacted_text, matches). Only the redacted text is safe to
    write. `matches` is non-empty exactly when the item must be quarantined."""
    matches = scan(text)
    if not matches:
        return text, []
    out = []
    cursor = 0
    for m in matches:
        out.append(text[cursor:m.start])
        out.append(_TOKENS.get(m.kind, "[REDACTED]"))
        cursor = m.end
    out.append(text[cursor:])
    return "".join(out), matches
