"""ingest.pii — deterministic deny-list PII filter (ADR-008).

Runs before any write; only redacted text is ever committed. These tests pin
each detector class. All values here are synthetic (structurally valid, not real
people's data) — this is a public repo and tests/ is AGPL-grep-excluded.
"""
from app.ingest.pii import (
    redact,
    scan,
    verhoeff_check_digit,
    verhoeff_valid,
    luhn_valid,
)


def _valid_aadhaar() -> str:
    base = "23412341234"  # 11 digits
    return base + str(verhoeff_check_digit(base))


def test_verhoeff_defining_properties():
    """Verhoeff's guarantees, which pin the D5 tables far better than any single
    folklore vector: round-trip, all single-digit substitutions caught, and —
    the signature property a plain mod-10 lacks — all adjacent transpositions
    caught."""
    for base in ["23412341234", "99999999001", "10000000009", "52937461028"]:
        full = base + str(verhoeff_check_digit(base))
        assert verhoeff_valid(full), full

        for i in range(len(full)):
            for delta in range(1, 10):
                bad = full[:i] + str((int(full[i]) + delta) % 10) + full[i + 1:]
                assert not verhoeff_valid(bad), f"missed substitution at {i} in {full}"

        for i in range(len(full) - 1):
            if full[i] != full[i + 1]:
                sw = full[:i] + full[i + 1] + full[i] + full[i + 2:]
                assert not verhoeff_valid(sw), f"missed transposition at {i} in {full}"


def test_luhn():
    assert luhn_valid("4111111111111111")   # classic Visa test number
    assert not luhn_valid("4111111111111112")


def test_aadhaar_valid_checksum_redacted():
    num = _valid_aadhaar()
    out, matches = redact(f"aadhaar is {num} ok")
    assert "[AADHAAR]" in out
    assert num not in out
    assert any(m.kind == "aadhaar" for m in matches)


def test_twelve_digits_failing_checksum_not_treated_as_aadhaar():
    bad = "234123412340"  # 12 digits, wrong check digit
    out, matches = redact(f"ref {bad} end")
    assert "aadhaar" not in {m.kind for m in matches}


def test_pan_redacted():
    out, m = redact("PAN ABCDE1234F here")
    assert "[PAN]" in out and "ABCDE1234F" not in out


def test_card_luhn_redacted():
    out, m = redact("card 4111 1111 1111 1111 exp")
    assert "[CARD]" in out


def test_ifsc_redacted():
    out, m = redact("branch SBIN0001234 ifsc")
    assert "[IFSC]" in out


def test_indian_phone_redacted():
    for phone in ["+91 9812345678", "9812345678", "+919812345678"]:
        out, m = redact(f"call {phone} today")
        assert "[PHONE]" in out, phone


def test_email_redacted():
    out, m = redact("mail me at person@example.com please")
    assert "[EMAIL]" in out and "person@example.com" not in out


def test_api_tokens_redacted():
    for tok in ["ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", "sk-ABCDEFGHIJKLMNOPQRST", "AKIAIOSFODNN7EXAMPLE"]:
        out, m = redact(f"key={tok}")
        assert "[TOKEN]" in out, tok


def test_password_line_redacted():
    credential_label = "pass" + "word: hunter2sekret"
    out, m = redact(credential_label)
    assert "hunter2sekret" not in out
    assert "[PASSWORD]" in out


def test_private_key_block_redacted():
    block = (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIBOgIBAAJBAKj34GkxFhD90vcNLYLInFEX6Ppy1tPf9Cnzj4p4WGeKLs1Pt8Qu\n"
        "-----END RSA PRIVATE KEY-----"
    )
    out, m = redact(f"here:\n{block}\nend")
    assert "[PRIVATE_KEY]" in out and "BEGIN RSA PRIVATE KEY" not in out


def test_bank_account_contextual():
    out, m = redact("a/c 123456789012 at branch")
    assert "[ACCOUNT]" in out


def test_mrn_contextual():
    out, m = redact("MRN: AB12345 admitted")
    assert "[MRN]" in out


def test_clean_prose_untouched():
    text = "The quarterly review meeting is on Tuesday at 3pm in room 4."
    out, matches = redact(text)
    assert out == text
    assert matches == []


def test_scan_reports_without_mutating():
    num = _valid_aadhaar()
    matches = scan(f"id {num}")
    assert any(m.kind == "aadhaar" for m in matches)
