"""HTTPS CoreClient: agent_run tagging and error mapping."""

import json

import httpx
import pytest

from claimshield_contracts.enums import AgentName
from claimshield_contracts.tool_api import CoreToolError, GetCaseRequest
from fetch.agents.core_client import CoreClient


def _client(handler) -> CoreClient:  # type: ignore[no-untyped-def]
    http = httpx.Client(base_url="http://core.test", transport=httpx.MockTransport(handler))
    return CoreClient("", "tok", agent=AgentName.CLAIM_AGENT, http=http)


def test_requests_are_tagged_with_agent_and_request_id() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["Authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(404, json={"detail": {"code": "not_found", "message": "nope"}})

    client = _client(handler).for_request("req_42")
    with pytest.raises(CoreToolError) as excinfo:
        client.get_case(GetCaseRequest(case_id="case_1"))
    assert seen["auth"] == "Bearer tok"
    assert seen["body"]["agent_run"] == {"agent": "claim_agent", "request_id": "req_42"}
    assert excinfo.value.status_code == 404 and excinfo.value.code == "not_found"


def test_non_json_errors_are_wrapped() -> None:
    client = _client(lambda request: httpx.Response(502, text="bad gateway"))
    with pytest.raises(CoreToolError) as excinfo:
        client.get_case(GetCaseRequest(case_id="case_1"))
    assert excinfo.value.status_code == 502


def test_connection_errors_become_core_tool_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(CoreToolError) as excinfo:
        _client(handler).get_case(GetCaseRequest(case_id="case_1"))
    assert excinfo.value.status_code == 503 and excinfo.value.code == "core_unreachable"
