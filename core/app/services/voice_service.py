"""Core's voice call manager. Owns insurer calls end to end (Fetch only requests them).

``VoiceCallManager`` implements the adapter's ``VoiceEventHandler``:

- every insurer utterance goes through ``security_policy.detect_sensitive_request``
  (in code, independent of the model) -> HumanIntervention + HUMAN_REQUIRED + pause;
- every agent utterance goes through ``check_agent_utterance``;
- model tool calls go through ``ToolCallDispatcher`` with case/call ids injected.

Each callback opens its own DB transaction, so the same code serves the
synchronous mock session and the threaded live session.
"""

import logging
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError
from sqlalchemy.orm import Session

from claimshield_contracts.api import VoiceEventIn, VoiceEventResult
from claimshield_contracts.enums import (
    CallMergePhase,
    CallSpeaker,
    CallStatus,
    CaseState,
    DisputeStatus,
    HumanReason,
    InterventionStatus,
    ServiceName,
    TurnAction,
    VoiceEventType,
)
from claimshield_contracts.ids import new_id
from claimshield_contracts.models import CallSession, CallTurn, HumanIntervention, InsurerResponse
from claimshield_contracts.tool_api import (
    TOOL_SPECS,
    CoreToolError,
    RecordInsurerResponseRequest,
    ToolName,
)

from .. import mappers
from ..adapters.xai_voice.base import GuardrailVerdict, TurnDecision, VoiceSessionConfig
from ..adapters.xai_voice.dispatcher import ToolCallDispatcher
from ..adapters.xai_voice.prompts import VOICE_TOOL_SCHEMAS, build_instructions
from ..audit import mask_phone, record_audit
from ..auth import identity_for
from ..db import as_utc, utcnow
from ..errors import Conflict, DomainError, InvalidInput, NotFound, SafetyBlocked, UpstreamError
from ..models_db import CallRow, CaseRow, InterventionRow
from . import notification_service
from .case_service import get_case_row, latest_dispute, transition
from .claim_service import claim_lines, get_case_claim_row
from .dispute_service import dispute_case_view
from .human_service import HANDOFF_OPTIONS, create_intervention, open_call_interventions
from .security_policy import (
    HOLD_LINE,
    SAFE_FALLBACK_LINE,
    AllowedFacts,
    check_agent_utterance,
    detect_sensitive_request,
    reject_sensitive_notes,
)

if TYPE_CHECKING:
    from ..container import Container

SENSITIVE_PROMPTS = {
    "ssn": "The representative asked for your Social Security number to verify your identity.",
    "dob_verification": "The representative asked to verify your date of birth.",
    "pin": "The representative asked for a PIN.",
    "password": "The representative asked for a password.",
    "auth_code": "The representative asked for a verification code.",
    "payment_card": "The representative asked for card details.",
    "bank_account": "The representative asked for bank account details.",
}
LIVE_CALL_STATUSES = (CallStatus.IN_PROGRESS.value, CallStatus.PAUSED_FOR_HUMAN.value)
HANDOFF_REASONS = frozenset(HumanReason) - {HumanReason.PATIENT_CONFIRMATION}
log = logging.getLogger("claimshield.voice")


def _get_call(db: Session, call_id: str) -> CallRow:
    call = db.get(CallRow, call_id)
    if call is None:
        raise NotFound(f"Call {call_id} not found")
    return call


def _append_turn(
    call: CallRow, speaker: CallSpeaker, text: str, flags: list[str] | None = None
) -> None:
    turn = CallTurn(speaker=speaker, text=text, at=utcnow(), flags=flags or [])
    call.turns = [*call.turns, mappers.dump(turn)]


def record_insurer_response(
    db: Session, req: RecordInsurerResponseRequest, *, actor: str
) -> InsurerResponse:
    """Persist what the insurer agreed to. The case resolves when the call ends."""
    call = _get_call(db, req.call_id)
    if call.case_id != req.case_id:
        raise InvalidInput("call_id does not belong to case_id")
    if call.status not in LIVE_CALL_STATUSES:
        raise Conflict("The call is not active.", code="call_not_active")
    claim = get_case_claim_row(db, req.case_id)
    line_ids = {line.id for line in claim_lines(db, claim.id)} if claim else set()
    referenced = set(req.removed_line_ids) | set(req.itemization_requested_line_ids)
    if not referenced <= line_ids:
        raise InvalidInput("Response references claim lines that are not on this claim")
    for text in (req.notes, req.representative_name, req.reference_number):
        reject_sensitive_notes(text)
    response = InsurerResponse(
        outcome=req.outcome,
        reference_number=req.reference_number,
        removed_line_ids=req.removed_line_ids,
        itemization_requested_line_ids=req.itemization_requested_line_ids,
        representative_name=req.representative_name,
        notes=req.notes,
        recorded_at=utcnow(),
    )
    call.insurer_response = mappers.dump(response)
    _append_turn(call, CallSpeaker.SYSTEM, f"Insurer response recorded: {req.outcome.value}")
    record_audit(
        db,
        actor=actor,
        action="voice.insurer_response_recorded",
        case_id=req.case_id,
        resource_type="call",
        resource_id=call.id,
        details={"outcome": req.outcome.value, "removed": len(req.removed_line_ids)},
    )
    return response


class VoiceToolsBackend:
    """Runs voice tool calls through ToolService with the VOICE identity."""

    def __init__(self, container: "Container") -> None:
        from .tool_service import ToolService

        self._tools = ToolService(container, identity_for(ServiceName.VOICE))

    def call(self, tool: ToolName, payload: dict[str, Any]) -> dict[str, Any]:
        request_model, _ = TOOL_SPECS[tool]
        try:
            request = request_model.model_validate(payload)
            result = getattr(self._tools, tool.value)(request)
        except ValidationError as exc:
            raise CoreToolError(422, "invalid_arguments", str(exc.errors()[:3])) from exc
        except DomainError as exc:
            raise CoreToolError(exc.status_code, exc.code, exc.message) from exc
        return result.model_dump(mode="json")


class VoiceCallManager:
    """Starts, supervises, pauses, resumes and closes insurer calls."""

    def __init__(self, container: "Container") -> None:
        self._c = container
        self._backend = VoiceToolsBackend(container)

    # ------------------------------------------------------------------ lifecycle

    def start_call(self, case_id: str, *, actor: str) -> CallSession:
        """Guarded start: APPROVED dispute + patient authorization + allowlisted number."""
        with self._c.session_scope() as db:
            case = get_case_row(db, case_id)
            dispute = latest_dispute(db, case_id)
            if dispute is None:
                raise Conflict("No dispute exists for this case.", code="no_dispute")
            number = case.payer_phone
            if not number:
                raise InvalidInput("No insurer phone number is on file for this case.")
            if number not in self._c.settings.allowed_numbers():
                raise SafetyBlocked(
                    "Insurer number is not in TELEPHONY_ALLOWED_NUMBERS; refusing to dial.",
                    code="number_not_allowed",
                )
            call = CallRow(
                id=new_id("call"),
                case_id=case.id,
                dispute_id=dispute.id,
                status=CallStatus.INITIATING.value,
                to_number_masked=mask_phone(number),
                turns=[],
            )
            db.add(call)
            transition(db, case, CaseState.CALL_IN_PROGRESS, actor=actor)
            record_audit(
                db, actor=actor, action="voice.call_started", case_id=case.id, resource_id=call.id
            )
            call_id = call.id

        try:
            dial = self._c.telephony.dial(call_id, number)
        except DomainError:
            self._end(call_id, "dial refused", failed=True)
            raise
        except Exception as exc:
            self._end(call_id, f"dial failed: {type(exc).__name__}", failed=True)
            raise UpstreamError("Telephony provider failed to place the call.") from exc

        with self._c.session_scope() as db:
            call = _get_call(db, call_id)
            call.provider_call_ref = dial.provider_call_ref
            call.status = CallStatus.IN_PROGRESS.value
            view = dispute_case_view(db, case_id)
        config = VoiceSessionConfig(
            call_id=call_id,
            case_id=case_id,
            instructions=build_instructions(view),
            tools=VOICE_TOOL_SCHEMAS,
            case_context=view,
            model=self._c.settings.xai_voice_model or None,
            voice=self._c.settings.xai_voice_name or None,
        )
        try:
            if not self._c.voice.needs_sip_attach:
                # Mock/test loopback adapters drive the session immediately.
                self._c.voice.start_session(config, self)
            # SIP mode: the session attaches only when xAI's signed
            # realtime.call.incoming webhook arrives (/voice/xai/webhook).
        except Exception as exc:
            log.exception("voice session failed to start", extra={"fields": {"call_id": call_id}})
            self._quiet_hangup(dial.provider_call_ref)
            self._end(call_id, f"voice session failed: {type(exc).__name__}", failed=True)
            raise UpstreamError("The voice session could not be started.") from exc
        return self.get_call(call_id)

    def attach_sip_session(self, call_id: str, xai_call_id: str, *, actor: str) -> CallSession:
        """SIP attach (task 15): start the realtime WS for the bridged insurer call.

        Idempotent: a duplicate inbound webhook with the same call id is ignored.
        The insurer leg stays alive in Telnyx while the session attaches.
        """
        with self._c.session_scope() as db:
            call = _get_call(db, call_id)
            if call.xai_call_ref == xai_call_id:
                already = True
            else:
                already = False
                call.xai_call_ref = xai_call_id
                _append_turn(call, CallSpeaker.SYSTEM, "xAI bridged the insurer call.")
                view = dispute_case_view(db, call.case_id)
                case_id = call.case_id
        if already:
            return self.get_call(call_id)  # idempotent duplicate webhook
        config = VoiceSessionConfig(
            call_id=call_id,
            case_id=case_id,
            instructions=build_instructions(view),
            tools=VOICE_TOOL_SCHEMAS,
            case_context=view,
            model=self._c.settings.xai_voice_model or None,
            voice=self._c.settings.xai_voice_name or None,
            bridge_call_id=xai_call_id,
        )
        try:
            self._c.voice.start_session(config, self)
        except Exception as exc:
            log.exception("sip attach failed", extra={"fields": {"call_id": call_id}})
            with self._c.session_scope() as db:
                call = _get_call(db, call_id)
                ref = call.provider_call_ref
            self._quiet_hangup(ref)
            self._end(call_id, f"sip attach failed: {type(exc).__name__}", failed=True)
            raise UpstreamError("The voice session could not attach to the call.") from exc
        return self.get_call(call_id)

    def get_call(self, call_id: str) -> CallSession:
        with self._c.session_scope() as db:
            return mappers.call_to_model(_get_call(db, call_id))

    def resume_after_human(self, call_id: str, *, actor: str) -> None:
        """Called after a call-linked intervention resolves with DONE."""
        with self._c.session_scope() as db:
            call = _get_call(db, call_id)
            if (
                open_call_interventions(db, call_id)
                or call.status != CallStatus.PAUSED_FOR_HUMAN.value
            ):
                return
            human_ref = call.human_call_ref
            call.human_call_ref = None
            call.merge_deadline_at = None
            # 17A: the AI resumes ONLY after this explicit transition -- and only
            # once the human leg is being safely removed.
            call.merge_phase = CallMergePhase.PATIENT_RELEASED.value
            if human_ref:
                _append_turn(call, CallSpeaker.SYSTEM, "The human left the call.")
            case = get_case_row(db, call.case_id)
            call.status = CallStatus.IN_PROGRESS.value
            call.open_intervention_id = None
            _append_turn(
                call, CallSpeaker.HUMAN, "Member verified directly with the representative."
            )
            transition(db, case, CaseState.CALL_IN_PROGRESS, actor=actor)
        if human_ref:
            self._quiet_hangup(human_ref)
        with self._c.session_scope() as db:
            _get_call(db, call_id).merge_phase = CallMergePhase.AI_RESUMING.value
        try:
            self._c.voice.resume_session(call_id, self)
        except Exception as exc:
            log.exception("voice session could not resume", extra={"fields": {"call_id": call_id}})
            self._end(call_id, f"voice session lost: {type(exc).__name__}", failed=True)
            raise UpstreamError("The call could not be resumed and was closed.") from exc
        with self._c.session_scope() as db:
            # AI_CALLING: the merge lifecycle is complete.
            _get_call(db, call_id).merge_phase = None

    def cancel_after_human(self, call_id: str, *, actor: str) -> None:
        """Called after a call-linked intervention resolves with CANCEL."""
        with self._c.session_scope() as db:
            call_row = _get_call(db, call_id)
            ref = call_row.provider_call_ref
            human_ref = call_row.human_call_ref
            call_row.human_call_ref = None
            call_row.merge_deadline_at = None
            if call_row.merge_phase is not None:
                call_row.merge_phase = CallMergePhase.PATIENT_RELEASED.value
        try:
            self._c.voice.end_session(call_id, "cancelled by patient")
        except Exception:
            log.exception(
                "voice session did not end cleanly", extra={"fields": {"call_id": call_id}}
            )
        finally:
            self._quiet_hangup(human_ref)
            self._quiet_hangup(ref)
            self.on_call_ended(call_id, "cancelled by patient")

    def _quiet_hangup(self, provider_call_ref: str | None) -> None:
        """Best-effort hangup; failures are logged, never left to strand a case."""
        if not provider_call_ref:
            return
        try:
            self._c.telephony.hangup(provider_call_ref)
        except Exception:
            log.exception("telephony hangup failed", extra={"fields": {"ref": provider_call_ref}})

    def request_handoff(
        self, call_id: str, *, reason: HumanReason, detail: str, actor: str
    ) -> HumanIntervention:
        """Pause an active call for a human (model tool call or explicit API request)."""
        if reason not in HANDOFF_REASONS:
            reason = HumanReason.CALL_ESCALATION  # pausing is always the safe default
        with self._c.session_scope() as db:
            call = _get_call(db, call_id)
            if call.status == CallStatus.PAUSED_FOR_HUMAN.value and call.open_intervention_id:
                existing = open_call_interventions(db, call_id)
                if existing:
                    return mappers.intervention_to_model(existing[0])
            if call.status != CallStatus.IN_PROGRESS.value:
                raise Conflict("The call is not in progress.", code="call_not_active")
            row = self._pause_for_human(db, call, reason=reason, prompt=detail, actor=actor)
            insurer_ref = call.provider_call_ref
            result = mappers.intervention_to_model(row)
        self._c.voice.pause_session(call_id)
        self._maybe_dial_human(call_id, insurer_ref)
        return result

    def handle_event(self, call_id: str, event: VoiceEventIn, *, actor: str) -> VoiceEventResult:
        """External events: insurer transcripts or status callbacks from bridges."""
        decision = TurnDecision(action=TurnAction.CONTINUE)
        if event.type == VoiceEventType.INSURER_UTTERANCE:
            decision = self.on_insurer_utterance(call_id, event.text or "")
            if decision.action == TurnAction.PAUSE_FOR_HUMAN:
                self._c.voice.pause_session(call_id)
        elif event.type == VoiceEventType.CALL_STATUS:
            with self._c.session_scope() as db:
                _append_turn(_get_call(db, call_id), CallSpeaker.SYSTEM, f"status: {event.status}")
        elif event.type == VoiceEventType.CALL_ENDED:
            self.on_call_ended(call_id, event.status or "ended by provider")
            decision = TurnDecision(action=TurnAction.END)
        return VoiceEventResult(
            action=decision.action,
            call=self.get_call(call_id),
            intervention_id=decision.intervention_id,
        )

    # ------------------------------------------------------------------ VoiceEventHandler

    def on_insurer_utterance(self, call_id: str, text: str) -> TurnDecision:
        with self._c.session_scope() as db:
            call = _get_call(db, call_id)
            sensitive = detect_sensitive_request(text)
            flags = [f"sensitive:{sensitive.category}"] if sensitive else []
            _append_turn(call, CallSpeaker.INSURER, text, flags)
            if sensitive is None or call.status != CallStatus.IN_PROGRESS.value:
                return TurnDecision(action=TurnAction.CONTINUE)
            row = self._pause_for_human(
                db,
                call,
                reason=sensitive.reason,
                prompt=SENSITIVE_PROMPTS.get(sensitive.category, "The representative needs you."),
                actor="core:security_policy",
            )
            insurer_ref = call.provider_call_ref
            intervention_id = row.id
        self._maybe_dial_human(call_id, insurer_ref)
        return TurnDecision(
            action=TurnAction.PAUSE_FOR_HUMAN,
            say=HOLD_LINE,
            intervention_id=intervention_id,
        )

    def on_agent_utterance(self, call_id: str, text: str) -> GuardrailVerdict:
        with self._c.session_scope() as db:
            call = _get_call(db, call_id)
            check = check_agent_utterance(
                text, AllowedFacts.from_view(dispute_case_view(db, call.case_id))
            )
            spoken = text if check.allowed else SAFE_FALLBACK_LINE
            _append_turn(
                call, CallSpeaker.AGENT, spoken, [f"blocked:{v}" for v in check.violations]
            )
            if not check.allowed:
                record_audit(
                    db,
                    actor="core:security_policy",
                    action="voice.guardrail_blocked",
                    case_id=call.case_id,
                    resource_id=call.id,
                    details={"violations": list(check.violations)},
                )
            return GuardrailVerdict(allowed=check.allowed, text=spoken, violations=check.violations)

    def on_tool_call(self, call_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        with self._c.session_scope() as db:
            call = _get_call(db, call_id)
            case_id = call.case_id
            _append_turn(call, CallSpeaker.SYSTEM, f"tool:{name}")
        dispatcher = ToolCallDispatcher(self._backend, case_id=case_id, call_id=call_id)
        return dispatcher.dispatch(name, arguments)

    def on_call_ended(self, call_id: str, reason: str) -> None:
        self._end(call_id, reason, failed=False)

    def _maybe_dial_human(self, call_id: str, insurer_ref: str | None) -> None:
        """17A: immediately connect the patient to the SAME live insurer call.

        Pause AI output (done by _pause_for_human), preserve the insurer
        connection, and dial HUMAN_HANDOFF_PHONE as a second leg. Live telephony
        bridges them on the human's call.answered webhook; the mock answers
        instantly. ``merge_phase`` tracks the first-class lifecycle; the call is
        paused and the AI stays silent the whole time. Idempotent: a duplicate
        sensitive request while already dials/connected does nothing extra
        (``call.status`` is no longer IN_PROGRESS, so no re-dial happens).
        A dial failure escalates to MERGE_FAILED -- never a silent fallback and
        never an automatic resume.
        """
        number = self._c.settings.human_handoff_phone
        if not number or not insurer_ref:
            return
        deadline = utcnow() + timedelta(seconds=self._c.settings.human_merge_timeout_s)
        with self._c.session_scope() as db:
            call = _get_call(db, call_id)
            if call.human_call_ref or call.merge_phase in (
                CallMergePhase.MERGE_FAILED.value,
                CallMergePhase.PATIENT_BEING_CONNECTED.value,
                CallMergePhase.PATIENT_CONNECTED.value,
                CallMergePhase.PATIENT_HANDLING_VERIFICATION.value,
            ):
                return  # idempotent: a human leg already exists or is in flight
            call.merge_phase = CallMergePhase.PATIENT_BEING_CONNECTED.value
            call.merge_deadline_at = deadline
        try:
            result = self._c.telephony.dial_human(call_id, number, insurer_ref)
        except Exception:
            log.exception(
                "human merge dial failed",
                extra={"fields": {"call_id": call_id}},
            )
            self._mark_merge_failed(call_id, "the human merge call could not be placed")
            return
        if result is None:  # adapter does not support a parallel human leg
            with self._c.session_scope() as db:
                call = _get_call(db, call_id)
                call.merge_phase = CallMergePhase.SENSITIVE_REQUEST_DETECTED.value
                call.merge_deadline_at = None
            return
        with self._c.session_scope() as db:
            call = _get_call(db, call_id)
            call.human_call_ref = result.provider_call_ref
            if result.status == "answered":
                call.merge_phase = CallMergePhase.PATIENT_CONNECTED.value
                call.merge_deadline_at = None
            _append_turn(
                call,
                CallSpeaker.SYSTEM,
                f"Connecting you to the representative ({mask_phone(number)}).",
            )

    def _mark_merge_failed(self, call_id: str, detail: str) -> None:
        """Explicit failure state: keep the AI paused, notify the patient, never
        auto-resume. The insurer leg stays alive within policy; the patient (or
        an operator) must act explicitly to continue or end the call."""
        with self._c.session_scope() as db:
            call = _get_call(db, call_id)
            if call.merge_phase == CallMergePhase.MERGE_FAILED.value:
                return  # idempotent
            call.merge_phase = CallMergePhase.MERGE_FAILED.value
            call.merge_deadline_at = None
            _append_turn(call, CallSpeaker.SYSTEM, f"Could not connect the patient: {detail}.")
            case = get_case_row(db, call.case_id)
            record_audit(
                db,
                actor="core:voice",
                action="voice.merge_failed",
                case_id=case.id,
                resource_id=call.id,
                details={"detail": detail},
            )
            notification_service.notify_status(
                db,
                self._c,
                case,
                "I couldn't reach your phone number to connect you to the call. "
                "The insurer is still on the line. Please check that your phone can "
                "receive calls, then tell me when you're ready and I'll try again.",
            )

    def expire_overdue_merges(self, *, now=None) -> int:
        """Called by the jobs worker: time out a stale PATIENT_BEING_CONNECTED.

        Only the unreachable-patient path expires; a failed merge already has an
        explicit MERGE_FAILED state. Returns the number of merges escalated.
        """
        count = 0
        with self._c.session_scope() as db:
            from sqlalchemy import select

            stmt = (
                select(CallRow)
                .where(CallRow.status == CallStatus.PAUSED_FOR_HUMAN.value)
                .where(CallRow.merge_phase == CallMergePhase.PATIENT_BEING_CONNECTED.value)
            )
            calls = list(db.scalars(stmt))
        for call in calls:
            if call.merge_deadline_at is None:
                continue
            deadline = as_utc(call.merge_deadline_at)
            if deadline <= as_utc(now or utcnow()):
                self._mark_merge_failed(call.id, "the patient did not answer in time")
                count += 1
        return count

    # ------------------------------------------------------------------ internals

    def _pause_for_human(
        self, db: Session, call: CallRow, *, reason: HumanReason, prompt: str, actor: str
    ) -> InterventionRow:
        case = get_case_row(db, call.case_id)
        row = create_intervention(
            db,
            case_id=case.id,
            reason=reason,
            prompt=prompt,
            options=HANDOFF_OPTIONS,
            actor=actor,
            call_id=call.id,
        )
        call.status = CallStatus.PAUSED_FOR_HUMAN.value
        call.open_intervention_id = row.id
        # 17A first-class state: the immediate human merge begins here.
        if call.merge_phase != CallMergePhase.MERGE_FAILED.value:
            call.merge_phase = CallMergePhase.SENSITIVE_REQUEST_DETECTED.value
        _append_turn(call, CallSpeaker.SYSTEM, f"Paused for human: {reason.value}")
        transition(db, case, CaseState.HUMAN_REQUIRED, actor=actor)
        notification_service.notify_handoff(db, self._c, case, row)
        return row

    def _end(self, call_id: str, reason: str, *, failed: bool) -> None:
        with self._c.session_scope() as db:
            call = _get_call(db, call_id)
            if call.ended_at is not None:
                return
            case = get_case_row(db, call.case_id)
            dispute = latest_dispute(db, case.id)
            call.ended_at = utcnow()
            call.end_reason = reason
            for row in open_call_interventions(db, call_id):
                row.status = InterventionStatus.CANCELLED.value
            resolved = bool(call.insurer_response) and not failed
            call.status = (CallStatus.COMPLETED if resolved else CallStatus.FAILED).value
            if dispute is not None:
                dispute.status = (
                    DisputeStatus.RESOLVED if resolved else DisputeStatus.FAILED
                ).value
            target = CaseState.RESOLVED if resolved else CaseState.FAILED
            transition(db, case, target, actor="core:voice", reason=reason)
            record_audit(
                db,
                actor="core:voice",
                action="voice.call_ended",
                case_id=case.id,
                resource_id=call.id,
                details={"reason": reason, "status": call.status},
            )
            notification_service.notify_status(
                db, self._c, case, _final_message(case, call), final=True
            )


def _final_message(case: CaseRow, call: CallRow) -> str:
    payer = case.payer_name or "your insurer"
    if not call.insurer_response:
        return f"The call with {payer} ended without a resolution ({call.end_reason})."
    response = InsurerResponse.model_validate(call.insurer_response)
    removed = len(response.removed_line_ids)
    itemized = len(response.itemization_requested_line_ids)
    parts = [f"Resolved with {payer}: {response.outcome.value.replace('_', ' ')}."]
    if removed:
        parts.append(f"{removed} charge(s) will be removed.")
    if itemized:
        parts.append(f"Itemization requested for {itemized} charge(s).")
    if response.reference_number:
        parts.append(f"Reference {response.reference_number}.")
    return " ".join(parts)
