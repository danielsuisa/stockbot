"""Verdicts from the preregistered gate file.

A report prints the string this module returns. It does not choose GO, NO-GO,
CLOSED-COST, or PENDING-REPLICATION on its own. The thresholds live in
docs/backtest/gates.json, which stores no result. An independent script's
replication record lives in docs/backtest/replication.json.
"""
import json
from pathlib import Path

CLOSED_COST = "CLOSED-COST"
GO = "GO"
NO_GO = "NO-GO"
PENDING = "PENDING-REPLICATION"

ROOT = Path(__file__).resolve().parents[1]
GATES_PATH = ROOT / "docs" / "backtest" / "gates.json"
COUNT_PATH = ROOT / "docs" / "backtest" / "trial-count.json"
REPLICATION_PATH = ROOT / "docs" / "backtest" / "replication.json"
TRIALS_PATH = ROOT / "docs" / "backtest" / "TRIALS.md"
MARKER_START = "<!-- TRIAL-COUNT -->"
MARKER_END = "<!-- /TRIAL-COUNT -->"


def load_gates(path=None):
    return json.loads(Path(path or GATES_PATH).read_text(encoding="utf-8"))


def trial(trial_id, gates=None):
    gates = gates if gates is not None else load_gates()
    try:
        return gates["trials"][trial_id]
    except KeyError as exc:
        raise KeyError(trial_id) from exc


def drawdown_fraction(gate):
    raw = gate["drawdown_fraction"]
    if isinstance(raw, str) and "/" in raw:
        num, den = raw.split("/", 1)
        return float(num) / float(den)
    return float(raw)


def completed_tests(gates=None):
    """Tests already run. This moves when the test is run, not when it is replicated."""
    gates = gates if gates is not None else load_gates()
    return len(gates["completed_prior"]) + len(gates["completed_added"])


def dsr_trials_next(gates=None):
    """Variant count for the next test's deflated Sharpe. At least 18."""
    gates = gates if gates is not None else load_gates()
    count = int(gates["dsr_n_next"])
    parts = int(gates["dsr_n_earlier_variants"]) + len(gates["completed_added"])
    if count < 18 or count != parts:
        raise ValueError("the next deflated Sharpe N is 15 earlier variants plus Faber, GEM, and GEM-BIL")
    return count


def effective_trials(gates=None):
    """The trial counter N. Present for callers that used the old name."""
    return completed_tests(gates)


def _cagr_ok(gate, growth, spy_growth):
    if growth is None or spy_growth is None:
        return False
    return growth >= spy_growth - gate["cagr_max_shortfall"] - 1e-12


def _boot_ok(gate, boot_rate):
    if boot_rate is None:
        return False
    return boot_rate + 1e-15 >= gate["bootstrap_floor"]


def leg_flags(trial_id, cagr, spy_cagr, boot_rate, gates=None):
    gate = trial(trial_id, gates)
    return {
        "cagr": _cagr_ok(gate, cagr, spy_cagr),
        "drawdown": _boot_ok(gate, boot_rate),
    }


def load_replication(path=None):
    file = Path(path or REPLICATION_PATH)
    if not file.exists():
        return {}
    return json.loads(file.read_text(encoding="utf-8"))


def replication_ok(trial_id, gates=None, path=None):
    """True when an independent script matched this trial inside the policy limits."""
    gates = gates if gates is not None else load_gates()
    policy = gates.get("replication") or {}
    if not policy.get("required_before_verdict", True):
        return True
    row = load_replication(path).get(trial_id)
    if not row or not row.get("passed"):
        return False
    gap = abs(float(row["cagr_gap"]))
    mismatch = int(row["mismatch"])
    return (
        gap <= float(policy["max_cagr_gap"]) + 1e-12
        and mismatch <= int(policy["max_mismatch_periods"])
    )


def replication_note(trial_id, path=None):
    row = load_replication(path).get(trial_id)
    if not row:
        return "Independent replication has not passed, so the verdict is PENDING-REPLICATION."
    gap = abs(float(row["cagr_gap"]))
    return (
        f"Independent no-cost replication over {row['window']}: "
        f"script CAGR {float(row['independent_cagr']):.4%} and main-signal CAGR "
        f"{float(row['main_cagr']):.4%}, gap {gap:.4%}, "
        f"holding mismatches {int(row['mismatch'])} of {int(row['months'])} months. "
        f"September 2026 signal: {row.get('sep_2026')}."
    )


def trend_verdict(trial_id, cagr, spy_cagr, boot_rate, early=None, gates=None, replicated=None, replication_path=None):
    """GO or NO-GO from the gate. PENDING-REPLICATION until the independent script matches.

    `replicated=True` skips the file, for a test of the gate arithmetic alone.
    """
    gate = trial(trial_id, gates)
    if gate.get("status") == CLOSED_COST:
        return CLOSED_COST
    if gate.get("kind") != "trend":
        raise ValueError(trial_id)
    flags = leg_flags(trial_id, cagr, spy_cagr, boot_rate, gates)
    ok = flags["cagr"] and flags["drawdown"]
    if gate.get("requires_early_window"):
        if not early:
            ok = False
        else:
            early_flags = leg_flags(
                trial_id, early.get("cagr"), early.get("spy_cagr"), early.get("boot_rate"), gates,
            )
            ok = ok and early_flags["cagr"] and early_flags["drawdown"]
    if replicated is None:
        replicated = replication_ok(trial_id, gates, replication_path)
    if not replicated:
        return PENDING
    return GO if ok else NO_GO


def verdict(trial_id, **metrics):
    """The only verdict string a report may print for this trial."""
    gate = trial(trial_id)
    if gate.get("status") == CLOSED_COST:
        return CLOSED_COST
    if gate.get("kind") == "trend":
        return trend_verdict(
            trial_id,
            metrics.get("cagr"),
            metrics.get("spy_cagr"),
            metrics.get("boot_rate"),
            early=metrics.get("early"),
            replicated=metrics.get("replicated"),
        )
    raise ValueError(trial_id)


def bootstrap_rate(strategy, spy, trial_id):
    from bot.trend_exec import block_bootstrap
    gate = trial(trial_id)
    return block_bootstrap(
        strategy, spy,
        draws=gate["bootstrap_draws"],
        block=gate["bootstrap_block"],
        seed=gate["bootstrap_seed"],
        fraction=drawdown_fraction(gate),
    )


def compound_months(dated):
    """Compound dated simple returns into calendar-month simple returns, in order."""
    buckets = {}
    order = []
    for day, ret in dated:
        key = day[:7]
        if key not in buckets:
            buckets[key] = 1.0
            order.append(key)
        buckets[key] *= 1.0 + ret
    return [buckets[key] - 1.0 for key in order]


def write_trial_count(gem_verdict, trials_path=None, count_path=None, gates=None):
    """Record the counter when gem-agg is run. PENDING-REPLICATION still increments it."""
    if gem_verdict not in (GO, NO_GO, PENDING):
        raise ValueError("the counter moves when the test is run")
    gates = gates if gates is not None else load_gates()
    count = completed_tests(gates)
    dsr_n = dsr_trials_next(gates)
    payload = {
        "completed_tests": count,
        "go": gates["go_count"],
        "completed_prior": list(gates["completed_prior"]),
        "completed_added": list(gates["completed_added"]),
        "dsr_n_next": dsr_n,
        "gem_agg_verdict": gem_verdict,
        "counter_includes_pending_replication": True,
        "effective_trials": count,
    }
    path = Path(count_path or COUNT_PATH)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    trials = Path(trials_path or TRIALS_PATH)
    text = trials.read_text(encoding="utf-8")
    if MARKER_START not in text or MARKER_END not in text:
        raise ValueError("TRIALS.md has no trial-count marker")
    start = text.index(MARKER_START)
    end = text.index(MARKER_END)
    snippet = (
        f"{MARKER_START}\n"
        f"Completed tests: **{count}** "
        f"({len(payload['completed_prior'])} prior + Faber + GEM + GEM-BIL). "
        f"GO: **{payload['go']}**. "
        f"The counter moved when those tests were run. "
        f"A verdict stays {PENDING} until an independent script matches. "
        f"The gem-agg verdict is {gem_verdict}. "
        f"The next test uses a deflated Sharpe N of **{dsr_n}** "
        f"({gates['dsr_n_earlier_variants']} earlier variants + Faber + GEM + GEM-BIL).\n"
        f"{MARKER_END}"
    )
    trials.write_text(text[:start] + snippet + text[end + len(MARKER_END):], encoding="utf-8")
    return count
