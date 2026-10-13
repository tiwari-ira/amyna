from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field

from claimshield_contracts.api import CallbackPhoneIn, CaseCreate, ConsentCreate, FinchNodeClaims
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


@router.put("/{case_id}/callback-phone", response_model=Case)
def set_callback_phone(
    case_id: str,
    body: CallbackPhoneIn,
    request: Request,
    identity: ServiceIdentity = Depends(require_scopes("cases:write")),
    container: Container = Depends(get_container),
) -> Case:
    """The patient's own number for joining an insurer call when it pauses for them
    (replaces the single HUMAN_HANDOFF_PHONE for this case)."""
    with container.session_scope() as db:
        case = case_service.get_case_row(db, case_id)
        assert_case_access(case, _patient_user(db, request))
        case_service.set_callback_phone(db, case, body.phone, actor=identity.actor)
        return mappers.case_to_model(case)


class AuthorizationRecordingIn(BaseModel):
    """The patient's spoken authorization memo (base64 audio, as Relay received it)."""

    audio_base64: str = Field(min_length=16, max_length=9_000_000)
    mime_type: str = Field(min_length=3, max_length=64)


class AuthorizationRecordingOut(BaseModel):
    stored: bool
    size_bytes: int


@router.put("/{case_id}/authorization-recording", response_model=AuthorizationRecordingOut)
def put_authorization_recording(
    case_id: str,
    body: AuthorizationRecordingIn,
    request: Request,
    identity: ServiceIdentity = Depends(require_scopes("cases:write")),
    container: Container = Depends(get_container),
) -> AuthorizationRecordingOut:
    """Store the memo that plays to the insurer when the call connects (converted to MP3)."""
    import base64
    import binascii

    from ..errors import InvalidInput
    from ..services import recording_service

    try:
        audio = base64.b64decode(body.audio_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise InvalidInput(
            "audio_base64 is not valid base64.", code="recording_unreadable"
        ) from exc
    with container.session_scope() as db:
        case = case_service.get_case_row(db, case_id)
        assert_case_access(case, _patient_user(db, request))
        row = recording_service.store_authorization(
            db, case, audio, body.mime_type, actor=identity.actor
        )
        return AuthorizationRecordingOut(stored=True, size_bytes=row.size_bytes)


@router.get("/{case_id}/finchnode/claims", response_model=FinchNodeClaims)
def get_finchnode_claims(
    case_id: str,
    request: Request,
    _: ServiceIdentity = Depends(require_scopes("cases:read")),
    container: Container = Depends(get_container),
) -> FinchNodeClaims:
    """The patient's bills on FinchNode, newest first (needs health-data consent).

    FinchNode bills are summaries: ``itemized=false`` means the patient must send the
    itemized statement before Amyna can check it."""
    from ..services import claim_service

    with container.session_scope() as db:
        assert_case_access(case_service.get_case_row(db, case_id), _patient_user(db, request))
        return claim_service.finchnode_claims(db, container, case_id)


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
