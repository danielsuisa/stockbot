"""Phase C item F: a timing run of the blind run's engine at its scale, on SYNTHETIC series only (random walks; no
market data): about 1,800 universe symbols a day, 2,320 signal sessions, 37,000 firings. Prints the minutes of
issuing, pools and draws and the peak memory -- never a result.

    python -m tests.timing_ensemble [firing scale] [symbol scale]   (1.0 1.0 = the blind run's size)"""
import array
import datetime as dt
import random
import sys
import time

from bot import ensemble_backtest as bt, ensemble_data as ed, signals

N_SYMBOLS, N_SIGNAL_DAYS, N_FIRINGS, HISTORY = 1800, 2320, 37000, 252


def peak_mb():
    try:
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    except ImportError:  # Windows
        import ctypes
        import ctypes.wintypes as w

        class PMC(ctypes.Structure):
            _fields_ = [("cb", w.DWORD), ("PageFaultCount", w.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t)]
        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        k32, psapi = ctypes.windll.kernel32, ctypes.windll.psapi
        k32.GetCurrentProcess.restype = w.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [w.HANDLE, ctypes.POINTER(PMC), w.DWORD]
        psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb)
        return pmc.PeakWorkingSetSize / 2 ** 20


def weekdays(start, n):
    d, out = dt.date.fromisoformat(start), []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += dt.timedelta(1)
    return out


def synthetic_series(dates, rng):
    c, closes, highs, lows, opens = 50.0, [], [], [], []
    for _ in dates:
        o = c * (1 + rng.gauss(0, 0.005))
        c = max(1.0, c * (1 + rng.gauss(0, 0.02)))
        opens.append(o)
        closes.append(c)
        highs.append(max(o, c) * (1 + abs(rng.gauss(0, 0.01))))
        lows.append(min(o, c) * (1 - abs(rng.gauss(0, 0.01))))
    f = lambda v: array.array("d", v)  # noqa: E731
    vol = f(rng.uniform(5e5, 5e6) for _ in dates)
    return {"dates": list(dates), "raw_open": f(opens), "raw_high": f(highs), "raw_low": f(lows),
            "raw_close": f(closes), "raw_volume": vol, "close": f(closes), "volume": vol,
            "segment": array.array("i", [0] * len(dates))}


def main(scale=1.0, sym_scale=1.0):
    rng = random.Random(1)
    n_sym, n_fire = int(N_SYMBOLS * sym_scale), int(N_FIRINGS * scale)
    sessions = weekdays("2016-01-04", HISTORY + N_SIGNAL_DAYS + 85)
    sig_days = sessions[HISTORY:HISTORY + N_SIGNAL_DAYS]
    t0 = time.monotonic()
    series = {f"S{i:04d}": synthetic_series(sessions, rng) for i in range(n_sym)}
    inp = ed.make_inputs(sessions, {d: ("09:30", "16:00") for d in sessions}, [], [], {}, series, {})
    syms = tuple(sorted(series))
    dec = bytes(10 * k // len(syms) for k in range(len(syms)))
    day_members = {d: (syms, dec) for d in sig_days}
    fired = sorted({(rng.choice(sig_days), rng.choice(signals.SIGNALS), rng.choice(syms)) for _ in range(n_fire)})
    quote = lambda sym, at: (10.0, 10.01, 100, 100, 3)  # noqa: E731
    t1 = time.monotonic()
    tickets, refusals = bt.issue(fired, inp, {}, quote)
    t2 = time.monotonic()
    fired_by_day = {}
    for d, _, s in fired:
        fired_by_day.setdefault(d, set()).add(s)
    kept, pools, small = bt.compare(tickets, inp, {}, day_members, fired_by_day)
    t3 = time.monotonic()
    bt.draws(kept, pools)
    t4 = time.monotonic()
    print(f"scale {scale}: {n_sym} symbols, {len(sig_days)} signal sessions, {len(fired)} firings, "
          f"{len(tickets)} tickets issued, {len(kept)} kept, {len(pools)} pools")
    print(f"minutes: synthetic data {(t1 - t0) / 60:.1f}, issuing {(t2 - t1) / 60:.1f}, pools {(t3 - t2) / 60:.1f}, "
          f"draws {(t4 - t3) / 60:.1f}, total engine {(t4 - t1) / 60:.1f}; peak memory {peak_mb():.0f} MB")


if __name__ == "__main__":
    main(*(float(a) for a in sys.argv[1:3]))
