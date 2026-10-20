# ADR 0012: Engine resumed for the swing-ticket line

- **Status:** Accepted. Decided by the user on 2026-10-20 (S0 prompt, "User decisions").
- **Context:**
  - [ADR 0007](0007-intraday-line-closed.md) (intraday line closed);
  - [ADR 0011](0011-insider-line-closed-engine-paused.md) (insider line closed, engine paused);
  - the [engine index](../README.md);
  - the project analysis "Break-even edge by account size". It is not stored in the repository; only the figures
    restated in the S0 prompt are used here.

## 1. Decision

- **Scope.** The engine resumes for **one line only: swing bracket tickets**.
  - The intraday line (ADR 0007) and the insider line (ADR 0011) stay closed.
  - No other line starts without a new user decision.
- **Account.**
  - $2,500 starting equity, IBKR Pro **Tiered**, long only.
  - The default model is a **cash account** with T+1 settlement. A margin account is open decision G5
    ([ADR 0013](0013-swing-ticket-validation-design.md)).
- **Target deliverable: a conditional bracket ticket.** Advisory only; execution stays manual (R1). For example:
  "If SYMBOL trades at or above X before date D, buy Q shares; stop-loss Y; take-profit Z; exit after N sessions if
  neither is hit." Every ticket states:
  - a **trigger level**, either a buy-stop or a stop-limit with a stated limit offset;
  - a **ticket expiry** (the last session the trigger may fill);
  - the **share quantity**;
  - the **stop-loss**, the **take-profit** and the **time exit** (N sessions);
  - every price **rounded to the tick** ($0.01 at or above $1).
- **Benchmark.**
  - Every result is compared with **SPY over the same dates**.
  - At book level the comparison includes **idle cash at the interest rate the account would actually earn**,
    verified from IBKR's official interest page (S0 audit S5).

## 2. Cost rationale (figures from the S0 prompt)

- **Intraday at $2,500 is too expensive.** An intraday ticket costs about **0.2–0.6R** per trade, about the best
  gross edge this repository has ever measured. ZBA X5 earned **+0.275R gross against 0.214R of costs**, and was NO-GO
  on the holdout (cited in S0 audit S1).
- **A swing ticket costs far less.** With 1% risk ($25 at $2,500) it costs about **0.03–0.10R**: wider stops make the
  position larger in dollars per R, so the fixed commission and the spread are a smaller fraction of R.
- The S0 audit (S6) recomputes this with **measured** spreads.

## 3. What stays closed or sealed

- The intraday and insider lines stay closed; their models and signals are not reused as hypotheses.
- `legacy_2016_2021` stays sealed and veto-only.
- `forward_2026q4` stays sealed and has never been ingested. Using it is decision G3. The ledger stays empty.
- The live stockbot is unchanged and read-only for the engine. That includes `bot/`, the `intraday` and ticket
  modules, `data/`, `.github/` and the repository settings.
- **No outcome is computed for this line** — no return, P&L or R for any ticket, signal or control — before a
  pre-registered ADR (ADR 0013, once Accepted) and its evaluator are committed.
