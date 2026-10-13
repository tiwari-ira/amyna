"""Patient notifications: stored in Core's outbox, then delivered by the notifier.

The ``text`` here is default copy. The Relay service may re-render it from
``kind`` + ``data`` (relay/src/ux/messages.ts). Buttons carry stable ids that
come back on POST /relay/webhook as ``choice``.

Network notifiers (``deferred``, i.e. NOTIFY_MODE=relay_service) deliver only
AFTER the transaction that created the notification commits, on one background
worker (so order is kept). The request never waits on the Relay service -- which
may itself be waiting on this request -- the patient never hears about a change
that rolled back, and no database lock is held during the HTTP call.
"""

import logging
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import TYPE_CHECKING

from sqlalchemy import event
from sqlalchemy.orm import Session

from claimshield_contracts.enums import NotificationKind
from claimshield_contracts.ids import new_id
from claimshield_contracts.models import Case, NotificationButton, PatientNotification

from ..audit import record_audit
from ..mappers import case_to_model, dump_all, notification_to_model
from ..models_db import CaseRow, DisputeRow, InterventionRow, NotificationRow

if TYPE_CHECKING:
    from ..container import Container

log = logging.getLogger("claimshield.notify")

_PENDING = "amyna.pending_notifications"
_LISTENING = "amyna.pending_listeners"


class NotificationDispatcher:
    """Runs deliveries off the request thread, one at a time (keeps their order)."""

    def __init__(self) -> None:
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="amyna-notify")

    def submit(self, work: Callable[[], None]) -> Future:
        future = self._pool.submit(work)
        future.add_done_callback(_log_failure)
        return future

    def drain(self, timeout: float = 10.0) -> None:
        """Wait for everything queued so far (tests, shutdown)."""
        self._pool.submit(lambda: None).result(timeout=timeout)


def _log_failure(future: Future) -> None:
    if future.exception() is not None:
        log.error(
            "notification delivery crashed",
            extra={"fields": {"error": type(future.exception()).__name__}},
        )


def _deliver(container: "Container", notification: PatientNotification, case: Case) -> None:
    """Send one stored notification and record the outcome in its own transaction."""
    try:
        result = container.notifier.send(notification, case)
    except Exception as exc:  # notifiers must not raise; never lose the outcome if one does
        log.warning("notifier raised", extra={"fields": {"error": type(exc).__name__}})
        delivered, ref, error = False, None, type(exc).__name__
    else:
        delivered, ref, error = result.delivered, result.ref, result.error
    with container.session_scope() as db:
        row = db.get(NotificationRow, notification.notification_id)
        if row is not None:
            row.delivered = delivered
            row.delivery_ref = ref
        record_audit(
            db,
            actor="core",
            action="notification.delivered" if delivered else "notification.delivery_failed",
            case_id=case.case_id,
            resource_type="notification",
            resource_id=notification.notification_id,
            details={"kind": notification.kind.value, "delivered": delivered, "error": error},
        )


def _dispatch_pending(session: Session) -> None:
    for container, notification, case in session.info.pop(_PENDING, []):
        container.notification_dispatcher.submit(
            lambda c=container, n=notification, k=case: _deliver(c, n, k)
        )


def _drop_pending(session: Session) -> None:
    session.info.pop(_PENDING, None)


def _deliver_after_commit(
    db: Session, container: "Container", notification: PatientNotification, case: Case
) -> None:
    if not db.info.get(_LISTENING):
        event.listen(db, "after_commit", _dispatch_pending)
        event.listen(db, "after_rollback", _drop_pending)
        db.info[_LISTENING] = True
    db.info.setdefault(_PENDING, []).append((container, notification, case))


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
    notification, case_model = notification_to_model(row), case_to_model(case)
    if getattr(container.notifier, "deferred", False):
        _deliver_after_commit(db, container, notification, case_model)
        details: dict[str, object] = {"kind": kind.value, "delivery": "after_commit"}
    else:
        result = container.notifier.send(notification, case_model)
        row.delivered = result.delivered
        row.delivery_ref = result.ref
        details = {"kind": kind.value, "delivered": result.delivered, "error": result.error}
    if intervention is not None:
        intervention.notified = True
    record_audit(
        db,
        actor="core",
        action="notification.sent",
        case_id=case.id,
        resource_type="notification",
        resource_id=row.id,
        details=details,
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
    db: Session,
    container: "Container",
    case: CaseRow,
    intervention: InterventionRow,
    *,
    dialing: str | None = None,
    problem: str | None = None,
) -> NotificationRow:
    """The call needs the patient. ``dialing`` = the masked number being called now;
    otherwise ``problem`` says why they can't be added to the call."""
    payer = case.payer_name or "your insurer"
    if dialing:
        text = (
            f"Your call with {payer} needs you. {intervention.prompt} I'm calling you now at "
            f"{dialing} to add you to the same call. Answer it and tell the representative "
            "yourself. I never share that for you, so please don't type it here. When you "
            "hang up (or tap Done), I'll take the call back over."
        )
    elif problem:
        text = (
            f"Your call with {payer} needs you. {intervention.prompt} I can't add you to the "
            f"call because {problem}, so they're on hold. I never share your SSN, PINs or "
            "passwords for you. Tap Done to have me continue without it, or Cancel call "
            "to end the call."
        )
    else:
        text = (
            f"Your call with {payer} needs you. {intervention.prompt} I never share your "
            "SSN, PINs or passwords. Tap Done to have me continue, or Cancel call to end it."
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
