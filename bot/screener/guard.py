"""Pre-commit guard (G4): the FROZEN paths in docs/screener/STATE.md must not appear in the branch's diff against
its merge base with origin/main. Exact paths or fnmatch globs, one per "- `path`" line between the FROZEN-BEGIN /
FROZEN-END markers.

    python -m bot.screener.guard            # exit 1 and list the offending paths
"""
import fnmatch
import re
import subprocess
import sys

from bot import common

STATE = common.ROOT / "docs" / "screener" / "STATE.md"


def frozen(text=None):
    """The FROZEN patterns in STATE.md (or `text`) -> list of paths / globs."""
    text = STATE.read_text("utf-8") if text is None else text
    m = re.search(r"<!-- FROZEN-BEGIN -->(.*?)<!-- FROZEN-END -->", text, re.S)
    return re.findall(r"^- `([^`]+)`", m.group(1), re.M) if m else []


def offending(changed, patterns):
    """Changed paths that match a FROZEN pattern."""
    return [p for p in changed if any(p == f or fnmatch.fnmatchcase(p, f) for f in patterns)]


def main():
    pats = frozen()
    if not pats:
        sys.exit("guard: no FROZEN list found in docs/screener/STATE.md")
    # against the merge base: main keeps moving (the squeeze commits its list hourly), and its own new commits are
    # not this branch's changes
    base = subprocess.run(["git", "merge-base", "origin/main", "HEAD"], cwd=common.ROOT, capture_output=True,
                          text=True, check=True).stdout.strip()
    out = subprocess.run(["git", "diff", "--name-only", base], cwd=common.ROOT, capture_output=True,
                         text=True, check=True).stdout.split()
    staged = subprocess.run(["git", "diff", "--name-only", "--cached", base], cwd=common.ROOT,
                            capture_output=True, text=True, check=True).stdout.split()
    bad = offending(sorted(set(out) | set(staged)), pats)
    if bad:
        print("guard: FROZEN paths changed:\n" + "\n".join(f"  {p}" for p in bad))
        return 1
    print(f"guard: ok ({len(pats)} frozen patterns, {len(set(out) | set(staged))} changed paths)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
