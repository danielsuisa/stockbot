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
| `bot/ensemble.py` | The driver. `--prefetch`: inputs only, outcome-free, resumable; `--require-complete` in the last job. `--blind`: network closed, a cache miss aborts with `MissingInput`, writes the report only, prints its path and the input fingerprint. `--verdict`: see items B-D below. Every crash prints the type and frames only |
| `.github/workflows/ensemble.yml` | Four chained prefetch jobs carrying the input cache (with `fail-on-cache-miss`), then the blind job. It is triggered only by a push to `ensemble-run-blind`, has no verdict step, and uploads one artifact: the report. The verdict has its own dispatch-only workflow (item D) |

Smaller changes: `levels.exits(..., close_at_end=True)` (section 2's exit at the last close); the series carry the raw
open.

**Tests:** 741 offline tests (720 at `ac59868`), all passing, covering section 9's lists. Among them:
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

## The blind run (needs the owner's separate go-ahead; not run)

- **Inputs:** about 116,000 Alpaca requests, about 10 hours, in four chained prefetch jobs of at most 5.5 hours.
- **Compute:** one offline job. The engine took 6.2 minutes at full scale on synthetic series (item F), so 2-3 hours is
  an upper bound. It writes only the report.
- **The cache must stay warm for phase D.** GitHub evicts a cache that has not been used for 7 days. The verdict run
  has to use the blind run's inputs: the same fingerprint, from the same commit (6.4 point 4). So it has to be
  dispatched within 7 days, and the verdict also has to reproduce the blind report (item D).

## The owner's rulings on the six choices (spec 4.2, `8f2e044`)

1. Gates 3 and 4 use x without b. b enters gate 1, and gate 5 through gate 1.
2. The top 1% of gate 4 is ceil(n / 100) tickets.
3. The rename rule keeps the all-time lookup. This is a known property: a later filing only finds the ticker, f and K
   were measured with this rule, and no price or signal after D is read.
4. Order validity and the time exit count the symbol's own bars. Known property.
5. Two bar-less symbols of one CIK may take the same other symbol. Known property.
6. Refusals are reported per signal only.

## Item E: the r_s check (`523ab08`)

For each signal, max(1, r_s) of the run must be within 1% (relative) of the K report's max(1, r_s). Otherwise the run
raises `RsMismatch`. This replaces the exact comparison and is recorded in 4.2. After the review, the check runs before
issuing, because r_s needs only the firings and the members. The outcome is the same, and a mismatch now fails hours
earlier.

## Items B-D: the verdict path, fixed before the blind run (`7f85247`, `ece4483`)

Spec 6.4 point 4 freezes the commit at the blind run, so the verdict path is complete in this commit and tested on
synthetic data only.

### B. The verdict report (`bot/ensemble_verdict.py`)

When b ≤ 0.03 R and both check 1 and comparability pass, the report holds:

- **Core numbers:** n, x̄ with and without b, both SEs, A, the 950th smallest A_k, and p.
- **Gate 1:** x̄ − b, t10 and t5.
- **Gate 2:** A, the 950th smallest A_k, and p.
- **Gate 3:** the ten block means (empty blocks as none), the two half means, and the count of positive blocks.
- **Gate 4:** the mean without the top ceil(n/100), and the mean with each signal removed.
- **Gate 5:** gate 1 under stress costs.
- **Reported, never gated:** each signal alone (n, x̄, both SEs), calendar years, filled tickets only, exit types per
  leg, the cost distribution of filled tickets (per signal and overall), and refusals per signal.

It writes three files:

- the JSON report, with no `Infinity`: an infinite t is written as `"inf"`;
- the ticket CSV, one row per kept ticket, with exits and costs;
- the Hebrew summary `docs/backtest/ensemble-<date>.md`, with the gates table and every assumption.

The assumptions include:
- rulings 1-2;
- C14, C15 and C17;
- the stress case, which keeps the same tickets and pools;
- the claim's scope (no signal stays in or out of favour for more than about a year; scenario E is out of scope);
- that a GO stays conditional on the forward check.

`run_ticket` keeps the exit types in memory only.

When b > 0.03 R, check 1 fails or comparability fails, the verdict stops before any return statistic. The JSON then
holds only the input fingerprint, the commit, K, r_s, w_s, check 1's numbers (the blind whitelist's), comparability, the
counts and the outcome. No CSV is written, and the summary says no return figure was computed.

Blind mode is unchanged: `bot/blind.py`, its whitelist and its tests are untouched. The blind report is built by the
same call as before (moved into `_blind`, which the verdict reuses).

### C. End-to-end `--verdict` tests on synthetic bars (`tests/test_ensemble_verdict.py`, 18 tests)

| Case | Result |
|---|---|
| Hand case, six tickets | Every reported number checked by hand: x̄, both SEs, A, the 950th A_k, p, gates 1-5 with their numbers, each signal alone, years, filled only, exit types, cost percentiles, refusals |
| Planted effect (20 tickets, 25 flat pool symbols) | GO, all five gates pass, 20 CSV rows, the GO label in the summary |
| No effect | NO-GO, gate 1 fails |
| Check 1 failing | "NO-GO (not confirmable)", no return statistic, no CSV |
| Comparability failing (10% quote refusals) | "NO-GO (not comparable)", no return statistic, no CSV |
| b > 0.03 R | "NO-GO (data)", no return statistic, no CSV |
| Files written | Only the three report files; one printed line; no log |
| Wrong fingerprint, empty fingerprint, tampered blind report, missing `--blind-report` | Exit 2, no file |
| Degenerate n | The summary renders with n = 1 and SE = 0 |

### D. How the verdict is started (`.github/workflows/ensemble-verdict.yml`; not run)

It starts only by an explicit act of the owner, never by a push.

1. **Trigger:** `workflow_dispatch` only. The inputs are the blind run id, the blind commit, the input fingerprint and a
   typed confirmation ("run the verdict once").
2. **First step:** it fails unless the ref is `refs/heads/ensemble-run-blind`, `github.sha` equals the blind commit,
   the confirmation matches and the fingerprint is non-empty. Inputs reach the shell only through `env`.
3. **Inputs:** it restores `ensemble-inputs-<blind run id>-4` with `fail-on-cache-miss`. A push-created cache on the run
   branch is readable by a dispatch on the same ref.
4. **The blind report:** it downloads the blind run's report artifact (`run-id`, `actions: read`).
5. **The run:** `--verdict --blind-commit … --owner-go-ahead --fingerprint … --blind-report …`. The driver checks again
   that HEAD equals the blind commit and that the fingerprint matches.
6. **Upload:** only the three verdict files. The glob `ensemble-2*.md` excludes the committed phase reports.

**Safer than proposed (added):**
- **The verdict must reproduce the blind report.** It recomputes the blind report from its inputs and refuses unless
  the result equals, at the same 6-digit rounding, the report the blind run uploaded. The fingerprint covers bars,
  filings, prices and sessions but not the cached quotes. This check covers the quotes too, and every count, cost and
  SE.
- **A required reviewer.** `environment: ensemble-verdict`, so a dispatch also waits for a second explicit approval.
- **One run at a time:** `concurrency`.

**Preconditions before the blind run (owner):**
1. Create the environment `ensemble-verdict` in the repository settings, with the owner as required reviewer.
   Otherwise GitHub creates it with no rules. Required reviewers need a public repository or a paid plan.
2. Merge PR #17. A dispatch-only workflow must exist on the default branch to be dispatchable. Then create
   `ensemble-run-blind` at that merge commit.
3. Dispatch the verdict within 7 days of the blind run. GitHub evicts a cache not used for 7 days, and fetching again
   would change the inputs.

## Item F: timing at the blind run's scale (synthetic series only)

`python -m tests.timing_ensemble 1.0 1.0`: random-walk series, 1,800 symbols, 2,320 signal sessions, 36,963 firings,
36,143 tickets issued and kept, 29,905 pools.

| Stage | Minutes |
|---|---|
| Issuing | 0.1 |
| Pools | 5.8 |
| Draws (1,000) | 0.3 |
| **Total engine** | **6.2** |

Peak memory: 988 MB.

This is far under 240 minutes, so no speed-up was needed beyond the one already made: open tickets per day in one pass
(`open_by_day`), replacing a scan over all tickets for each ticket. The synthetic quotes are instant. In the real blind
job they come from the disk cache, and loading the inputs (bars and filings) comes on top. The earlier estimate of 2-3
hours for the blind job is kept as an upper bound.

## Second independent review (items B-E)

The same reviewer, who did not write the code, reviewed `8f2e044`, `523ab08` and `7f85247`. They found no blocking bug
and **no other output path** in either mode:
- the verdict writes only its three files, and the CSV only when the gates were computed;
- when b > 0.03, check 1 or comparability fails, the verdict stops before any return statistic (they also checked the
  b > 0.03 path end to end);
- `bot/blind.py` and the blind output are unchanged.

The findings fixed with tests in `ece4483`, all confirmed by the reviewer:
1. An empty fingerprint skipped the check. It is now required, in the driver and the workflow.
2. The fingerprint does not cover the cached quotes. The verdict now has to reproduce the blind report exactly.
3. The upload glob also matched the committed phase reports.
4. There was no `concurrency` group.
5. The summary was missing assumptions (rulings 1-2, C14, C15, C17, the stress case, the claim's scope, the forward
   check).
6. `check_r_s` ran only after hours of issuing.
7. A None or infinite number broke the summary, and the JSON could hold `Infinity`.
8. The hand case's p sat on a tie.
9. An unused import, and the "blind:" prefix in the missing-input message.

Left as they are (operational points or nits, not bugs):
- **The 7-day cache:** a precondition, listed above.
- **The workflow must be on `main`:** a precondition, listed above.
- **When both check 1 and comparability fail,** the outcome names only "not confirmable". Both numbers are in the
  report.
- **Gate 4** lists "removed" means only for signals that have tickets. The pass result is unaffected.

## Next

**Stop.** PR #17 is open and not merged. The blind run, the verdict run, phase D and phase E are not approved.
