# Clean Hebrew in every bot message — design

Date: 2026-10-09 · Status: requested by the owner ("so fix them too, In addition, strip away irrelevant information
that merely clutters the text") after the shadow-message rewrite (PR #8) · Branch: `clean-messages`

## 1. Rules (the shadow template's, applied to every message)

1. Every line starts with an emoji or a Hebrew word (`common.rtl_bad_lines` is empty).
2. No `+`/`-` next to a number: a move is a word (`עלייה של 3.0%`, `ירידה של`, `יותר ב־2.0% מ־SPY`) or an arrow
   inside the code span (`3.0%▼`). A loss is "הפסד", never a minus.
3. Dollar amounts in words: `130 אלף דולר`, `1.1 מיליון דולר` (`common.amount`); prices stay `30.42$`.
4. Israeli dates (`24.9`, `24.9.2026`, with the weekday in headers) and Israel time — never ISO dates or UTC.
5. No internal names: no SIC codes, XBRL tags, form numbers, accession numbers, workflow event names, latencies,
   score breakdowns, method code names.
6. The footer names the real source: SEC messages keep the SEC footer (the freshness line only lists sources the
   run used, and is gone when there are none); squeeze and movers messages say FINRA, SEC, Yahoo, Nasdaq and CBOE
   (`common.send(..., source=...)`).

`tests/msgrules.py` checks rules 1, 2, 4 and the R unit on every rendered message in the tests.

## 2. What was cut as clutter

| Message | Removed | Why |
|---|---|---|
| Insider alert | quality breakdown (רוחב/בכירות/…), the 30-day totals line (repeats the reason), "other Form 4 actions", the per-row index link, the "normal / unknown" market line | the score, the reason and one link per buyer say it; the market line only appears when it moved the score (panic / euphoria) |
| Ticker report | SIC code, the 2–3% rule of thumb (the same for every stock), fiscal-year dates, the 9 Piotroski lines (only the failed ones stay), Altman inputs and market value, Beneish indices, XBRL tag lists, derived-input notes | one plain verdict per score; the numbers stay in the analysis |
| Risk factors | sentence counts, edited/boilerplate counts, the research name; at most 3 quoted changes (was 5) | one summary sentence and the changes that matter |
| Squeeze list | the criteria sentence, coverage and missing-data counts, the shortability note, float on every row, the manual-run marker | the criteria are in `/squeeze TICKER` |
| Movers | the Nasdaq failure count | |
| Status / health | latencies, run event names, the list of every waiting day, file paths | |
| Journal | the alert id, mean next to median, the by-regime breakdown | |

## 3. Unchanged

Logic, thresholds, numbers, state and journal files. The shadow message (PR #8) and the check prompt.

## 4. Tests

Existing message tests now assert the new wording; behaviour once checked through removed text (TTM basis, Altman
inputs, notes, counts) is asserted on the data instead. Every rendered message in the tests passes
`msgrules.check`.
