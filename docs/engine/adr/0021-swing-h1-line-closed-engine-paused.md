# ADR 0021: Swing H1 line closed; engine paused

- **Status:** Accepted. Decided by the user on 2026-10-21 (S2C prompt), after the [S2 report](../S2-report.md).
- **Context:**
  - [ADR 0011](0011-insider-line-closed-engine-paused.md) (the insider line closed; the earlier pause);
  - [ADR 0018](0018-intraday-h1-ticket-line-closed.md) (intraday H1 tickets closed);
  - [ADR 0019](0019-swing-ticket-implementation.md) and [ADR 0020](0020-s2-decisions.md) (the swing design and the S2
    decisions).

## 1. Evidence (S2, verdict NO-GO (a))

| Measure | Value |
|---|---|
| Confirmation (2025-01-01 → 2026-09-17), H1 mean net R per filled ticket | **−0.142R**, t −1.60 (933 fills, 89 ISO weeks) |
| Confirmation gross R / costs | −0.109R / 0.032R |
| Gate (ii), issued basis | +0.007R (t 0.16) |
| Development (2022-10-01 → 2024-12-31), H1 | +0.062R, t 0.62 |
| Confirmation by year, liquidity tier and secondary | all negative: 2025 −0.142, 2026 −0.141; T1 −0.139, T2 −0.146; S-b −0.105, S-a −0.035 |
| Book, $2,500 cash | +0.7% against SPY +30.4% over the same span |

The confirmation is negative before costs, so costs are not the cause. The development point estimate was noise
around 0.

## 2. Decision

- **The swing H1 line (non-earnings gap-and-hold) is closed.** S-a (the 20-day breakout) and S-b (gap-and-hold
  including earnings days) are **not** promoted.
- **The engine is paused.** No new research line, ingestion or engine change starts without a new user decision that
  names a **new hypothesis family**.
- **Closed lines:**

  | Line | ADR |
  |---|---|
  | Intraday models | [0007](0007-intraday-line-closed.md) |
  | Insider signals | [0011](0011-insider-line-closed-engine-paused.md) |
  | Intraday H1 tickets | [0018](0018-intraday-h1-ticket-line-closed.md) |
  | Swing H1 tickets | this ADR |

- **Unchanged:**
  - the live stockbot;
  - `D:\scalp-data`;
  - every `engine/*` branch, which stays pushed and unmerged;
  - both sealed windows. `forward_2026q4` has never been ingested, and the holdout, T and S ledgers are unchanged.

## 3. Cross-line lessons

Any future hypothesis must address these before it is pre-registered.

1. **The source of edge.**
   - On every opportunity set tested, the gross edge was about 0 or negative:
     - intraday ML (P3: PF before costs 0.73–0.84);
     - insider clusters (I2);
     - intraday setups (T2: gross +0.042R);
     - swing gap-and-hold (S2: gross −0.109R).
   - A new hypothesis must name a **different source of edge** than price patterns, insider clusters, or ML on OHLCV
     features.
2. **The cost and power dilemma at $2,500.**
   - High-turnover lines can be measured well, but cost about 0.2R per intraday ticket (T2: 0.196R), and none showed
     an edge.
   - Low-turnover lines are cheap (S2: 0.032R), but 4–5 years of data give too few trades to decide anything (S2's
     confirmation MDE: 0.251R).
   - A new line must show **both** a break-even case and adequate power **before** any data work.
     - The break-even case uses the project analysis "Break-even edge by account size"
       (`analysis/breakeven-edge-by-account-size.md`). Like ADR 0012, that analysis is not stored in this
       repository.
3. **Power from measured dispersion.**
   - S2's realized MDE was 0.251R, against the 0.154R planned from an assumed σ.
   - Future power estimates use dispersion **measured on the existing controls and populations, never on the new
     hypothesis's outcomes**, and include the clustering design effect.
4. **Proven safeguards: keep them all.**
   - fill realism (P3D, T1 A1);
   - point-in-time earnings exclusion by header acceptance time (S1 A2);
   - a survivorship universe (P1, T2, S1 A7);
   - fetches keyed on requested symbols, with held keys dropped (T2);
   - the issued-basis control comparison (T2, S2);
   - independent re-simulation before confirmation (S2).

## 4. What stays available

- **`forward_2026q4`** (2026-11-02 → 2027-01-29), never read.
- **The intraday confirmation window** (2025-01-02 → 2026-10-16), never read for intraday H1.
  - The swing confirmation (2025-01-01 → 2026-09-17) has now been read for the swing families: H1, S-a, S-b and their
    random controls.
- **All the infrastructure in the [engine index](../README.md):**
  - ingestion with the holdout guard;
  - signed artifacts;
  - the intraday and swing evaluators with their golden sets;
  - the trials ledgers;
  - the re-simulation check.
