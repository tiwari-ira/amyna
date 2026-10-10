"""Deterministic sensitive-request detection and agent guardrails."""

import pytest

from app.errors import SensitiveDataRejected
from app.services.security_policy import (
    AllowedFacts,
    check_agent_utterance,
    contains_sensitive_data,
    detect_sensitive_request,
    reject_sensitive_notes,
)
from claimshield_contracts.enums import HumanReason


@pytest.mark.parametrize(
    ("utterance", "category"),
    [
        ("Can you provide the member's Social Security number?", "ssn"),
        ("I'll need the SSN on file.", "ssn"),
        ("What are the last four of the member's social?", "ssn"),
        ("Please read me the last 4 digits of your SSN.", "ssn"),
        ("Can you confirm the member's date of birth?", "dob_verification"),
        ("What's the DOB?", "dob_verification"),
        ("What is the PIN on the account?", "pin"),
        ("I need the account password to continue.", "password"),
        ("Please read me the verification code we just texted.", "auth_code"),
        ("Can you give me the one-time code?", "auth_code"),
        ("What's the credit card number?", "payment_card"),
        ("And the routing number for the refund?", "bank_account"),
    ],
)
def test_detects_sensitive_requests(utterance: str, category: str) -> None:
    found = detect_sensitive_request(utterance)
    assert found is not None, utterance
    assert found.category == category


def test_ssn_maps_to_identity_verification_and_pin_to_credentials() -> None:
    assert detect_sensitive_request("SSN please").reason == HumanReason.IDENTITY_VERIFICATION
    assert detect_sensitive_request("PIN please").reason == HumanReason.CREDENTIAL_REQUEST


@pytest.mark.parametrize(
    "utterance",
    [
        "Thank you for calling Acme Health Plan member services, this is Sam.",
        "What is the claim number and the date of service?",
        "Your reference number is MOCK-REF-48213.",
        "I'll text you a confirmation code once the request is filed.",
        "Which charges are you disputing?",
    ],
)
def test_ignores_normal_conversation(utterance: str) -> None:
    assert detect_sensitive_request(utterance) is None


def test_sensitive_values_detected_in_free_text() -> None:
    assert contains_sensitive_data("my ssn is 123-45-6789") == ["ssn"]
    assert "pin" in contains_sensitive_data("PIN is 4821")
    assert "password" in contains_sensitive_data("password: hunter2")
    assert contains_sensitive_data("Verified directly with the representative.") == []


def test_reject_sensitive_notes() -> None:
    reject_sensitive_notes("Patient verified identity directly with the rep.")
    with pytest.raises(SensitiveDataRejected) as excinfo:
        reject_sensitive_notes("Their SSN is 123 45 6789")
    assert excinfo.value.extra["categories"] == ["ssn"]


FACTS = AllowedFacts(
    codes=frozenset({"70553", "01922", "J2060", "G0463", "J3490"}),
    text="MRI brain without and with contrast; Anesthesia for non-invasive imaging; "
    "Injection, lorazepam hydrochloride, 2 mg; CLM-2026-0915-001",
)


def test_agent_may_state_grounded_codes_and_terms() -> None:
    check = check_agent_utterance(
        "Line 3 is anesthesia, code 01922, for $780.00. Reference MOCK-REF-48213.", FACTS
    )
    assert check.allowed, check.violations


def test_agent_blocked_on_unknown_code() -> None:
    check = check_agent_utterance("The provider also billed 99215.", FACTS)
    assert not check.allowed and "code:99215" in check.violations


def test_agent_blocked_on_ungrounded_medical_fact() -> None:
    check = check_agent_utterance("The member was given propofol.", FACTS)
    assert not check.allowed and "term:propofol" in check.violations


def test_agent_blocked_from_saying_secrets() -> None:
    check = check_agent_utterance("The SSN is 123-45-6789.", FACTS)
    assert "sensitive:ssn" in check.violations


def test_insurer_reference_numbers_are_not_mistaken_for_secrets() -> None:
    """A bare 9-digit reference and a long non-card number are fine; real secrets aren't."""
    from app.services.security_policy import reject_sensitive_reference

    reject_sensitive_reference("482130917")  # 9 digits, no SSN separators
    reject_sensitive_reference("2026091500000123")  # 16 digits, fails the card checksum
    with pytest.raises(SensitiveDataRejected):
        reject_sensitive_reference("123-45-6789")
    assert contains_sensitive_data("card 4111 1111 1111 1111") == ["card_number"]
    assert contains_sensitive_data("claim 2026091500000123") == []
