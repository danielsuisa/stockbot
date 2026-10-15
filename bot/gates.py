"""Verdicts from the preregistered gate file.

A report prints the string this module returns. It does not choose GO, NO-GO,
or CLOSED-COST on its own. The thresholds live in docs/backtest/gates.json,
which is written before a run and stores no result.
"""
import json
from pathlib import Path

CLOSED_COST = "CLOSED-COST"
GO = "GO"
NO_GO = "NO-GO"

ROOT = Path(__file__).resolve().parents[1]
GATES_PATH = ROOT / "docs" / "backtest" / "gates.json"
COUNT_PATH = ROOT / "docs" / "backtest" / "trial-count.json"
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


def effective_trials(gem_result_present, gates=None):
    """Trials already counted, plus one only after the GEM result exists."""
    gates = gates if gates is not None else load_gates()
    counted = list(gates["counted_before_faber_gem"])
    if gates.get("increment_after") != "gem-agg":
        raise ValueError("the counter increments only after gem-agg")
    if not gates.get("faber_gem_is_one_trial"):
        raise ValueError("Faber and GEM are one trial")
    if gem_result_present:
        return len(counted) + 1
    return len(counted)


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


def trend_verdict(trial_id, cagr, spy_cagr, boot_rate, early=None, gates=None):
    """GO only when every required window passes both legs."""
    gate = trial(trial_id, gates)
    if gate.get("status") == CLOSED_COST:
        return CLOSED_COST
    if gate.get("kind") != "trend":
        raise ValueError(trial_id)
    flags = leg_flags(trial_id, cagr, spy_cagr, boot_rate, gates)
    ok = flags["cagr"] and flags["drawdown"]
    if gate.get("requires_early_window"):
        if not early:
            return NO_GO
        early_flags = leg_flags(
            trial_id, early.get("cagr"), early.get("spy_cagr"), early.get("boot_rate"), gates,
        )
        ok = ok and early_flags["cagr"] and early_flags["drawdown"]
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


def write_trial_count(gem_verdict, trials_path=None, count_path=None):
    """Record the counter after gem-agg has a verdict. GEM-BIL does not call this."""
    if gem_verdict not in (GO, NO_GO):
        raise ValueError("the counter moves only after a GEM verdict")
    count = effective_trials(True)
    payload = {
        "incremented_after": "gem-agg",
        "gem_agg_verdict": gem_verdict,
        "counted_before_faber_gem": load_gates()["counted_before_faber_gem"],
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
        f"Effective trials after the GEM result: **{count}**. "
        f"The list before Faber and GEM has {len(payload['counted_before_faber_gem'])} names. "
        f"Faber and GEM add one, and only after this result. "
        f"GEM-BIL does not add another. The gem-agg verdict is {gem_verdict}. "
        f"VIX is not in the count.\n"
        f"{MARKER_END}"
    )
    trials.write_text(text[:start] + snippet + text[end + len(MARKER_END):], encoding="utf-8")
    return count
