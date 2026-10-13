"""List tickets (docs/superpowers/specs/2026-10-13-list-tickets-design.md sections 2 and 3): the names new to a squeeze
list, the one tickets message, the paper journal at the backtest's fixed $100 risk, and the outcomes filled by the
backtest's own functions. SYNTHETIC data; the network is mocked; the equity is the synthetic test value."""
import json
import os
import tempfile
import unittest
from unittest import mock

from bot import common, levels, list_tickets as lt, ticket_command as tc
from tests import msgrules
from tests.test_ticket_command import QUOTE, SIZING, bars_until

ROWS = [{"t": f"S{k}"} for k in range(1, 8)]  # rank = position + 1


def common_price(t):
    """The E-sized position value as the message shows it."""
    return common.price(t["shares"] * t["planned_entry"])


def ticket(symbol="AAPL", rng=1.0, sizing=None, day="2026-03-09"):
    t = levels.guard(levels.plan(symbol, bars_until(day, 300, rng=rng), day,
                                 risk=0.005 * sizing["E"] if sizing else 100.0), QUOTE)
    t.update(symbol=symbol, day=day, valid_sessions=["2026-03-10", "2026-03-11", "2026-03-12"],
             time_exit_if_first="2026-04-09", universe=[], first_session_started=False,
             sizing=tc.sizing_info(sizing, t) if sizing else None)
    return t


class NewNames(unittest.TestCase):
    def test_first_list_takes_the_top_five(self):
        self.assertEqual(lt.new_names(ROWS, None), [(1, "S1"), (2, "S2"), (3, "S3"), (4, "S4"), (5, "S5")])

    def test_names_already_on_the_days_other_list_are_not_new(self):
        """Review finding 6: the first regular-session list after the pre-market one does not ticket the same
        names twice."""
        self.assertEqual(lt.new_names(ROWS[:3], None, ROWS[:2]), [(3, "S3")])

    def test_changed_list_takes_the_entered_names_by_rank(self):
        old = [{"t": "S1"}, {"t": "S3"}, {"t": "S9"}]
        self.assertEqual(lt.new_names(ROWS, old), [(2, "S2"), (4, "S4"), (5, "S5"), (6, "S6"), (7, "S7")])
        self.assertEqual(lt.new_names(ROWS[:2], ROWS), [])


class Message(unittest.TestCase):
    def test_long_messages_split_at_ticket_boundaries(self):
        """Review finding 5: five full tickets pass Telegram's limit; the parts break between tickets only."""
        items = [(k, f"S{k}", ticket(f"S{k}"), None) for k in range(1, 6)]
        parts = lt.parts(items)
        self.assertGreater(len(parts), 1)
        for part in parts:
            self.assertLessEqual(len(msgrules.visible(part)), lt.PART_MAX)
            msgrules.check(self, part)
        self.assertEqual(sum(p.count("🎫 כרטיס ל-") for p in parts), 5)
        for part in parts[1:]:
            self.assertTrue(part.startswith("🎫 כרטיס ל-"), part[:40])  # each part starts at a ticket
        self.assertEqual("".join(parts).count("1,330"), 1)

    def test_one_message_with_the_fact_once(self):
        items = [(1, "AAPL", ticket(), None), (2, "BRK.B", {"ok": False, "reason": "no usable quote",
                                                            "symbol": "BRK.B", "day": "2026-03-09"}, None),
                 (3, "ZZZ", None, "HTTPError")]
        text = "\n".join(lt.parts(items))
        msgrules.check(self, text)
        self.assertEqual(text.count("1,330"), 1)  # the list's measured fact, once
        self.assertIn("ירידה של <code>0.1%</code>", text)
        self.assertIn("<code>34%</code> מהעסקאות הרוויחו", text)
        self.assertNotIn(tc.measured_line(), text)  # the 35,083 line is /ticket's, not the list's
        self.assertEqual(text.count(f"תווית: {tc.LABEL}"), 1)  # one issued ticket, its label
        self.assertIn("רשימת הסקוויז, מקום <code>1</code>", text)
        refusal = [line for line in text.splitlines() if "BRK.B" in line]
        self.assertEqual(len(refusal), 1)  # a refusal is one line
        self.assertIn(tc.REASONS["no usable quote"], refusal[0])
        self.assertTrue(any("ZZZ" in line and "HTTPError" in line for line in text.splitlines()))

    def test_small_account_refusal_stays_one_line(self):
        t = {"ok": False, "reason": "shares below 2", "symbol": "AAA", "day": "2026-03-09",
             "sizing": {"E": 3_000.0, "risk": 15.0}}
        text = "\n".join(lt.parts([(1, "AAA", t, None)]))
        self.assertEqual(sum(1 for line in text.splitlines() if "AAA" in line), 1)


class Journal(unittest.TestCase):
    def test_record_is_the_fixed_100_ticket(self):
        t100 = ticket()  # /ticket's build without sizing: the backtest's $100 risk
        rec = lt.journal_record(t100, rank=2)
        self.assertEqual(rec["shares"], t100["shares"])
        self.assertEqual(rec["shares"], int(100.0 / t100["R"] + levels.EPS))
        self.assertEqual((rec["source"], rec["rank"], rec["signal"]), ("squeeze", 2, "squeeze-list"))
        self.assertIsNone(rec["outcome"])
        self.assertNotIn("sizing", rec)

    def test_membership_never_depends_on_e(self):
        """Review finding 1: the E-sized ticket may be refused (size, cost) where the $100 one is issued, or the
        reverse; the journal and the logged counts follow the $100 ticket only."""
        e_refused = {"ok": False, "reason": "size above 1% of dollar volume", "symbol": "AAPL", "day": "2026-03-09"}
        items = [(1, "AAPL", e_refused, None)]
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "ticket_journal.json")
            self.assertEqual(lt.journal(path, [(1, "AAPL", ticket())]), 1)  # the $100 ticket is journaled
            self.assertEqual(lt.journal(path, [(1, "AAPL", ticket())]), 0)  # once per symbol and signal day
            self.assertEqual(lt.journal(path, [(2, "BBB", e_refused)]), 0)  # a $100 refusal is not
            j = json.load(open(path, encoding="utf-8"))
        self.assertEqual(len(j["tickets"]), 1)
        text = json.dumps(j)
        big = ticket(sizing=SIZING)
        for derived in (f"{SIZING['E']}", f"{big['shares'] * big['R']:.2f}"):
            self.assertNotIn(derived, text)  # nothing from E reaches the file
        self.assertIsNotNone(items)

    def test_run_logs_counts_that_do_not_depend_on_e(self):
        e_sized = ticket(sizing=SIZING)
        fixed = ticket()
        builds = []

        def build(s, sizing=None):
            builds.append(sizing is not None)
            return e_sized if sizing else fixed
        with tempfile.TemporaryDirectory() as d, mock.patch.object(lt.ticket_command, "build", side_effect=build), \
                mock.patch.object(lt, "load_sizing", return_value=SIZING):
            parts, counts = lt.run([{"t": "AAPL"}], None, None, os.path.join(d, "tj.json"))
        self.assertEqual(sorted(builds), [False, True])  # the message's ticket at E, the journal's at $100
        self.assertEqual(set(counts), {"names", "journaled"})  # no count of E-sized issues or refusals
        self.assertEqual(counts, {"names": 1, "journaled": 1})
        self.assertIn(common_price(e_sized), "".join(parts))  # the Telegram message is E-sized

    def test_issued_in_its_first_session_is_flagged(self):
        t = {**ticket(), "first_session_started": True}
        self.assertTrue(lt.journal_record(t, rank=1)["issued_in_first_session"])
        self.assertFalse(lt.journal_record(ticket(), rank=1)["issued_in_first_session"])


def after_rows(*spec):
    return [(d, o, h, lo, c, 1_000_000) for d, o, h, lo, c in spec]


class Outcomes(unittest.TestCase):
    def rec(self):
        t = ticket()  # entry stop 51.01 (high 50.5 + 0.51?), see the levels
        return lt.journal_record(t, rank=1)

    def test_unfilled_after_the_valid_sessions(self):
        r = self.rec()
        low = r["entry_stop"] - 5
        rows = after_rows(*[(f"2026-03-1{k}", low, low + 1, low - 1, low) for k in range(3)])
        self.assertEqual(lt.outcome(r, rows), {"filled": False, "net_r": 0.0, "gross_r": 0.0, "cost_r": 0.0,
                                               "closed": "2026-03-12"})
        self.assertIsNone(lt.outcome(r, rows[:2]))  # two sessions: the order may still fill

    def test_stop_out_matches_the_backtest_functions(self):
        r = self.rec()
        P, S = r["entry_stop"], r["stop_loss"]
        rows = after_rows(("2026-03-10", P, P + 0.2, P - 0.1, P), ("2026-03-11", P, P, S - 1, S - 1))
        got = lt.outcome(r, rows)
        t = lt.as_ticket(r)
        f = levels.fill(t, rows)
        ex = levels.exits(t, f[1], f[0], rows)
        self.assertTrue(got["filled"])
        self.assertEqual(got["exits"], [e[0] for e in ex])
        self.assertAlmostEqual(got["gross_r"], levels.gross_r(t, f[1], ex))
        self.assertAlmostEqual(got["cost_r"], levels.cost_r(t, r["guard_spread"]))
        self.assertAlmostEqual(got["net_r"], got["gross_r"] - got["cost_r"])
        self.assertLess(got["net_r"], 0)

    def test_still_open_waits(self):
        r = self.rec()
        P = r["entry_stop"]
        rows = after_rows(("2026-03-10", P, P + 0.2, P - 0.1, P + 0.1))
        self.assertIsNone(lt.outcome(r, rows))

    def test_one_bad_record_does_not_block_the_others(self):
        r = self.rec()
        low = r["entry_stop"] - 5
        rows = [(f"2026-03-1{k}", low, low + 1, low - 1, low, 1_000_000) for k in range(3)]
        j = {"version": 1, "tickets": [{**r, "symbol": "BAD"}, dict(r)]}

        def daily(syms, start, end):
            if syms == ["BAD"]:
                raise RuntimeError("alpaca: 404")
            return {r["symbol"]: rows}
        with mock.patch.object(lt.alpaca, "daily", side_effect=daily), mock.patch.object(lt.common, "trace"):
            self.assertEqual(lt.followup(j, "2026-03-20"), 1)
        self.assertIsNone(j["tickets"][0]["outcome"])
        self.assertFalse(j["tickets"][1]["outcome"]["filled"])

    def test_followup_fills_only_open_squeeze_tickets(self):
        r = self.rec()
        low = r["entry_stop"] - 5
        rows = [(f"2026-03-1{k}", low, low + 1, low - 1, low, 1_000_000) for k in range(3)]
        j = {"version": 1, "tickets": [dict(r), {**r, "source": None}, {**r, "outcome": {"filled": False}}]}
        with mock.patch.object(lt.alpaca, "daily", return_value={r["symbol"]: rows}) as daily:
            n = lt.followup(j, "2026-03-20")
        self.assertEqual(n, 1)
        self.assertFalse(j["tickets"][0]["outcome"]["filled"])
        self.assertIsNone(j["tickets"][1]["outcome"])  # not a squeeze ticket
        self.assertEqual(daily.call_args.args[1:], ("2026-03-10", "2026-03-19"))  # after D, through yesterday


class Stats(unittest.TestCase):
    def test_section_by_hand(self):
        recs = [{"source": "squeeze", "outcome": o} for o in (
            {"filled": True, "net_r": 1.2}, {"filled": True, "net_r": -0.9}, {"filled": True, "net_r": -1.1},
            {"filled": False, "net_r": 0.0})] + [{"source": "squeeze", "outcome": None}]
        text = "\n".join(lt.stats_lines({"tickets": recs}))
        msgrules.check(self, text)
        self.assertIn("כרטיסים", text)
        self.assertIn("<code>5</code>", text)  # tickets
        self.assertIn("<code>75%</code>", text)  # filled: 3 of the 4 closed
        self.assertIn("הפסד של <code>20%</code> מהסיכון", text)  # mean net: (1.2 - 0.9 - 1.1 + 0) / 4 = -0.2
        self.assertIn("<code>25%</code> הרוויחו", text)  # 1 of 4
        self.assertIn("<code>120%</code>", text)  # mean win
        self.assertIn("<code>100%</code>", text)  # mean loss: (0.9 + 1.1) / 2
        self.assertIn("מדגם קטן", text)

    def test_tickets_issued_in_their_first_session_are_left_out(self):
        """Review finding 2: a ticket issued after its first valid session opened could be scored on moves already
        known; it is counted apart and kept out of the numbers."""
        recs = [{"source": "squeeze", "issued_in_first_session": True, "outcome": {"filled": True, "net_r": 5.0}},
                {"source": "squeeze", "outcome": {"filled": True, "net_r": -1.0}}]
        text = "\n".join(lt.stats_lines({"tickets": recs}))
        self.assertIn("הפסד של <code>100%</code> מהסיכון", text)  # only the second one counts
        self.assertIn("הונפקו כשיום הכניסה הראשון כבר נסחר, ולכן אינם נכללים במספרים: <code>1</code> כרטיסים", text)
        msgrules.check(self, text)

    def test_no_tickets_yet(self):
        text = "\n".join(lt.stats_lines({"tickets": []}))
        self.assertIn("עוד אין", text)
        msgrules.check(self, text)




class Workflow(unittest.TestCase):
    def test_squeeze_workflow_carries_the_ticket_inputs_and_commits_the_journal(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        wf = open(os.path.join(root, ".github", "workflows", "squeeze.yml"), encoding="utf-8").read()
        self.assertNotIn("pull_request", wf)
        for line in ("ALPACA_KEY_ID: ${{ secrets.ALPACA_KEY_ID }}", "ALPACA_SECRET_KEY: ${{ secrets.ALPACA_SECRET_KEY }}",
                     "TICKET_RISK_PCT: ${{ vars.TICKET_RISK_PCT }}", "data/ticket_journal.json"):
            self.assertIn(line, wf)


if __name__ == "__main__":
    unittest.main()
