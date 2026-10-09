from fastapi import APIRouter, Depends, HTTPException, Request, status
from starlette.concurrency import run_in_threadpool

from claimshield_contracts.api import RelayWebhookEvent, RelayWebhookResult

from ..auth import SIGNATURE_HEADER, TIMESTAMP_HEADER, SignatureError, verify_signature
from ..container import Container
from ..deps import get_container
from ..services import webhook_service

router = APIRouter(prefix="/relay", tags=["relay"])


@router.post("/webhook", response_model=RelayWebhookResult)
async def relay_webhook(
    event: RelayWebhookEvent,
    request: Request,
    container: Container = Depends(get_container),
) -> RelayWebhookResult:
    """Signed, idempotent patient event from the Relay service.

    Headers: ``X-Amyna-Timestamp`` (unix seconds) and
    ``X-Amyna-Signature: v1=<hex HMAC-SHA256(CORE_WEBHOOK_SECRET, "{ts}.{raw body}")>``.
    """
    try:
        verify_signature(
            container.settings.webhook_secret(),
            await request.body(),
            request.headers.get(TIMESTAMP_HEADER),
            request.headers.get(SIGNATURE_HEADER),
        )
    except SignatureError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "invalid_signature", "message": str(exc)},
        ) from exc
    return await run_in_threadpool(webhook_service.process_relay_event, container, event)
