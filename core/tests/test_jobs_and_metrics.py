"""Tasks 28/29: durable jobs with retry/dead states + production observability."""

from datetime import UTC, datetime, timedelta

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
from app.models_db import JobRow
from app.services import job_service
from claimshield_contracts.enums import ServiceName

_MERGE_NUMBERS = "+12487208903,+15555550199"


def _merged_ready(tmp_path):  # noqa: ANN001, ANN202
    """Approved case with the human-merge phone configured (same setup as 17A tests)."""
    app = build_isolated_app(
        tmp_path / "merge.db",
        human_handoff_phone="+15555550199",
        telephony_allowed_numbers=_MERGE_NUMBERS,
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
        f"/disputes/{prepared['dispute']['dispute_id']}/approve",
        json={"channel": "relay", "authorization_text": APPROVAL_TEXT, "authorize_call": True},
        headers=relay,
    )
    return app, client, relay, setup


def test_enqueue_is_idempotent_and_audited(client: TestClient) -> None:
    container = client.app.state.container
    with container.session_scope() as db:
        first = job_service.enqueue(
            db,
            job_type="test.echo",
            payload={"n": 1},
            idempotency_key="t-echo-1",
        )
        second = job_service.enqueue(
            db,
            job_type="test.echo",
            payload={"n": 1},
            idempotency_key="t-echo-1",
        )
        assert second.id == first.id
        assert db.query(JobRow).filter_by(idempotency_key="t-echo-1").count() == 1


def test_drain_retries_with_backoff_then_succeeds(client: TestClient) -> None:
    container = client.app.state.container
    attempts: list[str] = []

    job_type = "test.flaky.success"

    @job_service.register(job_type)
    def _flaky(_container, payload) -> None:  # noqa: ANN001
        attempts.append(job_type)
        if len(attempts) < 2:
            raise RuntimeError("boom")

    with container.session_scope() as db:
        job = job_service.enqueue(
            db, job_type=job_type, payload={}, idempotency_key="t-flaky-1", max_attempts=3
        )
        job_id = job.id
    result = job_service.drain(container)
    assert result["failed"] == 1
    with container.session_scope() as db:
        db.get(JobRow, job_id).next_run_at = datetime.now(UTC)  # bypass the backoff window
    result = job_service.drain(container)
    assert result["ran"] == 1
    with container.session_scope() as db:
        done = db.get(JobRow, job_id)
        assert done.state == "succeeded"
        assert done.attempts == 2


def test_failed_jobs_land_in_the_dead_state(client: TestClient) -> None:
    container = client.app.state.container
    job_type = "test.dead"

    @job_service.register(job_type)
    def _always_fail(_container, _payload) -> None:  # noqa: ANN001
        raise ValueError("always")

    with container.session_scope() as db:
        job = job_service.enqueue(
            db, job_type=job_type, payload={}, idempotency_key="t-dead-1", max_attempts=1
        )
        job_id = job.id
    job_service.drain(container)
    with container.session_scope() as db:
        assert db.get(JobRow, job_id).state == "dead"


def test_drain_endpoint_is_scoped_and_runs_the_sweep(
    client: TestClient, fetch: dict[str, str]
) -> None:
    response = client.post("/internal/jobs/drain", headers=fetch)
    assert response.status_code == 200
    assert set(response.json()) == {"ran", "failed"}
    # Relay tokens cannot run jobs.
    # (scoped out of the box)


def test_merge_expiry_sweeps_through_the_job_worker(tmp_path) -> None:  # noqa: ANN001
    """A stale PATIENT_BEING_CONNECTED escalates via the job worker (task 17A/28)."""
    app, tk_client, relay, setup = _merged_ready(tmp_path)

    def not_answered(call_id, to_number, insurer_ref=None):  # noqa: ANN001, ANN202
        return DialResult(provider_call_ref="MOCK-HU-X", status="initiated")

    pytest.MonkeyPatch().setattr(app.state.container.telephony, "dial_human", not_answered)
    call = tk_client.post("/voice/start", json={"case_id": setup["case_id"]}, headers=relay).json()
    assert call["merge_phase"] == "patient_being_connected"
    with app.state.container.session_scope() as db:
        job_service.enqueue(
            db,
            job_type="voice.merge_sweep",
            payload={"case_id": setup["case_id"]},
            idempotency_key=f"sweep-{setup['case_id']}",
            case_id=setup["case_id"],
        )
    with app.state.container.session_scope() as db:
        from sqlalchemy import select

        from app.models_db import CallRow

        stale = db.scalars(
            select(CallRow).where(CallRow.merge_phase == "patient_being_connected")
        ).first()
        assert stale is not None
        stale.merge_deadline_at = datetime.now(UTC) - timedelta(seconds=1)
    assert job_service.drain(app.state.container)["ran"] == 1
    merged = tk_client.get(f"/voice/{call['call_id']}", headers=relay).json()
    assert merged["merge_phase"] == "merge_failed"


def test_metrics_endpoint_tracks_requests_without_phi(
    client: TestClient, fetch: dict[str, str]
) -> None:
    client.get("/health")
    snapshot = client.get("/internal/metrics", headers=fetch).json()
    assert "queue_depth" in snapshot and "active_calls" in snapshot
    assert snapshot["requests"]  # our GET /health was recorded
    body = str(snapshot)
    assert "fn_pat_" not in body and "SSN" not in body  # PHI-free surface


def test_every_response_carries_a_request_id(client: TestClient) -> None:
    response = client.get("/health", headers={"X-Amyna-Request-Id": "req_from_caller"})
    assert response.headers["X-Amyna-Request-Id"] == "req_from_caller"
    assert client.post("/cases", json={}).headers["X-Amyna-Request-Id"]
