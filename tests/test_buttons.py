import os
import unittest
from unittest import mock

from bot import common, intraday, listen


class SendButtons(unittest.TestCase):
    def run_send(self, text, buttons):
        calls = []
        env = {"TG_TOKEN": "t", "TG_CHAT_ID": "1"}
        with mock.patch.dict(os.environ, env), mock.patch.object(common, "tg", side_effect=lambda m, **p: calls.append(p)), \
                mock.patch("time.sleep"):
            common.send(text, buttons=buttons)
        return calls

    def test_markup_on_last_part_only(self):
        kb = {"inline_keyboard": [[{"text": "x", "url": "https://x"}]]}
        calls = self.run_send("\n".join(["שורה ארוכה " * 30] * 40), kb)  # > 4000: several parts
        self.assertGreater(len(calls), 1)
        self.assertEqual([("reply_markup" in c) for c in calls], [False] * (len(calls) - 1) + [True])
        self.assertEqual(calls[-1]["reply_markup"], kb)

    def test_no_buttons_no_markup(self):
        self.assertNotIn("reply_markup", self.run_send("שלום", None)[0])


class ChartButtons(unittest.TestCase):
    def test_rows_and_dedupe(self):
        kb = intraday.chart_buttons(["AAPL", "TSLA", "AAPL", "NVDA", "AMD", "META"])
        rows = kb["inline_keyboard"]
        self.assertEqual([len(r) for r in rows], [4, 1])
        self.assertEqual(rows[0][0], {"text": "📈 AAPL", "url": "https://www.tradingview.com/chart/?symbol=AAPL"})

    def test_empty(self):
        self.assertIsNone(intraday.chart_buttons([]))

    def test_intraday_send_passes_buttons(self):
        with mock.patch.object(common, "send") as snd:
            intraday.send("x", False, signal=True, symbols=["AAPL"])
        self.assertEqual(snd.call_args.kwargs["buttons"]["inline_keyboard"][0][0]["text"], "📈 AAPL")


class Keyboard(unittest.TestCase):
    def test_buttons_are_commands_handle_understands(self):
        for row in listen.KEYBOARD["keyboard"]:
            for b in row:
                self.assertTrue(b["text"].startswith("/"), b)

    def test_help_and_intraday_carry_keyboard(self):
        with mock.patch.object(common, "send") as snd, mock.patch.object(common, "tg"):
            listen.handle("/help")
        self.assertIs(snd.call_args.kwargs["buttons"], listen.KEYBOARD)
        with mock.patch.object(common, "send") as snd, mock.patch.object(intraday, "load", return_value=None):
            listen.handle("/intraday")
        self.assertIs(snd.call_args.kwargs["buttons"], listen.KEYBOARD)


if __name__ == "__main__":
    unittest.main()
