"""Pre-FOMC windows and the CPI/PPI gap book. Pure functions. No orders.

The account, the commission, and the 1 percentage point bar are the T7/T8 lock in
docs/backtest/TRIALS.md. This module does not place or transmit an order.
"""
import math
import re

ACCOUNT = 460.0
HALF = 0.0001
FEE_PER_SHARE = 0.005
FEE_MIN = 1.0
FEE_CAP = 0.01
GAP = -0.005
N_EFF = 9
OOS_MIN = 40

MONTHS = {
    "January": 1, "February": 2, "March": 3, "April": 4, "May": 5, "June": 6,
    "July": 7, "August": 8, "September": 9, "October": 10, "November": 11, "December": 12,
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "Jun": 6, "Jul": 7, "Aug": 8,
    "Sep": 9, "Sept": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}


def announcement_day(month_text, first, second, year):
    """The announcement calendar day named by a Fed heading.

    A slash means the meeting crosses months, and the last day falls in the
    second month. A single month whose last day number is below its first day
    number also crosses into the next month: June 30-1 is July 1.
    """
    parts = month_text.split("/")
    start = MONTHS.get(parts[0])
    end = MONTHS.get(parts[-1])
    if start is None or end is None:
        return None
    if second is None:
        month, day, out_year = start, first, year
    elif len(parts) > 1:
        month, day, out_year = end, second, year
    elif second < first:
        month, day, out_year = start + 1, second, year
        if month == 13:
            month, out_year = 1, year + 1
    else:
        month, day, out_year = start, second, year
    return f"{out_year}-{month:02d}-{day:02d}"


def order_fee(shares, price):
    """IBKR Pro fixed commission, with the $1 floor and the 1% cap."""
    notional = abs(shares) * price
    if notional <= 0:
        return 0.0
    raw = max(FEE_MIN, FEE_PER_SHARE * abs(shares))
    return min(raw, FEE_CAP * notional)


def shares_for_cash(cash, price):
    """Fractional shares that keep cash at or above zero after the fee and the buy spread."""
    if cash <= 0 or price <= 0:
        return 0.0, 0.0, cash
    fill = price * (1 + HALF)
    lo, hi = 0.0, cash / fill
    for _ in range(60):
        mid = (lo + hi) / 2
        if mid * fill + order_fee(mid, price) <= cash:
            lo = mid
        else:
            hi = mid
    fee = order_fee(lo, price)
    return lo, fee, cash - (lo * fill + fee)


def sell_proceeds(shares, price):
    """Cash raised by a sale, after the sell spread and the commission."""
    if shares <= 0 or price <= 0:
        return 0.0, 0.0
    fee = order_fee(shares, price)
    return shares * price * (1 - HALF) - fee, fee


def meeting_dates(html):
    """Announcement dates from a Fed historical year page or the current calendars page.

    A Meeting heading counts. A conference call, a notation vote, and a cancelled
    meeting do not. The date is the last day named in the heading.
    """
    found = []
    for match in re.finditer(r"<h5[^>]*>(.*?)</h5>", html, re.S | re.I):
        text = re.sub(r"\s+", " ", re.sub("<[^>]+>", " ", match.group(1))).strip()
        low = text.lower()
        if "cancelled" in low or "conference call" in low or "notation vote" in low:
            continue
        if not re.search(r"\bMeeting\b", text):
            continue
        hit = re.search(
            r"([A-Za-z]+(?:/[A-Za-z]+)?)\s+(\d{1,2})(?:\s*[-–]\s*(\d{1,2}))?",
            text,
        )
        year = re.search(r"(20\d{2}|19\d{2})", text)
        if not hit or not year:
            continue
        second = int(hit.group(3)) if hit.group(3) else None
        day = announcement_day(hit.group(1), int(hit.group(2)), second, int(year.group(1)))
        if day:
            found.append(day)
    parts = re.split(r"<h4>\s*<a[^>]*>\s*(\d{4}) FOMC Meetings\s*</a>", html)
    for index in range(1, len(parts), 2):
        year = parts[index]
        body = parts[index + 1]
        for block in re.split(r"fomc-meeting__month", body)[1:]:
            month = re.search(r"<strong>([A-Za-z]+(?:/[A-Za-z]+)?)</strong>", block)
            date = re.search(r"fomc-meeting__date[^>]*>\s*([^<]+)", block)
            if not month or not date:
                continue
            numbers = [int(piece) for piece in re.findall(r"\d+", date.group(1))]
            if not numbers:
                continue
            second = numbers[-1] if len(numbers) > 1 else None
            first = numbers[0]
            day = announcement_day(month.group(1), first, second, int(year))
            if day:
                found.append(day)
    return sorted(set(found))


def bls_releases(html):
    """`(date, kind)` rows. Kind is CPI or PPI. The name has to match exactly."""
    rows = re.findall(
        r'class="date-cell"><p>([^<]+)</p></td>\s*'
        r'<td class="time-cell"><p>(.*?)</p></td>\s*'
        r'<td class="desc-cell"><p><strong>([^<]+)</strong>',
        html,
        re.S,
    )
    found = []
    for date_text, _time_text, name in rows:
        if name not in ("Consumer Price Index", "Producer Price Index"):
            continue
        hit = re.search(
            r"(January|February|March|April|May|June|July|August|September|"
            r"October|November|December)\s+(\d{1,2}),\s+(\d{4})",
            date_text,
        )
        if not hit:
            continue
        kind = "CPI" if name.startswith("Consumer") else "PPI"
        found.append((f"{hit.group(3)}-{MONTHS[hit.group(1)]:02d}-{int(hit.group(2)):02d}", kind))
    return found


def announcement_session(announcement, sessions):
    """The announcement session, or the next SPY session when the Fed date is closed."""
    for day in sessions:
        if day >= announcement:
            return day
    return None


def fomc_events(sessions, announcements, variant):
    """One event per announcement whose entry and exit both land on `sessions`.

    Variant A holds the session before the announcement. Variant C also holds the
    announcement session. The buy is the prior close, so the held sessions are the
    ones whose close-to-close move belongs to SPY.
    """
    if variant not in ("A", "C"):
        raise ValueError(variant)
    index = {day: pos for pos, day in enumerate(sessions)}
    events = []
    for announcement in announcements:
        session = announcement_session(announcement, sessions)
        if session is None:
            continue
        pos = index[session]
        if pos < 2:
            continue
        entry = sessions[pos - 2]
        if variant == "A":
            exit_day = sessions[pos - 1]
            spy_days = (sessions[pos - 1],)
        else:
            exit_day = sessions[pos]
            spy_days = (sessions[pos - 1], sessions[pos])
        events.append({
            "announcement": announcement,
            "session": session,
            "entry": entry,
            "exit": exit_day,
            "spy_days": spy_days,
        })
    return events


def sleeve_name(day, bil_first, sgov_first):
    """BIL before 2020. SGOV from 2020 once it prints. Cash accrual before BIL."""
    if sgov_first and day >= "2020-01-01" and day >= sgov_first:
        return "SGOV"
    if bil_first and day >= bil_first:
        return "BIL"
    return "IRX"


def _mark(book, day, prices):
    if book["kind"] in (None, "IRX"):
        return book["cash"]
    price = prices[book["kind"]].get(day)
    if price is None:
        return None
    return book["cash"] + book["shares"] * price


def _leave(book, day, price, realizations, fees):
    """Sell the current holding at `price`. IRX ignores `price` and realizes accrued interest."""
    if book["kind"] in (None, "IRX"):
        if book["interest"]:
            realizations.append((day, book["interest"]))
            book["interest"] = 0.0
        book["kind"] = None
        return
    if book["shares"] <= 0 or price is None:
        book["kind"] = None
        book["shares"] = 0.0
        book["basis"] = 0.0
        return
    proceeds, fee = sell_proceeds(book["shares"], price)
    realizations.append((day, proceeds - book["basis"]))
    book["cash"] += proceeds
    fees[0] += fee
    book["shares"] = 0.0
    book["basis"] = 0.0
    book["kind"] = None


def _enter(book, kind, price, fees):
    if kind == "IRX":
        book["kind"] = "IRX"
        return
    shares, fee, left = shares_for_cash(book["cash"], price)
    spent = book["cash"] - left
    book["cash"] = left
    book["shares"] = shares
    book["basis"] = spent
    book["kind"] = kind
    fees[0] += fee


def run_close_book(days, spy_days, prices, irx, bil_first, sgov_first):
    """Close-to-close book. `spy_days` earn SPY. Every other session earns the sleeve.

    The switch is at the prior close. The last close liquidates. The equity curve is
    pre-tax. Realizations feed the year-end tax, which is applied outside this path.
    """
    spy_days = set(spy_days)
    book = {"kind": None, "shares": 0.0, "basis": 0.0, "cash": ACCOUNT, "interest": 0.0}
    realizations = []
    fees = [0.0]
    trades = []
    entry_price = None

    def target(index):
        if index >= len(days) - 1:
            return None
        if days[index + 1] in spy_days:
            return "SPY"
        return sleeve_name(days[index], bil_first, sgov_first)

    def goto(index, kind, price_override=None):
        nonlocal entry_price
        if book["kind"] == kind:
            return
        leaving = book["kind"]
        if leaving == "SPY":
            trades.append({
                "exit": days[index],
                "entry_price": entry_price,
                "exit_price": price_override if price_override is not None else prices["SPY"][days[index]],
                "pnl": None,
            })
        price = price_override
        if price is None and leaving not in (None, "IRX"):
            price = prices[leaving].get(days[index])
        before = len(realizations)
        _leave(book, days[index], price, realizations, fees)
        if leaving == "SPY" and len(realizations) > before:
            trades[-1]["pnl"] = realizations[-1][1]
        if kind is None:
            return
        enter_price = price_override if kind == "SPY" and price_override is not None else None
        if enter_price is None and kind != "IRX":
            enter_price = prices[kind][days[index]]
        if kind == "SPY":
            entry_price = enter_price
        _enter(book, kind, enter_price if kind != "IRX" else None, fees)

    goto(0, target(0))
    curve = [(days[0], _mark(book, days[0], prices))]
    for index in range(1, len(days)):
        prev, day = days[index - 1], days[index]
        if book["kind"] == "IRX":
            if book["interest"] and day[:4] != prev[:4]:
                realizations.append((prev, book["interest"]))
                book["interest"] = 0.0
            yield_pct = irx.get(prev)
            if yield_pct is not None:
                grown = book["cash"] * (yield_pct / 100) / 252
                book["cash"] += grown
                book["interest"] += grown
        if index == len(days) - 1:
            goto(index, None)
        else:
            goto(index, target(index))
        curve.append((day, _mark(book, day, prices)))
    returns = []
    for (day0, equity0), (day1, equity1) in zip(curve, curve[1:]):
        if equity0 is None or equity1 is None or equity0 <= 0:
            continue
        returns.append((day1, equity1 / equity0 - 1))
    return {
        "curve": curve,
        "returns": returns,
        "terminal": curve[-1][1],
        "fees": fees[0],
        "realizations": realizations,
        "trades": [row for row in trades if row["pnl"] is not None],
    }


def beats(strategy_cagr, spy_cagr, margin=0.01):
    """True when the strategy's CAGR clears SPY by at least `margin`."""
    if strategy_cagr is None or spy_cagr is None:
        return False
    return strategy_cagr >= spy_cagr + margin - 1e-12


def release_days(rows):
    """One session per calendar day, even when CPI and PPI share it."""
    return sorted({day for day, _kind in rows})


def gap_signals(days, opens, closes, releases, vix=None, vix_filter=False):
    """Release sessions whose open is at least 0.5% under the prior close.

    With `vix_filter`, the gap also has to be strictly below the prior VIX1D close
    divided by 100 and by sqrt(252). A session with no prior VIX1D close is skipped.
    """
    release = set(releases)
    signals = []
    prev = None
    for day in days:
        if prev is not None and day in release and prev in closes and day in opens:
            gap = opens[day] / closes[prev] - 1
            implied = None
            if vix is not None and prev in vix:
                implied = (vix[prev] / 100) / math.sqrt(252)
            take = gap <= GAP
            if vix_filter:
                take = take and implied is not None and gap < -implied
            if take:
                signals.append({"day": day, "prev": prev, "gap": gap, "implied": implied})
        prev = day
    return signals


def run_open_fade(days, signals, prices, spy_open, spy_close, irx, bil_first, sgov_first):
    """Buy the official open and sell the close. The sleeve does not accrue that morning.

    This is the public-data description. It is not a 9:35 fill.
    """
    wanted = {row["day"]: row for row in signals}
    book = {"kind": None, "shares": 0.0, "basis": 0.0, "cash": ACCOUNT, "interest": 0.0}
    realizations = []
    fees = [0.0]
    trades = []
    _enter(book, sleeve_name(days[0], bil_first, sgov_first), _sleeve_price(days[0], prices, bil_first, sgov_first), fees)
    curve = [(days[0], _mark(book, days[0], prices))]
    for index in range(1, len(days)):
        prev, day = days[index - 1], days[index]
        if day in wanted:
            _flatten(book, prev, prices, realizations, fees)
            _enter(book, "SPY", spy_open[day], fees)
            entry_basis = book["basis"]
            _leave(book, day, spy_close[day], realizations, fees)
            trades.append({
                "day": day,
                "gap": wanted[day]["gap"],
                "implied": wanted[day]["implied"],
                "open": spy_open[day],
                "close": spy_close[day],
                "pnl": realizations[-1][1] if realizations else None,
                "gross": spy_close[day] / spy_open[day] - 1,
                "basis": entry_basis,
            })
            if index != len(days) - 1:
                kind = sleeve_name(day, bil_first, sgov_first)
                _enter(book, kind, _sleeve_price(day, prices, bil_first, sgov_first), fees)
        else:
            _hold_sleeve(book, prev, day, prices, irx, bil_first, sgov_first, realizations, fees)
            if index == len(days) - 1:
                _flatten(book, day, prices, realizations, fees)
        curve.append((day, _mark(book, day, prices)))
    returns = []
    for (_day0, equity0), (day1, equity1) in zip(curve, curve[1:]):
        if equity0 and equity1 and equity0 > 0:
            returns.append((day1, equity1 / equity0 - 1))
    return {
        "curve": curve,
        "returns": returns,
        "terminal": curve[-1][1],
        "fees": fees[0],
        "realizations": realizations,
        "trades": trades,
    }


def _sleeve_price(day, prices, bil_first, sgov_first):
    kind = sleeve_name(day, bil_first, sgov_first)
    if kind == "IRX":
        return None
    return prices[kind][day]


def _flatten(book, day, prices, realizations, fees):
    price = None if book["kind"] in (None, "IRX") else prices[book["kind"]].get(day)
    _leave(book, day, price, realizations, fees)


def _hold_sleeve(book, prev, day, prices, irx, bil_first, sgov_first, realizations, fees):
    """Accrue the sleeve from the prior close to this close, and switch tickers when it changes."""
    if book["kind"] == "IRX":
        if book["interest"] and day[:4] != prev[:4]:
            realizations.append((prev, book["interest"]))
            book["interest"] = 0.0
        yield_pct = irx.get(prev)
        if yield_pct is not None:
            grown = book["cash"] * (yield_pct / 100) / 252
            book["cash"] += grown
            book["interest"] += grown
    want = sleeve_name(day, bil_first, sgov_first)
    if book["kind"] != want:
        _flatten(book, day, prices, realizations, fees)
        _enter(book, want, _sleeve_price(day, prices, bil_first, sgov_first), fees)
