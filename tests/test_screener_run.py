"""Screener integration (offline): presets (validation, save, delete, defaults protected), messages under the bot's
message rules, /screen and /screens routing (local vs dispatched), the screener's own schedule, the listener wiring,
and the budget: a scheduled run over a 10,000-symbol universe stays under 150 Alpaca and 2000 SEC calls."""
import datetime as dt
import math
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

import msgrules
from bot import common, listen
from bot.screener import commands, evaluate, format as fmt, parser, presets, run, schedule, stores
from bot.screener.stores import bars as BARS
from test_screener_evaluate import DAYS, env, series, uni

NY = ZoneInfo("America/New_York")
UTC = dt.timezone.utc


class Presets(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        p = mock.patch.dict(os.environ, {"SCREENER_DATA": self.d.name})
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self.d.cleanup)

    def test_defaults(self):
        names = [p["name"] for p in presets.defaults()]
        self.assertEqual(names, ["unusual_volume_liquid", "squeeze_like", "oversold_midcaps"])
        self.assertEqual([p["enabled"] for p in presets.defaults()], [True, False, False])
        for p in presets.defaults():
            q = parser.parse(presets.query_text(p), strict=True)
            self.assertEqual(q.ignored, [], p["name"])

    def test_save_replace_delete(self):
        p = presets.parse_save("mylist cap_small,sh_price_o5 o=-change sessions=rth,post every=2h")
        self.assertEqual((p["name"], p["filters"], p["sort"], p["sessions"], p["every"], p["enabled"]),
                         ("mylist", "cap_small,sh_price_o5", "-change", ["rth", "post"], 120, True))
        self.assertEqual(presets.save(p), "added")
        self.assertEqual(presets.save(p), "replaced")
        self.assertEqual(presets.get("MYLIST")["every"], 120)
        self.assertTrue(presets.delete("mylist"))
        self.assertFalse(presets.delete("mylist"))

    def test_bad_input(self):
        for text, why in (("", "usage"), ("Bad*Name cap_small", "name"), ("x cap_small sessions=lunch", "sessions"),
                          ("x cap_small every=5m", "every"), ("x zz_unknown", "empty"), ("x cap_small;ls", "codes")):
            with self.assertRaises(presets.PresetError) as e:
                presets.parse_save(text)
            self.assertEqual(str(e.exception), why, text)
        with self.assertRaises(presets.PresetError):
            presets.save({**presets.parse_save("unusual_volume_liquid cap_small"), "name": "unusual_volume_liquid"})
        with self.assertRaises(presets.PresetError):
            presets.delete("squeeze_like")

    def test_due_and_claim(self):
        p = presets.get("unusual_volume_liquid")
        now = dt.datetime(2026, 10, 21, 15, 30, tzinfo=UTC)
        state = {"presets": {}}
        self.assertTrue(presets.due(p, "rth", now, state))
        state["presets"]["unusual_volume_liquid"] = {"at": "2026-10-21T15:00:00Z"}
        self.assertFalse(presets.due(p, "rth", now, state))  # 30 minutes ago, every 60
        state["presets"]["unusual_volume_liquid"] = {"at": "2026-10-21T14:31:00Z"}
        self.assertTrue(presets.due(p, "rth", now, state))  # 59 minutes: within the 5-minute slack
        self.assertFalse(presets.due(presets.get("squeeze_like"), "rth", now, {}))  # disabled
        st = {}
        self.assertTrue(presets.claim(st, "reg1", "2026-10-21"))
        self.assertFalse(presets.claim(st, "reg1", "2026-10-21"))

    def test_due_anchored_to_slot_time(self):
        # 2026-10-22: reg4 (13:30 NY) stamped its run at 13:38 after building stores; reg5 (14:30 NY) started on
        # time and was skipped as "52 minutes ago". Anchored to the slot's scheduled time it must run.
        from zoneinfo import ZoneInfo
        ny = ZoneInfo("America/New_York")
        p = presets.get("unusual_volume_liquid")
        state = {"presets": {"unusual_volume_liquid": {"at": "2026-10-22T17:38:14Z"}}}
        reg5 = dt.datetime(2026, 10, 22, 14, 30, tzinfo=ny)
        self.assertTrue(presets.due(p, "rth", reg5, state, reg5))
        late_reg4 = dt.datetime(2026, 10, 22, 13, 30, tzinfo=ny)
        self.assertFalse(presets.due(p, "rth", reg5, state, late_reg4))  # already ran in that slot
        daily = {**p, "every": 1440}
        self.assertFalse(presets.due(daily, "rth", reg5, state, reg5))  # ran today
        nxt = dt.datetime(2026, 10, 23, 14, 30, tzinfo=ny)
        self.assertTrue(presets.due(daily, "rth", nxt, state, nxt))  # next day's slot


def _result():
    b = {"AAPL": series([100.0] * 299 + [102.0]), "MSFT": series([50.0] * 299 + [49.0])}
    e = env(b, uni("AAPL", "MSFT"), fund={"facts": {}, "shares": {"100": [["2026-06-30", "2026-06-30", 15e9]]}})
    q = parser.parse("sh_price_o10,sh_float_u50000,an_recom_buy o=-change")
    return q, evaluate.run(q, e), e


class PartialSlot(unittest.TestCase):
    """A slot run whose daily bars stopped at the budget does not claim its preset's first list of the day."""
    setUp = Presets.setUp

    def run_slot(self, slot, partial, now):
        b = {"A": series([10.0] * 300, vols=[1e6] * 300)}
        e = env(b, uni("A"), session="RTH", now=now, partial=partial)
        e.notes = ["stale: bars (catching up)"] if partial else []
        ck = {"session": "RTH", "day": now.date().isoformat()}
        sent = []
        with mock.patch.object(run, "clock_now", return_value=(now, ck, DAYS, DAYS, None)), \
                mock.patch.object(run, "build_env", return_value=e), \
                mock.patch.object(run, "send", side_effect=lambda text, dry: sent.append(text)):
            run.slot_mode(slot)
        return sent

    def test_partial_run_defers_the_first_list(self):
        now = dt.datetime(2026, 10, 21, 10, 30, tzinfo=NY)
        self.assertEqual(self.run_slot("reg1", True, now), [])
        st = presets.load_state()
        self.assertNotIn("unusual_volume_liquid", st.get("presets", {}))  # not recorded: still due
        self.assertIn("reg1", st["slots"]["2026-10-21"])
        sent = self.run_slot("reg2", False, now + dt.timedelta(hours=1))
        self.assertEqual(len(sent), 1)  # the next slot sends the first list in full
        self.assertEqual(presets.load_state()["presets"]["unusual_volume_liquid"]["day"], "2026-10-21")


class Messages(unittest.TestCase):
    def test_screen_text_follows_the_message_rules(self):
        q, res, e = _result()
        text = fmt.screen_text("סריקה", q, res, e.now)
        msgrules.check(self, text)
        self.assertIn("AAPL", text)
        self.assertIn("≈", text)  # float is approximated (outstanding proxy)
        self.assertIn("an_recom_buy", text)  # the unavailable code is listed, not dropped
        self.assertIn("ניו־יורק", text)

    def test_change_and_presets_text(self):
        q, res, e = _result()
        msgrules.check(self, fmt.change_text("סריקה", res, ["MSFT", "TSLA"], e.now))
        state = {"presets": {"unusual_volume_liquid": {"at": "2026-10-21T15:00:00Z", "tickers": ["A", "B"]}}}
        text = fmt.presets_text(presets.defaults(), state)
        msgrules.check(self, text)
        self.assertIn("unusual_volume_liquid", text)

    def test_usage_text(self):
        msgrules.check(self, commands.USAGE)

    def test_result_hash_changes_with_the_set(self):
        q, res, e = _result()
        h1 = fmt.result_hash(res, "change")
        self.assertEqual(h1, fmt.result_hash(res, "change"))
        res.rows = []
        self.assertNotEqual(h1, fmt.result_hash(res, "change"))


class Routing(unittest.TestCase):
    def setUp(self):
        self.sent = []
        for p in (mock.patch.object(common, "send", side_effect=lambda t, **k: self.sent.append(t)),
                  mock.patch("builtins.print")):
            p.start()
            self.addCleanup(p.stop)

    def test_dispatch_on_actions(self):
        with mock.patch.dict(os.environ, {"GITHUB_REPOSITORY": "o/r", "GITHUB_TOKEN": "t"}), \
                mock.patch.object(common, "dispatch", return_value="") as d:
            self.assertEqual(listen.handle("/screen cap_small,sh_price_o5 o=-change"), 0)
            self.assertEqual(d.call_args.args, ("screener.yml", {"mode": "query", "query": "cap_small,sh_price_o5 o=-change"}))
            listen.handle("/screen unusual_volume_liquid")
            self.assertEqual(d.call_args.args[1], {"mode": "preset", "name": "unusual_volume_liquid"})
            listen.handle("/screen run squeeze_like")
            self.assertEqual(d.call_args.args[1], {"mode": "preset", "name": "squeeze_like"})
            listen.handle("/screen save mylist cap_small sessions=rth")
            self.assertEqual(d.call_args.args[1], {"mode": "save", "query": "mylist cap_small sessions=rth"})
            listen.handle("/screen del mylist")
            self.assertEqual(d.call_args.args[1], {"mode": "delete", "name": "mylist"})
            n = d.call_count
            listen.handle("/screen cap_small;rm")  # rejected before any dispatch
            listen.handle("/screen save Bad*Name cap_small")
            listen.handle("/screen zz_nothing")
            self.assertEqual(d.call_count, n)
        self.assertTrue(all("⏳" in t for t in self.sent[:5]))
        self.assertTrue(all("⚠️" in t for t in self.sent[5:]))

    def test_local_runs_in_process(self):
        with mock.patch.object(run, "query_mode", return_value=0) as qm:
            self.assertEqual(listen.handle("/screen cap_small"), 0)
        qm.assert_called_once_with(text="cap_small")

    def test_dispatch_failure_is_answered(self):
        with mock.patch.dict(os.environ, {"GITHUB_REPOSITORY": "o/r", "GITHUB_TOKEN": "t"}), \
                mock.patch.object(common, "dispatch", return_value="HTTP 403"):
            self.assertEqual(listen.handle("/screen cap_small"), 1)
        self.assertIn("HTTP 403", self.sent[-1])

    def test_usage_and_screens(self):
        listen.handle("/screen")
        self.assertEqual(self.sent[-1], commands.USAGE)
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"SCREENER_DATA": d}):
            listen.handle("/screens")
        self.assertIn("unusual_volume_liquid", self.sent[-1])

    def test_plain_ticker_rule_untouched(self):
        with mock.patch.object(listen, "reports", return_value=0) as r:
            listen.handle("what about SCREEN today")
        r.assert_called_once()


class Listener(unittest.TestCase):
    def test_help_and_menu_list_the_screener(self):
        self.assertIn("/screen", listen.HELP)
        self.assertIn("/screens", listen.HELP)
        self.assertEqual([c for c, _ in listen.MENU][-3:], ["screen", "screens", "help"])
        self.assertEqual(listen.MENU[:-3], listen.COMMANDS[:-1])
        msgrules.check(self, listen.HELP)

    def test_serve_ticks_the_screener_schedule(self):
        with mock.patch.object(listen.clock, "tick", return_value=[]), \
                mock.patch.object(listen.screener_clock, "tick", side_effect=[RuntimeError("x"), []]) as t, \
                mock.patch.object(listen, "updates", return_value=[]), mock.patch.object(common, "trace"), \
                mock.patch.object(listen.time, "monotonic", side_effect=[0, 0, 60, 60, 200, 200, 200]), \
                mock.patch.object(common, "log"), mock.patch.object(common, "summary"):
            self.assertEqual(listen.serve(seconds=100)[0], "deadline")
        self.assertGreaterEqual(t.call_count, 1)  # a schedule failure never stops the replies


class RefreshDeadline(unittest.TestCase):
    """A maintenance run that runs out of time keeps what it saved: the workflow is told at the first save
    (GITHUB_OUTPUT changed=true - a step killed by its timeout never reaches the final output), and a refresh past
    its deadline stops between steps with the stores written so far on disk (main's run 39806511832 lost them all)."""

    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.out = Path(self.d.name) / "github_output"
        self.out.write_text("", encoding="utf-8")
        p = mock.patch.dict(os.environ, {"SCREENER_CACHE": str(Path(self.d.name) / "cache"),
                                         "GITHUB_OUTPUT": str(self.out), "SCREENER_MODE": "refresh"})
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self.d.cleanup)
        saved = mock.patch.object(stores, "SAVED", [False])
        saved.start()
        self.addCleanup(saved.stop)

    def test_the_output_line_is_written_at_the_first_save(self):
        self.assertEqual(self.out.read_text(encoding="utf-8"), "")
        stores.save("x", {"a": 1}, "test")
        self.assertEqual(self.out.read_text(encoding="utf-8"), "changed=true\n")  # before the run ends
        stores.save("y", {"b": 2}, "test")
        self.assertEqual(self.out.read_text(encoding="utf-8"), "changed=true\n")  # once

    def test_a_refresh_past_its_deadline_stops_with_the_stores_saved(self):
        now = dt.datetime(2026, 10, 22, 21, 0, tzinfo=NY)
        ck = {"session": "CLOSED", "day": "2026-10-22"}
        uni = {"AAA": {"cik": 1, "etf": False}, "BBB": {"cik": 2, "etf": False}}
        called = []
        with mock.patch.object(run, "REFRESH_MINUTES", -1), \
                mock.patch.object(run, "clock_now", return_value=(now, ck, DAYS, DAYS, None)), \
                mock.patch.object(run.REF, "universe", return_value=(uni, {})), \
                mock.patch.object(run.SPLITS, "update", return_value=({}, 0)), \
                mock.patch.object(BARS, "_get_bars", side_effect=lambda *a: called.append(a) or {}), \
                mock.patch.object(run.SI, "update", side_effect=lambda *a: called.append("shortint")), \
                mock.patch.object(run.FUND, "update", side_effect=lambda *a, **k: called.append("fundamentals")), \
                mock.patch.object(run.OWN, "update", side_effect=lambda *a, **k: called.append("ownership")):
            self.assertEqual(run.refresh_mode(), 0)
        self.assertEqual(called, [])  # the deadline stopped the bars loop before a request, and every later step
        self.assertTrue(stores.path("bars").exists())  # what it had is on disk ...
        self.assertIn("changed=true", self.out.read_text(encoding="utf-8"))  # ... and the cache-save step runs


    def test_a_source_down_ends_its_step_not_the_refresh(self):
        # 2026-10-24: SEC answered 503 through every retry inside holders; the run exited 1 and long never ran
        import urllib.error
        now = dt.datetime(2026, 10, 23, 20, 40, tzinfo=NY)
        ck = {"session": "CLOSED", "day": "2026-10-23"}
        uni = {"AAA": {"cik": 1, "etf": False}}
        down = urllib.error.HTTPError("https://www.sec.gov/x", 503, "Service Unavailable", {}, None)
        called = []
        with mock.patch.object(run, "clock_now", return_value=(now, ck, DAYS, DAYS, None)), \
                mock.patch.object(run.REF, "universe", return_value=(uni, {})), \
                mock.patch.object(run.SPLITS, "update", return_value=({}, 0)), \
                mock.patch.object(BARS, "update", return_value={}), \
                mock.patch.object(BARS, "update_profile", return_value={}), \
                mock.patch.object(run.SI, "update"), mock.patch.object(run.FUND, "update"), \
                mock.patch.object(run.PF, "update", return_value=({}, {})), \
                mock.patch.object(run.REF, "eightk_ciks", return_value=set()), \
                mock.patch.object(run.REF, "reference", return_value=(None, None, 0)), \
                mock.patch.object(run.OWN, "update", return_value=(None, None, {})), \
                mock.patch.object(run.HOLD, "update", side_effect=down), \
                mock.patch.object(BARS, "update_long", side_effect=lambda *a, **k: called.append("long") or {}), \
                mock.patch.object(run.common, "trace"):
            self.assertEqual(run.refresh_mode(), 0)
        self.assertEqual(called, ["long"])  # the store after the failed one still ran


class Schedule(unittest.TestCase):
    def test_a_weekday(self):
        done, got, t = set(), [], dt.datetime(2026, 10, 21, 8, 0, tzinfo=UTC)  # 04:00 New York
        while t < dt.datetime(2026, 10, 22, 6, 0, tzinfo=UTC):
            for key, inputs in schedule.due(t, done):
                done.add(key)
                got.append((t.astimezone(NY).strftime("%H:%M"), key.split(":")[1], inputs["mode"]))
            t += dt.timedelta(minutes=1)
        self.assertEqual(got, [("07:30", "pre1", "slot"), ("09:15", "pre2", "slot")]
                         + [(f"{h}:30", f"reg{h - 9}", "slot") for h in range(10, 16)]
                         + [("16:30", "post1", "slot"), ("19:30", "post2", "slot"), ("20:30", "maint", "refresh")])

    def test_weekend_and_no_token(self):
        self.assertEqual(schedule.due(dt.datetime(2026, 10, 24, 15, 0, tzinfo=UTC), set()), [])
        with mock.patch.dict(os.environ, {"GITHUB_REPOSITORY": "", "GITHUB_TOKEN": ""}):
            self.assertEqual(schedule.tick(set(), dt.datetime(2026, 10, 21, 14, 31, tzinfo=UTC)), [])

    def test_tick_dispatches(self):
        with mock.patch.dict(os.environ, {"GITHUB_REPOSITORY": "o/r", "GITHUB_TOKEN": "t"}), \
                mock.patch.object(common, "dispatch", return_value="") as d, mock.patch.object(common, "log"):
            done = set()
            got = schedule.tick(done, dt.datetime(2026, 10, 21, 14, 31, tzinfo=UTC))  # 10:31 New York
        self.assertEqual(got, ["screener:reg1:2026-10-21"])
        self.assertEqual(d.call_args.args, ("screener.yml", {"mode": "slot", "slot": "reg1"}))

    def test_workflow_is_dispatch_only(self):
        wf = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "screener.yml").read_text("utf-8")
        self.assertNotIn("\n  schedule:", wf)  # no GitHub cron trigger
        self.assertIn("workflow_dispatch:", wf)
        self.assertIn("group: screener", wf)
        self.assertIn("timeout-minutes: 30", wf)
        for secret in ("TG_TOKEN", "TG_CHAT_ID", "SEC_UA", "ALPACA_KEY_ID", "ALPACA_SECRET_KEY"):
            self.assertIn(f"secrets.{secret}", wf)
        self.assertNotIn("${{ inputs.query }}\"", wf)  # inputs reach the script through env, never the shell line


class Budget(unittest.TestCase):
    """A scheduled RTH run over 10,000 symbols: the daily bars catch up one session, then stage 2 sized by the run's
    budget (D-040) - counted with Alpaca's paging (daily: 10,000 points a page; intraday: evaluate.PAGE_POINTS a page,
    OBSERVED 2026-10-22)."""

    def fake_get_bars(self, symbols, start, end, timeframe):
        per = {"1Day": 1, "30Min": 14, "1Hour": 16}[timeframe]
        page = 10_000 if timeframe == "1Day" else evaluate.PAGE_POINTS
        stores.count("alpaca", max(1, math.ceil(len(symbols) * per / page)))
        if timeframe == "1Day":
            return {s: [{"t": "2026-10-20T04:00:00Z", "o": 10, "h": 10, "l": 10, "c": 10, "v": 1e6, "n": 1}]
                    for s in symbols}
        return {s: [{"t": f"2026-10-21T{8 + k // 2:02}:{'00' if k % 2 == 0 else '30'}:00Z", "o": 10, "h": 10,
                     "l": 10, "c": 10, "v": 1e5, "n": 1} for k in range(per)] for s in symbols}

    def scheduled(self, liquid):
        """10,000 symbols, the first `liquid` with a 1M average volume (the rest 100K) -> (result, stage-2 calls)."""
        syms = [f"S{i:05}" for i in range(10_000)]
        days = DAYS[:-1]  # the store is one session behind
        b = {s: series([10.0] * 70, vols=[1e6 if i < liquid else 1e5] * 70, days=days) for i, s in enumerate(syms)}
        stores.COUNTS.update({k: 0 for k in stores.COUNTS})
        with mock.patch.object(BARS, "_get_bars", side_effect=self.fake_get_bars), \
                mock.patch.object(BARS, "end_param", side_effect=lambda d, now=None: d), \
                mock.patch.object(run.alpaca, "REQUESTS", [0]):
            stats = BARS.update(b, syms, DAYS, max_calls=run.SLOT_BARS_CALLS)
            self.assertEqual(stats["fetched"], 10_000)
            daily = stores.COUNTS["alpaca"]
            now = dt.datetime(2026, 10, 21, 11, 0, tzinfo=NY)
            e = env(b, uni(*syms), session="RTH", now=now, budget_left=run.alpaca_left,
                    fetch_today=lambda s: BARS.today(s, "2026-10-21", "10:44"))
            q = parser.parse(presets.query_text(presets.get("unusual_volume_liquid")))
            res = evaluate.run(q, e)
        return res, daily, stores.COUNTS["alpaca"] - daily

    def test_full_universe_run_stays_under_budget(self):
        res, daily, stage2 = self.scheduled(10_000)
        self.assertEqual(daily, math.ceil(10_000 / 800))  # 13 daily chunks of <= 800 symbols, 1 page each
        self.assertLess(daily + stage2, run.RUN_ALPACA_MAX, stores.COUNTS)
        self.assertLessEqual(stage2, run.RUN_ALPACA_MAX - run.ALPACA_RESERVE - daily)
        self.assertLessEqual(stage2, evaluate.stage2_calls(res.stage2, 14))  # the estimate bounds the real paging
        self.assertEqual(res.stage2 + res.truncated, 10_000)
        self.assertEqual(res.stage2, evaluate.stage2_fit(10_000, 14, run.RUN_ALPACA_MAX - run.ALPACA_RESERVE - daily))
        self.assertLess(stores.COUNTS["sec"], 2000)

    def test_long_store_stops_and_resumes(self):
        calls = []

        def fake(symbols, start, end, timeframe):
            calls.append(len(symbols))
            return {}
        long, syms, n = {}, [f"S{i:04}" for i in range(2000)], [0]
        with mock.patch.object(BARS, "_get_bars", side_effect=fake):
            stats = BARS.update_long(long, syms, DAYS, DAYS[-1], stop=lambda: len(calls) >= 1)
            self.assertTrue(stats["stopped"])
            self.assertEqual(len(long["m"]), 800)  # one chunk done
            BARS.update_long(long, syms, DAYS, DAYS[-1])
        self.assertEqual(calls[1:3], [800, 400])  # the rest only

    def test_unusual_volume_liquid_is_not_truncated(self):
        # the 2026-10-22 stage 1 of unusual_volume_liquid had 695 survivors (run 39806269003)
        res, daily, stage2 = self.scheduled(700)
        self.assertEqual((res.stage1, res.stage2, res.truncated), (700, 700, 0))
        self.assertEqual(stage2, math.ceil(700 * 14 / evaluate.PAGE_POINTS))  # 17 pages in one request
        self.assertEqual(evaluate.stage2_calls(700, 14), 18)  # the planner's bound: + the request itself
        self.assertLess(daily + stage2, run.RUN_ALPACA_MAX)


if __name__ == "__main__":
    unittest.main()
