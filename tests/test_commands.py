"""Block 6 - commands: /status (heartbeat + watchdog), /stats, /journal, /verify, /health; legacy-row upgrade."""
import datetime as dt
import json
import os
import tempfile
import unittest
from unittest import mock

from bot import check, common, health, journal, listen, market, scan
from test_signals import doc, tx

TODAY = dt.date(2026, 9, 24)
TICKERS = {"ACME": (12345, "Acme Corp"), "AAPL": (320193, "Apple")}


def subs(*rows):
    """submissions JSON with (acc, form, filed) rows, newest first."""
    keys = ("accessionNumber", "form", "filingDate", "primaryDocument")
    return {"filings": {"recent": {k: [r[i] if i < 3 else f"xslF345X05/{r[0]}.xml" for r in rows]
                                   for i, k in enumerate(keys)}}}


def state_file(tmp, **st):
    p = os.path.join(tmp, "state.json")
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(st, fh)
    return p


class Status(unittest.TestCase):
    def test_heartbeat_line(self):
        self.assertIn("עוד אין נתונים", scan.heartbeat_line(None))
        ok = scan.heartbeat_line({"at": "2026-09-24 05:41 UTC", "ok": True, "filings": 350, "errors": 0, "alerts": 2})
        self.assertIn("תקינה ✅ · <code>350</code> הגשות · <code>0</code> שגיאות · <code>2</code> התראות", ok)
        bad = scan.heartbeat_line({"at": "x", "ok": False, "failed": ["20260923"]})
        self.assertIn("נכשלה ❌", bad)
        self.assertIn("<code>2026-09-23</code>", bad)
        for text in (ok, bad):
            self.assertEqual(common.rtl_bad_lines(text), [])

    def test_status_watchdog_state(self):
        run = {"created_at": "2026-09-24T14:00:05Z", "conclusion": "success", "event": "schedule", "status": "completed"}
        with tempfile.TemporaryDirectory() as tmp:
            p = state_file(tmp, days={"20260922": "ok"}, buys=[], alerted={},
                           heartbeat={"at": "2026-09-23 05:40 UTC", "ok": True, "filings": 1, "errors": 0, "alerts": 0})
            with mock.patch.dict(os.environ, {"STATE_FILE": p}), mock.patch.object(scan, "published", return_value=None), \
                    mock.patch.object(common, "runs", return_value=run):
                text = scan.status(TODAY)
                with open(p, "w", encoding="utf-8") as fh:
                    json.dump({"days": {"20260922": "ok", "20260923": "ok"}}, fh)
                fine = scan.status(TODAY)
        self.assertIn("⚠ עוד לא נסרקו <code>2026-09-23</code>", text)  # the previous trading day is missing
        self.assertIn("ריצה אחרונה <code>2026-09-24 14:00</code> <code>success</code>", text)
        self.assertIn("💓 ריצה אחרונה: <code>2026-09-23 05:40 UTC</code>", text)
        self.assertIn("תקין, יום המסחר הקודם <code>2026-09-23</code> נסרק", fine)
        for t in (text, fine):
            self.assertEqual(common.rtl_bad_lines(t), [])


class Verify(unittest.TestCase):
    def run_verify(self, docs, rows, state):
        """docs: acc -> parsed Form 4 (or False = download failed); rows: submissions rows."""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"STATE_FILE": state_file(tmp, **state)}), \
                mock.patch.object(common, "tickers", return_value=TICKERS), \
                mock.patch.object(common, "submissions", return_value=subs(*rows)), \
                mock.patch.object(scan, "links", return_value={}), mock.patch.object(scan, "pay", return_value={}), \
                mock.patch.object(scan, "_get", side_effect=lambda u: docs[u.rsplit("/", 1)[1][:-4]]):
            text = scan.verify("acme", TODAY)
        self.assertEqual(common.rtl_bad_lines(text), [], text)
        return text

    def test_unknown_and_empty(self):
        with mock.patch.object(common, "tickers", return_value=TICKERS):
            self.assertIn("לא מצאתי את הטיקר <code>ZZZ</code>", scan.verify("zzz", TODAY))
        sell = doc([tx("S", 100, 10, ad="D")])
        text = self.run_verify({"a1": sell, "a2": False}, [("a1", "4", "2026-09-22"), ("a2", "4", "2026-09-22")], {})
        self.assertIn("(<code>1</code> נכשלו)", text)
        self.assertIn("לא נמצאו רכישות", text)

    def test_cluster_matches_state(self):
        docs = {f"a{i}": doc([tx("P", 1000, 50 + i)], owner=f"D{i}", ocik=str(100 + i)) for i in range(3)}
        docs["x"] = doc([tx("P", 10, 1)], issuer="999")  # the issuer's CIK appears as an owner elsewhere
        rows = [(a, "4", "2026-09-22") for a in docs] + [("old", "4", "2026-08-01")]  # before the window
        with mock.patch.object(common, "tickers", return_value=TICKERS):
            fresh = {a: scan.make_row(a, docs[a], docs[a]["buys"], "ACME", "2026-09-22") for a in ("a0", "a1", "a2")}
        state = {"buys": list(fresh.values()), "alerted": {"12345": ["a0"]}}
        text = self.run_verify(docs, rows, state)
        for want in ("<b>אשכול רכישות</b>", "<code>3</code> דיווחי רכישה · <code>3</code> רוכשים",
                     "השוואה לסריקה: ✅ תואם", "התראה נשלחה על החברה: כן"):
            self.assertIn(want, text)
        # the scan missed a filing and holds a different amount for another -> both reported
        changed = {**fresh["a1"], "value": 1.0}
        text = self.run_verify(docs, rows, {"buys": [fresh["a0"], changed], "alerted": {}})
        self.assertIn("⚠ יש הבדלים · חדשים שהסריקה עוד לא ראתה: <code>1</code>", text)
        self.assertIn("התראה נשלחה על החברה: לא", text)
        text = self.run_verify(docs, rows, {})
        self.assertIn("אין לה רכישות בשוק הפתוח של החברה בזיכרון (הדיווחים חדשים מדי", text)

    def test_amendment_and_plan(self):
        orig = doc([tx("P", 1000, 1000)], owner="BIG", ocik="7")
        fix = doc([tx("P", 1000, 900)], owner="BIG", ocik="7", form="4/A", original="2026-09-21")
        plan = doc([tx("P", 10, 10, notes=("F1",))], owner="PLAN", ocik="8", notes={"F1": "Rule 10b5-1 plan"})
        text = self.run_verify({"o": orig, "f": fix, "p": plan},
                               [("f", "4/A", "2026-09-23"), ("p", "4", "2026-09-22"), ("o", "4", "2026-09-21")], {})
        self.assertIn("<b>רכישה גדולה</b>", text)
        self.assertIn("<code>900.0K$</code>", text)  # the 4/A replaced the original purchase, not added to it
        self.assertIn("<code>1</code> בתוכנית <code>10b5-1</code> (לא נספרו)", text)


class Health(unittest.TestCase):
    def test_report(self):
        index = {"directory": {"item": [{"name": "form.20260922.idx"}, {"name": "form.20260923.idx"}, {"name": "x.gz"}]}}
        bodies = {"daily-index": index, "companyconcept": {"entityName": "Apple Inc."},
                  "efts": {"hits": {"total": {"value": 297}}}}

        def fetch(url, *a, **k):
            if "efts" in url:
                raise OSError("blocked")
            return json.dumps(next(v for key, v in bodies.items() if key in url)).encode()
        runs = {"daily-scan.yml": {"created_at": "2026-09-24T05:41:00Z", "conclusion": "success", "event": "schedule",
                                   "status": "completed"},
                "telegram-listen.yml": {"created_at": "2026-09-24T17:10:00Z", "conclusion": None, "event": "push",
                                        "status": "in_progress"}}
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(common, "fetch", side_effect=fetch), \
                mock.patch.dict(os.environ, {"STATE_FILE": state_file(tmp, days={"20260923": "ok"})}), \
                mock.patch.object(market, "chart", return_value={"meta": {"regularMarketPrice": 612.5}}), \
                mock.patch.object(common, "tg", return_value={"username": "Danielsuibot"}), \
                mock.patch.object(scan, "published", return_value=None), \
                mock.patch.object(common, "runs", side_effect=lambda wf, **k: runs.get(wf)):
            text = health.report(TODAY)
            os.environ["STATE_FILE"] = os.path.join(tmp, "missing.json")
            with mock.patch.object(market, "chart", return_value=None):
                bare = health.report(TODAY)
        for want in ("✅ האינדקס היומי של <code>SEC EDGAR</code>: תקין", "האינדקס האחרון <code>2026-09-23</code>",
                     "<code>Apple Inc.</code>", "❌ חיפוש טקסט מלא <code>EFTS</code> (גיבוי לאינדקס): נכשל",
                     "<code>SPY</code> <code>612.50$</code>", "הבוט <code>@Danielsuibot</code>",
                     "• סריקה יומית: ✅ ריצה אחרונה <code>2026-09-24 05:41</code>",
                     "• שומר ימים חסרים: אין מידע", "• מאזין טלגרם: ⏳", "💓 דופק", "🐕 שומר ימים חסרים: תקין",
                     "ימים ממתינים לסריקה: <code>"):
            self.assertIn(want, text)
        self.assertIn("❌ מחירי <code>Yahoo</code>: נכשל", bare)
        self.assertIn("עוד אין קובץ", bare)
        for t in (text, bare):
            self.assertEqual(common.rtl_bad_lines(t), [], t)

    def test_quick_fetch_404(self):
        with mock.patch.object(common, "fetch", return_value=None):
            self.assertIn("HTTP 404", health.probe("בדיקה", health._index))


class Commands(unittest.TestCase):
    def run_handle(self, text):
        sent = []
        with mock.patch.object(common, "tickers", return_value=TICKERS), \
                mock.patch.object(common, "send", side_effect=lambda t, **k: sent.append((t, k))), \
                mock.patch.object(common, "tg"):
            listen.handle(text)
        for t, _ in sent:
            self.assertEqual(common.rtl_bad_lines(t), [], t)
        return sent

    def test_new_commands(self):
        j = {"v": 1, "alerts": [{"id": f"2026-09-0{i}-ACME", "date": f"2026-09-0{i}", "ticker": "ACME", "quality": 70,
                                 "price": {"price": 5.0}, "returns": {}} for i in range(1, 8)]}
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "journal.json")
            journal.save(j, journal.Path(p))
            with mock.patch.dict(os.environ, {"JOURNAL_FILE": p}):
                self.assertIn("התראות ביומן: <code>7</code>", self.run_handle("/stats")[0][0])
                self.assertEqual(self.run_handle("/journal 3")[0][0].count("• התראה"), 3)
                self.assertEqual(self.run_handle("/journal")[0][0].count("• התראה"), 5)  # default
                self.assertEqual(self.run_handle("/journal lots")[0][0].count("• התראה"), 5)
        with mock.patch.object(scan, "verify", return_value="🔎 אימות") as v:
            self.assertEqual(self.run_handle("/verify $acme"), [("🔎 אימות", {"signal": True})])
        v.assert_called_once_with("ACME")
        self.assertIn("/verify AAPL", self.run_handle("/verify")[0][0])
        self.assertIn("<code>ZZZZ</code>", self.run_handle("/verify zzzz")[0][0])
        with mock.patch.object(health, "report", return_value="🩺 בריאות"):
            self.assertEqual(self.run_handle("/health@Danielsuibot"), [("🩺 בריאות", {})])

    def test_help_and_menu(self):
        names = [c for c, _ in listen.COMMANDS]
        self.assertEqual(names, ["check", "scan", "status", "stats", "journal", "verify", "health", "help"])
        self.assertTrue(all(1 <= len(d) <= 256 for _, d in listen.COMMANDS))
        for c in names:
            self.assertIn(f"/{c}", listen.HELP)
        self.assertEqual(common.rtl_bad_lines(listen.HELP), [])

    def test_check_reply_has_prompt(self):
        with mock.patch.object(check, "report", return_value="📊 דוח"):
            self.assertEqual(self.run_handle("/check acme"), [("📊 דוח", {"signal": True})])


class Legacy(unittest.TestCase):
    """Rows saved before schema 2 carry no trade signature, so joint filings cannot be merged: their days are re-read
    (a few per run) and the rebuilt rows replace them under the same accession - alert marks stay valid."""

    def test_legacy_days_are_reread_once(self):
        legacy = [{"acc": f"L{d}", "cik": 7, "ticker": "ACME", "company": "Acme", "insider": "X", "role": "דירקטור",
                   "insider_cik": "1", "od": True, "first": f"2026-09-{d:02d}", "last": f"2026-09-{d:02d}", "shares": 1,
                   "value": 5.0, "price": 5.0, "filed": f"2026-09-{d:02d}"} for d in (8, 9, 10, 11, 14, 15, 16)]
        days = {f"202609{d:02d}": "ok" for d in (8, 9, 10, 11, 14, 15, 16, 21, 22, 23)}
        calls = []

        def fake(day, state, today, stats=None, rescan=False):
            calls.append((day, rescan))
            return "ok"
        with tempfile.TemporaryDirectory() as tmp:
            p = state_file(tmp, days=days, buys=legacy, alerted={"7": ["L8"]})
            with mock.patch.dict(os.environ, {"STATE_FILE": p}), mock.patch.object(scan, "scan_day", side_effect=fake), \
                    mock.patch.object(common, "send"), mock.patch.object(scan, "alerts", return_value=[]), \
                    mock.patch.object(market, "regime", return_value={"tag": "normal"}), \
                    mock.patch.object(scan, "pick", return_value=[]):
                scan._reported[0] = False
                scan.main([])
                first = list(calls)
                calls.clear()
                scan.main([])
            st = json.loads(journal.Path(p).read_text("utf-8"))
        rescans = [d for d, r in first if r]
        self.assertEqual(rescans[:3], ["20260921", "20260922", "20260923"])  # the usual late-filing rescan
        self.assertEqual(rescans[3:], ["20260910", "20260911", "20260914", "20260915", "20260916"])  # newest 5 legacy
        self.assertEqual([d for d, r in calls if r][3:], ["20260908", "20260909"])  # the rest on the next run
        self.assertTrue(all("sig" in b for b in st["buys"]))
        self.assertEqual(st["alerted"], {"7": ["L8"]})

    def test_reread_original_keeps_its_amendment(self):
        idx = ("4  Acme  12345  20260922  edgar/data/12345/o.txt\n").encode()
        orig = doc([tx("P", 1000, 10)], owner="BIG", ocik="7")
        with mock.patch.object(common, "tickers", return_value=TICKERS):
            amended = {**scan.make_row("o", orig, orig["buys"], "ACME", "2026-09-22"), "value": 1.0, "amended_by": "f"}
        st = {"days": {}, "buys": [amended], "alerted": {}}
        with mock.patch.object(common, "tickers", return_value=TICKERS), \
                mock.patch.object(scan, "index_urls", return_value=({"o": "u/o.txt"}, "index")), \
                mock.patch.object(scan, "_get", return_value=orig), \
                mock.patch.object(common, "cik_tickers", return_value={12345: "ACME"}):
            self.assertEqual(scan.scan_day("20260922", st, TODAY, rescan=True), "ok")
        self.assertEqual((st["buys"][0]["value"], st["buys"][0]["amended_by"]), (1.0, "f"))


if __name__ == "__main__":
    unittest.main()
