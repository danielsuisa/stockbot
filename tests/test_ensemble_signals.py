"""Phase C, part 1 (spec sections 2, 4, 4.1, 4.2): the inputs keyed by CIK, the point-in-time universe with the rename
rule, the liquidity deciles and the four detectors. Counts only: no outcome of any ticket is computed here."""
import unittest

from bot import ensemble_data as ed, probe_ensemble as pe, signals
from tests.test_probe_ensemble import Bars, make_zip, series, sub, tx

S = Bars.S
D = S[300]


def inputs(series_by_sym, filed, rows=None, prices=None, purchases=(), releases=None):
    """A small Inputs: `rows` (filed, cik, symbol) default to one filing per symbol under CIK = symbol."""
    rows = rows if rows is not None else [(d, sym, sym) for sym, ds in filed.items() for d in ds]
    return ed.make_inputs(sessions=list(S), cal={d: ("09:30", "16:00") for d in S}, rows=rows,
                          purchases=list(purchases), prices_by_cik=prices or {}, series=series_by_sym,
                          releases=releases or {})


def liquid(n=301, close=10.0, volume=2_000_000, dates=None):
    return series(dates or S[:n], close=close, volume=volume)


class Inputs(unittest.TestCase):
    def test_market_prices_by_cik(self):
        z = make_zip({
            "SUBMISSION": [sub("a", "4", symbol="OLD", cik="0000000100"), sub("b", "4", symbol="NEW", cik="100"),
                           sub("c", "4/A", cik="100"), sub("d", "4", symbol="NONE", cik="100")],
            "NONDERIV_TRANS": [tx("a", code="S", ad="D", price="12"), tx("a", code="M", price="3"),
                               tx("b", code="F", ad="D", price="13"), tx("c", code="P", price="14"),
                               tx("d", code="P", price="15")]})
        got = sorted(ed.market_prices(z))
        self.assertEqual([(cik, px) for _, cik, _, _, px in got], [("100", 12.0), ("100", 13.0), ("100", 15.0)])
        # by CIK: two symbols of one issuer share one list, a filing without a usable symbol still counts, M and
        # amendments are dropped

    def test_inputs_hold_no_results(self):
        inp = inputs({"AAA": liquid()}, {"AAA": [S[200]]})
        self.assertEqual(set(vars(inp)), set(ed.INPUT_FIELDS))
        for name in ("result", "outcome", "gross", "return", "fill", "exit"):
            self.assertFalse(any(name in f for f in ed.INPUT_FIELDS), name)


class Universe(unittest.TestCase):
    def test_universe_point_in_time(self):
        inp = inputs({"AAA": liquid(), "LOW": liquid(close=4.99, volume=3_000_000), "LATE": liquid()},
                     {"AAA": [S[200]], "LOW": [S[200]], "LATE": [S[301]]})
        u = signals.universe(D, inp)
        self.assertEqual(set(u), {"AAA"})  # $5 on the raw close; a filing after D does not count
        self.assertEqual(u["AAA"]["bars_of"], "AAA")
        self.assertAlmostEqual(u["AAA"]["adv"], 20_000_000.0)

    def rename_case(self, price, new_filed=False, other=None):
        ser = {"NEW": liquid()}
        rows = [(S[250], "1", "OLD"), (S[0], "1", "NEW")] + ([(S[250], "1", "NEW")] if new_filed else [])
        # NEW is a filed symbol of the CIK (filed long ago: not in the first-pass universe unless new_filed)
        if other:
            ser["NEW2"] = liquid()
            rows.append((S[10], "1", "NEW2"))
        prices = {"1": [(S[260], S[259], "a", price)]} if price else {}
        filed = {}
        for d, _, sym in rows:
            filed.setdefault(sym, []).append(d)
        return signals.universe(D, inputs(ser, filed, rows=rows, prices=prices))

    def test_rename_recovers(self):
        for p in (12.0, 20.0, 5.0):  # NEW's raw close 10: within a factor of 2 of the CIK's price, edges included
            u = self.rename_case(p)
            self.assertEqual(u["OLD"]["bars_of"], "NEW", p)

    def test_rename_refuses(self):
        self.assertNotIn("OLD", self.rename_case(20.01))  # 10 < 20.01 / 2
        self.assertNotIn("OLD", self.rename_case(4.99))  # 10 > 2 x 4.99
        self.assertNotIn("OLD", self.rename_case(None))  # no market price of the CIK in the 365 days
        self.assertNotIn("OLD", self.rename_case(12.0, other=True))  # two other symbols with a bar on D
        u = self.rename_case(12.0, new_filed=True)  # NEW is itself in the universe: OLD is the same company twice
        self.assertEqual(set(u), {"NEW"})

    def test_rename_price_window(self):
        ser = {"NEW": liquid()}
        rows = [(S[250], "1", "OLD"), (S[0], "1", "NEW")]
        old_price = {"1": [(S[0], S[0], "a", 12.0)]}  # filed more than 365 days before D
        u = signals.universe(D, inputs(ser, {"OLD": [S[250]], "NEW": [S[0]]}, rows=rows, prices=old_price))
        self.assertNotIn("OLD", u)

    def test_deciles_ties(self):
        adv = {f"S{i:02d}": float(i // 2) for i in range(23)}  # ties in pairs, n = 23
        dec = signals.deciles(adv)
        order = sorted(adv, key=lambda s: (adv[s], s))
        self.assertEqual([dec[s] for s in order], [10 * r // 23 for r in range(23)])
        self.assertEqual(dec["S00"], 0)
        self.assertEqual(dec["S22"], 9)

    def test_no_look_ahead(self):
        inp = inputs({"AAA": liquid(n=400), "BBB": liquid(n=400, volume=3_000_000)}, {"AAA": [S[200]], "BBB": [S[200]]})
        cut = inputs(pe.through(inp.series, D), {"AAA": [S[200]], "BBB": [S[200]]})
        self.assertEqual(signals.universe(D, inp), signals.universe(D, cut))


class Detectors(unittest.TestCase):
    def test_s2_needs_a_bar_on_the_previous_session(self):
        closes = [10.0] * 300 + [10.5]  # +5% on D
        vols = [1_000_000] * 300 + [2_000_000]
        ok = series(S[:301], close=closes, volume=vols)
        gap_dates = S[:299] + [S[300]]  # no bar on the session before D
        gap = series(gap_dates, close=closes[1:], volume=vols[1:])
        inp = inputs({"OK": ok, "GAP": gap}, {"OK": [S[200]], "GAP": [S[200]]},
                     releases={"OK": {D}, "GAP": {D}})
        u = signals.universe(D, inp)
        self.assertEqual(signals.s2(D, inp, u), {"OK"})

    def test_s2_edges(self):
        for jump, fires in ((1.05, True), (1.0499, False)):
            closes = [10.0] * 300 + [10.0 * jump]
            vols = [1_000_000] * 300 + [2_000_000]
            inp = inputs({"A": series(S[:301], close=closes, volume=vols)}, {"A": [S[200]]}, releases={"A": {D}})
            self.assertEqual(signals.s2(D, inp, signals.universe(D, inp)) == {"A"}, fires, jump)

    def test_s2_only_on_a_universe_symbol(self):
        closes = [10.0] * 300 + [10.5]
        vols = [1_000_000] * 300 + [2_000_000]
        inp = inputs({"A": series(S[:301], close=closes, volume=vols)}, {"A": [S[200]]}, releases={"A": {D}})
        self.assertEqual(signals.s2(D, inp, {}), set())

    def test_s1_by_cik_on_the_universe_symbol(self):
        from tests.test_probe_ensemble import buy
        lines = [buy(o, D, 40_000 + int(o), issuer="0000000100", symbol="AAA") for o in "123"]
        inp = inputs({"AAA": liquid()}, {"AAA": [S[200]]}, rows=[(S[200], "100", "AAA")], purchases=lines)
        self.assertEqual(signals.s1(inp, {D: {"AAA"}}), [(D, "AAA")])
        self.assertEqual(signals.s1(inp, {D: set()}), [])  # not in the universe that session: no test firing

    def test_s1_non_session_filing_moves_to_the_next_session(self):
        from tests.test_probe_ensemble import buy
        import datetime as dt
        k = next(k for k in range(280, 320)
                 if (dt.date.fromisoformat(S[k]) + dt.timedelta(1)).isoformat() != S[k + 1])  # a Friday
        weekend = (dt.date.fromisoformat(S[k]) + dt.timedelta(1)).isoformat()
        lines = [buy(o, weekend, 40_000 + int(o), trade=S[k], issuer="100", symbol="AAA") for o in "123"]
        inp = inputs({"AAA": liquid(n=k + 2)}, {"AAA": [S[200]]}, rows=[(S[200], "100", "AAA")], purchases=lines)
        self.assertEqual(signals.s1(inp, {S[k + 1]: {"AAA"}}), [(S[k + 1], "AAA")])  # the next session

    def test_s1_without_a_symbol_uses_the_ciks_universe_symbol(self):
        from tests.test_probe_ensemble import buy
        lines = [buy(o, D, 40_000 + int(o), issuer="100", symbol=None) for o in "123"]
        inp = inputs({"AAA": liquid()}, {"AAA": [S[200]]}, rows=[(S[200], "100", "AAA")], purchases=lines)
        self.assertEqual(signals.s1(inp, {D: {"AAA"}}), [(D, "AAA")])

    def test_renamed_symbol_uses_the_other_bars(self):
        closes = [10.0] * 300 + [10.5]
        vols = [1_000_000] * 300 + [2_000_000]
        rows = [(S[250], "1", "OLD"), (S[0], "1", "NEW")]
        inp = inputs({"NEW": series(S[:301], close=closes, volume=vols)}, {"OLD": [S[250]], "NEW": [S[0]]}, rows=rows,
                     prices={"1": [(S[260], S[259], "a", 10.0)]}, releases={"OLD": {D}})
        u = signals.universe(D, inp)
        self.assertEqual(signals.s2(D, inp, u), {"OLD"})  # OLD fires on NEW's bars, with OLD's own release

    def test_firings_end_to_end_with_warm_up(self):
        inp = inputs({"AAA": liquid(n=400)}, {"AAA": [S[200]]})
        got = signals.firings(inp, S[300], S[330])
        self.assertEqual(set(got), {"firings", "members", "renamed", "deciles", "warm_up", "diagnostics"})
        self.assertEqual(got["members"][S[300]], frozenset({"AAA"}))
        self.assertEqual(got["deciles"][S[300]], (("AAA",), bytes([0])))  # (sorted symbols, their deciles)
        self.assertTrue(all(sig in ("S1", "S2", "S3", "S4") for _, sig, _ in got["firings"]))

    def test_no_look_ahead_detectors(self):
        closes = [10.0 + 0.01 * k for k in range(400)]
        inp = inputs({"A": series(S[:400], close=closes, volume=2_000_000)}, {"A": [S[200]]})
        cut = inputs(pe.through(inp.series, D), {"A": [S[200]]})
        u, uc = signals.universe(D, inp), signals.universe(D, cut)
        self.assertEqual(signals.s2(D, inp, u), signals.s2(D, cut, uc))
        self.assertEqual(signals.s3_state(D, inp, u), signals.s3_state(D, cut, uc))
        self.assertEqual(signals.s4_state(D, inp, u), signals.s4_state(D, cut, uc))


if __name__ == "__main__":
    unittest.main()
