"""The decision test's statistics (docs/superpowers/specs/2026-10-10-recommendation-algorithm-design.md 6.1, 6.3, 6.4,
6.6): blocks, the block-cluster SE, T10 and T5, check 1, the survivorship allowance b, the five gates and the outcome.
Pure functions over arrays; they print nothing. Until the verdict run (phase D) they run on synthetic numbers only."""
import math

CRIT_T10, CRIT_T5 = 2.262, 2.776  # Student's t, one-sided 2.5%, 9 and 4 degrees of freedom
MU_CONST, F, LIMIT_R, B_DATA_MAX = 4.1, 0.0041, 0.10, 0.03
LABELS = {"GO": "עבר מבחן היסטורי, במעקב קדימה", "NO-GO": "ללא יתרון מוכח"}


def block(index, n_sessions):
    """6.1: block = floor(10 x index of the signal session / number of sessions), 0-9."""
    return 10 * index // n_sessions


def long_block(b):
    """6.1: the five long blocks are the consecutive pairs of the ten."""
    return b // 2


def mean(x):
    return sum(x) / len(x)


def se(x, blocks, G):
    """6.3: SE = sqrt(G / (G - 1) x sum over blocks of u_g^2) / n, u_g = the sum over the block's tickets of
    (x_i - x-bar); an empty block has u_g = 0."""
    xb, u = mean(x), [0.0] * G
    for v, g in zip(x, blocks):
        u[g] += v - xb
    return math.sqrt(G / (G - 1) * sum(v * v for v in u)) / len(x)


def ses(x, blocks10):
    """(SE over the ten blocks, SE over the five long blocks)."""
    return se(x, blocks10, 10), se(x, [long_block(b) for b in blocks10], 5)


def mu_min(se10, se5):
    """Check 1: mu_min = 4.1 x the larger of the two estimated SEs."""
    return MU_CONST * max(se10, se5)


def b_allowance(K):
    """6.4: b = K x f x 1 R."""
    return K * F


def confirmable(mu, b):
    """Check 1 continues only if mu_min + b <= 0.10 R (unrounded)."""
    return mu + b <= LIMIT_R


def design_effect(x, se_max):
    """(the larger SE)^2 / (s^2 / n), s the sample standard deviation of x (n - 1); invariant under x -> -x, x + c."""
    xb, n = mean(x), len(x)
    s2 = sum((v - xb) ** 2 for v in x) / (n - 1)
    return se_max ** 2 / (s2 / n) if s2 else 0.0


def above_950th(A, A_k):
    """Gate 2: A strictly above the 950th smallest of the 1,000 A_k."""
    return A > sorted(A_k)[949]


def p_value(A, A_k):
    return (1 + sum(1 for a in A_k if a >= A)) / (len(A_k) + 1)


def gate1(x, b, blocks10):
    """Gate 1: with b subtracted from every x_i, x-bar > 0 and t >= the critical value under T10 and T5."""
    xs = [v - b for v in x]
    s10, s5 = ses(xs, blocks10)
    xb = mean(xs)
    t10 = xb / s10 if s10 else math.inf
    t5 = xb / s5 if s5 else math.inf
    return {"pass": xb > 0 and t10 >= CRIT_T10 and t5 >= CRIT_T5, "t10": t10, "t5": t5}


def gate3(x, blocks10):
    """Gate 3: the mean of x_i > 0 in at least 7 of the ten blocks, and in each half (blocks 0-4, 5-9). An empty
    block does not count as positive."""
    by = [[] for _ in range(10)]
    for v, b in zip(x, blocks10):
        by[b].append(v)
    positive = sum(1 for vals in by if vals and mean(vals) > 0)
    halves = [[v for b in range(lo, lo + 5) for v in by[b]] for lo in (0, 5)]
    ok_halves = all(h and mean(h) > 0 for h in halves)
    return {"pass": positive >= 7 and ok_halves, "blocks_positive": positive}


def top_count(n):
    """The top 1% of n tickets: ceil(n / 100) (at least one ticket)."""
    return math.ceil(n / 100)


def gate4(x, signals_of):
    """Gate 4: x-bar > 0 without the top 1% of tickets by x_i, and with each one of the four signals removed in
    turn."""
    rest = sorted(x)[:len(x) - top_count(len(x))]
    without_top = bool(rest) and mean(rest) > 0
    each = {}
    for s in sorted(set(signals_of)):
        kept = [v for v, g in zip(x, signals_of) if g != s]
        each[s] = bool(kept) and mean(kept) > 0
    return {"pass": without_top and all(each.values()), "without_top_1pct": without_top, "each_signal_removed": each}


def outcome(confirmable, comparable, gates, b=None):
    """6.4 and 6.6: NO-GO ("data") when b > 0.03 R; NO-GO when check 1 or comparability fails (no return statistic
    is computed then); otherwise GO only if every gate holds."""
    if b is not None and b > B_DATA_MAX:
        return "NO-GO (data)"
    if not confirmable:
        return "NO-GO (not confirmable)"
    if not comparable:
        return "NO-GO (not comparable)"
    return "GO" if all(gates.values()) else "NO-GO"
