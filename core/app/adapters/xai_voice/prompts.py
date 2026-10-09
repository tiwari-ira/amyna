"""System prompt and tool schemas for the insurer-call voice agent.

The prompt states the guardrails, but every one of them is ALSO enforced in code
(security_policy + dispatcher + state machine). The prompt is defense in depth,
not the control.
"""

from typing import Any

from claimshield_contracts.tool_api import DisputeCaseView

from ...services.notification_service import format_cents

VOICE_SYSTEM_PROMPT = """\
You are Amyna's phone assistant, calling a health insurer on behalf of a
patient about a billing dispute. Follow these rules without exception:

1. Authority. You act only for the patient named in the case context, who has
   explicitly approved this dispute and authorized this call. Say so when you
   introduce yourself. You are an automated assistant; never claim to be human.
2. Facts. Use ONLY facts returned by your tools (get_dispute_case,
   get_claim_line, get_supporting_evidence). Never invent, guess or embellish
   medical facts, diagnoses, medications, billing codes, dates or amounts. If
   you are asked something the tools do not answer, say you will confirm with
   the member and call request_human with reason "unknown_medical_fact".
3. Sensitive information. Never provide, confirm, spell, or guess a Social
   Security number, PIN, password, passcode, verification/authorization code,
   date of birth, card or bank details. If the representative asks for any of
   these, say: "I'm not able to provide that information. The member will
   verify their identity with you directly. Please hold for a moment." and
   immediately call request_human with reason "credential_request" (or
   "identity_verification" for SSN/DOB). Then wait.
4. Scope. Discuss only the disputed items in this case. Do not agree to new
   charges, payment plans, or changes to coverage. Do not make legal threats.
5. Outcome. When the representative states an outcome (charges removed,
   reprocessing, itemization requested, denial, callback), call
   record_insurer_response with the outcome, the reference number, the line ids
   affected and the representative's name if given. Read the reference number
   back to confirm it.
6. Tone. Be brief, polite and specific. One topic per turn. Spell numbers
   digit by digit when asked.
"""

CASE_CONTEXT_TEMPLATE = """\
Case context (from get_dispute_case at call start):
- Member: {member} (member reference {member_ref})
- Insurer: {payer}
- Claim number: {claim_number}; date of service: {date_of_service}; provider: {provider}
- Total billed: {total_billed}; disputed: {total_disputed}
- Disputed items:
{items}
"""


def build_instructions(view: DisputeCaseView) -> str:
    """System prompt plus a compact, PHI-reduced case summary."""
    items = (
        "\n".join(
            f"  * line {item.line_number} ({item.claim_line_id}): {item.summary} "
            f"[{item.action.value}, {format_cents(item.amount_cents)}]"
            for item in view.dispute.items
        )
        if view.dispute
        else "  * (none)"
    )
    context = CASE_CONTEXT_TEMPLATE.format(
        member=view.patient_display_name or "the member",
        member_ref=view.member_ref_masked or "on file",
        payer=view.payer_name or "the insurer",
        claim_number=view.claim_number or "unknown",
        date_of_service=view.date_of_service.isoformat() if view.date_of_service else "unknown",
        provider=view.provider_name or "unknown",
        total_billed=format_cents(view.total_billed_cents),
        total_disputed=format_cents(view.dispute.total_disputed_cents if view.dispute else 0),
        items=items,
    )
    return f"{VOICE_SYSTEM_PROMPT}\n{context}"


# Tool schemas exposed to the model. case_id/call_id are NOT parameters: the
# dispatcher injects them, so the model can never pick another case.
VOICE_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "get_dispute_case",
        "description": "Get the dispute for this call: claim number, dates, disputed items.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "type": "function",
        "name": "get_claim_line",
        "description": "Get one billed line of the claim under dispute.",
        "parameters": {
            "type": "object",
            "properties": {"line_id": {"type": "string"}},
            "required": ["line_id"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "get_supporting_evidence",
        "description": "Get the evidence behind a disputed line (visit evidence, finding).",
        "parameters": {
            "type": "object",
            "properties": {"line_id": {"type": "string"}},
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "record_insurer_response",
        "description": "Record what the insurer agreed to, with the reference number.",
        "parameters": {
            "type": "object",
            "properties": {
                "outcome": {
                    "type": "string",
                    "enum": [
                        "charges_removed",
                        "partially_removed",
                        "denied",
                        "pending_review",
                        "callback_required",
                    ],
                },
                "reference_number": {"type": "string"},
                "removed_line_ids": {"type": "array", "items": {"type": "string"}},
                "itemization_requested_line_ids": {"type": "array", "items": {"type": "string"}},
                "representative_name": {"type": "string"},
                "notes": {"type": "string"},
            },
            "required": ["outcome"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "request_human",
        "description": (
            "Pause the call and bring in the member. REQUIRED when asked for SSN, PIN, "
            "password, verification codes, DOB, card/bank details, or unknown medical facts."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "enum": [
                        "credential_request",
                        "identity_verification",
                        "unknown_medical_fact",
                        "call_escalation",
                    ],
                },
                "detail": {"type": "string"},
            },
            "required": ["reason", "detail"],
            "additionalProperties": False,
        },
    },
]
