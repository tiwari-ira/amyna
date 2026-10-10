"""FinchNode tool functions -- least privilege: one function per record type.

Core's FinchNode adapter calls these (after checking consent). Agents never
call them directly; they use the matching Core tools, and which agent may use
which tool is declared in ``claimshield_contracts.tool_api.AGENT_TOOLS``
(the Claim Agent is the only agent that reads FinchNode data).

Two sources, one result shape:
* the HTTP client (demo / sandbox / production) reads FinchNode's normalized
  records snapshot (``records.py``), which also says what is incomplete;
* the fixture client (mock) reads local FHIR bundles, which are complete by design.
"""

from typing import Any

from claimshield_contracts.enums import RecordType
from claimshield_contracts.models import Claim, HealthRecord, PatientRef

from . import records
from .normalize import normalize_claim, normalize_patient, normalize_records

MEDICATION_RESOURCES = ("MedicationAdministration",)


def _snapshot(client: Any, patient_id: str) -> tuple[bool, records.Snapshot | None]:
    """(is a records client, snapshot or None)."""
    reader = getattr(client, "snapshot", None)
    if reader is None:
        return False, None
    return True, reader(patient_id)


def get_patient_profile(client: Any, patient_id: str) -> PatientRef | None:
    """Demographics + coverage (payer, member reference, payer phone if the source has one)."""
    is_records, snapshot = _snapshot(client, patient_id)
    if is_records:
        return records.patient_profile(snapshot) if snapshot else None
    patient = client.get_patient(patient_id)
    if patient is None:
        return None
    return normalize_patient(patient, client.search("Coverage", patient_id), patient_id)


def get_patient_claims(client: Any, patient_id: str) -> list[Claim]:
    """Every claim on file. FinchNode claim records are summaries (``lines=[]``)."""
    is_records, snapshot = _snapshot(client, patient_id)
    if is_records:
        return records.claims(snapshot) if snapshot else []
    return [normalize_claim(resource) for resource in client.search("Claim", patient_id)]


def get_patient_encounters(client: Any, patient_id: str) -> list[HealthRecord]:
    is_records, snapshot = _snapshot(client, patient_id)
    if is_records:
        return records.encounters(snapshot) if snapshot else []
    return normalize_records(client.search("Encounter", patient_id), patient_id)


def get_patient_procedures(client: Any, patient_id: str) -> list[HealthRecord]:
    is_records, snapshot = _snapshot(client, patient_id)
    if is_records:
        return records.procedures(snapshot) if snapshot else []
    return normalize_records(client.search("Procedure", patient_id), patient_id)


def get_patient_medications(client: Any, patient_id: str) -> list[HealthRecord]:
    """Medications administered during care (not prescriptions or dispenses)."""
    is_records, snapshot = _snapshot(client, patient_id)
    if is_records:
        return records.medications(snapshot) if snapshot else []
    resources = [r for rt in MEDICATION_RESOURCES for r in client.search(rt, patient_id)]
    return normalize_records(resources, patient_id)


def record_gaps(client: Any, patient_id: str) -> dict[RecordType, str]:
    """Record types whose FinchNode category is not provably complete, with the reason
    (``not_shared``, ``missing``, ``sync_partial``, a warning code...). Empty = complete."""
    is_records, snapshot = _snapshot(client, patient_id)
    if not is_records:
        return {}
    if snapshot is None:
        return {record_type: "no_records" for record_type in records.CATEGORY_FOR}
    return {
        record_type: gap
        for record_type, category in records.CATEGORY_FOR.items()
        if (gap := snapshot.gap(category))
    }


def claims_gap(client: Any, patient_id: str) -> str | None:
    """Why the claims category may be incomplete (None when it is complete)."""
    is_records, snapshot = _snapshot(client, patient_id)
    if not is_records:
        return None
    return snapshot.gap("claims") if snapshot else "no_records"
