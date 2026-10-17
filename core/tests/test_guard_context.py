"""[1.1] The guard context is one statement and its facts did not change.

build_guard_context used to run nine COUNT/EXISTS queries on every transition;
requests that transition twice paid it twice. The single-statement rewrite
must (a) issue exactly one SQL statement and (b) report identical facts.
"""

from typing import Any

from fastapi import FastAPI
from sqlalchemy import event

from app.services.case_service import build_guard_context, get_case_row
from claimshield_contracts.enums import DisputeStatus


def _statements_during(app: FastAPI, case_id: str) -> tuple[int, Any]:
    engine = app.state.container.engine
    counter = {"n": 0}
    context = {}

    def _count(conn, _cursor, _statement, _params, _context, _executemany):  # type: ignore[no-untyped-def]
        counter["n"] += 1

    # The case row fetch must not count: only build_guard_context is measured.
    with app.state.container.session_scope() as db:
        case = get_case_row(db, case_id)
    event.listen(engine, "before_cursor_execute", _count)
    try:
        with app.state.container.session_scope() as db:
            context = build_guard_context(db, case)
    finally:
        event.remove(engine, "before_cursor_execute", _count)
    return counter["n"], context


def test_guard_context_is_one_statement(app: FastAPI, analyzed_case: dict) -> None:
    statements, context = _statements_during(app, analyzed_case["case_id"])
    assert statements == 1, f"guard context should be 1 statement, ran {statements}"


def test_guard_context_facts_are_unchanged(app: FastAPI, analyzed_case: dict) -> None:
    with app.state.container.session_scope() as db:
        context = build_guard_context(db, get_case_row(db, analyzed_case["case_id"]))
    assert context.has_visit and context.has_transcript and context.has_claim
    assert context.findings_count == 3
    # Only the duplicate line item is disputable before the patient answers;
    # the two uncertain findings join DISPUTABLE once resolved (test below).
    assert context.disputable_findings_count == 1
    # The duplicate line item is auto-evidence; the other two need the patient.
    assert context.open_confirmation_count == 2
    assert context.dispute_status is None  # not prepared yet in this fixture
    assert context.patient_authorization_id is None
    assert context.open_call_interventions == 0
    assert not context.has_insurer_response


def test_guard_context_sees_the_dispute_once_prepared(
    app: FastAPI, awaiting_approval: dict
) -> None:
    with app.state.container.session_scope() as db:
        context = build_guard_context(db, get_case_row(db, awaiting_approval["case_id"]))
    assert context.dispute_status == DisputeStatus.AWAITING_APPROVAL
    # All three findings became disputable once the patient answered.
    assert context.disputable_findings_count == 3
    assert context.open_confirmation_count == 0
