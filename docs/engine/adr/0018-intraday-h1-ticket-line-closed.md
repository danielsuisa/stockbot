# ADR 0018: Intraday H1 ticket line closed

- **Status:** Accepted. Decided by the user on 2026-10-21 (S1 prompt, Stage A), after the
  [T2 report](../T2-report.md).
- **Context:**
  - [ADR 0015](0015-intraday-ticket-validation-m1.md) (M1's intraday line);
  - [ADR 0016](0016-t1-pre-registered-design.md) (design);
  - [ADR 0017](0017-t2-decisions.md) (T2 amendments; the ORB 5 stop rule).

## 1. Evidence (T2 development run 1, 2022-02-01 → 2024-12-31)

| Measure | Value |
|---|---|
| H1 (VWAP hammer, VWAP engulfing, bull flag): mean net R per filled ticket | **−0.154R**, t −5.9 (3,786 fills) |
| H1 gross R / mean cost | +0.042R / 0.196R |
| Random entries on the same stocks and days (controls) | −0.145R |
| Setups vs random entries (issued basis, ADR 0017) | **−0.006R**, t −0.31 |
| H1 at the live bot's 20%-of-equity sizing | −0.348R |
| ORB 5 pipeline check | −0.494R, outside its band; no defect found (T2 §3) |

- H1 is negative in every year, for every setup and for both stocks and ETPs.
- The run stopped before confirmation under ADR 0017 item 2.

## 2. Lesson

- **On intraday stocks in play, the setups carry no information beyond the stock selection.** The random entries on
  the same names earn the same.
- **Costs dominate.** The spread alone costs about 0.1R per trade (spread plus the stop's tick, 0.12R; commissions
  and fees, 0.08R).
- **A larger account does not change this.** Spread scales with the position, and the gross before costs is close
  to 0.

## 3. Decision

- **The intraday H1 ticket line is closed.**
- **The intraday confirmation window** (2025-01-02 → 2026-10-16) was never read for H1. It stays available for a
  future intraday hypothesis.
- **Closed lines:**
  - intraday models ([ADR 0007](0007-intraday-line-closed.md));
  - intraday H1 tickets (this ADR);
  - insider signals ([ADR 0011](0011-insider-line-closed-engine-paused.md)).
- **Reopening any closed line needs a new user decision and a new hypothesis family.**
- **The live bot's `/intraday` tickets now carry their measured label.** This is a text-only stockbot change outside
  the engine branches, [danielsuisa/stockbot#26](https://github.com/danielsuisa/stockbot/pull/26).
- **The engine is active only for the swing-ticket line** ([ADR 0012](0012-engine-resumed-swing-tickets.md),
  [ADR 0013](0013-swing-ticket-validation-design.md)).
