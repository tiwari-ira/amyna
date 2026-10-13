"""Signed call events from Telnyx (POST /voice/telnyx/webhook).

Like the Relay webhook this route does not use the service bearer tokens: the
Ed25519 signature IS the authentication (adapters/telephony/webhook_signature.py).
Events are acknowledged with 200 promptly (Telnyx retries only 408/429/5xx;
other 4xx are never retried).

Each call has up to three legs, told apart by ``client_state``
(``TelnyxSipTelephony``): the insurer leg (``base64(call_id)``), the AI leg to
xAI's SIP endpoint (``call_id:ai``) and the patient leg (``call_id:human``).
The sequence itself lives in ``call_flow.py``:

- insurer: ``call.answered`` -> voicemail check; ``call.machine.detection.ended``
  -> recording or AI; ``call.playback.ended`` -> AI; ``call.hangup`` -> end the call.
- AI: ``call.answered`` -> join the conference and greet; ``call.hangup`` -> end.
- patient: ``call.answered`` -> hold the AI, join the conference;
  ``call.hangup`` -> unreachable (never answered) or finished (resume the AI).

In the transfer fallback (no conference) the xAI leg carries the insurer's
client_state with a ``sip:`` destination; those events are ignored.
"""

import json
import logging
from typing import TYPE_CHECKING

from claimshield_contracts.api import VoiceEventIn
from claimshield_contracts.enums import VoiceEventType

from ..adapters.telephony.telnyx_sip import LEG_AI, LEG_HUMAN, parse_client_state
from ..adapters.telephony.webhook_signature import verify_telnyx_signature
from ..errors import InvalidInput, NotFound
from . import call_flow
from .voice_service import INSURER_HUNG_UP

if TYPE_CHECKING:
    from ..container import Container

log = logging.getLogger("claimshield.telnyx")
ACTOR = "service:telnyx"


def process_telnyx_event(
    container: "Container",
    raw_body: bytes,
    *,
    timestamp: str | None,
    signature: str | None,
) -> dict[str, bool]:
    """Verify the signature, then dispatch one Telnyx event. Return {"received": ...}."""
    if container.settings.telnyx_webhook_verify:
        verify_telnyx_signature(
            container.settings.telnyx_public_key, raw_body, timestamp, signature
        )
    else:
        # TELNYX_WEBHOOK_VERIFY=false: the endpoint accepts unauthenticated
        # events (demo only). Keep the tunnel URL private; re-enable for prod.
        log.warning("Telnyx webhook signature verification is DISABLED")
    try:
        event = json.loads(raw_body)
    except ValueError as exc:
        # 4xx is never retried by Telnyx, which is right for a malformed body.
        raise InvalidInput(
            "Telnyx webhook body is not valid JSON", code="invalid_telnyx_event"
        ) from exc
    data = event.get("data") or {}
    payload = data.get("payload") or {}
    handled = _dispatch(
        container,
        str(data.get("event_type") or ""),
        payload,
        payload.get("client_state"),
    )
    return {"received": handled}


def _dispatch(container: "Container", event_type: str, payload: dict, client_state: object) -> bool:
    state = client_state if isinstance(client_state, str) else None
    call_id, leg = parse_client_state(state)
    if call_id is None:
        return False
    leg_ref = str(payload.get("call_control_id") or "")
    try:
        if leg == LEG_HUMAN:
            if event_type == "call.answered" and leg_ref:
                return call_flow.patient_answered(container, call_id, leg_ref)
            if event_type == "call.hangup":
                cause = str(payload.get("hangup_cause") or "")
                return call_flow.patient_hung_up(container, call_id, cause)
            return False
        if leg == LEG_AI:
            if event_type == "call.answered" and leg_ref:
                return call_flow.assistant_answered(container, call_id, leg_ref)
            if event_type == "call.hangup":
                return call_flow.assistant_hung_up(container, call_id)
            return False
        if str(payload.get("to") or "").startswith("sip:"):
            return False  # transfer fallback: the xAI leg manages itself
        if event_type == "call.hangup":
            return _end_call(container, call_id)
        if event_type == "call.answered" and leg_ref:
            return call_flow.insurer_answered(container, call_id, leg_ref)
        if event_type == "call.machine.detection.ended":
            result = str(payload.get("result") or "not_sure")
            return call_flow.machine_detected(container, call_id, result)
        if event_type == "call.playback.ended":
            return call_flow.playback_ended(
                container, call_id, str(payload.get("status") or "completed")
            )
    except NotFound:
        log.warning("event for an unknown call; ignoring", extra={"fields": {"call_id": call_id}})
    return False


def _end_call(container: "Container", call_id: str) -> bool:
    try:
        container.voice_manager.handle_event(
            call_id,
            VoiceEventIn(type=VoiceEventType.CALL_ENDED, status=INSURER_HUNG_UP),
            actor=ACTOR,
        )
    except NotFound:
        log.warning("hangup for unknown call; ignoring", extra={"fields": {"call_id": call_id}})
        return False
    return True
