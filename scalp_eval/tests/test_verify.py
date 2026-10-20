"""B4 cross-boundary round trip: the engine signs, L7 verifies, with a throwaway key made at test time.

R4 forbids importing scalp_engine here, so the engine is driven the way L7 will meet it in
production: as a separate process that hands over a signed JSON file."""
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from scalp_eval import contracts as c
from scalp_eval import verify as v

EXAMPLE = c.CONTRACTS / "examples" / "recommendation.example.json"


class CrossProcessRoundTrip(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = Path(cls.tmp.name)
        cls.key = Ed25519PrivateKey.generate()
        priv = d / "throwaway.pem"
        priv.write_bytes(cls.key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                               serialization.NoEncryption()))
        out = d / "signed.json"
        env = {k: val for k, val in os.environ.items() if not k.startswith("SCALP_ENGINE_SIGNING_KEY")}
        subprocess.run([sys.executable, "-m", "scalp_engine.signing", "sign", "--in", str(EXAMPLE),
                        "--out", str(out), "--key", str(priv)],
                       cwd=c.REPO_ROOT, env=env, check=True, capture_output=True, timeout=60)
        cls.envelope = json.loads(out.read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_engine_signature_verifies(self):
        payload = v.verify(self.envelope, [self.key.public_key()])
        self.assertEqual(payload, c.load_json(EXAMPLE))

    def test_tampered_payload_fails(self):
        env = copy.deepcopy(self.envelope)
        env["payload"]["target_price"] = "150.00"
        with self.assertRaises(InvalidSignature):
            v.verify(env, [self.key.public_key()])

    def test_untrusted_key_fails(self):
        with self.assertRaises(InvalidSignature):
            v.verify(self.envelope, [Ed25519PrivateKey.generate().public_key()])

    def test_malformed_envelope_fails(self):
        env = copy.deepcopy(self.envelope)
        env["signature"]["alg"] = "HS256"
        with self.assertRaises(c.ContractError):
            v.verify(env, [self.key.public_key()])
        env = copy.deepcopy(self.envelope)
        env["payload"]["qty"] = 1.5
        with self.assertRaises(c.ContractError):
            v.verify(env, [self.key.public_key()])


if __name__ == "__main__":
    unittest.main()


class Artifact(unittest.TestCase):
    def test_verify_artifact_roundtrip_and_tamper(self):
        import base64
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from scalp_eval.contracts import canonical_json
        from scalp_eval.verify import ARTIFACT_DOMAIN, key_id, verify_artifact
        key = Ed25519PrivateKey.generate()
        payload = {"kind": "insider_event_set", "table": "insider_events", "rule_version": "x", "rows": 3, "sha256": "b" * 64}
        sig = key.sign(ARTIFACT_DOMAIN + b"\n" + canonical_json(payload))
        env = {"schema_version": 1, "payload": payload,
               "signature": {"alg": "Ed25519", "canonicalization": "RFC8785-ascii-nofloat", "key_id": key_id(key.public_key()),
                             "value": base64.urlsafe_b64encode(sig).rstrip(b"=").decode("ascii")}}
        self.assertEqual(verify_artifact(env, [key.public_key()]), payload)
        env["payload"] = dict(payload, rows=4)
        with self.assertRaises(Exception):
            verify_artifact(env, [key.public_key()])
        with self.assertRaises(Exception):
            verify_artifact(dict(env, payload=payload), [Ed25519PrivateKey.generate().public_key()])
