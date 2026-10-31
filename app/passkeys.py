"""Passkey options and verification: a thin wrapper over `webauthn`. No SQL here.

`app/auth.py` owns challenges, stored passkeys and the throttle. Every check
that fails here raises `PasskeyRejected`, whatever went wrong underneath, so
callers can refuse without telling bad input from a bad signature.
"""
from __future__ import annotations

import base64
import binascii
import json
import re
from urllib.parse import urlsplit

from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import (
    bytes_to_base64url,
    decode_credential_public_key,
    decoded_public_key_to_cryptography,
    parse_authentication_credential_json,
)
from webauthn.helpers.cose import COSEAlgorithmIdentifier
from webauthn.helpers.structs import (
    AttestationConveyancePreference,
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

RP_NAME = "OneOS"
USER_ID = b"oneos-owner"
USER_NAME = "owner"
USER_DISPLAY_NAME = "OneOS owner"
CHALLENGE_BYTES = 32
ALGORITHMS = [COSEAlgorithmIdentifier.ECDSA_SHA_256, COSEAlgorithmIdentifier.RSASSA_PKCS1_v1_5_SHA_256]
MAX_RESPONSE_CHARS = 64 * 1024


class PasskeyRejected(ValueError):
    """A passkey answer, or the origin it is checked against, is not acceptable."""


def _strict_base64url(value) -> bytes:
    """Decode canonical unpadded base64url only; `webauthn`'s own decoder skips stray characters."""
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]*", value) or len(value) % 4 == 1:
        raise PasskeyRejected("not base64url")
    try:
        data = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (binascii.Error, ValueError) as exc:
        raise PasskeyRejected("not base64url") from exc
    if bytes_to_base64url(data) != value:
        raise PasskeyRejected("not canonical base64url")
    return data


def _load(response_json) -> dict:
    if not isinstance(response_json, str) or len(response_json) > MAX_RESPONSE_CHARS:
        raise PasskeyRejected("answer missing or too large")
    try:
        value = json.loads(response_json)
    except (ValueError, RecursionError) as exc:
        raise PasskeyRejected("answer is not JSON") from exc
    if not isinstance(value, dict):
        raise PasskeyRejected("answer is not an object")
    return value


def response_challenge(response_json) -> bytes:
    """The 32-byte challenge the answer's `clientDataJSON` claims to answer (not yet verified)."""
    response = _load(response_json).get("response")
    if not isinstance(response, dict):
        raise PasskeyRejected("answer has no response")
    try:
        client_data = json.loads(_strict_base64url(response.get("clientDataJSON")))
    except (ValueError, RecursionError) as exc:
        raise PasskeyRejected("clientDataJSON is not JSON") from exc
    if not isinstance(client_data, dict):
        raise PasskeyRejected("clientDataJSON is not an object")
    challenge = _strict_base64url(client_data.get("challenge"))
    if len(challenge) != CHALLENGE_BYTES:
        raise PasskeyRejected("challenge has the wrong length")
    return challenge


def response_credential_id(response_json) -> str:
    """The answer's credential id in the stored (canonical base64url) form."""
    value = _load(response_json).get("id")
    _strict_base64url(value)
    return value


def rp_from_origin(origin) -> tuple[str, str]:
    """(RP ID, expected origin): the origin's hostname, and the origin itself, checked exactly."""
    try:
        parsed = urlsplit(origin)
        hostname = parsed.hostname
    except (TypeError, ValueError, AttributeError) as exc:
        raise PasskeyRejected("origin is not a URL") from exc
    if (not isinstance(origin, str) or parsed.scheme != "https" or not parsed.netloc or not hostname
            or parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password):
        raise PasskeyRejected("origin must be https://host[:port] only")
    return hostname, origin


def _check_challenge(challenge):
    if type(challenge) is not bytes or len(challenge) != CHALLENGE_BYTES:
        raise ValueError("a challenge is exactly 32 bytes")


def registration_options(rp_id: str, challenge: bytes, exclude: list[bytes]) -> str:
    _check_challenge(challenge)
    return options_to_json(generate_registration_options(
        rp_id=rp_id,
        rp_name=RP_NAME,
        user_id=USER_ID,
        user_name=USER_NAME,
        user_display_name=USER_DISPLAY_NAME,
        challenge=challenge,
        attestation=AttestationConveyancePreference.NONE,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
        exclude_credentials=[PublicKeyCredentialDescriptor(id=key_id) for key_id in exclude],
        supported_pub_key_algs=ALGORITHMS,
    ))


def authentication_options(rp_id: str, challenge: bytes) -> str:
    _check_challenge(challenge)
    return options_to_json(generate_authentication_options(
        rp_id=rp_id,
        challenge=challenge,
        allow_credentials=[],
        user_verification=UserVerificationRequirement.REQUIRED,
    ))


def verify_registration(response_json: str, challenge: bytes, rp_id: str, origin: str) -> tuple[bytes, bytes, int]:
    """(credential id, COSE public key, sign count) of a verified registration."""
    try:
        _check_challenge(challenge)
        answer = _load(response_json)
        verified = verify_registration_response(
            credential=answer,
            expected_challenge=challenge,
            expected_rp_id=rp_id,
            expected_origin=origin,
            require_user_presence=True,
            require_user_verification=True,
            supported_pub_key_algs=ALGORITHMS,
        )
        # The id the browser reports is the one it will sign in with later.
        if bytes_to_base64url(verified.credential_id) != answer.get("id"):
            raise PasskeyRejected("credential id differs from the attested one")
        # "none" attestation signs nothing, so make sure the key is usable now.
        decoded_public_key_to_cryptography(decode_credential_public_key(verified.credential_public_key))
        if type(verified.sign_count) is not int or verified.sign_count < 0:
            raise PasskeyRejected("bad sign count")
        return verified.credential_id, verified.credential_public_key, verified.sign_count
    except PasskeyRejected:
        raise
    except Exception as exc:  # any failure inside webauthn is a refusal
        raise PasskeyRejected("registration not verified") from exc


def verify_authentication(response_json: str, challenge: bytes, rp_id: str, origin: str,
                          public_key: bytes, sign_count: int) -> int:
    """The new sign count of a verified sign-in with user verification."""
    try:
        _check_challenge(challenge)
        credential = parse_authentication_credential_json(_load(response_json))
        user_handle = credential.response.user_handle
        if user_handle is not None and user_handle != USER_ID:
            raise PasskeyRejected("passkey belongs to another user")
        verified = verify_authentication_response(
            credential=credential,
            expected_challenge=challenge,
            expected_rp_id=rp_id,
            expected_origin=origin,
            credential_public_key=public_key,
            credential_current_sign_count=sign_count,
            require_user_verification=True,
        )
        received = verified.new_sign_count
        # Counter rule, checked here too rather than trusting the library alone:
        # 0 and 0 is a synced passkey; otherwise the counter must move forward.
        if type(received) is not int or received < 0 or (not (received == 0 and sign_count == 0) and received <= sign_count):
            raise PasskeyRejected("sign counter did not increase")
        if not verified.user_verified:
            raise PasskeyRejected("user not verified")
        return received
    except PasskeyRejected:
        raise
    except Exception as exc:  # any failure inside webauthn is a refusal
        raise PasskeyRejected("sign-in not verified") from exc
