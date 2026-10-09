"""Enumerations shared by every Amyna service.

String values are part of the wire contract (HTTP JSON and uAgents messages).
Adding a member is a minor contract change; renaming or removing one is breaking.
"""

from enum import StrEnum


class CaseState(StrEnum):
    """Lifecycle of a case. Transitions are enforced in core/app/state_machine.py."""

    CREATED = "CREATED"
    VISIT_RECORDED = "VISIT_RECORDED"
    TRANSCRIBED = "TRANSCRIBED"
    EVIDENCE_EXTRACTED = "EVIDENCE_EXTRACTED"
    CLAIM_RECEIVED = "CLAIM_RECEIVED"
    ANALYZED = "ANALYZED"
    AWAITING_PATIENT_INPUT = "AWAITING_PATIENT_INPUT"
    FINDINGS_CONFIRMED = "FINDINGS_CONFIRMED"
    DISPUTE_PREPARED = "DISPUTE_PREPARED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    APPROVED = "APPROVED"
    CALL_IN_PROGRESS = "CALL_IN_PROGRESS"
    HUMAN_REQUIRED = "HUMAN_REQUIRED"
    RESOLVED = "RESOLVED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


class Channel(StrEnum):
    """Interface a request or decision came through."""

    RELAY = "relay"
    ASI1 = "asi1"
    API = "api"
    VOICE = "voice"


class Actor(StrEnum):
    """Who performed a human action."""

    PATIENT = "patient"
    CAREGIVER = "caregiver"
    STAFF = "staff"
    SYSTEM = "system"


class ConsentType(StrEnum):
    HEALTH_DATA_ACCESS = "health_data_access"
    RECORDING = "recording"


class Speaker(StrEnum):
    CLINICIAN = "clinician"
    STAFF = "staff"
    PATIENT = "patient"
    UNKNOWN = "unknown"


class VisitStatus(StrEnum):
    RECORDED = "recorded"
    TRANSCRIBED = "transcribed"
    EVIDENCE_EXTRACTED = "evidence_extracted"


class EvidenceSource(StrEnum):
    VISIT_TRANSCRIPT = "visit_transcript"
    FINCHNODE_RECORD = "finchnode_record"
    CLAIM = "claim"
    PATIENT_STATEMENT = "patient_statement"
    POLICY_DOCUMENT = "policy_document"
    INSURER_CALL = "insurer_call"


class EvidenceKind(StrEnum):
    PROCEDURE = "procedure"
    MEDICATION = "medication"
    SERVICE = "service"
    SUPPLY = "supply"


class CodeSystem(StrEnum):
    CPT = "CPT"
    HCPCS = "HCPCS"
    ICD10CM = "ICD10CM"
    RXNORM = "RXNORM"
    NDC = "NDC"
    SNOMED = "SNOMED"
    LOINC = "LOINC"
    LOCAL = "LOCAL"


class MatchMethod(StrEnum):
    """How a code candidate was obtained (strongest first)."""

    SOURCE_RECORD = "source_record"
    EXACT = "exact"
    ALIAS = "alias"
    FUZZY = "fuzzy"


class RecordType(StrEnum):
    ENCOUNTER = "encounter"
    PROCEDURE = "procedure"
    MEDICATION = "medication"
    CONDITION = "condition"


class ClaimSource(StrEnum):
    FINCHNODE = "finchnode"
    UPLOAD = "upload"


class IssueType(StrEnum):
    UNSUPPORTED_CHARGE = "unsupported_charge"
    DUPLICATE_CHARGE = "duplicate_charge"
    UNKNOWN_MEDICATION = "unknown_medication"
    CODE_MISMATCH = "code_mismatch"
    PRICE_ABOVE_PUBLISHED = "price_above_published"


class LineMatchStatus(StrEnum):
    MATCHED = "matched"
    UNSUPPORTED = "unsupported"
    DUPLICATE = "duplicate"
    UNKNOWN = "unknown"
    MISMATCH = "mismatch"
    PRICE_OUTLIER = "price_outlier"


class FindingStatus(StrEnum):
    OPEN = "open"  # waiting for patient confirmation
    CONFIRMED = "confirmed"  # will be disputed
    DISMISSED = "dismissed"  # patient or evidence says the charge is fine
    NEEDS_CLARIFICATION = "needs_clarification"  # dispute as an itemization request


class PatientAnswer(StrEnum):
    YES = "YES"
    NO = "NO"
    NOT_SURE = "NOT_SURE"


class DisputeAction(StrEnum):
    REMOVE_CHARGE = "remove_charge"
    REQUEST_ITEMIZATION = "request_itemization"
    CORRECT_CODE = "correct_code"
    REVIEW_PRICE = "review_price"


class DisputeStatus(StrEnum):
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    RESOLVED = "resolved"
    FAILED = "failed"


class HumanReason(StrEnum):
    """Why a human is needed. Credential/identity reasons always pause a call."""

    PATIENT_CONFIRMATION = "patient_confirmation"
    CREDENTIAL_REQUEST = "credential_request"
    IDENTITY_VERIFICATION = "identity_verification"
    UNKNOWN_MEDICAL_FACT = "unknown_medical_fact"
    CALL_ESCALATION = "call_escalation"
    PATIENT_TAKEOVER = "patient_takeover"
    AGENT_ERROR = "agent_error"


class InterventionStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"
    CANCELLED = "cancelled"


class HandoffChoice(StrEnum):
    """Resolution choices for call-linked interventions."""

    DONE = "DONE"
    CANCEL = "CANCEL"


class DecisionType(StrEnum):
    FINDING_ANSWER = "finding_answer"
    HANDOFF_RESOLUTION = "handoff_resolution"
    DISPUTE_APPROVAL = "dispute_approval"
    DISPUTE_REJECTION = "dispute_rejection"


class CallStatus(StrEnum):
    INITIATING = "initiating"
    IN_PROGRESS = "in_progress"
    PAUSED_FOR_HUMAN = "paused_for_human"
    COMPLETED = "completed"
    FAILED = "failed"


class CallSpeaker(StrEnum):
    INSURER = "insurer"
    AGENT = "agent"
    SYSTEM = "system"
    HUMAN = "human"


class LinkStatus(StrEnum):
    """Lifecycle of a user's link to a FinchNode health-record identity."""

    LINKED = "linked"
    REVOKED = "revoked"


class CallMergePhase(StrEnum):
    """First-class lifecycle of the immediate human merge on a paused call (17A).

    SENSITIVE_REQUEST_DETECTED -> PATIENT_BEING_CONNECTED -> PATIENT_CONNECTED
    -> PATIENT_HANDLING_VERIFICATION -> PATIENT_RELEASED -> AI_RESUMING.
    MERGE_FAILED is the explicit failure escape (the AI is never auto-resumed).
    """

    SENSITIVE_REQUEST_DETECTED = "sensitive_request_detected"
    PATIENT_BEING_CONNECTED = "patient_being_connected"
    PATIENT_CONNECTED = "patient_connected_with_human"
    PATIENT_HANDLING_VERIFICATION = "patient_handling_verification"
    PATIENT_RELEASED = "patient_released"
    AI_RESUMING = "ai_resuming"
    MERGE_FAILED = "merge_failed"


class InsurerOutcome(StrEnum):
    CHARGES_REMOVED = "charges_removed"
    PARTIALLY_REMOVED = "partially_removed"
    DENIED = "denied"
    PENDING_REVIEW = "pending_review"
    CALLBACK_REQUIRED = "callback_required"


class AgentName(StrEnum):
    ORCHESTRATOR = "orchestrator"
    VISIT_AGENT = "visit_agent"
    CLAIM_AGENT = "claim_agent"
    DISPUTE_AGENT = "dispute_agent"
    VOICE_AGENT = "voice_agent"


class AgentRunStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class NotificationKind(StrEnum):
    CONFIRMATION_QUESTION = "confirmation_question"
    APPROVAL_REQUEST = "approval_request"
    HUMAN_HANDOFF = "human_handoff"
    STATUS_UPDATE = "status_update"
    FINAL_REPORT = "final_report"


class RelayEventType(StrEnum):
    """Signed events the Relay service posts to Core's /relay/webhook."""

    BUTTON_REPLY = "button_reply"
    TEXT_REPLY = "text_reply"
    DELIVERY_STATUS = "delivery_status"


class ServiceName(StrEnum):
    """Callers that hold a Core service token."""

    RELAY = "relay"
    FETCH = "fetch"
    VOICE = "voice"


class WorkStatus(StrEnum):
    """Whether delegated agent work finished synchronously or is still running."""

    COMPLETED = "completed"
    PENDING = "pending"


class PatientDecision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


class VoiceEventType(StrEnum):
    """Events posted to POST /voice/{call_id}/event by telephony/voice bridges."""

    INSURER_UTTERANCE = "insurer_utterance"
    CALL_STATUS = "call_status"
    CALL_ENDED = "call_ended"


class TurnAction(StrEnum):
    """What the voice session should do after an insurer utterance."""

    CONTINUE = "continue"
    PAUSE_FOR_HUMAN = "pause_for_human"
    END = "end"


class WebhookResultStatus(StrEnum):
    PROCESSED = "processed"
    DUPLICATE = "duplicate"
    IGNORED = "ignored"
