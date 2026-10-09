"""Put the demo patient back to the starting line (``make reset-demo``).

Run this between judges. It:

1. deletes every case for the demo patient (``fn_pat_demo_001``) and everything
   hanging off those cases (consents, visit, transcript, evidence, claim, findings,
   questions, dispute, call, notifications), then
2. re-seeds the same clean starting case as ``make seed`` (recorded + transcribed
   visit, NO health-data consent yet).

FinchNode is never touched: it is read-only clinical data and nothing in a demo run
changes it. All per-run state lives in Core's database, so that is all we reset.

The audit trail (``audit_events``), agent run log and webhook idempotency ledger are
deliberately kept (append-only by design, and they have no foreign keys to cases).

Safety: refuses to run when APP_ENV=prod, and only ever deletes rows for the
synthetic demo patient id from the demo scenario fixture.
"""

import sys
import warnings

warnings.filterwarnings("ignore", message="Using `httpx` with `starlette.testclient`")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import delete, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from claimshield_contracts.demo import demo_patient  # noqa: E402
from claimshield_contracts.enums import ServiceName  # noqa: E402

from ..config import get_settings  # noqa: E402
from ..demo_support import create_demo_visit, service_headers  # noqa: E402
from ..main import create_app  # noqa: E402
from ..models_db import (  # noqa: E402
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

# Children before parents, so foreign keys never block a delete.
_BY_CASE_ID = (
    LineAssessmentRow,
    FindingRow,
    DecisionRow,
    InterventionRow,
    NotificationRow,
    CallRow,
    DisputeRow,
)


def wipe_patient_cases(db: Session, finchnode_patient_id: str) -> int:
    """Delete all cases (and their child rows) for one patient. Returns the case count."""
    case_ids = list(
        db.scalars(select(CaseRow.id).where(CaseRow.finchnode_patient_id == finchnode_patient_id))
    )
    if not case_ids:
        return 0
    claim_ids = list(db.scalars(select(ClaimRow.id).where(ClaimRow.case_id.in_(case_ids))))
    visit_ids = list(db.scalars(select(VisitRow.id).where(VisitRow.case_id.in_(case_ids))))

    for model in _BY_CASE_ID:
        db.execute(delete(model).where(model.case_id.in_(case_ids)))
    if claim_ids:
        db.execute(delete(ClaimLineRow).where(ClaimLineRow.claim_id.in_(claim_ids)))
    db.execute(delete(ClaimRow).where(ClaimRow.case_id.in_(case_ids)))
    db.execute(delete(EvidenceRow).where(EvidenceRow.case_id.in_(case_ids)))
    if visit_ids:
        db.execute(delete(TranscriptRow).where(TranscriptRow.visit_id.in_(visit_ids)))
    db.execute(delete(VisitRow).where(VisitRow.case_id.in_(case_ids)))
    db.execute(delete(ConsentRow).where(ConsentRow.case_id.in_(case_ids)))
    db.execute(delete(CaseRow).where(CaseRow.id.in_(case_ids)))
    return len(case_ids)


def main() -> None:
    settings = get_settings()
    if settings.app_env == "prod":
        sys.exit("Refusing to reset demo data with APP_ENV=prod.")

    patient = demo_patient()
    with TestClient(create_app(settings)) as client:
        container = client.app.state.container
        with container.session_scope() as db:
            removed = wipe_patient_cases(db, patient.finchnode_patient_id)
        result = create_demo_visit(
            client, service_headers(settings, ServiceName.RELAY), data_consent=False
        )

    print(f"Demo reset for {patient.display_name} ({patient.finchnode_patient_id}):")
    print(f"  removed     {removed} old case(s)")
    print(f"  fresh case  {result['case_id']} ({result['case_state']})")
    print("Next: in ASI:One / Relay, send RESTART, then start the demo from the top.")


if __name__ == "__main__":
    main()
