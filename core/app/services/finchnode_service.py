"""FinchNode hosted Connect + FinchNode webhooks (sandbox / production).

Production patients never type an identifier. Core opens a Connect session for
the signed-in patient's case, the patient approves sharing on FinchNode's pages,
and FinchNode returns the patient's ``subject`` -- by the ``consent.granted``
webhook or, as a fallback without a public URL, when the session is read back.
Until then the case holds a placeholder id (``PENDING_CONNECT_PREFIX``) that is
never sent to FinchNode. (finchnode.com/docs/connect/after-the-patient-connects)

Webhooks (finchnode.com/docs/webhooks/receive-and-verify): verify
``FinchNode-Signature: t=<unix>,v1=<hex HMAC-SHA256(secret, "t." + raw body)>``,
require body ``id`` == ``FinchNode-Event-Id``, store the id once, answer 2xx
within 5 s and do the work afterwards. Only the event id and type are logged.
"""

import hashlib
import hmac
import json
import logging
import re
import time
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from claimshield_contracts.api import FinchNodeConnectSession
from claimshield_contracts.enums import (
    Actor,
    Channel,
    ConsentType,
    LinkStatus,
    NotificationKind,
)
from claimshield_contracts.models import PatientRef

from ..audit import record_audit
from ..db import utcnow
from ..errors import Conflict, NotFound
from ..models_db import (
    PENDING_CONNECT_PREFIX,
    CaseRow,
    FinchConnectSessionRow,
    FinchLinkRow,
    FinchNodeWebhookEventRow,
    UserRow,
)
from . import case_service, identity_service, notification_service
from .case_service import TERMINAL_STATES, get_case_row

if TYPE_CHECKING:
    from ..container import Container

log = logging.getLogger("claimshield.finchnode")

ACTOR = "finchnode:connect"
SIGNATURE_TOLERANCE_S = 300
_SIGNATURE = re.compile(r"^[a-f0-9]{64}$")


class FinchNodeSignatureError(Exception):
    """The webhook is not provably from FinchNode (answer 401)."""


# --------------------------------------------------------------------------- connect


def is_pending(case: CaseRow) -> bool:
    return case.finchnode_patient_id.startswith(PENDING_CONNECT_PREFIX)


def prepare_connect_case(
    db: Session,
    container: "Container",
    *,
    owner: UserRow | None,
    case_id: str | None,
    actor: str,
) -> str:
    """The case a Connect session links (a new one, or the patient's own existing case)."""
    if not container.finchnode.supports_connect:
        raise Conflict(
            f"FINCHNODE_MODE={container.finchnode.mode} links patients by typed id, not Connect.",
            code="connect_unavailable",
        )
    if case_id:
        case = get_case_row(db, case_id)
        identity_service.assert_case_access(case, owner)
        return case.id
    case = case_service.create_case(
        db,
        patient=PatientRef(finchnode_patient_id=PENDING_CONNECT_PREFIX),
        channel=Channel.RELAY,
        external_ref=None,
        actor=actor,
        owner_id=owner.id if owner else None,
    )
    case.finchnode_patient_id = f"{PENDING_CONNECT_PREFIX}{case.id}"
    return case.id


def _model(row: FinchConnectSessionRow) -> FinchNodeConnectSession:
    return FinchNodeConnectSession(
        session_id=row.id,
        case_id=row.case_id,
        url=row.url,
        status=row.status,
        connected=row.status == "completed",
        expires_at=row.expires_at,
    )


def open_connect_session(
    container: "Container", *, case_id: str, user_id: str | None, return_url: str | None
) -> FinchNodeConnectSession:
    """Create the FinchNode session (outside any DB transaction) and remember it."""
    with container.session_scope() as db:
        attempt = len(
            db.scalars(
                select(FinchConnectSessionRow.id).where(FinchConnectSessionRow.case_id == case_id)
            ).all()
        )
    settings = container.settings
    remote = container.finchnode.create_connect_session(
        # The case id is opaque: it comes back on the session and in consent.granted.
        external_id=case_id,
        categories=settings.connect_categories() or None,
        sync_mode=settings.finchnode_sync_mode or None,
        return_url=return_url or settings.finchnode_connect_return_url or None,
        idempotency_key=f"amyna-{case_id}-{attempt + 1}",
    )
    if not remote.get("id") or not remote.get("url"):
        raise Conflict("FinchNode did not return a Connect link.", code="connect_failed")
    with container.session_scope() as db:
        row = FinchConnectSessionRow(
            id=str(remote["id"]),
            case_id=case_id,
            user_id=user_id,
            status=str(remote.get("status") or "pending"),
            url=str(remote["url"]),
            environment=remote.get("environment"),
            expires_at=remote.get("expires_at"),
        )
        db.merge(row)
        record_audit(
            db,
            actor=ACTOR,
            action="finchnode.connect_started",
            case_id=case_id,
            resource_type="connect_session",
            resource_id=row.id,
        )
        if remote.get("status") == "completed" and remote.get("subject"):
            stored = db.get(FinchConnectSessionRow, row.id)
            complete_connect(db, container, stored, subject=str(remote["subject"]))
        return _model(db.get(FinchConnectSessionRow, row.id))


def refresh_connect_session(
    container: "Container", *, session_id: str, owner: UserRow | None
) -> FinchNodeConnectSession:
    """Read the session back from FinchNode (the no-webhook fallback) and finish linking."""
    with container.session_scope() as db:
        row = db.get(FinchConnectSessionRow, session_id)
        if row is None:
            raise NotFound(f"Connect session {session_id} not found", code="connect_not_found")
        identity_service.assert_case_access(get_case_row(db, row.case_id), owner)
        if row.status == "completed":
            return _model(row)
    remote = container.finchnode.get_connect_session(session_id)
    with container.session_scope() as db:
        row = db.get(FinchConnectSessionRow, session_id)
        if remote.get("status") == "completed" and remote.get("subject"):
            complete_connect(db, container, row, subject=str(remote["subject"]))
        else:
            row.status = str(remote.get("status") or row.status)
        return _model(row)


def complete_connect(
    db: Session,
    container: "Container",
    row: FinchConnectSessionRow,
    *,
    subject: str,
    receipt_id: str | None = None,
    categories: list[str] | None = None,
    missing: list[str] | None = None,
) -> bool:
    """Bind the case to the patient's FinchNode subject and record the share (idempotent)."""
    if row.status == "completed" and row.subject == subject:
        return False
    case = get_case_row(db, row.case_id)
    if not is_pending(case) and case.finchnode_patient_id != subject:
        raise Conflict(
            "This case is already linked to different health records.",
            code="connect_subject_mismatch",
        )
    case.finchnode_patient_id = subject
    row.status, row.subject, row.completed_at = "completed", subject, utcnow()
    shared = ", ".join(categories) if categories else "the requested categories"
    case_service.record_consent(
        db,
        container,
        case_id=case.id,
        consent_type=ConsentType.HEALTH_DATA_ACCESS,
        granted=True,
        consent_text=(
            "Shared health records with Amyna through FinchNode Connect "
            f"(receipt {receipt_id or row.id}; categories: {shared})."
        ),
        actor_kind=Actor.PATIENT,
        channel=Channel(case.channel),
        actor=ACTOR,
    )
    identity_service.link_finch(db, case=case, actor=ACTOR)
    note = f" I couldn't get: {', '.join(missing)}." if missing else ""
    notification_service.notify(
        db,
        container,
        case=case,
        kind=NotificationKind.STATUS_UPDATE,
        text=f"Your health records are connected.{note}",
        data={"event": "finchnode_connected", "missing": ",".join(missing or [])},
    )
    record_audit(
        db,
        actor=ACTOR,
        action="finchnode.connected",
        case_id=case.id,
        resource_type="connect_session",
        resource_id=row.id,
        details={"missing_categories": list(missing or [])},
    )
    return True


# --------------------------------------------------------------------------- webhooks


def verify_finchnode_signature(
    secret: str,
    raw_body: bytes,
    signature_header: str | None,
    event_id_header: str | None,
    *,
    now: float | None = None,
) -> dict[str, Any]:
    """The verified event, or FinchNodeSignatureError (steps 2-5 of FinchNode's guide)."""
    if not secret:
        raise FinchNodeSignatureError("FINCHNODE_WEBHOOK_SECRET is not configured")
    pairs = [part.strip().partition("=") for part in (signature_header or "").split(",")]
    times = [value for key, _, value in pairs if key == "t"]
    signatures = [value for key, _, value in pairs if key == "v1"]
    if len(times) != 1 or not re.fullmatch(r"[0-9]{1,12}", times[0]):
        raise FinchNodeSignatureError("missing or malformed timestamp")
    if not signatures or not all(_SIGNATURE.fullmatch(s) for s in signatures):
        raise FinchNodeSignatureError("missing or malformed signature")
    current = time.time() if now is None else now
    if abs(current - int(times[0])) > SIGNATURE_TOLERANCE_S:
        raise FinchNodeSignatureError("timestamp outside tolerance (possible replay)")
    expected = hmac.new(secret.encode(), times[0].encode() + b"." + raw_body, hashlib.sha256)
    if not any(hmac.compare_digest(expected.hexdigest(), s) for s in signatures):
        raise FinchNodeSignatureError("signature mismatch")
    try:
        event = json.loads(raw_body)
    except ValueError as exc:
        raise FinchNodeSignatureError("body is not JSON") from exc
    if not isinstance(event, dict) or not event.get("id") or event["id"] != event_id_header:
        raise FinchNodeSignatureError("event id does not match FinchNode-Event-Id")
    return event


def record_webhook_event(container: "Container", event: dict[str, Any]) -> bool:
    """Store the event id once. False = already received (FinchNode retries for ~41 h)."""
    try:
        with container.session_scope() as db:
            db.add(
                FinchNodeWebhookEventRow(
                    event_id=str(event["id"]), event_type=str(event.get("type") or ""), result={}
                )
            )
    except IntegrityError:
        return False
    return True


def _latest_case(db: Session, subject: str) -> CaseRow | None:
    """The patient's most recent case: an open one first, else the newest."""
    cases = db.scalars(
        select(CaseRow)
        .where(CaseRow.finchnode_patient_id == subject)
        .order_by(CaseRow.created_at.desc())
    ).all()
    terminal = {s.value for s in TERMINAL_STATES}
    return next((c for c in cases if c.state not in terminal), cases[0] if cases else None)


def _on_consent_granted(db: Session, container: "Container", data: dict[str, Any]) -> str:
    row = db.get(FinchConnectSessionRow, str(data.get("sessionId") or ""))
    if row is None or not data.get("subject"):
        return "ignored: unknown session"
    complete_connect(
        db,
        container,
        row,
        subject=str(data["subject"]),
        receipt_id=data.get("receiptId"),
        categories=data.get("categories"),
        missing=data.get("missingCategories"),
    )
    return "connected"


def _on_records_updated(db: Session, container: "Container", subject: str) -> str:
    case = _latest_case(db, subject)
    if case is None:
        return "ignored: no case"
    notification_service.notify(
        db,
        container,
        case=case,
        kind=NotificationKind.STATUS_UPDATE,
        text=(
            "New records from your health system just arrived. When the bill for that "
            "visit comes, tap Review my bill and I'll check it against them."
        ),
        data={"event": "finchnode_records_updated", "state": case.state},
    )
    return "notified"


def _on_sharing_ended(db: Session, container: "Container", subject: str, event_type: str) -> str:
    for link in db.scalars(
        select(FinchLinkRow).where(
            FinchLinkRow.finchnode_patient_id == subject,
            FinchLinkRow.status == LinkStatus.LINKED.value,
        )
    ):
        link.status, link.revoked_at = LinkStatus.REVOKED.value, utcnow()
    terminal = {s.value for s in TERMINAL_STATES}
    cases = db.scalars(select(CaseRow).where(CaseRow.finchnode_patient_id == subject)).all()
    for case in cases:
        if case_service.has_consent(db, case.id, ConsentType.HEALTH_DATA_ACCESS):
            case_service.record_consent(
                db,
                container,
                case_id=case.id,
                consent_type=ConsentType.HEALTH_DATA_ACCESS,
                granted=False,
                consent_text=f"FinchNode reported {event_type}: the patient's sharing ended.",
                actor_kind=Actor.PATIENT,
                channel=Channel(case.channel),
                actor=ACTOR,
            )
    latest = next((c for c in cases if c.state not in terminal), None)
    if latest is not None:
        notification_service.notify(
            db,
            container,
            case=latest,
            kind=NotificationKind.STATUS_UPDATE,
            text=(
                "Your health-record sharing with Amyna has ended, so I can't read your "
                'records anymore. Send "restart" to connect them again.'
            ),
            data={"event": "finchnode_sharing_ended", "state": latest.state},
        )
    return "sharing ended"


def process_webhook_event(container: "Container", event: dict[str, Any]) -> str:
    """Apply one verified, first-time FinchNode event. Never raises."""
    event_type = str(event.get("type") or "")
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    subject = str(data.get("subject") or "")
    try:
        if subject:
            container.finchnode.invalidate(subject)
        with container.session_scope() as db:
            if event_type == "consent.granted":
                outcome = _on_consent_granted(db, container, data)
            elif event_type == "records.updated" and subject:
                outcome = _on_records_updated(db, container, subject)
            elif event_type in ("consent.revoked", "consent.expired") and subject:
                outcome = _on_sharing_ended(db, container, subject, event_type)
            elif event_type == "deletion.requested":
                log.warning("FinchNode deletion requested", extra={"fields": {"id": event["id"]}})
                outcome = "deletion requested: operator action needed"
            else:
                outcome = "recorded"
            record_audit(
                db,
                actor="finchnode",
                action=f"finchnode.{event_type or 'event'}",
                resource_type="finchnode_event",
                resource_id=str(event["id"]),
                details={"outcome": outcome},
            )
    except Exception as exc:  # never lose the event: keep the outcome for operators
        log.exception("FinchNode event failed", extra={"fields": {"id": event.get("id")}})
        outcome = f"failed: {type(exc).__name__}"
    with container.session_scope() as db:
        row = db.get(FinchNodeWebhookEventRow, str(event["id"]))
        if row is not None:
            row.result = {"outcome": outcome}
    log.info("FinchNode event", extra={"fields": {"id": event["id"], "type": event_type}})
    return outcome


__all__ = [
    "FinchNodeSignatureError",
    "complete_connect",
    "open_connect_session",
    "prepare_connect_case",
    "process_webhook_event",
    "record_webhook_event",
    "refresh_connect_session",
    "verify_finchnode_signature",
]
