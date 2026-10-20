# ADR 0008: Insider-signal validation design

- **Status: Accepted.**
  - Decided by the user on 2026-10-19, with the decisions recorded in §7 (I1 prompt, "User decisions").
  - Where §1–§5 below differ from §7 or §8, **§7 and §8 govern**. §1–§5 are kept as the Proposed text that was
    decided on.
  - The I2 measurement in §8 is pre-registered. It was committed on `engine/i1` before any I1 ingestion request
    and before any data exists.
- **Context:**
  - [ADR 0007](0007-intraday-line-closed.md): intraday line closed, lessons, reusable components.
  - [I0 audit](../I0-audit.md): the stockbot rule, the journal, the data and the engine gaps. Cited as audit I1.x–I5.x.
- **Order of work:** this ADR is Accepted, with E1–E5 decided → the I1 data phase → the parity test (audit I5.1) → a
  pre-registration commit of the exact gate (§4) → one evaluation run on committed code. No threshold changes after
  any result is seen.

## 1. Hypothesis

**H1.** Stocks with a stockbot **insider-buying cluster**, as defined at a frozen commit (E2), outperform a size- and
sector-matched control over H trading sessions. The return is measured from the signal's **public availability**.

- **Event:** issuer *i* has its cluster condition become true at time *t*. The condition is evaluated as of *t*,
  using only Form 4 lines whose filings were **accepted by *t***, applying the frozen rule:
  - code `P`, flag `A`, non-derivative, not 10b5-1;
  - officer or director;
  - at least 3 distinct people, at least $100,000 total;
  - 30 calendar days of transaction dates, looking back from *t*'s date;
  - joint merge.
  - The thresholds are confirmed against the GitHub repository variables (E2, audit I1.2).
- **Clock:** *t* = the EDGAR **acceptance time** of the filing that completes the cluster (audit I1.4, I3.2–I3.3).
  - The signal is usable at *t* + **latency** (proposed: 30 minutes).
  - **Entry:** the first regular-session open strictly after *t* + latency.
- **Amendments:** as-first-filed. A 4/A acts only from its own acceptance time (audit I1.5).
- **Re-trigger gap:** an issuer cannot generate a new event within 21 sessions of its previous event (the STEP 0
  convention). Overlapping windows would otherwise double-count.
- **Not part of H1:**
  - the "big" single-purchase trigger (a separate hypothesis, if ever tested);
  - Form 5;
  - OTC issuers (no price data, audit I4.1);
  - foreign private issuers (no scored block in the live rule).

## 2. Evaluation

**2.1 Event study (engine + evaluator, R4 split).**
- **Abnormal return** of event *e* over H ∈ {5, 20, 60} sessions, from the entry open to the close of session H:
  - **(a) vs SPY:** r_e − r_SPY over the same interval.
  - **(b) vs matched control:** r_e minus the mean of K random control stocks (proposed K = 5). Each control:
    - is drawn with a fixed seed;
    - from the same sector bucket (SIC → Fama–French 12, Proposed);
    - from the same 20-day dollar-volume decile (a size proxy, audit I5.2.4);
    - and passes the same price and liquidity floor (E3);
    - and has no event of its own in the prior 60 sessions.
- **Statistics:**
  - Mean and median abnormal return.
  - Standard errors **cluster-robust by entry month**; report the effective n (design effect 8.3 at 21 sessions in
    STEP 0).
  - Bootstrap 95% CI by month blocks.
- **Data rules fixed before any return is computed:**
  - corporate-action adjustment;
  - delisting return (cash-merger price; −100% for worthless removal; otherwise the last price minus 30%, Proposed);
  - a bad-price filter (STEP 0 open item 2);
  - the missing-quote rule (audit I4.2).

**2.2 Tradable version (L7, the evaluator side).**
- **Entry:** a marketable / market-on-open fill at the open plus ½ of the quoted SIP spread near the open. There are no
  passive limits (ADR 0007 §2.2).
- **Liquidity cap:** at most `LIQUIDITY_SHARE` of first-minute volume.
- **Exit:** time exit at the close of session H.
- **Account:** cash, $500 starting equity, T+1 settlement (`scalp_eval/account.py`).
- **Concurrency:** one position at a time. An event arriving while cash is committed is skipped and counted (Proposed).
- **Costs:** IBKR Tiered and Fixed, with regulatory fees (`scalp_eval/costs.py`).
- **Reports:** net PF, net mean return per trade, max drawdown, trades and skipped events.
- **Signing:** the engine signs event envelopes and the evaluator verifies every envelope (reusing `signing.py` and
  `verify.py`).

**2.3 Trials ledger.**
- Every evaluated variant (horizon, benchmark, the quality-score secondary if chosen) is appended to a trials ledger
  before its result is read.
- The gate counts the number of trials (§4).

## 3. Power (illustrative; from STEP 0 dispersion only, no means)

These figures come from STEP 0's numbers: SD 0.206 at 21 sessions, design effect 8.28, and events per year
(audit I1.8). Formulas:
- SE ≈ 0.206 · √(8.28 / n);
- minimum detectable mean effect (MDE) at 80% power, one-sided α = 0.05: ≈ 2.49 · SE.

STEP 0 events are keyed on filing date without the 4/A or joint rules, so the counts are approximate.

| Period | Expected events (≈ 10% lost to missing bars) | SE at 21 sessions | MDE at 21 sessions |
|---|---|---|---|
| `forward_2026q4` (61 sessions) | ≈ 100–125 | ≈ 0.053–0.057 | **≈ 13–14 pp** |
| 2022-02 → 2024-06 | ≈ 1,300 | ≈ 0.016 | ≈ 4.1 pp |
| 2024-07 → 2026-07-20 (last entry with H = 60 completed before data end) | ≈ 950 | ≈ 0.019 | ≈ 4.8 pp |
| 2022-02 → 2026-07-20 | ≈ 2,250 | ≈ 0.013 | ≈ 3.1 pp |
| `legacy_2016_2021` | ≈ 3,650 | ≈ 0.010 | ≈ 2.4 pp |

Caveats:
- With about 3 entry months, `forward_2026q4` cannot estimate a design effect, and cluster-robust inference on 3
  clusters is unreliable.
- At 63 sessions STEP 0's SD is dominated by outliers (likely data errors), so H = 60 power is not plannable until the
  bad-price rule exists.

## 4. Gate draft (to be pre-registered before any data is computed)

The values in brackets are options for E4, not decisions.

- **Primary metric:** the mean abnormal return vs the **matched control** at **H = [20]** sessions, on the
  confirmation period (E1), cluster-robust by entry month.
- **PASS requires all of:**
  1. Confirmation-period primary mean ≥ **[+2.0 pp]** with a cluster-robust t ≥ **[2.0]**. The t threshold is
     Bonferroni-adjusted for the number of trials in the ledger if more than one primary is declared.
  2. The same sign, and at least half the magnitude, in the development period. This guards against a one-period
     artifact.
  3. **Tradable:** L7 net mean return per trade > 0 and net PF ≥ **[1.2]** at $500 Tiered, with ≥ **[100]** trades in
     the confirmation period.
  4. Parity with the live journal is proven (audit I5.1) before the run.
  5. If `legacy_2016_2021` is used as a veto (E1): no veto failure. A failure means the primary mean is ≤ 0 there.
- **FAIL on any item.** No re-runs, no loosening.

## 5. Open decisions for the user (evidence and options; not decided here)

**E1. Evaluation windows.**
- **Evidence:**
  - Insider-derived returns have already been looked at on 2017–2026-03. Ensemble S1 had a signed mean of −0.032, with
    ticket exits keyed on filing date. STEP 0 saw dispersion only, over 2016–2026-03 (audit I1.8). The live rule's own
    acceptance-time returns have never been computed.
  - `forward_2026q4` is sealed with role `confirmation` for the closed intraday line. It is unopened, and its MDE is
    ≈ 13–14 pp (§3).
  - `legacy_2016_2021` is `veto_only`: run once, a fail blocks GO, a pass adds no evidence.
  - **R2 edges:**
    - Cluster windows and price warm-up must not read 2021, so the first event date is ≈ 2022-02-01 (30-day window and
      20-session warm-up from 2022-01-03).
    - Outcomes must not read 2026-11-02 onward, so with H = 60 the last entry is ≈ 2026-07-20, and with H = 20 it is
      ≈ 2026-09-17.
- **Options:**
  - **(a)** Development 2022-02-01 → 2024-06-28; confirmation 2024-07-01 → 2026-07-20. Both sealed windows untouched.
  - **(b)** As (a), plus `forward_2026q4` re-designated as a **second confirmation** for H1. This needs a recorded user
    decision and a ledger record. Its low power means it can only **veto** (for example, primary mean ≤ 0), not
    confirm. Results arrive after 2027-01-29, or about 2027-04 for H = 60.
  - **(c)** As (a), plus `legacy_2016_2021` run **once** at the end as a veto, under its existing role. It needs an
    owner-written unseal record in `holdout_ledger.jsonl`. It has the most power (MDE ≈ 2.4 pp), but S1 and STEP 0
    already touched it.
  - **(d)** Unseal `legacy_2016_2021` as development data (more power for development). This is not recommended,
    because the window was already used.
- **Recommendation:** **(a) + (c)**, keeping `forward_2026q4` sealed for a later decision. **(b)** is acceptable only
  as a veto-only check.

**E2. Frozen signal.**
- **Evidence:**
  - `2ffda0a` (2026-09-24 18:51 UTC) is the latest commit that changes the rule. Later commits change timing or text
    only (audit I1.7).
  - The thresholds may be overridden by repository variables that are not readable locally (audit I1.2).
  - The quality score uses a regime computed at run time from Yahoo (audit I1.3).
- **Options:**
  - **(a)** `2ffda0a`, raw cluster only, with the defaults 3 / $100K / 30 days, *after* the user confirms that the
    repository variables are unset or equal to them.
  - **(b)** As (a), plus quality score ≥ 70 as one pre-registered **secondary**. Its regime part needs a point-in-time
    reimplementation.
  - **(c)** As (a), with the actual repository-variable values if they differ.
- **Recommendation:** **(a)**, or **(c)** if the variables differ. The quality score is not primary.

**E3. Universe and liquidity floor.**
- **Evidence:**
  - In the 2024 Q1 sample, 784 of 978 purchase issuers have SIP bars. Of those, 193 have a median price under $5, 318
    a median dollar volume under $1M, and 100 under $100K.
  - At 09:35 the quoted spread has median 0.80% and p90 2.72%, and 24% of events have no quote (audit I4.1, I4.2).
- **Options:**
  - **(a)** No floor beyond exchange-listed, as the live rule does.
  - **(b)** Price ≥ $1, 20-day median dollar volume ≥ $100K, and quoted spread at entry ≤ 3%.
  - **(c)** Price ≥ $2, dollar volume ≥ $1M, spread ≤ 1%.
- **Recommendation:** **(b)** for the event study and **(c)** for the tradable L7 version, both fixed before any data
  is computed.

**E4. Primary horizon and gate threshold.**
- **Evidence:**
  - The journal follows up at 30, 90 and 180 calendar days.
  - STEP 0's 63-session SD is outlier-dominated.
  - The MDE at 21 sessions on confirmation is ≈ 4.8 pp (§3).
  - At $500 the round-trip commission is ≈ 0.14%, and the spread is the main cost (audit I4.2).
- **Options:**
  - **(a)** H = 20, a +2.0 pp mean with t ≥ 2.0.
  - **(b)** H = 20, t ≥ 3.0 with no magnitude floor.
  - **(c)** H = 60 primary, once the bad-price rule exists.
- **Recommendation:** **(a)**, with H = 5 and H = 60 as reported secondaries that carry no gate weight.
- **Note:** the confirmation MDE (≈ 4.8 pp) is larger than the +2.0 pp threshold. A true +2 pp effect would likely
  **fail** the t ≥ 2.0 condition. This trade-off is the user's to make (alternatives: lengthen the confirmation period
  or accept a lower t).

**E5. The live journal.**
- **Evidence:**
  - There are 106 alerts since 2026-09-24, 36 of them cluster-containing, and no follow-up filled yet.
  - The baseline is ambiguous for 16:30 and 19:30 New York alerts; prices are Yahoo closes; there are no fills; "big"
    alerts are mixed in (audit I2).
- **Options:**
  - **(a)** Ignore it.
  - **(b)** Use it only as the **parity oracle** (audit I5.1).
  - **(c)** Also re-score the cluster alerts from 2026-09-24 onward with the engine's own clock and L7, as an extra
    forward check that is descriptive only and has no gate weight. Its outcome period overlaps `forward_2026q4` from
    2026-11-02, so this falls under E1(b).
- **Recommendation:** **(b)** now. **(c)** only together with E1(b).

## 6. Consequences

- **Data, I1 phase (not started):**
  - SEC data sets 2022 Q1–2026 Q3;
  - acceptance headers for cluster-member filings;
  - Alpaca daily bars, corporate actions from 2022, and quotes at the open per event;
  - all under `SCALP_DATA_ROOT`, with the holdout guard on every request.
- **New contracts:** `insider_lines`, `insider_events`, `event_returns`, and a trials ledger. Any new dependency is
  justified in a follow-up ADR.
- **Unchanged:** `bot/`, the live scan and the journal.

## 7. Decision record (user, 2026-10-19; binding)

**E1. Windows.**
- **Development:** event dates (entry dates) 2022-02-01 → 2024-06-28.
- **Confirmation:** 2024-07-01 → 2026-07-20.
- **Post-S1 sub-window:** 2026-04-01 → 2026-07-20. It lies inside confirmation, is reported separately and carries no
  gate weight.
- **Overlap note (recorded as required).** Two earlier pieces of work already looked at insider data, so the
  confirmation window is **not fully untouched** for this signal family:
  - **STEP 0:** `bot/probe_insider.py`, 2016-01-04 → 2026-03-31. Dispersion only, signed returns blind.
  - **Ensemble S1:** signal dates 2017-01-03 → 2026-03-31. Its signed mean (−0.032) was observed.
  - Both keyed on filing date, without 4/A. S1 used ticket-style exits (I0 audit I1.8).
  - Only 2026-04-01 onward is unseen by both, hence the post-S1 sub-window.
- **`legacy_2016_2021`:** run **once**, as a veto, at the end of I2, under an unseal ledger record written by the owner
  at that time. It is not used in I1.
- **`forward_2026q4`:** stays sealed and unused.

**E2. Signal.**
- **Primary:** the raw **cluster** rule at stockbot commit `2ffda0a`.
- **Thresholds:** the repository variables `MIN_INSIDERS`, `MIN_CLUSTER_USD`, `MIN_SINGLE_USD` and `WINDOW_DAYS` were
  read on 2026-10-19 via `gh api repos/danielsuisa/stockbot/actions/variables`. They are **unset**; the only
  repository variable is `TICKET_RISK_PCT`. The two environments (`ensemble-verdict`, `index-verdict`) have no
  variables.
  - `daily-scan.yml:48-52` passes them as empty strings, and `bot/common.py:30-33` maps an empty value to the default.
  - **In force:** 3 insiders, $100,000 cluster total, 30-day window, $500,000 single purchase.
- **Secondary, no gate weight:** the **big** trigger, a single open-market purchase ≥ $500,000.
- The quality score is **not tested**.

**E3. Universes.** Both are evaluated point-in-time, from data available before the entry decision.

| Universe | Used for | Price | 20-day median dollar volume | Spread |
|---|---|---|---|---|
| (b) | Event study | ≥ $1 | ≥ $100K | ≤ 3% |
| (c) | Tradable version | ≥ $2 | ≥ $1M | ≤ 1% |

- **Price** is the last close before the entry decision, split-adjusted.
- **Dollar volume:** the median of close × volume over the 20 sessions ending at that close.
- **Spread:** the quoted spread, (ask − bid) / mid, from the SIP NBBO sample at the prior session's close − 5 minutes
  (ADR 0009).

**E4. Gate.** Applied **once** in I2, on the confirmation window. Both parts must pass.
- **(i) Significance.** At H = 20 sessions, the mean abnormal return of **cluster** events against the matched control,
  on universe (b), is > 0, with a clustered t ≥ 2.0 (§8).
- **(ii) Economics.** In the L7 tradable version on universe (c), the mean net return per trade is > 0, for a $500 cash
  account on IBKR Tiered (§8).
- **Reported only:**
  - H = 5 and H = 60;
  - the big trigger;
  - the post-S1 sub-window.
- **Legacy veto.** Statistic (i) is computed on 2016-01-01 → 2021-12-31. A mean abnormal return ≤ 0 blocks GO; a pass
  adds no evidence.
- This replaces the §4 draft: there is no +2.0 pp floor, no PF ≥ 1.2, and no 100-trade floor.

**E5. Journal.** The live journal (`data/journal.json`) is a **parity oracle only**. No journal returns are used.

## 8. I2 measurement spec (pre-registered; fixed before any data exists)

**8.1 Events entering the measurement.**
- Events come from the I1 event table (ADR 0009 §2): trigger `cluster` or `big`, with the rule version recorded.
- An event's **entry date** is the session of the first regular-session open strictly after `available_at`.
- **Overlap:** if an issuer has several events of the same trigger whose entry dates fall within H sessions of each
  other, only the **first** is kept, separately for each H. Dropped repeats are counted.
- **Universe:** an event is in universe (b) or (c) by its flags as of the entry decision (§7, E3).

**8.2 Returns.**
- **Total return** from the entry-day **open** to the **close of session H**, counting the entry day as session 1.
  - Splits are applied as price adjustments.
  - Cash dividends are reinvested at the ex-date close. They are taken from corporate actions (ADR 0009).
- **Delisting** before session H:
  - A merger with stated terms (cash and/or stock ratio from the corporate action) is valued at the deal value on the
    last trading day. A stock component is valued at the acquirer's close that day.
  - Any other delisting is valued at the last close × (1 − 0.30).
  - The position then holds that value as cash, earning 0, to session H.
- **Bad prices:** the rule is defined and tested in I1 (ADR 0009 §4). Flagged bars are excluded before any return is
  computed. An event or control whose path crosses a flagged bar is dropped and counted.

**8.3 Matched control.**
- For each event, draw **K = 5** stocks **without replacement** from universe (b) as of the event's entry decision,
  meeting all of:
  - the same **2-digit SIC** as the event issuer;
  - the same **dollar-volume decile**, with deciles computed over universe (b) members on that date;
  - no `cluster` or `big` event of their own within ±30 calendar days of the event's entry date;
  - not the event issuer.
- **Seed:** the integer of the first 8 bytes of SHA-256 of `f"{event_id}|i2-control-v1"`. The candidates are sorted by
  symbol and a seeded random sample is drawn.
- If fewer than K qualify, widen to the same **1-digit SIC**, keeping every other condition.
- If none qualify at 1 digit, drop the event and count it.
- If 1 ≤ n < K qualify at 1 digit, use the n that qualify, and report how often this happens.
- **Control returns** use the event's own entry and exit dates, the open-to-close definition, and the same delisting and
  bad-price rules.

**8.4 Abnormal return.** AR_e(H) = R_e(H) − mean over the event's controls of R_c(H).

**8.5 Inference.**
- Mean AR, with **standard errors clustered by the ISO calendar week of entry**.
- t = mean / clustered SE.
- Report the **minimum detectable effect** per window: 80% power, one-sided α = 0.05, (1.645 + 0.842) × SE.

**8.6 Tradable version (L7, evaluator side).**
- Each event is simulated on **its own $500 cash account**.
- **Shares** = floor(500 / entry price). If this is 0, the event is skipped and counted.
- **Entry** at the entry-day open + ½ spread, from the SIP NBBO sample at **09:35 ET** on the entry day.
- **Exit** at the session-H close − ½ spread, from the NBBO sample at **close − 5 minutes** on the exit day.
- **Costs:** IBKR **Tiered** with regulatory fees (`scalp_eval/costs.py`). Net return per trade = net P&L / $500.
- A missing quote sample on an entry or exit day drops the event from (ii), and the drop is counted.
- **Account-level simulation:** one $500 account, cash settlement T+1, no overlap. Reported only.

**8.7 Reported statistics.**
- Per window (development, confirmation, post-S1, and legacy for the veto): n, mean, median, hit rate (share > 0), and
  a bootstrap 95% CI. The bootstrap uses 10,000 resamples of entry-week blocks, seed `"i2-boot-v1"`.
- Per horizon H ∈ {5, 20, 60}, per trigger (cluster, big) and per universe.
- Counts of every drop: overlap, unmapped, no controls, bad price, missing quote, zero shares.

## 9. Amendments (user decisions 2026-10-20, recorded before any outcome exists)

These govern over §4, §7 and §8 where they differ. ADR 0010 holds the I2 implementation design.

**9.1 Gate (ii), economics: measured against SPY.**
- Gate (ii) is now the **mean over trades of (net trade return − SPY total return over the same entry-open →
  exit-close dates) > 0**: universe (c), cluster trigger, H = 20, a $500 cash account, IBKR Tiered.
- The absolute mean net return is reported only.
- *Reason:* an absolute-return gate credits market drift. A strategy could pass it while earning less than simply
  holding SPY.

**9.2 E1, the legacy veto, moves to phase I3.**
- I3 runs **only if the I2 gate passes**.
- If I2 fails, `legacy_2016_2021` stays sealed and nothing dated 2016–2021 is ingested.
- The veto's definition is unchanged: statistic (i) on 2016-01-01 → 2021-12-31; a mean abnormal return ≤ 0 blocks
  GO; a pass adds no evidence.
- `forward_2026q4` stays sealed and unused.

**9.3 Foreign private issuers are excluded from every primary statistic.**
- *Reason:* the live rule at `2ffda0a` never journals them (alerts are name-only); the I1 parity found 32 such cases.
- Their count is reported separately.

**9.4 The dual-CIK exclusion is kept.** The 130 events with a genuine CIK change on one ticker stay excluded and are
counted; they are not re-mapped.

**9.5 Verdict.**
- Both (i) and (ii) pass: **provisional GO, pending the I3 legacy veto**.
- Otherwise **NO-GO**, classified as:
  - (a) no abnormal return: (i) fails;
  - (b) abnormal return that is not tradable: (i) passes and (ii) fails.
