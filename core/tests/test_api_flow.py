"""Full mock flow through the public API plus the key guards."""

from typing import Any

from fastapi.testclient import TestClient

from app.config import Settings
from app.demo_support import APPROVAL_TEXT, answer_open_questions, run_demo_flow

APPROVAL = {"channel": "relay", "authorization_text": APPROVAL_TEXT}


def test_full_mock_flow_resolves(client: TestClient, settings: Settings) -> None:
    summary = run_demo_flow(client, settings)
    assert summary["state"] == "RESOLVED"
    assert summary["findings"] == 3 and summary["flagged_cents"] == 107000
    assert summary["reference_number"] == "MOCK-REF-48213"


def test_call_blocked_while_awaiting_approval(
    client: TestClient, relay: dict[str, str], awaiting_approval: dict[str, Any]
) -> None:
    response = client.post(
        "/voice/start", json={"case_id": awaiting_approval["case_id"]}, headers=relay
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "illegal_transition"


def test_letter_only_approval_resolves_and_never_calls(
    client: TestClient, relay: dict[str, str], awaiting_approval: dict[str, Any]
) -> None:
    case_id = awaiting_approval["case_id"]
    dispute_id = awaiting_approval["dispute"]["dispute_id"]
    approved = client.post(
        f"/disputes/{dispute_id}/approve", json={**APPROVAL, "authorize_call": False}, headers=relay
    ).json()
    assert approved["status"] == "approved" and approved["patient_authorization_id"] is None
    case = client.get(f"/cases/{case_id}", headers=relay).json()
    assert case["state"] == "RESOLVED" and case["calls"] == []
    response = client.post("/voice/start", json={"case_id": case_id}, headers=relay)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "illegal_transition"


def test_only_the_patient_can_approve(
    client: TestClient, relay: dict[str, str], awaiting_approval: dict[str, Any]
) -> None:
    dispute_id = awaiting_approval["dispute"]["dispute_id"]
    response = client.post(
        f"/disputes/{dispute_id}/approve", json={**APPROVAL, "actor": "staff"}, headers=relay
    )
    assert response.status_code == 403


def test_reject_ends_the_case(
    client: TestClient, relay: dict[str, str], awaiting_approval: dict[str, Any]
) -> None:
    dispute_id = awaiting_approval["dispute"]["dispute_id"]
    rejected = client.post(
        f"/disputes/{dispute_id}/reject",
        json={"channel": "relay", "reason": "changed my mind"},
        headers=relay,
    )
    assert rejected.json()["status"] == "rejected"
    case = client.get(f"/cases/{awaiting_approval['case_id']}", headers=relay).json()
    assert case["state"] == "REJECTED"


def test_yes_answers_dismiss_findings(
    client: TestClient, relay: dict[str, str], analyzed_case: dict[str, Any]
) -> None:
    answer_open_questions(client, relay, analyzed_case["case_id"], choice="YES")
    case = client.get(f"/cases/{analyzed_case['case_id']}", headers=relay).json()
    statuses = {f["line_number"]: f["status"] for f in case["findings"]}
    assert statuses == {3: "dismissed", 5: "confirmed", 6: "dismissed"}
    assert case["state"] == "FINDINGS_CONFIRMED"  # the duplicate is still disputable


def test_analysis_is_idempotent(
    client: TestClient, relay: dict[str, str], analyzed_case: dict[str, Any]
) -> None:
    again = client.post(
        f"/claims/{analyzed_case['claim']['claim_id']}/analyze", headers=relay
    ).json()
    assert [f["finding_id"] for f in again["findings"]] == [
        f["finding_id"] for f in analyzed_case["analysis"]["findings"]
    ]


def test_questions_are_sent_one_at_a_time(
    client: TestClient, relay: dict[str, str], analyzed_case: dict[str, Any]
) -> None:
    notes = client.get(f"/cases/{analyzed_case['case_id']}/notifications", headers=relay).json()
    questions = [n for n in notes if n["kind"] == "confirmation_question"]
    assert len(questions) == 1 and "anesthesia" in questions[0]["text"]
    assert [b["id"] for b in questions[0]["buttons"]] == ["YES", "NO", "NOT_SURE"]


def test_timeline_and_health(
    client: TestClient, relay: dict[str, str], analyzed_case: dict[str, Any]
) -> None:
    events = client.get(f"/cases/{analyzed_case['case_id']}/timeline", headers=relay).json()
    actions = {e["action"] for e in events}
    assert {"case.created", "case.transition", "claim.findings_recorded"} <= actions
    health = client.get("/health").json()
    assert health["status"] == "ok" and health["modes"]["FETCH_MODE"] == "local_direct"
