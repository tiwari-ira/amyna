"""FinchNode tool functions -- least privilege: one function per record type.

Core's FinchNode adapter calls these (after checking consent). Agents never
call them directly; they use the matching Core tools, and which agent may use
which tool is declared in ``claimshield_contracts.tool_api.AGENT_TOOLS``
(the Claim Agent is the only agent that reads FinchNode data).
"""

from claimshield_contracts.models import Claim, HealthRecord, PatientRef

from .client import FinchNodeClient
from .normalize import normalize_claim, normalize_patient, normalize_records

MEDICATION_RESOURCES = ("MedicationAdministration", "MedicationRequest")


def get_patient_profile(client: FinchNodeClient, patient_id: str) -> PatientRef | None:
    """Demographics + coverage (payer, member reference, payer phone)."""
    patient = client.get_patient(patient_id)
    if patient is None:
        return None
    return normalize_patient(patient, client.search("Coverage", patient_id))


def get_patient_claims(client: FinchNodeClient, patient_id: str) -> list[Claim]:
    return [normalize_claim(resource) for resource in client.search("Claim", patient_id)]


def get_patient_encounters(client: FinchNodeClient, patient_id: str) -> list[HealthRecord]:
    return normalize_records(client.search("Encounter", patient_id), patient_id)


def get_patient_procedures(client: FinchNodeClient, patient_id: str) -> list[HealthRecord]:
    return normalize_records(client.search("Procedure", patient_id), patient_id)


def get_patient_medications(client: FinchNodeClient, patient_id: str) -> list[HealthRecord]:
    resources = [r for rt in MEDICATION_RESOURCES for r in client.search(rt, patient_id)]
    return normalize_records(resources, patient_id)
