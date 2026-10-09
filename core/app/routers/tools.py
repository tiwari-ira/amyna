"""``POST /internal/tools/{tool}`` -- one typed route per tool in TOOL_SPECS.

Routes are generated from the contract registry so every tool is exposed with
its exact request/response schema and its own ``tools:<name>`` scope.
"""

from typing import Any

from fastapi import APIRouter, Depends

from claimshield_contracts.tool_api import TOOL_SPECS, ToolName

from ..auth import ServiceIdentity, require_scopes, tool_scope
from ..container import Container
from ..deps import get_container
from ..services.tool_service import ToolService

router = APIRouter(prefix="/internal/tools", tags=["internal-tools"])

TOOL_SUMMARIES: dict[ToolName, str] = {
    ToolName.FIND_OR_CREATE_CASE: "Load the open case for a FinchNode patient, or create one",
    ToolName.GET_CASE: "Full case detail",
    ToolName.RECORD_CONSENT: "Record a patient consent captured by an agent (e.g. in ASI:One)",
    ToolName.INGEST_CLAIM: "Attach the bill under review (FinchNode needs consent)",
    ToolName.GET_CASE_CLAIM: "The case's claim with lines",
    ToolName.GET_VISIT_TRANSCRIPT: "Visit + transcript for evidence extraction",
    ToolName.RECORD_VISIT_EVIDENCE: "Persist extracted visit evidence",
    ToolName.GET_SUPPORTING_EVIDENCE: "Evidence for the case (optionally for one line)",
    ToolName.GET_PATIENT_CLAIMS: "FinchNode claims (consent-gated)",
    ToolName.GET_PATIENT_ENCOUNTERS: "FinchNode encounters (consent-gated)",
    ToolName.GET_PATIENT_PROCEDURES: "FinchNode procedures (consent-gated)",
    ToolName.GET_PATIENT_MEDICATIONS: "FinchNode medications (consent-gated)",
    ToolName.RECORD_FINDINGS: "Persist discrepancy findings; opens confirmation questions",
    ToolName.GET_CLAIM_LINE: "One claim line of this case",
    ToolName.GET_DISPUTE_CASE: "PHI-reduced dispute view for dispute/voice agents",
    ToolName.GET_PATIENT_CONTEXT: "Authorized, minimized patient context (identifiers only)",
    ToolName.RECORD_DISPUTE: "Persist a dispute package; requests patient approval",
    ToolName.RECORD_PATIENT_DECISION: "Relay an explicit patient approve/reject from chat",
    ToolName.RESOLVE_HUMAN_INTERVENTION: "Answer a question or finish a handoff",
    ToolName.REQUEST_HUMAN: "Ask for a human; with call_id the call pauses",
    ToolName.START_INSURANCE_CALL: "Start the insurer call (approval + authorization required)",
    ToolName.RECORD_INSURER_RESPONSE: "Record the insurer's answer on the call (voice only)",
}


def _register(tool: ToolName) -> None:
    request_model, response_model = TOOL_SPECS[tool]

    def endpoint(
        req: request_model,  # type: ignore[valid-type]
        identity: ServiceIdentity = Depends(require_scopes(tool_scope(tool))),
        container: Container = Depends(get_container),
    ) -> Any:
        return getattr(ToolService(container, identity), tool.value)(req)

    router.add_api_route(
        f"/{tool.value}",
        endpoint,
        methods=["POST"],
        response_model=response_model,
        name=tool.value,
        operation_id=f"tool_{tool.value}",
        summary=TOOL_SUMMARIES[tool],
    )


for _tool in ToolName:
    _register(_tool)
