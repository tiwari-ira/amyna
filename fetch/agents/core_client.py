"""``CoreTools`` over HTTPS: how agents reach Core (they never touch the DB).

One client per agent. Every request is tagged with ``agent_run`` so Core can
enforce the per-agent tool allowlist and record AgentRuns. Pass ``http=`` to
reuse a client (tests pass FastAPI's TestClient).
"""

from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from claimshield_contracts.api import HumanResolutionResult
from claimshield_contracts.enums import AgentName
from claimshield_contracts.ids import new_id
from claimshield_contracts.models import (
    CallSession,
    CaseDetail,
    Claim,
    ClaimLine,
    Consent,
    Dispute,
    HumanIntervention,
    InsurerResponse,
)
from claimshield_contracts.tool_api import (
    TOOL_SPECS,
    AgentRunRef,
    CoreToolError,
    DisputeCaseView,
    FindOrCreateCaseRequest,
    FindOrCreateCaseResponse,
    GetCaseClaimRequest,
    GetCaseRequest,
    GetClaimLineRequest,
    GetDisputeCaseRequest,
    GetPatientContextRequest,
    GetSupportingEvidenceRequest,
    GetVisitTranscriptRequest,
    IngestClaimRequest,
    PatientClaims,
    PatientContext,
    PatientDataRequest,
    PatientRecords,
    RecordConsentRequest,
    RecordDisputeRequest,
    RecordFindingsRequest,
    RecordFindingsResponse,
    RecordInsurerResponseRequest,
    RecordPatientDecisionRequest,
    RecordVisitEvidenceRequest,
    RecordVisitEvidenceResponse,
    RequestHumanRequest,
    ResolveHumanInterventionRequest,
    StartInsuranceCallRequest,
    SupportingEvidence,
    ToolName,
    VisitTranscriptView,
)

R = TypeVar("R", bound=BaseModel)


class CoreClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        agent: AgentName,
        http: httpx.Client | None = None,
        timeout_s: float = 60.0,
        request_id: str | None = None,
    ) -> None:
        self._http = http or httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout_s)
        self._token = token
        self._agent = agent
        self._request_id = request_id

    def for_request(self, request_id: str) -> "CoreClient":
        """Same connection, but tool calls are correlated with an agent request id."""
        return CoreClient(
            "", self._token, agent=self._agent, http=self._http, request_id=request_id
        )

    def _call(self, tool: ToolName, req: BaseModel) -> Any:
        if getattr(req, "agent_run", "missing") is None:
            run = AgentRunRef(agent=self._agent, request_id=self._request_id or new_id("request"))
            req = req.model_copy(update={"agent_run": run})
        try:
            response = self._http.post(
                f"/internal/tools/{tool.value}",
                json=req.model_dump(mode="json"),
                headers={"Authorization": f"Bearer {self._token}"},
            )
        except httpx.HTTPError as exc:  # connect errors, timeouts, protocol errors
            raise CoreToolError(503, "core_unreachable", type(exc).__name__) from exc
        if response.status_code >= 400:
            raise _error(response)
        _, response_model = TOOL_SPECS[tool]
        try:
            return response_model.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            raise CoreToolError(502, "invalid_core_response", tool.value) from exc

    def find_or_create_case(self, req: FindOrCreateCaseRequest) -> FindOrCreateCaseResponse:
        return self._call(ToolName.FIND_OR_CREATE_CASE, req)

    def get_case(self, req: GetCaseRequest) -> CaseDetail:
        return self._call(ToolName.GET_CASE, req)

    def record_consent(self, req: RecordConsentRequest) -> Consent:
        return self._call(ToolName.RECORD_CONSENT, req)

    def ingest_claim(self, req: IngestClaimRequest) -> Claim:
        return self._call(ToolName.INGEST_CLAIM, req)

    def get_case_claim(self, req: GetCaseClaimRequest) -> Claim:
        return self._call(ToolName.GET_CASE_CLAIM, req)

    def get_visit_transcript(self, req: GetVisitTranscriptRequest) -> VisitTranscriptView:
        return self._call(ToolName.GET_VISIT_TRANSCRIPT, req)

    def record_visit_evidence(self, req: RecordVisitEvidenceRequest) -> RecordVisitEvidenceResponse:
        return self._call(ToolName.RECORD_VISIT_EVIDENCE, req)

    def get_supporting_evidence(self, req: GetSupportingEvidenceRequest) -> SupportingEvidence:
        return self._call(ToolName.GET_SUPPORTING_EVIDENCE, req)

    def get_patient_claims(self, req: PatientDataRequest) -> PatientClaims:
        return self._call(ToolName.GET_PATIENT_CLAIMS, req)

    def get_patient_encounters(self, req: PatientDataRequest) -> PatientRecords:
        return self._call(ToolName.GET_PATIENT_ENCOUNTERS, req)

    def get_patient_procedures(self, req: PatientDataRequest) -> PatientRecords:
        return self._call(ToolName.GET_PATIENT_PROCEDURES, req)

    def get_patient_medications(self, req: PatientDataRequest) -> PatientRecords:
        return self._call(ToolName.GET_PATIENT_MEDICATIONS, req)

    def record_findings(self, req: RecordFindingsRequest) -> RecordFindingsResponse:
        return self._call(ToolName.RECORD_FINDINGS, req)

    def get_claim_line(self, req: GetClaimLineRequest) -> ClaimLine:
        return self._call(ToolName.GET_CLAIM_LINE, req)

    def get_dispute_case(self, req: GetDisputeCaseRequest) -> DisputeCaseView:
        return self._call(ToolName.GET_DISPUTE_CASE, req)

    def get_patient_context(self, req: GetPatientContextRequest) -> PatientContext:
        return self._call(ToolName.GET_PATIENT_CONTEXT, req)

    def record_dispute(self, req: RecordDisputeRequest) -> Dispute:
        return self._call(ToolName.RECORD_DISPUTE, req)

    def record_patient_decision(self, req: RecordPatientDecisionRequest) -> Dispute:
        return self._call(ToolName.RECORD_PATIENT_DECISION, req)

    def resolve_human_intervention(
        self, req: ResolveHumanInterventionRequest
    ) -> HumanResolutionResult:
        return self._call(ToolName.RESOLVE_HUMAN_INTERVENTION, req)

    def request_human(self, req: RequestHumanRequest) -> HumanIntervention:
        return self._call(ToolName.REQUEST_HUMAN, req)

    def start_insurance_call(self, req: StartInsuranceCallRequest) -> CallSession:
        return self._call(ToolName.START_INSURANCE_CALL, req)

    def record_insurer_response(self, req: RecordInsurerResponseRequest) -> InsurerResponse:
        return self._call(ToolName.RECORD_INSURER_RESPONSE, req)


def _error(response: httpx.Response) -> CoreToolError:
    try:
        detail = response.json().get("detail")
    except ValueError:
        detail = None
    if isinstance(detail, dict):
        return CoreToolError(
            response.status_code, str(detail.get("code", "error")), str(detail.get("message", ""))
        )
    return CoreToolError(response.status_code, "error", str(detail or response.text)[:300])
