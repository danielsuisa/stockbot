"""ZBA v2, STEP 0 follow-up: the unit of SIP quote sizes over the years, and the pages of pre-open news a session."""
import json

from bot import alpaca, common

NEWS = "https://data.alpaca.markets/v1beta1/news"


def sizes(sym, at):
    js = alpaca._get(f"{alpaca.DATA}/{sym}/quotes", {"start": at, "limit": 200, "feed": "sip"})
    q = js.get("quotes") or []
    return {"n": len(q), "bs": sorted({x.get("bs") for x in q})[:12], "as": sorted({x.get("as") for x in q})[:12]}


def news_pages(prev, day):
    pages, items, token, syms = 0, 0, None, set()
    while True:
        q = {"start": alpaca.utc(prev, "16:00"), "end": alpaca.utc(day, "09:35"), "limit": 50, "sort": "asc",
             **({"page_token": token} if token else {})}
        js = alpaca._get(NEWS, q)
        pages += 1
        got = js.get("news") or []
        items += len(got)
        for x in got:
            syms |= set(x.get("symbols") or [])
        token = js.get("next_page_token")
        if not token or pages >= 60:
            return {"pages": pages, "items": items, "symbols": len(syms)}


def main():
    out = {"sizes": {}, "news": {}}
    for at in ("2016-03-01T15:00:00Z", "2019-03-01T15:00:00Z", "2022-03-01T15:00:00Z", "2024-03-01T15:00:00Z",
               "2025-03-03T15:00:00Z", "2026-09-01T15:00:00Z"):
        out["sizes"][at[:10]] = {s: sizes(s, at) for s in ("AAPL", "F")}
    for prev, day in (("2016-03-14", "2016-03-15"), ("2019-09-13", "2019-09-16"), ("2022-03-14", "2022-03-15"),
                      ("2025-09-12", "2025-09-15")):
        out["news"][day] = news_pages(prev, day)
    text = json.dumps(out, indent=1)
    print(text)
    common.summary("```json\n" + text + "\n```")


if __name__ == "__main__":
    main()
