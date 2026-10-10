# Going live: per-sponsor key checklist

Everything runs in mock mode with an empty `.env`. To go live, an owner fills in the
keys for **one** integration, flips **one** flag, and runs the check listed for it.
If a live flag is set without its keys, the process exits at startup with a message
naming the missing variable.

Before going live, confirm every item for that integration in
[VERIFY_LIST.md](VERIFY_LIST.md). Unverified sponsor details all live in adapter
files tagged `# VERIFY against current docs`.

---

## 0. Core service tokens (Member 1) — do this first for any shared deployment

| Create | Env var |
|---|---|
| Random token for the Relay service | `RELAY_SERVICE_TOKEN` |
| Random token for the Fetch agents | `FETCH_SERVICE_TOKEN` |
| Random token for voice/telephony callbacks | `VOICE_SERVICE_TOKEN` |
| Random HMAC secret for Relay service → Core events | `CORE_WEBHOOK_SECRET` |

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Set `APP_ENV=prod` to disable the dev fallback tokens. All services read the same `.env`.

---

## 1. Fetch.ai — Agentverse + ASI:One (Member 3) — **do this first, it is the riskiest**

| Step | Env var / flag |
|---|---|
| Create an Agentverse account and an API key | `AGENTVERSE_API_KEY` |
| Generate four seeds (`python -c "import secrets; print(secrets.token_hex(32))"`) | `ORCHESTRATOR_SEED`, `VISIT_AGENT_SEED`, `CLAIM_AGENT_SEED`, `DISPUTE_AGENT_SEED` |
| Print the derived addresses: `make agent-addresses` | `VISIT_AGENT_ADDRESS`, `CLAIM_AGENT_ADDRESS`, `DISPUTE_AGENT_ADDRESS`, `ORCHESTRATOR_ADDRESS` (only needed with `run_all --processes`; the Bureau derives it) |
| Optional public handle for the orchestrator | `ORCHESTRATOR_HANDLE` |
| Discoverability check: `HELLO_WORLD=1`, then `make agents-hello`, connect the mailbox from the Agentverse inspector link, and chat with the agent in ASI:One | `HELLO_WORLD=1` |
| Full workflow through agents | `FETCH_MODE=agentverse`, `HELLO_WORLD=0`, then `make agents` |

Step-by-step: [`fetch/agentverse/README_AGENTVERSE.md`](../fetch/agentverse/README_AGENTVERSE.md).

---

## 2. FinchNode (Member 4)

FinchNode is read-only: it serves the patient's records, never an itemized bill or an
insurer phone. One HTTP API for every online mode (the key decides the data):

| Step | Env var / flag |
|---|---|
| Keyless fictional demo (patients type `patient-demo-001`) | `FINCHNODE_MODE=demo` (base `https://api.finchnode.com/demo/v1`, no key) |
| Sign up at finchnode.com (free), create an application: purpose, categories `demographics, encounters, labs, medications, claims, documents`, sync mode **continuous** | -- |
| Create a sandbox key (`ck_test_…`, shown once) | `FINCHNODE_MODE=sandbox`, `FINCHNODE_API_KEY` (base defaults to `https://api.finchnode.com/api/v1`) |
| Set the application's webhook URL to `{PUBLIC_BASE_URL}/finchnode/webhook`; copy the `whsec_…` signing secret | `FINCHNODE_WEBHOOK_SECRET` |
| Insurer phone (FinchNode has none) | `PAYER_PHONES="Example Health Plan (Synthetic)=+1…"` + `TELEPHONY_ALLOWED_NUMBERS` |
| Production: complete the app settings (privacy policy URL, retention, webhook URL), create a `ck_live_…` key | `FINCHNODE_MODE=production` |
| Check | `python scripts/finchnode_sandbox.py records --subject u_…` prints what Core will read |

Patients link records through FinchNode's hosted **Connect** link (sandbox/production);
the typed-id path remains for mock/demo. Core reads FinchNode only through
`core/app/adapters/finchnode/*`, and only after a `health_data_access` consent on the case.
Walkthrough: [FINCHNODE_SANDBOX_DEMO.md](FINCHNODE_SANDBOX_DEMO.md).

---

## 3. Relay (Member 2)

| Step | Env var / flag |
|---|---|
| Create the Relay agent / app and get its token | `RELAY_AGENT_TOKEN` |
| Configure the webhook URL → `https://<public-host>/webhook/relay` and copy its signing secret | `RELAY_WEBHOOK_SECRET` |
| API base URL (if the SDK/API needs one) | `RELAY_API_BASE_URL` |
| Flip the Relay service to the platform | `RELAY_MODE=live` |
| Let Core push notifications to the Relay service | `NOTIFY_MODE=relay_service`, `RELAY_SERVICE_URL` |
| Check | `make relay`, then send "hi" from the Relay app; the service logs the verified event |

Expose the local service with a tunnel (for example `cloudflared tunnel --url http://localhost:3000`).

---

## 4. xAI Grok Voice (Member 1)

| Step | Env var / flag |
|---|---|
| Create an xAI API key with realtime voice access | `XAI_API_KEY` |
| Confirm the realtime endpoint, model and voice names | `XAI_REALTIME_URL`, `XAI_VOICE_MODEL`, `XAI_VOICE_NAME` (VERIFY) |
| Flip the flag | `XAI_VOICE_MODE=live` |
| Check | `python -m app.adapters.xai_voice.live --check` (from `core/`) opens a session, sends `session.update`, prints the server events, and closes |

---

## 5. Telephony — Telnyx Call Control → xAI SIP (Member 1)

| Step | Env var / flag |
|---|---|
| Telnyx API v2 key (`KEY…`) | `TELNYX_API_KEY` |
| Voice API (Call Control) connection ID | `TELNYX_CONNECTION_ID` |
| Your Telnyx caller number (E.164, owned by the account) | `TELNYX_PHONE_NUMBER` |
| Ed25519 public key (Mission Control → API Keys → Public key) | `TELNYX_PUBLIC_KEY` |
| Public HTTPS URL of this Core (e.g. ngrok static domain) | `PUBLIC_BASE_URL` |
| The SIP URI of xAI's voice bridge | `XAI_SIP_URI` (VERIFY format) |
| Allowlist **only** your own test line | `TELEPHONY_ALLOWED_NUMBERS=+1…` |
| Flip the flag | `TELEPHONY_MODE=live` |

Check credentials without placing a call (read-only, no credits needed):
`python -m app.adapters.telephony.telnyx_sip --check` (from `core/`) lists the
connection's active calls.

How the bridge works (`core/app/adapters/telephony/telnyx_sip.py`):

1. Core POSTs `/v2/calls` (Dial): insurer number as `to` (must be in the
   allowlist), `from=TELNYX_PHONE_NUMBER`, `client_state=base64(call_id)`,
   `webhook_url={PUBLIC_BASE_URL}/voice/telnyx/webhook`.
   Expected webhooks: `call.initiated`, then `call.answered` or `call.hangup`.
2. On `call.answered` for the insurer leg, Core's webhook route
   (`POST /voice/telnyx/webhook`, Ed25519-verified — Telnyx does NOT use the
   service bearer tokens) issues `POST /v2/calls/{call_control_id}/actions/transfer`
   with `to=<XAI_SIP_URI>` — Telnyx's imperative equivalent of a TwiML
   `<Dial><Sip>` bridge (Telnyx has no TwiML). Expected webhooks: `call.initiated` (xAI leg), `call.bridged`,
   `call.answered`/`call.hangup`. **The live path cannot work without this route
   and a stable `PUBLIC_BASE_URL`** (a free ngrok URL changing breaks the bridge).
3. Media flows insurer ⇄ Telnyx ⇄ xAI over SIP; audio never touches Core.
4. Core keeps a control WebSocket to the xAI realtime session for that call
   (instructions, tools, transcripts, tool calls).
5. `call.hangup` for the insurer leg ends the Core call (idempotent); the xAI
   leg (`to=sip:…`) is managed by the control WebSocket.

Fallback if SIP is not available: Telnyx Media Streaming to a Core WebSocket that
relays μ-law audio to the realtime API (not built; documented as an alternative).

---

## 6. Local Qwen3 via Ollama (Member 4, optional)

| Step | Env var / flag |
|---|---|
| Install Ollama and pull a model: `ollama pull qwen3:1.7b` | `OLLAMA_URL`, `OLLAMA_MODEL` |
| Flip the flag | `EXTRACTION_MODE=ollama` |
| Check | `python -m intelligence.extraction.qwen_ollama --demo` |

If Ollama is unreachable or returns invalid JSON, extraction falls back to the
deterministic rules extractor and logs a warning.
