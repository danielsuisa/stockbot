# ADR 0010: I2 implementation design (insider measurement)

- **Status:**
  - **Accepted** for the items the I2 prompt fixes (user, 2026-10-20). These are marked **[A]**.
  - Every other choice is **Proposed**, marked **[P]**, and the runs use it as written.
- **Commit order:** this ADR and the ADR 0008 amendments (§9 there) are committed before any return is computed for
  any event, control or SPY window. The evaluator code is committed before the first run.
- **Context:**
  - [ADR 0008](0008-insider-validation-design.md) §7–§9 (decisions, I2 measurement spec, amendments);
  - [ADR 0009](0009-insider-data-design.md);
  - [I1 report](../I1-report.md) and [I1 addendum](../I1-addendum.md).
- **R4 split:**
  - The engine supplies the event set (signed) and the daily universe-(b) table; neither contains an outcome.
  - Everything else is computed in `scalp_eval`, which reads only contract tables through its own loaders and imports
    nothing from `scalp_engine`.

## 1. Inputs (engine, no outcomes)

**1.1 [A] Signed event set.**
- The event set is the `insider_events` table (I1, 17,252 rows; canonical SHA-256 `31e3fad2…`).
- **[P] The digest is signed, not the rows.** The recommendation canonicalization forbids floats.
  - Payload `{"kind": "insider_event_set", "table": "insider_events", "rule_version", "rows", "sha256"}`, signed with
    Ed25519 under domain `scalp-engine/artifact/v1` and the engine's key (`D:\scalp-env\keys\p3_signing.pem`, key_id
    `b4388290d66a62da`; the only Ed25519 key, created with the P0 signing code).
  - New schemas: `artifact_digest` and `signed_artifact`.
- **[P] Canonical hash** (re-derived independently on each side):
  - rows read through the side's own `read_frame`, `ingested_at` dropped, columns sorted;
  - rows sorted by all columns, then serialised with `json.dumps(rows, sort_keys=True, separators=(",", ":"), default=str)`;
  - SHA-256 of the UTF-8 bytes.
- The evaluator verifies the signature, recomputes the hash from the table, and refuses to run on any mismatch.

**1.2 [A] Universe-(b) table `universe_b_daily` (new contract).** One row per (session *d*, candidate) for every
session in 2022-02-01 → 2026-07-20. The flags are as of the prior close:
- `price_ok`: last close on *d*−1, split-adjusted to *d*'s basis, ≥ $1;
- `dv_ok`: median close × volume over the 20 sessions ending *d*−1, ≥ $100K (a session without a bar counts 0);
- `spread_ok`: close − 5 min quoted spread on *d*−1 ≤ 3%;
- `universe_b` = all three;
- `sic2`, plus `dv_decile` (1–10) within universe (b) that day: rank by dollar volume ascending, ties by symbol,
  decile = ⌊10·rank / n⌋ + 1.

- **[P] Candidates** are the symbols that pass the I1 pre-filter at *d*−1 (the necessary condition; ADR 0009 §5),
  so every possible universe-(b) member has a row.
- **[P] CIK and SIC of a candidate** (ADR 0009 §4): the latest filed-symbol observation of the symbol in any Form
  3/4/5, on or before *d* and within 400 days, gives the CIK; `issuer_sic` gives the SIC.
  - No CIK, or a SIC that is null or `0000`, gives `sic2` = null: never a control.
- **[P] An event's own flags** remain those of the event table. The universe table is used for controls and deciles.
  The two agree by construction (same rule, same data); disagreements are counted and reported.

## 2. Returns (evaluator) [A]

- **Day count.** Day 1 is the entry day; day H is the H-th regular session counted from day 1. The return runs from
  day 1's open to day H's close.
- **Entry.**
  - The open of day 1.
  - If day 1 has no bar, use the first open within the next 5 sessions and shift day 1 (and day H) to it.
  - If there is none, drop and count.
  - Controls use the event's day 1 and day H, after any shift of the event. A control with no open on that day 1 is
    replaced (§3).
- **[P] Position arithmetic.** One share is bought at the entry open.
  - Splits and stock dividends with an ex-date in (day 1, day H] multiply the shares by new/old (stock dividend:
    × rate). Fractional rates come from the raw corporate action.
  - Cash dividends with an ex-date in (day 1, day H] buy shares × amount / close(ex-date) more shares, at that day's
    close. If there is no close that day, the dividend is held as cash.
  - R = (shares × exit price + held cash) / entry open − 1.
- **Exit price.** The same rule applies to events, controls and SPY:
  1. The close of day H, if it exists.
  2. Otherwise, if a merger record of the symbol has an effective date in (day 1, day H]:
     - cash merger: the cash rate;
     - stock merger: ratio (acquirer_rate / acquiree_rate) × the acquirer's close on the effective date;
     - stock-and-cash merger: that value plus the cash rate;
     - if the acquirer has no bar on the effective date ("not in the store"): the target's last close.
     - Shares are those held on the effective date.
  3. Otherwise, if the symbol has no bar from its last bar through the end of the store (2026-10-16): an other
     delisting, valued at the last close × 0.70.
  4. Otherwise, a temporary gap: the first close after day H.
  - **[P]** A delisting or merger value is held as cash to day H. The counts of each case are reported separately for
    events and controls.

## 3. Controls (evaluator) [A]

- **K = 5.** Candidates are the `universe_b_daily` rows of the event's entry date (before any shift) with
  `universe_b`, the event's `sic2` and the event's `dv_decile`. The event's `sic2` and decile come from its own row in
  the same table; an event without a row there cannot be matched and is dropped and counted.
- **Excluded:**
  - the event issuer;
  - any issuer (CIK) with **any** event in the event table within ±30 calendar days of the event's entry date, whatever
    that event's flags;
  - [P] any candidate whose symbol equals that of an excluded issuer's event.
- **Seed** = the first 8 bytes of SHA-256(`f"{event_id}|i2-control-v1"`) as an integer. The candidates are sorted by
  symbol, then shuffled with `random.Random(seed)`.
- **Walk the permutation.** A candidate without an open on the event's day 1 is skipped (replaced by the next). The
  first K valid candidates are taken.
- **Widening.** If fewer than K are valid at SIC2:
  - **[P]** redo the walk over the SIC1 set (same seed, sorted SIC1 candidates);
  - if 1 ≤ n < K are valid at SIC1, use them and count;
  - if 0, drop the event and count.
- **AR** = R(event) − mean R(controls).

## 4. Populations, overlap and inference

- **[P] Order:**
  1. trigger;
  2. mapped symbol;
  3. universe flag;
  4. non-FPI (user decision 2026-10-20: every primary statistic);
  5. the window, by event-table `entry_date`;
  6. the overlap rule;
  7. entry drops;
  8. control drops.
- Dual-CIK ("ambiguous") events are unmapped, so they drop at step 2 (130 in the table window; kept excluded).
- **[A] Overlap, per horizon.** Within an issuer (CIK) and trigger, an event whose entry falls within H sessions of
  the last kept event is dropped; the first is kept. Sessions are counted on the calendar between event-table entry
  dates.
- **[A] Inference.**
  - Mean AR, with SE clustered by ISO week of entry (event-table entry date, [P]).
  - CR1: SE² = G/(G − 1) · Σ_g (Σ_{i∈g} (x_i − x̄))² / n².
  - t = mean / SE.
  - Realized MDE = 2.8416 · SE, beside the I1 estimate.
- **[A] Bootstrap.** 95% percentile CI from 10,000 resamples of entry weeks with replacement, seed `"i2-boot-v1"`
  (an integer from SHA-256 as above), per statistic.
- **Reported per window** (development, confirmation, post-S1): n, mean, median, hit rate (share > 0), clustered SE,
  t and the bootstrap CI, for H ∈ {5, 20, 60} × trigger ∈ {cluster, big}. Universe (b) is used for the event study,
  universe (c) for L7.

## 5. L7 tradable version (evaluator; universe (c)) [A]

- **Entry** = the day-1 open + ½ the quoted spread (ask − bid, $) of the 09:35 NBBO sample on day 1.
  - Fallback: the close − 5 min sample of the session before day 1.
  - If both are missing (or invalid: bid ≤ 0 or ask < bid), drop and count.
- **Exit** = the exit price − ½ the spread of the close − 5 min sample on the exit session.
  - Fallback: the previous session's sample. If both are missing, drop and count.
  - **[P]** The spread applies only when the exit is a market close (case 1 or 4). Merger and delisting values (cases
    2 and 3) carry no spread.
- **Size and costs.**
  - Shares = ⌊500 / entry⌋; skip and count if 0.
  - Costs: IBKR **Tiered** with fees (`scalp_eval/costs.order_cost`) on the buy, at entry, and on the sell, at exit
    (no sell cost for a cash merger or delisting).
- **[P] Net trade return** = (shares × exit + cash dividends received in (day 1, day H] − shares × entry − costs) / $500.
  Splits change the share count; dividends are held as cash, not reinvested.
- **SPY return** over the same day 1 open → day H close, total return by §2, with no costs.
- **[A] Gate (ii) statistic:** the mean over trades of (net trade return − SPY return), cluster, H = 20. The mean
  absolute net return and the mean cost per trade (% of $500) are reported only.
- **Account-level simulation** (reported only):
  - one $500 cash account, one position at a time, T+1 settlement (proceeds usable from the session after exit);
  - events taken in `available_at` order, ties by event_id;
  - an event is skipped when settled cash is short of one share or a position is open;
  - [P] size = ⌊settled cash / entry⌋.
  - Reported: trades, skips, final equity and return, and the same against SPY buy-and-hold over the window.

## 6. Runs and the trials ledger

- **[A]** The development run (2022-02-01 → 2024-06-28) is a pipeline check with no gate weight. The confirmation
  run (2024-07-01 → 2026-07-20) happens **exactly once**, after the last development run, on committed code. Post-S1
  is computed from it.
- **[P] Ledger** `D:\scalp-data\derived\trials_i2.jsonl`, one record per run: run_id, window, commit, start and end
  times, the input hashes (event set, universe table) and the SHA-256 of every output.
  - The evaluator CLI refuses a confirmation run if the ledger already holds one, and refuses any run on a dirty work
    tree.
- **[A]** A development rerun is allowed only after a documented code defect, with a failing unit test committed
  before the fix and no spec change.

## 7. Contracts

- New table `universe_b_daily` (§1.2).
- New schemas `artifact_digest` and `signed_artifact` (§1.1).
- The outputs are JSON files under `derived/` (`i2_dev.json`, `i2_conf.json`). They are results, not store tables.
