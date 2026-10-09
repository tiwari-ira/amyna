"""Visits, transcripts and extracted visit evidence."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from claimshield_contracts.api import VisitComplete, VisitCreate
from claimshield_contracts.enums import CaseState, ConsentType, VisitStatus
from claimshield_contracts.ids import new_id
from claimshield_contracts.models import EvidenceItem

from ..audit import record_audit
from ..errors import Conflict, ConsentRequired, InvalidInput, NotFound
from ..mappers import dump_all
from ..models_db import ConsentRow, EvidenceRow, TranscriptRow, VisitRow
from .case_service import get_case_row, has_consent, transition


def get_visit_row(db: Session, visit_id: str) -> VisitRow:
    visit = db.get(VisitRow, visit_id)
    if visit is None:
        raise NotFound(f"Visit {visit_id} not found")
    return visit


def get_case_visit(db: Session, case_id: str) -> VisitRow | None:
    return db.scalars(select(VisitRow).where(VisitRow.case_id == case_id)).first()


def get_transcript_row(db: Session, visit: VisitRow) -> TranscriptRow | None:
    return db.get(TranscriptRow, visit.transcript_id) if visit.transcript_id else None


def create_visit(db: Session, *, body: VisitCreate, actor: str) -> VisitRow:
    """Register a recorded visit. Requires a granted `recording` consent on the case."""
    case = get_case_row(db, body.case_id)
    consent = db.get(ConsentRow, body.recording_consent_id)
    if (
        consent is None
        or consent.case_id != case.id
        or consent.consent_type != ConsentType.RECORDING.value
        or not consent.granted
        or not has_consent(db, case.id, ConsentType.RECORDING)  # revoked since?
    ):
        raise ConsentRequired(
            "A granted 'recording' consent for this case is required before recording a visit.",
            consent_type=ConsentType.RECORDING.value,
        )
    if get_case_visit(db, case.id) is not None:
        raise Conflict("This case already has a visit.", code="visit_exists")
    visit = VisitRow(
        id=new_id("visit"),
        case_id=case.id,
        provider_name=body.provider_name,
        facility_name=body.facility_name,
        occurred_at=body.occurred_at,
        recording_ref=body.recording_ref,
        recording_consent_id=consent.id,
        status=VisitStatus.RECORDED.value,
    )
    db.add(visit)
    transition(db, case, CaseState.VISIT_RECORDED, actor=actor)
    record_audit(db, actor=actor, action="visit.recorded", case_id=case.id, resource_id=visit.id)
    return visit


def complete_visit(db: Session, *, visit_id: str, body: VisitComplete, actor: str) -> TranscriptRow:
    """Store the transcript and move the case to TRANSCRIBED."""
    visit = get_visit_row(db, visit_id)
    if visit.transcript_id:
        raise Conflict("This visit already has a transcript.", code="transcript_exists")
    case = get_case_row(db, visit.case_id)
    transcript = TranscriptRow(
        id=new_id("transcript"),
        visit_id=visit.id,
        language=body.language,
        source=body.source,
        segments=[
            {"segment_id": new_id("segment"), **segment.model_dump(mode="json")}
            for segment in body.segments
        ],
    )
    db.add(transcript)
    visit.transcript_id = transcript.id
    visit.status = VisitStatus.TRANSCRIBED.value
    transition(db, case, CaseState.TRANSCRIBED, actor=actor)
    record_audit(
        db,
        actor=actor,
        action="visit.transcribed",
        case_id=case.id,
        resource_id=visit.id,
        details={"segments": len(body.segments), "source": body.source},
    )
    return transcript


def record_visit_evidence(
    db: Session, *, case_id: str, visit_id: str, evidence: list[EvidenceItem], actor: str
) -> list[EvidenceRow]:
    """Persist extracted evidence (from the Visit Agent) and move to EVIDENCE_EXTRACTED."""
    case = get_case_row(db, case_id)
    visit = get_visit_row(db, visit_id)
    if visit.case_id != case.id:
        raise InvalidInput("visit_id does not belong to case_id")
    if visit.status == VisitStatus.EVIDENCE_EXTRACTED.value:
        raise Conflict("Evidence was already recorded for this visit.", code="already_recorded")
    rows = [
        EvidenceRow(
            id=item.evidence_id,
            case_id=case.id,
            visit_id=visit.id,
            source=item.source.value,
            kind=item.kind.value,
            concept=item.concept,
            description=item.description,
            codes=dump_all(item.codes),
            attributes=dict(item.attributes),
            segment_ids=list(item.segment_ids),
            quote=item.quote,
            negated=item.negated,
            confidence=item.confidence,
            extractor=item.extractor,
        )
        for item in evidence
    ]
    db.add_all(rows)
    visit.status = VisitStatus.EVIDENCE_EXTRACTED.value
    transition(db, case, CaseState.EVIDENCE_EXTRACTED, actor=actor)
    record_audit(
        db,
        actor=actor,
        action="visit.evidence_recorded",
        case_id=case.id,
        resource_id=visit.id,
        details={"items": len(rows), "extractors": sorted({r.extractor for r in rows})},
    )
    return rows


def list_evidence(
    db: Session, *, case_id: str | None = None, visit_id: str | None = None
) -> list[EvidenceRow]:
    stmt = select(EvidenceRow)
    if case_id:
        stmt = stmt.where(EvidenceRow.case_id == case_id)
    if visit_id:
        stmt = stmt.where(EvidenceRow.visit_id == visit_id)
    return list(db.scalars(stmt.order_by(EvidenceRow.created_at)))
