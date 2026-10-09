"""Fixtures: an in-process Core (TestClient) and a live uvicorn Core for uAgents tests."""

import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.demo_support import build_isolated_app, create_demo_visit, service_headers
from claimshield_contracts.enums import ServiceName
from fetch.agents.settings import AgentSettings

SEEDS = {
    "ORCHESTRATOR_SEED": "claimshield-test-orchestrator-seed-0001",
    "VISIT_AGENT_SEED": "claimshield-test-visit-seed-0001",
    "CLAIM_AGENT_SEED": "claimshield-test-claim-seed-0001",
    "DISPUTE_AGENT_SEED": "claimshield-test-dispute-seed-0001",
}


@pytest.fixture
def agent_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Test seeds + dev tokens; uAgents storage files land in tmp_path."""
    for key, value in SEEDS.items():
        monkeypatch.setenv(key, value)
    for key in ("FETCH_SERVICE_TOKEN", "HELLO_WORLD", "EXTRACTION_MODE"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def core(tmp_path: Path) -> Iterator[dict[str, Any]]:
    """In-process Core with the demo visit recorded (no health-data consent yet)."""
    app = build_isolated_app(tmp_path / "core.db")
    with TestClient(app) as client:
        settings = app.state.container.settings
        relay = service_headers(settings, ServiceName.RELAY)
        setup = create_demo_visit(client, relay, data_consent=False)
        yield {"app": app, "client": client, "relay": relay, **setup}


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@contextmanager
def serve(app: FastAPI) -> Iterator[str]:
    """Serve a Core app with uvicorn on a free port; yields its base URL."""
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)


@pytest.fixture
def live_core(tmp_path: Path) -> Iterator[dict[str, Any]]:
    """Core served by uvicorn on a free port (real HTTP for the uAgents tests)."""
    app = build_isolated_app(tmp_path / "live.db")
    with serve(app) as base_url:
        relay = service_headers(app.state.container.settings, ServiceName.RELAY)
        setup = create_demo_visit(httpx.Client(base_url=base_url), relay, data_consent=False)
        yield {"base_url": base_url, "relay": relay, **setup}


def agent_settings(**overrides: Any) -> AgentSettings:
    settings = AgentSettings.from_env()
    return AgentSettings(**{**settings.__dict__, **overrides})
