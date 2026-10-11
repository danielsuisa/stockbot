# Recommendation algorithm phase C (up to, not including, the blind run) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:**
1. Measure K (section 6.4) and the quote coverage of 2017–2026. Each measurement ends in a report and a stop.
2. Build what the blind run needs, tested on synthetic bars only:
   - the point-in-time universe with the rename rule;
   - the four detectors;
   - ticket issuing with the one-open-ticket rule;
   - the random comparison (6.2);
   - the statistics (6.3);
   - the blind report (6.4);
   - the verdict gates, which run only in phase D.

**Architecture:** five modules: universe and detectors; the backtest engine; statistics; the blind report; and the run driver, which is resumable over cached inputs. Their names and contents are under "File structure". The engine reuses the phase A readers (`bot/probe_ensemble_data.py`), the levels engine (`bot/levels.py`) and the Alpaca, SEC and FINRA clients.

**Tech Stack:** Python 3.11+ stdlib; unittest with the network mocked; GitHub Actions for the live runs (secrets only there).

**Spec:** `docs/superpowers/specs/2026-10-10-recommendation-algorithm-design.md` on `main` at `90411ed`, sections 2–7 and 9 (the owner's approval of 2026-10-11 covers phase C up to and excluding the blind run).

## Global Constraints

- **Blind discipline, from the first commit.**
  - Until the blind run, the fill simulation (`levels.fill`, `exits`, `simulate`), the random comparison and the statistics run only on synthetic bars. There is no trial run, smoke test or timing run on real bars, whatever it prints.
  - Detectors, the universe, the rename rule, `levels.plan` and `levels.guard` may run on real data, for counts only.
  - No return or outcome of any ticket, pool member or stock is computed for output, printed, logged, cached or saved. Inputs (bars, quotes, filings, index pages) may be cached; results never.
- **The four enforcement points of 6.4 are tests and steps, not promises:**
  1. The report is built by one function from the arrays x and c, and is byte-identical under x → −x and x → x + const.
  2. A whitelist test: a report key outside the whitelist fails.
  3. Nothing is written besides the report.
  4. A fresh reviewer confirms there is no other output path.
- **Unrounded comparisons.** Every spec threshold is compared on unrounded values (5%, 3%, 0.03 R, 0.10 R, 20 pool members, 1%, 10%, the deciles). Rounding happens only in the printed report.
- **Matching.** Insider prices are matched by CIK, not by symbol (section 7 C).
- **Fixed $100 risk** for every ticket of the test. Sizing by equity (section 10) never enters it.
- **Hands off the live bot.** `/ticket`, `/equity` and the live workflows are unchanged. No merge to `main` without the owner's approval. E and every number derived from it stay out of the repository and its logs.
- **Out of scope.** No blind run and no verdict run in this phase. Phases D and E are not approved.

## Order of work and stops

1. **Part 1, counts-only code (Tasks 1–3).** The universe with the rename rule, the four detectors, and the counting runner. The two measurements need them, and they compute no outcome. *(Choice C1.)*
2. **Measurement 1a, K (Task 4).** Report, then **stop for the owner**. If the provisional b > 0.03 R, the result is NO-GO ("data") and nothing more is built.
3. **Measurement 1b, quote coverage (Task 5).** Report, then **stop for the owner**. If the sampled share of quote-based refusals is above 5%, stop and report before building.
4. **Part 2, the build (Tasks 6–11)**, on synthetic bars only.
5. **Independent review (Task 12)**, then **stop**. The blind run needs the owner's separate go-ahead.

## File structure

| File | Responsibility |
|---|---|
| `bot/signals.py` (new) | Pure, point in time: the universe of section 2 with the rename rule and liquidity deciles; S1–S4 detectors with the 4.1 clarifications; nothing reads a bar after D |
| `bot/ensemble_data.py` (new) | Loads and caches inputs only: insider rows and CIK-keyed P/S/F prices, bars (raw + split), item 2.02 releases and index-page times, the calendar, quotes. Reuses `probe_ensemble_data` and `alpaca` |
| `bot/ensemble_counts.py` (new) | The two measurements (K, quote coverage): counts only, whitelisted reports |
| `bot/backtest.py` (new) | Issuing tickets (refusals, one open or pending ticket per symbol and signal), the simulation through `levels`, pools, m_i, draws; in memory only |
| `bot/ensemble_stats.py` (new) | Blocks, the SE formula, T10/T5, check 1, b, the gates; pure functions |
| `bot/blind.py` (new) | The single report function from x and c, the whitelist, the writer of the one report file |
| `bot/ensemble.py` (new) | The driver: `--prefetch` (inputs only, resumable), `--blind` (report only), `--verdict` (phase D; refuses unless given the blind run's commit and the owner's go-ahead flag, see C24) |
| `.github/workflows/ensemble.yml` (new) | Chained jobs that carry the input cache (as `zba-v2.yml`); triggered only by pushing named run branches |
| `tests/test_signals.py`, `tests/test_backtest.py`, `tests/test_ensemble_stats.py`, `tests/test_blind.py`, `tests/test_ensemble_counts.py` | Section 9's tests, synthetic data only |

## Choices the spec leaves open (proposed decisions, for the owner's review; none settled by looking at data)

**Build order and detectors**
- **C1.** Part 1 builds, before the measurements, the universe with the rename rule and the four detectors in `bot/signals.py`. K must be measured with the detectors and universe of the test, and both compute counts only. Everything that computes an outcome is built after both measurements.
- **C2.** The detectors for K and for the quote sample are those of `bot/signals.py`, built per sections 2, 4 and 4.1 on top of phase A's tested code (`probe_ensemble` and `probe_ensemble_data`). The differences from phase A:
  - 8-K only (already the default);
  - S2 needs a bar on the session before D;
  - S1 and S2 count only on a universe symbol;
  - the universe has the rename rule;
  - S1's issuer is the CIK, with prices matched by CIK.

**K (6.4)**
- **C3. "Bars end for good."** A symbol's bars end for good at L when:
  - L is the date of its last raw Alpaca bar through 2026-03-31;
  - L < 2025-04-01;
  - on the first session after L, the rename rule of section 2 does not recover it. A symbol that the rename rule recovers has not ended. Its continuation is its CIK's other symbol, and that symbol's own end (if any) is judged the same way.

  A break by the bad-price rule is not an end, because bars continue. A symbol that ended before 2017-01-03 contributes nothing.
- **C4. The window and the denominators.** The window is "the 12 months before L": the sessions in (L − 365 days, L], clipped to the signal period 2017-01-03 … 2026-03-31.
  - A universe stock-month is a (symbol, calendar month) in which the symbol is in the universe on at least one session.
  - For the window, the stock-months are the calendar months the window touches in which the symbol is in the universe on a session inside the window.
  - So that partial months at the window's edges do not bias r_s, both the window rate and the all-symbol rate are also computed per universe stock-session. **The reported r_s is the per-stock-session ratio.** The per-stock-month ratio is reported beside it.
- **C5. Firings for K.** The four detectors' firings on universe symbols, 2017-01-03 … 2026-03-31. This is before the one-open-ticket rule and before the refusals, which depend on outcomes or quotes. The rate of signal s is firings / stock-sessions (and / stock-months).
- **C6. The provisional K.** Its w_s are phase A's firing shares, as the owner said: S1 1,511, S2 7,979, S3 13,355, S4 14,679 (S1 in the universe), out of 37,524. That gives w = 0.0403, 0.2126, 0.3559, 0.3912.
  - Reported: K = Σ w_s × max(1, r_s); K_max = max_s max(1, r_s); b = K × 0.41% × 1 R, and b_max = K_max × 0.41%.
  - The stop compares the provisional b, unrounded, with 0.03 R.
  - The final K is computed inside the blind run, with the issued-ticket shares, by the same code. The run also recomputes the r_s and checks that they are identical to the 1a report.

**Quote coverage (1b)**
- **C7. Sample.** `random.Random(20261011)`. Up to 50 firings per (signal, year), 2017 … 2026 (2026 is Q1), drawn from the C5 firing list sorted by (day, symbol). Where a stratum has fewer, all of them are taken. That is at most 2,000 firings.
- **C8. What is counted per sampled firing.** Section 3's `plan` runs at $100 risk with the signal's mode and H (S1 open/21, S2 breakout/42, S3 breakout/21, S4 open/5), on bars through D. The counts are:
  - firings passing the quote-free refusals (by refusal reason otherwise);
  - of those, no usable guard quote 5 minutes before D's close;
  - of those with a usable guard quote, the cost guard above 10%;
  - separately, for every firing passing the quote-free refusals, no usable quote 5 minutes after the open of the session after D.
- **C9. The stop rule.** The quote-based refusal share is (no guard quote + cost guard) / passing. It is estimated for the population by weighting each (signal, year) stratum by its firing count. **Stop if the weighted overall share > 5%** (unrounded). The per-signal and per-year shares are reported and do not stop by themselves. The entry-quote share is reported only, because it is not a refusal.
- **C10. API calls for 1b.**
  - Alpaca: at most 2,000 guard quotes + 2,000 entry quotes. At 190 requests a minute (`alpaca.GAP`) that is about 21 minutes.
  - The bars of the sampled symbols are already loaded for the detectors.
  - The firing list itself is rebuilt from the inputs cached by 1a.

**Universe, rename rule, deciles**
- **C11. The rename rule (section 2) on day D.** It is applied after a first pass of the universe without it, so "that other symbol is not itself in the universe on D" can be checked. A recovered symbol's bars are the other symbol's bars, whose price scale is checked against the CIK's insider price. The other symbol is not counted twice.
- **C12. The CIK's latest market-price transaction.** Codes P, S, F, price > 0, original Forms 4 and 5, filed in [D − 365 days, D). Latest by filing date, then trade date, then accession.
- **C13. Liquidity decile.** The ranks of the 20-session average dollar volume within D's universe, ascending with ties by symbol. decile = floor(10 × rank / n), 0–9.

**Tickets and the one-open-ticket rule**
- **C14.** A ticket is open or pending on day X from its signal day D through:
  - its last exit session, if filled;
  - otherwise the last session its order was valid (D + 1 for `open`, D + 3 for `breakout`).

  A firing of signal s on a symbol with an open or pending ticket of s is not issued, and is counted as "already open". The pool's "no ticket of any signal firing or open on D" uses the same windows over issued tickets plus that day's firings.
- **C15.** The bad-price rule in a ticket: when the symbol's series ends inside the ticket, everything still open exits at the last close before the break (section 2). A firing whose next session is in a new segment is refused as "series break".
- **C16. Price scale.** The split-adjusted series is rescaled by raw close(D) / split-adjusted close(D) (section 2), for the ticket and for every pool member.
- **C17. The end of the data.** A ticket whose time exit falls after 2026-03-31's available bars is not simulated beyond the data: it is dropped and counted ("open at data end"). The pool is treated the same way.

**Statistics and the report**
- **C18. Blocks.** The index is the signal session's position among all sessions of 2017-01-03 … 2026-03-31, and block = floor(10 × index / number of sessions). The five long blocks are the pairs (0–1, …, 8–9).
- **C19. Percentiles** of the cost in R, over filled tickets per signal: nearest rank (the ceil(q × n)-th smallest).
- **C20. Design effect** = (the larger SE)² / (s² / n), with s the sample standard deviation of x (n − 1). It is invariant under x → −x and x → x + c, so it passes test 1.
- **C21.** The whitelist's "largest |x_i − x̄|" is computed but never x̄ itself. The report function receives x and c and returns only whitelisted keys.
- **C22. The journal** (section 8, `data/ticket_journal.json`) is built and tested in phase C on synthetic tickets: the format and the writer. The blind run writes no journal. The live journal is phase E.
- **C23.** The pool members' and the drawn members' gross results are held in memory only, in an `array('d')` per pool key (D, decile, mode, H). They are shared by every ticket with the same key.
- **C24. The verdict gates** (6.4 gates 1–5 and the outcomes of 6.6) are written and tested in phase C on synthetic data, so the blind run's commit already contains them (6.4 point 4: "the same commit, unchanged, makes the verdict run"). `--verdict` refuses to run unless given both `--blind-commit <sha>`, equal to the checked-out commit, and `--owner-go-ahead`. It is never run in phase C.
- **C25. Draw order.** Draw k uses `random.Random(20261010 + k)` and picks, for each ticket in (signal day, symbol, signal) order, `rng.randrange(len(pool))` over the pool sorted by symbol.

## The blind run: runtime, API calls, resumption (estimates, for the go-ahead decision)

- **Inputs:**
  - Alpaca daily bars, raw and split-adjusted, for every symbol as filed: about 3,200 requests, as phase A.
  - The SEC quarterly zips (48), `submissions.zip` (1), and the EDGAR index pages for item 2.02 times (about 24,000 requests at the SEC limit).
  - The quotes:
    - a guard quote for every firing that passes the quote-free refusals, about 36,000;
    - an entry quote for every session a ticket could fill on: 1 for `open`, 3 for `breakout`, about 80,000. All candidate sessions are fetched, so the cache never records which session filled.

  In all, **about 116,000 Alpaca requests ≈ 10 hours at 190 a minute**.
- **Compute,** with every input cached and no network:
  - detectors, about 40 minutes;
  - issuing and simulating about 37,000 tickets, plus pools, about 60–120 minutes. Pools are shared per key (D, decile, mode, H): about 20,000 keys × about 170 members, about 3.4 million simulations of at most 45 sessions;
  - 1,000 draws over about 35,000 tickets, a few minutes.

  The pool time is an estimate. It is not measured on real bars before the blind run.
- **Resumption:**
  - `ensemble.yml` chains jobs of at most 5.5 hours, like `zba-v2.yml`.
  - The `--prefetch` jobs fetch inputs only and save the input cache (`actions/cache`) after each job. A job that stops resumes from the cache, because every input is keyed by its request.
  - The last job, `--blind`, restores the full input cache and computes everything in memory in one process, without network. It writes only the report.
  - If it fails, it is rerun from scratch from the cached inputs. No partial result exists to resume from, by design.
- The workflow is triggered only by pushing the run branch `ensemble-run-blind`, after the owner's go-ahead.

---

### Task 1: Inputs (`bot/ensemble_data.py`)

**Files:** Create `bot/ensemble_data.py`; Test `tests/test_ensemble_counts.py` (the loader part).

**Interfaces (produces):**
- `load(start, end, symbols=None) -> Inputs`, with fields:
  - `sessions`, `cal`;
  - `rows` (filed, cik, symbol);
  - `filed` {sym: dates}, `ciks` {sym: {cik}};
  - `prices_by_cik` {cik: sorted [(filed, trade, acc, price)]};
  - `purchases`;
  - `series` {sym: phase A series};
  - `releases` (`probe_ensemble_data.Releases`).
- Inputs only: no result field.

- [ ] **Step 1: Failing tests.**
  - `test_prices_by_cik`: two symbols of one CIK share one price list; P, S and F only; amendments dropped.
  - `test_inputs_hold_no_results`: `Inputs` has exactly the fields above.
- [ ] **Steps 2–5:** run (FAIL), implement (reusing `probe_ensemble_data` and `probe_survivorship.load_filings` keyed by CIK), run the full suite (OK), commit.

### Task 2: Universe with the rename rule, deciles (`bot/signals.py`)

**Interfaces (produces):**
- `universe(day, inp) -> {symbol: {"adv", "decile", "bars_of"}}`, where `bars_of` is the symbol whose bars it uses (itself, or the recovered other symbol).
- `rename(sym, day, inp, first_pass) -> other | None`.
- `deciles(adv_by_sym) -> {sym: 0..9}`.

- [ ] **Step 1: Failing tests** (section 9 "Universe: point in time, the bad-price rule", plus the rename rule at each condition):
  - `test_universe_point_in_time`: the Form 3/4/5 365-day window; $5; $10M; 252 bars in the segment; a bar on D.
  - `test_rename_recovers`: exactly one other symbol with a bar on D, the price within a factor of 2 of the CIK's latest P/S/F price, the other not in the first-pass universe.
  - `test_rename_refuses`: two other symbols; a price outside ×2 (2.01 and 0.49 at the edges); no CIK price in 365 days; the other already in the universe (then dropped as a duplicate).
  - `test_deciles_ties`: a known ranking with ties by symbol; n not divisible by 10.
  - `test_no_look_ahead`: bars after D removed give the same universe.
- [ ] **Steps 2–5:** as above.

### Task 3: Detectors (`bot/signals.py`)

**Interfaces (produces):** `s1(day, inp, u)`, `s2(day, inp, u)`, `s3_state(day, inp, u)`, `s4_state(day, inp, u)`, `firings(inp, start, end) -> [(day, signal, symbol)]`, with S3/S4 transitions and the 2017-01-03 warm-up of 4.1.

- [ ] **Step 1: Failing tests** (section 9 "Signals: each condition at its edge, no look-ahead … S2's acceptance time rule and its fallback"):
  - **S1:** 3 people, $100,000 and 30 days, each at its edge; identical lines count as one person; the 21-session block; re-firing after 21 sessions on a new filing; a non-session filing date moves to the next session; the symbol is in the universe that day; CIK grouping across padded CIKs.
  - **S2:** 1.05, 2× volume over the 20 sessions before D, the upper half of the range, each at its edge; no bar on the previous session means no firing; the acceptance time before or after 16:00 ET; the fallback without the 8-K condition (tested, unused while coverage ≥ 90%).
  - **S3:** 0.95 × the 252-session high and the top 20% of the D−147 → D−21 move, each at its edge; 21 sessions outside; the warm-up.
  - **S4:** the bottom 5% and ≤ −10% (both edges); the top half by liquidity; a reaction session in D−4 … D blocks it; the first session in the state.
  - **No look-ahead:** every detector gives the same result with the bars after D removed.
- [ ] **Steps 2–5:** as above.

### Task 4: Measurement 1a, K (counts only) and STOP

**Files:** `bot/ensemble_counts.py` (`k_report`), `tests/test_ensemble_counts.py`, and a throwaway workflow on `probe-ensemble`.

**Interfaces (produces):**
- `ended(inp) -> {sym: L}`, per C3.
- `k_counts(firings, inp, ended) -> {s: {"firings_end", "stock_sessions_end", "stock_months_end", "firings_all", "stock_sessions_all", "stock_months_all", "r_s", "r_s_months"}}`.
- `k_value(r, w) -> (K, K_max)`.
- `K_REPORT_KEYS`.

- [ ] **Step 1: Failing tests:**
  - `test_ended_with_rename`: a symbol whose CIK's other symbol continues within ×2 is not ended; one outside ×2 is ended; one ending on 2025-04-01 is not counted.
  - `test_window_edges`: 365 days, clipped to the period.
  - `test_r_s_by_hand`.
  - `test_k_and_b`: provisional w; K; K_max; b vs 0.03 at the edge, unrounded.
  - `test_k_report_whitelist`: keys only, and none of return, mean, median or win.
- [ ] **Step 2–4:** implement and run the full suite. **Step 5:** commit.
- [ ] **Step 6: Live run** on GitHub (counts only). Report: the four r_s with the counts behind each (both denominators), the provisional K, K_max, b and b_max. Commit the report and **stop for the owner**. If b > 0.03 R, the result is NO-GO ("data").

### Task 5: Measurement 1b, quote coverage (counts only) and STOP

**Interfaces (produces):**
- `sample(firings, seed=20261011, per=50) -> [(day, signal, symbol)]`.
- `quote_counts(sampled, inp) -> {(signal, year): {"sampled", "refused_quote_free": {reason: n}, "passing", "no_guard_quote", "cost_guard", "no_entry_quote"}}`.
- `weighted_share(counts, population) -> float`.

- [ ] **Step 1: Failing tests:**
  - `test_sample_stratified_and_seeded`: the same seed gives the same sample; strata with fewer than 50 are taken whole.
  - `test_quote_counts_by_hand`: each count on mocked quotes, including a locked quote, a quote older than 60 s, and the half-day close.
  - `test_weighted_share_edge`: exactly 5% does not stop; above 5% does.
  - `test_no_simulation_called`: `levels.fill`, `exits` and `simulate` are never called (patched to raise).
- [ ] **Steps 2–5:** implement, run the full suite, commit.
- [ ] **Step 6: Live run.** Report the counts per signal and year, the per-year and per-signal shares, and the weighted share against 5%. Give the sample size and the API calls (estimate C10, actual counted). Commit and **stop for the owner**. Above 5%: stop and report before building.

### Task 6: Issuing tickets (`bot/backtest.py`, synthetic bars only)

**Interfaces (produces):**
- `issue(firings, inp, quotes) -> (tickets, refusals)`, which applies section 3's refusals at $100 and the one-open-ticket rule (C14), and uses the rescaled series (C16).
- `windows(tickets)`.

- [ ] **Step 1: Failing tests:**
  - the default and signal modes and H;
  - each refusal at its threshold (15 bars, 2 shares, 1%, no usable quote, cost guard 10%);
  - one open or pending ticket per symbol and signal, with a pending breakout expiring;
  - different signals on one symbol;
  - a series break inside a ticket (C15);
  - open at the data end (C17).
- [ ] **Steps 2–5:** implement, run the full suite, commit. All on synthetic bars.

### Task 7: The random comparison (6.2, synthetic only)

**Interfaces (produces):**
- `pool(ticket, day_universe, windows, inp) -> [symbols]`.
- `pool_results(key, members, inp) -> array('d')`.
- `m(results)`.
- `draws(tickets, pools, k_from=0, n=1000) -> [A_k]`.
- `A(a)`.
- `comparability(counts) -> (quote_share, small_pool_share)`.

- [ ] **Step 1: Failing tests** (section 9 "Random comparison"):
  - the pool's membership rules: same decile, no ticket of any signal firing or open on D, the quote-free refusals at $100;
  - m_i;
  - dropping a pool with fewer than 20 members (counted), with 19 and 20 at the edge;
  - a draw reproducible from its seed;
  - A and A_k computed by the same function;
  - the 950th-smallest rule, with p = (1 + #{A_k ≥ A}) / 1001;
  - the two comparability shares at 5% and 2%.
- [ ] **Steps 2–5:** implement, run the full suite, commit.

### Task 8: The statistics (6.3, `bot/ensemble_stats.py`)

- [ ] **Step 1: Failing tests** (section 9 "Statistics"):
  - the hand example (x = (1, −1, 2 | 0, 1 | −2, 1, 1 | 3) gives x̄ 0.6667, SE 0.3966, t 1.68);
  - T10 and T5 on a known series;
  - the ten-block split on a period that does not divide evenly;
  - the five long blocks as pairs of the ten;
  - check 1's μ_min = 4.1 × the larger SE;
  - b = K × 0.0041 × 1 R;
  - μ_min + b ≤ 0.10 R at the edge;
  - the design effect.
- [ ] **Steps 2–5:** implement, run the full suite, commit.

### Task 9: The blind report (6.4, `bot/blind.py`)

**Interfaces (produces):**
- `BLIND_KEYS`.
- `report(x, c, counts, blocks, K, r_s, w_s) -> dict`, the only report builder.
- `write(report, path)`.

- [ ] **Step 1: Failing tests** (section 9 "Blind mode"):
  - `test_report_invariant_sign`: x → −x gives byte-identical JSON;
  - `test_report_invariant_shift`: x → x + 3.7 gives byte-identical JSON;
  - `test_whitelist`: any key outside `BLIND_KEYS` fails;
  - `test_never_in_report`: no x̄, A, A_k, mean, median or sum of g, a or x, no win rate, exit type, fill count, holding length or per-ticket value. The cost percentiles are allowed by the whitelist.
- [ ] **Steps 2–5:** implement, run the full suite, commit.

### Task 10: The gates and the journal (synthetic only)

- [ ] **Step 1: Failing tests.**
  - Each gate at its threshold (section 9 "Each gate at its threshold"):
    1. with b subtracted, x̄ > 0 and t ≥ 2.262 / 2.776;
    2. A strictly above the 950th A_k;
    3. 7 of 10 blocks and each half;
    4. without the top 1%, and with each signal removed;
    5. stress × 1.5.
  - The 6.6 outcomes.
  - The journal: its format and writer on synthetic tickets.
  - `--verdict` refuses without `--blind-commit` equal to HEAD and `--owner-go-ahead` (C24).
- [ ] **Steps 2–5:** implement, run the full suite, commit.

### Task 11: The driver and the blind workflow (not run)

**Files:** `bot/ensemble.py` (`--prefetch`, `--blind`, `--verdict`) and `.github/workflows/ensemble.yml` (chained, input cache only).

- [ ] **Step 1: Failing tests** (section 9 "nothing written besides the report", and the point-2 enforcement):
  - `test_blind_writes_only_the_report`: a synthetic end-to-end `--blind` with every network call mocked records every file write (`open`, `gzip.open`, `Path.write_*`, `os.replace`, `json.dump`). The only result file is the report. Input caches written by `--prefetch` hold inputs only.
  - `test_blind_prints_no_result`: stdout, stderr and `common.log` hold no x, g, a, c value. A planted `print(x̄)` is shown to fail it.
  - `test_prefetch_is_outcome_free`: the quote requests cover every candidate entry session, independent of fills.
  - `test_workflow_uploads_the_report_only`: `ensemble.yml` uploads only the report artifact and is triggered only by the run branch.
- [ ] **Steps 2–5:** implement, run the full suite, commit. **Never run on real bars in phase C.**

### Task 12: Independent review, then STOP

- [ ] A reviewer who did not write the code (the most capable model) reviews:
  - the code and the tests against sections 2–7 and 9;
  - the blind discipline;
  - every output path of `--blind`. This is 6.4 point 3: the reviewer confirms there is no other output path.

  Confirmed findings are fixed with tests.
- [ ] Write the phase C report: no outcome; the two measurements; the choices C1–C25 with the owner's rulings; the blind-run estimate.
- [ ] Open a PR to `main`. **Do not merge.** **Stop:** the blind run needs the owner's separate go-ahead.
