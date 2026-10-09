"""Task 11: the claim pull runs independent record sources CONCURRENTLY.

Deterministic proof: every source getter blocks on a 3-party barrier, so the
pull only completes if the three run in parallel threads. A failing source
never loses the successful ones.
"""

import threading
from datetime import UTC, datetime

from claimshield_contracts.enums import CaseState, Channel, ClaimSource, RecordType
from claimshield_contracts.models import (
    CaseDetail,
    Claim,
    ClaimLine,
    HealthRecord,
    PatientRef,
)
from claimshield_contracts.tool_api import (
    CoreToolError,
    PatientDataRequest,
    PatientRecords,
)
from fetch.agents.logic.claim_logic import _pull_patient_records


def _case() -> CaseDetail:
    now = datetime.now(UTC)
    claim = Claim(
        claim_id="clm_1",
        case_id="case_1",
        external_claim_id="CLM-2026-0915-001",
        payer_name="Acme Health Plan",
        provider_name="Memorial Hospital",
        date_of_service=datetime(2026, 9, 15, tzinfo=UTC).date(),
        total_billed_cents=84260,
        source=ClaimSource.FINCHNODE,
        lines=[
            ClaimLine(
                line_id="lin_1",
                claim_id="clm_1",
                line_number=1,
                description="MRI brain",
                amount_cents=84260,
            )
        ],
    )
    return CaseDetail(
        case_id="case_1",
        state=CaseState.CLAIM_RECEIVED,
        channel=Channel.RELAY,
        patient=PatientRef(finchnode_patient_id="fn_pat_demo_001"),
        created_at=now,
        updated_at=now,
        claim=claim,
    )


def _record(name: str, i: int) -> HealthRecord:
    return HealthRecord(
        record_id=f"rec_{name}_{i}",
        record_type=RecordType.PROCEDURE,
        patient_id="fn_pat_demo_001",
        display=f"{name} {i}",
        source_resource_type="Procedure",
        source_id=f"finch_{name}_{i}",
    )


class _BarrierTools:
    """Each patient pull blocks until all three are in flight (concurrency proof)."""

    def __init__(self, fail: set[str] | None = None) -> None:
        self.fail = fail or set()
        self.barrier = threading.Barrier(3)
        self.started: list[str] = []

    def _pull(self, name: str) -> PatientRecords:
        self.started.append(name)
        self.barrier.wait(timeout=5)  # deadlocks forever if sequential
        if name in self.fail:
            raise CoreToolError(502, "upstream_error", f"{name} down")
        return PatientRecords(records=[_record(name, i) for i in range(2)])

    def get_patient_encounters(self, req: PatientDataRequest) -> PatientRecords:
        return self._pull("encounters")

    def get_patient_procedures(self, req: PatientDataRequest) -> PatientRecords:
        return self._pull("procedures")

    def get_patient_medications(self, req: PatientDataRequest) -> PatientRecords:
        return self._pull("medications")

    # Unused members of the protocol
    def get_patient_context(self, req):  # type: ignore[no-untyped-def]
        raise CoreToolError(403, "forbidden", "")


def test_record_pulls_run_in_parallel() -> None:
    tools = _BarrierTools()
    records, failed = _pull_patient_records(
        tools,
        PatientDataRequest(case_id="case_1"),  # type: ignore[arg-type]
    )
    assert failed == []
    assert len(records) == 6
    assert sorted(tools.started) == ["encounters", "medications", "procedures"]


def test_a_failing_source_never_loses_the_others() -> None:
    tools = _BarrierTools(fail={"procedures"})
    records, failed = _pull_patient_records(
        tools,
        PatientDataRequest(case_id="case_1"),  # type: ignore[arg-type]
    )
    assert failed == ["procedures"]
    assert len(records) == 4  # the two healthy sources still come back
    sources = {r.source_id.rsplit("_", 1)[0] for r in records}
    assert "finch_procedures" not in sources
