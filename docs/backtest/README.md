# Backtest log

Research only. Nothing in this directory places or transmits an order.

## Replication

Every GO or NO-GO is recorded only after an independent script replicates it. The script is not the backtest that produced the result. Two checks are required:

- The no-cost CAGR is within 0.5 percentage points a year of the main signals' no-cost path.
- Holdings or signals differ in at most 2 periods.

Until both pass, `trend_verdict` returns `PENDING-REPLICATION`. The trial counter increments when the test is run, including while the verdict is still pending. Only the verdict column waits. The limits are `replication` in `gates.json`. The measured check is `replication.json`.

## Symmetry

Every gate reports two rows. Row (a) is fractional units for the strategy and for the benchmark. Row (b) is whole shares of a $460 account for both, and SPY exposure uses SPYM because one SPY share costs more than $460. The verdict is row (b). Faber, when out of the market, holds cash at 0%. A BIL sleeve is information only. If that sleeve would be a GO, the label is NOT-RECORDED until a pre-2014 test or a paper test. It does not add a trial. A bug-fix re-run does not change the trial counter. The fields are `symmetry` in `gates.json`.
