"""'Lazy Prices': sentence-level diff of Item 1A (Risk Factors) between the last two 10-Ks, ranked by what
matters: hedges that turned into facts ('may' -> 'has'), risk words that appeared or vanished, minus boilerplate."""
import bisect
import datetime as dt
import html
import re
import unicodedata
from collections import Counter
from difflib import SequenceMatcher

from bot import common
from bot.common import code, esc

MIN_CHARS = 2000  # shorter "sections" are TOC lines or "not required for smaller reporting companies"
TOP, SNIP, BUDGET = 5, 110, 1600  # changes listed, chars quoted per sentence, visible chars in the whole section
_SEC = r"(?:sec|securities\s+and\s+exchange\s+commission)"
KEYWORDS = {  # label -> (ranking weight, pattern for inflected forms minus boilerplate that is not a risk signal:
    # "amended and restated certificate", clinical "investigator/investigational", "by default"/"default encryption")
    "going concern": (10, r"going[\s-]+concern"),
    "material weakness": (9, r"material\s+weakness"),
    "restatement": (8, r"(?<!amended and )restate(?:ments?|[sd]|ing)?\b"
                       r"(?!\s+(?:certificate|bylaws|by-laws|articles|memorandum|charter|credit|agreement))"),
    "delisting": (7, r"de-?list|minimum\s+bid\s+price|continued\s+listing\s+(?:requirement|standard|rule|criteri)"),
    "SEC inquiry/subpoena": (7, r"subpoena|wells\s+notice|" + _SEC + r"(?:'s)?\s+(?:[\w-]+\s+){0,4}?"
                                r"(?:inquir|investigat|subpoena|enforcement)|(?:inquir(?:y|ies)|investigations?|"
                                r"enforcement\s+actions?|comment\s+letters?|requests?\s+for\s+information)\s+"
                                r"(?:[\w-]+\s+){0,4}?(?:by|from|of|with)\s+(?:the\s+)?(?:staff\s+of\s+the\s+)?"
                                + _SEC + r"\b"),
    "covenant": (5, r"covenant"),
    "customer concentration": (5, r"customer\s+concentration|concentrat\w*\s+(?:of|in|among)\s+(?:our\s+)?"
                                  r"(?:customers|customer\s+base)|(?:limited|small)\s+number\s+of\s+(?:[\w-]+\s+)?"
                                  r"customers|(?<!no )customers?\s+(?:[\w-]+\s+){0,2}?(?:accounted\s+for|represented)"
                                  r"\s+(?:approximately\s+|about\s+|more\s+than\s+|over\s+)?(?:\d|a\s+(?:significant|"
                                  r"substantial|large|majority))|(?:depend\w*|relian\w*|rel(?:y|ies|ying))\s+"
                                  r"(?:\w+\s+)?(?:on|upon)\s+(?:a\s+)?(?:few|small\s+number\s+of|limited\s+number\s+of|"
                                  r"single|one|our\s+largest|major|key|significant|large)\s+(?:[\w-]+\s+)?"
                                  r"customers?\b"),
    "liquidity": (4, r"liquidity"),
    # the pre-existing list, weighted lower: common in boilerplate, still worth a line when nothing heavier moved
    "default": (3, r"default(?:s|ed|ing)?\b(?<!by default)(?!\s+(?:end-to-end|encryption|settings?|options?|mode))"),
    "impairment": (2, r"impairment"), "investigation": (2, r"investigat(?:ions?|e[sd]?|ing|ive)\b"),
    "sanction": (2, r"sanction"), "litigation": (1, r"litigat"), "tariff": (1, r"tariff")}
_KW = {k: re.compile(r"\b(?:" + v + ")", re.I) for k, (_, v) in KEYWORDS.items()}
_W = {k: w for k, (w, _) in KEYWORDS.items()}
HEDGE, FIRM = {"may", "might", "could", "possibly", "potentially"}, {"has", "have", "had", "is", "are", "was", "were",
                                                                     "will", "did", "does"}
W_HEDGE, W_ADDED, W_REMOVED, NOVEL = 8, 6, 1.5, 1.5  # 'may'->'has', 'has been and may'; x removal, x new to section
_PREP = {"in", "of", "on", "by", "to", "from", "since", "until", "through", "during", "before", "after", "early", "mid",
         "late", "and", "or", "last", "next", "this", "each"}  # words before the month 'May'
_MONTHS = "january february march april may june july august september october november december"
_MASK = re.compile(r"\b(?:" + "|".join(_MONTHS.split()) + r"|(?:twen|thir|for|fif|six|seven|eigh|nine)ty|zero|one|two|"
                   r"three|four|five|six|seven|eight|nine|ten|eleven|twelve|hundred|thousand|[mb]illion)\b|\d")
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
    """(best sentence in pool of (sentence, word set) with char ratio >= 0.85, its ratio) = difflib's
    get_close_matches(s, pool, 1, 0.85) minus autojunk (it wrecks ratios of 200+ char strings). Prefilters: the
    exact length bound, and word-set overlap >= 0.5 (0.85-close pairs measured >= 0.64) -> a full ~1500x1300
    rewrite takes ~1 s, not minutes. No match -> (None, 0)."""
    ch, n, ws, best, hit = SequenceMatcher(None, b=s, autojunk=False), len(s), set(s.split()), 0.85, None
    for p, wp in pool:
        if 2 * min(n, len(p)) >= best * (n + len(p)) and 2 * len(ws & wp) >= 0.5 * (len(ws) + len(wp)):
            ch.set_seq1(p)
            if ch.quick_ratio() >= best and (r := ch.ratio()) >= best:
                best, hit = r, p
    return (hit, best) if hit else (None, 0)


def _labels(t):
    """Sentence -> {keyword label: span of its first match}, heaviest first ('SEC inquiry' absorbs 'investigation')."""
    t = t.translate(_Q)
    found = {k: m.span() for k, rx in _KW.items() if (m := rx.search(t))}
    if "SEC inquiry/subpoena" in found:
        found.pop("investigation", None)
    return dict(sorted(found.items(), key=lambda kv: -_W[kv[0]]))


def _kw_score(labels, known):
    """Heaviest label + half of the rest; x NOVEL for a label the other year's whole section never used."""
    ws = sorted((_W[k] * (1 if k in known else NOVEL) for k in labels), reverse=True)
    return ws[0] + sum(ws[1:]) / 2 if ws else 0


def _mask(s):
    """Normalized sentence with dates, years, amounts and number words blanked (recurring-clause test)."""
    return re.sub(r"(?:#[\s,.%$]*)+", "# ", _MASK.sub("#", s))


def _words(t):
    """Sentence -> [(start, end, bare lower-case word)] for a word-level diff that keeps original positions."""
    return [(m.start(), m.end(), re.sub(r"^\W+|\W+$", "", m.group().lower().translate(_Q)))
            for m in re.finditer(r"\S+", t)]


def _span(ws, i, j, n):
    """Char span of words ws[i:j] in their sentence (an empty range -> the point where they would be)."""
    if i < j:
        return ws[i][0], ws[j - 1][1]
    p = ws[i][0] if i < len(ws) else n
    return p, p


def _is_hedge(t, ws, k):
    """Word k is a hedge - not the month in 'May 2024' / 'in May, the FDA' (title-case 'That May Result' is)."""
    w = ws[k][2]
    if w != "may":
        return w in HEDGE
    month = k + 1 < len(ws) and ws[k + 1][2][:1].isdigit() or k and t[ws[k][0]] == "M" and ws[k - 1][2] in _PREP
    return not month


def _shifts(before, a, b, ops):
    """Hedge -> firm shifts in an edited pair: [(label, weight, opcode)]. A small replaced span whose old words hold
    a hedge and whose new words hold a firm verb and no hedge ('may be' -> 'is': 'may→is'), or firm words added
    right before a kept hedge ('may' -> 'has been, and may': '+has been')."""
    out = []
    for op in ops:
        tag, i1, i2, j1, j2 = op
        new = [w for *_, w in b[j1:j2]]
        firm = next((w for w in new if w in FIRM), None)
        if tag == "equal" or not firm or i2 - i1 > 5 or j2 - j1 > 5 or HEDGE & set(new):
            continue
        hedge = next((a[k][2] for k in range(i1, i2) if _is_hedge(before, a, k)), None)
        if tag == "replace" and hedge:
            out.append((f"{hedge}→{firm}", W_HEDGE, op))
        elif i2 < len(a) and _is_hedge(before, a, i2) and firm in {"has", "have", "had", "was", "were", "did"} \
                and {"and", "or"} & set(new):
            added = re.split(r" (?:and|or)\b", " ".join(new[new.index(firm):]))[0]  # 'has in the past'
            out.append(("+" + " ".join(added.split()[:4]), W_ADDED, op))
    return out


def _pair(before, after, lb, la, known):
    """Edited pair (prior, current sentence + their labels) -> material change: hedge -> firm shifts and keywords
    that appeared in it (x NOVEL if new to the whole section), or None when neither happened."""
    a, b = _words(before), _words(after)
    ops = SequenceMatcher(None, [w for *_, w in a], [w for *_, w in b], autojunk=False).get_opcodes()
    shifts, added = _shifts(before, a, b, ops)[:2], [k for k in la if k not in lb]
    if not (shifts or added):
        return None
    kept = [_W[k] for k in la if k in lb]  # a hedge that firmed up inside a covenant/going-concern sentence
    score = sum(w for _, w, _ in shifts) + _kw_score(added, known) + (max(kept) / 2 if shifts and kept else 0)
    if shifts:
        _, i1, i2, j1, j2 = shifts[0][2]
    else:  # centre on the diff op nearest to the first new keyword
        j = max(0, bisect.bisect_right([w[0] for w in b], la[added[0]][0]) - 1)
        _, i1, i2, j1, j2 = min((op for op in ops if op[0] != "equal"), default=("equal", 0, 0, j, j + 1),
                                key=lambda op: max(op[3] - j, j - op[4] + 1, 0))
    return {"kind": "hedge" if shifts else "kw", "labels": [s[0] for s in shifts] + added, "score": score,
            "before": before, "after": after, "sb": _span(a, i1, i2, len(before)), "sa": _span(b, j1, j2, len(after))}


def _one(kind, text, labels, known):
    """New or removed sentence with keywords -> change (removals score x W_REMOVED: dropping a risk says more)."""
    gone, span = kind == "removed", next(iter(labels.values()))
    return {"kind": kind, "labels": list(labels), "score": _kw_score(labels, known) * (W_REMOVED if gone else 1),
            "before": text if gone else None, "after": None if gone else text, "sb": span, "sa": span}


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
    lab = [{s: _labels(o) for s, o in d.items()} for d in sents]
    has = [set().union(*d.values()) for d in lab]  # labels used anywhere in this year's / last year's section
    edited, boiler, new, matched, top = 0, 0, 0, set(), []
    for s in (s for s in cur if s not in pri):
        m, ratio = _close(s, pool)
        if not m:
            new += 1
            top += [_one("new", cur[s], lab[0][s], has[1])] if lab[0][s] else []
            continue
        edited += 1
        matched.add(m)
        c = _pair(pri[m], cur[s], lab[1][m], lab[0][s], has[1])
        if c:
            top.append(c)
        elif ratio >= 0.95 or _mask(m) == _mask(s):  # cosmetic, or only dates/numbers moved in a recurring clause
            boiler += 1
    gone = [p for p, _ in pool if p not in matched]
    top += [_one("removed", pri[p], lab[1][p], has[0]) for p in gone if lab[1][p]]
    groups = {}  # same kind + heaviest label = one line with a count ('removed: delisting' x 6 in a turnaround)
    for c in sorted(top, key=lambda c: -c["score"]):  # stable: ties keep document order (edits/new, then removals)
        groups.setdefault((c["kind"], c["labels"][0]), []).append(c)
    top = [{**g[0], "more": len(g) - 1} for g in groups.values()]
    return {**res, "n_cur": len(cur), "n_pri": len(pri), "same": len(cur) - edited - new, "edited": edited,
            "boiler": boiler, "new": new, "removed": len(gone), "chars": [len(s) for s in secs],
            "words": [len(re.findall(r"[A-Za-z]+", s)) for s in secs], "top": top[:TOP]}


def _snip(t, span, width):
    """~width chars of t centred on span (the changed words), cut at spaces; '…' marks each cut."""
    if len(t) <= width:
        return t
    a, b = span
    lo = max(0, min(a, (a + b - width) // 2))  # a change wider than the window starts at its first word
    hi = min(len(t), lo + width)
    lo = max(0, hi - width) if hi == len(t) else lo
    if lo and t[lo - 1] != " ":  # mid-word: skip to the next word, or back to this word's start if it is changed
        lo = sp + 1 if (sp := t.find(" ", lo, a)) >= 0 else t.rfind(" ", 0, lo) + 1
    if hi < len(t) and t[hi] != " " and (sp := t.rfind(" ", lo + 1 if b >= hi else max(lo + 1, b), hi)) > lo:
        hi = sp  # end on a word boundary, never inside a change that fits the window
    return ("…" if lo else "") + t[lo:hi].strip().rstrip(",;:") + ("…" if hi < len(t) else "")


KIND = {"hedge": "נוסח הוקשח", "kw": "נוספה מילת סיכון למשפט קיים", "new": "חדש", "removed": "הוסר"}


def _change(c, width):
    """One ranked change -> label line + 'before' (not for new) and 'after' (not for removed) quotes."""
    label = ", ".join(c["labels"][:2]) + (f" +{len(c['labels']) - 2}" if len(c["labels"]) > 2 else "")
    out = [f"• {KIND[c['kind']]} ({code(label)})" + (f" ועוד {code(c['more'])} כאלה:" if c.get("more") else ":")]
    if c["before"] is not None:
        out.append(f"לפני: <i>{esc(_snip(c['before'], c['sb'], width))}</i>")
    if c["after"] is not None:
        out.append(f"אחרי: <i>{esc(_snip(c['after'], c['sa'], width))}</i>")
    return out


def format_he(res):
    """analyze() result -> Hebrew RTL-safe lines (<= BUDGET visible chars: quotes shrink, then changes drop)."""
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
            f"נערכו קלות: {code(res['edited'])} (מתוכם {code(res['boiler'])} שגרתיים: תאריכים, מספרים, ניסוח זעיר)"
            f" · חדשים לגמרי: {code(res['new'])} · הוסרו: {code(res['removed'])}",
            "💡 לפי מחקר Lazy Prices, שינוי נרחב בנוסח הדוח הוא סימן אזהרה."]
    if not res["top"]:
        changed = res["edited"] + res["new"] + res["removed"]
        return out + ([f"לא נמצאו שינויים מהותיים: אין מילות סיכון מהרשימה במשפטים שנוספו, הוסרו או נערכו, ואף"
                       f" ניסוח זהיר ({code('may/could')}) לא הפך לקביעה."] if changed else [])
    out.append("השינויים המהותיים ביותר (הקשחת ניסוח, מילות סיכון שנוספו או הוסרו):")
    for n, width in ((n, w) for n in range(len(res["top"]), 0, -1) for w in (SNIP, 90, 70)):
        lines = out + [x for c in res["top"][:n] for x in _change(c, width)]  # widest quotes, then fewer changes
        if common.visible("\n".join(lines)) <= BUDGET:
            break
    return lines
