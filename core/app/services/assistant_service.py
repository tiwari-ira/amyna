"""Amyna's LLM / language layer.

Amyna exists to remove as much work, confusion and technical knowledge from the
patient as possible. This module is the single wrapper all natural-language
understanding and explanation goes through, so patient language handling is
consistent, testable and -- above all -- *non-authoritative*.

The Amyna language layer MAY:
  * understand natural language (plain-English, voice-memo transcripts);
  * explain insurance terms in plain English;
  * extract *candidate* identifiers (claim #, provider, date, amount, name);
  * infer what the patient is trying to do and ask one simple follow-up;
  * summarize findings and draft conversational responses;
  * choose a next step ONLY from an explicit allowlist of workflow nudges.

The language layer MUST NOT (and, by construction in this service, CANNOT):
  * decide who the patient is, or select a patient record;
  * decide whether consent/authorization exists;
  * decide whether private records may be accessed;
  * decide whether a dispute may be submitted or a call placed;
  * decide what number may be dialed, or what sensitive info may be disclosed.

Those belong to Core's deterministic authorization layer (``auth.py``,
``state_machine.py``, ``security_policy.py``). Everything this service returns
is a *candidate* the backend verifies before acting. The service is stateless
and holds no database handle, no credentials and no record data: it only ever
receives the task-specific, authorization-gated context a caller chooses to
pass in. Extracted names/IDs are always returned with ``needs_confirmation``
set, and never used to look anything up.

The default :class:`HeuristicLanguageEngine` is deterministic and offline (no
external calls, no PHI sent anywhere). A real model can be slotted behind the
:class:`LanguageEngine` protocol; it keeps the same structured I/O and the
same hard policy boundary.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, Field


class PatientIntent(StrEnum):
    """What the patient is trying to do, inferred from their message."""

    REVIEW_BILL = "review_bill"
    QUESTION_ABOUT_BILL = "question_about_bill"
    DISPUTE_CHARGE = "dispute_charge"
    REQUEST_CALL = "request_call"
    EXPLAIN_TERM = "explain_term"
    RECORD_VISIT = "record_visit"
    PROVIDE_BILL = "provide_bill"
    UNKNOWN = "unknown"


class AssistantAction(StrEnum):
    """The allowlisted workflow nudges the language layer may *suggest*."""

    REQUEST_BILL = "request_bill"
    START_CONSENT = "start_consent"
    RECORD_VISIT = "record_visit"
    REVIEW_BILL = "review_bill"
    EXPLAIN_TERM = "explain_term"
    ASK_CLARIFYING = "ask_clarifying"
    PREPARE_DISPUTE_REVIEW = "prepare_dispute_review"


ALLOWED_ASSISTANT_ACTIONS: frozenset[AssistantAction] = frozenset(AssistantAction)


class CandidateValue(BaseModel):
    """A value the language layer extracted as a *candidate*. Never authoritative."""

    value: str
    confidence: float = Field(ge=0.0, le=1.0)
    # Always True: the backend must confirm with an authorized lookup / patient.
    needs_confirmation: bool = True


class UnderstoodMessage(BaseModel):
    """Structured result of understanding one free-form patient message."""

    intent: PatientIntent
    candidate_claim_number: CandidateValue | None = None
    candidate_patient_name: CandidateValue | None = None
    candidate_provider: CandidateValue | None = None
    candidate_amount_cents: CandidateValue | None = None
    suggested_action: AssistantAction
    follow_up_question: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)


class BillLineExtraction(BaseModel):
    description: str | None = None
    code: str | None = None
    amount_cents: int | None = None


class BillExtraction(BaseModel):
    """Everything Amyna pulled from a photo/PDF/text of a bill."""

    provider: CandidateValue | None = None
    patient_name: CandidateValue | None = None
    claim_number: CandidateValue | None = None
    amount_cents: CandidateValue | None = None
    insurer: CandidateValue | None = None
    service_date: CandidateValue | None = None
    line_items: list[BillLineExtraction] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)


class VisitExtraction(BaseModel):
    """What Amyna learned from a spoken/typed visit memo."""

    occurred_at: CandidateValue | None = None
    facility: CandidateValue | None = None
    services_received: list[str] = Field(default_factory=list)
    services_not_received: list[str] = Field(default_factory=list)
    discrepancy_candidates: list[str] = Field(default_factory=list)


class TermExplanation(BaseModel):
    term: str
    plain_language: str


class ClarifyingQuestion(BaseModel):
    question: str
    field: str
    options: list[str] = Field(default_factory=list)


class FindingsSummary(BaseModel):
    """A patient-facing summary built from structured findings; never invents facts."""

    count: int
    items: list[str] = Field(default_factory=list)
    plain_summary: str


class CallSummary(BaseModel):
    """A plain-English pre-call summary built only from an authorized dispute."""

    purpose: str
    points: list[str] = Field(default_factory=list)
    plain_summary: str


class FindingInput(BaseModel):
    """Structured evidence for one finding -- the ONLY allowed source of facts."""

    title: str
    explanation: str
    amount_cents: int | None = None


class LanguageEngine(Protocol):
    """The pluggable LLM behind the language layer. Structured in/out only."""

    def understand(self, message: str) -> UnderstoodMessage: ...
    def extract_bill(self, text: str) -> BillExtraction: ...
    def extract_visit(self, transcript: str) -> VisitExtraction: ...
    def explain_term(self, term: str) -> TermExplanation: ...
    def clarify(self, message: str, understood: UnderstoodMessage) -> ClarifyingQuestion | None: ...


_CLAIM = re.compile(r"\b(CLM[-\s]?\d[\d-]{4,})\b", re.IGNORECASE)
_MONEY = re.compile(r"\$?\b(\d{1,6}(?:,\d{3})*(?:\.\d{2}))\b")
_NAME_CLAIM = re.compile(
    r"\bmy name is ([a-z][a-z'-]*(?: [a-z][a-z'-]*){0,2}?)"
    r"(?=\s+(?:and|the|this|that|but|i|it|my|a|an|to|for|with)\b|[.!?,;]|$)",
    re.IGNORECASE,
)
_PROVIDER = re.compile(r"\b(hospital|clinic|medical|health|center|centre)\b", re.IGNORECASE)
_DUPLICATE = re.compile(r"\b(two|twice|duplicate)\b", re.IGNORECASE)
_SERVICES = ("mri", "x-ray", "ct scan", "ultrasound", "blood test", "ekg")

_REVIEW = ("review", "check", "look", "wrong", "right", "duplicate", "overcharg")
_DISPUTE = ("dispute", "fight", "fix", "remove", "shouldn't have", "never got", "didn't get")
_CALL = ("call", "phone", "speak to", "talk to")
_TERM = ("what is", "what's a", "what does", "mean", "claim number", "member id", "payer")
_VISIT = ("went to", "at the hospital", "appointment", "visit", "saw the doctor")
_BILL_WORDS = ("bill", "invoice", "statement", "charge", "charged")
_GLOSSARY: dict[str, str] = {
    "claim number": (
        "A claim number is the ID your insurance company assigns to this bill. "
        "It's usually printed near the top. I can find it on the bill for you."
    ),
    "member id": (
        "A member ID is the number on your insurance card that identifies you. "
        "If you send me a photo of the card or the bill, I can often find it."
    ),
    "payer": "The payer is the insurance company that is being asked to pay the bill.",
    "cpt": (
        "A CPT code is a 5-digit billing code for a medical service. You don't need "
        "to understand the codes -- I compare them against your visit records."
    ),
    "hcpcs": (
        "An HCPCS code is a billing code similar to a CPT code. You don't need the "
        "codes themselves -- I check them against your records."
    ),
    "eob": (
        "An EOB (explanation of benefits) is a statement from your insurer showing "
        "what was billed, what they paid, and what you owe. It is not a bill."
    ),
}


def _cents(candidate: str) -> int:
    return int(round(float(candidate.replace(",", "")) * 100))


@dataclass
class HeuristicLanguageEngine:
    """Deterministic offline engine: the safe default and test path."""

    _reserved: dict[str, object] = field(default_factory=dict, repr=False)

    def understand(self, message: str) -> UnderstoodMessage:
        text = message.strip()
        low = text.lower()
        intent = self._intent(low)
        claim = _CLAIM.search(text)
        amount = _MONEY.search(text)
        name = _NAME_CLAIM.search(text)
        understood = UnderstoodMessage(
            intent=intent,
            candidate_claim_number=(
                CandidateValue(value=claim.group(1).upper(), confidence=0.97) if claim else None
            ),
            candidate_patient_name=(
                CandidateValue(value=name.group(1).strip().title(), confidence=0.6)
                if name
                else None
            ),
            candidate_amount_cents=(
                CandidateValue(value=str(_cents(amount.group(1))), confidence=0.85)
                if amount
                else None
            ),
            suggested_action=self._action(intent),
            confidence=0.2 if intent == PatientIntent.UNKNOWN else 0.9,
        )
        understood.follow_up_question = self._follow_up(understood)
        return understood

    def extract_bill(self, text: str) -> BillExtraction:
        claim = _CLAIM.search(text)
        amount = _MONEY.search(text)
        provider = None
        for line in text.splitlines():
            if line.strip() and _PROVIDER.search(line):
                provider = CandidateValue(value=line.strip()[:200], confidence=0.6)
                break
        extraction = BillExtraction(
            provider=provider,
            claim_number=(
                CandidateValue(value=claim.group(1).upper(), confidence=0.97) if claim else None
            ),
            amount_cents=(
                CandidateValue(value=str(_cents(amount.group(1))), confidence=0.85)
                if amount
                else None
            ),
        )
        extraction.missing_fields = [
            field_name
            for field_name, value in (
                ("provider", extraction.provider),
                ("claim number", extraction.claim_number),
                ("amount", extraction.amount_cents),
            )
            if value is None
        ]
        return extraction

    def extract_visit(self, transcript: str) -> VisitExtraction:
        received: list[str] = []
        for service in _SERVICES:
            count = len(re.findall(rf"\b{re.escape(service)}\b", transcript, re.IGNORECASE))
            received.extend([service] * min(count, 3))
        disputed: list[str] = []
        if _DUPLICATE.search(transcript) and received:
            disputed.append(f"The memo suggests {received[0]} may have been billed more than once.")
        return VisitExtraction(services_received=received, discrepancy_candidates=disputed)

    def explain_term(self, term: str) -> TermExplanation:
        key = term.strip().lower().rstrip("?").replace("a ", "").replace("the ", "")
        plain: str | None = None
        for candidate, text in _GLOSSARY.items():
            if key and (candidate in key or key in candidate):
                plain = text
                break
        return TermExplanation(
            term=term,
            plain_language=plain
            or "That's a piece of insurance terminology. Send me the bill or tell me what "
            "you're seeing, and I'll explain exactly what it means for you.",
        )

    def clarify(self, message: str, understood: UnderstoodMessage) -> ClarifyingQuestion | None:
        if understood.candidate_claim_number:
            return None
        if understood.intent in (PatientIntent.REVIEW_BILL, PatientIntent.DISPUTE_CHARGE):
            return ClarifyingQuestion(
                question=(
                    "The claim number is the ID your insurer uses for this bill. It's usually "
                    "printed near the top. If you send me a photo of the bill, I can find it "
                    "for you."
                ),
                field="claim_number",
                options=["Send a photo of the bill"],
            )
        return None

    def _intent(self, low: str) -> PatientIntent:
        if any(word in low for word in _TERM):
            return PatientIntent.EXPLAIN_TERM
        if any(word in low for word in _CALL):
            return PatientIntent.REQUEST_CALL
        if any(word in low for word in _VISIT):
            return PatientIntent.RECORD_VISIT
        if any(word in low for word in _DISPUTE):
            return PatientIntent.DISPUTE_CHARGE
        if any(word in low for word in _REVIEW) or any(word in low for word in _BILL_WORDS):
            return PatientIntent.REVIEW_BILL
        return PatientIntent.UNKNOWN

    def _action(self, intent: PatientIntent) -> AssistantAction:
        return {
            PatientIntent.REVIEW_BILL: AssistantAction.REQUEST_BILL,
            PatientIntent.PROVIDE_BILL: AssistantAction.REVIEW_BILL,
            PatientIntent.DISPUTE_CHARGE: AssistantAction.PREPARE_DISPUTE_REVIEW,
            PatientIntent.REQUEST_CALL: AssistantAction.PREPARE_DISPUTE_REVIEW,
            PatientIntent.EXPLAIN_TERM: AssistantAction.EXPLAIN_TERM,
            PatientIntent.RECORD_VISIT: AssistantAction.RECORD_VISIT,
            PatientIntent.QUESTION_ABOUT_BILL: AssistantAction.ASK_CLARIFYING,
            PatientIntent.UNKNOWN: AssistantAction.ASK_CLARIFYING,
        }[intent]

    def _follow_up(self, understood: UnderstoodMessage) -> str | None:
        if understood.intent == PatientIntent.REVIEW_BILL and not understood.candidate_claim_number:
            return (
                "I can look into it. Send me a photo or PDF of the bill, or just tell me "
                "what happened."
            )
        return None


def compose_reply(assistant: AssistantService, message: str) -> tuple[UnderstoodMessage, str]:
    """Turn an understood message into ONE plain-English, ready-to-send reply.

    Core composes replies; conversational surfaces (Relay, ASI:One) stay thin.
    Replies never ask for identifiers directly and never mention workflow jargon.
    """
    understood = assistant.understand_patient_message(message)
    details: list[str] = []
    if understood.candidate_claim_number:
        details.append(f"the claim number {understood.candidate_claim_number.value}")
    if understood.candidate_amount_cents:
        amount = int(understood.candidate_amount_cents.value) / 100
        details.append(f"the amount ${amount:.2f}")
    seen = (" I can already see " + " and ".join(details) + " on this bill.") if details else ""
    if understood.intent == PatientIntent.EXPLAIN_TERM:
        reply = assistant.explain_term(message).plain_language
    elif understood.follow_up_question:
        reply = understood.follow_up_question + seen
    elif understood.intent == PatientIntent.DISPUTE_CHARGE:
        reply = (
            "I can look into that charge for you. Tell me a little about what happened, "
            "or send me a photo of the bill." + seen
        )
    elif understood.intent == PatientIntent.REQUEST_CALL:
        reply = (
            "I can contact your insurance company for you. First, let's review the bill "
            "together -- send me a photo of it or tell me what happened." + seen
        )
    elif understood.intent == PatientIntent.RECORD_VISIT:
        reply = (
            "Got it. If you recorded your visit, send me the recording and I'll note what "
            "actually happened. Otherwise, tell me what the bill says and we'll compare." + seen
        )
    else:
        reply = (
            "I'm here to help with your medical bills. Tell me in your own words what's "
            "going on -- for example 'this bill looks wrong' -- or send me a photo of it." + seen
        )
    return understood, reply


def _fmt_amount(cents: int | None) -> str:
    return f" (${cents / 100:.2f})" if cents is not None else ""


class AssistantService:
    """The Amyna language layer. Stateless, non-authoritative, structured I/O."""

    def __init__(self, engine: LanguageEngine | None = None) -> None:
        self._engine: LanguageEngine = engine or HeuristicLanguageEngine()

    def understand_patient_message(self, message: str) -> UnderstoodMessage:
        return self._engine.understand(message)

    def extract_bill_information(self, text: str) -> BillExtraction:
        return self._engine.extract_bill(text)

    def extract_visit_information(self, transcript: str) -> VisitExtraction:
        return self._engine.extract_visit(transcript)

    def explain_term(self, term: str) -> TermExplanation:
        return self._engine.explain_term(term)

    def generate_clarifying_question(
        self, message: str, understood: UnderstoodMessage
    ) -> ClarifyingQuestion | None:
        return self._engine.clarify(message, understood)

    def summarize_findings(self, findings: list[FindingInput]) -> FindingsSummary:
        """Only restates the caller's authoritative, structured findings."""
        items = [
            (
                f.title
                + _fmt_amount(f.amount_cents)
                + (f" -- {f.explanation}" if f.explanation else "")
            )
            for f in findings
        ]
        count = len(items)
        if count == 0:
            plain = "I checked this bill against your records and didn't find any errors."
        elif count == 1:
            plain = f"I found 1 charge that may be incorrect: {items[0]}"
        else:
            plain = f"I found {count} charges that may be incorrect."
        return FindingsSummary(count=count, items=items, plain_summary=plain)

    def explain_dispute(self, findings: list[FindingInput]) -> str:
        summary = self.summarize_findings(findings)
        if summary.count == 0:
            return summary.plain_summary
        joined = "; ".join(summary.items)
        return (
            f"{summary.plain_summary} ({joined}). I can ask your insurance company to "
            "review these charges for you."
        )

    def prepare_patient_call_summary(
        self, findings: list[FindingInput], *, payer: str
    ) -> CallSummary:
        """What Amyna will say -- built only from the authorized, verified dispute."""
        summary = self.summarize_findings(findings)
        points = list(summary.items)
        return CallSummary(
            purpose=f"Ask {payer} to review the flagged charges.",
            points=points,
            plain_summary=(
                f"I'm going to call {payer} and ask them to review "
                f"{summary.count} charge(s): {', '.join(points) if points else 'none'}. "
                "If they ask for private details like your Social Security number, you'll "
                "speak to them directly -- I never share that information."
            ),
        )
