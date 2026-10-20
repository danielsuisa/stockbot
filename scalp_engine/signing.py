"""Ed25519 signing of recommendations (ADR 0001 section 5).

Signed bytes: DOMAIN + b"\\n" + canonical_json(payload). The private key is a PKCS#8 PEM file
outside the repository; its path comes from --key or SCALP_ENGINE_SIGNING_KEY, and an optional
passphrase from SCALP_ENGINE_SIGNING_KEY_PASSPHRASE. L7 verifies with the public key only.

CLI:
  python -m scalp_engine.signing keygen --out PRIVATE.pem --pub-out PUBLIC.pem
  python -m scalp_engine.signing sign --in payload.json --out signed.json [--key PRIVATE.pem]
"""
import argparse
import base64
import hashlib
import json
import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from scalp_engine.contracts import REPO_ROOT, canonical_json, load_schema, validate

DOMAIN = b"scalp-engine/recommendation/v1"
CANONICALIZATION = "RFC8785-ascii-nofloat"
KEY_ENV = "SCALP_ENGINE_SIGNING_KEY"
PASS_ENV = "SCALP_ENGINE_SIGNING_KEY_PASSPHRASE"


def _outside_repo(path):
    p = Path(path).resolve()
    if p == REPO_ROOT or REPO_ROOT in p.parents:
        raise PermissionError(f"refusing key path inside the repository: {p}")
    return p


def key_id(public_key):
    raw = public_key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return hashlib.sha256(raw).hexdigest()[:16]


def message(payload):
    return DOMAIN + b"\n" + canonical_json(payload)


def sign(payload, private_key):
    """Validate payload against recommendation.schema.json and return the signed envelope."""
    validate(payload, load_schema("recommendation"))
    sig = private_key.sign(message(payload))
    envelope = {
        "schema_version": 1,
        "payload": payload,
        "signature": {
            "alg": "Ed25519",
            "canonicalization": CANONICALIZATION,
            "key_id": key_id(private_key.public_key()),
            "value": base64.urlsafe_b64encode(sig).rstrip(b"=").decode("ascii"),
        },
    }
    validate(envelope, load_schema("signed_recommendation"))
    return envelope


def verify(envelope, public_key):
    """Self-check for the engine side. The authoritative verifier is scalp_eval.verify."""
    validate(envelope, load_schema("signed_recommendation"))
    validate(envelope["payload"], load_schema("recommendation"))
    sig = envelope["signature"]
    if sig["key_id"] != key_id(public_key):
        return False
    try:
        public_key.verify(base64.urlsafe_b64decode(sig["value"] + "=="), message(envelope["payload"]))
    except Exception:
        return False
    return True


ARTIFACT_DOMAIN = b"scalp-engine/artifact/v1"


def sign_artifact(payload, private_key):
    """Sign an artifact digest (ADR 0010 section 1.1): payload validates against artifact_digest.schema.json; the
    signed bytes are ARTIFACT_DOMAIN + b"\n" + canonical_json(payload)."""
    validate(payload, load_schema("artifact_digest"))
    sig = private_key.sign(ARTIFACT_DOMAIN + b"\n" + canonical_json(payload))
    envelope = {"schema_version": 1, "payload": payload,
                "signature": {"alg": "Ed25519", "canonicalization": CANONICALIZATION,
                              "key_id": key_id(private_key.public_key()),
                              "value": base64.urlsafe_b64encode(sig).rstrip(b"=").decode("ascii")}}
    validate(envelope, load_schema("signed_artifact"))
    return envelope


def load_private_key(path=None):
    path = path or os.environ.get(KEY_ENV)
    if not path:
        raise RuntimeError(f"no signing key: pass --key or set {KEY_ENV}")
    p = _outside_repo(path)
    pw = os.environ.get(PASS_ENV)
    key = serialization.load_pem_private_key(p.read_bytes(), pw.encode() if pw else None)
    if not isinstance(key, Ed25519PrivateKey):
        raise TypeError("signing key is not Ed25519")
    return key


def keygen(out, pub_out):
    priv_path, pub_path = _outside_repo(out), Path(pub_out)
    if priv_path.exists():
        raise FileExistsError(priv_path)
    key = Ed25519PrivateKey.generate()
    pw = os.environ.get(PASS_ENV)
    enc = serialization.BestAvailableEncryption(pw.encode()) if pw else serialization.NoEncryption()
    priv_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, enc))
    pub_path.write_bytes(key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    return key_id(key.public_key())


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m scalp_engine.signing")
    sub = ap.add_subparsers(dest="cmd", required=True)
    k = sub.add_parser("keygen")
    k.add_argument("--out", required=True)
    k.add_argument("--pub-out", required=True)
    s = sub.add_parser("sign")
    s.add_argument("--in", dest="inp", required=True)
    s.add_argument("--out", required=True)
    s.add_argument("--key")
    a = ap.parse_args(argv)
    if a.cmd == "keygen":
        print(keygen(a.out, a.pub_out))
        return 0
    payload = json.loads(Path(a.inp).read_text(encoding="utf-8"))
    envelope = sign(payload, load_private_key(a.key))
    Path(a.out).write_text(json.dumps(envelope, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
