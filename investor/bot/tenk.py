"""'Lazy Prices': sentence-level diff of Item 1A (Risk Factors) between the last two 10-Ks."""
import datetime as dt
import html
import re
import unicodedata
from collections import Counter
from difflib import SequenceMatcher

from bot import common
from bot.common import code, esc

MIN_CHARS = 2000  # shorter "sections" are TOC lines or "not required for smaller reporting companies"
KEYWORDS = {  # label -> pattern for inflected forms, minus boilerplate that is not a risk signal:
    # "amended and restated certificate", clinical "investigator/investigational", "by default"/"default encryption"
    "material weakness": "material weakness", "going concern": "going concern",
    "investigation": r"investigat(?:ions?|e[sd]?|ing|ive)\b", "subpoena": "subpoena",
    "restatement": r"(?<!amended and )restate(?:ments?|[sd]|ing)?\b"
                   r"(?!\s+(?:certificate|bylaws|by-laws|articles|memorandum|charter|credit|agreement))",
    "covenant": "covenant",
    "default": r"default(?:s|ed|ing)?\b(?<!by default)(?!\s+(?:end-to-end|encryption|settings?|options?|mode))",
    "impairment": "impairment", "litigation": "litigat", "tariff": "tariff", "sanction": "sanction",
    "liquidity": "liquidity", "delisting": "delist"}
_KW = {k: re.compile(r"\b" + v, re.I) for k, v in KEYWORDS.items()}
_FURN = re.compile(r"(?i)\W*(?:page\s*)?\d{1,3}\W*|table\s+of\s+contents")  # page numbers, TOC back-links
_HIDDEN = re.compile(r"(?is)<(script|style|head|ix:header)\b.*?</\1\s*>|<!--.*?-->")
_BLOCK = re.compile(r"(?i)<(?:/?(?:p|div|br|tr|li|ul|ol|h\d|table|section|center|blockquote|dt|dd)|hr)\b[^>]*>")
_CELL = re.compile(r"(?i)</?t[dh]\b[^>]*>")
_ITEM = r"(?im)^[ \t]*item[ \t]*(?:no\.[ \t]*)?"
_HEAD = r"(?=[ \t]*[.:]?[ \t]*$|[ \t]*[.:][ \t])"  # a heading line, or a run-in "RISK FACTORS. The ..."
_START = (re.compile(_ITEM + r"1[ \t]*a\b[\s.:\-–—|]*risk\s+factors"), re.compile(_ITEM + r"1[ \t]*a\b"))
_END = re.compile(_ITEM + r"(?:1[ \t]*[bc]|2)\b")
_RF = re.compile(r"(?im)^[ \t]*risk\s+factors" + _HEAD)
_TOC = re.compile(r"(?m)^[ \t]*(?i:risk\s+factors)[ \t\d–-]*\n"  # TOC entry "Risk Factors / 24 / <next title>"
                  r"(?:[ \t]*(?i:page)?[ \t]*\w{1,3}(?:[–-]\d+)?[ \t]*\n){0,2}[ \t]*([A-Z][^\n]{2,70})$")
_EOS = re.compile(r"[.!?:;][\"”’)]*$")
_STOP = re.compile(r"\n|(?:(?<=[.!?])|(?<=[.!?][\"”’)]))\s+(?=[\"“(]?[A-Z0-9])")
_ABBR = re.compile(r"(?:\b[A-Z]|U\.S|e\.g|i\.e|\b(?:Inc|Corp|Co|Ltd|No|Nos|vs|Mr|Mrs|Ms|Dr|St|Jr|Sr))\.$")
_WHY = (("src", r"(?i)smaller\s+reporting"),  # why a found Item 1A is too short
        ("ref", r"(?i)incorporated\b.{0,80}\bby\s+reference|annual\s+report\s+to\s+(?:share|stock)holders"
                r"|exhibit\s+13"))
_Q = str.maketrans("‘’“”–—", "''\"\"--")


def to_text(raw):
    """10-K primary document (HTML or plain text) -> text, one block per line."""
    s = raw.decode("utf-8", "replace")
    if re.search(r"(?i)<(?:html|body|div|p)\b", s):
        s = re.sub(r"\s+", " ", _HIDDEN.sub(" ", s))  # source newlines are just spaces in HTML
        s = html.unescape(re.sub(r"<[^>]*>", "", _CELL.sub(" ", _BLOCK.sub("\n", s))))
    else:  # rare ASCII 10-K: re-flow hard-wrapped lines, blank lines separate paragraphs
        s = re.sub(r"(?<=\S)[ \t]*\r?\n(?=[ \t]*\S)", " ", s)
    s = unicodedata.normalize("NFKC", re.sub(r"[\u200b-\u200f\ufeff]", "", s))  # ligatures, odd spaces
    return "\n".join(x for x in (" ".join(line.split()) for line in s.splitlines()) if x)


def section(text):
    """Item 1A text: the longest span from a header to the first end line after it (TOC spans are short).
    Tried in order: 'Item 1A Risk Factors', bare 'Item 1A' (page headers) -> Item 1B/1C/2 line; then, for
    annual-report-style 10-Ks (GE, C, MS, INTC), a 'Risk Factors' heading -> the section the TOC lists next."""
    tries = [(rx, _END) for rx in _START]
    toc = _TOC.search(text)
    if toc and not re.search(r"(?i)risk\s+factors|\.$", toc.group(1)):
        title = re.sub(r"[\s\d–-]+$", "", toc.group(1)).split()
        end = re.compile(r"(?im)^[ \t]*" + r"\s+".join(map(re.escape, title)) + _HEAD)
        if len(end.findall(text)) <= 12:  # TOC, heading, a few running headers; per-page furniture is far more
            tries.append((_RF, end))
    best = ""
    for start, end in tries:
        ends = [m.start() for m in end.finditer(text)]
        for m in start.finditer(text):
            e = next((e for e in ends if e > m.end()), 0)
            if e - m.start() > len(best):
                best = text[m.start():e]
        if len(best) >= MIN_CHARS:
            break
    return best


def sentences(sec):
    """Section -> {normalized sentence: original}; drops headings/page headers/numbers, keeps >= 6 words, dedupes."""
    ls = sec.splitlines()
    rep = Counter(x for x in ls if len(x) < 80)  # running page headers ("Alphabet Inc.") repeat on every page
    lines = [x for x in ls if not _FURN.fullmatch(x) and rep[x] < 3 and (len(x) >= 80 or _EOS.search(x))]
    body = "".join(x + ("\n" if _EOS.search(x) else " ") for x in lines)  # unpunctuated long line: page break
    out, cur = {}, ""
    for p in _STOP.split(body):
        cur = f"{cur} {p}" if cur else p.strip()
        if _ABBR.search(cur):  # "U.S." / "Inc." is not a sentence end
            continue
        if len(re.findall(r"[A-Za-z]+", cur)) >= 6:
            out.setdefault(" ".join(cur.lower().translate(_Q).split()), cur)
        cur = ""
    return out


def _close(s, pool):
    """Best (sentence, word set) in pool with char ratio >= 0.85 = difflib.get_close_matches(s, pool, 1, 0.85)
    minus autojunk (it wrecks ratios of 200+ char strings). Prefilters: the exact length bound, and word-set
    overlap >= 0.5 (0.85-close pairs measured >= 0.64) -> a full ~1500x1300 rewrite takes ~1 s, not minutes."""
    ch, n, ws, best, hit = SequenceMatcher(None, b=s, autojunk=False), len(s), set(s.split()), 0.85, None
    for p, wp in pool:
        if 2 * min(n, len(p)) >= best * (n + len(p)) and 2 * len(ws & wp) >= 0.5 * (len(ws) + len(wp)):
            ch.set_seq1(p)
            if ch.quick_ratio() >= best and (r := ch.ratio()) >= best:
                best, hit = r, p
    return hit


def _tenks(cik, rows):
    """Last two original 10-Ks. 'recent' holds >= 1 year, but for heavy filers (banks' 424B2 flood) the prior
    10-K may sit in an older submissions page -> try the pages nearest to one year before the latest."""
    pick = lambda rs: [r for r in rs if r["form"] == "10-K" and r["primaryDocument"]]  # pre-2001: no primary doc
    ks = pick(rows)[:2]
    if len(ks) == 1:
        want = dt.date.fromisoformat(ks[0]["filingDate"]) - dt.timedelta(365)
        gap = lambda f: max(dt.date.fromisoformat(f["filingFrom"]) - want, want - dt.date.fromisoformat(f["filingTo"]))
        for f in sorted((common.submissions(cik) or {}).get("filings", {}).get("files", []), key=gap)[:3]:
            page = common.get_json(f"https://data.sec.gov/submissions/{f['name']}") or {}
            ks += pick(common.filings({"filings": {"recent": page}}))[:1]
            if len(ks) == 2:
                break
    return ks


def analyze(cik, rows):
    """Last two 10-Ks -> Item 1A sentence-diff metrics, or 'problem' saying why there are none."""
    ks = _tenks(cik, rows)
    res = {"dates": [r["filingDate"] for r in ks]}
    if len(ks) < 2:
        return {**res, "problem": "count"}
    secs, sents = [], []
    for r in ks:
        sec = section(to_text(common.fetch(common.doc_url(cik, r["accessionNumber"], r["primaryDocument"])) or b""))
        sents.append(sentences(sec) if len(sec) >= MIN_CHARS else {})
        print(f"tenk: Item 1A = {len(sec)} chars, {len(sents[-1])} sentences")  # public log: no company ids
        if not sents[-1]:
            why = next((k for k, rx in _WHY if re.search(rx, sec)), "short" if sec else "missing")
            return {**res, "problem": why, "which": r["filingDate"]}
        secs.append(sec)
    cur, pri = sents
    pool = [(s, set(s.split())) for s in pri if s not in cur]  # prior sentences without a verbatim successor
    edited, new, matched = 0, [], set()
    for s in (s for s in cur if s not in pri):
        m = _close(s, pool)
        if m:
            edited += 1
            matched.add(m)
        else:
            new.append(s)
    hits = sorted((-len(kw), i, kw, cur[s]) for i, s in enumerate(new)
                  for kw in [[k for k, rx in _KW.items() if rx.search(s)]] if kw)
    return {**res, "n_cur": len(cur), "n_pri": len(pri), "same": len(cur) - edited - len(new), "edited": edited,
            "new": len(new), "removed": len(pool) - len(matched), "chars": [len(s) for s in secs],
            "words": [len(re.findall(r"[A-Za-z]+", s)) for s in secs], "hits": [h[2:] for h in hits[:5]]}


def format_he(res):
    """analyze() result -> Hebrew RTL-safe lines."""
    out, d, p = ["<b>שינויים בגורמי הסיכון (Item 1A)</b>"], res["dates"], res.get("problem")
    if p == "count":
        return out + [f"נמצא רק דוח {code('10-K')} אחד (מ־{code(d[0])}); נדרשים שניים להשוואה." if d else
                      f"לא נמצאו דוחות שנתיים {code('10-K')} (למשל חברה זרה המגישה {code('20-F')} או ישות חדשה)."]
    out.append(f"השוואת {code('10-K')} מ־{code(d[0])} מול {code(d[1])}")
    if p:
        return out + [{"src": "בדוח מ־{} נכתב שכחברה מדווחת קטנה (smaller reporting company) היא פטורה מפרק זה.",
                       "ref": "בדוח מ־{} הפרק מופנה לדוח השנתי לבעלי המניות (נספח {}), ולכן לא הושווה.",
                       "short": "בדוח מ־{} פרק גורמי הסיכון קצר מדי להשוואה (ייתכן שלא נכלל).",
                       "missing": "לא אותר פרק גורמי הסיכון בדוח מ־{}."}[p].format(code(res["which"]), code("EX-13"))]
    pri, same = res["n_pri"], res["same"]
    grow, kept = f"{res['words'][0] / res['words'][1] - 1:+.0%}", f"{same / pri:.0%}"
    out += [f"משפטים: {code(res['n_cur'])} השנה מול {code(pri)} אשתקד · אורך הפרק {code(grow)} (מילים)",
            f"נשמרו מילה במילה: {code(kept)} ({code(same)} מתוך {code(pri)} משפטי אשתקד)",
            f"נערכו קלות: {code(res['edited'])} · חדשים לגמרי: {code(res['new'])} · הוסרו: {code(res['removed'])}",
            "💡 לפי מחקר Lazy Prices, שינוי נרחב בנוסח הדוח הוא סימן אזהרה."]
    if res["new"]:
        out.append("משפטים חדשים עם מילות סיכון:" if res["hits"] else "אין מילות סיכון מהרשימה במשפטים החדשים.")
    for kw, t in res["hits"]:  # <= 2 labels and ~150 chars each keep the section under ~1400 visible chars
        t = t if len(t) <= 150 else t[:147].rsplit(" ", 1)[0].rstrip(",;:") + "…"
        label = ", ".join(kw[:2]) + (f" +{len(kw) - 2}" if len(kw) > 2 else "")
        out.append(f"• חדש ({code(label)}): <i>{esc(t)}</i>")
    return out
