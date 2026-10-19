# ADR 0009: Insider data design (I1)

- **Status:**
  - **Accepted** for the items the I1 prompt fixes (user, 2026-10-19). These are marked **[A]**.
  - Every other choice is **Proposed**, marked **[P]**.
- **Commit order:** this ADR is committed after [ADR 0008](0008-insider-validation-design.md) §7–§8 and **before the
  first I1 ingestion request**.
- **Context:** [I0 audit](../I0-audit.md); the holdout guard (`scalp_contracts/holdout.yaml`).
  - Every I1 request is declared on or after 2022-01-03 and before 2026-11-02.

## 1. Acceptance time

- **[A] Source.** Each filing's SEC header `<ACCEPTANCE-DATETIME>` (`YYYYMMDDHHMMSS`, Eastern), converted to UTC with
  the tz database (`zoneinfo` America/New_York).
- **[P] Location.** The header is read from
  `https://www.sec.gov/Archives/edgar/data/{issuer_cik}/{accession without dashes}/{accession}-index-headers.html`.
  - A missing or unparsable header is recorded as `status = missing`; nothing is estimated.
- **[A] Cross-check.**
  - The submissions-JSON `acceptanceDateTime` is used only for a fixed-seed sample.
  - **[P]** The sample is 500 filings, seed `"i1-accept-check-v1"`.
  - Both readings of the JSON value are compared with the header, to the second: as-is UTC, and the engine's
    ET+8/+10 rule (`proxies.json_acceptance_utc`).
  - The agreement rate is reported. The JSON time is never used for events.
- **[P] Required filings.**
  - Every Form 4 or 4/A in the SEC Insider Transactions Data Sets 2022 Q1–2026 Q3 with at least one non-derivative
    line coded `P`.
  - **Sealed-window edge.** A filing whose `FILING_DATE` is 2022-01-03 may have been accepted after 17:30 ET on
    2021-12-31, inside `legacy_2016_2021`. Its header is requested **only if** one of its `P` lines has a transaction
    date ≥ 2022-01-03, which makes acceptance ≥ 2022-01-03. Other such filings are dropped and counted.
  - Any filing whose acceptance turns out to be before 2022-01-01 05:00 UTC is dropped and counted.
- **[P] Lines dated before 2022-01-03** are dropped at parse time and counted. No event window reaches them: the
  earliest event date is 2022-02-01, and its 30-day window starts 2022-01-02, a Sunday.

## 2. Event clock

- **[A] Evaluation.** The rule (§3) is evaluated at **every acceptance time** of a required filing.
  - **`available_at`** = the acceptance time of the filing that completes the trigger, plus **5 minutes**.
  - **Entry** = the first regular-session open strictly after `available_at`, from the stored calendar.
- **[P] Emission.** An event is emitted at acceptance time *t* when both hold:
  - the trigger holds as of *t*;
  - the filing accepted at *t* is one of its constituents.
  - This mirrors the live rule, which re-alerts only when a counted accession is new (`bot/scan.py:544`).
  - Repeats are handled by the I2 overlap rule (ADR 0008 §8.1).
- **[P] Rule date.**
  - The live window is `[today − 30, today]` on transaction dates, with `today` = the scan date.
  - The reimplementation uses `today` = the ET calendar date of *t*.
  - It uses lines from filings accepted ≤ *t*, **as first filed**.
  - A 4/A changes the state only from its own acceptance time, following `bot/scan.py:220-254` at `2ffda0a`.
- **[P] `event_id`** = `f"{trigger}-{cik}-{t:%Y%m%dT%H%M%SZ}"`. `rule_version` = `"stockbot-2ffda0a/3-100000-30-500000"`.

## 3. Rule (reimplemented in `scalp_engine`, no `bot` import)

- **[A]** The cluster and big triggers at `2ffda0a`, with the thresholds in force (ADR 0008 §7 E2: 3 / $100,000 /
  30 days / $500,000).
- **[A]** stockbot's insider test vectors are copied as fixtures, not imported.
- **[P] Line semantics** follow `bot/form4.py` at `2ffda0a`:
  - a buy line is code `P`, acquired `A`, non-derivative, not a plan line;
  - a plan line has a referenced footnote matching `10b5-?1` not negated by `NOT_PLAN`, or the `AFF10B5ONE` box with no
    plan footnote;
  - `od` = director or officer;
  - value = shares × price, or 0 if missing;
  - joint indirect twins merge (`form4.joint`).
- **[P] Data-set mapping:**
  - `AFF10B5ONE` ∈ {`1`, `true`} is the box.
  - Relationship comes from `RPTOWNER_RELATIONSHIP` (`Director`, `Officer`, `TenPercentOwner`, `Other`).
  - Footnote text comes from FOOTNOTES, joined on `ACCESSION_NUMBER` and the `*_FN` ids of the line.

## 4. Identity, prices and the bad-price rule

- **[A] CIK → point-in-time symbol.** Sources:
  - the filed issuer symbol;
  - Alpaca assets;
  - corporate-action rename and merger records (P1 and P2 methods).
  - Unmapped issuers are excluded and counted.
- **[P] Mapping order for event issuer *c* on entry date *d*:**
  1. The symbol filed on the completing filing, normalised to upper case, with a leading exchange prefix removed and
     `/` or `-` mapped to `.`. It is accepted if it has a SIP daily bar in the 10 sessions before *d*.
  2. Otherwise, follow Alpaca rename records from that symbol to the one valid on *d*.
  3. Otherwise, take the latest symbol filed by *c* within 180 days before *t* that has bars.
  4. Otherwise, the issuer is **unmapped**.
  - A symbol filed by two CIKs within 180 days of each other is **ambiguous**, and is excluded and counted.
- **[P] Symbol → CIK for control candidates.** The latest filed-symbol observation for that symbol among **all**
  Forms 3/4/5 in the data sets (any transaction code), on or before *d* and within 400 days. Otherwise the candidate
  has no CIK and no SIC, and is excluded.
- **[A] Prices.** Alpaca free SIP daily bars, raw, from 2022-01-03 to the last complete session, which is 2026-10-16
  when this ADR was written (≤ 2026-10-30).
  - Coverage: every event issuer, plus the P1 candidate pool (`ingest.candidate_pool`: latest assets + renames +
    delistings), which contains every stock that could qualify for universe (b).
- **[A] Corporate actions** from 2022-01-03 onward. Adjustments are applied at read time.
- **[P] Bad-price rule (`insider_prices.bad_bars`).** A bar is flagged when either holds:
  - (a) it is malformed: any OHLC ≤ 0, high < low, open or close outside [low, high], or volume < 0;
  - (b) it is a spike: on split-adjusted closes, |c_t/c_{t−1} − 1| > 0.75 and the move reverts by more than half within
    3 sessions, with no corporate action on that symbol between t−1 and t+3.
  - A close-to-close ratio within 2% of a split factor (1/2, 1/3, 1/4, 1/5, 1/8, 1/10, 1/20, 1/50 or the inverse) with
    no recorded split flags the symbol from that date as **missed split**.
  - Unit-tested with synthetic fixtures.

## 5. Spread inputs (SIP NBBO samples, existing P1 sampler)

- **[A] Universe spread filter.** One sample at **close − 5 min** (`close_minus_5m_v2`, look-backs 5 s and 60 s) on
  every session from 2022-01-03 to the last complete session.
  - **[P]** Sampled symbols: every pool symbol that passes the point-in-time pre-filter that day (close ≥ $1 and 20-day
    median dollar volume ≥ $100K, from bars up to that close), plus every event issuer on its entry date − 1.
  - The pre-filter is a necessary condition for universe (b), so no qualifying stock is skipped.
- **[A] Tradable version, for universe (c) candidate events:**
  - **09:35 ET** samples on the entry day (`open_plus_5m_v1`);
  - **close − 5 min** samples on each exit day for H = 5, 20 and 60.
  - **[P]** A universe (c) candidate is an event whose pre-entry close is ≥ $2, whose dollar volume is ≥ $1M, and whose
    prior-close spread is ≤ 1%.

## 6. Sector

- **[A]** The **current** SIC code from EDGAR submissions (`data.sec.gov/submissions/CIK##########.json`, fields
  `sic` and `sicDescription`). It is fetched once for every event-issuer and candidate CIK.
- **It is not point-in-time:** a firm that changed industry is matched on its current SIC.

## 7. Contracts (new tables)

| Table | Content | `event_at` |
|---|---|---|
| `insider_filings` | Data-set SUBMISSION and REPORTINGOWNER fields per accession and owner, plus `dataset_quarter` | Filing date 00:00 ET |
| `insider_lines` | Non-derivative transaction lines (all codes, ≥ 2022-01-03), with footnote ids and the text of referenced footnotes | Filing date 00:00 ET |
| `filing_acceptance` | accession, CIK, `accepted_at` (UTC), raw header value, status, source (`header` or `json_check`) | `accepted_at` |
| `cik_symbol_map` | Filed-symbol observations (CIK, symbol, filing date) and the resolved point-in-time mapping per event | Observation date |
| `issuer_sic` | CIK, SIC, description, `fetched_at` (not point-in-time) | `fetched_at` |
| `insider_events` | event_id, trigger, CIK, symbol, `available_at`, entry date, constituent accessions, universe (b)/(c) flags, `rule_version` | `available_at` |
| `raw_quote_samples` (extension) | New `sample_rule` value `open_plus_5m_v1` (09:35 ET) | sample time |
| `raw_bars_1d` (extension) | New symbol sets `sip-1d-i1-*` for 2022-01-03 onward | bar time |

## 8. Ingestion mechanics and budget

- **[A]** The P1 mechanics: append-only Parquet, a manifest per request, `check_range` before every request, resumable
  from the manifest.
- **[P] SEC fair access:** 8 requests/s (limit 10), with a declared User-Agent from `SEC_USER_AGENT`.
- **[A] Budget check before fetching.** Proceed only if the plan needs ≤ 24 h and leaves ≥ 8 GB free on `D:`.
- **[P] Parity input (ADR 0008 E5).**
  - Form 4 and 4/A filings accepted 2026-10-01 → 2026-10-16 are not in any published data set, and the 2026 Q4 file
    would overlap `forward_2026q4`.
  - They are read from the EDGAR daily index and each filing's ownership XML (the live scan's source), parsed into the
    same `insider_lines` schema with `dataset_quarter = "edgar-2026-10"`.
