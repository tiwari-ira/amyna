"""FETCH_MODE=local_direct: run fetch/agents/logic in-process (decision D4).

The logic talks to Core through ``InProcessTools`` -- the same ToolService,
scopes and audit trail as the HTTPS path, one transaction per tool call.
"""

from collections.abc import Callable
from typing import TYPE_CHECKING, TypeVar

from claimshield_contracts.agent_messages import (
    DISPUTE_ACTION_PREPARE,
    ClaimAnalysisRequest,
    DisputeRequest,
    VisitEvidenceRequest,
)
from claimshield_contracts.enums import AgentName, AgentRunStatus, ServiceName, WorkStatus
from claimshield_contracts.ids import new_id

from ...auth import identity_for
from ...db import utcnow
from ...errors import UpstreamError
from ...models_db import AgentRunRow
from .base import BridgeResult, FetchBridge

if TYPE_CHECKING:
    from ...container import Container

T = TypeVar("T")


class LocalDirectFetchBridge(FetchBridge):
    mode = "local_direct"

    def __init__(self, container: "Container") -> None:
        self._c = container

    def _tools(self):  # type: ignore[no-untyped-def]
        from ...services.inprocess_tools import InProcessTools

        return InProcessTools(self._c, identity_for(ServiceName.FETCH))

    def extract_visit_evidence(self, case_id: str, visit_id: str) -> BridgeResult:
        from fetch.agents.logic.visit_logic import VisitLogicDeps, handle_visit_evidence_request
        from intelligence.extraction.extractor import build_extractor

        settings = self._c.settings
        deps = VisitLogicDeps(
            extractor=build_extractor(
                settings.extraction_mode,
                ollama_url=settings.ollama_url,
                ollama_model=settings.ollama_model,
            )
        )
        request = VisitEvidenceRequest(
            request_id=new_id("request"), case_id=case_id, visit_id=visit_id
        )
        response = self._tracked(
            AgentName.VISIT_AGENT,
            case_id,
            request.request_id,
            lambda: handle_visit_evidence_request(self._tools(), request, deps),
        )
        return self._result(response.ok, response.error, "Visit")

    def analyze_claim(self, case_id: str, claim_id: str) -> BridgeResult:
        from fetch.agents.logic.claim_logic import ClaimLogicDeps, handle_claim_analysis_request
        from intelligence.discrepancy.engine import EngineConfig

        deps = ClaimLogicDeps(
            config=EngineConfig(confirmation_threshold=self._c.settings.confirmation_threshold)
        )
        request = ClaimAnalysisRequest(
            request_id=new_id("request"), case_id=case_id, claim_id=claim_id
        )
        response = self._tracked(
            AgentName.CLAIM_AGENT,
            case_id,
            request.request_id,
            lambda: handle_claim_analysis_request(self._tools(), request, deps),
        )
        return self._result(response.ok, response.error, "Claim")

    def prepare_dispute(self, case_id: str) -> BridgeResult:
        from fetch.agents.logic.dispute_logic import handle_dispute_request

        request = DisputeRequest(
            request_id=new_id("request"), case_id=case_id, action=DISPUTE_ACTION_PREPARE
        )
        response = self._tracked(
            AgentName.DISPUTE_AGENT,
            case_id,
            request.request_id,
            lambda: handle_dispute_request(self._tools(), request),
        )
        return self._result(response.ok, response.error, "Dispute")

    # ------------------------------------------------------------------ internals

    @staticmethod
    def _result(ok: bool, error: str | None, agent: str) -> BridgeResult:
        if not ok:
            if (error or "").startswith("records_unavailable"):
                # FinchNode could not provide every record type; nothing was recorded.
                raise UpstreamError(
                    "Some of the patient's FinchNode records could not be loaded; try again.",
                    code="finchnode_records_unavailable",
                )
            raise UpstreamError(f"{agent} Agent logic failed: {error}", code="agent_failed")
        return BridgeResult(status=WorkStatus.COMPLETED)

    def _tracked(self, agent: AgentName, case_id: str, request_id: str, run: Callable[[], T]) -> T:
        """Record an AgentRun around an in-process logic execution."""
        run_id = new_id("agent_run")
        with self._c.session_scope() as db:
            db.add(
                AgentRunRow(
                    id=run_id,
                    case_id=case_id,
                    agent=agent.value,
                    request_type=f"{agent.value}.local_direct",
                    request_id=request_id,
                    status=AgentRunStatus.RUNNING.value,
                )
            )
        try:
            result = run()
        except Exception as exc:
            self._finish(run_id, AgentRunStatus.FAILED, f"{type(exc).__name__}: {exc}")
            raise
        ok = getattr(result, "ok", True)
        self._finish(
            run_id,
            AgentRunStatus.SUCCEEDED if ok else AgentRunStatus.FAILED,
            None if ok else getattr(result, "error", None),
        )
        return result

    def _finish(self, run_id: str, status: AgentRunStatus, error: str | None) -> None:
        with self._c.session_scope() as db:
            row = db.get(AgentRunRow, run_id)
            row.status = status.value
            row.error = error
            row.finished_at = utcnow()
