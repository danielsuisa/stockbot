"""Loaders and a minimal validator for the shared contract files in scalp_contracts/.

scalp_eval has its own copy of this logic on purpose: the two sides share contract *files*,
never code (ARCHITECTURE R4, ADR 0001 section 2). Stdlib only.
"""
import datetime as dt
import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTRACTS = REPO_ROOT / "scalp_contracts"
SCHEMAS = CONTRACTS / "schemas"
TABLES = CONTRACTS / "tables"

MANDATORY_COLUMNS = {
    "event_at": "timestamp[us, tz=UTC]",
    "ingested_at": "timestamp[us, tz=UTC]",
    "source": "string",
    "feed": "string",
    "schema_version": "int32",
}


class ContractError(ValueError):
    """A contract file is malformed or an instance does not satisfy its contract."""


# ---------------------------------------------------------------- flat YAML subset

_KEY = re.compile(r"^([a-z][a-z0-9_]*(?:\.[a-z0-9_]+)*):\s*(.*)$")  # dotted keys allowed: window.x.first_date
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_INT = re.compile(r"^-?\d+$")


def load_flat_yaml(path):
    """Parse the strict YAML subset used by scalp_contracts/*.yaml: one `key: scalar` per line,
    `#` comments, no nesting, lists, anchors or multi-line values. Scalars: ints, ISO dates,
    plain or quoted strings. Anything else raises, so a richer file cannot be half-read."""
    out = {}
    for n, raw in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        line = raw.split(" #", 1)[0].rstrip() if not raw.lstrip().startswith("#") else ""
        if not line.strip():
            continue
        if line != line.lstrip():
            raise ContractError(f"{path}:{n}: indentation is not allowed")
        m = _KEY.match(line)
        if not m:
            raise ContractError(f"{path}:{n}: expected 'key: value'")
        key, val = m.group(1), m.group(2).strip()
        if key in out:
            raise ContractError(f"{path}:{n}: duplicate key {key!r}")
        if not val or val[0] in "[{&*!|>":
            raise ContractError(f"{path}:{n}: unsupported value for {key!r}")
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "'\"":
            out[key] = val[1:-1]
        elif _INT.match(val):
            out[key] = int(val)
        elif _DATE.match(val):
            out[key] = dt.date.fromisoformat(val)
        else:
            out[key] = val
    return out


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_schema(name):
    return load_json(SCHEMAS / f"{name}.schema.json")


def table_contracts():
    """{table name: contract} for every scalp_contracts/tables/*.json, each validated."""
    meta = load_schema("table_contract")
    out = {}
    for path in sorted(TABLES.glob("*.json")):
        contract = load_json(path)
        validate(contract, meta)
        cols = {c["name"]: c["type"] for c in contract["columns"]}
        for name, typ in MANDATORY_COLUMNS.items():
            if cols.get(name) != typ:
                raise ContractError(f"{path.name}: column {name!r} must exist with type {typ!r}")
        if len(cols) != len(contract["columns"]):
            raise ContractError(f"{path.name}: duplicate column names")
        if not set(contract["primary_key"]) <= set(cols):
            raise ContractError(f"{path.name}: primary_key names unknown columns")
        if not any(seg.startswith("date=") for seg in contract["partitioning"]):
            raise ContractError(f"{path.name}: partitioning needs a date={{date}} segment")
        out[contract["table"]] = contract
    return out


# ---------------------------------------------------------------- JSON Schema subset

_ANNOTATIONS = {"$schema", "$id", "$comment", "title", "description", "examples", "default"}
_TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}
_DATE_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$")


def _same(a, b):
    """JSON equality: 1 == true is False in JSON Schema."""
    return type(a) is type(b) and a == b if isinstance(a, bool) or isinstance(b, bool) else a == b


def validate(instance, schema, path="$"):
    """Validate against the JSON Schema subset the contracts use. Unknown keywords raise
    ContractError (fail closed), so a schema cannot silently rely on unsupported features."""
    errors = _errors(instance, schema, path)
    if errors:
        raise ContractError("; ".join(errors))


def _errors(v, s, path):
    unknown = set(s) - _ANNOTATIONS - set(_CHECKS)
    if unknown:
        raise ContractError(f"{path}: unsupported schema keyword(s) {sorted(unknown)}")
    out = []
    for key in s:
        if key in _CHECKS:
            out += _CHECKS[key](v, s, path)
    return out


def _type(v, s, path):
    types = s["type"] if isinstance(s["type"], list) else [s["type"]]
    return [] if any(_TYPES[t](v) for t in types) else [f"{path}: expected {s['type']}"]


def _props(v, s, path):
    if not isinstance(v, dict):
        return []
    out = []
    for k, sub in s["properties"].items():
        if k in v:
            out += _errors(v[k], sub, f"{path}.{k}")
    return out


def _additional(v, s, path):
    if not isinstance(v, dict):
        return []
    extra = [k for k in v if k not in s.get("properties", {})]
    if s["additionalProperties"] is False:
        return [f"{path}: unexpected key(s) {sorted(extra)}"] if extra else []
    if isinstance(s["additionalProperties"], dict):
        return [e for k in extra for e in _errors(v[k], s["additionalProperties"], f"{path}.{k}")]
    return []


def _required(v, s, path):
    if not isinstance(v, dict):
        return []
    return [f"{path}: missing {k!r}" for k in s["required"] if k not in v]


def _items(v, s, path):
    if not isinstance(v, list):
        return []
    return [e for i, x in enumerate(v) for e in _errors(x, s["items"], f"{path}[{i}]")]


def _unique(v, s, path):
    if not (isinstance(v, list) and s["uniqueItems"]):
        return []
    seen = [json.dumps(x, sort_keys=True) for x in v]
    return [f"{path}: items are not unique"] if len(seen) != len(set(seen)) else []


def _one_of(v, s, path):
    ok = sum(1 for sub in s["oneOf"] if not _errors(v, sub, path))
    return [] if ok == 1 else [f"{path}: matches {ok} of oneOf (expected exactly 1)"]


def _format(v, s, path):
    if s["format"] != "date-time":
        raise ContractError(f"{path}: unsupported format {s['format']!r}")
    if not isinstance(v, str):
        return []
    if not _DATE_TIME.match(v):
        return [f"{path}: not an RFC 3339 date-time"]
    try:
        dt.datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return [f"{path}: not an RFC 3339 date-time"]
    return []


def _bound(op, word):
    def check(v, s, path):
        key = word
        if _TYPES["number"](v) and not op(v, s[key]):
            return [f"{path}: {key} {s[key]}"]
        return []
    return check


def _length(op, key):
    def check(v, s, path):
        if isinstance(v, (str, list)) and not op(len(v), s[key]):
            return [f"{path}: {key} {s[key]}"]
        return []
    return check


_CHECKS = {
    "type": _type,
    "properties": _props,
    "additionalProperties": _additional,
    "required": _required,
    "items": _items,
    "uniqueItems": _unique,
    "oneOf": _one_of,
    "format": _format,
    "enum": lambda v, s, p: [] if any(_same(v, e) for e in s["enum"]) else [f"{p}: not in {s['enum']}"],
    "const": lambda v, s, p: [] if _same(v, s["const"]) else [f"{p}: must equal {s['const']!r}"],
    "pattern": lambda v, s, p: [] if not isinstance(v, str) or re.search(s["pattern"], v) else [f"{p}: does not match {s['pattern']}"],
    "minimum": _bound(lambda a, b: a >= b, "minimum"),
    "maximum": _bound(lambda a, b: a <= b, "maximum"),
    "minLength": _length(lambda a, b: a >= b, "minLength"),
    "maxLength": _length(lambda a, b: a <= b, "maxLength"),
    "minItems": _length(lambda a, b: a >= b, "minItems"),
    "maxItems": _length(lambda a, b: a <= b, "maxItems"),
}


# ---------------------------------------------------------------- canonical JSON

MAX_SAFE_INT = 2**53 - 1


def canonical_json(obj):
    """Canonical bytes: RFC 8785 (JCS) restricted to documents without floats and with ASCII-only
    strings. On that subset sorted-keys / no-whitespace json.dumps is byte-identical to JCS
    (ADR 0001 section 5.2); anything outside it raises instead of silently diverging."""
    _check_canonicalizable(obj, "$")
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def _check_canonicalizable(v, path):
    if isinstance(v, bool) or v is None:
        return
    if isinstance(v, float):
        raise ContractError(f"{path}: floats are not allowed in canonical JSON; use a decimal string")
    if isinstance(v, int):
        if abs(v) > MAX_SAFE_INT:
            raise ContractError(f"{path}: integer outside the IEEE-754 safe range")
        return
    if isinstance(v, str):
        if not v.isascii():
            raise ContractError(f"{path}: non-ASCII string")
        return
    if isinstance(v, list):
        for i, x in enumerate(v):
            _check_canonicalizable(x, f"{path}[{i}]")
        return
    if isinstance(v, dict):
        for k, x in v.items():
            if not isinstance(k, str) or not k.isascii():
                raise ContractError(f"{path}: keys must be ASCII strings")
            _check_canonicalizable(x, f"{path}.{k}")
        return
    raise ContractError(f"{path}: unsupported type {type(v).__name__}")
