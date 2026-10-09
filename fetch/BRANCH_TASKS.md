# branch-2-fetch — Member 3 (Fetch agents, Agentverse, ASI:One)

You own `fetch/` (shared branch with Member 4, who owns `finchnode/` and `intelligence/`).
Agents talk to each other with typed uAgents messages and to Core over HTTPS tools.
They never touch the database or FinchNode directly.

## Already working (offline, no Agentverse)

- [x] Pure logic in `fetch/agents/logic/` (no uagents imports): Visit, Claim and Dispute handlers + `ChatOrchestrator` state machine
- [x] Orchestrator uAgent (`orchestrator.py`): chat protocol, **always acknowledges**, manifest published, Agentverse README (`agentverse/agent_profiles/orchestrator.md`), `HELLO_WORLD=1` mode, typed requests to specialists correlated by `request_id` in `ctx.storage`, 5 s timeout sweep, REST `POST /trigger` for Core
- [x] Specialists (`visit_agent.py`, `claim_agent.py`, `dispute_agent.py`): internal, unpublished, thin wrappers
- [x] `CoreClient` (HTTPS `CoreTools`) tagging every call with `agent_run` (Core enforces `AGENT_TOOLS`)
- [x] `local_direct.py` (`make chat`): the exact ASI:One conversation with no Agentverse
- [x] `run_all.py`: Bureau (default) or `--processes`; `print_addresses.py`
- [x] Seeds only from env, fail loudly if missing, never logged
- [x] Tests: local_direct conversation, idempotency, allowlist, **real uAgents Bureau** (HELLO_WORLD, full chat workflow vs a live Core, Core-driven `FETCH_MODE=agentverse` trigger path, specialists ignoring unknown senders)
- [x] Safety: APPROVE / REJECT / DONE / CANCEL are exact-match commands; the orchestrator resumes from Core's state and never reviews a claim other than the one requested

## To implement for LIVE

1. **Discoverability first** (riskiest): `make agents-hello`, connect the mailbox, find the
   agent in ASI:One, get "Hello from Amyna". Follow `agentverse/README_AGENTVERSE.md`.
2. Full flow on Agentverse: `FETCH_MODE=agentverse`, `HELLO_WORLD=0`, `make agents`, run the
   demo conversation in ASI:One (`docs/DEMO_SCRIPT.md`). Confirm `StartSessionContent` /
   `EndSessionContent` handling with ASI:One.
3. Core → orchestrator trigger (`FETCH_MODE=agentverse`): Core POSTs `OrchestratorTrigger`
   to `ORCHESTRATOR_URL/trigger`; replies for core-triggered work are only logged. Works
   locally in a Bureau (tested); confirm it with the orchestrator on a mailbox.
4. Optional: LLM fallback in `intent.py` for `UNKNOWN` intents (keep rules first).
5. Optional: let the Dispute Agent polish letter prose with a larger model, as long as it
   adds **no facts** beyond the items and citations it was given.
6. If you run specialists as separate processes, verify that mailbox agents resolve each other.

## Env vars

`AGENTVERSE_API_KEY`, `ORCHESTRATOR_SEED`, `VISIT_AGENT_SEED`, `CLAIM_AGENT_SEED`,
`DISPUTE_AGENT_SEED`, `VISIT_AGENT_ADDRESS`, `CLAIM_AGENT_ADDRESS`, `DISPUTE_AGENT_ADDRESS`,
`ORCHESTRATOR_ADDRESS` (specialists answer only this sender),
`ORCHESTRATOR_HANDLE`, `HELLO_WORLD`, `FETCH_MODE`, `ORCHESTRATOR_URL`,
`AGENT_REQUEST_TIMEOUT_S`, `CORE_BASE_URL`, `FETCH_SERVICE_TOKEN`.

## Commands

```bash
make setup
make test-py                     # includes fetch/tests (Bureau tests take ~10 s)
make dev-local && make seed      # Core + demo patient with a recorded visit
make chat                        # offline ASI:One rehearsal (same logic as the agents)
make agent-addresses             # after adding seeds to .env
make agents-hello                # step 1: discoverability
FETCH_MODE=agentverse make agents
```

## Do not change without a PR to main

`shared/**` — especially `agent_messages.py`: any field **or docstring** change alters the
schema digest. Bump `AGENT_PROTOCOL_VERSION`, run
`python -c "from claimshield_contracts.agent_messages import write_digest_lock; write_digest_lock()"`,
and restart all agents together. The logic entry-point signatures are also contracts
(Core's local_direct bridge calls them): see `docs/CONTRACTS.md` §5.

## Definition of done

- [ ] Orchestrator discoverable in ASI:One; HELLO_WORLD verified
- [ ] Full demo conversation completes **inside ASI:One**, ending in RESOLVED with the SSN handoff
- [ ] Every inter-agent hop is a typed message; no agent touches the DB or FinchNode
- [ ] Specialists not published; only the orchestrator has README + manifest
- [ ] `make test`, `make smoke` green; Fetch items ticked in `docs/VERIFY_LIST.md`

## Team build order (yours in **bold**)

1. **Fetch hello-world discoverable in ASI:One**
2. **Orchestrator ↔ Claim Agent messaging**
3. FinchNode demo → sandbox (M4)
4. Relay thin UX → Core (M2)
5. Evidence pipeline (M2 + M4)
6. Claim comparison (M4)
7. Human approval (M2)
8. **Dispute Agent**
9. Grok Voice + SIP (M1)
10. Human takeover (M1 + M2)

Optional last, explicitly low priority, **do not build now**: Grok Imagine avatar.
