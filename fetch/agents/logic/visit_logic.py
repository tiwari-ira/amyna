"""Visit Agent logic: transcript -> extracted evidence -> recorded in Core."""

from dataclasses import dataclass, field

from claimshield_contracts.agent_messages import (
    VisitEvidenceRequest,
    VisitEvidenceResponse,
    evidence_to_msg,
)
from claimshield_contracts.enums import VisitStatus
from claimshield_contracts.models import EvidenceItem
from claimshield_contracts.tool_api import (
    CoreToolError,
    CoreTools,
    GetSupportingEvidenceRequest,
    GetVisitTranscriptRequest,
    RecordVisitEvidenceRequest,
)
from intelligence.codes.resolver import CodeResolver, default_resolver
from intelligence.extraction.extractor import Extractor, to_evidence_items
from intelligence.extraction.rule_based import RuleBasedExtractor


@dataclass
class VisitLogicDeps:
    extractor: Extractor = field(default_factory=RuleBasedExtractor)
    resolver: CodeResolver = field(default_factory=default_resolver)


def _response(
    req: VisitEvidenceRequest,
    *,
    visit_id: str | None = None,
    evidence: list[EvidenceItem] | None = None,
    case_state: str | None = None,
    error: str | None = None,
) -> VisitEvidenceResponse:
    return VisitEvidenceResponse(
        request_id=req.request_id,
        case_id=req.case_id,
        ok=error is None,
        error=error,
        visit_id=visit_id,
        evidence=[evidence_to_msg(item) for item in evidence or []],
        case_state=case_state,
    )


def handle_visit_evidence_request(
    tools: CoreTools, req: VisitEvidenceRequest, deps: VisitLogicDeps | None = None
) -> VisitEvidenceResponse:
    """Extract and record visit evidence once; afterwards return what is recorded."""
    deps = deps or VisitLogicDeps()
    try:
        view = tools.get_visit_transcript(
            GetVisitTranscriptRequest(case_id=req.case_id, visit_id=req.visit_id)
        )
        if view.visit is None:
            return _response(req, error="This case has no recorded visit.")
        visit_id = view.visit.visit_id
        if view.visit.status == VisitStatus.EVIDENCE_EXTRACTED:
            existing = tools.get_supporting_evidence(
                GetSupportingEvidenceRequest(case_id=req.case_id)
            ).evidence
            return _response(
                req, visit_id=visit_id, evidence=[e for e in existing if e.visit_id == visit_id]
            )
        if view.transcript is None:
            return _response(req, visit_id=visit_id, error="The visit has no transcript yet.")
        extracted = deps.extractor.extract(view.transcript)
        evidence = to_evidence_items(
            extracted,
            view.transcript,
            case_id=req.case_id,
            visit_id=visit_id,
            resolver=deps.resolver,
        )
        recorded = tools.record_visit_evidence(
            RecordVisitEvidenceRequest(case_id=req.case_id, visit_id=visit_id, evidence=evidence)
        )
        return _response(
            req, visit_id=visit_id, evidence=recorded.evidence, case_state=recorded.case_state
        )
    except CoreToolError as exc:
        return _response(req, error=f"{exc.code}: {exc.message}")
