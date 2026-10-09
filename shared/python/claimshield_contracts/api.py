"""Request/response bodies for Core's public HTTP API (used by the Relay service,
the smoke test, and any other client). Internal agent tools are in ``tool_api.py``.
"""

from datetime import date, datetime

from pydantic import Field, model_validator

from .enums import (
    Actor,
    CaseState,
    Channel,
    ClaimSource,
    ConsentType,
    HumanReason,
    RelayEventType,
    Speaker,
    TurnAction,
    VoiceEventType,
    WebhookResultStatus,
    WorkStatus,
)
from .models import (
    CallSession,
    CodeRef,
    ContractModel,
    Dispute,
    DisputeCandidate,
    EvidenceItem,
    HumanIntervention,
    InputModel,
    LineAssessment,
    PatientRef,
    Visit,
)

# --------------------------------------------------------------------------- errors


class ErrorDetail(ContractModel):
    """Shape of ``detail`` in every 4xx/5xx error body: ``{"detail": {...}}``."""

    code: str
    message: str


# --------------------------------------------------------------------------- cases


class CaseCreate(InputModel):
    patient: PatientRef
    channel: Channel = Channel.API
    external_ref: str | None = None


class ConsentCreate(InputModel):
    consent_type: ConsentType
    granted: bool = True
    consent_text: str = Field(min_length=10, description="The exact wording the patient agreed to.")
    actor: Actor = Actor.PATIENT
    channel: Channel


# --------------------------------------------------------------------------- visits


class VisitCreate(InputModel):
    case_id: str
    recording_consent_id: str = Field(description="Id of a granted `recording` consent.")
    provider_name: str | None = None
    facility_name: str | None = None
    occurred_at: datetime | None = None
    recording_ref: str | None = Field(default=None, description="URI of the stored recording.")


class TranscriptSegmentIn(InputModel):
    speaker: Speaker = Speaker.UNKNOWN
    speaker_label: str | None = None
    text: str = Field(min_length=1)
    start_ms: int | None = None
    end_ms: int | None = None


class VisitComplete(InputModel):
    language: str = "en"
    source: str = "relay_transcription"
    segments: list[TranscriptSegmentIn] = Field(min_length=1)


class VisitEvidenceResult(ContractModel):
    status: WorkStatus
    case_state: CaseState
    visit: Visit
    evidence: list[EvidenceItem]


# --------------------------------------------------------------------------- claims


class ClaimLineUpload(InputModel):
    line_number: int = Field(ge=1)
    code: CodeRef
    description: str
    date_of_service: date
    units: int = Field(default=1, ge=1)
    billed_cents: int = Field(ge=0)
    modifiers: list[str] = Field(default_factory=list)
    revenue_code: str | None = None
    ndc: str | None = None


class ClaimUpload(InputModel):
    external_claim_id: str
    payer_name: str | None = None
    provider_name: str | None = None
    facility_name: str | None = None
    date_of_service: date
    lines: list[ClaimLineUpload] = Field(min_length=1)


class ClaimIngest(InputModel):
    """Attach a claim to a case, either pulled from FinchNode or uploaded."""

    case_id: str
    source: ClaimSource
    finchnode_claim_id: str | None = None
    claim: ClaimUpload | None = None

    @model_validator(mode="after")
    def _source_matches_payload(self) -> "ClaimIngest":
        if self.source == ClaimSource.FINCHNODE and not self.finchnode_claim_id:
            raise ValueError("source=finchnode requires finchnode_claim_id")
        if self.source == ClaimSource.UPLOAD and self.claim is None:
            raise ValueError("source=upload requires claim")
        return self


class ClaimAnalysisResult(ContractModel):
    status: WorkStatus
    case_state: CaseState
    claim_id: str
    findings: list[DisputeCandidate]
    line_assessments: list[LineAssessment]
    next_question: HumanIntervention | None = None
    total_flagged_cents: int


# --------------------------------------------------------------------------- disputes


class DisputeCreate(InputModel):
    case_id: str


class DisputePreparation(ContractModel):
    """Result of POST /disputes. ``dispute`` is None while agents are still working."""

    status: WorkStatus
    case_state: CaseState
    dispute: Dispute | None = None


class DisputeApproval(InputModel):
    actor: Actor = Actor.PATIENT
    channel: Channel
    authorization_text: str = Field(
        min_length=10, description="Exact authorization wording the patient saw and accepted."
    )
    authorize_call: bool = Field(
        default=True, description="Also authorize Amyna to call the insurer."
    )


class DisputeRejection(InputModel):
    actor: Actor = Actor.PATIENT
    channel: Channel
    reason: str | None = None


# --------------------------------------------------------------------------- humans


class HumanInterventionCreate(InputModel):
    case_id: str
    reason: HumanReason
    prompt: str = Field(min_length=3)
    options: list[str] = Field(default_factory=lambda: ["DONE", "CANCEL"])
    call_id: str | None = None
    finding_id: str | None = None


class HumanInterventionResolve(InputModel):
    choice: str = Field(description="YES/NO/NOT_SURE for confirmations, DONE/CANCEL for handoffs.")
    notes: str | None = Field(
        default=None, description="Free text. Rejected if it contains SSNs, PINs or passwords."
    )
    actor: Actor = Actor.PATIENT
    channel: Channel = Channel.RELAY


class HumanResolutionResult(ContractModel):
    intervention: HumanIntervention
    case_state: CaseState
    next_question: HumanIntervention | None = None
    call: CallSession | None = None


# --------------------------------------------------------------------------- voice


class VoiceStartIn(InputModel):
    case_id: str


class VoiceEventIn(InputModel):
    type: VoiceEventType
    text: str | None = None
    status: str | None = None


class VoiceEventResult(ContractModel):
    action: TurnAction
    call: CallSession
    intervention_id: str | None = None


class HandoffIn(InputModel):
    reason: HumanReason = HumanReason.PATIENT_TAKEOVER
    detail: str = Field(min_length=3)


# --------------------------------------------------------------------------- relay webhook


class RelayWebhookEvent(InputModel):
    """Signed event from the Relay service (HMAC over ``{timestamp}.{raw body}``)."""

    event_id: str = Field(min_length=6, description="Idempotency key; processed at most once.")
    event_type: RelayEventType
    case_id: str
    notification_id: str | None = None
    intervention_id: str | None = None
    dispute_id: str | None = None
    choice: str | None = None
    text: str | None = None
    occurred_at: datetime


class RelayWebhookResult(ContractModel):
    status: WebhookResultStatus
    event_id: str
    detail: str | None = None
    case_state: CaseState | None = None


# --------------------------------------------------------------------------- health


class HealthStatus(ContractModel):
    status: str
    version: str
    contracts_version: str
    app_env: str
    database: str
    modes: dict[str, str]


# --------------------------------------------------------------------- assistant
# The Amyna language layer (core/app/services/assistant_service.py) over HTTP.
# These endpoints are STATELESS: no database access, no private data, no
# authorization decisions -- they understand language and return candidates.


class AssistantUnderstandIn(InputModel):
    message: str = Field(min_length=1, max_length=2000)


class AssistantUnderstandResult(ContractModel):
    intent: str
    # One of the allowlisted workflow nudges (never executed by the LLM layer).
    suggested_action: str
    # Ready-to-send, plain-English reply for the patient (conversational UIs).
    reply: str
    follow_up_question: str | None = None
    # Candidates only: the caller must verify them before acting.
    candidate_claim_number: str | None = None
    candidate_patient_name: str | None = None
    candidate_amount_cents: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)


class AssistantExplainIn(InputModel):
    term: str = Field(min_length=1, max_length=200)


class AssistantTermExplanation(ContractModel):
    term: str
    plain_language: str


# --------------------------------------------------------------------- identity


class AuthSessionCreate(InputModel):
    """Start an Amyna session for a verified identity subject.

    In dev/test/staging this issues a demo session (synthetic identities only).
    In production this endpoint is disabled: the identity provider (e.g. the
    Relay platform's verified user) must supply the Amyna-issued session token.
    """

    subject: str = Field(min_length=3, max_length=120)
    display_name: str | None = Field(default=None, max_length=120)


class AuthSessionResult(ContractModel):
    user_id: str
    patient_token: str = Field(
        description="Opaque bearer token; Core stores only its hash. Not PHI."
    )


# --------------------------------------------------------------------- ops


class JobsDrainResult(ContractModel):
    ran: int
    failed: int


class MetricsSnapshot(ContractModel):
    """GET /internal/metrics. Counts only -- never PHI."""

    requests: dict[str, int] = Field(default_factory=dict)
    latency_avg_ms: dict[str, int] = Field(default_factory=dict)
    queue_depth: dict[str, int] = Field(default_factory=dict)
    active_calls: int = 0
    open_interventions: int = 0
    stuck_cases_24h: int = 0
