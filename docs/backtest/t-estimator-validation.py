"""Validation of the t statistic of the recommendation-algorithm spec (section 6.3). Stdlib only.

1. A hand-checkable example of the block formula.
2. Simulated histories of overlapping tickets with NO edge. Tickets share daily shocks while they are open, so
   their results overlap in time; in some scenarios a signal also works or fails for months at a time. For each
   registered test (T10 and T5 together, then gate 3): its standard errors against the true one, how often it says
   GO without an edge (a correct test: about 2.5%), and how large an edge must be for it to be found in 80% of
   histories. The rule of the spec's revision 3 is shown for comparison only.

Usage: python t-estimator-validation.py [histories per scenario, default 2000] [seed offset, default 0]
  defaults      -> recomputes the figures quoted in the spec and exits 1 if any differs.
  other values  -> an independent run; exits 1 if a false-GO rate is outside the spec's figure by more than
                   3 Monte Carlo standard errors.
About two minutes for the defaults (it prints each scenario as it finishes); `500 1` takes half a minute. Only
month totals are simulated (a month's ticket results are summed in closed form), which is why it is fast; every
estimator here is a function of month totals.
"""
import math
import random
import sys
import time
from operator import mul

MONTHS, DAYS = 111, 21                          # 9.25 years of 21-session months
SIGNALS = [(2, 21, 0), (3, 42, 3), (4, 21, 3), (4, 5, 0)]   # tickets a day, horizon, up to n sessions pending
LOAD_ALL, LOAD_SIGNAL, NOISE = 0.02, 0.04, 1.1  # shared daily shock, per-signal daily shock, own noise (H = 21)


def t_stat(x, cluster, n_clusters, lags):
    """Spec 6.3 on ticket results x with their 0-based cluster indices -> (mean, se, t)."""
    totals, counts = [0.0] * n_clusters, [0] * n_clusters
    for v, g in zip(x, cluster):
        totals[g] += v
        counts[g] += 1
    return from_totals(totals, counts, lags)


def from_totals(totals, counts, lags):
    """The same statistic from each cluster's sum and number of tickets."""
    n, groups = sum(counts), len(totals)
    mean = sum(totals) / n
    u = [s - c * mean for s, c in zip(totals, counts)]
    var = sum(a * a for a in u)
    for lag in range(1, lags + 1):
        var += 2 * (1 - lag / (lags + 1)) * sum(map(mul, u[lag:], u))
    if var <= 0:
        var = sum(a * a for a in u)
    se = math.sqrt(groups / (groups - 1) * var) / n
    return mean, se, mean / se


def hand_example():
    mean, se, t = t_stat([1, -1, 2, 0, 1, -2, 1, 1, 3], [0, 0, 0, 1, 1, 2, 2, 2, 3], 4, 0)
    print(f"hand example: mean {mean:.4f}  se {se:.4f}  t {t:.2f}   (by hand: 0.6667, 0.3966, 1.68)")
    return (round(mean, 4), round(se, 4), round(t, 2)) == (0.6667, 0.3966, 1.68)


def calendar(bursty, rng):
    """A fixed calendar of tickets, reduced to what a month total needs: per month, the number of tickets, the SD
    of their summed own noise, and for each shock series (0 = shared, 1-4 = the signal's) the first day and the
    number of that month's tickets open on each following day."""
    total = MONTHS * DAYS + 60
    counts, noise, open_ = [0] * MONTHS, [0.0] * MONTHS, [[[0] * (total + 1) for _ in range(5)] for _ in range(MONTHS)]
    for m in range(MONTHS):
        for s, (rate, h, pending) in enumerate(SIGNALS):
            k = math.exp(rng.gauss(0, 1.0)) if bursty else 1.0
            for d in range(DAYS):
                start = m * DAYS + d
                for _ in range(int(rate * k + rng.random())):
                    end = start + h + (rng.randrange(pending) if pending else 0)
                    counts[m] += 1
                    noise[m] += NOISE * NOISE * (end - start) / 21
                    for series in (0, s + 1):           # +1 on its first open day, -1 after its last
                        open_[m][series][start] += 1
                        open_[m][series][end] -= 1
    weights = []
    for m in range(MONTHS):
        row = []
        for series in range(5):
            first, last, running, w = m * DAYS, min(total, (m + 1) * DAYS + 45), 0, []
            for d in range(first, last):
                running += open_[m][series][d]
                w.append(running)
            row.append((first, last, w))
        weights.append(row)
    return counts, [math.sqrt(v) for v in noise], weights


def history(cal, memory, rng):
    """One history without an edge -> the month totals of the ticket results."""
    counts, noise, weights = cal
    total = MONTHS * DAYS + 60
    shocks = [[rng.gauss(0, 1) for _ in range(total)]]
    for _ in range(4):
        drift, series = 0.0, []
        for d in range(total):
            if memory and d % DAYS == 0:        # the signal's edge drifts: it works or fails for a while
                drift = memory * drift + rng.gauss(0, 0.004)
            series.append(rng.gauss(0, 1) + drift / LOAD_SIGNAL)
        shocks.append(series)
    out = []
    for m in range(MONTHS):
        v = rng.gauss(0, noise[m])
        for series, (first, last, w) in enumerate(weights[m]):
            v += (LOAD_SIGNAL if series else LOAD_ALL) * sum(map(mul, w, shocks[series][first:last]))
        out.append(v)
    return out


def regroup(values, months_per_cluster):
    n = int((MONTHS - 1) / months_per_cluster) + 1
    out = [0] * n
    for m, v in enumerate(values):
        out[int(m / months_per_cluster)] += v
    return out


# label: (months per cluster, Newey-West lags, critical value: Student's t at one-sided 2.5%, or 2.0)
ESTIMATORS = {"T10 ten blocks": (11.1, 0, 2.262), "T5 five blocks": (22.2, 0, 2.776),
              "months, 6 lags": (1, 6, 2.0), "quarters, 2 lags": (3, 2, 2.0)}
RULES = {"registered (T10 and T5)": ["T10 ten blocks", "T5 five blocks"],
         "revision 3 (superseded)": ["months, 6 lags", "quarters, 2 lags", "T10 ten blocks"]}
REGISTERED = "registered (T10 and T5)"
SCENARIOS = [("A. overlap only", False, 0.0),
             ("B. A + bursts of tickets + edges that drift, half-life 1.4 months", True, 0.6),
             ("C. B with half-life 2.4 months", True, 0.75),
             ("D. B with half-life 6.6 months (about a year in or out of favour)", True, 0.9),
             ("E. B with half-life 13.5 months (two years and more; out of scope)", True, 0.95)]


def run(name, bursty, memory, histories, seed):
    rng = random.Random(seed)
    cal = calendar(bursty, rng)
    counts = {label: regroup(cal[0], span) for label, (span, _, _) in ESTIMATORS.items()}
    block_counts = regroup(cal[0], 11.1)
    rows = []                                   # per history: mean, {label: se}, the ten blocks' totals
    for _ in range(histories):
        totals = history(cal, memory, rng)
        ses = {}
        for label, (span, lags, _) in ESTIMATORS.items():
            mean, ses[label], _ = from_totals(regroup(totals, span), counts[label], lags)
        rows.append((mean, ses, regroup(totals, 11.1)))
    true = math.sqrt(sum(r[0] ** 2 for r in rows) / histories)

    def go(rule, mu, row):                      # every estimator of the rule, then gate 3 (blocks and halves)
        mean, ses, blocks = row
        if any((mean + mu) / ses[label] < ESTIMATORS[label][2] for label in RULES[rule]):
            return False
        above = [s / c + mu > 0 for s, c in zip(blocks, block_counts)]
        halves = [sum(blocks[a:b]) / sum(block_counts[a:b]) + mu > 0 for a, b in ((0, 5), (5, 10))]
        return sum(above) >= 7 and all(halves)

    def rate(rule, mu):
        return 100 * sum(go(rule, mu, r) for r in rows) / histories

    def edge_for_80(rule):                      # the edge found in 80% of histories, by bisection
        low, high = 0.0, 12 * true
        for _ in range(40):
            mid = (low + high) / 2
            low, high = (mid, high) if rate(rule, mid) < 80 else (low, mid)
        return high

    largest = sum(max(r[1][label] for label in RULES[REGISTERED]) for r in rows) / histories
    edge = edge_for_80(REGISTERED)
    out = {"SE / true SE": {label: round(sum(r[1][label] for r in rows) / histories / true, 2)
                            for label in RULES[REGISTERED]},
           "false GO %": {rule: round(rate(rule, 0), 1) for rule in RULES},
           "edge found in 80% / true SE": round(edge / true, 1),
           "edge found in 80% / larger estimated SE": round(edge / largest, 1)}
    print(f"\n{name}: {sum(cal[0])} tickets, true SE of the mean {true:.4f}R")
    print("  estimated / true SE:  " + ",  ".join(f"{label} {v:.2f}" for label, v in out["SE / true SE"].items()))
    for rule in RULES:
        print(f"  GO without an edge, {rule}: {out['false GO %'][rule]:.1f}%")
    print(f"  edge the registered test finds in 80% of histories: {out['edge found in 80% / true SE']:.1f} x the "
          f"true SE = {out['edge found in 80% / larger estimated SE']:.1f} x the larger of its two estimated SEs")
    sys.stdout.flush()
    return out


# The figures of spec section 6.3, as this script gives them with its defaults.
SPEC = [{'SE / true SE': {'T10 ten blocks': 0.95, 'T5 five blocks': 0.93},
  'false GO %': {'registered (T10 and T5)': 1.8, 'revision 3 (superseded)': 2.9},
  'edge found in 80% / true SE': 3.8,
  'edge found in 80% / larger estimated SE': 3.6},
 {'SE / true SE': {'T10 ten blocks': 0.89, 'T5 five blocks': 0.9},
  'false GO %': {'registered (T10 and T5)': 1.2, 'revision 3 (superseded)': 2.1},
  'edge found in 80% / true SE': 3.7,
  'edge found in 80% / larger estimated SE': 3.8},
 {'SE / true SE': {'T10 ten blocks': 0.88, 'T5 five blocks': 0.9},
  'false GO %': {'registered (T10 and T5)': 1.8, 'revision 3 (superseded)': 3.4},
  'edge found in 80% / true SE': 3.7,
  'edge found in 80% / larger estimated SE': 3.8},
 {'SE / true SE': {'T10 ten blocks': 0.69, 'T5 five blocks': 0.77},
  'false GO %': {'registered (T10 and T5)': 3.6, 'revision 3 (superseded)': 7.6},
  'edge found in 80% / true SE': 3.1,
  'edge found in 80% / larger estimated SE': 3.9},
 {'SE / true SE': {'T10 ten blocks': 0.49, 'T5 five blocks': 0.6},
  'false GO %': {'registered (T10 and T5)': 6.5, 'revision 3 (superseded)': 14.2},
  'edge found in 80% / true SE': 2.7,
  'edge found in 80% / larger estimated SE': 4.4}]

if __name__ == "__main__":
    histories = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
    offset = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    began = time.time()
    ok = hand_example()
    got = [run(name, bursty, memory, histories, offset + n + 1) for n, (name, bursty, memory) in enumerate(SCENARIOS)]
    print(f"\n{histories} histories a scenario, seed offset {offset}, {time.time() - began:.0f} s")
    if SPEC is None:
        print("SPEC = " + repr(got))
    elif (histories, offset) == (2000, 0):
        ok = ok and got == SPEC
        print("reproduces the spec's figures exactly:", "YES" if ok else "NO")
        for (name, *_), g, e in zip(SCENARIOS, got, SPEC):
            if g != e:
                print(f"  {name}\n    got  {g}\n    spec {e}")
    else:
        for (name, *_), g, e in zip(SCENARIOS, got, SPEC):
            for rule in RULES:
                p, q = e["false GO %"][rule] / 100, g["false GO %"][rule] / 100
                if abs(q - p) > 3 * math.sqrt(p * (1 - p) * (1 / histories + 1 / 2000)):
                    ok = False
                    print(f"  outside 3 standard errors: {name} / {rule}: {100 * q:.1f}% vs {100 * p:.1f}%")
            key = "edge found in 80% / larger estimated SE"
            if abs(g[key] - e[key]) > 0.3:
                ok = False
                print(f"  {name}: detectable edge {g[key]} vs {e[key]} x the larger estimated SE")
        print("consistent with the spec's figures (independent run):", "YES" if ok else "NO")
    sys.exit(0 if ok else 1)
