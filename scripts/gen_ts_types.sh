#!/usr/bin/env bash
# Regenerate shared/ts/core.ts (TypeScript types for the Relay service) from the
# committed OpenAPI schema. Run `make openapi` first if Core's API changed.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BIN="$ROOT/relay/node_modules/.bin/openapi-typescript"
if [ ! -x "$BIN" ]; then
  (cd "$ROOT/relay" && npm install)
fi
"$BIN" "$ROOT/shared/openapi/core.openapi.json" -o "$ROOT/shared/ts/core.ts" --default-non-nullable=false
echo "wrote shared/ts/core.ts"
