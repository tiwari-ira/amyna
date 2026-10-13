"""Mock voice session: a scripted insurer rep plus a deterministic stand-in for Grok.

Flow (generated from the case's dispute, so it stays consistent with the data):
greeting -> claim details (answered via get_dispute_case) -> SSN request
(Core's security policy pauses the call -> HUMAN_REQUIRED) -> resume after the
human resolves -> disputed items -> insurer agrees to remove charges / request
itemization -> record_insurer_response -> closing -> call ends -> RESOLVED.

Runs synchronously inside Core. Call cursors live in process memory (D11).
"""

import re
import threading
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from claimshield_contracts.enums import DisputeAction, InsurerOutcome, TurnAction
from claimshield_contracts.models import DisputeItem
from claimshield_contracts.tool_api import DisputeCaseView

from ...services.notification_service import format_cents
from ...services.security_policy import HOLD_LINE
from .base import RESUME_VERIFIED, VoiceAdapter, VoiceEventHandler, VoiceSessionConfig
from .dispatcher import CONTROL_KEY, CONTROL_PAUSE

MOCK_REFERENCE = "MOCK-REF-48213"
MOCK_REP_NAME = "Sam"
_REMOVABLE = (DisputeAction.REMOVE_CHARGE, DisputeAction.CORRECT_CODE, DisputeAction.REVIEW_PRICE)


@dataclass(frozen=True)
class InsurerLine:
    kind: str
    text: str


def _spoken_date(value: date | None) -> str:
    return f"{value:%B} {value.day}, {value.year}" if value else "unknown"


def insurer_script(view: DisputeCaseView) -> list[InsurerLine]:
    """What the fake representative says, in order."""
    payer = view.payer_name or "the health plan"
    items = view.dispute.items if view.dispute else []
    removed = [i for i in items if i.action in _REMOVABLE]
    itemized = [i for i in items if i.action == DisputeAction.REQUEST_ITEMIZATION]
    parts = ["Thanks for holding. I reviewed the claim and the documentation."]
    if removed:
        charges = " and ".join(f"the {format_cents(i.amount_cents)} charge" for i in removed)
        parts.append(f"I'm submitting a reprocessing request to remove {charges}.")
    if itemized:
        charges = " and ".join(f"the {format_cents(i.amount_cents)} charge" for i in itemized)
        parts.append(f"For {charges}, we'll request an itemized invoice from the provider.")
    parts.append(f"Your reference number is {MOCK_REFERENCE}.")
    return [
        InsurerLine(
            "greeting",
            f"Thank you for calling {payer} member services, this is {MOCK_REP_NAME}. "
            "Who am I speaking with?",
        ),
        InsurerLine("claim_details", "Okay. What is the claim number and the date of service?"),
        InsurerLine(
            "verification",
            "Thanks. Before I can discuss the claim I need to verify the member. "
            "Can you provide the member's Social Security number?",
        ),
        InsurerLine(
            "disputed_items", "Thank you, the member is verified. Which charges are you disputing?"
        ),
        InsurerLine("resolution", " ".join(parts)),
        InsurerLine("closing", "Is there anything else I can help you with today?"),
    ]


@dataclass
class _MockCall:
    config: VoiceSessionConfig
    script: list[InsurerLine]
    cursor: int = 0
    paused: bool = False
    ended: bool = False
    facts: dict[str, Any] = field(default_factory=dict)
    # A resume requested while the script is mid-turn (e.g. the patient could not
    # be reached during the same callback) continues in the running loop.
    running: bool = False
    resume_pending: bool = False


class MockVoiceAdapter(VoiceAdapter):
    """Deterministic voice session used in mock mode, tests and the smoke run."""

    mode = "mock"

    def __init__(self) -> None:
        self._calls: dict[str, _MockCall] = {}
        self._lock = threading.Lock()

    def start_session(self, config: VoiceSessionConfig, handler: VoiceEventHandler) -> None:
        call = _MockCall(config=config, script=insurer_script(config.case_context))
        with self._lock:
            self._calls[config.call_id] = call
        self._run(call, handler)

    def resume_session(
        self, call_id: str, handler: VoiceEventHandler, note: str = RESUME_VERIFIED
    ) -> None:
        call = self._calls.get(call_id)
        if call is None:
            raise RuntimeError(f"Unknown mock call {call_id} (was Core restarted mid-call?)")
        call.paused = False
        if call.running:
            call.resume_pending = True
            return
        self._run(call, handler)

    def pause_session(self, call_id: str) -> None:
        if call_id in self._calls:
            self._calls[call_id].paused = True

    def end_session(self, call_id: str, reason: str) -> None:
        if call_id in self._calls:
            self._calls[call_id].ended = True

    # ------------------------------------------------------------------ internals

    def _run(self, call: _MockCall, handler: VoiceEventHandler) -> None:
        call.running = True
        try:
            self._loop(call, handler)
        finally:
            call.running = False

    def _hold_or_continue(self, call: _MockCall) -> bool:
        """After a pause: True to keep talking (a resume already arrived)."""
        if call.resume_pending:
            call.resume_pending = False
            call.paused = False
            return True
        call.paused = True
        return False

    def _loop(self, call: _MockCall, handler: VoiceEventHandler) -> None:
        call_id = call.config.call_id
        while not call.paused and not call.ended and call.cursor < len(call.script):
            line = call.script[call.cursor]
            call.cursor += 1
            decision = handler.on_insurer_utterance(call_id, line.text)
            if decision.action == TurnAction.PAUSE_FOR_HUMAN:
                handler.on_agent_utterance(call_id, decision.say or HOLD_LINE)
                if self._hold_or_continue(call):
                    continue
                return
            reply = self._agent_reply(call, line, handler)
            handler.on_agent_utterance(call_id, reply)
            if call.paused and not self._hold_or_continue(call):
                return
        if not call.paused and not call.ended:
            call.ended = True
            handler.on_call_ended(call_id, "completed")

    def _case(self, call: _MockCall, handler: VoiceEventHandler) -> dict[str, Any]:
        return handler.on_tool_call(call.config.call_id, "get_dispute_case", {})

    def _agent_reply(self, call: _MockCall, line: InsurerLine, handler: VoiceEventHandler) -> str:
        """Stand-in for the model: tool calls + templated replies from tool output."""
        view = DisputeCaseView.model_validate(self._case(call, handler))
        member = view.patient_display_name or "the member"
        if line.kind == "greeting":
            return (
                f"Hi {MOCK_REP_NAME}, this is an automated assistant from Amyna calling on "
                f"behalf of {member}, who approved this dispute and authorized this call. "
                f"I'm calling about claim {view.claim_number}."
            )
        if line.kind == "claim_details":
            return (
                f"The claim number is {view.claim_number} and the date of service is "
                f"{_spoken_date(view.date_of_service)}."
            )
        if line.kind == "verification":
            result = handler.on_tool_call(
                call.config.call_id,
                "request_human",
                {"reason": "identity_verification", "detail": "Representative asked for the SSN."},
            )
            if result.get(CONTROL_KEY) == CONTROL_PAUSE:
                call.paused = True
            return HOLD_LINE
        if line.kind == "disputed_items":
            items = view.dispute.items if view.dispute else []
            listed = "; ".join(
                f"line {i.line_number}, {i.summary}, {format_cents(i.amount_cents)}" for i in items
            )
            return f"We're disputing {len(items)} item(s): {listed}."
        if line.kind == "resolution":
            return self._record_outcome(call, line, handler, view)
        return f"No, that's everything. Thank you for your help, {MOCK_REP_NAME}. Goodbye."

    def _record_outcome(
        self,
        call: _MockCall,
        line: InsurerLine,
        handler: VoiceEventHandler,
        view: DisputeCaseView,
    ) -> str:
        items: list[DisputeItem] = view.dispute.items if view.dispute else []
        mentioned = [i for i in items if format_cents(i.amount_cents) in line.text]
        removed = [i for i in mentioned if i.action in _REMOVABLE]
        itemized = [i for i in mentioned if i.action == DisputeAction.REQUEST_ITEMIZATION]
        match = re.search(r"reference number is ([A-Z0-9-]+)", line.text)
        reference = match.group(1) if match else None
        if removed and len(removed) == len(items):
            outcome = InsurerOutcome.CHARGES_REMOVED
        elif removed:
            outcome = InsurerOutcome.PARTIALLY_REMOVED
        else:
            outcome = InsurerOutcome.PENDING_REVIEW
        handler.on_tool_call(
            call.config.call_id,
            "record_insurer_response",
            {
                "outcome": outcome.value,
                "reference_number": reference,
                "removed_line_ids": [i.claim_line_id for i in removed],
                "itemization_requested_line_ids": [i.claim_line_id for i in itemized],
                "representative_name": MOCK_REP_NAME,
                "notes": "Mock call: representative agreed to reprocess the claim.",
            },
        )
        removed_text = " and ".join(format_cents(i.amount_cents) for i in removed) or "nothing"
        return (
            f"Thank you. To confirm, reference number {reference}: you will remove {removed_text}"
            + (
                f" and request an itemized invoice for "
                f"{' and '.join(format_cents(i.amount_cents) for i in itemized)}."
                if itemized
                else "."
            )
        )
