#!/usr/bin/env bash
# Create the two feature branches from main (idempotent).
#   bash scripts/setup_branches.sh          # local branches
#   bash scripts/setup_branches.sh --push   # also push main + branches to origin
# Each branch folder's BRANCH_TASKS.md lives on main too, so every branch starts with it.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PUSH=0
if [[ "${1:-}" == "--push" ]]; then PUSH=1; fi
BRANCHES=(branch-1-relay branch-2-fetch)

if [[ -n "$(git status --porcelain)" ]]; then
  echo "Working tree is not clean; commit or stash first." >&2
  exit 1
fi
git rev-parse --verify --quiet main >/dev/null || { echo "No 'main' branch found." >&2; exit 1; }

for branch in "${BRANCHES[@]}"; do
  if git rev-parse --verify --quiet "$branch" >/dev/null; then
    echo "exists:  $branch (owners merge main into it; see docs/BRANCH_WORKFLOW.md)"
  else
    git branch "$branch" main
    echo "created: $branch from main @ $(git rev-parse --short main)"
  fi
done

if [[ $PUSH == 1 ]]; then
  git push -u origin main "${BRANCHES[@]}"
fi

cat <<'MAP'

Branch ownership
  main            Member 1      core/ shared/ scripts/ docs/ .github/
  branch-1-relay  Member 2      relay/                        -> relay/BRANCH_TASKS.md
  branch-2-fetch  Members 3 + 4 fetch/ finchnode/ intelligence/ -> fetch/, finchnode/, intelligence/BRANCH_TASKS.md
MAP
