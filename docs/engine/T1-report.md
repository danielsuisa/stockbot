# T1 report — intraday tickets: pre-registration, data, signed tickets, evaluator (no outcomes)

- **Branch:** `engine/t1`, from `engine/s0` (886693e). Pushed as a backup, not merged.
- **Milestone:** M1 ([ADR 0015](adr/0015-intraday-ticket-validation-m1.md)).
- **Design:** [ADR 0016](adr/0016-t1-pre-registered-design.md).
- **Gate: PASS, with one exception outside T1.** Ingestion ended with 0 duplicates after one remedy, parity found 0
  unexplained mismatches, and the leakage tests, golden set, engine and eval suites and push checks pass.
  - **The exception:** stockbot runs 901 of 902. The one failure is a date-dependent stockbot test,
    `test_review.Signals.test_corrections_are_saved_before_alerts`.
  - It fails identically on the untouched `engine/s0`, and its code is unchanged on `origin/main`.
  - It passed on 2026-10-20 at 22:40Z and fails from 2026-10-21.
  - T1 changed no file under `bot/`, `tests/`, `data/` or `.github/` (section 8).

**No outcomes.** No return, P&L, fill or R was computed for any real ticket, signal or control. The evaluator ran
only on synthetic fixtures. Its CLI refuses real sets (exit 2) because `scalp_contracts/t_trials.jsonl` holds no T2
entry.

## 1. Commits, in order (UTC)

| # | Commit | Time | Subject |
|---|---|---|---|
| 1 | 2d8a19c | 10-20 18:02:33 | docs: ADR 0015 intraday ticket validation as M1 + rules |
| 2 | d9565d8 | 10-20 18:05:16 | docs: ADR 0016 T1 pre-registered design |
| 3 | d3a3b33 | 10-20 18:24:27 | feat(L0): top-up, stocks-in-play replay inputs, IEX+SIP minute bars, NBBO grid |
| — | — | 10-20 **18:24:37** | **First request** (stage-1 smoke, 2022-02-01) |
| 4 | f23cda9 | 10-20 18:30:46 | feat(intraday): stage 1/2 replay, tickets, controls (signed) + parity + leakage tests |
| 5 | cbd48cb | 10-20 18:30:46 | feat(eval): intraday ticket evaluator + golden set + T2 guard |
| 5a | b2cc1e9 | 10-21 01:42:51 | fix(L0): top-up daily window ends 20:00 ET of the last session |
| 5b | 5124483 | 10-21 01:49:39 | fix(L0): daily coverage of the full universe before the prefilter |
| 5c | 3e4ca11 | 10-21 05:55:50 | fix(L0): never store a held minute-bar key twice; remedy 65 duplicate keys |
| 6 | (this) | | docs: T1 report |

- **ADRs before the first request.** ADRs 0015 and 0016 were committed 22 and 19 minutes before the first request.
- **Code before every run that used it.**
  - The first stage-3 build ran at 21:13 after f23cda9.
  - Every rerun followed its fix commit.
  - The evaluator never ran on real data.
- **The three fix commits (5a–5c)** are outside the planned list. Each fixes a T1 data defect, test first (section 7).
  None touches a rule, a parameter or the design.

## 2. Stage 0

### Credentials

| Check | Result |
|---|---|
| `ALPACA_KEY_ID`, `ALPACA_SECRET_KEY`, `SEC_USER_AGENT` | All set, via `envload`; no value printed |
| Is either Alpaca value in a chat or transcript? | No. Scanned 701 transcript files; neither value occurs in any of them |

### Live bot

`bot/intraday.py` at `origin/main` is pinned at **09bfa55c7445762cf021e6a3a1d8128527f07eba** (2026-10-17 16:03:49
+03:00).

### Live-mode inputs: feed and timing

These are taken from `bot/intraday.py` and `bot/alpaca.py` at that SHA. The replay uses each input exactly as the
bot does.

| Stage | Input | Bot call | Feed | Live timing | Replay input |
|---|---|---|---|---|---|
| 1 | Universe | `alpaca.assets()`, active; NYSE/NASDAQ/ARCA, ≤ 5 letters, no fund words | — | Run start (dispatched 09:37) | The store's asset snapshot of 2026-10-17, active **and** inactive (ADR 0016 §1) |
| 1 | Daily stats: close, ATR14, 20-session ADV and $ADV | `alpaca.daily(..., before[-31], prev_day)` | **SIP**, raw `1Day` | Bars ≤ t − 1 | `raw_bars_1d` SIP raw, ≤ t − 1 |
| 1 | Pre-market volume and last price | `volume_since(04:00, 09:20)` | **SIP** `15Min` (bars 04:00–09:15) | Called at about 09:38 | SIP minutes 04:00–09:29 |
| 1 | The open (for the gap) | `live_bars(09:30, 09:30, "5Min")` | **IEX** | About 09:38; the 09:30 bar is complete | Open of the first IEX minute in 09:30–09:34. If there is none, the last SIP pre-market price (the bot's fallback) |
| 1 | Re-rank: opening relative volume | `opening_bars(day)` and the 14 prior sessions | **SIP** `5Min` 09:30 bar | The 09:55 scan | SIP minutes 09:30–09:34, today and the 14 prior sessions |
| 2 | Candles and VWAP | `live_bars(09:30, now)` | **IEX** `1Min` | Each candle close C + 25 s | IEX minutes before C |
| 2 | Bracket | `bracket()` | — | At C + 25 s | Copied verbatim; sizing replaced as in ADR 0016 §4 |
| — | Outcome (not used by T1) | `minute_bars` | SIP | After 16:15 | — |

## 3. Ingestion

### Coverage

| Step | Store / part | Sessions | Symbol-days | Requests |
|---|---|---|---|---|
| Top-up, calendar | `calendar`, 2026-10-19 → 2026-10-20 | 2 | — | 2 |
| Top-up, corporate actions | `actions-t1` | 2 | 59 rows | 1 |
| Top-up, daily bars | `sip-1d-t1-2026-10-20b`, universe + SPY | 2 | 10,309 rows | 18 |
| Daily coverage fill (remedy, §7) | `sip-1d-t1f0`, `sip-1d-t1f1` | 2022-01-03 → 2026-10-16 | 1,578 + 1,566 symbols | 232 |
| Stage-1 inputs | `t1s1`, plus pass `t1s1b` | 1,184 | 874,926 prefilter (dev 457,422; conf 417,504; top-up 2) | 13,024 + 2,368 |
| Denominators | `t1den`, `t1denp`, `t1denb`; `t1den2` after the remedy | 1,195 + 9 + 652 prior sessions | 23,609 | 1,864 |
| Top 20, full session | `t1top`, plus pass `t1topb` | 1,184 | about 23.7k | 2,368 + 2,314 |
| NBBO grid | `sip-t1grid`, plus pass `sip-t1gridb`; rule `t1_grid_v1` | 1,184 | Top 20 × 24 times | 45,631 + 29,052 |

- **Windows:**
  - development 2022-02-01 → 2024-12-31 (733 sessions);
  - confirmation 2025-01-02 → **2026-10-16** (449 sessions), the last complete session in the store when ADR 0016
    was committed;
  - top-up (parity only) 2026-10-19 → 2026-10-20, the last complete session when the top-up ran.

### Gate checks

| Check | Result |
|---|---|
| Duplicate keys | **0** in all 11 tables of the I1 check, plus `raw_bars_1m` on every date T1 wrote (343,731,513 rows). One remedy was needed (§7) |
| Sealed-window rows | **0** partitions dated ≤ 2021-12-31 or ≥ 2026-11-02 in the tables T1 wrote. Every request passed `check_range` and T1's own guard before the network |
| Request range | Earliest start 2022-01-03 05:00Z. Latest end 2026-10-21 03:59:59Z, which is still the 10-20 ET session (the refused 403 request, §7). Nothing on or after 2026-11-02 |
| Fetch lists | Only from data ≤ the decision time: daily bars ≤ t − 1, then the stage-1 watchlist decided at 09:38 from SIP ≤ 09:29 and IEX ≤ 09:34 |
| Disk floor (8 GB) | Held. 11.67 GB free before, **9.95 GB** after, about 1.7 GB used |
| Data files tracked in git | None. The store and derived outputs live under `D:\scalp-data` |

### Run facts

- **Requests:** 96,873 HTTP 200 and 1 HTTP 403, with 0 retries.
- **Download:** 11.5 GB.
- **Wall clock:** 18:24:37Z → 05:43:30Z, **11.3 h**, under the 24 h budget.
- **Request rate:** 115–170 a minute.
- **Projections** were logged every 20 days. None exceeded 24 h.

## 4. Counts only (no outcomes)

The table below is by window and year. **SIG** = setups detected on a candle at a scan close (several can fire on
one candle). **H1** = the bot's selection restricted to H1 setups in 09:40–11:30.

| Window | Year | Sessions | Prefilter sym-days | In play (top 20) | After re-rank | SIG hammer | SIG engulf | SIG flag | SIG ORB5 | H1 hammer | H1 engulf | H1 flag | 0-share | Controls (short) | ORB5 tickets | SIP-signal H1 (h / e / f) |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Development | 2022 | 231 | 176,608 | 4,612 | 3,410 | 823 | 374 | 560 | 563 | 661 | 330 | 305 | 1 | 3,888 (0) | 544 | 962 / 518 / 336 |
| Development | 2023 | 250 | 139,321 | 4,941 | 3,801 | 1,029 | 471 | 815 | 545 | 798 | 406 | 446 | 0 | 4,950 (0) | 529 | 1,148 / 640 / 513 |
| Development | 2024 | 252 | 141,493 | 5,037 | 3,859 | 1,025 | 426 | 875 | 616 | 807 | 368 | 466 | 0 | 4,923 (0) | 593 | 1,176 / 648 / 511 |
| **Development** | **all** | **733** | **457,422** | **14,590** | **11,070** | **2,877** | **1,271** | **2,250** | **1,724** | **2,266** | **1,104** | **1,217** | **1** | **13,761 (0)** | **1,666** | **3,286 / 1,806 / 1,360** |
| Confirmation | 2025 | 250 | 206,032 | 5,000 | 3,961 | 1,093 | 498 | 953 | 681 | 854 | 440 | 509 | 0 | 5,409 (0) | 647 | 1,179 / 709 / 530 |
| Confirmation | 2026 | 199 | 211,472 | 3,980 | 2,960 | 812 | 395 | 701 | 565 | 643 | 337 | 360 | 0 | 4,020 (0) | 529 | 852 / 565 / 381 |
| **Confirmation** | **all** | **449** | **417,504** | **8,980** | **6,921** | **1,905** | **893** | **1,654** | **1,246** | **1,497** | **777** | **869** | **0** | **9,429 (0)** | **1,176** | **2,031 / 1,274 / 911** |

- **H1 totals:** development **4,587**, confirmation **3,143**.
  - That is about 1,700 a year, above the audit's 900-a-year power threshold.
  - Every H1 ticket has exactly 3 controls; none fell short.
- **Liquidity rejections are not counted in T1.**
  - A rejection needs the fill minute's SIP volume, and finding the fill minute is the evaluator's fill rule.
  - Running it on real tickets is the outcome step that ADR 0016 §7 reserves for T2.
  - It is reported in T2.

**Signed sets.** Ed25519 with key b4388290d66a62da. Verified from the eval side with the digest re-derived
independently.
- Rules hash: 199a3d60227e50ad5e2c9026fd3821ebe1445913406fcbabefc60c79d001e2cc.

| Window | Set | Rows | sha256 (first 16) |
|---|---|---:|---|
| Development | H1 | 4,587 | 02845a5e576f1164 |
| Development | CONTROLS | 13,761 | d594add2f6cccd41 |
| Development | ORB5 | 1,666 | 34f0672948e6c510 |
| Development | H1_SIPSIG | 6,452 | cf5d9c1cb927cd37 |
| Confirmation | H1 | 3,143 | 3682ed75b8113df7 |
| Confirmation | CONTROLS | 9,429 | fd41ddde8dcc30b3 |
| Confirmation | ORB5 | 1,176 | df78b4c3ed8e31ac |
| Confirmation | H1_SIPSIG | 4,216 | 6ad343387bf836b8 |

The sets from before the coverage remedy are kept, unread, in `derived/t1/sets_v1/`.

## 5. Power: the expected confirmation MDE

- **Formula:** MDE = 2.8416 · SD / √n, with SD = 1.47R (audit §4) and SE taken as SD / √n.
- **Confirmation, upper bound:** n = 3,143 H1 tickets, the upper bound on fills.
  - **MDE ≈ 0.075R.**
- **Confirmation, 60% fill rate (assumed):** n ≈ 1,886, **MDE ≈ 0.096R**.
- **Development:** n = 4,587 tickets, MDE ≈ 0.062R.
- **The real MDE is wider.** Clustering by day widens the SE, and that design effect is unknown until T2 measures
  it.

## 6. Parity with the live bot

**Sessions since 2026-10-17 with a journal.**
- `data/intraday_state.json` has one commit in git history: a83748e (2026-10-20 15:30Z, the morning window).
- 2026-10-19: no state commit.
- 2026-10-20 power window and follow-up: no commit either, so the journal holds the morning window only.
- The replay used the bot's sizing test: E = $500 (consistent with every journaled share count, E ∈ [$492, $544)),
  p = 0.005 (`P_CAP_UNPROVEN`).

| Ticket (2026-10-20) | Live (entry stop / limit / stop / target / R / shares) | Replay | Result |
|---|---|---|---|
| SCO orb5 09:40 | 16.41 / 16.44 / 16.34 / 16.54 / 0.07 / 6 | identical | **match** |
| UGL orb15 09:50 | 44.99 / 45.05 / 44.86 / 45.26 / 0.13 / 2 | identical | **match** |
| OKLO orb15 09:50 | 27.20 / 27.31 / 26.75 / 28.11 / 0.46 / 3 | identical | **match** |
| KVYO vwap_hammer 10:00 | 19.43 / 19.48 / 19.30 / 19.70 / 0.14 / 5 | identical | **match** |

- **Tickets the replay issued that the live journal lacks:** 0, over both windows.
- **Watchlist after the re-rank:** the same 11 symbols, in order. Every journaled field matches bit for bit: gap,
  pre_vol, pre_rel, open, prev_close, ADV and open_relvol.
- **The one difference:** VICR's ATR, 22.873307142857136 live vs …133 in the replay. That is floating-point summation
  order in the 15th significant digit, and it changes no decision.
- **Unexplained mismatches: 0.**
- **The first parity run found 2 misses** (SCO, UGL). They led to the coverage remedy (§7). The output of that run
  is kept in `sets_v1/parity_2026-10-20.json`.

## 7. Defects found and fixed (each test first)

1. **Top-up daily window (b2cc1e9).**
   - The request ended at the calendar day's end. When the top-up ran (21:42 ET), that was within the free plan's
     15-minute limit, so Alpaca answered HTTP 403.
   - Nothing was written.
   - Fix: the window ends at 20:00 ET of the last session.
2. **Universe coverage (5124483).**
   - The daily store held only the symbol sets earlier phases had requested. The replayed prefilter therefore dropped
     **2,184 of 6,645** universe symbols for part or all of the range: warrants, units and rights, but also liquid
     ETPs such as SCO, UGL, AGQ and BOIL.
   - This shrank the universe without anyone deciding it, which T1 must not do.
   - Fix:
     - `Daily()` refuses to load while any universe symbol was never requested for a session range;
     - a fill step fetched exactly the gaps (232 requests);
     - a second pass (`b` parts) fetched only what no earlier part held;
     - the watchlists and sets were rebuilt and re-signed.
   - Effect: 1,157 of 1,184 watchlists changed, adding 5,990 symbol-days. Parity then matched 4 of 4.
3. **Duplicate keys (3e4ca11).**
   - Pass `t1s1b` re-fetched FNGU's SIP 09:30–09:34 on 13 sessions in August 2023. The denominators part already
     held those minutes: 65 keys, identical value for value.
   - Remedy, with the I2 routine and a T1 reason:
     - the 13 `t1den` files were set aside as `.orphan`;
     - their other rows were rewritten as `t1den2`.
   - `fetch_minutes` now drops any key a part already holds before it writes. Duplicates are now 0.

The replay inputs did not change: the set-aside rows are held, identical, in `t1s1b`.

## 8. Tests

| Item | Result |
|---|---|
| Leakage, synthetic | Pass. Perturbing or deleting every bar after the signal close changes nothing, and every scan reads minutes before its close (`test_t1_replay`) |
| Leakage, real | Pass. 30 seeded sessions, 690 perturbed cut-offs, 4,183 ticket comparisons, 0 violations (`derived/t1/leakage.json`) |
| Rules copy | Pass. Nine verbatim blocks hash to the bot's text at 09bfa55, and the bot's own test vectors pass against the copy (`test_intraday_rules`) |
| Golden set, 16 cases | Pass. Cases 1–14 and 16 are in `scalp_eval/tests/test_intraday_eval.py`, all hand-computed; case 1's net R of 1.925747 is worked to the cent from the Tiered fee schedule. Case 15 (control draw) is in `test_t1_replay` |
| CLI guard | Pass. Real sets are refused without a T2 `authorize` line; the repository ledger is empty |
| R4 static tests | Pass, engine and eval |
| Suites | **engine 187 pass; eval 114 pass; stockbot 901 of 902.** The failure is `test_review.Signals.test_corrections_are_saved_before_alerts`; detail below |

### The stockbot failure

- **The test.** It saves a correction for a fixed day, `20260923`, and then expects a correction message.
- **When it broke.** It passed in this session's run at 2026-10-20 22:40Z and fails on 2026-10-21.
- **It is not T1.**
  - It fails the same way on `engine/s0`, at 886693e, without T1.
  - `tests/test_review.py` and `bot/scan.py` are identical on `engine/s0` and `origin/main`; the test was last
    changed on 2026-10-09.
  - `git diff engine/s0 -- bot tests data .github` is empty on `engine/t1`.
- **The cause** appears to be a window measured from the fixed date that the calendar has now passed.
- **The live repository's own test run will fail the same way.** Fixing it is stockbot's change, under its own
  change control, not T1's.

## 9. Decisions needed

The recommendations are marked **(Rec.)**.

1. **Accept the coverage remedy** (§7.2) **(Rec.: accept).**
   - The universe is now the bot's own. It includes ETPs whose names carry no fund word (ProShares, for example), as
     the live bot does.
   - Excluding them would change a frozen rule.
2. **ADR 0016 T1 details.** Each can be overruled in the T2 prompt.
   - **(a) Control geometry.** The parent's R, target multiple and limit offset, with the trigger at the drawn
     candle's high + 1 tick. **(Rec.: keep.)**
   - **(b) Gate (ii)** leaves out tickets with no filled control and counts them. **(Rec.: keep.)**
   - **(c) Resting-limit and same-minute rules** (§4 of the ADR). **(Rec.: keep.)**
   - **(d) ORB 5 defect band** [−0.36R, +0.39R]. **(Rec.: keep.)**
3. **Liquidity-rejection counts** are moved to T2, where fills are allowed **(Rec.: accept)**.
4. **Writing the T2 real-set loader** under the T2 prompt, before the T2 `authorize` ledger line
   **(Rec.: authorize in the T2 prompt)**.
5. **Stockbot's expired-date test** (section 8) **(Rec.: fix it in stockbot, as its own change)**.
   - Until then, the "stockbot 902" check reads 901 of 902 on every branch.
6. **Parity scope** **(Rec.: accept 10-20 as T1's parity session)**.
   - Only one live journal exists (the 10-20 morning window).
   - A longer parity run could commit the bot's daily state, but that would touch `data/`/`.github/`, which is not
     T1's to change.

## 10. Next step: T2 prerequisites only (T2 not started)

- A T2 prompt that settles section 9 and authorizes:
  - the real-set loader;
  - one development run;
  - then one confirmation run.
- One line in `scalp_contracts/t_trials.jsonl`, `{"phase": "T2", "kind": "authorize", ...}`, written under that
  prompt.
- Unchanged:
  - **S1 stays on hold** until after T2.
  - `forward_2026q4` stays sealed until it is ingested after 2027-01-29 under a ledger record.

## 11. Push

The backup-push result is in the final message of the T1 session. `push_check.py` was run on the head that contains
this report, and `engine/t1` was pushed, not merged.
