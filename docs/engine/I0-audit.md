# I0 audit: insider-signal validation scoping (read-only)

Branch `engine/i0`, from `engine/p3d` 908f25d. Date 2026-10-19.

**Scope:** this audit reads stockbot code and docs, official documentation, and **one** SEC sample file: the 2024 Q1
Insider Transactions Data Set, which lies outside both sealed windows. It reads engine metadata, plus 2024 daily
bars already stored, for coverage counts only.

**Not done:**
- no bulk ingestion;
- no Alpaca requests;
- no returns or signal performance computed;
- no stockbot file changed.

The bot-side files cited (`bot/form4.py`, `bot/scan.py`, `bot/journal.py`, `bot/market.py`, `bot/common.py`,
`.github/workflows/daily-scan.yml`) are identical on `engine/i0` and `main`.

Each finding gives **F** (finding), **E** (evidence), **I** (impact) and **R** (recommendation).

---

## I1. The stockbot insider module

**I1.1 Data sources.**
- **F:**
  - The live scan reads the EDGAR daily index (`form.{day}.idx`) and keeps form types `4` and `4/A`.
  - It downloads each filing's full-submission `.txt` and parses the `<ownershipDocument>` XML.
  - EDGAR full-text search (EFTS) is the fallback, and the feed for the same-day runs.
  - The issuer→ticker map is SEC `company_tickers.json`.
  - There is no third-party insider feed.
- **E:**
  - `bot/scan.py:282-296` (daily index, 4 and 4/A filter, `.txt` URLs) and `bot/scan.py:257-273,286-291,800` (EFTS);
    `bot/form4.py:8,48-58` (XML parse); `bot/common.py:115` (ticker map).
  - SEC identity: the `SEC_UA` secret; the run exits if it is unset (`bot/common.py:47-51`).
  - Request rate: at most `min(8, SEC_RPS)` per second, under a global lock (`bot/common.py:38,54-57`).
- **I:** the live source and an engine source can be made equivalent at the filing level, because both use EDGAR
  ownership XML.
- **R:** the engine reads the same XML fields (via the SEC data sets, I3) and records accession numbers so the two can
  be reconciled.

**I1.2 Cluster definition (exact, current code).**
- **F:** a buy line requires all of:
  - `transactionAcquiredDisposedCode == "A"`;
  - `transactionCode == "P"`;
  - a non-derivative table line;
  - not a 10b5-1 plan line. A line is a plan line if a footnote it references matches `10b5-?1` and is not negated by
    `NOT_PLAN`, or if the `aff10b5One` box is ticked and the filing has no plan footnote.
- **F:** a **cluster** is:
  - at least `MIN_INSIDERS = 3` distinct officers or directors (keyed on `insider_cik` or the name);
  - with summed open-market value of at least `MIN_CLUSTER_USD = $100,000`;
  - over lines whose **transaction date** lies in `[today − WINDOW_DAYS, today]`, with `WINDOW_DAYS = 30` calendar
    days.
- **F:** other rules:
  - There is no per-line minimum value; a missing value counts as $0.
  - A 10% owner who is not an officer or director does not count toward the cluster.
  - A separate **"big"** trigger fires on any single open-market line of at least `MIN_SINGLE_USD = $500,000`, from any
    reporting owner.
  - The issuer must be in SEC's *current* ticker map.
  - Foreign private issuers (latest annual report a 20-F or 40-F) are listed without a scored block.
  - There is no price or market-cap filter. Liquidity under $2M a day is only flagged.
  - Indirect ownership is flagged, not excluded.
  - Joint "indirect twin" filings are merged into one row (`form4.joint`).
- **E:**
  - Buy-line rules: `bot/form4.py:11-12,66-76`.
  - Cluster and "big" tests: `bot/scan.py:19-22` (constants), `:315,323` (window on transaction date), `:433-442`.
  - Roles: `bot/form4.py:31,35`. Joint merge: `bot/form4.py:122-144`.
  - Universe: `bot/scan.py:320`. Foreign issuers: `bot/scan.py:354-357,552-553`. Liquidity flag: `bot/market.py:14`.
- **F:** the constants are `common.env(...)` defaults and can be overridden by GitHub repository variables
  (`.github/workflows/daily-scan.yml:48-52`). The values actually in force are **not visible locally**.
- **I:** the hypothesis must name the threshold values explicitly, not "the defaults".
- **R:** the user confirms the repository-variable values (E2).

**I1.3 Quality score and regime tag.**
- **F:** the quality score runs 0–100 and has five parts:
  - breadth: `min(30, 10·(people−1))`;
  - seniority: `min(25, round(25·Σweights/4.5))`, with weights CEO/CFO/Chair 1.5, other officer 1.0, director 0.7, and
    a token buy's weight halved;
  - size: `round(20·min(1, max(0, log10(total/1e5)/2)))`;
  - conviction: `round(15·min(1, median(pct)/0.25))`;
  - market: panic +10, euphoria −10.
- **F:** the regime tag uses Yahoo VIX, SPY and IWM:
  - `panic` if VIX ≥ 28, or SPY 20-day ≤ −7%, or IWM 20-day ≤ −10%;
  - `euphoria` if VIX ≤ 15 and (SPY 60-day ≥ +8% or IWM 60-day ≥ +12%);
  - otherwise `normal`.
  - It is computed **once per run, at run time**, not as of the trade or filing date.
- **E:** `bot/scan.py:408-425` (score); `bot/form4.py:13,34,117-119` (weights, token buy); `bot/market.py:94-109` (regime);
  `bot/scan.py:873` (computed at run time).
- **I:** the score mixes a run-time market state into an event attribute. A historical replay needs a point-in-time
  regime, from a source other than Yahoo.
- **R:** test the raw cluster as the primary hypothesis. The quality score is at most a pre-registered secondary (E2).

**I1.4 Timestamp.**
- **F:** the cluster window is keyed on **transaction date**.
  - A row's `filed` field is the daily-index day.
  - The EDGAR acceptance datetime is **not used** anywhere in the live path.
  - The journal `date` is the snapshot date: the UTC date for the 05:30 UTC morning scan, the New York date for the
    07:30 / 09:15 / 16:30 / 19:30 New York runs.
- **E:** `bot/form4.py:41`; `bot/scan.py:324,521,791,846`; `bot/clock.py:17-19`; `bot/sessions.py:14-17`.
- **I:** a validation that starts the clock at the transaction date would use information that was not yet public.
  Form 4s are due within 2 business days, but late filings are common (I3.3).
- **R:** start the clock at the **acceptance time**, plus a stated latency (ADR 0008 §1).

**I1.5 Amendments (Form 4/A).**
- **F:** amendments are handled:
  - A 4/A replaces its original under the original's accession. The match uses issuer, insider and
    `dateOfOriginalSubmission`, then identical trades or overlapping dates.
  - A 4/A with no purchases withdraws the original only if it restates the original's non-derivative lines.
  - An original read after its own 4/A is skipped.
  - The last 3 scanned days are re-read every run.
  - A correction message goes out if the total moves by more than 10%.
- **E:** `bot/scan.py:32,220-254,325,334,589-633,852`.
- **I:** a historical replay must decide whether it uses the **as-first-filed** state, which is what a live user saw,
  or the **amended** state, which is what is true later. The point-in-time choice is as-first-filed. The
  STEP 0 probe ignored 4/A entirely (`docs/backtest/insider-step0-2026-10-10.md:27-38`, gap 2).
- **R:** primary = as-first-filed. An amendment can only *remove* or *correct* a cluster, and only from the
  amendment's own acceptance time onward.

**I1.6 Tests.**
- **F:** the insider logic is covered by:
  - `tests/test_signals.py`: 16 tests. Despite the name, this is the insider module's test file.
  - `tests/test_bot.py`: 9 insider-specific tests (Form4 :120, Scan :341).
  - `tests/test_review.py`: 23 tests (amendments, corrections).
  - `tests/test_reliability.py`: 20 tests (index → EFTS fallback, corrections).
  - `tests/test_journal.py`: 16 tests (journal, follow-up).
  - `tests/test_intraday.py`: 8 tests (same-day runs).
  - All run in stockbot's suite of 902.
- **E:** line references as listed.
- **I:** the bot tests are fixtures for the live rule. The engine cannot import `bot` (R4).
- **R:** the engine reimplements the rule and proves parity on the live journal's recorded accessions (I5.1).

**I1.7 The commit that defines the signal.**
- **F:** `cff58d5` (2026-09-24) introduced the classification, the 10b5-1 drop, role weights, the quality score and the
  `MIN_*` constants.
- **F:** the **latest commit that changes the definition is `2ffda0a`**, at 2026-09-24 18:51 UTC. It added
  `NOT_PLAN`, the order-independent joint merge, the holdings-% calculation and the 4/A rules.
- **F:** later commits to `bot/scan.py` change timing or presentation only:
  - `8f0a6ba` (2026-09-28): same-day EFTS runs;
  - `f7a9c87` (2026-10-09): label text;
  - `8410856` (2026-10-11): logging.
- **F:** the first journal entry is from commit `d6f1158`, at 2026-09-24 20:30 UTC, after `2ffda0a`.
  `main` is at `1939f52` (2026-10-17).
- **E:** `git log -- bot/form4.py bot/scan.py`; `git log -1 --format=%ad 2ffda0a` → 2026-09-24 21:51:56 +0300.
- **R:** freeze `2ffda0a` as the rule definition (E2).

**I1.8 Prior insider research in this repo (affects E1).**
- **F:** the STEP 0 probe:
  - It ran on SEC data sets for 2016-01-04 → 2026-03-31 and read 171,933 purchase filings.
  - It approximated the cluster rule (30 days / 3 people / $100K / 21-session gap), keyed on **filing date**, without
    4/A or joint merge.
  - It counted **6,476 events**.
  - It reported return **dispersion only**: SD, design effect and percentiles, no means. It kept the signed returns
    blind.
- **E:** `bot/probe_insider.py:25-31`; `docs/backtest/insider-step0-2026-10-10.md:45-50` (event counts by year);
  `:74-77` (21 sessions: n 5,852, SD 0.206, SD clustered by month 0.594, design effect 8.28); `:87-90` (09:35 quoted
  spread: median 0.80%, p75 1.74%, p90 2.72%; 24% without a usable quote).
- **F:** ensemble signal **S1** is an insider construct with ticket-style exits. It ran over signal dates
  2017-01-03..2026-03-31, and its **signed** mean was observed. The ensemble verdict was NO-GO.
- **E:** `docs/backtest/ensemble-phaseA-2026-10-10.md:20`; `ensemble-verdict-report.json:228-233`
  (S1 `n 1533`, `x_bar −0.0319`, `se_t10 0.0191`); `docs/engine/P0-audit.md:171-176`, which lists the insider line
  among the research lines that read 2016–2021 daily data.
- **I:** no period from 2017 to 2026-03 is unseen by insider-derived returns in the strict sense. S1 is a different
  construct, though: different exits, filing-date keyed, no 4/A. The live rule's own returns from acceptance time have
  never been computed. This bears directly on E1.
- **R:** state this in the ADR 0008 hypothesis record, and choose E1 knowing it.

## I2. The live alert journal

- **F, location and schema:**
  - The journal is `data/journal.json`, committed by the scan workflow.
  - Each entry has `id`, `date`, `ticker`, `cik`, `company`, `sic`, `kind` (`cluster` | `big` | `cluster+big`),
    `quality`, `parts`, `regime`, `price` {price, asof, source}, `total`, `insiders`, `liquidity`, `scores`, `accs`
    (accession numbers) and `returns`.
  - An entry is recorded only after the Telegram message is sent.
  - **E:** `bot/journal.py:19-22,50,164-172`; `bot/scan.py:772-781`; `.github/workflows/daily-scan.yml:60`.
- **F, counts:** 106 alerts, dated 2026-09-24 → 2026-10-16, across 59 unique tickers.
  - By kind: `big` 70, `cluster` 19, `cluster+big` 17, so **36 entries include the cluster trigger**.
  - Regime: 106 `normal`. Price source: 106 `live`.
  - Quality buckets: 0–39: 67, 40–69: 23, 70–100: 16.
  - **E:** `data/journal.json` at `82a56ce` (2026-10-16 23:31 UTC), counted on `engine/i0`.
- **F, follow-up metric:**
  - It runs weekly on Sunday (`daily-scan.yml:4-5`), at 30, 90 and 180 **calendar** days.
  - `ret` = close of the first session on or after `date + h` divided by close of the first session on or after
    `date`, minus 1. The same is computed for SPY, and `excess = ret − spy`.
  - Prices are Yahoo daily closes. Missing prices are retried, not estimated.
  - `stats()` gives n, hit rate, and mean and median excess, by regime and quality bucket. It is sent to Telegram, not
    stored.
  - **E:** `bot/journal.py:13,65-88,95-146`; `bot/market.py:32-43,135-142`.
- **F, the journal's own summary:** no horizon is filled yet (0 of 106), so there are no summary numbers.
- **Point-in-time note:**
  - For the 16:30 and 19:30 New York alerts, the follow-up baseline is the same day's 16:00 close, which is **before**
    the alert.
  - The journal does not record the run mode or the time of day, so these cases cannot be separated.
  - **E:** `bot/journal.py:65-88`; `bot/scan.py:521,791`.
- **I and R:** see E5. No conclusion is drawn here.

## I3. Free historical Form 4 data

**I3.1 SEC Insider Transactions Data Sets.**
- **F, coverage:** quarterly zips from **2006 Q1 to 2026 Q3**, published quarterly.
  - Filings accepted after 17:30 ET on a quarter's last business day go into the next quarter's file.
  - Two URL roots are in use: `files/structureddata/...` up to 2026 Q1, and `files/datastandardsinnovation/...` for
    2026 Q2 and Q3.
  - **E:** https://www.sec.gov/data-research/sec-markets-data/insider-transactions-data-sets (fetched 2026-10-19);
    readme https://www.sec.gov/files/insider_transactions_readme.pdf §1–2.
- **F, contents:** eight tab-separated UTF-8 tables keyed by `ACCESSION_NUMBER`: SUBMISSION, REPORTINGOWNER,
  NONDERIV_TRANS, NONDERIV_HOLDING, DERIV_TRANS, DERIV_HOLDING, FOOTNOTES and OWNER_SIGNATURE, plus a W3C metadata
  JSON.
  - All fields the live rule uses are present: `TRANS_CODE`, `TRANS_ACQUIRED_DISP_CD`, `TRANS_SHARES`,
    `TRANS_PRICEPERSHARE`, `TRANS_DATE`, `DIRECT_INDIRECT_OWNERSHIP`, `RPTOWNER_RELATIONSHIP`/`RPTOWNER_TITLE`,
    `SHRS_OWND_FOLWNG_TRANS` (holdings %), FOOTNOTES (10b5-1 footnote text) and `DATE_OF_ORIG_SUB` (for 4/A).
  - **E:** readme §3–5.
- **F, verified on the sample 2024 Q1:**
  - The zip is 13.87 MB (91.5 MB unzipped); FOOTNOTES is the largest table at 44 MB.
  - 67,671 submissions: Form 4 61,366, 4/A 1,122, Form 3 3,884, 3/A 145, Form 5 1,139, 5/A 15.
  - `FILING_DATE` covers 2024-01-02 … 2024-03-29, formatted as `DD-MON-YYYY`.
  - Open-market purchase lines (`P` plus `A`): 5,914, in 3,071 filings, from 1,050 issuers.
  - SUBMISSION also has **`AFF10B5ONE`**, the 10b5-1 checkbox. It is in the zip's own readme but not in the PDF. Its
    values mix `0/1` and `false/true`.
  - **E:** local inspection of `2024q1_form345.zip`, kept outside git under `D:\scalp-data\i0_sample\`.
- **F, acceptance timestamp: absent.** The data set has no acceptance time.
  - `FILING_DATE` is a date only.
  - The word "accept" does not appear in either readme, and no column carries a time.
  - **E:** sample column lists for SUBMISSION and NONDERIV_TRANS; readme text search.
- **I:** the event clock (acceptance time plus latency) cannot come from the data sets alone.

**I3.2 Acceptance time: fallbacks.**
- **F:** two sources exist.
  - (a) The submissions JSON `acceptanceDateTime`: per CIK, or the nightly bulk `submissions.zip`, about 03:00 ET. It
    holds at least one year or 1,000 filings, with further pages for older filings.
  - (b) The per-filing EDGAR index page, or the `.txt` SEC header, which shows the acceptance time in Eastern.
  - **E:** https://www.sec.gov/search-filings/edgar-application-programming-interfaces (fetched 2026-10-19).
- **F:** the submissions JSON time is **not consistent across filers.**
  - The ensemble work found Apple and JPMorgan +10 h from the index page, but Microsoft +5 h (that is, UTC), across 30
    filings from 2017–2026. The owner's decision there was to take the time from the index page, in Eastern.
  - The engine's `json_acceptance_utc` assumes a uniform ET+8/ET+10 offset.
  - **E:** `docs/backtest/ensemble-phaseA-2026-10-10.md:29`; `scalp_engine/proxies.py:328-336`.
- **I:** the engine's P2 correction cannot be reused for Form 4s without checking it.
- **R (I1 prerequisite):** the source of truth is the `.txt` header / index page (Eastern).
  - Fetch it only for filings that are members of a candidate cluster.
  - Pre-register a check of the JSON rule against the header on a fixed-seed sample. Use the JSON only if they agree
    100%.
  - Not verified in I0: the network allowance was one sample file.

**I3.3 Filing lag (sample 2024 Q1, open-market purchase lines, filing date − transaction date).**
- **F:**

  | Form | Lines | Median lag | p75 | p90 | Filed within 2 days |
  |---|---|---|---|---|---|
  | 4 | 5,503 | 2 days | 4 days | 32 days | 59.9% |
  | 4/A | 153 | 14 days | 112 days | 301 days | 15.0% |
  | 5 | 255 | 197 days | 292 days | 700 days | 0% |

- **I:** about 40% of Form 4 purchase lines are filed more than 2 days after the trade. A transaction-date clock would
  front-run public information.
- **R:** use the acceptance-time clock (I1.4). Form 5 lines are excluded (the live scan reads only 4 and 4/A).

**I3.4 Amendments.**
- **F:** 4/A filings are included as filed (1,122 in the sample), and `DATE_OF_ORIG_SUB` is filled in all 1,122.
  - The data set does **not** link an amendment to its original accession.
  - The SEC warns the data may contain "redundancies, inconsistencies, and discrepancies relative to prior submissions".
  - **E:** readme §3; sample counts.
- **R:** reimplement the live 4/A matcher (`bot/scan.py:220-254`), applying each amendment from its own acceptance
  time onward (I1.5).

**I3.5 Fair access.**
- **F:**
  - Current maximum: **10 requests per second**.
  - Requests must carry a declared User-Agent, plus `Accept-Encoding: gzip, deflate`.
  - The SEC may limit request rates.
  - The engine client uses 5/s and `SEC_USER_AGENT` (`scalp_engine/proxies.py:51-58`).
  - **E:** https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data (fetched 2026-10-19).

**I3.6 Volume and fetch time (estimates, nothing fetched).**

| Item | Count | Size | Time at 5/s |
|---|---|---|---|
| Data sets 2022 Q1–2026 Q3 (outside sealed windows) | 19 zips | ≈ 260 MB compressed (≈ 14 MB per quarter, as in the sample) | < 5 min, bandwidth-bound |
| Data sets 2016 Q1–2021 Q4 (in `legacy_2016_2021`) | 24 zips | ≈ 330 MB | **sealed: only with an E1 decision** |
| Acceptance headers, cluster-member filings only | ≈ 2–4k per year (≈ 600 events per year × ≈ 4–6 filings; STEP 0 counts) | small | ≈ 10–15 min per year |
| Acceptance headers, every Form 4 purchase filing | ≈ 12k per year (3,071 per quarter in the sample) | small | ≈ 40 min per year |

- **Sealed-window edges (R2):**
  - The 2026 Q4 and 2027 Q1 data sets overlap `forward_2026q4`, so they cannot be fetched.
  - Lines in 2022 Q1 with transaction dates in 2021 have 2022 acceptance times. They are admissible by event time, but
    a 30-day window looking back from early January 2022 reaches into the legacy window.
  - **R:** the first admissible **event** date is set so that the cluster window and every price warm-up start on or
    after 2022-01-03 (ADR 0008 E1).

## I4. Price and cost data for the insider universe

**I4.1 Daily bars on the Alpaca free plan.**
- **F, plan:** the free plan serves SIP data delayed 15 minutes via REST; daily bars of earlier sessions are always
  available. The free (Basic) plan lists historical data "Since 2016".
  - **E:** [ADR 0003](adr/0003-features-regime-screener.md) §1 (`sip_eod`); https://docs.alpaca.markets/docs/about-market-data-api
    (fetched 2026-10-19).
- **F, already held:**
  - The engine stores SIP daily bars, `adjustment=raw`, 2023-08-01 → 2026-09-30.
  - Symbol sets: `sip-1d-v1` 8,370, `sip-1d-v2` 1,731, `sip-1d-v3` 14 (pool members and pre-rename or delisted
    tickers). That is 4,894,799 rows.
  - **E:** `D:\scalp-data\manifests\raw_bars_1d.jsonl` (metadata only).
- **F, OTC:** OTC names are **not covered**.
  - The 2026-10-17 asset snapshot lists 16,288 OTC assets, all `inactive`.
  - OTC-style tickers in the pool (`ADMNF`, `AIBAF`, `ALCUF`, `AAMCF`) have no SIP daily bars in 2024 Q1.
  - **E:** `asset_snapshots` (2026-10-17); `raw_bars_1d` 2024-01..03 (counts only).
- **F, coverage of the sample's issuers** (2024 Q1 Form 4 purchase issuers, 978 distinct symbols as filed):
  - 412 NASDAQ, 273 NYSE, 36 AMEX, 55 OTC, and 202 not in today's asset list (renamed, delisted or OTC).
  - **784 of 978 (80%)** have SIP daily bars in 2024 H1.
  - Of those 784: 193 have a median close under $5; 318 have median daily dollar volume under $1M; 100 under $100K.
- **I:** about 20% of filers are lost without symbol mapping. The STEP 0 probe lost 612 events, about 9%, to missing
  bars (`insider-step0-2026-10-10.md:67-69`).
  - Micro caps dominate the low-liquidity tail.
- **R, survivorship (reusing the P1 approach):**
  - Map CIK → point-in-time symbol, using Alpaca corporate actions (name changes, mergers, delistings) and the
    `cik_map` table.
  - Include delisted names, as `top500_v3` did for P1.
  - Corporate actions are stored only from 2023-08-01, so the 2022-01 → 2023-07 history must be ingested in I1. Its
    depth on Alpaca must be verified first.
  - Delisting returns (cash merger or worthless) need a rule fixed before any return is computed.
  - Split and dividend adjustment: bars are stored raw, so multi-day returns need a corporate-action adjustment rule,
    with a pre-registered bad-price filter (STEP 0 open item 2).

**I4.2 Spread for daily entries.**
- **F, options:**
  - (a) SIP NBBO quotes from Alpaca around the open: free if at least 15 minutes old (ADR 0003 §1). The STEP 0 probe
    measured the quoted spread at 09:35: median 0.80%, p75 1.74%, p90 2.72%, and 24% of events had no usable quote in
    the 60 s before 09:35.
  - (b) Bar-based spread estimators. In P2 these tracked the real NBBO poorly even for liquid names: Abdi–Ranaldo
    median absolute error 6.29 bps, Spearman 0.09.
  - (c) The opening auction price: not verified on the free plan in I0.
  - **E:** `docs/backtest/insider-step0-2026-10-10.md:87-90`; [P2 report](P2-report.md) F5.
- **I:**
  - At $500 the commission is small: IBKR Tiered at least $0.35 per order, about 0.14% round trip.
  - The **spread is the main cost** for micro caps, and it is missing for about a quarter of events at 09:35.
- **R:**
  - Use (a), with a conservative rule for missing quotes, fixed before any data is computed: for example, the
    event's 20-day median quoted spread, else exclusion.
  - Set a liquidity floor (E3).
  - Do not use (b) as the primary measure.

## I5. Engine gaps

**I5.1 Signal reimplementation and parity.**
- **F:** the engine cannot import `bot` (R4; `scalp_engine/CLAUDE.md`). The rule must be reimplemented on the
  data-set fields.
- **R:**
  - Write a parity test against the live journal. Its 36 cluster-containing alerts record their accessions (`accs`).
  - The reimplementation must reproduce exactly those clusters for 2026-09-24 → 2026-10-16, or list each difference
    with its cause (EFTS same-day versus index, threshold variables, joint merge).
  - Parity is checked before any return is computed.

**I5.2 Evaluator: daily-horizon events.** The P3 L7 evaluator is minute-bar and intraday. It needs:
1. **Entry at the next open after acceptance plus latency.** Use a market-on-open or marketable order, not a passive
   limit: P3D found −0.14 R per trade from limit adverse selection (ADR 0007 §2.2).
   - Fill price = the daily open plus half the quoted spread.
   - Liquidity cap = a share of the first-minute volume (reuse `LIQUIDITY_SHARE`).
2. **Multi-day holding:**
   - a time exit at the close of session H (5, 20 or 60);
   - corporate-action-adjusted returns;
   - delisting returns;
   - halts.
3. **Cash account over multi-day holds:**
   - `scalp_eval/account.py` already does settled-cash T+1;
   - it needs a concurrency policy for $500 (one position, or equal slices);
   - and a rule for events that arrive while cash is committed.
4. **Benchmarks:** SPY daily bars (the engine holds SPY), plus a size- and sector-matched random control.
   - Sector: SIC from the submissions JSON; the journal already carries `sic`.
   - Size: market cap needs shares outstanding, which Alpaca does not provide. The proxy is the 20-day dollar-volume
     decile from bars, and the choice goes into ADR 0008.
5. **Statistics:**
   - Clustering by entry month: STEP 0 found a design effect of 8.3 at 21 sessions.
   - Use cluster-robust (by month) standard errors, and report the effective n.
6. **Contracts:**
   - new tables `insider_lines`, `insider_events` and `event_returns`;
   - signed event envelopes (reuse `signing.py` and `verify.py`);
   - a trials ledger.

**I5.3 Recommended entry type.** Marketable or market-on-open at the first regular-session open after
acceptance + latency.
- Limit entries need their own pre-registered fill-selection measurement before use (P3D D2).

## Summary of prerequisites surfaced (for I1)

1. User decisions E1–E5 (ADR 0008).
2. Acceptance-time source rule (I3.2).
3. CIK → point-in-time symbol mapping, and the depth of corporate-action history (I4.1).
4. Bad-price, delisting and adjustment rules fixed before any return is computed.
5. The parity test design against the journal (I5.1).
