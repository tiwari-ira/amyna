"""Drive FinchNode's sandbox for a production-like demo (sandbox key, synthetic patients).

FinchNode is read-only: sandbox patients come only from its named scenarios, and the
sandbox controls play what a hospital would do later (finchnode.com/docs/testing).

    # The patient tapped "Connect my health records" in Relay. Instead of the browser,
    # complete that session with a synthetic patient (or open the link and pick
    # "Northstar Health System (Synthetic)" -- no hospital login in the sandbox):
    python scripts/finchnode_sandbox.py simulate --latest --scenario baseline-adult

    # Later: the hospital uploads the visit's records -> FinchNode sends records.updated
    python scripts/finchnode_sandbox.py advance --latest
    python scripts/finchnode_sandbox.py revoke --latest        # patient stops sharing
    python scripts/finchnode_sandbox.py status --latest
    python scripts/finchnode_sandbox.py records --latest       # what Core will read

``--latest`` reads the newest Connect session from Core's database (same .env as
Core); pass ``--session cs_...`` / ``--subject u_...`` instead when running elsewhere.
Needs FINCHNODE_MODE=sandbox and FINCHNODE_API_KEY=ck_test_... in the environment/.env.
"""

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))

from app.config import get_settings  # noqa: E402
from finchnode import tools  # noqa: E402
from finchnode.client import HttpFinchNodeClient  # noqa: E402

EVENTS = {
    "advance": "records.advance",
    "revoke": "consent.revoke",
    "expire": "consent.expire",
    "fail": "source.fail",
}


def _client() -> HttpFinchNodeClient:
    settings = get_settings()
    if settings.finchnode_mode != "sandbox":
        sys.exit(f"FINCHNODE_MODE is {settings.finchnode_mode!r}; these controls need 'sandbox'.")
    return HttpFinchNodeClient(
        mode="sandbox",
        base_url=settings.finchnode_base_url,
        api_key=settings.finchnode_api_key,
        cache_ttl_s=0,
    )


def _latest() -> tuple[str, str | None]:
    """(session id, subject) of the newest Connect session in Core's database."""
    from sqlalchemy import select

    from app.db import make_engine, make_session_factory, session_scope
    from app.models_db import FinchConnectSessionRow

    factory = make_session_factory(make_engine(get_settings().resolved_database_url))
    with session_scope(factory) as db:
        row = db.scalars(
            select(FinchConnectSessionRow).order_by(FinchConnectSessionRow.created_at.desc())
        ).first()
        if row is None:
            sys.exit("No Connect session yet: have the patient tap Connect in Relay first.")
        return row.id, row.subject


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("command", choices=["simulate", "status", "records", *EVENTS])
    parser.add_argument("--session", help="Connect session id (cs_...)")
    parser.add_argument("--subject", help="FinchNode subject (u_...)")
    parser.add_argument("--latest", action="store_true", help="use Core's newest session")
    parser.add_argument("--scenario", default="baseline-adult")
    parser.add_argument("--source", help="one source of a two-source patient")
    args = parser.parse_args()

    session_id, subject = args.session, args.subject
    if args.latest:
        session_id, latest_subject = _latest()
        subject = subject or latest_subject
    client = _client()

    if args.command == "simulate":
        if not session_id:
            sys.exit("simulate needs --session cs_... or --latest")
        session = client.simulate(session_id, args.scenario)
        for _ in range(30):  # simulation finishes in seconds; FinchNode then sends consent.granted
            if session.get("simulation_state") in ("completed", "failed"):
                break
            time.sleep(2)
            session = client.get_connect_session(session_id)
        print(json.dumps(session, indent=2))
        print(
            "Core links the case on consent.granted, or when the patient taps "
            "'I'm done connecting' in Relay."
        )
        return
    if args.command == "status":
        if not session_id:
            sys.exit("status needs --session cs_... or --latest")
        print(json.dumps(client.get_connect_session(session_id), indent=2))
        return
    if not subject:
        sys.exit(f"{args.command} needs --subject u_... (or --latest after the session completed)")
    if args.command == "records":
        print("profile:", tools.get_patient_profile(client, subject))
        for name in ("encounters", "procedures", "medications"):
            records = getattr(tools, f"get_patient_{name}")(client, subject)
            print(
                f"{name}: {len(records)}",
                *(f"  {r.occurred_on} {r.display}" for r in records),
                sep="\n",
            )
        for claim in tools.get_patient_claims(client, subject):
            print(
                f"bill {claim.external_claim_id}: ${claim.total_billed_cents / 100:,.2f}, "
                f"{'itemized' if claim.lines else 'summary only'}"
            )
        print("incomplete:", tools.record_gaps(client, subject) or "none")
        return
    print(json.dumps(client.sandbox_event(subject, EVENTS[args.command], args.source), indent=2))


if __name__ == "__main__":
    main()
