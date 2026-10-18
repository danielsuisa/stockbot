"""Deterministic replay (P1 Stage 2, ADR 0002 section 4).

The same consumer code runs on a HistoricalSource (the store, read only through data_access) or a
LiveSource (scalp_engine.live_source); only the stream source differs. Events are emitted in the total
order (available_at, priority, symbol, feed, seq):
  - session markers at their scheduled calendar times;
  - corporate actions at their event_at (ex/effective date 00:00 ET);
  - 1-minute bars at bar open + 60 s + bar_publish_delay_ms;
  - quote samples at sample time + quote_sample_delay_ms.
seq is the position after a sort that also covers the event payload, so the order is total and does
not depend on file order, thread scheduling or PYTHONHASHSEED.

Isolation: this module and everything it imports read no wall clock and open no network connection
(tests/test_replay.py checks both statically and at run time).
"""
import abc
import argparse
import datetime as dt
import hashlib
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import polars as pl

from scalp_engine.contracts import load_flat_yaml
from scalp_engine.data_access import day_bounds, quote_view, read_frame

CONFIG = Path(__file__).resolve().parent / "config" / "replay.yaml"
PRIORITY = {"session": 0, "corporate_action": 1, "bar": 2, "quote_sample": 3}
_EPOCH = dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)
_BAR_KEY = ["source", "feed", "symbol", "event_at"]
_BAR_FIELDS = ["open", "high", "low", "close", "volume", "vwap", "trade_count"]
_QUOTE_FIELDS = ["status", "quote_at", "bid_price", "bid_size", "ask_price", "ask_size", "bid_exchange",
                 "ask_exchange", "conditions", "tape", "size_unit", "lookback_ms", "sample_rule"]
_ACTION_FIELDS = ["action_type", "new_symbol", "ratio_new", "ratio_old", "cash_amount", "vendor_id"]


def load_config(path=CONFIG):
    cfg = load_flat_yaml(path)
    if cfg.get("schema_version") != 1:
        raise ValueError(f"{path}: unsupported schema_version")
    return {"bar_delay": dt.timedelta(milliseconds=cfg["bar_publish_delay_ms"]),
            "quote_delay": dt.timedelta(milliseconds=cfg["quote_sample_delay_ms"])}


def us(t):
    """Aware datetime -> integer microseconds since the epoch (exact)."""
    d = t - _EPOCH
    return (d.days * 86_400 + d.seconds) * 1_000_000 + d.microseconds


def _plain(v):
    if isinstance(v, dt.datetime):
        return us(v)
    if isinstance(v, dt.date):
        return v.isoformat()
    if isinstance(v, list):
        return [_plain(x) for x in v]
    return v  # str, int, float (json uses repr: shortest round-trip, deterministic), bool, None


@dataclass(frozen=True)
class Event:
    available_at: int  # microseconds since the epoch, UTC
    priority: int
    symbol: str
    feed: str
    seq: int
    kind: str
    payload: str  # canonical JSON of the event's market fields

    def canonical(self):
        return json.dumps([self.available_at, self.priority, self.symbol, self.feed, self.seq, self.kind,
                           self.payload], separators=(",", ":"), ensure_ascii=True)


def order(raw):
    """raw: iterable of (available_at_us, kind, symbol, feed, payload_dict) -> sorted list of Event."""
    keyed = []
    for avail, kind, symbol, feed, payload in raw:
        body = json.dumps({k: _plain(v) for k, v in payload.items()}, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True, allow_nan=False)
        keyed.append((avail, PRIORITY[kind], symbol, feed, kind, body))
    keyed.sort()
    return [Event(a, p, s, f, i, k, b) for i, (a, p, s, f, k, b) in enumerate(keyed)]


def stream_hash(events):
    """SHA-256 over the canonical serialization of an ordered event stream (one line per event)."""
    h = hashlib.sha256()
    n = 0
    for e in events:
        h.update(e.canonical().encode("ascii"))
        h.update(b"\n")
        n += 1
    return h.hexdigest(), n


class StreamSource(abc.ABC):
    @abc.abstractmethod
    def events(self, day):
        """Ordered list of Event for one ET trading day."""


class HistoricalSource(StreamSource):
    """Replays one trading day from the store. as_of (aware datetime, optional) ignores rows ingested after
    it and keeps the latest ingestion of each key, so later re-ingestion cannot change a past replay."""

    def __init__(self, root, config=None, as_of=None, feeds=("sip", "iex")):
        self.root = Path(root)
        self.cfg = config or load_config()
        self.as_of = as_of
        self.feeds = tuple(feeds)

    def _read(self, table, day, require):
        lo, hi = day_bounds(day)
        df = read_frame(self.root / table, lo, hi, require_dates=[day] if require else None)
        if df.height and self.as_of is not None:
            df = df.filter(pl.col("ingested_at") <= self.as_of)
        return df

    @staticmethod
    def _latest(df, key):
        if df.height == 0:
            return df
        return df.sort(key + ["ingested_at"]).unique(subset=key, keep="last", maintain_order=True)

    def events(self, day):
        raw = []
        cal = self._latest(self._read("calendar", day, True), ["trading_date"])
        if cal.height != 1:
            raise LookupError(f"{day} is not a trading session in the calendar")
        c = cal.row(0, named=True)
        for kind_name in ("session_open_at", "open_at", "close_at", "session_close_at"):
            raw.append((us(c[kind_name]), "session", "", "none",
                        {"marker": kind_name, "at": c[kind_name], "early_close": c["early_close"]}))
        acts = self._latest(self._read("corporate_actions", day, False), ["source", "vendor_id", "action_type", "symbol"])
        for r in acts.iter_rows(named=True):
            raw.append((us(r["event_at"]), "corporate_action", r["symbol"], "none",
                        {"event_at": r["event_at"], **{k: r[k] for k in _ACTION_FIELDS}}))
        bars = self._latest(self._read("raw_bars_1m", day, True), _BAR_KEY)
        if bars.height:
            bars = bars.filter(pl.col("feed").is_in(list(self.feeds)))
        delay = dt.timedelta(minutes=1) + self.cfg["bar_delay"]
        for r in bars.select(["event_at", "symbol", "feed"] + _BAR_FIELDS).iter_rows(named=True):
            raw.append((us(r["event_at"] + delay), "bar", r["symbol"], r["feed"],
                        {"event_at": r["event_at"], **{k: r[k] for k in _BAR_FIELDS}}))
        quotes = self._latest(quote_view(self._read("raw_quote_samples", day, False)), _BAR_KEY)
        for r in quotes.iter_rows(named=True):
            raw.append((us(r["event_at"] + self.cfg["quote_delay"]), "quote_sample", r["symbol"], r["feed"],
                        {"event_at": r["event_at"], **{k: r[k] for k in _QUOTE_FIELDS}}))
        return order(raw)


def day_hash(root, day, as_of=None):
    return stream_hash(HistoricalSource(root, as_of=as_of).events(day))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m scalp_engine.replay")
    ap.add_argument("day", type=dt.date.fromisoformat)
    ap.add_argument("--root", default=os.environ.get("SCALP_DATA_ROOT") or r"D:\scalp-data")
    a = ap.parse_args(argv)
    digest, n = day_hash(a.root, a.day)
    print(json.dumps({"day": a.day.isoformat(), "events": n, "sha256": digest}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
