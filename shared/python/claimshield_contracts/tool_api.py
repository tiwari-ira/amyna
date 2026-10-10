"""Schemas for Core's internal tool API (``POST /internal/tools/{tool_name}``).

Agents (Fetch) and the voice manager reach Core data only through these tools.
Each tool is scoped per caller in core/app/auth.py and audited.

``CoreTools`` is the protocol the pure agent logic in ``fetch/agents/logic``
depends on. It has two implementations:

- ``fetch/agents/core_client.py``    -> HTTPS calls to Core (uAgents, local_direct)
- ``core/app/services/inprocess_tools.py`` -> direct calls inside Core
  (``FETCH_MODE=local_direct`` bridge)
"""

from datetime import date
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, Field

from .api import ClaimUpload, HumanResolutionResult
from .enums import (
    Actor,
    AgentName,
    CaseState,
    Channel,
    ClaimSource,
    ConsentType,
    HumanReason,
    InsurerOutcome,
    PatientDecision,
)
from .models import (
    CallSession,
    Case,
    CaseDetail,
    Claim,
    ClaimLine,
    Consent,
    ContractModel,
    Dispute,
    DisputeCandidate,
    DisputeItem,
    EvidenceItem,
    HealthRecord,
    HumanIntervention,
    InputModel,
    InsurerResponse,
    LineAssessment,
    Transcript,
    Visit,
)


class ToolName(StrEnum):
    FIND_OR_CREATE_CASE = "find_or_create_case"
    GET_CASE = "get_case"
    RECORD_CONSENT = "record_consent"
    INGEST_CLAIM = "ingest_claim"
    GET_CASE_CLAIM = "get_case_claim"
    GET_VISIT_TRANSCRIPT = "get_visit_transcript"
    RECORD_VISIT_EVIDENCE = "record_visit_evidence"
    GET_SUPPORTING_EVIDENCE = "get_supporting_evidence"
    GET_PATIENT_CLAIMS = "get_patient_claims"
    GET_PATIENT_ENCOUNTERS = "get_patient_encounters"
    GET_PATIENT_PROCEDURES = "get_patient_procedures"
    GET_PATIENT_MEDICATIONS = "get_patient_medications"
    RECORD_FINDINGS = "record_findings"
    GET_CLAIM_LINE = "get_claim_line"
    GET_DISPUTE_CASE = "get_dispute_case"
    RECORD_DISPUTE = "record_dispute"
    RECORD_PATIENT_DECISION = "record_patient_decision"
    RESOLVE_HUMAN_INTERVENTION = "resolve_human_intervention"
    REQUEST_HUMAN = "request_human"
    START_INSURANCE_CALL = "start_insurance_call"
    RECORD_INSURER_RESPONSE = "record_insurer_response"
    GET_PATIENT_CONTEXT = "get_patient_context"


# --------------------------------------------------------------------------- base


class AgentRunRef(InputModel):
    """Optional provenance: which agent request produced this tool call."""

    agent: AgentName
    request_id: str


class ToolRequest(InputModel):
    case_id: str
    agent_run: AgentRunRef | None = None


# --------------------------------------------------------------------------- case setup


class FindOrCreateCaseRequest(InputModel):
    finchnode_patient_id: str
    channel: Channel
    external_ref: str | None = None
    agent_run: AgentRunRef | None = None


class FindOrCreateCaseResponse(ContractModel):
    case: Case
    created: bool


class GetCaseRequest(ToolRequest):
    pass


class RecordConsentRequest(ToolRequest):
    consent_type: ConsentType
    consent_text: str = Field(min_length=10)
    granted: bool = True
    actor: Actor = Actor.PATIENT
    channel: Channel


class IngestClaimRequest(ToolRequest):
    source: ClaimSource
    finchnode_claim_id: str | None = None
    claim: ClaimUpload | None = None


class GetCaseClaimRequest(ToolRequest):
    pass


# --------------------------------------------------------------------------- visit evidence


class GetVisitTranscriptRequest(ToolRequest):
    visit_id: str | None = None


class VisitTranscriptView(ContractModel):
    visit: Visit | None = None
    transcript: Transcript | None = None


class RecordVisitEvidenceRequest(ToolRequest):
    visit_id: str
    evidence: list[EvidenceItem]


class RecordVisitEvidenceResponse(ContractModel):
    evidence: list[EvidenceItem]
    case_state: CaseState


class GetSupportingEvidenceRequest(ToolRequest):
    line_id: str | None = None


class SupportingEvidence(ContractModel):
    evidence: list[EvidenceItem]
    line_assessment: LineAssessment | None = None
    finding: DisputeCandidate | None = None


# --------------------------------------------------------------------------- FinchNode


class PatientDataRequest(ToolRequest):
    """Least privilege: one tool per FinchNode record type, all consent-gated."""


class PatientRecords(ContractModel):
    records: list[HealthRecord]
    #: False when FinchNode could not provide this record type completely (not shared,
    #: partial sync, source outage...). Absence in ``records`` then proves nothing.
    complete: bool = True
    gap: str | None = None


class PatientClaims(ContractModel):
    claims: list[Claim]


class GetPatientContextRequest(ToolRequest):
    """Ask Core for the case's authorized patient context (prompt task 6).

    The language/agent layers never receive raw patient credentials; Core is the
    only door to private data, and it answers with the minimum, consent-gated set.
    """


class PatientContext(ContractModel):
    """Minimum data a language layer may ever see for a case."""

    case_id: str
    patient_display_name: str | None = None
    member_ref_masked: str | None = None
    payer_name: str | None = None
    finchnode_patient_id: str  # an identifier, not a credential
    has_health_data_consent: bool


# --------------------------------------------------------------------------- analysis


class RecordFindingsRequest(ToolRequest):
    claim_id: str
    findings: list[DisputeCandidate]
    line_assessments: list[LineAssessment]


class RecordFindingsResponse(ContractModel):
    findings: list[DisputeCandidate]
    case_state: CaseState
    open_questions: list[HumanIntervention]


class GetClaimLineRequest(ToolRequest):
    line_id: str


# --------------------------------------------------------------------------- disputes


class GetDisputeCaseRequest(ToolRequest):
    pass


class DisputeCaseView(ContractModel):
    """Minimal, PHI-reduced view of a case for the dispute and voice agents."""

    case_id: str
    case_state: CaseState
    patient_display_name: str | None = None
    member_ref_masked: str | None = None
    payer_name: str | None = None
    claim_number: str | None = None
    provider_name: str | None = None
    date_of_service: date | None = None
    total_billed_cents: int = 0
    dispute: Dispute | None = None
    findings: list[DisputeCandidate] = Field(default_factory=list)
    allowed_codes: list[str] = Field(
        default_factory=list, description="Codes the voice agent may mention (from case evidence)."
    )
    allowed_concepts: list[str] = Field(default_factory=list)


class RecordDisputeRequest(ToolRequest):
    claim_id: str
    items: list[DisputeItem] = Field(min_length=1)
    letter_text: str
    total_disputed_cents: int


class RecordPatientDecisionRequest(ToolRequest):
    """Relays an explicit patient decision captured in chat (ASI:One)."""

    dispute_id: str
    decision: PatientDecision
    authorization_text: str = Field(min_length=10)
    authorize_call: bool = False
    actor: Actor = Actor.PATIENT
    channel: Channel
    reason: str | None = None


class ResolveHumanInterventionRequest(ToolRequest):
    intervention_id: str
    choice: str
    notes: str | None = None
    actor: Actor = Actor.PATIENT
    channel: Channel


# --------------------------------------------------------------------------- calls


class RequestHumanRequest(ToolRequest):
    reason: HumanReason
    detail: str = Field(min_length=3)
    call_id: str | None = None


class StartInsuranceCallRequest(ToolRequest):
    pass


class RecordInsurerResponseRequest(ToolRequest):
    call_id: str
    outcome: InsurerOutcome
    reference_number: str | None = None
    removed_line_ids: list[str] = Field(default_factory=list)
    itemization_requested_line_ids: list[str] = Field(default_factory=list)
    representative_name: str | None = None
    notes: str | None = None


# --------------------------------------------------------------------------- registry

TOOL_SPECS: dict[ToolName, tuple[type[BaseModel], type[BaseModel]]] = {
    ToolName.FIND_OR_CREATE_CASE: (FindOrCreateCaseRequest, FindOrCreateCaseResponse),
    ToolName.GET_CASE: (GetCaseRequest, CaseDetail),
    ToolName.RECORD_CONSENT: (RecordConsentRequest, Consent),
    ToolName.INGEST_CLAIM: (IngestClaimRequest, Claim),
    ToolName.GET_CASE_CLAIM: (GetCaseClaimRequest, Claim),
    ToolName.GET_VISIT_TRANSCRIPT: (GetVisitTranscriptRequest, VisitTranscriptView),
    ToolName.RECORD_VISIT_EVIDENCE: (RecordVisitEvidenceRequest, RecordVisitEvidenceResponse),
    ToolName.GET_SUPPORTING_EVIDENCE: (GetSupportingEvidenceRequest, SupportingEvidence),
    ToolName.GET_PATIENT_CLAIMS: (PatientDataRequest, PatientClaims),
    ToolName.GET_PATIENT_ENCOUNTERS: (PatientDataRequest, PatientRecords),
    ToolName.GET_PATIENT_PROCEDURES: (PatientDataRequest, PatientRecords),
    ToolName.GET_PATIENT_MEDICATIONS: (PatientDataRequest, PatientRecords),
    ToolName.RECORD_FINDINGS: (RecordFindingsRequest, RecordFindingsResponse),
    ToolName.GET_CLAIM_LINE: (GetClaimLineRequest, ClaimLine),
    ToolName.GET_DISPUTE_CASE: (GetDisputeCaseRequest, DisputeCaseView),
    ToolName.RECORD_DISPUTE: (RecordDisputeRequest, Dispute),
    ToolName.RECORD_PATIENT_DECISION: (RecordPatientDecisionRequest, Dispute),
    ToolName.RESOLVE_HUMAN_INTERVENTION: (ResolveHumanInterventionRequest, HumanResolutionResult),
    ToolName.REQUEST_HUMAN: (RequestHumanRequest, HumanIntervention),
    ToolName.START_INSURANCE_CALL: (StartInsuranceCallRequest, CallSession),
    ToolName.RECORD_INSURER_RESPONSE: (RecordInsurerResponseRequest, InsurerResponse),
    ToolName.GET_PATIENT_CONTEXT: (GetPatientContextRequest, PatientContext),
}


# Least-privilege allowlist per agent. Core enforces it whenever a tool call
# carries ``agent_run`` (agents always send it). Agents share the Fetch token,
# so this catches bugs, not a hostile caller; issue per-agent tokens for that.
AGENT_TOOLS: dict[AgentName, frozenset[ToolName]] = {
    AgentName.ORCHESTRATOR: frozenset(
        {
            ToolName.FIND_OR_CREATE_CASE,
            ToolName.GET_CASE,
            ToolName.RECORD_CONSENT,
            ToolName.INGEST_CLAIM,
            ToolName.RESOLVE_HUMAN_INTERVENTION,
            ToolName.RECORD_PATIENT_DECISION,
        }
    ),
    AgentName.VISIT_AGENT: frozenset(
        {
            ToolName.GET_CASE,
            ToolName.GET_PATIENT_CONTEXT,
            ToolName.GET_VISIT_TRANSCRIPT,
            ToolName.GET_SUPPORTING_EVIDENCE,
            ToolName.RECORD_VISIT_EVIDENCE,
        }
    ),
    AgentName.CLAIM_AGENT: frozenset(
        {
            ToolName.GET_CASE,
            ToolName.GET_PATIENT_CONTEXT,
            ToolName.GET_CASE_CLAIM,
            ToolName.GET_SUPPORTING_EVIDENCE,
            ToolName.GET_PATIENT_CLAIMS,
            ToolName.GET_PATIENT_ENCOUNTERS,
            ToolName.GET_PATIENT_PROCEDURES,
            ToolName.GET_PATIENT_MEDICATIONS,
            ToolName.RECORD_FINDINGS,
        }
    ),
    AgentName.DISPUTE_AGENT: frozenset(
        {
            ToolName.GET_CASE,
            ToolName.GET_DISPUTE_CASE,
            ToolName.RECORD_DISPUTE,
            ToolName.START_INSURANCE_CALL,
        }
    ),
    AgentName.VOICE_AGENT: frozenset(
        {
            ToolName.GET_DISPUTE_CASE,
            ToolName.GET_CLAIM_LINE,
            ToolName.GET_SUPPORTING_EVIDENCE,
            ToolName.RECORD_INSURER_RESPONSE,
            ToolName.REQUEST_HUMAN,
            ToolName.GET_PATIENT_CONTEXT,
        }
    ),
}


class CoreToolError(Exception):
    """Raised by any CoreTools implementation when Core rejects a tool call."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(f"{status_code} {code}: {message}")
        self.status_code = status_code
        self.code = code
        self.message = message


class CoreTools(Protocol):
    """Everything agent logic may ask Core to do. One method per tool."""

    def find_or_create_case(self, req: FindOrCreateCaseRequest) -> FindOrCreateCaseResponse: ...
    def get_case(self, req: GetCaseRequest) -> CaseDetail: ...
    def record_consent(self, req: RecordConsentRequest) -> Consent: ...
    def ingest_claim(self, req: IngestClaimRequest) -> Claim: ...
    def get_case_claim(self, req: GetCaseClaimRequest) -> Claim: ...
    def get_patient_context(self, req: GetPatientContextRequest) -> PatientContext: ...
    def get_visit_transcript(self, req: GetVisitTranscriptRequest) -> VisitTranscriptView: ...
    def record_visit_evidence(
        self, req: RecordVisitEvidenceRequest
    ) -> RecordVisitEvidenceResponse: ...
    def get_supporting_evidence(self, req: GetSupportingEvidenceRequest) -> SupportingEvidence: ...
    def get_patient_claims(self, req: PatientDataRequest) -> PatientClaims: ...
    def get_patient_encounters(self, req: PatientDataRequest) -> PatientRecords: ...
    def get_patient_procedures(self, req: PatientDataRequest) -> PatientRecords: ...
    def get_patient_medications(self, req: PatientDataRequest) -> PatientRecords: ...
    def record_findings(self, req: RecordFindingsRequest) -> RecordFindingsResponse: ...
    def get_claim_line(self, req: GetClaimLineRequest) -> ClaimLine: ...
    def get_dispute_case(self, req: GetDisputeCaseRequest) -> DisputeCaseView: ...
    def record_dispute(self, req: RecordDisputeRequest) -> Dispute: ...
    def record_patient_decision(self, req: RecordPatientDecisionRequest) -> Dispute: ...
    def resolve_human_intervention(
        self, req: ResolveHumanInterventionRequest
    ) -> HumanResolutionResult: ...
    def request_human(self, req: RequestHumanRequest) -> HumanIntervention: ...
    def start_insurance_call(self, req: StartInsuranceCallRequest) -> CallSession: ...
    def record_insurer_response(self, req: RecordInsurerResponseRequest) -> InsurerResponse: ...
