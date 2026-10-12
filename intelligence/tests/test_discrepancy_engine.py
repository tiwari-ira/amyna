"""Discrepancy engine on the seed scenario plus targeted rule tests."""

from datetime import date

from claimshield_contracts.demo import load_demo_scenario
from claimshield_contracts.enums import (
    ClaimSource,
    CodeSystem,
    DisputeAction,
    EvidenceKind,
    FindingStatus,
    IssueType,
    LineMatchStatus,
    PatientAnswer,
    RecordType,
)
from claimshield_contracts.models import (
    Claim,
    ClaimLine,
    CodeRef,
    EvidenceItem,
    HealthRecord,
    Transcript,
)
from intelligence.codes.hospital_ingest import HospitalPriceIndex, StandardCharge, find_price_index
from intelligence.codes.resolver import CodeResolver
from intelligence.discrepancy.engine import analyze_claim
from intelligence.extraction.extractor import to_evidence_items
from intelligence.extraction.rule_based import RuleBasedExtractor

DOS = date(2026, 9, 15)


def _seed(claim: Claim, records, transcript: Transcript, resolver: CodeResolver):  # type: ignore[no-untyped-def]
    evidence = to_evidence_items(
        RuleBasedExtractor().extract(transcript),
        transcript,
        case_id="case_demo",
        visit_id="vis_demo",
        resolver=resolver,
    )
    return analyze_claim(
        claim,
        records,
        evidence,
        resolver=resolver,
        visit_id="vis_demo",
        price_index=find_price_index(claim.facility_name),
    )


def test_seed_scenario_matches_expected(claim, records, transcript, resolver) -> None:  # type: ignore[no-untyped-def]
    expected = load_demo_scenario()["expected"]
    result = _seed(claim, records, transcript, resolver)
    got = [
        {
            "line_number": f.line_number,
            "issue_type": f.issue_type.value,
            "amount_cents": f.amount_cents,
            "human_confirmation_required": f.human_confirmation_required,
        }
        for f in result.findings
    ]
    assert got == expected["findings"]
    assert result.total_flagged_cents == expected["total_flagged_cents"]
    matched = [
        a.line_number for a in result.line_assessments if a.status == LineMatchStatus.MATCHED
    ]
    assert matched == expected["matched_line_numbers"]


def test_seed_questions_are_clean(claim, records, transcript, resolver) -> None:  # type: ignore[no-untyped-def]
    result = _seed(claim, records, transcript, resolver)
    anesthesia = result.findings[0]
    assert anesthesia.confirmation.question == (
        "Did you receive anesthesia during your visit on September 15, 2026?"
    )
    assert anesthesia.confirmation.effects[PatientAnswer.NO] == FindingStatus.CONFIRMED
    assert anesthesia.status == FindingStatus.OPEN
    assert anesthesia.recommended_action == DisputeAction.REMOVE_CHARGE
    duplicate = result.findings[1]
    assert duplicate.status == FindingStatus.CONFIRMED and duplicate.confirmation is None
    unknown = result.findings[2]
    assert "Other than lorazepam and gadolinium contrast" in unknown.confirmation.question
    assert unknown.recommended_action == DisputeAction.REQUEST_ITEMIZATION


def test_matching_prefers_source_record_codes(claim, records, resolver) -> None:  # type: ignore[no-untyped-def]
    result = analyze_claim(claim, records, [], resolver=resolver)
    mri = next(a for a in result.line_assessments if a.line_number == 1)
    assert mri.status == LineMatchStatus.MATCHED
    assert mri.evidence_refs[0].ref_id == "fn:proc-mri-2026-09-15"


def _line(code: str, system: CodeSystem, cents: int, n: int = 1) -> ClaimLine:
    return ClaimLine(
        line_id=f"lin_{n}",
        claim_id="clm_t",
        line_number=n,
        code=CodeRef(system=system, code=code),
        description=code,
        date_of_service=DOS,
        billed_cents=cents,
    )


def _claim(*lines: ClaimLine) -> Claim:
    return Claim(
        claim_id="clm_t",
        case_id="case_t",
        external_claim_id="T-1",
        date_of_service=DOS,
        total_billed_cents=sum(line.billed_cents for line in lines),
        source=ClaimSource.UPLOAD,
        lines=list(lines),
    )


def test_explicit_negation_needs_no_patient_question(resolver: CodeResolver) -> None:
    negated = EvidenceItem(
        evidence_id="evd_1",
        case_id="case_t",
        visit_id="vis_t",
        kind=EvidenceKind.SERVICE,
        concept="anesthesia_imaging",
        description="NOT anesthesia",
        negated=True,
        confidence=0.9,
        extractor="rules",
    )
    result = analyze_claim(
        _claim(_line("01922", CodeSystem.CPT, 78000)),
        [],
        [negated],
        resolver=resolver,
        visit_id="vis_t",
    )
    [finding] = result.findings
    assert finding.confidence == 0.95 and not finding.human_confirmation_required


def test_code_mismatch_within_family(resolver: CodeResolver) -> None:
    record = HealthRecord(
        record_id="fn:p1",
        record_type=RecordType.PROCEDURE,
        patient_id="p",
        occurred_on=DOS,
        display="MRI brain without contrast",
        codes=[CodeRef(system=CodeSystem.CPT, code="70551")],
        source_resource_type="Procedure",
        source_id="p1",
    )
    result = analyze_claim(
        _claim(_line("70553", CodeSystem.CPT, 240000)), [record], [], resolver=resolver
    )
    [finding] = result.findings
    assert finding.issue_type == IssueType.CODE_MISMATCH
    assert finding.recommended_action == DisputeAction.CORRECT_CODE


def test_price_above_published_gross(resolver: CodeResolver) -> None:
    index = HospitalPriceIndex("Test Hospital", "2026-01-01")
    index.add(StandardCharge(CodeSystem.HCPCS, "G0463", "visit", "outpatient", 10000, 8000))
    encounter = HealthRecord(
        record_id="fn:e1",
        record_type=RecordType.ENCOUNTER,
        patient_id="p",
        occurred_on=DOS,
        display="Visit",
        source_resource_type="Encounter",
        source_id="e1",
    )
    result = analyze_claim(
        _claim(_line("G0463", CodeSystem.HCPCS, 25000)),
        [encounter],
        [],
        resolver=resolver,
        price_index=index,
    )
    [finding] = result.findings
    assert finding.issue_type == IssueType.PRICE_ABOVE_PUBLISHED
    assert not finding.human_confirmation_required


def test_unknown_codes_are_asked_not_assumed(resolver: CodeResolver) -> None:
    result = analyze_claim(_claim(_line("99999", CodeSystem.CPT, 100)), [], [], resolver=resolver)
    assert result.line_assessments[0].status == LineMatchStatus.UNKNOWN
    [finding] = result.findings
    assert finding.human_confirmation_required
    assert finding.confirmation is not None
    assert finding.confirmation.question == (
        "Did you receive 99999 during your visit on September 15, 2026?"
    )
