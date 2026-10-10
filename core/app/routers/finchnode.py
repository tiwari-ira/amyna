from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response, status
from starlette.concurrency import run_in_threadpool

from claimshield_contracts.api import FinchNodeConnectCreate, FinchNodeConnectSession

from ..auth import ServiceIdentity, require_scopes
from ..container import Container
from ..deps import get_container
from ..errors import Forbidden
from ..services import finchnode_service
from ..services.identity_service import PATIENT_TOKEN_HEADER, resolve_session_user

router = APIRouter(prefix="/finchnode", tags=["finchnode"])


@router.post(
    "/connect-sessions",
    response_model=FinchNodeConnectSession,
    status_code=status.HTTP_201_CREATED,
)
def create_connect_session(
    body: FinchNodeConnectCreate,
    request: Request,
    identity: ServiceIdentity = Depends(require_scopes("cases:write")),
    container: Container = Depends(get_container),
) -> FinchNodeConnectSession:
    """Start linking the signed-in patient's health records (FinchNode hosted Connect).

    Send the patient to ``url``. Linking finishes when FinchNode sends
    ``consent.granted`` (POST /finchnode/webhook) or when the session is read back.
    ``409 connect_unavailable`` in mock/demo modes, where patients type their id."""
    with container.session_scope() as db:
        owner = resolve_session_user(db, request.headers.get(PATIENT_TOKEN_HEADER))
        if container.settings.app_env == "prod" and not owner:
            raise Forbidden(
                "Production requires a patient session token to link records.",
                code="patient_session_required",
            )
        case_id = finchnode_service.prepare_connect_case(
            db, container, owner=owner, case_id=body.case_id, actor=identity.actor
        )
        user_id = owner.id if owner else None
    return finchnode_service.open_connect_session(
        container, case_id=case_id, user_id=user_id, return_url=body.return_url
    )


@router.get("/connect-sessions/{session_id}", response_model=FinchNodeConnectSession)
def get_connect_session(
    session_id: str,
    request: Request,
    _: ServiceIdentity = Depends(require_scopes("cases:read")),
    container: Container = Depends(get_container),
) -> FinchNodeConnectSession:
    """Read a session back from FinchNode; finishes linking if the patient is done."""
    with container.session_scope() as db:
        owner = resolve_session_user(db, request.headers.get(PATIENT_TOKEN_HEADER))
    return finchnode_service.refresh_connect_session(container, session_id=session_id, owner=owner)


@router.post("/webhook", status_code=status.HTTP_204_NO_CONTENT)
async def finchnode_webhook(
    request: Request,
    background: BackgroundTasks,
    container: Container = Depends(get_container),
) -> Response:
    """FinchNode lifecycle events (consent.granted, records.updated, consent.revoked...).

    Signed with the application's ``whsec_`` secret (FINCHNODE_WEBHOOK_SECRET).
    Answers 204 for a new event and 200 for a duplicate, before doing the work."""
    raw = await request.body()
    try:
        event = finchnode_service.verify_finchnode_signature(
            container.settings.finchnode_webhook_secret,
            raw,
            request.headers.get("FinchNode-Signature"),
            request.headers.get("FinchNode-Event-Id"),
        )
    except finchnode_service.FinchNodeSignatureError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "invalid_signature", "message": str(exc)},
        ) from exc
    if not await run_in_threadpool(finchnode_service.record_webhook_event, container, event):
        return Response(status_code=status.HTTP_200_OK)
    background.add_task(finchnode_service.process_webhook_event, container, event)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
