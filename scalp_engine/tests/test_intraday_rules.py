"""The live screener's rules as copied into scalp_engine/intraday_rules.py (ADR 0016 section 1): the verbatim blocks
hash to the bot's text at 09bfa55, and the bot's own test vectors (stockbot tests/test_intraday_screener.py at that
SHA) pass against the copy and its adapters. Synthetic data only."""
import hashlib
import re
import unittest
from pathlib import Path

from scalp_engine import intraday_rules as R

# SHA-256 of each block as extracted from `git show 09bfa55:bot/<file>.py` on 2026-10-20 (T1 Stage 0).
BOT_SEGMENTS = {
    "intraday.constants": "c31ebc5f46d48ade422f80cec20be6cebe22cd711eaa399037529891de4dbe62",
    "intraday.pure": "ca8d0b4abb3a4f7fa6e64b98fc956e14bcf7281e285d870157b39299d1ad783b",
    "ticket.mins": "3c5b8aa8f823a75e1911167493107df8a0c77112b74d9fc091354fead1a3b818",
    "ticket.hhmm": "757c00df394404d0fd817f436aa133cc55a40cca0ced4eb7e6fb2c871f60327c",
    "ticket.to5": "4f7ed4a6d7710b3a3f9ecaceb3e4a37610070286a898bbc951cc3a10713d0f51",
    "ticket.vwap": "a298fefa7f22214566d59829f7948212c505d3a7169b70193269124bc4fbe98e",
    "ticket.atr14": "a10db9884786628fac598460f81ec6b2b594e99d3d479d5e33d0a957775da83a",
    "levels.EPS": "749b507052400118beb226b6f97b5fcfa265cca639cdbc711f67808006a37204",
    "levels.px": "88f8843f457196a599cf36f599075520c3806eda1c54bc3e7620809d606f403c",
}
ATR, PREV = 1.00, 20.00


def c(start, o, h, l, c_, v, vwap=None):
    end = R.hhmm(R.mins(start) + 5)
    return {"start": start, "end": end, "o": o, "h": h, "l": l, "c": c_, "v": v, "vw": c_, "vwap": vwap or c_ - 0.05}


def minutes(start_hhmm, candles5):
    out, t = [], R.mins(start_hhmm)
    for o, h, l, c_, v in candles5:
        out.append((R.hhmm(t), o, h, l, c_, v, (o + c_) / 2))
        for k in range(1, 5):
            out.append((R.hhmm(t + k), c_, c_, c_, c_, 0, c_))
        t += 5
    return out


class Verbatim(unittest.TestCase):
    def test_blocks_hash_to_the_bot(self):
        src = Path(R.__file__).read_text(encoding="utf-8").replace("\r\n", "\n")
        blocks = dict(re.findall(r"# >>> verbatim (\S+)\n(.*?)# <<< verbatim \1\n", src, re.S))
        self.assertEqual(sorted(blocks), sorted(BOT_SEGMENTS))
        for name, text in blocks.items():
            self.assertEqual(hashlib.sha256(text.encode()).hexdigest(), BOT_SEGMENTS[name], name)

    def test_rules_hash_is_stable(self):
        self.assertEqual(len(R.rules_sha256()), 64)
        self.assertEqual(R.SOURCE_SHA, "09bfa55c7445762cf021e6a3a1d8128527f07eba")


class BotVectors(unittest.TestCase):
    """The bot's own tests at the SHA, against the copy."""

    def test_shapes(self):
        self.assertTrue(R.is_hammer(c("10:00", 20.0, 20.1, 19.5, 20.05, 1)))
        self.assertFalse(R.is_hammer(c("10:00", 20.0, 20.5, 19.9, 20.05, 1)))
        prev, b = c("10:00", 20.2, 20.25, 19.9, 20.0, 1), c("10:05", 19.95, 20.4, 19.9, 20.3, 1)
        self.assertTrue(R.is_bull_engulfing(prev, b))
        self.assertFalse(R.is_bull_engulfing(b, prev))

    def test_orb5(self):
        b5 = [c("09:30", 20, 20.5, 19.8, 20.3, 100), c("09:35", 20.3, 20.45, 20.1, 20.4, 90),
              c("09:40", 20.4, 20.8, 20.35, 20.7, 200)]
        s = R.orb(b5, 5)
        self.assertEqual((s["setup"], s["trigger_high"], s["stop_ref"]), ("orb5", 20.8, None))
        b5.append(c("09:45", 20.7, 21.0, 20.6, 20.9, 200))
        self.assertIsNone(R.orb(b5, 5))
        b5 = [c("09:30", 20, 20.5, 19.8, 20.3, 100), c("09:35", 20.3, 20.45, 20.1, 20.4, 90),
              c("09:40", 20.4, 20.8, 20.35, 20.7, 50)]
        self.assertIsNone(R.orb(b5, 5))
        b5[-1]["v"], b5[-1]["vwap"] = 300, 20.9
        self.assertIsNone(R.orb(b5, 5))

    def test_vwap_hammer_flag_red_to_green(self):
        b5 = [c("09:30", 20, 20.5, 19.9, 20.4, 100, 20.1), c("09:35", 20.4, 20.6, 20.3, 20.5, 90, 20.2),
              c("09:40", 20.5, 20.55, 20.3, 20.45, 80, 20.3), c("09:45", 20.45, 20.5, 20.0, 20.47, 120, 20.33)]
        s = R.vwap_pullback(b5)
        self.assertEqual((s["setup"], s["trigger_high"], s["stop_ref"]), ("vwap_hammer", 20.5, 20.0))
        b5 = [c("09:30", 20, 20.2, 19.9, 20.1, 100), c("09:35", 20.1, 20.9, 20.05, 20.8, 300),
              c("09:40", 20.8, 20.85, 20.6, 20.65, 80), c("09:45", 20.65, 20.7, 20.55, 20.6, 60)]
        s = R.bull_flag(b5, ATR)
        self.assertEqual((s["setup"], s["trigger_high"], s["stop_ref"]), ("flag", 20.9, 20.55))
        deep = b5[:2] + [c("09:40", 20.8, 20.85, 20.2, 20.3, 80), c("09:45", 20.3, 20.4, 20.2, 20.35, 60)]
        self.assertIsNone(R.bull_flag(deep, ATR))
        b5 = [c("09:30", 19.7, 19.8, 19.5, 19.6, 100), c("09:35", 19.6, 19.9, 19.55, 19.85, 90),
              c("09:40", 19.85, 20.2, 19.8, 20.1, 250)]
        self.assertEqual(R.red_to_green(b5, PREV)["stop_ref"], 19.5)

    def test_candles_only_completed(self):
        bars = minutes("09:30", [(20, 20.5, 19.8, 20.3, 100), (20.3, 20.45, 20.1, 20.4, 90)])
        self.assertEqual([b["end"] for b in R.candles(bars, "09:37")], ["09:35"])
        self.assertEqual(len(R.candles(bars, "09:40")), 2)

    def test_bracket(self):
        t = R.bracket("ACME", R._ticket("flag", 20.9, 20.55, "x", 2.0), ATR, 100.0)
        self.assertEqual((t["entry_stop"], t["stop_loss"], t["target"], t["R"], t["limit"]), (20.91, 20.54, 21.65, 0.37, 20.96))
        self.assertEqual(t["shares"], int(100 / 0.37))
        self.assertEqual(R.bracket("ACME", R._ticket("orb5", 20.9, None, "x", 3.0), ATR, 100.0)["R"], 0.10)
        wide = R.bracket("ACME", R._ticket("flag", 20.9, 18.0, "x", 2.0), ATR, 100.0)
        self.assertEqual((wide["R"], wide["stop_loss"]), (1.0, 19.91))
        self.assertEqual(R.bracket("ACME", R._ticket("flag", 20.9, 20.55, "x", 2.0), ATR, 100.0, max_position_value=100)["shares"], 4)
        self.assertFalse(R.bracket("ACME", R._ticket("flag", 20.9, 20.55, "x", 2.0), ATR, 0.5)["ok"])
        t = R.bracket("ACME", R._ticket("orb5", 100.0, None, "x", 3.0), 0.5, 100.0)
        self.assertEqual((t["R"], t["r_floored"]), (0.30, True))
        t = R.bracket("ACME", R._ticket("flag", 20.9, 20.55, "x", 2.0), ATR, 10_000.0, adv=100_000)
        self.assertEqual(t["shares"], 850)

    def test_study_mode_ok_is_r_positive(self):
        self.assertTrue(R.bracket("ACME", R._ticket("flag", 499.0, 498.0, "x", 2.0), 5.0, R.STUDY_RISK)["ok"])


class Adapters(unittest.TestCase):
    DAILY = {"ACME": [(f"2026-09-{d:02}", 20.0, 21.0, 19.0, 20.0, 2_000_000) for d in range(1, 29)],
             "TINY": [(f"2026-09-{d:02}", 3.0, 3.2, 2.9, 3.0, 2_000_000) for d in range(1, 29)],
             "SLOW": [(f"2026-09-{d:02}", 50.0, 50.3, 49.8, 50.0, 2_000_000) for d in range(1, 29)]}

    def test_daily_stats(self):
        got = R.daily_stats_from(self.DAILY)
        self.assertEqual(list(got), ["ACME"])
        self.assertAlmostEqual(got["ACME"]["atr"], 2.0)
        self.assertEqual(got["ACME"]["adv"], 2_000_000)
        self.assertEqual(R.daily_stats_from({"ACME": self.DAILY["ACME"][:15]}), {})  # fewer than 16 rows

    def test_in_play_and_rerank(self):
        stats = {"ACME": {"close": 20.0, "atr": 2.0, "adv": 2e6, "advd": 4e7}, "FLAT": {"close": 20.0, "atr": 2.0, "adv": 2e6, "advd": 4e7}}
        rows = R.in_play_from(stats, {"ACME": (300_000, 20.9), "FLAT": (10_000, 20.0)},
                              {"ACME": [("09:30", 20.9, 21, 20.8, 20.95, 5000, 20.9)], "FLAT": [("09:30", 20.05, 20.1, 20, 20.05, 50, 20.05)]})
        self.assertEqual([r["symbol"] for r in rows], ["ACME"])
        self.assertAlmostEqual(rows[0]["gap"], 0.045)
        self.assertAlmostEqual(rows[0]["pre_rel"], 0.15)
        # the open falls back to the last pre-market price when IEX has no 09:30 bar
        rows = R.in_play_from(stats, {"ACME": (300_000, 20.9)}, {})
        self.assertAlmostEqual(rows[0]["gap"], 0.045)
        today = {"ACME": (20.9, 21.5, 20.8, 21.4, 900_000), "DULL": (10, 10.1, 9.9, 10, 50_000)}
        hist = [{"ACME": (20, 20.5, 19.8, 20.3, 100_000), "DULL": (10, 10.1, 9.9, 10, 100_000)}]
        rel = R.open_relvol_from(["DULL", "ACME"], today, hist)
        watch = R.rerank_from([{"symbol": "DULL"}, {"symbol": "ACME"}], rel)
        self.assertEqual([w["symbol"] for w in watch], ["ACME"])
        self.assertEqual(watch[0]["open_relvol"], 9.0)

    def test_run_scan_dedupes_and_records_signal_candles(self):
        state = R.fresh_state("2026-10-19")
        state["watch"] = [{"symbol": "ACME", "gap": 0.03, "pre_rel": 0.2, "atr": ATR, "prev_close": PREV, "open_relvol": 2.0}]
        bars = {"ACME": minutes("09:30", [(20, 20.5, 19.8, 20.3, 100), (20.3, 20.45, 20.1, 20.4, 90),
                                         (20.4, 20.8, 20.35, 20.7, 300)])}
        got = R.run_scan_from(state, "09:45", bars, lambda s: {}, risk=100.0)
        self.assertEqual([t["setup"] for t in got], ["orb5"])
        self.assertEqual(R.run_scan_from(state, "09:50", bars, lambda s: {}, risk=100.0), [])
        self.assertEqual(state["sent"], {"ACME:orb5": "09:45"})
        self.assertIn(("09:45", "ACME", "orb5"), state["detected"])

    def test_afternoon_needs_an_up_day_above_vwap(self):
        up = minutes("09:30", [(20, 20.5, 19.8, 20.3, 100), (20.3, 20.45, 20.1, 20.4, 90), (20.4, 20.8, 20.35, 20.7, 300)])
        watch = [{"symbol": "UP", "gap": 0.0, "pre_rel": 0.2, "atr": ATR, "prev_close": PREV, "open_relvol": 2.0}]
        self.assertEqual([t["symbol"] for t in R.scan_from(watch, {"UP": up}, "09:45", 100.0, None)[0]], ["UP"])
        down = minutes("09:30", [(25, 25, 20.1, 20.3, 100), (20.3, 20.45, 20.1, 20.4, 90), (20.4, 20.8, 20.35, 20.7, 300)])
        self.assertEqual(R.scan_from(watch, {"UP": down}, "15:05", 100.0, None)[0], [])  # opened at 25, below it

    def test_scan_times(self):
        self.assertEqual(R.scan_times("morning")[0], "09:40")
        self.assertEqual(R.scan_times("morning")[-1], "11:30")
        self.assertEqual(len(R.scan_times("morning")), 23)
        self.assertEqual(R.scan_times("power"), ["15:00", "15:05", "15:10", "15:15", "15:20", "15:25", "15:30", "15:35",
                                                 "15:40", "15:45"])


if __name__ == "__main__":
    unittest.main()
