"""Failure paths: consent revocation, retries, cross-case ids, PHI in DB errors, races, voice."""

from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app.demo_support import (
    APPROVAL_TEXT,
    DATA_CONSENT,
    RECORDING_CONSENT,
    answer_open_questions,
    create_demo_visit,
)
from app.errors import UpstreamError
from app.models_db import AuditRow, CaseRow, VisitRow
from app.services import workflows
from claimshield_contracts.demo import demo_patient, demo_visit_complete, load_demo_scenario

CLAIM = {"source": "finchnode", "finchnode_claim_id": "CLM-2026-0915-001"}
APPROVAL = {"channel": "relay", "authorization_text": APPROVAL_TEXT, "authorize_call": True}


def _fail(*_: object, **__: object) -> None:
    raise RuntimeError("provider socket closed")


def _case(client: TestClient, headers: dict[str, str], case_id: str) -> dict[str, Any]:
    return client.get(f"/cases/{case_id}", headers=headers).json()


# --------------------------------------------------------------------------- consent


def test_revoked_consent_blocks_record_access(client: TestClient, relay: dict[str, str]) -> None:
    case_id = create_demo_visit(client, relay)["case_id"]
    revoke = {
        "consent_type": "health_data_access",
        "granted": False,
        "consent_text": DATA_CONSENT,
        "channel": "relay",
    }
    assert client.post(f"/cases/{case_id}/consents", json=revoke, headers=relay).is_success
    response = client.post("/claims", json={"case_id": case_id, **CLAIM}, headers=relay)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "consent_required"


def test_revoked_recording_consent_blocks_new_visits(
    client: TestClient, relay: dict[str, str]
) -> None:
    case_id = client.post(
        "/cases", json={"patient": demo_patient().model_dump(), "channel": "relay"}, headers=relay
    ).json()["case_id"]
    consent = {"consent_type": "recording", "consent_text": RECORDING_CONSENT, "channel": "relay"}
    granted = client.post(f"/cases/{case_id}/consents", json=consent, headers=relay).json()
    client.post(f"/cases/{case_id}/consents", json={**consent, "granted": False}, headers=relay)
    response = client.post(
        "/visits",
        json={"case_id": case_id, "recording_consent_id": granted["consent_id"]},
        headers=relay,
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "consent_required"


# --------------------------------------------------------------------------- retries


def test_visit_completion_retries_a_failed_extraction(
    app: FastAPI, client: TestClient, relay: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    case_id = client.post(
        "/cases", json={"patient": demo_patient().model_dump(), "channel": "relay"}, headers=relay
    ).json()["case_id"]
    consent = client.post(
        f"/cases/{case_id}/consents",
        json={"consent_type": "recording", "consent_text": RECORDING_CONSENT, "channel": "relay"},
        headers=relay,
    ).json()
    visit = client.post(
        "/visits",
        json={
            "case_id": case_id,
            "recording_consent_id": consent["consent_id"],
            **load_demo_scenario()["visit"],
        },
        headers=relay,
    ).json()
    bridge = app.state.container.fetch_bridge
    extract = bridge.extract_visit_evidence
    attempts: list[str] = []

    def flaky(case: str, visit_id: str) -> Any:
        attempts.append(visit_id)
        if len(attempts) == 1:
            raise UpstreamError("agents unavailable")
        return extract(case, visit_id)

    monkeypatch.setattr(bridge, "extract_visit_evidence", flaky)
    body = demo_visit_complete().model_dump(mode="json")
    url = f"/visits/{visit['visit_id']}/complete"
    assert client.post(url, json=body, headers=relay).status_code == 502

    retry = client.post(url, json=body, headers=relay)
    assert retry.status_code == 200 and retry.json()["case_state"] == "EVIDENCE_EXTRACTED"
    evidence = {e["evidence_id"] for e in retry.json()["evidence"]}
    again = client.post(url, json=body, headers=relay).json()  # a third call changes nothing
    assert evidence and {e["evidence_id"] for e in again["evidence"]} == evidence
    assert len(attempts) == 2


# --------------------------------------------------------------------------- ids and PHI


def test_answers_containing_an_ssn_are_rejected_without_echo(
    client: TestClient, relay: dict[str, str], analyzed_case: dict[str, Any]
) -> None:
    question = analyzed_case["analysis"]["next_question"]["intervention_id"]
    response = client.post(
        f"/human-intervention/{question}/resolve",
        json={"choice": "123-45-6789", "channel": "relay"},
        headers=relay,
    )
    assert response.status_code == 422 and "123-45-6789" not in response.text


def test_interventions_cannot_reference_another_cases_finding(
    client: TestClient, relay: dict[str, str], analyzed_case: dict[str, Any]
) -> None:
    other_finding = analyzed_case["analysis"]["findings"][0]["finding_id"]
    second_case = create_demo_visit(client, relay)["case_id"]
    response = client.post(
        "/human-intervention",
        json={
            "case_id": second_case,
            "reason": "patient_question",
            "prompt": "Can you confirm this charge?",
            "finding_id": other_finding,
        },
        headers=relay,
    )
    assert response.status_code == 422


def test_cases_are_reused_per_claim_never_across_claims(
    client: TestClient, fetch: dict[str, str]
) -> None:
    def find(claim: str) -> dict[str, Any]:
        body = {"finchnode_patient_id": "fn_pat_demo_001", "channel": "asi1", "external_ref": claim}
        return client.post("/internal/tools/find_or_create_case", json=body, headers=fetch).json()

    first = find("CLM-2026-0915-001")
    assert first["created"] and not find("CLM-2026-0915-001")["created"]
    other = find("CLM-2026-0999-999")
    assert other["created"] and other["case"]["case_id"] != first["case"]["case_id"]


def test_db_errors_hide_bound_parameters(
    app: FastAPI, client: TestClient, relay: dict[str, str]
) -> None:
    setup = create_demo_visit(client, relay)
    recording_consent = next(
        c["consent_id"]
        for c in _case(client, relay, setup["case_id"])["consents"]
        if c["consent_type"] == "recording"
    )
    with pytest.raises(IntegrityError) as excinfo, app.state.container.session_scope() as db:
        db.add(  # a second visit for the case violates the unique constraint
            VisitRow(
                id="vis_duplicate",
                case_id=setup["case_id"],
                recording_consent_id=recording_consent,
                recording_ref="member ssn 123-45-6789",
                status="recorded",
            )
        )
    assert "123-45-6789" not in str(excinfo.value)


def test_integrity_and_stale_errors_are_409s_without_sql(
    client: TestClient,
    relay: dict[str, str],
    analyzed_case: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = f"/claims/{analyzed_case['claim']['claim_id']}/analyze"
    for error, code in (
        (
            IntegrityError("INSERT INTO findings ...", {"quote": "ssn 123-45-6789"}, None),
            "conflict",
        ),
        (StaleDataError("UPDATE cases ..."), "concurrent_update"),
    ):

        def boom(*_: object, exc: Exception = error, **__: object) -> None:
            raise exc

        monkeypatch.setattr(workflows, "analyze_claim", boom)
        response = client.post(url, headers=relay)
        assert response.status_code == 409 and response.json()["detail"]["code"] == code
        assert "INSERT" not in response.text and "123-45-6789" not in response.text


# --------------------------------------------------------------------------- concurrency


def test_concurrent_case_writes_conflict_instead_of_overwriting(
    app: FastAPI, client: TestClient, relay: dict[str, str]
) -> None:
    case_id = create_demo_visit(client, relay)["case_id"]
    factory = app.state.container.session_factory
    with factory() as first, factory() as second:
        mine, theirs = first.get(CaseRow, case_id), second.get(CaseRow, case_id)
        assert mine is not None and theirs is not None
        mine.state = "FAILED"
        first.commit()
        theirs.state = "RESOLVED"
        with pytest.raises(StaleDataError):
            second.commit()
    assert _case(client, relay, case_id)["state"] == "FAILED"


def test_a_second_call_start_is_refused(
    client: TestClient, relay: dict[str, str], awaiting_approval: dict[str, Any]
) -> None:
    dispute_id = awaiting_approval["dispute"]["dispute_id"]
    client.post(f"/disputes/{dispute_id}/approve", json=APPROVAL, headers=relay)
    body = {"case_id": awaiting_approval["case_id"]}
    assert client.post("/voice/start", json=body, headers=relay).is_success
    second = client.post("/voice/start", json=body, headers=relay)
    assert second.status_code == 409
    assert len(_case(client, relay, awaiting_approval["case_id"])["calls"]) == 1


# --------------------------------------------------------------------------- voice


def test_voice_session_failure_hangs_up_and_closes_the_case(
    app: FastAPI,
    client: TestClient,
    relay: dict[str, str],
    awaiting_approval: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    container = app.state.container
    client.post(
        f"/disputes/{awaiting_approval['dispute']['dispute_id']}/approve",
        json=APPROVAL,
        headers=relay,
    )
    monkeypatch.setattr(container.voice, "start_session", _fail)
    response = client.post(
        "/voice/start", json={"case_id": awaiting_approval["case_id"]}, headers=relay
    )
    assert response.status_code == 502
    case = _case(client, relay, awaiting_approval["case_id"])
    assert case["state"] == "FAILED" and case["calls"][0]["status"] == "failed"
    assert container.telephony.hung_up == [case["calls"][0]["provider_call_ref"]]


def test_lost_voice_session_on_resume_closes_the_call(
    app: FastAPI,
    client: TestClient,
    relay: dict[str, str],
    awaiting_approval: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case_id = awaiting_approval["case_id"]
    client.post(
        f"/disputes/{awaiting_approval['dispute']['dispute_id']}/approve",
        json=APPROVAL,
        headers=relay,
    )
    call = client.post("/voice/start", json={"case_id": case_id}, headers=relay).json()
    assert call["status"] == "paused_for_human"
    monkeypatch.setattr(app.state.container.voice, "resume_session", _fail)
    response = client.post(
        f"/human-intervention/{call['open_intervention_id']}/resolve",
        json={"choice": "DONE", "channel": "relay"},
        headers=relay,
    )
    assert response.status_code == 502
    case = _case(client, relay, case_id)
    assert case["state"] == "FAILED" and case["calls"][0]["status"] == "failed"


# --------------------------------------------------------------------------- upstream and audit


class _FinchNodeDown:
    def get_patient(self, patient_id: str) -> None:
        raise httpx.ConnectError("FinchNode unreachable")

    def search(self, resource_type: str, patient_id: str) -> list[dict[str, Any]]:
        raise httpx.ConnectError("FinchNode unreachable")


def test_finchnode_outage_is_a_502_and_keeps_the_consent(
    app: FastAPI, client: TestClient, relay: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app.state.container.finchnode, "_client", _FinchNodeDown())
    case_id = create_demo_visit(client, relay)["case_id"]  # enrichment fails, consent stays
    consents = _case(client, relay, case_id)["consents"]
    assert any(c["consent_type"] == "health_data_access" and c["granted"] for c in consents)
    response = client.post("/claims", json={"case_id": case_id, **CLAIM}, headers=relay)
    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "finchnode_unavailable"


def test_failed_and_forbidden_tool_calls_are_audited(
    app: FastAPI, client: TestClient, fetch: dict[str, str], analyzed_case: dict[str, Any]
) -> None:
    run = {"agent": "visit_agent", "request_id": "req_audit"}
    forbidden = client.post(
        "/internal/tools/get_patient_procedures",
        json={"case_id": analyzed_case["case_id"], "agent_run": run},
        headers=fetch,
    )
    missing = client.post(
        "/internal/tools/get_case",
        json={"case_id": "case_missing", "agent_run": run},
        headers=fetch,
    )
    assert forbidden.status_code == 403 and missing.status_code == 404
    with app.state.container.session_scope() as db:
        rows = db.scalars(select(AuditRow).where(AuditRow.action.like("tool.%.failed"))).all()
        failures = {(row.action, row.details["code"]) for row in rows}
    assert ("tool.get_patient_procedures.failed", "forbidden") in failures
    assert ("tool.get_case.failed", "not_found") in failures


# --------------------------------------------------------------------------- malformed agent output

CLAIM_AGENT_RUN = {"agent": "claim_agent", "request_id": "req_findings"}
DISPUTE_AGENT_RUN = {"agent": "dispute_agent", "request_id": "req_dispute"}
ALL_EFFECTS = {"YES": "dismissed", "NO": "confirmed", "NOT_SURE": "needs_clarification"}


@pytest.mark.parametrize("defect", ["missing_answer_effect", "unknown_assessed_line"])
def test_malformed_findings_are_rejected_before_anything_is_written(
    client: TestClient, relay: dict[str, str], fetch: dict[str, str], defect: str
) -> None:
    case_id = create_demo_visit(client, relay)["case_id"]
    claim = client.post("/claims", json={"case_id": case_id, **CLAIM}, headers=relay).json()
    line = claim["lines"][0]
    effects = dict(ALL_EFFECTS)
    assessed_line = line["line_id"]
    if defect == "missing_answer_effect":
        del effects["NOT_SURE"]
    else:
        assessed_line = "line_from_another_claim"
    finding = {
        "finding_id": "fnd_regression01",
        "case_id": case_id,
        "claim_id": claim["claim_id"],
        "claim_line_id": line["line_id"],
        "line_number": line["line_number"],
        "issue_type": "unsupported_charge",
        "title": "Charge not supported by the visit record",
        "explanation": "Nothing in the visit evidence supports this line.",
        "amount_cents": line["billed_cents"],
        "confidence": 0.6,
        "human_confirmation_required": True,
        "confirmation": {"question": "Did you receive this service?", "effects": effects},
        "status": "open",
        "recommended_action": "remove_charge",
    }
    assessment = {
        "claim_line_id": assessed_line,
        "line_number": line["line_number"],
        "status": "unsupported",
    }
    response = client.post(
        "/internal/tools/record_findings",
        json={
            "case_id": case_id,
            "claim_id": claim["claim_id"],
            "findings": [finding],
            "line_assessments": [assessment],
            "agent_run": CLAIM_AGENT_RUN,
        },
        headers=fetch,
    )
    assert response.status_code == 422
    case = _case(client, relay, case_id)
    assert case["state"] == "CLAIM_RECEIVED" and case["findings"] == []


@pytest.mark.parametrize("defect", ["wrong_amount", "duplicate_item", "wrong_total"])
def test_dispute_items_must_match_the_confirmed_findings(
    client: TestClient,
    relay: dict[str, str],
    fetch: dict[str, str],
    analyzed_case: dict[str, Any],
    defect: str,
) -> None:
    case_id = analyzed_case["case_id"]
    answer_open_questions(client, relay, case_id)
    case = _case(client, relay, case_id)
    items = [
        {
            "finding_id": f["finding_id"],
            "claim_line_id": f["claim_line_id"],
            "line_number": f["line_number"],
            "issue_type": f["issue_type"],
            "action": f["recommended_action"],
            "amount_cents": f["amount_cents"],
            "summary": f["title"],
        }
        for f in case["findings"]
        if f["status"] in ("confirmed", "needs_clarification")
    ]

    def record(dispute_items: list[dict[str, Any]], total: int) -> httpx.Response:
        return client.post(
            "/internal/tools/record_dispute",
            json={
                "case_id": case_id,
                "claim_id": case["claim"]["claim_id"],
                "items": dispute_items,
                "letter_text": "Dear claims department, please review the items below.",
                "total_disputed_cents": total,
                "agent_run": DISPUTE_AGENT_RUN,
            },
            headers=fetch,
        )

    bad = [dict(item) for item in items]
    if defect == "wrong_amount":
        bad[0]["amount_cents"] += 100
    elif defect == "duplicate_item":
        bad.append(dict(bad[0]))
    total = sum(item["amount_cents"] for item in bad) + (1 if defect == "wrong_total" else 0)
    assert record(bad, total).status_code == 422
    valid = record(items, sum(item["amount_cents"] for item in items))
    assert valid.status_code == 200, valid.text  # the same case accepts the correct package
