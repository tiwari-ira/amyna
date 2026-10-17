"""Regression tests for the per-category sensitive-request cooldown.

The live-call fix (HANDLED_COOLDOWN_S, 120s) suppressed *any* repeat sensitive
request; a card-number (identity) request arriving right after the member had
handled an SSN (credential) request was silently answered by the agent. The
window is now per human-merge reason: the same kind stays suppressed, a new
kind still pauses the call.
"""

from fastapi import FastAPI

from app.db import utcnow
from app.models_db import InterventionRow
from app.services.human_service import create_intervention
from app.services.voice_service import VoiceCallManager
from claimshield_contracts.enums import HumanReason, InterventionStatus


def _handled(db: object, case_id: str, call_id: str, reason: HumanReason) -> InterventionRow:
    row = create_intervention(
        db,
        case_id=case_id,
        reason=reason,
        prompt="Please provide your member SSN.",
        options=["DONE", "CANCEL"],
        actor="voice",
        call_id=call_id,
    )
    row.status = InterventionStatus.RESOLVED.value
    row.resolved_at = utcnow()
    db.flush()
    return row


def test_same_kind_is_suppressed(app: FastAPI, analyzed_case: dict) -> None:
    manager = VoiceCallManager(app.state.container)
    with app.state.container.session_scope() as db:
        _handled(db, analyzed_case["case_id"], "call_a", HumanReason.CREDENTIAL_REQUEST)
        assert manager._recently_handled(db, "call_a", HumanReason.CREDENTIAL_REQUEST.value)


def test_different_kind_still_pauses(app: FastAPI, analyzed_case: dict) -> None:
    """The security regression this file exists for: card-after-SSN must pause."""
    manager = VoiceCallManager(app.state.container)
    with app.state.container.session_scope() as db:
        _handled(db, analyzed_case["case_id"], "call_a", HumanReason.CREDENTIAL_REQUEST)
        assert not manager._recently_handled(db, "call_a", HumanReason.IDENTITY_VERIFICATION.value)


def test_other_call_is_never_suppressed(app: FastAPI, analyzed_case: dict) -> None:
    manager = VoiceCallManager(app.state.container)
    with app.state.container.session_scope() as db:
        _handled(db, analyzed_case["case_id"], "call_a", HumanReason.CREDENTIAL_REQUEST)
        assert not manager._recently_handled(db, "call_b", HumanReason.CREDENTIAL_REQUEST.value)


def test_unattributed_reason_keeps_any_recent_behaviour(app: FastAPI, analyzed_case: dict) -> None:
    manager = VoiceCallManager(app.state.container)
    with app.state.container.session_scope() as db:
        _handled(db, analyzed_case["case_id"], "call_a", HumanReason.CREDENTIAL_REQUEST)
        assert manager._recently_handled(db, "call_a", None)
