"""Task 15: the docs-verified xAI SIP attach flow.

xAI's realtime voice is SIP-inbound: the carrier (Telnyx) transfers the insurer
leg to ``sip:{number}@sip.voice.x.ai``; xAI then posts a signed
``realtime.call.incoming`` webhook with ``data.call_id``; Core verifies the
signature and attaches the realtime WS with ``?call_id=``. The WS must NOT
start before that webhook (adapter.needs_sip_attach = True).
"""

import base64
import hashlib
import hmac
import json
import time

from fastapi.testclient import TestClient

from app.adapters.xai_voice.sip_webhook import (
    WEBHOOK_ID_HEADER,
    WEBHOOK_SIGNATURE_HEADER,
    WEBHOOK_TIMESTAMP_HEADER,
)
from app.demo_support import (
    APPROVAL_TEXT,
    answer_open_questions,
    build_isolated_app,
    create_demo_visit,
    service_headers,
)
from claimshield_contracts.enums import ServiceName

SECRET = "whsec_test"


def _ready(tmp_path):
    app = build_isolated_app(
        tmp_path / "sip.db",
        human_handoff_phone="+15555550199",
        telephony_allowed_numbers="+12487208903,+15555550199",
        xai_webhook_signing_secret=SECRET,
    )
    # Simulate live-voice attach mode: the WS session must wait for xAI's webhook.
    app.state.container.voice.needs_sip_attach = True
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
        f"/disputes/{prepared['dispute']['dispute_id']}/approve",
        json={"channel": "relay", "authorization_text": APPROVAL_TEXT, "authorize_call": True},
        headers=relay,
    )
    return app, client, relay, setup


def _webhook(core_call_id: str, xai_call_id: str) -> dict[str, str]:
    body = json.dumps(
        {
            "object": "event",
            "id": "evt_1",
            "type": "realtime.call.incoming",
            "data": {
                "call_id": xai_call_id,
                "sip_headers": [{"name": "X-Amyna-Call-Id", "value": core_call_id}],
                "metadata": {},
            },
        }
    )
    timestamp = str(int(time.time()))
    webhook_id = "evt_1"
    signed = f"{webhook_id}.{timestamp}.{body}"
    signature = base64.b64encode(
        hmac.new(SECRET.encode(), signed.encode(), hashlib.sha256).digest()
    )
    return {
        WEBHOOK_ID_HEADER: webhook_id,
        WEBHOOK_TIMESTAMP_HEADER: timestamp,
        WEBHOOK_SIGNATURE_HEADER: f"v1,{signature.decode()}",
        "Content-Type": "application/json",
    }, body


def test_sip_attach_waits_for_the_signed_webhook(tmp_path) -> None:  # noqa: ANN001
    app, client, relay, setup = _ready(tmp_path)
    call = client.post("/voice/start", json={"case_id": setup["case_id"]}, headers=relay).json()
    assert call["status"] == "in_progress"
    # The mock session only runs once we attach (needs_sip_attach waited).
    with app.state.container.session_scope() as db:
        from app.models_db import CallRow

        assert db.get(CallRow, call["call_id"]).xai_call_ref is None

    headers, body = _webhook(call["call_id"], "call_xai_1")
    response = client.post("/voice/xai/webhook", content=body, headers=headers)
    assert response.json() == {"received": True}
    # The mock session now runs to the SSN handoff (its scripted verification).
    call_after = client.get(f"/voice/{call['call_id']}", headers=relay).json()
    assert call_after["status"] == "paused_for_human"
    with app.state.container.session_scope() as db:
        from app.models_db import CallRow

        assert db.get(CallRow, call["call_id"]).xai_call_ref == "call_xai_1"

    # Duplicate webhook is idempotent: same call id, still one human merge.
    again = client.post("/voice/xai/webhook", content=body, headers=headers)
    assert again.json() == {"received": True}
    assert app.state.container.telephony.human_dialed == [call["call_id"]]


def test_bad_signature_is_rejected(tmp_path) -> None:  # noqa: ANN001
    app, client, relay, setup = _ready(tmp_path)
    call = client.post("/voice/start", json={"case_id": setup["case_id"]}, headers=relay).json()
    headers, body = _webhook(call["call_id"], "call_xai_2")
    headers[WEBHOOK_SIGNATURE_HEADER] = "v1,invalidsignature"
    assert client.post("/voice/xai/webhook", content=body, headers=headers).status_code == 401
