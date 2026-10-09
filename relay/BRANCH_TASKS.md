# branch-1-relay — Member 2 (Relay patient experience)

You own `relay/` (Node 20+ / TypeScript). The service talks **only** to Core's HTTP API
(`src/coreClient.ts`). It never calls FinchNode or Fetch.

## Already working in mock mode

- [x] HTTP server (`src/index.ts`, `src/app.ts`): `POST /webhook/relay`, `POST /internal/notify`, `GET /health`
- [x] Typed Core client generated from Core's OpenAPI (`shared/ts/core.ts`) incl. signed events to `POST /relay/webhook`
- [x] Inbound webhook: signature check (VERIFY scheme), durable idempotency ledger (`relay/data/events.json`), retry on failure
- [x] Retry-safe handlers: a stage advances only after its step succeeds; case / visit / claim ids are saved as soon as Core returns them; events are serialized per patient; Core 4xx → patient message (no retry storm), 5xx → platform retry; malformed events → 400
- [x] Conversation store persisted to `relay/data/conversations.json`
- [x] Flow: welcome → FinchNode id → health-data consent → recording consent → (mock) recording → audio ingest → (mock) transcription → Core extracts evidence → "Review my bill" → claim number → analysis → **one question card at a time** (YES / NO / NOT SURE) → approval card (Review / Approve / Reject) → call → **handoff card** (Done / Cancel) → final report
- [x] Core notifications rendered exactly once whether pushed (`/internal/notify`) or pulled (outbox); marked delivered only after a successful send, so a failed send is retried by the next pull
- [x] Mock Relay adapter (outbox + console), mock transcriber (synthetic demo transcript)
- [x] `npm run demo` drives the whole conversation against a running Core; CI runs it
- [x] vitest: signing parity with Core, ledger, rendering, config, client, full handler flow, failure handling (`tests/resilience.test.ts`)

## To implement for LIVE

1. **Relay adapter** `src/relayAdapter/live.ts`: replace the placeholder endpoints with the
   real Relay SDK/API (send message with quick-reply buttons, start recording, download
   recording). Keep the `RelayAdapter` interface; handlers must not change.
2. **Webhook** `src/webhook.ts`: confirm header names + signing algorithm
   (`verifyPlatformSignature`) and the event payload shape (`parsePlatformEvent`). If Relay
   uses a websocket instead, add `subscribe(onEvent)` to the live adapter and call
   `handleIncoming` for each event.
3. **Transcription** `src/handlers/transcription.ts`: implement `LiveTranscriber` (Relay's
   own transcription if it exists, else an STT provider). Output = Core `VisitComplete`
   with speaker-labelled segments (`clinician` / `staff` / `patient`).
4. **Identity**: replace "type your FinchNode patient ID" with however Relay links a user to
   a FinchNode patient (VERIFY).
5. **Human handoff UX** (`src/handlers/humanHandoff.ts`): handoffs arrive by push while a
   call is paused. Consider a reminder if the patient doesn't tap Done within N minutes, and
   a "join the call" deep link if Relay/Telnyx supports conferencing.
6. Copy polish in `src/ux/messages.ts` (keep button **ids** stable).

## Env vars

`RELAY_MODE=live`, `RELAY_AGENT_TOKEN`, `RELAY_WEBHOOK_SECRET`, `RELAY_API_BASE_URL`,
`RELAY_PORT`, `CORE_BASE_URL`, `RELAY_SERVICE_TOKEN`, `CORE_WEBHOOK_SECRET`.
Ask Member 1 to set `NOTIFY_MODE=relay_service` + `RELAY_SERVICE_URL` on Core so Core pushes
notifications (handoffs during calls need this; mock mode pulls instead).

## Commands

```bash
make setup                 # once
make dev-local             # Core on :8080 (terminal 1)
make relay-demo            # scripted patient conversation through your handlers (terminal 2)
make relay                 # the service on :3000 (tsx watch)
cd relay && npm test && npm run lint
make ts-types              # after Core's API changes on main (then commit shared/ts/core.ts)
```

Live check: tunnel `:3000` (e.g. `cloudflared tunnel --url http://localhost:3000`), point the
Relay webhook at `/webhook/relay`, send "hi" from the Relay app.

## Do not change without a PR to main

`shared/**` (contracts, OpenAPI, generated TS), `core/**`. Need a new Core endpoint or field?
Open a small PR to main first (see `docs/BRANCH_WORKFLOW.md`).

## Definition of done

- [ ] `RELAY_MODE=live` end to end with a real phone: consent → recording → transcript → review → answers → approval → call → handoff → report
- [ ] Platform signature verified against Relay's real scheme; duplicates ignored; failures retried
- [ ] No PHI in logs; recordings only under `relay/data/` (git-ignored)
- [ ] `npm test`, `npm run lint`, `make smoke` green; VERIFY items for Relay ticked in `docs/VERIFY_LIST.md`

## Team build order (yours in **bold**)

1. Fetch hello-world discoverable in ASI:One (M3)
2. Orchestrator ↔ Claim Agent messaging (M3)
3. FinchNode demo → sandbox (M4)
4. **Relay thin UX → Core**
5. Evidence pipeline: recording → transcript → evidence (**M2** + M4)
6. Claim comparison (M4)
7. **Human approval UX**
8. Dispute Agent (M3)
9. Grok Voice + SIP (M1)
10. **Human takeover / handoff** (M1 + **M2**)

Optional last, explicitly low priority, **do not build now**: Grok Imagine avatar.
