"""The production-like demo: a made-up itemized bill for FinchNode's synthetic sandbox
patient (scenario baseline-adult) checked against that patient's FinchNode records.

Records come from a saved copy of FinchNode's public demo snapshot; the bill is
scripts/examples/baseline_adult_bill.json (two planted problems)."""

import json
from pathlib import Path

from claimshield_contracts.api import ClaimUpload
from claimshield_contracts.enums import ClaimSource, EvidenceSource, IssueType, LineMatchStatus
from claimshield_contracts.models import Claim
from finchnode import records
from finchnode.records import Snapshot
from intelligence.codes.resolver import default_resolver
from intelligence.discrepancy.engine import analyze_claim

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = Snapshot.from_body(
    "u_0123456789abcdef",
    json.loads((ROOT / "finchnode/tests/fixtures/records_patient-demo-001.json").read_text()),
)
BILL = json.loads((ROOT / "scripts/examples/baseline_adult_bill.json").read_text())


def _claim() -> Claim:
    upload = ClaimUpload.model_validate({k: v for k, v in BILL.items() if not k.startswith("_")})
    return Claim(
        claim_id="clm_demo",
        case_id="case_demo",
        external_claim_id=upload.external_claim_id,
        provider_name=upload.provider_name,
        facility_name=upload.facility_name,
        date_of_service=upload.date_of_service,
        total_billed_cents=sum(line.billed_cents for line in upload.lines),
        source=ClaimSource.UPLOAD,
        lines=[
            {"line_id": f"lin_{line.line_number}", "claim_id": "clm_demo", **line.model_dump()}
            for line in upload.lines
        ],
    )


def _records() -> list:
    return [
        *records.encounters(SNAPSHOT),
        *records.procedures(SNAPSHOT),
        *records.medications(SNAPSHOT),
    ]


def test_finchnode_records_support_the_real_charges_and_catch_the_planted_ones() -> None:
    result = analyze_claim(_claim(), _records(), [], resolver=default_resolver())
    status = {a.line_number: a.status for a in result.line_assessments}
    assert status[1] == LineMatchStatus.MATCHED  # wellness visit <- FinchNode encounter
    assert status[2] == LineMatchStatus.MATCHED  # HbA1c <- LOINC 4548-4 on 2026-07-18
    assert status[4] == LineMatchStatus.MATCHED  # lipid panel <- LOINC 13457-7
    assert status[5] == LineMatchStatus.MATCHED  # glucose <- LOINC 2345-7
    issues = {f.line_number: f.issue_type for f in result.findings}
    assert issues == {3: IssueType.DUPLICATE_CHARGE, 6: IssueType.UNSUPPORTED_CHARGE}
    mri = next(f for f in result.findings if f.line_number == 6)
    assert mri.human_confirmation_required  # the patient is asked, never assumed
    assert "FinchNode records for 2026-07-18" in mri.explanation
    assert any(ref.source == EvidenceSource.FINCHNODE_RECORD for ref in mri.evidence_refs)


def test_incomplete_records_are_never_cited_as_evidence_of_absence() -> None:
    result = analyze_claim(
        _claim(), _records(), [], resolver=default_resolver(), records_complete=False
    )
    mri = next(f for f in result.findings if f.line_number == 6)
    assert mri.issue_type == IssueType.UNSUPPORTED_CHARGE and mri.human_confirmation_required
    assert "FinchNode records for" not in mri.explanation
    assert "could not be loaded" in mri.explanation
    assert not any(ref.source == EvidenceSource.FINCHNODE_RECORD for ref in mri.evidence_refs)
    assert mri.confidence < 0.7  # one fewer source behind the absence
