"""Patient identity (tasks 5/19): users own cases; IDs are never credentials."""

import pytest
from fastapi.testclient import TestClient

from app.errors import Forbidden
from app.services.identity_service import (
    PATIENT_TOKEN_HEADER,
    create_demo_session,
)


def _session(client: TestClient, subject: str) -> dict[str, str]:
    result = client.post("/auth/session", json={"subject": subject}).json()
    return {"patient_token": result["patient_token"], "user_id": result["user_id"]}


def test_auth_session_issues_an_opaque_token(client: TestClient) -> None:
    session = _session(client, "relay:patient-one")
    assert session["patient_token"].startswith("pt_")
    assert session["user_id"].startswith("user_")


def test_a_case_belongs_to_the_authenticated_user(
    client: TestClient, relay: dict[str, str]
) -> None:
    mary = _session(client, "relay:mary")
    headers_mary = {**relay, PATIENT_TOKEN_HEADER: mary["patient_token"]}
    case = client.post(
        "/cases",
        json={"patient": {"finchnode_patient_id": "fn_pat_demo_001"}, "channel": "relay"},
        headers=headers_mary,
    ).json()
    detail = client.get(f"/cases/{case['case_id']}", headers=headers_mary)
    assert detail.status_code == 200
    bob = _session(client, "relay:bob")
    headers_bob = {**relay, PATIENT_TOKEN_HEADER: bob["patient_token"]}
    assert client.get(f"/cases/{case['case_id']}", headers=headers_bob).status_code == 403
    # A service token alone does not prove the patient: owned cases need the session.
    assert client.get(f"/cases/{case['case_id']}", headers=relay).status_code == 403


def test_anonymous_cases_stay_service_reachable(client: TestClient, relay: dict[str, str]) -> None:
    """Legacy/demo flows without a patient token keep working (user NULL)."""
    case = client.post(
        "/cases",
        json={"patient": {"finchnode_patient_id": "fn_pat_demo_001"}, "channel": "relay"},
        headers=relay,
    ).json()
    assert client.get(f"/cases/{case['case_id']}", headers=relay).status_code == 200


def test_demo_sessions_are_disabled_in_production(
    client: TestClient,
) -> None:
    """create_demo_session refuses outside dev/test/staging (production uses the IDP)."""
    with (
        client.app.state.container.session_scope() as db,  # type: ignore[attr-defined]
        pytest.raises(Forbidden),
    ):
        create_demo_session(db, app_env="prod", subject="relay:x")


def test_patient_context_tool_returns_masked_identifiers(
    client: TestClient, relay: dict[str, str], fetch: dict[str, str]
) -> None:
    """task 6: agents/LLMs ask Core for the authorized context; no raw identifiers."""
    case = client.post(
        "/cases",
        json={
            "patient": {
                "finchnode_patient_id": "fn_pat_demo_001",
                "member_ref": "SYN-MBR-000123",
                "display_name": "Mary Doe",
            },
            "channel": "relay",
        },
        headers=relay,
    ).json()
    context = client.post(
        "/internal/tools/get_patient_context", json={"case_id": case["case_id"]}, headers=fetch
    ).json()
    assert context["case_id"] == case["case_id"]
    assert context["member_ref_masked"] == "•••123"
    assert "SYN-MBR-000123" not in str(context)
    assert context["has_health_data_consent"] is False
    # Relay's identity lacks the tool scope; patient surfaces never call tools.
    assert (
        client.post(
            "/internal/tools/get_patient_context", json={"case_id": case["case_id"]}, headers=relay
        ).status_code
        == 403
    )
