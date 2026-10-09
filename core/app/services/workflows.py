"""Operations that span several transactions (bridge calls, voice sessions).

Rule: commit Core's own writes BEFORE handing control to the fetch bridge or the
voice manager, because those open their own transactions (one per tool call,
exactly like the HTTP path). Used by public routers and by ToolService.
"""

from typing import TYPE_CHECKING

from claimshield_contracts.api import (
    ClaimAnalysisResult,
    DisputePreparation,
    HumanResolutionResult,
    VisitComplete,
    VisitEvidenceResult,
)
from claimshield_contracts.enums import (
    Actor,
    CaseState,
    Channel,
    HandoffChoice,
    HumanReason,
    VisitStatus,
    WorkStatus,
)
from claimshield_contracts.models import HumanIntervention

from .. import mappers
from ..adapters.fetch_bridge.base import BridgeResult
from ..errors import Conflict, InvalidInput, NotFound
from ..models_db import CallRow, DecisionRow, FindingRow
from . import claim_service, evidence_service, human_service, notification_service
from .case_service import get_case_row, latest_dispute

if TYPE_CHECKING:
    from ..container import Container


def complete_visit(
    container: "Container", *, visit_id: str, body: VisitComplete, actor: str
) -> VisitEvidenceResult:
    """Store the transcript, then let the Visit logic extract evidence.

    Retry-safe: if the transcript was stored but extraction failed, a retry re-runs
    extraction on the stored transcript (the new body is ignored); once evidence
    exists the call just returns it.
    """
    with container.session_scope() as db:
        visit = evidence_service.get_visit_row(db, visit_id)
        if visit.transcript_id is None:
            evidence_service.complete_visit(db, visit_id=visit_id, body=body, actor=actor)
        needs_extraction = visit.status != VisitStatus.EVIDENCE_EXTRACTED.value
        case_id = visit.case_id
    bridge = (
        container.fetch_bridge.extract_visit_evidence(case_id, visit_id)
        if needs_extraction
        else BridgeResult(status=WorkStatus.COMPLETED)
    )
    with container.session_scope() as db:
        visit = evidence_service.get_visit_row(db, visit_id)
        return VisitEvidenceResult(
            status=bridge.status,
            case_state=CaseState(get_case_row(db, case_id).state),
            visit=mappers.visit_to_model(visit),
            evidence=[
                mappers.evidence_to_model(e)
                for e in evidence_service.list_evidence(db, visit_id=visit_id)
            ],
        )


def analyze_claim(container: "Container", *, claim_id: str) -> ClaimAnalysisResult:
    """Run claim analysis once; afterwards return the stored analysis (idempotent)."""
    with container.session_scope() as db:
        claim = claim_service.get_claim_row(db, claim_id)
        case_id = claim.case_id
        needs_analysis = get_case_row(db, case_id).state == CaseState.CLAIM_RECEIVED.value
    if needs_analysis:
        container.fetch_bridge.analyze_claim(case_id, claim_id)
    with container.session_scope() as db:
        return claim_service.analysis_result(db, claim_id)


def prepare_dispute(container: "Container", *, case_id: str) -> DisputePreparation:
    """Ask the Dispute logic to draft the package (idempotent once it exists)."""
    with container.session_scope() as db:
        case = get_case_row(db, case_id)
        existing = latest_dispute(db, case_id)
        if existing is None and case.state != CaseState.FINDINGS_CONFIRMED.value:
            raise Conflict(
                f"Case must be FINDINGS_CONFIRMED to prepare a dispute (is {case.state}).",
                code="not_ready_for_dispute",
            )
    if existing is None:
        bridge = container.fetch_bridge.prepare_dispute(case_id)
        status = bridge.status
    else:
        status = WorkStatus.COMPLETED
    with container.session_scope() as db:
        dispute = latest_dispute(db, case_id)
        return DisputePreparation(
            status=status if dispute is None else WorkStatus.COMPLETED,
            case_state=CaseState(get_case_row(db, case_id).state),
            dispute=mappers.dispute_to_model(dispute) if dispute else None,
        )


def resolve_intervention(
    container: "Container",
    *,
    intervention_id: str,
    choice: str,
    notes: str | None,
    actor_kind: Actor,
    channel: Channel,
    actor: str,
    expected_case_id: str | None = None,
) -> HumanResolutionResult:
    """Resolve a human intervention; call-linked ones resume or cancel the call."""
    with container.session_scope() as db:
        if expected_case_id is not None:
            row = human_service.get_intervention_row(db, intervention_id)
            if row.case_id != expected_case_id:
                raise InvalidInput("intervention_id does not belong to case_id")
        row, decision = human_service.resolve(
            db,
            container,
            intervention_id=intervention_id,
            choice=choice,
            notes=notes,
            actor_kind=actor_kind,
            channel=channel,
            actor=actor,
        )
        call_id, case_id, decision_id = row.call_id, row.case_id, decision.id
        choice_made = decision.choice
    if call_id:
        if choice_made == HandoffChoice.DONE.value:
            container.voice_manager.resume_after_human(call_id, actor=actor)
        else:
            container.voice_manager.cancel_after_human(call_id, actor=actor)
    with container.session_scope() as db:
        row = human_service.get_intervention_row(db, intervention_id)
        upcoming = human_service.next_open_confirmation(db, case_id)
        return HumanResolutionResult(
            intervention=mappers.intervention_to_model(row, db.get(DecisionRow, decision_id)),
            case_state=CaseState(get_case_row(db, case_id).state),
            next_question=mappers.intervention_to_model(upcoming) if upcoming else None,
            call=container.voice_manager.get_call(call_id) if call_id else None,
        )


def request_human(
    container: "Container",
    *,
    case_id: str,
    reason: HumanReason,
    prompt: str,
    options: list[str],
    actor: str,
    call_id: str | None = None,
    finding_id: str | None = None,
) -> HumanIntervention:
    """Create a human intervention. With a call_id the call is paused (HUMAN_REQUIRED)."""
    if call_id:
        with container.session_scope() as db:
            call = db.get(CallRow, call_id)
            if call is None:
                raise NotFound(f"Call {call_id} not found")
            if call.case_id != case_id:
                raise InvalidInput("call_id does not belong to case_id")
        return container.voice_manager.request_handoff(
            call_id, reason=reason, detail=prompt, actor=actor
        )
    if reason == HumanReason.PATIENT_CONFIRMATION:
        raise InvalidInput("Patient confirmations are created by Core from findings.")
    with container.session_scope() as db:
        case = get_case_row(db, case_id)
        if finding_id is not None:
            finding = db.get(FindingRow, finding_id)
            if finding is None or finding.case_id != case.id:
                raise InvalidInput("finding_id does not belong to case_id")
        row = human_service.create_intervention(
            db,
            case_id=case.id,
            reason=reason,
            prompt=prompt,
            options=options,
            actor=actor,
            finding_id=finding_id,
        )
        notification_service.notify_intervention(db, container, case, row)
        return mappers.intervention_to_model(row)
