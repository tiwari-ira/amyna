"""Typed uAgents messages exchanged between Amyna Fetch agents.

uagents 0.25.x ``Model`` is built on ``pydantic.v1.BaseModel`` (decision D1 in
docs/ARCHITECTURE.md). Therefore:

- Messages subclass ``uagents_core.models.Model`` (== ``uagents.Model``) when it is
  installed, otherwise an equivalent shim on ``pydantic.v1.BaseModel``. Schema
  digests are identical either way (pinned in fixtures/agent_message_digests.json).
- Messages only nest other message DTOs from this module (never v2 models).
- Enum-like fields are plain ``str`` so adding an enum value does not change the
  schema digest (decision D2). Allowed values are listed in comments.

Changing ANY field -- or any message class docstring, which pydantic.v1 puts in
the schema -- changes the schema digest: bump AGENT_PROTOCOL_VERSION, update
fixtures/agent_message_digests.json (``write_digest_lock()``), and restart all
agents together.
"""

import hashlib
import json
from pathlib import Path
from typing import Any

from .models import DisputeCandidate, DisputeItem, EvidenceItem
from .version import AGENT_PROTOCOL_VERSION

try:  # pragma: no cover - exercised only where uagents is installed
    # uagents_core.models.Model is the class uagents re-exports as uagents.Model.
    # Import it from uagents_core: importing `uagents` itself configures root logging.
    from uagents_core.models import Model as AgentModel
except ImportError:  # Core and tests do not need uagents installed.
    from pydantic.v1 import BaseModel as _V1BaseModel

    class AgentModel(_V1BaseModel):  # type: ignore[no-redef]
        # Stand-in mirroring uagents_core.models.Model. NO docstring on purpose:
        # pydantic.v1 copies an inherited docstring into every subclass schema
        # ("description"), which would change the schema digests.

        def model_dump(self) -> dict[str, Any]:
            return self.dict()

        def model_dump_json(self) -> str:
            return self.json()

        @classmethod
        def model_validate(cls, obj: Any) -> "AgentModel":
            return cls.parse_obj(obj)

        @classmethod
        def model_validate_json(cls, obj: Any) -> "AgentModel":
            return cls.parse_raw(obj)

        @staticmethod
        def build_schema_digest(model: Any) -> str:
            schema = model.schema_json(indent=None, sort_keys=True)
            return f"model:{hashlib.sha256(schema.encode('utf8')).digest().hex()}"


# --------------------------------------------------------------------------- nested DTOs


class CodeRefMsg(AgentModel):
    system: str
    code: str
    display: str | None = None


class EvidenceRefMsg(AgentModel):
    source: str  # EvidenceSource value
    ref_id: str
    description: str


class PolicyRefMsg(AgentModel):
    doc_id: str
    title: str
    source: str
    url: str | None = None


class EvidenceItemMsg(AgentModel):
    evidence_id: str
    kind: str  # EvidenceKind value
    concept: str | None = None
    description: str
    codes: list[CodeRefMsg] = []
    attributes: dict[str, str] = {}
    negated: bool = False
    confidence: float


class FindingMsg(AgentModel):
    finding_id: str
    claim_line_id: str
    line_number: int
    issue_type: str  # IssueType value
    title: str
    explanation: str
    amount_cents: int
    confidence: float
    human_confirmation_required: bool
    confirmation_question: str | None = None
    status: str  # FindingStatus value
    recommended_action: str  # DisputeAction value
    evidence_refs: list[EvidenceRefMsg] = []
    policy_refs: list[PolicyRefMsg] = []


class DisputeItemMsg(AgentModel):
    finding_id: str
    claim_line_id: str
    line_number: int
    issue_type: str
    action: str  # DisputeAction value
    amount_cents: int
    summary: str
    policy_refs: list[PolicyRefMsg] = []


# --------------------------------------------------------------------------- Visit Agent


class VisitEvidenceRequest(AgentModel):
    request_id: str
    case_id: str
    visit_id: str | None = None
    schema_version: str = AGENT_PROTOCOL_VERSION


class VisitEvidenceResponse(AgentModel):
    request_id: str
    case_id: str
    ok: bool
    error: str | None = None
    visit_id: str | None = None
    evidence: list[EvidenceItemMsg] = []
    case_state: str | None = None
    schema_version: str = AGENT_PROTOCOL_VERSION


# --------------------------------------------------------------------------- Claim Agent


class ClaimAnalysisRequest(AgentModel):
    request_id: str
    case_id: str
    claim_id: str | None = None
    schema_version: str = AGENT_PROTOCOL_VERSION


class ClaimAnalysisResponse(AgentModel):
    request_id: str
    case_id: str
    ok: bool
    error: str | None = None
    claim_id: str | None = None
    external_claim_id: str | None = None
    findings: list[FindingMsg] = []
    matched_line_ids: list[str] = []
    total_billed_cents: int = 0
    total_flagged_cents: int = 0
    open_question_ids: list[str] = []
    case_state: str | None = None
    schema_version: str = AGENT_PROTOCOL_VERSION


# --------------------------------------------------------------------------- Dispute Agent

DISPUTE_ACTION_PREPARE = "prepare"
DISPUTE_ACTION_START_CALL = "start_call"


class DisputeRequest(AgentModel):
    request_id: str
    case_id: str
    action: str  # "prepare" | "start_call"
    dispute_id: str | None = None
    schema_version: str = AGENT_PROTOCOL_VERSION


class DisputeResponse(AgentModel):
    request_id: str
    case_id: str
    ok: bool
    error: str | None = None
    action: str
    dispute_id: str | None = None
    dispute_status: str | None = None
    items: list[DisputeItemMsg] = []
    total_disputed_cents: int = 0
    letter_preview: str | None = None
    call_id: str | None = None
    call_status: str | None = None
    open_intervention_id: str | None = None
    open_intervention_prompt: str | None = None
    case_state: str | None = None
    schema_version: str = AGENT_PROTOCOL_VERSION


# --------------------------------------------------------------------------- Core -> orchestrator

TRIGGER_VISIT_EVIDENCE = "visit_evidence"
TRIGGER_CLAIM_ANALYSIS = "claim_analysis"
TRIGGER_DISPUTE_PREPARE = "dispute_prepare"


class OrchestratorTrigger(AgentModel):
    """REST body Core posts to the orchestrator in FETCH_MODE=agentverse.

    ``auth_token`` carries FETCH_SERVICE_TOKEN because uAgents REST handlers do
    not expose request headers.
    """

    kind: str  # visit_evidence | claim_analysis | dispute_prepare
    case_id: str
    visit_id: str | None = None
    claim_id: str | None = None
    auth_token: str
    schema_version: str = AGENT_PROTOCOL_VERSION


class OrchestratorTriggerAck(AgentModel):
    accepted: bool
    request_id: str | None = None
    error: str | None = None


# --------------------------------------------------------------------------- converters


def evidence_to_msg(item: EvidenceItem) -> EvidenceItemMsg:
    """Convert a domain EvidenceItem into its message DTO."""
    return EvidenceItemMsg(
        evidence_id=item.evidence_id,
        kind=item.kind.value,
        concept=item.concept,
        description=item.description,
        codes=[
            CodeRefMsg(system=c.system.value, code=c.code, display=c.display) for c in item.codes
        ],
        attributes=dict(item.attributes),
        negated=item.negated,
        confidence=item.confidence,
    )


def finding_to_msg(finding: DisputeCandidate) -> FindingMsg:
    """Convert a domain DisputeCandidate into its message DTO."""
    return FindingMsg(
        finding_id=finding.finding_id,
        claim_line_id=finding.claim_line_id,
        line_number=finding.line_number,
        issue_type=finding.issue_type.value,
        title=finding.title,
        explanation=finding.explanation,
        amount_cents=finding.amount_cents,
        confidence=finding.confidence,
        human_confirmation_required=finding.human_confirmation_required,
        confirmation_question=finding.confirmation.question if finding.confirmation else None,
        status=finding.status.value,
        recommended_action=finding.recommended_action.value,
        evidence_refs=[
            EvidenceRefMsg(source=r.source.value, ref_id=r.ref_id, description=r.description)
            for r in finding.evidence_refs
        ],
        policy_refs=[
            PolicyRefMsg(doc_id=p.doc_id, title=p.title, source=p.source, url=p.url)
            for p in finding.policy_citations
        ],
    )


def dispute_item_to_msg(item: DisputeItem) -> DisputeItemMsg:
    """Convert a domain DisputeItem into its message DTO."""
    return DisputeItemMsg(
        finding_id=item.finding_id,
        claim_line_id=item.claim_line_id,
        line_number=item.line_number,
        issue_type=item.issue_type.value,
        action=item.action.value,
        amount_cents=item.amount_cents,
        summary=item.summary,
        policy_refs=[
            PolicyRefMsg(doc_id=p.doc_id, title=p.title, source=p.source, url=p.url)
            for p in item.policy_citations
        ],
    )


ALL_MESSAGES = (
    VisitEvidenceRequest,
    VisitEvidenceResponse,
    ClaimAnalysisRequest,
    ClaimAnalysisResponse,
    DisputeRequest,
    DisputeResponse,
    OrchestratorTrigger,
    OrchestratorTriggerAck,
)


DIGEST_LOCK_PATH = Path(__file__).parent / "fixtures" / "agent_message_digests.json"


def schema_digests() -> dict[str, str]:
    """Schema digest per message (what uAgents uses to route messages)."""
    return {m.__name__: AgentModel.build_schema_digest(m) for m in ALL_MESSAGES}


def write_digest_lock(path: Path = DIGEST_LOCK_PATH) -> None:
    """Regenerate the digest lock file after an intentional protocol change."""
    payload = {"agent_protocol_version": AGENT_PROTOCOL_VERSION, "digests": schema_digests()}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
