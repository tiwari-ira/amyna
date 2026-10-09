"""Signed call events from Telnyx (POST /voice/telnyx/webhook).

Like the Relay webhook this route does not use the service bearer tokens: the
Ed25519 signature IS the authentication (adapters/telephony/webhook_signature.py).
Events are acknowledged with 200 promptly (Telnyx retries only 408/429/5xx;
other 4xx are never retried) and dispatch is tiny (one transfer call, or one
local end).

The two events that matter:

- ``call.answered`` for the outbound insurer leg (``to`` is the E.164 insurer
  number) -> bridge the call to xAI's SIP endpoint (``transfer_to_sip``).
  There is no TwiML equivalent in Telnyx: this step is what completes the
  insurer <-> Telnyx <-> xAI bridge. The xAI leg's own ``call.answered``
  (``to`` is a sip: URI) is ignored.
- ``call.hangup`` for the insurer leg -> end the Core call
  (``VoiceCallManager._end`` is idempotent, so duplicated/late events are safe).

Correlation: ``payload.client_state`` carries ``base64(call_id)`` set by
``TelnyxSipTelephony.dial`` (and echoed by ``transfer_to_sip``).
"""

import json
import logging
from typing import TYPE_CHECKING

from claimshield_contracts.api import VoiceEventIn
from claimshield_contracts.enums import CallMergePhase, VoiceEventType

from ..adapters.telephony.telnyx_sip import TelnyxSipTelephony, split_client_state
from ..adapters.telephony.webhook_signature import verify_telnyx_signature
from ..audit import record_audit
from ..errors import DomainError, InvalidInput, NotFound
from ..models_db import CallRow

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
    to = str(payload.get("to") or "")
    if to.startswith("sip:"):
        return False  # the xAI leg: manages itself via the control WebSocket
    state = client_state if isinstance(client_state, str) else None
    call_id, is_human = split_client_state(state)
    if is_human:
        if event_type == "call.answered":
            return _bridge_human_leg(container, payload.get("call_control_id"), call_id)
        if event_type == "call.hangup":
            return _clear_human_leg(container, call_id)
        return False
    if event_type == "call.hangup":
        return _end_call(container, call_id)
    if event_type != "call.answered":
        return False
    return _bridge_to_xai(container, payload.get("call_control_id"), call_id)


def _bridge_to_xai(container: "Container", call_control_id: object, call_id: str | None) -> bool:
    """Transfer the answered insurer leg to xAI's SIP endpoint (mock-safe)."""
    adapter = container.telephony
    if not isinstance(adapter, TelnyxSipTelephony):
        return False  # TELEPHONY_MODE=mock: nothing to bridge
    if not call_control_id or call_id is None:
        log.warning("call.answered without call_control_id/client_state; ignoring")
        return False
    try:
        adapter.transfer_to_sip(str(call_control_id), call_id)
    except DomainError as exc:  # e.g. a duplicate delivery re-transferring
        log.warning(
            "transfer to xAI SIP failed",
            extra={"fields": {"call_id": call_id, "code": exc.code}},
        )
        return True  # ack anyway: retrying a 5xx would replay the same failure
    with container.session_scope() as db:
        record_audit(
            db,
            actor=ACTOR,
            action="voice.sip_bridged",
            resource_type="call",
            resource_id=call_id,
            details={"call_control_id": str(call_control_id)},
        )
    return True


def _end_call(container: "Container", call_id: str | None) -> bool:
    if call_id is None:
        return False
    try:
        container.voice_manager.handle_event(
            call_id,
            VoiceEventIn(type=VoiceEventType.CALL_ENDED, status="ended by Telnyx"),
            actor=ACTOR,
        )
    except NotFound:
        log.warning("hangup for unknown call; ignoring", extra={"fields": {"call_id": call_id}})
        return False
    return True


def _bridge_human_leg(container: "Container", call_control_id: object, call_id: str | None) -> bool:
    """A human dialed for a handoff just answered: bridge them into the call."""
    adapter = container.telephony
    if not isinstance(adapter, TelnyxSipTelephony) or not call_control_id or call_id is None:
        log.warning("human call.answered without live telephony/client_state; ignoring")
        return False
    with container.session_scope() as db:
        call = db.get(CallRow, call_id)
        if call is None:
            log.warning("human leg answered for an unknown call")
            return False
        insurer_ref = call.provider_call_ref
        call.human_call_ref = str(call_control_id)
    if not insurer_ref:
        log.warning("human answered but the call has no insurer leg to bridge to")
        return False
    try:
        adapter.bridge_calls(str(call_control_id), insurer_ref)
    except DomainError as exc:
        log.warning("human bridge failed", extra={"fields": {"call_id": call_id, "code": exc.code}})
        return True  # ack anyway: Telnyx would only retry the same failure
    with container.session_scope() as db:
        call = db.get(CallRow, call_id)
        if call is not None:
            # 17A: the patient is now speaking with the human representative.
            call.merge_phase = CallMergePhase.PATIENT_CONNECTED.value
            call.merge_deadline_at = None
        record_audit(
            db,
            actor=ACTOR,
            action="voice.human_bridged",
            resource_type="call",
            resource_id=call_id,
            details={"call_control_id": str(call_control_id)},
        )
    return True


def _clear_human_leg(container: "Container", call_id: str | None) -> bool:
    """The human hung up: release the merge; the call stays paused until DONE."""
    if call_id is None:
        return False
    with container.session_scope() as db:
        call = db.get(CallRow, call_id)
        if call is None or call.human_call_ref is None:
            return False
        call.human_call_ref = None
        # The verify-by-human interaction finished; the AI STAYS paused until the
        # patient explicitly completes the handoff (DONE) via the intervention.
        call.merge_phase = CallMergePhase.PATIENT_RELEASED.value
        call.merge_deadline_at = None
        record_audit(
            db,
            actor=ACTOR,
            action="voice.human_leg_ended",
            resource_type="call",
            resource_id=call_id,
        )
    return True
