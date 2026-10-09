"""Patient notifications: stored in Core's outbox, then delivered by the notifier.

The ``text`` here is default copy. The Relay service may re-render it from
``kind`` + ``data`` (relay/src/ux/messages.ts). Buttons carry stable ids that
come back on POST /relay/webhook as ``choice``.
"""

from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from claimshield_contracts.enums import NotificationKind
from claimshield_contracts.ids import new_id
from claimshield_contracts.models import NotificationButton

from ..audit import record_audit
from ..mappers import case_to_model, dump_all, notification_to_model
from ..models_db import CaseRow, DisputeRow, InterventionRow, NotificationRow

if TYPE_CHECKING:
    from ..container import Container

QUESTION_BUTTONS = [
    NotificationButton(id="YES", label="Yes"),
    NotificationButton(id="NO", label="No"),
    NotificationButton(id="NOT_SURE", label="Not sure"),
]
APPROVAL_BUTTONS = [
    NotificationButton(id="REVIEW", label="Review"),
    NotificationButton(id="APPROVE", label="Approve"),
    NotificationButton(id="REJECT", label="Reject"),
]
HANDOFF_BUTTONS = [
    NotificationButton(id="DONE", label="Done"),
    NotificationButton(id="CANCEL", label="Cancel call"),
]


def format_cents(cents: int) -> str:
    """``107000`` -> ``$1,070.00``."""
    return f"${cents / 100:,.2f}"


def approval_authorization_text(payer_name: str | None, total_cents: int) -> str:
    """The exact wording a patient accepts when approving (stored with the decision)."""
    payer = payer_name or "my insurer"
    return (
        f"I approve this dispute of {format_cents(total_cents)} and authorize Amyna to "
        f"call {payer} on my behalf. Amyna will never share my SSN, PINs or passwords "
        "and will pause the call for me if they are requested."
    )


def notify(
    db: Session,
    container: "Container",
    *,
    case: CaseRow,
    kind: NotificationKind,
    text: str,
    buttons: list[NotificationButton] | None = None,
    data: dict[str, str] | None = None,
    intervention: InterventionRow | None = None,
    dispute_id: str | None = None,
) -> NotificationRow:
    """Store a notification and hand it to the configured notifier."""
    row = NotificationRow(
        id=new_id("notification"),
        case_id=case.id,
        kind=kind.value,
        text=text,
        buttons=dump_all(buttons or []),
        data=data or {},
        intervention_id=intervention.id if intervention else None,
        dispute_id=dispute_id,
    )
    db.add(row)
    db.flush()
    result = container.notifier.send(notification_to_model(row), case_to_model(case))
    row.delivered = result.delivered
    row.delivery_ref = result.ref
    if intervention is not None:
        intervention.notified = True
    record_audit(
        db,
        actor="core",
        action="notification.sent",
        case_id=case.id,
        resource_type="notification",
        resource_id=row.id,
        details={"kind": kind.value, "delivered": result.delivered, "error": result.error},
    )
    return row


def notify_question(
    db: Session, container: "Container", case: CaseRow, intervention: InterventionRow
) -> NotificationRow:
    return notify(
        db,
        container,
        case=case,
        kind=NotificationKind.CONFIRMATION_QUESTION,
        text=intervention.prompt,
        buttons=QUESTION_BUTTONS,
        data={"finding_id": intervention.finding_id or ""},
        intervention=intervention,
    )


def notify_approval_request(
    db: Session, container: "Container", case: CaseRow, dispute: DisputeRow
) -> NotificationRow:
    total = format_cents(dispute.total_disputed_cents)
    text = (
        f"Your dispute package is ready: {len(dispute.items)} item(s), {total}. "
        "Tap Review to read the letter. Approve only if you agree.\n\n"
        + approval_authorization_text(case.payer_name, dispute.total_disputed_cents)
    )
    return notify(
        db,
        container,
        case=case,
        kind=NotificationKind.APPROVAL_REQUEST,
        text=text,
        buttons=APPROVAL_BUTTONS,
        data={"dispute_id": dispute.id, "total": total, "items": str(len(dispute.items))},
        dispute_id=dispute.id,
    )


def notify_handoff(
    db: Session, container: "Container", case: CaseRow, intervention: InterventionRow
) -> NotificationRow:
    payer = case.payer_name or "your insurer"
    text = (
        f"Amyna paused your call with {payer}. {intervention.prompt} "
        "Amyna never shares your SSN, PINs or passwords. Please verify directly with "
        "the representative, then tap Done to let Amyna continue."
    )
    return notify(
        db,
        container,
        case=case,
        kind=NotificationKind.HUMAN_HANDOFF,
        text=text,
        buttons=HANDOFF_BUTTONS,
        data={"call_id": intervention.call_id or "", "reason": intervention.reason},
        intervention=intervention,
    )


def notify_status(
    db: Session, container: "Container", case: CaseRow, text: str, *, final: bool = False
) -> NotificationRow:
    kind = NotificationKind.FINAL_REPORT if final else NotificationKind.STATUS_UPDATE
    return notify(db, container, case=case, kind=kind, text=text, data={"state": case.state})


def notify_intervention(
    db: Session, container: "Container", case: CaseRow, intervention: InterventionRow
) -> NotificationRow:
    """Generic human request (not tied to a call): the prompt plus its options as buttons."""
    return notify(
        db,
        container,
        case=case,
        kind=NotificationKind.HUMAN_HANDOFF,
        text=intervention.prompt,
        buttons=[
            NotificationButton(id=option, label=option.replace("_", " ").title())
            for option in intervention.options
        ],
        data={"reason": intervention.reason},
        intervention=intervention,
    )
