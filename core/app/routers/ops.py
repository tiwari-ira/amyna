"""Ops endpoints: metrics and the jobs drain (scoped; operator-facing).

Everything here runs read-only queries or enqueue/drain operations --
never PHI. Tokens: the FETCH service identity carries ``metrics:read`` and
``jobs:run``.
"""

from fastapi import APIRouter, Depends

from claimshield_contracts.api import JobsDrainResult, MetricsSnapshot

from ..auth import ServiceIdentity, require_scopes
from ..container import Container
from ..deps import get_container
from ..observability import metrics_snapshot
from ..services import job_service

router = APIRouter(prefix="/internal", tags=["ops"])


@router.post("/jobs/drain", response_model=JobsDrainResult)
def drain_jobs(
    _: ServiceIdentity = Depends(require_scopes("jobs:run")),
    container: Container = Depends(get_container),
) -> JobsDrainResult:
    """Run due durable jobs (worker tick). The 17A merge-expiry sweep rides this."""
    return JobsDrainResult(**job_service.drain(container))


@router.get("/metrics", response_model=MetricsSnapshot)
def metrics(
    _: ServiceIdentity = Depends(require_scopes("metrics:read")),
    container: Container = Depends(get_container),
) -> MetricsSnapshot:
    """Liveness++: throughput, failures, queue depth, active calls, stuck cases."""
    with container.session_scope() as db:
        return MetricsSnapshot.model_validate(metrics_snapshot(db))
