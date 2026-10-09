"""Load the synthetic demo scenario into the configured database (``make seed``).

Creates the demo patient's case (``fn_pat_demo_001``) with a recorded and
transcribed visit (evidence extracted) and NO health-data consent yet, so both
demos start from the same place: ASI:One (or Relay) asks for consent, pulls the
claim from FinchNode, and analyzes it. Requests go through Core's own HTTP API
in-process, so seeded data is validated exactly like real client input.
"""

import warnings

warnings.filterwarnings("ignore", message="Using `httpx` with `starlette.testclient`")

from fastapi.testclient import TestClient  # noqa: E402

from claimshield_contracts.demo import demo_claim_id, demo_patient  # noqa: E402
from claimshield_contracts.enums import ServiceName  # noqa: E402

from ..config import get_settings  # noqa: E402
from ..demo_support import create_demo_visit, service_headers  # noqa: E402
from ..main import create_app  # noqa: E402


def main() -> None:
    settings = get_settings()
    with TestClient(create_app(settings)) as client:
        result = create_demo_visit(
            client, service_headers(settings, ServiceName.RELAY), data_consent=False
        )
    patient = demo_patient()
    print("Seeded synthetic demo data:")
    print(f"  case        {result['case_id']} ({result['case_state']})")
    print(f"  patient     {patient.display_name} / FinchNode id {patient.finchnode_patient_id}")
    print(f"  claim       {demo_claim_id()} (pulled from FinchNode after consent)")
    print("Next: `make chat` (offline ASI:One rehearsal) or `make relay-demo`.")


if __name__ == "__main__":
    main()
