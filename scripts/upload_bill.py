"""Attach an itemized bill to a patient's case (demo operator tool).

FinchNode only has bill *summaries* (no line items), so in production the itemized
bill comes from the patient. Until photo/PDF intake exists, add the (made-up) bill
for them; then the patient taps "Review my bill" in Relay.

    python scripts/upload_bill.py --handle <their relay handle> --latest \\
        --bill scripts/examples/baseline_adult_bill.json

``--latest`` picks that patient's newest case from Core's database; or pass
``--case case_...``. Uses CORE_BASE_URL and RELAY_SERVICE_TOKEN (dev default otherwise).
"""

import argparse
import json
import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))

from app.config import get_settings  # noqa: E402
from claimshield_contracts.api import ClaimUpload  # noqa: E402
from claimshield_contracts.enums import ServiceName  # noqa: E402


def _latest_case(subject: str) -> str:
    from sqlalchemy import select

    from app.db import make_engine, make_session_factory, session_scope
    from app.models_db import CaseRow, UserRow

    factory = make_session_factory(make_engine(get_settings().resolved_database_url))
    with session_scope(factory) as db:
        user = db.scalar(select(UserRow).where(UserRow.auth_subject == subject))
        if user is None:
            sys.exit(f"No Amyna account for {subject} yet: have them text Amyna first.")
        case = db.scalars(
            select(CaseRow).where(CaseRow.user_id == user.id).order_by(CaseRow.created_at.desc())
        ).first()
        if case is None:
            sys.exit(f"{subject} has no case yet.")
        return case.id


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--bill", required=True, type=Path, help="ClaimUpload JSON file")
    parser.add_argument("--handle", help="the patient's Relay handle (their case is owned)")
    parser.add_argument("--case", help="case id (case_...)")
    parser.add_argument("--latest", action="store_true", help="the patient's newest case")
    args = parser.parse_args()

    settings = get_settings()
    base = os.environ.get("CORE_BASE_URL", settings.core_base_url).rstrip("/")
    token = settings.service_tokens()[ServiceName.RELAY]
    headers = {"Authorization": f"Bearer {token}"}
    subject = f"relay:{args.handle}" if args.handle else None
    case_id = args.case or (_latest_case(subject) if args.latest and subject else None)
    if not case_id:
        sys.exit("Pass --case case_..., or --handle <relay handle> --latest")

    raw = json.loads(args.bill.read_text())
    bill = ClaimUpload.model_validate({k: v for k, v in raw.items() if not k.startswith("_")})
    with httpx.Client(base_url=base, headers=headers, timeout=30) as http:
        if subject:  # the case belongs to the patient: act with their session
            session = http.post("/auth/session", json={"subject": subject})
            session.raise_for_status()
            http.headers["X-Amyna-Patient-Token"] = session.json()["patient_token"]
        response = http.post(
            "/claims",
            json={"case_id": case_id, "source": "upload", "claim": bill.model_dump(mode="json")},
        )
    if response.status_code >= 400:
        sys.exit(f"Core refused the bill: {response.status_code} {response.text[:300]}")
    claim = response.json()
    total = claim["total_billed_cents"] / 100
    print(
        f"Attached {claim['external_claim_id']} ({len(claim['lines'])} lines, ${total:,.2f}) "
        f"to {case_id}. Ask the patient to tap Review my bill."
    )


if __name__ == "__main__":
    main()
