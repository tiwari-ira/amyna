"""Shared fixtures: the synthetic demo transcript, FinchNode data and claim."""

import pytest

from claimshield_contracts.demo import demo_visit_complete
from claimshield_contracts.models import Claim, HealthRecord, Transcript
from finchnode import tools
from finchnode.client import FixtureClient
from intelligence.codes.resolver import CodeResolver, default_resolver

PATIENT = "fn_pat_demo_001"


@pytest.fixture(scope="session")
def resolver() -> CodeResolver:
    return default_resolver()


@pytest.fixture(scope="session")
def transcript() -> Transcript:
    body = demo_visit_complete()
    return Transcript(
        transcript_id="trn_demo",
        visit_id="vis_demo",
        source=body.source,
        segments=[
            {"segment_id": f"seg_{i}", **segment.model_dump()}
            for i, segment in enumerate(body.segments)
        ],
    )


@pytest.fixture(scope="session")
def records() -> list[HealthRecord]:
    client = FixtureClient()
    return (
        tools.get_patient_encounters(client, PATIENT)
        + tools.get_patient_procedures(client, PATIENT)
        + tools.get_patient_medications(client, PATIENT)
    )


@pytest.fixture(scope="session")
def claim() -> Claim:
    claims = tools.get_patient_claims(FixtureClient(), PATIENT)
    demo = next(c for c in claims if c.external_claim_id == "CLM-2026-0915-001")
    return demo.model_copy(update={"case_id": "case_demo"})
