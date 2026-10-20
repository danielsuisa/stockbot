# S0 audit: swing bracket tickets at $2,500 (read-only)

Branch `engine/s0` (from `engine/i2c` e26ceab). Date 2026-10-20.

- **Sources:**
  - stockbot `origin/main` at 66d8d31; citations are `path:line@commit`, where `S:` = `docs/superpowers/specs/` and
    `B:` = `docs/backtest/`;
  - the unmerged branches `origin/cursor/breakout-pings-f857` and `origin/cursor/overnight-mom-ac8f`, marked
    **[branch]**;
  - the engine store, for metadata and descriptive statistics without forward prices only;
  - IBKR's official interest page.
- Each finding is given as **F** finding, **E** evidence, **I** impact, **R** recommendation.
- **No outcome was computed.** Every data read is listed in the [S0 report](S0-report.md).

## S1. Prior internal evidence

**S1.1 Inventory.**

| # | Experiment | Mechanics | Outcome periods computed | Verdict | Commit |
|---|---|---|---|---|---|
| 1–2 | Owner's and IMPROVED squeeze tickets, plus the spread-gate tune | Intraday breakout, $100 risk (S:2026-10-08-ticket-backtest-design.md:64-86) | 2024; 2025-01-01 → 2026-10-07 | NO-GO: ORIGINAL −0.207R, t −4.33 (B:ticket-2026-10-09.md:5) | 6785fef, 2ce3a89 |
| 3–5 | ZBA 5-min ORB, K-grid, v2 → X5 | Intraday stop entry, 0.1×ATR stop, out at close | Development 2022-01-03 → 2026-10-08; **holdout 2016-01-26 → 2021-12-31** (one run) | Holdout X5 −0.033R, t −0.47: NO-GO (B:ticket-zba-v2-holdout-2026-10-10.md:5). **Pooled gross +0.275R vs costs 0.214R** (S:2026-10-08-ticket-backtest-design.md:398@70a9a58); line closed | 4e22580 … 70a9a58 |
| 6 | K10s shadow (forward) | As ZBA K10s | 2026-10-08 → 10-19 (8 sessions) | Not judged (needs ≥ 100 trades) | 060482d |
| 7–9 | Squeeze screener, reanalysis, forward test | Open entry; 10-session window | 2018-02 → 2023-12 in-sample; 2024-01 → about 2026-09 out-of-sample; forward from 2026-09-29 | Lift 7.04× was volatility; reanalysis found no stop/target combination with an edge (B:squeeze-2026-10-13-reanalysis.md:48) | 5351e5d, 5d09b85 |
| 10 | List tickets (paper) | `levels` breakout, H = 21 | Forward 2026-10-13 → 15: 8 tickets, 3 closed | Information only | bc4cc1e |
| 11 | Insider STEP 0 | Swing, h21 and h63 | 2016-01-04 → 2026-03-31, **dispersion only** | n/a | 2ccd6d0 |
| **12** | **Ensemble S1–S4 swing tickets** | `breakout` = buy-stop at D's high + $0.01, limit + 0.25×ATR, valid 3 sessions; R = 2×ATR; leg A target 1.5R, leg B trailing; time exit H; $100 risk (S:2026-10-10-recommendation-algorithm-design.md:94-114) | **Signals 2017-01-04 → 2026-03-31, outcomes to 2026-07-31**; the random pools computed generic breakout and open tickets on the same days | **NO-GO**: x̄ −0.0531R (n 35,083), cost 0.0610R, so gross ≈ +0.0004R. **S3 (within 5% of the 52-week high + top-20% momentum, breakout, H 21): −0.0836R (n 13,104)**. **S2 (earnings day ≥ +5% on 2× volume, breakout, H 42): −0.0330R (n 7,880)** (S:…:449-458@8e3589a; ensemble-verdict-report.json:227-250) | c14cd3c, e37c72a |
| 13 | Index program (SPY) | D = RSI2 dip in an uptrend, next open | 2006 → 2026-09 | STATIC; D t 1.80 (B:index-2026-10-13.md:3,11-12) | 18ea846 |
| 15 [branch] | 20-day-high breakout v1/v2 | **Intraday**, flat at 15:55 | 2024-01-02 → 2026-10-13 | v2 −0.206R, t −15.47: NO-GO | e7d2857, 12c7557 |
| 16 [branch] | **Swing pullback in an uptrend** (20/50-day touch) | Next open; stop under the signal low; target the 20-day high; max 15 sessions | In-sample 2016-01 → 2022-12; out-of-sample 2023-01 → 2026-10-13 | **NO-GO: −1.122R, t −3.84 out-of-sample** | 4b8842a |
| 17 [branch] | **Post-earnings drift** | Gap ≥ 5% with relative volume ≥ 3; next-open entry (no trigger); stop at the event-day low; hold 20 | In-sample 2016 → 2022; out-of-sample 2023 → 2026-10-14 | **CLOSED-COST**: H20 out-of-sample −0.267R, t −1.24 (n 69) | a5d0e8a, e54a4c7 |
| 18–19 [branch] | Penny ORB / VWAP, premarket catalyst, ETF and macro books | Intraday or ETF | 2006/2009/2016 → 2026-10 | All NO-GO, CLOSED-COST or pending | 4b8842a, 8600741 |
| engine | I1/I2 insider; P3/P3D intraday | H 5/20/60; intraday | 2022-02 → 2026-07; 2025-05 → 2026-10 | NO-GO (a); Outcome A | aeea512, 908f25d |

**S1.2 Untouched families.**
- **F:** the closest candidates already have outcomes computed, all negative:
  - 52-week-high breakout (Ensemble S3, −0.084R over 13,104 tickets);
  - generic 1-day-high breakout tickets (the ensemble random pools, gross ≈ 0R);
  - post-earnings drift with a buy-stop trigger (Ensemble S2, −0.033R), and [branch] at the next open (−0.267R);
  - single-stock pullback in an uptrend ([branch] −1.12R out-of-sample);
  - 20-day-high breakout, intraday only ([branch]).
- **F:** two families have **no outcome computed anywhere**: a **non-earnings multi-day gap-and-hold continuation**
  ticket, and a **standalone multi-day N-day (20- or 55-day) breakout** ticket. The second is adjacent to the
  touched S3 and 1-day-high pools.
- **E:** the table above; S1 agent inventory.
- **I:** the prior for breakout-family swing tickets is **negative**. Across 35,083 tickets in 2017 → 2026-03 the
  gross edge was about 0R before a cost of 0.061R per ticket at $100 risk.

**S1.3 Untouched periods.**
- **F:** single-stock swing outcomes have been computed for every period from **2016-01-04 → about 2026-10-14**:
  - by the ensemble on `main` (2017 → 2026-07);
  - by the [branch] books (2016 → 2026-10);
  - by the squeeze tests (2018 → 2026-09).
- **F:** before 2016 there are no Alpaca single-stock bars. **The only untouched period is forward from about
  2026-10-14**, plus the sealed `forward_2026q4`. The 2022 → 2026 store is untouched only for the two families named
  in S1.2.
- **I:** a confirmation window that is untouched for the *family* exists in history only for gap-and-hold or a
  standalone N-day breakout. For any other family, confirmation must be **forward**.

## S2. Existing ticket code

- **F, two engines:**
  - **Intraday:** `bot/ticket.py` and `bot/ticket_backtest.py`. One session of 1-minute bars; NO-GO; closed.
  - **Daily bracket:** `bot/levels.py@2984d05`, behind `/ticket`, the list tickets and `data/ticket_journal.json`.
- **F, `levels.plan` ticket fields** (`levels.py:50-91`):
  - breakout entry `entry_stop = px(high_D + 0.01)` with limit offset + 0.25×ATR;
  - valid `BREAKOUT_DAYS = 3` sessions;
  - R = min(2×ATR14, 10%·P); stop P − R; leg A target P + 1.5R; leg B trailing R; time exit at session k + 21;
  - two legs; tick rounding `px()` to $0.01 (≥ $1) or $0.0001;
  - sizing `int(risk / R)`, with refusals below 2 shares or above 1% of 20-day dollar volume.
  - Risk is `TICKET_RISK_PCT` (0.5% default), capped at 0.5% while `EDGE_PROVEN = False` (`levels.py:253-261`;
    `bot/equity.py:91-103`).
- **F, fill model** (`levels.fill` / `levels.exits`, `levels.py:172-231`):
  - trigger when high ≥ entry_stop;
  - a gap above the trigger fills at max(stop, open) if at or below the limit; otherwise the order rests at the limit;
  - the stop takes precedence over the target in the same bar;
  - the entry-session stop is checked against the session low even if that low came before the fill;
  - leg B assumes the high comes before the low (spec `rec-spec:123-128`);
  - costs are per-share IBKR-like commission on 4 orders plus the full spread, **not IBKR Tiered with fees**.
- **F, journal** (`data/ticket_journal.json@7ae69a9`):
  - 8 tickets (2026-10-13 → 15), all from the squeeze list (breakout, H = 21);
  - 5 open, 3 closed and filled, with net R +0.089, −1.023 and +1.668;
  - the code's own statistics exclude all 8 as `issued_in_first_session`.
- **F, defects** (`docs/backtest/levels-phaseB-2026-10-11.md@905ddf6`; S2 agent):
  - the follow-up uses **raw** daily bars (a split distorts a ticket);
  - delisted tickets never close (`close_at_end` is not passed);
  - the journal charges the guard spread, not the entry quote;
  - six minor items (sub-cent spread floor, odd-leg sizes, test gaps).
- **R, copy (never import) into the engine:**
  - `px` (tick rounding);
  - `atr14` / `atr_through`;
  - the `levels.fill` gap and resting-limit logic;
  - the `levels.exits` stop-first rule;
  - the `list_tickets.outcome` pending, unfilled and closed semantics;
  - the `ensemble_backtest.rows_after` split rescaling.
- **R, do not copy** the cost model (use `scalp_eval/costs.py` Tiered) or the high-before-low trail assumption
  (use minute bars or stop-first instead).

## S3. Candidate swing hypotheses

Evidence classes: **PR** peer-reviewed, **WP** working paper, **PRAC** practitioner, **INT** internal. Literature
citations are bibliographic; their pages were not re-fetched in S0. Signal counts use the liquid universe (close ≥ $5,
20-day median dollar volume ≥ $10M), signal-time data only, at most one signal per symbol per 20 sessions (`D:\scalp-data\derived\s0_input_stats.json`).

| Candidate | Mechanism and ticket form | Evidence | Prior internal tests | Data needs | Signals / year (2024–25 avg) |
|---|---|---|---|---|---|
| **C1** 52-week-high breakout | Anchoring near the 52-week high. Signal: close > prior 252-day high. Ticket: buy-stop at the signal-day high + 1 tick, limit offset + 0.25×ATR, expiry 3 sessions, stop 2×ATR, target 2R, time exit 20 sessions | **PR** George & Hwang (2004), *J. Finance* 59(5):2145-2176: nearness to the 52-week high predicts returns over months (a cross-sectional rank, not a breakout ticket). Decay: McLean & Pontiff (2016), *J. Finance* 71(1):5-32: anomaly returns about 26% lower out of sample and about 58% lower after publication | **INT: Ensemble S3, −0.084R over 13,104 tickets (2017 → 2026-03): touched, negative** | Daily bars (held) | ≈ 4,240 |
| **C2** N-day (20 or 55) high breakout in an uptrend | Trend continuation. Signal: close > prior N-day high, close > SMA200, SMA50 > SMA200. Ticket as C1 | **PR** Brock, Lakonishok & LeBaron (1992), *J. Finance* 47(5):1731-1764 (trading-range breaks on the DJIA index). Decay: Sullivan, Timmermann & White (1999), *J. Finance* 54(5):1647-1691 (the best rules fail out of sample after 1986). **PRAC** Donchian / turtle | Adjacent: Ensemble random 1-day-high breakout pools (gross ≈ 0R); [branch] 20-day breakout, intraday only. **Multi-day N-day: untouched** | Daily bars | ≈ 7,200 (N = 20) |
| **C3** Pullback in an uptrend | Short-term reversal within momentum. Signal: close > SMA200, SMA50 > SMA200, close < prior 5-day low. Ticket: buy-stop at the signal-day high | **PR** Jegadeesh (1990), *J. Finance* 45(3):881-898; Lehmann (1990), *QJE* 105(1):1-28 (short-term reversal); Jegadeesh & Titman (1993), *J. Finance* 48(1):65-91 (momentum). Decay: Avramov, Chordia & Goyal (2006), *J. Finance* 61(5):2365-2394 (reversal concentrated in illiquid stocks) | **[branch] swing pullback −1.12R, t −3.84 out-of-sample (2023 → 2026): touched, negative**; SPY RSI2 dip t 1.80 | Daily bars | ≈ 11,800 |
| **C4** Post-earnings drift with a trigger | Under-reaction to earnings news. Signal: 8-K Item 2.02 day with a gap and close up. Ticket: buy-stop at that day's high, expiry 3 sessions, stop at the event-day low | **PR** Bernard & Thomas (1989), *J. Accounting Research* 27(Suppl.):1-36. Decay: Chordia, Goyal, Sadka, Sadka & Shivakumar (2009), *FAJ* 65(4):18-32 (in illiquid stocks); Martineau (2022), *Critical Finance Review* 11(3-4) ("Rest in Peace Post-Earnings Announcement Drift") | **INT: Ensemble S2 (trigger, H 42) −0.033R; [branch] PEAD −0.267R: touched, negative** | 8-K dates for every issuer (held for about 909 CIKs only) | ≈ 3,600 Item 2.02 filings (covered CIKs); ≈ 720 after an assumed 20% trigger filter |
| **C5** Gap-and-hold continuation (non-earnings) | Information shock plus the day's acceptance. Signal: open ≥ 1.03 × prior high, close ≥ open, close in the top 30% of the day's range, **no 8-K Item 2.02 within ±1 day**. Ticket: buy-stop at the signal-day high + 1 tick, limit + 0.25×ATR, expiry 3 sessions, stop at the signal-day low (capped at 2×ATR), target 2R, time exit 20 sessions | **PRAC** only (gap-and-go). Related evidence on overnight vs intraday returns: Lou, Polk & Skouras (2019), *J. Financial Economics* 134(1):192-213 (context, not this rule). Weakest evidence class | **Untouched.** Only earnings-conditioned gaps were tested (S2, [branch] PEAD) | Daily bars; 8-K Item 2.02 dates for every issuer to exclude earnings (gap in S4) | ≈ 1,250 (earnings gaps not yet excluded: an upper bound) |

## S4. Data coverage (metadata only)

| Input | Held | Gap and cost of a later ingestion |
|---|---|---|
| Daily SIP bars | 2022-01-03 → 2026-10-16, about 10.4k symbols, including delisted and renamed ones (I1; `raw_bars_1d` parts v1–v3, i1a, i1b, i1c2) | Nothing earlier is allowed: 2021 and before is sealed, and there is no Alpaca data before 2016. **The 252-session warm-up means 52-week signals start 2023-01**, and SMA200 signals start 2022-10 |
| Minute bars | 2023-08-01 → 2026-10-16, 807 dates, **1,088 symbols** (P1 top-500 members), SIP and IEX | They resolve same-day stop/target conflicts and trigger times only for those names. Extending to the C-universe (about 2,000–3,000 names) for 2022-10 → 2026-09: ≈ 1,000 sessions × 8 chunks × ≈ 4 pages ≈ 32k requests, ≈ 3–4 h at 170 a minute (P1 rate). **Alternative: stop first on the conflict day** |
| Close − 5 min NBBO samples | 1,202 sessions, 2022-01 → 2026-10, about 4,500 pre-filtered symbols a day (`i1-close-v1/v2`), plus 09:35 entry samples for I1 events only | 09:35 samples on ticket fill days: ≈ 1 request per fill day × ≈ 1,000 days ≈ 2k requests, < 1 h |
| Corporate actions | 2022-01-03 → 2026-10-16 (210,189 rows) | None |
| EDGAR 8-K Item 2.02 | `edgar_filings`: 2023-06-01 → 2026-10-16, **909 of 1,053 CIKs** have an Item 2.02; 11,834 filings (P2 top-500 mapped CIKs only) | For C4 and C5 (earnings exclusion) across the full universe: re-fetch submissions JSON for ≈ 8,000 CIKs (I1 stored SIC only) ≈ 17 min at 8/s; 2022-01 → 2023-05 is in older JSON pages (+ ≈ 1–2k requests) |

## S5. Evaluator gaps (L7 for daily bracket tickets)

- **Entry.**
  - The I2 evaluator (`scalp_eval/insider_study.py`) supports open entry only.
  - Needed:
    - a **stop entry with expiry**: a trigger when high ≥ trigger within N sessions;
    - **an open gapping through the trigger**: fill at the open if at or below the limit, else skip (no resting
      fill, to avoid the `levels` rule-6 complexity);
    - a **stop-limit offset**;
    - an entry fill price = trigger, or the open if it gapped, plus ½ the measured spread (09:35 sample on the fill
      day, else the prior close − 5 min).
- **Exits.**
  - Needed:
    - a multi-day bracket path;
    - a **same-bar stop/target rule**: minute bars where held (1,088 names, 2023-08+), **otherwise stop first**;
      the fill-session stop is checked only after the fill minute where minute bars exist, else stop first;
    - a gap through the stop fills at the open;
    - a target limit fills at the target, or the open if it gapped above;
    - a time exit at the close of session N;
    - the ADR 0010 delisting and merger rule;
    - split and stock-dividend adjustment of all ticket levels at read time;
    - cash dividends credited.
- **Account.** A cash account with T+1 settlement (proceeds usable the next session), 1% risk ($25), a
  position-value cap, a concurrency cap, and the order of competing tickets (seeded rank). These are all new; the I2
  account simulation is single-position.
- **Benchmark.**
  - **F:** IBKR pays **0% on the first USD 10,000** of uninvested cash. The USD rate above that is 3.38%
    (BM − 0.5%), reduced proportionally below a $100,000 NAV (https://www.interactivebrokers.com/en/accounts/fees/pricing-interest-rates.php,
    read 2026-10-20; rates as of 2026-09-18).
  - **I:** a $2,500 account earns **no interest on idle cash**. The book benchmark is SPY total return against the
    book with idle cash at **0%**.
- **R:** extend `scalp_eval` with a `bracket.py` (entry, exits, conflicts) and a multi-position `book.py`. Golden
  cases cover:
  - a gap above the trigger (fill and skip);
  - expiry;
  - a same-bar conflict with and without minute bars;
  - a stop gapped through;
  - a split during the hold;
  - a delisting;
  - T+1 cash blocking.

## S6. Costs at $2,500 (measured spreads)

- **Spreads:** median close − 5 min quoted spread by liquidity tier (20-day median dollar volume), price ≥ $5,
  universe-(b) table (prior-close values), 2022-02-01 → 2026-07-20.

  | Tier | Stock-days | Names per day | Median spread | p75 | p90 | Median price |
  |---|---|---|---|---|---|---|
  | T1 ≥ $50M | 1,217,615 | 1,025 | 0.050% | 0.090% | 0.153% | $81.21 |
  | T2 $10–50M | 1,124,793 | 1,002 | 0.095% | 0.155% | 0.250% | $32.50 |
  | T3 $2–10M | 905,707 | 817 | 0.152% | 0.246% | 0.435% | $17.10 |
  | T4 $0.5–2M | 489,514 | 442 | 0.327% | 0.626% | 1.229% | $13.55 |

- **Cost per R:** risk $25 (1%), shares = ⌊25 / (P · stop)⌋, IBKR Tiered with fees on buy and sell
  (`scalp_eval/costs.py`), plus ½ the median spread in and ½ out. The table gives the total, with commission + fees
  and spread in brackets.

  | Tier (price) | Stop 3% | Stop 5% | Stop 8% |
  |---|---|---|---|
  | T1 ($81) | **0.047R** (0.031 + 0.016) | **0.040R** | **0.034R** |
  | T2 ($32.5) | **0.066R** (0.035 + 0.031) | **0.051R** | **0.042R** |
  | T3 ($17.1) | **0.091R** (0.041 + 0.050) | **0.066R** | **0.052R** |
  | T4 ($13.6) | **0.153R** (0.044 + 0.108) | **0.102R** | **0.075R** |

- **F:**
  - Measured costs are 0.03–0.09R in tiers T1–T3, in line with the 0.03–0.10R premise.
  - The commission floor ($0.35 a side) dominates in T1/T2; the spread dominates in T4.
  - Not included: stop slippage beyond the spread, and gaps through the stop, which are handled in the evaluator as
    an open fill.
  - At a 3% stop the position is about $810, a third of the account, which limits concurrency (S5).

## S7. Power (no outcomes; the assumptions were stated before any count)

- **Assumptions:**
  - per-ticket net R SD σ = 1.3R (a bracket with a 1R stop, 2R target and 20-session time exit);
  - trigger fill rate 50%;
  - within-week correlation ρ = 0.02 (the I1 measured 0.021 for 20-day abnormal returns);
  - design effect DE = 1 + (m_eff − 1)·ρ, with m_eff from the weekly signal counts;
  - MDE = **2.8416 · σ · √(DE / n_filled)** (the I1 method).
- **Windows:**
  - development 2023-01 → 2024-12;
  - confirmation 2025-01 → 2026-09-17 (the last 20-session exit before the store end);
  - `forward_2026q4`: 61 sessions.

| Candidate | DE | Development: filled / MDE | Confirmation: filled / MDE | `forward_2026q4`: filled / MDE |
|---|---|---|---|---|
| C1 52-week high | 3.14 | 3,921 / 0.104R | 3,099 / 0.118R | 513 / 0.289R |
| C2 20-day high + trend | 4.60 | 6,887 / 0.095R | 5,640 / 0.106R | 872 / 0.268R |
| C3 pullback | 6.52 | 11,252 / 0.089R | 8,902 / 0.100R | 1,430 / 0.249R |
| C4 PEAD trigger (top-500 coverage, assumed 20% trigger filter, DE 2.0) | 2.0 | 542 / 0.224R | 560 / 0.221R | 87 / 0.559R |
| C5 gap-and-hold (upper bound before the earnings exclusion) | 2.50 | 1,122 / 0.174R | 1,047 / 0.180R | 151 / 0.474R |

- **Book level vs SPY.**
  - Assumptions: σ of annual excess return of a $2,500 book of 3–8 concurrent tickets = 30%; 2 years.
  - SE = 21.2% a year, so **MDE ≈ 60% a year. Too weak to decide anything.** It must be reported only.
- **F:** per-ticket MDEs of about 0.10–0.18R are of the same order as plausible edges and larger than the measured cost
  (0.03–0.09R). Clustering, not count, limits power: many signals fall in the same weeks.
- `forward_2026q4` alone can only detect about 0.25–0.56R.

**Reads made in S0:**
- manifests: `raw_bars_1d`, `raw_bars_1m`, `raw_quote_samples`, `edgar_filings`, `corporate_actions`, `cik_map`
  (metadata);
- `universe_b_daily` (prior-close price, `dv20`, spread);
- `edgar_filings` (form and items only);
- `raw_bars_1d` (open, high, low, close and volume up to each signal date, for signal counts only).

No forward price was used and no return was computed. Outputs: `derived/s0_input_stats.json`,
`s0_cost_table.json`, `s0_power.json`.
