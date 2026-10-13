# Index program: decisions made by the implementer (Amendment 6, F4)

Each choice below concerns something the spec with its six amendments leaves open. F4's order applies: (1) the
literal text; (2) the later amendment over the earlier text; (3) the option less favourable to the timing
policies and to passing a gate.

No choice was made by looking at a return on real prices; none was computed before the verdict run. No choice
changes sections 4 to 7, a threshold, a window, a cost or an acceptance check. All were made before the verdict
run.

| # | Question | Options | Choice | Reason (F4) |
|---|---|---|---|---|
| 1 | Where do the costs of a switch come from, and is s × equity measured before or after them? | (a) Costs out of the cash leg after s × equity (before costs) moves; (b) size SPY net of costs | (a). At s = 1 the cash leg is slightly negative (about −$2.70) and the bill rate is charged on it | (1) Section 5: "puts s × equity in SPY at the execution price and leaves the rest in the cash leg"; "costs are charged on the trade's day" |
| 2 | Is the equity at the close before a window an "earlier close" for the drawdown? | (a) Yes: a fall on the first day counts; (b) closes inside the window only | (a) | (3) The larger drawdown is less favourable to the scale search and to gate 7.2a. Section 5 says the account exists at that close |
| 3 | Which calendar fixes TOM's planned entry day? | (a) Weekdays less the regular holidays; (b) also the special closures announced days before (2007-01-02, 2018-12-05, 2025-01-09) | (a) | (1) 4.2: "the one the exchange calendar gave in advance". On this file the choice changes no TOM entry, and the wiring count is 851 either way |
| 4 | T's state before its first evaluable month-end | (a) OFF; (b) undefined | (a) | (3) It is the less favourable to TREND. All windows start after the first month-end with 10 closes (2005-11-30), so it affects only the information variant T(12), whose first warm day is reported |
| 5 | TREND's order tickets in the blind report | (a) Switches only; (b) with yearly rebalances | (a) | (1) Standing rule 2: a rebalance needs the scale, and the scale needs a drawdown, which the blind run may not compute. Labelled in the report |
| 6 | E4 on a window's last day | (a) No execution at its close; an open execution from the previous day's signal is done; (b) no execution at all that day | (a) | (1) E4: "a signal on a window's last day is not executed"; an open execution's signal came the day before |
| 7 | A yearly rebalance on a day that exits at the close | (a) Done at the open; (b) skipped | (a) | (1) 6.2: "after any rule event of that open", and the close event comes later |
| 8 | Round trips (8.3) | A position carried into the window is counted from the window's first open; a trip open at the window's end is not counted; result = equity after the exit / equity before the entry − 1 | As stated | (1) 5 (the window takes the position at its first open). Information only |
| 9 | "x̄ per year" in 8.3 | (a) 252 × x̄; (b) x̄ of each calendar year | Both reported | (1) Both readings; neither decides anything |
| 10 | The partial year 2026 (January to September) | Counted as a calendar year | Counted | (1) B5 "calendar-year returns"; only reported, no gate |
| 11 | t when the standard error is 0 | 0 | 0 | (3) Less favourable to passing gate a |
| 12 | Ties at a threshold | Unrounded comparison, no tolerance | Unrounded | (1) 7.1: "the t values are compared unrounded"; applied to every gate |
| 13 | STATIC excluded by the scale search | (a) Outcome STATIC, flagged `static_excluded`; (b) a TIMING outcome | (a) | (3) No timing policy becomes eligible without the STATIC comparison |
| 14 | The data fingerprint of 8.2 | The sha256 of the two files' sha256, 16 characters | As stated | (1) 8.2 names a fingerprint without a form |
| 15 | Ties in the drawdown | The first peak and the first trough of equal depth | As stated | Dates only; the depth is the same |
| 16 | The verdict report's path | `docs/backtest/index-verdict-<date>.json`, refused when one exists (F3) | As stated | (1) F3: "refuses when a verdict report already exists on the branch" |
| 17 | How the verdict finds the blind report | The latest successful `index-blind.yml` run for the same commit | As stated | (1) F3: the same commit; that run's report must equal the recomputed one |

**A property of the spec found in phase 2 (no change made).** In a flat market TREND sits in the cash leg and
STATIC holds 60% in SPY. TREND's growth is then about 0.6 × the bill rate above STATIC's, which can pass 7.2b's
1.0 point on cash yield alone. The code follows the spec as written; this is recorded, not changed (F4 forbids a
change to 7.2).
