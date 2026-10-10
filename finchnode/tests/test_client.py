"""FinchNode clients: fixtures, keys per environment, the records snapshot, retries,
typed errors and Connect sessions."""

import json
from pathlib import Path

import httpx
import pytest

from finchnode.client import (
    API_BASE_URL,
    DEMO_BASE_URL,
    FinchNodeAuthError,
    FinchNodeConsentInactive,
    FinchNodeError,
    FinchNodeRequestError,
    FixtureClient,
    HttpFinchNodeClient,
    build_client,
)

RECORDS = json.loads(
    (Path(__file__).parent / "fixtures" / "records_patient-demo-001.json").read_text()
)


def _http(handler) -> httpx.Client:  # type: ignore[no-untyped-def]
    return httpx.Client(base_url="https://finchnode.test", transport=httpx.MockTransport(handler))


def _client(handler, **kwargs) -> HttpFinchNodeClient:  # type: ignore[no-untyped-def]
    options = {"mode": "sandbox", "base_url": "https://finchnode.test", "api_key": "ck_test_abc"}
    return HttpFinchNodeClient(
        **{**options, **kwargs}, http=_http(handler), backoff_s=0, sleep=lambda _s: None
    )


def test_fixture_client() -> None:
    client = FixtureClient()
    assert client.get_patient("fn_pat_demo_001")["id"] == "fn_pat_demo_001"
    assert len(client.search("Claim", "fn_pat_demo_001")) == 2
    assert client.get_patient("nobody") is None and client.search("Claim", "nobody") == []


def test_keys_must_match_the_environment() -> None:
    with pytest.raises(ValueError, match="FINCHNODE_API_KEY"):
        HttpFinchNodeClient(mode="sandbox", base_url="https://x")
    with pytest.raises(ValueError, match="ck_test_"):
        HttpFinchNodeClient(mode="sandbox", base_url="https://x", api_key="ck_live_123")
    with pytest.raises(ValueError, match="sandbox key"):
        HttpFinchNodeClient(mode="production", base_url="", api_key="ck_test_123")
    with pytest.raises(ValueError, match="no API key"):
        HttpFinchNodeClient(mode="demo", base_url="", api_key="ck_test_abc")


def test_default_base_urls() -> None:
    demo = HttpFinchNodeClient(mode="demo", base_url="")
    sandbox = HttpFinchNodeClient(mode="sandbox", base_url="", api_key="ck_test_x")
    assert str(demo._http.base_url).rstrip("/") == DEMO_BASE_URL
    assert str(sandbox._http.base_url).rstrip("/") == API_BASE_URL
    assert "Authorization" not in demo._http.headers
    demo._http.close()
    sandbox._http.close()


def test_snapshot_sends_the_key_and_is_cached_until_invalidated() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=RECORDS)

    clock = {"now": 0.0}
    client = _client(handler, clock=lambda: clock["now"], cache_ttl_s=30)
    first = client.snapshot("u_1")
    assert first is not None and first.subject == "u_1" and first.section("labs")
    assert seen[0].url.path == "/users/u_1/records"
    assert seen[0].headers["Authorization"] == "Bearer ck_test_abc"
    clock["now"] = 10
    assert client.snapshot("u_1") is first and len(seen) == 1  # cached
    client.invalidate("u_1")
    client.snapshot("u_1")
    clock["now"] = 100
    client.snapshot("u_1")  # TTL expired
    assert len(seen) == 3


def test_unknown_subject_is_none() -> None:
    client = _client(lambda request: httpx.Response(404, json={"error": {"code": "not_found"}}))
    assert client.snapshot("u_missing") is None


def test_rate_limit_waits_for_retry_after() -> None:
    calls = {"n": 0}
    waits: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "2"}, json={})
        return httpx.Response(200, json=RECORDS)

    client = HttpFinchNodeClient(
        mode="demo", base_url="https://finchnode.test", http=_http(handler), sleep=waits.append
    )
    assert client.snapshot("patient-demo-rate-limited") is not None
    assert waits == [2.0] and calls["n"] == 2


def test_long_retry_after_fails_instead_of_stalling() -> None:
    client = _client(lambda request: httpx.Response(429, headers={"Retry-After": "60"}, json={}))
    with pytest.raises(FinchNodeError) as raised:
        client.snapshot("u_1")
    assert raised.value.code == "finchnode_unavailable"


@pytest.mark.parametrize(
    ("status", "code", "error"),
    [
        (410, "consent_inactive", FinchNodeConsentInactive),
        (401, "unauthorized", FinchNodeAuthError),
        (403, "consent_scope_exceeded", FinchNodeAuthError),
        (400, "invalid_request", FinchNodeRequestError),
    ],
)
def test_errors_are_typed_and_never_name_the_subject(status: int, code: str, error: type) -> None:
    client = _client(lambda request: httpx.Response(status, json={"error": {"code": code}}))
    with pytest.raises(error) as raised:
        client.snapshot("u_secret")
    assert code in str(raised.value) and "u_secret" not in str(raised.value)


def test_server_errors_are_retried_then_raised() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503)

    with pytest.raises(FinchNodeError):
        _client(handler).snapshot("u_1")
    assert calls["n"] == 3


def test_connect_session_create_and_read() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "POST":
            return httpx.Response(
                201,
                json={
                    "id": "cs_1",
                    "status": "pending",
                    "subject": None,
                    "url": "https://finchnode.com/connect/cs_1",
                    "environment": "sandbox",
                },
            )
        return httpx.Response(
            200,
            json={"id": "cs_1", "status": "completed", "subject": "u_0123456789abcdef"},
        )

    client = _client(handler)
    created = client.create_connect_session(
        external_id="case_1", categories=["labs"], sync_mode="continuous", idempotency_key="k1"
    )
    assert created["url"].endswith("/connect/cs_1") and created["status"] == "pending"
    assert json.loads(seen[0].content) == {
        "externalId": "case_1",
        "categories": ["labs"],
        "syncMode": "continuous",
    }
    assert seen[0].headers["Idempotency-Key"] == "k1"
    assert client.get_connect_session("cs_1")["subject"] == "u_0123456789abcdef"


def test_demo_connect_field_names_are_normalized() -> None:
    client = HttpFinchNodeClient(
        mode="demo",
        base_url="https://finchnode.test",
        http=_http(
            lambda r: httpx.Response(
                201,
                json={
                    "id": "cs_d",
                    "status": "complete",
                    "patient_id": "patient-demo-001",
                    "connect_url": "https://finchnode.com/connect/cs_d",
                },
            )
        ),
    )
    session = client.create_connect_session(external_id="x")
    assert session["status"] == "completed" and session["subject"] == "patient-demo-001"
    assert session["url"] == "https://finchnode.com/connect/cs_d"


def test_sandbox_event_invalidates_the_cache() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}")
        if request.url.path.endswith("/events"):
            return httpx.Response(202, json={"status": "queued"})
        return httpx.Response(200, json=RECORDS)

    client = _client(handler)
    client.snapshot("u_1")
    client.sandbox_event("u_1", "records.advance")
    client.snapshot("u_1")
    assert seen == [
        "GET /users/u_1/records",
        "POST /sandbox/subjects/u_1/events",
        "GET /users/u_1/records",
    ]


def test_build_client_mock() -> None:
    assert isinstance(build_client("mock"), FixtureClient)
