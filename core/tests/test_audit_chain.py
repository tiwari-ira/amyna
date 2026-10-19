"""[3.2] Tamper-evident audit chain + the notifications ``since`` cursor."""

from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app.audit import verify_audit_chain
from app.models_db import AuditRow


def _audited_case(client: TestClient, relay: dict[str, str]) -> str:
    from app.demo_support import create_demo_visit

    return create_demo_visit(client, relay)["case_id"]


def test_the_audit_chain_verifies_after_a_full_flow(
    client: TestClient, relay: dict[str, str]
) -> None:
    case_id = _audited_case(client, relay)
    container = client.app.state.container
    with container.session_scope() as db:
        assert verify_audit_chain(db, case_id)
        events = len(list(db.scalars(select(AuditRow).where(AuditRow.case_id == case_id))))
    assert events > 3  # created/consented/transitioned, not just one row


def _tampered(client: TestClient, relay: dict[str, str], mode: str) -> Any:
    case_id = _audited_case(client, relay)
    container = client.app.state.container
    with container.session_scope() as db:
        assert verify_audit_chain(db, case_id)
        first = db.scalars(
            select(AuditRow).where(AuditRow.case_id == case_id).order_by(AuditRow.at).limit(2)
        ).all()[1]
    with container.session_scope() as db:
        if mode == "edit":
            db.execute(
                update(AuditRow)
                .where(AuditRow.id == first.id)
                .values(details={"smuggled": "edited-after-the-fact"})
            )
        else:
            db.delete(db.get(AuditRow, first.id))
    with container.session_scope() as db:
        return verify_audit_chain(db, case_id)


def test_an_edited_audit_row_breaks_the_chain(client: TestClient, relay: dict[str, str]) -> None:
    assert _tampered(client, relay, "edit") is False


def test_a_deleted_audit_row_breaks_the_chain(client: TestClient, relay: dict[str, str]) -> None:
    assert _tampered(client, relay, "delete") is False


def test_notifications_since_cursor_returns_only_the_delta(
    client: TestClient, relay: dict[str, str]
) -> None:
    case_id = _audited_case(client, relay)
    # Analyze the claim: the confirmation question is the first real notification
    # (the visit/consent steps alone create none).
    claim = client.post(
        "/claims",
        json={"case_id": case_id, "source": "finchnode", "finchnode_claim_id": "CLM-2026-0915-001"},
        headers=relay,
    ).json()
    client.post(f"/claims/{claim['claim_id']}/analyze", headers=relay)
    notes = client.get(f"/cases/{case_id}/notifications", headers=relay).json()
    assert notes, "analysis produced the first notification"
    # Add one more notification after the cursor moment.
    from app.db import utcnow
    from app.models_db import NotificationRow

    container = client.app.state.container
    with container.session_scope() as db:
        db.add(
            NotificationRow(
                id="ntf_cursor_probe",
                case_id=case_id,
                kind="status_update",
                text="later",
                buttons=[],
                data={},
                created_at=utcnow(),
            )
        )
    delta = client.get(
        f"/cases/{case_id}/notifications",
        params={"since": notes[-1]["created_at"]},
        headers=relay,
    ).json()
    assert [n["notification_id"] for n in delta] == ["ntf_cursor_probe"]

    bad = client.get(
        f"/cases/{case_id}/notifications", params={"since": "not-a-date"}, headers=relay
    )
    assert bad.status_code == 422
    assert bad.json()["detail"]["code"] == "bad_cursor"
