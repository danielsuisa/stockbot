"""Technical metrics (docs/screener/metric-spec.md): pure functions over ascending daily series, the last element the
latest bar (possibly today's in-progress one). Lists of floats in, float / str / bool / None out; None when there is
not enough data, a needed input is None, or a denominator is not positive. Results are fractions (0.05 = 5%) unless
noted."""
WINDOW = 250  # RSI / ATR look-back in bars


def _bad(*xs):
    return any(x is None for x in xs)


def sma(closes, n):
    """Mean of the last n closes; None with fewer than n."""
    if closes is None or n <= 0 or len(closes) < n or _bad(*closes[-n:]):
        return None
    return sum(closes[-n:]) / n


def rsi(closes, n=14, window=WINDOW):
    """Wilder RSI over at most the last `window` closes: averages of gains / losses seeded with the simple mean of the
    first n changes, then avg = (prev * (n - 1) + cur) / n. 100 when avg loss is 0 (50 when both are 0); None with
    fewer than n + 1 closes."""
    if closes is None:
        return None
    c = closes[-window:]
    if len(c) < n + 1 or _bad(*c):
        return None
    d = [b - a for a, b in zip(c, c[1:])]
    gain = sum(max(x, 0.0) for x in d[:n]) / n
    loss = sum(max(-x, 0.0) for x in d[:n]) / n
    for x in d[n:]:
        gain = (gain * (n - 1) + max(x, 0.0)) / n
        loss = (loss * (n - 1) + max(-x, 0.0)) / n
    if loss == 0:
        return 50.0 if gain == 0 else 100.0
    return 100.0 - 100.0 / (1.0 + gain / loss)


def atr(highs, lows, closes, n=14, alpha=None):
    """Wilder ATR over at most the last 250 bars: TR_i = max(h_i, c_{i-1}) - min(l_i, c_{i-1}) for i >= 1, seeded with
    the simple mean of the first n TRs, then atr += alpha * (TR - atr), alpha = 1/n (2/(n+1) via `alpha`). None with
    fewer than n + 1 bars or unequal series."""
    if _bad(highs, lows, closes) or not len(highs) == len(lows) == len(closes):
        return None
    h, l, c = highs[-WINDOW:], lows[-WINDOW:], closes[-WINDOW:]
    if len(c) < n + 1 or _bad(*h, *l, *c):
        return None
    a = 1.0 / n if alpha is None else alpha
    tr = [max(h[i], c[i - 1]) - min(l[i], c[i - 1]) for i in range(1, len(c))]
    out = sum(tr[:n]) / n
    for x in tr[n:]:
        out += a * (x - out)
    return out


def volatility(highs, lows, closes, n, denom="low"):
    """Mean over the last n bars of (high - low) / low (denom "low") or / close (denom "close"). None with fewer than
    n bars or a denominator <= 0."""
    if _bad(highs, lows, closes) or min(len(highs), len(lows), len(closes)) < n or n <= 0:
        return None
    h, l, c = highs[-n:], lows[-n:], closes[-n:]
    if _bad(*h, *l, *c):
        return None
    base = l if denom == "low" else c
    if min(base) <= 0:
        return None
    return sum((hi - lo) / b for hi, lo, b in zip(h, l, base)) / n


def perf(closes, n):
    """closes[-1] / closes[-1-n] - 1; None with n or fewer closes or a base <= 0."""
    if closes is None or len(closes) <= n or _bad(closes[-1], closes[-1 - n]) or closes[-1 - n] <= 0:
        return None
    return closes[-1] / closes[-1 - n] - 1


def perf_ytd(dates, closes):
    """closes[-1] / the last close of an earlier calendar year than dates[-1] (ISO 'YYYY-MM-DD') - 1; None without
    such a bar or with a base <= 0."""
    if _bad(dates, closes) or not dates or len(dates) != len(closes) or _bad(dates[-1], closes[-1]):
        return None
    year = dates[-1][:4]
    for d, c in zip(reversed(dates), reversed(closes)):
        if d is not None and d[:4] < year:
            return None if c is None or c <= 0 else closes[-1] / c - 1
    return None


def from_high(price, highs, n):
    """price / max(last n highs) - 1 (<= 0 normally); None with fewer than n bars or a max <= 0."""
    if _bad(price, highs) or n <= 0 or len(highs) < n or _bad(*highs[-n:]):
        return None
    top = max(highs[-n:])
    return None if top <= 0 else price / top - 1


def from_low(price, lows, n):
    """price / min(last n lows) - 1; None with fewer than n bars or a min <= 0."""
    if _bad(price, lows) or n <= 0 or len(lows) < n or _bad(*lows[-n:]):
        return None
    bottom = min(lows[-n:])
    return None if bottom <= 0 else price / bottom - 1


def new_high(highs, n):
    """highs[-1] >= max of the previous n - 1 highs; None with fewer than n (or n < 2)."""
    if highs is None or n < 2 or len(highs) < n or _bad(*highs[-n:]):
        return None
    return highs[-1] >= max(highs[-n:-1])


def new_low(lows, n):
    """lows[-1] <= min of the previous n - 1 lows; None with fewer than n (or n < 2)."""
    if lows is None or n < 2 or len(lows) < n or _bad(*lows[-n:]):
        return None
    return lows[-1] <= min(lows[-n:-1])


def cross(a_prev, a_now, b_prev, b_now):
    """"above" when a went from <= b to > b, "below" when from >= b to < b, else None."""
    if _bad(a_prev, a_now, b_prev, b_now):
        return None
    if a_prev <= b_prev and a_now > b_now:
        return "above"
    if a_prev >= b_prev and a_now < b_now:
        return "below"
    return None


def price_cross_sma(closes, n):
    """cross(close yesterday, close today, SMA(n) yesterday, SMA(n) today); None with fewer than n + 1 closes."""
    if closes is None or len(closes) < n + 1:
        return None
    return cross(closes[-2], closes[-1], sma(closes[:-1], n), sma(closes, n))


def sma_cross(closes, n_a, n_b):
    """cross(SMA(n_a) yesterday, today, SMA(n_b) yesterday, today); None with fewer than max(n_a, n_b) + 1 closes."""
    if closes is None or len(closes) < max(n_a, n_b) + 1:
        return None
    y = closes[:-1]
    return cross(sma(y, n_a), sma(closes, n_a), sma(y, n_b), sma(closes, n_b))


def _returns(c):
    return [b / a - 1 for a, b in zip(c, c[1:])]


def beta(stock_closes, index_closes, n=60, min_n=24):
    """OLS slope cov(r_s, r_i) / var(r_i) (population divisor for both) of the last n monthly returns (down to
    min_n), r = c_t / c_{t-1} - 1 on closes the caller aligned. None with fewer than min_n returns, unequal series,
    a close <= 0 or var == 0."""
    if _bad(stock_closes, index_closes) or len(stock_closes) != len(index_closes):
        return None
    s, i = stock_closes[-(n + 1):], index_closes[-(n + 1):]
    if len(s) - 1 < min_n or _bad(*s, *i) or min(s[:-1]) <= 0 or min(i[:-1]) <= 0:
        return None
    rs, ri = _returns(s), _returns(i)
    k = len(ri)
    ms, mi = sum(rs) / k, sum(ri) / k
    var = sum((x - mi) ** 2 for x in ri) / k
    if var <= 0:
        return None
    return sum((x - ms) * (y - mi) for x, y in zip(rs, ri)) / k / var


def _declined(prior_closes):
    # a 5-session decline before today
    p = list(prior_closes or ())
    return len(p) >= 6 and not _bad(p[-1], p[-6]) and p[-1] < p[-6]


def candlestick(o, h, l, c, prior_closes=()):
    """Today's candle -> frozenset of pattern codes (textbook rules, body = |c - o|, rng = h - l, upper / lower the
    shadows): d doji, dd dragonfly, gd gravestone, mw / mb marubozu, stw / stb spinning top, h hammer and ih inverted
    hammer (both after a 5-session decline of prior_closes, the closes before today), lls / lus long lower / upper
    shadow. Empty when rng <= 0; None when an input is None."""
    if _bad(o, h, l, c):
        return None
    rng = h - l
    if rng <= 0:
        return frozenset()
    body, upper, lower = abs(c - o), h - max(o, c), min(o, c) - l
    doji = body <= 0.1 * rng
    out = set()
    if doji:
        out.add("d")
        if upper <= 0.1 * rng and lower >= 0.6 * rng:
            out.add("dd")
        if lower <= 0.1 * rng and upper >= 0.6 * rng:
            out.add("gd")
    if body >= 0.95 * rng:
        out.add("mw" if c > o else "mb")  # body > 0 here, so c != o
    if body <= 0.3 * rng and upper >= body and lower >= body and not doji:
        out.add("stw" if c > o else "stb")
    if body > 0 and _declined(prior_closes):
        if lower >= 2 * body and upper <= 0.1 * rng:
            out.add("h")
        if upper >= 2 * body and lower <= 0.1 * rng:
            out.add("ih")
    if lower >= 0.6 * rng:
        out.add("lls")
    if upper >= 0.6 * rng:
        out.add("lus")
    return frozenset(out)


def ssr(prev_close, low, prev2_close=None, prev_low=None):
    """Rule 201 approximation: low <= 0.9 x prev_close (triggered today), or prev_low <= 0.9 x prev2_close (carried
    over from yesterday). None when prev_close or low is None."""
    if _bad(prev_close, low):
        return None
    if low <= 0.9 * prev_close:
        return True
    return bool(prev2_close is not None and prev_low is not None and prev_low <= 0.9 * prev2_close)


def day_range(h, l):
    """(h - l) / l (the Most Volatile signal); None when l <= 0."""
    if _bad(h, l) or l <= 0:
        return None
    return (h - l) / l
