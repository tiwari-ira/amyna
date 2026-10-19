"""NOTIFY_MODE=relay_service: Core delivers patient notifications only after the
transaction commits, on a background worker -- never inside the request.

Regression test for the Relay deadlock: Relay handles a patient tap while holding
that patient's queue and waits on Core; Core used to POST the next notification
to Relay inside the same request and stall 5 s per notification (httpx timeout).
"""

import threading
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.adapters.relay.base import DeliveryResult, PatientNotifier
from app.demo_support import build_isolated_app, create_demo_visit, service_headers
from app.models_db import CaseRow, NotificationRow
from app.services import notification_service
from claimshield_contracts.enums import NotificationKind, ServiceName

CLAIM = {"source": "finchnode", "finchnode_claim_id": "CLM-2026-0915-001"}


class _RecordingNotifier(PatientNotifier):
    mode = "relay_service"
    deferred = True

    def __init__(self, container: Any, *, delivered: bool = True) -> None:
        self.container = container
        self.delivered = delivered
        self.sent: list[tuple[str, bool, str]] = []

    def send(self, notification: Any, case: Any) -> DeliveryResult:
        with self.container.session_scope() as db:  # a new session sees only committed rows
            committed = db.get(NotificationRow, notification.notification_id) is not None
        self.sent.append((notification.kind.value, committed, threading.current_thread().name))
        return DeliveryResult(delivered=self.delivered, ref="relay:test")


@pytest.fixture
def deferred_app(tmp_path: Path) -> Any:
    app = build_isolated_app(tmp_path / "deferred.db")
    app.state.container.notifier = _RecordingNotifier(app.state.container)
    return app


def test_notifications_are_delivered_after_commit_off_the_request_thread(
    deferred_app: Any,
) -> None:
    container = deferred_app.state.container
    client = TestClient(deferred_app)
    relay = service_headers(container.settings, ServiceName.RELAY)
    setup = create_demo_visit(client, relay)
    claim = client.post("/claims", json={"case_id": setup["case_id"], **CLAIM}, headers=relay)
    client.post(f"/claims/{claim.json()['claim_id']}/analyze", headers=relay)
    container.notification_dispatcher.drain()

    sent = container.notifier.sent
    assert sent and sent[0][0] == NotificationKind.CONFIRMATION_QUESTION.value
    assert all(committed for _, committed, _ in sent)
    assert all(thread.startswith("amyna-notify") for _, _, thread in sent)
    rows = client.get(f"/cases/{setup['case_id']}/notifications", headers=relay).json()
    assert rows and all(row["delivered"] and row["delivery_ref"] == "relay:test" for row in rows)


def test_a_rolled_back_change_never_notifies_the_patient(deferred_app: Any) -> None:
    container = deferred_app.state.container
    client = TestClient(deferred_app)
    relay = service_headers(container.settings, ServiceName.RELAY)
    case_id = create_demo_visit(client, relay)["case_id"]
    container.notification_dispatcher.drain()
    before = len(container.notifier.sent)

    with pytest.raises(RuntimeError), container.session_scope() as db:
        notification_service.notify_status(db, container, db.get(CaseRow, case_id), "never sent")
        raise RuntimeError("roll back")
    container.notification_dispatcher.drain()
    assert len(container.notifier.sent) == before


def test_a_failed_delivery_is_recorded(tmp_path: Path) -> None:
    app = build_isolated_app(tmp_path / "failed.db")
    container = app.state.container
    container.notifier = _RecordingNotifier(container, delivered=False)
    client = TestClient(app)
    relay = service_headers(container.settings, ServiceName.RELAY)
    case_id = create_demo_visit(client, relay)["case_id"]
    with container.session_scope() as db:
        notification_service.notify_status(db, container, db.get(CaseRow, case_id), "hello")
    container.notification_dispatcher.drain()
    rows = client.get(f"/cases/{case_id}/notifications", headers=relay).json()
    assert rows[-1]["text"] == "hello" and rows[-1]["delivered"] is False


def test_sharded_delivery_keeps_per_case_order_across_cases(tmp_path: Path) -> None:
    """[3.1] Different cases deliver in parallel; one case's order is strict."""
    import time as _time

    app = build_isolated_app(tmp_path / "sharded.db")
    container = app.state.container
    notifier = _RecordingNotifier(container)
    container.notifier = notifier
    order: dict[str, list[str]] = {"case_a": [], "case_b": []}

    def slow_send(notification: Any, case: Any) -> DeliveryResult:
        _time.sleep(0.05)
        order[case.case_id if hasattr(case, "case_id") else "case_a"].append(
            notification.notification_id
        )
        return DeliveryResult(delivered=True)

    notifier.send = slow_send  # type: ignore[method-assign]
    dispatcher = container.notification_dispatcher
    for i in range(6):
        dispatcher.submit(
            lambda n=f"ntf_a{i}": slow_send(_StubNotification(n), _StubCase("case_a")),
            case_id="case_a",
        )
        dispatcher.submit(
            lambda n=f"ntf_b{i}": slow_send(_StubNotification(n), _StubCase("case_b")),
            case_id="case_b",
        )
    dispatcher.drain(timeout=30)
    assert order["case_a"] == [f"ntf_a{i}" for i in range(6)]  # strict FIFO per case
    assert order["case_b"] == [f"ntf_b{i}" for i in range(6)]


class _StubNotification:
    def __init__(self, notification_id: str) -> None:
        self.notification_id = notification_id


class _StubCase:
    def __init__(self, case_id: str) -> None:
        self.case_id = case_id
