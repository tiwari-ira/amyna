"""Loader for the synthetic demo scenario shared by seed, smoke test and tests."""

import json
from functools import cache
from importlib import resources
from typing import Any

from .api import TranscriptSegmentIn, VisitComplete
from .models import PatientRef


@cache
def load_demo_scenario() -> dict[str, Any]:
    """Return the raw demo scenario (see fixtures/demo_scenario.json)."""
    text = resources.files(__package__).joinpath("fixtures/demo_scenario.json").read_text("utf-8")
    return json.loads(text)


def demo_patient() -> PatientRef:
    """The synthetic demo patient (Jordan Rivera, fn_pat_demo_001)."""
    return PatientRef.model_validate(load_demo_scenario()["patient"])


def demo_visit_complete() -> VisitComplete:
    """Request body for POST /visits/{id}/complete with the demo transcript."""
    transcript = load_demo_scenario()["transcript"]
    return VisitComplete(
        language=transcript["language"],
        source=transcript["source"],
        segments=[TranscriptSegmentIn.model_validate(s) for s in transcript["segments"]],
    )


def demo_claim_id() -> str:
    """FinchNode claim id of the demo bill."""
    return load_demo_scenario()["claim"]["finchnode_claim_id"]
