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


# ------------------------------------------------------------------ [2.4] per-agent tokens


def _agent_app(tmp_path) -> tuple[TestClient, dict[str, dict[str, str]], str]:
    """An app whose Claim and Visit agents hold their own distinct tokens."""
    from pathlib import Path

    from app.demo_support import build_isolated_app, create_demo_visit, service_headers
    from claimshield_contracts.enums import ServiceName

    app = build_isolated_app(
        Path(tmp_path) / "agents.db",
        claim_agent_service_token="tok-claim",
        visit_agent_service_token="tok-visit",
    )
    client = TestClient(app)
    relay = service_headers(app.state.container.settings, ServiceName.RELAY)
    setup = create_demo_visit(client, relay)
    claim = client.post(
        "/claims",
        json={
            "case_id": setup["case_id"],
            "source": "finchnode",
            "finchnode_claim_id": "CLM-2026-0915-001",
        },
        headers=relay,
    ).json()
    client.post(f"/claims/{claim['claim_id']}/analyze", headers=relay)
    tokens = {
        "claim": {"Authorization": "Bearer tok-claim"},
        "visit": {"Authorization": "Bearer tok-visit"},
    }
    return client, tokens, setup["case_id"]


def test_a_per_agent_token_cannot_use_another_agents_tools(tmp_path) -> None:
    client, tokens, case_id = _agent_app(tmp_path)
    body = {
        "case_id": case_id,
        "agent_run": {"agent": "claim_agent", "request_id": "req_1"},
    }
    ok = client.post("/internal/tools/get_case", json=body, headers=tokens["claim"])
    assert ok.status_code == 200
    # The Claim Agent's token lacks the Visit Agent's tool scope outright.
    denied = client.post("/internal/tools/get_visit_transcript", json=body, headers=tokens["claim"])
    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "insufficient_scope"


def test_a_per_agent_token_cannot_impersonate_another_agent(tmp_path) -> None:
    client, tokens, case_id = _agent_app(tmp_path)
    body = {
        "case_id": case_id,
        "agent_run": {"agent": "visit_agent", "request_id": "req_1"},
    }
    mismatch = client.post("/internal/tools/get_case", json=body, headers=tokens["claim"])
    assert mismatch.status_code == 403
    assert "may not act as visit_agent" in mismatch.json()["detail"]["message"]
    # The Visit Agent's own token with the same claim is fine.
    fine = client.post("/internal/tools/get_case", json=body, headers=tokens["visit"])
    assert fine.status_code == 200


def test_responses_carry_contract_version(client: TestClient) -> None:
    assert client.get("/health").headers["X-Amyna-Contracts-Version"]
