"""Case lifecycle: creation, lookup, consents, guarded transitions, read models."""

import logging
from typing import TYPE_CHECKING

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from claimshield_contracts.enums import (
    Actor,
    CaseState,
    Channel,
    ConsentType,
    DisputeStatus,
    FindingStatus,
    HumanReason,
    InterventionStatus,
)
from claimshield_contracts.ids import new_id
from claimshield_contracts.models import CaseDetail, PatientRef, TimelineEvent

from .. import mappers
from ..audit import record_audit
from ..db import as_utc
from ..errors import ConsentRequired, DomainError, NotFound
from ..models_db import (
    AgentRunRow,
    AuditRow,
    CallRow,
    CaseRow,
    ClaimLineRow,
    ClaimRow,
    ConsentRow,
    DecisionRow,
    DisputeRow,
    EvidenceRow,
    FindingRow,
    InterventionRow,
    LineAssessmentRow,
    NotificationRow,
    TranscriptRow,
    VisitRow,
)
from ..state_machine import TERMINAL_STATES, GuardContext, check_transition
from .security_policy import reject_sensitive_notes

if TYPE_CHECKING:
    from ..container import Container

DISPUTABLE = (FindingStatus.CONFIRMED.value, FindingStatus.NEEDS_CLARIFICATION.value)
log = logging.getLogger("claimshield.cases")


# --------------------------------------------------------------------------- creation / lookup


def create_case(
    db: Session,
    *,
    patient: PatientRef,
    channel: Channel,
    external_ref: str | None,
    actor: str,
    owner_id: str | None = None,
) -> CaseRow:
    case = CaseRow(
        id=new_id("case"),
        state=CaseState.CREATED.value,
        channel=channel.value,
        user_id=owner_id,
        finchnode_patient_id=patient.finchnode_patient_id,
        patient_display_name=patient.display_name,
        member_ref=patient.member_ref,
        payer_name=patient.payer_name,
        payer_phone=patient.payer_phone,
        patient_state=patient.state,
        external_ref=external_ref,
    )
    db.add(case)
    db.flush()
    record_audit(
        db,
        actor=actor,
        action="case.created",
        case_id=case.id,
        resource_type="case",
        resource_id=case.id,
        details={"channel": channel.value},
    )
    return case


def get_case_row(db: Session, case_id: str) -> CaseRow:
    case = db.get(CaseRow, case_id)
    if case is None:
        raise NotFound(f"Case {case_id} not found")
    return case


def open_cases(db: Session, finchnode_patient_id: str) -> list[CaseRow]:
    """Non-terminal cases for a patient, newest first."""
    terminal = [s.value for s in TERMINAL_STATES]
    stmt = (
        select(CaseRow)
        .where(CaseRow.finchnode_patient_id == finchnode_patient_id)
        .where(CaseRow.state.not_in(terminal))
        .order_by(CaseRow.created_at.desc())
    )
    return list(db.scalars(stmt))


def find_or_create_case(
    db: Session,
    *,
    finchnode_patient_id: str,
    channel: Channel,
    external_ref: str | None,
    actor: str,
) -> tuple[CaseRow, bool]:
    """Reuse an open case for THIS claim, else create one (decision D18).

    With a claim number, a case matches if it is tagged with it or already holds that
    claim; otherwise an open case with no claim yet (e.g. a recorded visit waiting for
    its bill) is adopted. A case holding a different claim is never reused.
    """
    candidates = open_cases(db, finchnode_patient_id)
    claims = {
        row.case_id: row.external_claim_id
        for row in db.scalars(
            select(ClaimRow).where(ClaimRow.case_id.in_([c.id for c in candidates]))
        )
    }
    if external_ref is None and candidates:
        return candidates[0], False
    for case in candidates:
        if external_ref in (case.external_ref, claims.get(case.id)):
            return case, False
    for case in candidates:
        if case.id not in claims and case.external_ref in (None, external_ref):
            case.external_ref = external_ref
            return case, False
    patient = PatientRef(finchnode_patient_id=finchnode_patient_id)
    return (
        create_case(db, patient=patient, channel=channel, external_ref=external_ref, actor=actor),
        True,
    )


# --------------------------------------------------------------------------- consent


def latest_consent(db: Session, case_id: str, consent_type: ConsentType) -> ConsentRow | None:
    """The patient's most recent decision for this consent type (grants AND revocations)."""
    stmt = (
        select(ConsentRow)
        .where(ConsentRow.case_id == case_id, ConsentRow.consent_type == consent_type.value)
        # On a timestamp tie the revocation sorts first (fail safe).
        .order_by(ConsentRow.created_at.desc(), ConsentRow.granted.asc())
        .limit(1)
    )
    return db.scalars(stmt).first()


def has_consent(db: Session, case_id: str, consent_type: ConsentType) -> bool:
    """True only if the latest decision is a grant (a later revocation wins)."""
    latest = latest_consent(db, case_id, consent_type)
    return bool(latest and latest.granted)


def require_consent(db: Session, case_id: str, consent_type: ConsentType) -> None:
    if not has_consent(db, case_id, consent_type):
        raise ConsentRequired(
            f"Patient consent '{consent_type.value}' is required for this action.",
            consent_type=consent_type.value,
        )


def record_consent(
    db: Session,
    container: "Container",
    *,
    case_id: str,
    consent_type: ConsentType,
    granted: bool,
    consent_text: str,
    actor_kind: Actor,
    channel: Channel,
    actor: str,
) -> ConsentRow:
    case = get_case_row(db, case_id)
    reject_sensitive_notes(consent_text)  # chat channels embed the patient's own words
    consent = ConsentRow(
        id=new_id("consent"),
        case_id=case.id,
        consent_type=consent_type.value,
        granted=granted,
        consent_text=consent_text,
        actor=actor_kind.value,
        channel=channel.value,
    )
    db.add(consent)
    db.flush()
    record_audit(
        db,
        actor=actor,
        action="consent.recorded",
        case_id=case.id,
        resource_type="consent",
        resource_id=consent.id,
        details={"consent_type": consent_type.value, "granted": granted, "channel": channel.value},
    )
    if granted and consent_type == ConsentType.HEALTH_DATA_ACCESS:
        enrich_patient(db, container, case)
    return consent


def enrich_patient(db: Session, container: "Container", case: CaseRow) -> None:
    """Fill blank patient fields from FinchNode (only ever called after consent).

    Best effort: a FinchNode outage must not undo the consent being recorded.
    """
    try:
        profile = container.finchnode.get_patient_profile(case.finchnode_patient_id)
    except DomainError:
        log.warning("FinchNode enrichment skipped", extra={"fields": {"case_id": case.id}})
        return
    if profile is None:
        return
    case.patient_display_name = case.patient_display_name or profile.display_name
    case.member_ref = case.member_ref or profile.member_ref
    case.payer_name = case.payer_name or profile.payer_name
    case.payer_phone = case.payer_phone or profile.payer_phone
    case.patient_state = case.patient_state or profile.state
    record_audit(db, actor="core", action="case.enriched_from_finchnode", case_id=case.id)


# --------------------------------------------------------------------------- transitions


def _count(db: Session, model: type, *conditions: object) -> int:
    return int(db.scalar(select(func.count()).select_from(model).where(*conditions)) or 0)


def latest_dispute(db: Session, case_id: str) -> DisputeRow | None:
    stmt = (
        select(DisputeRow)
        .where(DisputeRow.case_id == case_id)
        .order_by(DisputeRow.created_at.desc())
        .limit(1)
    )
    return db.scalars(stmt).first()


def build_guard_context(db: Session, case: CaseRow) -> GuardContext:
    """Collect the facts the state-machine guards check."""
    dispute = latest_dispute(db, case.id)
    has_transcript = bool(
        db.scalar(
            select(
                exists()
                .where(TranscriptRow.visit_id == VisitRow.id)
                .where(VisitRow.case_id == case.id)
            )
        )
    )
    return GuardContext(
        has_visit=_count(db, VisitRow, VisitRow.case_id == case.id) > 0,
        has_transcript=has_transcript,
        has_claim=_count(db, ClaimRow, ClaimRow.case_id == case.id) > 0,
        findings_count=_count(db, FindingRow, FindingRow.case_id == case.id),
        open_confirmation_count=_count(
            db,
            InterventionRow,
            InterventionRow.case_id == case.id,
            InterventionRow.reason == HumanReason.PATIENT_CONFIRMATION.value,
            InterventionRow.status == InterventionStatus.OPEN.value,
        ),
        disputable_findings_count=_count(
            db, FindingRow, FindingRow.case_id == case.id, FindingRow.status.in_(DISPUTABLE)
        ),
        dispute_status=DisputeStatus(dispute.status) if dispute else None,
        patient_authorization_id=dispute.patient_authorization_id if dispute else None,
        open_call_interventions=_count(
            db,
            InterventionRow,
            InterventionRow.case_id == case.id,
            InterventionRow.call_id.is_not(None),
            InterventionRow.status == InterventionStatus.OPEN.value,
        ),
        has_insurer_response=_count(
            db, CallRow, CallRow.case_id == case.id, CallRow.insurer_response.is_not(None)
        )
        > 0,
    )


def transition(
    db: Session, case: CaseRow, target: CaseState, *, actor: str, reason: str | None = None
) -> None:
    """Apply a guarded transition and audit it. Raises TransitionError (409)."""
    db.flush()
    current = CaseState(case.state)
    check_transition(current, target, build_guard_context(db, case))
    case.state = target.value
    if target == CaseState.FAILED:
        case.failure_reason = reason
    record_audit(
        db,
        actor=actor,
        action="case.transition",
        case_id=case.id,
        details={"from": current.value, "to": target.value, "reason": reason},
    )


# --------------------------------------------------------------------------- read models


def get_case_detail(db: Session, case_id: str) -> CaseDetail:
    case = get_case_row(db, case_id)
    consents = db.scalars(select(ConsentRow).where(ConsentRow.case_id == case_id)).all()
    visit = db.scalars(select(VisitRow).where(VisitRow.case_id == case_id)).first()
    evidence = db.scalars(select(EvidenceRow).where(EvidenceRow.case_id == case_id)).all()
    claim = db.scalars(select(ClaimRow).where(ClaimRow.case_id == case_id)).first()
    lines = (
        db.scalars(select(ClaimLineRow).where(ClaimLineRow.claim_id == claim.id)).all()
        if claim
        else []
    )
    assessments = db.scalars(
        select(LineAssessmentRow)
        .where(LineAssessmentRow.case_id == case_id)
        .order_by(LineAssessmentRow.line_number)
    ).all()
    findings = db.scalars(
        select(FindingRow).where(FindingRow.case_id == case_id).order_by(FindingRow.line_number)
    ).all()
    interventions = db.scalars(
        select(InterventionRow)
        .where(InterventionRow.case_id == case_id)
        .order_by(InterventionRow.created_at, InterventionRow.sequence)
    ).all()
    decisions = {
        d.id: d for d in db.scalars(select(DecisionRow).where(DecisionRow.case_id == case_id))
    }
    calls = db.scalars(
        select(CallRow).where(CallRow.case_id == case_id).order_by(CallRow.started_at)
    ).all()
    runs = db.scalars(
        select(AgentRunRow).where(AgentRunRow.case_id == case_id).order_by(AgentRunRow.started_at)
    ).all()
    dispute = latest_dispute(db, case_id)
    base = mappers.case_to_model(case)
    return CaseDetail(
        **base.model_dump(),
        consents=[mappers.consent_to_model(c) for c in consents],
        visit=mappers.visit_to_model(visit) if visit else None,
        evidence=[mappers.evidence_to_model(e) for e in evidence],
        claim=mappers.claim_to_model(claim, list(lines)) if claim else None,
        line_assessments=[mappers.assessment_to_model(a) for a in assessments],
        findings=[mappers.finding_to_model(f) for f in findings],
        dispute=mappers.dispute_to_model(dispute) if dispute else None,
        interventions=[
            mappers.intervention_to_model(i, decisions.get(i.decision_id or ""))
            for i in interventions
        ],
        calls=[mappers.call_to_model(c) for c in calls],
        agent_runs=[mappers.agent_run_to_model(r) for r in runs],
    )


def timeline(db: Session, case_id: str) -> list[TimelineEvent]:
    """Audit events + patient notifications, oldest first."""
    get_case_row(db, case_id)
    events = [
        TimelineEvent(
            at=as_utc(a.at),
            kind="audit",
            action=a.action,
            actor=a.actor,
            summary=", ".join(f"{k}={v}" for k, v in sorted(a.details.items()) if v is not None),
        )
        for a in db.scalars(select(AuditRow).where(AuditRow.case_id == case_id))
    ]
    events.extend(
        TimelineEvent(
            at=as_utc(n.created_at),
            kind="notification",
            action=n.kind,
            actor="core",
            summary=n.text,
        )
        for n in db.scalars(select(NotificationRow).where(NotificationRow.case_id == case_id))
    )
    return sorted(events, key=lambda e: e.at)


def list_notifications(db: Session, case_id: str) -> list[NotificationRow]:
    get_case_row(db, case_id)
    stmt = (
        select(NotificationRow)
        .where(NotificationRow.case_id == case_id)
        .order_by(NotificationRow.created_at)
    )
    return list(db.scalars(stmt))
