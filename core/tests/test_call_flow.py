"""Live call sequence on Telnyx (offline: Telnyx is a MockTransport; no calls placed).

Voicemail check -> the patient's recorded authorization -> conference + AI leg
-> patient leg joins the same conference. Every step must be idempotent and a
failure must never strand the insurer silently.
"""

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.adapters.telephony.telnyx_sip import (
    TelnyxSipTelephony,
    ai_client_state,
    human_client_state,
    to_client_state,
)
from app.demo_support import build_isolated_app
from app.models_db import CallRow, CaseRow, ClaimRow, DisputeRow, NotificationRow
from app.names import spoken_name
from app.services import call_flow, recording_service

BASE = "https://amyna.example.test"


def _event(event_type: str, **payload: object) -> bytes:
    return json.dumps({"data": {"event_type": event_type, "payload": payload}}).encode()


class Telnyx:
    """Records every Telnyx request; answers like the Call Control API."""

    def __init__(self, fail: tuple[str, ...] = ()) -> None:
        self.requests: list[tuple[str, dict]] = []
        self._fail = fail

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = json.loads(request.content) if request.content else {}
        self.requests.append((path, body))
        if any(path.endswith(suffix) for suffix in self._fail):
            return httpx.Response(422, json={"errors": [{"code": "90018"}]})
        if path == "/v2/conferences":
            return httpx.Response(200, json={"data": {"id": "conf_1"}})
        if path == "/v2/calls":
            return httpx.Response(200, json={"data": {"call_control_id": "cc_ai"}})
        return httpx.Response(200, json={"data": {}})

    @property
    def paths(self) -> list[str]:
        return [path for path, _ in self.requests]

    def body(self, path: str) -> dict:
        return next(body for p, body in self.requests if p == path)


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    return _env(tmp_path, monkeypatch)


def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail: tuple[str, ...] = ()):
    app = build_isolated_app(
        tmp_path / "flow.db", telnyx_webhook_verify=False, public_base_url=BASE
    )
    telnyx = Telnyx(fail)
    adapter = TelnyxSipTelephony(
        api_key="KEY_test",
        connection_id="c1",
        from_number="+18089005823",
        sip_uri="sip:test@sip.x.ai",
        public_base_url=BASE,
        allowed=frozenset(),
        http=httpx.Client(transport=httpx.MockTransport(telnyx)),
    )
    monkeypatch.setattr(app.state.container, "telephony", adapter)
    _seed(app)
    return app, telnyx, TestClient(app)


def _seed(app, **call: object) -> None:
    rows = [
        CaseRow(
            id="case_x",
            state="CALL_IN_PROGRESS",
            channel="relay",
            finchnode_patient_id="fn_pat_demo_001",
            payer_name="Acme Health Plan",
            payer_phone="+15555550100",
            patient_display_name="Chi716 Greenfelder433",
        ),
        ClaimRow(
            id="clm_x",
            case_id="case_x",
            external_claim_id="CLM-X-001",
            date_of_service=date(2026, 1, 1),
            total_billed_cents=100,
            source="finchnode",
            received_at=datetime.now(UTC),
        ),
        DisputeRow(
            id="dsp_x",
            case_id="case_x",
            claim_id="clm_x",
            status="approved",
            items=[],
            total_disputed_cents=0,
            letter_text="Demo dispute letter.",
            patient_authorization_id="auth_x",
        ),
        CallRow(
            id="call_x",
            case_id="case_x",
            dispute_id="dsp_x",
            status="in_progress",
            provider_call_ref="cc_insurer",
            turns=[],
            **call,
        ),
    ]
    for row in rows:
        with app.state.container.session_scope() as db:
            db.add(row)


def _post(client: TestClient, body: bytes) -> dict:
    return client.post("/voice/telnyx/webhook", content=body).json()


def _call(app) -> CallRow:
    with app.state.container.session_scope() as db:
        call = db.get(CallRow, "call_x")
        db.expunge(call)
        return call


def _notes(app) -> list[str]:
    with app.state.container.session_scope() as db:
        return [n.text for n in db.query(NotificationRow).order_by(NotificationRow.created_at)]


def _answer(client: TestClient) -> None:
    _post(
        client,
        _event(
            "call.answered",
            to="+15555550100",
            call_control_id="cc_insurer",
            client_state=to_client_state("call_x"),
        ),
    )


def _human_detected(client: TestClient, result: str = "human") -> None:
    _post(
        client,
        _event(
            "call.machine.detection.ended",
            call_control_id="cc_insurer",
            client_state=to_client_state("call_x"),
            result=result,
        ),
    )


def _store_recording(app) -> None:
    with app.state.container.session_scope() as db:
        case = db.get(CaseRow, "case_x")
        recording_service.store_authorization(db, case, b"ID3fake-mp3", "audio/mpeg", actor="t")


# ------------------------------------------------------------------ call start


def test_a_person_answering_connects_the_ai_through_a_conference(env) -> None:
    app, telnyx, client = env
    _answer(client)
    assert telnyx.paths == []  # waiting for the voicemail check
    _human_detected(client)
    assert telnyx.paths == ["/v2/conferences", "/v2/calls"]
    conference = telnyx.body("/v2/conferences")
    assert conference["call_control_id"] == "cc_insurer"
    assert conference["beep_enabled"] == "never"
    ai_dial = telnyx.body("/v2/calls")
    assert ai_dial["to"] == "sip:test@sip.x.ai"
    assert ai_dial["client_state"] == ai_client_state("call_x")
    call = _call(app)
    assert call.conference_ref == "conf_1" and call.ai_leg_ref == "cc_ai"
    assert call.media_phase == "connecting_assistant"
    assert any("picked up. I'm talking to them now" in text for text in _notes(app))


def test_the_recording_plays_first_and_exactly_once(env) -> None:
    app, telnyx, client = env
    _store_recording(app)
    _answer(client)
    _human_detected(client)
    _human_detected(client)  # duplicate delivery
    starts = [body for path, body in telnyx.requests if path.endswith("/playback_start")]
    assert len(starts) == 1
    url = starts[0]["audio_url"]
    assert url.startswith(f"{BASE}/voice/recordings/") and starts[0]["audio_type"] == "mp3"
    assert "/v2/conferences" not in telnyx.paths  # the AI waits for the recording
    # Telnyx fetches the audio while the call is live.
    fetched = client.get(url.removeprefix(BASE))
    assert fetched.status_code == 200 and fetched.content == b"ID3fake-mp3"
    ended = _event(
        "call.playback.ended",
        call_control_id="cc_insurer",
        client_state=to_client_state("call_x"),
        status="completed",
    )
    _post(client, ended)
    _post(client, ended)  # duplicate: still one conference
    assert telnyx.paths.count("/v2/conferences") == 1
    assert any(t["text"] == "Played the member's recorded authorization." for t in _call(app).turns)
    assert any("playing your recorded authorization" in text for text in _notes(app))


def test_a_recording_that_fails_to_play_still_connects_the_ai(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, telnyx, client = _env(tmp_path, monkeypatch, fail=("/playback_start",))
    _store_recording(app)
    _answer(client)
    _human_detected(client)
    assert "/v2/conferences" in telnyx.paths
    assert any("did not play" in t["text"] for t in _call(app).turns)


def test_voicemail_hangs_up_and_lets_the_patient_retry(env) -> None:
    app, telnyx, client = env
    _store_recording(app)
    _answer(client)
    _human_detected(client, result="machine")
    assert telnyx.paths == ["/v2/calls/cc_insurer/actions/hangup"]  # no recording, no AI
    call = _call(app)
    assert call.end_reason == "voicemail" and call.ended_at is not None
    with app.state.container.session_scope() as db:
        assert db.get(CaseRow, "case_x").state == "APPROVED"
    assert any("went to voicemail" in text and 'Text "call"' in text for text in _notes(app))


def test_the_ai_greets_only_after_joining_the_conference(env, monkeypatch) -> None:
    app, telnyx, client = env
    spoken: list[str] = []
    monkeypatch.setattr(app.state.container.voice, "start_speaking", spoken.append)
    _answer(client)
    _human_detected(client)
    assert spoken == []
    answered = _event(
        "call.answered",
        to="sip:test@sip.x.ai",
        call_control_id="cc_ai",
        client_state=ai_client_state("call_x"),
    )
    _post(client, answered)
    _post(client, answered)  # duplicate
    joins = [body for path, body in telnyx.requests if path.endswith("/actions/join")]
    assert joins == [
        {"call_control_id": "cc_ai", "beep_enabled": "never", "end_conference_on_exit": False}
    ]
    assert _call(app).media_phase == "assistant_connected"
    assert spoken == ["call_x"]


def test_the_ai_leg_dropping_ends_the_whole_call(env) -> None:
    app, telnyx, client = env
    _answer(client)
    _human_detected(client)
    _post(
        client,
        _event("call.hangup", call_control_id="cc_ai", client_state=ai_client_state("call_x")),
    )
    assert "/v2/calls/cc_insurer/actions/hangup" in telnyx.paths  # the insurer is not stranded
    assert _call(app).end_reason.startswith("assistant unavailable")
    assert any("voice connection failed" in text for text in _notes(app))


def test_no_conference_falls_back_to_a_direct_transfer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, telnyx, client = _env(tmp_path, monkeypatch, fail=("/v2/conferences",))
    _answer(client)
    _human_detected(client)
    assert telnyx.paths[-1] == "/v2/calls/cc_insurer/actions/transfer"
    assert _call(app).media_phase == "assistant_transferred"


def test_a_stuck_voicemail_check_moves_on(env) -> None:
    app, telnyx, client = env
    _answer(client)
    later = datetime.now(UTC) + timedelta(seconds=30)
    assert call_flow.expire_media_steps(app.state.container, now=later) == 1
    assert "/v2/conferences" in telnyx.paths


# ------------------------------------------------------------------ patient leg


def _paused_with_conference(app, **call: object) -> None:
    with app.state.container.session_scope() as db:
        db.get(CaseRow, "case_x").state = "HUMAN_REQUIRED"
        row = db.get(CallRow, "call_x")
        row.status = "paused_for_human"
        row.conference_ref = "conf_1"
        row.ai_leg_ref = "cc_ai"
        for key, value in call.items():
            setattr(row, key, value)


def test_the_patient_joins_the_same_call_and_the_ai_is_held(env) -> None:
    app, telnyx, client = env
    _paused_with_conference(app, merge_phase="patient_being_connected")
    _post(
        client,
        _event(
            "call.answered", call_control_id="cc_human", client_state=human_client_state("call_x")
        ),
    )
    hold, join = telnyx.requests
    assert hold[0] == "/v2/conferences/conf_1/actions/hold"
    assert hold[1]["call_control_ids"] == ["cc_ai"]
    assert hold[1]["audio_url"] == f"{BASE}/voice/media/silence.wav"
    assert join[0] == "/v2/conferences/conf_1/actions/join"
    assert join[1]["call_control_id"] == "cc_human"
    assert _call(app).merge_phase == "patient_connected_with_human"
    assert client.get("/voice/media/silence.wav").headers["content-type"] == "audio/wav"


def test_a_late_patient_answer_is_hung_up(env) -> None:
    app, telnyx, client = env
    _paused_with_conference(app, merge_phase="merge_failed")
    _post(
        client,
        _event(
            "call.answered", call_control_id="cc_late", client_state=human_client_state("call_x")
        ),
    )
    assert telnyx.paths == ["/v2/calls/cc_late/actions/hangup"]


def test_a_patient_who_does_not_pick_up_does_not_drop_the_insurer(env, monkeypatch) -> None:
    app, telnyx, client = env
    resumed: list[str] = []
    monkeypatch.setattr(
        app.state.container.voice,
        "resume_session",
        lambda call_id, handler, note="verified": resumed.append(note),
    )
    _paused_with_conference(app, merge_phase="patient_being_connected", human_call_ref="cc_human")
    _post(
        client,
        _event(
            "call.hangup",
            call_control_id="cc_human",
            client_state=human_client_state("call_x"),
            hangup_cause="timeout",
        ),
    )
    call = _call(app)
    assert call.ended_at is None and call.status == "in_progress"
    assert call.merge_phase == "merge_failed"
    assert "/v2/calls/cc_insurer/actions/hangup" not in telnyx.paths
    assert "/v2/conferences/conf_1/actions/unhold" in telnyx.paths
    assert resumed == ["unreachable"]
    assert any("you didn't pick up" in text for text in _notes(app))


# ------------------------------------------------------------------ recording + names


def test_the_recording_url_stops_working_after_the_call(env) -> None:
    app, _, client = env
    _store_recording(app)
    with app.state.container.session_scope() as db:
        url = recording_service.playback_url(db, "case_x", BASE)
    path = url.removeprefix(BASE)
    assert client.get(path).status_code == 200
    with app.state.container.session_scope() as db:
        db.get(CallRow, "call_x").ended_at = datetime.now(UTC)
    assert client.get(path).status_code == 404
    assert client.get("/voice/recordings/not-a-real-token.mp3").status_code == 404


def test_a_new_call_invalidates_the_previous_recording_url(env) -> None:
    app, _, client = env
    _store_recording(app)
    with app.state.container.session_scope() as db:
        first = recording_service.playback_url(db, "case_x", BASE)
    with app.state.container.session_scope() as db:
        recording_service.playback_url(db, "case_x", BASE)
    assert client.get(first.removeprefix(BASE)).status_code == 404


def test_relay_uploads_the_authorization_memo(tmp_path: Path, monkeypatch) -> None:
    import base64

    from app.demo_support import create_demo_visit, service_headers
    from claimshield_contracts.enums import ServiceName

    app = build_isolated_app(tmp_path / "upload.db")
    client = TestClient(app)
    relay = service_headers(app.state.container.settings, ServiceName.RELAY)
    case_id = create_demo_visit(client, relay)["case_id"]
    path = f"/cases/{case_id}/authorization-recording"
    fake_mp3 = base64.b64encode(b"ID3" + b"\x00" * 64).decode()
    mp3 = {"audio_base64": fake_mp3, "mime_type": "audio/mpeg"}
    stored = client.put(path, json=mp3, headers=relay)
    assert stored.status_code == 200 and stored.json()["stored"] is True
    # A voice memo (AAC) needs ffmpeg; without it the upload fails clearly.
    monkeypatch.setattr(recording_service.shutil, "which", lambda _: None)
    memo = {**mp3, "mime_type": "audio/mp4"}
    missing = client.put(path, json=memo, headers=relay)
    assert missing.json()["detail"]["code"] == "recording_conversion_unavailable"
    assert client.put(path, json=mp3, headers={}).status_code == 401


@pytest.mark.skipif(
    __import__("shutil").which("ffmpeg") is None, reason="ffmpeg is not installed here"
)
def test_a_voice_memo_is_converted_to_mp3() -> None:
    import subprocess
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "memo.m4a"
        subprocess.run(
            [
                "ffmpeg",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "sine=duration=1",
                "-c:a",
                "aac",
                str(target),
            ],
            check=True,
        )
        mp3 = recording_service.to_mp3(target.read_bytes(), "audio/mp4")
    assert mp3[:3] == b"ID3" or mp3[:2] == b"\xff\xfb"


def test_names_from_synthetic_records_are_spoken_without_digits() -> None:
    assert spoken_name("Chi716 Greenfelder433") == "Chi Greenfelder"
    assert spoken_name("Mrs. Ana María O'Neil") == "Mrs. Ana María O'Neil"
    assert spoken_name("123") is None
    assert spoken_name(None) is None
