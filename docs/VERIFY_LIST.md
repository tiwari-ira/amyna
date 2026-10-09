# VERIFY list

Every sponsor detail the scaffold could not confirm is isolated in an adapter and tagged
`# VERIFY against current docs` (or `// VERIFY` in TypeScript). Check each item against
the sponsor's **current** documentation before flipping that integration's `*_MODE` to live.

Find them all: `grep -rn VERIFY core/app fetch finchnode intelligence relay/src .env.example`

Legend: ☐ = still to verify · ☑ = verified while building the scaffold (how is noted).

## Fetch.ai — uAgents / Agentverse / ASI:One (Member 3)

| | Item | Where |
|---|---|---|
| ☑ | `uagents==0.25.5` exists on PyPI (0.26.0 is newer; re-verify before upgrading) | `fetch/requirements.txt` |
| ☑ | `uagents.Model` is `uagents_core.models.Model`, built on `pydantic.v1.BaseModel`; schema digests are identical with our shim (checked in the installed package; pinned in `agent_message_digests.json`) | `shared/python/claimshield_contracts/agent_messages.py` |
| ☑ | Chat protocol import path `uagents_core.contrib.protocols.chat` (`ChatMessage`, `ChatAcknowledgement`, `TextContent`, `EndSessionContent`, `chat_protocol_spec`, v0.3.0) | `fetch/agents/orchestrator.py:23` |
| ☑ | `Agent(..., mailbox=True, readme_path, description, handle, publish_agent_details)`, `include(..., publish_manifest=True)`, `on_rest_post`, `on_interval`, `ctx.storage` exist in 0.25.5; REST stays served when `mailbox=True`; a Bureau merges agents' REST handlers | `fetch/agents/orchestrator.py`, `core/app/adapters/fetch_bridge/live.py:4` |
| ☑ | Agents in one process dispatch locally (no Almanac needed); covered by `fetch/tests/test_uagents_bureau.py` | `fetch/agents/run_all.py` |
| ☐ | Agentverse inspector → **Connect mailbox** flow, and where the README/manifest appear | `fetch/agentverse/README_AGENTVERSE.md:34` |
| ☐ | Handle rules for `ORCHESTRATOR_HANDLE` (allowed characters, uniqueness, approval) | `README_AGENTVERSE.md:38`, `.env.example` |
| ☐ | ASI:One discovery: how a published agent becomes discoverable/searchable (README keywords? category?) | `fetch/agentverse/agent_profiles/orchestrator.md` |
| ☐ | Separate-process mode: mailbox agents resolving each other without explicit endpoints | `fetch/agents/run_all.py:18`, `README_AGENTVERSE.md:65` |
| ☐ | Whether ASI:One sends `StartSessionContent` / expects `EndSessionContent` (we end the session on terminal replies) | `fetch/agents/orchestrator.py` |
| ☐ | Real FinchNode patient-id format for chat parsing (assumed `fn_pat_…`) | `fetch/agents/intent.py:13` |
| ☐ | Whether `AGENTVERSE_API_KEY` is needed at all (mailbox is connected via the inspector; not read by code) | `.env.example` |

## FinchNode (Member 4)

| | Item | Where |
|---|---|---|
| ☐ | Demo (keyless) and sandbox base URLs | `finchnode/client.py:5`, `.env.example` |
| ☐ | Endpoint paths (`/fhir/Patient/{id}`, `/fhir/{ResourceType}?patient=`) and whether responses are FHIR R4 Bundles | `finchnode/client.py:76-77` |
| ☐ | Auth header for sandbox (`Authorization: Bearer ck_test_…`) and the key prefix | `finchnode/client.py:33,102` |
| ☐ | Pagination (`Bundle.link[relation=next]`) | `finchnode/client.py:138` |
| ☐ | Which resource types exist for claims (Claim vs ExplanationOfBenefit) and medications (MedicationAdministration vs MedicationStatement/Request); `normalize.py` already accepts all of them | `finchnode/normalize.py`, `finchnode/tools.py` |
| ☐ | Coverage → payer name/phone/member id location | `finchnode/normalize.py::normalize_patient` |
| ☐ | Whether an MCP server exists and is worth using | `finchnode/mcp_optional.md` |

## Relay (Member 2)

| | Item | Where |
|---|---|---|
| ☐ | Official SDK vs HTTPS API; npm package name (none is installed on purpose) | `relay/src/relayAdapter/live.ts:4` |
| ☐ | Send-message endpoint, quick-reply button payload format, auth header | `relay/src/relayAdapter/live.ts:26,36` |
| ☐ | Appointment recording support (start, consent prompt, completion callback), recording download auth | `relay/src/relayAdapter/live.ts:41,47` |
| ☐ | Webhook signature header names + algorithm (assumed `x-relay-timestamp` / `x-relay-signature`, HMAC-SHA256 over `ts.body`) | `relay/src/webhook.ts:11,33` |
| ☐ | Webhook event payload shape (assumed `{id, type, from, text, button, recording}`) | `relay/src/webhook.ts:64` |
| ☐ | Events via webhook or websocket (if websocket, add `subscribe()` to the live adapter) | `relay/src/relayAdapter/live.ts:4` |
| ☐ | Whether Relay transcribes recordings (else choose an STT provider) | `relay/src/handlers/transcription.ts:3` |
| ☐ | How a Relay user is linked to a FinchNode patient (demo asks the patient to type it) | `relay/src/handlers/incomingMessage.ts` |

## xAI Grok Voice (Member 1)

| | Item | Where |
|---|---|---|
| ✅ (docs 2026-10-08) | Realtime WS URL `wss://api.x.ai/v1/realtime`, `?model=` (fresh) / `?call_id=` (SIP attach), `Authorization: Bearer` header, default voice `eve` | `core/app/adapters/xai_voice/live.py`, speech-to-speech docs |
| ✅ (docs 2026-10-08) | Session config: `session.update` with `voice`, `instructions`, `turn_detection server_vad`; `response.create` drives turns; per-response `instructions` overrides + `response.cancel` interruption + xAI's `force_message` extension verified | `live.py:93-104,178-206` |
| ☐ (partial) | Exact server event names for transcripts/function calls (`response.function_call_arguments.done`, transcription.completed) — schema described generically; confirm on first live call | `live.py:37-45` |
| ✅ (docs 2026-10-08) | SIP is inbound: register a number, carrier routes to `sip:{number}@sip.voice.x.ai;transport=tls`, xAI posts signed `realtime.call.incoming` webhook with `data.call_id` + `data.sip_headers`, attach WS with `?call_id=`; REFER (`POST /v1/realtime/calls/{call_id}/refer`) for outbound transfer; custom SIP headers are NOT sent on REFER | `adapters/xai_voice/sip_webhook.py`, `routers/voice.py` `/voice/xai/webhook`, `attach_sip_session` |
| ☐ | Whether our carrier preserves the `X-Amyna-Call-Id` SIP header into xAI's inbound webhook (attach correlation); attach is fail-safe otherwise | `telnyx_sip.py`, `routers/voice.py`; first live call decides |
| ☐ | xAI phone-number registration call (BYO trunk) payload + which endpoint; signing-secret rotation flow | registration is account setup; docs/SETUP_KEYS.md |

## Telnyx (Member 1)

| | Item | Where |
|---|---|---|
| ☐ | Voice API connection type (numeric id ↔ Call Control application), `to` as E.164, `client_state` echoed on webhooks | `core/app/adapters/telephony/telnyx_sip.py`, `TELNYX_CONNECTION_ID` |
| ☐ | `transfer` to an external `sip:` URI: `headers` (custom SIP headers) accepted, `call.bridged` semantics (insurer leg ↔ xAI leg) | `core/app/adapters/telephony/telnyx_sip.py` |
| ✅ (docs 2026-10-08) | Headers `telnyx-timestamp` + `telnyx-signature-ed25519`, Ed25519 over timestamp + raw body, public key from Mission Control → API keys → Public key; ack 200 promptly; dedupe by event `id`; replay window — matches `webhook_signature.py` | `core/app/adapters/telephony/webhook_signature.py` |
| ☐ | Exact Call Control payload field names (`client_state`, `timeout_secs`, transfer `to` sip, `bridge` action) — platform docs confirmed the actions exist; verify field names against the consolidated OpenAPI spec (developers.telnyx.com → OpenAPI) before the first live call | `core/app/adapters/telephony/telnyx_sip.py` |
| ☐ | `PUBLIC_BASE_URL` stability (free ngrok URLs can change; webhook_url is set per call at dial time) | `.env` |

## Ollama / Qwen3 (Member 4)

| | Item | Where |
|---|---|---|
| ☐ | `/api/chat` with `format` = JSON schema (structured outputs) and `think: false` for Qwen3 | `intelligence/extraction/qwen_ollama.py:7` |
| ☐ | Model tag you pulled (`qwen3:1.7b` / `qwen3:0.6b`) | `.env.example` (`OLLAMA_MODEL`) |

## CMS hospital price transparency (Member 4)

| | Item | Where |
|---|---|---|
| ☐ | Current template version and column names (`standard_charge|gross`, `code|1|type`, …) and JSON keys | `intelligence/codes/hospital_ingest.py:7` |

## Data licensing (Member 4)

| | Item | Where |
|---|---|---|
| ☐ | CPT descriptors are AMA-licensed: the sample table uses paraphrases; do not bulk-load CPT text | `intelligence/codes/data/README.md` |
| ☐ | RxNorm ids in the sample table (lorazepam 6470, midazolam 6960) before any real use | `intelligence/codes/data/sample_codes.csv` |
