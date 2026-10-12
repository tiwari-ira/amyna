"""Explicit case state machine with guards (pure; no database access).

The service layer builds a :class:`GuardContext` from the database and calls
:func:`check_transition` before every state change. See docs/STATE_MACHINE.md.
"""

from collections.abc import Callable
from dataclasses import dataclass

from claimshield_contracts.enums import CaseState as S
from claimshield_contracts.enums import DisputeStatus

from .errors import Conflict

TERMINAL_STATES: frozenset[S] = frozenset({S.RESOLVED, S.REJECTED, S.FAILED})

TRANSITIONS: dict[S, frozenset[S]] = {
    S.CREATED: frozenset({S.VISIT_RECORDED, S.CLAIM_RECEIVED}),
    S.VISIT_RECORDED: frozenset({S.TRANSCRIBED}),
    S.TRANSCRIBED: frozenset({S.EVIDENCE_EXTRACTED}),
    S.EVIDENCE_EXTRACTED: frozenset({S.CLAIM_RECEIVED}),
    S.CLAIM_RECEIVED: frozenset({S.ANALYZED}),
    S.ANALYZED: frozenset({S.AWAITING_PATIENT_INPUT, S.FINDINGS_CONFIRMED, S.RESOLVED}),
    S.AWAITING_PATIENT_INPUT: frozenset({S.FINDINGS_CONFIRMED, S.RESOLVED}),
    S.FINDINGS_CONFIRMED: frozenset({S.DISPUTE_PREPARED}),
    S.DISPUTE_PREPARED: frozenset({S.AWAITING_APPROVAL}),
    S.AWAITING_APPROVAL: frozenset({S.APPROVED, S.REJECTED}),
    S.APPROVED: frozenset({S.CALL_IN_PROGRESS, S.RESOLVED}),
    S.CALL_IN_PROGRESS: frozenset({S.HUMAN_REQUIRED, S.RESOLVED, S.APPROVED}),
    S.HUMAN_REQUIRED: frozenset({S.CALL_IN_PROGRESS, S.RESOLVED}),
    S.RESOLVED: frozenset(),
    S.REJECTED: frozenset(),
    S.FAILED: frozenset(),
}


@dataclass(frozen=True)
class GuardContext:
    """Facts about a case that guards need. Built from the DB by case_service."""

    has_visit: bool = False
    has_transcript: bool = False
    has_claim: bool = False
    findings_count: int = 0
    open_confirmation_count: int = 0
    disputable_findings_count: int = 0
    dispute_status: DisputeStatus | None = None
    patient_authorization_id: str | None = None
    open_call_interventions: int = 0
    has_insurer_response: bool = False


class TransitionError(Conflict):
    """Base class for rejected state changes (HTTP 409)."""

    def __init__(self, message: str, *, code: str, current: S, target: S) -> None:
        super().__init__(message, code=code, from_state=current.value, to_state=target.value)
        self.current = current
        self.target = target


class IllegalTransition(TransitionError):
    def __init__(self, current: S, target: S) -> None:
        super().__init__(
            f"Illegal case transition {current} -> {target}",
            code="illegal_transition",
            current=current,
            target=target,
        )


class GuardFailed(TransitionError):
    def __init__(self, current: S, target: S, reason: str) -> None:
        super().__init__(
            f"Transition {current} -> {target} blocked: {reason}",
            code="guard_failed",
            current=current,
            target=target,
        )
        self.reason = reason


Guard = Callable[[GuardContext], str | None]
"""A guard returns None when the transition is allowed, else the blocking reason."""


def _requires(condition: Callable[[GuardContext], bool], reason: str) -> Guard:
    return lambda ctx: None if condition(ctx) else reason


def _call_authorized(ctx: GuardContext) -> str | None:
    if ctx.dispute_status != DisputeStatus.APPROVED:
        return "an APPROVED dispute is required before any insurer call"
    if not ctx.patient_authorization_id:
        return "a stored patient_authorization_id is required before any insurer call"
    return None


def _call_resumable(ctx: GuardContext) -> str | None:
    if ctx.open_call_interventions:
        return "open human interventions must be resolved before the call resumes"
    return _call_authorized(ctx)


_has_visit = _requires(lambda c: c.has_visit, "a visit must exist")
_has_transcript = _requires(lambda c: c.has_transcript, "a transcript must exist")
_has_claim = _requires(lambda c: c.has_claim, "a claim must exist")
_ready_to_dispute = _requires(
    lambda c: c.open_confirmation_count == 0 and c.disputable_findings_count > 0,
    "all confirmations must be answered and at least one finding must remain disputable",
)
_nothing_to_dispute = _requires(
    lambda c: c.open_confirmation_count == 0 and c.disputable_findings_count == 0,
    "findings remain open or disputable",
)
_has_insurer_response = _requires(
    lambda c: c.has_insurer_response, "an insurer response must be recorded"
)

GUARDS: dict[tuple[S, S], Guard] = {
    (S.CREATED, S.VISIT_RECORDED): _has_visit,
    (S.CREATED, S.CLAIM_RECEIVED): _has_claim,
    (S.VISIT_RECORDED, S.TRANSCRIBED): _has_transcript,
    (S.TRANSCRIBED, S.EVIDENCE_EXTRACTED): _has_transcript,
    (S.EVIDENCE_EXTRACTED, S.CLAIM_RECEIVED): _has_claim,
    (S.CLAIM_RECEIVED, S.ANALYZED): _has_claim,
    (S.ANALYZED, S.AWAITING_PATIENT_INPUT): _requires(
        lambda c: c.open_confirmation_count > 0, "no open patient confirmations"
    ),
    (S.ANALYZED, S.FINDINGS_CONFIRMED): _ready_to_dispute,
    (S.ANALYZED, S.RESOLVED): _requires(lambda c: c.findings_count == 0, "findings exist"),
    (S.AWAITING_PATIENT_INPUT, S.FINDINGS_CONFIRMED): _ready_to_dispute,
    (S.AWAITING_PATIENT_INPUT, S.RESOLVED): _nothing_to_dispute,
    (S.FINDINGS_CONFIRMED, S.DISPUTE_PREPARED): _requires(
        lambda c: c.dispute_status is not None, "a dispute must be recorded"
    ),
    (S.DISPUTE_PREPARED, S.AWAITING_APPROVAL): _requires(
        lambda c: c.dispute_status == DisputeStatus.AWAITING_APPROVAL,
        "dispute must be awaiting approval",
    ),
    (S.AWAITING_APPROVAL, S.APPROVED): _requires(
        lambda c: c.dispute_status == DisputeStatus.APPROVED,
        "dispute must be approved by the patient",
    ),
    (S.AWAITING_APPROVAL, S.REJECTED): _requires(
        lambda c: c.dispute_status == DisputeStatus.REJECTED, "dispute must be rejected"
    ),
    (S.APPROVED, S.CALL_IN_PROGRESS): _call_authorized,
    (S.APPROVED, S.RESOLVED): _requires(
        lambda c: c.dispute_status == DisputeStatus.APPROVED and not c.patient_authorization_id,
        "only a dispute approved without call authorization resolves without a call",
    ),
    (S.CALL_IN_PROGRESS, S.HUMAN_REQUIRED): _requires(
        lambda c: c.open_call_interventions > 0, "no open call intervention"
    ),
    (S.HUMAN_REQUIRED, S.CALL_IN_PROGRESS): _call_resumable,
    (S.CALL_IN_PROGRESS, S.RESOLVED): _has_insurer_response,
    (S.CALL_IN_PROGRESS, S.APPROVED): _call_authorized,
    (S.HUMAN_REQUIRED, S.RESOLVED): _has_insurer_response,
}


def allowed_targets(current: S) -> frozenset[S]:
    """States reachable from ``current`` (FAILED is reachable from any non-terminal)."""
    if current in TERMINAL_STATES:
        return frozenset()
    return TRANSITIONS[current] | {S.FAILED}


def check_transition(current: S, target: S, ctx: GuardContext) -> None:
    """Raise IllegalTransition or GuardFailed unless ``current -> target`` is allowed."""
    if target not in allowed_targets(current):
        raise IllegalTransition(current, target)
    guard = GUARDS.get((current, target))
    reason = guard(ctx) if guard else None
    if reason:
        raise GuardFailed(current, target, reason)
