"""Phase B.1, the owner's condition 2: E, the dollar risk and the position value reach Telegram only -- never stdout,
stderr, common.log, common.summary or a file. Driven end to end through the listener's own update path with a
synthetic E (31,337.17); Telegram and Alpaca are fakes."""
import builtins
import contextlib
import gzip
import io
import os
import pathlib
import unittest
from unittest import mock

from bot import alpaca, common, listen
from tests.test_equity import FakeTelegram
from tests.test_ticket_command import CAL, QUOTE, bars_until

SYNTHETIC_E = "31337.17"  # synthetic, for tests only
# every printed form of E, the dollar risk (0.5% x E = 156.69; 78 shares x R 2 = 156.00) and the position value
# (78 x 50.51 = 3,939.78)
FORMS = ("31337.17", "31,337.17", "156.69", "156.00", "3939.78", "3,939.78")


class Leaks:
    """Records everything that could reach a public log or a file during a run."""

    def __init__(self):
        self.out, self.logs, self.summaries, self.writes = io.StringIO(), [], [], []

    def text(self):
        return "\n".join([self.out.getvalue(), *map(str, self.logs), *self.summaries, *self.writes])

    @contextlib.contextmanager
    def watch(self):
        real_open, real_gzip = builtins.open, gzip.open

        def recording(real):
            def opener(file, mode="r", *a, **k):
                if any(c in mode for c in "wax+"):
                    sink = io.BytesIO() if "b" in mode else io.StringIO()
                    self.writes.append(f"[file {file}]")
                    close = sink.close

                    def keep():
                        v = sink.getvalue()
                        self.writes.append(v.decode("utf-8", "replace") if isinstance(v, bytes) else v)
                        close()
                    sink.close = keep
                    sink.__exit__ = lambda *x: keep()
                    return sink
                return real(file, mode, *a, **k)
            return opener
        with contextlib.ExitStack() as st:
            st.enter_context(contextlib.redirect_stdout(self.out))
            st.enter_context(contextlib.redirect_stderr(self.out))
            st.enter_context(mock.patch.object(common, "log", side_effect=lambda e, **f: self.logs.append((e, f))))
            st.enter_context(mock.patch.object(common, "summary", side_effect=self.summaries.append))
            st.enter_context(mock.patch("builtins.open", recording(real_open)))
            st.enter_context(mock.patch.object(gzip, "open", recording(real_gzip)))
            st.enter_context(mock.patch.object(pathlib.Path, "write_text",
                                               lambda p, data, *a, **k: self.writes.append(str(data))))
            st.enter_context(mock.patch.object(pathlib.Path, "write_bytes",
                                               lambda p, data: self.writes.append(data.decode("utf-8", "replace"))))
            yield


def update(n, text, chat=7):
    return {"update_id": n, "message": {"chat": {"id": chat}, "text": text}}


class Privacy(unittest.TestCase):
    def run_listener(self, tg, *texts):
        leaks = Leaks()
        env = {"TG_TOKEN": "test-token", "TG_CHAT_ID": "7", "GITHUB_ACTIONS": "", "TICKET_RISK_PCT": "0.5"}
        with (mock.patch.dict(os.environ, env),
              mock.patch.object(common, "tg", side_effect=lambda m, **p: [] if m == "getUpdates" else tg(m, **p)),
              mock.patch.object(common, "tickers", return_value={"AAPL": (320193, "Apple")}),
              mock.patch.object(common, "freshness", return_value=""),
              mock.patch.object(alpaca, "calendar", return_value=CAL),
              mock.patch.object(alpaca, "daily", return_value={"AAPL": bars_until("2026-03-09", 300)}),
              mock.patch.object(alpaca, "quote_full", return_value=QUOTE),
              mock.patch("bot.ticket_command.signal_day", return_value="2026-03-09"),
              mock.patch("time.sleep")):
            with leaks.watch():
                for i, text in enumerate(texts):
                    listen.answer([update(i + 1, text)])
        return leaks

    def sent(self, tg):
        return "\n".join(p.get("text", "") for m, p in tg.calls if m == "sendMessage")

    def test_privacy_end_to_end(self):
        tg = FakeTelegram()
        leaks = self.run_listener(tg, f"/equity {SYNTHETIC_E}", "/ticket AAPL")
        telegram = self.sent(tg)
        for form in ("31,337.17", "156.69", "156.00", "3,939.78"):
            self.assertIn(form, telegram)  # the run did size the ticket by E: the test exercises the real path
        logged = leaks.text()
        for form in FORMS:
            self.assertNotIn(form, logged, f"{form} leaked")
        self.assertIn("update 1: handling", logged)  # the log was captured

    def test_privacy_when_the_store_fails(self):
        tg = FakeTelegram(fail={"pinChatMessage"})
        leaks = self.run_listener(tg, f"/equity {SYNTHETIC_E}")
        logged = leaks.text()
        for form in FORMS:
            self.assertNotIn(form, logged, f"{form} leaked")
        self.assertIn("EquityStoreError", logged)  # the type is logged, for debugging
        self.assertIn("לא הצלחתי לקרוא או לשמור את ההון", self.sent(tg))

    def test_privacy_when_the_text_is_garbage(self):
        tg = FakeTelegram()
        leaks = self.run_listener(tg, "/equity 31337.17.5x", "/equity 31337.17 999")
        for form in ("31337.17", "31337"):
            self.assertNotIn(form, leaks.text())
            self.assertNotIn(form, self.sent(tg))  # the usage reply does not echo the input either


class SyntheticDryRun(unittest.TestCase):
    """Condition 4: a dry run on GitHub uses only the synthetic E, marked as synthetic, and never reads the real pin."""

    def test_dry_run_synthetic_marked(self):
        from bot import ticket_command
        from tests import listen_dry_run
        with (mock.patch.object(common, "tg", side_effect=AssertionError("no Telegram call in a dry run")),
              mock.patch.object(common, "tickers", return_value={"AAPL": (320193, "Apple")}),
              mock.patch.object(ticket_command, "build", side_effect=lambda s, sizing=None: {"symbol": s, "sz": sizing}),
              mock.patch.object(ticket_command, "message",
                                side_effect=lambda t: f"🎫 כרטיס ל-{common.code(t['symbol'])} {t['sz']['E']:,.2f}"),
              mock.patch.dict(os.environ, {"TG_TOKEN": ""}), mock.patch("builtins.print") as out):
            self.assertEqual(listen_dry_run.main(["/equity 12345.67", "/ticket AAPL"], synthetic=True), 0)
        printed = [str(c.args[0]) for c in out.call_args_list if c.args]
        self.assertIn("synthetic", printed[0].lower())  # the run's first line says E is synthetic
        messages = [x for x in printed[1:] if "12,345.67" in x]  # printed[0] is the synthetic notice itself
        self.assertEqual(len(messages), 2)  # the /equity reply and the ticket, both sized by the synthetic E
        self.assertTrue(all(x.startswith(listen_dry_run.SYNTHETIC_MARK) for x in messages))
        self.assertEqual(listen_dry_run.SYNTHETIC_E, 12_345.67)

    def test_dry_run_refuses_with_a_token(self):
        from tests import listen_dry_run
        with mock.patch.dict(os.environ, {"TG_TOKEN": "a-real-looking-token"}), mock.patch("builtins.print"):
            self.assertEqual(listen_dry_run.main(["/ticket AAPL"], synthetic=True), 2)
            self.assertEqual(listen_dry_run.main("/ticket AAPL"), 2)


if __name__ == "__main__":
    unittest.main()
