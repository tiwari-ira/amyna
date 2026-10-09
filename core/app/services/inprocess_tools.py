"""``CoreTools`` implemented with direct ToolService calls (FETCH_MODE=local_direct).

Same scopes, same audit log, one transaction per call -- the agent logic cannot
tell whether it is talking to Core over HTTPS or in-process (decision D4).
Domain errors are translated to ``CoreToolError`` exactly like the HTTP client.
"""

from collections.abc import Callable
from typing import TYPE_CHECKING, TypeVar

from pydantic import BaseModel

from claimshield_contracts.api import HumanResolutionResult
from claimshield_contracts.enums import ServiceName
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
    VisitTranscriptView,
)

from ..auth import ServiceIdentity, identity_for
from ..errors import DomainError
from .tool_service import ToolService

if TYPE_CHECKING:
    from ..container import Container

R = TypeVar("R", bound=BaseModel)


class InProcessTools:
    def __init__(self, container: "Container", identity: ServiceIdentity | None = None) -> None:
        self._svc = ToolService(container, identity or identity_for(ServiceName.FETCH))

    @staticmethod
    def _call(method: Callable[[BaseModel], R], req: BaseModel) -> R:
        try:
            return method(req)
        except DomainError as exc:
            raise CoreToolError(exc.status_code, exc.code, exc.message) from exc

    def find_or_create_case(self, req: FindOrCreateCaseRequest) -> FindOrCreateCaseResponse:
        return self._call(self._svc.find_or_create_case, req)

    def get_case(self, req: GetCaseRequest) -> CaseDetail:
        return self._call(self._svc.get_case, req)

    def record_consent(self, req: RecordConsentRequest) -> Consent:
        return self._call(self._svc.record_consent, req)

    def ingest_claim(self, req: IngestClaimRequest) -> Claim:
        return self._call(self._svc.ingest_claim, req)

    def get_case_claim(self, req: GetCaseClaimRequest) -> Claim:
        return self._call(self._svc.get_case_claim, req)

    def get_patient_context(self, req: GetPatientContextRequest) -> PatientContext:
        return self._call(self._svc.get_patient_context, req)

    def get_visit_transcript(self, req: GetVisitTranscriptRequest) -> VisitTranscriptView:
        return self._call(self._svc.get_visit_transcript, req)

    def record_visit_evidence(self, req: RecordVisitEvidenceRequest) -> RecordVisitEvidenceResponse:
        return self._call(self._svc.record_visit_evidence, req)

    def get_supporting_evidence(self, req: GetSupportingEvidenceRequest) -> SupportingEvidence:
        return self._call(self._svc.get_supporting_evidence, req)

    def get_patient_claims(self, req: PatientDataRequest) -> PatientClaims:
        return self._call(self._svc.get_patient_claims, req)

    def get_patient_encounters(self, req: PatientDataRequest) -> PatientRecords:
        return self._call(self._svc.get_patient_encounters, req)

    def get_patient_procedures(self, req: PatientDataRequest) -> PatientRecords:
        return self._call(self._svc.get_patient_procedures, req)

    def get_patient_medications(self, req: PatientDataRequest) -> PatientRecords:
        return self._call(self._svc.get_patient_medications, req)

    def record_findings(self, req: RecordFindingsRequest) -> RecordFindingsResponse:
        return self._call(self._svc.record_findings, req)

    def get_claim_line(self, req: GetClaimLineRequest) -> ClaimLine:
        return self._call(self._svc.get_claim_line, req)

    def get_dispute_case(self, req: GetDisputeCaseRequest) -> DisputeCaseView:
        return self._call(self._svc.get_dispute_case, req)

    def record_dispute(self, req: RecordDisputeRequest) -> Dispute:
        return self._call(self._svc.record_dispute, req)

    def record_patient_decision(self, req: RecordPatientDecisionRequest) -> Dispute:
        return self._call(self._svc.record_patient_decision, req)

    def resolve_human_intervention(
        self, req: ResolveHumanInterventionRequest
    ) -> HumanResolutionResult:
        return self._call(self._svc.resolve_human_intervention, req)

    def request_human(self, req: RequestHumanRequest) -> HumanIntervention:
        return self._call(self._svc.request_human, req)

    def start_insurance_call(self, req: StartInsuranceCallRequest) -> CallSession:
        return self._call(self._svc.start_insurance_call, req)

    def record_insurer_response(self, req: RecordInsurerResponseRequest) -> InsurerResponse:
        return self._call(self._svc.record_insurer_response, req)
