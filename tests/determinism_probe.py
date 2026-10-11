"""Determinism probe (SYNTHETIC DATA ONLY): the universe, the detectors and the blind report computed in a fresh process,
printed as canonical text. tests.test_ensemble_determinism runs it under different PYTHONHASHSEED values and requires
identical bytes, because the verdict must reproduce the blind report in another process (6.4 point 4).

The input exercises every set the code iterates: the rename pool, symbols per CIK (a renamed symbol, two bar-less
symbols taking the same other symbol, a CIK with two others that refuses), and S1 without a symbol on a CIK with two
universe symbols.

    python -m tests.determinism_probe"""
import contextlib
import hashlib
import json
import os
import random
import sys
from unittest import mock

if os.environ.get("ENSEMBLE_PROBE_PRELUDE"):  # the local runner's time-zone stub (CI has the real tz database)
    exec(open(os.environ["ENSEMBLE_PROBE_PRELUDE"], encoding="utf-8").read())

from bot import blind, ensemble as en, ensemble_backtest as bt, ensemble_data as ed, signals  # noqa: E402
from tests.test_ensemble_backtest import GOOD_QUOTE  # noqa: E402
from tests.test_ensemble_signals import inputs  # noqa: E402
from tests.test_probe_ensemble import Bars, buy, series  # noqa: E402

S = Bars.S


def walk(rng, n=400, start=20.0):
    c, out = start, []
    for _ in range(n):
        c = max(6.0, c * (1 + rng.gauss(0, 0.03)))
        out.append(round(c, 4))
    return out


def rich_inputs():
    rng = random.Random(7)  # fixed: independent of the hash seed
    ser, rows = {}, []
    for j in range(60):
        sym = f"A{j:02d}"
        closes = walk(rng)
        vols = [int(rng.uniform(1.5e6, 6e6)) for _ in S]
        ser[sym] = series(list(S), close=closes, volume=vols)
        rows.append((S[200], sym, sym))
    # CIK R1: OLD1 (no bars) renames to NEW1
    ser["NEW1"] = series(list(S), close=walk(rng), volume=3_000_000)
    rows += [(S[0], "R1", "NEW1"), (S[250], "R1", "OLD1")]
    # CIK R2: OLDA and OLDB (no bars) both take NEWB (ruling 5)
    ser["NEWB"] = series(list(S), close=walk(rng), volume=3_000_000)
    rows += [(S[0], "R2", "NEWB"), (S[250], "R2", "OLDA"), (S[251], "R2", "OLDB")]
    # CIK R3: OLDC with two others with bars: the rule refuses
    ser["NEWC"], ser["NEWD"] = (series(list(S), close=walk(rng), volume=3_000_000) for _ in range(2))
    rows += [(S[0], "R3", "NEWC"), (S[0], "R3", "NEWD"), (S[250], "R3", "OLDC")]
    # CIK M: two universe symbols; S1 purchases without a symbol pick one of them
    ser["MA"], ser["MB"] = (series(list(S), close=walk(rng), volume=4_000_000) for _ in range(2))
    rows += [(S[200], "M", "MA"), (S[200], "M", "MB")]
    purchases = [buy(o, S[310], 40_000 + int(o), issuer="M", symbol=None) for o in "123"]
    prices = {c: [(S[260], S[259], "a", 20.0)] for c in ("R1", "R2", "R3")}
    for s in ser.values():
        s["raw_open"] = list(s["raw_close"])
    filed = {}
    for d, _, sym in rows:
        filed.setdefault(sym, []).append(d)
    releases = {sym: {S[k]} for k, sym in zip(range(300, 360, 3), sorted(ser))}
    return inputs(ser, filed, rows=rows, prices=prices, purchases=purchases, releases=releases)


def canon(v):
    """Sets and frozensets as sorted lists, dict items sorted: the printed text depends on content only."""
    if isinstance(v, (set, frozenset)):
        return sorted(canon(x) for x in v)
    if isinstance(v, dict):
        return sorted((repr(k), canon(x)) for k, x in v.items())
    if isinstance(v, (list, tuple)):
        return [canon(x) for x in v]
    if isinstance(v, bytes):
        return list(v)
    return v


@contextlib.contextmanager
def env(inp, got):
    with mock.patch.object(en, "prepare", return_value=(inp, got)), mock.patch.object(en, "check_r_s"), \
            mock.patch.object(en.alpaca, "quote_full", side_effect=lambda s, at: GOOD_QUOTE), \
            mock.patch.object(ed, "SIGNAL_START", S[300]), mock.patch.object(ed, "SIGNAL_END", S[359]), \
            mock.patch.object(bt, "POOL_MIN", 2), en.offline():
        yield


def main():
    inp = rich_inputs()
    got = signals.firings(inp, S[300], S[359])
    out = {"fingerprint": ed.fingerprint(inp), "rename_pool": sorted(inp.rename_pool),
           "firings": got["firings"], "renamed": canon(got["renamed"]), "members": canon(got["members"]),
           "deciles": canon(got["deciles"]), "diagnostics": canon(got["diagnostics"])}
    with env(inp, got):
        out["blind_report"] = blind.serialize(en.blind_report())
    from tests.test_ensemble_verdict import scenario  # the canned end-to-end scenario
    sinp, sgot = scenario("go")
    with env(sinp, sgot), mock.patch.object(ed, "SIGNAL_START", S[260]):
        out["blind_report_scenario"] = blind.serialize(en.blind_report())
    text = json.dumps(out, sort_keys=False, default=repr)
    sys.stdout.write(f"{len(got['firings'])} firings, {len(got['renamed'])} renamed\n{text}\n"
                     f"sha256 {hashlib.sha256(text.encode()).hexdigest()}\n")


if __name__ == "__main__":
    main()
