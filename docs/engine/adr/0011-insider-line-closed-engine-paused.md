# ADR 0011: Insider-signal line closed; engine paused

- **Status:** Accepted. Decided by the user on 2026-10-20 (I2C prompt, "User decisions").
- **Decision:**
  - The insider-signal line is closed. There is no I3.
  - The engine is paused: no new research line, ingestion, I3 or engine change starts without a new user decision.
  - The live stockbot insider alerts, their journal and `/stats` are unchanged.
- **Context:**
  - [ADR 0007](0007-intraday-line-closed.md) (intraday line closed);
  - [ADR 0008](0008-insider-validation-design.md) (design, decisions, the I2 spec, the §9 amendments);
  - [ADR 0009](0009-insider-data-design.md);
  - [ADR 0010](0010-i2-implementation.md);
  - reports [I0](../I0-report.md), [I1](../I1-report.md) + [addendum](../I1-addendum.md) and [I2](../I2-report.md).

## 1. Evidence chain

| Step | Result | Source |
|---|---|---|
| I1: rule reimplementation (stockbot `2ffda0a`, 3 insiders / $100K / 30 days / $500K) on the acceptance-time clock | **Parity: 106 of 106 live journal alerts matched**, 0 unexplained (2026-09-24 → 10-16) | I1 report, `engine/i1` 28a47e9 |
| I1: data | Acceptance times for 99.991% of required filings. Event table 17,252 rows (`31e3fad2…`). The duplicate-key item was remedied in I2 Stage 0, giving 0 duplicates and an unchanged hash | I1 report; I1 addendum 590f37b |
| I2 gate (i): cluster, universe (b), non-FPI, H = 20, vs matched controls, confirmation 2024-07-01 → 2026-07-20 | **Mean AR +1.08%, clustered t = 1.61 < 2.0** (n = 721; CI [−0.22%, +2.43%]; realized MDE 1.91%): FAIL | I2 report; run `i2-confirmation-20261020T061943Z` on c6d3ae8 |
| I2 gate (ii): cluster, universe (c), H = 20, $500 Tiered, net − SPY | **Mean −0.14%** (n = 626; t −0.20): FAIL | I2 report |
| I2 account level (reported only) | **−45.0%** vs **+36.0%** for SPY buy-and-hold (25 trades) | I2 report |
| Verdict | **NO-GO, class (a): no abnormal return** | I2 report, `engine/i2` aeea512 |

Every gate was pre-registered (ADR 0008 §7–§9, ADR 0010) and committed before the first outcome. The confirmation
run happened exactly once.

## 2. Lessons, each with evidence

1. **The point estimates were positive in both windows, but not significant on the confirmation window.**
   - Development +1.22% (t 1.29, n 1,096); confirmation +1.08% (t 1.61, n 721).
   - Pooling the two windows after seeing them is not admissible under the pre-registration: the development run was
     declared a pipeline check with no gate weight (ADR 0010 §6).
   - Any re-test needs a new hypothesis, a new ADR and untouched data.
2. **Volatility drag sank the account.**
   - The development-window population dispersion of 20-session abnormal returns is σ ≈ 0.167 (I1 power module).
   - A single-position $500 account that compounds through about 25 trades with that dispersion loses heavily even
     when the mean return per trade is positive: confirmation mean net +0.64% per trade, yet the account fell 45%.
   - Arithmetic means per trade are not what one small account earns.
3. **The big trigger's H = 5 result is not actionable.**
   - It reached t 2.93 (development) and 2.65 (confirmation). But it is one reported-only secondary among 18
     trigger × horizon × window cells, and its L7 version loses to SPY in both windows (−0.58% and −0.30%).
   - Acting on it would need a new pre-registration and untouched data.
4. **The live journal is still inflated.**
   - The I0 baseline issue remains in the live bot by user decision. For the 16:30 and 19:30 New York alerts, the
     follow-up baseline is that day's 16:00 close, which is before the alert (I0 audit I2; `bot/journal.py:65-88`).
   - So the journal's `/stats` overstate alert performance, and they are not evidence of an edge.
5. **The SEC submissions-JSON acceptance time is filer-inconsistent.**
   - Against the filing header, the JSON matched read as UTC for 264 of 492 sampled filings, and under the ET+8/+10
     rule for 228. Every filing fits exactly one reading.
   - The SEC header `ACCEPTANCE-DATETIME` (Eastern, via the tz database) is the source of truth (I1 report;
     ADR 0009 §1).

## 3. Sealed windows

- `legacy_2016_2021` (veto only) is unchanged. It was never run for this line, and nothing dated 2016–2021 was
  ingested in I0–I2.
- `forward_2026q4` (2026-11-02 → 2027-01-29) is unchanged and **has never been ingested**. It remains available as
  an untouched window for a future hypothesis, under a new user decision and its own ADR.
- `scalp_contracts/holdout_ledger.jsonl` is **empty**: no unseal record has ever been written.

## 4. Consequences

- A rule is added to `scalp_engine/CLAUDE.md`: the engine is paused.
- `docs/engine/ARCHITECTURE.md` carries a second status line under the ADR 0007 banner.
- `docs/engine/README.md` indexes every phase, component, store and branch, so a future session can resume without
  re-auditing.
- Nothing in `D:\scalp-data` is deleted, moved or rewritten. Branches `engine/p0` … `engine/i2c` stay pushed and
  unmerged.
