"""Shared plumbing: SEC-compliant HTTP, Telegram, ticker map, Hebrew/RTL formatting."""
import datetime as dt
import functools
import gzip
import hashlib
import html
import http.client
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DISCLAIMER = "⚠️ מידע לצורכי מחקר בלבד ואינו ייעוץ השקעות. מקור הנתונים: SEC EDGAR."

if hasattr(sys.stdout, "reconfigure"):  # Hebrew/emoji on any console
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def env(name, default=""):
    """Env var cast to the default's type; unset/empty -> default (unset repo Variables are harmless)."""
    v = os.environ.get(name, "").strip()
    return type(default)(v) if v else default


# ---------- HTTP ----------
_lock, _next = threading.Lock(), [0.0]
_gap = 1 / min(8.0, env("SEC_RPS", 8.0))  # SEC fair access: never above 8 req/s (hard limit is 10)


def fetch(url, data=None, headers=None, tries=5, timeout=60):
    """URL -> bytes, or None on 404 ("not published"). Exponential backoff on 429/5xx/network (+403 on SEC)."""
    sec = urllib.parse.urlsplit(url).netloc.endswith("sec.gov")
    cache = env("HTTP_CACHE") and sec and data is None and Path(env("HTTP_CACHE"), hashlib.sha1(url.encode()).hexdigest())
    if cache and cache.exists():  # dev-only disk cache (HTTP_CACHE=dir)
        return cache.read_bytes() or None
    h = {"Accept-Encoding": "gzip", "User-Agent": "Mozilla/5.0"}
    if sec:
        h["User-Agent"] = env("SEC_UA")
        if not h["User-Agent"]:
            sys.exit("SEC_UA is not set (e.g. 'InvestorBot you@example.com') - SEC requires a contact User-Agent")
    h.update(headers or {})
    for i in range(tries):
        if sec:
            with _lock:
                time.sleep(max(0.0, _next[0] - time.monotonic()))
                _next[0] = time.monotonic() + _gap
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data, h), timeout=timeout) as r:
                body = r.read()
                body = gzip.decompress(body) if r.headers.get("Content-Encoding") == "gzip" else body
            break
        except urllib.error.HTTPError as e:
            # daily-index files live on S3, which answers a missing file with 403 AccessDenied (XML), not 404
            if e.code == 404 or (sec and e.code == 403 and "xml" in (e.headers.get("Content-Type") or "")):
                body = None
                break
            if not (e.code in (429, 500, 502, 503, 504) or (sec and e.code == 403)) or i == tries - 1:
                raise
        except (OSError, http.client.HTTPException):
            if i == tries - 1:
                raise
        time.sleep(2 ** i)
    if cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(body or b"")
    if sec and body is not None:
        stamp("sec")
    return body


STAMPS = {}  # "sec" / "price" -> when this process last got live data (or the cached price's date)


def stamp(kind, value=None):
    STAMPS[kind] = value or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def get_json(url):
    body = fetch(url)
    return None if body is None else json.loads(body)


# ---------- EDGAR ----------
@functools.lru_cache(256)
def submissions(cik):
    return get_json(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json")


def filings(sub):
    """Columnar submissions['filings']['recent'] -> list of per-filing dicts, newest first."""
    r = sub["filings"]["recent"]
    return [dict(zip(r, row)) for row in zip(*r.values())]


def doc_url(cik, acc, name=""):
    """Archive URL of a filing document; without name -> the filing's index page."""
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc.replace('-', '')}/{name or acc + '-index.htm'}"


@functools.lru_cache(None)
def tickers():
    """{'AAPL': (320193, 'Apple Inc.'), ...} from SEC company_tickers.json."""
    d = get_json("https://www.sec.gov/files/company_tickers.json") or {}
    return {v["ticker"].upper(): (int(v["cik_str"]), v["title"]) for v in d.values()}


def noncommon(t, same=()):
    """Preferred/warrant/unit/right symbol: BA-PA, XYZ-WT, ABCD-U, or SWAGW when SWAG is in `same`."""
    return bool(re.search(r"-[PWUR]", t)) or (len(t) == 5 and t[-1] in "WUR" and t[:-1] in same)


@functools.lru_cache(None)
def cik_tickers():
    """{cik: ticker}: the first common-stock symbol in file (market-value) order - the file sometimes lists
    a warrant before its stock (SWAGW before SWAG); a CIK with no common symbol keeps its first one."""
    by = {}
    for t, (cik, _) in tickers().items():
        by.setdefault(cik, []).append(t)
    return {cik: next((t for t in ts if not noncommon(t, ts)), ts[0]) for cik, ts in by.items()}


# ---------- Telegram ----------
def tg(method, **params):
    """Call a Telegram Bot API method with TG_TOKEN -> result; waits out 429 'retry_after' flood limits."""
    url = f"https://api.telegram.org/bot{env('TG_TOKEN')}/{method}"
    for i in range(4):
        try:
            body = fetch(url, json.dumps(params).encode(), {"Content-Type": "application/json"}, tries=2)
            break
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise RuntimeError("Telegram rejected TG_TOKEN (401 Unauthorized) - check the secret") from e
            if e.code != 429 or i == 3:
                raise
            try:
                raw = e.read()
                wait = json.loads(gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw)["parameters"]["retry_after"]
            except Exception:
                wait = 5
            time.sleep(wait + 1)
    if body is None:
        raise RuntimeError("Telegram returned 404 - TG_TOKEN is wrong")
    return json.loads(body)["result"]


def visible(s):
    """Telegram's length measure: text after HTML parsing, in UTF-16 code units."""
    return len(html.unescape(re.sub(r"<[^>]+>", "", s)).encode("utf-16-le")) // 2


def chunks(text, limit):
    """Split on line boundaries (tags never span lines) into parts of <= limit visible units."""
    out, cur, n = [], [], 0
    for line in text.split("\n"):
        k = visible(line) + 1
        if cur and n + k > limit:
            out.append("\n".join(cur))
            cur, n = [], 0
        cur.append(line)
        n += k
    return out + ["\n".join(cur)] if cur else out


_sent = [0.0]
CHECK_PROMPT = "🤔 לפני פעולה: מה חייב להיות נכון בעולם כדי שהם יצדקו?"


def freshness():
    """The data-freshness line every message ends with (before the disclaimer)."""
    return f"🕒 נתונים נכון ל: SEC {code(STAMPS.get('sec', '—'))}, מחיר {code(STAMPS.get('price', '—'))}"


def send(text, chat_id=None, signal=False):
    """Send HTML text to Telegram, split on lines; every message ends with [the manual-check prompt when it carries
    a signal], the data-freshness line and the disclaimer. Prints instead when TG_TOKEN is unset (dry run)."""
    chat = chat_id or env("TG_CHAT_ID")
    if not (env("TG_TOKEN") and chat) and env("GITHUB_ACTIONS"):  # a misnamed secret must fail the run loudly
        sys.exit("TG_TOKEN / TG_CHAT_ID secret is missing or misnamed - nothing was sent")
    tail = "\n".join(([CHECK_PROMPT] if signal else []) + [freshness(), "", DISCLAIMER])
    for part in chunks(text.strip(), 4000 - visible(tail)):
        part = f"{part}\n\n{tail}"
        if not (env("TG_TOKEN") and chat):
            print(part, end="\n\n")
            continue
        time.sleep(max(0.0, _sent[0] + 1.1 - time.monotonic()))  # Telegram: ~1 message/second per chat
        try:
            tg("sendMessage", chat_id=chat, text=part, parse_mode="HTML", link_preview_options={"is_disabled": True})
        except urllib.error.HTTPError as e:
            if e.code != 400:
                raise
            tg("sendMessage", chat_id=chat, text=html.unescape(re.sub(r"<[^>]+>", "", part)))  # bad HTML -> plain
        _sent[0] = time.monotonic()


def log(event, **fields):
    """One structured (JSON) log line for the Actions log."""
    print(json.dumps({"event": event, "at": dt.datetime.now(dt.timezone.utc).strftime("%H:%M:%S"), **fields},
                     ensure_ascii=False, default=str), flush=True)


def summary(markdown):
    """Append to the GitHub Actions job summary (no-op outside Actions)."""
    if env("GITHUB_STEP_SUMMARY"):
        with open(env("GITHUB_STEP_SUMMARY"), "a", encoding="utf-8") as fh:
            fh.write(markdown.rstrip() + "\n")


def dispatch(workflow, inputs):
    """Start one of this repo's workflows with the run's GITHUB_TOKEN (needs actions: write) -> "" or the error."""
    repo, token = env("GITHUB_REPOSITORY"), env("GITHUB_TOKEN")
    if not (repo and token):
        return "no GITHUB_TOKEN"
    try:
        body = fetch(f"https://api.github.com/repos/{repo}/actions/workflows/{workflow}/dispatches",
                     json.dumps({"ref": env("GITHUB_REF_NAME", "main"), "inputs": inputs}).encode(),
                     {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                      "Content-Type": "application/json"}, tries=3)
        return "HTTP 404" if body is None else ""
    except urllib.error.HTTPError as e:
        return f"HTTP {e.code}"
    except (OSError, http.client.HTTPException) as e:  # timeout / connection reset: not an "SEC is down" story
        return f"network: {type(e).__name__}"


def runs(workflow, completed=False):
    """The latest (completed) run of one of this repo's workflows -> {created_at, conclusion, event, status} or None.
    Display only (/status, /health): one short attempt."""
    repo, token = env("GITHUB_REPOSITORY"), env("GITHUB_TOKEN")
    if not repo:
        return None
    try:
        body = fetch(f"https://api.github.com/repos/{repo}/actions/workflows/{workflow}/runs?per_page=1"
                     + ("&status=completed" if completed else ""),
                     headers={"Accept": "application/vnd.github+json", **({"Authorization": f"Bearer {token}"} if token else {})},
                     tries=1, timeout=15)
        r = (json.loads(body or b"{}").get("workflow_runs") or [None])[0]
        return r and {k: r[k] for k in ("created_at", "conclusion", "event", "status")}
    except (OSError, ValueError, http.client.HTTPException):
        return None


def failure(e):
    """Hebrew reason for a failed report or scan (fetch() exits with a message naming SEC_UA when it is missing)."""
    if isinstance(e, SystemExit) and "SEC_UA" in str(e.code):
        return f"הסוד {code('SEC_UA')} לא הוגדר ב־GitHub (ראו שלב 3 ב־README)."
    return "ייתכן ש־SEC לא זמין כרגע. נסו שוב מאוחר יותר."


# ---------- Hebrew / RTL formatting ----------
def esc(s):
    return html.escape(str(s), quote=False)


def code(x):
    """Wrap tickers, XBRL tags and numbers-with-symbols so they don't break the RTL line."""
    return f"<code>{esc(x)}</code>"


def money(x):
    """1234567 -> '1.2M$' (the single currency style used in every message); None -> 'חסר'."""
    if x is None:
        return "חסר"
    a, sign = abs(x), "-" if x < 0 else ""
    for div, unit in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if round(a / div, 1) >= 1:
            return f"{sign}{a / div:.1f}{unit}$"
    return f"{sign}{a:.0f}$"


def price(p):
    return "חסר" if p is None else f"{p:,.2f}$" if p >= 0.01 else f"{p:.4g}$"


def rtl_bad_lines(text):
    """Lines whose first strong character (letter/digit) is not Hebrew - bidi hygiene self-check."""
    bad = []
    for line in html.unescape(re.sub(r"<[^>]+>", "", text)).splitlines():
        m = re.search(r"[A-Za-z0-9א-ת]", line)
        if m and not "א" <= m.group() <= "ת":
            bad.append(line)
    return bad
