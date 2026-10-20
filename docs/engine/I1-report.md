# I1 report: pre-registration, ingestion, rule reimplementation, event set

Branch `engine/i1` (from `engine/i0` 6f7161a). Run 2026-10-19 → 2026-10-20.

**I1 gate: FAIL on one item as worded.** The store has **71,667 duplicate keys**. All are identical copies of P1 rows,
created when I1 re-fetched data P1 already held; I1's own writes contain 0 duplicates. Every other item passes.
Per the protocol I stopped here and did not alter the store; a remedy is proposed under Decisions needed.

**No outcomes were computed.** No return, abnormal return or P&L exists for any event or control. The MDE uses
population returns in the development window, with every issuer-day in an event's reach excluded.

## Stage 0 and commit order

- **Credentials.**
  - The first run found the Alpaca variables unset, then twice found a key that had appeared in chat; I refused to use
    those.
  - It resumed on 2026-10-19 at 15:17 UTC with a new key that you set at a local prompt. I checked locally, by hash,
    that it matches no chat-pasted value; it was never printed.
  - `SEC_USER_AGENT`: the first SEC run set it per process from the identity you gave for SEC. It is now set in your
    user environment.
- **Repository variables** `MIN_INSIDERS`, `MIN_CLUSTER_USD`, `MIN_SINGLE_USD` and `WINDOW_DAYS` are unset, so the
  thresholds in force are **3 / $100,000 / 30 days / $500,000** (ADR 0008 §7 E2).

| Time (UTC) | Commit or event |
|---|---|
| 10-19 03:43 | d47b117 `docs: ADR 0008 accepted + pre-registered I2 measurement` |
| 10-19 03:46 | 0f83c1f `docs: ADR 0009 insider data design` |
| 10-19 03:53:52 | Budget check PASS (planned 7.3 h; 10.4 GB free after) |
| **10-19 03:53:57** | **First ingestion request** (SEC). The running code equals the blob committed in 11a25c0 |
| 10-19 04:03 | 11a25c0 `feat(L0): insider data sets, acceptance headers, daily bars, actions, quote samples` |
| 10-19 07:22 | 3258f98 `fix(L0): skip EDGAR daily-index holidays via the quarter listing` (extra) |
| 10-19 07:29 | 7ef2f31 `feat(insider): rule reimplementation, parity, event table` |
| 10-19 07:31 | 3615fd4 `docs: I1 report` (the interim report, superseded by this one) |
| 10-19 15:17 → 10-20 05:29 | Alpaca steps on 3615fd4: base, bars, map, plan, close quotes, flags, 09:35 quotes, table |
| 10-20 05:3x | 1b54809 `feat(insider): MDE for gate (i) from population dispersion` (extra; your corrections), then the MDE run |

**Deviations, each reported:**
1. The ingestion code was committed 10 minutes after the run started; the code is byte-identical.
2. ADR 0009 was amended before any affected request: 4/A amendments of issuers with purchases are required filings.
3. Two extra commits: the EDGAR holiday fix (2026-10-12 answers 403) and the MDE module.
4. The quote sampling took 13.8 h, not the planned 3.5 h (12.2 GB of dense quote pages). The whole run took about
   17.8 h (SEC 3.6 h + Alpaca 14.2 h), inside the 24 h budget.
5. **`P1_LAST` was set to 2026-09-30, but P1's bars run to 2026-10-16.** This created the duplicate copies (Gate).

## Event counts (`insider_events`, entry dates 2022-02-01 → 2026-07-20; raw, before the I2 overlap rule)

The table has 17,252 rows. Universe flags are as of the entry decision.

| Window | Trigger | All | Universe (b) | Universe (c) | Foreign private issuer |
|---|---|---|---|---|---|
| Development | cluster | 5,023 | 3,592 | 2,442 | 0 |
| Development | big | 5,039 | 3,780 | 3,056 | 5 |
| Confirmation | cluster | 3,229 | 2,319 | 1,882 | 137 |
| Confirmation | big | 3,961 | 2,688 | 2,264 | 105 |
| … post-S1 (2026-04-01 → 07-20) | cluster | 589 | 412 | 358 | |
| … post-S1 | big | 629 | 393 | 324 | |

| Year | cluster all / (b) / (c) | big all / (b) / (c) |
|---|---|---|
| 2022 (from 02-01) | 2,105 / 1,522 / 1,001 | 2,260 / 1,767 / 1,475 |
| 2023 | 2,187 / 1,564 / 1,084 | 1,843 / 1,410 / 1,096 |
| 2024 | 1,385 / 942 / 680 | 1,898 / 1,273 / 1,018 |
| 2025 | 1,636 / 1,187 / 971 | 1,796 / 1,249 / 1,081 |
| 2026 (to 07-20) | 939 / 696 / 588 | 1,203 / 769 / 650 |

**Gate (i) population** (cluster, universe (b), not a foreign private issuer, after the overlap rule at H = 20):
1,139 development, **786 confirmation**, 115 post-S1.

**Symbol map** (table rows):
- filed 15,122; recent-filed 60; rename 12; unmapped 732.
- ambiguous 1,326, of which:
  - 1,196 are placeholder symbols (`NONE`, `N/A`) of unlisted issuers;
  - **130 are genuine CIK changes on one ticker** (58 cluster, 72 big): holding-company reorganizations, spin-offs and
    restructurings. These are excluded under the unchanged rule; see Decisions.

The I0-run figure of 161 counted every mapped event, including those outside the table's entry window.

## MDE for gate (i), from counts and the development-window population only

Method (`scalp_engine/insider_power.py`, committed 1b54809 before it ran):
- **Pseudo-abnormal return** of a universe (b) stock-day: the split-adjusted return from the open of d+1 to the close
  of d+20, minus the mean of 5 random universe (b) stocks of the same day.
- **Exclusion:** every issuer-day in **[entry − 20, entry + 60] sessions** of any cluster or big event. This contains
  the required [entry, entry + 60]; the 20 sessions before entry are added because a day's return runs to d+20.
  **196,208 of 2,570,531 eligible stock-days were excluded.**
- **Dispersion:** σ = **0.1672** and within-week ρ = **0.0213**, from 997 draws in 116 weeks (seeded `i1-power-v1`).
- **Formula:** design effect DE = 1 + (m_eff − 1)·ρ, with m_eff = Σ(weekly n²)/n; SE = σ·√(DE/n);
  **MDE = (2.0 + 0.8416)·SE = 2.8416·SE** (gate t ≥ 2.0 at 80% power, pinned by a unit test).

| Window | n | Entry weeks | m_eff | DE | SE | **MDE (H = 20)** |
|---|---|---|---|---|---|---|
| Development | 1,139 | 121 | 16.8 | 1.34 | 0.0057 | **1.63 pp** |
| Confirmation | 786 | 104 | 11.9 | 1.23 | 0.0066 | **1.88 pp** |
| Post-S1 (no gate weight) | 115 | 16 | 12.3 | 1.24 | 0.0174 | 4.94 pp |

The ρ estimate rests on 116 weeks of 8 draws each and is noisy; DE is sensitive to it.

## Coverage

| Input | Coverage |
|---|---|
| Acceptance times (SEC header) | **78,055 of 78,062 required filings (99.991%)**. 2 headers unparsable; 5 4/A that became required only after the October input, with no event depending on them. JSON cross-check: agrees as UTC for 264 and under the ET+8/+10 rule for 228 of 492, so the JSON time is filer-inconsistent |
| Daily bars | 10,372 symbols for 2022-01 → 2023-07 (part i1a); 1,295 new symbols for 2023-08 → 2026-09 (i1b); 2026-10 (i1c). Every mapped event has a bar in the 10 sessions before entry |
| Close − 5 min NBBO | 5,515,746 samples over 1,202 sessions: **94.9% ok**, 5.1% no quote within 60 s. 962 mapped events have no valid prior-close quote and are flagged out of (b) and (c), with the reason recorded |
| 09:35 NBBO, universe (c) events (9,644) | Entry: 9,209 valid (95.5%). Exit close − 5 min at H = 5 / 20 / 60: 99.6% / 99.4% / 98.6% valid. Every one has a sample row |
| Corporate actions | 57,843 rows for 2022-01-03 → 2023-07-31 (P1 holds later dates) |
| Submissions (SIC, annual form) | 8,022 CIKs (not point-in-time, as recorded in ADR 0009 §6) |

## Parity (ADR 0008 E5): 2026-09-24 → 2026-10-16

| Journal alerts | Matched | Explained | **Unexplained** | Found only by the reimplementation |
|---|---|---|---|---|
| 106 | **106** | 0 | **0** | 77, all with a cause: 42 no current ticker, 32 foreign private issuer (never journaled), 2 window anchor (live first run), 1 not yet in SEC's ticker map (bot state shows it read, no row) |

## Gate

| Item | Result |
|---|---|
| ADR 0008 (with the spec) and ADR 0009 committed before the first request | **PASS.** 03:43 and 03:46, before the first request at 03:53:57 UTC |
| Ingestion complete per plan | **PASS.** All SEC and Alpaca steps ran |
| Acceptance times ≥ 99% | **PASS.** 99.991% |
| 0 duplicate keys | **FAIL as worded.** I1's own writes: 0. Store-wide: **68,174 bar keys** (i1c vs P1 v1/v2/v3, 2026-10-01 → 10-16) and **3,493 quote keys** (i1-close vs P1 early-close re-samples, 7 days), **all identical in every value**. Readers keep one copy per key. All other I1 tables (`insider_*`, `filing_acceptance`, `issuer_sic`, `cik_symbol_map`, `insider_events`, calendar, actions) have 0 |
| 0 rows in sealed windows | **PASS.** 0 in every table I1 wrote or extended |
| Parity: 0 unexplained | **PASS.** 106 of 106 matched |
| No outcomes computed | **PASS.** None computed. The MDE uses population returns only, with event reach excluded |
| Suites | **PASS.** Engine 153/153, eval 74/74, stockbot 902/902 |
| Paths, no data files, push checks | **PASS.** Changes only in `docs/engine/`, `scalp_engine/`, `scalp_eval/` and `scalp_contracts/`. `bot/` and `.github/` are identical to `origin/main`, and `data/` is unchanged. The only tracked data-format file is the empty `holdout_ledger.jsonl`. The push checks are in the Pushes section |

**Run facts:**
- **SEC:** 92,018 requests, 1.33 GB, 3.6 h.
- **Alpaca:** 22,829 requests, 12.5 GB, 14.2 h, 0 retries.
- **Store growth:** `D:\scalp-data` 9.3 GB → 10 GB. D: has 12 GB free (floor 8 GB).

## Pushes

`engine/i1` → origin: the result is recorded in the final message of the I1 session. The checks run on the head that includes this report, using credentials from the user environment through `envload`, never from chat.

## Decisions needed

1. **The duplicate keys.**
   - *Recommended:* (a) remedy without network or data loss. Set aside the 12 `sip-1d-i1c` date files and the 7
     `i1-close-v1` early-close date files, using the store's existing set-aside mechanism (renamed `.orphan`, never
     read). Rewrite them as new parts holding only the keys P1 lacks, re-run the duplicate check, and record it all in
     the manifest. Then the gate item passes as worded.
   - Alternative: (b) accept the identical copies as documented. Readers deduplicate deterministically.
2. **Dual-CIK exclusion** (130 events in the table window: 58 cluster, 72 big). Kept unchanged this run, as you
   instructed.
   - *Recommended:* in I2's ADR, decide whether to map a ticker across a documented CIK change (holding-company
     reorganization or spin-off) instead of excluding it. Do not re-map in I1.
3. **I2 power.** The confirmation MDE is about 1.9 pp at H = 20 for gate (i). No decision is needed now; it is recorded
   for context.

## Next step: I2 prerequisites only

- A decision on item 1. If (a), the remedy is a short follow-up with no network.
- The owner-written unseal ledger record for the `legacy_2016_2021` veto, written at the end of I2.
- I2 is not started.
