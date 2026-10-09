from fastapi import APIRouter, Depends, Request, status

from claimshield_contracts.api import CaseCreate, ConsentCreate
from claimshield_contracts.models import (
    Case,
    CaseDetail,
    Consent,
    PatientNotification,
    TimelineEvent,
)

from .. import mappers
from ..auth import ServiceIdentity, require_scopes
from ..container import Container
from ..deps import get_container
from ..models_db import CaseRow, UserRow
from ..services import case_service
from ..services.identity_service import (
    PATIENT_TOKEN_HEADER,
    assert_case_access,
    resolve_session_user,
)

router = APIRouter(prefix="/cases", tags=["cases"])


def _patient_user(db, request: Request) -> UserRow | None:
    """The patient session forwarded by the caller, if any (header never logged)."""
    return resolve_session_user(db, request.headers.get(PATIENT_TOKEN_HEADER))


@router.post("", response_model=Case, status_code=status.HTTP_201_CREATED)
def create_case(
    body: CaseCreate,
    request: Request,
    identity: ServiceIdentity = Depends(require_scopes("cases:write")),
    container: Container = Depends(get_container),
) -> Case:
    with container.session_scope() as db:
        owner = _patient_user(db, request)
        if container.settings.app_env == "prod" and identity.name.value == "relay" and not owner:
            from ..errors import Forbidden

            raise Forbidden(
                "Production requires a patient session token on case creation.",
                code="patient_session_required",
            )
        row = case_service.create_case(
            db,
            patient=body.patient,
            channel=body.channel,
            external_ref=body.external_ref,
            actor=identity.actor,
            owner_id=owner.id if owner else None,
        )
        return mappers.case_to_model(row)


@router.get("/{case_id}", response_model=CaseDetail)
def get_case(
    case_id: str,
    request: Request,
    _: ServiceIdentity = Depends(require_scopes("cases:read")),
    container: Container = Depends(get_container),
) -> CaseDetail:
    with container.session_scope() as db:
        assert_case_access(case_service.get_case_row(db, case_id), _patient_user(db, request))
        return case_service.get_case_detail(db, case_id)


@router.post("/{case_id}/consents", response_model=Consent, status_code=status.HTTP_201_CREATED)
def record_consent(
    case_id: str,
    body: ConsentCreate,
    request: Request,
    identity: ServiceIdentity = Depends(require_scopes("consents:write")),
    container: Container = Depends(get_container),
) -> Consent:
    """Record a patient consent (health data access or visit recording)."""
    with container.session_scope() as db:
        assert_case_access(case_service.get_case_row(db, case_id), _patient_user(db, request))
        row = case_service.record_consent(
            db,
            container,
            case_id=case_id,
            consent_type=body.consent_type,
            granted=body.granted,
            consent_text=body.consent_text,
            actor_kind=body.actor,
            channel=body.channel,
            actor=identity.actor,
        )
        case = db.get(CaseRow, case_id)
        if body.granted and body.consent_type.value == "health_data_access":
            from ..services import identity_service

            identity_service.link_finch(db, case=case, actor=identity.actor)
        db.flush()
        return mappers.consent_to_model(row)


@router.get("/{case_id}/timeline", response_model=list[TimelineEvent])
def get_timeline(
    case_id: str,
    request: Request,
    _: ServiceIdentity = Depends(require_scopes("cases:read")),
    container: Container = Depends(get_container),
) -> list[TimelineEvent]:
    """Audit events and patient notifications, oldest first."""
    with container.session_scope() as db:
        assert_case_access(case_service.get_case_row(db, case_id), _patient_user(db, request))
        return case_service.timeline(db, case_id)


@router.get("/{case_id}/notifications", response_model=list[PatientNotification])
def get_notifications(
    case_id: str,
    request: Request,
    _: ServiceIdentity = Depends(require_scopes("cases:read")),
    container: Container = Depends(get_container),
) -> list[PatientNotification]:
    """Core's patient outbox (what the Relay service delivers / renders)."""
    with container.session_scope() as db:
        assert_case_access(case_service.get_case_row(db, case_id), _patient_user(db, request))
        return [
            mappers.notification_to_model(n) for n in case_service.list_notifications(db, case_id)
        ]
