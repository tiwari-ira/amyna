"""Production observability (task 29).

- Request IDs: every request gets an ``X-Amyna-Request-Id`` (echoed back);
  IDs come from the incoming header when present so one case fans out as one
  trace.
- Metrics: in-process counters/timers keyed ONLY by route template + status
  family (never PHI; the PHI-redacting log filter covers messages). Surface
  at ``GET /internal/metrics`` (scope ``metrics:read``) with queue depth,
  call volume, failure counters and stuck-case counts.
- Stuck/abandoned cases are computed from the DB on read.
"""

import time
from collections import Counter
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from claimshield_contracts.enums import CallStatus

from .db import utcnow
from .models_db import CallRow, CaseRow, InterventionRow, JobRow

STUCK_THRESHOLD = timedelta(hours=24)

_counters: Counter[str] = Counter()
_latencies: Counter[str] = Counter()  # ms, summed (avg = sum / count)


def record_request(path_template: str, status_code: int, duration_ms: float) -> None:
    key = f"{path_template}|{status_code // 100}xx"
    _counters[key] += 1
    _latencies[key] += int(duration_ms)


def metrics_snapshot(db: Session) -> dict[str, object]:
    """Current ops picture: throughput, failures, queue depth, active calls."""
    by_route = {k: _counters[k] for k in sorted(_counters)}
    latency_avg_ms = {
        k: int(_latencies[k] / _counters[k]) for k in sorted(_counters) if _counters[k]
    }
    open_jobs = db.scalar(select(func.count(JobRow.id)).where(JobRow.state == "queued")) or 0
    running_jobs = db.scalar(select(func.count(JobRow.id)).where(JobRow.state == "running")) or 0
    dead_jobs = db.scalar(select(func.count(JobRow.id)).where(JobRow.state == "dead")) or 0
    active_calls = (
        db.scalar(
            select(func.count(CallRow.id)).where(
                CallRow.status.in_(
                    [CallStatus.IN_PROGRESS.value, CallStatus.PAUSED_FOR_HUMAN.value]
                )
            )
        )
        or 0
    )
    open_interventions = (
        db.scalar(select(func.count(InterventionRow.id)).where(InterventionRow.status == "open"))
        or 0
    )
    cutoff = utcnow() - STUCK_THRESHOLD
    stuck_cases = (
        db.scalar(
            select(func.count(CaseRow.id)).where(
                CaseRow.state.in_(["AWAITING_PATIENT_INPUT", "CALL_IN_PROGRESS", "HUMAN_REQUIRED"]),
                CaseRow.updated_at < cutoff,
            )
        )
        or 0
    )
    return {
        "requests": by_route,
        "latency_avg_ms": latency_avg_ms,
        "queue_depth": {"queued": open_jobs, "running": running_jobs, "dead": dead_jobs},
        "active_calls": active_calls,
        "open_interventions": open_interventions,
        "stuck_cases_24h": stuck_cases,
    }


def install_request_metrics(app) -> None:  # type: ignore[no-untyped-def]
    """FastAPI middleware: request id + latency buckets (no PHI, ever)."""

    @app.middleware("http")
    async def _request_metrics(request, call_next):  # type: ignore[no-untyped-def]
        import uuid

        request_id = request.headers.get("X-Amyna-Request-Id") or f"req_{uuid.uuid4().hex[:16]}"
        start = time.perf_counter()
        response = await call_next(request)
        record_request(request.url.path, response.status_code, (time.perf_counter() - start) * 1000)
        response.headers["X-Amyna-Request-Id"] = request_id
        return response
