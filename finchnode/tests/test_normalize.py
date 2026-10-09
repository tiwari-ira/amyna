"""Normalization must PRESERVE source code system/code/display."""

from claimshield_contracts.enums import CodeSystem, RecordType
from finchnode import tools
from finchnode.client import FixtureClient
from finchnode.normalize import codes_from_concept, normalize_claim, normalize_medication, to_cents

PATIENT = "fn_pat_demo_001"


def test_source_codes_are_preserved_verbatim() -> None:
    [code] = codes_from_concept(
        {
            "coding": [
                {
                    "system": "http://www.ama-assn.org/go/cpt",
                    "code": "70553",
                    "display": "MRI brain without and with contrast",
                }
            ]
        }
    )
    assert code.system == CodeSystem.CPT
    assert code.code == "70553"
    assert code.display == "MRI brain without and with contrast"
    assert code.source_system_uri == "http://www.ama-assn.org/go/cpt"


def test_unknown_system_is_local_but_uri_kept() -> None:
    [code] = codes_from_concept({"coding": [{"system": "https://x.example/cs", "code": "ABC"}]})
    assert code.system == CodeSystem.LOCAL and code.source_system_uri == "https://x.example/cs"


def test_procedures_keep_codes_and_dates() -> None:
    [mri, iv] = tools.get_patient_procedures(FixtureClient(), PATIENT)
    assert mri.record_type == RecordType.PROCEDURE and mri.codes[0].code == "70553"
    assert mri.occurred_on.isoformat() == "2026-09-15" and mri.encounter_ref == "enc-2026-09-15"
    assert iv.codes == []  # uncoded source stays uncoded (no invented codes)


def test_medication_attributes() -> None:
    record = normalize_medication(
        {
            "resourceType": "MedicationAdministration",
            "id": "m1",
            "medicationCodeableConcept": {
                "coding": [
                    {"system": "http://www.nlm.nih.gov/research/umls/rxnorm", "code": "6470"}
                ]
            },
            "effectiveDateTime": "2026-09-15T16:15:00Z",
            "dosage": {"route": {"text": "IV"}, "dose": {"value": 1, "unit": "mg"}},
        },
        PATIENT,
    )
    assert record.attributes == {"dose": "1 mg", "route": "IV"}
    assert record.codes[0].system == CodeSystem.RXNORM


def test_patient_profile_uses_coverage_and_contained_payer() -> None:
    profile = tools.get_patient_profile(FixtureClient(), PATIENT)
    assert profile.payer_name == "Acme Health Plan"
    assert profile.payer_phone == "+12487208903"  # demo roster payer line (fixtures)
    assert profile.member_ref == "SYN-MBR-000123" and profile.state == "CA"


def test_claim_normalization_uses_integer_cents() -> None:
    claims = tools.get_patient_claims(FixtureClient(), PATIENT)
    demo = next(c for c in claims if c.external_claim_id == "CLM-2026-0915-001")
    assert [line.billed_cents for line in demo.lines] == [240000, 3500, 78000, 25000, 25000, 4000]
    assert demo.total_billed_cents == 375500
    assert demo.lines[1].code.system == CodeSystem.HCPCS and demo.lines[1].code.code == "J2060"
    assert to_cents("0.1") + to_cents("0.2") == 30


def test_claim_from_unit_price() -> None:
    claim = normalize_claim(
        {
            "resourceType": "Claim",
            "id": "c1",
            "billablePeriod": {"start": "2026-01-02"},
            "item": [
                {
                    "sequence": 1,
                    "productOrService": {
                        "coding": [{"system": "http://www.ama-assn.org/go/cpt", "code": "99213"}]
                    },
                    "quantity": {"value": 2},
                    "unitPrice": {"value": 75.5},
                }
            ],
        }
    )
    assert claim.lines[0].billed_cents == 15100 and claim.lines[0].units == 2
