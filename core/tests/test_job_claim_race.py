"""[1.2] The job claim is atomic: concurrent drains can never run one job twice.

The old claim (SELECT due, then UPDATE running in a separate transaction) let
two workers both run the same queued job. The guarded UPDATE ... RETURNING
makes the claim itself the arbiter: only rows still in a runnable state flip.
"""

import threading
from typing import Any

from fastapi.testclient import TestClient

from app.services import job_service


def _drain_concurrently(container: Any, round_no: int) -> int:
    """Two workers drain at once; returns how many times the handler ran."""
    executions = []
    job_type = "test.race.echo"

    @job_service.register(job_type)
    def _handler(_container: Any, payload: dict[str, Any]) -> None:
        executions.append(payload["marker"])
        # Widen the race window: the other worker is still inside drain().
        threading.Event().wait(0.05)

    with container.session_scope() as db:
        job_service.enqueue(
            db,
            job_type=job_type,
            payload={"marker": f"round-{round_no}"},
            idempotency_key=f"race-echo-{round_no}",
        )
    barrier = threading.Barrier(2)

    def _worker() -> None:
        barrier.wait()
        job_service.drain(container)

    threads = [threading.Thread(target=_worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return len(executions)


def test_concurrent_drains_claim_a_job_exactly_once(client: TestClient) -> None:
    container = client.app.state.container
    # Several rounds: the invariant is structural, each round must hold.
    for round_no in range(5):
        assert _drain_concurrently(container, round_no) == 1, f"round {round_no} ran the job twice"


def test_drain_never_reruns_succeeded_jobs(client: TestClient) -> None:
    container = client.app.state.container
    ran: list[str] = []

    @job_service.register("test.once.only")
    def _handler(_container: Any, _payload: dict[str, Any]) -> None:
        ran.append("x")

    with container.session_scope() as db:
        job_service.enqueue(
            db,
            job_type="test.once.only",
            payload={},
            idempotency_key="once-only",
        )
    assert job_service.drain(container) == {"ran": 1, "failed": 0}
    assert job_service.drain(container) == {"ran": 0, "failed": 0}
    assert len(ran) == 1
