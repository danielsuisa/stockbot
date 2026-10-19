# I1 report: pre-registration, ingestion, rule reimplementation, event set

Branch `engine/i1` (from `engine/i0` 6f7161a). Date 2026-10-19.

**I1 gate: FAIL (incomplete, blocked).** The Alpaca part of ingestion did not run. `ALPACA_KEY_ID` and
`ALPACA_SECRET_KEY` are unset in this machine's process and user environments. Constraint 4 forbids the key that
appeared in chat, so I stopped before any Alpaca request.

Everything that does not need Alpaca is done and passes:
- pre-registration;
- SEC ingestion;
- the rule reimplementation;
- parity.

The event table, universe flags, quote samples and the MDE need Alpaca data and are pending. **No outcomes were
computed.**

## Stage 0

- **Credentials.**
  - Alpaca: **unset**. Checked the process environment and the user and machine scopes; no value was printed.
  - `SEC_USER_AGENT` is also unset in the environment. For SEC requests it was set per process to the identity you
    gave earlier for SEC (name and e-mail, not a key). It was never printed.
- **Repository variables** (`gh api repos/danielsuisa/stockbot/actions/variables`, read-only):
  - `MIN_INSIDERS`, `MIN_CLUSTER_USD`, `MIN_SINGLE_USD` and `WINDOW_DAYS` are **unset**. The only variable is
    `TICKET_RISK_PCT`, and both environments have none.
  - Empty values fall back to the defaults (`bot/common.py:30-33`): **3 insiders / $100,000 / 30 days / $500,000**.
- **ADR 0008** is Accepted, with the decision record, the overlap note and the I2 measurement spec (§7–§8).

## Commit order and first request

| Time (UTC) | Event |
|---|---|
| 03:43:10 | d47b117 `docs: ADR 0008 accepted + pre-registered I2 measurement` |
| 03:46:03 | 0f83c1f `docs: ADR 0009 insider data design` |
| 03:53:52 | Budget check: 7.29 h planned (SEC 2.89 h, Alpaca 4.4 h), 10.4 GB free after the run. **PASS** |
| **03:53:57** | **First ingestion request** (SEC data-set index page). The module's SHA-256 is identical to the code committed in 11a25c0 |
| 04:03:52 | 11a25c0 `feat(L0): insider data sets, acceptance headers, daily bars, actions, quote samples` |
| 07:22:01 | 3258f98 `fix(L0): skip EDGAR daily-index holidays via the quarter listing` |
| 07:29:11 | 7ef2f31 `feat(insider): rule reimplementation, parity, event table` |

Deviations, each reported:
1. **The ingestion code was committed 10 minutes after the run started.** The running module's SHA-256
   (`38366cb8…`) equals the committed blob.
2. **ADR 0009 was amended** in 11a25c0, before the first request was answered for any affected filing. Every 4/A of an
   issuer that has purchases became a required filing, so amendments act from their own acceptance time.
3. **One extra fix commit** (3258f98). EDGAR has no daily index on Columbus Day, 2026-10-12, and answers 403. The
   October step stopped there; it resumed on the fixed, committed code.

## Data (SEC side: complete)

| Item | Result |
|---|---|
| Data sets 2022 Q1–2026 Q3 | 19 of 19 quarters. Filing dates 2022-01-03 → 2026-09-30. 1,031,382 filing×owner rows and 1,387,524 Table I lines (Forms 4 and 4/A). Lines dated before 2022-01-03 were dropped and counted per quarter in the manifest |
| Acceptance headers | **78,055 of 78,062 required filings (99.991%)**. 2 headers were unparsable. 5 amendments (4/A) became required only after the October parity data added their issuers' purchases; no event in the window depends on them. 0 sealed drops; 13 edge 4/A filings were excluded under ADR 0009 §1 |
| JSON cross-check (500 fixed-seed, 492 found) | Header agrees with JSON read as UTC for **264**, and with the ET+8/+10 rule for **228**. Every sampled filing fits exactly one reading, so the JSON time is filer-inconsistent. The header is the right source |
| Submissions (SIC, annual form) | 8,022 issuer CIKs |
| October parity input (EDGAR XML) | 10 index days (2026-10-12 holiday); 6,392 Forms 4 and 4/A; 0 unparsed |
| Duplicate keys | **0** in `insider_filings`, `insider_lines`, `filing_acceptance` and `issuer_sic` |
| Rows in sealed windows | **0**. Earliest `event_at` is 2022-01-03 05:00 UTC; latest is 2026-10-17 01:57 UTC (an acceptance time for a 2026-10-16 filing) |
| Alpaca: calendar 2022–23, corporate actions 2022-01-03 → 2023-07-31, daily bars (i1a, i1b, i1c), close − 5 min and 09:35 NBBO samples | **NOT RUN: credentials unset** |

## Parity (ADR 0008 E5): 2026-09-24 → 2026-10-16

| | Count |
|---|---|
| Journal alerts | 106 |
| **Matched** (same issuer, identical constituent accessions, while qualifying, in the run window) | **106** |
| Explained | 0 |
| **Unexplained** | **0** |
| Found only by the reimplementation | 77, all with a documented cause: 42 issuer has no current ticker; 32 foreign private issuer (alert is name-only, not journaled); 2 window anchor (the live first run built rows with its scan date as "today"); 1 issuer not in SEC's ticker map at scan time (the bot's own state shows the filing read but no row stored) |

The emulation starts from the bot's own alert record before the journal existed: 159 issuers in `data/state.json`
at `d0902d3`, read with `git show`. Nothing in stockbot was modified.

## Event counts (raw, before the I2 overlap rule; universe flags pending)

The rule fired 18,422 times in total, across 3,142 issuers. Windows below use the `available_at` ET date, because the
exact entry dates need the Alpaca calendar.

| Year | cluster | big |
|---|---|---|
| 2022 (from 2022-02-01) | 2,102 | 2,253 |
| 2023 | 2,186 | 1,844 |
| 2024 | 1,386 | 1,898 |
| 2025 | 1,634 | 1,795 |
| 2026 (to 2026-07-20) | 940 | 1,203 |

| Window | cluster | big |
|---|---|---|
| Development 2022-02-01 → 2024-06-28 | 5,020 | 5,037 |
| Confirmation 2024-07-01 → 2026-07-20 | 3,228 | 3,956 |
| … of which post-S1 2026-04-01 → 2026-07-20 | 589 | 626 |

Each contributing filing emits an event, so these counts include repeats. The I2 overlap rule keeps the first per
issuer within H. The split by universe (b) and (c) is pending.

## MDE for gate (i)

**Pending.** It needs the 20-day return dispersion of the universe (b) population in the development window, which
needs the Alpaca daily bars. No event returns were or will be used for it.

## Suites and hygiene

- Engine 149/149, eval 74/74 and stockbot 902/902: all OK.
- Changes are only in `docs/engine/`, `scalp_engine/`, `scalp_eval/` (contract test) and `scalp_contracts/`.
- `bot/` and `.github/` are identical to `origin/main`, and `data/` is unchanged.
- No data files are tracked; the only tracked data-format file is the empty `holdout_ledger.jsonl`.
- **Run facts (SEC):**
  - 92,018 requests (6,424 data sets, index and October; 77,572 headers; 8,022 submissions);
  - 1.33 GB downloaded;
  - 03:53:57Z → 07:27:23Z, 3.6 h;
  - store growth 287 MB (data-set zips 197 MB, tables 90 MB);
  - D: 12 GB free.

## Push

**Not done.** `push_check.py` scans new blobs for the Alpaca key values taken from the environment, and exits when
they are missing. I did not use the chat-pasted key for it.

## Decisions needed

1. **Alpaca credentials.**
   - *Recommended:* create a **new** Alpaca paper key, revoke the one pasted in chat, and set it as user environment
     variables yourself (PowerShell, values never in chat):
     `foreach ($n in 'ALPACA_KEY_ID','ALPACA_SECRET_KEY','SEC_USER_AGENT') { [Environment]::SetEnvironmentVariable($n, (Read-Host $n), 'User') }`
   - No restart is needed; `scalp_engine/envload.py` reads the user environment at run time.
2. **Resume I1 after that.** *Recommended: yes, in the same branch.* The remaining Alpaca work is about 4.4 h:
   `insider_prices base → bars → map → plan` (budget re-checked) `→ close-quotes → flags → open-quotes → table`. The MDE,
   the event table, the backup push and a final I1 report follow.
3. **Reimplementation-only events** (foreign private issuers, no current ticker). *Recommended:* keep them in the event
   table with their `fpi` flag. ADR 0008 §1 already excludes foreign private issuers from H1; unmapped issuers drop out
   at the symbol map.

## Next step (I2 prerequisites only)

- The completed I1 data and event table, with the I1 gate PASS.
- The owner-written unseal ledger record for the `legacy_2016_2021` veto, written at the end of I2.
- No I2 work has started.
