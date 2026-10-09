"""Signed patient events from the Relay service (POST /relay/webhook).

Idempotency: the ``event_id`` is claimed in its own transaction BEFORE any
processing, so concurrent duplicates cannot both run. Domain errors (for example
"already resolved") are recorded and acknowledged so Relay does not retry
forever; unexpected errors release the claim so a retry can succeed.
"""

from typing import TYPE_CHECKING

from sqlalchemy.exc import IntegrityError

from claimshield_contracts.api import RelayWebhookEvent, RelayWebhookResult
from claimshield_contracts.enums import (
    Actor,
    CaseState,
    Channel,
    RelayEventType,
    WebhookResultStatus,
)

from ..audit import record_audit
from ..errors import DomainError
from ..models_db import NotificationRow, WebhookEventRow
from . import dispute_service, workflows
from .case_service import get_case_row
from .notification_service import approval_authorization_text

if TYPE_CHECKING:
    from ..container import Container

ACTOR = "service:relay"


def process_relay_event(container: "Container", event: RelayWebhookEvent) -> RelayWebhookResult:
    stored = _claim_event(container, event)
    if stored is not None:
        return stored.model_copy(update={"status": WebhookResultStatus.DUPLICATE})
    try:
        result = _dispatch(container, event)
    except DomainError as exc:
        result = RelayWebhookResult(
            status=WebhookResultStatus.IGNORED, event_id=event.event_id, detail=exc.message
        )
    except Exception:
        with container.session_scope() as db:
            row = db.get(WebhookEventRow, event.event_id)
            if row is not None:
                db.delete(row)
        raise
    with container.session_scope() as db:
        db.get(WebhookEventRow, event.event_id).result = result.model_dump(mode="json")
    return result


def _claim_event(container: "Container", event: RelayWebhookEvent) -> RelayWebhookResult | None:
    """Insert the idempotency row; return the stored result if it already existed."""
    try:
        with container.session_scope() as db:
            db.add(
                WebhookEventRow(
                    event_id=event.event_id,
                    event_type=event.event_type.value,
                    case_id=event.case_id,
                    result={},
                )
            )
    except IntegrityError:
        with container.session_scope() as db:
            existing = db.get(WebhookEventRow, event.event_id)
            payload = existing.result if existing and existing.result else None
        if payload:
            return RelayWebhookResult.model_validate(payload)
        return RelayWebhookResult(
            status=WebhookResultStatus.DUPLICATE, event_id=event.event_id, detail="in progress"
        )
    return None


def _dispatch(container: "Container", event: RelayWebhookEvent) -> RelayWebhookResult:
    if event.event_type == RelayEventType.DELIVERY_STATUS:
        return _delivery_status(container, event)
    answer = event.choice or event.text
    if event.intervention_id and answer:
        resolution = workflows.resolve_intervention(
            container,
            intervention_id=event.intervention_id,
            choice=answer,
            notes=None,
            actor_kind=Actor.PATIENT,
            channel=Channel.RELAY,
            actor=ACTOR,
            expected_case_id=event.case_id,
        )
        return _processed(event, resolution.case_state, "intervention resolved")
    if event.dispute_id and event.choice:
        return _dispute_decision(container, event)
    with container.session_scope() as db:
        record_audit(
            db,
            actor=ACTOR,
            action="relay.patient_message",
            case_id=event.case_id,
            details={"event_type": event.event_type.value},
        )
    return RelayWebhookResult(
        status=WebhookResultStatus.IGNORED,
        event_id=event.event_id,
        detail="no pending question or decision matched this event",
    )


def _dispute_decision(container: "Container", event: RelayWebhookEvent) -> RelayWebhookResult:
    choice = (event.choice or "").strip().upper()
    if choice == "REVIEW":
        return RelayWebhookResult(
            status=WebhookResultStatus.IGNORED, event_id=event.event_id, detail="review requested"
        )
    with container.session_scope() as db:
        dispute = dispute_service.get_dispute_row(db, event.dispute_id or "")
        case = get_case_row(db, dispute.case_id)
        if case.id != event.case_id:
            return RelayWebhookResult(
                status=WebhookResultStatus.IGNORED, event_id=event.event_id, detail="case mismatch"
            )
        if choice == "APPROVE":
            dispute_service.approve(
                db,
                container,
                dispute_id=dispute.id,
                actor_kind=Actor.PATIENT,
                channel=Channel.RELAY,
                authorization_text=approval_authorization_text(
                    case.payer_name, dispute.total_disputed_cents
                ),
                authorize_call=True,
                actor=ACTOR,
            )
        elif choice == "REJECT":
            dispute_service.reject(
                db,
                container,
                dispute_id=dispute.id,
                actor_kind=Actor.PATIENT,
                channel=Channel.RELAY,
                reason=event.text,
                actor=ACTOR,
            )
        else:
            return RelayWebhookResult(
                status=WebhookResultStatus.IGNORED,
                event_id=event.event_id,
                detail=f"unknown dispute choice {choice}",
            )
        state = CaseState(case.state)
    return _processed(event, state, f"dispute {choice.lower()}")


def _delivery_status(container: "Container", event: RelayWebhookEvent) -> RelayWebhookResult:
    with container.session_scope() as db:
        row = db.get(NotificationRow, event.notification_id or "")
        if row is None or row.case_id != event.case_id:
            return RelayWebhookResult(
                status=WebhookResultStatus.IGNORED,
                event_id=event.event_id,
                detail="unknown notification",
            )
        row.delivered = (event.choice or "delivered").lower() == "delivered"
    return _processed(event, None, "delivery status recorded")


def _processed(
    event: RelayWebhookEvent, state: CaseState | None, detail: str
) -> RelayWebhookResult:
    return RelayWebhookResult(
        status=WebhookResultStatus.PROCESSED,
        event_id=event.event_id,
        detail=detail,
        case_state=state,
    )
