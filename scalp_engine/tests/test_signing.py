"""B4 signing: sign-then-verify round trip with a throwaway key generated at test time."""
import copy
import os
import tempfile
import unittest
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from scalp_engine import contracts as c
from scalp_engine import signing as s

EXAMPLE = c.CONTRACTS / "examples" / "recommendation.example.json"


class RoundTrip(unittest.TestCase):
    def setUp(self):
        self.key = Ed25519PrivateKey.generate()  # throwaway, never written inside the repo
        self.rec = c.load_json(EXAMPLE)

    def test_sign_then_verify(self):
        env = s.sign(self.rec, self.key)
        self.assertTrue(s.verify(env, self.key.public_key()))
        self.assertEqual(env["signature"]["key_id"], s.key_id(self.key.public_key()))

    def test_signature_is_deterministic(self):
        self.assertEqual(s.sign(self.rec, self.key), s.sign(copy.deepcopy(self.rec), self.key))

    def test_tampering_or_wrong_key_fails(self):
        env = s.sign(self.rec, self.key)
        tampered = copy.deepcopy(env)
        tampered["payload"]["qty"] = 121
        self.assertFalse(s.verify(tampered, self.key.public_key()))
        self.assertFalse(s.verify(env, Ed25519PrivateKey.generate().public_key()))

    def test_invalid_payload_is_not_signed(self):
        bad = dict(self.rec, stop_price=139.85)
        with self.assertRaises(c.ContractError):
            s.sign(bad, self.key)


class KeyStorage(unittest.TestCase):
    def test_key_paths_inside_repo_are_refused(self):
        with self.assertRaises(PermissionError):
            s.load_private_key(str(c.REPO_ROOT / "scalp_engine" / "key.pem"))
        with self.assertRaises(PermissionError):
            s.keygen(c.REPO_ROOT / "key.pem", c.REPO_ROOT / "key.pub.pem")

    def test_keygen_and_load_outside_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            priv, pub = Path(tmp) / "k.pem", Path(tmp) / "k.pub.pem"
            os.environ.pop(s.PASS_ENV, None)
            kid = s.keygen(priv, pub)
            key = s.load_private_key(str(priv))
            self.assertEqual(s.key_id(key.public_key()), kid)
            with self.assertRaises(FileExistsError):
                s.keygen(priv, pub)

    def test_missing_key_is_an_error(self):
        saved = os.environ.pop(s.KEY_ENV, None)
        try:
            with self.assertRaises(RuntimeError):
                s.load_private_key()
        finally:
            if saved is not None:
                os.environ[s.KEY_ENV] = saved


if __name__ == "__main__":
    unittest.main()
