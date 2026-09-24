"""Telegram listener: /help /check /scan /status /stats /journal /verify /health and free-text tickers -> Hebrew
replies. `--serve` (Actions): one long-poll consumer for ~5h20m, then the workflow starts the next one; without it,
one pass over the waiting messages."""
import datetime as dt
import http.client
import json
import re
import subprocess
import sys
import time
import traceback
import urllib.error
from pathlib import Path

from bot import check, common, fundamentals, health, journal, market, scan
from bot.common import code

MAX = 3  # reports per message
# "$xyz" or a 1-5 letter Latin word, optional class suffix (BRK.B / BRK-B); Hebrew prefixes ("ו-MSFT") are fine
TOKEN = re.compile(r"(?<![A-Za-z0-9$])(\$?)([A-Za-z]{1,5}(?:[.\-][A-Za-z])?)(?![A-Za-z0-9])")
# Everyday words that are also tickers: skipped in running text; "$IT" or a message of just "IT" still works
STOP = set("A AI AM AN AND ARE AT BE BUY BY CAN CEO DO FOR GO HAS HI I IF IN IPO IS IT ME MY NEW NO NOW OF OK ON OR "
           "OUT SEC SO THE TO UP US VS WE YES YOU".split())
COMMANDS = (("check", "דוח פורנזי לטיקר, למשל /check AAPL"), ("scan", "הרצת הסריקה היומית עכשיו"),
            ("status", "מצב הסריקה, דופק ושומר ימים חסרים"), ("stats", "תשואות ההתראות מול SPY"),
            ("journal", "ההתראות האחרונות ביומן, למשל /journal 5"), ("verify", "אימות מחדש מול EDGAR, למשל /verify AAPL"),
            ("health", "בדיקה חיה של המקורות והריצות"), ("help", "רשימת הפקודות"))
HINT = (f"שלחו טיקר באותיות גדולות, למשל {code('AAPL')}, עם דולר, {code('$msft')}, או {code('/check msft')}"
        f" (עד {code(MAX)} בהודעה), או {code('/help')} לרשימת הפקודות.")
HELP = "\n".join((
    "👋 <b>שלום! אני בוט מחקר מניות שעובד רק עם נתוני SEC EDGAR</b>",
    "הפקודות:",
    f"• דוח פורנזי לטיקר: {code('/check AAPL')} (עד {code(MAX)} טיקרים)",
    f"• הרצת הסריקה היומית עכשיו ושליחת ההתראות: {code('/scan')}",
    f"• מצב הסריקה, הדופק האחרון ושומר הימים החסרים: {code('/status')}",
    f"• תשואות ההתראות מול {code('SPY')} (30/90/180 יום): {code('/stats')}",
    f"• ההתראות האחרונות ביומן: {code('/journal 5')} (עד {code(20)})",
    f"• אימות התראה מחדש מול EDGAR: {code('/verify AAPL')}",
    f"• בדיקה חיה של SEC, Yahoo, טלגרם והריצות המתוזמנות: {code('/health')}",
    f"• רשימת הפקודות: {code('/help')}",
    f"אפשר גם לכתוב טיקר באותיות גדולות, {code('AAPL')}, או עם דולר, {code('$tsla')}.",
    "📊 הדוח כולל ציון פיוטרוסקי, אלטמן Z, בנייש M, רכישות בעלי עניין ושינויים בגורמי הסיכון בדוח השנתי.",
    f"🔔 כל בוקר אחרי יום מסחר אסרוק את דיווחי {code('Form 4')} ואתריע כשכמה בעלי עניין קונים מניות"
    " בשוק הפתוח, או כשיש רכישה גדולה במיוחד.",
    "⏱️ התשובה מגיעה בדרך כלל תוך שניות; דוח מלא לוקח כדקה."))


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
    if cmd == "stats":
        common.send(journal.stats_text(journal.load()))
        return 0
    if cmd == "journal":
        arg = rest.split()[0] if rest.split() else ""
        n = int(arg) if re.fullmatch(r"[0-9]{1,3}", arg) else 5  # ASCII only: "²".isdigit() is True, int() fails
        common.send(journal.journal_text(journal.load(), n))
        return 0
    if cmd == "verify":
        known, unknown = extract(rest, loose=True)
        if not known:
            common.send(f"לא מצאתי ברשימת החברות של SEC: {', '.join(map(code, unknown))}." if unknown else
                        f"כתבו טיקר אחרי הפקודה, למשל {code('/verify AAPL')}.")
            return 0
        common.send(scan.verify(known[0]), signal=True)
        return 0
    if cmd == "health":
        common.send(health.report())
        return 0
    common.send(HELP)  # /help, /start and any unknown command
    common.tg("setMyCommands", commands=[{"command": c, "description": d} for c, d in COMMANDS])  # Telegram's "/" menu
    return 0


SERVE_SECONDS = 5 * 3600 + 20 * 60  # the job allows 340 min: the last long poll and reply fit well inside it
POLL = 50  # getUpdates long-poll seconds (common.fetch's socket timeout is 60)
LIVE_AFTER = 120  # an in-progress listener run older than this is past its start-up: it is the live poller


class Conflict(Exception):
    """Telegram 409: another getUpdates consumer (or a webhook) owns this bot's updates."""


def updates(**params):
    """getUpdates -> list; a 409 becomes Conflict, never retried: the other consumer keeps the updates."""
    try:
        return common.tg("getUpdates", **params)
    except urllib.error.HTTPError as e:
        if e.code != 409:
            raise
        try:
            why = json.loads(e.read()).get("description", "")
        except Exception:
            why = ""
        raise Conflict(why or "409 Conflict") from e


def answer(ups):
    """ACK a batch, then answer the owner's messages in it -> how many failed. The ACK comes first: a crash
    mid-report must never make the next poll (in this job or the next one) re-process, and re-crash on, it.
    A 409 on the ACK raises Conflict before anything is handled: those updates belong to the other consumer."""
    updates(offset=max(u["update_id"] for u in ups) + 1, timeout=0)
    failed = 0
    for u in ups:
        m = u.get("message") or {}
        if str(m.get("chat", {}).get("id")) != common.env("TG_CHAT_ID"):
            print(f"update {u['update_id']}: ignored, not from TG_CHAT_ID")  # no ids/text: public repo logs are public
            continue
        print(f"update {u['update_id']}: handling")
        try:
            failed += handle(m.get("text") or m.get("caption") or "")
        except (Exception, SystemExit) as e:  # e.g. SEC down: tell the owner, keep going
            failed += 1
            traceback.print_exc()
            common.send(f"⚠️ לא הצלחתי לטפל בהודעה ({code(type(e).__name__)}): {common.failure(e)}")
    return failed


def refresh():
    """Before each batch of a long-lived process, what a fresh 10-minute run used to get for free: the scan commits
    data/ while we serve, and per-process caches (SEC submissions, quarter listings, prices, freshness stamps) age."""
    for mod in (common, scan, check, fundamentals, market, journal, health):
        for f in list(vars(mod).values()):
            if callable(getattr(f, "cache_clear", None)):
                f.cache_clear()
    common.STAMPS.clear()
    fundamentals.QUOTED.clear()
    market.CACHE.clear()
    market._loaded[0] = False
    scan._pay_budget[0] = scan.MAX_PAY
    if common.env("GITHUB_ACTIONS"):  # public repo: no credentials needed; only data/ is taken, the code stays
        for cmd in (["git", "fetch", "-q", "--depth=1", "origin", common.env("GITHUB_REF_NAME", "main")],
                    ["git", "checkout", "-q", "FETCH_HEAD", "--", "data"]):
            try:
                r = subprocess.run(cmd, cwd=common.ROOT, capture_output=True, text=True, timeout=60)
            except (OSError, subprocess.TimeoutExpired) as e:
                r = subprocess.CompletedProcess(cmd, 1, "", type(e).__name__)
            if r.returncode:
                print(f"data refresh skipped: {' '.join(cmd[:2])} -> {r.stderr.strip()[:200]}")
                break


def live_poller():
    """Another listener run that is already serving -> its id, else None. Skips this run and the run that
    dispatched it (it is in its last step, starting us); a run younger than LIVE_AFTER is still starting, and if
    both start, Telegram's 409 leaves exactly one. Runs API down -> None (409 is the backstop there too)."""
    repo, token = common.env("GITHUB_REPOSITORY"), common.env("GITHUB_TOKEN")
    if not repo:
        return None
    skip = {common.env("GITHUB_RUN_ID"), common.env("PARENT_RUN_ID")} - {""}
    try:
        body = common.fetch(f"https://api.github.com/repos/{repo}/actions/workflows/telegram-listen.yml/runs"
                            "?status=in_progress&per_page=20",
                            headers={"Accept": "application/vnd.github+json",
                                     **({"Authorization": f"Bearer {token}"} if token else {})}, tries=2, timeout=20)
        runs = json.loads(body or b"{}").get("workflow_runs") or []
    except (OSError, ValueError, http.client.HTTPException) as e:
        print(f"runs API unavailable ({type(e).__name__}): starting anyway")
        return None
    now = dt.datetime.now(dt.timezone.utc)
    for r in runs:
        started = dt.datetime.fromisoformat((r.get("run_started_at") or r["created_at"]).replace("Z", "+00:00"))
        if str(r["id"]) not in skip and r.get("status") == "in_progress" and (now - started).total_seconds() > LIVE_AFTER:
            return r["id"]
    return None


def output(**kv):
    """Step outputs for the workflow (no-op outside Actions)."""
    if common.env("GITHUB_OUTPUT"):
        with open(common.env("GITHUB_OUTPUT"), "a", encoding="utf-8") as fh:
            fh.writelines(f"{k}={v}\n" for k, v in kv.items())


def serve(seconds=None, stop_file=None, offset=None):
    """Long-poll until `seconds` pass or `stop_file` exists -> (reason, next offset). Reasons: "deadline", "stop",
    "conflict" (another consumer owns the updates: exit, never retry). Network/5xx blips are waited out; anything
    else (401, bugs) raises. Offsets: Telegram confirms every update below the offset of a getUpdates call, so the
    ACK in answer() already outlives this process; the next job is also handed our offset, belt and braces."""
    end = time.monotonic() + (SERVE_SECONDS if seconds is None else seconds)
    stop = Path(stop_file) if stop_file else None
    handled = failed = errors = 0
    reason = "deadline"
    while True:
        left = end - time.monotonic()
        if stop and stop.exists():
            reason = "stop"
            break
        if left <= 0:
            break
        try:
            ups = updates(timeout=max(0, min(POLL, int(left))), allowed_updates=["message"],
                          **({} if offset is None else {"offset": offset}))
            if ups:
                refresh()
                failed += answer(ups)
                handled += len(ups)
                offset = max(u["update_id"] for u in ups) + 1
            errors = 0
            continue
        except Conflict as e:
            print(f"getUpdates conflict: {e} - another listener is serving; exiting")
            reason = "conflict"
            break
        except urllib.error.HTTPError as e:
            if e.code < 500:
                raise
            print(f"getUpdates failed (HTTP {e.code}); retrying")
        except (OSError, http.client.HTTPException, ValueError) as e:  # network blip / bad body: wait it out
            print(f"getUpdates failed ({type(e).__name__}); retrying")
        errors += 1
        time.sleep(min(60, 5 * 2 ** min(errors, 4)))
    common.log("serve_end", reason=reason, updates=handled, failed=failed, offset=offset)
    common.summary(f"Listener: `{reason}` after {handled} update(s), {failed} failed; next offset `{offset}`")
    return reason, offset


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not (common.env("TG_TOKEN") and common.env("TG_CHAT_ID")):
        sys.exit("TG_TOKEN / TG_CHAT_ID secret is missing or misnamed")
    if "--serve" in argv:
        # the */10 cron is only a backup: it steps aside while a poller is live (explicit starts take over via 409)
        if common.env("GITHUB_EVENT_NAME") == "schedule" and (rid := live_poller()):
            print(f"listener run {rid} is already serving; exiting")
            return output(chain="false")
        reason, offset = None, int(common.env("TG_OFFSET")) if common.env("TG_OFFSET").isdigit() else None
        try:
            reason, offset = serve(stop_file=common.env("STOP_FILE") or None, offset=offset)
        finally:  # a crash still hands its offset on; only a planned end says "do not restart"
            output(offset="" if offset is None else offset, chain="false" if reason in ("stop", "conflict") else "true")
        return None
    try:
        ups = updates(timeout=0, allowed_updates=["message"])
        failed = answer(ups) if ups else 0
    except Conflict as e:
        return print(f"getUpdates conflict: {e} - another listener is serving")
    if not ups:
        return print("no new messages")
    if failed:  # failed reports turn the one-shot run red
        sys.exit(f"{failed} message(s) failed")


if __name__ == "__main__":
    main()
