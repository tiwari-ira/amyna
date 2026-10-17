"""[1.4] AGENT_WORK_INLINE=false: agent work runs as durable jobs, not in-request.

The mock/demo flow stays synchronous (inline), but staging/prod must answer
202 pending and let the jobs worker run the pipeline. These tests exercise the
async path end to end: enqueue -> drain -> findings/dispute recorded -> the
repeat POST is a completed no-op.
"""

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from app.demo_support import (
    answer_open_questions,
    build_isolated_app,
    create_demo_visit,
    service_headers,
)
from app.services import job_service
from claimshield_contracts.enums import ServiceName


def _async_app(tmp_path: Path) -> TestClient:
    app = build_isolated_app(tmp_path / "async.db", inline_agent_work=False)
    return TestClient(app)


def _analyzed_case(client: TestClient, relay: dict[str, str]) -> dict[str, Any]:
    setup = create_demo_visit(client, relay)
    # Async mode: the visit-evidence extraction is a job; run it before the bill.
    job_service.drain(client.app.state.container)
    claim = client.post(
        "/claims",
        json={
            "case_id": setup["case_id"],
            "source": "finchnode",
            "finchnode_claim_id": "CLM-2026-0915-001",
        },
        headers=relay,
    ).json()
    return {**setup, "claim": claim}


def test_analyze_returns_202_pending_then_job_completes(tmp_path: Path) -> None:
    client = _async_app(tmp_path)
    container = client.app.state.container
    relay = service_headers(container.settings, ServiceName.RELAY)
    setup = _analyzed_case(client, relay)

    first = client.post(f"/claims/{setup['claim']['claim_id']}/analyze", headers=relay)
    assert first.status_code == 202
    assert first.json()["status"] == "pending"

    # The worker (compose `jobs` service / job_worker.py) runs the pipeline.
    assert job_service.drain(container) == {"ran": 1, "failed": 0}
    case = client.get(f"/cases/{setup['case_id']}", headers=relay).json()
    assert case["state"] == "AWAITING_PATIENT_INPUT"
    assert len(case["findings"]) == 3

    # Idempotent: the repeat POST reports the stored analysis, no new job.
    second = client.post(f"/claims/{setup['claim']['claim_id']}/analyze", headers=relay)
    assert second.status_code == 200
    assert second.json()["status"] == "completed"
    assert job_service.drain(container) == {"ran": 0, "failed": 0}


def test_dispute_preparation_also_runs_as_a_job(tmp_path: Path) -> None:
    client = _async_app(tmp_path)
    container = client.app.state.container
    relay = service_headers(container.settings, ServiceName.RELAY)
    setup = _analyzed_case(client, relay)

    analysis = client.post(f"/claims/{setup['claim']['claim_id']}/analyze", headers=relay)
    assert analysis.status_code == 202
    job_service.drain(container)
    answer_open_questions(client, relay, setup["case_id"])

    prepared = client.post("/disputes", json={"case_id": setup["case_id"]}, headers=relay)
    assert prepared.status_code == 202
    assert prepared.json()["status"] == "pending"
    assert job_service.drain(container) == {"ran": 1, "failed": 0}
    case = client.get(f"/cases/{setup['case_id']}", headers=relay).json()
    assert case["state"] == "AWAITING_APPROVAL"
    assert case["dispute"] is not None and case["dispute"]["approved_at"] is None


def test_inline_mode_stays_synchronous(client: TestClient, analyzed_case: dict) -> None:
    """Dev/test default: the same request carries the analysis inline."""
    assert analyzed_case["analysis"]["status"] == "completed"
    assert len(analyzed_case["analysis"]["findings"]) == 3
