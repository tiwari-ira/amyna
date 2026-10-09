"""17A: first-class human-merge call states.

AI_CALLING -> SENSITIVE_REQUEST_DETECTED -> PATIENT_BEING_CONNECTED
-> PATIENT_CONNECTED_WITH_HUMAN -> PATIENT_HANDLING_VERIFICATION
-> PATIENT_RELEASED -> AI_RESUMING -> AI_CALLING, with MERGE_FAILED as the
explicit timeout/failure escape. The AI never resumes automatically.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.adapters.telephony.base import DialResult
from app.demo_support import (
    APPROVAL_TEXT,
    answer_open_questions,
    build_isolated_app,
    create_demo_visit,
    service_headers,
)
from claimshield_contracts.enums import ServiceName

APPROVAL = {"channel": "relay", "authorization_text": APPROVAL_TEXT, "authorize_call": True}
SSN_UTTERANCE = (
    "Before I can discuss the claim, can you give me the member's Social Security number?"
)


def _approved_case(tmp_path: Path, **settings: object):
    """A fully approved case ready for an insurer call."""
    app = build_isolated_app(tmp_path / "merge.db", **settings)
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


def _merged_ready(tmp_path: Path):
    return _approved_case(
        tmp_path,
        human_handoff_phone="+15555550199",
        telephony_allowed_numbers="+12487208903,+15555550199",
    )


def test_sensitive_request_immediately_connects_the_human(tmp_path: Path) -> None:
    """A sensitive request pauses the AI and connects the patient right away."""
    _, client, relay, setup = _merged_ready(tmp_path)
    call = client.post("/voice/start", json={"case_id": setup["case_id"]}, headers=relay).json()
    assert call["status"] == "paused_for_human"
    assert call["merge_phase"] == "patient_connected_with_human"


def test_no_handoff_phone_keeps_the_pause_only_flow(tmp_path: Path) -> None:
    """Without HUMAN_HANDOFF_PHONE there is no merge: phase is detection only."""
    _, client, relay, setup = _approved_case(tmp_path)
    call = client.post("/voice/start", json={"case_id": setup["case_id"]}, headers=relay).json()
    assert call["status"] == "paused_for_human"
    assert call["merge_phase"] == "sensitive_request_detected"


def test_a_failed_merge_is_an_explicit_state_and_never_auto_resumes(tmp_path: Path) -> None:
    app, client, relay, setup = _merged_ready(tmp_path)

    def boom(call_id, to_number, insurer_ref=None):
        raise RuntimeError("audio lines are busy")

    pytest.MonkeyPatch().setattr(app.state.container.telephony, "dial_human", boom)
    call = client.post("/voice/start", json={"case_id": setup["case_id"]}, headers=relay).json()
    assert call["merge_phase"] == "merge_failed"
    assert call["status"] == "paused_for_human"  # AI stays paused
    notes = client.get(f"/cases/{setup['case_id']}/notifications", headers=relay).json()
    assert any("couldn't reach" in n["text"] for n in notes)
    # A duplicate sensitive request after a failed merge does NOT dial again.
    voice = service_headers(app.state.container.settings, ServiceName.VOICE)
    again = client.post(
        f"/voice/{call['call_id']}/event",
        json={"type": "insurer_utterance", "text": SSN_UTTERANCE},
        headers=voice,
    ).json()
    assert again["call"]["merge_phase"] == "merge_failed"
    assert app.state.container.telephony.human_dialed == []
    resolved = client.post(
        f"/human-intervention/{call['open_intervention_id']}/resolve",
        json={"choice": "DONE", "channel": "relay"},
        headers=relay,
    ).json()
    assert resolved["case_state"] == "RESOLVED"


def test_an_unreachable_patient_times_out_and_escalates(tmp_path: Path) -> None:
    """PATIENT_BEING_CONNECTED has a safe deadline; expiry escalates, never resumes."""
    app, client, relay, setup = _merged_ready(tmp_path)

    def not_answered(call_id, to_number, insurer_ref=None):
        return DialResult(provider_call_ref="MOCK-HU-X", status="initiated")

    pytest.MonkeyPatch().setattr(app.state.container.telephony, "dial_human", not_answered)
    call = client.post("/voice/start", json={"case_id": setup["case_id"]}, headers=relay).json()
    assert call["merge_phase"] == "patient_being_connected"
    future = datetime.now(UTC) + timedelta(seconds=121)
    assert app.state.container.voice_manager.expire_overdue_merges(now=future) == 1
    merged = client.get(f"/voice/{call['call_id']}", headers=relay).json()
    assert merged["merge_phase"] == "merge_failed"
    assert merged["status"] == "paused_for_human"  # never auto-resumed
    assert app.state.container.telephony.hung_up == []  # insurer leg kept alive
    notes = client.get(f"/cases/{setup['case_id']}/notifications", headers=relay).json()
    assert any("did not answer" in n["text"] or "couldn't reach" in n["text"] for n in notes)


def test_done_runs_the_full_release_cycle_before_the_ai_resumes(tmp_path: Path) -> None:
    """DONE -> PATIENT_RELEASED -> AI_RESUMING -> merge lifecycle cleared."""
    _, client, relay, setup = _merged_ready(tmp_path)
    call = client.post("/voice/start", json={"case_id": setup["case_id"]}, headers=relay).json()
    assert call["merge_phase"] == "patient_connected_with_human"
    client.post(
        f"/human-intervention/{call['open_intervention_id']}/resolve",
        json={"choice": "DONE", "channel": "relay"},
        headers=relay,
    )
    finished: dict[str, Any] = client.get(f"/voice/{call['call_id']}", headers=relay).json()
    assert finished["merge_phase"] is None  # back to the AI_CALLING baseline
    texts = [t["text"] for t in finished["turns"]]
    assert "The human left the call." in texts
    assert "Member verified directly with the representative." in texts
