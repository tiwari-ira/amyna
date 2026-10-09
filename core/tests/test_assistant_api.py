"""The assistant endpoints: scoped, stateless, plain-English, never authoritative."""

import pytest
from fastapi.testclient import TestClient


def test_understand_requires_authentication(client: TestClient) -> None:
    assert client.post("/assistant/understand", json={"message": "hi"}).status_code == 401


def test_understand_is_relay_only(
    client: TestClient, relay: dict[str, str], fetch: dict[str, str]
) -> None:
    # The language layer belongs to patient-facing surfaces, not to agents.
    response = client.post(
        "/assistant/understand", json={"message": "this bill looks wrong"}, headers=fetch
    )
    assert response.status_code == 403
    ok = client.post(
        "/assistant/understand", json={"message": "this bill looks wrong"}, headers=relay
    )
    assert ok.status_code == 200


def test_understand_answers_plain_complaint_in_plain_english(
    client: TestClient, relay: dict[str, str]
) -> None:
    result = client.post(
        "/assistant/understand",
        json={"message": "I got this hospital bill and I don't think it's right"},
        headers=relay,
    ).json()
    assert result["intent"] == "review_bill"
    assert result["suggested_action"] == "request_bill"
    assert "bill" in result["reply"]
    # Never asks the patient to produce jargon it can discover itself.
    assert "FinchNode" not in result["reply"] and "ID" not in result["reply"]


def test_understand_extracts_candidates_but_never_accepts_identity(
    client: TestClient, relay: dict[str, str]
) -> None:
    result = client.post(
        "/assistant/understand",
        json={
            "message": (
                "My name is Mary and this bill for $842.60 with claim CLM-2026-0915-001 is wrong"
            )
        },
        headers=relay,
    ).json()
    assert result["candidate_claim_number"] == "CLM-2026-0915-001"
    assert result["candidate_patient_name"] == "Mary"
    assert result["candidate_amount_cents"] == "84260"
    assert 0.0 <= result["confidence"] <= 1.0


@pytest.mark.parametrize("term", ["What is a claim number?", "member id", "What does CPT mean?"])
def test_explain_term_in_plain_english(
    client: TestClient, relay: dict[str, str], term: str
) -> None:
    result = client.post("/assistant/explain", json={"term": term}, headers=relay).json()
    assert result["plain_language"]
    assert "I" in result["plain_language"]  # Amyna explains itself personally
