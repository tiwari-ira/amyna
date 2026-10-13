"""Deterministic safety policy for calls and human input (pure functions).

Runs IN CODE on every insurer utterance, independent of the voice model:

- :func:`detect_sensitive_request` -- the insurer asks for an SSN, PIN, password,
  auth/verification code, DOB verification, card or bank details -> pause + human.
- :func:`check_agent_utterance` -- the agent may only mention codes and medical
  terms that appear in the case evidence.
- :func:`reject_sensitive_notes` -- Core never stores secrets it refuses to say.

Keyword rules are deliberately conservative: a false positive pauses the call
(safe); see docs/SECURITY.md for known limitations.
"""

import re
from dataclasses import dataclass

from claimshield_contracts.enums import HumanReason
from claimshield_contracts.tool_api import DisputeCaseView

from ..errors import SensitiveDataRejected

_I = re.IGNORECASE

# Mentioning these at all is enough to pause (category, human reason, patterns).
_ALWAYS_SENSITIVE: tuple[tuple[str, HumanReason, tuple[str, ...]], ...] = (
    (
        "ssn",
        HumanReason.IDENTITY_VERIFICATION,
        (
            r"\bsocial security\b",
            r"\bs\.?s\.?n\b",
            r"\bsocial\b.{0,20}\b(number|digits)\b",
            r"\blast (four|4)\b.{0,30}\b(social|ssn)\b",
        ),
    ),
    (
        "dob_verification",
        HumanReason.IDENTITY_VERIFICATION,
        (r"\bdate of birth\b", r"\bbirth ?date\b", r"\bd\.?o\.?b\b", r"\bwhen were you born\b"),
    ),
    (
        "pin",
        HumanReason.CREDENTIAL_REQUEST,
        (r"\bpin\b", r"\bpin (number|code)\b", r"\bpasscode\b"),
    ),
    ("password", HumanReason.CREDENTIAL_REQUEST, (r"\bpass ?word\b", r"\bpass ?phrase\b")),
    (
        "payment_card",
        HumanReason.CREDENTIAL_REQUEST,
        (r"\b(credit|debit) card\b", r"\bcard number\b", r"\bcvv\b", r"\bsecurity code\b"),
    ),
    (
        "bank_account",
        HumanReason.CREDENTIAL_REQUEST,
        (r"\bbank account\b", r"\brouting number\b", r"\baccount number\b"),
    ),
)

# These only count when phrased as a request (insurers also *give* codes).
_AUTH_CODE_PATTERNS = (
    r"\b(verification|authori[sz]ation|authentication|one[- ]time|access|login) code\b",
    r"\b(otp|2fa|mfa)\b",
    r"\bcode (that )?(we|i) (just )?(sent|texted|emailed)\b",
)
_REQUEST_CUES = (
    r"\bwhat(?:'s| is)\b",
    r"\b(can|could|would) you\b",
    r"\bplease (provide|give|read|confirm|tell|share|say)\b",
    r"\bi (?:will |'ll )?need\b",
    r"\bmay i (have|get)\b",
    r"\bdo you have\b",
    r"\bverify\b",
    r"\?",
)


@dataclass(frozen=True)
class SensitiveRequest:
    category: str
    reason: HumanReason
    matched: str


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def detect_sensitive_request(text: str) -> SensitiveRequest | None:
    """Return the first sensitive request in an insurer utterance, if any."""
    for category, reason, patterns in _ALWAYS_SENSITIVE:
        for pattern in patterns:
            match = re.search(pattern, text, _I)
            if match:
                return SensitiveRequest(category, reason, match.group(0))
    for sentence in _sentences(text):
        for pattern in _AUTH_CODE_PATTERNS:
            match = re.search(pattern, sentence, _I)
            if match and any(re.search(cue, sentence, _I) for cue in _REQUEST_CUES):
                return SensitiveRequest("auth_code", HumanReason.CREDENTIAL_REQUEST, match.group(0))
    return None


_SSN_SHAPED = re.compile(r"(?<![\w-])\d{3}[- ]?\d{2}[- ]?\d{4}(?![\w-])")
# In an insurer *reference number* only the dashed/spaced SSN shape counts: plain
# 9-digit references are common and come from the insurer, not the patient.
_SSN_FORMATTED = re.compile(r"(?<![\w-])\d{3}[- ]\d{2}[- ]\d{4}(?![\w-])")
_PIN = re.compile(r"\bpin\b\D{0,12}\d{4,8}\b", _I)
_PASSWORD = re.compile(r"\bpass ?word\b\s*(is|:|=)\s*\S+", _I)
_CARD_SHAPED = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")


def _luhn_valid(digits: str) -> bool:
    """Every real payment card number passes the Luhn checksum; most other long
    numbers (claim ids, reference numbers) don't."""
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2:
            value = value * 2 - 9 if value > 4 else value * 2
        total += value
    return total % 10 == 0


def contains_sensitive_data(text: str | None, *, reference: bool = False) -> list[str]:
    """Categories of secret *values* present in ``text`` (not just mentions).

    ``reference=True`` is for an insurer reference number: bare 9-digit values are
    allowed there (formatted SSNs still are not)."""
    if not text:
        return []
    found = []
    if (_SSN_FORMATTED if reference else _SSN_SHAPED).search(text):
        found.append("ssn")
    if _PIN.search(text):
        found.append("pin")
    if _PASSWORD.search(text):
        found.append("password")
    if any(_luhn_valid(re.sub(r"\D", "", m.group(0))) for m in _CARD_SHAPED.finditer(text)):
        found.append("card_number")
    return found


def reject_sensitive_reference(text: str | None) -> None:
    """Like reject_sensitive_notes, for an insurer-issued reference number."""
    found = contains_sensitive_data(text, reference=True)
    if found:
        raise SensitiveDataRejected(
            "That reference number looks like a sensitive identifier; it was not stored.",
            categories=found,
        )


def reject_sensitive_notes(text: str | None) -> None:
    """Raise SensitiveDataRejected if free text contains SSNs, PINs, passwords, cards."""
    found = contains_sensitive_data(text)
    if found:
        raise SensitiveDataRejected(
            "Amyna never stores sensitive identifiers. Remove them and try again.",
            categories=found,
        )


# --------------------------------------------------------------------------- agent guardrail

MEDICAL_TERMS = frozenset(
    {
        "anesthesia",
        "anesthetic",
        "antibiotic",
        "biopsy",
        "cancer",
        "chemotherapy",
        "contrast",
        "ct scan",
        "diagnosis",
        "fentanyl",
        "fracture",
        "gadolinium",
        "general anesthesia",
        "hydromorphone",
        "infusion",
        "insulin",
        "intubation",
        "ketamine",
        "lorazepam",
        "midazolam",
        "morphine",
        "mri",
        "oxycodone",
        "propofol",
        "sedation",
        "seizure",
        "stroke",
        "surgery",
        "tumor",
        "ultrasound",
        "x-ray",
    }
)
_CODE_PATTERN = re.compile(
    r"(?<![\w-])(\d{5}|[A-V]\d{4}|[A-TV-Z]\d{2}\.\d{1,4})(?![\w-])", re.IGNORECASE
)

SAFE_FALLBACK_LINE = (
    "I'm sorry, I can only discuss the details documented in this dispute. "
    "I'll check with the member and follow up."
)
HOLD_LINE = (
    "I'm not able to provide that information. The member will verify their identity "
    "with you directly. Please hold for a moment while I connect them."
)


@dataclass(frozen=True)
class AllowedFacts:
    """What the voice agent may say: codes and terms present in case evidence."""

    codes: frozenset[str]
    text: str

    @classmethod
    def from_view(cls, view: DisputeCaseView) -> "AllowedFacts":
        parts: list[str] = [*view.allowed_concepts]
        for finding in view.findings:
            parts.extend([finding.title, finding.explanation])
        if view.dispute:
            parts.extend(item.summary for item in view.dispute.items)
        parts.extend(p for p in (view.claim_number, view.payer_name, view.provider_name) if p)
        return cls(codes=frozenset(c.upper() for c in view.allowed_codes), text=" ".join(parts))


@dataclass(frozen=True)
class UtteranceCheck:
    allowed: bool
    violations: tuple[str, ...]


def check_agent_utterance(text: str, allowed: AllowedFacts) -> UtteranceCheck:
    """Block codes, medical terms and secrets that are not grounded in the case."""
    violations: list[str] = []
    allowed_text = allowed.text.lower()
    for match in _CODE_PATTERN.finditer(text):
        code = match.group(1).upper()
        if code not in allowed.codes and code.lower() not in allowed_text:
            violations.append(f"code:{code}")
    lowered = text.lower()
    for term in sorted(MEDICAL_TERMS):
        if re.search(rf"\b{re.escape(term)}\b", lowered) and term not in allowed_text:
            violations.append(f"term:{term}")
    violations.extend(f"sensitive:{name}" for name in contains_sensitive_data(text))
    return UtteranceCheck(allowed=not violations, violations=tuple(violations))
