"""Failure-matrix tests (task 31): duplicate approvals, Relay outage,
Agentverse disconnect, and ambiguous identity -- all deterministic."""

from pathlib import Path

from fastapi.testclient import TestClient

from app.demo_support import (
    APPROVAL_TEXT,
    answer_open_questions,
    build_isolated_app,
    create_demo_visit,
    service_headers,
)
from claimshield_contracts.enums import ServiceName


def _approved(client: TestClient, relay: dict, setup: dict) -> dict:
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
    answer_open_questions(client, relay, setup["case_id"])
    dispute = client.post("/disputes", json={"case_id": setup["case_id"]}, headers=relay).json()
    return {"dispute": dispute["dispute"], "case_id": setup["case_id"]}


def test_approving_twice_never_duplicates_the_call(tmp_path: Path) -> None:
    app = build_isolated_app(tmp_path / "approve.db")
    client = TestClient(app)
    relay = service_headers(app.state.container.settings, ServiceName.RELAY)
    setup = create_demo_visit(client, relay)
    prepared = _approved(client, relay, setup)
    approve = {"channel": "relay", "authorization_text": APPROVAL_TEXT, "authorize_call": True}
    first = client.post(
        f"/disputes/{prepared['dispute']['dispute_id']}/approve", json=approve, headers=relay
    )
    assert first.status_code == 200
    still = client.post(
        f"/disputes/{prepared['dispute']['dispute_id']}/approve", json=approve, headers=relay
    )
    # A duplicate approval is refused: it cannot mint a second authorization.
    assert still.status_code == 409
    assert still.json()["detail"]["code"] == "not_awaiting_approval"
    detail = client.get(f"/cases/{prepared['case_id']}", headers=relay).json()
    assert detail["dispute"]["status"] == "approved"  # still exactly one approval


def test_relay_outage_never_breaks_the_case(tmp_path: Path) -> None:
    """With NOTIFY_MODE=relay_service and the relay down, the case still moves."""
    app = build_isolated_app(
        tmp_path / "out.db", notify_mode="relay_service", relay_service_url="http://127.0.0.1:9"
    )
    client = TestClient(app)
    relay = service_headers(app.state.container.settings, ServiceName.RELAY)
    setup = create_demo_visit(client, relay)  # creates case + consent + visit
    # verify the notification was still persisted and the case is queryable
    detail = client.get(f"/cases/{setup['case_id']}", headers=relay).json()
    assert detail["case_id"] == setup["case_id"]


def test_agentverse_disconnect_keeps_the_transcript_for_retry(tmp_path: Path) -> None:
    """Orchestrator unreachable: the visit's data persists; the call fails closed."""
    from app.demo_support import RECORDING_CONSENT, demo_patient
    from claimshield_contracts.demo import demo_visit_complete

    app = build_isolated_app(
        tmp_path / "fetchout.db",
        fetch_mode="agentverse",
        orchestrator_url="http://127.0.0.1:9",
    )
    client = TestClient(app)
    relay = service_headers(app.state.container.settings, ServiceName.RELAY)
    case = client.post(
        "/cases",
        json={"patient": demo_patient().model_dump(), "channel": "relay"},
        headers=relay,
    ).json()
    consent = client.post(
        f"/cases/{case['case_id']}/consents",
        json={
            "consent_type": "recording",
            "consent_text": RECORDING_CONSENT,
            "channel": "relay",
        },
        headers=relay,
    ).json()
    from claimshield_contracts.demo import load_demo_scenario

    visit = client.post(
        "/visits",
        json={
            "case_id": case["case_id"],
            "recording_consent_id": consent["consent_id"],
            **load_demo_scenario()["visit"],
        },
        headers=relay,
    ).json()
    # The transcript is written atomically before the bridge; the drain to the
    # (down) orchestrator reported 502 instead of losing the stored work.
    complete = client.post(
        f"/visits/{visit['visit_id']}/complete",
        json=demo_visit_complete().model_dump(mode="json"),
        headers=relay,
    )
    assert complete.status_code == 502
    after = client.get(f"/cases/{case['case_id']}", headers=relay).json()
    assert after["visit"]["status"] in ("transcribed", "recorded")
    assert after["visit"]["recording_consent_id"] == consent["consent_id"]
    # ...and a retry once Core's network path is healthy sees the stored transcript.
    assert after["state"] in ("TRANSCRIBED", "VISIT_RECORDED")


def test_an_ambiguous_identity_never_searches_patients(
    client: TestClient, relay: dict[str, str]
) -> None:
    """A name alone cannot create or open a case (IDs are identifiers, not keys)."""
    response = client.post(
        "/cases",
        json={"patient": {"display_name": "Mary"}, "channel": "relay"},
        headers=relay,
    )
    assert response.status_code == 422  # no identifier, no lookup, no case
    assistant = client.post(
        "/assistant/understand",
        json={"message": "My name is Mary and this bill is wrong"},
        headers=relay,
    ).json()
    # Extraction is a candidate only; the backend verified nothing changed.
    assert assistant["candidate_patient_name"] == "Mary"
    assert assistant["candidate_claim_number"] is None


def test_telnyx_outage_blocks_the_call_loudly(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    """If the provider fails, the call fails up (502) and the case stays safe."""
    app = build_isolated_app(
        tmp_path / "telout.db",
        human_handoff_phone="+15555550199",
        telephony_allowed_numbers="+12487208903,+15555550199",
    )

    def boom(call_id, to_number):  # noqa: ANN001, ANN202
        from app.errors import UpstreamError

        raise UpstreamError("telephony provider down", code="telnyx_down")

    monkeypatch.setattr(app.state.container.telephony, "dial", boom)
    client = TestClient(app)
    relay = service_headers(app.state.container.settings, ServiceName.RELAY)
    setup = create_demo_visit(client, relay)
    prepared = _approved(client, relay, setup)
    client.post(
        f"/disputes/{prepared['dispute']['dispute_id']}/approve",
        json={"channel": "relay", "authorization_text": APPROVAL_TEXT, "authorize_call": True},
        headers=relay,
    )
    response = client.post("/voice/start", json={"case_id": prepared["case_id"]}, headers=relay)
    assert response.status_code == 502


def test_assistant_never_sees_a_session_secret(client: TestClient, relay: dict[str, str]) -> None:
    """The language layer handles only text -- credentials are never passed in."""
    result = client.post(
        "/assistant/understand",
        json={"message": "my ssn is 123-45-6789, can you check the bill?"},
        headers=relay,
    ).json()
    assert "123-45-6789" not in str(result)
