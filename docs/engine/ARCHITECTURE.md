> **Status (2026-10-20): engine resumed only for the swing-ticket line ([ADR 0012](adr/0012-engine-resumed-swing-tickets.md)).**
>
> **Status (2026-10-19): the intraday scalping line is closed. See [ADR 0007](adr/0007-intraday-line-closed.md). This document is kept as the historical design.**
>
> **Status (2026-10-20): insider line closed and engine paused ([ADR 0011](adr/0011-insider-line-closed-engine-paused.md)).**

Intraday scalping · US equities · recommendations only

# Scalping Engine Architecture

An autonomous learning network that screens, times, and recommends bracket orders for intraday US-equity scalping, with a news and geopolitics layer. Execution stays manual in IBKR.

## 0. Correcting the objective

"Infinite money" is not an engineering target. Four hard limits block it: capacity (any intraday strategy starts moving price above a few hundred thousand dollars per trade), costs (spread + slippage eat a thin edge), the PDT rule (below $25,000, 3 day trades per 5 trading days) until the broker moves the account to FINRA's 2026 intraday-margin standard; brokers may phase in until 2027-10-20 (ADR 0001 §7), and overfitting (ZBA failed on exactly this).

**The target defined in this document:** proven positive expectancy out-of-sample after costs, bounded drawdown, and compounding over time. "Infinite" is excluded from the design on purpose: a system that plans for infinity scales size until it dies.

## 1. Rules that never break

**R1**

The system recommends only. No layer sends orders to IBKR automatically, not even "paper only". Execution is manual, from the card.

**R2**

The protected holdout (2016–2021) stays sealed: not for training, not for hyperparameter tuning, not for "just a look". One open = the sample is burned.

**R3**

Zero-spend. Any paid data source, model, or cloud resource requires explicit approval before use.

**R4**

Independent evaluator. The component that measures performance shares no code, features, or labels with the component that trains. GO/NO-GO is decided by it alone.

**R5**

Baseline before network. Every deep model must beat LightGBM on the same features out-of-sample, or it does not ship.

**R6**

Realistic costs in every measurement: actual spread, volume-dependent slippage, IBKR commissions, and 2–5 seconds of latency (a human reads a card and taps).

## 2. Data flow

## 3. System layers

L0

### Data ingestion

Free sources first: IBKR TWS API (historical 1-min bars, real-time L1 under the existing subscription), Alpaca IEX free feed, yfinance for EOD, SEC EDGAR, GDELT, Reuters/AP/Fed RSS, FOMC/CPI/NFP calendar.

- Every record is stored raw in Parquet with an `ingested_at` timestamp separate from the event time, so no feature can see the future.
- Deterministic replay engine: the same code runs backtest and live; only the stream source differs.

L1

### Feature store + market regime

- Microstructure: VWAP and deviation from it, RVOL, ATR-14 in percent, gap from prior close, pre-market range, spread, bid/ask imbalance, minutes since open.
- Breadth and market: VIX, advance/decline ratio, sector relative strength vs SPY / QQQ / IWM, 5- and 20-minute index trend.
- Regime: daily classification into `trend / chop / event` via HMM or rules, fed as input to the gating in L3.
- Every feature is versioned; a changed feature is a new version, never an overwrite.

L2

### Screener and stock universe

The universe is rebuilt every morning at 08:30 ET and refreshed every 5 minutes until 11:30. The screener looks for one thing: a move with liquidity that will resolve within hours.

| Parameter | Starting value | Why |
| --- | --- | --- |
| Price | $5 – $200 | Below 5 you get halts; above 200 the size does not fit the capital |
| 20-day average volume | > 2M | Slippage stays controlled |
| RVOL at open | > 2.0 | Someone has a reason to trade it today |
| ATR % | > 2% | The range covers costs |
| Spread | \< 0.10% | Otherwise the edge is gone at entry |
| Catalyst | flag | Earnings / news / upgrade / sector macro |
| Dilution | exclude | No active offering/ATM in the last 30 days (EDGAR) |

These values are a starting point. In phase P4 a ranking model (LightGBM LambdaRank) learns the weights from the screener's own outcomes, and the table becomes a minimum filter only.

L3

### Model core: three heads

- **Labeling:** triple-barrier. For every minute: does price touch `+k·ATR` before `−m·ATR` within horizon H (15–90 minutes). Label is Long / Short / None, weighted by move size.
- **Direction head:** probability for each of the three labels.
- **Timing head:** probability that the current minute is an entry minute (meta-labeling on top of the direction head: "should this signal be trusted now").
- **Exit head:** regression for optimal target and stop distance in ATR units, plus a time horizon for exit.
- **Structure:** sequence encoder over 120 one-minute bars (TCN or a small 4-layer Transformer), concatenated with the news embedding (L4) and the regime vector (L1). Regime gating via mixture-of-experts with 3 experts.
- **Training:** purged K-fold with a one-day embargo, monthly walk-forward, early stopping on profit factor after costs, not on accuracy.

L4

### News and geopolitics engine

- Pipeline: ingest → dedupe → NER (company / sector / country / currency) → event classification (earnings, regulation, macro, conflict, sanctions, energy) → impact score and half-life.
- Embeddings from a local open model (`bge-small` or similar, free). Sentiment and novelty (how different the item is from what is already known) enter as features.
- Geopolitics is translated into an exposure map: event → affected sectors / currencies / ETFs → stocks in the universe. The model does not "understand" politics; it learns the historical price response to an event type.
- Dual output: a vector for L3, and a kill-switch for L6 (for example, block entries 15 minutes around FOMC, full stop on an extreme-novelty event).

L5

### Bracket order builder

- Entry: limit at current price ± half spread, or stop-limit above the breakout level when the timing head says "wait".
- Stop: the stricter of the exit head output and structure (low of the last 5 minutes), rounded to tick.
- Target: R-multiple from the exit head, minimum 1.5R after costs.
- Size: fractional Kelly (¼) on the calibrated probability, capped at 1% risk per trade and by liquidity (no more than 1% of the minute's volume).
- Time-to-live: if not filled within 10 minutes the recommendation expires. Time-stop on the position by horizon H.
- Output: signed JSON plus a human-readable card. Example: `BUY 120 NVDA LMT 141.20 · STP 139.85 · TGT 143.60 · TTL 10m · p=0.63`

L6

### Risk engine

- Daily loss limit 2%: after it, the system stops recommending until the next day.
- Maximum 3 concurrent positions, pairwise correlation of open positions below 0.6.
- PDT enforcement is a config flag `broker_pdt_active` (default `true`). While `true` and equity is below $25,000, the 3-in-5 day-trade counter is part of the system, not of memory. The owner sets it to `false` only after confirming that IBKR has transitioned the account. Every recommendation records the flag in its `pdt` risk check (ADR 0001 §7).
- Event blackouts from L4; block any stock halted in the last 48 hours.
- Every block is logged with a reason; a blocked recommendation is kept in the journal to measure what the risk layer cost.

L7

### Independent evaluator

- Separate process, separate repo subtree, no imports from training code. It receives only signed recommendations and data.
- Fill simulation: slippage by spread and volume, 3 seconds of latency, IBKR commissions, a trade is rejected when liquidity is insufficient.
- Reports: profit factor, intraday Sharpe, max drawdown, expectancy in R, dispersion across walk-forward windows, and deflated Sharpe to correct for multiple trials.
- The only component authorized to say GO.

L8

### Learning loop

- Journal: every recommendation, whether it was executed, actual fill price, outcome. The gap between recommended and actual fill is the most important calibration feature.
- Weekly retraining on a rolling 18-month window. Drift detection on feature distributions and calibration; a breach sends the model back to shadow.
- A new model goes live only after beating the current one in L7 and two weeks of parallel shadow.

L9

### Delivery

A Telegram card (same infrastructure as the existing stock-research bot) with three buttons: Executed / Executed at a different price / Skipped. The tap feeds L8. Execution in IBKR is manual only.

## 4. Build order

Every phase closes with a gate. A gate that fails means stop and fix, never move on.

P0

### Repo audit and data contracts

Run the two-stage audit prompt on the existing repo. Document what from ZBA is reusable (ingestion, replay) and what is discarded. Define Parquet schemas, feature versioning, and the signed recommendation format.

Gate: signed ADR + holdout boundaries locked in code

P1

### L0 + replay

Ingest 1-min bars for the last 3 years (outside the holdout) for a universe of 500 liquid stocks. Deterministic replay that reproduces a trading day bit for bit.

Gate: the same day run twice yields an identical hash

P2

### L1 + L2 rule-based screener

Feature store and regime. Screener per the table. Measurement: do screener candidates move more than a random sample on the same day.

Gate: median range of candidates ≥ 1.5× random

P3

### Labeling + baseline

Triple-barrier, purged CV, LightGBM on L1 features only. L7 is built here, before any network, and measures the baseline.

Gate: profit factor > 1.2 after costs in 3 walk-forward windowsNO-GO: if the baseline fails, the problem is data/labeling, not the model

P4

### L3 network + screener ranker

Sequence encoder and the three heads. The ranker learns the screener weights.

Gate: statistically significant win over LightGBM (deflated Sharpe), not just on average

P5

### L4 news and geopolitics

News pipeline as an additional feature for L3. Measured as marginal lift only.

Gate: significant expectancy improvement, otherwise only the kill-switch stays

P6

### L5 + L6

Bracket builder, sizing, and risk engine. Unit tests for every block condition.

Gate: 100% of risk scenarios in the table are blocked in tests

P7

### Live shadow

6–8 weeks of real-time recommendations with no execution. L7 compares against simulated fills. At the end, and only then, one run on the holdout for final confirmation.

Gate: live tracks backtest within ±25% on expectancy; holdout not opened before this

P8

### Production at small size + L8

Manual execution at ¼ of computed size for one month. Learning loop active. Size increases only after 60 journaled trades above expectancy.

Gate: expectancy > 0.2R over 60 trades

## 5. GO / NO-GO criteria (L7)

| Metric | Threshold | Where |
| --- | --- | --- |
| Profit factor after costs | > 1.3 | Each walk-forward window separately |
| Intraday Sharpe (annualized) | > 1.5 | Deflated by number of trials |
| Max drawdown | \< 10% | On simulated equity |
| Expectancy | > 0.2 R | After slippage |
| Stability | 3 / 3 | Positive windows, none carrying the average alone |
| Holdout | 0 opens | Until P7 |

## 6. Tech stack (free)

**lang**Python 3.12

**data**Polars + DuckDB + Parquet

**ml**PyTorch, LightGBM, scikit-learn

**news**feedparser, GDELT, sentence-transformers (local)

**broker**ib_insync (read-only)

**api**FastAPI + SQLite for the journal

**delivery**python-telegram-bot

**infra**local machine / free GitHub Actions

## 7. What this document does not promise

- Intraday edge in liquid stocks at the seconds scale belongs to HFT and players with microsecond latency. This system hunts edge on a 15–90 minute horizon, not in seconds.
- Geopolitics is a noisy feature. It enters only if L7 proves it adds value; otherwise it remains a protection mechanism.
- "Autonomous" here means: learns, screens, and times on its own. Not: executes on its own.
