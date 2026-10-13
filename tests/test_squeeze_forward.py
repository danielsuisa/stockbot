"""The squeeze list's forward test (docs/superpowers/specs/2026-10-13-squeeze-forward-test.md): the population, the
605 cut, the three conditions of the verdict and the weekly blocks, on hand-computed SYNTHETIC journal entries."""
import datetime as dt
import math
import unittest

from bot import ensemble_stats as st, squeeze_forward as sf, squeeze_live
from tests import msgrules

SCREEN, PRE = squeeze_live.SCREEN, squeeze_live.SCREEN_PRE


def entry(day, r10, variant=SCREEN, trade=True):
    return {"date": day, "t": "AAA", "variant": variant,
            "outcome": {"trade": trade, "start": day, "r10": r10, "hit": False, "crash": False}
            if trade is not None else None}


def weeks(n, per_week=5, start="2026-10-12"):
    """n entry days, `per_week` of them in each week (Mondays onward)."""
    d0 = dt.date.fromisoformat(start)
    return [(d0 + dt.timedelta(7 * (k // per_week) + k % per_week)).isoformat() for k in range(n)]


class Population(unittest.TestCase):
    def test_filter_and_order(self):
        es = [entry("2026-10-12", 0.1), entry("2026-10-12", 0.2, variant="w0.7/si0.2/rv2"),
              entry("2026-10-13", 0.3, variant=PRE), entry("2026-10-13", 0.4, trade=False),
              entry("2026-10-14", 0.5, trade=None), entry("2026-10-15", 0.6)]
        self.assertEqual([e["outcome"]["r10"] for e in sf.population(es)], [0.1, 0.3, 0.6])  # journal order

    def test_progress_counts_only(self):
        es = [entry(d, 0.5) for d in weeks(41)]
        got = sf.progress(es)
        self.assertEqual((got["closed"], got["n"], got["done"]), (41, 605, False))
        self.assertNotIn("mean", got)  # no mean of x before the verdict
        self.assertEqual(sf.progress([entry(d, 0.5) for d in weeks(700)])["closed"], 605)


class Verdict(unittest.TestCase):
    def test_no_verdict_before_605(self):
        self.assertIsNone(sf.verdict([entry(d, 0.5) for d in weeks(604)]))

    def test_the_605_cut(self):
        es = [entry(d, 0.05) for d in weeks(605)] + [entry("2030-01-01", -100.0)]  # the 606th is never used
        got = sf.verdict(es)
        self.assertEqual(got["n"], 605)
        self.assertAlmostEqual(got["mean_x"], 0.04)  # x = r10 - 0.01

    def test_weekly_blocks_by_hand(self):
        days = weeks(605)  # 121 weeks of 5 entries
        r10 = [0.01 + (0.30 if k % 2 else -0.20) for k in range(605)]
        got = sf.verdict([entry(d, r) for d, r in zip(days, r10)])
        x = [r - 0.01 for r in r10]
        blocks = [k // 5 for k in range(605)]
        self.assertEqual(got["weeks"], 121)
        self.assertAlmostEqual(got["se"], st.se(x, blocks, 121))
        xb = sum(x) / 605
        u = [sum(x[g * 5:(g + 1) * 5]) - 5 * xb for g in range(121)]
        self.assertAlmostEqual(got["se"], math.sqrt(121 / 120 * sum(v * v for v in u)) / 605)  # the formula by hand
        self.assertAlmostEqual(got["t"], xb / got["se"])

    def test_go_needs_all_three(self):
        days = weeks(605)
        good = [entry(d, 0.06 + (0.01 if k % 2 else -0.01)) for k, d in enumerate(days)]
        g = sf.verdict(good)
        self.assertEqual(g["verdict"], "GO")
        self.assertTrue(g["mean_positive"] and g["t_ok"] and g["halves_positive"])
        # 1. the mean of x not above 0 (r10 exactly the cost)
        self.assertEqual(sf.verdict([entry(d, 0.01) for d in days])["verdict"], "NO-GO")
        # 2. t below 2.0: a mean of +0.5% with a large spread between weeks
        noisy = [entry(d, 0.015 + (0.5 if (k // 5) % 2 else -0.5)) for k, d in enumerate(days)]
        n = sf.verdict(noisy)
        self.assertTrue(n["mean_positive"])
        self.assertFalse(n["t_ok"])
        self.assertEqual(n["verdict"], "NO-GO")
        # 3. the first 302 not above 0, the last 303 well above: the mean and t can hold, the halves do not
        split = [entry(d, (0.0 if k < 302 else 0.10)) for k, d in enumerate(days)]
        s = sf.verdict(split)
        self.assertTrue(s["mean_positive"])
        self.assertFalse(s["halves_positive"])
        self.assertEqual(s["verdict"], "NO-GO")
        self.assertEqual((s["first_half_n"], s["second_half_n"]), (302, 303))


class StatsLine(unittest.TestCase):
    def test_line_before_the_verdict_shows_the_count_only(self):
        j = {"entries": [entry(d, 0.5) for d in weeks(41)]}
        text = squeeze_live.stats_text(j)
        self.assertIn("מבחן קדימה: הושלמו <code>41</code> מתוך <code>605</code> עסקאות. התוצאה תוצג כשיושלמו כולן.",
                      text)
        msgrules.check(self, text)

    def test_line_at_605_shows_the_verdict_and_the_mean(self):
        j = {"entries": [entry(d, 0.06 + (0.01 if k % 2 else -0.01)) for k, d in enumerate(weeks(605))]}
        line = sf.line(j["entries"])
        self.assertIn("GO", line)
        self.assertIn("<code>5.0%</code>", line)  # the mean of x: 6% - 1%
        msgrules.check(self, squeeze_live.stats_text(j))


if __name__ == "__main__":
    unittest.main()
