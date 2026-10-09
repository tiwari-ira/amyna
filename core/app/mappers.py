"""Row -> contract model mapping (and the reverse for JSON columns).

Keeping this in one place means services return contract models and routers
never see SQLAlchemy objects.
"""

from typing import Any

from pydantic import BaseModel

from claimshield_contracts.models import (
    AgentRun,
    CallSession,
    Case,
    Claim,
    ClaimLine,
    CodeRef,
    Consent,
    DisputeCandidate,
    EvidenceItem,
    HumanDecision,
    HumanIntervention,
    LineAssessment,
    PatientNotification,
    PatientRef,
    Transcript,
    Visit,
)
from claimshield_contracts.models import (
    Dispute as DisputeModel,
)

from .db import as_utc
from .models_db import (
    AgentRunRow,
    CallRow,
    CaseRow,
    ClaimLineRow,
    ClaimRow,
    ConsentRow,
    DecisionRow,
    DisputeRow,
    EvidenceRow,
    FindingRow,
    InterventionRow,
    LineAssessmentRow,
    NotificationRow,
    TranscriptRow,
    VisitRow,
)


def dump(model: BaseModel) -> dict[str, Any]:
    """JSON-safe dict for JSON columns."""
    return model.model_dump(mode="json")


def dump_all(models: list[Any]) -> list[dict[str, Any]]:
    return [dump(m) for m in models]


def case_to_model(row: CaseRow) -> Case:
    return Case(
        case_id=row.id,
        state=row.state,
        channel=row.channel,
        patient=PatientRef(
            finchnode_patient_id=row.finchnode_patient_id,
            display_name=row.patient_display_name,
            member_ref=row.member_ref,
            payer_name=row.payer_name,
            payer_phone=row.payer_phone,
            state=row.patient_state,
        ),
        external_ref=row.external_ref,
        created_at=as_utc(row.created_at),
        updated_at=as_utc(row.updated_at),
    )


def consent_to_model(row: ConsentRow) -> Consent:
    return Consent(
        consent_id=row.id,
        case_id=row.case_id,
        consent_type=row.consent_type,
        granted=row.granted,
        consent_text=row.consent_text,
        actor=row.actor,
        channel=row.channel,
        created_at=as_utc(row.created_at),
    )


def visit_to_model(row: VisitRow) -> Visit:
    return Visit(
        visit_id=row.id,
        case_id=row.case_id,
        provider_name=row.provider_name,
        facility_name=row.facility_name,
        occurred_at=as_utc(row.occurred_at),
        recording_ref=row.recording_ref,
        recording_consent_id=row.recording_consent_id,
        status=row.status,
        transcript_id=row.transcript_id,
        created_at=as_utc(row.created_at),
    )


def transcript_to_model(row: TranscriptRow) -> Transcript:
    return Transcript(
        transcript_id=row.id,
        visit_id=row.visit_id,
        language=row.language,
        source=row.source,
        segments=row.segments,
        created_at=as_utc(row.created_at),
    )


def evidence_to_model(row: EvidenceRow) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=row.id,
        case_id=row.case_id,
        visit_id=row.visit_id,
        source=row.source,
        kind=row.kind,
        concept=row.concept,
        description=row.description,
        codes=row.codes,
        attributes=row.attributes,
        segment_ids=row.segment_ids,
        quote=row.quote,
        negated=row.negated,
        confidence=row.confidence,
        extractor=row.extractor,
        created_at=as_utc(row.created_at),
    )


def line_to_model(row: ClaimLineRow) -> ClaimLine:
    return ClaimLine(
        line_id=row.id,
        claim_id=row.claim_id,
        line_number=row.line_number,
        code=CodeRef(
            system=row.code_system,
            code=row.code,
            display=row.code_display,
            source_system_uri=row.code_source_uri,
        ),
        description=row.description,
        date_of_service=row.date_of_service,
        units=row.units,
        billed_cents=row.billed_cents,
        modifiers=row.modifiers,
        revenue_code=row.revenue_code,
        ndc=row.ndc,
    )


def claim_to_model(row: ClaimRow, lines: list[ClaimLineRow]) -> Claim:
    return Claim(
        claim_id=row.id,
        case_id=row.case_id,
        external_claim_id=row.external_claim_id,
        payer_name=row.payer_name,
        provider_name=row.provider_name,
        facility_name=row.facility_name,
        date_of_service=row.date_of_service,
        total_billed_cents=row.total_billed_cents,
        source=row.source,
        lines=[line_to_model(line) for line in sorted(lines, key=lambda r: r.line_number)],
        received_at=as_utc(row.received_at),
    )


def assessment_to_model(row: LineAssessmentRow) -> LineAssessment:
    return LineAssessment(
        claim_line_id=row.claim_line_id,
        line_number=row.line_number,
        status=row.status,
        concept=row.concept,
        evidence_refs=row.evidence_refs,
        note=row.note,
    )


def finding_to_model(row: FindingRow) -> DisputeCandidate:
    return DisputeCandidate(
        finding_id=row.id,
        case_id=row.case_id,
        claim_id=row.claim_id,
        claim_line_id=row.claim_line_id,
        line_number=row.line_number,
        issue_type=row.issue_type,
        title=row.title,
        explanation=row.explanation,
        patient_facing_explanation=row.patient_facing_explanation,
        amount_cents=row.amount_cents,
        confidence=row.confidence,
        human_confirmation_required=row.human_confirmation_required,
        confirmation=row.confirmation,
        status=row.status,
        recommended_action=row.recommended_action,
        evidence_refs=row.evidence_refs,
        policy_citations=row.policy_citations,
        patient_answer=row.patient_answer,
        created_at=as_utc(row.created_at),
    )


def dispute_to_model(row: DisputeRow) -> DisputeModel:
    return DisputeModel(
        dispute_id=row.id,
        case_id=row.case_id,
        claim_id=row.claim_id,
        status=row.status,
        items=row.items,
        total_disputed_cents=row.total_disputed_cents,
        letter_text=row.letter_text,
        patient_authorization_id=row.patient_authorization_id,
        rejected_reason=row.rejected_reason,
        created_at=as_utc(row.created_at),
        approved_at=as_utc(row.approved_at),
    )


def decision_to_model(row: DecisionRow) -> HumanDecision:
    return HumanDecision(
        decision_id=row.id,
        case_id=row.case_id,
        decision_type=row.decision_type,
        choice=row.choice,
        actor=row.actor,
        channel=row.channel,
        notes=row.notes,
        authorization_text=row.authorization_text,
        authorize_call=row.authorize_call,
        intervention_id=row.intervention_id,
        dispute_id=row.dispute_id,
        finding_id=row.finding_id,
        created_at=as_utc(row.created_at),
    )


def intervention_to_model(
    row: InterventionRow, decision: DecisionRow | None = None
) -> HumanIntervention:
    return HumanIntervention(
        intervention_id=row.id,
        case_id=row.case_id,
        reason=row.reason,
        prompt=row.prompt,
        options=row.options,
        status=row.status,
        call_id=row.call_id,
        finding_id=row.finding_id,
        notified=row.notified,
        created_at=as_utc(row.created_at),
        resolved_at=as_utc(row.resolved_at),
        resolution=decision_to_model(decision) if decision else None,
    )


def call_to_model(row: CallRow) -> CallSession:
    return CallSession(
        call_id=row.id,
        case_id=row.case_id,
        dispute_id=row.dispute_id,
        status=row.status,
        merge_phase=row.merge_phase,
        provider_call_ref=row.provider_call_ref,
        to_number_masked=row.to_number_masked,
        turns=row.turns,
        insurer_response=row.insurer_response,
        open_intervention_id=row.open_intervention_id,
        started_at=as_utc(row.started_at),
        ended_at=as_utc(row.ended_at),
        end_reason=row.end_reason,
    )


def agent_run_to_model(row: AgentRunRow) -> AgentRun:
    return AgentRun(
        run_id=row.id,
        case_id=row.case_id,
        agent=row.agent,
        request_type=row.request_type,
        request_id=row.request_id,
        status=row.status,
        error=row.error,
        started_at=as_utc(row.started_at),
        finished_at=as_utc(row.finished_at),
    )


def notification_to_model(row: NotificationRow) -> PatientNotification:
    return PatientNotification(
        notification_id=row.id,
        case_id=row.case_id,
        kind=row.kind,
        text=row.text,
        buttons=row.buttons,
        data=row.data,
        intervention_id=row.intervention_id,
        dispute_id=row.dispute_id,
        delivered=row.delivered,
        delivery_ref=row.delivery_ref,
        created_at=as_utc(row.created_at),
    )
