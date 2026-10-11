# Recommendation algorithm: phase B.1 (sizing by equity)

Spec: `docs/superpowers/specs/2026-10-10-recommendation-algorithm-design.md`, section 10.
Plan: `docs/superpowers/plans/2026-10-11-recommendation-algorithm-phase-b1.md`, with the owner's conditions 1–5 and
changes 1–7 of 2026-10-11.

**Every equity figure in this report is synthetic:** 12,345.67 in the dry run and 31,337.17 in the tests. No real
equity appears in the repository, a commit, a log or a workflow summary.

## What was built

- **Rule (section 10):** for a live `/ticket`, risk = p × E and shares = floor(risk / R).
  - The 20% position cap, floor(0.20 × E / P), applies before every refusal, so the refusals see the final shares.
  - p is capped at 0.5% while the label is "ללא יתרון מוכח" ("no proven edge").
  - The message shows the open-risk limit as floor(3% / p) positions.
  - When E is set and the risk is under $20, the refusal message gives the small-account reason.
- **`/equity AMOUNT`** stores E as a bot message pinned silently in the owner's **private** chat. It accepts exactly
  one number between $100 and $100,000,000, plain or with thousands commas.
- **`/equity`** with no amount shows E and the date it was set.
- **`/ticket`** reads E once per command. Without E (no pin, a replaced or stale-layout pin, a chat that is not
  private, or Telegram unavailable), it uses `TICKET_RISK_USD` and says that the size is not tied to the account.
- **`TICKET_RISK_PCT`** is the repository variable for p, in percent: `0.5` means 0.5%. If it is unset, not a
  number, or ≤ 0, the default of 0.5% applies and the message says so.
- **The fixed $100 risk stays** for the backtest, the blind run, the verdict run and the shadow journal.

## The owner's conditions, and their proof

| Condition | How it is met | Test |
|---|---|---|
| 1. Only from the owner's chat | `listen.answer` drops other chats before `handle`; the store uses `TG_CHAT_ID` only | `test_equity_from_another_chat_is_ignored` |
| Private chat only | the store and `load` require `getChat(...).type == "private"` | `test_store_refuses_a_non_private_chat`, `test_ticket_falls_back_in_a_non_private_chat` |
| 2. Nothing reaches a log, an exception, a summary or a file | see the bullets below this table | `test_privacy_end_to_end` and `test_privacy_when_the_store_fails` (shown RED against a planted `print(E)`), `test_privacy_when_the_text_is_garbage`, `test_trace_*`, `test_*excepthook*`, `test_no_raw_traceback_in_the_listener_closure` |
| 3. Fallback and strict parsing | `load()` returns None for anything but this bot's exact equity message; bounds $100 to $100M | `test_parse_strict`, `test_load_fallbacks`, `test_ticket_fallback_line` |
| Stale pin | `load()` returns the pin's date, shown in `/equity` and on every ticket | `test_older_equity_pin_surfaces_with_its_date` |
| 4. Dry runs with a synthetic E only | `tests/listen_dry_run.py --synthetic-equity`: marked `[E סינתטי לבדיקה]` ("synthetic E for testing"); refuses with a Telegram token and any other `/equity` amount | `test_dry_run_synthetic_marked`, `test_dry_run_refuses_with_a_token`, `test_dry_run_synthetic_guard_reads_the_command_like_the_listener` |
| 5. Proof | the tests above, and the live check below after the first real `/equity` | — |

How condition 2 is met:
- `common.trace` replaces all 12 `print_exc` calls in the listener's import closure: `listen` 5, `check` 1, `scan`
  2, `shadow` 1, `squeeze_live` 3. It prints an exception's type, its frames and an HTTP status, never its message.
- `sys.excepthook` and `threading.excepthook` are set to the same printer when the listener starts.
- The store raises errors without a value.

**Live probe (Task 0, run 38114826298).** One test message with no number was sent, pinned, read back and deleted.
The run printed `private_chat: True`, `pin_succeeded: True` and `pinned_message_is_ours: True`.

## Synthetic dry run (run 38115317638)

The listener's own update path ran with the listener step's environment and no Telegram secrets. The inputs were
`/equity 12345.67`, `/ticket AAPL BRK.B` and `/equity`. `TICKET_RISK_PCT` is not set in the repository, so the
default note appears. Messages as printed (`<code>` shown as backticks):

```
SYNTHETIC EQUITY: every equity figure below comes from the synthetic E 12,345.67, not from an account
update 1: handling
[E סינתטי לבדיקה] 💼 הון לחישוב גודל: `12,345.67$` · נקבע ב-11.10.2026
סיכון לעסקה: `0.5%` מההון, כלומר `61.73$` (הערך `TICKET_RISK_PCT` הוא באחוזים: `0.5` פירושו `0.5%`)
מגבלת הסיכון הפתוח: עד `6` פוזיציות פתוחות בבת אחת (`3%` מההון; הבוט לא אוכף זאת)
שים לב: הערך `TICKET_RISK_PCT` לא הוגדר, ולכן נלקחה ברירת המחדל `0.5%`.

update 2: handling
[E סינתטי לבדיקה] 🎫 כרטיס ל-`AAPL` · יום האות 9.10.2026
מקור: בקשה ידנית · אותות: אין אות תומך
כניסה: קניית פריצה — סטופ `338.62$` · לימיט `340.23$` · בתוקף ב-12.10.2026, 13.10.2026, 14.10.2026
סטופ לוס (שתי המנות): `325.70$` · יעד מנה א': `358.00$` · טריילינג מנה ב': `12.92$`
המחירים מחושבים ממחיר הכניסה המתוכנן ויזוזו עם הכניסה בפועל (כניסה פחות `12.92$`, כניסה ועוד `19.38$`)
יציאה בזמן: בסגירה של יום המסחר ה-`21` אחרי הכניסה (כניסה ב-12.10.2026 ← יציאה ב-10.11.2026)
מניות: מנה א' `2` · מנה ב' `2` · סיכון `51.67$` (`0.4%` מההון) · שווי פוזיציה `1,354.48$` (`11.0%` מההון)
גודל לפי הון שנקבע ב-11.10.2026: סיכון של `0.5%` לעסקה · עד `6` פוזיציות פתוחות בבת אחת (`3%` מההון)
שים לב: הערך `TICKET_RISK_PCT` לא הוגדר, ולכן נלקחה ברירת המחדל `0.5%`.
עלות משוערת: `2.9%` מהסיכון (מרווח 5 דקות לפני סגירת יום האות, ועמלות)
ב-IBKR: פקודת bracket אחת לכל מנה, בכמויות שבשורת המניות — א': כניסה, יעד לימיט וסטופ · ב': כניסה ו-TRAIL — עד היציאה בזמן
תווית: ללא יתרון מוכח
⚠️ כרטיס מחושב בלבד, לא ייעוץ השקעות: הבוט לא שולח פקודות.
מקור: נרות יומיים וציטוט SIP של `Alpaca` (באיחור של `15` דקות) · מנוע הרמות של האלגוריתם

[E סינתטי לבדיקה] 🎫 כרטיס ל-`BRK.B` · יום האות 9.10.2026
מקור: בקשה ידנית · אותות: אין אות תומך
כניסה: קניית פריצה — סטופ `516.58$` · לימיט `517.84$` · בתוקף ב-12.10.2026, 13.10.2026, 14.10.2026
סטופ לוס (שתי המנות): `506.49$` · יעד מנה א': `531.71$` · טריילינג מנה ב': `10.09$`
המחירים מחושבים ממחיר הכניסה המתוכנן ויזוזו עם הכניסה בפועל (כניסה פחות `10.09$`, כניסה ועוד `15.13$`)
יציאה בזמן: בסגירה של יום המסחר ה-`21` אחרי הכניסה (כניסה ב-12.10.2026 ← יציאה ב-10.11.2026)
מניות: מנה א' `2` · מנה ב' `2` · סיכון `40.36$` (`0.3%` מההון) · שווי פוזיציה `2,066.32$` (`16.7%` מההון)
גודל לפי הון שנקבע ב-11.10.2026: סיכון של `0.5%` לעסקה · עד `6` פוזיציות פתוחות בבת אחת (`3%` מההון)
שים לב: הערך `TICKET_RISK_PCT` לא הוגדר, ולכן נלקחה ברירת המחדל `0.5%`.
שים לב: הגודל הוגבל כך שהפוזיציה לא תעלה על `20%` מההון.
עלות משוערת: `3.6%` מהסיכון (מרווח 5 דקות לפני סגירת יום האות, ועמלות)
ב-IBKR: פקודת bracket אחת לכל מנה, בכמויות שבשורת המניות — א': כניסה, יעד לימיט וסטופ · ב': כניסה ו-TRAIL — עד היציאה בזמן
תווית: ללא יתרון מוכח
⚠️ כרטיס מחושב בלבד, לא ייעוץ השקעות: הבוט לא שולח פקודות.
מקור: נרות יומיים וציטוט SIP של `Alpaca` (באיחור של `15` דקות) · מנוע הרמות של האלגוריתם

update 3: handling
[E סינתטי לבדיקה] 💼 הון לחישוב גודל: `12,345.67$` · נקבע ב-11.10.2026
סיכון לעסקה: `0.5%` מההון, כלומר `61.73$` (הערך `TICKET_RISK_PCT` הוא באחוזים: `0.5` פירושו `0.5%`)
מגבלת הסיכון הפתוח: עד `6` פוזיציות פתוחות בבת אחת (`3%` מההון; הבוט לא אוכף זאת)
שים לב: הערך `TICKET_RISK_PCT` לא הוגדר, ולכן נלקחה ברירת המחדל `0.5%`.

answers: 4, failed: 0
```

Checked by hand:
- **AAPL:** risk 61.73 / R 12.92 gives 4 shares, and the 20% cap of 7 does not bind.
- **BRK.B:** 6 shares by risk, cut to 4 by the cap (floor(2,469.13 / 516.58)), and the note appears.

## Live check after the first real `/equity` (the owner's condition 5)

After the owner sends the first real `/equity` (once a merge is approved):

1. **My check of the public log.** I download the log of the listener run that handled it and check that every line
   of the listener step matches a known line shape: `update N: handling`, `serve_end`, `getUpdates` retries,
   `runs API`, `data refresh`, and `common.trace` frames. I also check that no line contains `$`, "הון" ("equity")
   or a number with a thousands comma.
2. **The owner's check, with `gh`:**

   ```bash
   gh run view <run id> --repo danielsuisa/stockbot --log | grep -c "<E>"
   ```

   Expected: `0`. Run it for both forms, plain (`25000`) and with thousands commas (`25,000`).
3. **The owner's check, without `gh`:** open the run on GitHub (Actions → Telegram listener → the run → the step
   "Answer Telegram messages") and use the browser's find on the page. Search for the E plain and with thousands
   commas. Expected: no match in either form.

## Independent review

A reviewer who did not write the code checked the code, the tests, the privacy proof and the dry run:
- No critical finding.
- **The privacy requirement holds:** no path from E, the dollar risk or the position value to stdout, stderr,
  `common.log`, the summary, a file or a commit.

Fixed with tests:
- **Major:** the 1% dollar-volume refusal was applied before the 20% cap. The cap now comes first.
- **Minor, re-graded important:** the synthetic dry run did not read `/EQUITY` or `/equity@Bot` as `/equity`, so a
  real amount could have been printed. It now reads the command as the listener does.

Minor findings not acted on, for the owner:
1. When Telegram is down, `/ticket` and `/equity` report E as not set. The fallback is correct; the wording blames
   the setting.
2. If unpinning the previous pin fails, the owner is told the save failed, although the new E is pinned. A retry
   works.
3. Only the previous top pin is unpinned. If the owner pinned something else in between, an older equity pin can
   resurface; it is shown with its date, as required.
4. The import-closure test reads only `from bot import` forms and the three traceback function names.
5. An old E gets no age warning; its date is shown.

## Tests

616 offline tests, all passing.
