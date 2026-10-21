# ADR 0020: S2 decisions

- **Status:** Accepted. Decided by the user on 2026-10-21 (S2 prompt, decisions 1–4). Every item was fixed before any
  swing outcome existed.
- **Context:**
  - [ADR 0013](0013-swing-ticket-validation-design.md) and [ADR 0019](0019-swing-ticket-implementation.md);
  - the [S1 report](../S1-report.md).
- **"H1"** is the swing H1: non-earnings gap-and-hold.

## 1. Decisions

1. **The A5 fallback is accepted.**
   - Minute bars cover each ticket's own expiry sessions t + 1 … t + 3 only.
   - Later stop/target conflicts resolve stop first.
   - No closed-line output is deleted to make room.
2. **ADR 0013's CIK-identity rule stays.**
   - Unmapped issuers cannot be checked for earnings (A2) or foreign status (A3), so excluding them keeps H1 clean.
   - **Stage 0 count.** These are would-be signals: gap-and-hold with price ≥ $5 and 20-session median dollar volume
     ≥ $10M, one per symbol per 20 sessions, on issuers with no CIK. A2 cannot apply to them.

     | Window | Signals | Symbols | Of which survivorship names |
     |---|---:|---:|---:|
     | Development | 578 | 233 | 20 |
     | Confirmation | 533 | 261 | 8 |

     Another 6 (development) and 11 (confirmation) signals are on mapped issuers with no operating SIC.
3. **Gate (ii) uses the issued basis.**
   - For each issued H1 ticket *i* with at least one control:
     - Xᵢ = its net R if filled, else 0;
     - Cᵢ = the mean over its controls of (net R if filled, else 0).
   - **Gate (ii) is mean(Xᵢ − Cᵢ) > 0.** Its t is clustered by the ISO week of t and reported.
   - Tickets with 0 controls are excluded and counted.
   - The filled-basis difference (filled H1 tickets with at least one filled control) is reported only.
4. **Secondaries cannot be promoted.** S-a and S-b are reported only. A strong secondary result is a hypothesis for a
   future pre-registered test on untouched data, never a pass here.

## 2. Gate (confirmation window, unchanged from ADR 0013/0019)

- **(i)** Mean net R per filled H1 ticket > 0, with t ≥ 2.0 clustered by the ISO week of the fill (CR1).
- **(ii)** The issued-basis mean(Xᵢ − Cᵢ) > 0.
- **Verdict:**
  - both pass: **provisional GO**, then M2 (the reported forward check on `forward_2026q4` after 2027-01-29);
  - otherwise **NO-GO**: (a) (i) fails; (b) (i) passes and (ii) fails.

## 3. Reported only (development and confirmation)

- S-a and S-b, with the same statistics as H1.
  - S-a uses daily bars, stop first throughout.
- H1 without the late-8-K tickets: those with an Item 2.02 header acceptance in (t 20:00 ET, t + 4 sessions].
  - The flag is re-derived on the evaluator side from `issuer_filings` and `filing_acceptance`.
- The sign per year, and results without the top 1% of trades.
- H1 by liquidity tier, using the 20-session median close × volume at t: T1 ≥ $50M, T2 $10–50M.
- Exit-case counts (stop, target, time, gap, merger, delisting) and unfilled reasons.
- The 0-share skips (from the S1 build counts) and the control shortfall.
- **The book:** $2,500 cash, T+1, at most 4 positions, 40% cap, seeded order, idle cash at 0%. It is compared with SPY
  close-to-close plus cash dividends (not reinvested) over the book's first fill → last exit.
- **Mean gross R and mean cost R.**
  - Gross R is (net $P&L + IBKR commissions and fees) / $25.
  - Cost R is the commissions and fees / $25.
  - The spread is inside the fill and exit prices (A4), and is not split out.

## 4. Implementation choices fixed here [P, before any outcome]

- **Fallback half spread** when no sample is found: half of ADR 0019 §5's tier median spread, so **0.025% for T1 and
  0.0475% for T2**. The S1 code's single 0.05% placeholder is not used by the S2 driver.
- **Minute bars** are handed to the evaluator per ticket, and only on its own expiry sessions. A minute bar held for
  another reason (P1, T1) is never used outside that window.
- **The independent re-simulation check (S2 Stage 2):**
  - 200 development H1 tickets and 200 of their controls, drawn with the seed tag `s2-resim-v1`;
  - re-simulated by fresh code that does not import the evaluator's simulation functions;
  - every fill, exit and net R must agree within $0.01 per share;
  - the result is logged as `{"phase": "S2", "kind": "resim_check", "pass": …}`.
  - The confirmation CLI refuses to run without a passed check recorded after the last development run.

## 5. Final hashes (the authorize line names them)

- **Rules:** `9f0475fad96c4f7c102d62ad450609b14ee6336f44f78105b0ff9ed950e7b80e` (ADR 0013 §1, as ADR 0019).
- **Signing key:** b4388290d66a62da. **Rule version:** `adr0019-s1`.

| Set | Window | Rows | sha256 |
|---|---|---:|---|
| H1 | development | 1,419 | `4210e50114bc85b74b08b17f505786751a18bd27d4eda76835019dea8f11804b` |
| S-a | development | 11,666 | `e2c5a4a144272f1e97e323a8c7f0fd4bc87d6d612b5caf7e0c8673432b9e7d26` |
| S-b | development | 2,215 | `f38ebf24b39020b11807db56b2238b52cfc15983991f5402fe1f19345d22dd74` |
| controls | development | 41,748 | `0ec25551694e26be9c5f166943681ae9ebd0ba69fb7b436d9aedb0c92b34974e` |
| H1 | confirmation | 1,155 | `71e303ae99dc3ee33af45dd0d3e10ee7f7d2c8b0bd1e669ff87ce50f89145867` |
| S-a | confirmation | 9,511 | `a6d630d19e12b38077b4dbade94a3c7f692fd68ca7f191c515d4916206131c0e` |
| S-b | confirmation | 1,865 | `2494f6cd03ff900417cdfef29d704f1d08679cbe06e27f78df400fc7d38e1b47` |
| controls | confirmation | 34,215 | `fc05c9794ed8ccd48f97c9b3402a130471f67bd0c932e3ea28dc7f7c0b48141d` |

**Windows:** development 2022-10-01 → 2024-12-31; confirmation 2025-01-01 → 2026-09-17. Confirmation runs once.
