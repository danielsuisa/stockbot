"""The intraday candlestick screener (bot/intraday.py): setups over synthetic 5-minute candles, the bracket, outcomes, messages."""
import datetime as dt
import json
import os
import tempfile
import unittest
from unittest import mock

from bot import intraday, ticket
import msgrules

ATR, PREV = 1.00, 20.00


def c(start, o, h, l, c_, v, vwap=None):
    end = ticket.hhmm(ticket.mins(start) + 5)
    return {"start": start, "end": end, "o": o, "h": h, "l": l, "c": c_, "v": v, "vw": c_, "vwap": vwap or c_ - 0.05}


def minutes(start_hhmm, candles5):
    """5-minute candle specs (o, h, l, c, v) -> 1-minute bars: one minute carries the candle (others flat)."""
    out, t = [], ticket.mins(start_hhmm)
    for o, h, l, c_, v in candles5:
        out.append((ticket.hhmm(t), o, h, l, c_, v, (o + c_) / 2))
        for k in range(1, 5):
            out.append((ticket.hhmm(t + k), c_, c_, c_, c_, 0, c_))
        t += 5
    return out


class Shapes(unittest.TestCase):
    def test_hammer(self):
        self.assertTrue(intraday.is_hammer(c("10:00", 20.0, 20.1, 19.5, 20.05, 1)))  # long lower wick, close near high
        self.assertFalse(intraday.is_hammer(c("10:00", 20.0, 20.5, 19.9, 20.05, 1)))  # upper wick: not a hammer

    def test_engulfing(self):
        prev, b = c("10:00", 20.2, 20.25, 19.9, 20.0, 1), c("10:05", 19.95, 20.4, 19.9, 20.3, 1)
        self.assertTrue(intraday.is_bull_engulfing(prev, b))
        self.assertFalse(intraday.is_bull_engulfing(b, prev))


class Setups(unittest.TestCase):
    def test_orb5_first_close_above_range(self):
        b5 = [c("09:30", 20, 20.5, 19.8, 20.3, 100), c("09:35", 20.3, 20.45, 20.1, 20.4, 90),
              c("09:40", 20.4, 20.8, 20.35, 20.7, 200)]  # breaks 20.5 on 2x volume
        s = intraday.orb(b5, 5)
        self.assertEqual(s["setup"], "orb5")
        self.assertEqual(s["trigger_high"], 20.8)
        self.assertIsNone(s["stop_ref"])  # the 10% ATR stop
        b5.append(c("09:45", 20.7, 21.0, 20.6, 20.9, 200))
        self.assertIsNone(intraday.orb(b5, 5))  # the second candle above the range is a chase

    def test_orb_needs_volume_and_vwap(self):
        b5 = [c("09:30", 20, 20.5, 19.8, 20.3, 100), c("09:35", 20.3, 20.45, 20.1, 20.4, 90),
              c("09:40", 20.4, 20.8, 20.35, 20.7, 50)]
        self.assertIsNone(intraday.orb(b5, 5))
        b5[-1]["v"], b5[-1]["vwap"] = 300, 20.9
        self.assertIsNone(intraday.orb(b5, 5))

    def test_vwap_hammer(self):
        b5 = [c("09:30", 20, 20.5, 19.9, 20.4, 100, 20.1), c("09:35", 20.4, 20.6, 20.3, 20.5, 90, 20.2),
              c("09:40", 20.5, 20.55, 20.3, 20.45, 80, 20.3), c("09:45", 20.45, 20.5, 20.0, 20.47, 120, 20.33)]
        s = intraday.vwap_pullback(b5)
        self.assertEqual(s["setup"], "vwap_hammer")
        self.assertEqual((s["trigger_high"], s["stop_ref"]), (20.5, 20.0))

    def test_bull_flag(self):
        b5 = [c("09:30", 20, 20.2, 19.9, 20.1, 100), c("09:35", 20.1, 20.9, 20.05, 20.8, 300),  # impulse +0.8 ATR
              c("09:40", 20.8, 20.85, 20.6, 20.65, 80), c("09:45", 20.65, 20.7, 20.55, 20.6, 60)]  # small, quiet
        s = intraday.bull_flag(b5, ATR)
        self.assertEqual(s["setup"], "flag")
        self.assertEqual((s["trigger_high"], s["stop_ref"]), (20.9, 20.55))
        deep = b5[:2] + [c("09:40", 20.8, 20.85, 20.2, 20.3, 80), c("09:45", 20.3, 20.4, 20.2, 20.35, 60)]
        self.assertIsNone(intraday.bull_flag(deep, ATR))  # gave back more than half

    def test_red_to_green(self):
        b5 = [c("09:30", 19.7, 19.8, 19.5, 19.6, 100), c("09:35", 19.6, 19.9, 19.55, 19.85, 90),
              c("09:40", 19.85, 20.2, 19.8, 20.1, 250)]
        s = intraday.red_to_green(b5, PREV)
        self.assertEqual(s["setup"], "red_to_green")
        self.assertEqual(s["stop_ref"], 19.5)
        self.assertIsNone(intraday.red_to_green([c("09:30", 20.1, 20.3, 20, 20.2, 1)] + b5[1:], PREV))

    def test_candles_only_completed(self):
        bars = minutes("09:30", [(20, 20.5, 19.8, 20.3, 100), (20.3, 20.45, 20.1, 20.4, 90)])
        self.assertEqual([b["end"] for b in intraday.candles(bars, "09:37")], ["09:35"])
        self.assertEqual(len(intraday.candles(bars, "09:40")), 2)
        self.assertIn("vwap", intraday.candles(bars, "09:40")[0])


class Bracket(unittest.TestCase):
    def test_levels_and_size(self):
        t = intraday.bracket("ACME", intraday._ticket("flag", 20.9, 20.55, "x", 2.0), ATR, 100.0)
        self.assertEqual((t["entry_stop"], t["stop_loss"], t["target"]), (20.91, 20.54, 21.65))
        self.assertEqual(t["R"], 0.37)
        self.assertEqual(t["shares"], int(100 / 0.37))
        self.assertEqual(t["limit"], 20.96)

    def test_floor_and_cap(self):
        tight = intraday.bracket("ACME", intraday._ticket("orb5", 20.9, None, "x", 3.0), ATR, 100.0)
        self.assertEqual(tight["R"], 0.10)
        wide = intraday.bracket("ACME", intraday._ticket("flag", 20.9, 18.0, "x", 2.0), ATR, 100.0)
        self.assertEqual(wide["R"], 1.0)
        self.assertEqual(wide["stop_loss"], 19.91)

    def test_position_cap(self):
        t = intraday.bracket("ACME", intraday._ticket("flag", 20.9, 20.55, "x", 2.0), ATR, 100.0, max_position_value=100)
        self.assertEqual(t["shares"], 4)
        self.assertFalse(intraday.bracket("ACME", intraday._ticket("flag", 20.9, 20.55, "x", 2.0), ATR, 0.5)["ok"])


def _t(**kw):
    base = {"symbol": "ACME", "setup": "flag", "note": "x", "score": 2, "entry_stop": 20.91, "limit": 20.96,
            "stop_loss": 20.54, "target": 21.65, "R": 0.37, "r_floored": False, "shares": 270, "risk": 99.9,
            "time_exit": "15:55", "atr": ATR, "gap": 0.031, "pre_rel": 0.22, "open_relvol": 3.2, "at": "09:45",
            "last": 20.6, "vwap": 20.3, "ok": True}
    return {**base, **kw}


class Outcomes(unittest.TestCase):
    def test_target(self):
        bars = minutes("09:45", [(20.6, 20.7, 20.5, 20.65, 1), (20.65, 20.95, 20.6, 20.9, 1), (20.9, 21.7, 20.8, 21.6, 1)])
        o = intraday.outcome(_t(), bars)
        self.assertEqual((o["result"], o["fill"]), ("target", 20.91))
        self.assertEqual(o["r"], 2.0)

    def test_stop_before_target_same_minute_is_stop(self):
        bars = minutes("09:45", [(20.65, 20.95, 20.6, 20.9, 1), (20.9, 21.7, 20.5, 21.6, 1)])
        self.assertEqual(intraday.outcome(_t(), bars)["result"], "stop")

    def test_gap_past_limit_and_time_exit(self):
        bars = minutes("09:45", [(20.65, 20.95, 20.6, 20.9, 1), (21.2, 21.3, 21.1, 21.2, 1)])
        self.assertEqual(intraday.outcome(_t(entry_stop=21.0, limit=21.05), bars)["result"], "not filled (gapped past limit)")
        bars = minutes("09:45", [(20.65, 20.95, 20.6, 20.9, 1)]) + minutes("15:55", [(21.1, 21.15, 21.05, 21.1, 1)])
        o = intraday.outcome(_t(), bars)
        self.assertEqual(o["result"], "time")
        self.assertAlmostEqual(o["r"], round((21.1 - 20.91) / 0.37, 2))


class Messages(unittest.TestCase):
    def test_ticket_and_list_follow_the_rules(self):
        for text in (intraday.ticket_text(_t(), "2026-10-19", 10_000.0), intraday.ticket_text(_t(gap=-0.04, r_floored=True), "2026-10-19"),
                     intraday.list_text([_t()], "2026-10-19", "09:45", None), intraday.list_text([], "2026-10-19", "09:45", None),
                     intraday.watch_text([{"symbol": "ACME", "gap": 0.03, "pre_rel": 0.2, "atr": 1.0, "prev_close": 20.0,
                                           "open_relvol": 2.5}], "2026-10-19")):
            self.assertEqual(msgrules.problems(text), [], text)
        text = intraday.ticket_text(_t(), "2026-10-19", 10_000.0)
        for needle in ("20.91$", "20.54$", "21.65$", "270", "1.00%", "16:45", "22:55"):  # 09:45 NY = 16:45 Israel (EDT)
            self.assertIn(needle, text)

    def test_stats_text(self):
        st = {"day": "2026-10-19", "journal": [_t(outcome={"result": "target", "fill": 20.91, "r": 2.0}),
                                                _t(symbol="BETA", outcome={"result": "not filled", "r": 0.0})]}
        text = intraday.stats_text(st)
        self.assertEqual(msgrules.problems(text), [], text)
        self.assertIn("2.00", text)


class State(unittest.TestCase):
    def test_run_scan_dedupes_and_journals(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"INTRADAY_DIR": d, "TG_TOKEN": ""}):
            state = intraday.fresh_state("2026-10-19")
            state["watch"] = [{"symbol": "ACME", "gap": 0.03, "pre_rel": 0.2, "atr": ATR, "prev_close": PREV, "open_relvol": 2.0}]
            bars = {"ACME": minutes("09:30", [(20, 20.5, 19.8, 20.3, 100), (20.3, 20.45, 20.1, 20.4, 90),
                                             (20.4, 20.8, 20.35, 20.7, 300)])}
            with mock.patch.object(intraday.alpaca, "live_bars", return_value=bars), \
                    mock.patch.object(intraday, "sizing", return_value=(100.0, None, None)):
                got = intraday.run_scan(state, "2026-10-19", [], "09:45", dry=True)
                self.assertEqual([t["setup"] for t in got], ["orb5"])
                self.assertEqual(intraday.run_scan(state, "2026-10-19", [], "09:50", dry=True), [])
            self.assertEqual(len(state["journal"]), 1)
            intraday.save(state)
            self.assertEqual(intraday.load(None)["sent"], {"ACME:orb5": "09:45"})


if __name__ == "__main__":
    unittest.main()


class Stage1(unittest.TestCase):
    DAILY = {"ACME": [(f"2026-09-{d:02}", 20.0, 21.0, 19.0, 20.0, 2_000_000) for d in range(1, 29)],
             "TINY": [(f"2026-09-{d:02}", 3.0, 3.2, 2.9, 3.0, 2_000_000) for d in range(1, 29)],
             "SLOW": [(f"2026-09-{d:02}", 50.0, 50.3, 49.8, 50.0, 2_000_000) for d in range(1, 29)]}

    def test_daily_stats_filters(self):
        with mock.patch.object(intraday.alpaca, "daily", return_value=self.DAILY):
            got = intraday.daily_stats(["ACME", "TINY", "SLOW"], "2026-09-28", "2026-09-01")
        self.assertEqual(list(got), ["ACME"])  # TINY: price; SLOW: ATR 1% and $0.50
        self.assertAlmostEqual(got["ACME"]["atr"], 2.0)
        self.assertEqual(got["ACME"]["adv"], 2_000_000)

    def test_in_play_and_rerank(self):
        stats = {"ACME": {"close": 20.0, "atr": 2.0, "adv": 2e6, "advd": 4e7}, "FLAT": {"close": 20.0, "atr": 2.0, "adv": 2e6, "advd": 4e7}}
        with mock.patch.object(intraday.alpaca, "volume_since", return_value={"ACME": (300_000, 20.9), "FLAT": (10_000, 20.0)}), \
                mock.patch.object(intraday.alpaca, "live_bars", return_value={"ACME": [("09:30", 20.9, 21, 20.8, 20.95, 5000, 20.9)], "FLAT": [("09:30", 20.05, 20.1, 20, 20.05, 50, 20.05)]}):
            rows = intraday.in_play(stats, "2026-09-29")
        self.assertEqual([r["symbol"] for r in rows], ["ACME"])
        self.assertAlmostEqual(rows[0]["gap"], 0.045)
        self.assertAlmostEqual(rows[0]["pre_rel"], 0.15)
        today = {"ACME": (20.9, 21.5, 20.8, 21.4, 900_000), "DULL": (10, 10.1, 9.9, 10, 50_000)}
        hist = {"ACME": (20, 20.5, 19.8, 20.3, 100_000), "DULL": (10, 10.1, 9.9, 10, 100_000)}
        with mock.patch.object(intraday.alpaca, "opening_bars", side_effect=lambda syms, d: today if d == "2026-09-29" else hist):
            watch = intraday.rerank([{"symbol": "DULL"}, {"symbol": "ACME"}], "2026-09-29", ["2026-09-28"])
        self.assertEqual([w["symbol"] for w in watch], ["ACME"])  # DULL: relative volume 0.5 < 1.0 dropped
        self.assertEqual(watch[0]["open_relvol"], 9.0)


class LiveBars(unittest.TestCase):
    def test_iex_feed_and_parsing(self):
        js = {"bars": {"ACME": [{"t": "2026-09-29T13:30:00Z", "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 10, "vw": 1.2}]}}
        with mock.patch.object(intraday.alpaca, "_get", return_value=js) as g, \
                mock.patch.dict(os.environ, {"ALPACA_KEY_ID": "k", "ALPACA_SECRET_KEY": "s"}):
            got = intraday.alpaca.live_bars(["ACME", "NONE"], "2026-09-29", "09:30", "09:45")
        self.assertEqual(got, {"ACME": [("09:30", 1, 2, 0.5, 1.5, 10, 1.2)], "NONE": []})
        self.assertEqual(g.call_args[0][1]["feed"], "iex")
        self.assertEqual(g.call_args[0][1]["start"], "2026-09-29T13:30:00Z")


class EvidenceRules(unittest.TestCase):
    def test_stop_floor_by_price_and_adv_cap(self):
        t = intraday.bracket("ACME", intraday._ticket("orb5", 100.0, None, "x", 3.0), 0.5, 100.0)  # 10% ATR = 0.05 < 0.3%
        self.assertEqual(t["R"], 0.30)
        self.assertTrue(t["r_floored"])
        t = intraday.bracket("ACME", intraday._ticket("flag", 20.9, 20.55, "x", 2.0), ATR, 10_000.0, adv=100_000)
        self.assertEqual(t["shares"], 850)  # 1% of 85% of the average day

    def test_afternoon_needs_an_up_day_above_vwap(self):
        up = minutes("09:30", [(20, 20.5, 19.8, 20.3, 100), (20.3, 20.45, 20.1, 20.4, 90), (20.4, 20.8, 20.35, 20.7, 300)])
        down = minutes("09:30", [(21, 21.5, 20.8, 21.3, 100), (21.3, 21.45, 21.1, 21.4, 90), (21.4, 21.8, 21.35, 21.7, 300)])
        watch = [{"symbol": "UP", "gap": 0.0, "pre_rel": 0.2, "atr": ATR, "prev_close": PREV, "open_relvol": 2.0},
                 {"symbol": "DOWN", "gap": 0.0, "pre_rel": 0.2, "atr": ATR, "prev_close": PREV, "open_relvol": 2.0}]
        with mock.patch.object(intraday.alpaca, "live_bars", return_value={"UP": up, "DOWN": down}):
            morning = intraday.scan(watch, "2026-10-19", "09:45", 100.0, None)
            self.assertEqual(sorted(t["symbol"] for t in morning), ["DOWN", "UP"])
            with mock.patch.object(intraday, "candles", side_effect=lambda b, now: (lambda c: (c.__setitem__(0, {**c[0], "o": 25.0}) if b is down else None) or c)(intraday.with_vwap(b, intraday.ticket.to5(b)))):
                late = intraday.scan(watch, "2026-10-19", "15:05", 100.0, None)
        self.assertEqual([t["symbol"] for t in late], ["UP"])  # DOWN opened at 25 and is below it
