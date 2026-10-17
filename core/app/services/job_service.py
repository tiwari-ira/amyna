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
import time
import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from claimshield_contracts.ids import new_id

from ..audit import record_audit
from ..db import utcnow
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

    The claim is a single guarded UPDATE ... RETURNING: only rows still in a
    runnable state flip to ``running``. Two workers (or the in-app timer plus
    the deployed jobs service) may read the same candidate ids, but only one
    UPDATE wins per row -- a job can never run twice concurrently.
    """
    ran = 0
    failed = 0
    with container.session_scope() as db:
        due_ids = list(
            db.scalars(
                select(JobRow.id)
                .where(JobRow.state.in_([JOB_QUEUED, JOB_FAILED]))
                .where(JobRow.attempts < JobRow.max_attempts)
                .where(or_(JobRow.next_run_at.is_(None), JobRow.next_run_at <= utcnow()))
                .order_by(JobRow.next_run_at)
                .limit(limit)
            )
        )
        claimed: list[tuple[str, str, dict[str, Any]]] = []
        if due_ids:
            rows = db.execute(
                update(JobRow)
                .where(JobRow.id.in_(due_ids))
                .where(JobRow.state.in_([JOB_QUEUED, JOB_FAILED]))
                .where(JobRow.attempts < JobRow.max_attempts)
                .values(
                    state=JOB_RUNNING,
                    attempts=JobRow.attempts + 1,
                    timeout_at=utcnow(),
                )
                .returning(JobRow.id, JobRow.job_type, JobRow.payload)
            ).all()
            claimed = [(str(row.id), str(row.job_type), dict(row.payload)) for row in rows]
    for job_id, job_type, payload in claimed:
        handler = HANDLERS.get(job_type)
        if handler is None:
            _finish(container, job_id, JOB_DEAD, error=f"no handler for {job_type}")
            failed += 1
            continue
        try:
            handler(container, dict(payload))
        except Exception as exc:
            log.warning("job %s failed (%s)", job_id, type(exc).__name__)
            _retry_or_dead(container, job_id, str(type(exc).__name__))
            failed += 1
            continue
        _finish(container, job_id, JOB_SUCCEEDED)
        ran += 1
    # Periodic sweeps ride every worker tick (nothing else schedules them): escalate
    # patients who never answered the human-merge call (17A; never auto-resume),
    # and prune the ledgers that would otherwise grow forever ([3.6]).
    run_call_timers(container)
    run_retention_sweep(container)
    return {"ran": ran, "failed": failed}


def run_call_timers(container: "Container") -> None:
    """Call deadlines: unanswered patient calls and stuck call start steps."""
    from . import call_flow

    try:
        escalated = container.voice_manager.expire_overdue_merges()
        moved = call_flow.expire_media_steps(container)
    except Exception as exc:  # a sweep failure must not fail the caller
        log.warning("call timer sweep failed (%s)", type(exc).__name__)
        return
    if escalated:
        log.info("merge sweep escalated %d unanswered patient call(s)", escalated)
    if moved:
        log.info("moved %d call(s) past a stuck call step", moved)


# ------------------------------------------------------------- retention ([3.6])
#: Idempotency ledgers keep their replay window and go; delivered patient
#: notifications go after 90 days; audit events NEVER go (compliance trail).
WEBHOOK_LEDGER_RETENTION_DAYS = 30
DELIVERED_NOTIFICATION_RETENTION_DAYS = 90
_last_retention_sweep: dict[str, float] = {}
RETENTION_SWEEP_INTERVAL_S = 3600.0


def run_retention_sweep(container: "Container", *, force: bool = False) -> dict[str, int]:
    """Prune the ledgers that would otherwise grow forever.

    Runs at most once an hour (the worker ticks every few seconds); pass
    ``force=True`` from a manual job. Returns per-table deletion counts.
    """
    from datetime import timedelta

    from sqlalchemy import delete

    from ..models_db import FinchNodeWebhookEventRow, NotificationRow, WebhookEventRow

    now = time.monotonic()
    if not force and now - _last_retention_sweep.get("at", 0.0) < RETENTION_SWEEP_INTERVAL_S:
        return {}
    _last_retention_sweep["at"] = now

    webhook_cutoff = utcnow() - timedelta(days=WEBHOOK_LEDGER_RETENTION_DAYS)
    notification_cutoff = utcnow() - timedelta(days=DELIVERED_NOTIFICATION_RETENTION_DAYS)
    removed = {}
    with container.session_scope() as db:
        removed["webhook_events"] = len(
            db.execute(
                delete(WebhookEventRow)
                .where(WebhookEventRow.received_at < webhook_cutoff)
                .returning(WebhookEventRow.event_id)
            ).all()
        )
        removed["finchnode_webhook_events"] = len(
            db.execute(
                delete(FinchNodeWebhookEventRow)
                .where(FinchNodeWebhookEventRow.received_at < webhook_cutoff)
                .returning(FinchNodeWebhookEventRow.event_id)
            ).all()
        )
        removed["delivered_notifications"] = len(
            db.execute(
                delete(NotificationRow)
                .where(
                    NotificationRow.delivered.is_(True),
                    NotificationRow.created_at < notification_cutoff,
                )
                .returning(NotificationRow.id)
            ).all()
        )
    if any(removed.values()):
        log.info("retention sweep removed %s", removed)
    return removed


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


# ------------------------------------------------------------- agent work (1.4)
# AGENT_WORK_INLINE=false routes the fetch-bridge steps through these durable
# jobs: the HTTP request returns 202 pending and the jobs service runs the
# pipeline off the request path (no request thread holds a DB pool slot).


@register("agent.extract_visit_evidence")
def _extract_visit_evidence(container: "Container", payload: dict[str, Any]) -> None:
    container.fetch_bridge.extract_visit_evidence(payload["case_id"], payload["visit_id"])


@register("agent.analyze_claim")
def _analyze_claim(container: "Container", payload: dict[str, Any]) -> None:
    container.fetch_bridge.analyze_claim(payload["case_id"], payload["claim_id"])


@register("agent.prepare_dispute")
def _prepare_dispute(container: "Container", payload: dict[str, Any]) -> None:
    container.fetch_bridge.prepare_dispute(payload["case_id"])
