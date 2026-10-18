"""Account-level simulation (ADR 0005 section 8): one cash account, settled cash only (T+1), no margin or shorts,
at most 3 positions plus pending orders, signals at the same decision time ranked by p then symbol, and a daily loss
limit of 2% of start-of-session equity on realized P&L.

Each accepted recommendation is simulated with fills.simulate at the shares the account can afford; its cash flows
are timed events: reserve at decision, pay at fill (or release at expiry), proceeds at exit settle at the next
session. Sessions are fed in order (CashAccount.on_session), so bars need not be kept across sessions."""
import datetime as dt
import heapq
from dataclasses import dataclass

from scalp_eval.costs import order_cost
from scalp_eval.fills import simulate

MAX_OPEN = 3
DAILY_LOSS = 0.02
_END = dt.datetime.max.replace(tzinfo=dt.timezone.utc)


@dataclass
class Signal:
    t: object
    day: object
    symbol: str
    p: float
    limit: float
    stop: float
    target: float
    ttl_s: int
    horizon: int
    bars: tuple
    close_at: object
    half_spread: float
    half_spread_delayed: float


class CashAccount:
    def __init__(self, equity0, sessions, plan="tiered"):
        self.plan = plan
        self.next_session = {d: sessions[i + 1] if i + 1 < len(sessions) else None for i, d in enumerate(sessions)}
        self.settled, self.unsettled, self.open_cost, self.reserved = float(equity0), [], 0.0, 0.0
        self.events, self.seq, self.active = [], 0, 0
        self.trades = []
        self.blocked = {"concurrency": 0, "daily_loss": 0, "zero_shares": 0, "cash": 0}

    def equity(self):
        return self.settled + sum(a for _, a in self.unsettled) + self.open_cost

    def _settle(self, day):
        keep = []
        for d, a in self.unsettled:
            if d <= day:
                self.settled += a
            else:
                keep.append((d, a))
        self.unsettled = keep

    def _process_until(self, t, realized):
        while self.events and self.events[0][0] <= t:
            _, _, kind, x = heapq.heappop(self.events)
            if kind == "fill":
                self.reserved -= x["reserve"]
                self.settled -= x["buy_cash"]
                self.open_cost += x["buy_cash"]
            elif kind == "release":
                self.reserved -= x["reserve"]
                self.active -= 1
            elif kind == "exit":
                self.open_cost -= x["buy_cash"]
                self.unsettled.append((x["settle_day"], x["proceeds"]))
                self.active -= 1
                realized[0] += x["pnl"]

    def on_session(self, day, signals):
        """Process one session's signals (all with .day == day); every order resolves within the session."""
        self._settle(day)
        start, realized = self.equity(), [0.0]
        by_t = {}
        for s in signals:
            by_t.setdefault(s.t, []).append(s)
        for t in sorted(by_t):
            self._process_until(t, realized)
            for s in sorted(by_t[t], key=lambda s: (-s.p, s.symbol)):
                if realized[0] <= -DAILY_LOSS * start:
                    self.blocked["daily_loss"] += 1
                    continue
                if self.active >= MAX_OPEN:
                    self.blocked["concurrency"] += 1
                    continue
                avail = self.settled - self.reserved
                q = int(min(0.01 * self.equity() / (s.limit - s.stop), avail / s.limit) // 1) if s.limit > s.stop else 0
                while q > 0 and q * s.limit + order_cost(self.plan, "buy", q, s.limit, day) > avail:
                    q -= 1
                if q <= 0:
                    self.blocked["cash" if avail < s.limit else "zero_shares"] += 1
                    continue
                tr = simulate(s.t, s.limit, s.stop, s.target, s.ttl_s, s.horizon, q, s.bars, s.close_at,
                              s.half_spread, s.half_spread_delayed, day)
                buy_cash = q * s.limit + order_cost(self.plan, "buy", q, s.limit, day)
                self.reserved += buy_cash
                self.active += 1
                self.seq += 1
                self.trades.append((s, tr))
                if tr.status != "filled":
                    heapq.heappush(self.events, (tr.expires_at, self.seq, "release", {"reserve": buy_cash}))
                    continue
                proceeds = q * tr.exit_price - order_cost(self.plan, "sell", q, tr.exit_price, day)
                heapq.heappush(self.events, (tr.fill_at, self.seq, "fill", {"reserve": buy_cash, "buy_cash": buy_cash}))
                self.seq += 1
                heapq.heappush(self.events, (tr.exit_at, self.seq, "exit", {
                    "buy_cash": buy_cash, "proceeds": proceeds, "pnl": proceeds - buy_cash,
                    "settle_day": self.next_session.get(day) or day}))
        self._process_until(_END, realized)

    def final_equity(self):
        return self.settled + sum(a for _, a in self.unsettled) + self.open_cost


def run_account(signals, sessions, equity0, plan="tiered"):
    """Convenience wrapper: feeds sessions in order. Returns (trades, final equity, blocked counts)."""
    acc = CashAccount(equity0, sessions, plan)
    by_day = {}
    for s in signals:
        by_day.setdefault(s.day, []).append(s)
    for d in sessions:
        acc.on_session(d, by_day.get(d, []))
    return acc.trades, acc.final_equity(), acc.blocked
