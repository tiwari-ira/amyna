import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from starlette.concurrency import run_in_threadpool

from claimshield_contracts.api import HandoffIn, VoiceEventIn, VoiceEventResult, VoiceStartIn
from claimshield_contracts.models import CallSession, HumanIntervention

from ..adapters.telephony import webhook_signature
from ..adapters.telephony.telnyx_sip import CALL_ID_SIP_HEADER
from ..adapters.xai_voice import sip_webhook
from ..adapters.xai_voice.sip_webhook import XaiWebhookError, verify_xai_signature
from ..auth import ServiceIdentity, require_scopes
from ..container import Container
from ..deps import get_container
from ..services import telnyx_service
from ._ownership import guard_case

router = APIRouter(prefix="/voice", tags=["voice"])
log = logging.getLogger("claimshield.voice")


@router.post("/telnyx/webhook")
async def telnyx_webhook(
    request: Request,
    container: Container = Depends(get_container),
) -> dict[str, bool]:
    """Signed call events from Telnyx (Ed25519; no bearer token).

    Headers: ``Telnyx-Timestamp`` (unix seconds) and ``Telnyx-Signature-Ed25519``
    (base64 Ed25519 over ``timestamp`` + raw body; account public key from Mission
    Control). ``call.answered`` for the insurer leg bridges the call to xAI's SIP
    URI (transfer); ``call.hangup`` ends the Core call. Acked 200 per the Telnyx
    delivery contract.
    """
    try:
        return await run_in_threadpool(
            telnyx_service.process_telnyx_event,
            container,
            await request.body(),
            timestamp=request.headers.get(webhook_signature.TIMESTAMP_HEADER),
            signature=request.headers.get(webhook_signature.SIGNATURE_HEADER),
        )
    except webhook_signature.TelnyxSignatureError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "invalid_signature", "message": str(exc)},
        ) from exc


@router.post("/xai/webhook")
async def xai_incoming_call_webhook(
    request: Request,
    container: Container = Depends(get_container),
) -> dict[str, bool]:
    """Signed ``realtime.call.incoming`` from xAI for a bridged SIP call (task 15).

    Headers (verified against the current xAI SIP docs): ``webhook-id``,
    ``webhook-timestamp``, ``webhook-signature``. The payload's ``data.call_id``
    is attached to our call via the ``x-amyna-call-id`` SIP header we set on
    Telnyx's transfer; then the realtime WebSocket attaches with ``?call_id=``.
    """
    body = await request.body()
    try:
        verify_xai_signature(
            container.settings.xai_webhook_signing_secret,
            body,
            webhook_id=request.headers.get(sip_webhook.WEBHOOK_ID_HEADER),
            timestamp=request.headers.get(sip_webhook.WEBHOOK_TIMESTAMP_HEADER),
            signature=request.headers.get(sip_webhook.WEBHOOK_SIGNATURE_HEADER),
        )
        incoming = await run_in_threadpool(sip_webhook.parse_incoming_call, body)
    except XaiWebhookError as exc:
        log.warning("xAI inbound call rejected: %s", exc.message)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "invalid_signature", "message": str(exc)},
        ) from exc

    core_call_id = _header(incoming.sip_headers, CALL_ID_SIP_HEADER)
    if not core_call_id:
        core_call_id = container.voice_manager.open_unbridged_call_id()
    if not core_call_id:
        # xAI treats a 200 as handled; without a call to attach we cannot speak.
        log.warning("xAI inbound call without correlation header")
        return {"received": False}
    await run_in_threadpool(
        container.voice_manager.attach_sip_session,
        core_call_id,
        incoming.call_id,
        actor="service:telnyx",
    )
    return {"received": True}


@router.get("/recordings/{token}.mp3", include_in_schema=False)
def authorization_recording(
    token: str, container: Container = Depends(get_container)
) -> Response:
    """The patient's authorization memo for Telnyx playback (token per call, live calls only)."""
    from ..services import recording_service

    if len(token) > 100:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    with container.session_scope() as db:
        audio = recording_service.audio_for_token(db, token)
    if audio is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return Response(
        content=audio, media_type="audio/mpeg", headers={"Cache-Control": "no-store"}
    )


@router.get("/media/silence.wav", include_in_schema=False)
def hold_silence() -> Response:
    """Silent hold audio for the AI leg while the patient speaks (instead of hold music)."""
    from ..services.recording_service import silence_wav

    return Response(content=silence_wav(), media_type="audio/wav")


def _header(headers: dict[str, str], name: str) -> str | None:
    """SIP header lookup. Carriers do not agree on the letter case."""
    wanted = name.lower()
    for key, value in headers.items():
        if key.lower() == wanted and value:
            return value
    return None


@router.post("/start", response_model=CallSession, status_code=status.HTTP_201_CREATED)
def start_call(
    body: VoiceStartIn,
    request: Request,
    identity: ServiceIdentity = Depends(require_scopes("voice:start")),
    container: Container = Depends(get_container),
) -> CallSession:
    """Start the insurer call. 409 unless the dispute is APPROVED with stored authorization."""
    with container.session_scope() as db:
        guard_case(db, request, identity, case_id=body.case_id)
    return container.voice_manager.start_call(body.case_id, actor=identity.actor)


@router.get("/{call_id}", response_model=CallSession)
def get_call(
    call_id: str,
    request: Request,
    identity: ServiceIdentity = Depends(require_scopes("voice:read")),
    container: Container = Depends(get_container),
) -> CallSession:
    with container.session_scope() as db:
        guard_case(db, request, identity, call=call_id)
    return container.voice_manager.get_call(call_id)


@router.post("/{call_id}/event", response_model=VoiceEventResult)
def call_event(
    call_id: str,
    body: VoiceEventIn,
    identity: ServiceIdentity = Depends(require_scopes("voice:events")),
    container: Container = Depends(get_container),
) -> VoiceEventResult:
    """Insurer transcript or status event from a telephony/voice bridge."""
    return container.voice_manager.handle_event(call_id, body, actor=identity.actor)


@router.post(
    "/{call_id}/human-handoff",
    response_model=HumanIntervention,
    status_code=status.HTTP_201_CREATED,
)
def human_handoff(
    call_id: str,
    body: HandoffIn,
    request: Request,
    identity: ServiceIdentity = Depends(require_scopes("voice:handoff")),
    container: Container = Depends(get_container),
) -> HumanIntervention:
    """Pause the call and bring in the patient (e.g. the patient wants to take over)."""
    with container.session_scope() as db:
        guard_case(db, request, identity, call=call_id)
    return container.voice_manager.request_handoff(
        call_id, reason=body.reason, detail=body.detail, actor=identity.actor
    )
