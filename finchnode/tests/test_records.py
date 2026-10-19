"""FinchNode records snapshots -> contract models, using saved copies of FinchNode's
public demo responses (synthetic data, schemaVersion 2, fetched 2026-10-09)."""

import json
from pathlib import Path

import pytest

from claimshield_contracts.enums import ClaimSource, CodeSystem, RecordType
from finchnode import records, tools
from finchnode.normalize import us_state
from finchnode.records import Snapshot

FIXTURES = Path(__file__).parent / "fixtures"


def snapshot(name: str, subject: str = "u_0123456789abcdef") -> Snapshot:
    body = json.loads((FIXTURES / f"records_{name}.json").read_text())
    return Snapshot.from_body(subject, body)


class RecordsClient:
    """A records-capable client serving saved snapshots (what tools.py sees)."""

    def __init__(self, by_subject: dict[str, Snapshot | None]) -> None:
        self._by_subject = by_subject

    def snapshot(self, subject: str) -> Snapshot | None:
        return self._by_subject.get(subject)


BASELINE = snapshot("patient-demo-001")


def test_profile_uses_demographics_and_coverage() -> None:
    profile = records.patient_profile(BASELINE)
    assert profile is not None
    assert profile.finchnode_patient_id == "u_0123456789abcdef"
    assert profile.display_name == "Morgan Rivera"
    assert profile.payer_name == "Example Health Plan (Synthetic)"
    assert profile.member_ref == "SYN-MEMBER-10482"
    assert profile.state == "CA"
    assert profile.payer_phone is None  # FinchNode coverage has no phone


def test_a_procedure_code_becomes_one_line_at_the_claim_total() -> None:
    body = {
        "categories": ["claims"],
        "data": {
            "claims": {
                "explanationsOfBenefit": [
                    {
                        "id": "rec_pump",
                        "sourceRecordId": "ExplanationOfBenefit/rec_7448c25005976bed817b7126",
                        "providerName": "Provider not listed",
                        "servicePeriod": {"start": "2015-03-25", "end": "2015-04-18"},
                        "providerBilled": {"amount": 19593.85, "currency": "USD"},
                        "procedureCodes": [
                            {
                                "code": "Z000",
                                "display": "Encounter for general adult medical examination",
                            },
                            {
                                "code": "5A0211D",
                                "display": (
                                    "ASSIST WITH CARDIAC OUTPUT USING IMPELLER PUMP, INTERMITTENT"
                                ),
                            },
                        ],
                    }
                ]
            }
        },
    }
    [claim] = records.claims(Snapshot.from_body("u_07950e432471154c", body))
    assert claim.external_claim_id == "rec_7448c25005976bed817b7126"
    assert claim.total_billed_cents == 1_959_385
    assert len(claim.lines) == 1
    line = claim.lines[0]
    assert line.code.code == "5A0211D" and line.code.system == CodeSystem.LOCAL
    assert line.billed_cents == 1_959_385
    assert "IMPELLER PUMP" in line.description


def test_claims_are_summaries_without_lines() -> None:
    [claim] = records.claims(BASELINE)
    assert claim.external_claim_id == "claim-demo-001"
    assert claim.total_billed_cents == 18000 and claim.lines == []
    assert claim.date_of_service.isoformat() == "2026-07-18"
    assert claim.provider_name == "Northstar Health System (Synthetic)"
    assert claim.source == ClaimSource.FINCHNODE


def test_encounters_use_the_visit_type_and_location() -> None:
    [visit] = records.encounters(BASELINE)
    assert visit.display == "Annual wellness visit"
    assert visit.occurred_on.isoformat() == "2026-07-18"
    assert visit.facility.startswith("Northstar Demo Clinic")
    assert visit.codes[0].code == "wellness-visit" and visit.codes[0].system == CodeSystem.LOCAL


def test_labs_and_reports_are_procedure_records_with_loinc_codes() -> None:
    procedures = records.procedures(BASELINE)
    assert len(procedures) == 19 and {p.record_type for p in procedures} == {RecordType.PROCEDURE}
    a1c = [p for p in procedures if any(c.code == "4548-4" for c in p.codes)]
    assert len(a1c) == 3 and all(c.system == CodeSystem.LOINC for c in a1c[0].codes[:1])


def test_only_administered_medications_count() -> None:
    [metformin] = records.medications(BASELINE)
    assert metformin.source_resource_type == "MedicationAdministration"
    assert metformin.occurred_on.isoformat() == "2026-07-18"
    assert metformin.attributes == {
        "dose": "One 500 mg tablet by mouth (synthetic)",
        "route": "Oral route",
    }
    assert metformin.codes[0].system == CodeSystem.RXNORM and metformin.codes[0].code == "861007"


def test_a_complete_snapshot_has_no_gaps() -> None:
    client = RecordsClient({"u_1": BASELINE})
    assert tools.record_gaps(client, "u_1") == {}
    assert tools.claims_gap(client, "u_1") is None


def test_a_failed_source_makes_every_category_incomplete() -> None:
    client = RecordsClient({"u_1": snapshot("patient-demo-source-unavailable")})
    gaps = tools.record_gaps(client, "u_1")
    assert set(gaps) == {RecordType.ENCOUNTER, RecordType.PROCEDURE, RecordType.MEDICATION}
    assert set(gaps.values()) == {"sync_partial"}


def test_unshared_categories_are_not_complete() -> None:
    client = RecordsClient({"u_1": snapshot("patient-demo-consent-partial")})
    gaps = tools.record_gaps(client, "u_1")
    assert gaps == {RecordType.ENCOUNTER: "not_shared", RecordType.PROCEDURE: "not_shared"}
    assert tools.claims_gap(client, "u_1") == "not_shared"


def test_tools_read_the_snapshot_for_records_clients() -> None:
    client = RecordsClient({"u_1": BASELINE, "u_none": None})
    assert tools.get_patient_profile(client, "u_1").payer_name.startswith("Example Health")
    assert [c.external_claim_id for c in tools.get_patient_claims(client, "u_1")] == [
        "claim-demo-001"
    ]
    assert tools.get_patient_profile(client, "u_none") is None
    assert tools.get_patient_claims(client, "u_none") == []
    assert set(tools.record_gaps(client, "u_none").values()) == {"no_records"}


def test_odd_records_never_raise() -> None:
    odd = Snapshot.from_body(
        "u_odd",
        {
            "categories": ["claims", "labs"],
            "data": {
                "labs": [{"id": "rec_x", "date": "not a date", "codes": [{"system": None}]}],
                "claims": [{"resourceType": "ExplanationOfBenefit", "id": "rec_eob"}],
            },
            "meta": {"syncStatus": "complete"},
        },
    )
    [lab] = records.procedures(odd)
    assert lab.occurred_on is None and lab.codes == []
    assert records.claims(odd) == []  # no service date -> not placed on a timeline


@pytest.mark.parametrize(
    ("value", "state"), [("CA", "CA"), ("ca", "CA"), ("California", "CA"), ("US", None)]
)
def test_us_state(value: str, state: str | None) -> None:
    assert us_state(value) == state
