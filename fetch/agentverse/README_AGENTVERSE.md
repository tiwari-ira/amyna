# Agentverse + ASI:One setup (Member 3)

Agentverse is used **only** for registration, discovery, the mailbox and ASI:One routing.
All agent code runs on your machine (`mailbox=True`); nothing is deployed to Agentverse.
Only the **orchestrator** is public. The Visit / Claim / Dispute agents are internal.

> Do step 2 (hello world) first. It is the riskiest sponsor integration, and everything
> else already works offline (`make chat`, `make test`).

## 1. Account, key, seeds

1. Create an account at https://agentverse.ai and an API key → `AGENTVERSE_API_KEY` in `.env`.
2. Generate four seeds and put them in `.env` (never commit them, never log them):

   ```bash
   for v in ORCHESTRATOR VISIT_AGENT CLAIM_AGENT DISPUTE_AGENT; do
     echo "${v}_SEED=$(python -c 'import secrets; print(secrets.token_hex(32))')"
   done
   ```

3. Print the derived addresses and paste the three specialist ones into `.env`:

   ```bash
   make agent-addresses
   ```

## 2. Hello world: prove discoverability

```bash
make agents-hello          # HELLO_WORLD=1, orchestrator only, port 8000, mailbox=True
```

1. The log prints an **Agent inspector** link. Open it, click **Connect** → **Mailbox**.
   (VERIFY against current docs: exact inspector/mailbox UI flow.)
2. In Agentverse → *My agents*, open `amyna-orchestrator`. The README tab should
   show `fetch/agentverse/agent_profiles/orchestrator.md` (published via `readme_path`) and the
   chat protocol manifest (`publish_manifest=True`).
3. Optionally set a handle: `ORCHESTRATOR_HANDLE=Amyna` (VERIFY handle rules).
4. In ASI:One (https://asi1.ai), search for Amyna or chat with the agent address and
   send "hi". Expected reply: **Hello from Amyna**. Every message is acknowledged
   (`ChatAcknowledgement`) before the reply.

If the log shows `CERTIFICATE_VERIFY_FAILED` on macOS (python.org builds), run
`/Applications/Python\ 3.11/Install\ Certificates.command` once.

## 3. Full workflow

```bash
make dev-local      # Core on :8080 (terminal 1)
make seed           # demo patient with a recorded visit (terminal 2)
FETCH_MODE=agentverse HELLO_WORLD=0 make agents     # Bureau: all 4 agents (terminal 3)
```

Then in ASI:One follow [docs/DEMO_SCRIPT.md](../../docs/DEMO_SCRIPT.md#demo-1--asione-fetch-track).
Rehearse the identical conversation offline first with `make chat`.

### Bureau vs separate processes

- `python -m fetch.agents.run_all` (default): one process. Specialists are wired by
  address automatically and messages between agents are dispatched in-process. The
  orchestrator keeps its mailbox; the Bureau serves its REST `/trigger` on port 8000.
- `python -m fetch.agents.run_all --processes`: one process per agent (ports 8000-8003).
  Each agent uses its own mailbox; the orchestrator needs `VISIT/CLAIM/DISPUTE_AGENT_ADDRESS`
  and every specialist needs `ORCHESTRATOR_ADDRESS` in `.env` (they answer no one else
  and refuse to start without it). VERIFY against current docs that mailbox agents
  resolve each other.

## 4. How it maps to code

| Concern | File |
|---|---|
| Public agent, chat protocol, acknowledgements, HELLO_WORLD, REST trigger | `fetch/agents/orchestrator.py` |
| Conversation logic (pure) | `fetch/agents/logic/orchestrate.py` |
| Specialists (thin wrappers) | `fetch/agents/{visit,claim,dispute}_agent.py`, `specialist.py` |
| Typed messages (contract) | `shared/python/claimshield_contracts/agent_messages.py` |
| Agent → Core HTTPS tools | `fetch/agents/core_client.py` |
| Per-sender state, request correlation | `fetch/agents/session.py` (uses `ctx.storage`) |
| Agentverse profile text | `fetch/agentverse/agent_profiles/*.md` |

## 5. Troubleshooting

- **Startup handlers run late when offline**: uAgents marks each agent active on Agentverse
  before startup/interval handlers run (10 retries with backoff). Message handlers are not
  affected. Tests patch this call (`fetch/tests/test_uagents_bureau.py`).
- **"No valid reply was sent"** in logs means a handler declared `replies=` but did not send
  one. Specialists always reply with their typed response.
- **Schema digest mismatch / messages ignored**: agents run different contract versions.
  Restart all agents from the same commit (see `docs/BRANCH_WORKFLOW.md`).
