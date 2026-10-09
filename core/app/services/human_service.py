"""Human-in-the-loop: patient confirmation questions and call handoffs.

Confirmation questions are created for every uncertain finding but delivered
ONE AT A TIME (decision D6). Answers change finding status through the
finding's ``confirmation.effects``. Call-linked interventions are resumed or
cancelled by ``workflows.resolve_intervention`` (it owns the voice session).
"""

from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from claimshield_contracts.enums import (
    Actor,
    CaseState,
    Channel,
    DecisionType,
    HandoffChoice,
    HumanReason,
    InterventionStatus,
    PatientAnswer,
)
from claimshield_contracts.ids import new_id

from ..audit import record_audit
from ..db import utcnow
from ..errors import Conflict, InvalidInput, NotFound
from ..models_db import CaseRow, DecisionRow, FindingRow, InterventionRow
from . import notification_service
from .case_service import build_guard_context, get_case_row, transition
from .security_policy import reject_sensitive_notes

if TYPE_CHECKING:
    from ..container import Container

CONFIRMATION_OPTIONS = [a.value for a in PatientAnswer]
HANDOFF_OPTIONS = [c.value for c in HandoffChoice]
_CHOICE_ALIASES = {
    "Y": "YES",
    "YEAH": "YES",
    "YEP": "YES",
    "N": "NO",
    "NOPE": "NO",
    "NOTSURE": "NOT_SURE",
    "UNSURE": "NOT_SURE",
    "DONT_KNOW": "NOT_SURE",
    "I_DONT_KNOW": "NOT_SURE",
    "VERIFIED": "DONE",
    "FINISHED": "DONE",
}


def normalize_choice(choice: str, options: list[str]) -> str:
    """Map free-form button/text replies onto one of ``options``.

    The raw input is never echoed back: patients sometimes type secrets into replies.
    """
    key = choice.strip().upper().replace("'", "").replace("-", "_").replace(" ", "_")
    key = _CHOICE_ALIASES.get(key, key)
    if key not in options:
        raise InvalidInput(f"Choice must be one of {options}", options=options)
    return key


def is_confirmation(row: InterventionRow) -> bool:
    """A finding question (never call-linked); everything else is a handoff."""
    return (
        row.reason == HumanReason.PATIENT_CONFIRMATION.value
        and row.call_id is None
        and row.finding_id is not None
    )


def get_intervention_row(db: Session, intervention_id: str) -> InterventionRow:
    row = db.get(InterventionRow, intervention_id)
    if row is None:
        raise NotFound(f"Human intervention {intervention_id} not found")
    return row


def create_intervention(
    db: Session,
    *,
    case_id: str,
    reason: HumanReason,
    prompt: str,
    options: list[str],
    actor: str,
    call_id: str | None = None,
    finding_id: str | None = None,
    sequence: int = 0,
) -> InterventionRow:
    row = InterventionRow(
        id=new_id("intervention"),
        case_id=case_id,
        reason=reason.value,
        prompt=prompt,
        options=options,
        status=InterventionStatus.OPEN.value,
        sequence=sequence,
        call_id=call_id,
        finding_id=finding_id,
    )
    db.add(row)
    db.flush()
    record_audit(
        db,
        actor=actor,
        action="human.intervention_created",
        case_id=case_id,
        resource_type="intervention",
        resource_id=row.id,
        details={"reason": reason.value, "call_id": call_id, "finding_id": finding_id},
    )
    return row


def create_confirmations(
    db: Session, *, case_id: str, findings: list[FindingRow], actor: str
) -> list[InterventionRow]:
    """One PATIENT_CONFIRMATION intervention per uncertain finding, in line order."""
    ordered = sorted(findings, key=lambda f: f.line_number)
    return [
        create_intervention(
            db,
            case_id=case_id,
            reason=HumanReason.PATIENT_CONFIRMATION,
            prompt=(finding.confirmation or {})["question"],
            options=CONFIRMATION_OPTIONS,
            actor=actor,
            finding_id=finding.id,
            sequence=index,
        )
        for index, finding in enumerate(ordered)
    ]


def open_confirmations(db: Session, case_id: str) -> list[InterventionRow]:
    """Unanswered confirmation questions, in the order they will be asked."""
    stmt = (
        select(InterventionRow)
        .where(InterventionRow.case_id == case_id)
        .where(InterventionRow.reason == HumanReason.PATIENT_CONFIRMATION.value)
        .where(InterventionRow.status == InterventionStatus.OPEN.value)
        .order_by(InterventionRow.sequence)
    )
    return list(db.scalars(stmt))


def next_open_confirmation(db: Session, case_id: str) -> InterventionRow | None:
    pending = open_confirmations(db, case_id)
    return pending[0] if pending else None


def open_call_interventions(db: Session, call_id: str) -> list[InterventionRow]:
    stmt = (
        select(InterventionRow)
        .where(InterventionRow.call_id == call_id)
        .where(InterventionRow.status == InterventionStatus.OPEN.value)
    )
    return list(db.scalars(stmt))


def resolve(
    db: Session,
    container: "Container",
    *,
    intervention_id: str,
    choice: str,
    notes: str | None,
    actor_kind: Actor,
    channel: Channel,
    actor: str,
) -> tuple[InterventionRow, DecisionRow]:
    """Record the human's answer. Confirmation answers also advance the case."""
    row = get_intervention_row(db, intervention_id)
    if row.status != InterventionStatus.OPEN.value:
        raise Conflict("This intervention is already resolved.", code="already_resolved")
    reject_sensitive_notes(choice)
    reject_sensitive_notes(notes)
    normalized = normalize_choice(choice, row.options)
    confirmation = is_confirmation(row)
    decision = DecisionRow(
        id=new_id("decision"),
        case_id=row.case_id,
        decision_type=(
            DecisionType.FINDING_ANSWER if confirmation else DecisionType.HANDOFF_RESOLUTION
        ).value,
        choice=normalized,
        actor=actor_kind.value,
        channel=channel.value,
        notes=notes,
        intervention_id=row.id,
        finding_id=row.finding_id,
    )
    db.add(decision)
    row.status = InterventionStatus.RESOLVED.value
    row.resolved_at = utcnow()
    row.decision_id = decision.id
    record_audit(
        db,
        actor=actor,
        action="human.intervention_resolved",
        case_id=row.case_id,
        resource_type="intervention",
        resource_id=row.id,
        details={"reason": row.reason, "choice": normalized, "channel": channel.value},
    )
    if confirmation:
        _apply_answer(db, row, PatientAnswer(normalized))
        _advance_confirmations(db, container, get_case_row(db, row.case_id), actor=actor)
    return row, decision


def _apply_answer(db: Session, intervention: InterventionRow, answer: PatientAnswer) -> None:
    finding = db.get(FindingRow, intervention.finding_id)
    if finding is None or not finding.confirmation:
        raise InvalidInput("Confirmation is not linked to a finding with answer effects.")
    finding.status = finding.confirmation["effects"][answer.value]
    finding.patient_answer = answer.value


def _advance_confirmations(
    db: Session, container: "Container", case: CaseRow, *, actor: str
) -> None:
    """Ask the next question, or close the confirmation step."""
    db.flush()
    upcoming = next_open_confirmation(db, case.id)
    if upcoming is not None:
        notification_service.notify_question(db, container, case, upcoming)
        return
    if build_guard_context(db, case).disputable_findings_count:
        transition(db, case, CaseState.FINDINGS_CONFIRMED, actor=actor)
        notification_service.notify_status(
            db, container, case, "Thanks — your answers are recorded. Preparing your dispute."
        )
    else:
        transition(db, case, CaseState.RESOLVED, actor=actor, reason="nothing left to dispute")
        notification_service.notify_status(
            db,
            container,
            case,
            "Thanks — based on your answers there is nothing to dispute on this bill.",
            final=True,
        )
