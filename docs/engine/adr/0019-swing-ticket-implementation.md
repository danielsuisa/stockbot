# ADR 0019: Swing-ticket implementation design (S1)

- **Status.**
  - **Accepted** for everything the S1 prompt (2026-10-21) specifies, marked **[A]**.
  - Every other choice is **Proposed**, marked **[P]**. It is fixed here, before any data or outcome, and the runs use
    it as written.
- **Supersedes** ADR 0014 on `engine/s1-hold` (the on-hold S1 of 2026-10-20, never pushed). Its text is the base of
  this ADR. Its code is ported, with the changes below.
- **Commit order:** committed before the first S1 ingestion request.
- **Context:**
  - [ADR 0013](0013-swing-ticket-validation-design.md) (Accepted, G1–G6 = (a));
  - [S0 audit](../S0-audit.md);
  - [ADR 0010](0010-i2-implementation.md) §2 (exit and delisting rules);
  - [ADR 0017](0017-t2-decisions.md) (the survivorship method);
  - [ADR 0018](0018-intraday-h1-ticket-line-closed.md).
- **Frozen rules [A].**
  - **What is hashed.** The ADR 0013 text from "**Frozen ticket rules (H1).**" up to, not including,
    "## 2. Measurement". It is read as UTF-8, with line endings normalized to LF.
  - **The hash.** SHA-256 = **`9f0475fad96c4f7c102d62ad450609b14ee6336f44f78105b0ff9ed950e7b80e`**, carried in
    every signed set.
  - **Test.** A test recomputes it from the ADR file.
  - **ADR 0014's value** (`23207f95…`) used an unrecorded boundary and is superseded.
- **No outcomes in S1.**
  - No fill, return, P&L or R is computed for any real ticket or control.
  - The evaluator runs on synthetic data only. Its CLI refuses real sets until an S2 ledger entry exists (§8).
- **"H1"** in this ADR is the **swing** H1 of ADR 0013: non-earnings gap-and-hold.

## 1. Amendments to ADR 0013 [A]

These were made by the orchestrator before any outcome existed. The hypothesis, universe rules, windows and gate are
unchanged.

**A1. Entry follows real stop-limit behavior.**
- Once the trigger trades, the order is a resting buy limit at `limit` until the close of t + 3.
- A session that opens above the limit does **not** skip the ticket. The ticket fills at the limit, or at a later
  session's open if that open is ≤ limit, as soon as any price ≤ limit occurs before expiry.
- *Reason:* skipping removes exactly the adverse pullback fills. P3D found that fill selection is where the intraday
  edge died.

**A2. The earnings exclusion is point-in-time to issuance.**
- The ticket is issued at t 20:00 ET.
- An H1 signal is excluded if an 8-K with Item 2.02 has a **header** acceptance time in [t − 1 16:00 ET, t 20:00 ET].
- The count of H1 signals with an Item 2.02 8-K accepted in (t 20:00 ET, t + 4 sessions] is **reported, not
  excluded**. It measures late-filing leakage.
- *Reason:* after-close earnings on t drive the trigger window, and the submissions-JSON time is filer-inconsistent
  (I1).

**A3. Foreign private issuers (20-F and 40-F filers) are excluded from the universe.** *Reason:* their earnings arrive
by 6-K and cannot be identified.

**A4. Exit prices.**
- **Stop:** min(stop, the open if the session gapped below) − ½ spread, using the latest close − 5 min sample before
  the exit.
- **Target:** filled at the target only when the high is **strictly above** it, with no spread cost.
- **Time exit:** the close − ½ spread, from that session's close − 5 min sample.
- **Fill:** the trigger, or the open if the open is above the trigger and ≤ limit, plus ½ spread from the fill
  session's 09:35 sample. Fallback: the prior session's close − 5 min sample.

**A5. Minute bars by a rule fixed before any data.**
- **Primary scope:** every session t + 1 … t + 22 for every H1 and S-b ticket and their controls.
- **Use:** the trigger and fill minute, the fill-session stop (counted only after the fill minute), and same-session
  stop/target conflicts.
- **Fallback, if the budget fails:** the expiry sessions t + 1 … t + 3 only, with stop first on later conflicts.
- **S-a** uses daily bars, with stop first throughout, and is reported with that caveat.
- **The decision rule (§2).**
  - The `plan` step computes the exact requests and disk from the signed ticket lists, after the build and before any
    outcome.
  - It records which scope is used, and the minute step refuses to run without that record.

**A6. Top-up.**
- Daily bars, corporate actions and close − 5 min samples from 2026-10-19 to the last complete session before the run.
  At the time of writing that is **2026-10-20**.
- Nothing is ever requested on or after 2026-11-02.
- **T1/T2 already hold:**
  - the calendar and corporate actions for 2026-10-19 → 2026-10-20 (part `actions-t1`);
  - daily bars for the intraday universe, the survivorship names and SPY.
- **S1 fetches only the missing daily keys.** These are pool symbols never requested for those sessions. A fetch keeps
  **only the symbols it requested** and drops any key a part already holds (the T2 AHPAW lesson).

**A7. Survivorship.**
- **The names.** The universe includes every ticker Alpaca's asset list omits that was acquired, worthless-removed or
  renamed away between 2022-01-03 and 2026-09-17.
  - The method is T2's: stored corporate actions read with P1's `top500_v3` rules, via
    `t1_data.survivor_names` / `universe_spans`.
  - Each name is valid only before its delisting or rename. Its bars dated on or after that date are dropped (ticker
    reuse).
- **Coverage before any signal.**
  - T2's daily coverage check (no universe or survivorship name left unrequested for any session range) must pass
    before `swing_signals` loads bars.
  - Measured on 2026-10-21:
    - 3,147 names, of which 2,228 have daily bars;
    - 7,628 bar rows were dropped by validity;
    - S-b signals rose from 3,934 to 4,249.
- *Reason:* Alpaca's asset list leaves acquired companies out entirely (P1, T2).
- The swing universe still requires an issuer CIK and SIC (ADR 0013 rule 1). A survivorship name without them stays
  out, and is counted.

**A8. Housekeeping.**
- **Push checks.** The T2 changes to `push_check.py` stay: the trials ledgers are exempt, and blobs already in
  `origin/main`'s history are not flagged. The S trials ledger is added to the exempt list.
- **Stockbot.** The gate item is **901/902**, with exactly `test_review.Signals.test_corrections_are_saved_before_alerts`
  failing. Engine branches predate [danielsuisa/stockbot#25](https://github.com/danielsuisa/stockbot/pull/25).

## 2. Data plan and budget [A, figures P]

**Counts on 2026-10-21, no request:**
- 4,249 S-b signals with the survivorship names, 2022-10-01 → 2026-09-17, one per symbol per 20 sessions;
- about 3,043 universe CIKs;
- 9.72 GB free on `D:`.

| # | Ingestion | Scope (a function of the rules, then of the tickets only) | Requests | Time | Disk |
|---|---|---|---|---|---|
| 1 | EDGAR submissions | Every universe CIK not already done (`cik_done` markers; 3,232 done on 2026-10-20). The recent block, plus older pages overlapping 2022-01-03 → 2026-10-20 | ≤ 4,000 | ≤ 10 min at 8/s | < 0.2 GB |
| 2 | SEC headers (`ACCEPTANCE-DATETIME`) | Every 8-K or 8-K/A with Item 2.02 of a universe CIK, filed 2022-09-01 → 2026-10-20, not already held (25 held). Target: ≥ 99% with a time | ≤ 45,000 | ≤ 1.7 h | < 0.1 GB |
| 3 | A6 top-up | Daily bars 2026-10-19 → 20 for pool symbols missing them; close − 5 min samples on both sessions (pre-filter ∪ ticket symbols) | ≈ 60 | minutes | small |
| 4 | Minute bars (A5) | (symbol, session) pairs of the chosen scope, regular session 09:30–16:00 ET [P]. Pairs the store fully holds are skipped (P1's full-session parts; T1's stage-1 + top-20 parts together). Held keys are dropped on write | primary ≈ 15,000; fallback ≈ 2,500 | primary ≈ 1.5 h | primary ≈ 1.8–2.8 GB; fallback ≈ 0.3 GB |
| 5 | 09:35 NBBO samples | (symbol, session) for t + 1 … t + 3 of every H1 and S-b ticket and its controls, not already held | ≈ 6,000 | ≈ 0.6 h | small |

- **The budget rule [A].** Proceed with the primary scope only if all fetches need ≤ 24 h and leave ≥ 8 GB free.
  Otherwise use the A5 fallback and re-check. If that still fails, stop and report.
  - **Expectation at writing:** the primary scope would leave about 7–7.9 GB free, below the floor, so the
    **fallback is the likely outcome**.
  - The `plan` step decides on the exact figures, at 19.3 bytes per minute row (measured in T1).
- **Mechanics:**
  - P1 conventions: append-only, a manifest per request, `check_range` before every request, resumable.
  - SEC at 8 requests a second with `SEC_USER_AGENT`; Alpaca at 170 a minute.
  - Credentials through `envload` only.
- **Tables:**
  - `issuer_filings`: forms 8-K, 8-K/A, 10-K, 10-K/A, 20-F, 20-F/A, 40-F and 40-F/A per CIK, with accession, filing
    date, items and the JSON acceptance value. Rows filed before 2022-01-03 are dropped at parse.
  - `filing_acceptance`: the headers.
  - `swing_tickets`.
  - New parts: `raw_bars_1m` (`sip-1m-s1-v1`), `raw_bars_1d` (`sip-1d-s1t`) and `raw_quote_samples` (`s1-open-v1`,
    `s1-close-t1`).
- **Duplicates.** The I1 boundary constants are unchanged, and the top-up uses its own range. The store-wide duplicate
  check (T1/T2 `dupcheck`) runs after ingestion and must show 0.

## 3. Signal clock and universe

- **[A] Clock.** Signals use only data dated ≤ t. Tickets are issued at **t 20:00 ET**.
- **[P] Daily bar t** (regular-session OHLCV, delayed SIP) is available at t 16:15 ET.
- **[P] Inputs and their `available_at`:**
  - daily bars ≤ t;
  - the 8-K header acceptance ≤ t 20:00 ET;
  - filed-symbol observations (Form 4 issuer symbol) with filing date ≤ t;
  - annual-report filings with filing date ≤ t;
  - the current SIC, which is not point-in-time (as ADR 0009 §6).
- **Universe on t** (ADR 0013 §1 rule 1, G2 (a), A3, A7):
  - close_t ≥ $5;
  - the 20-session median of close × volume ending t ≥ $10M, a session without a bar counting 0;
  - a CIK from the latest filed-symbol observation ≤ t within 400 days [P];
  - a SIC that is not null, not `0000`, and **not a fund class** (6221, 6722, 6726) [P];
  - **A3:** the latest annual report filed on or before t is not a 20-F or 40-F. With none on file, the issuer counts as
    domestic, and that is counted [P];
  - **A7:** a survivorship name only before its delisting or rename.
- **[P] ATR14_t** = the mean of the true range over the 14 sessions ending t. TR = max(H − L, |H − C₋₁|, |L − C₋₁|).
- **Signals** (ADR 0013 §1), on 2022-10-01 → 2026-09-17:
  - **H1:** gap and hold (open_t ≥ 1.03·high_{t−1}, close_t ≥ open_t, close_t − low_t ≥ 0.70·range_t), plus the A2
    exclusion;
  - **S-b:** H1 without the earnings rule;
  - **S-a:** close_t > the max high of t − 20 … t − 1, close_t > SMA200_t, SMA50_t > SMA200_t;
  - one live ticket per symbol per set: no signal within 20 sessions of that set's last signal for the symbol.
- **Windows by t:** development **2022-10-01 → 2024-12-31**; confirmation **2025-01-01 → 2026-09-17**.
- **Tickets** (ADR 0013 §1 rule 5):
  - trigger = high_t + $0.01; limit = trigger + 0.25·ATR14_t; expiry t + 1 … t + 3;
  - stop: **H1 and S-b** max(low_t, trigger − 2·ATR14_t) − $0.01; **S-a** trigger − 2·ATR14_t;
  - R = trigger − stop; target = trigger + 2R;
  - time exit: the close of the 20th session counted from the fill session;
  - qty = min(⌊25 / R⌋, ⌊1,000 / limit⌋) [P: the 40% cap uses the limit price]; **skipped and counted if 0**;
  - every price rounded to $0.01, half up [P].
- **[P] `ticket_id`** = `f"{family}|{symbol}|{t}"`.
  - H1 and S-b share the family `gaphold`, so each H1 ticket has the same id, and the same controls, as its S-b twin.
  - S-a uses the family `nday`.

## 4. Controls [A as ADR 0013 §2; details P]

- **K = 3 per ticket**, seeded by SHA-256 of `f"{ticket_id}|s1-control-v1"` (the first 8 bytes as the
  `random.Random` seed).
- **Candidates:**
  - universe members on the same t (§3), excluding the ticket's symbol;
  - excluding any symbol with a signal of the **same family** on t;
  - for `gaphold`, also excluding any symbol with an A2 earnings 8-K in the same window [P].
- **The draw.** Candidates are sorted by symbol, then shuffled with `random.Random(seed)`. The walk takes the first K
  whose ticket has qty > 0. That ticket uses identical geometry, built from the control's own high_t, low_t and
  ATR14_t.
- **Control id** = `f"{ticket_id}|c{k}"`.

## 5. Evaluator (`scalp_eval/swing.py`) [A from ADR 0013 §2 + A1/A4; details P]

**Entry over expiry sessions e1 … e3 = t + 1 … t + 3.**
- **The trigger session:** the first session j with high_j ≥ trigger (with minute bars, the first minute with high ≥
  trigger).
- **open_j ≥ trigger and ≤ limit:** fill at open_j, plus ½ the 09:35 spread, capped at the limit [P].
- **open_j < trigger:** fill at the trigger, plus ½ spread, capped at the limit [P].
- **open_j > limit (A1):** triggered, and the limit rests.
  - Fill at the limit if any later price in session j is ≤ limit (with minute bars, the first such minute).
  - Otherwise, in sessions k ≤ t + 3: at open_k if open_k ≤ limit (plus ½ spread, capped at the limit), else at the
    limit if low_k ≤ limit.
  - Otherwise the ticket is **unfilled**.
- **[P]** A resting-limit fill is at the limit, with no spread added.

**The fill session.**
- With minute bars, stop and target count only on minutes after the fill minute.
- Without minute bars, **stop first**: if low ≤ stop the ticket is stopped that session; otherwise target if
  high > target.

**Holding sessions.** The fill session is session 1; the time exit is at the close of session 20.
- a gap below the stop exits at the open, minus ½ spread;
- a gap above the target exits at the open, with no spread [P];
- low ≤ stop → stop exit;
- high > target (strictly) → target exit;
- both in one session: minute bars decide the first occurrence; without them, stop first. The same minute → stop
  first.

**[P] Spreads.**
- "The latest close − 5 min sample before the exit" means:
  - the previous session's sample for intraday stops and gaps;
  - the session's own sample for time exits.
- A missing sample falls back one more session. If it is still missing, the spread is the median spread of the
  ticket's G2 tier (S0 S6).

**Corporate actions** (ADR 0010 §2, adapted):
- a split or stock dividend with ex-date after the fill multiplies the shares and divides the stop and target;
- cash dividends are paid as cash;
- a merger or other delisting before the exit values the position at the deal value, or at the last close × 0.70, on
  that date, with no spread and no sell cost.

**Costs:** IBKR **Tiered** with fees (`scalp_eval/costs.order_cost`) on the buy and the sell. **[P]** The target fill
is a limit sell, charged Tiered as well.

**Net R** = net $P&L / $25. **Unfilled tickets: 0R, counted.**

**Book:**
- $2,500 cash, T+1 settlement (sale proceeds are usable from the next session), at most 4 open positions;
- a position is at most 40% of current equity; if not, qty is scaled down to ⌊0.4·equity / fill⌋ [P];
- same-session fills are ranked by seeded order (SHA-256 of `f"{ticket_id}|s2-book-v1"`);
- a ticket is skipped if its cash is unsettled, 4 positions are open, or the scaled qty is 0;
- idle cash earns **0%**;
- the comparison is SPY total return over the book's first fill → last exit.

## 6. Inference [A]

- **Gate (i):** mean net R per **filled** H1 ticket, with SE clustered by **ISO week of the fill** (CR1,
  G/(G − 1)).
  - t = mean / SE; realized **MDE = 2.8416·SE**.
  - Week-block bootstrap: 10,000 draws, seed `"s2-boot-v1"`.
- **Gate (ii) [P]:** per **issued** H1 ticket, (ticket net R − the mean of its controls' net R), with unfilled = 0R on
  both sides (as ADR 0017's issued basis). Clustered by the ISO week of t. The filled basis is reported only.
- **Reported:** fill rate, mean, median, hit rate, the CI, and the exit-case counts, per window, set and horizon.

## 7. Signed inputs [A]

- The engine writes `swing_tickets` (sets H1, S-a, S-b and controls) and one Ed25519-signed digest per (set, window).
- **[P] Digest payload:** `{"kind": "swing_ticket_set", "table": "swing_tickets", "rule_version": "adr0019-s1",
  "rows", "sha256", "set", "window", "rules_sha256"}`, signed under the domain `scalp-engine/artifact/v1`.
- `scalp_eval` verifies the signature, the row hash and `rules_sha256` before use.
- **Leakage tests:**
  - **Future perturbation:** altering or deleting any bar dated after t leaves every signal and ticket at t unchanged.
  - **Clock:** every input row has `available_at` ≤ t 20:00 ET.

## 8. Trials ledger and the S2 guard [A]

- **The ledger:** `scalp_contracts/s_trials.jsonl`, append-only and tracked, like T's ledger. One record per run:
  commit, inputs' hashes, outputs' hashes and times.
- Development reruns are allowed only for a documented defect, with a failing test first. **One confirmation run.**
- **Guard:**
  - `python -m scalp_eval.swing` refuses any real (signed) ticket set unless the ledger holds a
    `{"phase": "S2", "kind": "authorize"}` record.
  - That record is written at the start of S2, not in S1.
  - S1 creates the ledger empty.

## 9. Golden set [A]

At least 18 hand-computed cases, which the evaluator must reproduce exactly:
1. a trigger touched intraday;
2. an open above the trigger and ≤ limit;
3. an open above the limit, then a pullback fill (A1);
4. an open above the limit and never ≤ limit (unfilled);
5. expiry without a touch;
6. a fill-session stop with minute bars;
7. a fill-session stop without minute bars;
8. a same-session conflict resolved by minute bars;
9. a same-session conflict with stop first;
10. a target touched but not traded through;
11. a gap below the stop;
12. a gap above the target;
13. a time exit;
14. a split during the hold;
15. a dividend;
16. a cash merger;
17. an other delisting;
18. the 40% cap;
19. a 0-share skip;
20. a book blocked by T+1;
21. a book at its 4-position cap.
