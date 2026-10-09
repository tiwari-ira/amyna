#!/usr/bin/env bash
# Migrate the configured database and load the synthetic demo scenario.
# Docker: docker compose exec core python -m app.seed.seed_demo
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PYTHON:-$ROOT/.venv/bin/python}"
cd "$ROOT/core"
"$PY" -m alembic upgrade head
"$PY" -m app.seed.seed_demo
