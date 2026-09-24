"""Offline smoke tests (stdlib unittest, no network): python -m unittest discover -s tests -v"""
import datetime as dt
import io
import os
import unittest
import urllib.error
from email.message import Message
from unittest import mock

from bot import check, common, form4, fundamentals, listen, scan, tenk

TX = ("<nonDerivativeTransaction><transactionDate><value>{d}</value></transactionDate><transactionCoding>"
      "<transactionCode>{c}</transactionCode></transactionCoding><transactionAmounts><transactionShares><value>{s}"
      "</value></transactionShares><transactionPricePerShare><value>{p}</value></transactionPricePerShare>"
      "<transactionAcquiredDisposedCode><value>{a}</value></transactionAcquiredDisposedCode></transactionAmounts>"
      "</nonDerivativeTransaction>")
F4 = ("<SEC-DOCUMENT><XML>\n<?xml version='1.0'?>\n<ownershipDocument><documentType>4</documentType><issuer>"
      "<issuerCik>0000012345</issuerCik><issuerName>Acme &amp; Co</issuerName><issuerTradingSymbol>ACME"
      "</issuerTradingSymbol></issuer><reportingOwner><reportingOwnerId><rptOwnerCik>0000000777</rptOwnerCik>"
      "<rptOwnerName>DOE JANE</rptOwnerName></reportingOwnerId><reportingOwnerRelationship><isDirector>true"
      "</isDirector><isOfficer>1</isOfficer><officerTitle>CEO</officerTitle></reportingOwnerRelationship>"
      "</reportingOwner><reportingOwner><reportingOwnerId><rptOwnerCik>888</rptOwnerCik><rptOwnerName>Doe Trust"
      "</rptOwnerName></reportingOwnerId><reportingOwnerRelationship><isTenPercentOwner>1</isTenPercentOwner>"
      "</reportingOwnerRelationship></reportingOwner><nonDerivativeTable>"
      + TX.format(d="2026-09-10", c="P", s="1000", p="10.00", a="A")
      + TX.format(d="2026-09-11-05:00", c="P", s="500", p="12", a="A")
      + TX.format(d="2026-09-12", c="S", s="700", p="13", a="D")
      + "</nonDerivativeTable></ownershipDocument></XML></SEC-DOCUMENT>")


class Resp:
    def __init__(self, body):
        self.body, self.headers = body, {}

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def http_error(code, body=b"", ctype="text/html"):
    h = Message()
    h["Content-Type"] = ctype
    return urllib.error.HTTPError("u", code, "x", h, io.BytesIO(body))


class Common(unittest.TestCase):
    def test_formatting(self):
        self.assertEqual([common.money(x) for x in (1234567, 999950, 950, -2.1e9, None)],
                         ["1.2M$", "1.0M$", "950$", "-2.1B$", "חסר"])
        self.assertEqual((common.price(12.3456), common.price(0.0035)), ("12.35$", "0.0035$"))
        self.assertEqual(common.visible("<b>a&amp;b</b> 📊"), 6)  # "a&b " + emoji (2 UTF-16 units)
        self.assertEqual(common.rtl_bad_lines("✅ שלום <code>AAPL</code>\nAAPL עולה\n<b>12</b> מניות"),
                         ["AAPL עולה", "12 מניות"])

    def test_chunks_and_send(self):
        text = "\n".join(f"• שורה {i} <code>{'x' * 90}</code>" for i in range(100))
        parts = common.chunks(text, 3000)
        self.assertEqual("\n".join(parts), text)
        self.assertTrue(all(common.visible(p) <= 3000 for p in parts) and len(parts) == 4)
        with mock.patch.dict(os.environ, {"TG_TOKEN": "", "TG_CHAT_ID": "", "GITHUB_ACTIONS": ""}), \
                mock.patch("builtins.print") as out:  # no token -> dry run prints each message
            common.send(text)
        msgs = [c.args[0] for c in out.call_args_list]
        self.assertEqual(len(msgs), len(common.chunks(text, 4000 - common.visible(common.DISCLAIMER))))
        self.assertTrue(len(msgs) > 1 and all(m.endswith(common.DISCLAIMER) and common.visible(m) <= 4096 for m in msgs))
        with mock.patch.dict(os.environ, {"TG_TOKEN": "", "GITHUB_ACTIONS": "true"}), self.assertRaises(SystemExit):
            common.send("שלום")

    def test_fetch_semantics(self):
        sec = "https://www.sec.gov/x"
        with mock.patch.dict(os.environ, {"SEC_UA": "t t@t.com"}), mock.patch.object(common.time, "sleep") as slp:
            for err in (http_error(404), http_error(403, b"<Error/>", "application/xml")):
                with mock.patch.object(common.urllib.request, "urlopen", side_effect=[err]):
                    self.assertIsNone(common.fetch(sec))  # 404 / S3 AccessDenied = "not published"
            with mock.patch.object(common.urllib.request, "urlopen",
                                   side_effect=[http_error(503), http_error(403), Resp(b"ok")]) as u:
                self.assertEqual(common.fetch(sec), b"ok")  # 5xx and SEC's HTML 403 are retried
            self.assertEqual(u.call_count, 3)
            self.assertIn(mock.call(1), slp.call_args_list)
            self.assertIn(mock.call(2), slp.call_args_list)
            with mock.patch.object(common.urllib.request, "urlopen", side_effect=[http_error(403)]), \
                    self.assertRaises(urllib.error.HTTPError):
                common.fetch("https://example.com/x")  # non-SEC 403 is not retried

    def test_tg_retry_after(self):
        calls = iter([http_error(429, b'{"parameters":{"retry_after":7}}'), b'{"ok":true,"result":1}'])

        def fake(*a, **k):
            x = next(calls)
            if isinstance(x, Exception):
                raise x
            return x
        with mock.patch.object(common, "fetch", side_effect=fake), mock.patch.object(common.time, "sleep") as slp:
            self.assertEqual(common.tg("sendMessage"), 1)
        slp.assert_called_with(8)

    def test_cik_tickers_prefers_common(self):
        m = {"SWAGW": (1, "W"), "SWAG": (1, "W"), "BRK-B": (2, "B"), "BRK-A": (2, "B"), "PHXE-P": (3, "P")}
        with mock.patch.object(common, "tickers", return_value=m):
            common.cik_tickers.cache_clear()
            self.assertEqual(common.cik_tickers(), {1: "SWAG", 2: "BRK-B", 3: "PHXE-P"})
        common.cik_tickers.cache_clear()


class Form4(unittest.TestCase):
    def test_parse(self):
        d = form4.parse(F4)
        self.assertEqual((d["issuer_cik"], d["issuer"], d["symbol"]), (12345, "Acme & Co", "ACME"))
        self.assertEqual(d["owners"][0], {"cik": "777", "name": "Doe Jane", "role": "דירקטור, CEO", "od": True})
        self.assertEqual((d["owners"][1]["role"], d["owners"][1]["od"]), ("בעל 10%", False))
        self.assertEqual([b["date"] for b in d["buys"]], ["2026-09-10", "2026-09-11"])  # the sale is excluded
        s = form4.summarize(d)
        self.assertEqual((s["shares"], s["value"], s["first"], s["last"]), (1500, 16000, "2026-09-10", "2026-09-11"))
        self.assertAlmostEqual(s["price"], 16000 / 1500)
        self.assertEqual(form4.insider(d)["name"], "Doe Jane")
        self.assertIsNone(form4.parse("<html>no xml</html>"))


def facts(vals, flow, unit="USD", form="10-K"):
    return {"units": {unit: [{"end": e, "val": v, "accn": "a" + e[:4], "form": form, "filed": f"{int(e[:4]) + 1}-02-15",
                              **({"start": e[:4] + "-01-01"} if flow else {})} for e, v in vals.items()]}}


Y = ("2023-12-31", "2024-12-31", "2025-12-31")
FLOWS = {"Revenues": (80, 100, 120), "CostOfRevenue": (50, 60, 66), "NetIncomeLoss": (5, 8, 12),
         "NetCashProvidedByUsedInOperatingActivities": (6, 10, 15), "OperatingIncomeLoss": (9, 14, 18),
         "WeightedAverageNumberOfSharesOutstandingBasic": (10, 10, 9.5)}
INSTS = {"Assets": (100, 110, 125), "AssetsCurrent": (40, 50, 60), "LiabilitiesCurrent": (30, 40, 40),
         "Liabilities": (50, 55, 60), "RetainedEarningsAccumulatedDeficit": (20, 25, 30), "StockholdersEquity": (50, 55, 65)}


def companyfacts():
    g = {t: facts(dict(zip(Y, v)), True, "shares" if "Shares" in t else "USD") for t, v in FLOWS.items()}
    g |= {t: facts(dict(zip(Y, v)), False) for t, v in INSTS.items()}
    g["Revenues"]["units"]["USD"] += [{"start": "2025-10-01", "end": "2025-12-31", "val": 999, "accn": "q",
                                       "form": "10-K", "filed": "2026-02-15"},  # a quarter inside a 10-K: ignored
                                      {"start": "2025-01-01", "end": "2025-12-31", "val": 777, "accn": "z",
                                       "form": "10-Q", "filed": "2026-03-01"}]  # not a 10-K: ignored
    dei = {"EntityCommonStockSharesOutstanding": facts({"2026-02-01": 10}, False, "shares", "10-Q")}
    return {"facts": {"us-gaap": g, "dei": dei}}


class Fundamentals(unittest.TestCase):
    def run_it(self, sic, quote=(50.0, 1.0), px="ACME"):
        with mock.patch.object(common, "get_json", return_value=companyfacts()), \
                mock.patch.object(common, "cik_tickers", return_value={1: px}), \
                mock.patch.object(fundamentals, "quote", return_value=quote):
            res = fundamentals.analyze(1, "ACME", sic)
        self.assertEqual(common.rtl_bad_lines("\n".join(fundamentals.format_he(res))), [])
        return res

    def test_bank(self):
        res = self.run_it(6022)
        self.assertEqual(res["fy"], list(reversed(Y)))
        self.assertEqual(res["v"]["rev"][:2], [120, 100])  # the 10-Q and the quarterly 10-K fact are ignored
        self.assertEqual([c["ok"] for c in res["pio"]], [True] * 9)
        self.assertIn("ltd0", res["notes"])  # missing LongTermDebt counted as 0, and said so
        self.assertTrue(res["fin"] and "alt" not in res)
        self.assertIn("9/9", "\n".join(fundamentals.format_he(res)))

    def test_altman_beneish(self):
        z = 1.2 * 20 / 125 + 1.4 * 30 / 125 + 3.3 * 18 / 125 + 1.0 * 120 / 125
        self.assertAlmostEqual(self.run_it(3571)["alt"]["z"], z + 0.6 * 500 / 60)
        self.assertAlmostEqual(self.run_it(3571, (50.0, 0.5))["alt"]["z"], z + 0.6 * 250 / 60)  # 1:2 split after
        r = self.run_it(3571, px="ACME-P")  # only a preferred ticker -> never price it, fall back to Z''
        self.assertEqual((r["alt"]["kind"], r["alt"]["why"]), ("Z''", "noncommon"))
        self.assertAlmostEqual(r["alt"]["z"], 6.56 * 20 / 125 + 3.26 * 30 / 125 + 6.72 * 18 / 125 + 1.05 * 65 / 60)
        self.assertNotIn("m", r["ben"])  # no receivables/PP&E/SG&A/depreciation tags -> no invented Beneish
        self.assertIn("AccountsReceivableNetCurrent", r["ben"]["miss"])


def risk_doc(sents):
    body = "".join(f"<p>{s}</p>" + ("<p>12.</p><p>Acme Corp.</p>" if i % 9 == 8 else "") for i, s in enumerate(sents))
    return ("<html><body><p>Table of Contents</p><p>Item 1A. Risk Factors 12</p><p>Item 1B. Unresolved Staff "
            "Comments 30</p><p>Item 1. Business</p><p>We make widgets, see Item 1A. Risk Factors below.</p>"
            f"<p>Item 1A. Risk Factors</p>{body}<p>Item 1B. Unresolved Staff Comments</p><p>None.</p>"
            "<p>Item 2. Properties</p></body></html>").encode()


WORDS = ("supply weather credit labor pricing currency software regulatory privacy climate energy shipping "
         "insurance pension patent brand retail wholesale lending hiring leasing tax audit freight "
         "semiconductor fraud cyber outsourcing logistics warranty recall inflation interest housing "
         "travel advertising licensing mining farming banking").split()[:40]
BASE = [f"Our {w} exposure could adversely affect results of operations, margins and the overall financial "
        f"condition of the {w} segment." for w in WORDS]


class Tenk(unittest.TestCase):
    def test_section_and_diff(self):
        cur = BASE[:36] + [BASE[36].replace("could", "may"), BASE[37].replace("margins", "profit margins"),
                           "Substantial doubt exists about our ability to continue as a going concern next year.",
                           "Recently imposed tariffs on imported components have increased our costs significantly."]
        docs = {"cur": risk_doc(cur), "pri": risk_doc(BASE)}
        sec = tenk.section(tenk.to_text(docs["cur"]))
        self.assertTrue(sec.startswith("Item 1A. Risk Factors\nOur supply"))  # not the TOC or the cross-reference
        self.assertNotIn("Unresolved", sec)
        rows = [{"form": "10-K", "primaryDocument": d, "accessionNumber": "0-0-" + d, "filingDate": f}
                for d, f in (("cur", "2026-02-01"), ("pri", "2025-02-01"))]
        with mock.patch.object(common, "fetch", side_effect=lambda url: docs[url.rsplit("/", 1)[1]]):
            r = tenk.analyze(1, rows)
        self.assertEqual((r["n_cur"], r["n_pri"], r["same"], r["edited"], r["new"], r["removed"]), (40, 40, 36, 2, 2, 2))
        self.assertEqual([h[0] for h in r["hits"]], [["going concern"], ["tariff"]])
        self.assertEqual(common.rtl_bad_lines("\n".join(tenk.format_he(r))), [])

    def test_keywords(self):
        hit = lambda s: [k for k, rx in tenk._KW.items() if rx.search(s)]
        self.assertEqual(hit("our amended and restated certificate of incorporation"), [])
        self.assertEqual(hit("the principal investigator reported"), [])
        self.assertEqual(hit("enabled by default"), [])
        self.assertEqual(hit("we restated our 2023 results"), ["restatement"])
        self.assertEqual(hit("we would be in default under the credit agreement"), ["default"])


TICKERS = {"AAPL": (1, "Apple"), "MSFT": (2, "Microsoft"), "ALL": (3, "Allstate"), "GOOD": (4, "Gladstone"),
           "IT": (5, "Gartner"), "BRK-B": (6, "Berkshire"), "TSLA": (7, "Tesla")}


class Listen(unittest.TestCase):
    def test_extract(self):
        cases = {"AAPL": ["AAPL"], "aapl": ["AAPL"], "$tsla?": ["TSLA"], "all good": [], "IT": ["IT"],
                 "I think IT is ON": [], "תבדוק לי את AAPL ו-MSFT": ["AAPL", "MSFT"], "מה דעתך על BRK.B": ["BRK-B"],
                 "מה ה-beta של TSLA?": ["TSLA"], "": []}
        with mock.patch.object(common, "tickers", return_value=TICKERS):
            for text, want in cases.items():
                self.assertEqual(listen.extract(text)[0], want, text)
            self.assertEqual(listen.extract("$zzz"), ([], ["ZZZ"]))

    def test_main_acks_first_and_filters_chat(self):
        ups = [{"update_id": 5, "message": {"chat": {"id": 42}, "text": "AAPL"}},
               {"update_id": 6, "message": {"chat": {"id": 9}, "text": "MSFT"}}]
        log = []

        def tg(method, **p):
            log.append(("ack" if "offset" in p else method, p.get("offset")))
            return ups if "offset" not in p else True
        with mock.patch.dict(os.environ, {"TG_TOKEN": "1:x", "TG_CHAT_ID": "42"}), \
                mock.patch.object(common, "tg", side_effect=tg), mock.patch.object(common, "tickers", return_value=TICKERS), \
                mock.patch.object(check, "report", side_effect=lambda t: log.append(("report", t)) or t), \
                mock.patch.object(common, "send"):
            listen.main()
        self.assertEqual(log, [("getUpdates", None), ("ack", 7), ("report", "AAPL")])


def buy(acc, cik, who, value, od=True, last="2026-09-20"):
    return {"acc": acc, "cik": cik, "ticker": f"T{cik}", "company": "Co", "insider": who, "role": "דירקטור",
            "insider_cik": who, "od": od, "first": last, "last": last, "shares": 1, "value": value, "price": 1.0,
            "filed": last}


class Scan(unittest.TestCase):
    today = dt.date(2026, 9, 24)

    def setUp(self):
        scan.published.cache_clear()  # the quarter listing is cached per process

    tearDown = setUp

    def test_listed_day_is_fetched(self):  # the positive path: a listed day is downloaded from the right quarter
        got, listing = [], {"directory": {"item": [{"name": "form.20260908.idx"}]}}
        with mock.patch.object(common, "get_json", side_effect=lambda u: got.append(u) or listing), \
                mock.patch.object(common, "fetch", return_value=b"hdr\n---\n") as f, \
                mock.patch.object(common, "cik_tickers", return_value={}):
            self.assertEqual(scan.scan_day("20260908", {"days": {}, "buys": [], "alerted": {}}, self.today), "ok")
        base = "https://www.sec.gov/Archives/edgar/daily-index/2026/QTR3/"
        self.assertEqual((got, f.call_args.args[0]), ([base + "index.json"], base + "form.20260908.idx"))

    def test_pick_and_prune(self):
        days = scan.pick({"days": {}}, self.today)
        self.assertEqual((len(days), days[0], days[-1]), (22, "20260825", "20260923"))  # first run: 30-day backfill
        done = {d: "ok" for d in days[:-7]}
        self.assertEqual(scan.pick({"days": done}, self.today), days[-5:])  # catch-up: at most 5, most recent
        st = scan.prune({"days": {"20260801": "ok"}, "buys": [buy("old", 1, "a", 1, last="2026-08-01")],
                         "alerted": {"1": ["old"]}}, self.today)
        self.assertEqual(st, {"days": {}, "buys": [], "alerted": {}})

    def test_rules_and_alert_once(self):
        st = {"days": {}, "alerted": {}, "buys": [buy("a1", 1, "x", 60000), buy("a2", 1, "y", 50000),  # cluster
                                                  buy("b1", 2, "z", 600000, od=False),  # big single buy (10% owner)
                                                  buy("c1", 3, "w", 50000)]}  # neither
        with mock.patch.object(scan, "foreign", return_value=False):
            msgs = scan.alerts(st, self.today)
            self.assertEqual([sorted(m) for _, m in msgs], [["1", "2"]])
            self.assertEqual(common.rtl_bad_lines(msgs[0][0]), [])
            for _, marks in msgs:
                st["alerted"].update(marks)
            self.assertEqual(scan.alerts(st, self.today), [])  # nothing new -> no repeat
            st["buys"].append(buy("a3", 1, "v", 1000))
            (text, marks), = scan.alerts(st, self.today)
            self.assertEqual((list(marks), text.count("🆕")), (["1"], 1))

    def test_listing_decides_holiday(self):  # SEC answers a missing index with 404, S3 403 or an HTML 503
        listing = {"directory": {"item": [{"name": "form.20260908.idx"}]}}
        st = {"days": {}, "buys": [], "alerted": {}}
        with mock.patch.object(common, "get_json", return_value=listing), \
                mock.patch.object(common, "fetch", side_effect=AssertionError("must not fetch an unlisted index")):
            self.assertEqual(scan.scan_day("20260907", st, self.today), "holiday")  # >= 3 days old
            self.assertIsNone(scan.scan_day("20260923", st, self.today))  # recent: not published yet, retry

    def test_failed_day_does_not_block_later_days(self):
        seen = []

        def fake(day, state, today):
            seen.append(day)
            if day == "20260907":
                raise RuntimeError("SEC 503")
            return "ok"
        with mock.patch.dict(os.environ, {"STATE_FILE": os.path.join(os.devnull, "none.json")}), \
                mock.patch.object(scan, "scan_day", side_effect=fake), mock.patch.object(scan, "alerts", return_value=[]), \
                mock.patch("sys.argv", ["scan", "--days", "20260907,20260908", "--dry"]), \
                mock.patch("traceback.print_exc"), self.assertRaises(SystemExit) as e:
            scan.main()
        self.assertEqual(seen, ["20260907", "20260908"])
        self.assertIn("20260907", str(e.exception.code))

    def test_scan_day(self):
        idx = ("Form Type   Company Name   CIK   Date Filed  File Name\n" + "-" * 20 + "\n"
               "4      Acme Co            12345   20260922  edgar/data/12345/0000-26-1.txt\n"
               "4      Doe Jane           777     20260922  edgar/data/777/0000-26-1.txt\n"  # same filing, owner line
               "4/A    Acme Co            12345   20260922  edgar/data/12345/0000-26-2.txt\n"  # amendment: skipped
               "4      Unlisted Inc       55      20260922  edgar/data/55/0000-26-3.txt\n").encode()
        other = form4.parse(F4.replace("0000012345", "55"))
        listing = {"directory": {"item": [{"name": "form.20260922.idx"}]}}
        with mock.patch.object(common, "fetch", return_value=idx), mock.patch.object(common, "get_json", return_value=listing), \
                mock.patch.object(scan.form4, "fetch", side_effect=lambda u: other if "/55/" in u else form4.parse(F4)) as f, \
                mock.patch.object(common, "cik_tickers", return_value={12345: "ACME"}), \
                mock.patch.object(common, "tickers", return_value={"ACME": (12345, "Acme & Co")}):
            st = {"days": {}, "buys": [], "alerted": {}}
            self.assertEqual(scan.scan_day("20260922", st, self.today), "ok")
        self.assertEqual(sorted(c.args[0].rsplit("/", 1)[1] for c in f.call_args_list), ["0000-26-1.txt", "0000-26-3.txt"])
        self.assertEqual([(b["acc"], b["ticker"], b["value"]) for b in st["buys"]], [("0000-26-1", "ACME", 16000.0)])


class Check(unittest.TestCase):
    def test_unknown_ticker(self):
        with mock.patch.object(common, "tickers", return_value={}):
            msg = check.report("zzzz")
        self.assertIn("<code>ZZZZ</code>", msg)
        self.assertEqual(common.rtl_bad_lines(msg), [])


if __name__ == "__main__":
    unittest.main()
