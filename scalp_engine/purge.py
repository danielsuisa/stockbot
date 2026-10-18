"""Purged K-fold and walk-forward splits (ADR 0005 sections 4-5). Row identity: decision time t, label end, session.

A training row is purged when its label interval [t, label_end] overlaps the time span of any test/validation row,
and rows in the embargo session(s) after a test/validation block are dropped."""
import datetime as dt


def kfold_blocks(sessions, k=5):
    """K contiguous blocks of sessions (as equal as possible)."""
    n = len(sessions)
    edges = [round(i * n / k) for i in range(k + 1)]
    return [sessions[edges[i]:edges[i + 1]] for i in range(k)]


def purged_kfold(rows, sessions, k=5, embargo=1):
    """Yield (train_idx, valid_idx) over rows = [(session, t, label_end)], purging overlaps and embargoing the
    `embargo` sessions after each validation block."""
    pos = {s: i for i, s in enumerate(sessions)}
    for block in kfold_blocks(sessions, k):
        bset = set(block)
        last = pos[block[-1]]
        emb = set(sessions[last + 1:last + 1 + embargo])
        valid = [i for i, r in enumerate(rows) if r[0] in bset]
        span = (min(rows[i][1] for i in valid), max(rows[i][2] for i in valid)) if valid else None
        train = [i for i, r in enumerate(rows) if r[0] not in bset and r[0] not in emb
                 and not (span and r[1] <= span[1] and r[2] >= span[0])]
        yield train, valid


def walkforward_train(rows, sessions, test_start, months=18, embargo=1):
    """Training row indices for a test month starting at session test_start: sessions in the `months` calendar
    months before test_start, minus the `embargo` sessions right before it, minus rows whose label ends at or after
    test_start's first decision."""
    y, m = test_start.year, test_start.month - months
    while m <= 0:
        y, m = y - 1, m + 12
    first = dt.date(y, m, 1)
    before = [s for s in sessions if first <= s < test_start]
    keep = set(before[:-embargo] if embargo else before)
    test_t0 = min((r[1] for r in rows if r[0] >= test_start), default=None)
    return [i for i, r in enumerate(rows) if r[0] in keep and (test_t0 is None or r[2] < test_t0)]
