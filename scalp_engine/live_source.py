"""LiveSource skeleton for the Alpaca IEX stream (P1: no live connection; tested on synthetic fixtures).

The transport is injected: `batches` yields decoded stream messages, each a list of dicts as Alpaca's
websocket sends them ({"T": "b", "S": symbol, "o", "h", "l", "c", "v", "t", "n", "vw"} for minute
bars; control and other message types are ignored here). Events get the same available_at rule,
payload and ordering as HistoricalSource, so a live day and its replay serialize identically.
This module reads no clock and opens no connection; a real transport arrives in a later phase.
"""
import datetime as dt

from scalp_engine.data_access import ET
from scalp_engine.replay import StreamSource, load_config, order, us

_FIELDS = {"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume", "vw": "vwap", "n": "trade_count"}


def _ts(s):
    base, _, frac = s.rstrip("Z").partition(".")
    t = dt.datetime.fromisoformat(base).replace(tzinfo=dt.timezone.utc)
    return t.replace(microsecond=int((frac + "000000")[:6])) if frac else t


class LiveSource(StreamSource):
    def __init__(self, batches, config=None, feed="iex"):
        self.batches = batches
        self.cfg = config or load_config()
        self.feed = feed

    def events(self, day):
        """Minute bars of `day` (ET) from the injected stream, in replay order."""
        delay = dt.timedelta(minutes=1) + self.cfg["bar_delay"]
        raw = []
        for batch in self.batches:
            for m in batch:
                if m.get("T") != "b":
                    continue
                t = _ts(m["t"])
                if t.astimezone(dt.timezone(dt.timedelta(hours=-5))).date() != day and \
                        t.astimezone(dt.timezone(dt.timedelta(hours=-4))).date() != day:
                    continue
                payload = {"event_at": t}
                for k, name in _FIELDS.items():
                    v = m.get(k)
                    payload[name] = int(v) if name in ("volume", "trade_count") and v is not None else (
                        float(v) if v is not None else None)
                raw.append((us(t + delay), "bar", m["S"], self.feed, payload))
        return order(raw)
