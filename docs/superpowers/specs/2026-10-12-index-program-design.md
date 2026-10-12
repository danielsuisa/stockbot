# Index program: exposure management on the S&P 500 ETF (design and test protocol)

Date: 2026-10-12. Author: the project's analyst. Owner: danielsuisa. Status: **design approved by the owner's
brief; nothing built, nothing measured**. This spec governs the index program. It does not change the closed
ensemble test (`2026-10-10-recommendation-algorithm-design.md`, result NO-GO), and it does not reuse its tickets.

## 0. Standing rules

1. **Research only.** Nothing here approves real money. Real money is the owner's separate decision.
2. **Blind rule.** Before the verdict run (phase 4), no return, profit, equity curve or drawdown of any rule or
   policy is computed, printed, logged, cached or saved on real prices, anywhere (local, CI, tests). Tests use
   synthetic prices only. Price-agreement checks between data sources are allowed.
3. **One verdict run.** The verdict runs once, at a recorded commit, after the owner approves it. There is no
   second attempt on this period, and no rule, parameter, cost or window in this spec changes after the run.
4. **No merge to `main` without the owner's explicit approval.** The live bot, `/ticket`, `/equity` and the live
   workflows stay untouched. No ensemble workflow is dispatched. Nothing is pushed to `ensemble-run-blind`.
5. **Privacy (unchanged).** The owner's equity E and every number derived from it stay out of the repository and
   its logs. The backtest uses model accounts ($10,000, $5,000, $25,000), which are not the owner's numbers.
6. Standard library only. Secrets only from the environment or GitHub secrets.

## 1. The brief (the owner's answers, 2026-10-12)

| Question | Answer |
|---|---|
| Objective | Maximum total net profit, measured in USD |
| Capital next year | $5,000 to $25,000 |
| Instruments | Index ETFs, individual stocks, defined-risk options. No leverage, no short selling |
| Largest acceptable drawdown | 20% of the account, peak to trough |
| Execution | Manual, every trading day |
| Data budget | Free data only |

**What cannot be promised.** No rule makes every trade profitable. The program aims at a high share of winning
trades where the rule allows it, and at a positive total profit after costs. Losing trades and losing years are
part of every policy below.

## 2. Why this design (measured facts from the closed test)

The closed test measured 35,083 swing tickets on individual stocks (2017 to 2026):

- **L1, cost.** Mean cost was 0.0610 R per issued ticket. The mean selection edge before costs was 0.0079 R.
  Cost was about 8 times the edge. So the new program trades only the most liquid instrument, with auction
  orders, where the cost per trade is a few basis points of the position.
- **L2, structure.** Mean gross result of a ticket was +0.0004 R in a rising market: tight stops and trailing
  exits gave away the market's own rise. So the new program does not use tight stops. Its profit engine is the
  market's long-run rise, and its rules decide **when to hold the market and when to hold Treasury bills**.
- **L3, stock selection.** Four public signals added 0.0079 R before costs. So phase 1 does not select stocks.

Options are deferred: there is no free historical options data to test them, and one SPY contract covers 100
shares, which is several times the account. Individual stocks are excluded from phase 1 by L3.

## 3. Instruments, data and windows

- **Equity leg:** SPY (first trading day 1993-01-29), dividend-adjusted daily open, high, low, close.
- **Cash leg:** a Treasury-bill fund, modelled from the 3-month Treasury bill rate (FRED series `DTB3`):
  return on trading day t = `DTB3` of the previous trading day / 100 x d / 360, minus a fund fee of
  0.14% a year x d / 365, where d is the number of calendar days since the previous trading day.
  A real T-bill ETF (BIL) is compared with this model in the verdict report, for information only.
- **Signals use adjusted closes.** A ratio of a close to an average of earlier closes does not change when later
  dividends are back-adjusted, so a signal computed today equals the signal as it was on its day.
- **Data files** go to `data/index/` with a manifest (source, download date, rows, sha256) when the source's
  terms allow storing its data in a public repository. Otherwise only the manifest is committed and the files
  are kept as workflow artifacts. Either way every later run checks the sha256, and is offline and deterministic.

| Window | Dates | Use |
|---|---|---|
| H, full history | 1994-01-03 to 2026-09-30 | Source of the fitting periods and of the operating scale (6.2) |
| S, stress | 2007-03-01 to 2026-09-30 | After the trend rule's publication; includes 2008. Conditions of 7.2 |
| P, post-publication | 2009-01-02 to 2026-09-30 | After all three publications. Overlay tests and 7.2 |

P has 71 calendar quarters. Signals are computed on the whole file, so every window starts with warm signals.

## 4. The rules (frozen; each comes from a dated publication)

Notation: day t's adjusted close is C(t), its adjusted open is O(t). "Trading days" are the days in the SPY file.

### 4.1 T, trend (Faber, "A Quantitative Approach to Tactical Asset Allocation", SSRN 962461, February 2007)

On the last trading day of each calendar month: SMA10 = the mean of the adjusted closes of the last trading days
of the 10 most recent months, this month included. T is ON if C > SMA10, otherwise OFF. A change of state is
executed at the **open of the next trading day**. (The paper trades at the signal day's close. The next open is
used because the signal is known only after the close.)

### 4.2 TOM, turn of the month (McConnell and Xu, Financial Analysts Journal, 2008)

The paper is "Equity Returns at the Turn of the Month"; the rule is used as Quantpedia summarises it.
For each calendar month with last trading day L: buy at the **close of the trading day before L**, sell at the
**close of the third trading day of the next month**. The position covers four close-to-close days.

The entry day is the one the exchange calendar gave in advance. If that day does not trade, entry is at the next
close. This happened once: the planned day 2012-10-30 did not trade, so that entry is at the close of
2012-10-31. The exit counts the days that actually traded, so it needs no advance knowledge.

### 4.3 D, dip in an uptrend (rule family: Connors and Alvarez, "Short Term Trading Strategies That Work", 2008)

At the close of day t, if no D position is open, C(t) > SMA200(t) and RSI2(t) < 10: buy at the **open of day
t+1**. Exit: at the first close t' on or after the entry day with C(t') > SMA5(t'), sell at the **open of day
t'+1**. No stop and no time limit.

- SMA200 and SMA5 are means of the last 200 and 5 adjusted closes, day t included.
- RSI2 is Wilder's RSI with period 2 on adjusted closes: the first average gain and average loss are the simple
  means of the first two daily changes; after that avg = (previous avg + today's value) / 2;
  RSI2 = 100 - 100 / (1 + avg gain / avg loss), and 100 when avg loss is 0. It is computed from the file's start.
- The thresholds (10, 200, 5) and the use of SPY instead of single stocks are **this spec's choice, fixed before
  any test**. The book reports several thresholds; no other value is tried.

### 4.4 What is not allowed

No other rule, parameter, instrument or filter is added, searched or tuned. Section 8.3 lists the only
neighbouring values that are reported, for information, and they take no part in any decision.

## 5. Execution and cost model

- **Orders.** Open executions are market-on-open orders at O(t). Close executions are market-on-close orders at
  C(t). Every change between SPY and the cash leg trades **both legs** and pays costs on both.
- **Shares** are fractional in the backtest (the broker supports fractional ETF shares; a lower-priced S&P 500
  fund is the alternative in live use). The choice of the live instrument and account type is not part of
  this spec.
- **Commission:** $0.35 per order (IBKR Pro tiered: $0.0035 per share, minimum $0.35, at these order sizes).
- **Execution cost** (difference between the file's open or close and a real auction fill), per side, per leg:
  **base 1 basis point**, **stress 3 basis points**, optimistic 0. All three are reported.
- **Model account:** $10,000 at the start of each window. $5,000 and $25,000 are reported for information.
- **Accounting.** Equity is marked at every close. Entry day: from the execution price to the close. Exit day:
  from the previous close to the execution price. Costs are charged on the trade's day. The cash leg's return
  of day t is credited to the money that was in the cash leg at the close of day t-1. A position open at a
  window's end is marked at the last close.
- **Each window is a fresh account.** It starts in the cash leg at the close before the window's first day. At
  the window's first open it takes the policy's current position and pays the normal costs.

## 6. Policies and the drawdown budget

### 6.1 Policies

A policy says, for every moment, whether the account is "in SPY" or not.

| Policy | In SPY when | Exists |
|---|---|---|
| STATIC | Always | Always |
| TREND | T is ON | Always |
| TACT | A passing overlay holds a position | Only if at least one overlay passes 7.1 |

A policy trades when its state changes, at the execution time of the rule event that changed it, and at the
yearly rebalance of 6.2. An "order ticket" is one order in one leg.

### 6.2 Scale

- **Scale s.** A policy that goes into SPY puts s x equity in SPY at the execution price and leaves the rest in
  the cash leg. Out of SPY, everything is in the cash leg.
- **Yearly rebalance.** On the first trading day of each calendar year, at the open, after any rule event of that
  open: a policy that was in SPY at the previous close and stays in SPY, and whose SPY weight at the previous
  close differed from s by more than 0.05, trades the dollar amount that would have restored s at that close.
- **A window's scale is fixed from data before the window:**

| Scale for | Fitting period |
|---|---|
| Window S | 1994-01-03 to 2007-02-28 |
| Window P | 1994-01-03 to 2008-12-31 |
| Phase 5 (operating scale) | H, 1994-01-03 to 2026-09-30 |

- **Budget.** The owner's limit is 20%. Run the policy over the fitting period (fresh $10,000 account, base
  costs) with s = 0.01, 0.02, ... in rising order and stop at the first s whose maximum drawdown is above
  **16%**. The scale is the last s before it. The margin is there because the future's worst case can be worse
  than the past's. A policy that is above 16% already at 0.01 is excluded.
- **Crash cap: no scale is above 0.60.** From 25 August to 19 October 1987 the S&P 500 fell 33% on daily closes
  while its month-end close stayed above the 10-month average, so T was fully invested (the analyst's figures
  from the index's published closes, not from this program's files). H starts in 1994 and holds no such fall,
  so it is kinder to T than to STATIC. 0.60 x 33% = 20%. The cap applies to every policy.
- A scale is computed once, at base costs and $10,000, and is reused for every cost level and account size.

### 6.3 Growth and drawdown

- **Growth rate** = (final equity / initial equity) ^ (365.25 / calendar days from the window's first day to its
  last day) - 1.
- **Maximum drawdown** = the largest fall of the closing equity from its highest earlier close in the window.

## 7. Tests and decisions (all inside the single verdict run, in this order)

### 7.1 Overlay test (TOM and D, each alone, window P, base costs)

1. The overlay's account holds SPY with all its equity (s = 1) while the rule holds a position, and the cash
   leg otherwise. r(t) is its net daily return.
2. pi = the share of P's trading days on which the rule holds SPY **after the close**: from a position's entry
   day to the day before its exit day, for both rules. The benchmark's daily return is
   b(t) = pi x SPY's close-to-close return + (1 - pi) x the cash leg's return (no costs, which works
   against the rule). **x(t) = r(t) - b(t)**: what the rule's choice of days adds over holding the same average
   position all the time. x_bar is the mean of x(t) over P.
3. Standard error with the 71 calendar quarters as blocks, by the formula of `bot/ensemble_stats.se`:
   u_g = the sum over quarter g of (x(t) - x_bar); SE = sqrt(G / (G - 1) x sum of u_g^2) / n. t = x_bar / SE.
4. Gates. An overlay **passes** only with all four:
   - **a. Holm (5% one-sided for the pair, 70 degrees of freedom):** the larger of the two t values meets this
     gate if it is at least **1.9945**. The smaller meets it only if the larger met gate a and it is at least
     **1.6670**. The t values are compared unrounded.
   - **b. Consistency:** trading day number i (from 0) of the n days of P is in block floor(10 x i / n). The mean
     of x is positive in at least 7 of the 10 blocks, and in each half (blocks 0 to 4, blocks 5 to 9).
   - **c. Size:** 252 x x_bar is at least 1.0% a year.
   - **d. Stress:** x_bar is above zero with the stress costs.

**Power, stated before the test.** With 17.75 years, this test confirms with 80% probability only a rule whose
true timing Sharpe ratio (yearly mean of x over its yearly standard deviation) is about 0.7 or more. A real but
weaker effect will usually fail. A failure means "not confirmed", not "proven absent".

### 7.2 Which timing policy is eligible

Each policy gets its scales by 6.2. A timing policy (TREND, or TACT when it exists) is **eligible** only if all
four hold, each policy at its own scale for the window, at base costs unless said otherwise:

- **a. The limit held out of sample:** its maximum drawdown is at most **20%** over P and at most 20% over S.
- **b.** Its growth rate over P is above STATIC's by at least **1.0 percentage point a year**.
- **c.** Its growth rate over S is above STATIC's.
- **d.** Its growth rate over P is above STATIC's with the stress costs.

### 7.3 Program outcome

- **STATIC:** no timing policy is eligible. The recommendation is the static mix at its operating scale, which
  needs no algorithm, and the research on these rules ends.
- **TIMING:** at least one is eligible. If only one is, it is the selected policy. If both are, the selected
  policy is TREND, unless TACT's growth rate is above TREND's by at least 0.5 percentage point over P and is
  not below it over S; then it is TACT. The program continues to phase 5 with the selected policy at its
  operating scale.

**What 7.2 and 7.3 are.** A decision rule fixed in advance, not a statistical proof. History has few deep
falls (2000 to 2002, 2008, 2020, 2022), so the comparison rests on few events. The trend rule is in the
program as a **drawdown limiter that allows a larger position for the same worst case**, not as a claim that it
beats the market. Condition a tests exactly that claim, with a scale fixed before the window. T's timing
statistic x is reported by 7.1's formula for information and decides nothing.

## 8. Reports

### 8.1 Data probe report (phase 1)

Sources tried and their HTTP status; first day, last day and row count of each file; agreement between sources
(share of days inside the tolerance, the largest differences with their dates); how dividends are handled;
missing `DTB3` days; sha256 of each committed file. No return of any rule or policy.

**Acceptance.** SPY needs a full-history source (1993-01-29 to 2026-09-30, daily open, high, low, close, and
dividends or adjusted prices) that passes three checks:

1. **Integrity:** all prices positive; low <= open <= high and low <= close <= high on every day; the same
   trading days as each comparison source over the compared years.
2. **Against Alpaca's adjusted daily bars, 2016-01-04 to 2026-09-30:** adjusted close-to-close change within
   0.0005 on at least 99.5% of days, and the same day's open-to-close ratio within 0.0015 on at least 99% of
   days.
3. **Against a second full-history source, 1993-01-29 to 2015-12-31:** the same two tolerances. If only one
   full-history source is available, stop: the owner decides whether to supply a second file by hand or to
   accept one source.

`DTB3` covers 1993-12-01 to 2026-09-30; a missing day takes the previous value. If no source passes, stop and
tell the owner which file to download by hand. No paid source, and no sign-up without the owner.

### 8.2 Blind report (phase 3, real prices, counts only)

Data fingerprint; trading days per year; for each rule the number of entries per year and pi per window; for
TREND the number of order tickets per year. Nothing else. Its purpose is to catch wiring errors before the
single run.

**Wiring check.** TOM must hold SPY after the close on **851** days of P: 2 days from the December 2008 window,
4 days from each of the 212 windows of January 2009 to August 2026 less one day in October 2012, and 2 days
from the September 2026 window. Any other count is explained before phase 4.

### 8.3 Verdict report (phase 4)

- **Each overlay:** days, pi, x_bar per year, SE, t, gates a to d, the 10 block means; round trips, share of
  winning round trips, mean win, mean loss, worst round trip, longest run of losses.
- **Each policy, each window (S, P, and H at the operating scale), each cost level:** the scale (fitted value
  and value after the cap), growth rate, final equity of the model account, maximum drawdown with its dates,
  worst calendar year, share of positive years, order tickets, mean SPY weight. The same at base costs for
  $5,000 and $25,000.
- **References:** SPY held with s = 1, and the cash leg alone. BIL against the cash model.
- **Eligibility and outcome** by 7.2 and 7.3, with every number they use.
- **Information only, no decision:** the outcome with the crash cap removed (what crash safety costs); the
  outcome with budgets of 15% and 17%; T with 8 and 12 months; D with RSI2 thresholds 5 and 15; T's timing
  statistic.
- **Files:** a JSON report, a daily equity CSV per policy, and a Hebrew summary `docs/backtest/index-<date>.md`
  that states the outcome plainly, also when it is STATIC.

## 9. Phases (each ends with a stop for review)

1. **Data probe.** Sources, acceptance, committed files, report 8.1.
2. **Engine on synthetic prices.** Rules, accounting, costs, scale, statistics, each with hand-computed tests.
3. **Blind run** on the real files: report 8.2.
4. **Verdict.** A `workflow_dispatch` workflow in a GitHub environment whose required reviewer is the owner. It
   runs once, at a commit recorded before the run.
5. **Paper phase, only on TIMING.** After each close the bot sends the instruction for the next open and close,
   in percent of the account (no E), and tracks a model account. At least 6 months. Its details are written
   into this spec before it starts.

## 10. Out of scope

Options; individual stocks; leverage, short selling, leveraged or inverse funds; intraday rules; other asset
classes; any search over rules or parameters; machine learning; any change to the live bot before phase 5.

## 11. Risks and limits

- **The budget is not a guarantee.** The scale comes from the worst case of 1994 to 2026 and from the 1987
  fall. A fall worse than both is possible (1929 to 1932 was), and it would break the limit.
- **Few events.** One history, with four deep falls. Growth at equal drawdown is a noisy comparison.
- **Hindsight.** The rules were chosen knowing public discussion of their later results. Post-publication
  windows and fixed parameters reduce this bias and do not remove it.
- **Decay.** Across 97 published predictors, returns were 58% lower after publication (McLean and Pontiff).
- **Data.** Vendor adjustments; opening prices of the 1990s are less reliable, and they are part of the
  fitting periods.
- **Not modelled:** taxes, the shekel-dollar rate, missed or late manual orders.

## 12. Expectation before the test (the analyst's estimate, not a measurement)

- With the crash cap a policy holds at most 60% in SPY. TREND at that scale: about the Treasury-bill rate
  (4.25% on 2026-10-09) plus 2% to 4% a year as a multi-year average. STATIC at a scale near 0.30: the bill
  rate plus 1% to 2%. Both with drawdowns up to the budget and with losing years.
- Without the crash cap (reported for information): the bill rate plus about 3% to 7% a year, and a fall like
  1987 would break the 20% limit.
- Published sizes, before any decay: TOM 7.2% a year at 6.9% volatility (1926 to 2005). A dip rule of this
  family on Nasdaq-100 stocks won 64% of its trades (2006 to 2025, before costs). The trend rule on the S&P 500
  mainly reduced deep falls; most of its sell signals did not beat holding.

## 13. Sources

- Faber: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=962461
- Trend on the S&P 500, 1871 to 2017: https://earlyretirementnow.com/2018/04/25/market-timing-and-risk-management-part-2-momentum/
- Turn of the month: https://quantpedia.com/strategies/turn-of-the-month-in-equity-indexes
- Dip rule, tested form: https://backtest.substack.com/p/the-2-period-rsi-a-simple-system
- Notes on Connors and Alvarez: https://www.cxoadvisory.com/technical-trading/a-few-notes-on-short-term-trading-strategies-that-work/
- McLean and Pontiff: https://counterpointfunds.com/wp-content/uploads/2017/07/PredictabilityMcleanPontiff.pdf
- Overnight returns and costs (why that rule is not here): https://alphaarchitect.com/trading-costs-wipe-out-the-overnight-return-anomaly/
- Commissions: https://www.interactivebrokers.com/en/pricing/commissions-stocks.php
- Treasury-bill rate: https://www.slickcharts.com/treasury/3-month
