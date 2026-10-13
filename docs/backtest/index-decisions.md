# Index program: decisions made by the implementer (Amendment 6, F4)

Each choice below concerns something the spec with its six amendments leaves open. F4's order applies: (1) the
literal text; (2) the later amendment over the earlier text; (3) the option less favourable to the timing
policies and to passing a gate.

No choice was made by looking at a return on real prices; none was computed before the verdict run. No choice
changes sections 4 to 7, a threshold, a window, a cost or an acceptance check. All were made before the verdict
run.

| # | Question | Options | Choice | Reason (F4) |
|---|---|---|---|---|
| 1 | Where do the costs of a switch come from, and is s × equity measured before or after them? | (a) Costs out of the cash leg after s × equity (before costs) moves; (b) size SPY net of costs | (a). At s = 1 the cash leg is slightly negative (about −$2.70): a negligible loan against section 1's "no leverage", on which the bill rate is charged | (1) Section 5: "puts s × equity in SPY at the execution price and leaves the rest in the cash leg"; "costs are charged on the trade's day" |
| 2 | Is the equity at the close before a window an "earlier close" for the drawdown? | (a) Yes: a fall on the first day counts; (b) closes inside the window only | (a) | (3) The larger drawdown is less favourable to the scale search and to gate 7.2a. Section 5 says the account exists at that close |
| 3 | Which calendar fixes TOM's planned entry day? | (a) Weekdays less the regular holidays; (b) also the special closures announced days before (2007-01-02, 2018-12-05, 2025-01-09) | (a) | The literal text ("gave in advance") fits both readings, since those closures were announced days before. Both give the same TOM entries on this file: none of those three days is among a month's last two planned days. The wiring count is 851 either way, so the choice changes nothing |
| 4 | T's state before its first evaluable month-end | (a) OFF; (b) undefined | (a) | Neutral, information only. All windows start after the first month-end with 10 closes (2005-11-30), so it affects only the information variant T(12), whose first warm day is reported |
| 5 | TREND's order tickets in the blind report | (a) Switches only; (b) with yearly rebalances | (a) | (1) Standing rule 2: a rebalance needs the scale, and the scale needs a drawdown, which the blind run may not compute. Labelled in the report |
| 6 | E4 on a window's last day | (a) No execution at its close; an open execution from the previous day's signal is done; (b) no execution at all that day | (a) | (1) E4: "a signal on a window's last day is not executed"; an open execution's signal came the day before |
| 7 | A yearly rebalance on a day that exits at the close | (a) Done at the open; (b) skipped | (a) | (1) 6.2: "after any rule event of that open", and the close event comes later |
| 8 | Round trips (8.3) | A position carried into the window is counted from the window's first open; a trip open at the window's end is not counted; result = equity after the exit / equity before the entry − 1 | As stated | (1) 5 (the window takes the position at its first open). Information only |
| 9 | "x̄ per year" in 8.3 | (a) 252 × x̄; (b) x̄ of each calendar year | Both reported | (1) Both readings; neither decides anything |
| 10 | The partial year 2026 (January to September) | Counted as a calendar year | Counted | (1) B5 "calendar-year returns"; only reported, no gate |
| 11 | t when the standard error is 0 | 0 | 0 | (3) Less favourable to passing gate a |
| 12 | Ties at a threshold | Unrounded comparison, no tolerance | Unrounded | (1) 7.1: "the t values are compared unrounded"; applied to every gate |
| 13 | STATIC excluded by the scale search | (a) Outcome STATIC, flagged `static_excluded`; (b) a TIMING outcome | (a), and the run is reported as blocked (F6) if it happens | (3) No timing policy becomes eligible without the STATIC comparison. It cannot happen at s = 0.01 on SPY in practice |
| 14 | The data fingerprint of 8.2 | The sha256 of the two files' sha256, 16 characters | As stated | (1) 8.2 names a fingerprint without a form |
| 15 | Ties in the drawdown | The first peak and the first trough of equal depth | As stated | Dates only; the depth is the same |
| 16 | The verdict report's path | `docs/backtest/index-verdict-<date>.json`, refused when one exists (F3) | As stated | (1) F3: "refuses when a verdict report already exists on the branch" |
| 17 | How the verdict finds the blind report | The latest successful `index-blind.yml` run for the same commit, found by the guard job | As stated | (1) F3: the same commit; that run's report must equal the recomputed one |
| 18 | Pi with a position open at the file's end | It counts through the last day | As stated | (1) 7.1: "from a position's entry day to the day before its exit day"; with no exit, to the end. It only enters the benchmark |
| 19 | E4 in the fitting windows | Applied to every window, the fitting ones included | As stated | (1) E4 says "a window's last day" and E4's second bullet treats the fitting account as a window |
| 20 | The information variants D(5), D(15), T(8), T(12) | The whole decision is re-run: D's thresholds change D's overlay test and TACT | As stated | (1) 8.3: "the outcome with" these values. Information only, after the decision (E4) |
| 21 | Holm with two equal t values | The rule that sorts first takes the first threshold | As stated | Equal t values make both comparisons identical; no effect |
| 22 | The cash return on the file's first day | 0 | 0 | No previous trading day exists; no window starts there |
| 23 | Periods in the blind report | Entries per year over the whole file; TREND's tickets over H | As stated | 8.2 does not fix the periods; counts only |
| 24 | Rounding in the blind report | Pi to 6 digits | As stated | Makes the E5/F3 comparison exact across processes |
| 25 | The verdict report's file date | The runner's UTC date | As stated | A name only |
| 26 | Safeguards around the single verdict run (second review) | A guard job without environment, run before the owner is asked. It checks the environment's reviewer and branch, that the commit is the head of `index-program`, that no verdict run succeeded and no verdict report was uploaded or committed, and the blind run's TOM count. The blind job fails on a count other than 851. The report records the commit and both run ids | As stated | (1) F3 and F6: narrower checks would let a second verdict through |

**A property of the spec found in phase 2 (no change made).** In a flat market TREND sits in the cash leg and
STATIC holds 60% in SPY. TREND's growth is then about 0.6 × the bill rate above STATIC's, which can pass 7.2b's
1.0 point on cash yield alone. The code follows the spec as written; this is recorded, not changed (F4 forbids a
change to 7.2).

## Review findings and answers (F1)

**First review (phase 2, tasks 1 to 6):**
- **Fixed (`a1a0875`):**
  - the drawdown counts the close before the window;
  - the SMA is exact;
  - first-day and TOM carry-in tests;
  - the privacy tests are stronger;
  - the cold T(12) is noted;
  - an excluded STATIC is flagged;
  - the summary states STATIC's operating scale.
- **Answered as decisions above:** the costs at s = 1, the blind tickets, the round trips, the partial year, and
  the edge cases.
- **Recorded as a property of the spec:** the flat market.

**Second review (tasks 7 and 8, the decisions):**
- **Fixed with tests:**
  1. The verdict could run unprotected if the environment were missing. The guard job now checks the environment
     before the owner is asked; the environment was created and read back.
  2. A second verdict was possible through a re-run or a re-push. The guard job refuses a successful earlier run
     or an uploaded report.
  3. Nothing checked the head commit of `index-program`. The guard job compares it with `git ls-remote`.
  4. No automatic check of 851. The blind job fails after its upload, and the guard and the driver refuse.
  5. The record of the run. The report holds the commit, the blind run's id and the verdict run's id.
  6. `// empty` in the run lookup.
  7. The refusals now run before the approval.
  8. GitHub's SSH host key now comes from `gh api meta`, with `StrictHostKeyChecking=yes`. The actions stay
     pinned by tag, as in the repository's other workflows.
  9. Comparisons with an average are exact (`n × C > fsum`).
  10. A drawdown from the close before the window reports that close's date.
- **Corrected above:**
  11. The reasons of decisions #1, #3, #4 and #13.
  12. The unrecorded choices, now #18 to #25.
