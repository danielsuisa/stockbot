# Recommendation algorithm: phase B (levels engine, costs, `/ticket`)

Spec: `docs/superpowers/specs/2026-10-10-recommendation-algorithm-design.md`, revision 7 (clarified 2026-10-11): sections
3, 5 and 8.
Plan: `docs/superpowers/plans/2026-10-11-recommendation-algorithm-phase-b.md`, with the owner's decisions 1–9.
Code: `bot/levels.py` (pure functions), `bot/ticket_command.py` (data path, message), `bot/listen.py` (`/ticket`).
Tests: `tests/test_levels.py` (32), `tests/test_ticket_command.py` (15), `tests/test_commands.py` (4 for `/ticket`).

**No signal and no outcome.** Every ticket says "אין אות תומך" (no supporting signal) and uses `breakout` with
H = 21. No outcome or return of any past ticket is computed, printed, logged or saved. The daily-bar fill model in
`levels` (`fill`, `exits`, `gross_r`, `simulate`) is tested on synthetic bars only and is never called from
`/ticket`. A test checks that no such call appears in `bot/ticket_command.py`.

## What was built

- **Levels (spec section 3):**
  - ATR = `ticket.atr14` through D, and R = 2 × ATR.
  - Breakout: stop at D's high + $0.01, limit at stop + 0.25 × ATR, valid for 3 sessions.
  - Open: limit at D's close + 0.5 × ATR.
  - Shares = floor(risk / R), split into two legs. Risk comes from `TICKET_RISK_USD` (default $100).
  - Stop loss F − R, target F + 1.5 × R, trailing amount R. In the message these are shown from the planned entry.
- **Refusals:**
  - Quote-free: fewer than 15 bars; no bar on D (added after the review); shares < 2; position above 1% of the
    20-session average dollar volume.
  - Quote-based: no usable quote; cost guard above 10% of risk.
- **Costs (section 5):**
  - Commission: $0.0035 a share, minimum $0.35, over 4 orders.
  - Quote: the latest SIP quote no older than 60 s. A locked or crossed quote counts $0.01.
  - Quote times come from the exchange calendar: 5 minutes before D's close (12:55 ET on a 13:00 close) and 5
    minutes after the entry session's open.
  - Stress case: spread × 1.5.
- **Partial universe mark (owner 2026-10-11):** raw close ≥ $5, 20-session dollar volume ≥ $10M, and ≥ 252 sessions
  before D, each failure named in the message. The insider-filing condition comes in phase C. The mark never
  refuses a ticket.
- **`/ticket SYMBOL`:**
  - D is the latest session closed at least 15 minutes ago.
  - The reply is Hebrew per section 8, including the position value (shares × planned entry) and the label
    "ללא יתרון מוכח" (no proven edge).
  - Class shares: `BRK.B` and `BRK-B` both work.
  - At most 3 symbols per message.

## Live dry run (GitHub Actions run 38088600694, Sunday 2026-10-11, D = Friday 2026-10-09)

The messages as printed (`<code>` shown as backticks). They cover a full ticket, a class share, a ticket outside
the tested universe and a refusal:

```
🎫 כרטיס ל-`AAPL` · יום האות 9.10.2026
מקור: בקשה ידנית · אותות: אין אות תומך
כניסה: קניית פריצה — סטופ `338.62$` · לימיט `340.23$` · בתוקף ב-12.10.2026, 13.10.2026, 14.10.2026
סטופ לוס (שתי המנות): `325.70$` · יעד מנה א': `358.00$` · טריילינג מנה ב': `12.92$`
המחירים מחושבים ממחיר הכניסה המתוכנן ויזוזו עם הכניסה בפועל (כניסה פחות `12.92$`, כניסה ועוד `19.38$`)
יציאה בזמן: בסגירה של יום המסחר ה-`21` אחרי הכניסה (כניסה ב-12.10.2026 ← יציאה ב-10.11.2026)
מניות: מנה א' `3` · מנה ב' `4` · שווי פוזיציה `2,370.34$` · סיכון `90.42$`
עלות משוערת: `1.8%` מהסיכון (מרווח 5 דקות לפני סגירת יום האות, ועמלות)
ב-IBKR: שתי פקודות bracket בחצי כמות — א': כניסה, יעד לימיט וסטופ · ב': כניסה ו-TRAIL — עד היציאה בזמן
תווית: ללא יתרון מוכח
⚠️ כרטיס מחושב בלבד, לא ייעוץ השקעות: הבוט לא שולח פקודות.
מקור: נרות יומיים וציטוט SIP של `Alpaca` (באיחור של `15` דקות) · מנוע הרמות של האלגוריתם

🎫 כרטיס ל-`BRK.B` · יום האות 9.10.2026
מקור: בקשה ידנית · אותות: אין אות תומך
כניסה: קניית פריצה — סטופ `516.58$` · לימיט `517.84$` · בתוקף ב-12.10.2026, 13.10.2026, 14.10.2026
סטופ לוס (שתי המנות): `506.49$` · יעד מנה א': `531.71$` · טריילינג מנה ב': `10.09$`
המחירים מחושבים ממחיר הכניסה המתוכנן ויזוזו עם הכניסה בפועל (כניסה פחות `10.09$`, כניסה ועוד `15.13$`)
יציאה בזמן: בסגירה של יום המסחר ה-`21` אחרי הכניסה (כניסה ב-12.10.2026 ← יציאה ב-10.11.2026)
מניות: מנה א' `4` · מנה ב' `5` · שווי פוזיציה `4,649.22$` · סיכון `90.81$`
עלות משוערת: `1.6%` מהסיכון (מרווח 5 דקות לפני סגירת יום האות, ועמלות)
ב-IBKR: שתי פקודות bracket בחצי כמות — א': כניסה, יעד לימיט וסטופ · ב': כניסה ו-TRAIL — עד היציאה בזמן
תווית: ללא יתרון מוכח
⚠️ כרטיס מחושב בלבד, לא ייעוץ השקעות: הבוט לא שולח פקודות.
מקור: נרות יומיים וציטוט SIP של `Alpaca` (באיחור של `15` דקות) · מנוע הרמות של האלגוריתם

🎫 כרטיס ל-`PLUG` · יום האות 9.10.2026
מקור: בקשה ידנית · אותות: אין אות תומך
כניסה: קניית פריצה — סטופ `1.76$` · לימיט `1.78$` · בתוקף ב-12.10.2026, 13.10.2026, 14.10.2026
סטופ לוס (שתי המנות): `1.58$` · יעד מנה א': `2.03$` · טריילינג מנה ב': `0.18$`
המחירים מחושבים ממחיר הכניסה המתוכנן ויזוזו עם הכניסה בפועל (כניסה פחות `0.18$`, כניסה ועוד `0.27$`)
יציאה בזמן: בסגירה של יום המסחר ה-`21` אחרי הכניסה (כניסה ב-12.10.2026 ← יציאה ב-10.11.2026)
מניות: מנה א' `282` · מנה ב' `282` · שווי פוזיציה `992.64$` · סיכון `99.91$`
עלות משוערת: `9.6%` מהסיכון (מרווח 5 דקות לפני סגירת יום האות, ועמלות)
ב-IBKR: שתי פקודות bracket בחצי כמות — א': כניסה, יעד לימיט וסטופ · ב': כניסה ו-TRAIL — עד היציאה בזמן
מחוץ ליקום שנבדק: מחיר מתחת ל-5$ (תנאי דיווחי בעלי העניין ייבדק בשלב הבא)
תווית: ללא יתרון מוכח
⚠️ כרטיס מחושב בלבד, לא ייעוץ השקעות: הבוט לא שולח פקודות.
מקור: נרות יומיים וציטוט SIP של `Alpaca` (באיחור של `15` דקות) · מנוע הרמות של האלגוריתם

אין כרטיס ל-`LITB` ליום 9.10.2026: הפוזיציה גדולה מ-1% ממחזור הדולרים היומי הממוצע
⚠️ כרטיס מחושב בלבד, לא ייעוץ השקעות: הבוט לא שולח פקודות.
מקור: נרות יומיים וציטוט SIP של `Alpaca` (באיחור של `15` דקות) · מנוע הרמות של האלגוריתם
```

Checked by hand:
- **AAPL:** R = 12.92, 7 shares (3 + 4), risk $90.42, position $2,370.34.
- **Valid sessions:** 12–14.10.
- **Time exit:** a fill on 12.10 exits on 10.11.2026, the 21st session after it.
- **PLUG:** cost (4 × $0.99 + 564 × $0.01) / $99.91 = 9.6%, so it passes the guard; its universe mark is price
  below $5.
- **LITB:** refused, size above 1% of dollar volume.
- **First dry run (run 38088138077):** failed, because `common.send` exits in Actions without a Telegram token. The
  dry run now prints instead.

## Independent review

A reviewer who did not write the code reviewed the code, the tests, the dry run and the limits:
- **No critical finding.** The no-signal and no-outcome limits hold, and the dry-run numbers are consistent.
- **Fixed with tests:**
  - **M1:** a symbol without a bar on D (halted, delisted or not yet published) took its levels from an older
    session. It is now refused, and the cache entry without that bar is dropped.
  - **m1 (re-graded important):** levels of a stock below $1 were shown at 2 decimals, and trail amounts did not
    follow the stock's price scale.
- **For the owner before phase C, M2:** on a breakout that rests at its limit, the entry session's high can come
  before the fill. Rule 4 and decision 6 still count that high, for leg B's running maximum and for leg A's target.
  This has no effect in phase B, because the simulation is unused. It would favour such entries in phase C.
- **Minor findings, not acted on:**
  1. The $0.01 spread floor also lifts normal sub-cent spreads. The spec applies it only to locked or crossed
     quotes.
  2. Between 16:00 and 16:15 ET, the "first entry day already trading" note can name a session that has closed.
  3. `/ticket` drops unknown or extra symbols silently, and one failure stops the rest.
  4. The IBKR line says "בחצי כמות" (in half quantity), although odd share counts give unequal legs.
  5. The 1% refusal averages 15–19 bars when there are fewer than 20 (a ruling).
  6. Test gaps: a resting order followed by a lower open; leg B's exit type; exits in open mode; the exit index for
     a fill on session 2 or 3. The no-simulation test only checks the source text.

## Limitations

- `/ticket` reaches Telegram only after a merge to main (the listener runs from main), which needs the owner's
  approval.
- The free Alpaca plan lags 15 minutes, so a ticket is always for the latest closed session.
- Signals, the journal, the full universe and the scan are phases C–E.
