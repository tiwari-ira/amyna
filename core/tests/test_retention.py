"""[3.6] Retention: ledgers are pruned on a schedule; the audit trail never is."""

from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.audit import record_audit
from app.db import utcnow
from app.demo_support import create_demo_visit, service_headers
from app.models_db import AuditRow, FinchNodeWebhookEventRow, NotificationRow, WebhookEventRow
from app.services import job_service
from claimshield_contracts.enums import ServiceName


def _case_id(client: TestClient) -> str:
    relay = service_headers(client.app.state.container.settings, ServiceName.RELAY)
    return create_demo_visit(client, relay)["case_id"]


def _seed(client: TestClient) -> None:
    """Old rows (one per ledger) plus a fresh one; the audit trail stays forever."""
    container = client.app.state.container
    old = utcnow() - timedelta(days=120)
    case_id = _case_id(client)
    with container.session_scope() as db:
        db.add(WebhookEventRow(event_id="evt_old", event_type="x", result={}, received_at=old))
        db.add(
            FinchNodeWebhookEventRow(event_id="fn_old", event_type="x", result={}, received_at=old)
        )
        db.add(
            NotificationRow(
                id="ntf_old",
                case_id=case_id,
                kind="status_update",
                text="old",
                buttons=[],
                data={},
                delivered=True,
                created_at=old,
            )
        )
        db.add(WebhookEventRow(event_id="evt_new", event_type="x", result={}))
        record_audit(db, actor="t", action="old.event", case_id=case_id)


def test_retention_sweep_prunes_old_ledgers_but_never_audit(client: TestClient) -> None:
    _seed(client)
    container = client.app.state.container
    removed = job_service.run_retention_sweep(container, force=True)
    assert removed == {
        "webhook_events": 1,
        "finchnode_webhook_events": 1,
        "delivered_notifications": 1,
    }
    with container.session_scope() as db:
        assert db.get(WebhookEventRow, "evt_old") is None
        assert db.get(WebhookEventRow, "evt_new") is not None  # inside the window
        assert db.get(FinchNodeWebhookEventRow, "fn_old") is None
        assert db.get(NotificationRow, "ntf_old") is None
        assert db.scalars(select(AuditRow.id).limit(1)).first() is not None


def test_undelivered_notifications_are_kept(client: TestClient) -> None:
    """A notification the patient never saw stays in the outbox (retry surface)."""
    container = client.app.state.container
    old = utcnow() - timedelta(days=120)
    with container.session_scope() as db:
        db.add(
            NotificationRow(
                id="ntf_pending",
                case_id=_case_id(client),
                kind="status_update",
                text="pending",
                buttons=[],
                data={},
                delivered=False,
                created_at=old,
            )
        )
    removed = job_service.run_retention_sweep(container, force=True)
    assert removed["delivered_notifications"] == 0
    with container.session_scope() as db:
        assert db.get(NotificationRow, "ntf_pending") is not None


def test_the_sweep_is_time_gated(client: TestClient) -> None:
    container = client.app.state.container
    assert job_service.run_retention_sweep(container) == {}  # too soon after the last
