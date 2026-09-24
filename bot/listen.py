"""Telegram polling (Actions, every 10 min): /help /check /scan /status and free-text tickers -> Hebrew replies."""
import re
import sys
import traceback

from bot import check, common, scan
from bot.common import code

MAX = 3  # reports per message
# "$xyz" or a 1-5 letter Latin word, optional class suffix (BRK.B / BRK-B); Hebrew prefixes ("ו-MSFT") are fine
TOKEN = re.compile(r"(?<![A-Za-z0-9$])(\$?)([A-Za-z]{1,5}(?:[.\-][A-Za-z])?)(?![A-Za-z0-9])")
# Everyday words that are also tickers: skipped in running text; "$IT" or a message of just "IT" still works
STOP = set("A AI AM AN AND ARE AT BE BUY BY CAN CEO DO FOR GO HAS HI I IF IN IPO IS IT ME MY NEW NO NOW OF OK ON OR "
           "OUT SEC SO THE TO UP US VS WE YES YOU".split())
COMMANDS = (("check", "דוח פורנזי לטיקר, למשל /check AAPL"), ("scan", "הרצת הסריקה היומית עכשיו"),
            ("status", "מתי נסרק יום המסחר האחרון"), ("help", "רשימת הפקודות"))
HINT = (f"שלחו טיקר באותיות גדולות, למשל {code('AAPL')}, עם דולר, {code('$msft')}, או {code('/check msft')}"
        f" (עד {code(MAX)} בהודעה), או {code('/help')} לרשימת הפקודות.")
HELP = "\n".join((
    "👋 <b>שלום! אני בוט מחקר מניות שעובד רק עם נתוני SEC EDGAR</b>",
    "הפקודות:",
    f"• דוח פורנזי לטיקר: {code('/check AAPL')} (עד {code(MAX)} טיקרים)",
    f"• הרצת הסריקה היומית עכשיו ושליחת ההתראות: {code('/scan')}",
    f"• מתי נסרק יום המסחר האחרון ומה בזיכרון: {code('/status')}",
    f"• רשימת הפקודות: {code('/help')}",
    f"אפשר גם לכתוב טיקר באותיות גדולות, {code('AAPL')}, או עם דולר, {code('$tsla')}.",
    "📊 הדוח כולל ציון פיוטרוסקי, אלטמן Z, בנייש M, רכישות בעלי עניין ושינויים בגורמי הסיכון בדוח השנתי.",
    f"🔔 כל בוקר אחרי יום מסחר אסרוק את דיווחי {code('Form 4')} ואתריע כשכמה בעלי עניין קונים מניות"
    " בשוק הפתוח, או כשיש רכישה גדולה במיוחד.",
    "⏱️ ההודעות נבדקות בערך כל 10 דקות, כך שהתשובה עשויה להתעכב מעט."))


def extract(text, loose=False):
    """Text -> (known tickers, unknown candidates worth a reply), deduped, in order. In plain text only '$xyz' and
    ALL-CAPS 1-5 letter words that are not everyday words count ('all good', 'ok thanks', 'hi' are not tickers);
    in /check's arguments (loose) every word counts, in any case."""
    m = common.tickers()
    toks = [(d, s, s.upper().replace(".", "-")) for d, s in TOKEN.findall(text)]  # SEC map spells classes BRK-B
    known, unknown = [], []
    for dollar, sym, t in toks:
        if dollar or loose:
            (known if t in m else unknown).append(t)
        elif sym.isupper() and t not in STOP and t in m:
            known.append(t)
    return list(dict.fromkeys(known)), list(dict.fromkeys(unknown))


def reports(text, loose=False):
    """Ticker text -> one report per ticker (<= MAX) plus notes; returns how many reports failed."""
    known, unknown = extract(text, loose)
    notes = [f"לא מצאתי ברשימת החברות של SEC: {', '.join(map(code, unknown))}."] if unknown else []
    if not known:
        notes += ([] if unknown else ["לא זיהיתי טיקר בהודעה."]) + [HINT]
    elif len(known) > MAX:
        notes.append(f"אכין דוח רק ל־{code(MAX)} הראשונים: {', '.join(map(code, known[:MAX]))}.")
    if notes:
        common.send("\n".join(notes))
    failed = 0
    for t in known[:MAX]:
        try:
            common.send(check.report(t), signal=True)
        except Exception as e:  # one failed ticker must not block the others
            failed += 1
            traceback.print_exc()
            common.send(f"⚠️ הדוח עבור {code(t)} נכשל ({code(type(e).__name__)}): {common.failure(e)}")
    return failed


def run_scan():
    """/scan. On Actions the daily-scan workflow is dispatched: it is the only writer of data/state.json, so an
    alert still goes out once. Locally the scan runs in this process. Either way the owner hears back."""
    if common.env("GITHUB_REPOSITORY") and common.env("GITHUB_TOKEN"):
        err = common.dispatch("daily-scan.yml", {"notify": "true"})
        if err:  # a GitHub permission problem must not be reported as "SEC is down"
            print(f"workflow dispatch failed: {err}")
            common.send(f"⚠️ לא הצלחתי להפעיל את הסריקה ב־GitHub ({code(err)}). בדקו שבקובץ"
                        f" {code('telegram-listen.yml')} מופיעה ההרשאה {code('actions: write')}.")
            return 1
        common.send("⏳ הפעלתי את הסריקה היומית. ההתראות, או הודעה שאין חדש, יגיעו בעוד כמה דקות.")
    else:
        common.send("⏳ מריץ את הסריקה היומית, זה עשוי לקחת כמה דקות...")
        scan.main(["--notify"])
    return 0


def handle(text):
    """One message from the owner's chat -> Hebrew replies; returns how many reports failed."""
    word, rest = (text.split(maxsplit=1) + ["", ""])[:2]  # any whitespace: "/check\nAAPL" works too
    if not word.startswith("/"):
        return reports(text)  # plain text: $XYZ / ALL-CAPS tickers only
    cmd = word[1:].split("@")[0].lower()  # "/check@MyBot" in groups
    if cmd == "check":
        if rest:
            return reports(rest, loose=True)
        common.send(f"כתבו טיקר אחרי הפקודה, למשל {code('/check AAPL')} או {code('/check msft tsla')}.")
        return 0
    if cmd == "scan":
        return run_scan()
    if cmd == "status":
        common.send(scan.status())
        return 0
    common.send(HELP)  # /help, /start and any unknown command
    common.tg("setMyCommands", commands=[{"command": c, "description": d} for c, d in COMMANDS])  # Telegram's "/" menu
    return 0


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
            failed += handle(m.get("text") or m.get("caption") or "")  # failed reports also turn the run red
        except (Exception, SystemExit) as e:  # e.g. SEC down: tell the owner, keep going, mark the run red
            failed += 1
            traceback.print_exc()
            common.send(f"⚠️ לא הצלחתי לטפל בהודעה ({code(type(e).__name__)}): {common.failure(e)}")
    if failed:
        sys.exit(f"{failed} message(s) failed")


if __name__ == "__main__":
    main()
