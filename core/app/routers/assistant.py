"""Amyna language layer over HTTP (stateless).

Conversational surfaces (Relay, future patients apps) send the patient's own
words; Core understands them and composes ONE plain-English reply. These
endpoints touch no database and no private data: understanding language is all
they do. Case creation, consent, authorization and record access belong to the
existing, guarded routers.
"""

from fastapi import APIRouter, Depends

from claimshield_contracts.api import (
    AssistantExplainIn,
    AssistantTermExplanation,
    AssistantUnderstandIn,
    AssistantUnderstandResult,
)

from ..auth import ServiceIdentity, require_scopes
from ..services.assistant_service import AssistantService, compose_reply

router = APIRouter(prefix="/assistant", tags=["assistant"])
_assistant = AssistantService()  # stateless, deterministic default engine


@router.post("/understand", response_model=AssistantUnderstandResult)
def understand(
    body: AssistantUnderstandIn,
    _: ServiceIdentity = Depends(require_scopes("assistant:use")),
) -> AssistantUnderstandResult:
    """Understand plain-English patient language and draft Amyna's next reply."""
    understood, reply = compose_reply(_assistant, body.message)
    return AssistantUnderstandResult(
        intent=understood.intent.value,
        suggested_action=understood.suggested_action.value,
        reply=reply,
        follow_up_question=understood.follow_up_question,
        candidate_claim_number=(
            understood.candidate_claim_number.value if understood.candidate_claim_number else None
        ),
        candidate_patient_name=(
            understood.candidate_patient_name.value if understood.candidate_patient_name else None
        ),
        candidate_amount_cents=(
            understood.candidate_amount_cents.value if understood.candidate_amount_cents else None
        ),
        confidence=understood.confidence,
    )


@router.post("/explain", response_model=AssistantTermExplanation)
def explain(
    body: AssistantExplainIn,
    _: ServiceIdentity = Depends(require_scopes("assistant:use")),
) -> AssistantTermExplanation:
    """Explain insurance jargon in plain English."""
    term = _assistant.explain_term(body.term)
    return AssistantTermExplanation(term=term.term, plain_language=term.plain_language)
