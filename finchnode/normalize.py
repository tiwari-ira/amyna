"""Normalize FinchNode (FHIR R4-style) resources into shared contract models.

Source codes are PRESERVED: every ``coding`` keeps its original system URI,
code and display (``CodeRef.source_system_uri``). Downstream logic prefers
these source codes over re-deriving codes from text (ARCHITECTURE rule 5).
"""

from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from claimshield_contracts.enums import ClaimSource, CodeSystem, RecordType
from claimshield_contracts.models import Claim, ClaimLine, CodeRef, HealthRecord, PatientRef

SYSTEM_URIS: dict[str, CodeSystem] = {
    "http://www.ama-assn.org/go/cpt": CodeSystem.CPT,
    "urn:oid:2.16.840.1.113883.6.12": CodeSystem.CPT,
    "https://www.cms.gov/Medicare/Coding/HCPCSReleaseCodeSets": CodeSystem.HCPCS,
    "urn:oid:2.16.840.1.113883.6.285": CodeSystem.HCPCS,
    "http://www.nlm.nih.gov/research/umls/rxnorm": CodeSystem.RXNORM,
    "http://hl7.org/fhir/sid/ndc": CodeSystem.NDC,
    "http://hl7.org/fhir/sid/icd-10-cm": CodeSystem.ICD10CM,
    "http://snomed.info/sct": CodeSystem.SNOMED,
    "http://loinc.org": CodeSystem.LOINC,
}
MEDICATION_TYPES = ("MedicationAdministration", "MedicationStatement", "MedicationRequest")


def code_system_for(uri: str | None) -> CodeSystem:
    """Map a FHIR coding system URI to a CodeSystem (unknown URIs -> LOCAL)."""
    return SYSTEM_URIS.get(uri or "", CodeSystem.LOCAL)


def codes_from_concept(concept: dict[str, Any] | None) -> list[CodeRef]:
    """All codings of a CodeableConcept, verbatim."""
    if not concept:
        return []
    return [
        CodeRef(
            system=code_system_for(coding.get("system")),
            code=str(coding["code"]),
            display=coding.get("display"),
            source_system_uri=coding.get("system"),
        )
        for coding in concept.get("coding", [])
        if coding.get("code")
    ]


def _concept_text(concept: dict[str, Any] | None) -> str:
    if not concept:
        return ""
    if concept.get("text"):
        return concept["text"]
    displays = [c.get("display") for c in concept.get("coding", []) if c.get("display")]
    return displays[0] if displays else ""


def _to_date(value: str | None) -> date | None:
    if not value:
        return None
    if len(value) == 10:
        return date.fromisoformat(value)
    return datetime.fromisoformat(value.replace("Z", "+00:00")).date()


def _ref_id(reference: dict[str, Any] | None) -> str | None:
    ref = (reference or {}).get("reference")
    return ref.split("/", 1)[-1] if ref else None


def to_cents(value: float | int | str | None) -> int:
    """Money -> integer cents without float rounding surprises."""
    if value is None:
        return 0
    return int((Decimal(str(value)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


# --------------------------------------------------------------------------- patient


def normalize_patient(patient: dict[str, Any], coverages: list[dict[str, Any]]) -> PatientRef:
    """Patient + first active Coverage -> PatientRef (payer, member ref, payer phone)."""
    names = patient.get("name") or [{}]
    display = " ".join([*names[0].get("given", []), names[0].get("family", "")]).strip() or None
    address = (patient.get("address") or [{}])[0]
    coverage = next((c for c in coverages if c.get("status", "active") == "active"), None) or {}
    payer_name = payer_phone = None
    if coverage:
        payor = (coverage.get("payor") or [{}])[0]
        payer_name = payor.get("display")
        contained = {
            f"#{res.get('id')}": res
            for res in coverage.get("contained", [])
            if res.get("resourceType") == "Organization"
        }
        org = contained.get(payor.get("reference", ""), {})
        payer_name = payer_name or org.get("name")
        payer_phone = next(
            (t.get("value") for t in org.get("telecom", []) if t.get("system") == "phone"), None
        )
    return PatientRef(
        finchnode_patient_id=patient["id"],
        display_name=display,
        member_ref=coverage.get("subscriberId"),
        payer_name=payer_name,
        payer_phone=payer_phone,
        state=address.get("state"),
    )


# --------------------------------------------------------------------------- records


def normalize_encounter(resource: dict[str, Any], patient_id: str) -> HealthRecord:
    concepts = resource.get("type") or [{}]
    period = resource.get("period") or {}
    return HealthRecord(
        record_id=f"fn:{resource['id']}",
        record_type=RecordType.ENCOUNTER,
        patient_id=patient_id,
        occurred_on=_to_date(period.get("start")),
        display=_concept_text(concepts[0]) or "Encounter",
        codes=[code for concept in concepts for code in codes_from_concept(concept)],
        status=resource.get("status"),
        facility=(resource.get("serviceProvider") or {}).get("display"),
        source_resource_type="Encounter",
        source_id=resource["id"],
    )


def normalize_procedure(resource: dict[str, Any], patient_id: str) -> HealthRecord:
    performers = resource.get("performer") or [{}]
    return HealthRecord(
        record_id=f"fn:{resource['id']}",
        record_type=RecordType.PROCEDURE,
        patient_id=patient_id,
        occurred_on=_to_date(
            resource.get("performedDateTime")
            or (resource.get("performedPeriod") or {}).get("start")
        ),
        display=_concept_text(resource.get("code")) or "Procedure",
        codes=codes_from_concept(resource.get("code")),
        status=resource.get("status"),
        performer=(performers[0].get("actor") or {}).get("display"),
        encounter_ref=_ref_id(resource.get("encounter")),
        source_resource_type="Procedure",
        source_id=resource["id"],
    )


def normalize_medication(resource: dict[str, Any], patient_id: str) -> HealthRecord:
    dosage = resource.get("dosage") or {}
    if isinstance(dosage, list):  # MedicationStatement/Request use a list
        dosage = dosage[0] if dosage else {}
    dose = dosage.get("dose") or {}
    attributes = {
        key: value
        for key, value in {
            "dose": f"{dose.get('value')} {dose.get('unit', '')}".strip() if dose else None,
            "route": _concept_text(dosage.get("route")) or None,
        }.items()
        if value
    }
    effective = resource.get("effectiveDateTime") or (resource.get("effectivePeriod") or {}).get(
        "start"
    )
    concept = resource.get("medicationCodeableConcept")
    return HealthRecord(
        record_id=f"fn:{resource['id']}",
        record_type=RecordType.MEDICATION,
        patient_id=patient_id,
        occurred_on=_to_date(effective or resource.get("authoredOn")),
        display=_concept_text(concept) or "Medication",
        codes=codes_from_concept(concept),
        status=resource.get("status"),
        encounter_ref=_ref_id(resource.get("context") or resource.get("encounter")),
        attributes=attributes,
        source_resource_type=resource["resourceType"],
        source_id=resource["id"],
    )


_NORMALIZERS = {
    "Encounter": normalize_encounter,
    "Procedure": normalize_procedure,
    **{name: normalize_medication for name in MEDICATION_TYPES},
}


def normalize_records(resources: list[dict[str, Any]], patient_id: str) -> list[HealthRecord]:
    """Normalize every supported resource; unsupported types are skipped."""
    return [
        _NORMALIZERS[res["resourceType"]](res, patient_id)
        for res in resources
        if res.get("resourceType") in _NORMALIZERS
    ]


# --------------------------------------------------------------------------- claims


def normalize_claim(resource: dict[str, Any]) -> Claim:
    """FHIR Claim / ExplanationOfBenefit -> Claim. Ids are FinchNode-scoped (``fn:``)."""
    identifiers = resource.get("identifier") or [{}]
    external_id = identifiers[0].get("value") or resource["id"]
    period = resource.get("billablePeriod") or {}
    claim_id = f"fn:{resource['id']}"
    lines = []
    for item in resource.get("item", []):
        codes = codes_from_concept(item.get("productOrService"))
        if not codes:
            continue
        served = _to_date(item.get("servicedDate")) or _to_date(period.get("start"))
        net = (item.get("net") or {}).get("value")
        if net is None:
            unit = (item.get("unitPrice") or {}).get("value", 0)
            net = Decimal(str(unit)) * Decimal(str((item.get("quantity") or {}).get("value", 1)))
        lines.append(
            ClaimLine(
                line_id=f"{claim_id}:{item['sequence']}",
                claim_id=claim_id,
                line_number=int(item["sequence"]),
                code=codes[0],
                description=codes[0].display or _concept_text(item.get("productOrService")),
                date_of_service=served,
                units=int((item.get("quantity") or {}).get("value", 1)),
                billed_cents=to_cents(net),
                modifiers=[
                    m["coding"][0]["code"] for m in item.get("modifier", []) if m.get("coding")
                ],
            )
        )
    return Claim(
        claim_id=claim_id,
        external_claim_id=external_id,
        payer_name=(resource.get("insurer") or {}).get("display"),
        provider_name=(resource.get("provider") or {}).get("display"),
        facility_name=(resource.get("provider") or {}).get("display"),
        date_of_service=_to_date(period.get("start")) or lines[0].date_of_service,
        total_billed_cents=sum(line.billed_cents for line in lines),
        source=ClaimSource.FINCHNODE,
        lines=lines,
    )
