"""Block 5 - portfolio & risk layer: journal, weekly follow-up, liquidity guard, sizing helper, concentration."""
import datetime as dt
import json
import os
import tempfile
import unittest
from unittest import mock

from bot import check, common, journal, market, scan
from test_reliability import row

TODAY = dt.date(2026, 9, 24)
SCORES = {"piotroski": "6/9", "altman": "Z 2.10", "beneish": -2.4}


def entry(i, date="2026-01-05", ticker="ACME", sic=3571, quality=75, regime="normal", returns=None, **kw):
    return {"id": f"{date}-{ticker}{i}", "date": date, "ticker": ticker, "cik": 7, "company": "Acme", "sic": sic,
            "kind": "cluster", "quality": quality, "parts": {}, "regime": regime,
            "price": {"price": 10.0, "asof": "2026-01-05 06:00", "source": "live"}, "total": 1e6, "insiders": 3,
            "liquidity": 5e6, "scores": dict(SCORES), "accs": ["a"], "returns": returns or {}, **kw}


def ret(excess, end="2026-02-04"):
    return {"end": end, "ret": excess + 0.01, "spy": 0.01, "excess": excess}


def chart(closes, start=dt.date(2026, 6, 1), volume=100_000):
    ts = [int(dt.datetime.combine(start + dt.timedelta(i), dt.time(14), dt.timezone.utc).timestamp())
          for i in range(len(closes))]
    return {"meta": {"currency": "USD"}, "timestamp": ts,
            "indicators": {"quote": [{"close": closes, "volume": [volume] * len(closes)}]}}


class Journal(unittest.TestCase):
    def test_load_save_and_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"STATE_FILE": os.path.join(tmp, "s.json"), "JOURNAL_FILE": ""}):
                self.assertEqual(journal.path(), journal.Path(tmp) / "journal.json")  # travels with the state file
                self.assertEqual(journal.load(), {"v": 1, "alerts": []})  # missing file -> empty journal
            p = journal.Path(tmp) / "j.json"
            p.write_text(json.dumps({"v": 1, "alerts": [{"id": "x", "date": "2026-01-01", "ticker": "A"}]}), "utf-8")
            j = journal.load(p)  # an older entry gains the fields later code reads
            self.assertEqual((j["alerts"][0]["returns"], j["alerts"][0]["sic"]), ({}, None))
            journal.record(j, entry(1))
            journal.save(j, p)
            self.assertEqual(journal.load(p)["alerts"][1]["ticker"], "ACME")
            self.assertFalse(p.with_suffix(".tmp").exists())

    def test_record_unique_ids(self):
        j = {"alerts": []}
        ids = [journal.record(j, {**entry(0), "id": "2026-09-24-ACME"})["id"] for _ in range(3)]
        self.assertEqual(ids, ["2026-09-24-ACME", "2026-09-24-ACME-2", "2026-09-24-ACME-3"])
        self.assertTrue(all(e["returns"] == {} for e in j["alerts"]))

    def test_concentration(self):
        j = {"alerts": [entry(1, date="2026-09-01", sic=3571), entry(2, date="2026-09-01", sic=3572),
                        entry(3, date="2026-01-01", sic=3571),  # older than 180 days: closed
                        entry(4, date="2026-09-01", sic=2834), entry(5, date="2026-09-01", sic=None)]}
        self.assertEqual(journal.concentration(j, 3579, TODAY), 2)
        self.assertEqual((journal.concentration(j, None, TODAY), journal.concentration(j, 1000, TODAY)), (0, 0))

    def test_followup(self):
        j = {"alerts": [entry(1, date="2026-06-01"), entry(2, date="2026-09-20"),
                        entry(3, date="2026-01-05", ticker="GONE")]}
        prices = {"ACME": 10.0, "SPY": 500.0}

        def close(t, day):
            if t == "GONE":
                return None  # delisted / no data: the horizon stays empty, never estimated
            late = day > dt.date(2026, 6, 1)
            return day, prices[t] * ((1.2 if t == "ACME" else 1.05) if late else 1)
        with mock.patch.object(market, "close_on", side_effect=close):
            self.assertEqual(journal.followup(j, TODAY), 2)  # ACME 30 + 90 days; the Sep alert is too young
            self.assertEqual(journal.followup(j, TODAY), 0)  # measured horizons are never re-fetched
        r = j["alerts"][0]["returns"]
        self.assertEqual(sorted(r), ["30", "90"])
        self.assertEqual((r["30"]["ret"], r["30"]["spy"], r["30"]["excess"]), (0.2, 0.05, 0.15))
        self.assertEqual((j["alerts"][1]["returns"], j["alerts"][2]["returns"]), ({}, {}))

    def test_stats_and_texts(self):
        j = {"alerts": [entry(1)]}
        self.assertIn("עדיין אין התראות", journal.stats_text(j))
        self.assertIn("היומן ריק", journal.journal_text({"alerts": []}))
        j = {"alerts": [entry(1, returns={"30": ret(0.10), "90": ret(0.20)}),
                        entry(2, ticker="BETA", quality=30, regime="panic", returns={"30": ret(-0.05)}),
                        entry(3, ticker="GAMA", quality=55, returns={"30": ret(0.02)}), entry(4, ticker="NEW")]}
        s = journal.stats(j)
        self.assertEqual((s["count"], s["horizons"][30]["n"], s["horizons"][90]["n"], s["horizons"][180]["n"]), (4, 3, 1, 0))
        self.assertAlmostEqual(s["horizons"][30]["hit"], 2 / 3)
        self.assertAlmostEqual(s["horizons"][30]["median"], 0.02)
        self.assertEqual(sorted(s["quality"]), ["0–39", "40–69", "70–100"])
        self.assertAlmostEqual(s["quality"]["70–100"]["mean"], 0.20)  # the longest horizon each alert reached
        self.assertEqual(s["best"][0][0]["ticker"], "ACME")
        self.assertEqual(s["worst"][0][0]["ticker"], "BETA")
        for text in (journal.stats_text(j), journal.journal_text(j, 3), journal.journal_text(j, 99)):
            self.assertEqual(common.rtl_bad_lines(text), [])
        self.assertIn("אחרי <code>30</code> יום: <code>3</code> התראות · הצלחה <code>67%</code>", journal.stats_text(j))
        self.assertIn("פאניקה <code>1</code>", journal.stats_text(j))
        text = journal.journal_text(j, 2)
        self.assertEqual(text.count("• התראה"), 2)
        self.assertIn("<code>NEW</code>", text.splitlines()[1])  # newest first
        self.assertIn("עוד אין תשואה", text)
        self.assertEqual(journal.journal_text(j, 0).count("• התראה"), 1)

    def test_entry_scores_and_context(self):
        ev = {"open": [row("a", "x", 1e6), row("b", "y", 2e6, insider_cik=None, insider="Y")], "cluster": (3, 3e6),
              "big": None, "score": 80, "parts": {"size": 20}}
        snap = scan.snapshot({**ev, "uniq": ev["open"]}, TODAY)
        e = journal.entry_for(ev, snap, {"tag": "panic"}, {"price": 4.5, "asof": "t", "source": "live", "split": 1},
                              1.5e6, SCORES, 3571, "Acme")
        self.assertEqual((e["id"], e["kind"], e["regime"], e["insiders"], e["price"]),
                         ("2026-09-24-ACME", "cluster", "panic", 2, {"price": 4.5, "asof": "t", "source": "live"}))
        self.assertEqual(journal.scores_of(None), {"piotroski": None, "altman": None, "beneish": None})
        self.assertEqual(journal.scores_of({"error": "x"})["altman"], None)
        res = {"pio": [{"ok": True}, {"ok": False}, {"ok": None}], "alt": {"kind": "Z''", "z": 3.456}, "ben": {"m": -1.234}}
        self.assertEqual(journal.scores_of(res), {"piotroski": "1/2", "altman": "Z'' 3.46", "beneish": -1.23})
        lines = journal.context_lines({**e, "price": {"price": 4.5, "asof": "2026-09-23 06:00", "source": "cache"}}, 3)
        text = "\n".join(lines)
        for want in ("(⚠ מחיר מ־<code>2026-09-23 06:00</code>)", "⚠ נזילות נמוכה", "ריכוז סקטוריאלי: כבר <code>3</code>",
                     "<code>SIC 35xx</code>", "בנייש <code>-2.4</code>"):
            self.assertIn(want, text)
        self.assertEqual(common.rtl_bad_lines(text), [])
        plain = "\n".join(journal.context_lines({**e, "liquidity": None, "scores": journal.scores_of(None),
                                                 "price": {"price": None}}, 2))
        self.assertNotIn("⚠", plain)
        self.assertEqual(plain.count("חסר"), 5)


class Market(unittest.TestCase):
    def test_history_liquidity_close_on(self):
        closes = [10.0] * 60 + [None] + [20.0] * 29
        with mock.patch.object(market, "chart", return_value=chart(closes)):
            bars = market.history("ACME")
            self.assertEqual(len(bars), 89)  # the null bar is dropped
            self.assertAlmostEqual(market.liquidity("ACME"), 20.0 * 100_000)  # the last 30 days only
            self.assertEqual(market.close_on("ACME", dt.date(2026, 6, 1)), (dt.date(2026, 6, 1), 10.0))
        with mock.patch.object(market, "chart", return_value=None):
            self.assertEqual((market.history("ACME"), market.liquidity("ACME"), market.close_on("ACME", TODAY)),
                             ([], None, None))
        with mock.patch.object(market, "chart", return_value=chart([5.0], volume=0)):
            self.assertIsNone(market.liquidity("ACME"))  # no traded volume -> unknown, not $0
        with mock.patch.object(market, "chart", return_value=chart([None, 7.0])):
            self.assertEqual(market.close_on("ACME", dt.date(2026, 6, 1))[1], 7.0)  # first session with a close


class Enrich(unittest.TestCase):
    def test_enrich_and_failures(self):
        with mock.patch.object(common, "submissions", return_value={"sic": "3571", "name": "Acme Corp"}), \
                mock.patch.object(market, "quote", return_value={"price": 3.0, "split": 1.0, "asof": "t", "source": "live"}), \
                mock.patch.object(market, "liquidity", return_value=900_000.0), \
                mock.patch.object(scan.fundamentals, "analyze", side_effect=RuntimeError("companyfacts 404")):
            j = {"alerts": [entry(i, date="2026-09-01", sic=3570) for i in range(3)]}
            lines, f = scan.enrich("7", "ACME", j, TODAY)
        self.assertEqual((f["sic"], f["liquidity"], f["company"], f["scores"]["altman"]), (3571, 900_000.0, "Acme Corp", None))
        text = "\n".join(lines)
        self.assertIn("⚠ נזילות נמוכה", text)
        self.assertIn("ריכוז סקטוריאלי", text)
        self.assertEqual(common.rtl_bad_lines(text), [])

    def test_alerts_carry_context_and_journal_entries(self):
        st = {"buys": [row("a1", "x", 60_000), row("a2", "y", 50_000), row("a3", "z", 40_000)], "alerted": {},
              "info": [], "regime": {"tag": "normal"}}
        ctx = (["💵 מחיר בהתראה: <code>$3.00</code> · נזילות: חסר"],
               {"sic": 3571, "quote": {"price": 3.0, "asof": "t", "source": "live"}, "liquidity": None,
                "scores": journal.scores_of(None), "company": "Acme"})
        with mock.patch.object(scan, "enrich", return_value=ctx) as en, mock.patch.object(scan, "links", return_value={}), \
                mock.patch.object(scan, "foreign", return_value=False), mock.patch.object(scan, "pay", return_value={}):
            msgs = scan.alerts(st, TODAY, {"alerts": []})
        self.assertEqual(len(msgs), 1)
        text, marks, snaps, entries = msgs[0]
        self.assertIn("💵 מחיר בהתראה", text)
        self.assertEqual((entries["7"]["ticker"], entries["7"]["sic"], entries["7"]["regime"]), ("ACME", 3571, "normal"))
        self.assertEqual(en.call_args.args[:2], ("7", "ACME"))

    def test_run_records_delivered_alerts(self):
        entry_ = entry(1, date=TODAY.isoformat())
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(os.environ, {"STATE_FILE": os.path.join(tmp, "s.json"), "JOURNAL_FILE": ""}), \
                mock.patch.object(scan, "scan_day", return_value="ok"), mock.patch.object(common, "send"), \
                mock.patch.object(market, "regime", return_value={"tag": "normal"}), \
                mock.patch.object(scan, "alerts", return_value=[["🔔", {"7": ["a"]}, {"7": {"id": entry_["id"],
                                                                 "date": TODAY.isoformat()}}, {"7": entry_}]]):
            scan._reported[0] = False
            scan.main(["--days", "20260923"])
            j = journal.load(journal.Path(tmp) / "journal.json")
            state = json.loads(journal.Path(tmp, "s.json").read_text("utf-8"))
        self.assertEqual([e["id"] for e in j["alerts"]], [entry_["id"]])
        self.assertEqual(state["sent"]["7"]["id"], entry_["id"])


class Followup(unittest.TestCase):
    def test_weekly_followup_mode(self):
        sent = []
        with tempfile.TemporaryDirectory() as tmp:
            p = journal.Path(tmp) / "journal.json"
            journal.save({"v": 1, "alerts": [entry(1, date="2026-06-01")]}, p)
            with mock.patch.dict(os.environ, {"JOURNAL_FILE": str(p), "SCAN_MODE": "followup",
                                              "HEARTBEAT_FILE": os.path.join(tmp, "hb")}), \
                    mock.patch.object(common, "send", side_effect=lambda t, **k: sent.append(t)), \
                    mock.patch.object(market, "close_on", side_effect=lambda t, d: (d, 11.0 if d > dt.date(2026, 6, 1) else 10.0)):
                scan._reported[0] = False
                scan.main([])
            self.assertEqual(sorted(journal.load(p)["alerts"][0]["returns"]), ["30", "90"])
            self.assertTrue(os.path.exists(os.path.join(tmp, "hb")))
        self.assertEqual(len(sent), 1)
        self.assertIn("סיכום שבועי", sent[0])
        self.assertIn("מדידות תשואה חדשות השבוע: <code>2</code>", sent[0])
        self.assertEqual(common.rtl_bad_lines(sent[0]), [])

    def test_dry_followup_does_not_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = journal.Path(tmp) / "journal.json"
            journal.save({"v": 1, "alerts": [entry(1, date="2026-06-01")]}, p)
            with mock.patch.dict(os.environ, {"JOURNAL_FILE": str(p)}), mock.patch.object(common, "send"), \
                    mock.patch.object(market, "close_on", return_value=(TODAY, 10.0)):
                scan.main(["--followup", "--dry"])
            self.assertEqual(journal.load(p)["alerts"][0]["returns"], {})


class CheckSizing(unittest.TestCase):
    def report(self, liq):
        with mock.patch.object(common, "tickers", return_value={"ACME": (7, "Acme")}), \
                mock.patch.object(common, "submissions", return_value={"sic": "3571", "sicDescription": "Computers"}), \
                mock.patch.object(common, "filings", return_value=[]), \
                mock.patch.object(market, "liquidity", return_value=liq), \
                mock.patch.object(check.fundamentals, "analyze", side_effect=RuntimeError("down")), \
                mock.patch.object(check.tenk, "analyze", return_value={"error": "no 10-K"}), \
                mock.patch.object(check.tenk, "format_he", return_value=["<b>גורמי סיכון</b>", "אין דוח"]), \
                mock.patch("traceback.print_exc"):
            return check.report("acme")

    def test_liquidity_and_sizing_lines(self):
        low = self.report(1_200_000.0)
        self.assertIn("💧 נזילות: <code>1.2M$</code> ליום (ממוצע 30 יום) · ⚠ נזילות נמוכה", low)
        self.assertIn("📏 כלל אצבע (מידע כללי, לא המלצה): 2–3% מהתיק, יציאה לפי זמן 12 חודשים · ⚠ נזילות נמוכה", low)
        self.assertIn("החלק \"דוחות כספיים\" נכשל", low)  # one broken section never kills the report
        self.assertIn("לא נמצאו רכישות", low)
        self.assertEqual(common.rtl_bad_lines(low), [])
        high = self.report(50_000_000.0)
        self.assertNotIn("⚠ נזילות נמוכה", high)
        self.assertIn("חסר (נתוני Yahoo לא זמינים)", self.report(None))

    def test_main_sends_with_prompt(self):
        with mock.patch.object(check, "report", return_value="📊 דוח"), mock.patch.object(common, "send") as snd, \
                mock.patch("sys.argv", ["check", "ACME"]):
            check.main()
        self.assertEqual(snd.call_args.kwargs, {"signal": True})
        with mock.patch.object(check, "report", side_effect=RuntimeError("SEC down")), \
                mock.patch.object(common, "send") as snd, mock.patch("sys.argv", ["check", "acme"]), \
                self.assertRaises(RuntimeError):
            check.main()
        self.assertIn("<code>ACME</code> נכשל", snd.call_args.args[0])
        with mock.patch("sys.argv", ["check"]), mock.patch.dict(os.environ, {"TICKER": ""}), self.assertRaises(SystemExit):
            check.main()


class SchemaCompat(unittest.TestCase):
    """7: state/journal files written by older versions still load, and the new code writes a readable superset."""

    def test_v1_state_and_journal_roundtrip(self):
        v1 = {"days": {"20260921": "ok"}, "alerted": {"7": ["a"]},
              "buys": [{"acc": "a", "cik": 7, "ticker": "ACME", "company": "Acme", "insider": "X", "role": "מנהל",
                        "insider_cik": "1", "od": True, "first": "2026-09-20", "last": "2026-09-20", "shares": 1,
                        "value": 5.0, "price": 5.0, "filed": "2026-09-21"}]}
        with tempfile.TemporaryDirectory() as tmp:
            p = journal.Path(tmp) / "state.json"
            p.write_text(json.dumps(v1), "utf-8")
            st = scan.prune(scan.load(p), TODAY)
            for k in scan.DEFAULTS:
                self.assertIn(k, st)
            b = st["buys"][0]
            self.assertEqual((b["kind"], b["weight"], b["plan_value"]), ("open", 1.0, 0))
            scan.save(p, st)
            again = scan.load(p)
            self.assertEqual(again["buys"][0]["acc"], "a")
            self.assertEqual(again["alerted"], {"7": ["a"]})  # the once-only guarantee survives the migration
            jp = journal.Path(tmp) / "journal.json"
            jp.write_text(json.dumps({"alerts": [{"id": "x", "date": "2026-09-01", "ticker": "ACME"}]}), "utf-8")
            j = journal.load(jp)
            self.assertEqual(j["v"], journal.SCHEMA)
            journal.stats_text(j)
            journal.journal_text(j)


if __name__ == "__main__":
    unittest.main()
