# Branch Workflow

## Branches

| Branch | Owner(s) | Folders you edit | Folders you only read |
|---|---|---|---|
| `main` | Member 1 | `core/`, `shared/`, `scripts/`, `docs/`, `.github/`, root files | — |
| `branch-1-relay` | Member 2 | `relay/` | everything else |
| `branch-2-fetch` | Member 3 (agents), Member 4 (data + intelligence) | `fetch/`, `finchnode/`, `intelligence/` | everything else |

Both feature branches were created from `main` by `scripts/setup_branches.sh`.
Folder ownership is disjoint, so merges between branches should not conflict.
`.github/CODEOWNERS` enforces review by the folder owner.

## Daily loop

```bash
git checkout branch-2-fetch          # or branch-1-relay
git fetch origin
git merge origin/main                # pull contract + Core changes from main
make setup                           # only if dependencies changed
make test && make smoke              # must stay green in mock mode
# ... work inside your folders ...
git push origin branch-2-fetch
# open a PR into main when a BRANCH_TASKS.md milestone is done
```

Merge `main` into your branch at least once a day, and always before opening a PR.
Prefer merges over rebases on shared branches (Members 3 and 4 share `branch-2-fetch`).

## Contract-change process

`shared/` is the contract between all teams. **Never change it on a feature branch.**

1. Open a small PR **into `main`** that changes `shared/python/claimshield_contracts/…`.
2. Bump the version in `shared/python/claimshield_contracts/version.py`:
   - `CONTRACTS_VERSION` for HTTP/domain models (additive = minor, breaking = major);
   - `AGENT_PROTOCOL_VERSION` for uAgents messages. **Any** field change alters the
     schema digest, so agents on different versions stop understanding each other.
     Restart all agents together after a bump.
3. Run `make contracts` (regenerates `shared/openapi/core.openapi.json` and
   `shared/ts/core.ts`) and commit the generated files. CI fails if they drift.
4. Update `docs/CONTRACTS.md`.
5. Member 1 reviews and merges. Every branch then merges `main`.

Adding an optional field with a default is backward compatible for HTTP clients
(responses ignore unknown fields). Renaming or removing fields is breaking.

## What "done" means for a PR into main

- `make lint`, `make test`, `make smoke` pass locally and in CI (mock mode, no keys).
- New live integrations are behind a `*_MODE` flag that defaults to mock.
- New sponsor assumptions are tagged `# VERIFY against current docs` and listed in
  `docs/VERIFY_LIST.md`.
- No secrets, seeds or real PHI in the diff.
