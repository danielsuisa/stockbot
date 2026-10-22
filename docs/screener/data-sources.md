# Screener engine — data sources and caching (Phase D)

## Sources

All sources are free, need no paid key, and are used within their published limits. finviz is never a runtime source
(G1); Yahoo and nasdaq.com are not used by the engine (G3).

| code | source | endpoint | cadence | rate limit (engine) | terms / note |
|---|---|---|---|---|---|
| SIP-D | Alpaca market data, free plan, consolidated tape | `GET /v2/stocks/bars?timeframe=1Day&feed=sip&adjustment=split`, ≤ 800 symbols per request, `limit` 10000 points across symbols, paged | daily | 190/min shared pacer (`bot/alpaca._get`), under the plan's 200/min; run budget < 150 calls | SIP only when the query's end is ≥ 15 min old (free plan). OHLC regular-session, volume all day (OBSERVED). Keys from the existing `ALPACA_KEY_ID` / `ALPACA_SECRET_KEY` secrets. |
| SIP-T | same | `timeframe=30Min`, 04:00 → now − 16 min, stage-2 survivors sized by the run's budget (D-040; OBSERVED: 588–840 bars a page) | each run | same | Never IEX for volume (IEX is a small slice of the tape). |
| SIP-H | same | `timeframe=1Hour`, 63 sessions (cumulative volume at each hour mark) | nightly increment, ≤ 500 calls a night | same | Time-of-day volume profile. |
| SIP-M | same | `timeframe=1Month` since 2016-01 | monthly | same | OBSERVED: history starts 2016-01. |
| AST | Alpaca trading API, read-only reference | `GET /v2/assets?status=active&asset_class=us_equity` | daily | 1 call | Fields: `shortable`, `easy_to_borrow`, `attributes` (contains `has_options`). No order endpoint is ever called (G5). |
| CA | Alpaca market data, corporate actions (read-only) | `GET /v1/corporate-actions?types=forward_split,reverse_split`, 1,000 a page, 3 years | nightly | ≈ 3 calls | Split ex-dates and rates: holdings and share counts are scaled to the evaluated day (D-057). |
| SEC-PF | SEC XBRL frames, dei:EntityPublicFloat | `data.sec.gov/api/xbrl/frames/dei/EntityPublicFloat/USD/CY{year}Q2I.json` (this year and last) + raw and split-adjusted Alpaca closes on each fact's date | nightly (closes only for new facts; split closes monthly) | 2 SEC + ≈ 80 Alpaca on first build | Issuer-reported float fallback (D-053). |
| CAL | Alpaca trading API, read-only reference | `GET /v2/calendar` | daily | 1 call | Sessions and early closes (D-005). |
| NEWS | Alpaca news (Benzinga) | `GET /v1beta1/news`, 50 items per page | on demand | same pacer | Only when a screen uses `news_date` / `n_majornews`. |
| NT | Nasdaq Trader symbol directory | `nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt`, `otherlisted.txt` | daily (files carry "File Creation Time") | 2 calls | Pipe-delimited reference files published for download; test issues dropped. This is not nasdaq.com (G3). |
| OCC | OCC Directory of Listed Products | `marketdata.theocc.com/delo-download?prodType=EU&downloadFields=OS;US;SN;EXCH&format=txt` | daily | 1 call | Equity option underlyings across all exchanges (6309 lines, OBSERVED 2026-10-21). OCC's site terms not reviewed (flagged). Fallback: Alpaca `has_options`. |
| SEC-F | SEC XBRL frames | `data.sec.gov/api/xbrl/frames/{taxonomy}/{tag}/{unit}/{CYyyyy[Qq][I]}.json` | weekly full, daily cheap | 8 req/s (`common.fetch`, `SEC_UA`); run budget < 2000 | Public domain. One value per company, concept and calendar period. Fields: `accn, cik, end, start, val` (OBSERVED). Tag fallbacks are required: plain `Revenues` covers 1941 companies in CY2025Q2 (OBSERVED). |
| SEC-S | SEC submissions | `data.sec.gov/submissions/CIK##########.json` | 30-day rolling refresh; daily for CIKs filing an 8-K | same | `sic`, `addresses`, `stateOfIncorporation`, recent filings with `items` and `acceptanceDateTime`. |
| SEC-4 | SEC Form 3/4/5 | EDGAR daily index → each filing's XML, for the days after the newest bulk quarter (accessions already read are skipped) | daily increment, capped per run | same | Holdings after each transaction, per reporting owner and line. |
| SEC-345 | SEC Insider Transactions Data Sets (flattened Forms 3/4/5) | ZIP hrefs from `www.sec.gov/data-research/sec-markets-data/insider-transactions-data-sets` (recent quarters under `/files/datastandardsinnovation/...`, older under `/files/structureddata/...`); tables SUBMISSION, REPORTINGOWNER, NONDERIV_TRANS, NONDERIV_HOLDING (D-045) | quarterly; the last 8 quarters once each | 1 call a quarter (+1 listing) | Public domain; "as filed", SEC disclaims accuracy. |
| SEC-13 | SEC Schedules 13D/13G (XML since 2024-12, D-047) | discovery: `edgar/full-index/YYYY/QTRn/form.idx` (closed quarters once) + the daily form index; then the latest filing per (issuer, filer): `edgar/data/<cik>/<accession>.txt` | nightly, ≤ 300 filings | same SEC budget | Public domain. Text-era `SC 13D/13G` filings are not read. |
| SEC-T | SEC ticker map | `www.sec.gov/files/company_tickers.json` (`common.tickers`) | daily | 1 call | Ticker → CIK. |
| FINRA-SI | FINRA consolidated short interest | `api.finra.org/data/group/otcMarket/name/consolidatedShortInterest` (`bot/shorts`) | twice monthly; usable from the 8th session after settlement | paged, 5000 rows | Rule 4560 data. The weekly-reporting proposal SR-FINRA-2026-012 was withdrawn, so twice monthly is assumed. |

## Metric → source map

Availability: **EXACT** = the definition is reproduced on full-market data. **APPROX** = a documented approximation
(shown with "≈"). **UNAVAILABLE_FREE** = no free, ToS-clean source. Computation details are in `metric-spec.md`.

## Metrics

| metric | source | availability | cadence | rate / budget | ToS | computation |
|---|---|---|---|---|---|---|
| `price` | SIP-D, SIP-T | EXACT | each run | SIP-T calls | Alpaca free plan | last RTH (or PRE/POST) bar close |
| `change` | SIP-D, SIP-T | EXACT | each run | | | price / prev close − 1 |
| `change_open` | SIP-T | EXACT | each run | | | price / RTH open − 1 |
| `gap` | SIP-D, SIP-T | EXACT | each run | | | open / prev close − 1 |
| `volume` | SIP-D, SIP-T | EXACT | each run | | | Σ volume 04:00 → now |
| `avg_volume` | SIP-D | EXACT | daily | | | mean 63 sessions |
| `rel_volume` | SIP-H, SIP-T | EXACT | each run | | | cumulative-volume profile |
| `volume_pct_float` | derived | APPROX | each run | | | volume / float |
| `trades` | SIP-D, SIP-T | APPROX | each run | | | Σ bar `n` |
| `perf` | SIP-D | EXACT | daily | | | family |
| `perf_w` | SIP-D | EXACT | daily | | | 5 sessions |
| `perf_m` | SIP-D | EXACT | daily | | | 21 sessions |
| `perf_q` | SIP-D | EXACT | daily | | | 63 sessions |
| `perf_h` | SIP-D | EXACT | daily | | | 126 sessions |
| `perf_y` | SIP-D | EXACT | daily | | | 252 sessions |
| `perf_3y` | SIP-D anchor | EXACT | daily | 1 anchor window a month | | 756 sessions |
| `perf_5y` | SIP-D anchor | EXACT | daily | same | | 1260 sessions |
| `perf_10y` | SIP-D anchor | APPROX | daily | same | | 2520 sessions; `None` before 2016 |
| `perf_ytd` | SIP-D | EXACT | daily | | | vs last close of the prior year |
| `volatility` | SIP-D | EXACT | daily | | | family |
| `volatility_w` | SIP-D | EXACT | daily | | | mean (h − l) / l, 5 |
| `volatility_m` | SIP-D | EXACT | daily | | | mean (h − l) / l, 21 |
| `rsi14` | SIP-D | EXACT | daily | | | Wilder RSI(14) |
| `atr14` | SIP-D | EXACT | daily | | | Wilder ATR(14) |
| `sma20` | SIP-D | EXACT | daily | | | family |
| `sma50` | SIP-D | EXACT | daily | | | family |
| `sma200` | SIP-D | EXACT | daily | | | family |
| `price_vs_sma20` | SIP-D | EXACT | daily | | | price / SMA20 − 1 |
| `price_vs_sma50` | SIP-D | EXACT | daily | | | price / SMA50 − 1 |
| `price_vs_sma200` | SIP-D | EXACT | daily | | | price / SMA200 − 1 |
| `sma20_vs_sma50` | SIP-D | EXACT | daily | | | SMA ratio − 1 |
| `sma20_vs_sma200` | SIP-D | EXACT | daily | | | SMA ratio − 1 |
| `sma50_vs_sma20` | SIP-D | EXACT | daily | | | SMA ratio − 1 |
| `sma50_vs_sma200` | SIP-D | EXACT | daily | | | SMA ratio − 1 |
| `sma200_vs_sma20` | SIP-D | EXACT | daily | | | SMA ratio − 1 |
| `sma200_vs_sma50` | SIP-D | EXACT | daily | | | SMA ratio − 1 |
| `cross_price_sma20` | SIP-D | EXACT | daily | | | sign change |
| `cross_price_sma50` | SIP-D | EXACT | daily | | | sign change |
| `cross_price_sma200` | SIP-D | EXACT | daily | | | sign change |
| `cross_sma20_sma50` | SIP-D | EXACT | daily | | | sign change |
| `cross_sma20_sma200` | SIP-D | EXACT | daily | | | sign change |
| `cross_sma50_sma20` | SIP-D | EXACT | daily | | | sign change |
| `cross_sma50_sma200` | SIP-D | EXACT | daily | | | sign change |
| `cross_sma200_sma20` | SIP-D | EXACT | daily | | | sign change |
| `cross_sma200_sma50` | SIP-D | EXACT | daily | | | sign change |
| `hl20d` | SIP-D | EXACT | daily | | | family |
| `hl50d` | SIP-D | EXACT | daily | | | family |
| `hl52w` | SIP-D | EXACT | daily | | | family |
| `hlall` | SIP-M, SIP-D | APPROX | daily | | | family, since 2016-01 |
| `from_high_20d` | SIP-D | EXACT | daily | | | price / max high − 1 |
| `from_low_20d` | SIP-D | EXACT | daily | | | price / min low − 1 |
| `from_high_50d` | SIP-D | EXACT | daily | | | as above |
| `from_low_50d` | SIP-D | EXACT | daily | | | as above |
| `from_high_52w` | SIP-D | EXACT | daily | | | as above |
| `from_low_52w` | SIP-D | EXACT | daily | | | as above |
| `from_high_all` | SIP-M, SIP-D | APPROX | daily | | | since 2016-01 |
| `from_low_all` | SIP-M, SIP-D | APPROX | daily | | | since 2016-01 |
| `new_high_20d` | SIP-D | EXACT | daily | | | today's high ≥ prior max |
| `new_low_20d` | SIP-D | EXACT | daily | | | today's low ≤ prior min |
| `new_high_50d` | SIP-D | EXACT | daily | | | as above |
| `new_low_50d` | SIP-D | EXACT | daily | | | as above |
| `new_high_52w` | SIP-D | EXACT | daily | | | as above |
| `new_low_52w` | SIP-D | EXACT | daily | | | as above |
| `new_high_all` | SIP-M, SIP-D | APPROX | daily | | | since 2016-01 |
| `new_low_all` | SIP-M, SIP-D | APPROX | daily | | | since 2016-01 |
| `beta` | SIP-M | APPROX | monthly | ~13 calls a month | | 60-month OLS vs SPY |
| `candlestick` | SIP-D, SIP-T | APPROX | each run | | | textbook rules |
| `pattern` | none | UNAVAILABLE_FREE | — | — | proprietary | — |
| `ssr` | SIP-D, SIP-T | APPROX | each run | | | low ≤ 0.9 × prev close |
| `ah_close` | SIP-T | APPROX | each run | | | last POST bar close |
| `ah_change` | SIP-T | APPROX | each run | | | ah_close / close − 1 |
| `exchange` | NT | EXACT | daily | 2 calls | reference file | exchange code map |
| `is_etf` | NT | EXACT | daily | | | ETF = Y |
| `is_shell` | SEC-S | APPROX | 30 days | | public domain | SIC 6770 |
| `optionable` | AST, OCC | EXACT | daily | 2 calls | OCC terms flagged | has_options or OCC underlying |
| `shortable` | AST | APPROX | daily | | broker-specific | Alpaca `shortable` |
| `sector` | SEC-S | APPROX | 30 days | rolling 400/run | public domain | SIC crosswalk |
| `industry` | SEC-S | APPROX | 30 days | rolling 400/run | public domain | SIC crosswalk |
| `country` | SEC-S | APPROX | 30 days | rolling 400/run | public domain | business address |
| `index` | none | UNAVAILABLE_FREE | — | — | D-020 | — |
| `theme` | none | UNAVAILABLE_FREE | — | — | proprietary | — |
| `subtheme` | none | UNAVAILABLE_FREE | — | — | proprietary | — |
| `shares_outstanding` | SEC-F (dei) | APPROX | weekly | 6 calls | public domain | cover-page shares |
| `market_cap` | SEC-F, SIP | APPROX | each run | | | price × shares |
| `float` | SEC-F, SEC-345, SEC-4, SEC-13 | APPROX | daily | 9 bulk calls once; ≤ 800 Form 4 + ≤ 300 13D/13G a night | public domain | outstanding − insiders − other ≥ 5% holders (D-049) |
| `float_pct` | derived | APPROX | daily | | | float / outstanding |
| `short_float` | FINRA-SI, derived | APPROX | twice monthly | 2–3 pages | FINRA data | SI / float |
| `short_ratio` | FINRA-SI, SIP-D | APPROX | twice monthly | | | SI / avg volume |
| `insider_own` | SEC-345, SEC-4 | APPROX | daily | ≤ 800/run | public domain | Σ holdings / outstanding |
| `insider_trans` | SEC-4 | APPROX | daily | | | 6-month change |
| `inst_own` | none | UNAVAILABLE_FREE | — | — | D-021 | — |
| `inst_trans` | none | UNAVAILABLE_FREE | — | — | D-021 | — |
| `recom` | none | UNAVAILABLE_FREE | — | — | licensed | — |
| `target_price` | none | UNAVAILABLE_FREE | — | — | licensed | — |
| `forward_pe` | none | UNAVAILABLE_FREE | — | — | licensed estimates | — |
| `peg` | none | UNAVAILABLE_FREE | — | — | licensed estimates | — |
| `eps_growth_this_y` | none | UNAVAILABLE_FREE | — | — | licensed estimates | — |
| `eps_growth_next_y` | none | UNAVAILABLE_FREE | — | — | licensed estimates | — |
| `eps_next_5y` | none | UNAVAILABLE_FREE | — | — | licensed estimates | — |
| `eps_rev` | none | UNAVAILABLE_FREE | — | — | licensed estimates | — |
| `pe` | SEC-F, SIP | APPROX | weekly + each run | ~300 frame calls/week | public domain | price / EPS_ttm |
| `ps` | SEC-F | APPROX | weekly | | | cap / revenue_ttm |
| `pb` | SEC-F | APPROX | weekly | | | cap / equity |
| `pc` | SEC-F | APPROX | weekly | | | cap / cash |
| `pfcf` | SEC-F | APPROX | weekly | | | cap / FCF_ttm |
| `ev_ebitda` | SEC-F | APPROX | weekly | | | EV / EBITDA_ttm |
| `ev_sales` | SEC-F | APPROX | weekly | | | EV / revenue_ttm |
| `dividend_yield` | SEC-F | APPROX | weekly | | | DPS_ttm / price |
| `payout` | SEC-F | APPROX | weekly | | | dividends / NI |
| `div_growth` | SEC-F | APPROX | weekly | | | family |
| `div_growth_1y` | SEC-F | APPROX | weekly | | | DPS YoY |
| `div_growth_3y` | SEC-F | APPROX | weekly | | | DPS CAGR 3 |
| `div_growth_5y` | SEC-F | APPROX | weekly | | | DPS CAGR 5 |
| `div_growth_years` | SEC-F | APPROX | weekly | | | growth streak |
| `eps_qoq` | SEC-F | APPROX | weekly | | | quarter YoY |
| `eps_yoy_ttm` | SEC-F | APPROX | weekly | | | TTM YoY |
| `eps_3y` | SEC-F | APPROX | weekly | | | CAGR 3 |
| `eps_5y` | SEC-F | APPROX | weekly | | | CAGR 5 |
| `sales_qoq` | SEC-F | APPROX | weekly | | | quarter YoY |
| `sales_yoy_ttm` | SEC-F | APPROX | weekly | | | TTM YoY |
| `sales_3y` | SEC-F | APPROX | weekly | | | CAGR 3 |
| `sales_5y` | SEC-F | APPROX | weekly | | | CAGR 5 |
| `roa` | SEC-F | APPROX | weekly | | | NI_ttm / assets |
| `roe` | SEC-F | APPROX | weekly | | | NI_ttm / equity |
| `roi` | SEC-F | APPROX | weekly | | | NI_ttm / (equity + LTD) |
| `current_ratio` | SEC-F | APPROX | weekly | | | CA / CL |
| `quick_ratio` | SEC-F | APPROX | weekly | | | (CA − inv) / CL |
| `lt_debt_eq` | SEC-F | APPROX | weekly | | | LTD / equity |
| `debt_eq` | SEC-F | APPROX | weekly | | | debt / equity |
| `gross_margin` | SEC-F | APPROX | weekly | | | GP / revenue |
| `oper_margin` | SEC-F | APPROX | weekly | | | OI / revenue |
| `net_margin` | SEC-F | APPROX | weekly | | | NI / revenue |
| `earnings_date` | SEC-S | APPROX | daily for 8-K filers | ≤ 600/run | public domain | 8-K Item 2.02 |
| `ipo_date` | SIP-D | APPROX | once per symbol | | | first bar |
| `news_date` | NEWS | APPROX | on demand | ≤ 40 pages | Alpaca terms | item times |
| `news_keywords` | none | UNAVAILABLE_FREE | — | — | Elite | — |
| `etf_heldby` | none | UNAVAILABLE_FREE | — | — | Elite | — |

## Caching and precompute (D2)

**Stores** (`bot/screener/stores/`, gz-JSON under `.cache/screener/`, written to a temp file and renamed atomically).
Every store records `as_of`, `source` and row counts. The engine refuses (marks "stale") any metric whose store is
older than its max age: bars 1 session, short interest 20 sessions, fundamentals 120 days, ownership 200 days.

| store | contents | refresh | cost |
|---|---|---|---|
| `universe` | Nasdaq Trader ∪, minus test issues; ETF flag; exchange; OCC optionable; Alpaca shortable / has_options; SEC CIK; warrants, units, rights, preferreds and notes dropped by name and suffix rules | daily (first run of the day) | 2 NT + 1 OCC + 1 AST + 1 SEC-T |
| `bars` | per symbol, the last 320 sessions of split-adjusted daily OHLCV + trade count. Backfilled once; afterwards each daily run appends the newest sessions. A full refetch when an overlap bar moved more than 0.5% (split adjustment) | daily | backfill ≈ 13 chunks × 26 pages ≈ 340 calls once; daily ≈ 13 calls |
| `long` | per symbol, monthly closes/highs/lows since 2016-01 (beta, all-time high/low) and the 3Y/5Y/10Y anchor closes | monthly / anchor windows monthly | ≈ 150 + 3 × 26 calls a month |
| `profile` | per symbol, the cumulative volume at each hour mark (04:00–20:00) for each of the last 63 sessions (mean taken at use) | nightly increment | ≈ 20 calls a night once built; the first backfill is bounded at 500 calls a night |
| `intraday` | today's 30-minute bars for stage-2 survivors (never cached across runs) | each run | sized by the run's Alpaca budget (D-040): ≈ 18–30 calls for `unusual_volume_liquid` |
| `shortint` | the newest usable FINRA report and the previous 5 | on publication days | 2–3 FINRA pages |
| `fundamentals` | per CIK: quarterly and annual facts for about 30 concepts (with fallbacks), from frames | weekly full (≈ 300 frame calls); daily for the latest quarter only | ≤ 2000 SEC calls a run |
| `reference` | per CIK: SIC, country, state, 8-K Item 2.02 times | rolling (400 CIKs a run) + every CIK in today's 8-K index | ≤ 600 SEC calls a run |
| `ownership` | per issuer CIK: latest holdings per officer and director, with history for the 6-month change | daily from the EDGAR daily index (≤ 600 Form 4s a run) | ≤ 600 SEC calls a run |

**Actions.** `actions/cache` keyed `screener-<store>-<ISO week>` with restore-keys fallback, plus the local
`LOCAL_CACHE_ROOT`. Cache files are never committed (`.cache/` is gitignored, and the Phase I sweep checks). Only small
state goes to git, through the existing commit-with-retry step: `data/screener_presets.json` (user presets) and
`data/screener_state.json` (last-result hashes for dedup).

**Two-stage evaluation (E3).**
- Stage 1 evaluates the static and daily filters on the stores, with no network call.
- Stage 2 fetches 30-minute SIP bars only for the survivors, as many as the run's remaining Alpaca budget allows (D-040: estimate = requests + pages at the OBSERVED ~580-bar page). Beyond that, symbols are ranked by the sort key on daily data and the cut is reported.

**Budget assertion.** Each run logs request counts per source (`alpaca`, `sec`, `finra`, `nt`, `occ`). The planner tests
assert that a full-universe scheduled RTH run stays under 150 Alpaca calls (stage 2 cut to what fits) and 2000 SEC
calls, and that `unusual_volume_liquid`'s stage 1 is fetched whole.

## Gate D totals

Counted over the 137 rows of the table above: the 136 catalog metrics plus the sort-only `short_ratio`.
`tests/test_screener_spec.py` keeps the table complete.

| availability | metrics | share |
|---|---|---|
| EXACT | 60 | 43.8% |
| APPROX | 61 | 44.5% |
| UNAVAILABLE_FREE | 16 | 11.7% |
