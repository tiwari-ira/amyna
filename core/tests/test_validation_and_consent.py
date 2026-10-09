"""Consent gates and input validation."""

from typing import Any

from fastapi.testclient import TestClient

from app.demo_support import create_demo_visit
from claimshield_contracts.demo import demo_patient


def test_finchnode_claim_requires_consent(client: TestClient, relay: dict[str, str]) -> None:
    setup = create_demo_visit(client, relay, data_consent=False)
    response = client.post(
        "/claims",
        json={
            "case_id": setup["case_id"],
            "source": "finchnode",
            "finchnode_claim_id": "CLM-2026-0915-001",
        },
        headers=relay,
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "consent_required"


def test_visit_requires_recording_consent(client: TestClient, relay: dict[str, str]) -> None:
    case = client.post(
        "/cases", json={"patient": demo_patient().model_dump(), "channel": "relay"}, headers=relay
    ).json()
    response = client.post(
        "/visits",
        json={"case_id": case["case_id"], "recording_consent_id": "cns_fake"},
        headers=relay,
    )
    assert response.status_code == 409 and response.json()["detail"]["code"] == "consent_required"


def test_invalid_choice_is_422(
    client: TestClient, relay: dict[str, str], analyzed_case: dict[str, Any]
) -> None:
    question = analyzed_case["analysis"]["next_question"]["intervention_id"]
    response = client.post(
        f"/human-intervention/{question}/resolve",
        json={"choice": "MAYBE", "channel": "relay"},
        headers=relay,
    )
    assert response.status_code == 422


def test_free_text_answers_are_normalized(
    client: TestClient, relay: dict[str, str], analyzed_case: dict[str, Any]
) -> None:
    question = analyzed_case["analysis"]["next_question"]["intervention_id"]
    response = client.post(
        f"/human-intervention/{question}/resolve",
        json={"choice": "not sure", "channel": "relay"},
        headers=relay,
    )
    assert response.status_code == 200
    assert response.json()["intervention"]["resolution"]["choice"] == "NOT_SURE"


def test_unknown_fields_are_rejected(client: TestClient, relay: dict[str, str]) -> None:
    response = client.post(
        "/cases",
        json={"patient": demo_patient().model_dump(), "channel": "relay", "oops": 1},
        headers=relay,
    )
    assert response.status_code == 422


def test_upload_claim_and_not_found(client: TestClient, relay: dict[str, str]) -> None:
    case = client.post(
        "/cases", json={"patient": demo_patient().model_dump(), "channel": "api"}, headers=relay
    ).json()
    claim = {
        "external_claim_id": "UP-1",
        "date_of_service": "2026-09-15",
        "lines": [
            {
                "line_number": 1,
                "code": {"system": "CPT", "code": "99213"},
                "description": "Office visit",
                "date_of_service": "2026-09-15",
                "billed_cents": 15000,
            }
        ],
    }
    response = client.post(
        "/claims",
        json={"case_id": case["case_id"], "source": "upload", "claim": claim},
        headers=relay,
    )
    assert response.status_code == 201 and response.json()["total_billed_cents"] == 15000
    assert client.get("/claims/clm_missing", headers=relay).status_code == 404
