"""Phase B.1 (spec section 10): the equity store, a bot message pinned in the owner's private chat. Every value here
is synthetic (E = 31,337.17); no real equity appears in the repository."""
import os
import unittest
from unittest import mock

from bot import common, equity

SYNTHETIC_E = 31_337.17  # synthetic, for tests only


class FakeTelegram:
    """A stand-in for common.tg: a private chat, a bot with id 42, pins kept newest first."""

    def __init__(self, chat_type="private", fail=None):
        self.chat_type, self.fail, self.calls, self.pins, self.next_id = chat_type, fail or set(), [], [], 100
        self.messages = {}

    def __call__(self, method, **p):
        self.calls.append((method, p))
        if method in self.fail:
            raise RuntimeError("telegram down")
        if method == "getMe":
            return {"id": 42, "is_bot": True}
        if method == "getChat":
            chat = {"id": 7, "type": self.chat_type}
            if self.pins:
                chat["pinned_message"] = self.messages[self.pins[0]]
            return chat
        if method == "sendMessage":
            self.next_id += 1
            self.messages[self.next_id] = {"message_id": self.next_id, "from": {"id": 42, "is_bot": True},
                                           "date": 1_791_000_000 + self.next_id, "text": p["text"]}
            return self.messages[self.next_id]
        if method == "pinChatMessage":
            self.pins.insert(0, p["message_id"])
            return True
        if method == "unpinChatMessage":
            self.pins = [m for m in self.pins if m != p["message_id"]]
            return True
        if method == "deleteMessage":
            self.messages.pop(p["message_id"], None)
            return True
        raise AssertionError(method)


class Probe(unittest.TestCase):
    """Task 0: the live probe of the two Telegram behaviours the store rests on."""

    def run_probe(self, tg):
        with mock.patch.object(common, "tg", side_effect=tg), mock.patch.dict(os.environ, {"TG_CHAT_ID": "7"}):
            return equity.probe()

    def test_probe_true_and_cleans_up(self):
        tg = FakeTelegram()
        self.assertEqual(self.run_probe(tg), (True, True, True))
        methods = [m for m, _ in tg.calls]
        self.assertEqual(methods, ["getMe", "getChat", "sendMessage", "pinChatMessage", "getChat", "unpinChatMessage",
                                   "deleteMessage"])
        sent = dict(tg.calls)["sendMessage"]
        self.assertFalse(any(ch.isdigit() for ch in sent["text"]))  # the test message carries no number
        self.assertTrue(dict(tg.calls)["pinChatMessage"]["disable_notification"])
        self.assertEqual((tg.pins, tg.messages), ([], {}))

    def test_probe_not_private(self):
        self.assertEqual(self.run_probe(FakeTelegram(chat_type="group"))[0], False)

    def test_probe_cleans_up_when_a_step_fails(self):
        tg = FakeTelegram(fail={"pinChatMessage"})
        self.assertEqual(self.run_probe(tg), (True, False, False))
        self.assertEqual(tg.messages, {})  # the test message is deleted anyway

    def test_probe_prints_three_booleans_only(self):
        with mock.patch.object(equity, "probe", return_value=(True, True, False)), \
                mock.patch("builtins.print") as out:
            self.assertEqual(equity.main(["--probe"]), 1)  # a false boolean fails the run
        self.assertEqual([c.args[0] for c in out.call_args_list],
                         ["private_chat: True", "pin_succeeded: True", "pinned_message_is_ours: False"])


if __name__ == "__main__":
    unittest.main()
