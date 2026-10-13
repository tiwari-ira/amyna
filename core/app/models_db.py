"""SQLAlchemy 2.x tables. Only Core touches these (agents use /internal/tools/*).

Enum values are stored as strings (the contract enums' values). JSON columns hold
``model_dump(mode="json")`` payloads; always reassign them, never mutate in place.
"""

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base, utcnow

ID = String(40)
#: ``cases.finchnode_patient_id`` placeholder while the patient is still in FinchNode
#: Connect (the real subject arrives with consent.granted). Never sent to FinchNode.
PENDING_CONNECT_PREFIX = "pending-connect:"


def _created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class UserRow(Base):
    """Authenticated Amyna accounts. Cases belong to users (task #19)."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    auth_subject: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = _created()


class PatientSessionRow(Base):
    """Opaque session tokens for patient endpoints. Only the hash is stored."""

    __tablename__ = "patient_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = _created()


class FinchLinkRow(Base):
    """A user's authorized link to a FinchNode identity (an identifier, not a credential)."""

    __tablename__ = "finch_links"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    finchnode_patient_id: Mapped[str] = mapped_column(String(80), index=True)
    status: Mapped[str] = mapped_column(String(16), default="linked")
    created_at: Mapped[datetime] = _created()
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CaseRow(Base):
    __tablename__ = "cases"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    state: Mapped[str] = mapped_column(String(32), index=True)
    channel: Mapped[str] = mapped_column(String(16))
    # The owning Amyna account (None for legacy/demo cases).
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), index=True)
    finchnode_patient_id: Mapped[str] = mapped_column(String(80), index=True)
    patient_display_name: Mapped[str | None] = mapped_column(String(200))
    member_ref: Mapped[str | None] = mapped_column(String(80))
    payer_name: Mapped[str | None] = mapped_column(String(200))
    payer_phone: Mapped[str | None] = mapped_column(String(32))
    # The patient's own number, confirmed by the patient, for the human merge leg.
    patient_phone: Mapped[str | None] = mapped_column(String(32))
    patient_state: Mapped[str | None] = mapped_column(String(2))
    external_ref: Mapped[str | None] = mapped_column(String(120))
    failure_reason: Mapped[str | None] = mapped_column(Text)
    # Optimistic locking: every UPDATE checks the version it read, so two concurrent
    # requests can never both apply a transition (e.g. two insurer calls).
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    __mapper_args__ = {"version_id_col": version}


class ConsentRow(Base):
    __tablename__ = "consents"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    consent_type: Mapped[str] = mapped_column(String(32))
    granted: Mapped[bool] = mapped_column(Boolean)
    consent_text: Mapped[str] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(String(16))
    channel: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = _created()


class VisitRow(Base):
    __tablename__ = "visits"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True, unique=True)
    provider_name: Mapped[str | None] = mapped_column(String(200))
    facility_name: Mapped[str | None] = mapped_column(String(200))
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recording_ref: Mapped[str | None] = mapped_column(String(500))
    recording_consent_id: Mapped[str] = mapped_column(ForeignKey("consents.id"))
    status: Mapped[str] = mapped_column(String(32))
    transcript_id: Mapped[str | None] = mapped_column(ID)
    created_at: Mapped[datetime] = _created()


class TranscriptRow(Base):
    __tablename__ = "transcripts"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    visit_id: Mapped[str] = mapped_column(ForeignKey("visits.id"), index=True)
    language: Mapped[str] = mapped_column(String(16))
    source: Mapped[str] = mapped_column(String(64))
    segments: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    created_at: Mapped[datetime] = _created()


class EvidenceRow(Base):
    __tablename__ = "evidence_items"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    visit_id: Mapped[str | None] = mapped_column(ForeignKey("visits.id"), index=True)
    source: Mapped[str] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(32))
    concept: Mapped[str | None] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(Text)
    codes: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    attributes: Mapped[dict[str, str]] = mapped_column(JSON)
    segment_ids: Mapped[list[str]] = mapped_column(JSON)
    quote: Mapped[str | None] = mapped_column(Text)
    negated: Mapped[bool] = mapped_column(Boolean, default=False)
    confidence: Mapped[float] = mapped_column(Float)
    extractor: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = _created()


class ClaimRow(Base):
    __tablename__ = "claims"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True, unique=True)
    external_claim_id: Mapped[str] = mapped_column(String(80), index=True)
    payer_name: Mapped[str | None] = mapped_column(String(200))
    provider_name: Mapped[str | None] = mapped_column(String(200))
    facility_name: Mapped[str | None] = mapped_column(String(200))
    date_of_service: Mapped[date] = mapped_column(Date)
    total_billed_cents: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(16))
    received_at: Mapped[datetime] = _created()


class ClaimLineRow(Base):
    __tablename__ = "claim_lines"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    claim_id: Mapped[str] = mapped_column(ForeignKey("claims.id"), index=True)
    line_number: Mapped[int] = mapped_column(Integer)
    code_system: Mapped[str] = mapped_column(String(16))
    code: Mapped[str] = mapped_column(String(32))
    code_display: Mapped[str | None] = mapped_column(String(300))
    code_source_uri: Mapped[str | None] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(String(300))
    date_of_service: Mapped[date] = mapped_column(Date)
    units: Mapped[int] = mapped_column(Integer, default=1)
    billed_cents: Mapped[int] = mapped_column(Integer)
    modifiers: Mapped[list[str]] = mapped_column(JSON)
    revenue_code: Mapped[str | None] = mapped_column(String(8))
    ndc: Mapped[str | None] = mapped_column(String(20))


class LineAssessmentRow(Base):
    __tablename__ = "line_assessments"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    claim_line_id: Mapped[str] = mapped_column(ForeignKey("claim_lines.id"))
    line_number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32))
    concept: Mapped[str | None] = mapped_column(String(80))
    evidence_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()


class FindingRow(Base):
    __tablename__ = "findings"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    claim_id: Mapped[str] = mapped_column(ForeignKey("claims.id"))
    claim_line_id: Mapped[str] = mapped_column(ForeignKey("claim_lines.id"))
    line_number: Mapped[int] = mapped_column(Integer)
    issue_type: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(300))
    explanation: Mapped[str] = mapped_column(Text)
    # Plain-English wording for the patient (task 12); None = use `explanation`.
    patient_facing_explanation: Mapped[str | None] = mapped_column(Text)
    amount_cents: Mapped[int] = mapped_column(Integer)
    confidence: Mapped[float] = mapped_column(Float)
    human_confirmation_required: Mapped[bool] = mapped_column(Boolean)
    confirmation: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32))
    recommended_action: Mapped[str] = mapped_column(String(32))
    evidence_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    policy_citations: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    patient_answer: Mapped[str | None] = mapped_column(String(16))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class DisputeRow(Base):
    __tablename__ = "disputes"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True, unique=True)
    claim_id: Mapped[str] = mapped_column(ForeignKey("claims.id"))
    status: Mapped[str] = mapped_column(String(32))
    items: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    total_disputed_cents: Mapped[int] = mapped_column(Integer)
    letter_text: Mapped[str] = mapped_column(Text)
    patient_authorization_id: Mapped[str | None] = mapped_column(ID)
    rejected_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class InterventionRow(Base):
    __tablename__ = "human_interventions"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    reason: Mapped[str] = mapped_column(String(40))
    prompt: Mapped[str] = mapped_column(Text)
    options: Mapped[list[str]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), index=True)
    sequence: Mapped[int] = mapped_column(Integer, default=0)
    call_id: Mapped[str | None] = mapped_column(ID, index=True)
    finding_id: Mapped[str | None] = mapped_column(ID)
    decision_id: Mapped[str | None] = mapped_column(ID)
    notified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = _created()
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DecisionRow(Base):
    __tablename__ = "human_decisions"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    decision_type: Mapped[str] = mapped_column(String(32))
    choice: Mapped[str] = mapped_column(String(32))
    actor: Mapped[str] = mapped_column(String(16))
    channel: Mapped[str] = mapped_column(String(16))
    notes: Mapped[str | None] = mapped_column(Text)
    authorization_text: Mapped[str | None] = mapped_column(Text)
    authorize_call: Mapped[bool] = mapped_column(Boolean, default=False)
    intervention_id: Mapped[str | None] = mapped_column(ID)
    dispute_id: Mapped[str | None] = mapped_column(ID)
    finding_id: Mapped[str | None] = mapped_column(ID)
    created_at: Mapped[datetime] = _created()


class CallRow(Base):
    __tablename__ = "call_sessions"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    dispute_id: Mapped[str] = mapped_column(ForeignKey("disputes.id"))
    status: Mapped[str] = mapped_column(String(32))
    provider_call_ref: Mapped[str | None] = mapped_column(String(120))
    # The dialed-and-bridged human leg of a paused call (None when no merge).
    human_call_ref: Mapped[str | None] = mapped_column(String(120))
    # First-class 17A human-merge lifecycle phase (None until a sensitive request).
    merge_phase: Mapped[str | None] = mapped_column(String(32))
    # While PATIENT_BEING_CONNECTED: the safe deadline for the human to answer.
    merge_deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # xAI-side call id after the carrier leg bridges to xAI SIP (task 15).
    xai_call_ref: Mapped[str | None] = mapped_column(String(120))
    # Telnyx conference that holds the insurer, the AI leg and (when needed) the patient.
    conference_ref: Mapped[str | None] = mapped_column(String(120))
    # Telnyx call control id of the outbound leg to the xAI SIP endpoint.
    ai_leg_ref: Mapped[str | None] = mapped_column(String(120))
    # Call start sequence: answered -> playing_authorization -> connecting_assistant
    # -> assistant_connected (or assistant_transferred when conferencing failed).
    media_phase: Mapped[str | None] = mapped_column(String(32))
    # Safety deadline for the current media step (e.g. a playback that never ends).
    media_deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    to_number_masked: Mapped[str | None] = mapped_column(String(32))
    turns: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    insurer_response: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    open_intervention_id: Mapped[str | None] = mapped_column(ID)
    started_at: Mapped[datetime] = _created()
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_reason: Mapped[str | None] = mapped_column(Text)


class AuthorizationRecordingRow(Base):
    """The patient's spoken authorization memo, converted for phone playback (MP3).

    PHI: served only through an unguessable token (only its hash is stored) and
    only while the case has a live insurer call.
    """

    __tablename__ = "authorization_recordings"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), unique=True, index=True)
    audio: Mapped[bytes] = mapped_column(LargeBinary)
    mime_type: Mapped[str] = mapped_column(String(32))
    size_bytes: Mapped[int] = mapped_column(Integer)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = _created()


class AgentRunRow(Base):
    __tablename__ = "agent_runs"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    case_id: Mapped[str | None] = mapped_column(ID, index=True)
    agent: Mapped[str] = mapped_column(String(32))
    request_type: Mapped[str] = mapped_column(String(64))
    request_id: Mapped[str | None] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(16))
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = _created()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NotificationRow(Base):
    __tablename__ = "notifications"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    text: Mapped[str] = mapped_column(Text)
    buttons: Mapped[list[dict[str, str]]] = mapped_column(JSON)
    data: Mapped[dict[str, str]] = mapped_column(JSON)
    intervention_id: Mapped[str | None] = mapped_column(ID)
    dispute_id: Mapped[str | None] = mapped_column(ID)
    delivered: Mapped[bool] = mapped_column(Boolean, default=False)
    delivery_ref: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = _created()


class AuditRow(Base):
    """Append-only audit trail. ``details`` is redacted before insert (audit.py)."""

    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    at: Mapped[datetime] = _created()
    actor: Mapped[str] = mapped_column(String(40))
    action: Mapped[str] = mapped_column(String(80), index=True)
    case_id: Mapped[str | None] = mapped_column(ID, index=True)
    resource_type: Mapped[str | None] = mapped_column(String(40))
    resource_id: Mapped[str | None] = mapped_column(ID)
    details: Mapped[dict[str, Any]] = mapped_column(JSON)


class JobRow(Base):
    """Durable jobs for long-running work (task 28).

    Long operations (transcription, evidence extraction, claim analysis,
    notifications, dispute preparation, voice-call sweeps, external API
    retries) persist here with id, case, correlation id, state, attempt
    count, timeout and retry policy -- so no work depends on one HTTP
    request staying open.
    """

    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    job_type: Mapped[str] = mapped_column(String(64), index=True)
    case_id: Mapped[str | None] = mapped_column(ID, index=True)
    correlation_id: Mapped[str] = mapped_column(String(80), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(120), unique=True)
    state: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    timeout_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FinchConnectSessionRow(Base):
    """A FinchNode hosted Connect session started for a case (the patient's link)."""

    __tablename__ = "finch_connect_sessions"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)  # FinchNode's cs_... id
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    subject: Mapped[str | None] = mapped_column(String(80), index=True)
    url: Mapped[str] = mapped_column(Text)
    environment: Mapped[str | None] = mapped_column(String(16))
    expires_at: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = _created()
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FinchNodeWebhookEventRow(Base):
    """Idempotency ledger for POST /finchnode/webhook (FinchNode retries ~41 h)."""

    __tablename__ = "finchnode_webhook_events"

    event_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(64))
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    received_at: Mapped[datetime] = _created()


class WebhookEventRow(Base):
    """Idempotency ledger for POST /relay/webhook."""

    __tablename__ = "webhook_events"

    event_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(32))
    case_id: Mapped[str | None] = mapped_column(ID)
    result: Mapped[dict[str, Any]] = mapped_column(JSON)
    received_at: Mapped[datetime] = _created()
