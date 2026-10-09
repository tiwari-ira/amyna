"""FinchNode clients: fixtures, sandbox auth, pagination, retries."""

import httpx
import pytest

from finchnode.client import FixtureClient, HttpFinchNodeClient, build_client


def _http(handler) -> httpx.Client:  # type: ignore[no-untyped-def]
    return httpx.Client(base_url="https://finchnode.test", transport=httpx.MockTransport(handler))


def test_fixture_client() -> None:
    client = FixtureClient()
    assert client.get_patient("fn_pat_demo_001")["id"] == "fn_pat_demo_001"
    assert len(client.search("Claim", "fn_pat_demo_001")) == 2
    assert client.get_patient("nobody") is None and client.search("Claim", "nobody") == []


def test_sandbox_requires_test_key() -> None:
    with pytest.raises(ValueError, match="FINCHNODE_API_KEY"):
        HttpFinchNodeClient(mode="sandbox", base_url="https://x")
    with pytest.raises(ValueError, match="ck_test_"):
        HttpFinchNodeClient(mode="sandbox", base_url="https://x", api_key="ck_live_123")


def test_sandbox_sends_bearer_and_follows_pages() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if "page=2" in str(request.url):
            return httpx.Response(200, json={"entry": [{"resource": {"id": "b"}}]})
        return httpx.Response(
            200,
            json={
                "entry": [{"resource": {"id": "a"}}],
                "link": [{"relation": "next", "url": "https://finchnode.test/fhir/Claim?page=2"}],
            },
        )

    client = HttpFinchNodeClient(
        mode="sandbox",
        base_url="https://finchnode.test",
        api_key="ck_test_abc",
        http=_http(handler),
    )
    assert [r["id"] for r in client.search("Claim", "p1")] == ["a", "b"]
    assert seen[0].headers["Authorization"] == "Bearer ck_test_abc"
    assert seen[0].url.params["patient"] == "p1"


def test_demo_uses_the_public_fake_api_and_rejects_a_key() -> None:
    from finchnode.client import DEMO_BASE_URL

    with pytest.raises(ValueError, match="no API key"):
        HttpFinchNodeClient(mode="demo", base_url="https://finchnode.test", api_key="ck_test_abc")
    client = HttpFinchNodeClient(mode="demo", base_url="")
    assert str(client._http.base_url).rstrip("/") == DEMO_BASE_URL
    client._http.close()


def test_demo_mode_is_keyless_and_retries_5xx() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        assert "Authorization" not in request.headers
        if calls["n"] == 1:
            return httpx.Response(503)
        return httpx.Response(404)

    client = HttpFinchNodeClient(
        mode="demo", base_url="https://finchnode.test", http=_http(handler), backoff_s=0
    )
    assert client.get_patient("p1") is None
    assert calls["n"] == 2


def test_build_client_mock() -> None:
    assert isinstance(build_client("mock"), FixtureClient)
