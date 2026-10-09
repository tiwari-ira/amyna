"""Core test fixtures: an isolated app on a temp SQLite DB, clients and service headers."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings
from app.demo_support import (
    answer_open_questions,
    build_isolated_app,
    create_demo_visit,
    service_headers,
)
from claimshield_contracts.enums import ServiceName


@pytest.fixture
def app(tmp_path: Path) -> FastAPI:
    return build_isolated_app(tmp_path / "core.db")


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def settings(app: FastAPI) -> Settings:
    return app.state.container.settings


@pytest.fixture
def relay(settings: Settings) -> dict[str, str]:
    return service_headers(settings, ServiceName.RELAY)


@pytest.fixture
def fetch(settings: Settings) -> dict[str, str]:
    return service_headers(settings, ServiceName.FETCH)


@pytest.fixture
def voice(settings: Settings) -> dict[str, str]:
    return service_headers(settings, ServiceName.VOICE)


@pytest.fixture
def analyzed_case(client: TestClient, relay: dict[str, str]) -> dict[str, Any]:
    """Demo case analyzed and waiting for patient answers."""
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
    analysis = client.post(f"/claims/{claim['claim_id']}/analyze", headers=relay).json()
    return {**setup, "claim": claim, "analysis": analysis}


@pytest.fixture
def awaiting_approval(
    client: TestClient, relay: dict[str, str], analyzed_case: dict[str, Any]
) -> dict[str, Any]:
    answer_open_questions(client, relay, analyzed_case["case_id"])
    prepared = client.post(
        "/disputes", json={"case_id": analyzed_case["case_id"]}, headers=relay
    ).json()
    return {**analyzed_case, "dispute": prepared["dispute"]}
