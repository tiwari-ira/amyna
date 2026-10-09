"""FastAPI application factory.

Run:  uvicorn app.main:create_app --factory --port 8080   (from core/)
"""

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from claimshield_contracts.version import CONTRACTS_VERSION

from . import __version__
from .audit import configure_logging
from .config import Settings, get_settings
from .container import Container, build_container
from .errors import DomainError
from .observability import install_request_metrics
from .routers import (
    assistant,
    cases,
    claims,
    disputes,
    health,
    human,
    identity,
    ops,
    relay_webhook,
    tools,
    visits,
    voice,
)

log = logging.getLogger("claimshield.core")

DESCRIPTION = """
Amyna Core owns the case (single source of truth).

* **Public API** — used by the Relay service and other clients (Bearer service token).
* **/internal/tools/** — used by Fetch agents and the voice manager; scoped per caller.
* **/relay/webhook** — signed (HMAC-SHA256) idempotent patient events.
* **/voice/telnyx/webhook** — signed (Ed25519) Telnyx call events: completes the
  insurer ⇄ xAI SIP bridge on ``call.answered``, ends the call on ``call.hangup``.

Prototype: synthetic data only, not HIPAA-ready.
"""

TAGS = [
    {"name": "cases", "description": "Cases, consents, timeline and notifications"},
    {"name": "visits", "description": "Recorded visits, transcripts and visit evidence"},
    {"name": "claims", "description": "Claims and discrepancy analysis"},
    {"name": "disputes", "description": "Dispute packages and patient approval"},
    {"name": "human", "description": "Human-in-the-loop questions and handoffs"},
    {"name": "voice", "description": "Insurer calls (Core owns the session)"},
    {"name": "relay", "description": "Signed events from the Relay service"},
    {"name": "assistant", "description": "Stateless natural-language understanding"},
    {"name": "auth", "description": "Patient sessions and identity binding"},
    {"name": "ops", "description": "Metrics and durable jobs drain"},
    {"name": "internal-tools", "description": "Agent + voice tools (scoped service tokens)"},
    {"name": "health", "description": "Liveness and configured modes"},
]


async def _domain_error_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, DomainError)
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.to_detail()})


async def _concurrent_update_handler(_: Request, exc: Exception) -> JSONResponse:
    log.warning("optimistic lock conflict: %s", type(exc).__name__)
    detail = {"code": "concurrent_update", "message": "The case changed concurrently; retry."}
    return JSONResponse(status_code=409, content={"detail": detail})


async def _integrity_error_handler(_: Request, exc: Exception) -> JSONResponse:
    # Never echo the SQL or its parameters (they can contain PHI).
    log.warning("integrity error: %s", type(exc).__name__)
    detail = {"code": "conflict", "message": "The request conflicts with existing data."}
    return JSONResponse(status_code=409, content={"detail": detail})


def create_app(settings: Settings | None = None, container: Container | None = None) -> FastAPI:
    """Build the app. Fails fast if a live mode is missing its keys."""
    settings = settings or (container.settings if container else get_settings())
    configure_logging(settings.log_level)
    settings.validate_for_startup()
    for name in settings.using_dev_credentials():
        log.warning("%s is empty; using the built-in development value", name)

    app = FastAPI(
        title="Amyna Core",
        version=__version__,
        description=DESCRIPTION,
        openapi_tags=TAGS,
    )
    app.state.container = container or build_container(settings)
    app.add_exception_handler(DomainError, _domain_error_handler)
    app.add_exception_handler(StaleDataError, _concurrent_update_handler)
    app.add_exception_handler(IntegrityError, _integrity_error_handler)

    @app.middleware("http")
    async def _contracts_header(request: Request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        response.headers["X-Amyna-Contracts-Version"] = CONTRACTS_VERSION
        return response

    for module in (
        health,
        cases,
        visits,
        claims,
        disputes,
        human,
        voice,
        relay_webhook,
        tools,
        assistant,
        identity,
        ops,
    ):
        app.include_router(module.router)
    install_request_metrics(app)
    return app
