# ADR 0005: P3 pre-registered design (labels, LightGBM baseline, L7 evaluator)

- **Status:** Accepted. The items specified by the P3 prompt were accepted at the start (user, 2026-10-18). The six
  items this ADR marked **Proposed** were accepted afterwards; see the decision record at the end.
- **Commit order:** this ADR is committed before any label, feature, model or evaluator code runs. Nothing below may
  change after training starts.
- **Context:** [ADR 0004](0004-p2-failure-plan-change.md) (plan change, $500 cash account, long only) and
  [ADR 0003](0003-features-regime-screener.md) (P2 features).

## 1. Decision points

- **When:** every 5 minutes from 09:45 to 11:30 ET, 22 per session.
  - 09:45 is the first time a delayed NBBO sample (09:30 + 15 min) exists.
- **Universe:** that month's `top500_v3` members.
- **Tradability filters,** all live-eligible, all at decision time *t* (ADR 0004 §2):

| Filter | Rule |
|---|---|
| Price | 5 ≤ px ≤ 200. px = the IEX `last_price`, else `prev_close` |
| Volume | `adv20` > 2,000,000 |
| Spread | `spread_nbbo_delayed` < 0.0010. A null fails |
| Dilution | `dilution_filing@2.0.0` = false. `unknown` fails |

- **Span:** every session from 2023-09-01 to the last ingested session, 2026-10-16. Nothing is read inside a sealed
  window.

## 2. Labels (long only, SIP bars)

**Volatility scale:**
- σ₁ = the standard deviation (ddof 1) of 1-minute log close-to-close returns of SIP regular-session bars (09:30 ≤ open
  < close), between consecutive bars, over the **5 sessions before *d***.
- σ₁ needs ≥ 500 returns; otherwise the row has no label and is dropped.
- σ_H = σ₁·√H. It is point-in-time: `sip_eod`.

**Entry and barriers:**
- **Entry reference:** the open of the first SIP bar that opens after *t* + 3 s, that is, the bar opening at *t* + 1 min.
  If no such bar opens before the regular close, there is no label.
- **Barriers:** stop = entry·(1 − 1.0·σ_H); target = entry·(1 + 1.5·σ_H).
- **Path:** the SIP bars opening in [entry bar, entry bar + H min), capped at open < the regular close.

**Label and outcome:**
- **Label** = 1 if a bar's high ≥ target before any bar's low ≤ stop; otherwise 0. If both happen in the same bar, the
  stop counts as first.
- **Realized gross R** (for the engine's own metric):
  - target first: +1.5;
  - stop first: −1.0;
  - otherwise (close of the last path bar − entry) / (entry·σ_H).
- **Primary configuration:** H = 60. **Secondary, reported only:** H = 30 and H = 90.
- **Label interval:** [*t*, entry bar + H min]. Purging uses it (§5).

## 3. Feature sets (registry `registry_p3.json`; P2 ids kept)

**`live`** contains every feature whose profile is `iex_realtime`, `sip_delayed_15m` or `sip_eod`.
- **From the P2 `iex_live` snapshot rows at *t*:**
  - `rvol_open`, `rvol_pre`, `gap_pct`, `vwap_dev_pct`, `pm_range_pct`, `minutes_since_open`;
  - `spread_ar` (IEX bars), `spread_nbbo_delayed`;
  - `atr14_pct`, `adv20`, `prev_close`;
  - `earnings_8k` (null becomes −1), `macro_day`;
  - `sector_etf` (categorical), `spy_rv_intraday` (IEX), `vix_prev_close`, `spy_ret20`, `spy_sma20_dev`;
  - `regime` (categorical).
- **New, on IEX bars available at *t* (open + 62 s):**
  - `ret_5m`, `ret_15m`, `ret_30m`: log of the last price over the last price at *t* − k;
  - `ret_open`: log of the last price over the open of the first regular-session bar;
  - `range_open_pct`: (max high − min low) of the regular-session bars ÷ last price.
- **New, `sip_eod`:**
  - `sigma_1m_5d` (σ₁ above);
  - `dist_stop_pct` = σ_60, the stop distance at H = 60.

**`research`** is `live` plus the `research_only` real-time SIP features.
- From the P2 `sip_research` rows: `rvol_open_sip`, `rvol_pre_sip`, `vwap_dev_pct_sip`, `pm_range_pct_sip`,
  `spread_ar_sip`, `spread_nbbo_rt`, `spy_rv_intraday_sip`.
- New on SIP bars: `ret_5m_sip`, `ret_15m_sip`, `ret_30m_sip`, `ret_open_sip`, `range_open_pct_sip`.

**Use:** the gate uses `live`. The live − research difference is reported as the value of a paid real-time feed.

## 4. Model

- **Classifier:** LightGBM binary, objective log-loss.
- **Fixed parameters:**
  - learning_rate 0.05;
  - bagging_fraction 0.8, bagging_freq 1;
  - lambda_l2 1.0;
  - max_bin 63;
  - seed 7, deterministic, force_col_wise, num_threads 8;
  - at most 400 rounds, at least 20.
- **Grid (8 configurations):** num_leaves {15, 63} × min_data_in_leaf {200, 2000} × feature_fraction {0.5, 0.9}.

**Engine after-cost metric** (the engine's own approximation; the L7 cost model is authoritative):
- Per row:
  - q = ⌊min(5 / (entry·σ_H), 500 / entry)⌋;
  - cost_R = (2·max(0.35, 0.0035·q) + q·entry·`spread_nbbo_delayed`) / (q·entry·σ_H);
  - net R = gross R − cost_R;
  - rows with q = 0 are excluded.
- **PF(set)** = Σ positive net R ÷ |Σ negative net R|.

**Early stopping:** on the validation fold, PF of the rows in the top 10% by predicted p. Evaluated every round,
patience 30 rounds.

**Configuration selection (live, H60):**
- Inside each training window, purged K-fold: K = 5 contiguous blocks of sessions.
  - Purged: any training row whose label interval overlaps a validation row's interval.
  - Embargo: the session after each validation block.
- The configuration with the highest mean validation PF is chosen.
- The final model is trained on the whole training window with the mean best round count of that configuration.

**Threshold (training folds only):**
- Candidates are the 0.80, 0.90, 0.95 and 0.98 quantiles of the out-of-fold p.
- The threshold maximizes out-of-fold PF among candidates with ≥ 300 out-of-fold trades. Ties go to the higher
  quantile.

**Other models (Proposed, to keep compute bounded):**
- These are `research`/H60 and `live`/`research` × H30/H90.
- Each month they reuse that month's `live`/H60 configuration and run their own 5-fold out-of-fold pass, used only for
  the threshold and the round count.

**Trials ledger:** `D:\scalp-data\derived\trials_p3.jsonl`.
- One line per (month, model, configuration, fold) fit and per final fit.
- Each line holds the parameters, the best round and the validation PF.
- The trials count reported is the number of distinct (configuration × feature set × label) combinations evaluated.

## 5. Walk-forward

- Retraining is monthly. Each test month M uses a model trained on the rolling **18 months** of decision points
  before M.
- **Purge and embargo at the boundary:**
  - Labels whose interval reaches the first test decision are purged.
  - The last session before M is embargoed: one day.
- **Test windows:**
  - **W1:** 2025-05-01..2025-10-31.
  - **W2:** 2025-11-01..2026-04-30.
  - **W3:** 2026-05-01..2026-10-16, the last ingested session.
- That is 18 test months; the earliest training month is 2023-11.

## 6. Recommendations (engine → L7, signed)

- One recommendation per test decision point with p ≥ threshold.
- **Contract:** `recommendation.schema.json`, signed per ADR 0001 §5 with Ed25519.
  - The key is `D:\scalp-env\keys\p3_signing.pem`, outside the repo.
  - The evaluator holds only the public key.

| Field | Value |
|---|---|
| side | long |
| entry | LMT, limit = IEX last price at *t* + ½ × last·`spread_nbbo_delayed`, rounded **up** to $0.01 |
| stop | limit·(1 − σ_H), rounded **down** to $0.01 |
| target | limit·(1 + 1.5·σ_H), rounded **up** to $0.01 |
| ttl_seconds | 600 |
| horizon_minutes | H |
| p | p, 6 decimals |
| qty | max(1, ⌊min(5 / (limit − stop), 500 / limit)⌋). The evaluator re-sizes (§8) |
| model_version | `p3-<set>-h<H>-<YYYY-MM>` |
| feature_versions | the registry ids used |
| regime | the P2 regime at *t* |
| risk_checks | the four tradability filters, all passed |

The recommendations are stored as JSON Lines under `D:\scalp-data\recommendations\p3\`, outside git.

## 7. L7 fill rules (`scalp_eval`, SIP bars)

- **Latency:** the order can fill from the first SIP bar that opens after *t* + 3 s (*t* + 1 min).
- **Entry window:** bars opening in [*t* + 1 min, *t* + 10 min), the TTL. Otherwise the order expires.
- **Entry fill:** the first bar whose low < limit, strictly. The fill price is the limit.
- **Liquidity:** if shares > 1% of the fill bar's volume, the order is rejected. No trade.
- **Exits:**
  - The path starts at the fill bar.
  - **Fill bar (Proposed):** only the stop is checked, because the order of prices inside that bar is unknown.
  - **Later bars:**
    - If open ≤ stop, exit at open − $0.01 (a gap).
    - Else if low ≤ stop, exit at stop − $0.01.
    - Else if high > target (strictly), exit at target.
    - If stop and target are both hit in one bar, the stop wins.
  - **Time exit:** at the close of the last bar opening before min(fill bar + H min, regular close). The exit price is
    that close − ½ spread.
- **Spread:**
  - ½ spread = ½ × (ask − bid) of the real NBBO `ok` sample at *t*, through `quote_view`.
  - **Proposed fallback:** the latest earlier `ok` sample of the session.
- **Before costs:** the same fills, with these differences:
  - entry at the IEX last price (limit − ½ delayed spread);
  - a stop exit at the stop with no tick;
  - a time exit at the close with no ½ spread;
  - no commission or fees.

## 8. L7 costs and sizing

Commissions and fees follow IBKR Pro US stocks, `https://www.interactivebrokers.com/en/pricing/commissions-stocks.php`,
read on 2026-10-18.

**Tiered (gate):**
- **Commission:** $0.0035/share (≤ 300k shares/month), minimum $0.35, maximum 1% of trade value. The maximum applies
  even below the minimum, per the schedule's note.
- **Clearing:** NSCC/DTC $0.00020/share.
- **Pass-through:** NYSE 0.000175 × commission; FINRA 0.000565 × commission.
- **Exchange fee (Proposed):** $0.0030/share on every order. That is the Reg NMS access-fee cap, a conservative
  upper bound, because the per-venue fee table was not itemized.
- **Regulatory:**
  - SEC fee 0.0000206 × sale value;
  - FINRA CAT $0.000003/share;
  - FINRA TAF $0.000166/share sold, maximum $8.30, before 2026-10-01; $0 from 2026-10-01 (fee holiday). The pre-holiday
    rate is FINRA's standing rate, Proposed.

**Fixed (reported):**
- $0.005/share, minimum $1.00, maximum 1% of trade value.
- Plus the regulatory fees above. Exchange and clearing fees are included in the rate.

**Signal level (gate):**
- Every recommendation is simulated independently with its own $500 account.
- Shares = ⌊min(1% × 500 / (limit − stop), 500 / limit)⌋.
- 0 shares → skipped and counted.

**Account level (reported):**
- One account per window, starting with $500 of settled cash.
- **Cash account:**
  - buys only with settled cash;
  - sale proceeds settle at the next session (T+1);
  - no margin, no shorts.
- Shares = ⌊min(1% × equity / (limit − stop), available settled cash / limit)⌋.
  - **Proposed:** equity = cash plus open positions at cost.
  - A pending order reserves its cash.
- At most 3 positions and pending orders together. Signals at the same *t* are ranked by p, then by symbol.
- **Daily loss limit:** no new entries after realized P&L of the session reaches −2% of start-of-session equity.
- **Capital sensitivity:** the same run at $2,000, $10,000 and $25,000.

## 9. Metrics (per window and per model; Tiered and Fixed; before and after costs)

| Metric | Definition |
|---|---|
| Trades | Filled trades |
| Skipped | 0-share recommendations |
| Rejected | Liquidity rejections |
| Expired | Not filled within the TTL |
| PF | Σ winning $ ÷ \|Σ losing $\| |
| Expectancy in R | mean(P&L ÷ (shares·(limit − stop))) |
| Win rate | Share of trades with P&L > 0 |
| Max drawdown | On the cumulative P&L ordered by exit time, in $ and as % of $500 |
| Intraday Sharpe | Mean ÷ std of daily P&L × √252 |
| PF bootstrap 95% CI | 2,000 resamples of trades, seeded with sha256(`p3-bootstrap-v1|<window>|<model>`) |
| Trials count | From the ledger |

## 10. Gate and failure classification

**Gate:** in **each** of W1, W2 and W3, the signal-level PF after costs is > 1.2, at $500 on Tiered, for the `live`/H60
model, with ≥ 100 trades. A window with fewer trades fails.

**Failure classification:**
- **(a) No gross edge:** the before-cost PF is ≤ 1.2 or the before-cost expectancy is ≤ 0 in a failing window. This is
  a data or labeling problem.
- **(b) Costs at $500:** there is a gross edge, but the after-cost gate fails. The break-even capital is reported from
  the account-level sensitivity runs.
- **(c) Too few trades.**

## 11. Run discipline

- Stage 5 (the evaluation) runs once on committed code. Its timestamp and commit are recorded.
- The engine's walk-forward run is also recorded.
- Nothing is re-run and no parameter changes after a gate result.

## Decision record

- **Date:** 2026-10-18.
- **Accepted by:** the user, via the orchestrator, at the start of P3D.
- **Outcome:** all six Proposed items of this ADR are **Accepted** as written:
  1. secondary models reuse the month's `live`/H60 configuration (§4);
  2. in the fill bar only the stop is checked (§7);
  3. the spread fallback is the latest earlier `ok` sample (§7);
  4. the $0.0030/share exchange fee (§8);
  5. the FINRA TAF rate before the 2026-10-01 fee holiday (§8);
  6. account equity counted at cost (§8).
- **P3 stays FAILED, classification (a).** Nothing from P3 is re-run or re-tuned.
