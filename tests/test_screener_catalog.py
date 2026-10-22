"""Screener catalog (Gate B): every filter code the build prompt names is in bot/screener/catalog.yaml with a status;
the catalog parses; statuses and evidence tags are from the fixed vocabularies; G1: no finviz domain in engine code
or workflows."""
import json
import re
import unittest
from pathlib import Path

from bot.screener import catalog

ROOT = Path(__file__).resolve().parents[1]
STATUSES = {catalog.EXACT, catalog.APPROX, catalog.UNAVAILABLE}
EVIDENCE = {"OBSERVED", "INFERRED", "ASSUMED", "UNKNOWN", "DECISION"}


def _codes(prefix, options):
    return [f"{prefix}_{o}" for o in options.split()]


R = lambda a, b, step=1: " ".join(str(x) for x in range(a, b + 1, step))  # noqa: E731
# Every code the prompt's Phase B lists (prefix: options), custom examples included.
PROMPT = (
    _codes("exch", "amex cboe nasd nyse") + _codes("idx", "sp500 ndx dji rut")
    + _codes("sec", "basicmaterials communicationservices consumercyclical consumerdefensive energy financial "
                    "healthcare industrials realestate technology utilities")
    + _codes("ind", "stocksonly exchangetradedfund biotechnology semiconductors banksregional softwareapplication "
                    "reitretail shellcompanies")
    + _codes("geo", "usa notusa asia europe latinamerica bric china israel canada unitedkingdom caymanislands")
    + _codes("cap", "mega large mid small micro nano largeover midover smallover microover largeunder midunder "
                    "smallunder microunder 2to10 0.3to2")
    + _codes("sh_price", "u1 u2 u3 u4 u5 u7 u10 u15 u20 u30 u40 u50 o1 o2 o3 o4 o5 o7 o10 o15 o20 o30 o40 o50 o60 "
                         "o70 o80 o90 o100 1to5 1to10 1to20 5to10 5to20 5to50 10to20 10to50 20to50 50to100 2.5to7")
    + _codes("targetprice", "a50 a40 a30 a20 a10 a5 above below b5 b10 b20 b30 b40 b50")
    + _codes("fa_div", "none pos high veryhigh " + " ".join(f"o{n}" for n in range(1, 11)) + " 2to4")
    + _codes("sh_short", "low high u5 u10 u15 u20 u25 u30 o5 o10 o15 o20 o25 o30 10to40")
    + _codes("an_recom", "strongbuy buybetter buy holdbetter hold holdworse sell sellworse strongsell")
    + _codes("sh_opt", "option short notoption notshort optionshort optionnotshort notoptionshort "
                       "notoptionnotshort shortsalerestricted notshortsalerestricted halted nothalted so10k so100k "
                       "so1m so10m uo1m uo10m uo100m uo1b")
    + _codes("earningsdate", "today todaybefore todayafter tomorrow tomorrowbefore tomorrowafter yesterday "
                             "yesterdaybefore yesterdayafter nextdays5 prevdays5 thisweek nextweek prevweek thismonth")
    + _codes("ipodate", "today yesterday prevweek prevmonth prevquarter prevyear prev2yrs prev3yrs prev5yrs more1 "
                        "more5 more10 more15 more20 more25")
    + _codes("sh_avgvol", "u50 u100 u500 u750 u1000 o50 o100 o200 o300 o400 o500 o750 o1000 o2000 100to500 "
                          "100to1000 500to1000 500to10000 250to")
    + _codes("sh_relvol", "o10 o5 o3 o2 o1.5 o1 o0.75 o0.5 o0.25 u2 u1.5 u1 u0.75 u0.5 u0.25 u0.1 1.5to3")
    + _codes("sh_curvol", "u50 u100 u500 u1000 o0 o50 o100 o500 o1000 o2000 o5000 o10000 o20000 o50sf o100sf")
    + _codes("sh_trades", "o1000 u100")
    + _codes("sh_outstanding", "u1 u5 u10 u20 u50 u100 o1 o2 o5 o10 o20 o50 o100 o200 o500 o1000 5to25")
    + _codes("sh_float", "u1 u5 u10 u20 u50 u100 o1 o2 o5 o10 o20 o50 o100 o200 o500 o1000 "
                         + " ".join(f"u{n}p o{n}p" for n in range(10, 100, 10)))
    + _codes("fa_pe", "low profitable high " + " ".join(f"u{n} o{n}" for n in range(5, 55, 5)) + " 5to15 10to20 15to25")
    + _codes("fa_fpe", "low profitable high " + " ".join(f"u{n} o{n}" for n in range(5, 55, 5)) + " 5to15 10to20")
    + _codes("fa_peg", "low high u1 u1.5 u2 u3 o1 o1.5 o2 o3")
    + _codes("fa_ps", "low high " + " ".join(f"u{n} o{n}" for n in range(1, 11)))
    + _codes("fa_pb", "low high " + " ".join(f"u{n} o{n}" for n in range(1, 11)))
    + _codes("fa_pc", "low high") + _codes("fa_pfcf", "low high")
    + [f"{p}_{o}" for p in ("fa_epsqoq", "fa_salesqoq", "fa_eps5years", "fa_sales5years", "fa_epsyoy", "fa_epsyoy1",
                            "fa_estltgrowth") for o in ("neg", "pos", "poslow", "high", "u5", "o25")]
    + _codes("fa_epsrev", "bp bm bn ep em en eu5 eo5 rp rm rn ru5 ro5") + _codes("fa_divgrowth", "1ypos 3yo5 cy3")
    + _codes("fa_roa", "pos neg high vhigh " + " ".join(f"u{n} o{n}" for n in range(-25, 30, 5)))
    + _codes("fa_roe", "pos neg high vhigh " + " ".join(f"u{n} o{n}" for n in range(-50, 35, 5)))
    + _codes("fa_roi", "pos neg high vhigh " + " ".join(f"u{n} o{n}" for n in range(-50, 35, 5)))
    + _codes("fa_curratio", "high low u0.5 u1 u10 o0.5 o1 o10")
    + _codes("fa_quickratio", "high low u0.1 u0.5 u5 o0.1 o0.5 o5")
    + _codes("fa_ltdebteq", "high low u0.1 u0.5 u1 o0.1 o0.5 o1") + _codes("fa_debteq", "high low u0.1 u1 o0.1 o1")
    + _codes("fa_grossmargin", "pos neg high vhigh " + " ".join(f"u{n} o{n}" for n in range(0, 100, 10)))
    + _codes("fa_opermargin", "pos neg high vhigh") + _codes("fa_netmargin", "pos neg high vhigh")
    + _codes("fa_payoutratio", "none low high " + " ".join(f"u{n} o{n}" for n in range(10, 110, 10)))
    + _codes("sh_insiderown", "low high o10") + _codes("sh_insidertrans", "veryneg neg pos verypos")
    + _codes("sh_instown", "low high o10") + _codes("sh_insttrans", "veryneg neg pos verypos")
    + _codes("ta_perf", "dup ddown d5u d10u d15u d5o d10o d15o 1wup 1wdown 4w10o 13wup 26w50o ytd10u 52w20o 3yup "
                        "5y50o 10y100o intraday")
    + _codes("ta_perf2", "dup 13wup 26w50o")
    + _codes("ta_volatility", "wo3 wo5 mo3 mo5")
    + _codes("ta_rsi", "ob90 ob80 ob70 ob60 os40 os30 os20 os10 " + " ".join(f"u{n} o{n}" for n in range(10, 100, 10)))
    + _codes("ta_gap", "up down " + " ".join(f"u{n}" for n in range(-10, 1)) + " " + " ".join(f"o{n}" for n in range(0, 11)))
    + _codes("ta_change", "up down " + " ".join(f"u{n}" for n in range(-20, 1)) + " "
             + " ".join(f"o{n}" for n in range(0, 21)) + " -10to10 -5to5")
    + _codes("ta_changeopen", "up down u-5 o5 -5to5")
    + [f"ta_sma{n}_{o}" for n in (20, 50, 200) for o in
       ("pa pb p5a p10a p20a p30a p40a p50a p5b p10b p20b p30b p40b p50b cross cross_above cross_below".split())]
    + _codes("ta_sma20", "sa50 sb50") + _codes("ta_sma50", "sa200 sb200") + _codes("ta_sma200", "sa50 sb50")
    + [f"{p}_{o}" for p in ("ta_highlow20d", "ta_highlow50d", "ta_highlow52w", "ta_alltime")
       for o in ("nh nl a5h a10h b5h b10h a5l a10l b5l b10l b0to10h 0to25-bhx 10to30-bhx".split())]
    + _codes("ta_pattern", "horizontal horizontal2 tlresistance tlsupport wedgeup wedgedown wedge triangleasc "
                           "triangledesc channelup channeldown channel doubletop doublebottom multipletop "
                           "multiplebottom headandshoulders headandshouldersinv")
    + _codes("ta_candlestick", "lls lws h ih stw stb d dd gd mw mb")
    + _codes("ta_beta", "u0 u0.5 u1 u1.5 u2 o0 o0.5 o1 o1.5 o2 o3 0to0.5 0.5to1 0.5to1.5 1to1.5 1to2")
    + _codes("ta_averagetruerange", "o0.25 o0.5 o1 o5 u0.25 u0.5 u1 u5 o10 u10")
)
PROMPT_SIGNALS = ("ta_topgainers ta_toplosers ta_newhigh ta_newlow ta_mostvolatile ta_mostactive ta_unusualvolume "
                  "ta_overbought ta_oversold n_downgrades n_upgrades n_earningsbefore n_earningsafter n_majornews "
                  "it_latestbuys it_latestsales f_ssr f_halted").split() + [
    f"ta_p_{p}" for p in "tlsupport tlresistance horizontal wedgeup wedge wedgedown wedgeresistance wedgesupport "
                         "channelup channel channeldown doubletop multipletop doublebottom multiplebottom "
                         "headandshoulders headandshouldersinv".split()]


class Catalog(unittest.TestCase):
    def test_catalog_parses_and_uses_fixed_vocabularies(self):
        c = catalog.load()
        self.assertEqual(c["version"], 1)
        self.assertEqual(len(c["filters"]), len({f["prefix"] for f in c["filters"]}))
        for f in c["filters"]:
            self.assertIn(f["status"], STATUSES, f["prefix"])
            self.assertIn(f["evidence"], EVIDENCE, f["prefix"])
            self.assertTrue(f["reason"].strip(), f["prefix"])  # every status carries its one-line reason
            for name, o in f["options"].items():
                self.assertIn(o.get("evidence", f["evidence"]), EVIDENCE, f"{f['prefix']}_{name}")
                self.assertIn(o.get("status", f["status"]), STATUSES, f"{f['prefix']}_{name}")
        for s in c["signals"]:
            self.assertIn(s["status"], STATUSES, s["code"])
            self.assertTrue(s["reason"].strip(), s["code"])

    def test_every_prompt_code_is_present_with_a_status(self):
        self.assertGreater(len(PROMPT), 700)
        missing = []
        for code in PROMPT:
            try:
                p = catalog.resolve(code)
            except catalog.UnknownCode:
                missing.append(code)
                continue
            self.assertIn(p.status, STATUSES, code)
            if p.status != catalog.UNAVAILABLE:
                self.assertIsNotNone(p.pred, code)
        self.assertEqual(missing, [])

    def test_every_prompt_signal_is_present_with_a_status(self):
        sigs = catalog.signals()
        for code in PROMPT_SIGNALS:
            self.assertIn(code, sigs)
            self.assertIn(sigs[code]["status"], STATUSES)

    def test_observed_public_filters_are_all_catalogued(self):
        """The 82 filter definitions read from finviz on 2026-10-21 (OBSERVED), Elite ones included and flagged."""
        keys = ("exch idx sec industry geo cap fa_div sh_short an_recom sh_opt earningsdate sh_avgvol sh_relvol "
                "sh_curvol sh_trades sh_price targetprice ipodate sh_outstanding sh_float theme subtheme fa_pe fa_fpe "
                "fa_peg fa_ps fa_pb fa_pc fa_pfcf fa_evebitda fa_evsales fa_divgrowth fa_epsyoy fa_epsyoy1 fa_epsqoq "
                "fa_epsyoyttm fa_eps3years fa_eps5years fa_estltgrowth fa_salesqoq fa_salesyoyttm fa_sales3years "
                "fa_sales5years fa_epsrev fa_roa fa_roe fa_roi fa_curratio fa_quickratio fa_ltdebteq fa_debteq "
                "fa_grossmargin fa_opermargin fa_netmargin fa_payoutratio sh_insiderown sh_insidertrans sh_instown "
                "sh_insttrans ta_perf ta_perf2 ta_volatility ta_rsi ta_gap ta_sma20 ta_sma50 ta_sma200 ta_change "
                "ta_changeopen ta_highlow20d ta_highlow50d ta_highlow52w ta_alltime ta_pattern ta_candlestick "
                "ta_beta ta_averagetruerange ah_close ah_change news_date news_keywords etf_heldby").split()
        self.assertEqual(len(keys), 82)
        self.assertEqual(sorted(f["key"] for f in catalog.load()["filters"]), sorted(keys))
        elite = {f["key"] for f in catalog.load()["filters"] if f["elite"]}
        self.assertEqual(elite, {"sh_trades", "ah_close", "ah_change", "news_keywords", "etf_heldby"})

    def test_counts_cover_every_public_filter(self):
        n = catalog.counts()
        self.assertEqual(sum(n["filters"].values()) + n["elite_filters"], len(catalog.load()["filters"]))
        self.assertEqual(sum(n["signals"].values()), 35)


class Predicates(unittest.TestCase):
    def test_strict_over_at_the_boundary(self):
        p = catalog.resolve("sh_short_o20")
        self.assertFalse(catalog.check(p.pred, 0.20, catalog.factor(p.metric, p.prefix)))  # exactly 20%: not over
        self.assertTrue(catalog.check(p.pred, 0.2001, catalog.factor(p.metric, p.prefix)))
        self.assertFalse(catalog.check(catalog.resolve("ta_change_u7").pred, 0.07, 100.0))  # 7.000000000000001
        self.assertTrue(catalog.check(catalog.resolve("ta_change_7to9").pred, 0.07, 100.0))

    def test_cap_mid_includes_exactly_2_billion(self):
        p = catalog.resolve("cap_mid")
        f = catalog.factor(p.metric, p.prefix)
        self.assertTrue(catalog.check(p.pred, 2e9, f))
        self.assertTrue(catalog.check(catalog.resolve("cap_small").pred, 2e9, f))  # both buckets' edges are inclusive
        self.assertFalse(catalog.check(catalog.resolve("cap_midover").pred, 2e9, f))  # "over" is strict

    def test_units(self):
        self.assertEqual(catalog.factor("avg_volume", "sh_avgvol"), 1e-3)  # K shares
        self.assertEqual(catalog.factor("float", "sh_float"), 1e-6)  # M shares
        self.assertEqual(catalog.factor("market_cap", "cap"), 1e-9)  # $B
        self.assertEqual(catalog.factor("change", "ta_change"), 100.0)  # fraction -> percent points
        p = catalog.resolve("sh_avgvol_o500")
        self.assertTrue(catalog.check(p.pred, 500_001, catalog.factor(p.metric, p.prefix)))
        self.assertFalse(catalog.check(p.pred, 500_000, catalog.factor(p.metric, p.prefix)))

    def test_updown_semantics_are_finvizs(self):
        f = 100.0
        self.assertTrue(catalog.check(catalog.resolve("ta_change_u5").pred, 0.051, f))  # "Up 5%"
        self.assertFalse(catalog.check(catalog.resolve("ta_change_u5").pred, 0.04, f))
        self.assertTrue(catalog.check(catalog.resolve("ta_change_d5").pred, -0.06, f))  # "Down 5%"
        self.assertTrue(catalog.check(catalog.resolve("ta_gap_u-5").pred, -0.06, f))  # prompt grammar: under -5%
        self.assertTrue(catalog.check(catalog.resolve("ta_change_-5to5").pred, 0.05, f))  # inclusive range

    def test_or_and_option_metric_overrides(self):
        p = catalog.resolve("sec_technology|healthcare")
        self.assertEqual(len(p.alts), 1)
        self.assertEqual(p.metrics(), ["sector"])
        self.assertEqual(catalog.resolve("ta_sma50_cross200a").metric, "cross_sma50_sma200")
        self.assertEqual(catalog.resolve("ta_perf_13wup").metric, "perf_q")
        self.assertEqual(catalog.resolve("ta_highlow52w_b0to10h").pred, ("range", -10, 0))
        self.assertEqual(catalog.resolve("sh_opt_optionshort").metrics(), ["optionable", "shortable"])

    def test_unavailable_filters_resolve_but_are_not_available(self):
        for code in ("an_recom_buy", "fa_fpe_u15", "idx_sp500", "ta_pattern_wedge", "fa_epsrev_eu5"):
            p = catalog.resolve(code)
            self.assertFalse(p.available, code)
            self.assertTrue(p.reason, code)


class Implemented(unittest.TestCase):
    def test_every_available_option_has_a_calculator(self):
        from bot.screener import parser, run
        from bot.screener.metrics.registry import METRICS
        fns = {"earnings_window", "ipo_window", "news_window", "not_in"}
        for f in catalog.load()["filters"]:
            for name in f["options"]:
                p = catalog.resolve(f"{f['prefix']}_{name}")
                if not p.available:
                    continue
                for a in (p, *p.alts):
                    if a.pred[0] == "fn":
                        self.assertIn(a.pred[1], fns, a.code)
                    else:
                        for m in a.metrics():
                            self.assertIn(m, METRICS, a.code)
        self.assertIn("news", run.sources([parser.parse("news_date_today")]))


class NoFinviz(unittest.TestCase):
    """G1: the engine and the workflows never reference finviz's domain (docs only)."""

    def test_no_finviz_domain_outside_docs(self):
        domain = "finviz" + ".com"
        hits = []
        for base in (ROOT / "bot" / "screener", ROOT / ".github"):
            for path in base.rglob("*"):
                if path.is_file() and path.suffix in (".py", ".yaml", ".yml", ".json", ".md", ".txt"):
                    if domain in path.read_text("utf-8", errors="replace").lower():
                        hits.append(str(path.relative_to(ROOT)))
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
