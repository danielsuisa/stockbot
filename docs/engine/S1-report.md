# S1 report — swing tickets: close intraday H1, then pre-registration, data, signed tickets, evaluator (no outcomes)

- **Branch:** `engine/s1`, from `engine/t2` (a8d0a7b). Pushed as a backup, not merged.
  - The on-hold S1 of 2026-10-20 (ADR 0014, built from `engine/s0`, never pushed) is kept as `engine/s1-hold`. Its
    code was ported and amended.
- **Design:**
  - [ADR 0018](adr/0018-intraday-h1-ticket-line-closed.md) (the intraday H1 line is closed);
  - [ADR 0019](adr/0019-swing-ticket-implementation.md) (the swing implementation, A1–A8).
- **Gate: PASS.**
- **No outcomes.** No fill, return, P&L or R was computed for any real ticket or control. The evaluator ran only on its
  21 synthetic golden cases and unit fixtures.
  - The CLI refuses a real set:

    ```
    refused: no S2 authorize record in scalp_contracts/s_trials.jsonl; S1 evaluates synthetic data only
    (ADR 0019 section 8)
    ```

    `python -m scalp_eval.swing --window confirmation --pubkey …` exits with code 1. `s_trials.jsonl` is empty.

## 1. Order (UTC, 2026-10-21)

| Commit | Time | Subject |
|---|---|---|
| 35aa05e | 13:40:55 | docs: ADR 0018 close intraday H1 ticket line + rules |
| 6117d9f | 13:45:20 | docs: ADR 0019 swing-ticket implementation design + amendments A1-A8 |
| da044e1 | 13:47:31 | feat(L0): EDGAR 8-K items, acceptance headers, top-up, minute bars, 09:35 samples |
| d2d825b | 13:47:31 | feat(swing): signals, tickets, controls (signed) + leakage tests |
| — | **13:48:00** | **First ingestion request** (SEC headers) |
| 5ee230c | 13:48:41 | feat(eval): swing bracket evaluator + golden set + S2 guard |
| (this) | | docs: S1 report |

- **ADR 0019 came 2 min 40 s before the first request.**
- **Run commits.** Every run used committed code: the ingestion steps used da044e1 and d2d825b, and the build ran at
  15:44Z.
- **The evaluator commit** came 41 s after the first request. It takes no part in ingestion and never ran on real data.

## 2. Stage A

- **ADR 0018 is Accepted.** It records:
  - **Evidence:** H1 −0.154R (t −5.9); random entries −0.145R; setups vs random −0.006R (t −0.31); the 20% cap
    −0.348R; ORB 5 −0.494R.
  - **The lesson:** the setups carry no information beyond the selection, and costs dominate.
  - **The intraday confirmation window** stays unread.
  - **Reopening a closed line** needs a new user decision and a new hypothesis family.
- **The `scalp_engine/CLAUDE.md` rule:** "Engine active only for the swing-ticket line (ADR 0012, 0013). Closed lines:
  intraday models (0007), intraday H1 tickets (0018), insider signals (0011). No outcome before a pre-registered ADR
  is committed."
- **The `ARCHITECTURE.md` banner and the README index** were updated.

## 3. Stage 0

- **Credentials:** all three are set through `envload`. Neither Alpaca value occurs in any of 458 transcripts.
- **`engine/t2` state:** a8d0a7b, clean.
- **Rules hash:** SHA-256 of ADR 0013 §1, as defined in ADR 0019, is **`9f0475fad96c4f7c102d62ad450609b14ee6336f44f78105b0ff9ed950e7b80e`**.
  - A test recomputes it from the ADR file, and every signed set carries it.
  - ADR 0014's `23207f95…` used an unrecorded boundary and is superseded.

## 4. Counts (Stage 3; no outcomes)

Windows are by signal date: development 2022-10-01 → 2024-12-31, confirmation 2025-01-01 → 2026-09-17.

| Window | Year | Set | Signals (tickets) | A2 earnings excluded | A3 excluded | Late 8-K (reported) | 0-share skips | Survivorship signals |
|---|---|---|---:|---:|---:|---:|---:|---:|
| Development | 2022 | H1 | 320 | 82 | 0 | 12 | 7 | 55 |
| Development | 2023 | H1 | 453 | 370 | 0 | 34 | 7 | 73 |
| Development | 2024 | H1 | 646 | 434 | 0 | 70 | 22 | 60 |
| **Development** | **all** | **H1** | **1,419** | **886** | **0** | **116** | **36** | **188** |
| Confirmation | 2025 | H1 | 562 | 442 | 0 | 34 | 15 | 39 |
| Confirmation | 2026 | H1 | 593 | 370 | 0 | 46 | 36 | 9 |
| **Confirmation** | **all** | **H1** | **1,155** | **812** | **0** | **80** | **51** | **48** |
| Development | all | S-b | 2,215 | — | 5 | — | 66 | 244 |
| Confirmation | all | S-b | 1,865 | — | 86 | — | 100 | 71 |
| Development | all | S-a | 11,666 | — | 12 | — | 486 | 1,200 |
| Confirmation | all | S-a | 9,511 | — | 709 | — | 662 | 487 |

- **Controls:** development 41,748, confirmation 34,215. They are drawn for H1/S-b (shared ids) and S-a, K = 3, with
  no shortfall.
- **Signed sets:** 8 digests, rule version `adr0019-s1`, key b4388290d66a62da:

  | Window | H1 | S-b | S-a | Controls |
  |---|---:|---:|---:|---:|
  | Development | 1,419 | 2,215 | 11,666 | 41,748 |
  | Confirmation | 1,155 | 1,865 | 9,511 | 34,215 |

- **Why A3 is concentrated in 2026.**
  - The universe maps a symbol to a CIK through Form 4 issuer symbols.
  - 193 of the 219 foreign-issuer (20-F/40-F) CIKs first appear in the Form 4 data in 2026. Before that they had no
    CIK and were already outside the universe.
  - From 2026, A3 excludes them by form. The universe is free of foreign issuers throughout.
- **Survivorship (A7).**
  - 3,147 names were added. 2,228 have daily bars, and 7,628 rows dated after a delisting or rename were dropped.
  - Daily coverage was verified (no gap) before any signal was computed.
  - S-b signals: 3,934 without the added names, 4,249 with them, before A3.
- **Leakage (real data).** At three cut-offs (2023-06-15, 2024-12-31, 2026-03-13), altering or deleting every daily
  bar after the cut-off left every ticket and control on or before it unchanged: 15,158, 57,048 and 87,892 rows
  checked. The synthetic future-perturbation and clock tests pass.

## 5. MDE update

Using S0 S7's assumptions, unchanged: σ = 1.3R, 50% fill, iid SE.

| Window | H1 tickets | Expected fills | MDE (2.8416 · σ / √fills) |
|---|---:|---:|---:|
| Confirmation | 1,155 | ≈ 578 | **≈ 0.154R** |
| Development | 1,419 | ≈ 710 | ≈ 0.139R |

- S0 expected about 1,050 confirmation fills (MDE ≈ 0.18R) before the earnings exclusion. The exclusion removes 41% of
  gap-and-hold signals.
- Week clustering widens these figures, and S2 measures by how much.

## 6. Coverage

| Item | Result |
|---|---|
| Acceptance times (Item 2.02 8-Ks of universe CIKs, filed 2022-09-01 → 2026-10-19) | **44,345 of 44,345 (100%)**: 44,320 fetched + 25 held; 0 missing |
| EDGAR submissions | 3,231 universe CIKs, all fetched on 2026-10-20 (filing range to 10-19; 0 requests now) |
| Minute bars, A5 fallback | 47,236 (symbol, session) pairs in scope: 10,558 already fully held, 36,678 fetched (36,643 with bars; 35 without trades) |
| 09:35 samples | 47,236 pairs: 45,911 valid quotes (97.2%); 1,325 with no quote within 60 s (A4 falls back to the prior close − 5 min); 0 unsampled |
| A6 top-up | calendar and corporate actions for 2026-10-19 → 20 already held (T1); daily bars for 4,839 pool symbols never requested (2,834 rows; requested symbols only); close − 5 samples on both sessions, 2,892 + 2,893 |
| Duplicate keys | **0 store-wide**: the I1 tables, `raw_bars_1m` on all T1/T2/S1 dates (358M rows; 330M on the 967 S1 dates), `swing_tickets` (`set`, `ticket_id`) and `issuer_filings` |
| `issuer_filings` co-registrants | 292 accessions appear under two issuer CIKs (co-registrant filings). That is the table's design (key accession + `ingested_at`; identity accession + CIK), not duplication. Both rows feed A2 |
| Sealed-window rows; requests on or after 2026-11-02 | **0; none.** Every request starts on or after 2022-10-04 and ends by 2026-10-21 03:59:59Z (the 10-20 session) |

**Recorded deviations (no effect on any signal):**
- The submissions were fetched on 2026-10-20 with filings through 10-19. The last A2 window ends 2026-09-17 20:00 ET
  and the late-filing window by 09-23.
- The close − 5 min top-up sampled ticket and control symbols only. `insider_prices.session_list` ends at I1's last
  session, so the pre-filter list was empty. Every symbol that can exit on those sessions was sampled.

## 7. Data choice (A5)

**The fallback scope (t + 1 … t + 3).** The `plan` step decided it from the signed ticket lists at 15:57:45Z, before
any outcome. The primary scope (t + 1 … t + 22) failed the disk floor.

| Scope | Pairs | Requests | Hours | Disk | Free after | Budget |
|---|---:|---:|---:|---:|---:|---|
| Primary | 247,092 | 11,321 + 5,832 | 1.68 | 1.86 GB | **7.71 GB** | FAIL (floor 8 GB) |
| Fallback | 36,678 | 2,920 + 5,832 | 0.86 | 0.28 GB | 9.29 GB | PASS |

**Consequence:**
- Minute bars set the trigger and fill minute and the fill-session stop.
- Conflicts in later holding sessions resolve stop first, per A5.
- S-a uses daily bars only, stop first throughout.

## 8. Run facts

- **Requests:** 48,012 with 0 retries.
  - SEC: 44,320 headers, 13:48–15:44Z.
  - Alpaca: 3,692 (top-up 33, minute 1,741, 09:35 samples 1,918), 15:46–16:34Z.
- **Wall clock:** 2.8 h.
- **Download:** about 1.6 GB.
- **Disk:** 9.72 → **9.43 GB free**, so the floor held.
- **Suites:**

  | Suite | Result | Note |
  |---|---|---|
  | Engine | 204/204 | |
  | Eval | 143/143 | the 21 golden cases reproduce exactly; R4 static tests pass |
  | Stockbot | **901/902** | exactly `test_review.Signals.test_corrections_are_saved_before_alerts` fails, per A8 |

- **Push:** `push_check.py`, with `s_trials.jsonl` added to the exempt ledgers (A8), then `engine/s1` pushed. The
  result is in the final message.

## 9. Decisions needed

The recommendations are marked **(Rec.)**.

1. **The A5 scope.** The budget chose the fallback, a pre-registered rule.
   - **(Rec.: accept.)**
   - The alternative is freeing disk so the primary scope fits:
     - `D:\scalp-data` holds about 3.4 GB of outputs from the closed intraday-model line: `recommendations` 1.7 GB,
       `screener_snapshots` 1.1 GB and `decision_points` 0.64 GB;
     - set-aside `.orphan` files are negligible: 242 files, about 0 GB;
     - deleting any of it is your decision, not the engine's;
     - it would add about 1.6 GB of minute bars and change no rule.
2. **The CIK identity rule (ADR 0013).** Issuers without a Form 4 symbol observation fall outside the universe. That
   is how foreign issuers stayed out before 2026, but it also drops any domestic issuer whose insiders never filed.
   - **(Rec.: keep it for S2 as pre-registered.)** Revisit it only in a later hypothesis family.
3. **Merge PRs.** **(Rec.: merge both)**
   - [danielsuisa/stockbot#25](https://github.com/danielsuisa/stockbot/pull/25), the clock fix;
   - [danielsuisa/stockbot#26](https://github.com/danielsuisa/stockbot/pull/26), the `/intraday` label.

## 10. Next step (S2 prerequisites only; S2 not started)

- An S2 prompt that authorizes:
  - the real-set run driver for `scalp_eval/swing.py`;
  - one development run;
  - then exactly one confirmation run.
- The `{"phase": "S2", "kind": "authorize"}` line in `scalp_contracts/s_trials.jsonl`, written under that prompt.
  It records the rules hash and the 8 signed-set digests.
