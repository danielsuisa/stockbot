"""Phase A of the recommendation algorithm (spec 2026-10-10, section 7 A): readers, universe and firing counters.
No return of any ticket or stock is computed anywhere in the code under test."""
import io
import unittest
import zipfile

from bot import probe_ensemble as pe, probe_ensemble_data as pd


def tsv(rows):
    head = list(rows[0])
    return "\n".join(["\t".join(head)] + ["\t".join(str(r.get(k, "")) for k in head) for r in rows]) + "\n"


def make_zip(tables):
    """{table name: [row dicts]} -> an in-memory ZipFile shaped like SEC's Form 3/4/5 data set."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, rows in tables.items():
            z.writestr(f"{name}.tsv", tsv(rows))
    buf.seek(0)
    return zipfile.ZipFile(buf)


def sub(acc, form, symbol="ACME", cik="100", filed="05-JAN-2017", box=""):
    return {"ACCESSION_NUMBER": acc, "FILING_DATE": filed, "DOCUMENT_TYPE": form, "ISSUERCIK": cik,
            "ISSUERTRADINGSYMBOL": symbol, "AFF10B5ONE": box}


def tx(acc, code="P", ad="A", shares="1000", price="10", date="03-JAN-2017", fn=""):
    return {"ACCESSION_NUMBER": acc, "TRANS_CODE": code, "TRANS_ACQUIRED_DISP_CD": ad, "TRANS_SHARES": shares,
            "TRANS_PRICEPERSHARE": price, "TRANS_DATE": date, "TRANS_SHARES_FN": fn}


def owner(acc, cik, rel="Director"):
    return {"ACCESSION_NUMBER": acc, "RPTOWNERCIK": cik, "RPTOWNER_RELATIONSHIP": rel}


def note(acc, fid, text):
    return {"ACCESSION_NUMBER": acc, "FOOTNOTE_ID": fid, "FOOTNOTE_TXT": text}


class InsiderReaders(unittest.TestCase):
    def test_normalise_symbols(self):
        self.assertEqual(pd.normalise_symbol("brk-b"), "BRK.B")
        self.assertEqual(pd.normalise_symbol(" AAPL, AAPLW"), "AAPL")
        self.assertEqual(pd.normalise_symbol("goog;googl"), "GOOG")
        for bad in ("NONE", "N/A", "", None, "TOOLONG", "12AB"):
            self.assertIsNone(pd.normalise_symbol(bad), bad)

    def test_symbol_filings_all_forms(self):
        z = make_zip({"SUBMISSION": [sub("a", "3"), sub("b", "4"), sub("c", "5"), sub("d", "4/A"), sub("e", "10-K"),
                                     sub("f", "4", symbol="NONE")]})
        rows = pd.symbol_filings(z)
        self.assertEqual(sorted(rows), [("2017-01-05", "100", "ACME")] * 4)  # 10-K and the unusable symbol dropped
        by_date, ciks = pd.index(rows + [("2017-01-02", "200", "ACME")])
        self.assertEqual(by_date["ACME"], ["2017-01-02"] + ["2017-01-05"] * 4)
        self.assertEqual(ciks["ACME"], {"100", "200"})

    def test_purchases_plan_and_role(self):
        z = make_zip({
            "SUBMISSION": [sub("a", "4"), sub("b", "4"), sub("c", "4"), sub("d", "4", box="1"), sub("e", "4/A")],
            "NONDERIV_TRANS": [tx("a"), tx("b", fn="F1"), tx("c"), tx("d"), tx("e"), tx("a", code="S", ad="D")],
            "REPORTINGOWNER": [owner("a", "1"), owner("b", "2"), owner("c", "3", rel="TenPercentOwner"),
                               owner("d", "4"), owner("e", "5")],
            "FOOTNOTES": [note("b", "F1", "Purchased under a Rule 10b5-1 trading plan.")]})
        got = pd.purchases(z)
        self.assertEqual([(p["owner"], p["trade_date"], p["shares"], p["price"], p["value"]) for p in got],
                         [("1", "2017-01-03", 1000.0, 10.0, 10000.0)])  # plan, 10% owner, box, 4/A, sale excluded
        self.assertEqual((got[0]["filed"], got[0]["issuer"], got[0]["symbol"]), ("2017-01-05", "100", "ACME"))

    def test_a_denied_plan_footnote_still_counts(self):
        z = make_zip({"SUBMISSION": [sub("a", "4")], "NONDERIV_TRANS": [tx("a", fn="F1")],
                      "REPORTINGOWNER": [owner("a", "1", rel="Officer")],
                      "FOOTNOTES": [note("a", "F1", "Not made pursuant to a Rule 10b5-1 plan.")]})
        self.assertEqual(len(pd.purchases(z)), 1)


def cols(rows):
    """Row dicts -> EDGAR's columnar shape."""
    return {k: [r[k] for r in rows] for k in rows[0]}


def filing(acc, form, items, accepted):
    return {"accessionNumber": acc, "form": form, "items": items, "acceptanceDateTime": accepted,
            "filingDate": accepted[:10]}


class Submissions(unittest.TestCase):
    SESSIONS = ["2024-02-01", "2024-02-02", "2024-02-05", "2024-07-01", "2024-07-02"]

    def zip_path(self, d):
        import json
        import os
        p = os.path.join(d, "submissions.zip")
        main = {"cik": "320193", "filings": {"recent": cols([
            filing("a", "8-K", "2.02,9.01", "2024-02-01T21:30:00.000Z"), filing("b", "8-K", "5.02", "2024-02-01T12:00:00.000Z"),
            filing("c", "10-Q", "", "2024-02-02T12:00:00.000Z")]), "files": [{"name": "CIK0000320193-submissions-001.json"}]}}
        page = cols([filing("d", "8-K/A", "2.02", "2024-07-01T14:00:00.000Z")])
        other = {"cik": "999", "filings": {"recent": cols([filing("e", "8-K", "2.02", "2024-02-01T12:00:00.000Z")])}}
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("CIK0000320193.json", json.dumps(main))
            z.writestr("CIK0000320193-submissions-001.json", json.dumps(page))
            z.writestr("CIK0000000999.json", json.dumps(other))
        return p

    def test_items_and_pages(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            got = pd.earnings_releases(self.zip_path(d), {"320193"}, "America/New_York")
        self.assertEqual(got, {"320193": ["2024-02-01T21:30", "2024-07-01T14:00"]})  # 5.02 and 10-Q out, CIK 999 not asked

    def test_acceptance_tz(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            got = pd.earnings_releases(self.zip_path(d), {"0000320193"}, "UTC")
        self.assertEqual(got["320193"], ["2024-02-01T16:30", "2024-07-01T10:00"])  # winter UTC-5, summer UTC-4

    def test_reaction_session_after_close_et(self):
        s = self.SESSIONS
        self.assertEqual(pd.reaction_session("2024-02-01T15:59", s), "2024-02-01")
        self.assertEqual(pd.reaction_session("2024-02-01T16:00", s), "2024-02-02")
        self.assertEqual(pd.reaction_session("2024-02-03T10:00", s), "2024-02-05")  # Saturday -> Monday
        self.assertEqual(pd.reaction_session("2024-02-02T18:00", s), "2024-02-05")
        self.assertIsNone(pd.reaction_session("2024-07-02T17:00", s))  # past the last session


def weekdays(start, n):
    import datetime as dt
    d, out = dt.date.fromisoformat(start), []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += dt.timedelta(1)
    return out


class Bars(unittest.TestCase):
    S = weekdays("2016-01-04", 400)

    def test_bad_price_breaks(self):
        d = self.S[:6]
        self.assertEqual(pd.segments(d, [10, 39.9, 159.6, 31.9, 31.9, 127.6], self.S), [0, 0, 1, 1, 1, 2])
        # x3.99 same, x4.0 new, /5 kept (a fall is real), x4.0 new
        gap10 = [self.S[0], self.S[11]]  # 10 sessions without a bar between them
        gap11 = [self.S[0], self.S[12]]
        self.assertEqual(pd.segments(gap10, [10, 10], self.S), [0, 0])
        self.assertEqual(pd.segments(gap11, [10, 10], self.S), [0, 1])

    def test_history_restarts_after_break(self):
        closes = [10.0] * 280 + [40.0] * 20
        seg = pd.segments(self.S[:300], closes, self.S)
        self.assertEqual(pd.earlier_in_segment(seg, 279), 279)
        self.assertEqual(pd.earlier_in_segment(seg, 290), 10)  # counted from the break at bar 280

    def test_join_raw_and_split(self):
        from unittest import mock
        d = self.S[:3]
        raw = {"AAA": [(d[0], 1, 2, 0.5, 10, 100), (d[1], 1, 2, 0.5, 10, 100), (d[2], 1, 2, 0.5, 10, 100)]}
        adj = {"AAA": {d[0]: (1, 1.0, 1000), d[2]: (1, 1.0, 1000)}}  # d[1] missing from the split-adjusted feed
        with mock.patch.object(pd.alpaca, "daily", return_value=raw), mock.patch.object(pd, "split_bars", return_value=adj):
            s = pd.load_series(["AAA"], d[0], d[2], self.S)["AAA"]
        self.assertEqual(s["dates"], [d[0], d[2]])
        got = {k: list(s[k]) for k in ("raw_close", "close", "volume", "raw_volume", "raw_high", "raw_low", "segment")}
        self.assertEqual(got, {"raw_close": [10, 10], "close": [1.0, 1.0], "volume": [1000, 1000], "raw_volume": [100, 100],
                               "raw_high": [2, 2], "raw_low": [0.5, 0.5], "segment": [0, 0]})


    def test_split_cache_is_keyed_by_symbols(self):
        import pathlib
        import tempfile
        from unittest import mock
        bar = lambda: {"t": "2016-01-04T05:00:00Z", "o": 1, "c": 2, "v": 3}  # noqa: E731
        with tempfile.TemporaryDirectory() as d, mock.patch.object(pd.alpaca, "CACHE", pathlib.Path(d)):
            with mock.patch.object(pd.alpaca, "_get", side_effect=lambda u, q: {"bars": {s: [bar()] for s in q["symbols"].split(",")}}) as g:
                self.assertEqual(set(pd.split_bars(["AAA"], "2016-01-01", "2016-01-05")), {"AAA"})
                self.assertEqual(set(pd.split_bars(["BBB"], "2016-01-01", "2016-01-05")), {"BBB"})  # not AAA's file
                pd.split_bars(["AAA"], "2016-01-01", "2016-01-05")
                self.assertEqual(g.call_count, 2)  # the third call is a cache hit
                self.assertEqual(g.call_args.args[1]["adjustment"], "split")



def series(dates, close=10.0, volume=2_000_000, raw_close=None, raw_volume=None):
    n = len(dates)
    rc = raw_close if raw_close is not None else (close if isinstance(close, list) else [close] * n)
    x = {"dates": list(dates), "close": [close] * n if not isinstance(close, list) else close,
         "volume": [volume] * n if not isinstance(volume, list) else volume,
         "raw_close": rc if isinstance(rc, list) else [rc] * n,
         "raw_volume": raw_volume if raw_volume is not None else ([volume] * n if not isinstance(volume, list) else volume),
         "raw_high": [c * 1.01 for c in (rc if isinstance(rc, list) else [rc] * n)],
         "raw_low": [c * 0.99 for c in (rc if isinstance(rc, list) else [rc] * n)]}
    x["segment"] = pd.segments(x["dates"], x["close"], Bars.S)
    return x


class Universe(unittest.TestCase):
    S = Bars.S
    D = S[300]  # 300 earlier sessions

    def test_universe_conditions(self):
        import datetime as dt
        d0 = dt.date.fromisoformat(self.D)
        y = (d0 - dt.timedelta(365)).isoformat()
        ok = series(self.S[:301])
        cases = {"OK": (ok, [(d0 - dt.timedelta(10)).isoformat()]),
                 "EDGE": (ok, [y]),  # filed exactly 365 days before D: in
                 "OLD": (ok, [(d0 - dt.timedelta(366)).isoformat()]),
                 "SAME": (ok, [self.D]),  # filed on D itself: not "before D"
                 "NOBAR": (series(self.S[:300]), [y]),
                 "CHEAP": (series(self.S[:301], raw_close=4.99), [y]),
                 "THIN": (series(self.S[:301], close=10.0, volume=999_000), [y]),  # $9.99M
                 "YOUNG": (series(self.S[49:301]), [y])}  # 251 earlier bars
        u = pe.universe(self.D, {k: v[0] for k, v in cases.items()}, {k: v[1] for k, v in cases.items()})
        self.assertEqual(sorted(u), ["EDGE", "OK"])
        self.assertAlmostEqual(u["OK"], 20_000_000)

    def test_split_vs_raw_roles(self):
        raw = [4.0] * 290 + [40.0] * 11  # a 1:10 reverse split at bar 290: raw x10, split-adjusted flat
        s = series(self.S[:301], close=40.0, volume=500_000, raw_close=raw, raw_volume=[5_000_000] * 290 + [500_000] * 11)
        self.assertEqual(s["segment"][-1], 0)  # the split-adjusted series has no jump: one segment
        u = pe.universe(self.D, {"AAA": s}, {"AAA": [self.S[250]]})
        self.assertIn("AAA", u)  # $5 uses the raw close on D (40); dollar volume = raw close x raw volume
        early = pe.universe(self.S[289], {"AAA": s}, {"AAA": [self.S[250]]})
        self.assertNotIn("AAA", early)  # before the split the raw close was 4.00 < $5

    def test_top_half_by_liquidity(self):
        u = {"A": 30e6, "B": 20e6, "C": 20e6, "D": 10e6, "E": 50e6}
        self.assertEqual(pe.liquidity_top_half(u), {"E", "A", "B"})  # ceil(5 / 2) = 3, the tie B / C by symbol



def buy(owner, filed, value=40_000.0, trade=None, shares=None, price=10.0, issuer="100", symbol="ACME"):
    return {"filed": filed, "issuer": issuer, "symbol": symbol, "owner": owner, "trade_date": trade or filed,
            "shares": shares if shares is not None else value / price, "price": price, "value": value}


class S1(unittest.TestCase):
    S = Bars.S

    def test_people_and_dollars(self):
        S = self.S
        two = [buy("1", S[300]), buy("2", S[301], value=70_000)]
        self.assertEqual(pe.s1_firings(two, S), [])
        three = two + [buy("3", S[302])]
        self.assertEqual(pe.s1_firings(three, S), [(S[302], "ACME")])
        short = [buy("1", S[300], 33_333), buy("2", S[300], 33_333, price=10.01), buy("3", S[301], 33_333)]  # $99,999
        self.assertEqual(pe.s1_firings(short, S), [])
        self.assertEqual(pe.s1_firings(short[:2] + [buy("3", S[301], 33_334)], S), [(S[301], "ACME")])  # $100,000

    def test_identical_lines_count_as_one_person(self):
        S = self.S
        joint = [buy("1", S[300], trade=S[299], shares=5000, value=50_000), buy("2", S[300], trade=S[299], shares=5000,
                 value=50_000), buy("3", S[301], value=60_000)]
        self.assertEqual(pe.s1_firings(joint, S), [])  # owners 1 and 2 report the same purchase: two people, $110k
        self.assertEqual(pe.s1_firings(joint + [buy("4", S[302])], S), [(S[302], "ACME")])  # $150k with the line once

    def test_trade_window_and_the_21_session_gap(self):
        import datetime as dt
        S = self.S
        old = (dt.date.fromisoformat(S[302]) - dt.timedelta(31)).isoformat()
        stale = [buy("1", S[300], trade=old), buy("2", S[301]), buy("3", S[302])]
        self.assertEqual(pe.s1_firings(stale, S), [])  # owner 1's trade is 31 days before the third filing
        first = [buy("1", S[300], 40_000), buy("2", S[300], 40_001), buy("3", S[300], 40_002)]
        blocked = first + [buy(o, S[321], 40_010 + int(o)) for o in "456"]  # 21 sessions later: within the gap
        self.assertEqual(pe.s1_firings(blocked, S), [(S[300], "ACME")])
        again = first + [buy(o, S[322], 40_010 + int(o)) for o in "456"]  # 22 sessions later
        self.assertEqual(pe.s1_firings(again, S), [(S[300], "ACME"), (S[322], "ACME")])


def bars_for(n, close, volume, raw_close=None, high=None, low=None):
    s = series(Bars.S[:n], close=close, volume=volume, raw_close=raw_close)
    if high is not None:
        s["raw_high"], s["raw_low"] = high, low
    s["segment"] = pd.segments(s["dates"], s["close"], Bars.S)
    return s


class S2(unittest.TestCase):
    S = Bars.S

    def make(self, jump, vol, close_pos=0.75):
        n = 30
        close = [100.0] * (n - 1) + [100.0 * jump]
        volume = [1000] * (n - 1) + [vol]
        hi, lo = [101.0] * n, [99.0] * n
        lo[-1], hi[-1] = close[-1] - 10 * close_pos, close[-1] + 10 * (1 - close_pos)
        return bars_for(n, close, volume, raw_close=close, high=hi, low=lo)

    def test_price_volume_range_and_the_8k(self):
        D, S = self.S[29], self.S
        rel = {"A": {D}}
        self.assertEqual(pe.s2_firings(D, {"A": self.make(1.05, 2000)}, rel, True), {"A"})
        self.assertEqual(pe.s2_firings(D, {"A": self.make(1.0499, 2000)}, rel, True), set())
        self.assertEqual(pe.s2_firings(D, {"A": self.make(1.05, 1999)}, rel, True), set())
        self.assertEqual(pe.s2_firings(D, {"A": self.make(1.05, 2000, close_pos=0.5)}, rel, True), {"A"})  # midpoint in
        self.assertEqual(pe.s2_firings(D, {"A": self.make(1.05, 2000, close_pos=0.49)}, rel, True), set())
        self.assertEqual(pe.s2_firings(D, {"A": self.make(1.05, 2000)}, {"A": {S[28]}}, True), set())  # not its session
        self.assertEqual(pe.s2_firings(D, {"A": self.make(1.05, 2000)}, {}, False), {"A"})  # the fallback: no 8-K


class S3S4(unittest.TestCase):
    S = Bars.S

    def ramp(self, start, end, n=300):
        return [start + (end - start) * k / (n - 1) for k in range(n)]

    def test_s3_state_near_high_and_top_fifth(self):
        D = self.S[299]
        ser = {sym: bars_for(300, self.ramp(10, top), 1_000_000) for sym, top in
               (("A", 30), ("B", 25), ("C", 20), ("D", 15), ("E", 12))}
        ser["F"] = bars_for(300, self.ramp(10, 60)[:290] + [30.0] * 10, 1_000_000)  # the strongest D-147 -> D-21, far from its high
        u = {k: 1.0 for k in ser}
        self.assertEqual(pe.s3_state(D, ser, u), {"A"})  # top 20% of 6 = 2 (A, F); F is not within 5% of its high

    def test_transitions(self):
        S = self.S
        states = [(S[i], {"A"} if i in (0, 22) else set()) for i in range(23)]
        self.assertEqual(pe.transitions(states, 21), [(S[0], "A"), (S[22], "A")])  # 21 sessions outside before S[22]
        states = [(S[i], {"A"} if i in (0, 21) else set()) for i in range(22)]
        self.assertEqual(pe.transitions(states, 21), [(S[0], "A")])  # only 20 outside
        states = [(S[i], {"A"} if i in (1, 2, 4) else set()) for i in range(5)]
        self.assertEqual(pe.transitions(states, 1), [(S[1], "A"), (S[4], "A")])

    def test_s4_state(self):
        S, D = self.S, self.S[299]
        base = [10.0] * 294
        ser = {"A": bars_for(300, base + [10.0] * 5 + [8.99], 3_000_000),  # -10.1%
               "B": bars_for(300, base + [10.0] * 5 + [9.0], 3_000_000),  # -10.0%: in
               "C": bars_for(300, base + [10.0] * 5 + [9.5], 3_000_000)}
        for i in range(37):
            ser[f"Z{i:02d}"] = bars_for(300, base + [10.0] * 6, 3_000_000)
        u = {k: 30e6 for k in ser}
        u.update({"A": 99e6, "B": 98e6})
        self.assertEqual(pe.s4_state(D, ser, u, {}, S), {"A", "B"})  # bottom 5% of 40 = 2
        self.assertEqual(pe.s4_state(D, ser, u, {"A": {S[295]}}, S), {"B"})  # a reaction session 4 sessions back
        self.assertEqual(pe.s4_state(D, ser, u, {"A": {S[294]}}, S), {"A", "B"})  # 5 sessions back: outside
        u.update({"A": 1e6})
        self.assertEqual(pe.s4_state(D, ser, u, {}, S), {"B"})  # A is not in the top half by liquidity


class Ranks(unittest.TestCase):
    def test_rank_cut_ties_and_small(self):
        v = {"G": 1, "F": 5, "E": 5, "D": 3, "C": 2, "B": 9, "A": 0}
        self.assertEqual(pe.rank_cut(v, 0.20, True), {"B", "E"})  # ceil(1.4) = 2; E before F on the tie
        self.assertEqual(pe.rank_cut(v, 0.05, False), {"A"})
        self.assertEqual(pe.rank_cut({}, 0.2, True), set())

    def test_no_look_ahead(self):
        S = Bars.S
        D = S[299]
        ser = {sym: bars_for(330, [10 + k * 0.01 * (j + 1) for k in range(330)], 1_000_000) for j, sym in enumerate("ABCDE")}
        u = {k: 1.0 for k in ser}
        cut = pe.through(ser, D)
        self.assertEqual(len(cut["A"]["dates"]), 300)
        for f in (lambda x: pe.s2_firings(D, x, {}, False), lambda x: pe.s3_state(D, x, u),
                  lambda x: pe.s4_state(D, x, u, {}, S), lambda x: pe.universe(D, x, {k: [S[200]] for k in x})):
            full = f(ser)
            for s in ser.values():  # poison every bar after D
                for k in ("close", "raw_close", "volume", "raw_volume", "raw_high", "raw_low"):
                    s[k][300:] = [float("nan")] * 30
            self.assertEqual(f(ser), full)
            self.assertEqual(f(pe.through(ser, D)), full)



class PointInTime(unittest.TestCase):
    def test_release_symbols_follow_the_issuer_over_time(self):
        filings = [("2017-03-01", "100", "OLD"), ("2019-05-01", "100", "NEW"), ("2019-06-01", "200", "OLD")]
        rel = {"100": ["2017-10-25T16:30", "2019-10-25T16:30"], "0000000200": ["2019-11-01T07:00"]}
        got = pd.release_symbols(rel, filings)
        self.assertEqual(got, {"OLD": ["2017-10-25T16:30", "2019-11-01T07:00"], "NEW": ["2019-10-25T16:30"]})
        # issuer 100's 2019 release is not OLD's: OLD belongs to issuer 200 by then

    def test_index_accepted_parses_edgar_index(self):
        from unittest import mock
        page = b'<div class="infoHead">Accepted</div>\n<div class="info">2024-02-01 16:30:43</div>'
        with mock.patch.object(pd.common, "fetch", return_value=page) as f:
            self.assertEqual(pd.index_accepted("320193", "0000320193-24-000006"), "2024-02-01T16:30")
        self.assertIn("/320193/000032019324000006/0000320193-24-000006-index.htm", f.call_args.args[0])



class Report(unittest.TestCase):
    """The report holds the whitelisted keys only and no word of a return statistic."""

    def test_report_keys_only(self):
        import json
        import re
        from unittest import mock
        S = Bars.S[:330]
        ser = {sym: bars_for(330, [10 + k * 0.01 * (j + 1) for k in range(330)], 2_000_000) for j, sym in enumerate("ABCDE")}
        rows = [(S[100], "100", sym) for sym in ser]
        buys = [buy(o, S[300], 40_000 + int(o)) for o in "123"]
        with mock.patch.object(pe, "_calendar", return_value=S),                 mock.patch.object(pe, "load_insider", return_value=(rows, buys, {}, ["2015Q4", "2026Q1"])),                 mock.patch.object(pd, "download", return_value="x.zip"),                 mock.patch.object(pe, "tz_check", return_value={"pairs": [], "tz": "America/New_York"}),                 mock.patch.object(pd, "earnings_releases", return_value={"100": [S[310] + "T07:00"]}),                 mock.patch.object(pd, "load_series", return_value=ser),                 mock.patch.object(pe, "SIGNAL_START", S[260]), mock.patch.object(pe, "SIGNAL_END", S[-1]):
            out = pe.run(False)
        self.assertEqual(tuple(out), pe.REPORT_KEYS)
        text = json.dumps(out, default=sorted)
        self.assertIsNone(re.search(r"(?i)(return|mean|median|win)", text), text)
        self.assertIn(out["fallback"], ("8-K kept", "8-K dropped"))


if __name__ == "__main__":
    unittest.main()
