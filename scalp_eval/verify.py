"""Verification of signed recommendations (ADR 0001 section 5). L7 holds public keys only.

Signed bytes: DOMAIN + b"\\n" + canonical_json(payload), re-derived here from the contract,
not imported from scalp_engine (R4).
"""
import base64
import hashlib

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from scalp_eval.contracts import canonical_json, load_schema, validate

DOMAIN = b"scalp-engine/recommendation/v1"


def load_public_key(path):
    with open(path, "rb") as f:
        key = serialization.load_pem_public_key(f.read())
    if not isinstance(key, Ed25519PublicKey):
        raise TypeError("public key is not Ed25519")
    return key


def key_id(public_key):
    raw = public_key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return hashlib.sha256(raw).hexdigest()[:16]


def verify(envelope, trusted_keys):
    """Return the payload if the envelope is well-formed and signed by a trusted key; else raise.

    trusted_keys: iterable of Ed25519PublicKey."""
    validate(envelope, load_schema("signed_recommendation"))
    payload = envelope["payload"]
    validate(payload, load_schema("recommendation"))
    by_id = {key_id(k): k for k in trusted_keys}
    sig = envelope["signature"]
    key = by_id.get(sig["key_id"])
    if key is None:
        raise InvalidSignature(f"unknown key_id {sig['key_id']}")
    key.verify(base64.urlsafe_b64decode(sig["value"] + "=="), DOMAIN + b"\n" + canonical_json(payload))
    return payload
