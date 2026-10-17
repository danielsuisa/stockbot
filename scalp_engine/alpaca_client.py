"""Read-only Alpaca REST client for L0 ingestion (R1: market data and reference endpoints only).

- Keys come from ALPACA_KEY_ID / ALPACA_SECRET_KEY (stockbot's names) and travel only in headers.
- TLS: stdlib urllib with the default verifying context (the OS trust store on Windows). Never disabled.
- Every market-data request names its UTC event range and passes data_access.check_range first,
  so no request can cover a sealed holdout window.
- A shared token bucket keeps the account under its 200 requests/min; 429 and 5xx back off.
- Every page is recorded in the table's manifest: endpoint, params, status, rows, body sha256.
"""
import datetime as dt
import hashlib
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from scalp_engine import storage
from scalp_engine.data_access import check_range

DATA = "https://data.alpaca.markets"
TRADING = "https://paper-api.alpaca.markets"
_ALLOWED = ("/v2/stocks/bars", "/v2/stocks/quotes", "/v1/corporate-actions", "/v2/assets", "/v2/calendar")


class CredentialsMissing(RuntimeError):
    pass


class ApiError(RuntimeError):
    def __init__(self, status, detail):
        super().__init__(f"HTTP {status}: {detail}")
        self.status, self.detail = status, detail


class RateLimiter:
    """Token bucket: at most `per_minute` acquisitions in any 60 s window, shared by all threads."""

    def __init__(self, per_minute):
        self.gap = 60.0 / per_minute
        self.next = 0.0
        self.lock = threading.Lock()

    def acquire(self):
        with self.lock:
            now = time.monotonic()
            wait = max(0.0, self.next - now)
            self.next = max(now, self.next) + self.gap
        if wait:
            time.sleep(wait)


def request_id(path, params):
    canon = json.dumps({"path": path, "params": params}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode()).hexdigest()


class Client:
    def __init__(self, per_minute=170, timeout=120):
        kid, secret = os.environ.get("ALPACA_KEY_ID", "").strip(), os.environ.get("ALPACA_SECRET_KEY", "").strip()
        if not (kid and secret):
            raise CredentialsMissing("ALPACA_KEY_ID / ALPACA_SECRET_KEY are not set")
        self._headers = {"APCA-API-KEY-ID": kid, "APCA-API-SECRET-KEY": secret, "Accept": "application/json"}
        self.limiter = RateLimiter(per_minute)
        self.timeout = timeout
        self.stats = {"requests": 0, "bytes": 0, "retries": 0}
        self._stats_lock = threading.Lock()

    def get(self, base, path, params, *, table, event_range, tries=8):
        """GET base+path?params -> (parsed JSON, request id). event_range = (start, end) in UTC, or
        None for reference endpoints with no event time (assets)."""
        if path not in _ALLOWED:
            raise ValueError(f"endpoint not allowed (read-only market data): {path}")
        if event_range is not None:
            check_range(*event_range)
        params = {k: v for k, v in params.items() if v is not None}
        rid = request_id(path, params)
        url = f"{base}{path}?{urllib.parse.urlencode(params)}"
        for attempt in range(tries):
            self.limiter.acquire()
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=self._headers),
                                            timeout=self.timeout) as r:
                    body = r.read()
                    status = r.status
                break
            except urllib.error.HTTPError as e:
                status = e.code
                detail = e.read()[:300]
                if status in (429, 500, 502, 503, 504) and attempt < tries - 1:
                    with self._stats_lock:
                        self.stats["retries"] += 1
                    time.sleep(min(60, 2 ** attempt))
                    continue
                text = detail.decode("utf-8", "replace")
                storage.append_manifest(table, {"kind": "request", "table": table, "path": path, "params": params,
                                                "request_id": rid, "status": status, "error": text})
                raise ApiError(status, text) from e
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                if attempt < tries - 1:
                    with self._stats_lock:
                        self.stats["retries"] += 1
                    time.sleep(min(60, 2 ** attempt))
                    continue
                raise
        data = json.loads(body)
        with self._stats_lock:
            self.stats["requests"] += 1
            self.stats["bytes"] += len(body)
        storage.append_manifest(table, {
            "kind": "request", "table": table, "path": path, "params": params, "request_id": rid,
            "status": status, "rows": _count(data), "bytes": len(body),
            "body_sha256": hashlib.sha256(body).hexdigest(),
            "received_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        })
        return data, rid

    def pages(self, path, params, key, *, table, event_range):
        """Follow next_page_token. Yields (symbol -> rows dict, request id) per page."""
        token = None
        while True:
            data, rid = self.get(DATA, path, dict(params, page_token=token), table=table, event_range=event_range)
            yield data.get(key) or {}, rid
            token = data.get("next_page_token")
            if not token:
                return


def _count(data):
    if isinstance(data, list):
        return len(data)
    for key in ("bars", "quotes", "corporate_actions"):
        if isinstance(data.get(key), dict):
            return sum(len(v) for v in data[key].values())
    return None
