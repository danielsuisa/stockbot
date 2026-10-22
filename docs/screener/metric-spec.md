# Screener engine — metric specification (Phase C)

Every metric the catalog (`bot/screener/catalog.yaml`) can reference has exactly one row below.
`tests/test_screener_spec.py` checks that. Columns:
- **formula**: what the engine computes, in natural units. Fractions (0.05 = 5%) are converted to the code's
  percent points by `catalog.UNIT_FACTOR`.
- **window** and **source**.
- **latency**: how old the input is when a screen runs.
- **approx**: `≈` = the value is shown with the "≈" marker and a footnote (G9).
- **evidence**: the tag for the definition itself.

## Conventions (apply to every row)

- **Time.** Everything is computed in America/New_York. Sessions come from the Alpaca calendar (D-005): PRE 04:00–open,
  RTH open–close, POST close–20:00, CLOSED otherwise. Alerts show the time in New York and in Israel.
- **Bars.** Alpaca SIP, `adjustment=split` (DECISION D-030: performance and technicals are split-adjusted, not
  dividend-adjusted).
  - A daily bar has regular-session open/high/low/close and whole-day (04:00–20:00) volume, both OBSERVED (D-006).
  - "Today in progress" is built from 5-minute SIP bars up to now − 16 min: price = last RTH bar close; open = first RTH
    bar open; high/low = RTH bars; volume = all bars since 04:00.
  - In PRE, the price is the last pre-market trade, labelled "pre-market".
- **"Price"** is today's price in PRE/RTH/POST when stage 2 ran for the stock, else the last session close.
  - finviz itself shows the regular-session close in PRE and POST (OBSERVED on its help page: "current stock price or
    the close price during the last trading session").
  - Parity runs therefore use CLOSED mode (completed daily bars only).
- **Missing input.** A missing input makes the metric `None`. A `None` never passes a filter, and the row's
  "missing" counter says so.
- **Staleness.** A metric whose store is older than its max age is `None` and listed as "stale": bars 1 session,
  short interest 20 sessions, fundamentals 120 days, ownership 200 days.

## Metrics

| metric | formula | window | source | latency | approx | evidence |
|---|---|---|---|---|---|---|
| `price` | Last price (see conventions) | — | Alpaca SIP | ≥ 15 min | | OBSERVED (finviz help) |
| `change` | price / previous RTH close − 1 | 1 session | Alpaca SIP | ≥ 15 min | | OBSERVED |
| `change_open` | price / today's RTH open − 1 (`None` before the open) | today | Alpaca SIP | ≥ 15 min | | OBSERVED |
| `gap` | today's RTH open / previous RTH close − 1. In PRE: pre-market price / previous close, labelled "pre-market" | today | Alpaca SIP | ≥ 15 min | | OBSERVED |
| `volume` | Shares traded 04:00–20:00 today (so far), or in the last session | today | Alpaca SIP | ≥ 15 min | | OBSERVED (finviz help); whole-day basis INFERRED |
| `avg_volume` | Mean daily volume of the last 63 completed sessions | 63 sessions | Alpaca SIP daily | 1 session | | OBSERVED "3-month"; 63 INFERRED (forum report of a finviz reply) |
| `rel_volume` | In session: volume so far / mean of the same cumulative time-of-day volume over the last 63 sessions (30-minute profile, 04:00 start). After 20:00: full-day volume / `avg_volume` | 63 sessions | Alpaca SIP 30-min + daily | ≥ 15 min | | OBSERVED "intraday adjusted"; profile method INFERRED |
| `volume_pct_float` | volume / float | today | derived | ≥ 15 min | ≈ | INFERRED |
| `trades` | SIP trade count of the session (bar `n`) | today | Alpaca SIP | ≥ 15 min | ≈ | ASSUMED (finviz Elite definition unknown) |
| `perf` | Family: see `perf_w` … `perf_10y` (every `ta_perf` option names one) | — | — | — | | OBSERVED |
| `perf_w` | close / close 5 sessions earlier − 1 (today's price when in session) | 5 | Alpaca SIP daily | 1 session | | OBSERVED (finviz help) |
| `perf_m` | as `perf_w`, 21 sessions | 21 | Alpaca SIP daily | 1 session | | OBSERVED |
| `perf_q` | as `perf_w`, 63 sessions | 63 | Alpaca SIP daily | 1 session | | OBSERVED |
| `perf_h` | as `perf_w`, 126 sessions | 126 | Alpaca SIP daily | 1 session | | OBSERVED |
| `perf_y` | as `perf_w`, 252 sessions | 252 | Alpaca SIP daily | 1 session | | OBSERVED |
| `perf_3y` | as `perf_w`, 756 sessions | 756 | Alpaca SIP daily (anchor bar) | 1 session | | OBSERVED |
| `perf_5y` | as `perf_w`, 1260 sessions | 1260 | Alpaca SIP daily (anchor bar) | 1 session | | OBSERVED |
| `perf_10y` | as `perf_w`, 2520 sessions. `None` when the anchor predates Alpaca's history (2016-01) | 2520 | Alpaca SIP daily | 1 session | ≈ | OBSERVED; history limit DECISION |
| `perf_ytd` | price / last close of the prior calendar year − 1 | YTD | Alpaca SIP daily | 1 session | | INFERRED |
| `volatility` | Family: see `volatility_w`, `volatility_m` | — | — | — | | OBSERVED |
| `volatility_w` | mean over 5 sessions of (high − low) / low (DECISION D-032; parity also tests / close) | 5 | Alpaca SIP daily | 1 session | | OBSERVED "average daily high/low % range"; denominator DECISION |
| `volatility_m` | as `volatility_w`, 21 sessions | 21 | Alpaca SIP daily | 1 session | | OBSERVED; denominator DECISION |
| `rsi14` | RSI(14) on closes, Wilder smoothing seeded with the simple mean of the first 14 changes, over the last 250 closes | 14 (+warm-up) | Alpaca SIP daily | 1 session | | OBSERVED RSI(14); smoothing INFERRED |
| `atr14` | Wilder average (α = 1/14, seeded with the simple mean) of TR = max(high, prev close) − min(low, prev close); parity also tests α = 2/15 | 14 | Alpaca SIP daily | 1 session | | OBSERVED TR and "exponential (14-days)"; α INFERRED |
| `sma20` | Family: price vs SMA20 and its crosses; see `price_vs_sma20`, `cross_*` | — | — | — | | OBSERVED |
| `sma50` | Family, as `sma20` | — | — | — | | OBSERVED |
| `sma200` | Family, as `sma20` | — | — | — | | OBSERVED |
| `price_vs_sma20` | price / mean(last 20 closes) − 1 (today's price counts as today's close in session) | 20 | Alpaca SIP daily | 1 session | | OBSERVED SMA; relative form INFERRED |
| `price_vs_sma50` | as above, 50 | 50 | Alpaca SIP daily | 1 session | | INFERRED |
| `price_vs_sma200` | as above, 200 | 200 | Alpaca SIP daily | 1 session | | INFERRED |
| `sma20_vs_sma50` | SMA20 / SMA50 − 1 | 50 | derived | 1 session | | INFERRED |
| `sma20_vs_sma200` | SMA20 / SMA200 − 1 | 200 | derived | 1 session | | INFERRED |
| `sma50_vs_sma20` | SMA50 / SMA20 − 1 | 50 | derived | 1 session | | INFERRED |
| `sma50_vs_sma200` | SMA50 / SMA200 − 1 | 200 | derived | 1 session | | INFERRED |
| `sma200_vs_sma20` | SMA200 / SMA20 − 1 | 200 | derived | 1 session | | INFERRED |
| `sma200_vs_sma50` | SMA200 / SMA50 − 1 | 200 | derived | 1 session | | INFERRED |
| `cross_price_sma20` | "above" when price > SMA20 today and close ≤ SMA20 yesterday; "below" for the reverse; else `None` | 21 | derived | 1 session | | INFERRED (sign change, yesterday → today) |
| `cross_price_sma50` | as above, SMA50 | 51 | derived | 1 session | | INFERRED |
| `cross_price_sma200` | as above, SMA200 | 201 | derived | 1 session | | INFERRED |
| `cross_sma20_sma50` | "above"/"below" when SMA20 − SMA50 changed sign since yesterday | 51 | derived | 1 session | | INFERRED |
| `cross_sma20_sma200` | as above, SMA20 vs SMA200 | 201 | derived | 1 session | | INFERRED |
| `cross_sma50_sma20` | as above, SMA50 vs SMA20 | 51 | derived | 1 session | | INFERRED |
| `cross_sma50_sma200` | as above, SMA50 vs SMA200 | 201 | derived | 1 session | | INFERRED |
| `cross_sma200_sma20` | as above, SMA200 vs SMA20 | 201 | derived | 1 session | | INFERRED |
| `cross_sma200_sma50` | as above, SMA200 vs SMA50 | 201 | derived | 1 session | | INFERRED |
| `hl20d` | Family: see `from_high_20d`, `from_low_20d`, `new_high_20d`, `new_low_20d` | — | — | — | | OBSERVED |
| `hl50d` | Family, 50 sessions | — | — | — | | OBSERVED |
| `hl52w` | Family, 252 sessions | — | — | — | | OBSERVED |
| `hlall` | Family, all history available (since 2016-01) | — | — | — | ≈ | OBSERVED |
| `from_high_20d` | price / max(high over the last 20 sessions incl. today) − 1 (≤ 0) | 20 | Alpaca SIP daily | 1 session | | OBSERVED "percentual distance from the record high" |
| `from_low_20d` | price / min(low over the last 20 sessions incl. today) − 1 (≥ 0) | 20 | Alpaca SIP daily | 1 session | | OBSERVED |
| `from_high_50d` | as above, 50 | 50 | Alpaca SIP daily | 1 session | | OBSERVED |
| `from_low_50d` | as above, 50 | 50 | Alpaca SIP daily | 1 session | | OBSERVED |
| `from_high_52w` | as above, 252 | 252 | Alpaca SIP daily | 1 session | | OBSERVED |
| `from_low_52w` | as above, 252 | 252 | Alpaca SIP daily | 1 session | | OBSERVED |
| `from_high_all` | as above, all bars since 2016-01 (monthly highs before the daily window) | all | Alpaca SIP monthly + daily | 1 session | ≈ | OBSERVED; history limit DECISION |
| `from_low_all` | as above, lows | all | Alpaca SIP monthly + daily | 1 session | ≈ | OBSERVED; history limit DECISION |
| `new_high_20d` | today's high ≥ max(high of the previous 19 sessions) | 20 | Alpaca SIP daily | 1 session | | INFERRED |
| `new_low_20d` | today's low ≤ min(low of the previous 19 sessions) | 20 | Alpaca SIP daily | 1 session | | INFERRED |
| `new_high_50d` | as above, 50 | 50 | Alpaca SIP daily | 1 session | | INFERRED |
| `new_low_50d` | as above, 50 | 50 | Alpaca SIP daily | 1 session | | INFERRED |
| `new_high_52w` | as above, 252 | 252 | Alpaca SIP daily | 1 session | | INFERRED |
| `new_low_52w` | as above, 252 | 252 | Alpaca SIP daily | 1 session | | INFERRED |
| `new_high_all` | as above, all history since 2016-01 | all | Alpaca SIP | 1 session | ≈ | INFERRED |
| `new_low_all` | as above, all history since 2016-01 | all | Alpaca SIP | 1 session | ≈ | INFERRED |
| `beta` | OLS slope of 60 monthly % changes of the stock on SPY's (both month-end closes; ≥ 24 months required) | 60 months | Alpaca SIP monthly | 1 month | ≈ | OBSERVED (finviz help); SPY proxy DECISION |
| `candlestick` | Today's (or the last) daily candle, textbook rules: body = \|c − o\|, range = h − l. Doji: body ≤ 0.1·range; dragonfly / gravestone dojis by shadow side; marubozu: body ≥ 0.95·range; spinning top: body ≤ 0.3·range with both shadows ≥ body; hammer: lower shadow ≥ 2·body, upper ≤ 0.1·range, after a 5-session decline; inverted hammer mirrored; long lower/upper shadow: shadow ≥ 0.6·range | 1 (+5) | Alpaca SIP daily | 1 session | ≈ | ASSUMED (textbook; finviz thresholds unknown) |
| `pattern` | — (chart patterns are proprietary) | — | none | — | | UNKNOWN |
| `ssr` | Rule 201 short-sale restriction: today's low ≤ 0.9 × previous close (triggered today), or yesterday's low ≤ 0.9 × the close before (carried over) | 2 sessions | Alpaca SIP | ≥ 15 min | ≈ | OBSERVED (Rule 201 text); trade-level trigger approximated by bar lows |
| `ah_close` | Last SIP bar close between the RTH close and 20:00 today (`None` before the close) | today POST | Alpaca SIP 5-min | ≥ 15 min | ≈ | INFERRED |
| `ah_change` | ah_close / today's RTH close − 1 | today POST | Alpaca SIP 5-min | ≥ 15 min | ≈ | INFERRED |
| `exchange` | Listing exchange: nasdaqlisted → `nasd`; otherlisted A → `amex`, N → `nyse`, P → `arca`, Z → `cboe`, V → `iex` | — | Nasdaq Trader symbol directory | 1 day | | OBSERVED (file layout); `arca` has no finviz option (INFERRED) |
| `is_etf` | ETF flag = Y in the symbol directory | — | Nasdaq Trader | 1 day | | OBSERVED |
| `is_shell` | SIC 6770 (blank checks) | — | SEC submissions | 30 days | ≈ | INFERRED |
| `optionable` | Alpaca asset attribute `has_options`, or the symbol is an OCC equity underlying | — | Alpaca assets, OCC | 1 day | | OBSERVED (finviz help "has listed options") |
| `shortable` | Alpaca asset `shortable` (Alpaca's own borrow availability, broker-specific) | — | Alpaca assets | 1 day | ≈ | OBSERVED (finviz help "available to sell short"); broker basis DECISION |
| `sector` | SEC SIC code → finviz-style sector (static crosswalk `bot/screener/sic.py`, labelled "SIC-mapped") | — | SEC submissions | 30 days | ≈ | DECISION |
| `industry` | SEC SIC code → finviz-style industry (same crosswalk) | — | SEC submissions | 30 days | ≈ | DECISION |
| `country` | SEC business-address country; a US state → `usa`; else the state of incorporation | — | SEC submissions | 30 days | ≈ | INFERRED (finviz: "geographic location of a company") |
| `index` | — | — | none (D-020) | — | | UNKNOWN |
| `theme` | — | — | none (proprietary) | — | | UNKNOWN |
| `subtheme` | — | — | none (proprietary) | — | | UNKNOWN |
| `shares_outstanding` | dei:EntityCommonStockSharesOutstanding from the latest 10-K/10-Q cover, at most 400 days old (`bot/shorts` rules) | — | SEC XBRL frames | ≤ 1 quarter | ≈ | OBSERVED (finviz help: total − treasury) |
| `market_cap` | price × shares_outstanding | — | derived | as inputs | ≈ | OBSERVED (finviz help) |
| `float` | shares_outstanding − Σ latest Form 3/4/5 direct + indirect holdings of officers and directors (reporting owners who are not only 10% owners), when ≥ 1 such filing in 24 months; else shares_outstanding (`float_method = outstanding_proxy`) | — | SEC XBRL + Form 3/4/5 | ≤ 1 quarter | ≈ | OBSERVED definition; ≥ 5% holders and Rule 144 not subtracted (DECISION D-033) |
| `float_pct` | float / shares_outstanding | — | derived | as inputs | ≈ | INFERRED |
| `short_float` | FINRA short interest (newest report usable today: 8th session after settlement) / float | twice monthly | FINRA + derived | 8–23 sessions | ≈ | OBSERVED (finviz help) |
| `short_ratio` | FINRA short interest / avg_volume (63 sessions); FINRA `daysToCoverQuantity` kept for comparison (D-034) | twice monthly | FINRA + Alpaca | 8–23 sessions | ≈ | INFERRED |
| `insider_own` | Σ latest holdings of officers and directors / shares_outstanding | 24 months of filings | SEC Form 3/4/5 | 1 session | ≈ | OBSERVED wording ("owned by management") |
| `insider_trans` | (holdings now − holdings 6 months ago) / holdings 6 months ago, same owners | 6 months | SEC Form 4 | 1 session | ≈ | OBSERVED wording; 6-month window DECISION |
| `inst_own` | — | — | none (D-021) | — | | UNKNOWN |
| `inst_trans` | — | — | none (D-021) | — | | UNKNOWN |
| `recom` | — (analyst ratings) | — | none | — | | UNKNOWN |
| `target_price` | — (analyst targets) | — | none | — | | UNKNOWN |
| `forward_pe` | — (estimates) | — | none | — | | UNKNOWN |
| `peg` | — (estimates) | — | none | — | | UNKNOWN |
| `eps_growth_this_y` | — (current-FY estimate) | — | none | — | | UNKNOWN |
| `eps_growth_next_y` | — (next-FY estimate) | — | none | — | | UNKNOWN |
| `eps_next_5y` | — (long-term estimate) | — | none | — | | UNKNOWN |
| `eps_rev` | — (surprise vs estimates) | — | none | — | | UNKNOWN |
| `pe` | price / EPS_ttm. EPS_ttm = Σ last 4 quarters of diluted EPS (Q4 = FY − 9M; basic EPS when diluted is missing). `None` when EPS_ttm ≤ 0 | TTM | SEC XBRL frames | ≤ 1 quarter | ≈ | OBSERVED (finviz help); diluted basis DECISION |
| `ps` | market_cap / revenue_ttm | TTM | SEC XBRL | ≤ 1 quarter | ≈ | OBSERVED |
| `pb` | market_cap / latest stockholders' equity (`None` when ≤ 0) | latest quarter | SEC XBRL | ≤ 1 quarter | ≈ | OBSERVED |
| `pc` | market_cap / latest cash and equivalents (`None` when ≤ 0) | latest quarter | SEC XBRL | ≤ 1 quarter | ≈ | OBSERVED |
| `pfcf` | market_cap / (CFO_ttm − capex_ttm) (`None` when ≤ 0) | TTM | SEC XBRL | ≤ 1 quarter | ≈ | OBSERVED "annual FCF"; TTM DECISION |
| `ev_ebitda` | (market_cap + total debt − cash) / (operating income_ttm + D&A_ttm) | TTM | SEC XBRL | ≤ 1 quarter | ≈ | INFERRED |
| `ev_sales` | (market_cap + total debt − cash) / revenue_ttm | TTM | SEC XBRL | ≤ 1 quarter | ≈ | INFERRED |
| `dividend_yield` | Σ dividends declared per share over the last 4 quarters (annual when quarters are missing) / price. Companies with fundamentals but no dividend facts = 0 | TTM | SEC XBRL | ≤ 1 quarter | ≈ | OBSERVED (forward estimate first; the engine has TTM only) |
| `payout` | dividends paid_ttm / net income_ttm (`None` when NI ≤ 0) | TTM | SEC XBRL | ≤ 1 quarter | ≈ | OBSERVED |
| `div_growth` | Family: `div_growth_1y/3y/5y`, `div_growth_years` | — | — | — | ≈ | OBSERVED |
| `div_growth_1y` | annual DPS / DPS a year earlier − 1 | 1 FY | SEC XBRL annual | ≤ 1 year | ≈ | INFERRED |
| `div_growth_3y` | DPS CAGR over 3 fiscal years | 3 FY | SEC XBRL annual | ≤ 1 year | ≈ | INFERRED |
| `div_growth_5y` | DPS CAGR over 5 fiscal years | 5 FY | SEC XBRL annual | ≤ 1 year | ≈ | INFERRED |
| `div_growth_years` | Consecutive fiscal years with DPS up on the year before | ≤ 10 FY | SEC XBRL annual | ≤ 1 year | ≈ | INFERRED |
| `eps_qoq` | diluted EPS of the latest quarter / same quarter a year earlier − 1 (base ≤ 0 → `None`) | 2 quarters | SEC XBRL | ≤ 1 quarter | ≈ | OBSERVED (finviz help) |
| `eps_yoy_ttm` | EPS_ttm / EPS_ttm a year earlier − 1 (base ≤ 0 → `None`) | 8 quarters | SEC XBRL | ≤ 1 quarter | ≈ | INFERRED |
| `eps_3y` | CAGR of annual diluted EPS over 3 FY; mean YoY when the base ≤ 0 | 3 FY | SEC XBRL annual | ≤ 1 year | ≈ | OBSERVED "annual growth"; CAGR DECISION |
| `eps_5y` | as above, 5 FY | 5 FY | SEC XBRL annual | ≤ 1 year | ≈ | OBSERVED; CAGR DECISION |
| `sales_qoq` | revenue of the latest quarter / same quarter a year earlier − 1 | 2 quarters | SEC XBRL | ≤ 1 quarter | ≈ | OBSERVED |
| `sales_yoy_ttm` | revenue_ttm / revenue_ttm a year earlier − 1 | 8 quarters | SEC XBRL | ≤ 1 quarter | ≈ | INFERRED |
| `sales_3y` | revenue CAGR over 3 FY | 3 FY | SEC XBRL annual | ≤ 1 year | ≈ | INFERRED |
| `sales_5y` | revenue CAGR over 5 FY | 5 FY | SEC XBRL annual | ≤ 1 year | ≈ | OBSERVED "annual sales increase over past 5 years"; CAGR DECISION |
| `roa` | net income_ttm / latest total assets | TTM | SEC XBRL | ≤ 1 quarter | ≈ | OBSERVED (annual earnings / total assets); TTM INFERRED |
| `roe` | net income_ttm / latest stockholders' equity (`None` when equity ≤ 0) | TTM | SEC XBRL | ≤ 1 quarter | ≈ | OBSERVED |
| `roi` | net income_ttm / (equity + long-term debt) | TTM | SEC XBRL | ≤ 1 quarter | ≈ | OBSERVED "invested capital"; components INFERRED |
| `current_ratio` | current assets / current liabilities | latest quarter | SEC XBRL | ≤ 1 quarter | ≈ | OBSERVED |
| `quick_ratio` | (current assets − inventory) / current liabilities | latest quarter | SEC XBRL | ≤ 1 quarter | ≈ | OBSERVED |
| `lt_debt_eq` | long-term debt (non-current) / equity | latest quarter | SEC XBRL | ≤ 1 quarter | ≈ | OBSERVED |
| `debt_eq` | (short-term debt + current LTD + long-term debt) / equity. The help text says current liabilities / equity; both variants are reported in parity (D-031) | latest quarter | SEC XBRL | ≤ 1 quarter | ≈ | OBSERVED conflict → DECISION |
| `gross_margin` | gross profit_ttm (or revenue − cost of revenue) / revenue_ttm | TTM | SEC XBRL | ≤ 1 quarter | ≈ | OBSERVED |
| `oper_margin` | operating income_ttm / revenue_ttm | TTM | SEC XBRL | ≤ 1 quarter | ≈ | OBSERVED |
| `net_margin` | net income_ttm / revenue_ttm | TTM | SEC XBRL | ≤ 1 quarter | ≈ | OBSERVED |
| `earnings_date` | Past: the latest 8-K with Item 2.02, at its EDGAR acceptance time; before 09:30 → "before open", at/after 16:00 → "after close", else "during". Future: the same-quarter 8-K of last year + 364 days, labelled "est." | — | SEC submissions | 1 session | ≈ | OBSERVED (finviz help "nearest earnings date"); method DECISION |
| `ipo_date` | Date of the first SIP daily bar; the first bar on 2016-01-04 means "on or before" | — | Alpaca SIP | 1 day | ≈ | DECISION |
| `news_date` | Times of the Alpaca news items naming the symbol (Benzinga feed) | today / yesterday / last N | Alpaca news | ≥ minutes | ≈ | DECISION |
| `news_keywords` | — (Elite) | — | none | — | | UNKNOWN |
| `etf_heldby` | — (Elite) | — | none | — | | UNKNOWN |

## Signals (s=)

| signal | rule | cap | evidence |
|---|---|---|---|
| `ta_topgainers` | change > 0, highest first | 200 | OBSERVED cap; > 0 INFERRED |
| `ta_toplosers` | change < 0, lowest first | 200 | OBSERVED cap |
| `ta_newhigh` | `new_high_52w`, highest change first | 200 | OBSERVED cap; ranking INFERRED |
| `ta_newlow` | `new_low_52w`, lowest change first | 200 | OBSERVED cap |
| `ta_mostvolatile` | today's (high − low) / low, highest first | 200 | OBSERVED |
| `ta_mostactive` | volume, highest first | 200 | OBSERVED |
| `ta_unusualvolume` | rel_volume, highest first (rel_volume > 1 required, INFERRED) | 200 | OBSERVED |
| `ta_overbought` | rsi14 > 70, highest first | 200 | OBSERVED cap; threshold DECISION |
| `ta_oversold` | rsi14 < 30, lowest first | 100 | OBSERVED cap; threshold DECISION |
| `n_earningsbefore` | earnings 8-K accepted today before 09:30 | all | OBSERVED |
| `n_earningsafter` | earnings 8-K accepted today at/after 16:00 | all | OBSERVED |
| `n_majornews` | today's Alpaca news count, highest first | 40 | OBSERVED cap |
| `it_latestbuys` | Form 4 code-P purchases filed in the last 2 sessions | all | INFERRED |
| `it_latestsales` | Form 4 code-S sales filed in the last 2 sessions | all | INFERRED |
| `f_ssr` | `ssr` | all | OBSERVED (Rule 201) |
