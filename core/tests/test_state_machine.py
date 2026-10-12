"""State-machine transitions and guards (pure, no DB)."""

import pytest

from app.state_machine import (
    TERMINAL_STATES,
    TRANSITIONS,
    GuardContext,
    GuardFailed,
    IllegalTransition,
    allowed_targets,
    check_transition,
)
from claimshield_contracts.enums import CaseState as S
from claimshield_contracts.enums import DisputeStatus

APPROVED_CTX = GuardContext(
    dispute_status=DisputeStatus.APPROVED, patient_authorization_id="hdc_approval"
)


def test_every_state_has_a_transition_entry() -> None:
    assert set(TRANSITIONS) == set(S)


def test_terminal_states_have_no_exits() -> None:
    for state in TERMINAL_STATES:
        assert allowed_targets(state) == frozenset()
        with pytest.raises(IllegalTransition):
            check_transition(state, S.FAILED, GuardContext())


def test_failed_is_reachable_from_every_non_terminal_state() -> None:
    for state in set(S) - TERMINAL_STATES:
        check_transition(state, S.FAILED, GuardContext())


def test_illegal_transition_raises_with_codes() -> None:
    with pytest.raises(IllegalTransition) as excinfo:
        check_transition(S.CREATED, S.APPROVED, GuardContext())
    detail = excinfo.value.to_detail()
    assert detail["code"] == "illegal_transition"
    assert detail["from_state"] == "CREATED" and detail["to_state"] == "APPROVED"
    assert excinfo.value.status_code == 409


@pytest.mark.parametrize(
    ("ctx", "reason_fragment"),
    [
        (GuardContext(), "APPROVED dispute"),
        (GuardContext(dispute_status=DisputeStatus.AWAITING_APPROVAL), "APPROVED dispute"),
        (GuardContext(dispute_status=DisputeStatus.APPROVED), "patient_authorization_id"),
    ],
)
def test_call_blocked_without_approval_and_authorization(
    ctx: GuardContext, reason_fragment: str
) -> None:
    with pytest.raises(GuardFailed) as excinfo:
        check_transition(S.APPROVED, S.CALL_IN_PROGRESS, ctx)
    assert reason_fragment in excinfo.value.reason
    assert excinfo.value.to_detail()["code"] == "guard_failed"


def test_call_allowed_with_approved_dispute_and_authorization() -> None:
    check_transition(S.APPROVED, S.CALL_IN_PROGRESS, APPROVED_CTX)


def test_call_cannot_resume_while_human_needed() -> None:
    blocked = GuardContext(
        dispute_status=DisputeStatus.APPROVED,
        patient_authorization_id="hdc_approval",
        open_call_interventions=1,
    )
    with pytest.raises(GuardFailed):
        check_transition(S.HUMAN_REQUIRED, S.CALL_IN_PROGRESS, blocked)
    check_transition(S.HUMAN_REQUIRED, S.CALL_IN_PROGRESS, APPROVED_CTX)


def test_resume_rechecks_authorization() -> None:
    with pytest.raises(GuardFailed):
        check_transition(S.HUMAN_REQUIRED, S.CALL_IN_PROGRESS, GuardContext())


def test_human_required_needs_open_intervention() -> None:
    with pytest.raises(GuardFailed):
        check_transition(S.CALL_IN_PROGRESS, S.HUMAN_REQUIRED, GuardContext())
    check_transition(S.CALL_IN_PROGRESS, S.HUMAN_REQUIRED, GuardContext(open_call_interventions=1))


def test_findings_confirmed_waits_for_all_answers() -> None:
    waiting = GuardContext(open_confirmation_count=1, disputable_findings_count=2)
    with pytest.raises(GuardFailed):
        check_transition(S.AWAITING_PATIENT_INPUT, S.FINDINGS_CONFIRMED, waiting)
    done = GuardContext(open_confirmation_count=0, disputable_findings_count=2)
    check_transition(S.AWAITING_PATIENT_INPUT, S.FINDINGS_CONFIRMED, done)


def test_resolved_without_findings_only() -> None:
    with pytest.raises(GuardFailed):
        check_transition(S.ANALYZED, S.RESOLVED, GuardContext(findings_count=3))
    check_transition(S.ANALYZED, S.RESOLVED, GuardContext(findings_count=0))


def test_an_unfinished_call_can_return_to_approved() -> None:
    check_transition(S.CALL_IN_PROGRESS, S.APPROVED, APPROVED_CTX)
    with pytest.raises(GuardFailed):
        check_transition(S.CALL_IN_PROGRESS, S.APPROVED, GuardContext())


def test_resolution_requires_insurer_response() -> None:
    with pytest.raises(GuardFailed):
        check_transition(S.CALL_IN_PROGRESS, S.RESOLVED, GuardContext())
    check_transition(S.CALL_IN_PROGRESS, S.RESOLVED, GuardContext(has_insurer_response=True))


def test_happy_path_is_connected() -> None:
    path = [
        S.CREATED,
        S.VISIT_RECORDED,
        S.TRANSCRIBED,
        S.EVIDENCE_EXTRACTED,
        S.CLAIM_RECEIVED,
        S.ANALYZED,
        S.AWAITING_PATIENT_INPUT,
        S.FINDINGS_CONFIRMED,
        S.DISPUTE_PREPARED,
        S.AWAITING_APPROVAL,
        S.APPROVED,
        S.CALL_IN_PROGRESS,
        S.HUMAN_REQUIRED,
        S.CALL_IN_PROGRESS,
        S.RESOLVED,
    ]
    for current, target in zip(path, path[1:], strict=False):
        assert target in allowed_targets(current), f"{current} -> {target}"
