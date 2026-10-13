# Index program, phase 1: data probe (spec 8.1)

Spec: `docs/superpowers/specs/2026-10-12-index-program-design.md`, sections 3 and 8.1. Probe code:
`bot/index_probe.py`, with synthetic tests in `tests/test_index_probe.py`. GitHub run: `38271895893` (temporary
workflow on the branch `index-data-probe`).

**Blind rule.** Nothing here computes a return, profit, equity curve or drawdown of any rule or policy. The only
comparisons are price agreement between sources, which the spec allows.

**Result: no SPY source passes the acceptance rule of 8.1.** The probe stops here, as 8.1 says.

## Sources

| Source | Where tried | Status | First day | Last day | Rows | Prices | Storing (terms) |
|---|---|---|---|---|---|---|---|
| Stooq SPY / BIL CSV | this machine | 200, an HTML page: a JavaScript proof-of-work bot check instead of the CSV | — | — | — | not reached | Terms page not reachable (behind the same wall) |
| Stooq SPY / BIL CSV | GitHub runner | `URLError` (no answer) | — | — | — | not reached | — |
| FRED `DTB3` CSV | this machine | no answer (timeout) | — | — | — | — | — |
| FRED `DTB3` CSV | GitHub runner | 200, CSV | 1993-12-01 | 2026-09-30 | 8,566 | rate, % | **Not allowed:** FRED's terms forbid users to "store, cache, or archive" FRED content, whatever the series' copyright status |
| Yahoo chart SPY (unofficial) | both | 200, JSON | 1993-01-29 | 2026-09-30 | 8,475 | raw OHLC plus `adjclose`; 136 dividends | **Not allowed:** Yahoo's terms forbid automated collection and redistribution. Compared in memory only, never written |
| Yahoo chart BIL | GitHub runner | 200, JSON | 2007-05-30 | 2026-09-30 | 4,866 | raw plus `adjclose`; 127 dividends | Not allowed; not written |
| Alpaca SPY daily, `adjustment=all` | GitHub runner (repo secrets) | 200 | 2016-01-04 | 2026-09-30 | 2,701 | split and dividend adjusted | Not committed (exchange-licensed data); compared in memory only |
| Tiingo | not tried | needs a key (sign-up) | — | — | — | — | named as an option for the owner |

Notes:
- **Dividends.** Yahoo gives raw prices plus `adjclose`. The probe adjusts open, high and low by `adjclose / close`
  of the same day. Alpaca's `adjustment=all` adjusts for splits and dividends. Stooq's handling could not be
  checked.
- **`DTB3`** has 352 missing days (".") between 1993-12-01 and 2026-09-30, all of them bond-market holidays; the
  first are 1993-12-24, 1994-01-17 and 1994-02-21. Under 8.1, a missing day takes the previous value.
- **The FRED file** (sha256 `5ea29eadd254056ad0529cc92cb50a160001c7be1ef8c01e8577d4d45a893adc`) exists only as an
  artifact of run 38271895893. It was not committed. Nothing was deleted; the artifact expires under GitHub's
  retention.

## Acceptance (8.1)

| Check | Candidate | Numbers | Result |
|---|---|---|---|
| A full-history source | Yahoo (only one reached) | 1993-01-29 to 2026-09-30, 8,475 days | Reached, but may not be stored |
| 1. Integrity | Yahoo | Raw prices: 0 non-positive, 0 days with open or close outside [low, high]. Adjusted prices: 27 days outside by floating-point rounding only (all within 1e-9 relative, e.g. 1993-03-04) | Pass on raw prices; see question 2 |
| 1. Same trading days | Yahoo vs Alpaca, 2016-2026 | 2,701 against 2,701; none only in one | Pass |
| 2. Against Alpaca, 2016-01-04 to 2026-09-30 | Yahoo | Close-to-close change within 0.0005 on **93.59%** of days (need 99.5%); open-to-close within 0.0015 on **98.85%** (need 99%) | **Fail** |
| 3. Against a second full-history source, 1993-2015 | — | No second full-history source reached (Stooq blocked) | **Stop** (8.1: the owner decides) |

Largest differences in check 2:
- **Close-to-close change:** 0.0269 on 2020-03-13, 0.0264 on 2020-03-12, 0.0221 on 2020-03-18, 0.0185 on
  2020-03-19 and 0.0080 on 2020-03-10.
- **Open-to-close ratio:** 0.0288 on 2020-03-12, 0.0180 on 2020-03-18, 0.0136 on 2016-11-07, 0.0089 on
  2017-05-17 and 0.0076 on 2020-03-09.

The pattern is a hypothesis, not verified: the large gaps fall on the most volatile days. That fits Alpaca's daily
bar ending with extended-hours trades rather than the official closing auction.

## What the owner can supply by hand (8.1: no source passed)

1. **SPY, full history**, daily open, high, low and close, with dividends or adjusted prices, 1993-01-29 to
   2026-09-30, from a source whose terms allow keeping it. The simplest is Stooq's own CSV, downloaded in a
   browser: `https://stooq.com/q/d/l/?s=spy.us&i=d`. A second full-history file is needed for check 3, for example
   the same series from a broker export or from Tiingo (free tier, needs the owner's sign-up).
2. **The bill rate.** FRED's terms forbid storing its data. The same series comes from its originator, the
   Federal Reserve Board's H.15 release (3-month Treasury bill, secondary market, discount basis, business days),
   through the Board's Data Download Program. It is published by a federal agency; its terms should still be
   confirmed before it is committed.
