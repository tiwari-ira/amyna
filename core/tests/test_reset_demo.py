"""``make reset-demo``: the demo patient can be demoed back to back, judge after judge."""

from typing import Any

from fastapi.testclient import TestClient

from app.config import Settings
from app.demo_support import create_demo_visit, run_demo_flow
from app.seed.reset_demo import wipe_patient_cases
from claimshield_contracts.demo import demo_claim_id, demo_patient

PATIENT = demo_patient().finchnode_patient_id


def _asi1_find_case(client: TestClient, fetch: dict[str, str]) -> dict[str, Any]:
    """What the ASI:One orchestrator does when a judge sends the patient + claim ids."""
    response = client.post(
        "/internal/tools/find_or_create_case",
        json={
            "finchnode_patient_id": PATIENT,
            "channel": "asi1",
            "external_ref": demo_claim_id(),
        },
        headers=fetch,
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_reset_gives_every_judge_the_same_fresh_start(
    client: TestClient, settings: Settings, relay: dict[str, str], fetch: dict[str, str]
) -> None:
    # Judge 1: full run to RESOLVED.
    first = run_demo_flow(client, settings)
    assert first["state"] == "RESOLVED"

    # Judge 2 walks away mid-demo: claim pulled, questions open, never finished.
    abandoned = create_demo_visit(client, relay)
    client.post(
        "/claims",
        json={
            "case_id": abandoned["case_id"],
            "source": "finchnode",
            "finchnode_claim_id": demo_claim_id(),
        },
        headers=relay,
    ).raise_for_status()

    # Without a reset, the next ASI:One run would resume that half-finished case.
    assert _asi1_find_case(client, fetch)["case"]["case_id"] == abandoned["case_id"]

    # Reset: wipe this patient's cases, re-seed the clean starting case.
    container = client.app.state.container
    with container.session_scope() as db:
        assert wipe_patient_cases(db, PATIENT) >= 2
    seeded = create_demo_visit(client, relay, data_consent=False)

    # ASI:One now adopts the fresh seeded case: visit evidence ready, no consent, no claim.
    found = _asi1_find_case(client, fetch)
    assert found["created"] is False
    assert found["case"]["case_id"] == seeded["case_id"]
    assert found["case"]["state"] == "EVIDENCE_EXTRACTED"
    detail = client.get(f"/cases/{seeded['case_id']}", headers=relay).json()
    assert detail["claim"] is None
    assert detail["findings"] == []

    # And the whole scripted demo runs identically again.
    second = run_demo_flow(client, settings)
    assert second["state"] == "RESOLVED"
    assert second["findings"] == first["findings"]
    assert second["flagged_cents"] == first["flagged_cents"]


def test_wipe_with_nothing_to_delete_is_a_no_op(client: TestClient) -> None:
    with client.app.state.container.session_scope() as db:
        assert wipe_patient_cases(db, PATIENT) == 0
