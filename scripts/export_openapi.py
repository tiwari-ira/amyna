"""Export Core's OpenAPI schema to shared/openapi/core.openapi.json.

make openapi                              # write
python scripts/export_openapi.py --check  # CI: fail if the committed file is stale
"""

import argparse
import json
import sys
import tempfile
from pathlib import Path

from app.demo_support import isolated_settings
from app.main import create_app

REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT = REPO_ROOT / "shared" / "openapi" / "core.openapi.json"


def render() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        app = create_app(isolated_settings(Path(tmp) / "openapi.db", log_level="ERROR"))
        schema = app.openapi()
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="exit 1 if the file is out of date")
    args = parser.parse_args()
    text = render()
    if args.check:
        current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""
        if current != text:
            print(
                "shared/openapi/core.openapi.json is stale: run `make contracts`.", file=sys.stderr
            )
            return 1
        print("OpenAPI schema is up to date.")
        return 0
    OUTPUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUTPUT.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
