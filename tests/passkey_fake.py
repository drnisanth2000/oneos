"""A synthetic platform authenticator for tests. Never a real device.

It holds one EC P-256 (ES256) key made with `cryptography` and answers
registration and sign-in options the way a browser's `PublicKeyCredential`
`toJSON()` would: base64url fields, a `clientDataJSON` with type, challenge
and origin, and authenticator data with the RP ID hash, flags and counter.
Every field a negative test needs to break is a keyword argument.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import struct

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

ORIGIN = "https://localhost:8443"
UP, UV, AT = 0x01, 0x04, 0x40


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64url(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def options_challenge(options_json: str) -> bytes:
    return unb64url(json.loads(options_json)["challenge"])


def _options_rp_id(options: dict) -> str:
    return options["rp"]["id"] if "rp" in options else options["rpId"]


class FakeAuthenticator:
    def __init__(self, *, credential_id: bytes | None = None, user_handle: bytes = b"oneos-owner"):
        self.credential_id = os.urandom(16) if credential_id is None else credential_id
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.user_handle = user_handle

    @property
    def id(self) -> str:
        return b64url(self.credential_id)

    def _cose_key(self) -> bytes:
        numbers = self.key.public_key().public_numbers()
        return cbor2.dumps({1: 2, 3: -7, -1: 1, -2: numbers.x.to_bytes(32, "big"), -3: numbers.y.to_bytes(32, "big")})

    @staticmethod
    def _client_data(kind, challenge, origin):
        return json.dumps({"type": kind, "challenge": b64url(challenge), "origin": origin, "crossOrigin": False}).encode()

    @staticmethod
    def _flags(up, uv, extra=0):
        return (UP if up else 0) | (UV if uv else 0) | extra

    def register(self, options_json: str, *, origin: str = ORIGIN, rp_id: str | None = None,
                 challenge: bytes | None = None, up: bool = True, uv: bool = True,
                 sign_count: int = 0, kind: str = "webauthn.create", fmt: str = "none") -> str:
        options = json.loads(options_json)
        rp_id = _options_rp_id(options) if rp_id is None else rp_id
        challenge = unb64url(options["challenge"]) if challenge is None else challenge
        auth_data = (
            hashlib.sha256(rp_id.encode()).digest()
            + bytes([self._flags(up, uv, AT)])
            + struct.pack(">I", sign_count)
            + bytes(16)  # AAGUID: all zeros, as with "none" attestation
            + struct.pack(">H", len(self.credential_id)) + self.credential_id
            + self._cose_key()
        )
        attestation = cbor2.dumps({"fmt": fmt, "attStmt": {}, "authData": auth_data})
        return json.dumps({
            "id": self.id, "rawId": self.id, "type": "public-key",
            "response": {
                "clientDataJSON": b64url(self._client_data(kind, challenge, origin)),
                "attestationObject": b64url(attestation),
                "transports": ["internal"],
            },
            "authenticatorAttachment": "platform", "clientExtensionResults": {},
        })

    def assertion(self, options_json: str, *, origin: str = ORIGIN, rp_id: str | None = None,
                  challenge: bytes | None = None, up: bool = True, uv: bool = True,
                  sign_count: int = 0, kind: str = "webauthn.get",
                  signer: ec.EllipticCurvePrivateKey | None = None, flip_signature: bool = False,
                  user_handle: bytes | None = None) -> str:
        options = json.loads(options_json)
        rp_id = _options_rp_id(options) if rp_id is None else rp_id
        challenge = unb64url(options["challenge"]) if challenge is None else challenge
        auth_data = hashlib.sha256(rp_id.encode()).digest() + bytes([self._flags(up, uv)]) + struct.pack(">I", sign_count)
        client_data = self._client_data(kind, challenge, origin)
        signature = (signer or self.key).sign(auth_data + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256()))
        if flip_signature:
            signature = signature[:-1] + bytes([signature[-1] ^ 0x01])
        return json.dumps({
            "id": self.id, "rawId": self.id, "type": "public-key",
            "response": {
                "clientDataJSON": b64url(client_data),
                "authenticatorData": b64url(auth_data),
                "signature": b64url(signature),
                "userHandle": b64url(self.user_handle if user_handle is None else user_handle),
            },
            "authenticatorAttachment": "platform", "clientExtensionResults": {},
        })
