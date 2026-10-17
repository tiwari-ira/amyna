"""[2.1] Per-IP rate limiting and the auth-failure circuit."""

from pathlib import Path

from fastapi.testclient import TestClient

from app.demo_support import build_isolated_app, service_headers
from claimshield_contracts.enums import ServiceName


def _client(tmp_path: Path, per_minute: int) -> TestClient:
    app = build_isolated_app(tmp_path / "rate.db", rate_limit_per_minute=per_minute)
    return TestClient(app)


def test_requests_past_the_window_are_429_and_health_is_exempt(tmp_path: Path) -> None:
    client = _client(tmp_path, per_minute=5)
    for _ in range(5):
        assert client.get("/health").status_code == 200  # exempt: never counted
    for _ in range(5):
        assert client.get("/cases/case_x").status_code == 401  # auth failure, not 429 (yet)
    # The 6th non-health request in the window hits the cap.
    limited = client.get("/cases/case_x")
    assert limited.status_code == 429
    assert limited.headers["Retry-After"]
    assert limited.json()["detail"]["code"] == "rate_limited"


def test_repeated_bad_tokens_trip_the_auth_circuit(tmp_path: Path) -> None:
    client = _client(tmp_path, per_minute=10_000)
    for _ in range(20):
        assert client.get("/cases/case_x").status_code == 401
    # The circuit opened: even /health is exempt, but business routes are 429.
    assert client.get("/health").status_code == 200
    assert client.get("/cases/case_x").status_code == 429


def test_valid_traffic_below_the_cap_flows_normally(tmp_path: Path) -> None:
    client = _client(tmp_path, per_minute=100)
    relay = service_headers(client.app.state.container.settings, ServiceName.RELAY)
    case = client.post(
        "/cases",
        json={"patient": {"finchnode_patient_id": "fn_pat_demo_001"}, "channel": "relay"},
        headers=relay,
    )
    assert case.status_code == 201
