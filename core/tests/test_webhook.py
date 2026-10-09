"""Signed, idempotent POST /relay/webhook."""

import json
import time
from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient

from app.auth import SIGNATURE_HEADER, TIMESTAMP_HEADER, sign_payload
from app.config import Settings
from app.demo_support import signed_post


def _event(case_id: str, **extra: Any) -> dict[str, Any]:
    return {
        "event_id": extra.pop("event_id", "evt_abcdef01"),
        "event_type": extra.pop("event_type", "button_reply"),
        "case_id": case_id,
        "occurred_at": datetime.now(UTC).isoformat(),
        **extra,
    }


def _first_question(analyzed_case: dict[str, Any]) -> str:
    return analyzed_case["analysis"]["next_question"]["intervention_id"]


def test_valid_signature_answers_question(
    client: TestClient, settings: Settings, analyzed_case: dict[str, Any]
) -> None:
    event = _event(
        analyzed_case["case_id"], intervention_id=_first_question(analyzed_case), choice="NO"
    )
    response = signed_post(client, settings.webhook_secret(), "/relay/webhook", event)
    assert response.status_code == 200 and response.json()["status"] == "processed"


def test_duplicate_event_is_processed_once(
    client: TestClient, settings: Settings, analyzed_case: dict[str, Any]
) -> None:
    event = _event(
        analyzed_case["case_id"], intervention_id=_first_question(analyzed_case), choice="NO"
    )
    first = signed_post(client, settings.webhook_secret(), "/relay/webhook", event)
    second = signed_post(client, settings.webhook_secret(), "/relay/webhook", event)
    assert first.json()["status"] == "processed"
    assert second.json()["status"] == "duplicate"


def test_bad_or_stale_signatures_are_rejected(
    client: TestClient, analyzed_case: dict[str, Any]
) -> None:
    event = _event(analyzed_case["case_id"], choice="NO")
    assert signed_post(client, "wrong-secret", "/relay/webhook", event).status_code == 401
    body = json.dumps(event).encode()
    stale = str(int(time.time()) - 3600)
    response = client.post(
        "/relay/webhook",
        content=body,
        headers={
            "Content-Type": "application/json",
            TIMESTAMP_HEADER: stale,
            SIGNATURE_HEADER: sign_payload("dev-core-webhook-secret", stale, body),
        },
    )
    assert response.status_code == 401
    assert "tolerance" in response.json()["detail"]["message"]


def test_unmatched_text_is_logged_and_ignored(
    client: TestClient, settings: Settings, analyzed_case: dict[str, Any]
) -> None:
    event = _event(
        analyzed_case["case_id"], event_type="text_reply", text="thanks!", event_id="evt_text01"
    )
    response = signed_post(client, settings.webhook_secret(), "/relay/webhook", event)
    assert response.json()["status"] == "ignored"


def test_approval_button_approves_dispute(
    client: TestClient, settings: Settings, relay: dict[str, str], awaiting_approval: dict[str, Any]
) -> None:
    event = _event(
        awaiting_approval["case_id"],
        dispute_id=awaiting_approval["dispute"]["dispute_id"],
        choice="APPROVE",
        event_id="evt_approve1",
    )
    response = signed_post(client, settings.webhook_secret(), "/relay/webhook", event)
    assert response.json()["case_state"] == "APPROVED"
    dispute = client.get(
        f"/disputes/{awaiting_approval['dispute']['dispute_id']}", headers=relay
    ).json()
    assert dispute["patient_authorization_id"]


def test_secrets_typed_into_replies_are_never_stored(
    app: Any, client: TestClient, settings: Settings, analyzed_case: dict[str, Any]
) -> None:
    from app.models_db import WebhookEventRow

    event = _event(
        analyzed_case["case_id"],
        event_id="evt_secret01",
        event_type="text_reply",
        intervention_id=_first_question(analyzed_case),
        choice="no, my ssn is 123-45-6789",
    )
    response = signed_post(client, settings.webhook_secret(), "/relay/webhook", event)
    assert response.json()["status"] == "ignored" and "123-45-6789" not in response.text
    with app.state.container.session_scope() as db:
        stored = db.get(WebhookEventRow, "evt_secret01").result
    assert "123-45-6789" not in json.dumps(stored)
