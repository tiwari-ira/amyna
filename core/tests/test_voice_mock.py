"""Mock voice call: SSN handoff, injected sensitive requests, guardrails, allowlist."""

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.adapters.xai_voice.dispatcher import ToolCallDispatcher
from app.demo_support import (
    APPROVAL_TEXT,
    answer_open_questions,
    build_isolated_app,
    create_demo_visit,
    service_headers,
)
from app.models_db import CallRow, CaseRow, InterventionRow
from claimshield_contracts.enums import ServiceName
from claimshield_contracts.tool_api import ToolName

APPROVAL = {"channel": "relay", "authorization_text": APPROVAL_TEXT, "authorize_call": True}


@pytest.fixture
def paused_call(
    client: TestClient, relay: dict[str, str], awaiting_approval: dict[str, Any]
) -> dict[str, Any]:
    client.post(
        f"/disputes/{awaiting_approval['dispute']['dispute_id']}/approve",
        json=APPROVAL,
        headers=relay,
    )
    call = client.post(
        "/voice/start", json={"case_id": awaiting_approval["case_id"]}, headers=relay
    ).json()
    return {**awaiting_approval, "call": call}


def test_ssn_request_pauses_and_notifies(
    client: TestClient, relay: dict[str, str], paused_call: dict[str, Any]
) -> None:
    call = paused_call["call"]
    assert call["status"] == "paused_for_human"
    flagged = [t for t in call["turns"] if "sensitive:ssn" in t["flags"]]
    assert flagged and flagged[0]["speaker"] == "insurer"
    intervention = client.get(
        f"/human-intervention/{call['open_intervention_id']}", headers=relay
    ).json()
    assert intervention["reason"] == "identity_verification" and intervention["options"] == [
        "DONE",
        "CANCEL",
    ]
    case = client.get(f"/cases/{paused_call['case_id']}", headers=relay).json()
    assert case["state"] == "HUMAN_REQUIRED"
    notes = client.get(f"/cases/{paused_call['case_id']}/notifications", headers=relay).json()
    handoff = [n for n in notes if n["kind"] == "human_handoff"]
    assert handoff and "never shares your SSN" in handoff[0]["text"]
    agent_lines = [t["text"] for t in call["turns"] if t["speaker"] == "agent"]
    assert not any(any(ch.isdigit() for ch in line) and "SSN" in line for line in agent_lines)


def test_resolution_notes_with_ssn_are_rejected(
    client: TestClient, relay: dict[str, str], paused_call: dict[str, Any]
) -> None:
    response = client.post(
        f"/human-intervention/{paused_call['call']['open_intervention_id']}/resolve",
        json={"choice": "DONE", "notes": "my ssn is 123-45-6789", "channel": "relay"},
        headers=relay,
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "sensitive_data_rejected"


def test_cancel_ends_call_as_failed(
    client: TestClient, relay: dict[str, str], paused_call: dict[str, Any]
) -> None:
    response = client.post(
        f"/human-intervention/{paused_call['call']['open_intervention_id']}/resolve",
        json={"choice": "CANCEL", "channel": "relay"},
        headers=relay,
    ).json()
    assert response["case_state"] == "FAILED" and response["call"]["status"] == "failed"


def test_resolving_handoff_completes_call(
    client: TestClient, relay: dict[str, str], voice: dict[str, str], paused_call: dict[str, Any]
) -> None:
    call_id = paused_call["call"]["call_id"]
    client.post(
        f"/human-intervention/{paused_call['call']['open_intervention_id']}/resolve",
        json={"choice": "DONE", "channel": "relay"},
        headers=relay,
    )
    call = client.get(f"/voice/{call_id}", headers=voice).json()
    assert call["status"] == "completed"
    assert call["insurer_response"]["outcome"] == "partially_removed"
    assert not any(t["flags"] and t["flags"][0].startswith("blocked") for t in call["turns"])


def test_event_endpoint_detects_pin_requests(
    client: TestClient,
    relay: dict[str, str],
    voice: dict[str, str],
    paused_call: dict[str, Any],
    app: Any,
) -> None:
    call_id = paused_call["call"]["call_id"]
    container = app.state.container
    with container.session_scope() as db:  # put the call back in progress, as a live bridge would
        call = db.get(CallRow, call_id)
        db.get(InterventionRow, call.open_intervention_id).status = "resolved"
        call.status, call.open_intervention_id = "in_progress", None
        db.get(CaseRow, call.case_id).state = "CALL_IN_PROGRESS"
    response = client.post(
        f"/voice/{call_id}/event",
        json={"type": "insurer_utterance", "text": "What is the PIN on the account?"},
        headers=voice,
    ).json()
    assert response["action"] == "pause_for_human" and response["intervention_id"]
    assert response["call"]["status"] == "paused_for_human"


def test_number_outside_allowlist_is_never_dialed(
    client: TestClient, relay: dict[str, str], awaiting_approval: dict[str, Any], app: Any
) -> None:
    client.post(
        f"/disputes/{awaiting_approval['dispute']['dispute_id']}/approve",
        json=APPROVAL,
        headers=relay,
    )
    from app.models_db import CaseRow

    with app.state.container.session_scope() as db:
        db.get(CaseRow, awaiting_approval["case_id"]).payer_phone = "+15555550199"
    response = client.post(
        "/voice/start", json={"case_id": awaiting_approval["case_id"]}, headers=relay
    )
    assert response.status_code == 409 and response.json()["detail"]["code"] == "number_not_allowed"
    assert app.state.container.telephony.dialed == []


class _Backend:
    def __init__(self) -> None:
        self.calls: list[tuple[ToolName, dict]] = []

    def call(self, tool: ToolName, payload: dict) -> dict:
        self.calls.append((tool, payload))
        return {"ok": True}


def test_dispatcher_injects_ids_and_rejects_model_supplied_case_id() -> None:
    backend = _Backend()
    dispatcher = ToolCallDispatcher(backend, case_id="case_real", call_id="call_real")
    result = dispatcher.dispatch("get_dispute_case", {"case_id": "case_other"})
    assert result["error"] == "invalid_arguments" and backend.calls == []
    dispatcher.dispatch("request_human", '{"reason": "credential_request", "detail": "PIN asked"}')
    tool, payload = backend.calls[0]
    assert tool == ToolName.REQUEST_HUMAN
    assert payload["case_id"] == "case_real" and payload["call_id"] == "call_real"
    assert dispatcher.dispatch("delete_everything", {})["error"] == "unknown_tool"


def _back_in_progress(app: Any, call_id: str) -> None:
    """Resolve the open handoff in the DB and resume the call, as a live bridge would."""
    with app.state.container.session_scope() as db:
        call = db.get(CallRow, call_id)
        db.get(InterventionRow, call.open_intervention_id).status = "resolved"
        call.status, call.open_intervention_id = "in_progress", None
        db.get(CaseRow, call.case_id).state = "CALL_IN_PROGRESS"


def test_cancel_with_a_failing_hangup_still_closes_the_call(
    client: TestClient,
    relay: dict[str, str],
    paused_call: dict[str, Any],
    app: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def hangup_fails(_ref: str) -> None:
        raise RuntimeError("carrier unreachable")

    monkeypatch.setattr(app.state.container.telephony, "hangup", hangup_fails)
    response = client.post(
        f"/human-intervention/{paused_call['call']['open_intervention_id']}/resolve",
        json={"choice": "CANCEL", "channel": "relay"},
        headers=relay,
    )
    assert response.status_code == 200
    assert response.json()["case_state"] == "FAILED"


def test_a_call_handoff_is_never_a_confirmation_question(
    client: TestClient, relay: dict[str, str], paused_call: dict[str, Any], app: Any
) -> None:
    call_id = paused_call["call"]["call_id"]
    _back_in_progress(app, call_id)
    handoff = client.post(
        "/human-intervention",
        json={
            "case_id": paused_call["case_id"],
            "reason": "patient_confirmation",
            "prompt": "Please confirm with the representative.",
            "call_id": call_id,
        },
        headers=relay,
    )
    assert handoff.status_code == 201
    assert handoff.json()["reason"] == "call_escalation"  # pausing is the safe default
    resolved = client.post(
        f"/human-intervention/{handoff.json()['intervention_id']}/resolve",
        json={"choice": "DONE", "channel": "relay"},
        headers=relay,
    )
    assert resolved.status_code == 200


def test_insurer_notes_with_an_ssn_are_rejected(
    client: TestClient, voice: dict[str, str], paused_call: dict[str, Any]
) -> None:
    response = client.post(
        "/internal/tools/record_insurer_response",
        json={
            "case_id": paused_call["case_id"],
            "call_id": paused_call["call"]["call_id"],
            "outcome": "partially_removed",
            "notes": "Rep confirmed member SSN 123-45-6789.",
        },
        headers=voice,
    )
    assert response.status_code == 422 and "123-45-6789" not in response.text


# ------------------------------------------------------------------ human merge


def _ready_to_call(tmp_path: Path):
    """A fresh case, fully approved, with HUMAN_HANDOFF_PHONE configured."""
    app = build_isolated_app(
        tmp_path / "merge.db",
        human_handoff_phone="+15555550199",
        # The demo payer line (from the fixtures) + the human-merge line.
        telephony_allowed_numbers="+12487208903,+15555550199",
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
    answer_open_questions(client, relay, setup["case_id"])
    prepared = client.post("/disputes", json={"case_id": setup["case_id"]}, headers=relay).json()
    client.post(
        f"/disputes/{prepared['dispute']['dispute_id']}/approve", json=APPROVAL, headers=relay
    )
    return app, client, relay, setup


def test_a_paused_call_dials_bridges_and_releases_the_human(tmp_path: Path) -> None:
    """HUMAN_HANDOFF_PHONE set: the SSN pause dials the human (mock: bridged
    instantly); DONE hangs the human up first, then the insurer call finishes."""
    app, client, relay, setup = _ready_to_call(tmp_path)
    call = client.post("/voice/start", json={"case_id": setup["case_id"]}, headers=relay).json()
    call_id = call["call_id"]
    telephony = app.state.container.telephony
    assert call["status"] == "paused_for_human"
    assert telephony.human_dialed == [call_id]
    assert telephony.bridged == [(f"MOCK-HU-{call_id[-8:]}", f"MOCK-CA-{call_id[-8:]}")]
    resolved = client.post(
        f"/human-intervention/{call['open_intervention_id']}/resolve",
        json={"choice": "DONE", "channel": "relay"},
        headers=relay,
    ).json()
    assert resolved["case_state"] == "RESOLVED"
    assert telephony.hung_up[0] == f"MOCK-HU-{call_id[-8:]}"  # the human leaves first


def test_duplicate_sensitive_request_does_not_merge_a_second_human_leg(tmp_path: Path) -> None:
    """17A: a repeated sensitive request (or duplicate event) while paused must be
    idempotent -- one open intervention, one dialed human leg, one bridge."""
    app, client, relay, setup = _ready_to_call(tmp_path)
    voice = service_headers(app.state.container.settings, ServiceName.VOICE)
    call = client.post("/voice/start", json={"case_id": setup["case_id"]}, headers=relay).json()
    call_id = call["call_id"]
    telephony = app.state.container.telephony
    assert call["status"] == "paused_for_human"
    assert telephony.human_dialed == [call_id]
    first_intervention = call["open_intervention_id"]
    # The insurer repeats the sensitive request while the call is already paused.
    again = client.post(
        f"/voice/{call_id}/event",
        json={
            "type": "insurer_utterance",
            "text": "Once more, I need the member's Social Security number.",
        },
        headers=voice,
    ).json()
    assert again["call"]["status"] == "paused_for_human"
    assert again["call"]["open_intervention_id"] == first_intervention
    assert telephony.human_dialed == [call_id]
    assert len(telephony.bridged) == 1


def test_a_failed_human_dial_still_pauses_the_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the human cannot be dialed, the proven pause-only flow continues."""
    app, client, relay, setup = _ready_to_call(tmp_path)

    def boom(call_id, to_number, insurer_ref=None):
        raise RuntimeError("audio lines are busy")

    monkeypatch.setattr(app.state.container.telephony, "dial_human", boom)
    call = client.post("/voice/start", json={"case_id": setup["case_id"]}, headers=relay).json()
    assert call["status"] == "paused_for_human"
    assert app.state.container.telephony.human_dialed == []
