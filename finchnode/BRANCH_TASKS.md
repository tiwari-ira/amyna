# branch-2-fetch — Member 4 (FinchNode health data)

You own `finchnode/` (and `intelligence/`, see `intelligence/BRANCH_TASKS.md`). This is a
pure library: **only Core calls it** (`core/app/adapters/finchnode/*`), after checking the
case's `health_data_access` consent. Agents reach the data through Core's tools
(`get_patient_claims`, `get_patient_encounters`, `get_patient_procedures`,
`get_patient_medications`); only the Claim Agent is allowed to call them (`AGENT_TOOLS`).

## Already working (mock)

- [x] `FixtureClient`: synthetic FHIR-style fixtures for `fn_pat_demo_001` (patient, coverage, encounters, procedures, medications, two claims)
- [x] `HttpFinchNodeClient` skeleton for demo (keyless) / sandbox (bearer `ck_test_…`): retries on 5xx/timeouts, FHIR `next` paging, refuses non-sandbox keys
- [x] `normalize.py`: FHIR → contract models, **preserving source `system/code/display` + original URI**; money → integer cents
- [x] `tools.py`: one least-privilege function per record type
- [x] Tests: normalization keeps source codes, coverage/payer parsing, cents, client auth/paging/retry

## To implement for LIVE (build-order step 3: demo → sandbox)

1. Confirm endpoints, auth, envelope and paging against FinchNode docs (`client.py`, all `VERIFY`).
2. `FINCHNODE_MODE=demo`: verified base URL `https://api.finchnode.com/demo/v1` (no API key). FHIR reads use `/fhir/Patient/{id}` and `/fhir/{type}?patient={id}`. The sample bill is an ExplanationOfBenefit, so a Claim 404 falls back to that.
3. `FINCHNODE_MODE=sandbox`: `FINCHNODE_API_KEY=ck_test_…`; same check.
4. Add fixtures that mirror real payloads (still synthetic) so mock mode stays realistic.
5. Optional: `mcp_optional.md`, only if FinchNode's MCP server adds value.

## Env vars

`FINCHNODE_MODE` (`mock|demo|sandbox`), `FINCHNODE_BASE_URL`, `FINCHNODE_API_KEY`.

## Commands

```bash
python -m finchnode.client --patient fn_pat_demo_001 --mode mock
FINCHNODE_MODE=demo FINCHNODE_BASE_URL=... python -m finchnode.client --patient <id> --mode demo
.venv/bin/python -m pytest finchnode/tests
```

## Do not change without a PR to main

`shared/**` (`HealthRecord`, `Claim`, `ClaimLine`, `CodeRef`, `PatientRef`), `core/**`.

## Definition of done

- [ ] Sandbox data flows through Core: consent → `POST /claims` (source finchnode) → analysis uses real records
- [ ] Source codes preserved end to end (visible in `GET /cases/{id}` → `claim.lines[].code.source_system_uri`)
- [ ] No real PHI committed; fixtures stay synthetic
- [ ] Tests green; FinchNode items ticked in `docs/VERIFY_LIST.md`

## Team build order (yours in **bold**)

1. Fetch hello-world (M3) · 2. Orchestrator ↔ Claim Agent (M3) · 3. **FinchNode demo → sandbox** ·
4. Relay thin UX (M2) · 5. **Evidence pipeline** (M2 + M4) · 6. **Claim comparison** · 7. Human approval (M2) ·
8. Dispute Agent (M3) · 9. Grok Voice + SIP (M1) · 10. Human takeover (M1 + M2).
Optional, low priority, **do not build now**: Grok Imagine avatar.
