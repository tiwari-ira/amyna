from fastapi import APIRouter, Depends, Request, Response, status

from claimshield_contracts.api import (
    DisputeApproval,
    DisputeCreate,
    DisputePreparation,
    DisputeRejection,
)
from claimshield_contracts.enums import WorkStatus
from claimshield_contracts.models import Dispute

from .. import mappers
from ..auth import ServiceIdentity, require_scopes
from ..container import Container
from ..deps import get_container
from ..services import dispute_service, workflows
from ._ownership import guard_case

router = APIRouter(prefix="/disputes", tags=["disputes"])


@router.post("", response_model=DisputePreparation)
def prepare_dispute(
    body: DisputeCreate,
    response: Response,
    request: Request,
    identity: ServiceIdentity = Depends(require_scopes("disputes:write")),
    container: Container = Depends(get_container),
) -> DisputePreparation:
    """Ask the Dispute Agent logic to draft the package. 202 while agents work."""
    with container.session_scope() as db:
        guard_case(db, request, identity, case_id=body.case_id)
    result = workflows.prepare_dispute(container, case_id=body.case_id)
    if result.status == WorkStatus.PENDING:
        response.status_code = status.HTTP_202_ACCEPTED
    return result


@router.get("/{dispute_id}", response_model=Dispute)
def get_dispute(
    dispute_id: str,
    request: Request,
    identity: ServiceIdentity = Depends(require_scopes("disputes:read")),
    container: Container = Depends(get_container),
) -> Dispute:
    with container.session_scope() as db:
        guard_case(db, request, identity, dispute=dispute_id)
        return mappers.dispute_to_model(dispute_service.get_dispute_row(db, dispute_id))


@router.post("/{dispute_id}/approve", response_model=Dispute)
def approve_dispute(
    dispute_id: str,
    body: DisputeApproval,
    request: Request,
    identity: ServiceIdentity = Depends(require_scopes("disputes:decide")),
    container: Container = Depends(get_container),
) -> Dispute:
    """Explicit patient approval. Stores the authorization that gates any insurer call."""
    with container.session_scope() as db:
        guard_case(db, request, identity, dispute=dispute_id)
        row = dispute_service.approve(
            db,
            container,
            dispute_id=dispute_id,
            actor_kind=body.actor,
            channel=body.channel,
            authorization_text=body.authorization_text,
            authorize_call=body.authorize_call,
            actor=identity.actor,
        )
        db.flush()
        return mappers.dispute_to_model(row)


@router.post("/{dispute_id}/reject", response_model=Dispute)
def reject_dispute(
    dispute_id: str,
    body: DisputeRejection,
    request: Request,
    identity: ServiceIdentity = Depends(require_scopes("disputes:decide")),
    container: Container = Depends(get_container),
) -> Dispute:
    with container.session_scope() as db:
        guard_case(db, request, identity, dispute=dispute_id)
        row = dispute_service.reject(
            db,
            container,
            dispute_id=dispute_id,
            actor_kind=body.actor,
            channel=body.channel,
            reason=body.reason,
            actor=identity.actor,
        )
        db.flush()
        return mappers.dispute_to_model(row)
