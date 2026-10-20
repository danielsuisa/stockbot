# ADR 0015: Intraday ticket validation opened as M1

- **Status:** Accepted. Decided by the user on 2026-10-20 (T1 prompt, "User decisions").
- **Context:**
  - [ADR 0007](0007-intraday-line-closed.md) (intraday model line closed);
  - [ADR 0012](0012-engine-resumed-swing-tickets.md) (engine resumed for swing tickets);
  - [ADR 0013](0013-swing-ticket-validation-design.md) (swing design, Accepted);
  - the stockbot intraday audit `docs/research/intraday-audit-2026-10-17.md` §4 at `origin/main`;
  - the live screener `bot/intraday.py` at `origin/main` 09bfa55c7445762cf021e6a3a1d8128527f07eba (2026-10-17).

## 1. Decision

- **Milestone M1's first line is intraday ticket validation.** It tests the live screener's own rules, as written in
  the audit §4: hypothesis H1 (a VWAP-pullback reversal candle or a bull-flag high, 09:40–11:30 ET, stop under the
  candle, target 2R, out at 15:55).
  - **Rules only.** No model, no learned parameter, no feature search, no ML.
  - The rules are copied from `bot/intraday.py` at the SHA above, never imported, and hashed into every signed set.
- **The swing line is on hold, not cancelled.**
  - S1 (ADR 0014 on `engine/s1`, local, unpushed) does not run. It resumes after T2, without changes to
    ADR 0013.
  - The S1 store partitions already written stay where they are and are not read by T1.
- **ADR 0007 still stands for models.** The intraday ML line (P0–P3D) stays closed. Nothing from P2/P3 (features,
  labels, LightGBM scores, decision points) is used as an input or a hypothesis.
- **Account.** $2,500, IBKR Pro Tiered, long only, as in ADR 0012.
- **Unchanged.**
  - The live bot. `bot/intraday.py` is under change control. `bot/`, `tests/`, `data/`, `.github/` and the
    repository settings stay untouched.
  - Both sealed windows. `legacy_2016_2021` stays sealed and veto-only.
  - `forward_2026q4` (2026-11-02 → 2027-01-29) stays sealed. It becomes a **reported-only forward check**. It is
    ingested only after 2027-01-29, under a holdout-ledger record, and never feeds a decision of T1 or T2.

## 2. Phases

| Phase | Scope | Outcomes |
|---|---|---|
| T1 | ADR 0016 pre-registration; ingestion; signed tickets and controls; parity with the live bot; the evaluator on synthetic fixtures | **None** |
| T2 | One development run, then one confirmation run, under ADR 0016's gate | Yes, after a T2 ledger entry |

## 3. Rules for T1 and T2

- No outcome — no return, P&L or R for any ticket, signal or control — before T2's ledger entry. The evaluator's CLI
  refuses real sets until that entry exists.
- Development reruns only for documented defects, each with a failing test first. One confirmation run.
- Nothing before 2022-01-03, and no request or read on or after 2026-11-02.
