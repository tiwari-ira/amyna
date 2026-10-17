"""Docs-as-code: keep README's test-count claims honest (see .github/workflows/ci.yml).

The README drifted before CI existed ("43 Relay vitest tests" while the suite had
70). This script counts the real suites and fails when a README claim overstates
them. Claims are written as "<n>+ <suite> tests", so they never under-promise.
"""

import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
README = REPO / "README.md"


def python_tests() -> int:
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    # pytest 9 prints one "path: count" line per file; sum the counts.
    return sum(int(m) for m in re.findall(r":\s*(\d+)$", out, re.M))


def vitest_tests() -> int:
    total = 0
    for path in (REPO / "relay" / "tests").glob("*.test.ts"):
        total += len(re.findall(r"\bit\s*\(", path.read_text(encoding="utf-8")))
    return total


def readme_claims() -> dict[str, int]:
    text = README.read_text(encoding="utf-8")
    py = re.search(r"(\d+)\+ Python tests", text)
    ts = re.search(r"(\d+)\+ Relay vitest tests", text)
    if py is None or ts is None:
        raise SystemExit(
            "README no longer contains '<n>+ Python tests' / '<n>+ Relay vitest tests' claims;"
            " update scripts/check_docs.py to match the new wording."
        )
    return {"python": int(py.group(1)), "vitest": int(ts.group(1))}


def main() -> int:
    actual = {"python": python_tests(), "vitest": vitest_tests()}
    claimed = readme_claims()
    failures = [
        f"README claims {claimed[name]}+ {name} tests but the suite has {actual[name]}"
        for name, actual_count in actual.items()
        if claimed[name] > actual_count
    ]
    for name, count in actual.items():
        print(f"{name}: {count} collected (README claims {claimed[name]}+)")
    if failures:
        print(*failures, sep="\n", file=sys.stderr)
        return 1
    print("docs OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
