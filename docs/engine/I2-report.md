# I2 report: I1 remedy + pre-registered insider measurement

Branch `engine/i2` (from `engine/i1` 28a47e9). Date 2026-10-20.

## Verdict: **NO-GO, class (a): no abnormal return**

Gate (i) fails on the confirmation window: mean abnormal return +1.08%, but clustered t = 1.61 < 2.0. Gate (ii) also
fails: mean excess over SPY −0.14%.

Per ADR 0008 §9.2, **I3 does not run**, `legacy_2016_2021` stays sealed, and nothing dated 2016–2021 is ingested.
`forward_2026q4` stayed sealed and unused.

## Stage 0: I1 remedy (details in [I1-addendum.md](I1-addendum.md))

- **Cause:** `P1_LAST` was 2026-09-30 in I1, but P1's bars run to 2026-10-16. It is now fixed and guarded by a test
  and a manifest check.
- **Remedy:** 12 bar files and 7 early-close quote files were set aside (`.orphan`). The keys P1 lacked were rewritten
  as new parts: 1,042 bar rows and 28,847 quote rows. Every coverage assertion held.
- **Re-check:** **0 duplicate keys** across 10 tables. The universe flags (`fe84a79c…`) and the event table
  (`31e3fad2…`) rebuilt from the repaired store hash exactly as in I1. **The I1 gate item now passes.**

## Gate statistics (confirmation window 2024-07-01 → 2026-07-20, one run)

**(i) Event study.** Cluster trigger, universe (b), non-FPI, after overlap, H = 20, against matched controls:

| n | Mean AR | Clustered SE (CR1) | t | Bootstrap 95% CI | Median | Hit rate | Realized MDE (2.8416·SE) | I1 MDE estimate |
|---|---|---|---|---|---|---|---|---|
| 721 | **+1.08%** | 0.67% | **1.61** | [−0.22%, +2.43%] | +0.06% | 50.2% | 1.91% | 1.88% |

**(ii) L7 tradable version.** Cluster trigger, universe (c), H = 20, $500 Tiered, net trade return minus SPY over
the same dates:

| n | **Mean (net − SPY)** | SE | t | Bootstrap 95% CI | Mean absolute net return (reported only) | Cost per trade |
|---|---|---|---|---|---|---|
| 626 | **−0.14%** | 0.72% | −0.20 | [−1.59%, +1.23%] | +0.64% | 0.21% of $500 |

## Results by window × horizon × trigger

Event study: universe (b), mean AR (t) [n].

| | Development (no gate weight) | Confirmation | Post-S1 (2026-04-01 → 07-20, no gate weight) |
|---|---|---|---|
| cluster H5 | +1.06% (1.86) [1,538] | +0.46% (1.67) [946] | −0.18% (−0.29) [140] |
| **cluster H20** | +1.22% (1.29) [1,096] | **+1.08% (1.61) [721]** | +1.04% (0.55) [103] |
| cluster H60 | +2.93% (1.89) [1,008] | +0.10% (0.08) [677] | −0.81% (−0.45) [93] |
| big H5 | +0.61% (2.93) [2,245] | +0.83% (2.65) [1,690] | −0.08% (−0.08) [238] |
| big H20 | +0.60% (1.46) [1,825] | +0.70% (1.35) [1,412] | −0.04% (−0.02) [192] |
| big H60 | −0.38% (−0.53) [1,592] | +1.31% (1.35) [1,230] | +2.21% (1.28) [157] |

L7: universe (c), mean (net − SPY) (t) [n]; the mean absolute net return in brackets after it.

| | Development | Confirmation | Post-S1 |
|---|---|---|---|
| cluster H5 | −0.44% (−1.38) [1,060] {net −0.00%} | −0.04% (−0.11) [812] {net +0.14%} | +0.21% (0.22) [121] |
| **cluster H20** | −0.66% (−0.97) [772] {net +0.43%} | **−0.14% (−0.20) [626] {net +0.64%}** | +2.37% (1.36) [89] |
| cluster H60 | +0.41% (0.34) [715] {net +3.76%} | −0.38% (−0.36) [584] {net +4.74%} | +3.30% (1.68) [79] |
| big H5 | −0.58% (−2.44) [2,011] | −0.30% (−1.13) [1,551] | −0.67% (−0.79) [217] |
| big H20 | −1.05% (−2.33) [1,565] | −0.13% (−0.26) [1,260] | +0.56% (0.36) [173] |
| big H60 | −1.77% (−1.86) [1,342] | −0.29% (−0.29) [1,100] | +2.97% (2.00) [143] |

The cluster signal is positive in point estimate at H = 20 in both windows (about +1.1–1.2%), but significant in
neither. The big trigger at H = 5 clears t ≥ 2 in both windows, but it is a reported-only secondary with no gate
weight, and its L7 version loses to SPY in both.

## Drops and cases (confirmation, gate (i) population: cluster, (b), H = 20)

- **Population:** 2,182 cluster events in (b) in the window, non-FPI (137 foreign private issuers excluded, 74 for
  big) → 1,396 dropped by the overlap rule → 786.
- **Then:** 65 with no SIC (closed-end funds and BDCs without an EDGAR SIC) → **721 measured**.
  - 0 entry shifts and 0 entry drops.
  - 1 control redraw (no open on day 1).
- **Controls:** 560 events matched at SIC2 and 161 widened to SIC1; 1 had fewer than K = 5.
- **Exit cases:**

  | | Close | Cash merger | Stock merger | Merger, no acquirer | Other delisting | Gap |
  |---|---|---|---|---|---|---|
  | Events | 721 | 0 | 0 | 0 | 0 | 0 |
  | Controls | 3,582 | 8 | 4 | 0 | 7 | 0 |

- **L7 (cluster, (c), H = 20):** 1,746 → overlap 1,116 dropped → 630 → 4 with zero shares (price > $500) →
  **626 trades**. Exit cases: 626 closes; no quote drops.
- The excluded **dual-CIK events (130)** never enter the populations: they are unmapped.

## Account-level simulation (reported only; cluster, (c), H = 20; $500 cash, T+1, one position)

| Window | Trades | Skipped (busy or unsettled) | Final equity | Return | SPY buy-and-hold over the window |
|---|---|---|---|---|---|
| Development | 29 | 743 | $327.45 | −34.5% | +20.8% |
| Confirmation | 25 | 601 | $274.96 | −45.0% | +36.0% |

## Process gate

| Item | Result |
|---|---|
| Stage 0 complete: 0 duplicates, event-table hash unchanged | **PASS** |
| ADR 0010 and the evaluator committed before the first outcome | **PASS.** ADR 0010 + ADR 0008 amendments 478d0d5 at 06:10:05Z; inputs 1c42beb at 06:11Z; evaluator c6d3ae8 at 06:18:51Z; **first outcome** (development run start) at **06:18:55Z** |
| Confirmation run exactly once | **PASS.** The ledger holds one; the CLI refuses a second |
| Golden set (15 hand-computed cases), unit tests, R4 static tests | **PASS** |
| Engine / eval / stockbot suites | **PASS.** Engine 159/159, eval 94/94, stockbot 902/902 |
| Data files, paths, disk, push checks | **PASS.** No data files tracked (only the empty `holdout_ledger.jsonl`). Changes only in `docs/engine/`, `scalp_engine/`, `scalp_eval/` and `scalp_contracts/`. `bot/` and `.github/` are identical to `origin/main`, and `data/` is unchanged. D: has 12 GB free. Push checks: see Pushes |

**Trials ledger** (`D:\scalp-data\derived\trials_i2.jsonl`):

| run_id | Window | Commit | Start → end (UTC) | Output SHA-256 |
|---|---|---|---|---|
| i2-development-20261020T061855Z | development | c6d3ae8 | 06:18:55 → 06:19:23 | `6ccf2e33…` |
| i2-confirmation-20261020T061943Z | confirmation | c6d3ae8 | 06:19:43 → 06:20:11 | `2575e380…` |

- Both runs share the inputs: the event set `31e3fad2…` (signature verified) and the universe digest `e0770911…`.
- There was no development rerun: the pipeline check found no defect.

**Commits:**
1. e0829d1 remedy
2. 590f37b addendum
3. 478d0d5 ADR 0010
4. 1c42beb inputs
5. c6d3ae8 evaluator
6. this report

**Run facts:**
- no vendor request;
- universe table 5,125,859 rows (4,790,773 in universe (b)) over 1,119 sessions;
- each run took 28 s;
- `D:\scalp-data` 10 GB → 11 GB (universe table); D: has 12 GB free (floor 8 GB).

**Pushes:** `engine/i2` → origin, run under the P2 0.5 checks with credentials from the user environment (`envload`). The outcome is in the final message of this session.

## Decisions needed

1. **Close the insider-signal line.** *Recommended: yes.*
   - The frozen live rule's cluster signal shows no significant abnormal return on the pre-registered confirmation
     window.
   - Its tradable version does not beat SPY.
   - Per ADR 0008 §9.2, I3 and the legacy veto do not run; `legacy_2016_2021` stays sealed.
2. **The live stockbot insider alerts** (they are live and unchanged). *Recommended:* treat them as informational, not
   as a validated edge, and say so where they are presented. Changing the bot is your decision and outside the
   engine's scope.
3. **The big-trigger H = 5 result** (t 2.93 development, 2.65 confirmation) is a reported-only secondary.
   - *Recommended:* do not act on it. It was not the pre-registered hypothesis, and its L7 version loses to SPY in both
     windows.
   - Testing it properly would need a new pre-registration and data that no one has seen, such as a forward period
     after 2027-01-29.
4. **The sealed windows.** *Recommended:* leave both as they are. `forward_2026q4` stays available for a future
   hypothesis with its own ADR.

## Next step (NO-GO: recommendation only)

Stop the insider validation line here. No I3.
