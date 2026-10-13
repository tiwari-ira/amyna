"""Disputes: package recording, patient approval/rejection, voice-safe case view."""

from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from claimshield_contracts.enums import (
    Actor,
    CaseState,
    Channel,
    DecisionType,
    DisputeStatus,
)
from claimshield_contracts.ids import new_id
from claimshield_contracts.models import DisputeItem
from claimshield_contracts.tool_api import DisputeCaseView

from .. import mappers
from ..audit import mask_member_ref, record_audit
from ..db import utcnow
from ..errors import Conflict, Forbidden, InvalidInput, NotFound
from ..mappers import dump_all
from ..models_db import DecisionRow, DisputeRow, EvidenceRow, FindingRow
from ..names import spoken_name
from . import notification_service
from .case_service import DISPUTABLE, get_case_row, latest_dispute, transition
from .claim_service import claim_lines, get_case_claim_row
from .notification_service import format_cents
from .security_policy import reject_sensitive_notes

if TYPE_CHECKING:
    from ..container import Container

APPROVERS = frozenset({Actor.PATIENT, Actor.CAREGIVER})


def get_dispute_row(db: Session, dispute_id: str) -> DisputeRow:
    dispute = db.get(DisputeRow, dispute_id)
    if dispute is None:
        raise NotFound(f"Dispute {dispute_id} not found")
    return dispute


def record_dispute(
    db: Session,
    container: "Container",
    *,
    case_id: str,
    claim_id: str,
    items: list[DisputeItem],
    letter_text: str,
    total_disputed_cents: int,
    actor: str,
) -> DisputeRow:
    """Persist the Dispute Agent's package and request patient approval."""
    case = get_case_row(db, case_id)
    if latest_dispute(db, case_id) is not None:
        raise Conflict("This case already has a dispute.", code="dispute_exists")
    claim = get_case_claim_row(db, case_id)
    if claim is None or claim.id != claim_id:
        raise InvalidInput("claim_id does not match the case's claim")
    disputable = {
        f.id: f
        for f in db.scalars(
            select(FindingRow).where(
                FindingRow.case_id == case_id, FindingRow.status.in_(DISPUTABLE)
            )
        )
    }
    if len({item.finding_id for item in items}) != len(items):
        raise InvalidInput("Each finding can appear in the dispute only once")
    for item in items:
        finding = disputable.get(item.finding_id)
        if finding is None:
            raise InvalidInput("Dispute items must reference confirmed findings of this case")
        if item.claim_line_id != finding.claim_line_id or item.amount_cents != finding.amount_cents:
            raise InvalidInput("Dispute items must match their finding's claim line and amount")
    if total_disputed_cents != sum(item.amount_cents for item in items):
        raise InvalidInput("total_disputed_cents must equal the sum of item amounts")

    dispute = DisputeRow(
        id=new_id("dispute"),
        case_id=case.id,
        claim_id=claim.id,
        status=DisputeStatus.AWAITING_APPROVAL.value,
        items=dump_all(items),
        total_disputed_cents=total_disputed_cents,
        letter_text=letter_text,
    )
    db.add(dispute)
    transition(db, case, CaseState.DISPUTE_PREPARED, actor=actor)
    transition(db, case, CaseState.AWAITING_APPROVAL, actor=actor)
    record_audit(
        db,
        actor=actor,
        action="dispute.prepared",
        case_id=case.id,
        resource_type="dispute",
        resource_id=dispute.id,
        details={"items": len(items), "total_cents": total_disputed_cents},
    )
    notification_service.notify_approval_request(db, container, case, dispute)
    return dispute


def approve(
    db: Session,
    container: "Container",
    *,
    dispute_id: str,
    actor_kind: Actor,
    channel: Channel,
    authorization_text: str,
    authorize_call: bool,
    actor: str,
) -> DisputeRow:
    """Record the patient's explicit approval; optionally authorize the insurer call."""
    if actor_kind not in APPROVERS:
        raise Forbidden("Only the patient (or their caregiver) can approve a dispute.")
    dispute = get_dispute_row(db, dispute_id)
    if dispute.status != DisputeStatus.AWAITING_APPROVAL.value:
        raise Conflict("This dispute is not awaiting approval.", code="not_awaiting_approval")
    reject_sensitive_notes(authorization_text)
    case = get_case_row(db, dispute.case_id)
    decision = DecisionRow(
        id=new_id("decision"),
        case_id=case.id,
        decision_type=DecisionType.DISPUTE_APPROVAL.value,
        choice="APPROVE",
        actor=actor_kind.value,
        channel=channel.value,
        authorization_text=authorization_text,
        authorize_call=authorize_call,
        dispute_id=dispute.id,
    )
    db.add(decision)
    dispute.status = DisputeStatus.APPROVED.value
    dispute.approved_at = utcnow()
    dispute.patient_authorization_id = decision.id if authorize_call else None
    transition(db, case, CaseState.APPROVED, actor=actor)
    record_audit(
        db,
        actor=actor,
        action="dispute.approved",
        case_id=case.id,
        resource_type="dispute",
        resource_id=dispute.id,
        details={"authorize_call": authorize_call, "channel": channel.value},
    )
    payer = case.payer_name or "your insurer"
    if not authorize_call:
        # Letter only: nothing else for Amyna to do, so close the case.
        transition(db, case, CaseState.RESOLVED, actor=actor, reason="approved without a call")
        notification_service.notify_status(
            db,
            container,
            case,
            f"Approved. Your dispute letter for {payer} is ready; no call was authorized.",
            final=True,
        )
        return dispute
    total = format_cents(dispute.total_disputed_cents)
    notification_service.notify_status(
        db, container, case, f"Approved. Amyna will call {payer} about {total}."
    )
    return dispute


def reject(
    db: Session,
    container: "Container",
    *,
    dispute_id: str,
    actor_kind: Actor,
    channel: Channel,
    reason: str | None,
    actor: str,
) -> DisputeRow:
    if actor_kind not in APPROVERS:
        raise Forbidden("Only the patient (or their caregiver) can reject a dispute.")
    dispute = get_dispute_row(db, dispute_id)
    if dispute.status != DisputeStatus.AWAITING_APPROVAL.value:
        raise Conflict("This dispute is not awaiting approval.", code="not_awaiting_approval")
    reject_sensitive_notes(reason)
    case = get_case_row(db, dispute.case_id)
    db.add(
        DecisionRow(
            id=new_id("decision"),
            case_id=case.id,
            decision_type=DecisionType.DISPUTE_REJECTION.value,
            choice="REJECT",
            actor=actor_kind.value,
            channel=channel.value,
            notes=reason,
            dispute_id=dispute.id,
        )
    )
    dispute.status = DisputeStatus.REJECTED.value
    dispute.rejected_reason = reason
    transition(db, case, CaseState.REJECTED, actor=actor, reason="patient rejected the dispute")
    record_audit(
        db, actor=actor, action="dispute.rejected", case_id=case.id, resource_id=dispute.id
    )
    notification_service.notify_status(
        db, container, case, "Understood — the dispute was not sent.", final=True
    )
    return dispute


def dispute_case_view(db: Session, case_id: str) -> DisputeCaseView:
    """The minimal, PHI-reduced view the dispute and voice agents work from."""
    case = get_case_row(db, case_id)
    claim = get_case_claim_row(db, case_id)
    lines = claim_lines(db, claim.id) if claim else []
    dispute = latest_dispute(db, case_id)
    findings = db.scalars(
        select(FindingRow).where(FindingRow.case_id == case_id).order_by(FindingRow.line_number)
    ).all()
    evidence = db.scalars(select(EvidenceRow).where(EvidenceRow.case_id == case_id)).all()
    codes = {line.code for line in lines}
    codes.update(code["code"] for row in evidence for code in row.codes)
    concepts = [row.concept for row in evidence if row.concept and not row.negated]
    concepts.extend(row.description for row in evidence if not row.negated)
    concepts.extend(line.description for line in lines)
    return DisputeCaseView(
        case_id=case.id,
        case_state=CaseState(case.state),
        patient_display_name=spoken_name(case.patient_display_name),
        member_ref_masked=mask_member_ref(case.member_ref),
        payer_name=case.payer_name,
        claim_number=claim.external_claim_id if claim else None,
        provider_name=(claim.provider_name or claim.facility_name) if claim else None,
        date_of_service=claim.date_of_service if claim else None,
        total_billed_cents=claim.total_billed_cents if claim else 0,
        dispute=mappers.dispute_to_model(dispute) if dispute else None,
        findings=[mappers.finding_to_model(f) for f in findings],
        allowed_codes=sorted(codes),
        allowed_concepts=sorted(set(concepts)),
    )
