"""/screen and /screens for bot/listen.py. On Actions the listener validates the input, then dispatches the screener
workflow (it has the stores' cache and writes data/screener_*.json); locally everything runs in this process.

    /screen <codes> [o=<sort>] [s=<signal>]     a one-off screen (a pasted screener URL works too)
    /screen <name> | /screen run <name>         a saved screen now
    /screen save <name> <codes> [o=..] [sessions=pre,rth,post] [every=60m]
    /screen del <name>
    /screens                                    the saved screens, their schedule and last run"""
from bot import common
from bot.common import code
from bot.screener import format as fmt
from bot.screener import parser, presets

WORKFLOW = "screener.yml"
USAGE = "\n".join((
    "🔎 <b>סורק בסגנון finviz על נתונים חינמיים</b>",
    f"סריקה: {code('/screen cap_small,sh_float_u50,sh_short_o20 o=-change')}",
    f"עם סיגנל: {code('/screen s=ta_unusualvolume,sh_avgvol_o500')}",
    f"סריקה שמורה: {code('/screen unusual_volume_liquid')} · הרשימה: {code('/screens')}",
    f"שמירה: {code('/screen save NAME CODES sessions=rth every=60m')} · מחיקה: {code('/screen del NAME')}",
    "הנתונים באיחור של כ־15 דקות; ערכים מקורבים מסומנים ב־≈."))


def _on_actions():
    return common.env("GITHUB_REPOSITORY") and common.env("GITHUB_TOKEN")


def _dispatch(inputs, wait_text):
    err = common.dispatch(WORKFLOW, inputs)
    if err:
        print(f"workflow dispatch failed: {err}")
        common.send(f"⚠️ לא הצלחתי להפעיל את הסורק ב־GitHub ({code(err)}).", source=fmt.SOURCE)
        return 1
    common.send(wait_text, source=fmt.SOURCE)
    return 0


def _local(mode, **kw):
    from bot.screener import run  # the stores and their network code load only when a screen runs here
    return {"query": run.query_mode, "preset": run.preset_mode, "save": run.save_mode,
            "delete": run.delete_mode}[mode](**kw)


def screen(rest):
    """/screen ... -> 0 (answered) or 1 (a GitHub dispatch failed)."""
    words = rest.split()
    if not words:
        common.send(USAGE, source=fmt.SOURCE)
        return 0
    head = words[0].lower()
    if head == "save":
        text = rest.split(None, 1)[1] if len(words) > 1 else ""
        try:
            presets.parse_save(text)  # validate here: a bad input is answered at once
        except presets.PresetError as e:
            from bot.screener.run import SAVE_ERR
            common.send("⚠️ " + SAVE_ERR.get(str(e), str(e)), source=fmt.SOURCE)
            return 0
        if _on_actions():
            return _dispatch({"mode": "save", "query": text}, "⏳ שומר את הסריקה. האישור יגיע בעוד כ־2 דקות.")
        return _local("save", text=text)
    if head in ("del", "delete", "rm"):
        name = words[1].lower() if len(words) > 1 else ""
        if not presets.NAME.match(name):
            common.send(f"⚠️ כתבו את שם הסריקה, למשל {code('/screen del mylist')}.", source=fmt.SOURCE)
            return 0
        if _on_actions():
            return _dispatch({"mode": "delete", "name": name}, "⏳ מוחק את הסריקה. האישור יגיע בעוד כ־2 דקות.")
        return _local("delete", name=name)
    name = words[1].lower() if head == "run" and len(words) > 1 else (head if len(words) == 1 else "")
    if name and presets.get(name):
        if _on_actions():
            return _dispatch({"mode": "preset", "name": name},
                             f"⏳ מריץ את {code(name)}. התוצאות יגיעו בעוד כ־2–4 דקות.")
        return _local("preset", name=name)
    if head == "run":
        common.send(f"⚠️ אין סריקה שמורה בשם {code(name)}. לרשימה: {code('/screens')}", source=fmt.SOURCE)
        return 0
    try:
        q = parser.parse(rest, strict=True)
    except parser.InputError:
        common.send(f"⚠️ הקודים לא תקינים. מותר: אותיות קטנות, ספרות ו־{code('_ . | , - =')},"
                    f" עד {code(parser.MAX_CODES)} קודים.", source=fmt.SOURCE)
        return 0
    if not q.filters and not q.signal:
        common.send("\n".join([f"⚠️ לא זיהיתי קודי סינון או שם של סריקה שמורה. לרשימה: {code('/screens')}"]
                              + fmt.ignored_lines(q.ignored)), source=fmt.SOURCE)
        return 0
    if _on_actions():
        return _dispatch({"mode": "query", "query": rest.strip()}, "⏳ הסורק רץ. התוצאות יגיעו בעוד כ־2–4 דקות.")
    return _local("query", text=rest.strip())


def screens(rest=""):
    """/screens: every saved screen with its schedule and last run."""
    common.send(fmt.presets_text(presets.all_presets(), presets.load_state()), source=fmt.SOURCE)
    return 0
