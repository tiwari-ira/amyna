"""Production-style FinchNode: hosted Connect, signed webhooks, claim summaries, and the
patient's own callback number for the human merge.

FinchNode itself is replaced by a stand-in that serves FinchNode's real demo records
payload (synthetic) and Connect sessions, behind the real LiveFinchNodeAdapter."""

import hashlib
import hmac
import json
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.adapters.finchnode.live import LiveFinchNodeAdapter
from app.demo_support import (
    APPROVAL_TEXT,
    answer_open_questions,
    build_isolated_app,
    create_demo_visit,
    service_headers,
)
from claimshield_contracts.enums import ServiceName
from finchnode.records import Snapshot

ROOT = Path(__file__).resolve().parents[2]
RECORDS = json.loads((ROOT / "finchnode/tests/fixtures/records_patient-demo-001.json").read_text())
SUBJECT = "u_0123456789abcdef"
SECRET = "whsec_test_finchnode"


class FakeFinchNode:
    """FinchNode sandbox stand-in: Connect sessions + one synthetic patient's records."""

    def __init__(self) -> None:
        self.sessions: dict[str, dict[str, Any]] = {}
        self.created: list[dict[str, Any]] = []
        self.invalidated: list[str | None] = []

    def snapshot(self, subject: str) -> Snapshot | None:
        return Snapshot.from_body(subject, RECORDS) if subject == SUBJECT else None

    def invalidate(self, subject: str | None = None) -> None:
        self.invalidated.append(subject)

    def create_connect_session(self, **kwargs: Any) -> dict[str, Any]:
        self.created.append(kwargs)
        session_id = f"cs_test{len(self.created)}"
        self.sessions[session_id] = {
            "id": session_id,
            "url": f"https://finchnode.com/connect/{session_id}",
            "status": "pending",
            "subject": None,
            "environment": "sandbox",
            "expires_at": "2026-10-11T00:00:00Z",
        }
        return dict(self.sessions[session_id])

    def get_connect_session(self, session_id: str) -> dict[str, Any]:
        return dict(self.sessions[session_id])


def signed(event: dict[str, Any], secret: str = SECRET, at: int | None = None):
    body = json.dumps(event).encode()
    stamp = str(at if at is not None else int(time.time()))
    digest = hmac.new(secret.encode(), stamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    headers = {
        "FinchNode-Signature": f"t={stamp},v1={digest}",
        "FinchNode-Event-Id": event["id"],
        "Content-Type": "application/json",
    }
    return body, headers


@pytest.fixture
def env(tmp_path: Path) -> dict[str, Any]:
    app = build_isolated_app(tmp_path / "connect.db", finchnode_webhook_secret=SECRET)
    fake = FakeFinchNode()
    app.state.container.finchnode = LiveFinchNodeAdapter(
        mode="sandbox",
        base_url="",
        api_key="ck_test_x",
        client=fake,  # type: ignore[arg-type]
    )
    client = TestClient(app)
    relay = service_headers(app.state.container.settings, ServiceName.RELAY)
    token = client.post(
        "/auth/session", json={"subject": "relay:+15550001111"}, headers=relay
    ).json()["patient_token"]
    patient = {**relay, "X-Amyna-Patient-Token": token}
    return {"app": app, "client": client, "fake": fake, "relay": relay, "patient": patient}


def _connect(env: dict[str, Any]) -> dict[str, Any]:
    response = env["client"].post("/finchnode/connect-sessions", json={}, headers=env["patient"])
    assert response.status_code == 201, response.text
    return response.json()


def _granted(session: dict[str, Any], event_id: str = "evt_granted") -> dict[str, Any]:
    return {
        "id": event_id,
        "object": "event",
        "type": "consent.granted",
        "data": {
            "subject": SUBJECT,
            "receiptId": "rcpt_test",
            "sessionId": session["session_id"],
            "externalId": session["case_id"],
            "environment": "sandbox",
            "categories": ["demographics", "encounters", "labs", "medications", "claims"],
            "missingCategories": [],
        },
    }


def _webhook(env: dict[str, Any], event: dict[str, Any], **kwargs: Any):
    body, headers = signed(event, **kwargs)
    return env["client"].post("/finchnode/webhook", content=body, headers=headers)


def test_connect_links_the_patient_when_finchnode_grants_consent(env: dict[str, Any]) -> None:
    client, patient = env["client"], env["patient"]
    session = _connect(env)
    assert session["url"].startswith("https://finchnode.com/connect/") and not session["connected"]
    [created] = env["fake"].created
    assert created["external_id"] == session["case_id"]  # opaque id, never PII
    assert "claims" in created["categories"] and created["idempotency_key"]
    case = client.get(f"/cases/{session['case_id']}", headers=patient).json()
    assert case["records_linked"] is False

    assert _webhook(env, _granted(session)).status_code == 204
    case = client.get(f"/cases/{session['case_id']}", headers=patient).json()
    assert case["records_linked"] is True
    assert case["patient"]["finchnode_patient_id"] == SUBJECT
    assert case["patient"]["display_name"] == "Morgan Rivera"
    assert case["patient"]["payer_name"] == "Example Health Plan (Synthetic)"
    assert any(c["consent_type"] == "health_data_access" and c["granted"] for c in case["consents"])
    notes = client.get(f"/cases/{session['case_id']}/notifications", headers=patient).json()
    assert notes[-1]["data"]["event"] == "finchnode_connected"
    assert _webhook(env, _granted(session)).status_code == 200  # duplicate delivery


def test_finchnode_bills_are_listed_but_need_the_itemized_bill(env: dict[str, Any]) -> None:
    client, patient = env["client"], env["patient"]
    session = _connect(env)
    _webhook(env, _granted(session))
    listed = client.get(f"/cases/{session['case_id']}/finchnode/claims", headers=patient).json()
    assert listed["complete"] is True
    assert listed["claims"] == [
        {
            "external_claim_id": "claim-demo-001",
            "provider_name": "Northstar Health System (Synthetic)",
            "payer_name": "Example Health Plan (Synthetic)",
            "date_of_service": "2026-07-18",
            "total_billed_cents": 18000,
            "itemized": False,
        }
    ]
    ingest = client.post(
        "/claims",
        json={
            "case_id": session["case_id"],
            "source": "finchnode",
            "finchnode_claim_id": "CLAIM-DEMO-001",  # any capitalization matches
        },
        headers=patient,
    )
    assert ingest.status_code == 422
    detail = ingest.json()["detail"]
    assert detail["code"] == "claim_not_itemized" and detail["total_billed_cents"] == 18000


def test_reading_the_session_back_finishes_linking_without_webhooks(env: dict[str, Any]) -> None:
    session = _connect(env)
    env["fake"].sessions[session["session_id"]].update(status="completed", subject=SUBJECT)
    read = (
        env["client"]
        .get(f"/finchnode/connect-sessions/{session['session_id']}", headers=env["patient"])
        .json()
    )
    assert read["connected"] is True
    case = env["client"].get(f"/cases/{session['case_id']}", headers=env["patient"]).json()
    assert case["records_linked"] and case["patient"]["finchnode_patient_id"] == SUBJECT


@pytest.mark.parametrize(
    "tamper",
    [
        {"secret": "whsec_wrong"},
        {"at": int(time.time()) - 3600},
    ],
)
def test_webhooks_must_be_signed_by_finchnode(env: dict[str, Any], tamper: dict[str, Any]) -> None:
    session = _connect(env)
    assert _webhook(env, _granted(session), **tamper).status_code == 401


def test_a_replayed_body_under_a_new_event_id_is_rejected(env: dict[str, Any]) -> None:
    session = _connect(env)
    body, headers = signed(_granted(session))
    headers["FinchNode-Event-Id"] = "evt_other"
    response = env["client"].post("/finchnode/webhook", content=body, headers=headers)
    assert response.status_code == 401


def test_new_records_notify_and_revocation_stops_reads(env: dict[str, Any]) -> None:
    client, patient = env["client"], env["patient"]
    session = _connect(env)
    _webhook(env, _granted(session))
    updated = {"id": "evt_updated", "type": "records.updated", "data": {"subject": SUBJECT}}
    assert _webhook(env, updated).status_code == 204
    notes = client.get(f"/cases/{session['case_id']}/notifications", headers=patient).json()
    assert notes[-1]["data"]["event"] == "finchnode_records_updated"
    assert SUBJECT in env["fake"].invalidated

    revoked = {"id": "evt_revoked", "type": "consent.revoked", "data": {"subject": SUBJECT}}
    assert _webhook(env, revoked).status_code == 204
    notes = client.get(f"/cases/{session['case_id']}/notifications", headers=patient).json()
    assert notes[-1]["data"]["event"] == "finchnode_sharing_ended"
    blocked = client.get(f"/cases/{session['case_id']}/finchnode/claims", headers=patient)
    assert blocked.status_code == 409 and blocked.json()["detail"]["code"] == "consent_required"


def test_connect_is_unavailable_with_typed_ids(tmp_path: Path) -> None:
    app = build_isolated_app(tmp_path / "mock.db")
    client = TestClient(app)
    relay = service_headers(app.state.container.settings, ServiceName.RELAY)
    response = client.post("/finchnode/connect-sessions", json={}, headers=relay)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "connect_unavailable"


# --------------------------------------------------------------------------- callback phone

APPROVAL = {"channel": "relay", "authorization_text": APPROVAL_TEXT, "authorize_call": True}


def _approved(tmp_path: Path, **settings: object):
    app = build_isolated_app(tmp_path / "phone.db", **settings)
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
    return app, client, relay, setup["case_id"]


def test_the_human_merge_dials_the_patients_own_number(tmp_path: Path) -> None:
    app, client, relay, case_id = _approved(
        tmp_path, telephony_allow_patient_phones=True, human_handoff_phone=""
    )
    case = client.put(
        f"/cases/{case_id}/callback-phone", json={"phone": "(555) 555-0123"}, headers=relay
    ).json()
    assert case["callback_phone_masked"] == "+1••••••0123"
    call = client.post("/voice/start", json={"case_id": case_id}, headers=relay).json()
    assert call["merge_phase"] == "patient_connected_with_human"
    assert app.state.container.telephony.human_numbers == ["+15555550123"]


def test_a_patient_number_outside_the_allowlist_is_never_dialed(tmp_path: Path) -> None:
    app, client, relay, case_id = _approved(tmp_path, human_handoff_phone="")
    client.put(f"/cases/{case_id}/callback-phone", json={"phone": "555-555-0123"}, headers=relay)
    call = client.post("/voice/start", json={"case_id": case_id}, headers=relay).json()
    # Nobody is dialed; the insurer waits for the patient's choice in the chat.
    assert call["merge_phase"] == "sensitive_request_detected"
    assert app.state.container.telephony.human_numbers == []
    notes = client.get(f"/cases/{case_id}/notifications", headers=relay).json()
    assert any("isn't approved for calls" in n["text"] for n in notes)


def test_invalid_callback_numbers_are_rejected(env: dict[str, Any]) -> None:
    session = _connect(env)
    response = env["client"].put(
        f"/cases/{session['case_id']}/callback-phone",
        json={"phone": "call me"},
        headers=env["patient"],
    )
    assert response.status_code == 422
