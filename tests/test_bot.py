"""Offline smoke tests (stdlib unittest, no network): python -m unittest discover -s tests -v"""
import datetime as dt
import json
import io
import os
import tempfile
import unittest
import urllib.error
from email.message import Message
from unittest import mock

from bot import check, common, form4, fundamentals, listen, market, scan, tenk

# alerts() enriches each alert from EDGAR/Yahoo (block 5); unit tests replace that with an all-missing context
EMPTY_CONTEXT = ([], {"sic": None, "quote": {"price": None, "split": 1.0, "asof": None, "source": None},
                      "liquidity": None, "scores": {"piotroski": None, "altman": None, "beneish": None},
                      "company": None})
NO_ENRICH = mock.patch.object(scan, "enrich", return_value=EMPTY_CONTEXT)

TX = ("<nonDerivativeTransaction><transactionDate><value>{d}</value></transactionDate><transactionCoding>"
      "<transactionCode>{c}</transactionCode></transactionCoding><transactionAmounts><transactionShares><value>{s}"
      "</value></transactionShares><transactionPricePerShare><value>{p}</value></transactionPricePerShare>"
      "<transactionAcquiredDisposedCode><value>{a}</value></transactionAcquiredDisposedCode></transactionAmounts>"
      "<ownershipNature><directOrIndirectOwnership><value>{o}</value></directOrIndirectOwnership></ownershipNature>"
      "</nonDerivativeTransaction>")
F4 = ("<SEC-DOCUMENT><XML>\n<?xml version='1.0'?>\n<ownershipDocument><documentType>4</documentType><issuer>"
      "<issuerCik>0000012345</issuerCik><issuerName>Acme &amp; Co</issuerName><issuerTradingSymbol>ACME"
      "</issuerTradingSymbol></issuer><reportingOwner><reportingOwnerId><rptOwnerCik>0000000777</rptOwnerCik>"
      "<rptOwnerName>DOE JANE</rptOwnerName></reportingOwnerId><reportingOwnerRelationship><isDirector>true"
      "</isDirector><isOfficer>1</isOfficer><officerTitle>CEO</officerTitle></reportingOwnerRelationship>"
      "</reportingOwner><reportingOwner><reportingOwnerId><rptOwnerCik>888</rptOwnerCik><rptOwnerName>Doe Trust"
      "</rptOwnerName></reportingOwnerId><reportingOwnerRelationship><isTenPercentOwner>1</isTenPercentOwner>"
      "</reportingOwnerRelationship></reportingOwner><nonDerivativeTable>"
      + TX.format(d="2026-09-10", c="P", s="1000", p="10.00", a="A", o="D")
      + TX.format(d="2026-09-11-05:00", c="P", s="500", p="12", a="A", o="I")
      + TX.format(d="2026-09-12", c="S", s="700", p="13", a="D", o="D")
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
        self.assertEqual(d["owners"][0], {"cik": "777", "name": "Doe Jane", "role": "דירקטור, CEO", "od": True,
                                          "weight": 1.5})  # CEO: top role weight
        self.assertEqual((d["owners"][1]["role"], d["owners"][1]["od"]), ("בעל 10%", False))
        self.assertEqual([(b["date"], b["indirect"]) for b in d["buys"]], [("2026-09-10", False), ("2026-09-11", True)])  # sale excluded
        s = form4.summarize(d)
        self.assertEqual((s["shares"], s["value"], s["first"], s["last"]), (1500, 16000, "2026-09-10", "2026-09-11"))
        self.assertAlmostEqual(s["price"], 16000 / 1500)
        self.assertEqual(form4.insider(d)["name"], "Doe Jane")
        self.assertIsNone(form4.parse("<html>no xml</html>"))
        self.assertEqual((s["sig"], s["indirect"]), ([["2026-09-10", 1000.0, 10.0], ["2026-09-11", 500.0, 12.0]], True))

    def test_joint_reports_count_once(self):  # ETRA 2026-09-21: OrbiMed and its director filed the same trades
        fund = {"name": "OrbiMed", "sig": [["2026-09-21", 1e6, 15.0]], "indirect": True, "value": 15e6}
        out = form4.joint([fund, dict(fund, name="Gordon")])
        self.assertEqual([(x["name"], x["value"]) for x in out], [("OrbiMed / Gordon", 15e6)])
        direct = dict(fund, indirect=False)
        self.assertEqual(len(form4.joint([direct, dict(direct, name="Twin")])), 2)  # two direct buyers stay two
        self.assertEqual(len(form4.joint([{"name": "a"}, {"name": "b"}])), 2)  # old state rows (no sig) never merge


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
        self.assertEqual([(c["kind"], c["labels"]) for c in r["top"]],
                         [("new", ["going concern"]), ("new", ["tariff"])])
        self.assertEqual(r["boiler"], 2)  # could -> may (hedge to hedge), margins -> profit margins
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
    def test_extract(self):  # unknown text = ticker(s); Hebrew/long sentences only yield $ or ALL-CAPS symbols
        cases = {"AAPL": ["AAPL"], "aapl": [], "$tsla?": ["TSLA"], "aapl msft": [], "AAPL MSFT": ["AAPL", "MSFT"],
                 "all good": [], "ok thanks": [], "hi": [], "IT": [], "$it": ["IT"], "I think IT is ON": [],
                 "תבדוק לי את AAPL ו-MSFT": ["AAPL", "MSFT"], "מה דעתך על BRK.B": ["BRK-B"],
                 "מה ה-beta של TSLA?": ["TSLA"], "": []}
        with mock.patch.object(common, "tickers", return_value=TICKERS):
            for text, want in cases.items():
                self.assertEqual(listen.extract(text)[0], want, text)
            self.assertEqual(listen.extract("$zzz"), ([], ["ZZZ"]))
            self.assertEqual(listen.extract("aapl msft tsla it", loose=True)[0], ["AAPL", "MSFT", "TSLA", "IT"])

    def run_handle(self, text, env=None):
        """handle(text) with Telegram/report/scan stubbed -> (return value, [sent texts], [tg calls])."""
        sent, calls = [], []
        with mock.patch.dict(os.environ, env or {}), mock.patch.object(common, "tickers", return_value=TICKERS), \
                mock.patch.object(common, "send", side_effect=lambda t, **k: sent.append(t)), \
                mock.patch.object(common, "tg", side_effect=lambda m, **p: calls.append(m)), \
                mock.patch.object(check, "report", side_effect=lambda t: f"דוח {t}"):
            res = listen.handle(text)
        for s in sent:
            self.assertEqual(common.rtl_bad_lines(s), [], s)
        return res, sent, calls

    def test_commands(self):
        for cmd in ("/help", "/start", "/HELP@Danielsuibot", "/foo", "/scanner"):  # unknown commands get help too
            res, sent, calls = self.run_handle(cmd)
            self.assertEqual((res, sent, calls), (0, [listen.HELP], ["setMyCommands"]), cmd)
        self.assertEqual(self.run_handle("/check aapl msft")[1], ["דוח AAPL", "דוח MSFT"])
        self.assertEqual(self.run_handle("/check@Danielsuibot\ntsla")[1], ["דוח TSLA"])
        res, sent, _ = self.run_handle("/check")
        self.assertTrue(res == 0 and "/check AAPL" in sent[0])  # usage
        self.assertEqual(self.run_handle("MSFT")[1], ["דוח MSFT"])  # plain text: ALL-CAPS ticker
        for text in ("all good", "ok thanks", "hi", "msft"):  # lowercase words are never tickers (4.7)
            res, sent, _ = self.run_handle(text)
            self.assertTrue(res == 0 and len(sent) == 1 and "לא זיהיתי טיקר" in sent[0], text)
        res, sent, _ = self.run_handle("$zzzzq")
        self.assertIn("<code>ZZZZQ</code>", sent[0])  # not a listed ticker -> says so
        with mock.patch.object(scan, "status", return_value="📋 מצב"):
            self.assertEqual(self.run_handle("/status")[1], ["📋 מצב"])

    def test_scan_local_runs_in_process(self):
        with mock.patch.object(scan, "main") as m:
            res, sent, _ = self.run_handle("/scan", {"GITHUB_REPOSITORY": "", "GITHUB_TOKEN": ""})
        m.assert_called_once_with(["--notify"])
        self.assertEqual((res, len(sent)), (0, 1))

    def test_scan_on_actions_dispatches_workflow(self):
        env = {"GITHUB_REPOSITORY": "o/r", "GITHUB_TOKEN": "t", "GITHUB_REF_NAME": "main"}
        with mock.patch.object(common, "fetch", return_value=b"") as f, mock.patch.object(scan, "main") as m:
            res, sent, _ = self.run_handle("/scan", env)
        url, data, headers = f.call_args.args[:3]
        self.assertEqual(url, "https://api.github.com/repos/o/r/actions/workflows/daily-scan.yml/dispatches")
        self.assertEqual(json.loads(data), {"ref": "main", "inputs": {"notify": "true"}})
        self.assertEqual((headers["Authorization"], res, len(sent), m.called), ("Bearer t", 0, 1, False))
        with mock.patch.object(common, "fetch", side_effect=http_error(403)):
            res, sent, _ = self.run_handle("/scan", env)
        self.assertTrue(res == 1 and "HTTP 403" in sent[0] and "actions: write" in sent[0])  # not "SEC is down"

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

    def test_failed_report_is_reported_and_turns_run_red(self):
        ups = [{"update_id": 1, "message": {"chat": {"id": 42}, "text": "AAPL MSFT"}}]
        sent = []
        with mock.patch.dict(os.environ, {"TG_TOKEN": "1:x", "TG_CHAT_ID": "42"}), \
                mock.patch.object(common, "tg", side_effect=lambda m, **p: ups if "offset" not in p else True), \
                mock.patch.object(common, "tickers", return_value=TICKERS), mock.patch("traceback.print_exc"), \
                mock.patch.object(check, "report", side_effect=lambda t: t if t == "MSFT" else 1 / 0), \
                mock.patch.object(common, "send", side_effect=lambda t, **k: sent.append(t)), self.assertRaises(SystemExit):
            listen.main()
        self.assertEqual(len(sent), 2)  # AAPL: Hebrew failure note; MSFT: its report still goes out
        self.assertIn("<code>AAPL</code>", sent[0])
        self.assertEqual(sent[1], "MSFT")


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
        self.assertEqual((st["days"], st["buys"], st["alerted"], st["info"]), ({}, [], {}, []))

    def test_rules_and_alert_once(self):
        st = {"days": {}, "alerted": {}, "buys": [buy("a1", 1, "x", 60000), buy("a2", 1, "y", 50000),  # cluster
                                                  buy("a4", 1, "u", 20000),  # (3 distinct insiders since v2)
                                                  buy("b1", 2, "z", 600000, od=False),  # big single buy (10% owner)
                                                  buy("c1", 3, "w", 50000)]}  # neither
        with mock.patch.object(scan, "foreign", return_value=False), mock.patch.object(scan, "links", return_value={}), NO_ENRICH:
            msgs = scan.alerts(st, self.today)
            self.assertEqual([sorted(m) for _, m, *_ in msgs], [["1", "2"]])
            self.assertEqual(common.rtl_bad_lines(msgs[0][0]), [])
            for _, marks, *_ in msgs:
                st["alerted"].update(marks)
            self.assertEqual(scan.alerts(st, self.today), [])  # nothing new -> no repeat
            st["buys"].append(buy("a3", 1, "v", 1000))
            (text, marks, *_), = scan.alerts(st, self.today)
            self.assertEqual((list(marks), text.count("🆕")), (["1"], 1))

    def test_joint_report_is_not_a_cluster(self):
        sig = [["2026-09-21", 1000000.0, 15.0]]
        st = {"days": {}, "alerted": {}, "buys": [dict(buy("e1", 9, "fund", 15e6), sig=sig, indirect=True),
                                                  dict(buy("e2", 9, "partner", 15e6), sig=sig, indirect=True)]}
        with mock.patch.object(scan, "foreign", return_value=False), mock.patch.object(scan, "links", return_value={}), NO_ENRICH:
            (text, marks, *_), = scan.alerts(st, self.today)
        self.assertEqual(sorted(marks["9"]), ["e1", "e2"])  # both filings are marked as alerted
        self.assertIn("רכישה גדולה", text)
        self.assertNotIn("אשכול", text)  # one purchase, so no 2-insider cluster
        self.assertIn("<code>15.0M$</code>", text)
        self.assertNotIn("30.0M$", text)  # dollars counted once

    def test_listing_decides_holiday(self):  # SEC answers a missing index with 404, S3 403 or an HTML 503
        listing = {"directory": {"item": [{"name": "form.20260908.idx"}]}}
        st = {"days": {}, "buys": [], "alerted": {}}
        with mock.patch.object(common, "get_json", return_value=listing), \
                mock.patch.object(common, "fetch", side_effect=AssertionError("must not fetch an unlisted index")):
            self.assertEqual(scan.scan_day("20260907", st, self.today), "holiday")  # >= 3 days old
            self.assertIsNone(scan.scan_day("20260923", st, self.today))  # recent: not published yet, retry

    def test_failed_day_does_not_block_later_days(self):
        seen = []

        def fake(day, state, today, stats=None, rescan=False):
            seen.append(day)
            if day == "20260907":
                raise RuntimeError("SEC 503")
            return "ok"
        with mock.patch.dict(os.environ, {"STATE_FILE": os.path.join(os.devnull, "none.json")}), \
                mock.patch.object(scan, "scan_day", side_effect=fake), mock.patch.object(scan, "alerts", return_value=[]), \
                mock.patch.object(market, "regime", return_value={"tag": "normal"}), \
                mock.patch("sys.argv", ["scan", "--days", "20260907,20260908", "--dry"]), \
                mock.patch("traceback.print_exc"), self.assertRaises(SystemExit) as e:
            scan.main()
        self.assertEqual(seen, ["20260907", "20260908"])
        self.assertIn("20260907", str(e.exception.code))

    def test_scan_day(self):
        idx = ("Form Type   Company Name   CIK   Date Filed  File Name\n" + "-" * 20 + "\n"
               "4      Acme Co            12345   20260922  edgar/data/12345/0000-26-1.txt\n"
               "4      Doe Jane           777     20260922  edgar/data/777/0000-26-1.txt\n"  # same filing, owner line
               "4/A    Acme Co            12345   20260922  edgar/data/12345/0000-26-2.txt\n"  # amends 0000-26-1
               "4      Unlisted Inc       55      20260922  edgar/data/55/0000-26-3.txt\n").encode()
        other = form4.parse(F4.replace("0000012345", "55"))
        fix = form4.parse(F4.replace("<documentType>4</documentType>", "<documentType>4/A</documentType>"
                                     "<dateOfOriginalSubmission>2026-09-22</dateOfOriginalSubmission>")
                          .replace("<value>10.00</value>", "<value>11.00</value>"))  # corrected price
        listing = {"directory": {"item": [{"name": "form.20260922.idx"}]}}
        with mock.patch.object(common, "fetch", return_value=idx), mock.patch.object(common, "get_json", return_value=listing), \
                mock.patch.object(scan.form4, "fetch", side_effect=lambda u: other if "/55/" in u else fix if u.endswith("-2.txt") else form4.parse(F4)) as f, \
                mock.patch.object(common, "cik_tickers", return_value={12345: "ACME"}), \
                mock.patch.object(common, "tickers", return_value={"ACME": (12345, "Acme & Co")}):
            st = {"days": {}, "buys": [], "alerted": {}}
            self.assertEqual(scan.scan_day("20260922", st, self.today), "ok")
        self.assertEqual(sorted(c.args[0].rsplit("/", 1)[1] for c in f.call_args_list),
                         ["0000-26-1.txt", "0000-26-2.txt", "0000-26-3.txt"])
        # the 4/A replaces the original row under the ORIGINAL accession: corrected $, never a second row
        self.assertEqual([(b["acc"], b["ticker"], b["value"], b.get("amended_by")) for b in st["buys"]],
                         [("0000-26-1", "ACME", 17000.0, "0000-26-2")])


class ScanCommands(unittest.TestCase):
    today = dt.date(2026, 9, 24)

    def test_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "state.json")
            with mock.patch.dict(os.environ, {"STATE_FILE": path}), mock.patch.object(scan, "published", return_value=None),                     mock.patch.object(common, "runs", return_value=None):
                self.assertIn("עוד לא רצה", scan.status(self.today))
                with open(path, "w", encoding="utf-8") as fh:
                    json.dump({"days": {"20260921": "ok", "20260922": "ok", "20260907": "holiday"},
                               "buys": [buy("a1", 1, "x", 1000)], "alerted": {"1": ["a1"]}}, fh)
                text = scan.status(self.today)
        self.assertEqual(common.rtl_bad_lines(text), [])
        for want in ("<code>2026-09-22</code>", "<code>2</code> ימי מסחר", "<code>1</code> חגים",
                     "(האחרון <code>2026-09-23</code>)", "בזיכרון: <code>1</code>", "התראה: <code>1</code>"):
            self.assertIn(want, text)

    def test_heartbeat_every_run(self):  # 4.1: silence is a failure - every run says what it did
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"STATE_FILE": os.path.join(tmp, "s.json")}), \
                mock.patch.object(scan, "scan_day", return_value="ok"), mock.patch.object(common, "send") as snd, \
                mock.patch.object(market, "regime", return_value={"tag": "normal"}):
            with mock.patch.object(scan, "alerts", return_value=[]):
                scan.main(["--days", "20260923", "--notify"])
                scan.main(["--days", "20260923"])  # the scheduled run reports too
            self.assertEqual(snd.call_count, 2)
            self.assertIn("אין התראות חדשות", snd.call_args.args[0])
            self.assertEqual(common.rtl_bad_lines(snd.call_args.args[0]), [])
            with mock.patch.object(scan, "alerts", return_value=[["🔔 התראה", {"1": ["a"]}, {}, {}]]):
                scan.main(["--days", "20260923"])
            self.assertEqual(snd.call_args_list[-2].args[0], "🔔 התראה")  # the alert, then the heartbeat
            self.assertIn("<code>1</code> התראות חדשות", snd.call_args.args[0])

    def test_failure_reason(self):
        self.assertIn("SEC_UA", common.failure(SystemExit("SEC_UA is not set")))
        self.assertIn("SEC לא זמין", common.failure(SystemExit("days that failed and will be retried: 20260907")))


class Check(unittest.TestCase):
    def test_unknown_ticker(self):
        with mock.patch.object(common, "tickers", return_value={}):
            msg = check.report("zzzz")
        self.assertIn("<code>ZZZZ</code>", msg)
        self.assertEqual(common.rtl_bad_lines(msg), [])


if __name__ == "__main__":
    unittest.main()
