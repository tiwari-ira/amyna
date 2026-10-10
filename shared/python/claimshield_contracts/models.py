"""Pydantic v2 domain models shared by Core, the Fetch agent logic, and tooling.

These are the shapes Core returns over HTTP and the shapes the intelligence
library produces. uAgents messages live in ``agent_messages.py`` (pydantic.v1,
see docs/ARCHITECTURE.md decision D1).

Conventions: money is integer cents, ids are prefixed strings (``ids.py``),
datetimes are timezone-aware UTC.
"""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from .enums import (
    Actor,
    AgentName,
    AgentRunStatus,
    CallMergePhase,
    CallSpeaker,
    CallStatus,
    CaseState,
    Channel,
    ClaimSource,
    CodeSystem,
    ConsentType,
    DecisionType,
    DisputeAction,
    DisputeStatus,
    EvidenceKind,
    EvidenceSource,
    FindingStatus,
    HumanReason,
    InsurerOutcome,
    InterventionStatus,
    IssueType,
    LineMatchStatus,
    LinkStatus,
    MatchMethod,
    NotificationKind,
    PatientAnswer,
    RecordType,
    Speaker,
    VisitStatus,
)


class ContractModel(BaseModel):
    """Base for domain/response models. Unknown fields are ignored (forward compatible)."""

    model_config = ConfigDict(extra="ignore")


class InputModel(BaseModel):
    """Base for request bodies. Unknown fields are rejected to catch client typos."""

    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------- codes


class CodeRef(ContractModel):
    """A code exactly as it appears in a source record or claim line."""

    system: CodeSystem
    code: str
    display: str | None = None
    source_system_uri: str | None = Field(
        default=None, description="Original coding-system URI preserved from the source record."
    )


class CodeCandidate(ContractModel):
    """A ranked code suggestion produced by the code resolver."""

    system: CodeSystem
    code: str
    display: str
    concept: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    match_method: MatchMethod


# --------------------------------------------------------------------------- case


class PatientRef(ContractModel):
    """Minimal patient reference. Demographics are enriched from FinchNode after consent."""

    finchnode_patient_id: str = Field(min_length=1, max_length=80)
    display_name: str | None = Field(default=None, max_length=200)
    member_ref: str | None = Field(default=None, max_length=80)
    payer_name: str | None = Field(default=None, max_length=200)
    payer_phone: str | None = Field(default=None, max_length=32)
    state: str | None = Field(
        default=None, min_length=2, max_length=2, description="Two-letter US state."
    )


class Consent(ContractModel):
    consent_id: str
    case_id: str
    consent_type: ConsentType
    granted: bool
    consent_text: str
    actor: Actor
    channel: Channel
    created_at: datetime


class Case(ContractModel):
    case_id: str
    state: CaseState
    channel: Channel
    patient: PatientRef
    external_ref: str | None = None
    #: Masked patient callback number for the human merge (None = not given yet).
    callback_phone_masked: str | None = None
    #: False while the patient is still linking records through FinchNode Connect.
    records_linked: bool = True
    created_at: datetime
    updated_at: datetime


# --------------------------------------------------------------------------- visit


class TranscriptSegment(ContractModel):
    segment_id: str
    speaker: Speaker = Speaker.UNKNOWN
    speaker_label: str | None = None
    text: str
    start_ms: int | None = None
    end_ms: int | None = None


class Transcript(ContractModel):
    transcript_id: str
    visit_id: str
    language: str = "en"
    source: str
    segments: list[TranscriptSegment]
    created_at: datetime | None = None

    def full_text(self) -> str:
        """Return the transcript as plain text, one segment per line."""
        return "\n".join(segment.text for segment in self.segments)


class Visit(ContractModel):
    visit_id: str
    case_id: str
    provider_name: str | None = None
    facility_name: str | None = None
    occurred_at: datetime | None = None
    recording_ref: str | None = None
    recording_consent_id: str
    status: VisitStatus
    transcript_id: str | None = None
    created_at: datetime


class EvidenceItem(ContractModel):
    """A fact extracted from appointment evidence, e.g. 'lorazepam 1 mg IV was given'."""

    evidence_id: str
    case_id: str
    visit_id: str | None = None
    source: EvidenceSource = EvidenceSource.VISIT_TRANSCRIPT
    kind: EvidenceKind
    concept: str | None = Field(
        default=None, description="Normalized concept key from the code table, e.g. 'lorazepam'."
    )
    description: str
    codes: list[CodeCandidate] = Field(default_factory=list)
    attributes: dict[str, str] = Field(default_factory=dict)
    segment_ids: list[str] = Field(default_factory=list)
    quote: str | None = None
    negated: bool = False
    confidence: float = Field(ge=0.0, le=1.0)
    extractor: str
    created_at: datetime | None = None


# --------------------------------------------------------------------------- FinchNode


class HealthRecord(ContractModel):
    """A normalized FinchNode record. ``codes`` are preserved verbatim from the source."""

    record_id: str
    record_type: RecordType
    patient_id: str
    occurred_on: date | None = None
    display: str
    codes: list[CodeRef] = Field(default_factory=list)
    status: str | None = None
    facility: str | None = None
    performer: str | None = None
    encounter_ref: str | None = None
    attributes: dict[str, str] = Field(default_factory=dict)
    source: str = "finchnode"
    source_resource_type: str
    source_id: str


# --------------------------------------------------------------------------- claims


class ClaimLine(ContractModel):
    line_id: str
    claim_id: str
    line_number: int
    code: CodeRef
    description: str
    date_of_service: date
    units: int = 1
    billed_cents: int
    modifiers: list[str] = Field(default_factory=list)
    revenue_code: str | None = None
    ndc: str | None = None


class Claim(ContractModel):
    claim_id: str
    case_id: str | None = None
    external_claim_id: str
    payer_name: str | None = None
    provider_name: str | None = None
    facility_name: str | None = None
    date_of_service: date
    total_billed_cents: int
    source: ClaimSource
    lines: list[ClaimLine]
    received_at: datetime | None = None


# --------------------------------------------------------------------------- findings


class EvidenceRef(ContractModel):
    """Pointer to the evidence behind a finding (what was checked and where)."""

    source: EvidenceSource
    ref_id: str
    description: str
    quote: str | None = None


class PolicyCitation(ContractModel):
    doc_id: str
    title: str
    section: str | None = None
    source: str
    jurisdiction: str
    doc_type: str
    effective_date: date | None = None
    url: str | None = None
    snippet: str
    score: float


class ConfirmationQuestion(ContractModel):
    """One clean patient question plus how each answer changes the finding."""

    question: str
    options: list[PatientAnswer] = Field(
        default_factory=lambda: [PatientAnswer.YES, PatientAnswer.NO, PatientAnswer.NOT_SURE]
    )
    effects: dict[PatientAnswer, FindingStatus]


class LineAssessment(ContractModel):
    claim_line_id: str
    line_number: int
    status: LineMatchStatus
    concept: str | None = None
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    note: str | None = None


class DisputeCandidate(ContractModel):
    """A potential billing discrepancy ("finding") with evidence and confidence."""

    finding_id: str
    case_id: str
    claim_id: str
    claim_line_id: str
    line_number: int
    issue_type: IssueType
    title: str
    explanation: str
    # Plain-English wording for the patient (task 12). Falls back to
    # ``explanation`` when None; the language layer may never invent facts,
    # it can only restate this and the evidence refs.
    patient_facing_explanation: str | None = None
    amount_cents: int
    confidence: float = Field(ge=0.0, le=1.0)
    human_confirmation_required: bool
    confirmation: ConfirmationQuestion | None = None
    status: FindingStatus
    recommended_action: DisputeAction
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    policy_citations: list[PolicyCitation] = Field(default_factory=list)
    patient_answer: PatientAnswer | None = None
    created_at: datetime | None = None


# --------------------------------------------------------------------------- humans


class HumanDecision(ContractModel):
    """A recorded human decision. Approval decisions double as patient authorizations."""

    decision_id: str
    case_id: str
    decision_type: DecisionType
    choice: str
    actor: Actor
    channel: Channel
    notes: str | None = None
    authorization_text: str | None = None
    authorize_call: bool = False
    intervention_id: str | None = None
    dispute_id: str | None = None
    finding_id: str | None = None
    created_at: datetime


class HumanIntervention(ContractModel):
    """A request for human input: a confirmation question or a call handoff."""

    intervention_id: str
    case_id: str
    reason: HumanReason
    prompt: str
    options: list[str]
    status: InterventionStatus
    call_id: str | None = None
    finding_id: str | None = None
    notified: bool = False
    created_at: datetime
    resolved_at: datetime | None = None
    resolution: HumanDecision | None = None


# --------------------------------------------------------------------------- disputes


class DisputeItem(ContractModel):
    finding_id: str
    claim_line_id: str
    line_number: int
    issue_type: IssueType
    action: DisputeAction
    amount_cents: int
    summary: str
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    policy_citations: list[PolicyCitation] = Field(default_factory=list)


class Dispute(ContractModel):
    dispute_id: str
    case_id: str
    claim_id: str
    status: DisputeStatus
    items: list[DisputeItem]
    total_disputed_cents: int
    letter_text: str
    patient_authorization_id: str | None = None
    rejected_reason: str | None = None
    created_at: datetime


# --------------------------------------------------------------------- identity


class UserAccount(ContractModel):
    """An authenticated Amyna account. Cases belong to users, not to typed IDs."""

    user_id: str
    auth_subject: str
    display_name: str | None = None
    created_at: datetime


class FinchLink(ContractModel):
    """A user's authorized connection to a FinchNode patient identity (§5)."""

    link_id: str
    user_id: str
    finchnode_patient_id: str  # an identifier, never a credential
    status: LinkStatus
    created_at: datetime
    revoked_at: datetime | None = None

    approved_at: datetime | None = None


# --------------------------------------------------------------------------- calls


class CallTurn(ContractModel):
    speaker: CallSpeaker
    text: str
    at: datetime
    flags: list[str] = Field(default_factory=list)


class InsurerResponse(ContractModel):
    outcome: InsurerOutcome
    reference_number: str | None = None
    removed_line_ids: list[str] = Field(default_factory=list)
    itemization_requested_line_ids: list[str] = Field(default_factory=list)
    representative_name: str | None = None
    notes: str | None = None
    recorded_at: datetime


class CallSession(ContractModel):
    call_id: str
    case_id: str
    dispute_id: str
    status: CallStatus
    # First-class 17A human-merge lifecycle (None until a sensitive request).
    merge_phase: CallMergePhase | None = None
    provider_call_ref: str | None = None
    to_number_masked: str | None = None
    turns: list[CallTurn] = Field(default_factory=list)
    insurer_response: InsurerResponse | None = None
    open_intervention_id: str | None = None
    started_at: datetime
    ended_at: datetime | None = None
    end_reason: str | None = None


# --------------------------------------------------------------------------- ops


class AgentRun(ContractModel):
    run_id: str
    case_id: str | None = None
    agent: AgentName
    request_type: str
    request_id: str | None = None
    status: AgentRunStatus
    error: str | None = None
    started_at: datetime
    finished_at: datetime | None = None


class NotificationButton(ContractModel):
    id: str
    label: str


class PatientNotification(ContractModel):
    """A message Core wants delivered to the patient (rendered by the Relay service)."""

    notification_id: str
    case_id: str
    kind: NotificationKind
    text: str
    buttons: list[NotificationButton] = Field(default_factory=list)
    data: dict[str, str] = Field(default_factory=dict)
    intervention_id: str | None = None
    dispute_id: str | None = None
    delivered: bool = False
    delivery_ref: str | None = None
    created_at: datetime


class TimelineEvent(ContractModel):
    at: datetime
    kind: str
    action: str
    actor: str
    summary: str


class CaseDetail(Case):
    """Everything Core knows about a case. Returned by GET /cases/{id} and tool get_case."""

    consents: list[Consent] = Field(default_factory=list)
    visit: Visit | None = None
    evidence: list[EvidenceItem] = Field(default_factory=list)
    claim: Claim | None = None
    line_assessments: list[LineAssessment] = Field(default_factory=list)
    findings: list[DisputeCandidate] = Field(default_factory=list)
    dispute: Dispute | None = None
    interventions: list[HumanIntervention] = Field(default_factory=list)
    calls: list[CallSession] = Field(default_factory=list)
    agent_runs: list[AgentRun] = Field(default_factory=list)

    def open_interventions(self) -> list[HumanIntervention]:
        """Interventions still waiting for a human, oldest first."""
        return [i for i in self.interventions if i.status == InterventionStatus.OPEN]

    def has_consent(self, consent_type: ConsentType) -> bool:
        """True if a granted consent of this type exists."""
        return any(c.consent_type == consent_type and c.granted for c in self.consents)
