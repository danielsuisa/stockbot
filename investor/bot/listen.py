"""Telegram polling (Actions, every 10 min): tickers in free text -> forensic reports; /start -> help."""
import re
import sys
import traceback

from bot import check, common
from bot.common import code

MAX = 3  # reports per message
# "$xyz" or a 1-5 letter Latin word, optional class suffix (BRK.B / BRK-B); Hebrew prefixes ("ו-MSFT") are fine
TOKEN = re.compile(r"(?<![A-Za-z0-9$])(\$?)([A-Za-z]{1,5}(?:[.\-][A-Za-z])?)(?![A-Za-z0-9])")
# Everyday words that are also tickers: skipped in running text; "$IT" or a message of just "IT" still works
STOP = set("A AI AM AN AND ARE AT BE BUY BY CAN CEO DO FOR GO HAS HI I IF IN IPO IS IT ME MY NEW NO NOW OF OK ON OR "
           "OUT SEC SO THE TO UP US VS WE YES YOU".split())
HINT = (f"שלחו טיקר באותיות גדולות, כמו {code('AAPL')}, או עם {code('$')}, כמו {code('$msft')}"
        f" (עד {code(MAX)} בהודעה), או {code('/help')} להסבר.")
HELP = "\n".join((
    "👋 <b>שלום! אני בוט מחקר מניות שעובד רק עם נתוני SEC EDGAR</b>",
    f"🔔 כל בוקר אחרי יום מסחר אסרוק את דיווחי {code('Form 4')} ואתריע כשכמה בעלי עניין קונים מניות"
    " בשוק הפתוח, או כשיש רכישה גדולה במיוחד.",
    f"📊 שלחו טיקר, למשל {code('AAPL')} או {code('$TSLA')} (עד {code(MAX)} בהודעה), ותקבלו דוח פורנזי:"
    " ציון פיוטרוסקי, אלטמן Z, בנייש M, רכישות בעלי עניין ושינויים בגורמי הסיכון בדוח השנתי.",
    "⏱️ ההודעות נבדקות בערך כל 10 דקות, כך שהתשובה עשויה להתעכב מעט."))


def extract(text):
    """Free text (Hebrew/English) -> (known tickers, unknown candidates worth a reply), deduped, in order.
    A candidate is '$xyz', an ALL-CAPS word that is not an everyday word, or - in any case - a message that
    is just one word ('nvda'). Lowercase words inside sentences are ignored: 'all good' is not ALL + GOOD."""
    m = common.tickers()
    toks = [(d, s, s.upper().replace(".", "-")) for d, s in TOKEN.findall(text)]  # SEC map spells classes BRK-B
    single = len(toks) == 1 and not re.search(r"[^\W\d_]", TOKEN.sub("", text))  # no other words at all
    known, unknown = [], []
    for dollar, sym, t in toks:
        if dollar or single:
            (known if t in m else unknown).append(t)
        elif sym.isupper() and t not in STOP and t in m:
            known.append(t)
    return list(dict.fromkeys(known)), list(dict.fromkeys(unknown))


def handle(text):
    """One message from the owner's chat -> Hebrew replies."""
    if (text.split() or [""])[0].split("@")[0].lower() in ("/start", "/help"):
        return common.send(HELP)
    known, unknown = extract(text)
    notes = [f"לא מצאתי ברשימת החברות של SEC: {', '.join(map(code, unknown))}."] if unknown else []
    if not known:
        notes += ([] if unknown else ["לא זיהיתי טיקר בהודעה."]) + [HINT]
    elif len(known) > MAX:
        notes.append(f"אכין דוח רק ל־{code(MAX)} הראשונים: {', '.join(map(code, known[:MAX]))}.")
    if notes:
        common.send("\n".join(notes))
    for t in known[:MAX]:
        try:
            common.send(check.report(t))
        except Exception as e:  # one failed ticker must not block the others
            traceback.print_exc()
            common.send(f"⚠️ הדוח עבור {code(t)} נכשל ({code(type(e).__name__)}). נסו שוב מאוחר יותר.")


def main():
    if not (common.env("TG_TOKEN") and common.env("TG_CHAT_ID")):
        sys.exit("TG_TOKEN / TG_CHAT_ID secret is missing or misnamed")
    ups = common.tg("getUpdates", timeout=0, allowed_updates=["message"])
    if not ups:
        return print("no new messages")
    # ACK before processing: a crash mid-report must never make the next run re-process (and re-crash on) it
    common.tg("getUpdates", offset=max(u["update_id"] for u in ups) + 1, timeout=0)
    failed = 0
    for u in ups:
        m = u.get("message") or {}
        if str(m.get("chat", {}).get("id")) != common.env("TG_CHAT_ID"):
            print(f"update {u['update_id']}: ignored, not from TG_CHAT_ID")  # no ids/text: public repo logs are public
            continue
        print(f"update {u['update_id']}: handling")
        try:
            handle(m.get("text") or m.get("caption") or "")
        except (Exception, SystemExit) as e:  # e.g. SEC down: tell the owner, keep going, mark the run red
            failed += 1
            traceback.print_exc()
            common.send(f"⚠️ לא הצלחתי לטפל בהודעה ({code(type(e).__name__)}); ייתכן ש־SEC לא זמין כרגע."
                        " נסו שוב מאוחר יותר.")
    if failed:
        sys.exit(f"{failed} message(s) failed")


if __name__ == "__main__":
    main()
