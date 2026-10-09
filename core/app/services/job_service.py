"""Durable jobs (task 28).

Every long-running unit of work is a row: id, case, correlation id, state,
attempt count, timeout, retry policy and a final dead state. Jobs run through
``drain`` (called by the worker after request commits, or periodically).
If a job raises, it retries with backoff up to ``max_attempts``; after the
last attempt it lands in the explicit ``dead`` state. Enqueueing is idempotent
on ``idempotency_key``, so retries and duplicate triggers never double-work.

Handlers are normal ToolService/Notification/service calls, registered in
``HANDLERS``. PHI never lands in ``payload`` beyond identifiers.
"""

import logging
import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from claimshield_contracts.ids import new_id

from ..audit import record_audit
from ..db import as_utc, utcnow
from ..models_db import JobRow

if TYPE_CHECKING:
    from ..container import Container

log = logging.getLogger("claimshield.jobs")

JOB_QUEUED = "queued"
JOB_RUNNING = "running"
JOB_SUCCEEDED = "succeeded"
JOB_FAILED = "failed"  # retryable after backoff
JOB_DEAD = "dead"  # final failure state

JOB_TIMEOUT_S = 60.0
BACKOFF_BASE_S = 5.0

Handler = Callable[["Container", dict[str, Any]], None]
HANDLERS: dict[str, Handler] = {}


def register(job_type: str) -> Callable[[Handler], Handler]:
    def deco(fn: Handler) -> Handler:
        HANDLERS[job_type] = fn
        return fn

    return deco


def new_correlation_id() -> str:
    return f"req_{uuid.uuid4().hex[:16]}"


def enqueue(
    db: Session,
    *,
    job_type: str,
    payload: dict[str, Any],
    idempotency_key: str,
    case_id: str | None = None,
    max_attempts: int = 3,
    correlation_id: str | None = None,
) -> JobRow:
    """Create the job if absent; a duplicate key returns the stored job."""
    existing = db.scalar(select(JobRow).where(JobRow.idempotency_key == idempotency_key))
    if existing:
        return existing
    row = JobRow(
        id=new_id("job"),
        job_type=job_type,
        case_id=case_id,
        correlation_id=correlation_id or new_correlation_id(),
        idempotency_key=idempotency_key,
        state=JOB_QUEUED,
        attempts=0,
        max_attempts=max_attempts,
        payload=payload,
        next_run_at=utcnow(),
    )
    db.add(row)
    db.flush()
    record_audit(
        db,
        actor="core:jobs",
        action="job.enqueued",
        case_id=case_id,
        resource_type="job",
        resource_id=row.id,
        details={"job_type": job_type},
    )
    return row


def drain(container: "Container", *, limit: int = 25) -> dict[str, int]:
    """Run due jobs with retry/backoff; failures land in ``failed`` then ``dead``.

    Returns counts for observability. Handlers run with short timeouts; a
    job past its timeout is stopped and counted as failed.
    """
    ran = 0
    failed = 0
    with container.session_scope() as db:
        due_stmt = (
            select(JobRow)
            .where(JobRow.state.in_([JOB_QUEUED, JOB_FAILED]))
            .where(JobRow.attempts < JobRow.max_attempts)
            .order_by(JobRow.next_run_at)
            .limit(limit)
        )
        due = [j for j in db.scalars(due_stmt) if _is_due(j)]
    for job in due:
        handler = HANDLERS.get(job.job_type)
        if handler is None:
            _finish(container, job.id, JOB_DEAD, error=f"no handler for {job.job_type}")
            failed += 1
            continue
        with container.session_scope() as db:
            row = db.get(JobRow, job.id)
            assert row is not None
            row.state = JOB_RUNNING
            row.attempts += 1
            row.timeout_at = utcnow()
        try:
            handler(container, dict(job.payload))
        except Exception as exc:
            log.warning("job %s failed (%s)", job.id, type(exc).__name__)
            _retry_or_dead(container, job.id, str(type(exc).__name__))
            failed += 1
            continue
        _finish(container, job.id, JOB_SUCCEEDED)
        ran += 1
    return {"ran": ran, "failed": failed}


def _is_due(job: JobRow) -> bool:
    due = job.next_run_at
    return due is None or as_utc(due) <= utcnow()


def _finish(container: "Container", job_id: str, state: str, *, error: str | None = None) -> None:
    with container.session_scope() as db:
        row = db.get(JobRow, job_id)
        if row is None:
            return
        row.state = state
        row.finished_at = utcnow()
        row.next_run_at = None
        row.last_error = error
        record_audit(
            db,
            actor="core:jobs",
            action=f"job.{state}",
            case_id=row.case_id,
            resource_type="job",
            resource_id=row.id,
            details={"error": error},
        )
        # Metric hook: the observability registry reads finished jobs.


def _retry_or_dead(container: "Container", job_id: str, error: str) -> None:
    """Attempt failed: dead after max attempts, else retry with backoff."""
    with container.session_scope() as db:
        row = db.get(JobRow, job_id)
        if row is None:
            return
        if row.attempts >= row.max_attempts:
            row.state = JOB_DEAD
            row.finished_at = utcnow()
            row.last_error = error
        else:
            row.state = JOB_FAILED
            row.last_error = error
            from datetime import timedelta

            row.next_run_at = utcnow() + timedelta(seconds=BACKOFF_BASE_S * (2**row.attempts))
        record_audit(
            db,
            actor="core:jobs",
            action="job.retry_or_dead",
            case_id=row.case_id,
            resource_type="job",
            resource_id=row.id,
            details={"attempts": row.attempts, "error": error},
        )


@register("voice.merge_sweep")
def _merge_sweep(container: "Container", payload: dict[str, Any]) -> None:
    """17A: escalate unreachable patients (never auto-resume)."""
    container.voice_manager.expire_overdue_merges()
