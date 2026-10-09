"""The owner's rules for every Telegram text (shared by the message tests): Hebrew first on every line, no "+"/"-"
glued to a number (it flips in a right-to-left line: write the sign as a word or an arrow), Israeli dates, Israel time,
no R units."""
import html
import re

from bot import common

SIGN_BEFORE = re.compile(r"(?<![\w.])[+\-−]\d")  # "-3.0%", "+2.2%"
SIGN_AFTER = re.compile(r"\d[%$]?[\-−](?![\w\d])")  # "1.21R-" as it shows after the flip
ISO = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
R_UNIT = re.compile(r"\d(\.\d+)?R\b")


def visible(text):
    """What Telegram shows: tags (and with them link targets) removed, entities unescaped."""
    return html.unescape(re.sub(r"<[^>]+>", "", text))


def problems(text):
    v = visible(text)
    out = [f"not Hebrew first: {line!r}" for line in common.rtl_bad_lines(text)]
    out += [f"{name}: {m.group()!r} in {v[max(0, m.start() - 30):m.end() + 30]!r}"
            for name, rx in (("sign before a number", SIGN_BEFORE), ("sign after a number", SIGN_AFTER),
                             ("ISO date", ISO), ("R unit", R_UNIT)) for m in rx.finditer(v)]
    if "UTC" in v:
        out.append("UTC time (use Israel time)")
    return out


def check(test, text):
    """Fail `test` with every rule `text` breaks."""
    test.assertEqual(problems(text), [], text)
