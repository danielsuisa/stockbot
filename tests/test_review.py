"""Regression tests for the adversarial review's confirmed findings (block 7)."""
import datetime as dt
import json
import os
import tempfile
import unittest
from unittest import mock

from bot import common, form4, health, journal, listen, market, scan
from test_bot import NO_ENRICH
from test_reliability import row
from test_signals import doc, tx

TODAY = dt.date(2026, 9, 24)
TICKERS = {"ACME": (12345, "Acme")}


def make(acc, d, filed="2026-09-21"):
    with mock.patch.object(common, "tickers", return_value=TICKERS):
        return scan.make_row(acc, d, d["buys"], "ACME", filed)


class Amendments(unittest.TestCase):
    """A1-A3: a 4/A replaces exactly its own original, withdraws only what it restates, and never double counts."""

    def test_same_day_filings_of_one_insider(self):
        a, b = doc([tx("P", 100, 10, date="2026-09-17")]), doc([tx("P", 10000, 10, date="2026-09-18")])
        buys = {"A": make("A", a), "B": make("B", b)}
        fix = doc([tx("P", 10000, 11, date="2026-09-18")], form="4/A", original="2026-09-21")
        self.assertEqual(scan.amend(buys, "F", fix, make("F", fix)), "B")  # B's trades, not the first same-day row
        self.assertEqual((buys["A"]["value"], buys["B"]["value"], buys["B"]["amended_by"]), (1000.0, 110000.0, "F"))

    def test_unrelated_or_partial_amendments_withdraw_nothing(self):
        buys = {"A": make("A", doc([tx("P", 100, 10, date="2026-09-17")]))}
        sale = doc([tx("S", 50, 12, date="2026-09-19", ad="D")], form="4/A", original="2026-09-21")
        grant_only = doc([tx("A", 5, 0, table="derivativeTransaction")], form="4/A", original="2026-09-21")
        removed = {}
        for d in (sale, grant_only):
            self.assertIsNone(scan.amend(buys, "X", d, None, removed))
        self.assertEqual((list(buys), removed), (["A"], {}))
        recoded = doc([tx("A", 100, 0, date="2026-09-17")], form="4/A", original="2026-09-21")  # really a grant
        self.assertEqual(scan.amend(buys, "R", recoded, None, removed), "A")
        self.assertEqual((buys, removed["A"]["by"]), ({}, "R"))

    def test_withdrawn_original_is_never_read_back(self):
        orig = doc([tx("P", 1000, 1000)], owner="BIG", ocik="7")
        st = {"days": {}, "buys": [], "alerted": {}, "removed": {"o": {"by": "w", "cik": 12345, "date": "2026-09-21"}}}
        with mock.patch.object(common, "tickers", return_value=TICKERS), \
                mock.patch.object(scan, "index_urls", return_value=({"o": "u/o.txt"}, "index")), \
                mock.patch.object(scan, "_get", return_value=orig), \
                mock.patch.object(common, "cik_tickers", return_value={12345: "ACME"}):
            self.assertEqual(scan.scan_day("20260921", st, TODAY), "ok")
        self.assertEqual(st["buys"], [])

    def test_original_read_after_its_amendment_is_not_added(self):
        orig = doc([tx("P", 1000, 1000)], owner="BIG", ocik="7")
        fix = doc([tx("P", 1000, 900)], owner="BIG", ocik="7", form="4/A", original="2026-09-18")
        st = {"days": {}, "buys": [make("F", fix, filed="2026-09-22")], "alerted": {}}
        with mock.patch.object(common, "tickers", return_value=TICKERS), \
                mock.patch.object(scan, "index_urls", return_value=({"O": "u/O.txt"}, "index")), \
                mock.patch.object(scan, "_get", return_value=orig), \
                mock.patch.object(common, "cik_tickers", return_value={12345: "ACME"}):
            scan.scan_day("20260918", st, TODAY)
        self.assertEqual([b["acc"] for b in st["buys"]], ["F"])  # $900K once, not $1.9M

    def test_seen_survives_a_catch_up(self):
        st = {"days": {f"202609{d:02d}": "ok" for d in range(8, 24) if dt.date(2026, 9, d).weekday() < 5},
              "buys": [], "alerted": {}, "seen": {}}
        st["seen"] = {d: ["x"] for d in st["days"]}
        kept = scan.prune(st, TODAY)["seen"]
        self.assertEqual(len(kept), scan.RESCAN + scan.MAX_CATCHUP + 1)


class Corrections(unittest.TestCase):
    """A4: only what the 4/A changed moves the corrected total."""

    def test_aging_and_new_buys_are_not_blamed_on_the_amendment(self):
        rs = [row("a", "x", 400000), row("b", "y", 300000), row("c", "z", 200000)]
        st = {"buys": rs, "sent": {"7": scan.snapshot(scan.evaluate(rs, None), TODAY)}, "removed": {}, "regime": None}
        st["buys"] = [rs[1], {**rs[2], "amended_by": "n"}, row("d", "w", 5e6)]  # a aged out, d is new, c: no-op 4/A
        self.assertEqual(scan.corrections(st, TODAY), [])
        st["buys"][0] = {**rs[1], "value": 30000.0, "amended_by": "m"}  # a real 4/A: $300K -> $30K
        (msg,) = scan.corrections(st, TODAY)
        self.assertIn("ל־<code>630.0K$</code>", msg)  # 900K - 270K; the aged-out $400K still counts, the new $5M not
        self.assertEqual(common.rtl_bad_lines(msg), [])

    def test_old_snapshots_without_rows_still_work(self):
        rs = [row("a", "x", 400000), row("b", "y", 300000), row("c", "z", 300000)]
        snap = scan.snapshot(scan.evaluate(rs, None), TODAY)
        del snap["rows"]
        st = {"buys": [{**rs[0], "value": 40000.0, "amended_by": "a2"}, *rs[1:]], "sent": {"7": snap}, "removed": {}}
        (msg,) = scan.corrections(st, TODAY)
        self.assertIn("<code>640.0K$</code>", msg)


class Signals(unittest.TestCase):
    def test_joint_merge_is_order_independent(self):  # A5
        fund = {**row("f", "fund", 1e6, od=False, weight=0.0), "sig": [["2026-09-20", 1, 1]], "indirect": True}
        director = {**row("g", "dir", 1e6, weight=0.7), "sig": [["2026-09-20", 1, 1]], "indirect": True}
        others = [row("h", "x", 60000), row("i", "y", 50000)]
        results = []
        for pair in ([fund, director], [director, fund]):
            ev = scan.evaluate(pair + others, None)
            results.append((bool(ev["cluster"]), ev["score"], len(ev["open"])))
        self.assertEqual(results[0], results[1])
        self.assertTrue(results[0][0])  # the director's board seat makes it an insider purchase either way

    def test_nothing_new_to_count_sends_nothing(self):  # A6
        rs = [row("a", "x", 400000), row("b", "y", 300000), row("c", "z", 300000)]
        plan = row("p", "x", 9000, kind="plan")
        st = {"buys": rs + [plan], "alerted": {"7": ["a", "b", "c"]}, "info": [], "regime": None}
        with mock.patch.object(scan, "foreign", return_value=False), mock.patch.object(scan, "pay", return_value={}), \
                mock.patch.object(scan, "links", return_value={}), NO_ENRICH:
            self.assertEqual(scan.alerts(st, TODAY), [])  # a 10b5-1 filing later: no re-send, no journal duplicate
            st["buys"].append(row("d", "w", 50000))
            (text, marks, _, entries), = scan.alerts(st, TODAY)  # a real new buyer: alert, marking the plan row too
        self.assertIn("p", marks["7"])
        self.assertEqual(text.count("🆕"), 1)

    def test_holdings_pct_spans_ownership_forms(self):  # A7
        direct = tx("P", 10000, 10, post="1000000")
        ira = tx("P", 10000, 10, own="I", post="10000")
        for lines in ([direct, ira], [ira, direct]):
            d = doc(lines)
            self.assertAlmostEqual(form4.summarize(d)["pct"], 20000 / 1010000)

    def test_denied_plan_footnote(self):  # A9
        d = doc([tx("P", 100, 10, notes=("F1",))],
                notes={"F1": "The purchase was not made pursuant to a Rule 10b5-1 trading plan."})
        self.assertFalse(d["buys"][0]["plan"])
        d = doc([tx("P", 100, 10, notes=("F1",))], notes={"F1": "Effected pursuant to a Rule 10b5-1 trading plan."})
        self.assertTrue(d["buys"][0]["plan"])

    def test_one_issuer_failing_does_not_block_the_rest(self):  # A10
        rs = [row("a", "x", 900000, cik=7, ticker="ACME"), row("b", "y", 800000, cik=8, ticker="BETA")]
        st = {"buys": rs, "alerted": {}, "info": [], "regime": None}
        with mock.patch.object(scan, "foreign", side_effect=lambda c: (_ for _ in ()).throw(OSError("503")) if c == 7 else False), \
                mock.patch.object(scan, "pay", return_value={}), \
                mock.patch.object(scan, "links", side_effect=RuntimeError("x")), \
                mock.patch.object(scan, "enrich", side_effect=ValueError("bad json")):
            (text, marks, _, entries), = scan.alerts(st, TODAY)
        self.assertEqual(list(marks), ["8"])  # ACME is retried next run; BETA goes out without its context
        self.assertIn("<code>BETA</code>", text)

    def test_corrections_are_saved_before_alerts(self):  # A8
        rs = [row("a", "x", 400000), row("b", "y", 300000), row("c", "z", 300000)]
        snap = scan.snapshot(scan.evaluate(rs, None), TODAY)
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "s.json")
            st = {"days": {"20260923": "ok"}, "buys": [{**rs[0], "value": 40000.0, "amended_by": "a2"}, *rs[1:]],
                  "alerted": {"7": ["a", "b", "c"]}, "sent": {"7": snap}}
            with open(p, "w", encoding="utf-8") as fh:
                json.dump(st, fh)
            with mock.patch.dict(os.environ, {"STATE_FILE": p}), mock.patch.object(common, "send") as snd, \
                    mock.patch.object(scan, "scan_day", return_value="ok"), mock.patch("traceback.print_exc"), \
                    mock.patch.object(market, "regime", return_value={"tag": "normal"}), \
                    mock.patch.object(scan, "alerts", side_effect=RuntimeError("SEC down")):
                scan._reported[0] = False
                with self.assertRaises(RuntimeError):
                    scan.main(["--days", "20260923"])
                self.assertIn("תיקון להתראה", snd.call_args_list[0].args[0])
                saved = json.loads(open(p, encoding="utf-8").read())
        self.assertEqual(saved["sent"]["7"]["acks"], ["a2"])  # next run will not send it again


class Numbers(unittest.TestCase):
    def test_spy_measured_over_the_stocks_sessions(self):  # B1
        j = {"alerts": [{"id": "x", "date": "2026-03-01", "ticker": "X", "returns": {}}]}

        def close(t, day):
            if t == "X":  # no trade on the first sessions: the stock's own start is 03-04
                return (max(day, dt.date(2026, 3, 4)), 10.0 if day <= dt.date(2026, 3, 4) else 12.0)
            return day, {dt.date(2026, 3, 4): 100.0}.get(day, 90.0 if day < dt.date(2026, 3, 4) else 110.0)
        with mock.patch.object(market, "close_on", side_effect=close):
            journal.followup(j, TODAY)
        r = j["alerts"][0]["returns"]["30"]
        self.assertEqual((r["start"], r["spy"]), ("2026-03-04", 0.1))  # SPY 100 -> 110 over the same dates

    def test_thin_stock_liquidity_counts_quiet_days(self):  # B2
        ts = [int(dt.datetime.combine(dt.date(2026, 8, 3) + dt.timedelta(i), dt.time(14), dt.timezone.utc).timestamp())
              for i in range(21)]
        vol = [300_000 if i % 2 == 0 else 0 for i in range(21)]
        r = {"timestamp": ts, "indicators": {"quote": [{"close": [10.0] * 21, "volume": vol}]}}
        with mock.patch.object(market, "chart", return_value=r):
            liq = market.liquidity("THIN")
        self.assertAlmostEqual(liq, 3_000_000 * 11 / 21)
        self.assertLess(liq, market.LOW_LIQUIDITY)

    def test_stale_last_price_is_marked(self):  # B5
        old = int((dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=12)).timestamp())
        r = {"meta": {"currency": "USD", "regularMarketPrice": 3.0, "regularMarketTime": old}}
        with mock.patch.object(market, "chart", return_value=r):
            q = market.quote("HALT")
        self.assertEqual(q["source"], "stale")
        line = journal.context_lines({"price": q, "liquidity": None, "scores": journal.scores_of(None), "sic": None}, 0)[0]
        self.assertIn("⚠ מחיר מ־", line)
        market.CACHE.clear()

    def test_realized_vol_fallback_uses_the_vix_scale(self):  # B6
        spy = [100 * (1.0015 ** i) * (1.004 if i % 2 else 0.996) for i in range(90)]  # +9% in 60 days, choppy
        hist = {"SPY": [(dt.date(2026, 5, 1) + dt.timedelta(i), c, 1) for i, c in enumerate(spy)], "IWM": [], "%5EVIX": []}
        with mock.patch.object(market, "history", side_effect=lambda t, rng="6mo": hist[t]):
            r = market.regime()
        self.assertEqual(r["vol_source"], "SPY realized")
        self.assertLess(r["vix"], 15)  # raw realized vol alone would have said "euphoria"
        self.assertEqual(r["tag"], "normal")

    def test_unscored_alerts_are_left_out_of_quality_buckets(self):  # B7
        e = {"id": "x", "ticker": "X", "quality": None, "regime": "normal",
             "returns": {"30": {"end": "x", "ret": 0.1, "spy": 0.0, "excess": 0.1}}}
        self.assertEqual(journal.stats({"alerts": [e]})["quality"], {})


class Listener(unittest.TestCase):
    def test_status_lookups_are_quick(self):  # C3
        calls = []

        def fetch(url, *a, **k):
            calls.append(k)
            raise OSError("timeout")
        scan.published.cache_clear()
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "s.json")
            with open(p, "w", encoding="utf-8") as fh:
                json.dump({"days": {"20260923": "ok"}}, fh)
            with mock.patch.dict(os.environ, {"STATE_FILE": p, "GITHUB_REPOSITORY": "o/r"}), \
                    mock.patch.object(common, "fetch", side_effect=fetch):
                scan.status(TODAY)
        scan.published.cache_clear()
        self.assertTrue(calls and all(k.get("tries") == 1 and k.get("timeout") == 15 for k in calls), calls)

    def test_dispatch_network_error_is_reported_as_such(self):  # C4
        with mock.patch.dict(os.environ, {"GITHUB_REPOSITORY": "o/r", "GITHUB_TOKEN": "t"}), \
                mock.patch.object(common, "fetch", side_effect=TimeoutError("timed out")):
            self.assertEqual(common.dispatch("daily-scan.yml", {}), "network: TimeoutError")

    def test_runs_can_ask_for_completed_only(self):  # C5
        with mock.patch.dict(os.environ, {"GITHUB_REPOSITORY": "o/r", "GITHUB_TOKEN": ""}), \
                mock.patch.object(common, "fetch", return_value=b'{"workflow_runs": []}') as f:
            self.assertIsNone(common.runs("x.yml", completed=True))
        self.assertTrue(f.call_args.args[0].endswith("runs?per_page=1&status=completed"))

    def test_journal_argument_is_ascii_digits(self):  # C6
        sent = []
        with mock.patch.object(common, "send", side_effect=lambda t, **k: sent.append(t)), \
                mock.patch.object(journal, "load", return_value={"alerts": []}):
            self.assertEqual(listen.handle("/journal ²"), 0)
        self.assertIn("היומן ריק", sent[0])

    def test_health_index_falls_back_to_last_quarter(self):  # C7
        def fetch(url, *a, **k):
            if "QTR4" in url:
                return None  # 404: the new quarter has no index yet
            return json.dumps({"directory": {"item": [{"name": "form.20260930.idx"}]}}).encode()
        with mock.patch.object(common, "fetch", side_effect=fetch), \
                mock.patch.object(health.dt, "datetime", wraps=dt.datetime) as fake:
            fake.now.return_value = dt.datetime(2026, 10, 1, 6, tzinfo=dt.timezone.utc)
            self.assertIn("<code>2026-09-30</code>", health._index())


if __name__ == "__main__":
    unittest.main()
