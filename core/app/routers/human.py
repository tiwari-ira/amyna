from fastapi import APIRouter, Depends, status

from claimshield_contracts.api import (
    HumanInterventionCreate,
    HumanInterventionResolve,
    HumanResolutionResult,
)
from claimshield_contracts.models import HumanIntervention

from .. import mappers
from ..auth import ServiceIdentity, require_scopes
from ..container import Container
from ..deps import get_container
from ..models_db import DecisionRow
from ..services import human_service, workflows

router = APIRouter(prefix="/human-intervention", tags=["human"])


@router.post("", response_model=HumanIntervention, status_code=status.HTTP_201_CREATED)
def create_intervention(
    body: HumanInterventionCreate,
    identity: ServiceIdentity = Depends(require_scopes("human:write")),
    container: Container = Depends(get_container),
) -> HumanIntervention:
    """Ask a human for input. With `call_id` the call is paused (HUMAN_REQUIRED)."""
    return workflows.request_human(
        container,
        case_id=body.case_id,
        reason=body.reason,
        prompt=body.prompt,
        options=body.options,
        actor=identity.actor,
        call_id=body.call_id,
        finding_id=body.finding_id,
    )


@router.get("/{intervention_id}", response_model=HumanIntervention)
def get_intervention(
    intervention_id: str,
    _: ServiceIdentity = Depends(require_scopes("human:read")),
    container: Container = Depends(get_container),
) -> HumanIntervention:
    with container.session_scope() as db:
        row = human_service.get_intervention_row(db, intervention_id)
        decision = db.get(DecisionRow, row.decision_id) if row.decision_id else None
        return mappers.intervention_to_model(row, decision)


@router.post("/{intervention_id}/resolve", response_model=HumanResolutionResult)
def resolve_intervention(
    intervention_id: str,
    body: HumanInterventionResolve,
    identity: ServiceIdentity = Depends(require_scopes("human:write")),
    container: Container = Depends(get_container),
) -> HumanResolutionResult:
    """Answer a question (YES/NO/NOT_SURE) or finish a handoff (DONE/CANCEL).

    Notes are rejected (422) if they contain SSNs, PINs, passwords or card numbers.
    """
    return workflows.resolve_intervention(
        container,
        intervention_id=intervention_id,
        choice=body.choice,
        notes=body.notes,
        actor_kind=body.actor,
        channel=body.channel,
        actor=identity.actor,
    )
