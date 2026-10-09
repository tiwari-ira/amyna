# Contracts (v1.0.0)

The contract package is `shared/python/claimshield_contracts/` (owned by `main`).
Generated artifacts: `shared/openapi/core.openapi.json` (`make openapi`) and
`shared/ts/core.ts` (`make ts-types`). CI fails if either drifts. Change process:
[BRANCH_WORKFLOW.md](BRANCH_WORKFLOW.md#contract-change-process).

| Module | Contents |
|---|---|
| `enums.py` | Wire enums: `CaseState`, `IssueType`, `EvidenceSource`, `HumanReason`, `FindingStatus`, `DisputeAction`, … |
| `models.py` | Pydantic v2 domain models (`Case`, `Visit`, `Transcript`, `EvidenceItem`, `HealthRecord`, `Claim`, `ClaimLine`, `DisputeCandidate`, `HumanDecision`, `HumanIntervention`, `Dispute`, `CallSession`, `AgentRun`, `CodeCandidate`, `PatientNotification`, `CaseDetail`) |
| `api.py` | Public HTTP request/response bodies |
| `tool_api.py` | `/internal/tools/*` schemas, `TOOL_SPECS`, `AGENT_TOOLS`, `CoreTools` protocol, `CoreToolError` |
| `agent_messages.py` | uAgents messages (pydantic.v1-based; digests pinned in `fixtures/agent_message_digests.json`) |
| `version.py` | `CONTRACTS_VERSION`, `AGENT_PROTOCOL_VERSION` |

Conventions: money = integer cents; ids = prefixed strings (`case_…`, `fnd_…`, `hin_…`);
timestamps = ISO-8601 UTC; every response carries `X-Amyna-Contracts-Version`.

## 1. Authentication

`Authorization: Bearer <token>` with one token per caller. Scopes (`core/app/auth.py`):

| Caller | Token | Scopes |
|---|---|---|
| Relay service | `RELAY_SERVICE_TOKEN` | `cases:read/write`, `consents:write`, `visits:write`, `claims:write`, `claims:analyze`, `disputes:read/write/decide`, `human:read/write`, `voice:start/read/handoff` |
| Fetch agents | `FETCH_SERVICE_TOKEN` | `cases:read` + `tools:<name>` for every tool except `record_insurer_response` |
| Voice / telephony bridge | `VOICE_SERVICE_TOKEN` | `voice:read/events/handoff` + the five voice tools |

`POST /relay/webhook` uses an HMAC signature instead (section 6). `GET /health` is open.

## 2. Public API

| Method | Path | Scope | Body → Response |
|---|---|---|---|
| GET | `/health` | — | → `HealthStatus` |
| POST | `/cases` | `cases:write` | `CaseCreate` → `Case` (201) |
| GET | `/cases/{case_id}` | `cases:read` | → `CaseDetail` |
| POST | `/cases/{case_id}/consents` | `consents:write` | `ConsentCreate` → `Consent` (201) |
| GET | `/cases/{case_id}/timeline` | `cases:read` | → `TimelineEvent[]` |
| GET | `/cases/{case_id}/notifications` | `cases:read` | → `PatientNotification[]` (Core's patient outbox) |
| POST | `/visits` | `visits:write` | `VisitCreate` → `Visit` (201) · needs a granted `recording` consent |
| POST | `/visits/{visit_id}/complete` | `visits:write` | `VisitComplete` → `VisitEvidenceResult` · Visit logic extracts evidence |
| GET | `/visits/{visit_id}/evidence` | `cases:read` | → `EvidenceItem[]` |
| POST | `/claims` | `claims:write` | `ClaimIngest` → `Claim` (201) · `source=finchnode` needs `health_data_access` consent |
| GET | `/claims/{claim_id}` | `cases:read` | → `Claim` |
| POST | `/claims/{claim_id}/analyze` | `claims:analyze` | → `ClaimAnalysisResult` (`status=pending` in `FETCH_MODE=agentverse`) · idempotent |
| POST | `/disputes` | `disputes:write` | `DisputeCreate` → `DisputePreparation` (202 while pending) · idempotent |
| GET | `/disputes/{dispute_id}` | `disputes:read` | → `Dispute` |
| POST | `/disputes/{dispute_id}/approve` | `disputes:decide` | `DisputeApproval` → `Dispute` · actor must be patient/caregiver |
| POST | `/disputes/{dispute_id}/reject` | `disputes:decide` | `DisputeRejection` → `Dispute` |
| POST | `/human-intervention` | `human:write` | `HumanInterventionCreate` → `HumanIntervention` (201) · with `call_id` the call pauses |
| GET | `/human-intervention/{id}` | `human:read` | → `HumanIntervention` |
| POST | `/human-intervention/{id}/resolve` | `human:write` | `HumanInterventionResolve` → `HumanResolutionResult` · notes with SSN/PIN/password → 422 |
| POST | `/voice/start` | `voice:start` | `VoiceStartIn` → `CallSession` (201) · 409 unless APPROVED + authorization |
| GET | `/voice/{call_id}` | `voice:read` | → `CallSession` |
| POST | `/voice/{call_id}/event` | `voice:events` | `VoiceEventIn` → `VoiceEventResult` |
| POST | `/voice/{call_id}/human-handoff` | `voice:handoff` | `HandoffIn` → `HumanIntervention` (201) |
| POST | `/relay/webhook` | HMAC | `RelayWebhookEvent` → `RelayWebhookResult` · idempotent by `event_id` |

Interactive docs: http://localhost:8080/docs (Authorize with a service token).

## 3. Internal tool API — `POST /internal/tools/{tool}`

Every request is a JSON body with `case_id` (except `find_or_create_case`) and an optional
`agent_run: {agent, request_id}`. Agents always send `agent_run`, and Core enforces the
per-agent allowlist `AGENT_TOOLS` on it (this catches bugs; agents share one token, so it
is not a security boundary). Every call is audited.

| Tool | Request → Response | Agents allowed (`AGENT_TOOLS`) |
|---|---|---|
| `find_or_create_case` | `FindOrCreateCaseRequest` → `FindOrCreateCaseResponse` | orchestrator |
| `get_case` | `GetCaseRequest` → `CaseDetail` | orchestrator, visit, claim, dispute |
| `record_consent` | `RecordConsentRequest` → `Consent` | orchestrator |
| `ingest_claim` | `IngestClaimRequest` → `Claim` | orchestrator |
| `get_case_claim` | `GetCaseClaimRequest` → `Claim` | claim |
| `get_visit_transcript` | `GetVisitTranscriptRequest` → `VisitTranscriptView` | visit |
| `record_visit_evidence` | `RecordVisitEvidenceRequest` → `RecordVisitEvidenceResponse` | visit |
| `get_supporting_evidence` | `GetSupportingEvidenceRequest` → `SupportingEvidence` | visit, claim, voice |
| `get_patient_claims` · `get_patient_encounters` · `get_patient_procedures` · `get_patient_medications` | `PatientDataRequest` → `PatientClaims` / `PatientRecords` (consent-gated) | claim |
| `record_findings` | `RecordFindingsRequest` → `RecordFindingsResponse` | claim |
| `get_claim_line` | `GetClaimLineRequest` → `ClaimLine` | voice |
| `get_dispute_case` | `GetDisputeCaseRequest` → `DisputeCaseView` (PHI-reduced) | dispute, voice |
| `record_dispute` | `RecordDisputeRequest` → `Dispute` | dispute |
| `record_patient_decision` | `RecordPatientDecisionRequest` → `Dispute` | orchestrator |
| `resolve_human_intervention` | `ResolveHumanInterventionRequest` → `HumanResolutionResult` | orchestrator |
| `request_human` | `RequestHumanRequest` → `HumanIntervention` | voice |
| `start_insurance_call` | `StartInsuranceCallRequest` → `CallSession` | dispute |
| `record_insurer_response` | `RecordInsurerResponseRequest` → `InsurerResponse` | voice only (not in Fetch scopes) |

`CoreTools` (in `tool_api.py`) has one method per tool. Implementations:
`fetch/agents/core_client.py` (HTTPS) and `core/app/services/inprocess_tools.py` (in-process, `FETCH_MODE=local_direct`).
Both raise `CoreToolError(status_code, code, message)`.

## 4. Agent messages (uAgents, `AGENT_PROTOCOL_VERSION` 1.0.0)

All carry `request_id`, `case_id` and `schema_version`. Enum-like fields are strings.

| Message | Direction | Key fields |
|---|---|---|
| `VisitEvidenceRequest` | orchestrator → Visit | `visit_id?` |
| `VisitEvidenceResponse` | Visit → orchestrator | `ok`, `error?`, `visit_id`, `evidence: EvidenceItemMsg[]`, `case_state?` |
| `ClaimAnalysisRequest` | orchestrator → Claim | `claim_id?` |
| `ClaimAnalysisResponse` | Claim → orchestrator | `ok`, `findings: FindingMsg[]`, `matched_line_ids`, `total_billed_cents`, `total_flagged_cents`, `open_question_ids`, `case_state` |
| `DisputeRequest` | orchestrator → Dispute | `action: "prepare" | "start_call"`, `dispute_id?` |
| `DisputeResponse` | Dispute → orchestrator | `ok`, `dispute_id`, `dispute_status`, `items: DisputeItemMsg[]`, `total_disputed_cents`, `letter_preview`, `call_id`, `call_status`, `open_intervention_id`, `open_intervention_prompt`, `case_state` |
| `OrchestratorTrigger` | Core → orchestrator REST `POST /trigger` | `kind: visit_evidence | claim_analysis | dispute_prepare`, ids, `auth_token` (= Fetch token) |
| `OrchestratorTriggerAck` | response | `accepted`, `request_id?`, `error?` |

Protocols (`fetch/agents/protocols.py`): `claimshield-visit-evidence`, `claimshield-claim-analysis`,
`claimshield-dispute`, `claimshield-orchestrator-replies`. The orchestrator also speaks the
Fetch chat protocol (`AgentChatProtocol` 0.3.0) and always acknowledges.

Sender rules: specialists answer only `ORCHESTRATOR_ADDRESS` (set it with
`make agent-addresses`; the Bureau derives it from `ORCHESTRATOR_SEED`). The orchestrator
accepts a `*Response` only from the agent its `request_id` was sent to. A specialist always
replies: an unexpected error becomes `ok=false` with a generic `error`.

**Changing any field or any message docstring changes the schema digest.** Bump
`AGENT_PROTOCOL_VERSION`, rewrite the lock (`write_digest_lock()`), and restart every agent.

## 5. Logic entry points (Member 3 ↔ Core)

Core's `fetch_bridge` (local_direct) calls these directly, so their signatures are contracts:

```python
fetch.agents.logic.visit_logic.handle_visit_evidence_request(tools, VisitEvidenceRequest, VisitLogicDeps | None) -> VisitEvidenceResponse
fetch.agents.logic.claim_logic.handle_claim_analysis_request(tools, ClaimAnalysisRequest, ClaimLogicDeps | None) -> ClaimAnalysisResponse
fetch.agents.logic.dispute_logic.handle_dispute_request(tools, DisputeRequest) -> DisputeResponse
```

All are idempotent: they return already-recorded evidence, findings or disputes instead of redoing work.

## 6. Signed Relay events (`POST /relay/webhook`)

```
X-Amyna-Timestamp: <unix seconds>
X-Amyna-Signature: v1=<hex HMAC-SHA256(CORE_WEBHOOK_SECRET, "<timestamp>.<raw body>")>
```

Requests more than 300 s old are rejected. `event_id` is processed at most once (a duplicate
returns `status: duplicate`). Python reference: `core/app/auth.py::sign_payload`; TypeScript
reference: `relay/src/webhook.ts::signCoreEvent` (a test pins a shared vector).

| `event_type` | Fields | Effect |
|---|---|---|
| `button_reply` / `text_reply` | `intervention_id`, `choice` (`YES/NO/NOT_SURE`, `DONE/CANCEL`) | resolves the intervention |
| `button_reply` | `dispute_id`, `choice` (`APPROVE/REJECT/REVIEW`) | approves (with Core's authorization wording) / rejects / no-op |
| `delivery_status` | `notification_id`, `choice` (`delivered`) | marks delivery |

## 7. Patient notifications (Core → patient)

Stored in Core's outbox. With `NOTIFY_MODE=relay_service` they are also POSTed to the Relay
service: `POST {RELAY_SERVICE_URL}/internal/notify` with body `{notification, case}` and header
`Authorization: Bearer RELAY_SERVICE_TOKEN`.

| `kind` | Buttons (ids) | Reply goes to |
|---|---|---|
| `confirmation_question` | `YES` `NO` `NOT_SURE` | intervention (one question at a time) |
| `approval_request` | `REVIEW` `APPROVE` `REJECT` | dispute |
| `human_handoff` | `DONE` `CANCEL` | call-linked intervention |
| `status_update`, `final_report` | — | — |

## 8. Errors

Body: `{"detail": {"code": "<code>", "message": "...", ...extra}}`.

| HTTP | Codes |
|---|---|
| 401 | `unauthorized`, `invalid_signature` |
| 403 | `insufficient_scope`, `forbidden` |
| 404 | `not_found` |
| 409 | `illegal_transition`, `guard_failed` (with `from_state`/`to_state`), `consent_required`, `already_analyzed`, `already_resolved`, `already_recorded`, `claim_exists`, `visit_exists`, `transcript_exists`, `dispute_exists`, `not_awaiting_approval`, `not_ready_for_dispute`, `no_dispute`, `call_not_active`, `number_not_allowed`, `safety_blocked`, `concurrent_update` (optimistic lock lost: re-read and retry), `conflict` (unique-constraint violation; SQL is never echoed) |
| 422 | request validation, `invalid_input`, `sensitive_data_rejected` (the offending value is never echoed) |
| 502 | `upstream_error`, `agent_failed`, `orchestrator_unreachable`, `finchnode_unavailable` |

### Retries and idempotency

| Call | On retry |
|---|---|
| `POST /visits/{id}/complete` | the first stored transcript is kept (a retry body is ignored); extraction re-runs only if it had not finished |
| `POST /claims/{id}/analyze`, `POST /disputes` | return the stored result. `status: pending` (agentverse mode) means agents are working and Core will notify the patient |
| `POST /relay/webhook` | `event_id` processed once |
| `POST /voice/start` | a second start is refused (409); the case allows one call |
| Relay service `POST /webhook/relay` | platform event ids are deduped in a durable ledger. If handling fails (Core 5xx or network), the id is released and a 500 asks the platform to retry. Core 4xx → the patient is told and the platform gets 200. Malformed events → 400 |

## 9. Versioning

- HTTP/domain: additive fields with defaults are minor version bumps. Clients ignore unknown
  response fields, while request bodies reject unknown fields.
- Agent messages: any change is breaking for running agents, so bump the version and restart them together.
- `CONTRACTS_VERSION` and `AGENT_PROTOCOL_VERSION` live in `version.py`; `GET /health` reports the former.
