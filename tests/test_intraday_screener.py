"""The intraday screener (bot/intraday.py) around the deck's rules: stage 1, the scan, the journal, outcomes, the card,
the schedule. The rules themselves are tested in tests/test_deck.py."""
import datetime as dt
import json
import os
import tempfile
import unittest
from unittest import mock

from bot import deck, intraday, ticket
import msgrules

ATR, PREV = 1.00, 20.00
DAY = "2026-10-19"


def c(start, o, h, l, c_, v, vwap=None):
    end = ticket.hhmm(ticket.mins(start) + 5)
    return {"start": start, "end": end, "o": o, "h": h, "l": l, "c": c_, "v": v, "vw": c_, "vwap": vwap or c_ - 0.05}


def minutes(start_hhmm, candles5, traded=1):
    """5-minute candle specs (o, h, l, c, v) -> 1-minute bars: the first minute carries the candle, the next
    `traded` - 1 minutes trade at its close, the rest are flat without volume."""
    out, t = [], ticket.mins(start_hhmm)
    for o, h, l, c_, v in candles5:
        out.append((ticket.hhmm(t), o, h, l, c_, v, (o + c_) / 2))
        for k in range(1, 5):
            out.append((ticket.hhmm(t + k), c_, c_, c_, c_, 1 if k < traded else 0, c_))
        t += 5
    return out


def _t(**kw):
    base = {"symbol": "ACME", "setup": "flag", "note": "", "score": 0.6, "entry_stop": 21.91, "limit": 21.96,
            "stop_loss": 21.39, "target": 22.95, "R": 0.52, "r_floored": False, "shares": 48, "risk": 24.96,
            "time_exit": "15:55", "atr": ATR, "gap": 0.031, "pre_rel": 0.22, "open_relvol": 3.2, "at": "09:45",
            "last": 21.6, "vwap": 20.5, "ok": True, "stop_limit": 21.34, "r_capped": False, "level": (21.5, "yday_high"),
            "checks": {k: "" for k in deck.CHECKS}, "cost_r": 0.06, "wyckoff": ["spring"], "valid_until": "10:30",
            "premarket": False}
    return {**base, **kw}


class Outcomes(unittest.TestCase):
    def test_target(self):
        t = _t(entry_stop=20.91, limit=20.96, stop_loss=20.54, target=21.65, R=0.37)
        bars = minutes("09:45", [(20.6, 20.7, 20.5, 20.65, 1), (20.65, 20.95, 20.6, 20.9, 1), (20.9, 21.7, 20.8, 21.6, 1)])
        o = intraday.outcome(t, bars)
        self.assertEqual((o["result"], o["fill"]), ("target", 20.91))
        self.assertEqual(o["r"], 2.0)

    def test_stop_before_target_same_minute_is_stop(self):
        t = _t(entry_stop=20.91, limit=20.96, stop_loss=20.54, target=21.65, R=0.37)
        bars = minutes("09:45", [(20.65, 20.95, 20.6, 20.9, 1), (20.9, 21.7, 20.5, 21.6, 1)])
        self.assertEqual(intraday.outcome(t, bars)["result"], "stop")


class Messages(unittest.TestCase):
    def test_card_follows_the_message_rules(self):
        for t in (_t(), _t(premarket=True, at="08:35", vwap=None), _t(gap=-0.04, r_floored=True, wyckoff=[]),
                  _t(r_capped=True, setup="breakout", level=(50.0, "round"))):
            text = intraday.ticket_text(t, DAY)
            self.assertEqual(msgrules.problems(text), [], text)
        for text in (intraday.list_text([_t()], DAY, "09:45"), intraday.list_text([], DAY, "09:45"),
                     intraday.watch_text([{"symbol": "ACME", "gap": 0.03, "pre_rel": 0.2, "atr": 1.0, "prev_close": 20.0,
                                           "open_relvol": 2.5}], DAY),
                     intraday.exit_text({"journal": [_t()]}, DAY, "15:55")):
            self.assertEqual(msgrules.problems(text), [], text)

    def test_card_shows_what_the_deck_asks(self):
        text = intraday.ticket_text(_t(), DAY)
        for needle in ("Bull flag (פרק 5 במצגת)", "21.91$", "21.96$", "21.39$", "22.95$", "0.52$", "48", "24.96$",
                       "0.06", "✓ הקשר", "✓ כלל קבוע", "Spring", "10:30", "15:55", "22:55", "שיא אתמול",
                       "SELL STP סטופ לוס"):
            self.assertIn(needle, text)
        self.assertEqual(sum(text.count(f"✓ {n}") for n in intraday.CHECK_NAMES.values()), 8)
        self.assertNotIn("Fill outside RTH", text)

    def test_premarket_card_has_the_order_settings(self):
        text = intraday.ticket_text(_t(premarket=True, at="08:35"), DAY)
        for needle in ("Fill outside RTH", "SELL STP LMT", "21.34$", "ההגנה לא מובטחת", "15:35"):  # 08:35 NY = 15:35 IL
            self.assertIn(needle, text)

    def test_stats_text(self):
        st = {"day": DAY, "journal": [_t(outcome={"result": "target", "fill": 20.91, "r": 2.0}),
                                      _t(symbol="BETA", outcome={"result": "not filled", "r": 0.0})]}
        text = intraday.stats_text(st)
        self.assertEqual(msgrules.problems(text), [], text)
        self.assertIn("2.00", text)


class Labels(unittest.TestCase):
    """VWAP pullback and Bull flag tickets carry T2's negative result; every other setup is outside the tested track."""

    def test_label_by_setup(self):
        for s in ("vwap_hammer", "vwap_engulf", "flag"):
            self.assertEqual(intraday.label(s), "תווית: נבדק: תוחלת שלילית (T2) · כרטיס למחקר בלבד")
        for s in ("hammer", "engulf", "inside", "nr7", "breakout", "retest"):
            self.assertEqual(intraday.label(s), "תווית: מחוץ למסלול שנבדק · כרטיס למחקר בלבד")

    def test_every_setup_has_a_name_and_its_label_on_the_card(self):
        for s in deck.SETUPS:
            text = intraday.ticket_text(_t(setup=s), DAY)
            self.assertIn(intraday.label(s), text)
            self.assertIn(f"(פרק {deck.CHAPTER[s]} במצגת)", text)
            self.assertEqual(sum(1 for line in text.splitlines() if line.startswith("תווית:")), 1)
            self.assertEqual(msgrules.problems(text), [], text)


class State(unittest.TestCase):
    def test_run_scan_dedupes_and_keeps_the_journal_schema(self):
        t = _t(score=0.5)
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"INTRADAY_DIR": d, "TG_TOKEN": ""}):
            state = intraday.fresh_state(DAY)
            state["watch"] = [{"symbol": "ACME", "gap": 0.03, "pre_rel": 0.2, "atr": ATR, "prev_close": PREV, "open_relvol": 2.0}]
            with mock.patch.object(intraday, "scan", return_value=[dict(t)]), \
                    mock.patch.object(intraday, "context", return_value={"ACME": {}}):
                got = intraday.run_scan(state, DAY, [], "09:45", dry=True)
                self.assertEqual([x["setup"] for x in got], ["flag"])
                self.assertEqual(intraday.run_scan(state, DAY, [], "09:50", dry=True), [])
            self.assertEqual(len(state["journal"]), 1)
            self.assertEqual(tuple(state["journal"][0]), intraday.JOURNAL_KEYS)  # the journal's schema is unchanged
            intraday.save(state)
            back = intraday.load(None)
            self.assertEqual(back["sent"], {"ACME:flag": "09:45"})
            merged = intraday.sent_tickets(back)[0]
            self.assertEqual((merged["cost_r"], merged["stop_limit"]), (0.06, 21.34))  # the card's fields come back

    def test_watchlist_refreshes_until_0935_then_reranks_at_0955(self):
        state = intraday.fresh_state(DAY)
        state["watch"] = [{"symbol": "ACME", "gap": 0.03, "pre_rel": 0.2, "atr": ATR, "prev_close": PREV}]
        run = {"stats": {"ACME": {}}, "extra": {"ACME": {}}}
        with mock.patch.object(intraday, "watchlist", return_value=(state["watch"], [])) as wl, \
                mock.patch.object(intraday, "rerank", return_value=[dict(state["watch"][0], open_relvol=2.0)]) as rr, \
                mock.patch.object(intraday, "scan", return_value=[]), \
                mock.patch.object(intraday, "context", return_value={"ACME": {}}):
            for now in ("08:00", "08:05", "09:35", "09:40", "09:55", "10:00"):
                intraday.run_scan(state, DAY, [], now, dry=True, run=run)
        self.assertEqual([call.args[2] for call in wl.call_args_list], ["08:00", "08:05", "09:35"])
        self.assertEqual(rr.call_count, 1)

    def test_scan_fetches_a_quote_only_for_a_near_ticket(self):
        watch = [{"symbol": "ACME", "gap": 0.0, "pre_rel": 0.2, "atr": ATR, "prev_close": 49.9, "prev_high": 52.5,
                  "prev_low": 49.8, "open_relvol": 2.0}]
        bars = {"ACME": minutes("09:30", [(51.8, 51.9, 51.4, 51.5, 1000), (51.5, 51.6, 51.1, 51.2, 1000),
                                          (51.2, 51.3, 50.8, 50.9, 1000), (50.9, 50.95, 50.4, 50.5, 1000),
                                          (50.02, 50.19, 49.61, 50.15, 1000)])}
        with mock.patch.object(intraday.alpaca, "iex_quote", return_value=(50.14, 50.16)) as q:
            got = intraday.scan(watch, DAY, "09:55", {DAY: ("09:30", "16:00")}, {"ACME": {}}, bars=bars)
        self.assertEqual(q.call_count, 1)
        self.assertEqual([(x["setup"], x["entry_stop"], x["stop_loss"], x["shares"]) for x in got],
                         [("hammer", 50.2, 49.6, 41)])
        with mock.patch.object(intraday.alpaca, "iex_quote", return_value=None):
            blocked = []
            self.assertEqual(intraday.scan(watch, DAY, "09:55", {DAY: ("09:30", "16:00")}, {"ACME": {}}, bars=bars,
                                           blocked=blocked), [])
        self.assertIn(("ACME", "hammer", "cost", "אין ציטוט"), blocked)


class Schedule(unittest.TestCase):
    def test_time_exit_follows_the_close(self):
        self.assertEqual(intraday.time_exit(DAY, {DAY: ("09:30", "16:00")}), "15:55")
        self.assertEqual(intraday.time_exit("2026-11-27", {"2026-11-27": ("09:30", "13:00")}), "12:55")

    def test_exit_run_sends_only_at_its_close(self):
        cal = {DAY: ("09:30", "16:00")}
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"INTRADAY_DIR": d}):
            st = intraday.fresh_state(DAY)
            st["journal"] = [_t()]
            intraday.save(st)
            for now, sends in (("12:51", 0), ("15:51", 1)):
                with mock.patch.object(intraday, "_now", return_value=dt.datetime.fromisoformat(f"{DAY}T{now}").replace(tzinfo=intraday.NY)), \
                        mock.patch.object(intraday.alpaca, "calendar", return_value=cal), \
                        mock.patch.object(intraday, "_sleep_until"), mock.patch.object(intraday, "send") as s:
                    intraday.main(["--mode", "exit", "--dry"])
                self.assertEqual(s.call_count, sends, now)

    def test_window_is_the_users(self):
        self.assertEqual(intraday.WINDOWS, {"morning": ("08:00", "10:30")})


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
        self.assertEqual((got["ACME"]["high"], got["ACME"]["low"]), (21.0, 19.0))  # yesterday's levels

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

    def test_premarket_build_uses_sip_older_than_15_minutes(self):
        stats = {"ACME": {"close": 20.0, "atr": 2.0, "adv": 2e6, "advd": 4e7}}
        with mock.patch.object(intraday.alpaca, "volume_since", return_value={"ACME": (50_000, 20.6)}) as v, \
                mock.patch.object(intraday.alpaca, "live_bars") as lb:
            rows = intraday.in_play(stats, "2026-09-29", "08:20")
        self.assertEqual(v.call_args[0][2:], ("04:00", "08:04"))  # 16 minutes back: under the free plan's limit
        lb.assert_not_called()  # no open print before the open
        self.assertAlmostEqual(rows[0]["gap"], 0.03)  # from the last pre-market price


class LiveData(unittest.TestCase):
    def test_iex_feed_and_parsing(self):
        js = {"bars": {"ACME": [{"t": "2026-09-29T13:30:00Z", "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 10, "vw": 1.2}]}}
        with mock.patch.object(intraday.alpaca, "_get", return_value=js) as g, \
                mock.patch.dict(os.environ, {"ALPACA_KEY_ID": "k", "ALPACA_SECRET_KEY": "s"}):
            got = intraday.alpaca.live_bars(["ACME", "NONE"], "2026-09-29", "09:30", "09:45")
        self.assertEqual(got, {"ACME": [("09:30", 1, 2, 0.5, 1.5, 10, 1.2)], "NONE": []})
        self.assertEqual(g.call_args[0][1]["feed"], "iex")
        self.assertEqual(g.call_args[0][1]["start"], "2026-09-29T13:30:00Z")

    def test_iex_quote_latest_and_replay(self):
        with mock.patch.object(intraday.alpaca, "_get", return_value={"quotes": {"ACME": {"bp": 20.1, "ap": 20.12}}}) as g:
            self.assertEqual(intraday.alpaca.iex_quote("ACME"), (20.1, 20.12))
        self.assertTrue(g.call_args[0][0].endswith("/quotes/latest"))
        self.assertEqual(g.call_args[0][1]["feed"], "iex")
        with mock.patch.object(intraday.alpaca, "_get", return_value={"quotes": [{"bp": 20.0, "ap": 20.05}]}) as g:
            self.assertEqual(intraday.alpaca.iex_quote("ACME", "2026-09-29T12:35:00Z"), (20.0, 20.05))
        self.assertEqual((g.call_args[0][1]["start"], g.call_args[0][1]["feed"]), ("2026-09-29T08:00:00Z", "iex"))
        with mock.patch.object(intraday.alpaca, "_get", return_value={"quotes": []}):
            self.assertIsNone(intraday.alpaca.iex_quote("ACME", "2026-09-29T12:35:00Z"))
        with mock.patch.object(intraday.alpaca, "iex_quote", return_value=(20.0, 20.0)):
            self.assertEqual(intraday.spread_of("ACME", DAY, "09:45"), 0.01)  # a locked quote counts a tick
        with mock.patch.object(intraday.alpaca, "iex_quote", return_value=(0, 20.0)):
            self.assertIsNone(intraday.spread_of("ACME", DAY, "09:45"))


if __name__ == "__main__":
    unittest.main()
