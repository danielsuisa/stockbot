# ADR 0013: Swing-ticket validation design

- **Status: Proposed.** Only the user sets it to Accepted. Nothing in it has run: there is no ticket code, and no
  outcome has been computed.
- **Context:**
  - [ADR 0012](0012-engine-resumed-swing-tickets.md) (scope, account, ticket format, benchmark);
  - [S0 audit](../S0-audit.md), cited as S0 S1–S7;
  - I1/I2 methods (ADR 0008–0010): pre-registration, signed inputs, trials ledger, one confirmation run, the
    2.8416·SE MDE.

## 1. Primary hypothesis (recommended; G1 decides)

**H1, non-earnings gap-and-hold continuation (S0 S3, C5).** Stocks that gap up at least 3% above the prior high on a
day with no earnings release, and hold the gap into the close, continue higher over the next 20 sessions. A conditional
bracket ticket captures that move net of IBKR Tiered costs.

- **Why this one:** it is the only S3 family with **no outcome computed anywhere** in stockbot or the engine (S0 S1.2),
  so 2022-10 → 2026-09 is untouched for it (S0 S1.3).
- **Its evidence is practitioner-only (PRAC), and the internal prior for swing breakout-style tickets is negative.**
  Ensemble: gross ≈ 0R before 0.061R of costs over 35,083 tickets; S3 52-week −0.084R (S0 S1.1).

**Frozen ticket rules (H1).** Signal at the close of session t (all inputs dated ≤ t):
1. **Universe** (G2): price ≥ $5 and 20-session median dollar volume ≥ $10M at t; not an ETF or fund; mapped to an
   operating issuer with a SIC.
2. **Gap and hold:**
   - open_t ≥ 1.03 × high_{t−1};
   - close_t ≥ open_t;
   - close_t − low_t ≥ 0.70 × (high_t − low_t).
3. **Not earnings:** no 8-K with Item 2.02 accepted in [t − 1 session 16:00 ET, t 16:00 ET].
4. **One live ticket per symbol:** no signal within 20 sessions of the symbol's last signal.
5. **Ticket.**
   - **Trigger:** a buy stop-limit, trigger = high_t + $0.01, limit = trigger + 0.25 × ATR14_t.
   - **Expiry:** sessions t + 1 … t + 3.
   - **Stop:** max(low_t, trigger − 2 × ATR14_t) − $0.01.
   - **R** = trigger − stop.
   - **Target:** trigger + 2R.
   - **Time exit:** the close of the 20th session counted from the fill session.
   - **Quantity:** ⌊$25 / R⌋, i.e. 1% of $2,500, capped so that position value ≤ 40% of the account; skipped if 0.
   - **Rounding:** every price rounded to the tick ($0.01).

**Secondaries (reported only, no gate weight):**
- **S-a, C2:** a 20-session-high breakout in an uptrend (close > prior 20-day high, close > SMA200, SMA50 > SMA200),
  with the same ticket form and stop at trigger − 2 × ATR14.
- **S-b:** H1 including earnings days (rule 3 dropped), to measure what the exclusion removes.

## 2. Measurement (all in `scalp_eval`; the engine supplies signed inputs only, R4)

- **Per-ticket net R** for each filled ticket = (exit − fill) × shares + cash dividends − IBKR Tiered costs with fees
  (`scalp_eval/costs.py`), divided by $25.
  - **Fill:** the trigger, or the open when the session opens above the trigger and at or below the limit; skipped
    when it opens above the limit. Plus ½ the measured spread (09:35 sample on the fill day, else the prior close − 5
    min sample).
  - **Exits:**
    - stop (or the open if it gapped below);
    - target limit (or the open if it gapped above);
    - time exit at the close minus ½ spread.
  - **Same-bar stop and target:** minute bars where held, else **stop first**. The fill-session stop counts only
    after the fill minute where minute bars exist, else stop first.
  - Splits adjust all levels; delisting and mergers follow ADR 0010 §2.
- **Unfilled tickets** are counted and reported (fill rate); they carry 0R.
- **Random-entry control** (gate ii): for each ticket, K = 3 controls are drawn (seeded by SHA-256 of
  `f"{ticket_id}|s1-control-v1"`). Each is a random universe stock on the same signal day with **no** gap-and-hold
  signal, a ticket built by the identical geometry (trigger at its high_t + 1 tick, stop from its own low_t and ATR,
  2R target, 20-session exit), and the identical fill and cost rules.
- **Inference:**
  - mean net R per filled ticket, with SE clustered by ISO week of the fill (CR1), t and a week-block bootstrap
    (10,000, seeded);
  - realized MDE = 2.8416 · SE;
  - the same statistics for the per-ticket difference (ticket − mean of its controls).
- **Book simulation** (reported or gated, G4):
  - $2,500 cash, T+1 settlement, 1% risk;
  - at most 4 open positions and the 40% position cap;
  - competing tickets ranked by seeded random order;
  - idle cash at **0%**: IBKR pays nothing on the first $10,000 (S0 S5).
  - Compared with SPY total return over the same dates.
- **Trials ledger:** every run (commit, inputs' hashes, outputs' hashes). Development reruns only for documented
  defects, each with a failing test first. **One confirmation run.**

## 3. Gate draft (pre-registered before any outcome exists; G4 sets the values)

- **(i)** On the confirmation window, mean net R per filled H1 ticket **> 0, with clustered t ≥ 2.0**.
- **(ii)** H1 **beats the random-entry control**: mean (ticket net R − control net R) **> 0** [option: with t ≥ 2.0].
- **(iii)** The book at $2,500 against SPY with idle cash at 0%. S0 S7 puts the 2-year MDE at about 60% a year, so the
  **recommendation is reported only**.
- **Verdict:**
  - (i) and (ii) pass: **provisional GO**, which opens a forward paper check (G3) before any delivery (G6);
  - otherwise **NO-GO**, classified as (a) no edge (i fails) or (b) no edge over random entry (ii fails).

## 4. Open decisions for the user (evidence and options; not decided here)

**G1. Primary hypothesis.**
- **Evidence:** S0 S1–S3.
  - Of the five candidates, C1 (52-week, S3 −0.084R), C3 (pullback, [branch] −1.12R) and C4 (earnings with a trigger,
    S2 −0.033R; [branch] −0.267R) were already tested negative.
  - C2 (standalone N-day breakout) is adjacent to tested negatives (the random 1-day-high pools, gross ≈ 0R).
  - C5 is untouched but practitioner-only.
- **Options:**
  - (a) C5 gap-and-hold (non-earnings);
  - (b) C2 N-day breakout;
  - (c) do not pursue: the internal prior for swing breakout-style tickets is negative.
- *Recommendation:* **(a)**, with clear expectations. If the user doubts the practitioner-only premise, (c) is
  defensible.

**G2. Universe and liquidity floor.**
- **Evidence:** measured median spreads of 0.050% (T1 ≥ $50M), 0.095% (T2 $10–50M), 0.152% (T3 $2–10M) and 0.327%
  (T4). Cost per R at a 3–8% stop: T1 0.034–0.047R, T2 0.042–0.066R, T3 0.052–0.091R, T4 0.075–0.153R (S0 S6).
- **Options:**
  - (a) price ≥ $5 and dollar volume ≥ $10M (T1 + T2);
  - (b) ≥ $2M (adds T3);
  - (c) ≥ $50M (T1 only; about 40% fewer signals).
- *Recommendation:* **(a)**.

**G3. Windows.**
- **Evidence:**
  - The store holds single-stock daily bars from 2022-01 to 2026-10 only (2021 and earlier is sealed or absent), so
    H1 signals with SMA and ATR warm-up start in 2022-10.
  - Every single-stock period from 2016 → 2026-10 is touched for the other families, but not for C5 (S0 S1.3).
  - Expected counts and MDE (assumptions in S0 S7; C5 is an upper bound before the earnings exclusion):

    | Window | Filled tickets | MDE |
    |---|---|---|
    | Development 2022-10 → 2024-12 | ≈ 1,120 | ≈ 0.17R |
    | Confirmation 2025-01 → 2026-09-17 | ≈ 1,050 | ≈ 0.18R |
    | `forward_2026q4` (61 sessions) | ≈ 150 | ≈ 0.47R |

- **Options:**
  - (a) the historical development and confirmation windows above, with `forward_2026q4` as a **reported-only live
    forward check** after its window closes (ingested then, under a ledger record);
  - (b) the historical development window, with confirmation **forward only**: a paper journal from 2026-11, needing
    about 12 months for about 1,000 tickets;
  - (c) the historical windows, with `forward_2026q4` as an additional gate item (too weak at ≈ 0.47R).
- *Recommendation:* **(a)**. `legacy_2016_2021` stays sealed (no bars before 2016; ZBA already used it).

**G4. Gate thresholds; book gated or reported.**
- **Options:**
  - (a) (i) t ≥ 2.0 and (ii) difference > 0, with (iii) reported;
  - (b) (i) t ≥ 2.0 and (ii) difference with t ≥ 2.0, with (iii) reported;
  - (c) as (a) plus a minimum mean net R floor (e.g. ≥ 0.10R).
- *Recommendation:* **(a)**. (iii) cannot be gated at about 60% a year MDE.

**G5. Cash or margin account at $2,500.**
- **Evidence:**
  - Cash: T+1, so proceeds are usable the next session; with at most 4 positions at 1% risk this rarely binds.
  - Margin: FINRA requires at least $2,000 equity for a margin account (Rule 4210(b)); IBKR margin interest is charged
    on debits.
  - PDT is irrelevant to swing holds, which are held overnight and are not day trades. FINRA's 2026 Rule 4210
    amendments remove the PDT designation; firms may phase in until 2027-10-20 (ADR 0001 §7, FINRA RN 26-10). IBKR's
    own phase-in status was **not verified** in S0 (sources were limited to IBKR's pricing and interest pages).
- **Options:** (a) cash (the default); (b) margin.
- *Recommendation:* **(a) cash.** Leverage adds risk and interest without helping a 1%-risk book of 4 positions.

**G6. Delivery.** A later phase could add a stockbot `/swing` Telegram command that sends the frozen ticket text daily.
It is **out of scope**: recorded only, after a provisional GO and the forward check.
