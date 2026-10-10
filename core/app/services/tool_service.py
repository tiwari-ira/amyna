"""Implementation of every ``/internal/tools/*`` tool.

Shared by the HTTP router (``routers/tools.py``), the in-process ``CoreTools``
used by the local_direct fetch bridge, and the voice tool backend. Each method:
checks the caller's ``tools:<name>`` scope (defense in depth; the router checks
too), owns its transaction(s), and writes an audit event.
"""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from claimshield_contracts.api import HumanResolutionResult
from claimshield_contracts.enums import (
    AgentRunStatus,
    CaseState,
    ConsentType,
    PatientDecision,
    RecordType,
)
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
    AGENT_TOOLS,
    AgentRunRef,
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

from .. import mappers
from ..audit import mask_member_ref, record_audit
from ..auth import ServiceIdentity, tool_scope
from ..db import utcnow
from ..errors import DomainError, Forbidden, InvalidInput, NotFound
from ..models_db import AgentRunRow, ClaimLineRow, ClaimRow, FindingRow, LineAssessmentRow
from . import (
    case_service,
    claim_service,
    dispute_service,
    evidence_service,
    voice_service,
    workflows,
)
from .human_service import HANDOFF_OPTIONS

if TYPE_CHECKING:
    from ..container import Container

log = logging.getLogger("claimshield.tools")


class ToolService:
    def __init__(self, container: "Container", caller: ServiceIdentity) -> None:
        self._c = container
        self._caller = caller

    @property
    def _actor(self) -> str:
        return self._caller.actor

    # ------------------------------------------------------------------ plumbing

    def _authorize(
        self, tool: ToolName, agent_run: AgentRunRef | None = None, case_id: str | None = None
    ) -> None:
        reason = None
        if not self._caller.has(tool_scope(tool)):
            reason = f"{self._caller.name.value} may not call tool {tool.value}"
        elif agent_run is not None and tool not in AGENT_TOOLS[agent_run.agent]:
            reason = f"{agent_run.agent.value} is not allowed to call {tool.value}"
        if reason:
            self._audit_failure(tool, case_id, "forbidden")
            raise Forbidden(reason)

    def _audit_failure(self, tool: ToolName, case_id: str | None, code: str) -> None:
        """Failed and forbidden calls are audited too (own transaction, never raises)."""
        try:
            with self._c.session_scope() as db:
                record_audit(
                    db,
                    actor=self._actor,
                    action=f"tool.{tool.value}.failed",
                    case_id=case_id,
                    details={"code": code},
                )
        except Exception:  # pragma: no cover - auditing must not mask the real error
            log.exception("could not audit failed tool call")

    @contextmanager
    def _tx(
        self, tool: ToolName, case_id: str | None, agent_run: AgentRunRef | None = None
    ) -> Iterator[Session]:
        """Authorize, open a transaction, audit success and failure."""
        self._authorize(tool, agent_run, case_id)
        try:
            with self._c.session_scope() as db:
                yield db
                self._audit(db, tool, case_id, agent_run)
        except DomainError as exc:
            self._audit_failure(tool, case_id, exc.code)
            raise

    def _audit(
        self, db: Session, tool: ToolName, case_id: str | None, agent_run: AgentRunRef | None
    ) -> None:
        record_audit(
            db,
            actor=self._actor,
            action=f"tool.{tool.value}",
            case_id=case_id,
            details={
                "agent": agent_run.agent.value if agent_run else None,
                "request_id": agent_run.request_id if agent_run else None,
            },
        )
        if agent_run is not None:
            db.add(
                AgentRunRow(
                    id=new_id("agent_run"),
                    case_id=case_id,
                    agent=agent_run.agent.value,
                    request_type=tool.value,
                    request_id=agent_run.request_id,
                    status=AgentRunStatus.SUCCEEDED.value,
                    finished_at=utcnow(),
                )
            )

    def _patient_id(self, db: Session, case_id: str) -> str:
        case = case_service.get_case_row(db, case_id)
        case_service.require_consent(db, case_id, ConsentType.HEALTH_DATA_ACCESS)
        return case.finchnode_patient_id

    # ------------------------------------------------------------------ case setup

    def find_or_create_case(self, req: FindOrCreateCaseRequest) -> FindOrCreateCaseResponse:
        with self._tx(ToolName.FIND_OR_CREATE_CASE, None, req.agent_run) as db:
            row, created = case_service.find_or_create_case(
                db,
                finchnode_patient_id=req.finchnode_patient_id,
                channel=req.channel,
                external_ref=req.external_ref,
                actor=self._actor,
            )
            return FindOrCreateCaseResponse(case=mappers.case_to_model(row), created=created)

    def get_case(self, req: GetCaseRequest) -> CaseDetail:
        with self._tx(ToolName.GET_CASE, req.case_id, req.agent_run) as db:
            return case_service.get_case_detail(db, req.case_id)

    def record_consent(self, req: RecordConsentRequest) -> Consent:
        with self._tx(ToolName.RECORD_CONSENT, req.case_id, req.agent_run) as db:
            row = case_service.record_consent(
                db,
                self._c,
                case_id=req.case_id,
                consent_type=req.consent_type,
                granted=req.granted,
                consent_text=req.consent_text,
                actor_kind=req.actor,
                channel=req.channel,
                actor=self._actor,
            )
            return mappers.consent_to_model(row)

    def ingest_claim(self, req: IngestClaimRequest) -> Claim:
        with self._tx(ToolName.INGEST_CLAIM, req.case_id, req.agent_run) as db:
            row = claim_service.ingest_claim(
                db,
                self._c,
                case_id=req.case_id,
                source=req.source,
                finchnode_claim_id=req.finchnode_claim_id,
                upload=req.claim,
                actor=self._actor,
            )
            return claim_service.claim_model(db, row)

    def get_case_claim(self, req: GetCaseClaimRequest) -> Claim:
        with self._tx(ToolName.GET_CASE_CLAIM, req.case_id, req.agent_run) as db:
            row = claim_service.get_case_claim_row(db, req.case_id)
            if row is None:
                raise NotFound("This case has no claim yet.")
            return claim_service.claim_model(db, row)

    # ------------------------------------------------------------------ visit evidence

    def get_patient_context(self, req: GetPatientContextRequest) -> PatientContext:
        """Consent-gated, minimized patient context (prompt task 6).

        Core is the only authority that links a case to a patient identity; the
        language/agent layers get identifiers and masked labels -- never secrets.
        """
        with self._tx(ToolName.GET_PATIENT_CONTEXT, req.case_id, req.agent_run) as db:
            case = case_service.get_case_row(db, req.case_id)
            return PatientContext(
                case_id=case.id,
                patient_display_name=case.patient_display_name,
                member_ref_masked=mask_member_ref(case.member_ref) if case.member_ref else None,
                payer_name=case.payer_name,
                finchnode_patient_id=case.finchnode_patient_id,
                has_health_data_consent=case_service.has_consent(
                    db, case.id, ConsentType.HEALTH_DATA_ACCESS
                ),
            )

    def get_visit_transcript(self, req: GetVisitTranscriptRequest) -> VisitTranscriptView:
        with self._tx(ToolName.GET_VISIT_TRANSCRIPT, req.case_id, req.agent_run) as db:
            visit = (
                evidence_service.get_visit_row(db, req.visit_id)
                if req.visit_id
                else evidence_service.get_case_visit(db, req.case_id)
            )
            if visit is None:
                return VisitTranscriptView()
            if visit.case_id != req.case_id:
                raise InvalidInput("visit_id does not belong to case_id")
            transcript = evidence_service.get_transcript_row(db, visit)
            return VisitTranscriptView(
                visit=mappers.visit_to_model(visit),
                transcript=mappers.transcript_to_model(transcript) if transcript else None,
            )

    def record_visit_evidence(self, req: RecordVisitEvidenceRequest) -> RecordVisitEvidenceResponse:
        with self._tx(ToolName.RECORD_VISIT_EVIDENCE, req.case_id, req.agent_run) as db:
            rows = evidence_service.record_visit_evidence(
                db,
                case_id=req.case_id,
                visit_id=req.visit_id,
                evidence=req.evidence,
                actor=self._actor,
            )
            db.flush()
            return RecordVisitEvidenceResponse(
                evidence=[mappers.evidence_to_model(r) for r in rows],
                case_state=CaseState(case_service.get_case_row(db, req.case_id).state),
            )

    def get_supporting_evidence(self, req: GetSupportingEvidenceRequest) -> SupportingEvidence:
        with self._tx(ToolName.GET_SUPPORTING_EVIDENCE, req.case_id, req.agent_run) as db:
            case_service.get_case_row(db, req.case_id)
            evidence = evidence_service.list_evidence(db, case_id=req.case_id)
            assessment = finding = None
            if req.line_id:
                assessment = db.scalars(
                    select(LineAssessmentRow).where(
                        LineAssessmentRow.case_id == req.case_id,
                        LineAssessmentRow.claim_line_id == req.line_id,
                    )
                ).first()
                finding = db.scalars(
                    select(FindingRow).where(
                        FindingRow.case_id == req.case_id, FindingRow.claim_line_id == req.line_id
                    )
                ).first()
            return SupportingEvidence(
                evidence=[mappers.evidence_to_model(e) for e in evidence],
                line_assessment=mappers.assessment_to_model(assessment) if assessment else None,
                finding=mappers.finding_to_model(finding) if finding else None,
            )

    # ------------------------------------------------------------------ FinchNode (consent-gated)

    def get_patient_claims(self, req: PatientDataRequest) -> PatientClaims:
        with self._tx(ToolName.GET_PATIENT_CLAIMS, req.case_id, req.agent_run) as db:
            claims = self._c.finchnode.get_patient_claims(self._patient_id(db, req.case_id))
            return PatientClaims(claims=claims)

    def _records(self, pid: str, record_type: RecordType, records: list) -> PatientRecords:
        gap = self._c.finchnode.record_gaps(pid).get(record_type)
        return PatientRecords(records=records, complete=gap is None, gap=gap)

    def get_patient_encounters(self, req: PatientDataRequest) -> PatientRecords:
        with self._tx(ToolName.GET_PATIENT_ENCOUNTERS, req.case_id, req.agent_run) as db:
            pid = self._patient_id(db, req.case_id)
            records = self._c.finchnode.get_patient_encounters(pid)
            return self._records(pid, RecordType.ENCOUNTER, records)

    def get_patient_procedures(self, req: PatientDataRequest) -> PatientRecords:
        with self._tx(ToolName.GET_PATIENT_PROCEDURES, req.case_id, req.agent_run) as db:
            pid = self._patient_id(db, req.case_id)
            records = self._c.finchnode.get_patient_procedures(pid)
            return self._records(pid, RecordType.PROCEDURE, records)

    def get_patient_medications(self, req: PatientDataRequest) -> PatientRecords:
        with self._tx(ToolName.GET_PATIENT_MEDICATIONS, req.case_id, req.agent_run) as db:
            pid = self._patient_id(db, req.case_id)
            records = self._c.finchnode.get_patient_medications(pid)
            return self._records(pid, RecordType.MEDICATION, records)

    # ------------------------------------------------------------------ analysis

    def record_findings(self, req: RecordFindingsRequest) -> RecordFindingsResponse:
        with self._tx(ToolName.RECORD_FINDINGS, req.case_id, req.agent_run) as db:
            return claim_service.record_findings(
                db,
                self._c,
                case_id=req.case_id,
                claim_id=req.claim_id,
                findings=req.findings,
                assessments=req.line_assessments,
                actor=self._actor,
            )

    def get_claim_line(self, req: GetClaimLineRequest) -> ClaimLine:
        with self._tx(ToolName.GET_CLAIM_LINE, req.case_id, req.agent_run) as db:
            line = db.get(ClaimLineRow, req.line_id)
            claim = db.get(ClaimRow, line.claim_id) if line else None
            if line is None or claim is None or claim.case_id != req.case_id:
                raise NotFound(f"Claim line {req.line_id} not found on this case")
            return mappers.line_to_model(line)

    # ------------------------------------------------------------------ disputes

    def get_dispute_case(self, req: GetDisputeCaseRequest) -> DisputeCaseView:
        with self._tx(ToolName.GET_DISPUTE_CASE, req.case_id, req.agent_run) as db:
            return dispute_service.dispute_case_view(db, req.case_id)

    def record_dispute(self, req: RecordDisputeRequest) -> Dispute:
        with self._tx(ToolName.RECORD_DISPUTE, req.case_id, req.agent_run) as db:
            row = dispute_service.record_dispute(
                db,
                self._c,
                case_id=req.case_id,
                claim_id=req.claim_id,
                items=req.items,
                letter_text=req.letter_text,
                total_disputed_cents=req.total_disputed_cents,
                actor=self._actor,
            )
            db.flush()
            return mappers.dispute_to_model(row)

    def record_patient_decision(self, req: RecordPatientDecisionRequest) -> Dispute:
        with self._tx(ToolName.RECORD_PATIENT_DECISION, req.case_id, req.agent_run) as db:
            if dispute_service.get_dispute_row(db, req.dispute_id).case_id != req.case_id:
                raise InvalidInput("dispute_id does not belong to case_id")
            if req.decision == PatientDecision.APPROVE:
                row = dispute_service.approve(
                    db,
                    self._c,
                    dispute_id=req.dispute_id,
                    actor_kind=req.actor,
                    channel=req.channel,
                    authorization_text=req.authorization_text,
                    authorize_call=req.authorize_call,
                    actor=self._actor,
                )
            else:
                row = dispute_service.reject(
                    db,
                    self._c,
                    dispute_id=req.dispute_id,
                    actor_kind=req.actor,
                    channel=req.channel,
                    reason=req.reason,
                    actor=self._actor,
                )
            db.flush()
            return mappers.dispute_to_model(row)

    def resolve_human_intervention(
        self, req: ResolveHumanInterventionRequest
    ) -> HumanResolutionResult:
        self._authorize(ToolName.RESOLVE_HUMAN_INTERVENTION, req.agent_run, req.case_id)
        result = workflows.resolve_intervention(
            self._c,
            intervention_id=req.intervention_id,
            choice=req.choice,
            notes=req.notes,
            actor_kind=req.actor,
            channel=req.channel,
            actor=self._actor,
            expected_case_id=req.case_id,
        )
        with self._c.session_scope() as db:
            self._audit(db, ToolName.RESOLVE_HUMAN_INTERVENTION, req.case_id, req.agent_run)
        return result

    # ------------------------------------------------------------------ calls

    def request_human(self, req: RequestHumanRequest) -> HumanIntervention:
        self._authorize(ToolName.REQUEST_HUMAN, req.agent_run, req.case_id)
        intervention = workflows.request_human(
            self._c,
            case_id=req.case_id,
            reason=req.reason,
            prompt=req.detail,
            options=HANDOFF_OPTIONS,
            actor=self._actor,
            call_id=req.call_id,
        )
        with self._c.session_scope() as db:
            self._audit(db, ToolName.REQUEST_HUMAN, req.case_id, req.agent_run)
        return intervention

    def start_insurance_call(self, req: StartInsuranceCallRequest) -> CallSession:
        self._authorize(ToolName.START_INSURANCE_CALL, req.agent_run, req.case_id)
        call = self._c.voice_manager.start_call(req.case_id, actor=self._actor)
        with self._c.session_scope() as db:
            self._audit(db, ToolName.START_INSURANCE_CALL, req.case_id, req.agent_run)
        return call

    def record_insurer_response(self, req: RecordInsurerResponseRequest) -> InsurerResponse:
        with self._tx(ToolName.RECORD_INSURER_RESPONSE, req.case_id, req.agent_run) as db:
            return voice_service.record_insurer_response(db, req, actor=self._actor)
