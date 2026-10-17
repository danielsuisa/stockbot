# Intraday strategies of the most consistent US day traders — what the screener takes from them (2026-10-17, v2)

Scope: strategies with the highest *consistency* (Sharpe, positive expectancy over thousands of trades), not the
biggest single wins. v2 is the wide pass: three sweeps (academic/empirical, practitioner records, market structure
and screener mechanics), 79 sources, of which 61 are primary (peer-reviewed journals, SSRN/SFI working papers,
SEC/FINRA/Nasdaq/NYSE/Federal Reserve documents, official broker and screener-vendor documentation, contest-verified
records, the traders' own books). Reliability class is marked on every source: **PR** peer-reviewed · **WP** working
paper · **REG** regulator/exchange · **OFF** official broker/vendor doc · **A** contest/brokerage-verified record ·
**B** the trader's own book or training material · **C** reputable interview/secondary · **S** self-reported · **L**
legal record.

## 1. What the evidence says

**Relative volume is the edge; everything else is timing.** The only strategy with verifiable, large-sample numbers
is the opening-range breakout on "stocks in play" [2]: ~7,000 stocks incl. delisted, 2016–2023, net of $0.0035/share.
Without the relative-volume filter Sharpe 0.48; with it (first-5-minute volume ≥ 100% of its 14-day mean, top 20 by
that ratio) Sharpe 2.81, IRR 41.6%, hit ratio 48.4%, max drawdown 12%. The high-volume return premium [17] and the
intraday-momentum literature [13, 14] (effects concentrated on high-volume, high-volatility days) back the same filter
independently. Practitioners agree without the numbers: Cameron requires 3–5x [26, 28], SMB "unusual volume" [31],
Kullamägi ~10x on an episodic pivot [38].

**Time of day.** Volume and variance are U-shaped [12]; the first 30 minutes carry 11–12.6% of the day's volume
[55] and the closing auction another ~12% [56]. Cameron's own 2016 log shows profit and trade count decaying through
the day, best 09:30–10:00 [27]. Late-day continuation is predicted better by the cumulative trend from the prior close
to 15:30 than by the opening bar [14 beats 13 out of sample]. Profits of overnight anomalies reverse intraday and vice
versa [16], so flat at the close.

**Opening-range breakout.** 5-minute range beats 15/30/60 in [2] (Sharpe 2.81 vs 1.43/0.21/0.40, unexplained by the
authors). Best stop in [1, 2]: a fixed small fraction of ATR14 (5–10%), hold to the close, 1% of equity at risk, 4x
leverage cap. Independent replications keep the gross edge but find the net edge fragile to slippage on the stop
distance [4 and community replications]; one falsification study on MNQ futures rejects single-bar ORB after friction
[4]. So: liquid names only, and never a stop smaller than the spread-plus-slippage budget.

**Candlesticks.** No peer-reviewed study finds stand-alone value for hammer / engulfing / doji entries on US large caps,
daily [5, 9] or 5-minute [8]. The positive results need a specific exit rule [6, 7] or are non-US mid/small caps
[10, 11]; Bulkowski's daily-bar statistics (bullish engulfing 63%, hammer 60%) are near the coin-flip he himself warns
about [24]. Candles are entry-timing triggers inside a filtered setup, never the edge.

**VWAP.** Institutional benchmark since [18]; a *trailing stop* at max(VWAP, noise band) lifted the SPY intraday
momentum Sharpe from 0.61 to 1.24 [3]; as a *mean-reversion entry* there is no academic evidence, only vendor tests
[19]. Practitioners use it as the line (Aziz: stop at VWAP [29]).

**Practitioner records.** Contest/brokerage-verified: Schwartz 1984 [44], Minervini 1997/2021 [40, 41], Kell 2020
+941% [40], Lai/J Law 2024 [42], Soreide 2019 [43]. Tax-return-backed: Zanger $18M 1998–2000 [45]. Self-commissioned
audit: Cameron ~$20M since 2017 [26], whose company paid a $3M FTC settlement in 2022 over earnings claims [25].
Self-reported: Kullamägi (win rate 25–30%, winners ~3R+) [38, 39], Grittani [46]. The only published intraday
win-rate-with-R datapoint is one Bear Bull trader, 78–84% / 0.24–0.63R over three months [29]. Risk rules converge:
≤ 1% a trade, minimum 2:1, half off at 1–2R and stop to breakeven [30, 37, 41, 48], daily kill switch at ~2% of equity
[64].

**Base rate.** 85–97% of day traders lose net [19–23]; commissions and aggressive orders turn gross edges into net
losses [21, 23]; of Brazilian futures day traders persisting >300 days, 97% lost [22].

**Market structure that shapes the screener.** Quoted spreads in micro caps are 6–113 cents, ~1% effective, vs
0.05–0.2% in $2–5B names [57]; small caps quote wider than 5 cents 46% of the time [58]. LULD bands are 20–40%
below $3 and doubled 09:30–09:45 [52, 53]. Rule 201 blocks shorts after a 10% drop for two sessions [51] — long only.
The PDT $25k rule was retired on June 4, 2026 (brokers may phase in until Oct 20, 2027) [47–50]; Alpaca gives 4x
intraday from $2k. Alpaca's free plan: IEX live, SIP only 15 minutes back, 200 calls/min [60]; brackets not in
extended hours [61]; IBKR stops are simulated server-side, regular hours by default [62, 63].

## 2. What the screener implements (bot/intraday.py), v2

| Parameter | Value | Sources |
|---|---|---|
| Side | long only | [51] Rule 201; [10] only bullish reversals test profitable |
| Price | $5–$500 | [2] open > $5; [57, 58] spread bins; [52] LULD below $3 |
| Average volume (20 sessions) | ≥ 1M shares and ≥ $20M | [2] 1M; [27] names under 500K lost; [57] |
| ATR14 | ≥ 2% of the close and ≥ $0.50 | [2] $0.50; 2% so a 2:1 target fits a session |
| In play (09:38) | gap ≥ 2% either way, or pre-market volume ≥ 10% of a day | [26–29, 31] gapper lists; [17] |
| In play (09:55+) | opening 5-minute relative volume ≥ 1.0 vs its 14-session mean, top 20 kept | [2] exact; [16] Trade-Ideas same-slot method |
| Score | relative volume only (5x = full score); the gap earns nothing | [2]; gaps fade on average [16, Vainio]; gap rules failed [4] |
| Setups | ORB 5 / ORB 15 (first close above the range, candle volume ≥ 1.5x, above VWAP); VWAP pullback with hammer / engulfing; bull flag (micro-pullback keeping > 50% of the impulse); red-to-green | [2]; [26, 28, 29]; [34] Brooks; candles as triggers only [5–11] |
| Afternoon window (15:00–15:45) | only names above the open and above VWAP at scan time | [14] cumulative day trend; [3] VWAP as the line |
| Entry | buy stop 1 tick above the trigger candle, limit = entry + 5% ATR; no market orders | [34]; [21] aggressive orders lose; [62] |
| Stop | ORB 5: 10% of ATR14 under the entry; others: under the trigger candle, floored at max(10% ATR, 0.3% of price), capped at 1 ATR | [1, 2]; [57, 58] spread budget; [25] stops pay only after a confirmed breakout |
| Target | 2 x risk; after +1R stop to max(entry, VWAP); time exit 15:55 NY | [29, 30, 34, 41, 48] 2:1; [3] VWAP trail; [14, 16] flat at close |
| Size | equity x TICKET_RISK_PCT (as /ticket); ≤ 20% of equity; ≤ 1% of 85% of the average day's volume | [1, 2] 1%; [65, 66] R-multiples, ¼-Kelly; [55, 56] close auction not available by 15:55 |
| Windows | 09:40–11:30 and 15:00–15:45 NY, every 5 minutes on completed candles | [12, 27, 55] |
| Data | IEX for candles (live), SIP for relative volume (15-minute lag respected) | [60] |

Not implemented, deliberately: a daily kill switch (−2% of equity → cancel all, flatten [64]) and LULD-band
distance [52] need live broker state the bot does not have; both are for the IBKR side. Expected, if the studies
hold: hit rate 45–50% with positive expectancy from 2:1 — not 70%. Not backtested in this repo yet (the ZBA holdout,
docs/backtest/ticket-zba-v2-holdout-2026-10-10.md, was NO-GO on a universe dominated by cheap names); the paper
journal (/intraday stats) is the measurement.

## 3. Sources

### A. Academic / empirical
1. Zarattini & Aziz (2023), "Can Day Trading Really Be Profitable?", SSRN 4416622 — WP. https://papers.ssrn.com/abstract=4416622
2. Zarattini, Barbon & Aziz (2024), "A Profitable Day Trading Strategy for the U.S. Equity Market", SSRN 4729284 / SFI 24-98 — WP. https://www.alexandria.unisg.ch/server/api/core/bitstreams/3c2989c4-688d-4d78-8a71-f02690990d51/content ; https://ideas.repec.org/p/chf/rpseri/rp2498.html
3. Zarattini, Aziz & Barbon (2024), "Beat the Market: An Effective Intraday Momentum Strategy for S&P500 ETF (SPY)", SSRN 4824172 — WP. https://alexandria.unisg.ch/server/api/core/bitstreams/a99aba00-f967-49b3-aceb-f544dc386e0b/content
4. Mesfin (2026), "Structural Limits of OHLCV-Based Intraday Momentum Signals in MNQ Futures", arXiv 2605.04004 — WP. https://arxiv.org/pdf/2605.04004
5. Marshall, Young & Rose (2006), "Candlestick technical trading strategies: Can they create value for investors?", J. Banking & Finance 30(8) — PR. https://ideas.repec.org/a/eee/jbfina/v30y2006i8p2303-2323.html
6. Caginalp & Laurent (1998), "The predictive power of price patterns", Applied Mathematical Finance 5 — PR. https://ideas.repec.org/a/taf/apmtfi/v5y1998i3-4p181-205.html
7. Lu, Chen & Hsu (2015), "Trend definition or holding strategy: What determines the profitability of candlestick charting?", J. Banking & Finance 61 — PR. https://ideas.repec.org/a/eee/jbfina/v61y2015icp172-183.html
8. Fock, Klein & Zwergel (2005), "Performance of candlestick analysis on intraday futures data", J. Derivatives 13(1) — PR. https://opus.bibliothek.uni-augsburg.de/opus4/frontdoor/index/index/docId/35176
9. Horton (2009), "Stars, crows, and doji: The use of candlesticks in stock selection", QREF 49(2) — PR. https://ideas.repec.org/a/eee/quaeco/v49y2009i2p283-294.html
10. Lu, Shiu & Liu (2012), "Profitable candlestick trading strategies", Review of Financial Economics 21(2) — PR. https://ideas.repec.org/a/wly/revfec/v21y2012i2p63-68.html
11. Chen, Bao & Zhou (2016), "The predictive power of Japanese candlestick charting in Chinese stock market", Physica A 457 — PR; companion Zhu, Atri & Yegen (2016), Pacific-Basin Finance J. 37 — PR. https://ideas.repec.org/a/eee/phsmap/v457y2016icp148-165.html
12. Wood, McInish & Ord (1985), J. Finance 40(3); McInish & Wood (1990), JBF 14(1) — PR; Admati & Pfleiderer (1988), RFS 1(1) — PR. https://digitalcommons.memphis.edu/facpubs/11503 ; https://ideas.repec.org/a/oup/rfinst/v2y1989i2p189-223.html
13. Gao, Han, Li & Zhou (2018), "Market intraday momentum", JFE 129(2) — PR; replications Limkriangkrai, Chai & Zheng (2023), PBFJ 80 — PR; Li, Sakkas & Urquhart (2022), J. Financial Markets 57 — PR. https://profiles.wustl.edu/en/publications/market-intraday-momentum/ ; https://researchmgt.monash.edu/ws/files/519509174/494419119_oa.pdf ; https://reading-9.eprints-hosting.org/95566/1/Accepted-Version.pdf
14. Baltussen, Da, Lammers & Martens (2021), "Hedging demand and market intraday momentum", JFE 142(1) — PR. https://academicweb.nd.edu/~zda/intramom.pdf
15. Heston, Korajczyk & Sadka (2010), "Intraday patterns in the cross-section of stock returns", J. Finance 65(4) — PR. https://www.bauer.uh.edu/departments/finance/documents/Heston_Korajczyk_Sadka_paper_UH.pdf
16. Lou, Polk & Skouras (2019), "A tug of war: Overnight versus intraday expected returns", JFE 134(1) — PR; Bogousslavsky (2021), JFE 141(1) — PR; Vainio (2026), Univ. of Turku MSc thesis — thesis. https://personal.lse.ac.uk/polk/research/TugOfWar.pdf ; https://ideas.repec.org/a/eee/jfinec/v141y2021i1p172-194.html ; https://www.utupub.fi/bitstreams/88057d2a-1ee8-4c05-9592-c535a21b502c/download
17. Gervais, Kaniel & Mingelgrin (2001), "The high-volume return premium", J. Finance 56(3) — PR. https://sites.duke.edu/sgervais/?p=64
18. Berkowitz, Logue & Noser (1988), "The total cost of transactions on the NYSE", J. Finance 43(1) — PR; Madhavan (2002), "VWAP strategies". https://www.smallake.kr/wp-content/uploads/2014/07/TP_Spring_2002_Madhavan.pdf
19. Barber, Lee, Liu & Odean (2014), "The cross-section of speculator skill: Evidence from day trading", J. Financial Markets 18 — PR. https://faculty.haas.berkeley.edu/odean/papers/day%20traders/Day%20Trading%20Skill%20110523.pdf
20. Barber, Lee, Liu, Odean & Zhang (2020), "Do Day Traders Rationally Learn About Their Ability?", SSRN 2535636 — WP. https://faculty.haas.berkeley.edu/odean/papers/day%20traders/day%20trading%20and%20learning%20110217.pdf
21. Barber, Lee, Liu & Odean (2009), "Just How Much Do Individual Investors Lose by Trading?", RFS 22(2) — PR. https://ideas.repec.org/a/oup/rfinst/v22y2009i2p609-632.html
22. Chague, De-Losso & Giovannetti (2020), "Day Trading for a Living?", SSRN 3423101 — WP. https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3423101
23. Jordan & Diltz (2003), "The Profitability of Day Traders", Financial Analysts Journal 59(6) — PR. https://ideas.repec.org/a/taf/ufajxx/v59y2003i6p85-94.html
24. Bulkowski candlestick statistics (daily bars): Fidelity "Eight best candles" summary; ThePatternSite — C. https://fidelity.com/bin-public/060_www_fidelity_com/documents/EightBestCandles.pdf ; https://thepatternsite.com/Hammer.html ; https://ThePatternSite.com/BullEngulfing.html
25. Kaminski & Lo (2014), "When do stop-loss rules stop losses?", J. Financial Markets 18 — PR. https://dspace.mit.edu/bitstream/handle/1721.1/114876/Lo_When%20Do%20Stop-Loss.pdf

### B. Practitioners
26. Warrior Trading results page (SingerLewak audit, self-commissioned) — S. https://www.warriortrading.com/results/ ; FTC settlement 2022 — L. https://www.ftc.gov/node/78867
27. Cameron, 2016 year in review (accuracy 66%, P/L 1.27, best 09:30–10:00) — S. https://medium.com/@WarriorTrading/2016-year-in-review-trading-statistics-222-244-91-dc6f98e17086 ; 2023 results — S. https://www.warriortrading.com/analyzing-my-day-trading-results-for-2023/
28. Warrior Trading strategy page and 5-pillar / micro-pullback scripts — S. https://www.warriortrading.com/day-trading/ ; https://kr.tradingview.com/script/mbqMf3pF-Ross-Cameron-5-Pillars-Filter ; https://www.tradingview.com/script/K0f6aI3t-Micro-Pullback-Entry-System/
29. Aziz, "How to Day Trade for a Living" figures; "Advanced Techniques"; BBT 5-min ORB deck — B. https://bearbulltraders.com/docs/AndrewAziz-How_to_Day_Trade_for_a_Living_AUDIOBOOK-FIGS.pdf ; https://bearbulltraders.com/wp-content/uploads/pdfs/Advanced-Techniques-in-Day-Trading.pdf ; https://bearbulltraders.com/wp-content/uploads/2023/04/5-Min-ORB-Strategy-ORB.pdf
30. Bellafiore, "One Good Trade" (Wiley 2010) — B; summary https://traderlion.com/trading-books/one-good-trade-summary-review/
31. SMB Capital, "Super Stocks In Play" — B. https://www.smbtraining.com/blog/?p=30132
32. SMB PlayBook template — B. https://www.smbtraining.com/blog/the-smb-playbook-compiling-our-best-trades
33. Lance Breitstein, Chat With Traders 236; SMB show notes — C. https://chatwithtraders.com/236 ; https://www.smbtraining.com/blog/wall-streets-top-trader-invites-me-to-his-penthouse-show-notes
34. Al Brooks, MoneyShow interview 2012; trader's equation / 40-60 rule; "Trading Price Action" trilogy (Wiley) — C/B. https://moneyshow.com/articles/videotranstr-26221 ; https://www.brookstradingcourse.com/support-forum/30-traders-equation-and-probability/40-60-probability/ ; https://www.topstep.com/blog/the-traders-equation-should-i-take-this-trade
35. Raschke & Connors, "Street Smarts" (1996); Raschke, "Trading Sardines" (2019); "New Market Wizards" — B/C. https://lindaraschke.net/bio-linda/ ; https://tradingsetupsreview.com/the-holy-grail-trading-setup
36. Minervini, "Trade Like a Stock Market Wizard" (2013); risk management — B/C. https://traderlion.com/investing-champions/mark-minervinis-risk-management/ ; https://www.finermarketpoints.com/post/3-key-lessons-from-trade-like-a-stock-market-wizard
37. Steve Burns, Chat With Traders 005; "New Trader, Rich Trader" — C/B. https://chatwithtraders.com/episode/005-steve-burns-what-separates-new-traders-from-rich-traders-and-how-to-cross-the-divide
38. Kullamägi, "Market Wizards: The Next Generation" (Schwager & Coyle 2026) ch. 1; Chat With Traders 212 — C/S. https://chatwithtraders.com/212 ; https://completetradersedge.com/wp-content/uploads/2026/09/CTE-Research-Sheet-Kristjan-Kullamagi-market-wizards-next-generation.pdf
39. Kullamägi stream notes and screener code — secondary. https://retailtradersrepository.substack.com/p/kristjan-kullamagi-qullamaggie-stream-092 ; https://tikamalma.substack.com/p/qullamaggie-swing-trading-setups
40. US Investing Championship 2020 final standings (Kell +941%) — A. https://www.businesswire.com/news/home/20210125005140/en/U.S.-Investing-Championship-2020-Final-Standings
41. USIC 2021 (Minervini +334.8%, Mattox) — A. https://www.businesswire.com/news/home/20211026005502/en
42. USIC 2024 final results — A. https://www.businesswire.com/news/home/20250127956815/en/2024-United-States-Investing-Championship-Final-Results
43. USIC compilation incl. Soreide 2019, Luk 2025 — C. https://completetradersedge.com/wp-content/uploads/2026/09/CTE-Research-Sheet-US-Investing-Championship-2.pdf ; Soreide interview https://www.benzinga.com/news/earnings/22/05/27209037/
44. Marty Schwartz, "Market Wizards" (1989), "Pit Bull" (1998) — B/C. https://www.turtletrader.com/trader-schwartz
45. Dan Zanger, TASC interview 2003 (Fortune tax-return figures); CMC Opto — C. https://www.traders.com/Documentation/FEEDbk_docs/2003/08/Abstracts_new/Interview/interview.html ; https://www.cmcmarkets.com/en-gb/opto/world-record-breaking-trader-dan-zangers-tricks-of-the-trade
46. Tim Grittani, CNN Money 2013 — C/S. https://money.cnn.com/2013/12/16/investing/penny-stock-trader-millionaire/ ; Nathan Michaud, Chat With Traders 055 — C. https://chatwithtraders.com/episode/055-nathan-michaud-trading-momo-stocks-and-scaling-up-for-larger-gains-w-investors-underground ; Mark Douglas, "Trading in the Zone" (2000) — B. https://bookmap.com/blog/5-truths-of-trading/ ; Oliver Kell, "Victory in Stock Trading"; wedge pop — C. https://traderlion.com/technical-analysis/chart-patterns/wedge-pop-the-money-pattern/

### C. Market structure, risk rules, screener mechanics
47. SEC Release 34-105226 approving SR-FINRA-2025-017 (PDT rule retired; intraday margin regime) — REG. https://public-inspection.federalregister.gov/2026-07485.pdf
48. FINRA Regulatory Notice 26-10 (effective June 4, 2026; phase-in to Oct 20, 2027) — REG. https://www.finra.org/sites/default/files/2026-04/Regulatory-Notice-26-10.pdf ; FINRA Notice 24-13 — REG. https://finra.org/sites/default/files/2024-10/Regulatory-Notice-24-13.pdf
49. SEC Investor Bulletin "Margin Rules for Day Trading" (legacy 4x rule) — REG. https://www.sec.gov/files/daytrading.pdf
50. Alpaca, "FINRA Retires the PDT Rule" (4x intraday from $2,000) — OFF. https://alpaca.markets/blog/finra-retires-the-pdt-rule-introducing-alpacas-new-intraday-margin-framework/ ; Federal Reserve Regulation T, 12 CFR 220 — REG. https://www.ecfr.gov/current/title-12/chapter-II/subchapter-A/part-220
51. SEC Rule 201 (short-sale circuit breaker) press release and staff FAQ — REG. https://www.sec.gov/news/press/2010/2010-26.htm ; https://www.sec.gov/divisions/marketreg/rule201faq.htm
52. Nasdaq LULD FAQ (bands by tier and price) — REG. https://m.nasdaqtrader.com/content/MarketRegulation/LULD_FAQ.pdf
53. SEC Release 34-69002 (LULD bands doubled 09:30–09:45, 15:35–16:00) — REG. https://www.sec.gov/files/rules/sro/edga/2013/34-69002.pdf
54. Sidley, SEC proposal to rescind Rule 611; Rule 612 compliance extended to Nov 2027 — C. https://www.sidley.com/zh-hans/insights/newsupdates/2026/06/sec-proposes-rescission-of-the-order-protection-rule ; SEC Press Release 2024-137 (half-penny ticks) — REG. https://www.sec.gov/newsroom/press-releases/2024-137
55. NYSE Data Insights, "Opening Auction: Filling Behaviors" (09:30–10:00 = 11–12.6% of volume) — REG. https://nyse.com/data-insights/opening-auction-filling-behaviors-opportunities-early-in-the-day
56. BMLL, closing auction share 10.9–12.3% of S&P 500 notional — C. https://www.bmlltech.com/news/market-insight/plotting-at-the-triple-witching-hour-how-the-closing-auction-has-changed
57. Collver (2014), SEC Division of Trading & Markets, "A characterization of market quality for small capitalization US equities" — REG. https://www.sec.gov/marketstructure/research/small_cap_liquidity.pdf
58. Hu et al. (2019), SEC DERA, "Tick Size Pilot Plan Threshold Analysis" — REG. https://sec.gov/files/dera_wp_ticksize-thresholdanalysis.pdf
59. Finviz screener help (relative volume, gap definitions); Trade-Ideas "Relative Volume" filter (same-time-of-day, 30-day); thinkorswim RelativeVolumeStDev — OFF. https://finviz.com/help/screener.ashx ; https://www.trade-ideas.com/help/filter.html?code=RV ; https://toslc.thinkorswim.com/center/reference/Tech-Indicators/studies-library/R-S/RelativeVolumeStDev
60. Alpaca market-data plans (IEX live; SIP ≥ 15 minutes old; 200 calls/min) — OFF. https://docs.alpaca.markets/us/docs/about-market-data-api ; https://docs.alpaca.markets/docs/market-data-faq
61. Alpaca "Orders at Alpaca" (bracket/OCO/OTO, trailing, no brackets in extended hours) — OFF. https://docs.alpaca.markets/us/docs/orders-at-alpaca
62. IBKR Web API "Bracket Orders & OCA Groups"; "Trailing Stop Limit" — OFF. https://www.interactivebrokers.com/docs/web-api/v1/endpoints/orders/bracket-orders-oca-groups ; https://www.interactivebrokers.com/docs/general/order-types/trailing-stop-limit
63. IBKR FINRA Rule 5350 stop-order disclosure; "Stop Orders"; TWS API trigger methods — OFF. https://interactivebrokers.com.au/en/trading/orders/stop.php ; https://www.interactivebrokers.com/docs/tws-api/doc/orders/trigger-methods
64. Topstep "Daily Loss Limit" (2% of nominal, auto-flatten) — OFF. https://intercom.help/topstep-llc/en/articles/8284207 ; SMB on daily max loss https://www.smbtraining.com/blog/a-lesson-on-trading-losses
65. Van Tharp, R-multiples and percent-risk sizing; "Trade Your Way to Financial Freedom" (2nd ed. 2007) — B. https://vantharp.com/Weekly_update/Weekly_273_May_31_2006.htm
66. MacLean, Thorp & Ziemba (2010), "Good and bad properties of the Kelly criterion" — PR. https://www.stat.berkeley.edu/~aldous/157/Papers/Good_Bad_Kelly.pdf ; Thorp (1997) https://sites.oxy.edu/lengyel/M330/thorp/paper.htm
67. SEC Release 33-8400 (Form 8-K within four business days; EDGAR cutoffs) — REG. https://www.sec.gov/files/rules/final/33-8400.htm ; https://www.sec.gov/page/edgar-how-do-i-determine-status-my-filing
68. Alpaca real-time and historical news (Benzinga) — OFF. https://docs.alpaca.markets/us/docs/streaming-real-time-news ; https://docs.alpaca.markets/us/docs/historical-news-data
69. TrendSpider VWAP tests (vendor, images only) — C. https://trendspider.com/blog/daytrading-with-the-vwap-indicator/ ; daytradingz gap-and-go — C. https://daytradingz.com/gap-and-go-strategy/ ; CXO Advisory reviews of [1, 2] — C. https://www.cxoadvisory.com/technical-trading/day-trading-with-an-opening-range-breakout-strategy ; https://www.cxoadvisory.com/individual-investing/intraday-trading-of-overactive-stocks-via-opening-range-breakout/ ; Concretum summary of [2] https://concretumgroup.com/a-profitable-day-trading-strategy-for-the-u-s-equity-market/

Count: 69 numbered entries, 79 distinct documents (several entries group a paper with its replication or
companion); 61 primary (PR, WP, REG, OFF, A, B), 18 secondary or self-reported (C, S, L).
