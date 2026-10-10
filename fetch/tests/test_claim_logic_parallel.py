"""Task 11: the claim pull runs independent record sources CONCURRENTLY.

Deterministic proof: every source getter blocks on a 3-party barrier, so the
pull only completes if the three run in parallel threads. A failing source
never loses the successful ones -- and never lets the analysis record findings
from a partial pull (a missing source would read as "not in your records").
"""

import threading
from datetime import UTC, datetime

from claimshield_contracts.agent_messages import ClaimAnalysisRequest
from claimshield_contracts.enums import CaseState, Channel, ClaimSource, CodeSystem, RecordType
from claimshield_contracts.models import (
    CaseDetail,
    Claim,
    ClaimLine,
    CodeRef,
    HealthRecord,
    PatientRef,
)
from claimshield_contracts.tool_api import (
    CoreToolError,
    PatientDataRequest,
    PatientRecords,
)
from fetch.agents.logic.claim_logic import (
    RECORDS_UNAVAILABLE,
    ClaimLogicDeps,
    _pull_patient_records,
    handle_claim_analysis_request,
)


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
                code=CodeRef(system=CodeSystem.CPT, code="70553"),
                description="MRI brain",
                date_of_service=datetime(2026, 9, 15, tzinfo=UTC).date(),
                billed_cents=84260,
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

    def __init__(self, fail: set[str] | None = None, incomplete: set[str] | None = None) -> None:
        self.fail = fail or set()
        self.incomplete = incomplete or set()
        self.barrier = threading.Barrier(3)
        self.started: list[str] = []
        self.recorded: list[object] = []

    def _pull(self, name: str) -> PatientRecords:
        self.started.append(name)
        self.barrier.wait(timeout=5)  # deadlocks forever if sequential
        if name in self.fail:
            raise CoreToolError(502, "upstream_error", f"{name} down")
        gap = "sync_partial" if name in self.incomplete else None
        return PatientRecords(
            records=[_record(name, i) for i in range(2)], complete=gap is None, gap=gap
        )

    def get_patient_encounters(self, req: PatientDataRequest) -> PatientRecords:
        return self._pull("encounters")

    def get_patient_procedures(self, req: PatientDataRequest) -> PatientRecords:
        return self._pull("procedures")

    def get_patient_medications(self, req: PatientDataRequest) -> PatientRecords:
        return self._pull("medications")

    def get_case(self, req):  # type: ignore[no-untyped-def]
        return _case()

    def record_findings(self, req):  # type: ignore[no-untyped-def]
        self.recorded.append(req)
        raise CoreToolError(409, "stop", "recorded")  # the analysis ran; stop here

    # Unused members of the protocol
    def get_patient_context(self, req):  # type: ignore[no-untyped-def]
        raise CoreToolError(403, "forbidden", "")


def test_record_pulls_run_in_parallel() -> None:
    tools = _BarrierTools()
    records, failed, gaps = _pull_patient_records(
        tools,
        PatientDataRequest(case_id="case_1"),  # type: ignore[arg-type]
    )
    assert failed == [] and gaps == {}
    assert len(records) == 6
    assert sorted(tools.started) == ["encounters", "medications", "procedures"]


def test_a_failing_source_never_loses_the_others() -> None:
    tools = _BarrierTools(fail={"procedures"})
    records, failed, _ = _pull_patient_records(
        tools,
        PatientDataRequest(case_id="case_1"),  # type: ignore[arg-type]
    )
    assert failed == ["procedures"]
    assert len(records) == 4  # the two healthy sources still come back
    sources = {r.source_id.rsplit("_", 1)[0] for r in records}
    assert "finch_procedures" not in sources


def _analyze(tools: _BarrierTools):  # type: ignore[no-untyped-def]
    request = ClaimAnalysisRequest(request_id="req_1", case_id="case_1", claim_id="clm_1")
    return handle_claim_analysis_request(
        tools,  # type: ignore[arg-type]
        request,
        ClaimLogicDeps(use_price_index=False),
    )


def test_a_failed_source_postpones_the_analysis_and_records_nothing() -> None:
    tools = _BarrierTools(fail={"medications"})
    response = _analyze(tools)
    assert not response.ok and response.error.startswith(RECORDS_UNAVAILABLE)
    assert "medications" in response.error
    assert tools.recorded == []  # nothing stored; a later retry analyzes everything


def test_incomplete_sources_are_reported_and_still_analyzed() -> None:
    tools = _BarrierTools(incomplete={"procedures"})
    _, failed, gaps = _pull_patient_records(
        tools,
        PatientDataRequest(case_id="case_1"),  # type: ignore[arg-type]
    )
    assert failed == [] and gaps == {"procedures": "sync_partial"}
    analyzed = _BarrierTools(incomplete={"procedures"})
    _analyze(analyzed)
    assert len(analyzed.recorded) == 1  # analyzed with what came back (no absence claims)
