"""The intraday candlestick screener (bot/intraday.py): setups over synthetic 5-minute candles, the bracket, outcomes, messages."""
import datetime as dt
import json
import os
os.environ.setdefault('NEWS_TAG', '0')  # no network in tests
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


INSIDE = [(20.15, 20.2, 19.9, 20.0, 100), (20.0, 20.4, 20.0, 20.3, 100), (20.3, 20.8, 20.2, 20.7, 150),
          (20.7, 21.0, 20.5, 20.9, 200), (20.85, 20.95, 20.6, 20.8, 80)]


def _setup(**kw):
    base = {"setup": "flag", "trigger_high": 20.9, "stop_anchor": 20.55, "note": "x", "score": 2.0,
            "candle_end": "10:00", "valid_mode": "window", "band": False}
    return {**base, **kw}


class Setups(unittest.TestCase):
    def test_candles_only_completed(self):
        bars = minutes("09:30", [(20, 20.5, 19.8, 20.3, 100), (20.3, 20.45, 20.1, 20.4, 90)])
        self.assertEqual([b["end"] for b in intraday.candles(bars, "09:37")], ["09:35"])
        self.assertEqual(len(intraday.candles(bars, "09:40")), 2)
        self.assertIn("vwap", intraday.candles(bars, "09:40")[0])


class Bracket(unittest.TestCase):
    def test_levels_and_size(self):
        t = intraday.bracket("ACME", _setup(), ATR, 100.0)
        self.assertEqual((t["entry_stop"], t["stop_loss"], t["target"]), (20.91, 20.54, 21.65))
        self.assertEqual(t["R"], 0.37)
        self.assertEqual(t["shares"], int(100 / 0.37))
        self.assertEqual(t["limit"], 20.96)  # 0.05 above the trigger, the worked ticket's offset

    def test_vwap_band_rejects_instead_of_moving_the_stop(self):
        tight = _setup(setup="vwap_hammer", trigger_high=20.10, stop_anchor=20.05, band=True, valid_mode="next")
        self.assertEqual(intraday.bracket("ACME", tight, ATR, 100.0)["reason"], "stop outside the volatility band")
        wide = _setup(setup="vwap_hammer", trigger_high=20.9, stop_anchor=18.0, band=True, valid_mode="next")
        self.assertFalse(intraday.bracket("ACME", wide, ATR, 100.0)["ok"])
        # the same wide stop is kept on a flag: the band is the VWAP rule, and the stop is not pulled in
        kept = intraday.bracket("ACME", _setup(trigger_high=20.9, stop_anchor=18.0), ATR, 100.0)
        self.assertTrue(kept["ok"])
        self.assertEqual(kept["stop_loss"], 17.99)

    def test_position_cap_and_a_risk_that_buys_nothing(self):
        t = intraday.bracket("ACME", _setup(), ATR, 100.0, max_position_value=210)
        self.assertEqual(t["shares"], 10)  # cash binds, and the commission is still under 0.6 of the risk unit
        tiny = intraday.bracket("ACME", _setup(), ATR, 100.0, max_position_value=100)
        self.assertEqual(tiny["reason"], "commissions eat the risk")  # 4 shares, $2 minimum round trip
        self.assertFalse(intraday.bracket("ACME", _setup(), ATR, 0.2)["ok"])


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


class Followup(unittest.TestCase):
    NY = intraday.NY

    def _state(self):
        return {"day": "2026-10-22", "watch": [], "sent": {}, "stats": None, "journal": [_t()]}

    def test_at_1620_the_request_ends_16_minutes_ago_and_is_not_cached(self):
        # 2026-10-19..22: the 16:20 followup asked for bars to 20:00 and the free plan answered HTTP 403
        seen = {}

        def bars(syms, day):
            seen["cut"] = intraday.alpaca.CUTOFF.get(day)
            return {"ACME": minutes("09:45", [(20.6, 20.7, 20.5, 20.65, 1), (20.65, 20.95, 20.6, 20.9, 1),
                                              (20.9, 21.7, 20.8, 21.6, 1)])}
        st = self._state()
        now = dt.datetime(2026, 10, 22, 16, 20, tzinfo=self.NY)
        with mock.patch.object(intraday.alpaca, "minute_bars", side_effect=bars):
            text = intraday.followup(st, now)
        self.assertEqual(seen["cut"], "16:04")
        self.assertNotIn("2026-10-22", intraday.alpaca.CUTOFF)  # removed after the request
        self.assertEqual(st["journal"][0]["outcome"]["result"], "target")
        self.assertEqual(msgrules.problems(text), [], text)

    def test_before_1616_nothing_is_filled(self):
        st = self._state()
        with mock.patch.object(intraday.alpaca, "minute_bars", side_effect=AssertionError("no request")):
            intraday.followup(st, dt.datetime(2026, 10, 22, 15, 0, tzinfo=self.NY))
        self.assertNotIn("outcome", st["journal"][0])

    def test_after_2016_the_whole_day_is_read(self):
        seen = {}
        st = self._state()
        with mock.patch.object(intraday.alpaca, "minute_bars",
                               side_effect=lambda s, d: seen.update(cut=intraday.alpaca.CUTOFF.get(d)) or {}):
            intraday.followup(st, dt.datetime(2026, 10, 22, 21, 0, tzinfo=self.NY))
        self.assertIsNone(seen["cut"])
        self.assertEqual(st["journal"][0]["outcome"]["result"], "not filled")

    def test_a_scan_before_0936_answers_instead_of_failing(self):
        # 2026-10-23 04:21 New York: /intraday now read the 04:00-09:20 pre-market bars before they existed -> 403
        sent = []
        now = dt.datetime(2026, 10, 23, 4, 21, tzinfo=self.NY)
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"INTRADAY_DIR": d, "TG_TOKEN": ""}), \
                mock.patch.object(intraday, "_now", return_value=now), \
                mock.patch.object(intraday.alpaca, "calendar", return_value={"2026-10-22": 1, "2026-10-23": 1}), \
                mock.patch.object(intraday, "watchlist", side_effect=AssertionError("no scan")), \
                mock.patch.object(intraday, "send", side_effect=lambda text, *a, **k: sent.append(text)):
            self.assertEqual(intraday.main(["--mode", "once"]), 0)
        self.assertEqual(len(sent), 1)
        self.assertIn("09:36", sent[0])
        self.assertEqual(msgrules.problems(sent[0]), [], sent[0])


class Messages(unittest.TestCase):
    def test_ticket_and_list_follow_the_rules(self):
        for text in (intraday.ticket_text(_t(), "2026-10-19", 10_000.0), intraday.ticket_text(_t(gap=-0.04, r_floored=True), "2026-10-19"),
                     intraday.list_text([_t()], "2026-10-19", "09:45", None), intraday.list_text([], "2026-10-19", "09:45", None),
                     intraday.watch_text([{"symbol": "ACME", "gap": 0.03, "pre_rel": 0.2, "atr": 1.0, "prev_close": 20.0,
                                           "open_relvol": 2.5}], "2026-10-19")):
            self.assertEqual(msgrules.problems(text), [], text)
        text = intraday.ticket_text(_t(), "2026-10-19", 10_000.0)
        for needle in ("20.91$", "20.54$", "21.65$", "270", "1.00%", "16:45", "22:55", "שתיים ליחידה"):
            self.assertIn(needle, text)
        self.assertNotIn("סטופ לנקודת הכניסה", text)  # the deck does not trail the stop to breakeven
        warn = {"symbol": "ACME", "setup": "shooting_star", "warn": True, "note": "כוכב נופל על התנגדות",
                "at": "10:15", "last": 20.05, "level": 21.2, "vwap": 20.4}
        caution = intraday.warn_text(warn, "2026-10-19")
        self.assertEqual(msgrules.problems(caution), [], caution)
        self.assertIn("אין פקודת", caution)
        self.assertNotIn("טריגר", caution)

    def test_stats_text(self):
        st = {"day": "2026-10-19", "journal": [_t(outcome={"result": "target", "fill": 20.91, "r": 2.0}),
                                                _t(symbol="BETA", outcome={"result": "not filled", "r": 0.0})]}
        text = intraday.stats_text(st)
        self.assertEqual(msgrules.problems(text), [], text)
        self.assertIn("2.00", text)


class Labels(unittest.TestCase):
    """Each ticket carries its setup's measured status: negative (T2; ZBA and T2 for ORB 5) or untested."""

    def test_label_by_setup(self):
        for s in ("vwap_hammer", "vwap_engulf", "flag"):
            self.assertEqual(intraday.label(s), "תווית: נבדק: תוחלת שלילית (T2) · כרטיס למחקר בלבד")
        self.assertEqual(intraday.label("orb5"), "תווית: נבדק: תוחלת שלילית (ZBA, T2) · כרטיס למחקר בלבד")
        for s in ("orb15", "red_to_green", "some_new_setup"):
            self.assertEqual(intraday.label(s), "תווית: מחוץ למסלול שנבדק · כרטיס למחקר בלבד")

    def test_every_ticket_shows_its_setup_label(self):
        for s in intraday.SETUP_NAMES:
            text = intraday.ticket_text(_t(setup=s), "2026-10-19", 10_000.0)
            self.assertIn(intraday.label(s), text)
            self.assertEqual(sum(1 for line in text.splitlines() if line.startswith("תווית:")), 1)
            self.assertEqual(msgrules.problems(text), [], text)


class State(unittest.TestCase):
    def test_run_scan_dedupes_and_journals(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"INTRADAY_DIR": d, "TG_TOKEN": ""}):
            state = intraday.fresh_state("2026-10-19")
            state["watch"] = [{"symbol": "ACME", "gap": 0.03, "pre_rel": 0.2, "atr": ATR, "prev_close": PREV, "open_relvol": 2.0}]
            bars = {"ACME": minutes("09:30", INSIDE)}
            with mock.patch.object(intraday.alpaca, "live_bars", return_value=bars), \
                    mock.patch.object(intraday, "sizing", return_value=(100.0, None, None)):
                got = intraday.run_scan(state, "2026-10-19", [], "09:55", dry=True)
                self.assertEqual([t["setup"] for t in got], ["inside"])
                self.assertEqual(intraday.run_scan(state, "2026-10-19", [], "10:00", dry=True), [])
            self.assertEqual(len(state["journal"]), 1)
            intraday.save(state)
            self.assertEqual(intraday.load(None)["sent"], {"ACME:inside": "09:55"})


class LongOnlyMerge(unittest.TestCase):
    def _b(self, o, c, v, vwap=20.0):
        return {"o": o, "h": max(o, c) + 0.05, "l": min(o, c) - 0.05, "c": c, "v": v, "vwap": vwap}

    def test_volume_confirms_a_green_candle_in_an_uptrend(self):
        b5 = [self._b(20.0, 20.2, 1000), self._b(20.2, 20.3, 1000), self._b(20.3, 20.6, 3000), self._b(20.6, 20.55, 500)]
        ok, rv = intraday.long_confirmed(b5, {"open_relvol": 2.0})
        self.assertTrue(ok)
        self.assertAlmostEqual(rv, 3.0)

    def test_rejections(self):
        base = [self._b(20.0, 20.2, 1000), self._b(20.2, 20.3, 1000), self._b(20.3, 20.3, 1000)]
        self.assertEqual(intraday.long_confirmed(base + [self._b(20.3, 20.4, 1200)], {})[1], "no volume confirmation")
        self.assertEqual(intraday.long_confirmed(base + [self._b(20.6, 20.4, 5000)], {})[1], "red candle")  # volume on a down candle
        self.assertEqual(intraday.long_confirmed(base + [self._b(20.3, 20.5, 5000, vwap=21.0)], {})[1], "no uptrend")
        self.assertEqual(intraday.long_confirmed(base + [self._b(20.3, 20.6, 5000)], {"open_relvol": 1.1})[1], "weak opening volume")

    def test_gap_down_is_not_in_play(self):
        stats = {"DROP": {"close": 20.0, "atr": 2.0, "adv": 2e6, "advd": 4e7}}
        with mock.patch.object(intraday.alpaca, "volume_since", return_value={"DROP": (900_000, 19.0)}), \
                mock.patch.object(intraday.alpaca, "live_bars", return_value={"DROP": [("09:30", 19.0, 19.1, 18.9, 19.0, 5000, 19.0)]}):
            self.assertEqual(intraday.in_play(stats, "2026-09-29"), [])

    def test_no_shooting_star_caution_by_default(self):
        with mock.patch.object(intraday.deck, "signals", return_value=[]), \
                mock.patch.object(intraday.deck, "shooting_star", return_value={"setup": "shooting_star", "warn": True}):
            self.assertEqual(intraday.setups([{}], 1.0, 20.0), [])

    def test_rerank_does_not_send_the_watchlist_again(self):
        state = intraday.fresh_state("2026-10-19")
        state["watch"] = [{"symbol": "ACME", "gap": 0.03, "pre_rel": 0.2, "atr": 1.0, "prev_close": 20.0}]
        ranked = [{**state["watch"][0], "open_relvol": 2.0}]
        sent = []
        with mock.patch.object(intraday, "rerank", return_value=ranked) as rk, \
                mock.patch.object(intraday, "scan", return_value=[]), \
                mock.patch.object(intraday, "sizing", return_value=(100.0, None, None)), \
                mock.patch.object(intraday, "send", side_effect=lambda *a, **k: sent.append(a[0])):
            self.assertEqual(intraday.run_scan(state, "2026-10-19", ["2026-10-16"], "09:55", dry=True), [])
        rk.assert_called_once()
        self.assertEqual(sent, [])
        self.assertEqual(state["watch"][0]["open_relvol"], 2.0)

    def test_ticket_carries_the_hebrew_long_confirmation(self):
        text = intraday.ticket_text(_t(candle_relvol=1.7), "2026-10-19", 10_000.0)
        self.assertIn("אישור לונג: מעל VWAP ומעל פתיחת היום, נר ירוק בנפח פי <code>1.7</code> מהנרות הקודמים", text)
        self.assertIn("BUY STP LMT", text)
        self.assertEqual(msgrules.problems(text), [], text)


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
    def test_adv_cap_still_limits_the_deck_size(self):
        t = intraday.bracket("ACME", _setup(), ATR, 10_000.0, adv=100_000)
        self.assertEqual(t["shares"], 850)  # 1% of 85% of the average day

    def test_afternoon_needs_an_up_day_above_vwap(self):
        up = minutes("09:30", INSIDE)
        down = minutes("09:30", INSIDE)
        watch = [{"symbol": "UP", "gap": 0.0, "pre_rel": 0.2, "atr": ATR, "prev_close": PREV, "open_relvol": 2.0},
                 {"symbol": "DOWN", "gap": 0.0, "pre_rel": 0.2, "atr": ATR, "prev_close": PREV, "open_relvol": 2.0}]
        with mock.patch.object(intraday.alpaca, "live_bars", return_value={"UP": up, "DOWN": down}):
            morning = intraday.scan(watch, "2026-10-19", "09:55", 100.0, None)
            self.assertEqual(sorted(t["symbol"] for t in morning), ["DOWN", "UP"])

            real = intraday.candles

            def candles(bars1, now):
                out = real(bars1, now)
                if bars1 is down and out:  # the session opened above the last close: not an up day
                    out[0] = {**out[0], "o": 25.0}
                return out

            with mock.patch.object(intraday, "candles", side_effect=candles):
                late = intraday.scan(watch, "2026-10-19", "15:05", 100.0, None)
        self.assertEqual([t["symbol"] for t in late], ["UP"])
