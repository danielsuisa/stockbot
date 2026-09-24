"""Block 4 - reliability: heartbeat, watchdog, freshness line, EDGAR links, source fallback, rescans/corrections."""
import datetime as dt
import io
import json
import os
import tempfile
import unittest
import urllib.error
from email.message import Message
from pathlib import Path
from unittest import mock

from bot import common, form4, listen, market, scan

TODAY = dt.date(2026, 9, 24)


def http_error(code):
    h = Message()
    h["Content-Type"] = "text/html"
    return urllib.error.HTTPError("u", code, "x", h, io.BytesIO(b""))


def row(acc, who, value, cik=7, ticker="ACME", od=True, weight=1.0, last="2026-09-20", **kw):
    return {"acc": acc, "cik": cik, "ticker": ticker, "company": "Acme", "insider": who, "role": "מנהל",
            "insider_cik": who, "od": od, "weight": weight, "first": last, "last": last, "shares": 1, "value": value,
            "price": 1.0, "filed": last, "kind": "open", "plan_value": 0, "pct": 0.2, "post": 5, "sig": None, **kw}


class Messages(unittest.TestCase):
    def sent(self, text, **kw):
        with mock.patch.dict(os.environ, {"TG_TOKEN": "", "TG_CHAT_ID": "", "GITHUB_ACTIONS": ""}), \
                mock.patch("builtins.print") as out:
            common.send(text, **kw)
        return [c.args[0] for c in out.call_args_list]

    def test_tail_order_freshness_and_prompt(self):
        common.STAMPS.clear()
        (m,) = self.sent("שלום")
        self.assertTrue(m.endswith(f"🕒 נתונים נכון ל: SEC <code>—</code>, מחיר <code>—</code>\n\n{common.DISCLAIMER}"))
        self.assertNotIn(common.CHECK_PROMPT, m)
        common.stamp("sec", "2026-09-24 10:00 UTC")
        common.stamp("price", "2026-09-23 20:00 (⚠ מטמון)")
        (m,) = self.sent("התראה", signal=True)
        tail = m.split("\n")[-4:]
        self.assertEqual(tail[0], common.CHECK_PROMPT)
        self.assertIn("SEC <code>2026-09-24 10:00 UTC</code>, מחיר <code>2026-09-23 20:00 (⚠ מטמון)</code>", tail[1])
        self.assertEqual(common.rtl_bad_lines(m), [])
        big = "\n".join(f"• שורה {i} " + "א" * 90 for i in range(90))
        parts = self.sent(big, signal=True)
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(p.endswith(common.DISCLAIMER) and common.CHECK_PROMPT in p and common.visible(p) <= 4096
                            for p in parts))
        common.STAMPS.clear()

    def test_fetch_stamps_sec_time(self):
        common.STAMPS.clear()
        with mock.patch.dict(os.environ, {"SEC_UA": "t t@t.com", "HTTP_CACHE": ""}), \
                mock.patch.object(common.urllib.request, "urlopen") as u:
            u.return_value.__enter__.return_value.read.return_value = b"{}"
            u.return_value.__enter__.return_value.headers = {}
            common.fetch("https://www.sec.gov/x")
        self.assertIn("UTC", common.STAMPS["sec"])
        common.STAMPS.clear()

    def test_log_and_job_summary(self):
        with mock.patch("builtins.print") as out:
            common.log("day", day="20260923", filings=3)
        rec = json.loads(out.call_args.args[0])
        self.assertEqual((rec["event"], rec["day"], rec["filings"]), ("day", "20260923", 3))
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "summary.md")
            with mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": p}):
                common.summary("### hi\n")
                common.summary("row")
            self.assertEqual(open(p, encoding="utf-8").read(), "### hi\nrow\n")


class GitHub(unittest.TestCase):
    env = {"GITHUB_REPOSITORY": "o/r", "GITHUB_TOKEN": "t", "GITHUB_REF_NAME": "main"}

    def test_dispatch(self):
        with mock.patch.dict(os.environ, self.env):
            with mock.patch.object(common, "fetch", return_value=b"") as f:
                self.assertEqual(common.dispatch("daily-scan.yml", {"notify": "true"}), "")
            self.assertIn("/actions/workflows/daily-scan.yml/dispatches", f.call_args.args[0])
            with mock.patch.object(common, "fetch", return_value=None):
                self.assertEqual(common.dispatch("x.yml", {}), "HTTP 404")
            with mock.patch.object(common, "fetch", side_effect=http_error(403)):
                self.assertEqual(common.dispatch("x.yml", {}), "HTTP 403")
        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": ""}):
            self.assertEqual(common.dispatch("x.yml", {}), "no GITHUB_TOKEN")

    def test_runs(self):
        body = json.dumps({"workflow_runs": [{"created_at": "2026-09-24T05:31:00Z", "conclusion": "success",
                                              "event": "schedule", "status": "completed", "id": 1}]}).encode()
        with mock.patch.dict(os.environ, self.env), mock.patch.object(common, "fetch", return_value=body):
            self.assertEqual(common.runs("watchdog.yml"), {"created_at": "2026-09-24T05:31:00Z", "conclusion": "success",
                                                           "event": "schedule", "status": "completed"})
        with mock.patch.dict(os.environ, self.env), mock.patch.object(common, "fetch", side_effect=OSError):
            self.assertIsNone(common.runs("watchdog.yml"))
        with mock.patch.dict(os.environ, {"GITHUB_REPOSITORY": ""}):
            self.assertIsNone(common.runs("watchdog.yml"))


LISTING = {"directory": {"item": [{"name": "form.20260923.idx"}]}}
IDX = ("4  Acme  7  20260923  edgar/data/7/0000-26-1.txt\n4/A  Acme  7  20260923  edgar/data/7/0000-26-2.txt\n").encode()


class Sources(unittest.TestCase):
    def setUp(self):
        scan.published.cache_clear()

    tearDown = setUp

    def test_index_then_efts_fallback(self):
        with mock.patch.object(common, "get_json", return_value=LISTING), mock.patch.object(common, "fetch", return_value=IDX):
            self.assertEqual(scan.index_urls("20260923"), ({"0000-26-1": "https://www.sec.gov/Archives/edgar/data/7/0000-26-1.txt",
                                                            "0000-26-2": "https://www.sec.gov/Archives/edgar/data/7/0000-26-2.txt"},
                                                           "index"))
        self.assertEqual(scan.index_urls("20260922"), (None, "index"))  # not listed: holiday / not yet published
        hits = lambda n, total: {"hits": {"total": {"value": total}, "hits": [
            {"_id": f"0000-26-{i}:primary_doc.xml", "_source": {"ciks": ["0000000007", "0000000099"]}} for i in n]}}
        pages = iter([hits(range(100), 150), hits(range(100, 150), 150), hits([], 0)])
        scan.published.cache_clear()

        def gj(url):
            return LISTING if url.endswith("index.json") else next(pages)
        with mock.patch.object(common, "get_json", side_effect=gj), \
                mock.patch.object(common, "fetch", side_effect=http_error(503)), mock.patch("builtins.print"):
            urls, source = scan.index_urls("20260923")
        self.assertEqual((source, len(urls)), ("efts", 150))
        self.assertEqual(urls["0000-26-5"], "https://www.sec.gov/Archives/edgar/data/7/0000265/0000-26-5.txt")
        scan.published.cache_clear()
        with mock.patch.object(common, "get_json", side_effect=lambda u: LISTING if u.endswith("index.json") else {}), \
                mock.patch.object(common, "fetch", side_effect=http_error(503)), mock.patch("builtins.print"), \
                self.assertRaises(urllib.error.HTTPError):
            scan.index_urls("20260923")  # both sources down -> the day fails (and is retried), never silently empty

    def test_rescan_fetches_only_new_filings(self):
        st = {"days": {"20260923": "ok"}, "buys": [], "alerted": {}, "seen": {"20260923": ["0000-26-1"]}}
        with mock.patch.object(scan, "index_urls", return_value=({"0000-26-1": "u1", "0000-26-2": "u2"}, "index")), \
                mock.patch.object(scan, "_get", return_value=None) as g, mock.patch.object(common, "cik_tickers", return_value={}):
            stats = {}
            self.assertEqual(scan.scan_day("20260923", st, TODAY, stats, rescan=True), "ok")
        self.assertEqual([c.args[0] for c in g.call_args_list], ["u2"])
        self.assertEqual(st["seen"]["20260923"], ["0000-26-1", "0000-26-2"])
        self.assertEqual((stats["days"][0]["rescan"], stats["days"][0]["filings"]), (True, 1))


class Corrections(unittest.TestCase):
    def state(self):
        rs = [row("a", "x", 400000), row("b", "y", 300000), row("c", "z", 300000)]
        ev = scan.evaluate(rs, None)
        return {"buys": rs, "sent": {"7": scan.snapshot(ev, TODAY)}, "removed": {}, "regime": None}

    def test_material_amendment_sends_correction(self):
        st = self.state()
        self.assertEqual(st["sent"]["7"]["total"], 1_000_000)
        st["buys"][0] = {**st["buys"][0], "value": 40000.0, "amended_by": "a2"}  # a 4/A cut the $400K to $40K
        (msg,) = scan.corrections(st, TODAY)
        self.assertIn("<code>1.0M$</code>", msg)
        self.assertIn("<code>640.0K$</code>", msg)
        self.assertEqual(common.rtl_bad_lines(msg), [])
        self.assertEqual(scan.corrections(st, TODAY), [])  # acknowledged: never repeated

    def test_small_amendment_is_silent_and_withdrawal_is_reported(self):
        st = self.state()
        st["buys"][0] = {**st["buys"][0], "value": 390000.0, "amended_by": "a2"}  # -1%: not material
        self.assertEqual(scan.corrections(st, TODAY), [])
        self.assertEqual(st["sent"]["7"]["acks"], ["a2"])
        st["removed"] = {"b": {"by": "b2", "cik": 7, "date": "2026-09-20"}}
        del st["buys"][1]  # a 4/A withdrew y's purchase -> only 2 insiders left
        (msg,) = scan.corrections(st, TODAY)
        self.assertIn("כבר לא עומדת בכללי ההתראה", msg)


class Heartbeat(unittest.TestCase):
    def run_main(self, argv, **patches):
        sent = []
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(os.environ, {"STATE_FILE": os.path.join(tmp, "s.json"), "HEARTBEAT_FILE": os.path.join(tmp, "hb")}), \
                mock.patch.object(common, "send", side_effect=lambda t, **k: sent.append(t)), \
                mock.patch.object(market, "regime", return_value={"tag": "normal"}), \
                mock.patch.object(scan, "alerts", return_value=[]), mock.patch("traceback.print_exc"), \
                mock.patch.multiple(scan, **patches) if patches else mock.patch.object(scan, "_get", scan._get):
            scan._reported[0] = False
            try:
                scan.main(argv)
                code = 0
            except SystemExit as e:
                code = e.code
            except Exception as e:  # main alarms, then re-raises the crash (the run must still fail)
                code = type(e).__name__
            marker = os.path.exists(os.path.join(tmp, "hb"))
            state = json.load(open(os.path.join(tmp, "s.json"), encoding="utf-8")) if os.path.exists(os.path.join(tmp, "s.json")) else {}
        return code, sent, marker, state

    def test_success_heartbeat(self):
        code, sent, marker, state = self.run_main(["--days", "20260923"], scan_day=mock.Mock(return_value="ok"))
        self.assertEqual(code, 0)
        self.assertTrue(sent[-1].startswith("✅ סריקה <code>") and "אין התראות חדשות" in sent[-1] and marker)
        self.assertEqual((state["heartbeat"]["ok"], state["heartbeat"]["alerts"]), (True, 0))

    def test_failed_day_and_crash_alarm(self):
        code, sent, marker, state = self.run_main(["--days", "20260923"], scan_day=mock.Mock(side_effect=RuntimeError("503")))
        self.assertTrue(code and sent[-1].startswith("❌ הסריקה נכשלה: לא הצלחתי לסרוק את <code>2026-09-23</code>"))
        self.assertEqual((len(sent), marker), (1, True))  # one alarm, no duplicate from main's handler
        code, sent, _, _ = self.run_main(["--days", "20260923"], scan_day=mock.Mock(return_value="ok"),
                                         prune=mock.Mock(side_effect=KeyError("days")))
        self.assertTrue(sent and sent[-1].startswith("❌ הסריקה נכשלה: <code>KeyError</code>"))
        code, sent, _, _ = self.run_main(["--days", "20260923"], scan_day=mock.Mock(side_effect=SystemExit("SEC_UA is not set")))
        self.assertIn("SEC_UA", sent[-1])

    def test_alarm_step(self):
        with tempfile.TemporaryDirectory() as tmp:
            hb = os.path.join(tmp, "hb")
            env = {"HEARTBEAT_FILE": hb, "COMMIT_OUTCOME": "success", "RUN_URL": "https://github.com/o/r/actions/runs/1"}
            with mock.patch.dict(os.environ, env), mock.patch.object(common, "send") as snd:
                scan.alarm_step("step failed")
                self.assertIn("❌ הסריקה נכשלה: step failed", snd.call_args.args[0])  # nothing reported yet -> alarm
                self.assertIn('href="https://github.com/o/r/actions/runs/1"', snd.call_args.args[0])
                snd.reset_mock()
                scan.alarm_step("step failed")  # the marker now exists and the commit succeeded -> no duplicate
                snd.assert_not_called()
                os.environ["COMMIT_OUTCOME"] = "failure"
                scan.alarm_step("push failed")  # a failed commit/push after a sent heartbeat still alarms
                snd.assert_called_once()


class Watchdog(unittest.TestCase):
    def setUp(self):
        scan.published.cache_clear()

    tearDown = setUp

    def run_wd(self, days, listing, env=None, dispatch=""):
        sent = []
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "s.json")
            json.dump({"days": days, "buys": [], "alerted": {}}, open(p, "w", encoding="utf-8"))
            with mock.patch.dict(os.environ, {"STATE_FILE": p, "GITHUB_ACTIONS": "", **(env or {})}), \
                    mock.patch.object(common, "get_json", return_value={"directory": {"item": [{"name": n} for n in listing]}}), \
                    mock.patch.object(common, "send", side_effect=lambda t, **k: sent.append(t)), \
                    mock.patch.object(common, "dispatch", return_value=dispatch) as d, mock.patch.object(scan, "main") as m, \
                    mock.patch("builtins.print"):
                try:
                    scan.watchdog(TODAY)
                    code = 0
                except SystemExit as e:
                    code = e.code
        return sent, d, m, code

    def test_previous_trading_day_skips_holidays(self):
        with mock.patch.object(common, "get_json", return_value={"directory": {"item": [{"name": "form.20260922.idx"}]}}):
            self.assertEqual(scan.previous_trading_day(TODAY), "20260922")  # 09-23 not listed -> holiday
        scan.published.cache_clear()
        with mock.patch.object(common, "get_json", return_value=None), mock.patch("builtins.print"):
            self.assertEqual(scan.previous_trading_day(TODAY), "20260923")  # listing unavailable -> assume trading

    def test_ok_missed_local_and_actions(self):
        sent, d, m, _ = self.run_wd({"20260923": "ok"}, ["form.20260923.idx"])
        self.assertEqual(sent, [])  # nothing missed -> quiet
        sent, d, m, _ = self.run_wd({"20260922": "ok"}, ["form.20260922.idx", "form.20260923.idx"])
        self.assertEqual(sent, ["⚠️ לא סרקתי את <code>2026-09-23</code> — מריץ את הסריקה שוב."])
        m.assert_called_once_with([])  # local: runs the scan right here
        sent, d, m, _ = self.run_wd({"20260922": "ok"}, ["form.20260922.idx", "form.20260923.idx"], {"GITHUB_ACTIONS": "true"})
        d.assert_called_once_with("daily-scan.yml", {"notify": "true"})
        m.assert_not_called()
        sent, d, m, code = self.run_wd({"20260922": "ok"}, ["form.20260922.idx", "form.20260923.idx"],
                                       {"GITHUB_ACTIONS": "true"}, dispatch="HTTP 403")
        self.assertTrue(code and "HTTP 403" in sent[-1] and "actions: write" in sent[-1])
        self.assertEqual([common.rtl_bad_lines(s) for s in sent], [[], []])


class Links(unittest.TestCase):
    def test_alert_rows_link_each_form4_and_its_index(self):
        subs = {"filings": {"recent": {"form": ["4", "10-K"], "accessionNumber": ["0000-26-1", "0000-26-9"],
                                       "primaryDocument": ["xslF345X06/form4.xml", "k.htm"]}}}
        with mock.patch.object(common, "submissions", return_value=subs):
            urls = scan.links(7)
        self.assertEqual(urls, {"0000-26-1": "https://www.sec.gov/Archives/edgar/data/7/0000261/xslF345X06/form4.xml"})
        with mock.patch.object(scan, "pay", return_value={}):
            line = scan.row_line(row("0000-26-1", "Jane Doe", 250000), set(), urls)
        self.assertIn('<a href="https://www.sec.gov/Archives/edgar/data/7/0000261/xslF345X06/form4.xml">Jane Doe</a>', line)
        self.assertIn('<a href="https://www.sec.gov/Archives/edgar/data/7/0000261/0000-26-1-index.htm">אינדקס</a>', line)
        self.assertEqual(common.rtl_bad_lines(line), [])


class Prices(unittest.TestCase):
    def test_cached_price_fallback_is_marked(self):
        common.STAMPS.clear()
        market.CACHE.clear()
        market.CACHE["ACME"] = {"price": 12.5, "asof": "2026-09-23 20:00"}
        with mock.patch.object(market, "chart", return_value=None):
            q = market.quote("ACME", "2026-08-01")
        self.assertEqual((q["price"], q["source"], q["asof"]), (12.5, "cache", "2026-09-23 20:00"))
        self.assertIn("מטמון", common.STAMPS["price"])
        with mock.patch.object(market, "chart", return_value=None):
            self.assertEqual(market.quote("NOPE")["price"], None)  # no live and no cached price -> None, never a default
        market.CACHE.clear()
        common.STAMPS.clear()

    def test_live_quote_splits_and_remembers(self):
        r = {"meta": {"currency": "USD", "regularMarketPrice": 10.0},
             "events": {"splits": {"1": {"date": 1_900_000_000, "numerator": 1, "denominator": 30}}}}
        with mock.patch.object(market, "chart", return_value=r):
            q = market.quote("BYND", "2026-08-05")
        self.assertEqual((q["price"], round(q["split"], 5), q["source"]), (10.0, round(1 / 30, 5), "live"))
        self.assertEqual(market.CACHE["BYND"]["price"], 10.0)
        market.CACHE.clear()

    def test_altman_says_when_the_price_is_cached(self):
        from test_bot import companyfacts  # the shared synthetic XBRL fixture
        from bot import fundamentals
        cached = {"price": 50.0, "split": 1.0, "asof": "2026-09-23 20:00", "source": "cache"}
        with mock.patch.object(common, "get_json", return_value=companyfacts()), \
                mock.patch.object(common, "cik_tickers", return_value={1: "ACME"}), \
                mock.patch.object(market, "quote", return_value=cached):
            text = "\n".join(fundamentals.format_he(fundamentals.analyze(1, "ACME", 3571)))
        self.assertIn("(⚠ מחיר מ־<code>2026-09-23 20:00</code>)", text)
        self.assertEqual(common.rtl_bad_lines(text), [])
        with mock.patch.object(market, "chart", return_value={"meta": {"currency": "CAD", "regularMarketPrice": 9.0}}):
            market.CACHE["ACME"] = {"price": 7.0, "asof": "x"}
            self.assertIsNone(market.quote("ACME")["price"])  # Yahoo answered in CAD: no cached USD substitute
        market.CACHE.clear()

    def test_prune_keeps_prices_of_tracked_tickers_only(self):
        st = {"days": {}, "buys": [row("a", "x", 1, ticker="ACME", last=TODAY.isoformat())], "alerted": {},
              "prices": {"ACME": {"price": 1}, "SPY": {"price": 2}, "OLD": {"price": 3}}}
        self.assertEqual(sorted(scan.prune(st, TODAY)["prices"]), ["ACME", "SPY"])


class FalsePositives(unittest.TestCase):
    def test_plain_text_needs_dollar_or_caps(self):
        m = {"ALL": 1, "GOOD": 2, "OK": 3, "HI": 4, "AAPL": 5}
        with mock.patch.object(common, "tickers", return_value=m):
            for text in ("all good", "ok thanks", "hi", "aapl"):
                self.assertEqual(listen.extract(text), ([], []), text)
            self.assertEqual(listen.extract("AAPL")[0], ["AAPL"])
            self.assertEqual(listen.extract("$aapl")[0], ["AAPL"])
            self.assertEqual(listen.extract("aapl", loose=True)[0], ["AAPL"])  # /check accepts any case


if __name__ == "__main__":
    unittest.main()
