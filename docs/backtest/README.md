# Backtest log

Research only. Nothing in this directory places or transmits an order.

## Replication

Every GO or NO-GO is recorded only after an independent script replicates it. The script is not the backtest that produced the result. Two checks are required:

- The no-cost CAGR is within 0.5 percentage points a year of the main signals' no-cost path.
- Holdings or signals differ in at most 2 periods.

Until both pass, `trend_verdict` returns `PENDING-REPLICATION`. The trial counter increments when the test is run, including while the verdict is still pending. Only the verdict column waits. The limits are `replication` in `gates.json`. The measured check is `replication.json`.
