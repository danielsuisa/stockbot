# Intraday screener — current-system audit, evidence map and pre-registered experiment plan (2026-10-17)

Status: **audit and plan only. No strategy logic changed; no data downloaded for the experiments; the 2016–2021
holdout is untouched.** The plan below needs the owner's go (the repo's change control: a pre-registered section
that passes the power gate of spec section 15 before any backtest spends data).

## 1. Current-system audit

**What exists (bot/intraday.py, shipped 2026-10-17, commits 27eec8c / 6acc44b / d68387c).** A two-stage screener:
stage 1 picks "stocks in play" (price $5–$500, 20-session ADV ≥ 1M shares and $20M, ATR14 ≥ 2% and ≥ $0.50, gap
≥ 2% or pre-market volume ≥ 10% of a day; from 09:55 opening-5-minute relative volume ≥ 1.0, top 20); stage 2 scans
completed 5-minute IEX candles every 5 minutes (09:40–11:30, 15:00–15:45) for five long setups — ORB 5, ORB 15,
VWAP pullback with hammer / engulfing, bull flag, red-to-green — and issues a bracket ticket (buy stop-limit above
the trigger candle; stop under it floored at max(10% ATR, 0.3% of price), capped at 1 ATR; ORB 5 uses exactly 10% ATR;
target 2R; time exit 15:55; size = equity × TICKET_RISK_PCT, ≤ 20% of equity, ≤ 1% of 85% of ADV). Tickets are
paper-journaled and scored after the close. Nothing is sent to a broker. 22 unit tests cover shapes, setups, bracket
arithmetic, outcomes, state and message rules.

**What the repo had already established before this screener was written** — and what the screener's own research
note (intraday-strategies-2026-10-17.md v1/v2) understated:

- **The 5-minute ORB on stocks in play — the exact Zarattini-Barbon-Aziz 2024 spec, same universe (open > $5, 14-day
  ADV ≥ 1M, ATR14 > $0.50, top 20 by opening relative volume ≥ 1) — was tested here with conservative fills, a
  tick-level fill minute, spread gates, Rule 201 and a 20-seed random-side benchmark, across 22 pre-registered
  variants (spec 2026-10-08, section 14).** Development 2022–2026: the best variant X5 reached +0.199R (t 2.14) at tick
  level. The single pre-registered holdout 2016–2021: **−0.033R, t −0.47, positive in 3 of 6 years, −0.267R without the
  top 1% of trades → NO-GO.** Pooled 2016–2026: +0.061R, day-clustered t 1.08, 95% CI −0.04R to +0.18R; gross +0.275R
  minus costs 0.214R (spread 0.154R + commission 0.060R on a 0.37%-of-price stop). **The ZBA line was closed on
  2026-10-10**; the project rule is that no variant of the 5-minute ORB is retested without a new pre-registered
  section passing the power gate.
- Of the screener's components, the same program already measured: breakout-confirmation entries (the first minute
  closing beyond the range on ≥ 1.5× / 2× volume — the mechanism of the screener's ORB 5/15 trigger): **C15 −0.108R
  (t −2.65), C2 −0.123R (t −3.37)**; a wider volatility stop (max of k × ATR and the candle's far side, k = 0.10 / 0.25 —
  the mechanism of the screener's candle-based stop): **V10 −0.043R (t −4.73), V25 −0.046R (t −5.08)**, i.e. the
  wider stop raised the win rate to 42–43% and destroyed the open-tail payoff; requiring 2:1 room (RR) +0.132R, not
  better than base; a catalyst requirement (N) +0.212R; a candle-shape filter on the opening bar (F) +0.126R, t 1.2.
- My earlier note that the ZBA holdout failed "on a universe dominated by cheap names" was wrong: the universe was
  the paper's ($5+, 1M ADV, ATR > $0.50). It is corrected in this audit and in the research note.

**Assessment.** The screener is a faithful implementation of what the literature and the best-documented
practitioners describe, and it is well engineered for its purpose (paper tickets, journal, no execution). But as a
*strategy* it rests on a line this repo already rejected out of sample, plus four setups that have never been tested
here, two of whose mechanisms (breakout confirmation on volume; candle-based stops wider than 10% ATR) were measured
negative inside the ORB program. Its paper journal (/intraday stats) cannot decide anything in usable time (section
15 arithmetic below). It should therefore be treated as **a signal generator for research, not a recommendation
engine**, and its tickets labelled accordingly (done in this commit: "מחוץ למסלול שנבדק" on every ticket).

## 2. Evidence map

Classes: PR peer-reviewed · WP working paper · REG regulator/exchange · OFF official broker/vendor doc · INT internal
backtest of this repo (pre-registered, realistic fills) · PRAC practitioner (self-reported / book). Source numbers
refer to docs/research/intraday-strategies-2026-10-17.md §3.

| Component | External evidence | Internal evidence (INT) | Status |
|---|---|---|---|
| Relative-volume selection (opening 5-min, top 20) | WP [2]: Sharpe 0.48→2.81; PR [17], [13], [14] consistent | Selection used in all 22 ZBA variants; its *incremental* value never isolated here (no "random 20 from the prefilter" control was run) | **Supported externally, unverified internally** |
| Liquidity floor ($5, 1M ADV, ATR ≥ $0.50) | WP [2]; REG [57], [58] spreads | ZBA costs 0.21R on this universe with a 0.37% stop; cut by spread ≤ 0.05 × D had +0.52R on 136 trades (not significant) | **Supported (as a cost control), not as an edge** |
| 5-min ORB entry on stocks in play | WP [1], [2]; critique WP [4]; community replications lose the net edge to slippage | **NO-GO holdout 2016–2021; line closed 2026-10-10** | **Contradicted internally** |
| Breakout confirmation on candle volume (ORB 5/15 trigger as implemented) | PRAC [26], [28] | C15/C2 −0.11/−0.12R, t ≈ −3 | **Contradicted internally** |
| Candle-based stop (floored 10% ATR, capped 1 ATR) + 2R target | PRAC 2:1 everywhere [29, 30, 34, 41]; PR [25] stops help only in momentum regimes | V10/V25 −0.04/−0.05R, t ≈ −5 (as a *stop*, with open-tail exit); RR +0.13R (as a *filter*); a 2R *target* was never run (X2/X4 were other rules) | **Partly contradicted; the 2R-target version untested** |
| VWAP pullback with hammer / engulfing | No PR evidence for VWAP mean-reversion entries; PR [5, 8, 9] no candle value; WP [3] VWAP as a trailing stop helps | never tested | **Externally unsupported, internally untested** |
| Bull flag / micro-pullback | PRAC only [26, 28, 29] | never tested | **Untested** |
| Red-to-green | PRAC only [28] | never tested | **Untested** |
| Afternoon window with day-trend filter | PR [14] (index futures / ETFs, hedging-demand channel), PR [12] | never tested on single stocks here | **Externally plausible, untested** |
| Time exit at the close; long only | PR [14, 16]; REG [51] | ZBA used both | **Supported** |
| Gap as a score input | PR [16] gaps fade; WP [4] gap rules failed | not isolated | **Rejected (already removed in v2)** |
| 1% risk sizing, ADV cap, 4× leverage | WP [1–3]; REG [47–50]; PR [66] | portfolio() in the backtest uses it | **Supported (risk control, not edge)** |
| News catalyst | PRAC [26, 31]; INT N +0.212R vs +0.135R base (dev only) | dev-period only; not in the holdout variant | **Promising, unverified** |

## 3. Baseline (proposed, not run)

Framework: `bot/ticket_backtest.py` as is (conservative fills: a stop fills at the stop or the minute's open beyond
it; a minute touching stop and target counts as the stop; half-spread each side from the SIP quote at the trigger;
$0.0035/share, $0.35 minimum per order; Rule 201; survivorship caveat: Alpaca's asset list lacks some names delisted
before 2022, estimated 10–25% of 2016 names). Extensions needed (small): a 2R target exit, the 0.3%-of-price stop
floor, LULD-halt skip via `ticket.halted`, the ADV share cap, and the four new setup detectors (pure functions
already in bot/intraday.py, reusable on cached 1-minute SIP bars).

- **Control A (random selection):** 20 random names a day from the prefilter (price/ADV/ATR only), same entry
  mechanics — isolates the value of relative-volume selection.
- **Control B (random side):** the existing 20-seed mirror — isolates direction.
- **Benchmark:** SPY buy-and-hold over the same days; the per-trade metric is net R, the book metric is
  `portfolio()` at 1% risk, 4× cap.
- Periods: **development 2022-01-03 → 2024-12-31**, **validation 2025-01-02 → 2026-10-16** (used once, for the
  selection check), **holdout 2016-01-26 → 2021-12-31 untouched, one run**, as in section 14.

## 4. Pre-registered experiment plan (needs the owner's go; nothing runs before it)

Primary hypothesis H1 (fixed now): on stocks in play (prefilter + top 20 by opening relative volume ≥ 1), a long
entry one tick above a **VWAP-pullback reversal candle** or a **bull-flag** high between 09:40 and 11:30, stop under
the candle floored at max(10% ATR, 0.3% of price), target 2R, out at 15:55, has mean net R ≥ +0.10 with day-clustered
t ≥ 2.5 in development, and mean net R > 0 in the validation period. (One primary; the ORB and red-to-green setups
are secondary — ORB only as a documented negative control, since the line is closed.)

Ablations (each changes one thing; Holm correction over the 6 comparisons): A1 random-20 selection instead of
RVOL-20; A2 no 0.3% stop floor; A3 EoD exit instead of 2R target; A4 afternoon window on/off; A5 catalyst required;
A6 strong RVOL ≥ 5 only. Robustness: ±$0.005 and ±$0.01 slippage per share each side; without the top 1% of trades;
per-year sign; 2× spread stress. Multiple-testing: the selected variant's planning edge is halved (section 15).

**Power gate, Check 1 (computed before any data, spec section 15).** Payoff shape: win +1.9R / loss −1.1R after
~0.1R costs, assumed p = 0.40 → SD ≈ 1.47R, mean +0.10R. For μ = 0.10R: n = (2.49 × 1.47 / 0.10)² ≈ 1,340 filled trades.
At ~750 trades/year (20 names, ~15% trigger a setup) → **1.8 years: FAILS the 1.5-year gate.** It passes only with
≥ 900 trades/year (1.5 y) — e.g. a 30-name watchlist or both windows — or with μ = 0.15R (0.8 y). For comparison X5's
open-tail payoff needed 14 years. **So the first step is a free one: count signals (no trades, no fills) on 2024 from
the SIP cache to measure N. If N < 900/year the design is redesigned (wider watchlist) or dropped before any backtest.**

Cost of the full run: ~250 development days × 20 names of 1-minute bars ≈ 5,000 Alpaca requests (≈ 30 min at the
free-plan rate) plus quotes at triggers; the holdout about 1.5× that. Runs as a dispatched workflow committing
`docs/backtest/intraday-*.md/.csv` with the run id, like `zba-v2.yml`.

## 5. Provisional decision report (final classifications only after §4 runs)

| Component | Provisional |
|---|---|
| Relative-volume selection | RETAIN as selection (pending A1) |
| Liquidity floor, long only, close exit, 1% risk, ADV cap | RETAIN (risk controls, supported) |
| 5-min ORB entry (incl. confirmation on volume) | **REJECT** — holdout NO-GO; C15/C2 negative; line closed |
| ORB 15 | REJECT (15-min Sharpe 1.43 in [2]; same closed mechanism) |
| VWAP pullback with reversal candle | MODIFY → test as H1 |
| Bull flag | MODIFY → test as H1 |
| Red-to-green | MODIFY → secondary test only |
| Candle stop + 2R target | MODIFY → test (A2, A3) |
| Afternoon day-trend window | MODIFY → test (A4) |
| Gap score | REJECT (removed in v2) |
| Catalyst | MODIFY → test (A5) |

## 6. Verification run (this commit)

`python -m unittest discover -s tests` → see commit; 22 screener tests + the suite. Passing tests say the code does
what it says; they say nothing about an edge.

## 7. Next steps (smallest set before prospective paper trading)

1. Owner's go on §4. 2. Signal count on 2024 (free) → N; Check 1 pass/fail. 3. If pass: development run + ablations;
Check 2 with measured SD and N. 4. If pass: validation period once; then the holdout once. 5. Only a GO on all gates
unblocks the shadow line for this screener (spec 14.8 structure). Until then tickets stay research-only.

## 8. Honest assessment

The available evidence **does not support a credible net-profitability advantage for the screener as shipped.** Its
best-documented component (ORB on stocks in play) failed this repo's own pre-registered holdout after realistic costs;
the remaining setups are untested here and have no peer-reviewed support; the external evidence for the selection
filter (relative volume) is a commercial working paper plus consistent but indirect academic results. The result is
**inconclusive for the new setups and negative for ORB** — the screener should keep running only as a signal
generator for the experiments above, with every ticket labelled as outside the tested track.
