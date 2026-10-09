"""Dispute Agent logic: confirmed findings -> evidence-backed dispute package; and
requesting the insurer call (Core owns the call itself).

The letter is a deterministic template. A larger reasoning model may polish the
prose later, but must not add facts that are not in the items/citations.
"""

from claimshield_contracts.agent_messages import (
    DISPUTE_ACTION_PREPARE,
    DISPUTE_ACTION_START_CALL,
    DisputeRequest,
    DisputeResponse,
    dispute_item_to_msg,
)
from claimshield_contracts.enums import DisputeAction, FindingStatus
from claimshield_contracts.models import Dispute, DisputeCandidate, DisputeItem
from claimshield_contracts.tool_api import (
    CoreToolError,
    CoreTools,
    DisputeCaseView,
    GetCaseRequest,
    GetDisputeCaseRequest,
    RecordDisputeRequest,
    StartInsuranceCallRequest,
)

DISPUTABLE = (FindingStatus.CONFIRMED, FindingStatus.NEEDS_CLARIFICATION)
ACTION_TEXT = {
    DisputeAction.REMOVE_CHARGE: "remove this charge",
    DisputeAction.REQUEST_ITEMIZATION: "provide an itemized statement (drug name, NDC, dose) "
    "or remove this charge",
    DisputeAction.CORRECT_CODE: "correct the billed code to match the documented service",
    DisputeAction.REVIEW_PRICE: "review this charge against the published price",
}
LETTER_PREVIEW_CHARS = 600


def money(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def build_items(findings: list[DisputeCandidate]) -> list[DisputeItem]:
    """One item per disputable finding. NOT SURE answers become itemization requests."""
    items = []
    for finding in sorted(findings, key=lambda f: f.line_number):
        if finding.status not in DISPUTABLE:
            continue
        action = (
            DisputeAction.REQUEST_ITEMIZATION
            if finding.status == FindingStatus.NEEDS_CLARIFICATION
            else finding.recommended_action
        )
        items.append(
            DisputeItem(
                finding_id=finding.finding_id,
                claim_line_id=finding.claim_line_id,
                line_number=finding.line_number,
                issue_type=finding.issue_type,
                action=action,
                amount_cents=finding.amount_cents,
                summary=f"{finding.title} - {ACTION_TEXT[action]}",
                evidence_refs=finding.evidence_refs,
                policy_citations=finding.policy_citations,
            )
        )
    return items


def render_letter(
    view: DisputeCaseView, items: list[DisputeItem], findings: list[DisputeCandidate]
) -> str:
    """Plain-text dispute letter built only from case data and citations."""
    by_id = {f.finding_id: f for f in findings}
    total = sum(item.amount_cents for item in items)
    lines = [
        f"To: {view.payer_name or 'Member Services'}",
        f"Re: Claim {view.claim_number} - date of service {view.date_of_service}",
        f"Member: {view.patient_display_name or 'the member'} "
        f"(member ref {view.member_ref_masked})",
        "",
        f"I am disputing {len(items)} charge(s) totaling {money(total)} on this claim:",
        "",
    ]
    for index, item in enumerate(items, start=1):
        finding = by_id.get(item.finding_id)
        lines.append(
            f"{index}. Line {item.line_number} ({money(item.amount_cents)}): {item.summary}."
        )
        if finding:
            lines.append(f"   Why: {finding.explanation}")
        for citation in item.policy_citations:
            section = f", {citation.section}" if citation.section else ""
            lines.append(f"   Reference: {citation.title}{section} ({citation.source}).")
    lines += [
        "",
        "Please reprocess the claim accordingly and send me the corrected explanation of "
        "benefits, or the documentation that supports each charge.",
        "",
        "Prepared by Amyna at the member's request (synthetic demo data).",
    ]
    return "\n".join(lines)


def _from_dispute(req: DisputeRequest, dispute: Dispute, case_state: str) -> DisputeResponse:
    return DisputeResponse(
        request_id=req.request_id,
        case_id=req.case_id,
        ok=True,
        action=req.action,
        dispute_id=dispute.dispute_id,
        dispute_status=dispute.status.value,
        items=[dispute_item_to_msg(item) for item in dispute.items],
        total_disputed_cents=dispute.total_disputed_cents,
        letter_preview=dispute.letter_text[:LETTER_PREVIEW_CHARS],
        case_state=case_state,
    )


def _error(req: DisputeRequest, message: str) -> DisputeResponse:
    return DisputeResponse(
        request_id=req.request_id, case_id=req.case_id, ok=False, error=message, action=req.action
    )


def _prepare(tools: CoreTools, req: DisputeRequest) -> DisputeResponse:
    view = tools.get_dispute_case(GetDisputeCaseRequest(case_id=req.case_id))
    if view.dispute is not None:
        return _from_dispute(req, view.dispute, view.case_state.value)
    case = tools.get_case(GetCaseRequest(case_id=req.case_id))
    if case.claim is None:
        return _error(req, "This case has no claim.")
    items = build_items(view.findings)
    if not items:
        return _error(req, "There are no confirmed findings to dispute.")
    dispute = tools.record_dispute(
        RecordDisputeRequest(
            case_id=req.case_id,
            claim_id=case.claim.claim_id,
            items=items,
            letter_text=render_letter(view, items, view.findings),
            total_disputed_cents=sum(item.amount_cents for item in items),
        )
    )
    state = tools.get_dispute_case(GetDisputeCaseRequest(case_id=req.case_id)).case_state
    return _from_dispute(req, dispute, state.value)


def _start_call(tools: CoreTools, req: DisputeRequest) -> DisputeResponse:
    call = tools.start_insurance_call(StartInsuranceCallRequest(case_id=req.case_id))
    case = tools.get_case(GetCaseRequest(case_id=req.case_id))
    prompt = next(
        (i.prompt for i in case.interventions if i.intervention_id == call.open_intervention_id),
        None,
    )
    return DisputeResponse(
        request_id=req.request_id,
        case_id=req.case_id,
        ok=True,
        action=req.action,
        dispute_id=call.dispute_id,
        dispute_status=case.dispute.status.value if case.dispute else None,
        call_id=call.call_id,
        call_status=call.status.value,
        open_intervention_id=call.open_intervention_id,
        open_intervention_prompt=prompt,
        case_state=case.state.value,
    )


def handle_dispute_request(tools: CoreTools, req: DisputeRequest) -> DisputeResponse:
    """``prepare`` drafts + records the package (idempotent); ``start_call`` asks Core to call."""
    try:
        if req.action == DISPUTE_ACTION_PREPARE:
            return _prepare(tools, req)
        if req.action == DISPUTE_ACTION_START_CALL:
            return _start_call(tools, req)
        return _error(req, f"Unknown dispute action '{req.action}'.")
    except CoreToolError as exc:
        return _error(req, f"{exc.code}: {exc.message}")
