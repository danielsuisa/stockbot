"""Screener parser (Gate E): filter strings, custom ranges, decimals, negatives, '|' OR, sort / signal / view keys,
pasted URLs (parsed, never fetched), unknown and unavailable codes listed (never dropped silently), the strict
Telegram input rules."""
import unittest

from bot.screener import catalog, parser

# (code string, expected filters as (prefix, predicate)); 40+ strings
CASES = [
    ("cap_small", [("cap", ("range", 0.3, 2))]),
    ("cap_2to10", [("cap", ("range", 2, 10))]),
    ("cap_0.3to2", [("cap", ("range", 0.3, 2))]),
    ("cap_to0.05", [("cap", ("<=", 0.05))]),
    ("cap_200to", [("cap", (">=", 200))]),
    ("sh_float_u50", [("sh_float", ("<", 50))]),
    ("sh_float_u12.5", [("sh_float", ("<", 12.5))]),
    ("sh_float_u30p", [("sh_float", ("<", 30))]),
    ("sh_short_o20", [("sh_short", (">", 20))]),
    ("sh_short_high", [("sh_short", (">", 20))]),
    ("sh_short_10to40", [("sh_short", ("range", 10, 40))]),
    ("sh_relvol_o1.5", [("sh_relvol", (">", 1.5))]),
    ("sh_relvol_0.5to2.25", [("sh_relvol", ("range", 0.5, 2.25))]),
    ("sh_avgvol_o500", [("sh_avgvol", (">", 500))]),
    ("sh_avgvol_100to500", [("sh_avgvol", ("range", 100, 500))]),
    ("sh_curvol_o1000", [("sh_curvol", (">", 1000))]),
    ("sh_price_o2", [("sh_price", (">", 2))]),
    ("sh_price_5to50", [("sh_price", ("range", 5, 50))]),
    ("sh_price_u0.5", [("sh_price", ("<", 0.5))]),
    ("fa_pe_u15", [("fa_pe", ("<", 15))]),
    ("fa_pe_low", [("fa_pe", ("<", 15))]),
    ("fa_pe_profitable", [("fa_pe", (">", 0))]),
    ("fa_roe_o15", [("fa_roe", (">", 15))]),
    ("fa_roe_u-10", [("fa_roe", ("<", -10))]),
    ("fa_roa_o-5", [("fa_roa", (">", -5))]),
    ("fa_grossmargin_-20to20", [("fa_grossmargin", ("range", -20, 20))]),
    ("fa_curratio_o1.5", [("fa_curratio", (">", 1.5))]),
    ("fa_debteq_u0.5", [("fa_debteq", ("<", 0.5))]),
    ("fa_div_none", [("fa_div", ("eq", 0))]),
    ("fa_div_pos", [("fa_div", (">", 0))]),
    ("ta_rsi_os30", [("ta_rsi", ("<", 30))]),
    ("ta_rsi_ob70", [("ta_rsi", (">", 70))]),
    ("ta_rsi_nos50", [("ta_rsi", (">", 50))]),
    ("ta_rsi_25to45", [("ta_rsi", ("range", 25, 45))]),
    ("ta_gap_u-5", [("ta_gap", ("<", -5))]),
    ("ta_gap_u3", [("ta_gap", (">", 3))]),
    ("ta_gap_d2", [("ta_gap", ("<", -2))]),
    ("ta_change_-10to10", [("ta_change", ("range", -10, 10))]),
    ("ta_change_up", [("ta_change", (">", 0))]),
    ("ta_changeopen_d5", [("ta_changeopen", ("<", -5))]),
    ("ta_volatility_wo5", [("ta_volatility", (">", 5))]),
    ("ta_highlow52w_b0to10h", [("ta_highlow52w", ("range", -10, 0))]),
    ("ta_highlow20d_nh", [("ta_highlow20d", ("is", True))]),
    ("ta_sma50_pa", [("ta_sma50", (">", 0))]),
    ("ta_sma200_pb20", [("ta_sma200", ("<", -20))]),
    ("ta_beta_0.5to1.5", [("ta_beta", ("range", 0.5, 1.5))]),
    ("ta_averagetruerange_o0.75", [("ta_averagetruerange", (">", 0.75))]),
    ("ta_perf_13wup", [("ta_perf", (">", 0))]),
    ("ta_perf_26w50o", [("ta_perf", (">", 50))]),
    ("ta_perf_ytd10u", [("ta_perf", ("<", -10))]),
    ("cap_small,sh_float_u50,sh_short_o20,ta_rsi_os30",
     [("cap", ("range", 0.3, 2)), ("sh_float", ("<", 50)), ("sh_short", (">", 20)), ("ta_rsi", ("<", 30))]),
    ("ta_sma50_pa,ta_sma200_pa,ta_highlow52w_b0to10h",
     [("ta_sma50", (">", 0)), ("ta_sma200", (">", 0)), ("ta_highlow52w", ("range", -10, 0))]),
    ("CAP_SMALL", [("cap", ("range", 0.3, 2))]),  # case-insensitive
]


class Codes(unittest.TestCase):
    def test_at_least_forty_code_strings(self):
        self.assertGreaterEqual(len(CASES), 40)

    def test_each_code_string_parses_to_its_predicates(self):
        for text, want in CASES:
            q = parser.parse(text)
            self.assertEqual([(p.prefix, p.pred) for p in q.filters], want, text)
            self.assertEqual(q.ignored, [], text)

    def test_or_inside_one_filter(self):
        q = parser.parse("sec_technology|healthcare,exch_nasd|nyse")
        self.assertEqual([p.prefix for p in q.filters], ["sec", "exch"])
        sec = q.filters[0]
        self.assertEqual([sec.option] + [a.option for a in sec.alts], ["technology", "healthcare"])

    def test_custom_flag_and_evidence(self):
        p = parser.parse("sh_float_u37").filters[0]
        self.assertTrue(p.custom)
        self.assertEqual(p.evidence, "INFERRED")
        self.assertFalse(parser.parse("sh_float_u50").filters[0].custom)  # a named OBSERVED option


class Keys(unittest.TestCase):
    def test_sort_signal_view(self):
        q = parser.parse("cap_small o=-change s=ta_unusualvolume v=111")
        self.assertEqual(q.sort, ("change", True, "change"))
        self.assertEqual(q.signal["code"], "ta_unusualvolume")
        self.assertEqual(q.view, "overview")
        self.assertEqual(parser.parse("o=ticker").sort, ("ticker", False, "ticker"))
        self.assertEqual(parser.parse("o=-marketcap").sort, ("market_cap", True, "marketcap"))

    def test_signal_and_filters_in_one_list(self):
        q = parser.parse("s=ta_unusualvolume,sh_avgvol_o500,sh_price_o2")
        self.assertEqual(q.signal["code"], "ta_unusualvolume")
        self.assertEqual([p.code for p in q.filters], ["sh_avgvol_o500", "sh_price_o2"])

    def test_a_pasted_url_is_parsed_not_fetched(self):
        url = ("https://example.com/screener.ashx?v=111&f=cap_small,sh_float_u50,sh_short_o20,ta_rsi_os30"
               "&o=-change&s=ta_unusualvolume&r=21")
        q = parser.parse(url)
        self.assertEqual([p.code for p in q.filters], ["cap_small", "sh_float_u50", "sh_short_o20", "ta_rsi_os30"])
        self.assertEqual((q.sort[0], q.signal["code"]), ("change", "ta_unusualvolume"))
        q2 = parser.parse("screener?v=340&s=ta_topgainers")
        self.assertEqual(q2.signal["code"], "ta_topgainers")
        self.assertEqual(q2.view, "snapshot")

    def test_query_string_with_encoded_commas(self):
        q = parser.parse("f=cap_small%2Csh_price_o5&o=price")
        self.assertEqual([p.code for p in q.filters], ["cap_small", "sh_price_o5"])

    def test_canonical_key(self):
        self.assertEqual(parser.parse("o=-change  cap_small s=ta_topgainers").key(),
                         "cap_small s=ta_topgainers o=-change")


class Ignored(unittest.TestCase):
    def test_unknown_and_unavailable_codes_are_listed(self):
        q = parser.parse("cap_small,zz_nothing,an_recom_buy,fa_fpe_u15,s=ta_p_wedge,o=nosuch")
        self.assertEqual([p.code for p in q.filters], ["cap_small"])
        got = dict(q.ignored)
        self.assertEqual(got["zz_nothing"], "unknown code")
        self.assertTrue(got["an_recom_buy"].startswith("unavailable: "))
        self.assertTrue(got["fa_fpe_u15"].startswith("unavailable: "))
        self.assertTrue(got["ta_p_wedge"].startswith("unavailable: "))
        self.assertEqual(got["o=nosuch"], "unknown sort key")
        self.assertIsNone(q.signal)

    def test_a_second_code_of_the_same_filter_replaces_the_first(self):
        q = parser.parse("sh_price_o2,sh_price_o5")
        self.assertEqual([p.code for p in q.filters], ["sh_price_o5"])
        self.assertEqual(q.ignored, [("sh_price_o2", "replaced by sh_price_o5")])

    def test_two_signals(self):
        q = parser.parse("s=ta_topgainers,ta_toplosers")
        self.assertEqual(q.signal["code"], "ta_topgainers")
        self.assertEqual(q.ignored, [("ta_toplosers", "only one signal per screen")])

    def test_approx_flag(self):
        self.assertTrue(parser.parse("sh_float_u50").approx)
        self.assertFalse(parser.parse("sh_price_o5,ta_rsi_os30").approx)


class Strict(unittest.TestCase):
    def test_telegram_input_rules(self):
        parser.parse("cap_small,sh_price_o5 o=-change", strict=True)
        with self.assertRaises(parser.InputError):
            parser.parse("cap_small;rm -rf", strict=True)
        with self.assertRaises(parser.InputError):
            parser.parse(",".join(["sh_price_o5"] * 26), strict=True)
        with self.assertRaises(parser.InputError):
            parser.parse("sh_price_o5<script>", strict=True)


if __name__ == "__main__":
    unittest.main()
