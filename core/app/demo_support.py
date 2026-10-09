"""Shared helpers for tests, the smoke run (scripts/smoke_e2e.py) and the seed.

Everything here goes through Core's public HTTP API, so the demo setup is
validated exactly like a real client's requests.
"""

import contextlib
import json
import os
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI

from claimshield_contracts.demo import demo_patient, demo_visit_complete, load_demo_scenario
from claimshield_contracts.enums import Channel, ServiceName
from claimshield_contracts.ids import new_id

from .auth import SIGNATURE_HEADER, TIMESTAMP_HEADER, sign_payload
from .config import Settings
from .db import Base
from .main import create_app

RECORDING_CONSENT = (
    "I agree that Amyna may record and transcribe this appointment for my bill review."
)
APPROVAL_TEXT = "I approve this dispute and authorize Amyna to call my insurer."
DATA_CONSENT = (
    "I allow Amyna to read my FinchNode health records (encounters, procedures, "
    "medications and claims) to review this bill."
)


# Every env var the Settings model reads. A configured developer .env (loaded
# into the process environment when fetch/agents/settings.py is imported) must
# never leak into isolated tests or the mock smoke run.
REPO_ENV_VARS = (
    "APP_ENV",
    "LOG_LEVEL",
    "DATABASE_URL",
    "RELAY_SERVICE_TOKEN",
    "FETCH_SERVICE_TOKEN",
    "VOICE_SERVICE_TOKEN",
    "CORE_WEBHOOK_SECRET",
    "CONFIRMATION_THRESHOLD",
    "NOTIFY_MODE",
    "RELAY_SERVICE_URL",
    "XAI_VOICE_MODE",
    "XAI_API_KEY",
    "XAI_REALTIME_URL",
    "XAI_VOICE_MODEL",
    "XAI_VOICE_NAME",
    "TELEPHONY_MODE",
    "TELNYX_API_KEY",
    "TELNYX_CONNECTION_ID",
    "TELNYX_PHONE_NUMBER",
    "TELNYX_PUBLIC_KEY",
    "TELNYX_WEBHOOK_VERIFY",
    "PUBLIC_BASE_URL",
    "XAI_SIP_URI",
    "TELEPHONY_ALLOWED_NUMBERS",
    "HUMAN_HANDOFF_PHONE",
    "TELNYX_API_KEY_2",
    "TELNYX_CONNECTION_ID_2",
    "TELNYX_PHONE_NUMBER_2",
    "TELNYX_API_KEY_3",
    "TELNYX_CONNECTION_ID_3",
    "TELNYX_PHONE_NUMBER_3",
    "FETCH_MODE",
    "ORCHESTRATOR_URL",
    "FINCHNODE_MODE",
    "FINCHNODE_API_KEY",
    "FINCHNODE_BASE_URL",
    "EXTRACTION_MODE",
    "OLLAMA_URL",
    "OLLAMA_MODEL",
)


@contextlib.contextmanager
def clean_env() -> Iterator[None]:
    """Temporarily pop every REPO_ENV_VARS variable; restore it afterwards.

    pydantic Settings read the process environment even with ``_env_file=None``,
    so a developer's configured .env would otherwise redirect isolated runs
    (e.g. FINCHNODE_MODE=sandbox sends mock tests at the live API).
    """
    saved = {name: os.environ.pop(name) for name in REPO_ENV_VARS if name in os.environ}
    try:
        yield
    finally:
        os.environ.update(saved)


def isolated_settings(db_path: Path, **overrides: Any) -> Settings:
    """Mock-everything settings on a private SQLite file (never reads .env or the env)."""
    with clean_env():
        return Settings(  # type: ignore[call-arg]
            _env_file=None,
            app_env="test",
            database_url=f"sqlite:///{db_path}",
            **overrides,
        )


def build_isolated_app(db_path: Path, **overrides: Any) -> FastAPI:
    """App + fresh schema (create_all; migrations are tested separately)."""
    app = create_app(isolated_settings(db_path, **overrides))
    Base.metadata.create_all(app.state.container.engine)
    return app


def service_headers(settings: Settings, service: ServiceName) -> dict[str, str]:
    return {"Authorization": f"Bearer {settings.service_tokens()[service]}"}


def _ok(response: httpx.Response) -> dict[str, Any]:
    if response.status_code >= 400:
        raise RuntimeError(f"{response.request.method} {response.request.url} -> {response.text}")
    return response.json()


def create_demo_visit(
    client: httpx.Client, headers: dict[str, str], *, data_consent: bool = True
) -> dict[str, str]:
    """Demo patient case with a recorded + transcribed visit (evidence extracted)."""
    case = _ok(
        client.post(
            "/cases",
            json={"patient": demo_patient().model_dump(), "channel": Channel.RELAY.value},
            headers=headers,
        )
    )
    case_id = case["case_id"]
    recording = _ok(
        client.post(
            f"/cases/{case_id}/consents",
            json={
                "consent_type": "recording",
                "consent_text": RECORDING_CONSENT,
                "channel": "relay",
            },
            headers=headers,
        )
    )
    if data_consent:
        _ok(
            client.post(
                f"/cases/{case_id}/consents",
                json={
                    "consent_type": "health_data_access",
                    "consent_text": DATA_CONSENT,
                    "channel": "relay",
                },
                headers=headers,
            )
        )
    visit_meta = load_demo_scenario()["visit"]
    visit = _ok(
        client.post(
            "/visits",
            json={
                "case_id": case_id,
                "recording_consent_id": recording["consent_id"],
                **visit_meta,
            },
            headers=headers,
        )
    )
    result = _ok(
        client.post(
            f"/visits/{visit['visit_id']}/complete",
            json=demo_visit_complete().model_dump(mode="json"),
            headers=headers,
        )
    )
    return {"case_id": case_id, "visit_id": visit["visit_id"], "case_state": result["case_state"]}


def answer_open_questions(
    client: httpx.Client, headers: dict[str, str], case_id: str, choice: str = "NO"
) -> None:
    """Answer every open confirmation question with the same choice (tests/demos)."""
    while True:
        case = _ok(client.get(f"/cases/{case_id}", headers=headers))
        pending = [i for i in case["interventions"] if i["status"] == "open" and i["finding_id"]]
        if not pending:
            return
        _ok(
            client.post(
                f"/human-intervention/{pending[0]['intervention_id']}/resolve",
                json={"choice": choice, "channel": "relay"},
                headers=headers,
            )
        )


# --------------------------------------------------------------------------- full mock flow


class FlowError(AssertionError):
    """A demo-flow expectation failed (message says which step)."""


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise FlowError(message)


def signed_post(
    client: httpx.Client, secret: str, path: str, payload: dict[str, Any]
) -> httpx.Response:
    """POST a JSON body signed the way the Relay service signs events to Core."""
    body = json.dumps(payload).encode()
    timestamp = str(int(time.time()))
    return client.post(
        path,
        content=body,
        headers={
            "Content-Type": "application/json",
            TIMESTAMP_HEADER: timestamp,
            SIGNATURE_HEADER: sign_payload(secret, timestamp, body),
        },
    )


def run_demo_flow(
    client: httpx.Client, settings: Settings, log: Any = lambda _message: None
) -> dict[str, Any]:
    """Drive the whole synthetic scenario through Core's public API; return a summary.

    create case -> consents -> visit -> transcript -> evidence -> claim -> analyze ->
    (call blocked) -> answers (signed webhook + REST) -> dispute -> approval ->
    call -> SSN handoff -> human resolves -> RESOLVED.
    """
    relay = service_headers(settings, ServiceName.RELAY)
    expected = load_demo_scenario()["expected"]

    setup = create_demo_visit(client, relay)
    case_id, visit_id = setup["case_id"], setup["visit_id"]
    _expect(setup["case_state"] == "EVIDENCE_EXTRACTED", f"visit evidence: {setup}")
    concepts = {
        e["concept"] for e in _ok(client.get(f"/visits/{visit_id}/evidence", headers=relay))
    }
    _expect({"mri_brain_contrast", "lorazepam"} <= concepts, f"evidence concepts {concepts}")
    _expect("anesthesia_imaging" not in concepts, "transcript must not evidence anesthesia")
    log(f"1. case {case_id}: consents, visit, transcript -> evidence {sorted(concepts)}")

    claim = _ok(
        client.post(
            "/claims",
            json={
                "case_id": case_id,
                "source": "finchnode",
                "finchnode_claim_id": "CLM-2026-0915-001",
            },
            headers=relay,
        )
    )
    _expect(len(claim["lines"]) == 6, "claim should have 6 lines")
    log(f"2. claim {claim['external_claim_id']} ingested from FinchNode (6 lines)")

    analysis = _ok(client.post(f"/claims/{claim['claim_id']}/analyze", headers=relay))
    issues = [(f["line_number"], f["issue_type"]) for f in analysis["findings"]]
    _expect(
        issues == [(f["line_number"], f["issue_type"]) for f in expected["findings"]],
        f"findings {issues}",
    )
    _expect(analysis["total_flagged_cents"] == expected["total_flagged_cents"], "flagged total")
    question = analysis["next_question"]
    _expect(question and "anesthesia" in question["prompt"], f"first question: {question}")
    log(f"3. analysis: {len(issues)} findings, first question: {question['prompt']}")

    blocked = client.post("/voice/start", json={"case_id": case_id}, headers=relay)
    _expect(blocked.status_code == 409, f"call must be blocked before approval: {blocked.text}")
    log(f"4. insurer call blocked before approval ({blocked.json()['detail']['code']})")

    webhook = signed_post(
        client,
        settings.webhook_secret(),
        "/relay/webhook",
        {
            "event_id": new_id("request"),
            "event_type": "button_reply",
            "case_id": case_id,
            "intervention_id": question["intervention_id"],
            "choice": "NO",
            "occurred_at": datetime.now(UTC).isoformat(),
        },
    )
    _expect(_ok(webhook)["status"] == "processed", f"webhook answer: {webhook.text}")
    second = _ok(client.get(f"/cases/{case_id}", headers=relay))
    pending = [i for i in second["interventions"] if i["status"] == "open"]
    _expect(len(pending) == 1, f"one question left: {pending}")
    log("5. anesthesia answered NO via signed Relay webhook")

    resolved = _ok(
        client.post(
            f"/human-intervention/{pending[0]['intervention_id']}/resolve",
            json={"choice": "NOT_SURE", "channel": "relay"},
            headers=relay,
        )
    )
    _expect(
        resolved["case_state"] == "FINDINGS_CONFIRMED", f"after answers: {resolved['case_state']}"
    )
    log("6. unclassified drug answered NOT SURE -> FINDINGS_CONFIRMED")

    prepared = _ok(client.post("/disputes", json={"case_id": case_id}, headers=relay))
    dispute = prepared["dispute"]
    _expect(
        dispute and dispute["total_disputed_cents"] == expected["total_flagged_cents"],
        "dispute total",
    )
    _expect(
        prepared["case_state"] == "AWAITING_APPROVAL", f"dispute state {prepared['case_state']}"
    )
    log(f"7. dispute {dispute['dispute_id']}: {len(dispute['items'])} items, awaiting approval")

    approved = _ok(
        client.post(
            f"/disputes/{dispute['dispute_id']}/approve",
            json={
                "channel": "relay",
                "authorization_text": APPROVAL_TEXT,
                "authorize_call": True,
            },
            headers=relay,
        )
    )
    _expect(bool(approved["patient_authorization_id"]), "authorization id stored")
    log("8. patient approved; authorization stored")

    call = _ok(client.post("/voice/start", json={"case_id": case_id}, headers=relay))
    _expect(call["status"] == "paused_for_human", f"call should pause for SSN: {call['status']}")
    paused = _ok(client.get(f"/cases/{case_id}", headers=relay))
    _expect(paused["state"] == "HUMAN_REQUIRED", f"case state {paused['state']}")
    kinds = [n["kind"] for n in _ok(client.get(f"/cases/{case_id}/notifications", headers=relay))]
    _expect("human_handoff" in kinds, "patient notified about the handoff")
    log("9. mock call: insurer asked for SSN -> HUMAN_REQUIRED, patient notified via Relay outbox")

    done = _ok(
        client.post(
            f"/human-intervention/{call['open_intervention_id']}/resolve",
            json={
                "choice": "DONE",
                "notes": "Patient verified identity directly with the representative.",
                "channel": "relay",
            },
            headers=relay,
        )
    )
    _expect(done["case_state"] == "RESOLVED", f"after handoff: {done['case_state']}")
    final = _ok(client.get(f"/cases/{case_id}", headers=relay))
    response = final["calls"][-1]["insurer_response"]
    _expect(response and response["reference_number"], "insurer response recorded")
    log(f"10. call resumed and finished: RESOLVED, reference {response['reference_number']}")
    return {
        "case_id": case_id,
        "state": final["state"],
        "findings": len(final["findings"]),
        "flagged_cents": analysis["total_flagged_cents"],
        "insurer_outcome": response["outcome"],
        "reference_number": response["reference_number"],
    }
