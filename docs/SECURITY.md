# Security & Safety

> **Prototype disclaimer.** Amyna is a hackathon prototype. It is **not**
> HIPAA-compliant and has not had a security review. Use **synthetic data only**:
> no real patients, member IDs, phone numbers, recordings, or credentials.

## 1. Safety gates enforced in code

| Gate | Where | How it is tested |
|---|---|---|
| No insurer call without an APPROVED dispute **and** a stored `patient_authorization_id` | `core/app/state_machine.py` guard on `APPROVED → CALL_IN_PROGRESS` and `HUMAN_REQUIRED → CALL_IN_PROGRESS` | `core/tests/test_state_machine.py`, `core/tests/test_api_flow.py` (call blocked → 409) |
| Approval must come from the patient with explicit authorization text | `core/app/services/dispute_service.py` (`approve`) | `test_api_flow.py` |
| SSN / PIN / password / auth-code / DOB-verification / card-number requests pause the call and create a `HumanIntervention` | `core/app/services/security_policy.py::detect_sensitive_request`, invoked on **every** insurer utterance by `VoiceCallManager` in addition to the model's own judgement | `core/tests/test_security_policy.py`, `core/tests/test_voice_mock.py` |
| The voice agent cannot state billing codes or medical facts that are not in case evidence | `security_policy.check_agent_utterance` against `AllowedFacts` built from the case | `test_security_policy.py` |
| The voice model cannot pick which case a tool acts on | `core/app/adapters/xai_voice/dispatcher.py` injects `case_id`/`call_id` | `test_voice_mock.py` |
| Core never stores the secrets it refuses to say | `human_service.resolve` rejects notes that contain SSN-like values, PINs, passwords (422 `sensitive_data_rejected`) | `test_security_policy.py` |
| Core only dials allowlisted numbers | `TELEPHONY_ALLOWED_NUMBERS`, checked in `VoiceCallManager.start` for every telephony mode | `test_voice_mock.py` |
| No health data is read before consent | `case_service.require_consent(..., HEALTH_DATA_ACCESS)` before any FinchNode call; `RECORDING` consent before `POST /visits` | `test_validation_and_consent.py` |
| A revoked consent wins | consents are append-only; `case_service.has_consent` uses the **latest** decision per type (a revocation beats an earlier grant, and wins a timestamp tie) | `test_failure_paths.py::test_revoked_consent_blocks_record_access` |
| Approval is an exact command, never inferred | ASI:One: `fetch/agents/intent.py` accepts APPROVE / REJECT / DONE / CANCEL only as the whole message ("I don't approve" is re-prompted); Relay: approval is a button | `fetch/tests/test_intent_and_session.py`, `test_orchestrator_local_direct.py` |
| One insurer call per dispute, even under double taps | optimistic locking on `cases.version` (409 `concurrent_update`) + the state machine (a second start is an illegal transition) | `test_failure_paths.py` |
| A failed call never strands a case | dial / voice-session / resume failures hang up (best effort) and close the call and case as FAILED with a patient notice | `test_failure_paths.py` |
| Cross-case references are rejected | findings, dispute items, interventions and call ids must belong to the case they are recorded on (422) | `test_failure_paths.py` |

## 2. Authentication & authorization

- **Service tokens per caller** (`RELAY_SERVICE_TOKEN`, `FETCH_SERVICE_TOKEN`,
  `VOICE_SERVICE_TOKEN`), sent as `Authorization: Bearer …`. Each identity has an
  explicit scope set in `core/app/auth.py`. For example, the Relay identity cannot
  call `/internal/tools/*`, and the voice identity can only call the five voice
  tools.
- **One token per agent in staging/prod** (`ORCHESTRATOR_SERVICE_TOKEN`,
  `VISIT_AGENT_SERVICE_TOKEN`, `CLAIM_AGENT_SERVICE_TOKEN`,
  `DISPUTE_AGENT_SERVICE_TOKEN`): each agent identity carries exactly its own
  `AGENT_TOOLS` allowlist as scopes, and Core rejects a token that claims to act
  as a different agent. Dev/test falls back to the shared FETCH token.
  Prod refuses to start unless all seven tokens are set and distinct.
- Tokens are compared with `hmac.compare_digest`.
- In `APP_ENV=dev|test`, empty tokens fall back to well-known dev tokens and Core
  logs a warning. In `APP_ENV=prod`, missing tokens are a startup error.
- **Patient sessions expire and can be revoked**: a 24 h sliding window renewed on
  each authenticated request (`POST /auth/revoke` logs the token out; a leaked
  token can be neutralized by its owner at any time).
  `core/tests/test_identity.py`.
- **Per-IP rate limiting + auth-failure circuit** (`core/app/rate_limit.py`,
  `RATE_LIMIT_PER_MINUTE`, default 600): a sliding minute window over all routes
  (except `/health`), plus a lockout after 20 rejected tokens in the window.
  `core/tests/test_rate_limit.py`.
- **Signed webhooks**: `POST /relay/webhook` requires
  `X-Amyna-Timestamp` + `X-Amyna-Signature: v1=<hex HMAC-SHA256(CORE_WEBHOOK_SECRET, "{timestamp}.{raw_body}")>`.
  Requests more than 300 s old are rejected (replay protection). Each `event_id` is
  processed once; duplicates return the stored result (idempotency).
- The Relay service verifies Relay-platform webhooks with `RELAY_WEBHOOK_SECRET`
  (header names / algorithm are **VERIFY** items).
- The orchestrator's REST trigger (Core → Fetch in `FETCH_MODE=agentverse`) carries
  the Fetch service token in the request body, because uAgents REST handlers don't
  expose headers.
- **Agent-to-agent**: specialists answer only `ORCHESTRATOR_ADDRESS` (they refuse to
  start with a mailbox and no address), and the orchestrator accepts a specialist
  reply only from the agent it sent that request to. Within a Bureau the address is
  derived from `ORCHESTRATOR_SEED` automatically.
- **Per-agent tool allowlist** (`AGENT_TOOLS`): enforced by Core when a tool call
  carries `agent_run`. All agents share the Fetch token, so this catches bugs, not a
  hostile caller; issue per-agent tokens before production.
- In `RELAY_MODE=mock` the Relay service skips platform signature verification
  (there is no platform); `live` always verifies. docker-compose therefore publishes
  every port on `127.0.0.1` only (Postgres also uses a default dev password); use a
  tunnel to receive live Relay webhooks.

## 3. PHI handling (prototype level)

- **Structured JSON logs with redaction** (`core/app/audit.py`): a logging filter
  masks SSN-like numbers, phone numbers, emails, member IDs and DOB phrases in every
  log record. Known PHI keys (`display_name`, `member_ref`, `text`, `quote`,
  `notes`, `authorization_text`, …) are replaced with `[REDACTED]` in audit details.
- **Audit trail**: every state transition, every `/internal/tools/*` call
  (caller, tool, case, resource ids — never free text), approval, handoff and call
  event is written to `audit_events`.
- **Data minimization**: the voice agent sees a `DisputeCaseView` with a masked
  member reference and only the dispute's facts.
- **No SQL parameters in errors or logs**: the engine uses `hide_parameters=True`,
  and integrity / stale-data errors return a generic 409 without SQL.
- **Rejections never echo the value**: a choice or note containing an SSN-like value
  is rejected with 422 without repeating it.
- Agent seeds are read only from env, never logged, and git-ignored (`*.seed`, `seeds/`).

## 4. Known limitations (be honest in the demo)

1. **Realtime audio guardrails are partly post-hoc.** With a speech-to-speech model,
   audio is produced together with its transcript, so `check_agent_utterance` runs on
   transcript events and interrupts/pauses the call on a violation. Prevention relies
   on tool-gated facts plus the system prompt (`core/app/adapters/xai_voice/prompts.py`).
2. **Keyword-based sensitive-request detection** may give false positives (the call
   pauses, which is safe) and can miss creative phrasings; the model is also told to
   call `request_human`.
3. **No encryption at rest, no key management, no BAA-covered vendors** — required
   before any real PHI.
4. **ASI:One identity**: chat answers are attributed to the ASI:One sender address;
   there is no strong patient identity verification in the prototype. Anyone who
   knows a FinchNode patient id and claim number can resume that open case from a
   new chat (the demo relies on this to resume across chats). Bind ASI:One senders to
   verified patients before real use.
5. **Single-process mock call state**: mock call cursors live in Core's memory.

## Identity model (tasks 5/19)

- An `fn_pat_...` ID is an identifier, never a credential.
- Cases belong to authenticated accounts (`users`); Relay forwards a patient
  session token (`X-Amyna-Patient-Token`) per patient; Core keeps only the
  token hash (sha256). Owned cases reject any other session (or a bare service
  token) on reads and consent writes (`case_forbidden`).
- Granting health-data consent binds the case's FinchNode identity to the
  owner (`finch_links`, audited as `identity.finch_linked`); all record reads
  remain consent-gated tool calls.
- `POST /auth/session` issues demo sessions for dev/test/staging; production
  disables it (`dev_sessions_disabled`) and in production a Relay-created case
  without a patient session is refused (`patient_session_required`).
- The ASI:One/orchestrator path stays demo-scoped: an Agentverse sender
  address alone proves nothing and never acts on user-bound cases.

## 17A human-merge (sensitive requests)

The insurer call carries an explicit merge lifecycle
(`sensitive_request_detected → patient_being_connected → patient_connected_with_human →
patient_handling_verification → patient_released → ai_resuming`), with
`merge_failed` on dial/timeout failures. The AI never resumes automatically;
only the explicit patient DONE resumes it, and only after the human leg has
been released. An unreachable patient escalates (deadline via
`HUMAN_MERGE_TIMEOUT_S`, swept by a durable job) and gets a plain-language
notification; the insurer leg is kept alive within policy. No sensitive value
ever passes through or comes back (redaction in `security_policy` +
`audit.py`).

## Data at rest and tamper evidence (hardening pass)

- **Spoken authorizations leave the database in prod**
  (`AUTHORIZATION_AUDIO_STORE=file`): the MP3 is Fernet-encrypted
  (authenticated AES) under `AUTHORIZATION_AUDIO_DIR`; the row keeps only a
  storage key. Database dumps and backups never carry spoken PHI.
  `core/app/audio_store.py`, `core/tests/test_audio_store.py`.
- **Backups are encrypted, or they do not exist**: `deploy/backup.sh` pipes
  `pg_dump` through `openssl enc -aes-256-cbc -pbkdf2` and refuses to run
  without `BACKUP_PASSPHRASE`.
- **The audit trail is tamper-evident**: every `audit_events` row chains to the
  previous event of its case (`prev_hash`/`entry_hash` over the full record).
  `audit.verify_audit_chain` detects any later edit or deletion; audit rows
  are never pruned (the retention sweep touches only webhook ledgers and
  delivered notifications). `core/tests/test_audit_chain.py`.
- **Agent protocol attestation at startup**: every agent process verifies its
  uAgents message digests against the committed lock
  (`agent_message_digests.json`) and refuses to serve on drift — a
  version-skewed agent fails loudly instead of silently dropping messages.
  `core/tests/test_voice_cooldown.py`-style tests: `shared/python/tests/test_contracts.py`.
- **Property-based fuzzing** (`hypothesis`) covers the hand-rolled parsers
  (intent routing, PHI masking, sensitive-data detection, money parsing) with
  adversarial inputs and per-example deadlines that double as a ReDoS check.
  It found and fixed real short-value leaks in `mask_phone`/`mask_member_ref`.
  `core/tests/test_property_based.py`.
- **`/docs` and `/openapi.json` do not exist outside dev/test** (they name every
  route, including internal tools, on a public endpoint).
  Postgres connections carry server-side backstops: `statement_timeout=10s`,
  `idle_in_transaction_session_timeout=30s`.
