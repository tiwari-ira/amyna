"""Claims: ingestion (FinchNode or upload), findings persistence, analysis read model."""

from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from claimshield_contracts.api import (
    ClaimAnalysisResult,
    ClaimUpload,
    FinchNodeClaims,
    FinchNodeClaimSummary,
)
from claimshield_contracts.enums import (
    CaseState,
    ClaimSource,
    ConsentType,
    FindingStatus,
    PatientAnswer,
    WorkStatus,
)
from claimshield_contracts.ids import new_id
from claimshield_contracts.models import Claim, DisputeCandidate, LineAssessment
from claimshield_contracts.tool_api import RecordFindingsResponse

from .. import mappers
from ..audit import record_audit
from ..errors import Conflict, InvalidInput, NotFound
from ..mappers import dump, dump_all
from ..models_db import ClaimLineRow, ClaimRow, FindingRow, LineAssessmentRow
from . import notification_service
from .case_service import (
    DISPUTABLE,
    get_case_row,
    require_consent,
    transition,
)
from .human_service import create_confirmations, next_open_confirmation, open_confirmations

if TYPE_CHECKING:
    from ..container import Container


def get_claim_row(db: Session, claim_id: str) -> ClaimRow:
    claim = db.get(ClaimRow, claim_id)
    if claim is None:
        raise NotFound(f"Claim {claim_id} not found")
    return claim


def get_case_claim_row(db: Session, case_id: str) -> ClaimRow | None:
    return db.scalars(select(ClaimRow).where(ClaimRow.case_id == case_id)).first()


def claim_lines(db: Session, claim_id: str) -> list[ClaimLineRow]:
    stmt = (
        select(ClaimLineRow)
        .where(ClaimLineRow.claim_id == claim_id)
        .order_by(ClaimLineRow.line_number)
    )
    return list(db.scalars(stmt))


def claim_model(db: Session, claim: ClaimRow) -> Claim:
    return mappers.claim_to_model(claim, claim_lines(db, claim.id))


def _claim_from_upload(upload: ClaimUpload) -> Claim:
    return Claim(
        claim_id="pending",
        external_claim_id=upload.external_claim_id,
        payer_name=upload.payer_name,
        provider_name=upload.provider_name,
        facility_name=upload.facility_name,
        date_of_service=upload.date_of_service,
        total_billed_cents=sum(line.billed_cents for line in upload.lines),
        source=ClaimSource.UPLOAD,
        lines=[
            {"line_id": "pending", "claim_id": "pending", **line.model_dump()}
            for line in upload.lines
        ],
    )


def find_finchnode_claim(claims: list[Claim], wanted: str) -> Claim | None:
    """Match a claim number as patients and platforms write it (case, ``fn:`` prefix)."""
    key = wanted.strip().lower()
    if not key:
        return None
    return next(
        (
            claim
            for claim in claims
            if key in (claim.external_claim_id.lower(), claim.claim_id.lower())
        ),
        None,
    )


def finchnode_claims(db: Session, container: "Container", case_id: str) -> FinchNodeClaims:
    """The patient's FinchNode bills, summarized for picking one (consent-gated)."""
    case = get_case_row(db, case_id)
    require_consent(db, case_id, ConsentType.HEALTH_DATA_ACCESS)
    claims = container.finchnode.get_patient_claims(case.finchnode_patient_id)
    gap = container.finchnode.claims_gap(case.finchnode_patient_id)
    return FinchNodeClaims(
        claims=[
            FinchNodeClaimSummary(
                external_claim_id=claim.external_claim_id,
                provider_name=claim.provider_name,
                payer_name=claim.payer_name,
                date_of_service=claim.date_of_service,
                total_billed_cents=claim.total_billed_cents,
                itemized=bool(claim.lines),
            )
            for claim in sorted(
                claims,
                key=lambda c: (c.total_billed_cents, c.date_of_service.toordinal()),
                reverse=True,
            )
        ],
        complete=gap is None,
        gap=gap,
    )


def ingest_claim(
    db: Session,
    container: "Container",
    *,
    case_id: str,
    source: ClaimSource,
    finchnode_claim_id: str | None,
    upload: ClaimUpload | None,
    actor: str,
) -> ClaimRow:
    """Attach the bill under review to the case (FinchNode source needs consent)."""
    case = get_case_row(db, case_id)
    if get_case_claim_row(db, case_id) is not None:
        raise Conflict("This case already has a claim.", code="claim_exists")
    if source == ClaimSource.FINCHNODE:
        require_consent(db, case_id, ConsentType.HEALTH_DATA_ACCESS)
        available = container.finchnode.get_patient_claims(case.finchnode_patient_id)
        incoming = find_finchnode_claim(available, finchnode_claim_id or "")
        if incoming is None:
            gap = container.finchnode.claims_gap(case.finchnode_patient_id)
            raise NotFound(
                f"FinchNode claim {finchnode_claim_id} not found for this patient",
                code="claim_not_found",
                claims_complete=gap is None,
            )
        if not incoming.lines:
            # FinchNode claim records are summaries (no line items): the itemized bill
            # has to come from the patient before anything can be checked.
            raise InvalidInput(
                "This claim on the patient's records has no itemized charges. "
                "Upload the itemized bill instead.",
                code="claim_not_itemized",
                external_claim_id=incoming.external_claim_id,
                provider_name=incoming.provider_name,
                date_of_service=incoming.date_of_service.isoformat(),
                total_billed_cents=incoming.total_billed_cents,
            )
    elif upload is not None:
        incoming = _claim_from_upload(upload)
    else:
        raise InvalidInput("source=upload requires a claim body")

    claim = ClaimRow(
        id=new_id("claim"),
        case_id=case.id,
        external_claim_id=incoming.external_claim_id,
        payer_name=incoming.payer_name or case.payer_name,
        provider_name=incoming.provider_name,
        facility_name=incoming.facility_name,
        date_of_service=incoming.date_of_service,
        total_billed_cents=sum(line.billed_cents for line in incoming.lines),
        source=source.value,
    )
    db.add(claim)
    db.flush()  # parent row first: tables have FKs but no ORM relationships
    db.add_all(
        ClaimLineRow(
            id=new_id("line"),
            claim_id=claim.id,
            line_number=line.line_number,
            code_system=line.code.system.value,
            code=line.code.code,
            code_display=line.code.display,
            code_source_uri=line.code.source_system_uri,
            description=line.description,
            date_of_service=line.date_of_service,
            units=line.units,
            billed_cents=line.billed_cents,
            modifiers=list(line.modifiers),
            revenue_code=line.revenue_code,
            ndc=line.ndc,
        )
        for line in incoming.lines
    )
    transition(db, case, CaseState.CLAIM_RECEIVED, actor=actor)
    record_audit(
        db,
        actor=actor,
        action="claim.ingested",
        case_id=case.id,
        resource_type="claim",
        resource_id=claim.id,
        details={"source": source.value, "lines": len(incoming.lines)},
    )
    return claim


def _has_findings(db: Session, case_id: str) -> bool:
    return (
        db.scalars(select(FindingRow.id).where(FindingRow.case_id == case_id)).first() is not None
    )


def _validate_findings(
    findings: list[DisputeCandidate], assessments: list[LineAssessment], line_ids: set[str]
) -> None:
    """Reject malformed agent output with 422 (never a DB integrity error)."""
    if len({f.finding_id for f in findings}) != len(findings):
        raise InvalidInput("Duplicate finding ids")
    for item in [*findings, *assessments]:
        if item.claim_line_id not in line_ids:
            raise InvalidInput("A finding or line assessment references an unknown claim line")
    for finding in findings:
        if not finding.human_confirmation_required:
            continue
        if finding.confirmation is None:
            raise InvalidInput("A finding that needs confirmation has no question")
        missing = set(PatientAnswer) - set(finding.confirmation.effects)
        if missing or set(finding.confirmation.options) - set(finding.confirmation.effects):
            raise InvalidInput("Every confirmation answer needs an effect (YES / NO / NOT_SURE)")


def record_findings(
    db: Session,
    container: "Container",
    *,
    case_id: str,
    claim_id: str,
    findings: list[DisputeCandidate],
    assessments: list[LineAssessment],
    actor: str,
) -> RecordFindingsResponse:
    """Persist Claim Agent output, open confirmation questions, advance the case."""
    case = get_case_row(db, case_id)
    claim = get_claim_row(db, claim_id)
    if claim.case_id != case.id:
        raise InvalidInput("claim_id does not belong to case_id")
    if _has_findings(db, case_id) or case.state != CaseState.CLAIM_RECEIVED.value:
        raise Conflict("This claim was already analyzed.", code="already_analyzed")
    _validate_findings(findings, assessments, {line.id for line in claim_lines(db, claim.id)})

    db.add_all(
        LineAssessmentRow(
            id=new_id("assessment"),
            case_id=case.id,
            claim_line_id=a.claim_line_id,
            line_number=a.line_number,
            status=a.status.value,
            concept=a.concept,
            evidence_refs=dump_all(a.evidence_refs),
            note=a.note,
        )
        for a in assessments
    )
    rows = [
        FindingRow(
            id=f.finding_id,
            case_id=case.id,
            claim_id=claim.id,
            claim_line_id=f.claim_line_id,
            line_number=f.line_number,
            issue_type=f.issue_type.value,
            title=f.title,
            explanation=f.explanation,
            patient_facing_explanation=f.patient_facing_explanation,
            amount_cents=f.amount_cents,
            confidence=f.confidence,
            human_confirmation_required=f.human_confirmation_required,
            confirmation=dump(f.confirmation) if f.confirmation else None,
            status=(FindingStatus.OPEN if f.human_confirmation_required else f.status).value,
            recommended_action=f.recommended_action.value,
            evidence_refs=dump_all(f.evidence_refs),
            policy_citations=dump_all(f.policy_citations),
        )
        for f in findings
    ]
    db.add_all(rows)
    transition(db, case, CaseState.ANALYZED, actor=actor)
    record_audit(
        db,
        actor=actor,
        action="claim.findings_recorded",
        case_id=case.id,
        resource_id=claim.id,
        details={"findings": len(rows), "issue_types": sorted({r.issue_type for r in rows})},
    )

    uncertain = [r for r in rows if r.human_confirmation_required]
    if uncertain:
        create_confirmations(db, case_id=case.id, findings=uncertain, actor=actor)
        transition(db, case, CaseState.AWAITING_PATIENT_INPUT, actor=actor)
        first = next_open_confirmation(db, case.id)
        if first is not None:
            notification_service.notify_question(db, container, case, first)
    elif any(r.status in DISPUTABLE for r in rows):
        transition(db, case, CaseState.FINDINGS_CONFIRMED, actor=actor)
        notification_service.notify_status(
            db, container, case, f"We found {len(rows)} billing issue(s). Preparing your dispute."
        )
    else:
        transition(db, case, CaseState.RESOLVED, actor=actor, reason="no discrepancies found")
        notification_service.notify_status(
            db, container, case, "Good news: we found no billing discrepancies.", final=True
        )

    db.flush()
    return RecordFindingsResponse(
        findings=[mappers.finding_to_model(r) for r in rows],
        case_state=CaseState(case.state),
        open_questions=[mappers.intervention_to_model(q) for q in open_confirmations(db, case.id)],
    )


def analysis_result(db: Session, claim_id: str) -> ClaimAnalysisResult:
    """Current analysis state for a claim (completed once findings are recorded)."""
    claim = get_claim_row(db, claim_id)
    case = get_case_row(db, claim.case_id)
    findings = db.scalars(
        select(FindingRow).where(FindingRow.claim_id == claim.id).order_by(FindingRow.line_number)
    ).all()
    assessments = db.scalars(
        select(LineAssessmentRow)
        .where(LineAssessmentRow.case_id == case.id)
        .order_by(LineAssessmentRow.line_number)
    ).all()
    analyzed = case.state != CaseState.CLAIM_RECEIVED.value
    upcoming = next_open_confirmation(db, case.id)
    return ClaimAnalysisResult(
        status=WorkStatus.COMPLETED if analyzed else WorkStatus.PENDING,
        case_state=CaseState(case.state),
        claim_id=claim.id,
        findings=[mappers.finding_to_model(f) for f in findings],
        line_assessments=[mappers.assessment_to_model(a) for a in assessments],
        next_question=mappers.intervention_to_model(upcoming) if upcoming else None,
        total_flagged_cents=sum(f.amount_cents for f in findings),
    )
