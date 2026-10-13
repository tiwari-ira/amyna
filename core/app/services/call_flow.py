"""Live call sequence on Telnyx: voicemail check, authorization playback, conference.

    insurer answers -> voicemail check (call.machine.detection.ended)
      machine          -> hang up, tell the patient (text "call" to retry)
      human / not_sure -> play the patient's recorded authorization (playback_start)
    call.playback.ended (or no recording / playback failed / timed out)
      -> conference from the insurer leg, then dial the AI (xAI SIP) as its own leg
    AI leg answers -> join the conference -> the AI greets
    sensitive request -> the patient's phone is dialed (voice_service)
    patient answers -> AI leg held (hears nothing) -> patient joins the conference
    patient hangs up or taps Done -> AI unheld and resumes; the call goes on

Every step is idempotent: it only runs from the expected ``media_phase``, so
duplicate or late webhooks do nothing. Steps that wait on Telnyx have a deadline
(``media_deadline_at``); ``expire_media_steps`` moves a stuck call forward.

If Telnyx refuses to create the conference, the insurer leg is transferred
straight to the AI (the earlier, working path); the patient then cannot be
added to that call and is told so.
"""

import contextlib
import logging
from datetime import timedelta
from typing import TYPE_CHECKING

from sqlalchemy import select

from claimshield_contracts.enums import CallMergePhase, CallSpeaker, CallStatus

from ..adapters.telephony.telnyx_sip import TelnyxSipTelephony
from ..audit import record_audit
from ..db import as_utc, utcnow
from ..errors import DomainError
from ..models_db import CallRow
from . import notification_service, recording_service
from .case_service import get_case_row
from .voice_service import (
    ASSISTANT_FAILED_PREFIX,
    AUTH_PLAYED_TURN,
    MEDIA_CONNECTED,
    MEDIA_CONNECTING,
    MEDIA_PLAYING,
    MEDIA_SCREENING,
    MEDIA_TRANSFERRED,
    VOICEMAIL_REASON,
    _append_turn,
)

if TYPE_CHECKING:
    from ..container import Container

log = logging.getLogger("claimshield.call_flow")
ACTOR = "service:telnyx"
SCREEN_S = 6  # longest wait for the voicemail check before going ahead anyway
PLAYBACK_MAX_S = 60  # the memo is trimmed to 45 s when stored
CONNECT_MAX_S = 35  # the AI leg must answer and join within this
SILENCE_PATH = "/voice/media/silence.wav"


def _live(container: "Container") -> TelnyxSipTelephony | None:
    adapter = container.telephony
    return adapter if isinstance(adapter, TelnyxSipTelephony) else None


def _deadline(seconds: int):
    return utcnow() + timedelta(seconds=seconds)


# ------------------------------------------------------------------ insurer leg


def insurer_answered(container: "Container", call_id: str, leg_ref: str) -> bool:
    adapter = _live(container)
    if adapter is None:
        return False  # TELEPHONY_MODE=mock: nothing to connect
    with container.session_scope() as db:
        call = db.get(CallRow, call_id)
        if call is None or call.ended_at is not None:
            return False
        if call.media_phase is not None:
            return True  # duplicate delivery
        call.provider_call_ref = call.provider_call_ref or leg_ref
        call.media_phase = MEDIA_SCREENING
        call.media_deadline_at = _deadline(SCREEN_S)
        _append_turn(call, CallSpeaker.SYSTEM, "The insurer line answered.")
    if not container.settings.telephony_voicemail_detection:
        after_screening(container, call_id)
    return True


def machine_detected(container: "Container", call_id: str, result: str) -> bool:
    """Voicemail check result: human | machine | not_sure."""
    if result != "machine":
        after_screening(container, call_id)
        return True
    with container.session_scope() as db:
        call = db.get(CallRow, call_id)
        if call is None or call.ended_at is not None or call.media_phase != MEDIA_SCREENING:
            return False
        call.media_deadline_at = None
        _append_turn(call, CallSpeaker.SYSTEM, "Voicemail answered; hanging up.")
    container.voice_manager.end_call(call_id, VOICEMAIL_REASON, failed=True)
    return True


def after_screening(container: "Container", call_id: str) -> None:
    """A person answered: play the authorization, or connect the AI right away."""
    adapter = _live(container)
    with container.session_scope() as db:
        call = db.get(CallRow, call_id)
        if call is None or call.ended_at is not None or call.media_phase != MEDIA_SCREENING:
            return
        case = get_case_row(db, call.case_id)
        url = recording_service.playback_url(db, case.id, container.settings.public_base_url)
        payer = case.payer_name or "your insurer"
        if url and adapter is not None:
            call.media_phase = MEDIA_PLAYING
            call.media_deadline_at = _deadline(PLAYBACK_MAX_S)
            text = (
                f"{payer} picked up. I'm playing your recorded authorization, "
                "then I'll talk to them."
            )
        else:
            text = f"{payer} picked up. I'm talking to them now."
        notification_service.notify_status(db, container, case, text)
        insurer_ref = call.provider_call_ref
    if url and adapter is not None and insurer_ref:
        try:
            adapter.playback_start(insurer_ref, url, call_id)
        except DomainError as exc:
            log.warning("authorization playback failed to start (%s)", exc.code)
            playback_ended(container, call_id, "failed")
        else:
            with container.session_scope() as db:
                record_audit(
                    db, actor=ACTOR, action="voice.authorization_playing", resource_id=call_id
                )
        return
    connect_assistant(container, call_id)


def playback_ended(container: "Container", call_id: str, status: str) -> bool:
    with container.session_scope() as db:
        call = db.get(CallRow, call_id)
        if call is None or call.ended_at is not None or call.media_phase != MEDIA_PLAYING:
            return False
        if status == "completed":
            _append_turn(call, CallSpeaker.SYSTEM, AUTH_PLAYED_TURN)
        else:
            # Never replaced with generated speech: the AI introduces the authorization.
            _append_turn(
                call, CallSpeaker.SYSTEM, f"The recorded authorization did not play ({status})."
            )
    connect_assistant(container, call_id)
    return True


def connect_assistant(container: "Container", call_id: str) -> None:
    """Conference from the insurer leg, then dial the AI as its own leg."""
    adapter = _live(container)
    with container.session_scope() as db:
        call = db.get(CallRow, call_id)
        if (
            adapter is None
            or call is None
            or call.ended_at is not None
            or call.media_phase not in (MEDIA_SCREENING, MEDIA_PLAYING)
        ):
            return
        call.media_phase = MEDIA_CONNECTING
        call.media_deadline_at = _deadline(CONNECT_MAX_S)
        insurer_ref = call.provider_call_ref
    try:
        conference = adapter.create_conference(call_id, insurer_ref)
    except DomainError as exc:
        log.warning("conference could not be created (%s); transferring instead", exc.code)
        _transfer_instead(container, adapter, call_id, insurer_ref)
        return
    with container.session_scope() as db:
        call = db.get(CallRow, call_id)
        call.conference_ref = conference
        record_audit(db, actor=ACTOR, action="voice.conference_created", resource_id=call_id)
    try:
        dial = adapter.dial_assistant(call_id)
    except DomainError as exc:
        log.warning("AI leg could not be dialed (%s)", exc.code)
        _assistant_failed(container, call_id, "the voice assistant could not be dialed")
        return
    with container.session_scope() as db:
        db.get(CallRow, call_id).ai_leg_ref = dial.provider_call_ref


def _transfer_instead(
    container: "Container", adapter: TelnyxSipTelephony, call_id: str, insurer_ref: str
) -> None:
    try:
        adapter.transfer_to_sip(insurer_ref, call_id)
    except DomainError:
        _assistant_failed(container, call_id, "the voice assistant could not join")
        return
    with container.session_scope() as db:
        call = db.get(CallRow, call_id)
        call.media_phase = MEDIA_TRANSFERRED
        call.media_deadline_at = None
        record_audit(db, actor=ACTOR, action="voice.sip_bridged", resource_id=call_id)


def _assistant_failed(container: "Container", call_id: str, detail: str) -> None:
    container.voice_manager.end_call(call_id, f"{ASSISTANT_FAILED_PREFIX}: {detail}", failed=True)


# ------------------------------------------------------------------ AI leg


def assistant_answered(container: "Container", call_id: str, leg_ref: str) -> bool:
    adapter = _live(container)
    if adapter is None:
        return False
    with container.session_scope() as db:
        call = db.get(CallRow, call_id)
        if call is None:
            return False
        ended = call.ended_at is not None
        stale = ended or call.media_phase != MEDIA_CONNECTING
        conference = call.conference_ref
        if not stale:
            call.ai_leg_ref = leg_ref
    if stale or not conference:
        if ended:
            container.voice_manager._quiet_hangup(leg_ref)
        return True
    try:
        adapter.join_conference(conference, leg_ref)
    except DomainError as exc:
        log.warning("AI leg could not join the conference (%s)", exc.code)
        _assistant_failed(container, call_id, "the voice assistant could not join")
        return True
    with container.session_scope() as db:
        call = db.get(CallRow, call_id)
        call.media_phase = MEDIA_CONNECTED
        call.media_deadline_at = None
        _append_turn(call, CallSpeaker.SYSTEM, "The voice assistant joined the call.")
    container.voice_manager.assistant_ready(call_id)
    return True


def assistant_hung_up(container: "Container", call_id: str) -> bool:
    with container.session_scope() as db:
        call = db.get(CallRow, call_id)
        if call is None or call.ended_at is not None:
            return False
        ringing = call.media_phase == MEDIA_CONNECTING
    detail = "the voice assistant did not answer" if ringing else "the voice assistant left"
    _assistant_failed(container, call_id, detail)
    return True


# ------------------------------------------------------------------ patient leg

_NO_ANSWER = {
    "timeout": "you didn't pick up",
    "no_answer": "you didn't pick up",
    "user_busy": "your line was busy",
    "call_rejected": "the call was declined",
    "originator_cancel": "the call to you was cancelled",
}


def patient_answered(container: "Container", call_id: str, leg_ref: str) -> bool:
    """The patient picked up: hold the AI, then join the patient to the conference."""
    adapter = _live(container)
    if adapter is None:
        return False
    with container.session_scope() as db:
        call = db.get(CallRow, call_id)
        if call is None:
            return False
        stale = (
            call.ended_at is not None
            or call.status != CallStatus.PAUSED_FOR_HUMAN.value
            or call.merge_phase != CallMergePhase.PATIENT_BEING_CONNECTED.value
            or not call.conference_ref
        )
        conference, ai_leg = call.conference_ref, call.ai_leg_ref
        if not stale:
            call.human_call_ref = leg_ref
    if stale:
        # A late answer (timed out, already ended, or a duplicate leg): never a 4th person.
        container.voice_manager._quiet_hangup(leg_ref)
        return True
    try:
        if ai_leg:
            # Held: the AI neither hears the patient's answer nor speaks.
            adapter.hold_participants(
                conference,
                [ai_leg],
                audio_url=container.settings.public_base_url.rstrip("/") + SILENCE_PATH,
            )
        adapter.join_conference(conference, leg_ref)
    except DomainError as exc:
        log.warning("patient could not join the conference (%s)", exc.code)
        container.voice_manager.patient_unreachable(call_id, "the call couldn't add you")
        return True
    with container.session_scope() as db:
        call = db.get(CallRow, call_id)
        call.merge_phase = CallMergePhase.PATIENT_CONNECTED.value
        call.merge_deadline_at = None
        _append_turn(call, CallSpeaker.SYSTEM, "The member joined the call.")
        record_audit(
            db,
            actor=ACTOR,
            action="voice.patient_joined",
            case_id=call.case_id,
            resource_id=call_id,
        )
    return True


def patient_hung_up(container: "Container", call_id: str, cause: str) -> bool:
    with container.session_scope() as db:
        call = db.get(CallRow, call_id)
        if call is None:
            return False
        phase = call.merge_phase
        call.human_call_ref = None
        ended = call.ended_at is not None
    if ended:
        return True
    if phase == CallMergePhase.PATIENT_BEING_CONNECTED.value:
        detail = _NO_ANSWER.get(cause, "the call to you didn't connect")
        container.voice_manager.patient_unreachable(call_id, detail)
    elif phase in (
        CallMergePhase.PATIENT_CONNECTED.value,
        CallMergePhase.PATIENT_HANDLING_VERIFICATION.value,
    ):
        container.voice_manager.patient_left(call_id)
    return True


# ------------------------------------------------------------------ deadlines


def expire_media_steps(container: "Container", *, now=None) -> int:
    """Move calls whose Telnyx step never reported back. Returns how many moved."""
    current = as_utc(now or utcnow())
    with container.session_scope() as db:
        due = [
            (call.id, call.media_phase, call.provider_call_ref)
            for call in db.scalars(
                select(CallRow).where(
                    CallRow.ended_at.is_(None), CallRow.media_deadline_at.is_not(None)
                )
            )
            if as_utc(call.media_deadline_at) <= current
        ]
    for call_id, phase, insurer_ref in due:
        if phase == MEDIA_SCREENING:
            after_screening(container, call_id)
        elif phase == MEDIA_PLAYING:
            adapter = _live(container)
            if adapter is not None and insurer_ref:
                with contextlib.suppress(DomainError):
                    adapter.playback_stop(insurer_ref)
            playback_ended(container, call_id, "timed out")
        elif phase == MEDIA_CONNECTING:
            _assistant_failed(container, call_id, "the voice assistant did not join in time")
        else:
            with container.session_scope() as db:
                db.get(CallRow, call_id).media_deadline_at = None
    return len(due)
