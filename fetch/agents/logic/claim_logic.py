"""Claim Agent logic: claim + FinchNode records + visit evidence -> findings."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from claimshield_contracts.agent_messages import (
    ClaimAnalysisRequest,
    ClaimAnalysisResponse,
    finding_to_msg,
)
from claimshield_contracts.enums import LineMatchStatus
from claimshield_contracts.models import CaseDetail, DisputeCandidate, LineAssessment
from claimshield_contracts.tool_api import (
    CoreToolError,
    CoreTools,
    GetCaseRequest,
    PatientDataRequest,
    RecordFindingsRequest,
)
from intelligence.codes.hospital_ingest import find_price_index
from intelligence.codes.resolver import CodeResolver, default_resolver
from intelligence.discrepancy.engine import EngineConfig, analyze_claim
from intelligence.policy_rag.retrieve import PolicyRetriever, cite_findings, default_retriever


@dataclass
class ClaimLogicDeps:
    resolver: CodeResolver = field(default_factory=default_resolver)
    retriever: PolicyRetriever = field(default_factory=default_retriever)
    config: EngineConfig = field(default_factory=EngineConfig)
    use_price_index: bool = True


def _response(
    req: ClaimAnalysisRequest,
    case: CaseDetail | None = None,
    findings: list[DisputeCandidate] | None = None,
    assessments: list[LineAssessment] | None = None,
    *,
    open_question_ids: list[str] | None = None,
    case_state: str | None = None,
    error: str | None = None,
) -> ClaimAnalysisResponse:
    claim = case.claim if case else None
    findings = findings or []
    return ClaimAnalysisResponse(
        request_id=req.request_id,
        case_id=req.case_id,
        ok=error is None,
        error=error,
        claim_id=claim.claim_id if claim else req.claim_id,
        external_claim_id=claim.external_claim_id if claim else None,
        findings=[finding_to_msg(f) for f in findings],
        matched_line_ids=[
            a.claim_line_id for a in assessments or [] if a.status == LineMatchStatus.MATCHED
        ],
        total_billed_cents=claim.total_billed_cents if claim else 0,
        total_flagged_cents=sum(f.amount_cents for f in findings),
        open_question_ids=open_question_ids or [],
        case_state=case_state,
    )


def _pull_patient_records(tools: CoreTools, data: PatientDataRequest) -> tuple[list, list[str]]:
    """Pull encounters/procedures/medications CONCURRENTLY (prompt task 11).

    Independent network inputs run in parallel with the same correlation id;
    a failed source never loses the successful ones -- analysis proceeds with
    what came back and the failed source names are returned for the run log.
    """
    pulls = {
        "encounters": lambda: tools.get_patient_encounters(data).records,
        "procedures": lambda: tools.get_patient_procedures(data).records,
        "medications": lambda: tools.get_patient_medications(data).records,
    }
    records: list = []
    failed: list[str] = []
    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="claim-records") as pool:
        futures = {name: pool.submit(fn) for name, fn in pulls.items()}
        for name, future in futures.items():
            try:
                records.extend(future.result())
            except CoreToolError:
                failed.append(name)
    return records, failed


def handle_claim_analysis_request(
    tools: CoreTools, req: ClaimAnalysisRequest, deps: ClaimLogicDeps | None = None
) -> ClaimAnalysisResponse:
    """Analyze the case's claim once; afterwards return the recorded findings."""
    deps = deps or ClaimLogicDeps()
    try:
        case = tools.get_case(GetCaseRequest(case_id=req.case_id))
        if case.claim is None:
            return _response(req, error="This case has no claim yet.")
        if req.claim_id and req.claim_id != case.claim.claim_id:
            return _response(req, case, error="claim_id does not match the case's claim.")
        if case.findings:
            open_ids = [i.intervention_id for i in case.open_interventions() if i.finding_id]
            return _response(
                req,
                case,
                case.findings,
                case.line_assessments,
                open_question_ids=open_ids,
                case_state=case.state.value,
            )
        data = PatientDataRequest(case_id=req.case_id)
        records, failed_sources = _pull_patient_records(tools, data)
        if len(failed_sources) == 3:
            return _response(req, case, error="all record pulls failed")
        claim = case.claim
        analysis = analyze_claim(
            claim,
            records,
            case.evidence,
            resolver=deps.resolver,
            visit_id=case.visit.visit_id if case.visit else None,
            price_index=(
                find_price_index(claim.facility_name, claim.provider_name)
                if deps.use_price_index
                else None
            ),
            config=deps.config,
        )
        findings = cite_findings(
            analysis.findings,
            deps.retriever,
            state=case.patient.state,
            payer=claim.payer_name or case.patient.payer_name,
            as_of=claim.date_of_service,
        )
        recorded = tools.record_findings(
            RecordFindingsRequest(
                case_id=req.case_id,
                claim_id=claim.claim_id,
                findings=findings,
                line_assessments=analysis.line_assessments,
            )
        )
        return _response(
            req,
            case,
            recorded.findings,
            analysis.line_assessments,
            open_question_ids=[q.intervention_id for q in recorded.open_questions],
            case_state=recorded.case_state.value,
        )
    except CoreToolError as exc:
        return _response(req, error=f"{exc.code}: {exc.message}")
