# Recommendation algorithm: phase C build (up to, not including, the blind run)

Spec: `docs/superpowers/specs/2026-10-10-recommendation-algorithm-design.md`, sections 2-7 and 9, with section 4.2 (the
owner's rulings C1-C25). Plan: `docs/superpowers/plans/2026-10-11-recommendation-algorithm-phase-c.md`.
Measurements (accepted): `docs/backtest/ensemble-phaseC-measurements-2026-10-11.md`.

**No outcome was computed on market data.** The simulation, the random comparison and the statistics ran only on
synthetic bars, in the tests. Nothing in this phase ran the blind run or the verdict run.

## What was built

| Module | Content |
|---|---|
| `bot/ensemble_data.py` | Inputs only: insider rows and purchases, market-price transactions keyed by CIK, bars (raw and split-adjusted, raw open included), item 2.02 releases, the calendar. The insider data sets are pinned to 2026Q1 (section 6.1). An input fingerprint |
| `bot/signals.py` | The universe of section 2 with the rename rule (C11, C12), liquidity deciles (C13), and S1-S4 with 4.1/4.2 (8-K only; S2's bar on the session before D; S1/S2 on universe symbols; S1 by CIK) |
| `bot/ensemble_counts.py` | The two measurements (counts only), already run and accepted |
| `bot/ensemble_backtest.py` | The plan named it `bot/backtest.py`, but that name is taken by the squeeze backtest. It does: issuing (refusals at $100; one open or pending ticket per symbol and signal, C14; the C17 date rule); the simulation through `levels`, including rule 6, a break or a delisting exiting at the last close (C15), the entry spread with the guard fallback, and the stress costs; pools (decile, exclusions, quote-free refusals, at least 20 members, shared per key), m_i, A and A_k from one function, and draws in C25 order |
| `bot/ensemble_stats.py` | Blocks, the SE of 6.3, T10/T5, check 1, b, the design effect, the five gates, and the outcomes, including NO-GO ("data") when b > 0.03 R |
| `bot/blind.py` | The single report function from x and c. It holds the whitelist only, is byte-identical under x → −x and x → x + c, and has one writer |
| `bot/ticket_journal.py` | The journal's format and writer (C22). The blind run writes none |
| `bot/ensemble.py` | The driver. `--prefetch`: inputs only, outcome-free, resumable; `--require-complete` in the last job. `--blind`: network closed, a cache miss aborts with `MissingInput`, writes the report only, prints its path and the input fingerprint. `--verdict`: refuses without `--blind-commit` equal to HEAD and `--owner-go-ahead`. Every crash prints the type and frames only |
| `.github/workflows/ensemble.yml` | Four chained prefetch jobs carrying the input cache (with `fail-on-cache-miss`), then the blind job. It is triggered only by a push to `ensemble-run-blind`, has no verdict step, and uploads one artifact: the report |

Smaller changes: `levels.exits(..., close_at_end=True)` (section 2's exit at the last close); the series carry the raw
open.

**Tests:** 720 offline tests, all passing, covering section 9's lists. Among them:
- the invariance tests;
- the whitelist;
- "nothing written besides the report", shown to catch a planted print of x̄;
- "outcome-free prefetch";
- `MissingInput` on a cache miss without keys;
- each gate at its threshold;
- the hand example of 6.3.

## Independent review (6.4 point 3)

A reviewer who did not write the code traced every output path of `--blind` and `--prefetch` and found **no other
output path**:
- prints and logs: none with a value;
- exceptions: type and frames only, and `MissingInput` escapes broad handlers;
- files: with the network closed nothing can be fetched, so nothing is written except the report;
- the input caches: the quotes are chosen without any outcome;
- the workflow: one artifact.

Fixed with tests after the review:
1. **The draw order.** Draws now run in (signal day, symbol, signal) order, as ruling C25 says. It had been
   (day, signal, symbol).
2. **Pinned inputs.** The insider data sets are pinned to 2026Q1, and an input fingerprint is printed so the verdict
   run can show it used the blind run's inputs.
3. **Incomplete inputs.** A fourth prefetch job; the last one fails unless every quote and index page is cached;
   `fail-on-cache-miss`; a cache miss in the blind job (no secrets) surfaces as `MissingInput`.
4. **NO-GO ("data") when b > 0.03 R**, in both the outcome and the report's `continue`.
5. **Rows capped per ticket** (validity + H + 1), for time.
6. **Test gaps:** gate 1 at its critical values, gate 3 at exactly 7 of 10, and the `--verdict` refusal test, which
   now mocks the right function.

## Known properties of the report (ruling C14, and what the review added)

The count of firings not issued because a ticket was already open depends on how long earlier tickets stayed open.
The review adds that these whitelisted numbers depend on it in the same weak way:
- the issued tickets per block;
- the pools dropped for being small, and the small-pool share;
- n;
- w_s, K and b;
- check 1.

None of them reveals the direction or the level of x. The report is invariant under x → −x and x → x + c. No rule or
decision depends on these counts beyond what the spec already says.

## Choices still open, for the owner (found during the build; none settled by data)

1. **Gates 3 and 4: is b subtracted?** The spec subtracts b in gate 1, and in gate 5 through gate 1. The code follows
   the spec as written: gates 3 and 4 use x without b. The other reading subtracts b everywhere, which is stricter.
2. **"The top 1% of tickets" in gate 4** is ceil(n / 100) tickets, so at least one is removed.
3. **The rename rule's "other filed symbol"** is any symbol the CIK ever filed under, including filings made after D.
   This is how C3/C11 were measured in `18ff0b7`, and K's check compares r_s with that measurement. A point-in-time
   reading would use only filings before D. That would change r_s, so the K measurement would have to be run again.
4. **Bars versus sessions.** The validity of an order and the time exit count the symbol's own bars, as 4.1 does for
   the lags. The spec speaks of sessions. They differ only when a stock has no bar on a session (rare in this
   universe).
5. **Two bar-less symbols of one CIK** can both take the same other symbol in the rename rule. This is rare.
6. **Refusals per block.** The whitelist allows them; the report gives refusals per signal only.

## The blind run (needs the owner's separate go-ahead; not run)

- **Inputs:** about 116,000 Alpaca requests, about 10 hours, in four chained prefetch jobs of at most 5.5 hours.
- **Compute:** one offline job of about 2-3 hours. It writes only the report.
- **The cache must stay warm for phase D.** GitHub evicts a cache that has not been used for 7 days. The verdict run
  has to use the blind run's inputs: the same fingerprint, from the same commit (6.4 point 4). So it should follow
  within 7 days, or the cache has to be refreshed without being changed.

## Next

**Stop.** The PR to `main` is open and not merged. The blind run, phase D and phase E are not approved.
