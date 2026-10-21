# T2 report — intraday tickets: pre-outcome fixes, development run, confirmation

- **Branch:** `engine/t2`, from `engine/t1` (2252531). Pushed as a backup, not merged.
- **Decisions:** [ADR 0017](adr/0017-t2-decisions.md).

## Verdict

**Stopped before confirmation (ADR 0017 item 2). No gate verdict. The confirmation window was not read.**

- **Why it stopped.**
  - The ORB 5 pipeline check came out at **−0.494R** net on development, outside its band [−0.36R, +0.39R].
  - The investigation found **no defect** (section 3), so the rule required stopping before confirmation.
  - The CLI refuses a confirmation run, and a test asserts that.
- **The development evidence is decisively negative** (reported only):
  - H1 mean net R **−0.154** (t −5.9, CI −0.206 to −0.104).
  - It is negative in every year, for every setup and for both asset classes.
  - The issued-basis control difference is **−0.006R** (t −0.31), so the setups do no better than random entries on the
    same stocks in play.

## 1. Stage 0

### Survivorship (item 7)

- **Added:** 3,147 names: 2,588 delisted or acquired, 623 renamed old tickers, 64 in both. Each is valid only before
  its delisting or rename.
- **Watchlists:** 744 of 1,184 changed, with 1,347 new symbol-days.

| H1 tickets | T1 | T2 | Added | Removed |
|---|---:|---:|---:|---:|
| Development | 4,587 | 4,751 | 453 | 289 |
| Confirmation | 3,143 | 3,137 | 90 | 96 |

- **Duplicates:** 0, after one remedy.
  - The cause: Alpaca had returned AHPAW's daily bars for a T1 request that never named it, and T2 then fetched them
    again (210 keys, identical values).
  - The fix (b1abbb4): fetches now keep only requested symbols.
- **Leakage:** pass. 30 sessions, 690 cut-offs, 4,172 ticket checks.
- **Signed sets:** all 8 were re-signed (hashes in ADR 0017 §3); the rules hash 199a3d60… is unchanged.

### Parity (item 6)

| Day | Journal | Tickets | Watchlist | Unexplained |
|---|---|---|---|---:|
| 2026-10-20 | a83748e (the only journal commit) | 4/4 identical (SCO orb5, UGL orb15, OKLO orb15, KVYO vwap_hammer); 0 replay-only | 11/11 identical; VICR ATR differs at 1e-15 | **0** |

### Order and timing

| Event | Time (UTC, 10-21) |
|---|---|
| ADR 0017 (8ce43e7) | 12:22:18 |
| `authorize` (773c3a5) | 12:33:26 |
| **First outcome** (development run start) | **12:33:34** |

## 2. Development run 1 (reported only)

| Set | Issued | Fill rate | n | Mean net R | SE (CR1) | t | Bootstrap 95% CI | Gross R | Cost R |
|---|---:|---:|---:|---:|---:|---:|---|---:|---:|
| **H1** | 4,751 | 79.7% | 3,786 | **−0.154** | 0.026 | −5.90 | −0.206 / −0.104 | +0.042 | 0.196 |
| Controls | 14,253 | 80.4% | 11,455 | −0.145 | 0.020 | −7.20 | −0.184 / −0.106 | +0.054 | 0.199 |
| H1, 20%-cap sizing | 4,751 | 88.2% | 4,189 | −0.348 | 0.026 | −13.57 | −0.396 / −0.296 | +0.029 | 0.377 |
| H1, SIP signal | 6,613 | 75.0% | 4,959 | −0.117 | 0.024 | −4.78 | −0.163 / −0.069 | +0.060 | 0.177 |
| ORB 5 | 1,688 | 77.1% | 1,302 | **−0.494** | 0.044 | −11.10 | −0.579 / −0.406 | −0.090 | 0.403 |

- **Realized MDE** (H1, 2.8416 × SE): 0.074R.
- **Without the top 1% of trades,** H1 is −0.179R (t −6.90).

| H1 by setup, asset class and year | n | Mean | 2022 | 2023 | 2024 |
|---|---:|---:|---:|---:|---:|
| VWAP hammer | 1,899 | −0.180 | −0.105 | −0.263 | −0.160 |
| VWAP engulf | 930 | −0.145 | −0.104 | −0.171 | −0.156 |
| Flag | 957 | −0.112 | −0.154 | −0.066 | −0.126 |
| Stocks | 3,509 | −0.158 | −0.120 | −0.188 | −0.160 |
| ETPs | 277 | −0.102 (CI −0.280 / +0.074) | −0.078 | −0.226 | +0.029 |
| **All H1** | 3,786 | −0.154 | −0.115 | −0.190 | −0.150 |

**Gate (ii)** (computed on development, reported only):

| Basis | n | Mean | t | Notes |
|---|---:|---:|---:|---|
| Issued (ADR 0017) | 4,751 | −0.006 | −0.31 | CI −0.045 / +0.033; 0 tickets without controls |
| Filled (T1, reported only) | 3,711 | −0.011 | −0.45 | 75 dropped for having no filled control |

**Counts (development H1):**
- liquidity rejections at the fill minute: 467 (controls 1,411; ORB 5 250; SIP signal 966);
- no-quote skips: 0;
- 0-share skips: 1;
- control shortfall: 0.

**Book.** $2,500 cash account, one position at a time, T+1; reported only:
- it ends at **$889 (−64.4%)**, against SPY **+30.3%** over the same window;
- 434 trades were taken;
- 2,490 tickets were blocked by settlement and 862 by an open position.

## 3. The ORB 5 investigation (no defect)

1. **Independent re-simulation.** Fresh code applying the same written rules agreed with the evaluator on **200 of 200**
   seeded ORB 5 tickets, status and net R alike.
2. **Costs.** 0.403R per trade: spread and the stop's extra tick 0.281R, commissions and fees 0.122R. ORB 5's stop is
   the 10%-ATR floor, so R is small (median 0.59% of price). For H1 the median R is 0.87% and the cost 0.196R.
3. **Gross gap to the live bot's own outcome rule.** The table changes one pre-registered ADR 0016 rule at a time. The
   final row equals the evaluator's gross exactly.

| Rule applied | Gross R | n |
|---|---:|---:|
| Bot rule | +0.020 | 1,510 |
| + target needs a trade-through | +0.006 | 1,510 |
| + **a stop counts in the fill minute** | **−0.143** | 1,510 |
| + order live at C + 60 s | −0.083 | 1,382 |
| + resting limit | −0.070 | 1,552 |
| + liquidity rejection | **−0.090** | 1,302 |

**Conclusion.** The band (a T1 detail anchored on ZBA's EOD-exit numbers) did not fit this bracket mechanism. No
failing test can pin a defect, so the stop rule applies.

## 4. Process gate

| Item | Result |
|---|---|
| Stage 0 | **PASS** |
| ADR 0017 and `authorize` before the first outcome | **PASS** |
| Development runs logged | **PASS** (1 run) |
| Exactly one confirmation run | **0 runs.** Stopped by ADR 0017's own rule; no confirmation was started |
| Golden set, unit and R4 tests | **PASS** |
| Engine suite | **PASS:** 194/194 |
| Eval suite | **PASS:** 119/119 (one T1 guard test, "no T2 ledger entry", was updated to assert the T2 ledger state) |
| Stockbot | 901/902, exactly `test_review…test_corrections_are_saved_before_alerts`, as allowed (fix in [danielsuisa/stockbot#25](https://github.com/danielsuisa/stockbot/pull/25)) |
| Allowed paths, no data files tracked | **PASS.** The only fixture is the 4.5 KB live-journal JSON |
| Disk floor | **PASS:** 9.75 GB free |
| Push checks | See the final message |

## 5. Records

**Commits:**

| Commit | Subject |
|---|---|
| 6cba707 | Survivorship |
| 8d1e1aa, c25edf3 | Loader and the no-outcome `check` (extra commits, before any outcome) |
| b1abbb4 | AHPAW fix |
| 27f6724 | Parity |
| 8ce43e7 | ADR 0017 |
| 773c3a5 | `authorize` |
| 5bebebc | Run, investigation, stop |
| (this) | Report |

**Trials ledger** (`t_trials.jsonl`), in order:
1. authorize;
2. development run 1 start and result;
3. orb_investigation (defect_found false);
4. stop_before_confirmation.

**Stage 0 fetches:**
- 24,425 requests with 0 retries, 08:19–11:42Z (3.4 h): daily fill 58, stage 1 2,594, denominators 975, top 20 1,328,
  grid 19,470.
- The development run took 25 minutes.

**Results:** `derived/t2/development_run1.json` and `development_run1_tickets.json`.

## 6. Decisions needed

The recommendations are marked **(Rec.)**.

1. **The H1 line.**
   - **(Rec.: close it, NO-GO on development evidence, without spending confirmation.)** H1 loses about 0.15R per
     trade after IBKR costs at $2,500, in every year, setup and asset class. It does no better than random entries on
     the same stocks.
   - The alternative is a reported-only confirmation run under a new user decision. It is not recommended: the
     window would be spent with no realistic path to (i) passing.
   - The unread confirmation window stays clean for a future pre-registered hypothesis.
2. **Resume S1 (swing tickets)** as planned after T2 **(Rec.: yes)**.
3. **Merge [danielsuisa/stockbot#25](https://github.com/danielsuisa/stockbot/pull/25)** (clock fix; 902/902 on its
   branch) **(Rec.: merge)**.

## 7. Next step (prerequisites only)

- A user decision on item 1. If the line is closed, a closing ADR like ADR 0007/0011.
- Then the S1 prompt, resuming from `engine/s1`.
