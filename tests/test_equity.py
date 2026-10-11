"""Phase B.1 (spec section 10): the equity store, a bot message pinned in the owner's private chat. Every value here
is synthetic (E = 31,337.17); no real equity appears in the repository."""
import os
import re
import unittest
from unittest import mock

from bot import common, equity, levels
from tests import msgrules

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
                                           "date": 1_791_000_000 + self.next_id * 86_400,
                                           "text": re.sub(r"<[^>]+>", "", p["text"])}  # Telegram returns plain text
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


def with_tg(tg, fn, *a, env=None):
    with mock.patch.object(common, "tg", side_effect=tg),             mock.patch.dict(os.environ, {"TG_CHAT_ID": "7", **(env or {})}):
        return fn(*a)


class Store(unittest.TestCase):
    def test_parse_strict(self):
        for ok, value in (("25000", 25000.0), ("25,000", 25000.0), ("25000.5", 25000.5), ("1,234,567.89", 1234567.89),
                          ("100", 100.0), ("100,000,000", 100_000_000.0), (" 31337.17 ", 31337.17)):
            self.assertEqual(equity.parse_amount(ok), value, ok)
        for bad in ("", "abc", "25000 30000", "-5", "0", "1e5", "25k", "25,00", "25000.123", "$25000", "99.99",
                    "100,000,000.01", "25000x", "2,5000"):
            self.assertIsNone(equity.parse_amount(bad), bad)

    def test_store_and_load(self):
        tg = FakeTelegram()
        self.assertTrue(with_tg(tg, equity.store, SYNTHETIC_E))
        sent = [p for m, p in tg.calls if m == "sendMessage"][0]
        self.assertTrue(sent["disable_notification"])
        self.assertTrue([p for m, p in tg.calls if m == "pinChatMessage"][0]["disable_notification"])
        got = with_tg(tg, equity.load)
        self.assertEqual(got[0], SYNTHETIC_E)
        self.assertRegex(got[1], r"^\d{4}-\d{2}-\d{2}$")

    def test_store_refuses_a_non_private_chat(self):
        tg = FakeTelegram(chat_type="group")
        self.assertFalse(with_tg(tg, equity.store, SYNTHETIC_E))
        self.assertNotIn("sendMessage", [m for m, _ in tg.calls])  # nothing stored, nothing pinned
        self.assertIsNone(with_tg(tg, equity.load))

    def test_load_fallbacks(self):
        def chat_with(msg):
            tg = FakeTelegram()
            tg.messages[1] = {"message_id": 1, "date": 1_791_000_000, **msg}
            tg.pins = [1]
            return tg
        good = f"{equity.MARKER}: 31,337.17$"
        for msg in ({"from": {"id": 7, "is_bot": False}, "text": good},  # the owner's own pin
                    {"from": {"id": 43, "is_bot": True}, "text": good},  # another bot
                    {"from": {"id": 42, "is_bot": True}, "text": good + " "},  # the layout changed
                    {"from": {"id": 42, "is_bot": True}, "text": f"{equity.MARKER}: 31337.17$"},  # no thousands comma
                    {"from": {"id": 42, "is_bot": True}, "text": f"{equity.MARKER}: 99.00$"},  # out of bounds
                    {"from": {"id": 42, "is_bot": True}}):  # no text
            self.assertIsNone(with_tg(chat_with(msg), equity.load), msg)
        self.assertIsNone(with_tg(FakeTelegram(), equity.load))  # no pin at all
        self.assertIsNone(with_tg(FakeTelegram(fail={"getChat"}), equity.load))  # Telegram error

    def test_store_error_names_no_value(self):
        tg = FakeTelegram(fail={"pinChatMessage"})
        with self.assertRaises(equity.EquityStoreError) as cm:
            with_tg(tg, equity.store, SYNTHETIC_E)
        self.assertNotIn("31", str(cm.exception))

    def test_two_equity_newest_wins(self):
        tg = FakeTelegram()
        with_tg(tg, equity.store, 20_000.0)
        with_tg(tg, equity.store, SYNTHETIC_E)
        self.assertEqual(len(tg.pins), 1)  # the older equity pin is unpinned
        self.assertEqual(with_tg(tg, equity.load)[0], SYNTHETIC_E)

    def test_older_equity_pin_surfaces_with_its_date(self):
        tg = FakeTelegram()
        with_tg(tg, equity.store, 20_000.0)
        first = tg.pins[0]
        with_tg(tg, equity.store, SYNTHETIC_E)
        tg.pins.append(first)  # say the older pin stayed (an unpin that failed), then the owner unpins the newest
        tg.pins = tg.pins[1:]
        got = with_tg(tg, equity.load)
        self.assertEqual(got[0], 20_000.0)
        self.assertEqual(got[1], equity.date_of(tg.messages[first]["date"]))  # the older date is the one shown


class Settings(unittest.TestCase):
    def p(self, value):
        env = {} if value is None else {"TICKET_RISK_PCT": value}
        with mock.patch.dict(os.environ, env, clear=False):
            if value is None:
                os.environ.pop("TICKET_RISK_PCT", None)
            return equity.p_setting()

    def test_p_setting_unset(self):
        self.assertEqual(self.p(None), (0.005, "unset"))

    def test_p_setting_non_numeric(self):
        self.assertEqual(self.p("half"), (0.005, "non-numeric"))

    def test_p_setting_not_positive(self):
        self.assertEqual(self.p("0"), (0.005, "not positive"))
        self.assertEqual(self.p("-1"), (0.005, "not positive"))

    def test_p_setting_percent_unit(self):
        self.assertEqual(self.p("0.4"), (0.004, None))  # 0.4 means 0.4%
        self.assertEqual(self.p("1"), (0.01, None))  # cut to 0.5% by levels.p_used, with a note


class Reply(unittest.TestCase):
    def test_reply(self):
        text = equity.reply(SYNTHETIC_E, "2026-10-11", 0.005, None)
        msgrules.check(self, text)
        for part in (common.code(common.price(SYNTHETIC_E)), common.code(common.price(156.69)), common.code(6),
                     "11.10.2026", "TICKET_RISK_PCT", "באחוזים"):
            self.assertIn(part, text)

    def test_reply_notes(self):
        capped = equity.reply(SYNTHETIC_E, "2026-10-11", 0.01, None)
        msgrules.check(self, capped)
        self.assertIn("הוגבל", capped)  # the p cap note
        default = equity.reply(SYNTHETIC_E, "2026-10-11", 0.005, "non-numeric")
        msgrules.check(self, default)
        self.assertIn("ברירת המחדל", default)
        small = equity.reply(3_000.0, "2026-10-11", 0.005, None)  # risk 15 < 20
        msgrules.check(self, small)
        self.assertIn("חשבון קטן", small)


if __name__ == "__main__":
    unittest.main()
