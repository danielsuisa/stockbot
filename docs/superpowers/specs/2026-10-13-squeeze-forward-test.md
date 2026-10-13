# Squeeze list: forward test (registered 2026-10-13, before any outcome of the current screen was judged)

Status: approved by the owner. This spec changes nothing in the screen, the alerts or `/ticket`.

## Why

The owner's goal for the bot is to find stocks before a sharp rise or a short squeeze. The measurements so far
found no edge: the list of 2026-09-27 picked stocks that rose 50% and fell 33% about equally often
(`docs/backtest/squeeze-2026-09-27.md`); buying every one of its 1,330 out-of-sample picks for 10 sessions gave a
mean of +0.28% and a median of -1.6% before costs (`docs/backtest/squeeze-2026-10-13-reanalysis.md`); breakout
tickets on these lists lost 0.21 R a trade (`docs/backtest/ticket-2026-10-09.md`). The current screen (2026-10-08)
was never tested, and no history of it is free of survivorship bias. The clean evidence is forward. The bot
already journals every listed name and its outcome after 10 sessions (`data/squeeze_journal.json`). This spec
fixes in advance how that journal is judged.

## The test

1. **Population.** Journal entries whose `variant` is the current screen or its pre-market list (`SCREEN` and
   `SCREEN_PRE` in `bot/squeeze_live.py`), with `outcome.trade` true, in journal order. Entries of an older
   variant do not count. If the screen's definition changes, this test ends unjudged and a new one is registered.
2. **One trade per entry,** as the journal already defines it: the journal's `r10`. No stop.
3. **Cost:** 1.0% a trade. x = r10 - 0.01.
4. **Sample size: the first 605 closed trades.** From n = (2.49 x SD / mu)^2 with SD = 0.296 (the 1,330 picks) and
   mu = 0.03. At the journal's pace this takes about a year, inside the 1.5 years of the power gate
   (`2026-10-08-ticket-backtest-design.md`, section 15).
5. **Verdict,** computed on exactly those 605 and never earlier. **GO** only if all three hold: the mean of x is
   above 0; t is at least 2.0, with the entry weeks as blocks and the standard error of `bot/ensemble_stats.se`;
   the mean of x is above 0 in the first 302 and in the last 303. Otherwise **NO-GO**. With this threshold the
   test finds a true mean of 3% about 70% of the time.
6. **Until then** `/squeeze stats` shows one more line with the count only, for example
   "מבחן קדימה: הושלמו 41 מתוך 605 עסקאות. התוצאה תוצג כשיושלמו כולן." No mean of x is shown before the verdict.
7. **At 605** the same line shows the verdict and the mean of x.
8. **After a GO** nothing happens by itself: it only allows writing a spec for tickets on this list. After a
   **NO-GO** the screen keeps its label and is not tested again. Real money is the owner's separate decision.
9. N, the cost and the rule do not change after registration.

## Build

- `bot/squeeze_forward.py`: pure functions `population(entries)`, `progress(entries)`, `verdict(entries)`.
- `tests/test_squeeze_forward.py`: hand-computed cases (population filter, the 605 cut, each of the three
  conditions, the weekly blocks).
- `bot/squeeze_live.py`: only `stats_text` gains the one line. Nothing else in the live bot changes.
