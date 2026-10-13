"""17A: first-class human-merge call states.

AI_CALLING -> SENSITIVE_REQUEST_DETECTED -> PATIENT_BEING_CONNECTED
-> PATIENT_CONNECTED_WITH_HUMAN -> PATIENT_HANDLING_VERIFICATION
-> PATIENT_RELEASED -> AI_RESUMING -> AI_CALLING, with MERGE_FAILED as the
explicit timeout/failure escape. After a failed merge the insurer stays on the
line and the AI resumes only to say the member will verify later; it never
provides the requested information.
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


class _ScriptedVoice:
    """Stands in for a live session: records commands, speaks only when told."""

    needs_sip_attach = False
    mode = "scripted"

    def __init__(self) -> None:
        self.resumed: list[str] = []
        self.paused: list[str] = []

    def start_session(self, config, handler) -> None:
        handler.on_insurer_utterance(config.call_id, SSN_UTTERANCE)

    def resume_session(self, call_id, handler, note="verified") -> None:
        self.resumed.append(note)

    def pause_session(self, call_id) -> None:
        self.paused.append(call_id)

    def end_session(self, call_id, reason) -> None:
        return None

    def start_speaking(self, call_id) -> None:
        return None


def _scripted(app) -> _ScriptedVoice:
    voice = _ScriptedVoice()
    pytest.MonkeyPatch().setattr(app.state.container, "voice", voice)
    return voice


def test_a_failed_merge_keeps_the_insurer_and_resumes_without_the_info(tmp_path: Path) -> None:
    app, client, relay, setup = _merged_ready(tmp_path)
    voice = _scripted(app)

    def boom(call_id, to_number, insurer_ref=None):
        raise RuntimeError("audio lines are busy")

    pytest.MonkeyPatch().setattr(app.state.container.telephony, "dial_human", boom)
    call = client.post("/voice/start", json={"case_id": setup["case_id"]}, headers=relay).json()
    assert call["merge_phase"] == "merge_failed"
    assert call["status"] == "in_progress"  # the insurer is still on the line
    assert voice.resumed == ["unreachable"]  # the AI tells the rep; it never shares the info
    assert app.state.container.telephony.hung_up == []
    notes = client.get(f"/cases/{setup['case_id']}/notifications", headers=relay).json()
    assert any("couldn't connect you to the call" in n["text"] for n in notes)
    # The same request right afterwards does not pause or dial again.
    voice_headers = service_headers(app.state.container.settings, ServiceName.VOICE)
    again = client.post(
        f"/voice/{call['call_id']}/event",
        json={"type": "insurer_utterance", "text": SSN_UTTERANCE},
        headers=voice_headers,
    ).json()
    assert again["call"]["status"] == "in_progress"
    assert app.state.container.telephony.human_dialed == []


def test_an_unreachable_patient_times_out_and_the_call_goes_on(tmp_path: Path) -> None:
    """PATIENT_BEING_CONNECTED has a deadline: the ringing leg is dropped, the
    insurer stays, and the AI continues without the information."""
    app, client, relay, setup = _merged_ready(tmp_path)
    voice = _scripted(app)

    def not_answered(call_id, to_number, insurer_ref=None):
        return DialResult(provider_call_ref="MOCK-HU-X", status="initiated")

    pytest.MonkeyPatch().setattr(app.state.container.telephony, "dial_human", not_answered)
    call = client.post("/voice/start", json={"case_id": setup["case_id"]}, headers=relay).json()
    assert call["merge_phase"] == "patient_being_connected"
    future = datetime.now(UTC) + timedelta(seconds=121)
    assert app.state.container.voice_manager.expire_overdue_merges(now=future) == 1
    merged = client.get(f"/voice/{call['call_id']}", headers=relay).json()
    assert merged["merge_phase"] == "merge_failed"
    assert merged["status"] == "in_progress"
    assert app.state.container.telephony.hung_up == ["MOCK-HU-X"]  # only the ringing leg
    assert voice.resumed == ["unreachable"]
    notes = client.get(f"/cases/{setup['case_id']}/notifications", headers=relay).json()
    assert any("didn't pick up in time" in n["text"] for n in notes)


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
    assert texts.count("The member verified directly with the representative.") == 1


def test_the_patient_hanging_up_resumes_the_ai(tmp_path: Path) -> None:
    """Hanging up after speaking with the rep counts as Done (no chat tap needed)."""
    app, client, relay, setup = _merged_ready(tmp_path)
    voice = _scripted(app)
    call = client.post("/voice/start", json={"case_id": setup["case_id"]}, headers=relay).json()
    assert call["merge_phase"] == "patient_connected_with_human"
    app.state.container.voice_manager.patient_left(call["call_id"])
    after = client.get(f"/voice/{call['call_id']}", headers=relay).json()
    assert after["status"] == "in_progress"
    assert voice.resumed == ["verified"]
    # A late Done tap in the chat is harmless.
    late = client.post(
        f"/human-intervention/{call['open_intervention_id']}/resolve",
        json={"choice": "DONE", "channel": "relay"},
        headers=relay,
    )
    assert late.status_code == 409
    assert voice.resumed == ["verified"]


def test_the_model_asking_again_right_after_does_not_redial(tmp_path: Path) -> None:
    """Seen live: after the member finished, the model called request_human again
    and the phone was dialed a second time."""
    app, client, relay, setup = _merged_ready(tmp_path)
    _scripted(app)
    call = client.post("/voice/start", json={"case_id": setup["case_id"]}, headers=relay).json()
    manager = app.state.container.voice_manager
    manager.patient_left(call["call_id"])
    result = manager.on_tool_call(
        call["call_id"],
        "request_human",
        {"reason": "identity_verification", "detail": "Representative asked for the SSN."},
    )
    assert result["status"] == "already_handled"
    assert app.state.container.telephony.human_dialed == [call["call_id"]]  # still one dial


def test_the_insurers_own_number_is_never_dialed_as_the_patient(tmp_path: Path) -> None:
    """Seen live: the handoff phone was the insurer's own line."""
    app, client, relay, setup = _approved_case(
        tmp_path,
        human_handoff_phone="+12487208903",  # the demo insurer's number
        telephony_allowed_numbers="+12487208903",
    )
    call = client.post("/voice/start", json={"case_id": setup["case_id"]}, headers=relay).json()
    assert app.state.container.telephony.human_dialed == []
    assert call["merge_phase"] == "sensitive_request_detected"
    notes = client.get(f"/cases/{setup['case_id']}/notifications", headers=relay).json()
    assert any("insurer's own line" in n["text"] for n in notes)


def test_a_number_read_aloud_is_never_stored(tmp_path: Path) -> None:
    app, client, relay, setup = _merged_ready(tmp_path)
    _scripted(app)
    call = client.post("/voice/start", json={"case_id": setup["case_id"]}, headers=relay).json()
    voice_headers = service_headers(app.state.container.settings, ServiceName.VOICE)
    client.post(
        f"/voice/{call['call_id']}/event",
        json={"type": "insurer_utterance", "text": "Okay, I have 123-45-6789 on file."},
        headers=voice_headers,
    )
    turns = client.get(f"/voice/{call['call_id']}", headers=relay).json()["turns"]
    assert all("123-45-6789" not in t["text"] for t in turns)
    assert any("redacted" in t["flags"] for t in turns)
