from fastapi import APIRouter, Depends, Request, status

from claimshield_contracts.api import VisitComplete, VisitCreate, VisitEvidenceResult
from claimshield_contracts.models import EvidenceItem, Visit

from .. import mappers
from ..auth import ServiceIdentity, require_scopes
from ..container import Container
from ..deps import get_container
from ..services import evidence_service, workflows
from ._ownership import guard_case

router = APIRouter(prefix="/visits", tags=["visits"])


@router.post("", response_model=Visit, status_code=status.HTTP_201_CREATED)
def create_visit(
    body: VisitCreate,
    request: Request,
    identity: ServiceIdentity = Depends(require_scopes("visits:write")),
    container: Container = Depends(get_container),
) -> Visit:
    """Register a recorded visit (requires a granted `recording` consent)."""
    with container.session_scope() as db:
        guard_case(db, request, identity, case_id=body.case_id)
        row = evidence_service.create_visit(db, body=body, actor=identity.actor)
        db.flush()
        return mappers.visit_to_model(row)


@router.post("/{visit_id}/complete", response_model=VisitEvidenceResult)
def complete_visit(
    visit_id: str,
    body: VisitComplete,
    request: Request,
    identity: ServiceIdentity = Depends(require_scopes("visits:write")),
    container: Container = Depends(get_container),
) -> VisitEvidenceResult:
    """Attach the transcript; the Visit Agent logic then extracts evidence."""
    with container.session_scope() as db:
        guard_case(db, request, identity, visit=visit_id)
    return workflows.complete_visit(container, visit_id=visit_id, body=body, actor=identity.actor)


@router.get("/{visit_id}/evidence", response_model=list[EvidenceItem])
def get_visit_evidence(
    visit_id: str,
    request: Request,
    identity: ServiceIdentity = Depends(require_scopes("cases:read")),
    container: Container = Depends(get_container),
) -> list[EvidenceItem]:
    with container.session_scope() as db:
        guard_case(db, request, identity, visit=visit_id)
        evidence_service.get_visit_row(db, visit_id)
        return [
            mappers.evidence_to_model(e)
            for e in evidence_service.list_evidence(db, visit_id=visit_id)
        ]
