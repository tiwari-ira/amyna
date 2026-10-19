"""mypy ratchet: fail on any NEW type error, allow the committed baseline.

The codebase predates type checking; 100+ known errors are boundary noise
(DB ``str`` vs contract ``StrEnum`` that Pydantic coerces at runtime). The
baseline freezes them: CI fails only when an error appears that is not in the
baseline, and contributors shrink the file as they fix errors. mypy found a
real contract bug on day one (Dispute.approved_at silently dropped), so the
gate pays for itself immediately.

Usage:
    python scripts/mypy_gate.py            # check (CI)
    python scripts/mypy_gate.py --update    # regenerate the baseline
"""

import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BASELINE = REPO / "scripts" / ".mypy_baseline.txt"
TARGETS = [
    "core/app",
    "shared/python/claimshield_contracts",
    "intelligence",
    "finchnode",
    "fetch",
    "scripts",
]

# "path:12: error: Message  [code]" -> ("path", "code", "Message")
LINE = re.compile(r"^(?P<loc>[^:\n]+):\d+: error: (?P<msg>.+?)(?:  \[(?P<code>[a-z-]+)\])?$")


def normalized_errors() -> set[str]:
    proc = subprocess.run(
        [sys.executable, "-m", "mypy", *TARGETS],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    if proc.returncode not in (0, 1):
        print(proc.stdout + proc.stderr, file=sys.stderr)
        raise SystemExit(f"mypy itself failed (exit {proc.returncode})")
    found = set()
    for line in proc.stdout.splitlines():
        match = LINE.match(line)
        if match:
            code = match.group("code") or "untagged"
            loc = match.group("loc").replace("\\", "/")
            found.add(f"{loc} :: {code} :: {match.group('msg')}")
    return found


def main() -> int:
    current = normalized_errors()
    if "--update" in sys.argv:
        BASELINE.write_text("\n".join(sorted(current)) + "\n", encoding="utf-8")
        print(f"baseline updated: {len(current)} errors")
        return 0
    if not BASELINE.exists():
        raise SystemExit("scripts/.mypy_baseline.txt is missing; run scripts/mypy_gate.py --update")
    baseline = set(BASELINE.read_text(encoding="utf-8").splitlines()) - {""}
    new = current - baseline
    fixed = baseline - current
    if new:
        print("NEW mypy errors (fix them, do not baseline them):", file=sys.stderr)
        print("\n".join(sorted(new)), file=sys.stderr)
    if fixed:
        print(f"{len(fixed)} errors fixed since the baseline -- regenerate it with:")
        print("    python scripts/mypy_gate.py --update")
    print(f"mypy: {len(current)} errors (baseline {len(baseline)})")
    return 1 if new else 0


if __name__ == "__main__":
    raise SystemExit(main())
