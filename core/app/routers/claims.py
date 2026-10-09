from fastapi import APIRouter, Depends, status

from claimshield_contracts.api import ClaimAnalysisResult, ClaimIngest
from claimshield_contracts.models import Claim

from ..auth import ServiceIdentity, require_scopes
from ..container import Container
from ..deps import get_container
from ..services import claim_service, workflows

router = APIRouter(prefix="/claims", tags=["claims"])


@router.post("", response_model=Claim, status_code=status.HTTP_201_CREATED)
def ingest_claim(
    body: ClaimIngest,
    identity: ServiceIdentity = Depends(require_scopes("claims:write")),
    container: Container = Depends(get_container),
) -> Claim:
    """Attach the bill under review (FinchNode source requires health-data consent)."""
    with container.session_scope() as db:
        row = claim_service.ingest_claim(
            db,
            container,
            case_id=body.case_id,
            source=body.source,
            finchnode_claim_id=body.finchnode_claim_id,
            upload=body.claim,
            actor=identity.actor,
        )
        return claim_service.claim_model(db, row)


@router.get("/{claim_id}", response_model=Claim)
def get_claim(
    claim_id: str,
    _: ServiceIdentity = Depends(require_scopes("cases:read")),
    container: Container = Depends(get_container),
) -> Claim:
    with container.session_scope() as db:
        return claim_service.claim_model(db, claim_service.get_claim_row(db, claim_id))


@router.post("/{claim_id}/analyze", response_model=ClaimAnalysisResult)
def analyze_claim(
    claim_id: str,
    _: ServiceIdentity = Depends(require_scopes("claims:analyze")),
    container: Container = Depends(get_container),
) -> ClaimAnalysisResult:
    """Run the Claim Agent logic (idempotent). `status=pending` in FETCH_MODE=agentverse."""
    return workflows.analyze_claim(container, claim_id=claim_id)
