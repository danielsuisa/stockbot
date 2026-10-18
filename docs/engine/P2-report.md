# P2 report: L1 features, regime and L2 rule-based screener (2026-10-18)

- **Branch:** `engine/p2`, made from `engine/p1`. Worktree: `D:\investor\.claude\worktrees\engine-p2`.
- **Data:** under `D:\scalp-data`, outside git.
- **Spec:** [ADR 0003](adr/0003-features-regime-screener.md).

**P2 gate: FAIL.** The primary ratio is **R = 1.43**, below the required 1.5. As instructed, nothing was adjusted or
re-measured. Every other gate item passes.

## Findings (new)

| # | Finding | Evidence | Impact | Recommendation |
|---|---|---|---|---|
| F1 | The IEX-live screener selects stocks that move 1.43× more than random members over the next 90 min. That is below the 1.5 gate | ADR 0003 §9 measurement, run once on c37f9e3 | **Gate FAIL** | Do not tune. The P2 gate is a property of the L2 table as written. See decisions |
| F2 | Most of the lift is volatility persistence | ATR-decile-matched ratio: 1.28 (IEX), 1.34 (SIP) | The RVOL, spread and dilution filters add about 1.1× beyond ATR | Any redesign should be judged on the ATR-matched ratio |
| F3 | The edge decays over time: per-quarter R falls from about 1.5–1.6 in 2023–24 to about 1.2–1.35 in 2026 | Per-quarter table below | Out-of-period reliability is weaker than the pooled figure | Track per-quarter in every later measurement |
| F4 | IEX-live has no candidate in 23.4% of snapshots. The SIP variant has none in 1.3% | Candidate counts | IEX pre-market volume is too thin for pre-market RVOL > 2 to fire | Live feed: compare in P3 as planned (ADR 0002 §3) |
| F5 | Bar-based spread estimators track the real NBBO poorly. Out of sample, IEX bars: Abdi–Ranaldo median absolute error 6.29 bps, Spearman 0.09, filter agreement 61%. Both estimators underestimate | Spread evaluation, 19.6M rows | Before 09:45, the spread filter rests on a weak proxy | Use them only as a fallback. The delayed NBBO is used from 09:45 |
| F6 | EDGAR's `acceptanceDateTime` is not UTC: it is ET + 8 h under EDT and ET + 10 h under EST | 12 earnings 8-Ks checked against the filing headers | Would have shifted every catalyst and dilution timestamp | Corrected in code; the vectors are pinned in a test |
| F7 | Leakage test 1 caught a research-only real-time NBBO spread stored in the live rows. No filter used it | First run of the test | A latent leak for later phases | Fixed before the build: the column is null in live rows |
| F8 | The 424B-family dilution rule flags issuers of structured notes (large banks). About 6% of member-snapshots carry `dilution = true` | 1,073 of 18,500 rows on 2024-04-17 | Banks are mostly excluded by the rule as specified | See decision 3 |
| F9 | Full-text-search CIK matching by frequency made 7 clear errors out of 25, for example X → a CMBS trust | Name check | Wrong filings would have been attached | Fixed: a frequency match now needs a Form 25 or 15 within 45 days of the delisting; v1 and v2 of the map are superseded |
| F10 | BLS rejects requests without a contact User-Agent | 403 with a plain UA | `SEC_USER_AGENT` is also sent to bls.gov | See decision 4 |

## Implementation

**Stage 0:**
- **0.1:** local `main` fast-forwarded 450 commits to 1939f52. The untracked files `push-squeeze.cmd` and
  `squeeze-screener.bundle` were left untouched.
- **0.2:** the legacy holdout window starts on calendar date 2016-01-01; ADR 0002 is amended and the holdout file
  hash is pinned in tests.
- **0.3:** the last quote sample is now the session close − 5 min (`close_minus_5m_v2`). The 7 early-close days were
  re-sampled into an appended part (424 requests, 7.5 min). Quote figures in P1-data-quality are updated: 0
  `after_close`, 0.60% missing. The replay hash of 2024-11-29 changes accordingly, because its samples changed.
- **0.4:** skipped. The run started before the 2026-10-30 extended session had closed.
- **0.5:** see the backup section below.

**Proxies:**

| Source | Result |
|---|---|
| Cboe VIX | 873 sessions |
| Macro releases (federalreserve.gov, bls.gov) | FOMC 28, CPI 40, NFP 40 |
| EDGAR submissions | 624,983 filings for 1,045 CIKs (1,150 requests, 4.5 min) |
| SIP daily bars for the 14 market-set ETFs | 39 requests |

Whole-document sources drop out-of-range rows at parse time (ADR 0003 §0.2, Proposed).

**EDGAR coverage of the 1,074 members:** 1,070 have a CIK.

| Group | Members | Matched by |
|---|---|---|
| Current tickers | 975 | SEC company tickers file |
| Pre-rename tickers | 19 | 18 via the renamed company, 1 via company tickers |
| Delisted or inactive | **71 of 83 (85.5%)** | 52 exact full-text matches, 17 frequency matches that passed the Form 25/15 check, 2 via company tickers |
| Unknown | 12 | 8 rejected, 4 with no match |

**Live profile, verified:**
- Alpaca's FAQ (https://docs.alpaca.markets/us/docs/market-data-faq) allows historical SIP queries without a
  subscription when the `end` parameter is at least 15 minutes old. Live data on the free offering is IEX only.
- A `v2/delayed_sip` stream is documented (https://docs.alpaca.markets/docs/real-time-stock-pricing-data). Which
  plans include it is not stated.
- So SIP features are live-eligible only when `available_at` is at least the event end + 15 min.

**Features:**
- 23 registered features (`registry_p2.json`; id = name@semver+code hash).
- Daily features are split-adjusted as of *d*. Snapshot features use as-of joins on bar open + 62 s.
- Sector assignment is by trailing 60-session correlation with the SPDR sector ETFs.
- The regime uses rules only.
- The screener is the L2 table unchanged, run at 37 snapshots per session over 784 sessions, both variants: 29,008
  snapshots per variant and 1.07M member-rows.
- Output: `screener_snapshots` (1.1 GB) and `feature_daily` (16 MB).

**Spread estimators.** The choice rule was fixed in ADR 0003 §3: the train split's IEX median absolute error decides.
**The choice is Abdi–Ranaldo.**

| Split | Feed | CS median abs. error | AR median abs. error | CS / AR Spearman | CS / AR filter agreement |
|---|---|---|---|---|---|
| Train 2023-09..2024-12 | IEX | 5.23 bps | **4.21 bps** | −0.12 / 0.08 | 74.7% / 75.2% |
| Train | SIP | 3.92 | 4.09 | 0.08 / 0.32 | 70.6% / 73.9% |
| **Test 2025-01..2026-10** | IEX | 7.38 | **6.29** | −0.24 / 0.09 | 58.1% / 61.4% |
| Test | SIP | 6.04 | 6.17 | 0.08 / 0.33 | 55.8% / 63.1% |

**Leakage tests:**

| Test | Result |
|---|---|
| 1. Future perturbation: 3 seeded random sessions × 6 times × 2 variants, every feature compared, plus daily, VIX, split and sector perturbations | **Pass** |
| 2. Stored availability: every `screener_snapshots` and `feature_daily` row has inputs available at or before its decision time | **Pass**: 0 violations in 2.15M snapshot rows and the daily rows |
| 3. Replay parity: the three P1 days, all 37 times, both variants | **Pass** |

**Tests:**

| Suite | Result |
|---|---|
| `scalp_engine` | **95 OK**, 0 skipped |
| `scalp_eval` | **52 OK** |
| stockbot | **902 OK** (same command as P1) |

**Measurement:**
- Run once on commit c37f9e3, 2026-10-18T13:46:18Z–13:51:24Z.
- The spec was committed in 36902f5 at 2026-10-18T11:35:16Z, and the measurement code in c37f9e3 at 12:58:23Z.

| | IEX-live (primary) | SIP research |
|---|---|---|
| **R, 90 min** | **1.4304** | 1.5344 |
| Median range: candidates / random | 1.514% / 1.059% | 2.034% / 1.325% |
| R, ATR-matched | 1.2840 | 1.3445 |
| R to the close (secondary) | 1.3754 | 1.4602 |
| R to the close, ATR-matched | 1.2357 | 1.2881 |
| Pairs: candidates / random / matched | 229,409 / 227,500 / 228,710 | 299,994 / 276,690 / 282,181 |
| Dropped pairs (no decision price or no forward bar): candidates / random / matched | 0 / 1,909 / 699 | 13 / 23,317 / 17,826 |
| Candidates per snapshot: mean, p10, p25, p50, p75, p90, max | 7.9, 0, 1, 6, 13, 19, 123 | 10.3, 3, 5, 9, 13, 18, 108 |
| Zero-candidate snapshots | 6,797 of 29,008 (23.4%) | 363 (1.3%) |

**R per quarter:**

| Quarter | 23Q3 | 23Q4 | 24Q1 | 24Q2 | 24Q3 | 24Q4 | 25Q1 | 25Q2 | 25Q3 | 25Q4 | 26Q1 | 26Q2 | 26Q3 | 26Q4 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| IEX-live | 1.48 | 1.46 | 1.63 | 1.53 | 1.42 | 1.51 | 1.38 | 1.35 | 1.64 | 1.41 | 1.35 | 1.27 | 1.22 | 1.35 |
| SIP | 1.61 | 1.67 | 1.87 | 1.81 | 1.53 | 1.64 | 1.50 | 1.41 | 1.75 | 1.48 | 1.40 | 1.34 | 1.27 | 1.39 |

**Disk and run time:**
- **Disk:** the data root grew from 5.5 GB to 6.9 GB; D: has 15 GB free, so the 8 GB floor held.
- **Run time:** about 1.6 h of compute in total.

| Step | Time |
|---|---|
| Re-sample | 7.5 min |
| Proxies | about 8 min |
| Spread evaluation | 36 min |
| Screener build | 39 min |
| Leakage and full suite | 8 min |
| Measurement | 5 min |

**Regressions:** none.

**Commits:**
- 2dc6f5c `fix: P2 prerequisites (holdout start, early-close samples, top-up)`
- 36902f5 `docs: ADR 0003 + pre-registered P2 measurement`
- f52d601 `feat(L0): VIX, EDGAR and macro-calendar ingestion`
- 8ded87a `feat(L1): feature registry, features, regime + leakage tests`
- 8235620 `feat(L2): screener + snapshot contract`
- c37f9e3 `feat(eval): P2 screener measurement`
- `docs: P2 report` (this commit)

**Backup pushes (0.5):**
- `engine/p0` and `engine/p1`:
  - **Workflows:** none would trigger. The checker was self-tested on known cases.
  - **Secrets:** 0 Alpaca or email hits in 35 and 81 new blobs.
  - **Data files:** none.
  - **Pushed.** No workflow run was created.
- `engine/p2`: the result is reported in the final message, after this commit.

## Gate

| Criterion | Result |
|---|---|
| 0.2 and 0.3 complete; engine, eval and stockbot suites pass (stockbot at 902) | **PASS** |
| All three leakage tests pass | **PASS** |
| Spread estimator chosen on the fixed split; out-of-sample error reported | **PASS**: Abdi–Ranaldo; IEX test median abs. error 6.29 bps |
| Spec committed before the measurement code ran | **PASS**: 36902f5 (11:35:16Z) → c37f9e3 (12:58:23Z) → run at 13:46:18Z |
| **Primary ratio ≥ 1.5** | **FAIL: 1.4304** |
| Disk ≥ 8 GB free; no data files in git; diff only in allowed paths | **PASS**: 15 GB free. `git ls-files` shows only the empty ledger. `engine/p1...engine/p2` touches 33 files, all in the allowed paths |

**P2 gate: FAIL** on the primary ratio. Per the run rules, nothing was changed after the measurement.

## Decisions needed

1. **Screener next step.** All options need a new pre-registration and a new, untouched test period. The 2023-09..2026-10
   window is now used for the screener.
   - (a) Accept L2 as a minimum filter only, as ARCHITECTURE L2 already intends, and let P4's LambdaRank learn the
     ranking. Whether P3 may start on a screener that failed P2 is your call.
   - (b) Redesign the rules (for example IEX-specific RVOL baselines or a regular-hours-only start) and pre-register a
     new gate.
   - (c) Revisit the gate threshold itself.
   - *My recommendation: (a), with the gate outcome recorded as a known weakness. I do not recommend (c).*
2. **Proposed items in ADR 0003** that need your acceptance:
   - parse-time dropping for whole-document sources;
   - the `public_realtime` and `public_eod` profiles;
   - dilution `unknown` passes;
   - the full-text-search CIK mapping, with the Form 25/15 check.
   - *My recommendation: accept all four.*
3. **Dilution rule and banks (F8).**
   - (a) Keep the 424B-family rule literally.
   - (b) Exclude 424B2 (structured-note supplements).
   - *My recommendation: decide before P3; (b) would need a new pre-registration.*
4. **BLS identity (F10).** Allow `SEC_USER_AGENT` to be sent to bls.gov, or provide a separate `BLS_USER_AGENT`.
5. **Credentials.** The Alpaca keys still come from the values you pasted on 2026-10-17; no env vars are set.
   - *My recommendation: rotate the keys, and set `ALPACA_KEY_ID`, `ALPACA_SECRET_KEY` and `SEC_USER_AGENT` as User
     environment variables.*

## Next step: P3 prerequisites only (P3 not started)

1. Decision 1. Under the phase rules, P3 does not start on a failed P2 gate without your explicit go.
2. Decisions 2–5.
3. A P3 ADR, covering:
   - the triple-barrier label parameters (k, m, H);
   - purged K-fold with a one-day embargo;
   - the IEX-vs-SIP feature comparison;
   - the cost model;
   - a new untouched evaluation period that respects both holdout windows.
