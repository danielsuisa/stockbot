# Intraday screener — H1 signal count on 2024, cache only (power gate check 1) — 2026-10-25

Run 40228865622 · 0s · Alpaca requests attempted and blocked: 1 ({'v2/calendar': 1}) · nothing downloaded

## Coverage

- Sessions in 2024: 0; with a cached top-20 list: 0; of those with cached minute bars: 0
- Ticker-days replayed: 0 · without minute bars: 0 · without a previous close: 0
- The top-20 list is the ZBA development run's (prefilter + opening relative volume ≥ 1); the screener's pre-market stage is not replayed (its pre-market bars are not in the cache).

## Signals (one per symbol, setup and day; the scan loop's first hit)

| Setup | Signals | Entry level touched later | Per 252 sessions |
|---|---:|---:|---:|
| vwap_hammer (H1) | 0 | 0 | 0 |
| vwap_engulf (H1) | 0 | 0 | 0 |
| flag (H1) | 0 | 0 | 0 |
| red_to_green | 0 | 0 | 0 |
| orb5 | 0 | 0 | 0 |
| orb15 | 0 | 0 | 0 |

**H1 total: 0 signals (0 a year); entry touched: 0 (0 a year).** "Touched" is an upper bound on fills (no limit, no slippage, no halt check), not a fill simulation.

## Power gate, check 1 (section 15; parameters as pre-registered, not changed)

- Required filled trades: n = (2.49 × 1.47R / 0.1R)² = 1340
- With every H1 signal filled: 0/year → inf years → FAIL (limit 1.5)
- With only the touched entries filled: 0/year → inf years → FAIL

No backtest was run, no fill simulated, no parameter changed.
