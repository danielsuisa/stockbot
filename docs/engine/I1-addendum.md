# I1 addendum: duplicate-key remedy (I2 Stage 0)

Date 2026-10-20, branch `engine/i2`, code e0829d1 (`scalp_engine/i1_remedy.py`). No network. The
[I1 report](I1-report.md) is unchanged and keeps its original FAIL.

## Cause

- `insider_prices.P1_LAST` was 2026-09-30, but P1 had stored daily bars to 2026-10-16.
  - I1's part `sip-1d-i1c` therefore fetched 2026-10-01 → 10-16 again for every symbol.
  - That repeated 68,174 (symbol, date) keys of P1's parts `sip-1d-v1`, `v2` and `v3`.
- I1's close − 5 min quote part `i1-close-v1` sampled the same NBBO as P1's early-close re-sample (`sip-samples-v2`) on
  7 early-close days, repeating 3,493 keys.
- Every repeated key was identical in every value. I1's own writes had no duplicates.

## Remedy (user decision 2026-10-20, option (a))

1. **Cause fixed.**
   - `P1_LAST` = 2026-10-16.
   - `bars_plan()` fetches, inside P1's dates, only the symbols P1 lacks. `test_plan_never_overlaps_p1_dates` fails
     on the old constant.
   - `step_bars` checks the constant against the manifest's last P1 session.
2. **Set aside and rewrite**, per affected date file:
   - write a new part holding only the keys P1 lacks (`sip-1d-i1c2`, `i1-close-v2`), with rows unchanged;
   - assert that every key of the old file is identical in P1 or present and identical in the new part;
   - rename the old file to `.orphan-0` (the store's existing set-aside suffix; never read);
   - append a `set_aside` record to the manifest (file SHA-256, rows, identical-in-P1, kept rows, new part).

| Table | Files set aside | Rows | Identical in P1 | Kept in new part |
|---|---|---|---|---|
| `raw_bars_1d` (2026-10-01 → 10-16) | 12 | 69,216 | 68,174 | 1,042 (`sip-1d-i1c2`) |
| `raw_quote_samples` (7 early-close days) | 7 | 32,340 | 3,493 | 28,847 (`i1-close-v2`) |

Every assertion held; no value was lost.

## Re-check (`derived/i2_stage0_recheck.json`)

| Check | Result |
|---|---|
| Store-wide duplicate keys, 10 tables (bars, quote samples, corporate actions, calendar, the insider tables, `cik_symbol_map`, `insider_events`) | **0** |
| Universe flags rebuilt from the repaired store, canonical SHA-256 | `fe84a79c…` = I1 baseline (18,422 rows) |
| Event table rebuilt, canonical SHA-256 (without `ingested_at`) | `31e3fad2…` = I1 baseline (17,252 rows) |

Before the remedy, the baseline hashes were taken once, and the rebuild method was confirmed to reproduce them on the
unchanged store.

## I1 gate item, now

**0 duplicate keys: PASS** (re-check above). The I1 gate's other items stand as reported. With this item, every I1
gate item passes.
