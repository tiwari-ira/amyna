from fastapi import APIRouter, Depends
from sqlalchemy import text

from claimshield_contracts.api import HealthStatus
from claimshield_contracts.version import CONTRACTS_VERSION

from .. import __version__
from ..container import Container
from ..deps import get_container

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthStatus)
def health(container: Container = Depends(get_container)) -> HealthStatus:
    """Liveness, database reachability and the configured integration modes (no auth)."""
    try:
        with container.session_scope() as db:
            db.execute(text("SELECT 1"))
        database = "ok"
    except Exception as exc:  # report, don't crash, so orchestration can see it
        database = f"error: {type(exc).__name__}"
    return HealthStatus(
        status="ok" if database == "ok" else "degraded",
        version=__version__,
        contracts_version=CONTRACTS_VERSION,
        app_env=container.settings.app_env,
        database=database,
        modes=container.settings.modes(),
    )
