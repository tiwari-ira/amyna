"""Service-token authentication and per-caller scoping over HTTP."""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.auth import SCOPES, tool_scope
from claimshield_contracts.enums import ServiceName
from claimshield_contracts.tool_api import ToolName


def test_missing_and_wrong_tokens_are_401(client: TestClient) -> None:
    assert client.get("/cases/case_x").status_code == 401
    response = client.get("/cases/case_x", headers={"Authorization": "Bearer nope"})
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "unauthorized"


def test_health_needs_no_token(client: TestClient) -> None:
    assert client.get("/health").status_code == 200


def test_relay_cannot_call_internal_tools(client: TestClient, relay: dict[str, str]) -> None:
    response = client.post("/internal/tools/get_case", json={"case_id": "x"}, headers=relay)
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "insufficient_scope"


def test_voice_cannot_approve_or_start_calls(client: TestClient, voice: dict[str, str]) -> None:
    approve = client.post(
        "/disputes/dsp_x/approve",
        json={"channel": "voice", "authorization_text": "I approve everything here."},
        headers=voice,
    )
    assert approve.status_code == 403
    assert client.post("/voice/start", json={"case_id": "x"}, headers=voice).status_code == 403


def test_fetch_cannot_record_insurer_responses(client: TestClient, fetch: dict[str, str]) -> None:
    response = client.post(
        "/internal/tools/record_insurer_response",
        json={"case_id": "x", "call_id": "y", "outcome": "denied"},
        headers=fetch,
    )
    assert response.status_code == 403


def test_every_tool_route_is_scoped(client: TestClient, app: Any) -> None:
    paths = set(app.openapi()["paths"])
    for tool in ToolName:
        assert f"/internal/tools/{tool.value}" in paths
        assert any(tool_scope(tool) in SCOPES[name] for name in ServiceName)


def test_fetch_token_can_use_agent_tools(
    client: TestClient, fetch: dict[str, str], analyzed_case: dict[str, Any]
) -> None:
    response = client.post(
        "/internal/tools/get_case", json={"case_id": analyzed_case["case_id"]}, headers=fetch
    )
    assert response.status_code == 200
    assert response.json()["state"] == "AWAITING_PATIENT_INPUT"


@pytest.mark.parametrize(
    ("agent", "tool", "allowed"),
    [
        ("claim_agent", "get_patient_procedures", True),
        ("visit_agent", "get_patient_procedures", False),
        ("claim_agent", "start_insurance_call", False),
    ],
)
def test_per_agent_allowlist(
    client: TestClient,
    fetch: dict[str, str],
    analyzed_case: dict[str, Any],
    agent: str,
    tool: str,
    allowed: bool,
) -> None:
    body = {
        "case_id": analyzed_case["case_id"],
        "agent_run": {"agent": agent, "request_id": "req_1"},
    }
    response = client.post(f"/internal/tools/{tool}", json=body, headers=fetch)
    assert (response.status_code != 403) is allowed, response.text


def test_responses_carry_contract_version(client: TestClient) -> None:
    assert client.get("/health").headers["X-Amyna-Contracts-Version"]
