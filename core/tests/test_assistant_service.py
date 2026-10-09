"""The Amyna language layer: plain-English in/out, structured and never authoritative."""

import pytest

from app.services.assistant_service import (
    ALLOWED_ASSISTANT_ACTIONS,
    AssistantService,
    FindingInput,
    PatientIntent,
)


@pytest.fixture
def assistant() -> AssistantService:
    return AssistantService()  # default: deterministic offline engine


def test_understands_a_plain_bill_complaint(assistant: AssistantService) -> None:
    understood = assistant.understand_patient_message("I think this hospital bill is wrong")
    assert understood.intent in (PatientIntent.REVIEW_BILL, PatientIntent.DISPUTE_CHARGE)
    # Amyna minimizes questions: it asks for the bill, not a claim number.
    assert understood.suggested_action in ALLOWED_ASSISTANT_ACTIONS
    assert understood.follow_up_question is not None
    assert "bill" in understood.follow_up_question.lower()


def test_extracts_a_candidate_claim_number(assistant: AssistantService) -> None:
    understood = assistant.understand_patient_message(
        "Please review my bill. The claim is CLM-2026-0915-001."
    )
    claim = understood.candidate_claim_number
    assert claim is not None and claim.value == "CLM-2026-0915-001"
    # Anything extracted is a candidate: the backend must verify it, identity included.
    assert claim.needs_confirmation is True


def test_a_name_is_never_used_to_identify_a_patient(assistant: AssistantService) -> None:
    understood = assistant.understand_patient_message("My name is Mary and this bill looks wrong")
    name = understood.candidate_patient_name
    assert name is not None and name.value == "Mary"
    # The language layer only reports a candidate; it NEVER searches patients by name.
    assert name.needs_confirmation is True


def test_explains_technical_terms_in_plain_english(assistant: AssistantService) -> None:
    term = assistant.explain_term("What is a claim number?")
    assert term.term == "What is a claim number?"
    assert "ID" in term.plain_language and "insurance" in term.plain_language.lower()


def test_extract_bill_information_fields_and_missing(assistant: AssistantService) -> None:
    text = "Memorial Hospital\nClaim: CLM-2026-0915-001\nAmount: $842.60"
    bill = assistant.extract_bill_information(text)
    assert bill.provider is not None and "Memorial" in bill.provider.value
    assert bill.claim_number is not None
    assert bill.amount_cents is not None and bill.amount_cents.value == "84260"
    assert bill.missing_fields == []


def test_extract_bill_information_reports_missing_fields(assistant: AssistantService) -> None:
    bill = assistant.extract_bill_information("Memorial Hospital")
    assert "claim number" in bill.missing_fields and "amount" in bill.missing_fields


def test_extract_visit_information_finds_a_duplicate_candidate(assistant: AssistantService) -> None:
    visit = assistant.extract_visit_information(
        "I went to the hospital last Tuesday for an MRI, but this bill says they did two."
    )
    assert "mri" in visit.services_received
    assert visit.discrepancy_candidates, "a 'two X' memo should flag a duplicate candidate"


def test_summarize_findings_never_invents_facts(assistant: AssistantService) -> None:
    findings = [
        FindingInput(
            title="Duplicate MRI",
            explanation="Your visit shows one MRI, the bill shows two.",
            amount_cents=84260,
        )
    ]
    summary = assistant.summarize_findings(findings)
    assert summary.count == 1
    assert "$842.60" in summary.items[0]
    assert "MRI" in summary.items[0]
    empty = assistant.summarize_findings([])
    assert empty.count == 0 and "didn't find any errors" in empty.plain_summary


def test_call_summary_only_uses_the_authorized_dispute(assistant: AssistantService) -> None:
    call = assistant.prepare_patient_call_summary(
        [FindingInput(title="Unlisted drug", explanation="No such drug in your records.")],
        payer="Acme Insurance",
    )
    assert "Acme Insurance" in call.purpose
    assert "Unlisted drug" in call.plain_summary
    # Amyna never handles sensitive verification: the patient does, directly.
    assert "Social Security" in call.plain_summary
    assert "never share" in call.plain_summary


def test_every_suggested_action_is_allowlisted(assistant: AssistantService) -> None:
    for message in (
        "I got this bill and I don't think it's right.",
        "They charged me for something I don't remember getting.",
        "Call them for me.",
        "What is a claim number?",
        "I went to the hospital for an MRI.",
        "hello",
    ):
        action = assistant.understand_patient_message(message).suggested_action
        assert action in ALLOWED_ASSISTANT_ACTIONS
