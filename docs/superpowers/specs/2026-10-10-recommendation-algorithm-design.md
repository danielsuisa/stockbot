# Recommendation algorithm (levels engine + four-signal ensemble) — design

Date: 2026-10-10 · Status: revision 5. Design approved in chat in three parts and revised after four reviews by
the owner (2026-10-10). This file is committed only on the owner's approval, and that approval covers the
methodology and phase A only (section 1.1) · Background:
`docs/superpowers/specs/2026-10-08-ticket-backtest-design.md` sections 6, 14.14, 15, 15.1, 16;
`docs/backtest/insider-step0-2026-10-10.md`; `docs/backtest/t-estimator-validation.py`

## 1. Purpose and decisions

The bot computes, and the owner executes by hand in IBKR: a full recommendation per stock — entry price, stop loss,
take-profit, trailing stop, time exit and size. The algorithm never sends an order.

Owner's decisions (2026-10-10):

- An original, complex algorithm, not another published single strategy. Complexity means several independent weak
  signals combined with fixed equal weights, never more filters on one signal (ZBA v2: 22 variants, NO-GO).
- Three sources of stocks: the algorithm's own daily scan, the bot's existing lists, any symbol the owner sends.
- Two layers: a **levels engine** (section 3; makes no edge claim) and a **selection engine** (section 4; the only
  part that claims an edge, and the only part judged by section 6).
- 1,000 random draws in the benchmark; the t statistic, the handling of overlapping trades and the costs are fixed
  in this spec before any return is computed (sections 5 and 6).
- Every recommendation carries the label "ללא יתרון מוכח" until section 6 gives a GO.

### 1.1 What approval of this spec covers

- **Approved:** the methodology of this document, and phase A (section 7): the data sources and their coverage, with
  no return computed.
- **Not approved by it:** phases B to E. Each needs the owner's explicit go-ahead after the previous phase's report.
  The blind run (C) and the verdict run (D) each need their own go-ahead; D also needs check 1 to have passed.
- **Never covered by this spec:** real money. A GO of section 6 changes a label and starts the forward check; trading
  on the recommendations is the owner's separate decision.
- **What the simulations of 6.3 do and do not show:** they check the test on a simulated model of how ticket results
  depend on each other. They are not a test of the algorithm on market data, and real results may depend on each
  other in ways the model lacks. They justify the registered rules; they predict nothing about the outcome.
- **The principle:** whatever passes technically, the outcome is set only by the data under the rules fixed here in
  advance — never by the expectation that the algorithm will succeed.

Out of scope: order automation, shorts, options, intraday signals, machine-learning models, tuning any number in
this spec on returns, real-fill bookkeeping (a later `/fill` command; until then "actual costs" means section 5).

## 2. Data (free sources only, stdlib only, existing clients and cache patterns)

- **Daily bars:** Alpaca, by symbol, split-adjusted, plus the raw close of each day (two series). A ticket is
  simulated on the split-adjusted series rescaled to the raw close of its signal day, so prices, share counts and
  commissions are in the money of that day. Dividends are ignored (a small bias against long tickets, the same for
  the random benchmark).
- **Quotes:** Alpaca historical SIP quotes (`alpaca.quote_full`), as in section 6 of the ticket spec.
- **Insider filings:** SEC Insider Transactions Data Sets (2015Q1 → 2026Q1 measured usable in STEP 0); later
  quarters from EDGAR daily indexes + Form 4 XML (the live scan's path).
- **Earnings releases:** EDGAR bulk `submissions.zip`: 8-K filings with item 2.02 and their acceptance time, by CIK;
  CIK → symbol as filed, from the insider data sets. To be verified in phase A (section 7). Pre-registered fallback
  if item 2.02 is available for fewer than 90% of universe stock-years: signal S2 drops the 8-K condition and keeps
  its price and volume conditions.
- **Universe on day D (point in time, includes later-delisted names):** symbols that appear as the issuer's trading
  symbol in a Form 3/4/5 filed in the 365 days before D (operating companies have insiders; funds do not), with an
  Alpaca bar on D, raw close ≥ $5, 20-session average dollar volume ≥ $10M, and ≥ 252 earlier sessions of bars.
  Liquidity decile on D = decile of the 20-session average dollar volume within that day's universe.
- **Bad prices (registered before any return is looked at):** a one-day close ratio ≥ 4 or a gap of more than 10
  sessions without bars ends a symbol's series (treated as a different security afterwards). A ticket open at that
  point exits at the last close before it. Downward one-day moves are kept as real.
- **Known biases, reported with every result:** about 10% of symbols as filed have no Alpaca bars (renamed or
  delisted; STEP 0), a delisted stock exits at its last close, Form 4/A amendments are ignored.

## 3. Levels engine (`bot/levels.py`, pure functions; one code path for backtest, shadow and live)

Input: symbol, entry mode, horizon H, daily bars through signal day D, the quote, the risk per trade.
`ATR` = `ticket.atr14` from bars through D. **R = 2 × ATR.** All numbers below are fixed conventions.

**Entry modes:**

- `open`: limit-on-open for the session after D, limit = close of D + 0.5 × ATR. Fills at the official open when
  open ≤ limit; otherwise no fill. Planned entry P = close of D.
- `breakout`: buy stop at high of D + $0.01, limit = stop + 0.25 × ATR, valid for the 3 sessions after D. On a
  session whose high ≥ stop: fills at max(stop, open) when that is ≤ limit; when the open is above the limit, the
  order rests at the limit and fills at the limit if a later low (that session or a remaining valid session) is ≤
  limit. Otherwise no fill. Planned entry P = stop.

**Which mode and H:** a ticket issued by a signal uses that signal's mode and H (section 4). A ticket without a
supporting signal (`/ticket SYMBOL`, or a symbol of the bot's lists, with no signal firing or open) uses
**`breakout` and H = 21**. When several signals are firing or open on the symbol, the ticket shown is that of the
most recent firing and the others are listed.

**Size:** shares = floor(risk / R); leg A = floor(shares / 2), leg B = shares − leg A. Backtest and shadow use
risk = $100; live uses env `TICKET_RISK_USD` (default 100).

**Exits** (prices from the fill F):

- Stop loss, both legs: F − R.
- Leg A: take-profit limit at F + 1.5 × R.
- Leg B: trailing stop, trailing amount R (an IBKR `TRAIL` order); no target.
- Time exit: whatever is open is sold at the close of the H-th session after the entry session.

**Refusals (`NO TICKET` with the reason, counted in every report).** Quote-free: fewer than 15 daily bars ·
shares < 2 · shares × P > 1% of the 20-session average dollar volume. Quote-based: no usable quote · cost guard:
(guard spread × shares + commission of 4 orders) / (shares × R) > 0.10.

**Daily-bar simulation rules** (the only fill model of the backtest; conservative where a daily bar is ambiguous):

1. A stop fills at the stop, or at the session's open when the open is beyond it.
2. On any session, when both a stop and leg A's target are inside the bar, the stop is taken first.
3. On the entry session the stop is checked against that session's low (for `breakout` even though the low may have
   come before the fill).
4. Leg B: each session, the running maximum is first raised by that session's high, then the session's low is
   checked against (running maximum − R); the exit is at that level, or at the open when the open is below the
   level carried from the previous session. This assumes the high comes before the low.
5. A take-profit limit fills at its price (at the open when the open is above it).

**Gross result of a ticket in R:** Σ over legs of leg shares × (exit − F) / (shares × R), each stock in its own R.
**Cost in R:** section 5's costs / (shares × R). A ticket that never fills has gross result 0 and cost 0.

**IBKR mapping (in the message):** two bracket orders of half size each — A: entry + limit target + stop; B: entry
+ trailing stop — both good till the time exit, which the bot announces on its day.

## 4. Selection engine (`bot/signals.py`, pure detectors over section 2's data)

Four signals, each from a different mechanism. Each firing issues one ticket through section 3 with equal risk.
A symbol has at most one open or pending ticket per signal; the same symbol may hold tickets of different signals.

| Signal | Fires on day D when | Entry | H |
|---|---|---|---|
| **S1 insider cluster** | D is the first filing date on which ≥ 3 distinct officers/directors have open-market purchases (code P, not a 10b5-1 line) totalling ≥ $100,000 with trade dates in the 30 days before; purchases with the same issuer, trade date, shares and price count as one person; no second event for the issuer within 21 sessions | `open` | 21 |
| **S2 earnings drift** | D is the reaction session of an 8-K item 2.02 (the acceptance day, or the next session when accepted at or after 16:00 ET) and: close/previous close ≥ 1.05, volume ≥ 2 × the 20-session average, close in the upper half of D's range | `breakout` | 42 |
| **S3 momentum near the 52-week high** | D is the first session after ≥ 21 sessions outside the state: close ≥ 0.95 × the highest close of the last 252 sessions, and the return from D−147 to D−21 is in the top 20% of that day's universe | `breakout` | 21 |
| **S4 short-term reversal** | D is the first session in the state: the 5-session return is in the bottom 5% of that day's universe and ≤ −10%, the stock is in the top half of the universe by liquidity, and no S2 reaction session fell in the last 5 sessions | `open` | 5 |

- **Scan (daily, after the close):** every ticket issued that day is journaled. The message shows the 5 highest
  ranked: by the number of signals firing or open on the symbol, then by liquidity.
- **`/ticket SYMBOL` and the bot's lists:** section 3's ticket for the symbol with the signals firing or open on
  it, or "אין אות תומך" (then `breakout`, H = 21). A symbol outside section 2's universe still gets a ticket, marked
  "מחוץ ליקום שנבדק". The squeeze list is shown as expected volatility, not as a buy signal (measured: no
  direction). Piotroski / Altman / Beneish are shown as information and do not enter any rule.
- Weights are equal and fixed. No signal is dropped, re-weighted or re-parametrised after any result is seen.

## 5. Costs (how "actual costs" are computed)

**Backtest and shadow, per filled ticket:**

- Commission: $0.0035 a share, minimum $0.35 an order, 4 orders (two entries, two exits).
- Spread: the SIP quote at 09:35 ET of the entry session (the latest quote no older than 60 s; a locked or crossed
  quote counts $0.01); the full spread is charged on every share (half in, half out), on every exit type. When no
  such quote exists, the guard spread is charged instead.
- Guard spread (what the bot can know when it issues the ticket): the SIP quote at 15:55 ET of D, same freshness
  rule; without it → `NO TICKET` (no usable quote).
- Slippage beyond the spread is in the fill rules of section 3 (gaps fill at the open).
- Stress case for gate 5: every spread × 1.5.

**Forward (from the day the signals run live):** the shadow journal stores, for every issued ticket, the guard
quote, the 09:35 quote of the entry session, and two simulated outcomes: the daily-bar model of section 3 and a
1-minute-bar replay in which leg B trails continuously. Forward cost and fill checks are in section 6.5.

## 6. The decision test (fixed here; one run)

### 6.1 Period, unit, blocks

- Period: signal days 2017-01-03 → 2026-03-31 (bars start 2016-01-04 and S3 needs 252 sessions; the insider data
  sets end at 2026Q1). Nothing in this spec was chosen on returns, so the whole period is unseen evidence.
- **Issued ticket:** a signal firing that passes every refusal of section 3. Refused firings are counted by reason
  and are outside the test. The unit of every quantity below is **R per issued ticket**, filled or not.
- **Ten equal blocks:** the period's sessions in order are split into ten consecutive blocks of equal length
  (block = floor(10 × index of the signal session / number of sessions), 0–9). 2026 covers one quarter only, so
  calendar years are reported but never gated; consistency is judged on the ten blocks, which weigh equally.
  **Five long blocks** = the consecutive pairs of the ten (0–1, 2–3, …, 8–9), about 22 months each.

### 6.2 The random comparison

- **Pool of ticket i:** universe stocks on its signal day D, in the same liquidity decile, with no ticket of any
  signal firing or open on D, that pass the quote-free refusals of section 3 at risk $100. Each member gets a ticket
  by section 3 with the same entry mode and H on the same day. Members sorted by symbol.
- The same rules for the real ticket, a pool member and a drawn member: the same simulation, an unfilled ticket
  counts 0, the result is in the stock's own R.
- g_i = gross result of ticket i. c_i = its cost in R. m_i = the mean gross result of its pool.
  A ticket whose pool has fewer than 20 members is dropped (counted).
- **a_i = g_i − m_i** (selection before costs). **x_i = a_i − c_i** (net excess: the selection must beat a random
  pick of the same day, liquidity and order type by more than the full cost of trading it).
- **1,000 draws:** draw k uses `random.Random(20261010 + k)` and replaces every ticket, independently, by one
  uniformly chosen member of its pool: **A_k = mean over i of (gross result of the drawn member − m_i)**.
  **A = mean of a_i.** A and A_k are the same quantity in the same unit — gross R per issued ticket net of the
  pool mean; a drawn stock has no quote, so costs are not in this comparison and are judged through x_i in gates
  1, 3, 4 and 5.
- 95th percentile = the 950th smallest of the 1,000 A_k; A must be strictly above it.
  Reported: p = (1 + #{A_k ≥ A}) / 1001.
- **Comparability (checked in the blind run, section 6.4):** quote-based refusals ≤ 5% of the firings that pass the
  quote-free refusals (the pool cannot be screened by quotes), and tickets dropped for a small pool ≤ 2%.

### 6.3 The t statistic, overlapping trades and long memory

Formula, for G blocks in time order, n tickets, mean x̄ and u_g = Σ over the tickets of block g of (x_i − x̄):

**SE = √(G / (G − 1) × Σ_g u_g²) / n; t = x̄ / SE.** A ticket belongs to the block of its signal day.

**Two registered tests; both must pass** (both are reported, neither is chosen afterwards):

| | Blocks | Critical value (Student's t, one-sided 2.5%) |
|---|---|---|
| T10 | the ten equal blocks of 6.1 | 2.262 (9 degrees of freedom) |
| T5 | the five long blocks of 6.1 | 2.776 (4 degrees of freedom) |

Blocks of 11 and 22 months are far longer than any ticket (at most 45 sessions), so overlapping tickets fall almost
entirely inside one block; no lag correction is needed. Overlap is also limited at the source: one open or pending
ticket per symbol and signal (section 4). The Newey–West estimators of revisions 2–3 are dropped: on the
simulations below they added nothing to T10 and T5.

**Checked by hand:** x = (1, −1, 2 | 0, 1 | −2, 1, 1 | 3) in four blocks: x̄ = 0.6667, u = (0, −0.3333, −2, 2.3333),
Σu² = 9.5556, SE = √(4/3 × 9.5556) / 9 = 0.3966, t = 1.68. The implementation must reproduce these numbers.

**Checked on simulated histories without any edge** (`docs/backtest/t-estimator-validation.py`: 30–54 thousand
tickets with horizons 5, 21 and 42 sessions that share daily shocks while open; in B–E each signal's edge also
drifts, staying in or out of favour for a while; 2,000 histories a scenario; a correct test says GO in 2.5%).
First figure: the script's defaults. Second: an independent run with other seeds (`2000 7`).

| Scenario | False GO, T10 and T5 | Edge found in 80% of histories, in units of the larger estimated SE | Estimated / true SE (the larger of T10, T5) | False GO, revision 3's rule (first run) |
|---|---|---|---|---|
| A. overlap only | 1.8% / 1.4% | 3.6 / 3.8 | 0.95 / 0.92 | 2.9% |
| B. + bursts of tickets, edges drift with a half-life of 1.4 months | 1.2% / 1.8% | 3.8 / 3.7 | 0.90 / 0.92 | 2.1% |
| C. half-life 2.4 months | 1.8% / 2.0% | 3.8 / 3.7 | 0.90 / 0.88 | 3.4% |
| D. half-life 6.6 months (about a year in or out of favour) | 3.6% / 3.5% | 3.9 / 4.1 | 0.77 / 0.73 | 7.6% |
| E. half-life 13.5 months (two years and more) | 6.5% / 7.2% | 4.4 / 4.4 | 0.60 / 0.59 | 14.2% |

Precision: a cell of 2,000 histories is known to about ± 0.7 points at 2.5% and ± 0.8 at 3.5%.

**Scope, decided before any return is seen:**

- **A–D are in scope.** The test must hold when a signal stays in or out of favour for up to about a year. In A–C
  the false-GO rate is at or below 2.5%. In D it is about 3.5%: above the target, stated and accepted rather than
  hidden — T5 halves it from revision 3's 7.6%, and no stricter fixed threshold is added to chase it.
- **E is out of scope.** With regimes of two years and more, 9.25 years hold only four or five independent
  stretches; the false-GO rate is about 7% and no test on this history can fix that. A GO therefore claims an edge
  *on the assumption that a signal does not stay in or out of favour for more than about a year*; the forward check
  (6.5) and the label "במעקב קדימה" carry what remains.
- **The estimated SE is not trusted to be close to the true one.** In D the larger estimated SE is only 0.73–0.77
  of the true SE. Check 1 (6.4) therefore does not correct the SE by an assumed percentage: its constant is the edge
  that the test actually finds in 80% of histories, measured in units of the estimated SE, in the least favourable
  in-scope scenario.

**Reproducing these figures:** with its defaults the script recomputes the first figures and exits with an error on
any difference (about two minutes; it prints each scenario as it finishes). With any other number of histories or
seed offset it is an independent run and fails if a false-GO rate is more than 3 Monte Carlo standard errors from
the figures above, or a detectable edge more than 0.3 away (`500 1` takes about half a minute). The exact comparison
relies on CPython's `random` module (computed on Python 3.13.16); on an interpreter with different random streams,
use an independent run.

### 6.4 The blind run, check 1, the gates

**Blind run** (`--blind`): the full program runs, and its report contains only this whitelist:

- counts of firings, refusals by reason and issued tickets, per signal and per block; pools dropped; the two
  comparability shares of 6.2;
- cost in R of filled tickets: median, 75th and 90th percentile, per signal (no counts);
- n, the two SEs of 6.3 (of x), SD_c = the larger SE × √n and the design effect against independent tickets;
- the largest |x_i − x̄| (a data-error check; unlike the largest |x_i| it does not change when every x_i changes
  sign or is shifted by a constant, so it is consistent with test 1 below);
- μ_min of check 1.

Never output, logged, cached or saved in blind mode: x̄, A, A_k, any mean, median, quantile or sum of g, a or x;
win rates; exit-type counts; fill counts; holding lengths; per-ticket, per-cluster or per-signal results; charts.

Enforced, not promised:

1. The report is built by one function from the arrays x and c. Tests: its output is byte-identical when every x_i
   is replaced by −x_i, and when the same constant is added to every x_i.
2. In blind mode no per-ticket result is written to disk, cache, log or workflow artifact; the workflow uploads
   the report only.
3. Before the blind run a fresh reviewer confirms there is no other output path (as in 14.12 of the ticket spec).
4. The run is made from a recorded commit; the same commit, unchanged, makes the verdict run.

**Check 1 (can the test find 0.10 R with the tickets that actually exist?):**
μ_min = 4.1 × the larger of the two estimated SEs (T10, T5). 4.1 is the largest in-scope figure of 6.3's table: in
scenario D an edge of 4.1 estimated SEs is the one found in 80% of histories, and in A–C a smaller one is. The
constant is in units of the estimated SE, so it already contains how far the estimate falls below the truth in
every in-scope scenario; nothing is assumed about that gap. Continue only if **μ_min ≤ 0.10 R** and both
comparability conditions hold.

What the claim is: when check 1 passes and a signal does not stay in or out of favour for more than about a year,
an edge of 0.10 R is found with a probability of at least 80%. Under scenario E the same check would need 4.4
estimated SEs, so there the probability is lower; that case is outside the claim.

**If any of them fails:** NO-GO ("not confirmable" or "not comparable"). The report states the failed number. No
return statistic is computed for output, then or later, and the verdict run is not made.

**GO only if all hold, on the pooled issued tickets:**

1. x̄ > 0 and t ≥ the critical value under both T10 and T5.
2. A strictly above the 950th smallest of the 1,000 A_k.
3. The mean of x_i is > 0 in at least 7 of the ten equal blocks, and in each half (blocks 0–4, blocks 5–9).
4. x̄ > 0 without the top 1% of tickets by x_i, and x̄ > 0 with each one of the four signals removed in turn.
5. Gate 1 holds in the stress case of section 5.

Reported, never gated: every signal alone, calendar years, filled tickets only, exit types, cost distribution,
refusals. The run is single-shot: afterwards no rule of this spec changes and it is not run again on this period.

### 6.5 Forward check (1.5 years of shadow tickets, no money)

A GO stays conditional until, on the forward tickets: the median cost in R is ≤ 1.25 × the backtest's median; the
mean difference between the 1-minute replay and the daily-bar model is within ± 0.05 R; and the forward x̄ is not
below the backtest's x̄ by more than 2 forward standard errors (6.3's formula with calendar months as blocks).

### 6.6 What the outcomes change

- **GO:** the label becomes "עבר מבחן היסטורי, במעקב קדימה". Never "guaranteed".
- **NO-GO (check 1, comparability or the gates):** the levels engine, `/ticket` and the scan keep working with
  "ללא יתרון מוכח"; the signals are shown as information. No second attempt on this period.
- The test confirms the rule over all issued tickets. An owner who takes a few of them gets a noisier sample of the
  same rule.

## 7. Phases (each ends in a report; the next starts only on the owner's go-ahead, section 1.1; a plan per phase)

- **A — data probe (no returns):** `submissions.zip` item 2.02 coverage; the universe size per year; firings per
  signal per year. Fallback of section 2 decided here.
- **B — levels engine + `/ticket`:** section 3 and the cost guard, live for any symbol, labelled unproven.
- **C — signals, journal, statistics with their tests, review, blind run, check 1.**
- **D — the single verdict run** (only after check 1 passes, from the blind run's commit).
- **E — daily scan live + forward shadow** (section 6.5).

## 8. Output

- `/ticket SYMBOL` and the daily scan: Hebrew, RTL, the bot's message conventions (numbers and symbols in
  `<code>`): source, signals, entry (type, stop/limit), stop loss, target (leg A), trailing amount (leg B), time
  exit date, shares per leg, cost in R, the label.
- `data/ticket_journal.json`: every issued ticket and its outcomes.
- `docs/backtest/ensemble-<date>.md` (+ CSV of tickets, verdict run only): Hebrew summary, the gates table, every
  assumption above.

## 9. Tests (offline, network mocked, as in the rest of the bot)

Levels: both entry modes at their edges (gap over the limit, late fill at the limit, expiry), the default mode and
H = 21 without a signal, sizing and the 2-share minimum, each refusal at its threshold, the five simulation rules
(stop gap, stop-first, entry-session stop, trailing with high-before-low, target at the open), time exit, costs,
an unfilled ticket = 0 with no cost. Signals: each condition at its edge, no look-ahead (a detector given bars
through D never reads later bars), the one-ticket-per-symbol rule, S2's acceptance time rule and its fallback.
Universe: point in time, the bad-price rule. Random comparison: the pool's membership rules, m_i, a draw
reproducible from its seed, A and A_k computed by the same function, the 950th-smallest rule, the two
comparability shares. Statistics: the hand example of 6.3 (0.6667, 0.3966, 1.68), T10 and T5 on a known series, the
ten-block split on a period that does not divide evenly, the five long blocks as pairs of the ten. Blind mode: the two invariance tests,
the whitelist (a report key outside it fails the test), nothing written besides the report. Each gate at its
threshold.
